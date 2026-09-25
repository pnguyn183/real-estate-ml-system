"""Scoped runtime helpers for finite, local research jobs."""
from contextlib import contextmanager
import os


@contextmanager
def keep_system_awake():
    """Prevent idle system sleep only while this thread owns the request.

    Does not change the power plan, keep the display on, or prevent a user
    explicitly suspending/shutting down the machine. Windows releases the
    request when the owning process exits, including abnormal termination.
    """
    setter = None
    if os.name == "nt":
        import ctypes
        setter = ctypes.windll.kernel32.SetThreadExecutionState
        setter.argtypes = [ctypes.c_uint]
        setter.restype = ctypes.c_uint
        if not setter(0x80000001):  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
            raise OSError("Windows did not accept the experiment's temporary sleep request")
    try:
        yield
    finally:
        if setter is not None:
            setter(0x80000000)  # release this thread's request
