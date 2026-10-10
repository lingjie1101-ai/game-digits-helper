"""Native game-window capture, Umi-OCR integration and confirmation-digit extraction."""
from __future__ import annotations

import base64
import configparser
import ctypes
from ctypes import wintypes
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.request

from PIL import Image, ImageOps
from windows_capture import WindowsCapture

ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
PORT = 12255
URL = f'http://127.0.0.1:{PORT}'
KEYWORDS = ['确认数字', '验证码', '确认码', '校验码', '验证数字', '认证码', '安全码']
user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE


def list_windows():
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback_type
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                text = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, text, length + 1)
                if text.value and text.value!='Program Manager' and not text.value.startswith('游戏数字随手贴'):
                    windows.append((int(hwnd), text.value))
        return True
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    if not user32.EnumWindows(callback, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return sorted(windows, key=lambda item: item[1].casefold())


def write_clipboard(digits: str, owner=0):
    if not re.fullmatch(r'[0-9]{1,32}', digits):
        raise ValueError('剪贴板只接受数字')
    encoded = (digits + '\0').encode('utf-16-le')
    handle = kernel32.GlobalAlloc(0x0002, len(encoded))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    transferred = False
    try:
        address = kernel32.GlobalLock(handle)
        if not address:
            raise ctypes.WinError(ctypes.get_last_error())
        ctypes.memmove(address, encoded, len(encoded))
        kernel32.GlobalUnlock(handle)
        if not user32.OpenClipboard(owner):
            raise RuntimeError('剪贴板被其他程序占用，请稍后重新识别')
        try:
            if not user32.EmptyClipboard() or not user32.SetClipboardData(13, handle):
                raise ctypes.WinError(ctypes.get_last_error())
            transferred = True
        finally:
            user32.CloseClipboard()
    finally:
        if not transferred:
            kernel32.GlobalFree(handle)


def read_clipboard_text():
    if not user32.OpenClipboard(0):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        handle = user32.GetClipboardData(13)
        if not handle:
            return None
        address = kernel32.GlobalLock(handle)
        if not address:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.wstring_at(address)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def extract_digits(blocks, mode='context', length=4, keywords=None, threshold=.75):
    """Return exactly one high-confidence candidate; reject other numerical fields."""
    normalized = [(unicodedata.normalize('NFKC', str(b['text'])), float(b['score'])) for b in blocks]
    raw = '\n'.join(text for text, _ in normalized)
    if mode == 'digits':
        text = re.sub(r'\s+', '', raw)
        if not re.fullmatch(r'[0-9]{1,32}', text):
            return None, 0, raw, '数字区域内包含其他文字，或未找到数字'
        candidates = [(text, min((score for _, score in normalized), default=0))]
    elif mode == 'context':
        words = keywords if keywords is not None else KEYWORDS
        if not words:
            raise ValueError('至少填写一个提示关键词')
        pattern = re.compile('(?:' + '|'.join(re.escape(word) for word in words) + r')[\s:：,，]*([0-9]{1,32})(?![0-9A-Za-z])')
        candidates = []
        for text, score in normalized:
            # Engine blocks are kept intact so a level later in the line never joins the code.
            candidates.extend((match.group(1), score) for match in pattern.finditer(text))
        # Text blocks can split between the label and the digit. Only join spatially adjacent blocks.
        for a, b in zip(blocks, blocks[1:]):
            ta = unicodedata.normalize('NFKC', str(a['text']))
            tb = unicodedata.normalize('NFKC', str(b['text']))
            if any(ta.rstrip().endswith(word) for word in words) and re.match(r'^\s*[0-9]', tb):
                ba, bb = a.get('box'), b.get('box')
                if ba and bb:
                    ya = sum(p[1] for p in ba)/4; yb = sum(p[1] for p in bb)/4
                    height = max(p[1] for p in ba)-min(p[1] for p in ba)
                    gap = min(p[0] for p in bb)-max(p[0] for p in ba)
                    if abs(ya-yb)<=max(4,height*.6) and -4<=gap<=max(10,height*2):
                        candidates.extend((m.group(1), min(float(a['score']),float(b['score']))) for m in pattern.finditer(ta+tb))
    else:
        raise ValueError('识别模式错误')
    candidates = [(digits, score) for digits,score in candidates if not length or len(digits)==length]
    unique = {}
    for digits,score in candidates:
        unique[digits] = max(score,unique.get(digits,0))
    if not unique:
        return None,0,raw,'未找到符合位数的确认数字'
    if len(unique)!=1:
        return None,0,raw,'发现多个不同的确认数字，请缩小识别区域'
    digits,score = next(iter(unique.items()))
    if score<threshold:
        return None,score,raw,f'置信度 {score:.0%} 低于 {threshold:.0%}，未复制'
    return digits,score,raw,''


class StableDigits:
    def __init__(self):
        self.reset()
    def reset(self):
        self.candidate = ''; self.count = 0; self.last_copied = ''; self.absent = 0
    def observe(self, digits):
        if not digits:
            self.absent += 1; self.candidate = ''; self.count = 0
            if self.absent>=3:
                self.last_copied = ''
            return False
        self.absent = 0
        if digits==self.candidate:
            self.count += 1
        else:
            self.candidate = digits; self.count = 1
        return self.count>=2 and digits!=self.last_copied
    def copied(self, digits):
        self.last_copied = digits


class UmiService:
    def __init__(self, root=ROOT):
        self.folder = Path(root)/'third_party'/'Umi-OCR'
        self.process = None
        self.started = False
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def launch(self, arguments):
        # PyInstaller's DLL directory is inherited by children. Umi ships its own
        # Python 3.8 runtime, so it must not resolve our Python 3.12 DLLs.
        environment = os.environ.copy()
        frozen = getattr(sys, 'frozen', False)
        previous = None
        if frozen:
            bundle = Path(sys._MEIPASS).resolve()
            environment['PATH'] = os.pathsep.join(
                entry for entry in environment.get('PATH', '').split(os.pathsep)
                if entry and not Path(entry).resolve().is_relative_to(bundle))
            for key in list(environment):
                if key.startswith('_PYI_'):
                    del environment[key]
            kernel32.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
            kernel32.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
            buffer = ctypes.create_unicode_buffer(32768)
            kernel32.GetDllDirectoryW(len(buffer), buffer)
            previous = buffer.value or None
            if not kernel32.SetDllDirectoryW(None):
                raise ctypes.WinError(ctypes.get_last_error())
        try:
            return subprocess.Popen([str(self.folder/'Umi-OCR.exe'), *arguments],
                                    cwd=self.folder, env=environment,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        finally:
            if frozen and not kernel32.SetDllDirectoryW(previous):
                raise ctypes.WinError(ctypes.get_last_error())
    def request(self, route, payload=None, timeout=30):
        body = None if payload is None else json.dumps(payload).encode('utf-8')
        request = urllib.request.Request(URL+route,data=body,headers={'Content-Type':'application/json'})
        with self.opener.open(request,timeout=timeout) as response:
            return json.load(response)
    def start(self,cancel_event=None):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError('已取消引擎启动')
        exe = self.folder/'Umi-OCR.exe'
        if not exe.is_file():
            raise FileNotFoundError(f'缺少识别引擎：{exe}。请完整解压程序包。')
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',PORT))==0:
                raise RuntimeError(f'识别端口 {PORT} 已被占用，请先退出已有的数字随手贴')
        data = self.folder/'UmiOCR-data'
        pre = data/'.pre_settings'
        configs = {'i18n':'zh_CN','opengl':'AA_UseSoftwareOpenGL','server_port':PORT,'last_pid':-1,'last_ptime':-1}
        pre.write_text(json.dumps(configs),encoding='utf-8')
        settings = data/'.settings'
        parser = configparser.ConfigParser(interpolation=None); parser.optionxform = str
        if settings.exists():
            parser.read(settings,encoding='utf-8-sig')
        if 'Global' not in parser:
            parser['Global'] = {}
        parser['Global']['server.enable'] = 'true'; parser['Global']['server.host'] = '127.0.0.1'
        parser['Global']['window.startupInvisible'] = 'true'
        with settings.open('w',encoding='utf-8') as handle:
            parser.write(handle)
        self.process = self.launch(['--hide'])
        self.started = True
        deadline = time.monotonic()+40
        while time.monotonic()<deadline:
            with socket.socket() as probe:
                running = probe.connect_ex(('127.0.0.1',PORT))==0
            if running:
                options = self.request('/api/ocr/get_options',timeout=5)
                if not isinstance(options,dict) or 'data.format' not in options:
                    raise RuntimeError('识别引擎返回了意外接口，请查看 Umi-OCR 日志')
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError('已取消引擎启动')
                return
            if self.process.poll() is not None and self.process.returncode!=0:
                raise RuntimeError(f'Umi-OCR 启动退出码：{self.process.returncode}')
            time.sleep(.25)
        raise TimeoutError('Umi-OCR 启动超时，请查看 third_party/Umi-OCR/UmiOCR-data/logs')
    def recognize(self,image):
        image = ImageOps.expand(image.convert('RGB'),border=10,fill='white')
        scale = min(3, max(1, 150/image.height), 1800/image.width)
        if scale!=1:
            image = image.resize((round(image.width*scale),round(image.height*scale)),Image.Resampling.LANCZOS)
        buffer = io.BytesIO(); image.save(buffer,format='PNG')
        result = self.request('/api/ocr',{'base64':base64.b64encode(buffer.getvalue()).decode('ascii'),'options':{'tbpu.parser':'none','data.format':'dict'}})
        if result.get('code')==101:
            return []
        if result.get('code')!=100 or not isinstance(result.get('data'),list):
            raise RuntimeError(f'Umi-OCR 识别错误：{result.get("code")} / {result.get("data")}')
        # Express boxes in the caller's image coordinates, removing OCR padding/scale.
        blocks=result['data']
        for block in blocks:
            if 'box' in block:
                block['box']=[[(point[0]/scale)-10,(point[1]/scale)-10] for point in block['box']]
        return blocks
    def stop(self):
        if not self.started:
            return
        try:
            command = self.launch(['--quit'])
            try:
                code = command.wait(timeout=5)
            except subprocess.TimeoutExpired:
                command.kill(); command.wait()
                raise
            if code:
                raise RuntimeError(f'Umi-OCR 关闭退出码：{code}')
            # The --quit launcher exits before the service finishes shutting down.
            deadline = time.monotonic()+5
            while time.monotonic()<deadline:
                with socket.socket() as probe:
                    running = probe.connect_ex(('127.0.0.1',PORT))==0
                if not running:
                    return
                time.sleep(.1)
            raise TimeoutError('Umi-OCR 服务关闭超时')
        finally:
            self.started = False


class WindowCapture:
    def __init__(self, hwnd, on_error=None):
        if not user32.IsWindow(hwnd):
            raise ValueError('所选窗口已关闭，请刷新窗口列表')
        if user32.IsIconic(hwnd):
            raise ValueError('所选窗口已最小化，请先还原窗口')
        self.hwnd = hwnd; self.lock = threading.Lock(); self.latest = None
        self.closed = False; self.error = ''; self.last_update = 0; self.capture = None; self.control = None
        self.on_error = on_error
    def start(self):
        self.capture = WindowsCapture(window_hwnd=self.hwnd,cursor_capture=None,draw_border=None,minimum_update_interval=None)
        @self.capture.event
        def on_frame_arrived(frame, control):
            try:
                now = time.monotonic()
                if now-self.last_update<.10:
                    return
                rgb = frame.frame_buffer[:,:,:3][:,:,::-1].copy()
                image = Image.fromarray(rgb)
                with self.lock:
                    self.latest = (image,now)
                self.last_update = now
            except Exception as error:
                self.error = str(error); control.stop()
                if self.on_error:
                    self.on_error(self.error)
        @self.capture.event
        def on_closed():
            self.closed = True
        self.control = self.capture.start_free_threaded()
    def snapshot(self):
        if not user32.IsWindow(self.hwnd) or self.closed:
            raise RuntimeError('目标窗口已关闭，捕获已结束')
        if self.error:
            raise RuntimeError(self.error)
        if self.control and self.control.is_finished() and not self.closed:
            self.control.wait()
            raise RuntimeError('窗口捕获进程已结束')
        if user32.IsIconic(self.hwnd):
            raise RuntimeError('目标窗口已最小化，请还原后重新开始识别')
        with self.lock:
            if self.latest is None:
                return None
            return self.latest[0].copy(),self.latest[1]
    def stop(self):
        if self.control and not self.control.is_finished():
            self.control.stop()


def crop_region(image,region):
    x,y,w,h = region
    left = max(0,round(x*image.width)); top = max(0,round(y*image.height))
    right = min(image.width,round((x+w)*image.width)); bottom = min(image.height,round((y+h)*image.height))
    if right-left<8 or bottom-top<8:
        raise ValueError('识别区域太小，请重新框选')
    return image.crop((left,top,right,bottom))
