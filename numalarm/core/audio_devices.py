"""默认音频设备读取（Windows Core Audio，零依赖 ctypes 实现）。

供 doctor 检查「虚拟声卡抢占默认扬声器 / 默认录音未指向 CABLE Output」使用。
任何 COM 调用失败都静默返回 (None, None)，不影响 doctor 其余检查项。

COM 调用要点：对象指针的第一成员是虚表指针，调用方法需要「双重解引用」——
先读出虚表地址，再按虚表结构索引成员并包装为函数指针。
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, Structure, byref, c_long, c_ulong, c_uint32, c_void_p, c_wchar_p
from ctypes import WINFUNCTYPE
from typing import Optional, Tuple

_CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
_IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
_PKEY_Device_FriendlyName_FMTID = "{A45C254E-DF1C-4EFD-8020-67D146A850E0}"

E_RENDER = 0    # 播放（扬声器）
E_CAPTURE = 1   # 录制（麦克风）
E_CONSOLE = 0   # 角色：控制台（默认设备）
STGM_READ = 0
VT_LPWSTR = 31


class _GUID(Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class _PROPERTYKEY(Structure):
    _fields_ = [("fmtid", _GUID), ("pid", c_ulong)]


class _PROPVARIANT(Structure):
    class _U(Structure):
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort),
                    ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort), ("p", c_void_p)]

    _anonymous_ = ("u",)
    _fields_ = [("u", _U)]


class _ComVtbl(Structure):
    """通用 COM 虚表（前 8 个槽位，覆盖本模块用到的所有方法）。"""
    _fields_ = [(f"f{i}", c_void_p) for i in range(8)]


def _guid(text: str) -> _GUID:
    g = _GUID()
    ctypes.windll.ole32.CLSIDFromString(c_wchar_p(text), byref(g))
    return g


def _vtable_of(obj_p: c_void_p) -> _ComVtbl:
    """对象指针 -> 虚表结构（双重解引用）。"""
    vtable_addr = ctypes.cast(obj_p, POINTER(c_void_p)).contents.value
    return _ComVtbl.from_address(vtable_addr)


_PKEY_Device_FriendlyName = _PROPERTYKEY(fmtid=_guid(_PKEY_Device_FriendlyName_FMTID), pid=14)

_ReleaseProto = WINFUNCTYPE(c_ulong, c_void_p)
_GetDefaultAudioEndpointProto = WINFUNCTYPE(c_long, c_void_p, c_uint32, c_uint32, POINTER(c_void_p))
_OpenPropertyStoreProto = WINFUNCTYPE(c_long, c_void_p, c_uint32, POINTER(c_void_p))
_GetValueProto = WINFUNCTYPE(c_long, c_void_p, POINTER(_PROPERTYKEY), POINTER(_PROPVARIANT))


def _friendly_name(device_p: c_void_p) -> Optional[str]:
    """读取设备友好名称；失败返回 None。"""
    store_p = c_void_p()
    hr = _OpenPropertyStoreProto(_vtable_of(device_p).f4)(device_p, STGM_READ, byref(store_p))
    if hr != 0 or not store_p.value:
        return None
    try:
        pv = _PROPVARIANT()
        hr = _GetValueProto(_vtable_of(store_p).f5)(store_p, byref(_PKEY_Device_FriendlyName), byref(pv))
        if hr != 0 or pv.vt != VT_LPWSTR or not pv.p:
            return None
        return ctypes.wstring_at(pv.p)
    finally:
        ctypes.windll.ole32.PropVariantClear(byref(pv))
        try:
            _ReleaseProto(_vtable_of(store_p).f2)(store_p)
        except Exception:  # noqa: BLE001 清理失败不影响主流程
            pass


def get_default_devices() -> Tuple[Optional[str], Optional[str]]:
    """返回（默认播放设备名, 默认录音设备名）；任一失败对应项为 None。

    典型返回（安装虚拟声卡后）：("扬声器 (2- Realtek(R) Audio)",
    "CABLE Output (VB-Audio Virtual Cable)")。
    """
    initialized = False
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
        initialized = True
    except Exception:  # noqa: BLE001
        pass
    try:
        enum_p = c_void_p()
        hr = ctypes.windll.ole32.CoCreateInstance(
            byref(_guid(_CLSID_MMDeviceEnumerator)), None, 1,  # CLSCTX_ALL
            byref(_guid(_IID_IMMDeviceEnumerator)), byref(enum_p))
        if hr != 0 or not enum_p.value:
            return None, None
        try:
            get_default = _GetDefaultAudioEndpointProto(_vtable_of(enum_p).f4)
            release_enum = _ReleaseProto(_vtable_of(enum_p).f2)
            results: list = []
            for data_flow in (E_RENDER, E_CAPTURE):
                device_p = c_void_p()
                hr = get_default(enum_p, data_flow, E_CONSOLE, byref(device_p))
                if hr != 0 or not device_p.value:
                    results.append(None)
                    continue
                try:
                    results.append(_friendly_name(device_p))
                finally:
                    try:
                        _ReleaseProto(_vtable_of(device_p).f2)(device_p)
                    except Exception:  # noqa: BLE001
                        pass
            playback, capture = results
            return playback, capture
        finally:
            try:
                release_enum(enum_p)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 COM 环境异常时静默降级
        return None, None
    finally:
        if initialized:
            ctypes.windll.ole32.CoUninitialize()
