"""Portable session storage, Windows singleton and pystray integration."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import threading
import time
import tkinter as tk
from tkinter import ttk
import pystray
from PIL import Image,ImageDraw
from core import ROOT,user32,kernel32,list_windows

TITLE='游戏数字随手贴 V3.5 · 紧凑版'

class CheckedRotatingHandler(RotatingFileHandler):
    def handleError(self,record):raise

class SingleInstance:
    def __init__(self,name='GameDigitsPaste_V35'):
        kernel32.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype=wintypes.HANDLE
        kernel32.CreateEventW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.BOOL,wintypes.LPCWSTR]
        kernel32.CreateEventW.restype=wintypes.HANDLE
        kernel32.SetEvent.argtypes=[wintypes.HANDLE]
        kernel32.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel32.CloseHandle.argtypes=[wintypes.HANDLE]
        self.mutex=kernel32.CreateMutexW(None,False,'Local\\'+name)
        error=ctypes.get_last_error()
        if not self.mutex:raise ctypes.WinError(error)
        self.primary=error!=183
        self.show_event=kernel32.CreateEventW(None,False,False,'Local\\'+name+'_Show')
        if not self.show_event:self.close();raise ctypes.WinError(ctypes.get_last_error())
        if not self.primary:kernel32.SetEvent(self.show_event)
    def requested(self):return kernel32.WaitForSingleObject(self.show_event,0)==0
    def close(self):
        for name in ('show_event','mutex'):
            handle=getattr(self,name,None)
            if handle:kernel32.CloseHandle(handle);setattr(self,name,None)

def window_identity(hwnd,title):
    kernel32.CloseHandle.argtypes=[wintypes.HANDLE]
    pid=wintypes.DWORD()
    user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    kernel32.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel32.OpenProcess.restype=wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    process=kernel32.OpenProcess(0x1000,False,pid.value)
    if not process:raise ctypes.WinError(ctypes.get_last_error())
    try:
        size=wintypes.DWORD(32768);name=ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(process,0,name,ctypes.byref(size)):raise ctypes.WinError(ctypes.get_last_error())
        return {'title':title,'executable':str(Path(name.value)).casefold()}
    finally:kernel32.CloseHandle(process)

def session_options(tab):
    tab.settings()
    return {**{key:getattr(tab,key).get() for key in ('mode','length','interval','threshold','keywords','input_mode','paste_enabled','replace_existing','profile')},
            'region':list(tab.region),'input_region':list(tab.input_region) if tab.input_region else None,'size':list(tab.image_size) if tab.image_size else tab.expected_size}

def apply_options(tab,options):
    region=options['region'];tab.validate_region(region)
    tab.region=tuple(region)
    if options.get('input_region') is not None:tab.validate_region(options['input_region'])
    tab.input_region=tuple(options['input_region']) if options.get('input_region') else None
    for key in ('mode','length','interval','threshold','keywords','input_mode','paste_enabled','replace_existing','profile'):
        getattr(tab,key).set(options[key])
    if tab.mode.get() not in ('确认提示文字','仅数字区域') or tab.input_mode.get() not in ('自动定位确认弹窗','手动标记位置'):raise ValueError('保存的识别模式错误')
    size=options.get('size')
    if size is not None and (len(size)!=2 or any(not isinstance(n,int) or n<=0 for n in size)):raise ValueError('保存的窗口尺寸错误')
    tab.expected_size=tuple(size) if size else None
    tab.settings();tab.update_input_hint()

class DesktopFeatures:
    def __init__(self,app,singleton=None):
        self.app=app;self.singleton=singleton;self.session_path=ROOT/'runtime'/'session.json'
        self.last_state=None;self.restore_rows=[];self.saved_selected=0;self.resume_tabs=set();self.paused=False;self.closed=False;self.restore_pause=None
        self.logger=logging.Logger('paste-events-'+str(id(self)))
        handler=CheckedRotatingHandler(ROOT/'runtime'/'paste-events.jsonl',maxBytes=1024*1024,backupCount=3,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(message)s'));self.logger.addHandler(handler);self.logger.propagate=False
        self.tray=pystray.Icon('GameDigitsPasteV35',self.icon_image(),'游戏数字随手贴 V3.5',menu=pystray.Menu(
            pystray.MenuItem('显示窗口',lambda *_:app.events.put(('show',None)),default=True),
            pystray.MenuItem('暂停 / 恢复全部',lambda *_:app.events.put(('pause-all',None))),
            pystray.MenuItem('退出',lambda *_:app.events.put(('exit',None)))))
        self.tray.run_detached()
        bar=ttk.Frame(app.root,padding=(12,0,12,5));bar.pack(before=app.notebook,fill='x')
        self.pause_button=ttk.Button(bar,text='全部暂停',width=8,command=self.toggle_pause);self.pause_button.pack(side='left')
        ttk.Button(bar,text='收进托盘',width=8,command=self.hide).pack(side='left',padx=6)
        ttk.Label(bar,text='Ctrl+Alt+P',foreground='#798477',font=('Microsoft YaHei',8)).pack(side='right')
        self.load()
        app.root.after(1200,self.save_tick)
        app.root.after(100,self.singleton_tick)
    @staticmethod
    def icon_image():
        image=Image.new('RGBA',(64,64),(0,0,0,0));d=ImageDraw.Draw(image)
        d.rounded_rectangle((4,4,60,60),radius=14,fill='#247754');d.line((21,19,43,19,27,45),fill='white',width=7)
        return image
    def load(self):
        if not self.session_path.exists():return
        state=json.loads(self.session_path.read_text(encoding='utf-8'))
        if state.get('schema')!=1 or not isinstance(state.get('tabs'),list) or not 1<=len(state['tabs'])<=8:raise ValueError('保存的会话格式错误；原文件已保留')
        self.restore_rows=state['tabs'];self.saved_selected=int(state.get('selected',0))
        self.restore_pause=state.get('resume_tabs',[]) if state.get('paused',False) else None
        for index,row in enumerate(self.restore_rows):
            tab=self.app.tabs[0] if index==0 else self.app.add_tab()
            tab.bound_identity=row.get('identity');apply_options(tab,row['options'])
            if tab.bound_identity:self.app.rename_tab(tab,tab.bound_identity['title'])
            tab.status.set('正在恢复上次窗口配置…')
        if state.get('mini'):self.app.toggle_mini()
        size=state.get('normal_size','920x660')
        if not re.fullmatch(r'[0-9]{3,4}x[0-9]{3,4}',size):raise ValueError('保存的界面尺寸错误')
        self.app.normal_size=size
        if not self.app.mini:self.app.root.geometry(size)
        self.app.notebook.select(self.app.tabs[min(max(self.saved_selected,0),len(self.app.tabs)-1)].host)
    def restore(self):
        windows=list_windows();identities=[]
        for hwnd,title in windows:
            if any(row.get('identity',{}).get('title')==title for row in self.restore_rows if row.get('identity')):
                identities.append((hwnd,window_identity(hwnd,title)))
        for tab,row in zip(self.app.tabs,self.restore_rows):
            identity=row.get('identity')
            if not identity:continue
            matches=[hwnd for hwnd,key in identities if key==identity]
            if len(matches)!=1:
                tab.status.set('上次窗口未找到，请选择后连接。' if not matches else '找到多个同名窗口，请手动选择。');continue
            if any(t is not tab and t.capture and t.capture.hwnd==matches[0] for t in self.app.tabs):
                tab.status.set('上次窗口已被其他标签连接。');continue
            tab.refresh_windows();index=next(i for i,(hwnd,_) in enumerate(tab.windows) if hwnd==matches[0]);tab.window_combo.current(index)
            try:
                tab.connect();apply_options(tab,row['options']);tab.active=bool(row.get('active',True));tab.next_job=0
                tab.status.set('已恢复上次游戏窗口和配置。' if tab.active else '已恢复上次窗口，保持暂停。')
            except Exception as error:tab.active=False;tab.status.set('恢复窗口失败：'+str(error))
        self.restore_rows=[]
        if self.restore_pause is not None:
            self.paused=True;self.pause_button.configure(text='恢复全部')
            self.resume_tabs={self.app.tabs[i] for i in self.restore_pause if isinstance(i,int) and 0<=i<len(self.app.tabs)}
            self.restore_pause=None
    def state(self):
        app=self.app
        return {'schema':1,'mini':app.mini,'paused':self.paused,'resume_tabs':[app.tabs.index(tab) for tab in self.resume_tabs if tab in app.tabs],'normal_size':app.normal_size if app.mini else f'{app.root.winfo_width()}x{app.root.winfo_height()}',
                'selected':app.tabs.index(app.selected()) if app.selected() in app.tabs else 0,
                'tabs':[{'identity':tab.bound_identity,'active':tab.active,'options':session_options(tab)} for tab in app.tabs]}
    def save(self):
        if self.restore_rows:return
        state=self.state()
        if state==self.last_state:return
        temp=self.session_path.with_suffix('.tmp');temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.replace(self.session_path);self.last_state=state
    def save_tick(self):
        if self.app.closing:return
        try:self.save()
        except Exception as error:
            if self.app.selected():self.app.selected().status.set('配置保存失败：'+str(error))
        self.app.root.after(1200,self.save_tick)
    def singleton_tick(self):
        if self.app.closing:return
        if self.singleton and self.singleton.requested():self.show()
        self.app.root.after(100,self.singleton_tick)
    def show(self):
        self.app.root.deiconify();self.app.root.lift()
    def hide(self):self.app.root.withdraw()
    def toggle_pause(self):
        if not self.paused:
            self.resume_tabs={tab for tab in self.app.tabs if tab.active}
            for tab in self.app.tabs:
                self.app.cancel_paste(tab);tab.pending_paste=None
                if tab.active:tab.active=False;tab.invalidate();tab.status.set('全部暂停；再次按 Ctrl+Alt+P 恢复。')
            self.paused=True;self.pause_button.configure(text='恢复全部')
        else:
            for tab in self.resume_tabs:
                if tab in self.app.tabs and tab.capture and not tab.closing:tab.active=True;tab.next_job=0;tab.status.set('识别已恢复。')
            self.resume_tabs.clear();self.paused=False;self.pause_button.configure(text='全部暂停')
        self.tray.title='游戏数字随手贴 V3.5 · '+('已暂停' if self.paused else '运行中')
    def trace(self,record):self.logger.info(json.dumps(record,ensure_ascii=False))
    def close(self):
        if self.closed:return
        self.closed=True;self.tray.stop()
        for handler in self.logger.handlers:handler.close()

