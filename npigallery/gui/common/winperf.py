"""Windows timing tweaks for smooth video."""

import os
import sys

_PROCESS_POWER_THROTTLING = 4  # PROCESS_INFORMATION_CLASS
_EXECUTION_SPEED = 0x1  # PROCESS_POWER_THROTTLING_EXECUTION_SPEED
_IGNORE_TIMER_RESOLUTION = 0x4  # PROCESS_POWER_THROTTLING_IGNORE_TIMER_RESOLUTION (Windows 11)


def apply() -> bool:
    """Request 1 ms timers and no power throttling. True when the timer request succeeded."""
    if sys.platform != "win32" or os.environ.get("NPI_TIMER") == "0":
        return False
    import ctypes
    from ctypes import wintypes

    ok = False
    try:
        ok = ctypes.windll.winmm.timeBeginPeriod(1) == 0  # TIMERR_NOERROR
    except (AttributeError, OSError):
        pass

    class _ThrottlingState(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]

    try:
        # ControlMask says "I decide these", StateMask 0 says "do not throttle / honour my timer request".
        state = _ThrottlingState(1, _EXECUTION_SPEED | _IGNORE_TIMER_RESOLUTION, 0)
        kernel32 = ctypes.windll.kernel32
        kernel32.SetProcessInformation(
            kernel32.GetCurrentProcess(), _PROCESS_POWER_THROTTLING, ctypes.byref(state), ctypes.sizeof(state)
        )
    except (AttributeError, OSError):
        pass
    return ok
