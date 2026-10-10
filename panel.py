from __future__ import annotations
import ctypes
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from PIL import Image, ImageTk
from compact_ui import build_tab, open_settings, draw_empty
from core import ROOT, KEYWORDS, UmiService, WindowCapture, StableDigits, list_windows, crop_region, extract_digits, write_clipboard, user32


class GameTab:
    def __init__(self, hub, host):
        self.hub = hub; self.host = host; self.root = hub.root
        self.events = queue.Queue(); self.tasks = hub.tasks
        self.engine = hub.engine; self.ready = hub.ready; self.closing = False
        self.capture = None; self.image = None; self.photo = None; self.image_size = None
        self.bound_identity=None;self.expected_size=None
        self.region = (0,0,1,1); self.transform = (0,0,1,1)
        self.input_region = None; self.pending_paste = None
        self.auto_target = None; self.target_reason = '等待确认弹窗'
        self.frame_time=0;self.last_render_time=0;self.last_paste_time=0;self.last_paste_digits=''
        self.selection_kind = None; self.drag_start = None; self.drag_end = None
        self.active = False; self.busy = False; self.next_job = 0; self.revision = 0
        self.stable = StableDigits(); self.current_digits = ''; self.windows = []
        self.profile_path = hub.profile_path; self.profiles = hub.profiles
        self._ui(); self.owner = hub.owner; self.refresh_windows()
        self.root.after(100,self._poll)

    def _ui(self):
        build_tab(self)
    def show_settings(self):
        open_settings(self)

    def invalidate(self):
        self.hub.cancel_paste(self)
        self.revision += 1; self.stable.reset(); self.current_digits = ''; self.pending_paste = None
        self.auto_target = None
        self.result.set('— — — —'); self.result_hint.set('连续两次识别一致后复制'); self.copy_button.configure(state='disabled')
    def settings_changed(self):
        self.invalidate(); self.next_job = 0; self._controls()
    def settings(self):
        interval = float(self.interval.get()); threshold = float(self.threshold.get())/100
        if not .3<=interval<=30:
            raise ValueError('识别间隔请填写 0.3–30 秒')
        if not 0<=threshold<=1:
            raise ValueError('置信度请填写 0–100')
        length = 0 if self.length.get()=='不限' else int(self.length.get())
        words = [word.strip() for word in self.keywords.get().replace('，',',').split(',') if word.strip()]
        if self.mode.get()=='确认提示文字' and not words:
            raise ValueError('至少填写一个提示关键词')
        return {'mode':'context' if self.mode.get()=='确认提示文字' else 'digits','length':length,'keywords':words,'threshold':threshold,'interval':interval}
    def _controls(self):
        valid_region = self.image is not None and self.region[2]*self.image.width>=8 and self.region[3]*self.image.height>=8
        ready = self.ready and valid_region and not self.closing
        self.start_button.configure(text='暂停识别' if self.active else '开始识别',state='normal' if ready else 'disabled')
        self.once_button.configure(state='normal' if ready and not self.busy and not self.active else 'disabled')
    def refresh_windows(self):
        selected = self.window_combo.current()
        previous = self.windows[selected][0] if 0<=selected<len(self.windows) else None
        self.windows = list_windows()
        self.window_combo['values'] = [title for hwnd,title in self.windows]
        match = next((index for index,(hwnd,title) in enumerate(self.windows) if hwnd==previous),None)
        if match is None:self.window_combo.set('请选择游戏窗口')
        else:self.window_combo.current(match)
    def connect(self):
        index = self.window_combo.current()
        if index<0:
            raise ValueError('请先选择窗口')
        hwnd,title = self.windows[index]
        if any(tab is not self and tab.capture and tab.capture.hwnd==hwnd for tab in self.hub.tabs):
            raise ValueError('这个窗口已绑定其他标签，请切换到对应标签')
        from desktop_features import window_identity
        identity=window_identity(hwnd,title)
        self.settings(); self.disconnect()
        capture = WindowCapture(hwnd,on_error=lambda error:self.events.put(('capture-error',error)))
        capture.start()
        self.bound_identity=identity
        self.capture = capture; self.capture_started = time.monotonic(); self.active = True; self.next_job = 0
        self.profile.set(title[:80]); self.hub.rename_tab(self,title); self.status.set('窗口已连接，收到画面后自动识别…')
    def disconnect(self):
        self.cancel_selection()
        self.active = False; self.invalidate()
        if self.capture:
            self.capture.stop(); self.capture = None
        self.bound_identity=None;self.expected_size=None
        self.image = None; self.image_size = None; self.region = (0,0,1,1)
        self.input_region = None; self.paste_enabled.set(True); self.input_mode.set('自动定位确认弹窗'); self.input_hint.set('等待确认弹窗，定位后直接点击游戏输入框。')
        self.render(); self.status.set('捕获已停止。'); self._controls()
    def open_image(self):
        file = filedialog.askopenfilename(filetypes=[('图片','*.png *.jpg *.jpeg *.bmp *.webp'),('所有文件','*.*')])
        if not file:
            return
        image = Image.open(file); image.load(); self.disconnect()
        self.image = image.convert('RGB'); self.image_size = self.image.size; self.render()
        self.status.set('图片已打开，支持识别一次或框选数字区域。'); self._controls()
    def render(self):
        if not self.image:
            draw_empty(self.canvas); return
        width,height = max(1,self.canvas.winfo_width()),max(1,self.canvas.winfo_height())
        scale = min(width/self.image.width,height/self.image.height)
        dw,dh = max(1,round(self.image.width*scale)),max(1,round(self.image.height*scale))
        dx,dy = (width-dw)/2,(height-dh)/2
        self.transform = (dx,dy,dw,dh)
        self.photo = ImageTk.PhotoImage(self.image.resize((dw,dh),Image.Resampling.BILINEAR),master=self.canvas)
        self.canvas.delete('all'); self.canvas.create_image(dx,dy,image=self.photo,anchor='nw')
        x,y,w,h = self.region
        self.canvas.create_rectangle(dx+x*dw,dy+y*dh,dx+(x+w)*dw,dy+(y+h)*dh,outline='#3bab75',width=2)
        display_region = self.effective_input()
        if display_region:
            ix,iy,iw,ih = display_region
            self.canvas.create_rectangle(dx+ix*dw,dy+iy*dh,dx+(ix+iw)*dw,dy+(iy+ih)*dh,outline='#db7829',width=3)
        if self.selection_kind and self.drag_start and self.drag_end:
            ax,ay = self.drag_start; bx,by = self.drag_end
            self.canvas.create_rectangle(dx+ax*dw,dy+ay*dh,dx+bx*dw,dy+by*dh,outline='#e77714',width=3,dash=(6,3))
        self.region_text.set(f'识别范围 {round(x*self.image.width)}, {round(y*self.image.height)} · {round(w*self.image.width)} × {round(h*self.image.height)} 像素')
    def full_region(self):
        self.cancel_selection(); self.invalidate(); self.region = (0,0,1,1); self.next_job = 0
        self.render(); self._controls(); self.status.set('识别范围已恢复为整个窗口。')
    def toggle(self):
        if self.active:
            self.active = False; self.invalidate(); self.status.set('自动复制已暂停。')
        else:
            self.settings(); self.invalidate(); self.active = True; self.next_job = 0; self.status.set('自动识别已开启。')
        self._controls()
    def submit(self,single=False,token=None):
        if self.busy or not self.ready or self.image is None:
            return
        options = self.settings()
        if self.capture:
            snapshot = self.capture.snapshot()
            if snapshot is None:
                raise RuntimeError('窗口首帧尚未到达')
            if token is not None and snapshot[1]<token['time']:
                self.status.set('等待点击后的新画面，再核对数字…')
                return
            self.image = snapshot[0]
            self.frame_time = snapshot[1]
        if self.input_mode.get()=='自动定位确认弹窗' and self.auto_target and self.auto_target.layout_matches(self.image):
            options['ocr_box']=self.auto_target.ocr_box()
        # Preserve full-frame coordinates; the worker crops a previously proven dialog.
        crop = self.image.copy() if self.input_mode.get()=='自动定位确认弹窗' else crop_region(self.image,self.region)
        self.hub.task_sequence+=1
        self.tasks.put_nowait((-10 if token is not None else 0,self.hub.task_sequence,(self,self.revision,crop,options,single,token)))
        self.busy = True; self.status.set('正在识别确认数字…'); self._controls()
    def _poll(self):
        if self.closing:
            return
        try:
            if self.capture:
                snapshot = self.capture.snapshot()
                if snapshot:
                    image,self.frame_time = snapshot
                    if self.expected_size and self.expected_size!=image.size:
                        if self.input_mode.get()=='手动标记位置':
                            self.active=False;self.input_region=None;self.paste_enabled.set(False);self.invalidate();self.status.set('窗口尺寸变化，手动位置已停用，请重新标记。')
                        else:
                            self.region=(0,0,1,1);self.input_region=None;self.invalidate();self.next_job=0;self.status.set('尺寸变化，正在重新自动定位。')
                        self.expected_size=None
                    if self.image_size is not None and self.image_size!=image.size:
                        self.cancel_selection()
                        self.active = False; self.invalidate(); self.region = (0,0,1,1)
                        self.input_region = None; self.paste_enabled.set(False); self.input_hint.set('尺寸已改变，请重新标记输入框。')
                        self.status.set('窗口尺寸已变化，已暂停。请确认识别范围后重新开始。')
                    if self.image is None:
                        self.status.set('已收到实时画面，自动识别已开启。' if self.active else '已收到实时画面。')
                    self.image_size = image.size; self.image = image
                elif time.monotonic()-self.capture_started>8:
                    raise RuntimeError('捕获超过 8 秒仍未收到画面，请检查目标窗口状态')
            if self.image is not None and self.hub.selected() is self and self.canvas.winfo_ismapped() and time.monotonic()-self.last_render_time>=.2:
                self.render()
                self.last_render_time=time.monotonic()
            self.update_input_hint()
            self._drain()
            if self.pending_paste and not self.hub.valid_request(self,self.pending_paste):
                reason = '点击后超过 5 秒，数字核对超时。' if time.monotonic()-self.pending_paste['time']>5 else '游戏焦点、输入位置或运行状态已改变。'
                self.hub.paste_trace(self,'已取消本次粘贴：'+reason)
                self.pending_paste = None
            if self.pending_paste and self.fast_ready() and self.frame_time>=self.pending_paste['time'] and self.hub.valid_paste(self,self.pending_paste):
                token=self.pending_paste;token['path']='confirmed_visual'
                self.hub.paste(self,self.current_digits,token);self.pending_paste=None
            elif self.pending_paste and self.busy:
                self.hub.promote_queued(self,self.pending_paste)
            elif self.pending_paste and not self.busy:
                self.submit(token=self.pending_paste)
            elif self.active and not self.busy and not self.fast_ready() and time.monotonic()>=self.next_job:
                self.submit()
            self.update_phase()
            self._controls()
        except Exception as error:
            self.active = False; self.invalidate(); self.status.set(f'操作已暂停：{error}')
            if self.capture and (self.capture.closed or self.capture.error):
                self.capture.stop(); self.capture = None; self.image = None
            self._controls()
        self.update_phase()
        self.root.after(30,self._poll)
    def _drain(self):
        while not self.events.empty():
            kind,data = self.events.get_nowait()
            if self.closing:
                continue
            if kind=='discarded':
                self.busy = False
            elif kind=='ready':
                self.ready = True; self.status.set('离线引擎已就绪，自动识别中。' if self.capture and self.active else '离线引擎已就绪，请选择并连接游戏窗口。')
            elif kind=='result':
                self.busy = False
                version,digits,score,raw,reason,single,interval,token,target,target_reason = data
                if version!=self.revision:
                    continue
                self.auto_target = target; self.target_reason = target_reason
                self.update_input_hint()
                self.current_digits = digits or ''
                self.result.set(digits or '— — — —')
                self.copy_button.configure(state='normal' if digits else 'disabled')
                self.result_hint.set(f'置信度 {score:.0%}' if digits else reason)
                should_copy = self.stable.observe(digits)
                if token is not None:
                    if self.pending_paste is token and self.hub.valid_paste(self,token):
                        if digits and self.stable.count>=2:
                            self.hub.paste(self,digits,token)
                            self.pending_paste = None
                        elif not digits:
                            self.hub.paste_trace(self,'本次未发送粘贴：'+reason)
                            self.pending_paste = None; self.status.set(reason)
                    elif self.pending_paste is token and digits and target and target.matches(self.image):
                        self.hub.paste_trace(self,'本次点击位置不在当前确认输入框内。')
                        self.pending_paste=None
                elif digits and (single or should_copy) and self.hub.selected() is self and time.monotonic()>=self.hub.clipboard_hold_until:
                    self.copy(digits)
                else:
                    self.status.set(reason if not digits else '数字已识别，持续确认中。' if self.stable.count<2 else '数字已确认，正在监测变化。')
                self.next_job = time.monotonic()+(.05 if digits and target and self.stable.count<2 else interval)
            elif kind=='ocr-error':
                self.busy = False; version,error = data
                if version==self.revision:
                    self.active = False; self.invalidate(); self.status.set(f'识别已暂停：{error}')
            elif kind in ('engine-error','capture-error'):
                self.active = False; self.invalidate(); self.status.set(f'启动或捕获错误：{data}')
                if kind=='engine-error':
                    self.ready = False
    def copy(self,digits):
        write_clipboard(digits,self.owner)
        self.stable.copied(digits)
        self.result_hint.set(f'已复制 {digits} · Ctrl+V 即可粘贴')
        self.status.set(f'{time.strftime("%H:%M:%S")} · 确认数字 {digits} 已写入剪贴板。')
        self.history.insert(0,f'{digits} ({time.strftime("%H:%M:%S")})'); self.history = self.history[:5]
        self.history_text.set('最近：'+self.history[0])
    def manual_copy(self):
        if self.current_digits:
            self.copy(self.current_digits)
    def effective_input(self):
        if self.input_mode.get()=='自动定位确认弹窗':
            return self.auto_target.region if self.auto_target and self.auto_target.matches(self.image) else None
        return self.input_region
    def fast_ready(self):
        return bool(self.input_mode.get()=='自动定位确认弹窗' and self.auto_target and self.stable.count>=2 and self.stable.candidate==self.auto_target.digits and self.current_digits==self.auto_target.digits and self.auto_target.matches(self.image))
    def update_phase(self):
        if not self.ready:text,color='启动中','#a76d11'
        elif not self.capture:text,color='未连接','#798477'
        elif not self.active:text,color='已暂停','#ac4242'
        elif self.hub.paste_job and self.hub.paste_job['tab'] is self:text,color='正在粘贴','#a76d11'
        elif self.fast_ready():text,color=('可以点击' if self.paste_enabled.get() else '已确认 · 粘贴关闭'),'#247754'
        elif self.busy:text,color='正在识别','#a76d11'
        else:text,color='等待弹窗','#798477'
        if self.phase.get()!=text:self.phase.set(text);self.phase_label.configure(foreground=color)
    def update_input_hint(self):
        if self.input_mode.get()=='自动定位确认弹窗':
            message=self.target_reason if self.auto_target is None or self.effective_input() else '弹窗内容已改变，等待重新定位'
            if self.input_hint.get()!=message:self.input_hint.set(message)
        else:
            message='手动位置已标记；点击游戏对应输入框。' if self.input_region else '手动模式：请在预览拖框标记输入框。'
            if self.input_hint.get()!=message:self.input_hint.set(message)
    def input_mode_changed(self):
        self.cancel_selection();self.pending_paste=None;self.auto_target=None;self.next_job=0
        self.update_input_hint();self.render()
    def save_profile(self):
        name = self.profile.get().strip()
        if not name:
            raise ValueError('请填写游戏配置名称')
        options = self.settings()
        self.profiles[name] = {'region':self.region,'mode':self.mode.get(),'length':self.length.get(),'interval':self.interval.get(),'threshold':self.threshold.get(),'keywords':self.keywords.get(),'size':self.image_size,'input_region':self.input_region,'paste_enabled':self.paste_enabled.get(),'replace_existing':self.replace_existing.get(),'input_mode':self.input_mode.get()}
        temp = self.profile_path.with_suffix('.tmp'); temp.write_text(json.dumps(self.profiles,ensure_ascii=False,indent=2),encoding='utf-8'); temp.replace(self.profile_path)
        self.hub.refresh_profiles(); self.status.set(f'已保存配置：{name}')
    def load_profile(self):
        name = self.profile.get().strip()
        if name not in self.profiles:
            raise ValueError('请从下拉列表选择已保存的配置')
        config = self.profiles[name]
        region = config['region']
        if len(region)!=4 or any(not isinstance(v,(float,int)) for v in region) or any(v<0 or v>1 for v in region) or region[0]+region[2]>1.000001 or region[1]+region[3]>1.000001:
            raise ValueError('保存的识别区域格式错误')
        if self.image is not None and config.get('size') and tuple(config['size'])!=self.image.size:
            raise ValueError('当前窗口尺寸与保存配置不同，请重新框选并保存')
        self.settings_changed(); self.region = tuple(region)
        for key in ('mode','length','interval','threshold','keywords'):
            getattr(self,key).set(config[key])
        input_region = config.get('input_region')
        if input_region is not None:
            self.validate_region(input_region)
        self.input_region = tuple(input_region) if input_region is not None else None
        self.input_mode.set(config.get('input_mode','手动标记位置'))
        self.paste_enabled.set(bool(config.get('paste_enabled',False)) and (self.input_mode.get()=='自动定位确认弹窗' or self.input_region is not None))
        self.replace_existing.set(bool(config.get('replace_existing',True)))
        self.input_hint.set('输入框已从配置载入。' if self.input_region else '请标记确认数字输入框。')
        self.settings(); self.render(); self.status.set(f'已载入配置：{name}')
    @staticmethod
    def validate_region(region):
        if len(region)!=4 or any(not isinstance(v,(float,int)) for v in region) or any(v<0 or v>1 for v in region) or region[2]<=0 or region[3]<=0 or region[0]+region[2]>1.000001 or region[1]+region[3]>1.000001:
            raise ValueError('输入框区域格式错误')
    def select_ocr_region(self):
        self.select_area('ocr')
    def begin_input_mark(self):
        self.input_mode.set('手动标记位置')
        self.select_area('input')
    def select_area(self,kind):
        if self.image is None:
            raise ValueError('先连接游戏窗口，再选择区域')
        self.hub.ensure_preview()
        self.selection_kind = kind; self.drag_start = None; self.drag_end = None; self.pending_paste = None
        self.canvas.configure(cursor='crosshair'); self.cancel_button.configure(state='normal')
        label = '白色确认输入框（设置粘贴位置）' if kind=='input' else '确认提示文字（设置 OCR 范围）'
        self.selection_hint.set('正在标记：'+label+'。在左侧实时预览拖框，松开完成；取消框选或 Esc 退出。识别继续运行。')
        self.render()
    def cancel_selection(self):
        self.selection_kind = None; self.drag_start = None; self.drag_end = None
        self.canvas.configure(cursor='arrow'); self.cancel_button.configure(state='disabled')
        self.selection_hint.set('橙框是粘贴触发范围；直接在游戏输入框左键点击。')
        if self.image is not None:self.render()
    def selection_point(self,event,clamp=False):
        dx,dy,dw,dh = self.transform
        x,y = (event.x-dx)/dw,(event.y-dy)/dh
        if not clamp and not (0<=x<=1 and 0<=y<=1):return None
        return max(0,min(1,x)),max(0,min(1,y))
    def selection_press(self,event):
        if not self.selection_kind:return
        self.drag_start = self.selection_point(event); self.drag_end = self.drag_start
        self.canvas.focus_set()
    def selection_drag(self,event):
        if not self.selection_kind or self.drag_start is None:return
        self.drag_end = self.selection_point(event,True); self.render()
    def selection_release(self,event):
        if not self.selection_kind or self.drag_start is None:return
        kind = self.selection_kind; self.drag_end = self.selection_point(event,True)
        ax,ay = self.drag_start; bx,by = self.drag_end
        region = (min(ax,bx),min(ay,by),abs(bx-ax),abs(by-ay))
        if region[2]*self.image.width<8 or region[3]*self.image.height<8:
            self.drag_start = None; self.drag_end = None
            self.selection_hint.set('框选范围太小，请重新拖框（至少 8×8 像素）；识别继续运行。')
            self.render(); return
        self.cancel_selection()
        if kind=='input':
            self.input_region = region; self.paste_enabled.set(True)
            self.input_hint.set('输入框已标记并启用点击粘贴。由你点击游戏里的对应输入框。')
            self.status.set('输入框标记完成，已启用点击粘贴；OCR 识别范围保持不变。')
        else:
            self.invalidate(); self.region = region; self.next_job = 0
            self.status.set('OCR 识别范围已选择。')
        self.render(); self._controls()
    def paste_setting_changed(self):
        self.pending_paste = None
        if self.paste_enabled.get() and self.input_mode.get()=='手动标记位置' and self.input_region is None:
            self.paste_enabled.set(False)
            self.begin_input_mark()
    def close(self):
        if self.settings_dialog is not None and self.settings_dialog.winfo_exists():self.settings_dialog.destroy()
        self.cancel_selection()
        self.closing = True; self.active = False; self.invalidate()
        if self.capture:
            self.capture.stop(); self.capture = None
