"""Finds the graphics processors in this computer (names, vendor / device ids, integrated or not).

Windows: asks DXGI through `ctypes` (no extra package, no subprocess, does not wake a sleeping
discrete GPU). Other systems return an empty list: the Settings page then only offers
"None" and "Automatic", because Qt gives no way to pick a GPU for video decoding there.

    from npigallery.gui.common import gpuinfo
    for gpu in gpuinfo.detect():
        print(gpu.key, gpu.name, gpu.integrated)
"""

import ctypes
import re
import sys
import uuid
from ctypes import c_int32, c_long, c_size_t, c_uint, c_uint32, c_void_p
from dataclasses import dataclass
from functools import lru_cache

VENDOR_INTEL = 0x8086
VENDOR_AMD = 0x1002
VENDOR_MICROSOFT = 0x1414  # "Microsoft Basic Render Driver" (software)
_SOFTWARE_FLAG = 2  # DXGI_ADAPTER_FLAG_SOFTWARE
_INTEGRATED_VRAM_LIMIT = 1 << 30  # single-GPU guess only: Intel / AMD with under 1 GiB of own memory


@dataclass(frozen=True)
class Gpu:
    key: str  # stable id kept in the preferences: "gpu:8086:9a49"
    name: str
    vendor_id: int
    device_id: int
    video_memory: int  # bytes of dedicated memory (small for integrated GPUs)
    integrated: bool


def display_name(gpu: Gpu, several: bool = False) -> str:
    """'NVIDIA GeForce RTX 5070 / RX 9070 XT', plus '(integrated)' / '(dedicated)' when there is more than one GPU."""
    name = re.sub(r"\((?:R|TM|r|tm)\)", "", gpu.name)
    name = re.sub(r"\s{2,}", " ", name).strip()
    if several:
        name += " (integrated)" if gpu.integrated else " (dedicated)"
    return name


def make_key(vendor_id: int, device_id: int) -> str:
    return f"gpu:{vendor_id:04x}:{device_id:04x}"


@lru_cache(maxsize=1)
def detect() -> tuple[Gpu, ...]:
    """The GPUs of this computer, best first. Cached; call `refresh()` to look again."""
    if sys.platform != "win32":
        return ()
    try:
        return tuple(_detect_windows())
    except Exception:  # a broken driver must never stop the program from starting
        return ()


def refresh() -> tuple[Gpu, ...]:
    detect.cache_clear()
    return detect()


# // Windows / DXGI


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _GUID:
    u = uuid.UUID(text)
    return _GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))


class _AdapterDesc1(ctypes.Structure):
    _fields_ = [
        ("Description", ctypes.c_wchar * 128),
        ("VendorId", c_uint),
        ("DeviceId", c_uint),
        ("SubSysId", c_uint),
        ("Revision", c_uint),
        ("DedicatedVideoMemory", c_size_t),
        ("DedicatedSystemMemory", c_size_t),
        ("SharedSystemMemory", c_size_t),
        ("LuidLow", c_uint32),
        ("LuidHigh", c_int32),
        ("Flags", c_uint),
    ]


def _method(iface: int, index: int, restype, *argtypes):
    """Bind method number `index` of a COM interface pointer (its vtable slot)."""
    vtable = ctypes.cast(iface, ctypes.POINTER(ctypes.POINTER(c_void_p)))[0]
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtable[index])


def _release(iface: int) -> None:
    if iface:
        _method(iface, 2, ctypes.c_ulong)(iface)


def _adapter_desc(adapter: int) -> _AdapterDesc1:
    desc = _AdapterDesc1()
    _method(adapter, 10, c_long, ctypes.POINTER(_AdapterDesc1))(adapter, ctypes.byref(desc))  # GetDesc1
    return desc


def _power_saving_luid(factory: int):
    """LUID of the adapter Windows treats as 'minimum power' (the integrated GPU on a hybrid system)."""
    iid_factory6 = _guid("c1b6694f-ff09-44a9-b03c-77900a0a1d17")
    iid_adapter1 = _guid("29038f61-3839-4626-91fd-086879011a05")
    factory6 = c_void_p()
    query = _method(factory, 0, c_long, ctypes.POINTER(_GUID), ctypes.POINTER(c_void_p))
    if query(factory, ctypes.byref(iid_factory6), ctypes.byref(factory6)) != 0 or not factory6.value:
        return None
    try:
        adapter = c_void_p()
        enum = _method(
            factory6.value, 29, c_long, c_uint, c_int32, ctypes.POINTER(_GUID), ctypes.POINTER(c_void_p)
        )  # EnumAdapterByGpuPreference(0, DXGI_GPU_PREFERENCE_MINIMUM_POWER, ...)
        if enum(factory6.value, 0, 1, ctypes.byref(iid_adapter1), ctypes.byref(adapter)) != 0 or not adapter.value:
            return None
        try:
            desc = _adapter_desc(adapter.value)
            return (desc.LuidLow, desc.LuidHigh)
        finally:
            _release(adapter.value)
    finally:
        _release(factory6.value)


def _detect_windows() -> list[Gpu]:
    dxgi = ctypes.WinDLL("dxgi")
    iid_factory1 = _guid("770aae78-f26f-4dba-a829-253c83d1b387")
    factory = c_void_p()
    if dxgi.CreateDXGIFactory1(ctypes.byref(iid_factory1), ctypes.byref(factory)) != 0 or not factory.value:
        return []
    found: list[tuple[_AdapterDesc1, tuple[int, int]]] = []
    try:
        enum = _method(factory.value, 12, c_long, c_uint, ctypes.POINTER(c_void_p))  # EnumAdapters1
        index = 0
        while True:
            adapter = c_void_p()
            if enum(factory.value, index, ctypes.byref(adapter)) != 0 or not adapter.value:
                break  # DXGI_ERROR_NOT_FOUND: no more adapters
            index += 1
            try:
                desc = _adapter_desc(adapter.value)
            finally:
                _release(adapter.value)
            if desc.Flags & _SOFTWARE_FLAG or desc.VendorId == VENDOR_MICROSOFT:
                continue
            found.append((desc, (desc.LuidLow, desc.LuidHigh)))
        power_saving = _power_saving_luid(factory.value) if len(found) > 1 else None
    finally:
        _release(factory.value)

    gpus: list[Gpu] = []
    seen: set[str] = set()
    for desc, luid in found:
        key = make_key(desc.VendorId, desc.DeviceId)
        if key in seen:  # two identical cards share ids: the first one stands for both
            continue
        seen.add(key)
        if len(found) > 1 and power_saving is not None:
            integrated = luid == power_saving
        else:
            integrated = desc.VendorId in (VENDOR_INTEL, VENDOR_AMD) and desc.DedicatedVideoMemory < _INTEGRATED_VRAM_LIMIT
        gpus.append(Gpu(key, desc.Description.strip(), desc.VendorId, desc.DeviceId, int(desc.DedicatedVideoMemory), integrated))
    gpus.sort(key=lambda g: (g.integrated, -g.video_memory))  # dedicated first
    return gpus