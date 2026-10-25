"""Restore only the native Chrome window belonging to our launched process."""
import ctypes
from ctypes import wintypes
import sys


def restore_session_window(session):
    process=getattr(session,'process',None)
    if process and process.poll() is None:
        return restore_browser_window(process.pid)
    return False


def restore_browser_window(pid):
    if sys.platform != 'win32' or not pid:
        return False
    user32=ctypes.WinDLL('user32',use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user32.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
    user32.SetForegroundWindow.argtypes=[wintypes.HWND]
    user32.BringWindowToTop.argtypes=[wintypes.HWND]
    user32.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT]
    user32.GetClassNameW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    found=[]
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    @callback_type
    def visit(hwnd,_):
        owner=wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
        if owner.value==pid:
            name=ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd,name,128)
            if name.value=='Chrome_WidgetWin_1':
                found.append(hwnd)
        return True
    user32.EnumWindows.argtypes=[callback_type,wintypes.LPARAM]
    user32.EnumWindows(visit,0)
    for hwnd in found:
        user32.ShowWindow(hwnd,9)  # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        # Foreground activation can be refused for a background worker. Raising
        # and immediately unpinning its own window restores actual visibility.
        user32.SetWindowPos(hwnd,-1,0,0,0,0,0x0001|0x0002|0x0010|0x0040)
        user32.SetWindowPos(hwnd,-2,0,0,0,0,0x0001|0x0002|0x0010|0x0040)
    return bool(found)
