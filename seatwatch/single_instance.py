"""Windows session-local mutex; releases automatically on process exit."""
import ctypes


class SingleInstance:
    def __init__(self, name):
        self.name=name
        self.handle=None

    def __enter__(self):
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p]
        kernel.CreateMutexW.restype=ctypes.c_void_p
        self.kernel=kernel
        ctypes.set_last_error(0)
        self.handle=kernel.CreateMutexW(None,False,'Local\\'+self.name)
        error=ctypes.get_last_error()
        if not self.handle:
            raise ctypes.WinError(error)
        if error==183:
            self.__exit__()
            raise RuntimeError('프로그램이 이미 실행 중입니다.')
        return self

    def __exit__(self,*args):
        if self.handle:
            self.kernel.CloseHandle.argtypes=[ctypes.c_void_p]
            self.kernel.CloseHandle(self.handle)
            self.handle=None
