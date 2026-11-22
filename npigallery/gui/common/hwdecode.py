"""Hardware (GPU) video decoding for the viewer.

QtMultimedia's FFmpeg backend decodes on the GPU when it is told which device type to use. This
module picks that type *before* the backend starts, so call `configure()` once at startup, before
`QApplication` is created (the backend reads its environment when it first plays something).

The choice comes from Settings > Video (preference `video_hwaccel`):

    auto            Direct3D 11 on Windows (Intel, AMD and NVIDIA all decode through it),
                    VAAPI on Linux, VideoToolbox on macOS.
    none            decode on the CPU.
    gpu:<ven>:<dev> (Windows) run the program on that GPU, e.g. "gpu:10de:2206" = an RTX 3080.
                    Windows cannot hand a single decoder a GPU, so this writes the same per-app
                    "Graphics settings" entry as Settings > System > Display > Graphics (power saving
                    = integrated GPU, high performance = dedicated GPU). It needs a restart.

Environment overrides, handy for experiments (they win over the Settings choice):

    NPI_HWDECODE    auto | d3d11va | dxva2 | cuda | vaapi | videotoolbox | none
    NPI_GPU         default | intel | nvidia      (Windows only)
    NPI_HWDECODE_LOG=1   prints what the backend decides (which decoder it opens, or why it falls back).

A variable that is already set in the environment is never overwritten.
"""

import os
import sys

from ...config import APP_NAME
from . import gpuinfo

AUTO = "auto"
NONE = "none"
SELECTION_KEY = "pref/video_hwaccel"  # same QSettings entry as Preferences("video_hwaccel")
_OWNED_KEY = "hw/gpu_registry_owned"  # we wrote the Windows graphics entry, so we may remove it again

DEVICE_BY_PLATFORM = {"win32": "d3d11va", "linux": "vaapi", "darwin": "videotoolbox"}
VALID = ("d3d11va", "dxva2", "cuda", "vaapi", "videotoolbox", "none")
GPU_PREFERENCE = {"default": 0, "intel": 1, "nvidia": 2}  # 1 = power saving (iGPU), 2 = high performance (dGPU)
_REGISTRY_KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"

_started_with: str | None = None


# ---- the saved choice -----


def _settings():
    from PySide6.QtCore import QSettings

    return QSettings(APP_NAME, APP_NAME)


def saved_selection() -> str:
    """The choice stored in the preferences ('auto' when there is none)."""
    try:
        return str(_settings().value(SELECTION_KEY, AUTO, str) or AUTO)
    except Exception:
        return AUTO


def started_with() -> str:
    """The choice this run started with; Settings compares it to show 'restart to apply'."""
    return _started_with if _started_with is not None else saved_selection()


def _default_device() -> str:
    return DEVICE_BY_PLATFORM.get(sys.platform, "none")


def _gpu_preference_for(gpu: "gpuinfo.Gpu", gpus) -> int:
    if len(gpus) < 2:
        return 0  # one GPU: nothing to choose
    return GPU_PREFERENCE["intel"] if gpu.integrated else GPU_PREFERENCE["nvidia"]


def resolve(selection: str) -> tuple[str, int]:
    """(decoder device type, Windows GPU preference 0/1/2) for a saved choice."""
    if selection == NONE:
        return "none", 0
    if selection.startswith("gpu:"):
        gpus = gpuinfo.detect()
        gpu = next((g for g in gpus if g.key == selection), None)
        if gpu is not None:
            return _default_device(), _gpu_preference_for(gpu, gpus)
    return _default_device(), 0  # auto, or a GPU that is no longer there


# ---- Windows per-app GPU preference ----


def apply_gpu_preference(value: int) -> bool:
    """Windows: 1 / 2 write the entry for this executable, 0 removes it (only if we wrote it)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        exe = os.path.abspath(sys.executable)
        settings = _settings()
        if value in (1, 2):
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REGISTRY_KEY) as key:
                winreg.SetValueEx(key, exe, 0, winreg.REG_SZ, f"GpuPreference={value};")
            settings.setValue(_OWNED_KEY, True)
        elif settings.value(_OWNED_KEY, False, bool):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_KEY, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, exe)
            except FileNotFoundError:
                pass
            settings.setValue(_OWNED_KEY, False)
    except OSError:
        return False
    return True


def set_gpu_preference(kind: str) -> bool:
    """NPI_GPU=default|intel|nvidia. True when it was applied."""
    value = GPU_PREFERENCE.get(kind.strip().lower())
    return value is not None and apply_gpu_preference(value)


def apply_selection(selection: str) -> None:
    """Called by Settings when the choice changes: the Windows entry is written right away so the
    next start already runs on that GPU (the decoder type is read from the preferences at start)."""
    apply_gpu_preference(resolve(selection)[1])


def restart_arguments() -> list[str]:
    """Arguments for `sys.executable` that start this program again the way it was started."""
    if getattr(sys, "frozen", False):
        return sys.argv[1:]
    return list(getattr(sys, "orig_argv", [sys.executable] + sys.argv)[1:])


# ---- startup -----


def chosen_device() -> str:
    """The decoder device type that will be requested ('none' = CPU)."""
    wanted = os.environ.get("NPI_HWDECODE", "").strip().lower()
    if wanted in VALID:
        return wanted
    if wanted == AUTO:
        return _default_device()
    return resolve(saved_selection())[0]


def configure() -> str:
    """Choose the GPU decoder; returns the device type that was requested."""
    global _started_with
    _started_with = saved_selection()
    device = chosen_device()
    if "QT_FFMPEG_DECODING_HW_DEVICE_TYPES" not in os.environ:
        # Qt only parses a list of known names, so an unknown one ("none") switches every GPU decoder off.
        os.environ["QT_FFMPEG_DECODING_HW_DEVICE_TYPES"] = device
    gpu = os.environ.get("NPI_GPU", "").strip().lower()
    if gpu:
        if not set_gpu_preference(gpu):
            print(f"[npigallery] NPI_GPU={gpu!r} was not applied (Windows only: default / intel / nvidia)", file=sys.stderr)
    else:
        apply_gpu_preference(resolve(_started_with)[1])  # idempotent; makes sure the entry matches the choice
    if os.environ.get("NPI_HWDECODE_LOG") == "1":
        rules = os.environ.get("QT_LOGGING_RULES", "")
        os.environ["QT_LOGGING_RULES"] = (rules + ";" if rules else "") + "qt.multimedia.ffmpeg*=true"
        print(f"[npigallery] hardware decoding: {device} (choice: {_started_with})", file=sys.stderr)
    return device