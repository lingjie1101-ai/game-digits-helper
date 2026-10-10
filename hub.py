from __future__ import annotations
import heapq,json,queue,threading,time
import tkinter as tk
from tkinter import ttk,messagebox
from core import ROOT,UmiService,extract_digits,user32
from panel import GameTab
from interaction import InputMonitor,PasteController,hit_input,foreground_is,frame_rect
from dialog_input import locate_input,candidate_at
from desktop_features import DesktopFeatures

class App:
    def __init__(self,root,engine=None,manage_engine=True,singleton=None):
        self.root=root; self.engine=engine or UmiService(); self.manage_engine=manage_engine
        self.ready=False; self.closing=False; self.tabs=[]; self.counter=0; self.stop_event=threading.Event()
        self.paste_job=None;self.paste_thread=None;self.features=None
        self.mini=False;self.normal_size='920x660'
        self.tasks=queue.PriorityQueue(); self.task_sequence=0; self.events=queue.Queue(); self.clipboard_hold_until=0
        self.profile_path=ROOT/'runtime'/'profiles.json'; self.profile_path.parent.mkdir(exist_ok=True)
        self.profiles={}
        if self.profile_path.exists():
            self.profiles=json.loads(self.profile_path.read_text(encoding='utf-8'))
            if not isinstance(self.profiles,dict):raise ValueError('配置顶层必须为字典')
        root.title('游戏数字随手贴 V3.5 · 紧凑版');root.geometry('920x660');root.minsize(800,600);root.configure(bg='#f3f6f2')
        style=ttk.Style(root);style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei',9))
        style.configure('TFrame',background='#f3f6f2')
        style.configure('TLabel',background='#f3f6f2',foreground='#254336')
        style.configure('TButton',padding=(8,2))
        style.configure('Accent.TButton',background='#247754',foreground='white')
        style.map('Accent.TButton',background=[('active','#195b3f')])
        style.configure('TNotebook.Tab',padding=(12,5))
        style.configure('TLabelframe',background='#f3f6f2')
        style.configure('TLabelframe.Label',background='#f3f6f2',foreground='#254336',font=('Microsoft YaHei',9,'bold'))
        bar=ttk.Frame(root,padding=(12,8));bar.pack(fill='x')
        ttk.Label(bar,text='游戏数字随手贴',font=('Microsoft YaHei',12,'bold')).pack(side='left')
        self.version_label=ttk.Label(bar,text='V3.5 · 紧凑版',foreground='#697f70');self.version_label.pack(side='left',padx=10)
        ttk.Button(bar,text='＋ 窗口',width=6,command=self.add_tab).pack(side='right')
        ttk.Button(bar,text='关闭标签',width=6,command=self.remove_tab).pack(side='right',padx=(0,6))
        self.mini_button=ttk.Button(bar,text='小窗模式',width=6,command=self.toggle_mini);self.mini_button.pack(side='right',padx=(0,6))
        self.notebook=ttk.Notebook(root);self.notebook.pack(fill='both',expand=True,padx=8,pady=(0,8))
        root.update_idletasks();self.owner=user32.GetAncestor(root.winfo_id(),2)
        self.add_tab();self.notebook.bind('<<NotebookTabChanged>>',self.tab_changed)
        root.report_callback_exception=self.callback_error;root.protocol('WM_DELETE_WINDOW',self.close)
        self.paste_controller=PasteController()
        self.monitor=InputMonitor(lambda x,y,hwnd,sx,sy:self.events.put(('click',(x,y,hwnd,time.monotonic(),(sx,sy)))),lambda:self.events.put(('key',None)),lambda:self.events.put(('mouse-cancel',None)),lambda:self.events.put(('pause-all',None)))
        self.monitor.start()
        self.features=DesktopFeatures(self,singleton)
        self.worker=threading.Thread(target=self.worker_loop,daemon=True);self.worker.start()
        root.after(15,self.poll)
    def add_tab(self):
        if self.closing:return
        if len(self.tabs)>=8:raise ValueError('最多同时使用8个标签')
        self.counter+=1;frame=ttk.Frame(self.notebook)
        self.notebook.add(frame,text=f'窗口 {self.counter}')
        tab=GameTab(self,frame);self.tabs.append(tab);self.notebook.select(frame)
        return tab
    def selected(self):
        selected=self.notebook.select()
        return next((tab for tab in self.tabs if str(tab.host)==selected),None)
    def rename_tab(self,tab,title):
        short=title.split(' - ')[-1]
        self.notebook.tab(tab.host,text=short[:26])
    def tab_changed(self,event=None):
        tab=self.selected()
        if tab and tab.image is not None and not self.mini:self.root.after_idle(tab.render)
    def toggle_mini(self):
        if not self.mini:self.normal_size=f'{self.root.winfo_width()}x{self.root.winfo_height()}'
        self.mini=not self.mini
        for tab in self.tabs:
            if self.mini:tab.preview_panel.grid_remove()
            else:tab.preview_panel.grid()
            tab.body_panel.columnconfigure(0,weight=0 if self.mini else 1)
            tab.body_panel.columnconfigure(1,weight=1 if self.mini else 0)
        if self.mini:
            self.version_label.pack_forget();self.root.minsize(380,600);self.root.geometry('380x600')
        else:
            self.version_label.pack(side='left',padx=10);self.root.minsize(800,600);self.root.geometry(self.normal_size)
        self.mini_button.configure(text='展开预览' if self.mini else '小窗模式')
    def ensure_preview(self):
        if self.mini:self.toggle_mini();self.root.update_idletasks()
    def remove_tab(self):
        tab=self.selected()
        if not tab:return
        tab.close();self.notebook.forget(tab.host);self.tabs.remove(tab);tab.host.destroy()
        if not self.tabs:self.add_tab()
    def refresh_profiles(self):
        for tab in self.tabs:
            if tab.profile_combo is not None and tab.profile_combo.winfo_exists():
                tab.profile_combo['values']=list(self.profiles)
    def callback_error(self,kind,error,traceback):
        tab=self.selected()
        if tab:
            tab.active=False;tab.invalidate();tab.status.set(f'操作错误：{error}');tab._controls()
        else:messagebox.showerror('操作错误',str(error))
    def worker_loop(self):
        try:
            if self.manage_engine:self.engine.start(cancel_event=self.stop_event)
            else:
                options=self.engine.request('/api/ocr/get_options',timeout=5)
                if not isinstance(options,dict) or 'data.format' not in options:raise RuntimeError('识别引擎接口错误')
            self.events.put(('ready',None))
            while True:
                _,_,task=self.tasks.get()
                if task is None:return
                tab,version,image,options,single,token=task
                if self.closing or tab.closing or version!=tab.revision:
                    tab.events.put(('discarded',None));continue
                try:
                    box=options.get('ocr_box')
                    blocks=self.engine.recognize(image.crop(box) if box else image)
                    if box:
                        for block in blocks:
                            if 'box' in block:block['box']=[[x+box[0],y+box[1]] for x,y in block['box']]
                    digits,score,raw,reason=extract_digits(blocks,**{key:options[key] for key in ('mode','length','keywords','threshold')})
                    target,target_reason=locate_input(image,blocks,digits,options['keywords'])
                    tab.events.put(('result',(version,digits,score,raw,reason,single,options['interval'],token,target,target_reason)))
                except Exception as error:tab.events.put(('ocr-error',(version,str(error))))
        except Exception as error:self.events.put(('engine-error',str(error)))
    def valid_request(self,tab,token):
        if self.closing or tab.closing or tab.selection_kind or not tab.active or not tab.paste_enabled.get() or not tab.capture:return False
        if token['revision']!=tab.revision or token['hwnd']!=tab.capture.hwnd or time.monotonic()-token['time']>5:return False
        return foreground_is(token['hwnd'])
    def promote_queued(self,tab,token):
        latest=tab.capture.snapshot()
        if latest is None or latest[1]<token['time']:return False
        image,stamp=latest;options=tab.settings()
        if tab.input_mode.get()=='自动定位确认弹窗' and tab.auto_target and tab.auto_target.layout_matches(image):options['ocr_box']=tab.auto_target.ocr_box()
        if tab.input_mode.get()!='自动定位确认弹窗':return False
        with self.tasks.mutex:
            for index,(priority,sequence,task) in enumerate(self.tasks.queue):
                if task is not None and task[0] is tab and task[1]==tab.revision:
                    self.tasks.queue[index]=(-10,sequence,(tab,tab.revision,image,options,False,token))
                    heapq.heapify(self.tasks.queue);return True
        return False
    def valid_paste(self,tab,token):
        if not self.valid_request(tab,token):return False
        region=tab.effective_input()
        sx,sy=token.get('start',(token['x'],token['y']))
        return hit_input(token['hwnd'],token['x'],token['y'],region,tab.image_size) and hit_input(token['hwnd'],sx,sy,region,tab.image_size)
    def paste_trace(self,tab,message,**details):
        tab.paste_status.set(message)
        record={'time':time.strftime('%Y-%m-%d %H:%M:%S'),'message':message,**details}
        try:
            self.features.trace(record)
        except OSError as error:
            tab.paste_status.set(message+'；日志写入失败：'+str(error))
    def paste(self,tab,digits,token):
        if not self.valid_paste(tab,token):return
        if self.paste_job:
            self.paste_trace(tab,'上一条粘贴正在处理，请稍后再点。');return
        capture=tab.capture;region=tab.effective_input();size=tab.image_size;target=tab.auto_target if tab.input_mode.get()=='自动定位确认弹窗' else None
        replace=tab.replace_existing.get();epoch=self.monitor.input_epoch
        job={'tab':tab,'digits':digits,'token':token,'cancel':threading.Event()}
        self.paste_job=job;self.clipboard_hold_until=float('inf')
        def guard():
            if job['cancel'].is_set() or self.stop_event.is_set() or self.monitor.input_epoch!=epoch or time.monotonic()-token['time']>5:return False
            if not foreground_is(token['hwnd']) or not hit_input(token['hwnd'],token['x'],token['y'],region,size):return False
            if target:
                snapshot=capture.snapshot()
                if not snapshot or snapshot[1]<token['time'] or not target.matches(snapshot[0]):return False
            return True
        def send():
            try:
                self.paste_controller.paste(token['hwnd'],digits,self.owner,replace,guard=guard)
                self.events.put(('paste-done',(job,None)))
            except Exception as error:self.events.put(('paste-done',(job,str(error))))
        self.paste_thread=threading.Thread(target=send,name='confirmed-input-paste',daemon=True);self.paste_thread.start()
    def cancel_paste(self,tab=None):
        if self.paste_job and (tab is None or self.paste_job['tab'] is tab):self.paste_job['cancel'].set()
    def paste_done(self,job,error):
        if self.paste_job is not job:return
        self.paste_job=None;self.clipboard_hold_until=time.monotonic()+.5
        tab,digits,token=job['tab'],job['digits'],job['token']
        if tab.closing:return
        if error:
            self.paste_trace(tab,'粘贴未完成：'+error,hwnd=token['hwnd'])
            return
        tab.stable.copied(digits)
        elapsed=round((time.monotonic()-token['time'])*1000)
        tab.last_paste_time=time.monotonic();tab.last_paste_digits=digits
        self.paste_trace(tab,f'Windows 已接收 Ctrl+V · {elapsed} 毫秒；以游戏输入框为准。',hwnd=token['hwnd'],elapsed_ms=elapsed,path=token.get('path','ocr'))
        tab.status.set(f'{time.strftime("%H:%M:%S")} · 已向游戏发送粘贴 {digits}。')
        tab.history.insert(0,f'发送 {digits} ({time.strftime("%H:%M:%S")})');tab.history=tab.history[:5]
        tab.history_text.set('最近：'+tab.history[0])
    def click(self,x,y,hwnd,stamp,start=None):
        for tab in self.tabs:tab.pending_paste=None
        if self.paste_job:
            self.cancel_paste();return
        for tab in self.tabs:
            if not tab.capture or tab.capture.hwnd!=hwnd:continue
            if tab.selection_kind:
                self.paste_trace(tab,'收到游戏点击：请先完成或取消预览中的框选。');return
            if not tab.active:
                self.paste_trace(tab,'收到游戏点击：当前识别已暂停。');return
            if not tab.paste_enabled.get():
                self.paste_trace(tab,'收到游戏点击：点击自动粘贴尚未启用。');return
            sx,sy = start if start is not None else (x,y)
            latest=tab.capture.snapshot()
            if latest:tab.image,tab.frame_time=latest;tab.image_size=tab.image.size
            region=tab.effective_input()
            candidate=bool(region and hit_input(hwnd,x,y,region,tab.image_size) and hit_input(hwnd,sx,sy,region,tab.image_size))
            if not candidate and tab.input_mode.get()=='自动定位确认弹窗' and tab.image is not None:
                rect=frame_rect(hwnd)
                if (rect.right-rect.left,rect.bottom-rect.top)==tab.image_size:
                    candidate=candidate_at(tab.image,x-rect.left,y-rect.top) and candidate_at(tab.image,sx-rect.left,sy-rect.top)
            if candidate:
                if tab.fast_ready() and tab.last_paste_digits==tab.current_digits and time.monotonic()-tab.last_paste_time<.35:
                    self.paste_trace(tab,'连续点击已合并，上一条粘贴刚发送。',hwnd=hwnd);return
                tab.pending_paste={'hwnd':hwnd,'x':x,'y':y,'time':stamp,'revision':tab.revision,'start':(sx,sy)}
                tab.status.set('正在核对当前确认数字，随后粘贴…')
                self.paste_trace(tab,'收到输入框点击，正在核对新画面的数字。',hwnd=hwnd,x=x,y=y)
                return
            self.paste_trace(tab,'收到游戏点击，但位置不在已标记的输入框内。',hwnd=hwnd,x=x,y=y)
            return
    def poll(self):
        while not self.events.empty():
            kind,data=self.events.get()
            if kind=='closed':self.root.destroy();return
            if self.closing:continue
            if kind=='ready':
                self.ready=True
                for tab in self.tabs:tab.events.put(('ready',None))
                self.features.restore()
            elif kind=='engine-error':
                self.ready=False
                for tab in self.tabs:tab.events.put(('engine-error',data))
            elif kind=='click':self.click(*data)
            elif kind=='paste-done':self.paste_done(*data)
            elif kind=='show':self.features.show()
            elif kind=='pause-all':self.features.toggle_pause()
            elif kind=='exit':self.close()
            elif kind=='key':
                self.cancel_paste()
                for tab in self.tabs:
                    if tab.pending_paste:self.paste_trace(tab,'已取消本次粘贴：检测到新的键盘输入。')
                    tab.pending_paste=None
            elif kind=='mouse-cancel':
                self.cancel_paste()
                for tab in self.tabs:
                    if tab.pending_paste:self.paste_trace(tab,'已取消本次粘贴：鼠标再次操作。')
                    tab.pending_paste=None
        if not self.closing:
            try:self.monitor.check()
            except Exception as error:
                for tab in self.tabs:
                    tab.paste_enabled.set(False);tab.pending_paste=None;tab.input_hint.set(f'点击监听错误：{error}')
        self.root.after(15,self.poll)
    def close(self):
        if self.closing:return
        try:self.features.save()
        except Exception as error:(ROOT/'runtime'/'session-save-error.txt').write_text(str(error),encoding='utf-8')
        self.cancel_paste()
        self.closing=True;self.stop_event.set();self.root.withdraw();self.monitor.stop()
        self.features.close()
        for tab in self.tabs:tab.close()
        def shutdown():
            if self.paste_thread:self.paste_thread.join(timeout=2)
            self.task_sequence+=1;self.tasks.put((-100,self.task_sequence,None));self.worker.join(timeout=35)
            try:
                if self.manage_engine:self.engine.stop()
            except Exception as error:(ROOT/'runtime'/'shutdown-error.txt').write_text(str(error),encoding='utf-8')
            finally:self.events.put(('closed',None))
        threading.Thread(target=shutdown,daemon=True).start()
