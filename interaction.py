"""Reuse pynput for click monitoring and foreground-only Ctrl+A / Ctrl+V."""
from __future__ import annotations
import ctypes
import time
from ctypes import wintypes
from pynput import keyboard, mouse
from pynput.keyboard import _win32 as keyboard_win32
from core import user32, write_clipboard

class POINT(ctypes.Structure):
    _fields_ = [('x',ctypes.c_long),('y',ctypes.c_long)]
class RECT(ctypes.Structure):
    _fields_ = [('left',ctypes.c_long),('top',ctypes.c_long),('right',ctypes.c_long),('bottom',ctypes.c_long)]
user32.WindowFromPoint.argtypes = [POINT]; user32.WindowFromPoint.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND,ctypes.POINTER(RECT)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
dwmapi = ctypes.WinDLL('dwmapi')
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD]
dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

def frame_rect(hwnd):
    # WGC uses painted DWM bounds; GetWindowRect also includes invisible resize borders.
    rect = RECT()
    result = dwmapi.DwmGetWindowAttribute(hwnd,9,ctypes.byref(rect),ctypes.sizeof(rect))
    if result:
        raise OSError(f'DWM窗口边界读取失败：0x{result & 0xffffffff:08X}')
    return rect

def root_at(x,y):
    hwnd = user32.WindowFromPoint(POINT(round(x),round(y)))
    return int(user32.GetAncestor(hwnd,2)) if hwnd else 0

def foreground_is(hwnd):
    foreground = user32.GetForegroundWindow()
    return bool(foreground and user32.GetAncestor(foreground,2)==hwnd)

def hit_input(hwnd,x,y,region,size):
    if region is None or size is None or not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        return False
    rect = frame_rect(hwnd)
    width,height = rect.right-rect.left,rect.bottom-rect.top
    if width<=0 or height<=0 or abs(width-size[0])>4 or abs(height-size[1])>4:
        return False
    rx,ry,rw,rh = region
    nx,ny = (x-rect.left)/width,(y-rect.top)/height
    return rx<=nx<=rx+rw and ry<=ny<=ry+rh

OWN_INPUT_TAG=0x47503535

class InputMonitor:
    def __init__(self, on_click, on_key, on_cancel=None, on_pause=None):
        self.on_click = on_click; self.on_cancel = on_cancel or (lambda:None); self.press = None
        self.mouse = mouse.Listener(on_click=self.mouse_event)
        self.input_epoch=0;self.on_key=on_key
        self.hotkey=keyboard.HotKey(keyboard.HotKey.parse('<ctrl>+<alt>+p'),on_pause or (lambda:None))
        self.keyboard=keyboard.Listener(on_press=self.key_press,on_release=self.key_release,win32_event_filter=lambda msg,data:data.dwExtraInfo!=OWN_INPUT_TAG)
    def key_press(self,key):
        self.input_epoch+=1;self.on_key();self.hotkey.press(self.keyboard.canonical(key))
    def key_release(self,key):self.hotkey.release(self.keyboard.canonical(key))
    def mouse_event(self,x,y,button,pressed):
        if pressed:
            self.input_epoch+=1;self.on_cancel()
            self.press = (x,y,root_at(x,y)) if button==mouse.Button.left else None
        elif button==mouse.Button.left:
            start = self.press; self.press = None
            if start is None:return
            sx,sy,hwnd = start
            if (x-sx)**2+(y-sy)**2<=36 and hwnd==root_at(x,y):
                self.on_click(x,y,hwnd,sx,sy)
    def start(self):
        self.mouse.start(); self.keyboard.start()
    def check(self):
        for listener in (self.mouse,self.keyboard):
            if not listener.is_alive():
                listener.join()
                raise RuntimeError('点击或键盘监听已停止，请重启程序')
    def stop(self):
        self.press = None
        self.mouse.stop(); self.keyboard.stop()

class CheckedKeyboardController(keyboard.Controller):
    """Keep pynput's Windows key mapping but check SendInput's event count."""
    def _handle(self,key,is_press):
        packet = keyboard_win32.INPUT(type=keyboard_win32.INPUT.KEYBOARD,
            value=keyboard_win32.INPUT_union(ki=keyboard_win32.KEYBDINPUT(**key._parameters(is_press))))
        packet.value.ki.dwExtraInfo=OWN_INPUT_TAG
        count = keyboard_win32.SendInput(1,ctypes.byref(packet),ctypes.sizeof(packet))
        if count!=1:
            raise RuntimeError('Windows 未接收模拟按键（SendInput 返回 '+str(count)+'）。请核对工具与游戏的运行权限。')

class PasteController:
    def __init__(self):
        self.keyboard = CheckedKeyboardController()
    def shortcut(self,hwnd,vk):
        if not foreground_is(hwnd):
            raise RuntimeError('游戏窗口焦点已改变，已取消粘贴')
        key = keyboard.KeyCode.from_vk(vk)
        with self.keyboard.pressed(keyboard.Key.ctrl_l):
            time.sleep(.04)
            if not foreground_is(hwnd):
                raise RuntimeError('游戏窗口焦点已改变，已取消粘贴')
            self.keyboard.press(key)
            try:time.sleep(.04)
            finally:self.keyboard.release(key)
    def paste(self,hwnd,digits,owner,replace=True,guard=None):
        if not foreground_is(hwnd):
            raise RuntimeError('游戏窗口焦点已改变，已取消粘贴')
        if any(user32.GetAsyncKeyState(key)&0x8000 for key in (0x10,0x11,0x12)):
            raise RuntimeError('请松开 Shift、Ctrl、Alt 后点击输入框')
        if guard and not guard():raise RuntimeError('本次粘贴已取消：输入、画面或窗口状态已改变')
        write_clipboard(digits,owner)
        if guard and not guard():raise RuntimeError('本次粘贴已取消：输入、画面或窗口状态已改变')
        with self.keyboard.pressed(keyboard.Key.ctrl_l):
            time.sleep(.04)
            for vk in ((0x41,0x56) if replace else (0x56,)):
                if guard and not guard():raise RuntimeError('本次粘贴已取消：输入、画面或窗口状态已改变')
                if not foreground_is(hwnd):raise RuntimeError('游戏窗口焦点已改变，已取消粘贴')
                key=keyboard.KeyCode.from_vk(vk);self.keyboard.press(key)
                try:time.sleep(.04)
                finally:self.keyboard.release(key)
                if vk==0x41:time.sleep(.02)
