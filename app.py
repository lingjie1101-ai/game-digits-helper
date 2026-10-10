import ctypes
import sys
import tkinter as tk
from core import user32
from hub import App
from desktop_features import SingleInstance

if __name__=='__main__':
    user32.SetProcessDpiAwarenessContext.argtypes=[ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    instance=SingleInstance()
    if not instance.primary:
        instance.close();sys.exit(0)
    try:
        root=tk.Tk()
        if '--hidden' in sys.argv:root.withdraw()
        App(root,singleton=instance);root.mainloop()
    finally:instance.close()
