"""Worker identity and liveness, independent of the last status timestamp."""
import ctypes
from ctypes import wintypes
import json
import socket
from .session import ROOT


class BrowserClosed(RuntimeError):
    pass


def process_stamp(pid):
    """PID plus Windows creation time prevents reuse of an unrelated process."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def read_status():
    try:
        return json.loads((ROOT/'work'/'login-status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def worker_alive(row):
    stamp = row.get('process_stamp')
    return bool(stamp and row.get('worker_id') and process_stamp(row.get('pid')) == stamp)


def port_open(port=9335):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.3):
            return True
    except OSError:
        return False


def worker_connected(row):
    return row.get('state') not in (None, 'STARTING', 'CLOSED') and worker_alive(row) and port_open(row.get('port',9335))
