import os
import sys

# Standard flock constants
LOCK_SH = 1  # Shared lock
LOCK_EX = 2  # Exclusive lock
LOCK_UN = 8  # Unlock

if sys.platform == 'win32':
    import msvcrt
    import ctypes
    import ctypes.wintypes as wintypes

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ('Internal', wintypes.ULONG),
            ('InternalHigh', wintypes.ULONG),
            ('Offset', wintypes.DWORD),
            ('OffsetHigh', wintypes.DWORD),
            ('hEvent', wintypes.HANDLE),
        ]

    # Windows API Constants
    LOCKFILE_EXCLUSIVE_LOCK = 0x00000002

    kernel32 = ctypes.windll.kernel32

    def flock(fd, operation):
        """Windows implementation of flock using native Win32 LockFileEx APIs."""
        try:
            handle = msvcrt.get_osfhandle(fd)
        except ValueError as e:
            raise OSError(f"Invalid file descriptor: {e}")

        overlapped = OVERLAPPED()

        if operation & LOCK_UN:
            ret = kernel32.UnlockFileEx(
                handle,
                0,             # reserved
                0xFFFFFFFF,    # low-order 32 bits of length
                0xFFFFFFFF,    # high-order 32 bits of length
                ctypes.byref(overlapped)
            )
        else:
            flags = 0
            if operation & LOCK_EX:
                flags |= LOCKFILE_EXCLUSIVE_LOCK
            
            # Shared lock corresponds to flags = 0
            ret = kernel32.LockFileEx(
                handle,
                flags,
                0,             # reserved
                0xFFFFFFFF,    # low-order 32 bits of length
                0xFFFFFFFF,    # high-order 32 bits of length
                ctypes.byref(overlapped)
            )

        if not ret:
            raise ctypes.WinError()

else:
    import fcntl

    def flock(fd, operation):
        """POSIX implementation mapping straight to fcntl.flock."""
        op_map = 0
        if operation & LOCK_SH:
            op_map |= fcntl.LOCK_SH
        if operation & LOCK_EX:
            op_map |= fcntl.LOCK_EX
        if operation & LOCK_UN:
            op_map |= fcntl.LOCK_UN

        fcntl.flock(fd, op_map)
