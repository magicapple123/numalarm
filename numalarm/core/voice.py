"""语音提醒：Windows SAPI 离线语音合成与输出设备路由。

原理：QQ 语音通话只传输「麦克风」采集的声音。将离线 TTS（Windows 自带 SAPI）
合成出的语音输出到虚拟声卡的播放端（如 VB-Cable 的 CABLE Input），并在 QQ 中
把麦克风设为虚拟声卡的录音端（CABLE Output），对方接听后即可听到固定提醒语音。

本模块不引入新依赖：SAPI 通过 pywin32（可选依赖，Windows 自动安装）调用。
"""

from __future__ import annotations

import threading
from typing import List

from numalarm.common.exceptions import NumAlarmError
from numalarm.common.logger import get_logger

logger = get_logger("voice")


def _new_voice():
    """创建 SAPI 语音合成对象（线程内需先初始化 COM）。"""
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:  # pragma: no cover
        raise NumAlarmError("缺少 pywin32，无法使用语音提醒功能；请 pip install pywin32") from exc
    pythoncom.CoInitialize()
    return win32com.client.Dispatch("SAPI.SpVoice")


def _find_device(voice, hint: str):
    """在 SAPI 音频输出设备中按名称关键字查找设备；未找到返回 None。"""
    if not hint:
        return None
    try:
        outputs = voice.GetAudioOutputs()
        for i in range(outputs.Count):
            item = outputs.Item(i)
            if hint.lower() in (item.GetDescription() or "").lower():
                return item
    except Exception as exc:  # noqa: BLE001 枚举失败按未找到处理
        logger.debug("枚举语音输出设备失败：%s", exc)
    return None


def list_output_devices() -> List[str]:
    """列出系统可用的语音合成输出设备描述（用于自检与配置提示）。"""
    voice = _new_voice()
    outputs = voice.GetAudioOutputs()
    return [outputs.Item(i).GetDescription() for i in range(outputs.Count)]


def synthesize_to_file(text: str, path: str, volume: int = 100) -> bool:
    """将文本离线合成为 wav 文件（不外放声音，用于自检与预生成）。"""
    result = {"ok": False}

    def _run() -> None:
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            voice.Volume = int(volume)
            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            stream.Format.Type = 39  # SAFT16kHz16BitMono
            stream.Open(path, 3)  # SSFMCreateForWrite
            voice.AudioOutputStream = stream
            voice.Speak(text)
            stream.Close()
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("语音合成失败：%s", exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=max(len(text) * 0.6 + 10.0, 15.0))
    return result["ok"]


def speak_to_device(text: str, device_hint: str, volume: int = 100, timeout: float = 30.0) -> bool:
    """将文本合成并输出到名称含 device_hint 的音频设备（同步阻塞，带超时保护）。

    :return: 是否完整播放成功
    """
    result = {"ok": False, "device_found": False}

    def _run() -> None:
        try:
            voice = _new_voice()
            voice.Volume = int(volume)
            device = _find_device(voice, device_hint)
            if device is None:
                logger.warning("未找到名称含「%s」的语音输出设备", device_hint)
                return
            result["device_found"] = True
            voice.AudioOutput = device
            voice.Speak(text)
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("语音播放异常：%s", exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if not result["ok"] and not result["device_found"]:
        logger.warning("语音提醒未播放：输出设备未找到或播放超时")
    return result["ok"]
