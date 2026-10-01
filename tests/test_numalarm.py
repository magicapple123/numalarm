# numalarm 单元测试（纯逻辑，不依赖 QQ / 桌面环境，可跨平台运行）
import ctypes

import pytest

from numalarm.common.config import ConfigManager, NumAlarmConfig
from numalarm.common.exceptions import (
    CODE_BUSY,
    CODE_CALL_TIMEOUT,
    CODE_PARAM_ERROR,
    CODE_SUCCESS,
    BusyError,
    CallTimeoutError,
    ParameterError,
)
from numalarm.core.call_executor import CallExecutor, DebounceStore
from numalarm.core import state_detector


# ---------------- 配置 ----------------
def test_config_defaults():
    cfg = NumAlarmConfig()
    assert cfg.server.port == 18600
    assert cfg.qq_process_name == "QQ.exe"
    assert cfg.retry.max_attempts == 0  # 不限次
    assert cfg.retry.ring_seconds == 45
    assert cfg.presence.idle_seconds == 180
    assert cfg.debounce.window_seconds == 300


def test_example_config_parses(tmp_path, monkeypatch):
    import yaml
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "config.example.yaml"
    cfg = NumAlarmConfig(**yaml.safe_load(example.read_text(encoding="utf-8")))
    assert cfg.hotkey.wake_panel == "ctrl+alt+z"
    assert cfg.search_method in ("click", "hotkey")


def test_resolve_target_alias():
    mgr = ConfigManager(config_path=None)
    mgr.config = NumAlarmConfig(default_target="张三", target_alias={"老板": "张三"})
    assert mgr.resolve_target(None) == "张三"
    assert mgr.resolve_target("老板") == "张三"
    assert mgr.resolve_target("李四") == "李四"


def test_resolve_target_empty_raises():
    mgr = ConfigManager(config_path=None)
    mgr.config = NumAlarmConfig()
    with pytest.raises(ParameterError):
        mgr.resolve_target(None)


# ---------------- 防抖 ----------------
def test_debounce_store(tmp_path):
    store = DebounceStore(tmp_path / "state.json")
    assert store.check("张三", 300) is None  # 无记录
    store.mark("张三")
    remain = store.check("张三", 300)
    assert remain is not None and 0 < remain <= 300
    assert store.check("李四", 300) is None  # 其他目标不受影响


def test_debounce_window_expired(tmp_path):
    import time

    store = DebounceStore(tmp_path / "state.json")
    store.mark("张三")
    time.sleep(0.05)
    assert store.check("张三", 0.01) is None  # 窗口极小 => 已过期


# ---------------- 异常与状态码 ----------------
def test_status_codes():
    assert BusyError().code == CODE_BUSY
    assert CallTimeoutError().code == CODE_CALL_TIMEOUT
    assert ParameterError().code == CODE_PARAM_ERROR
    assert ParameterError().code != CODE_SUCCESS


def test_to_result_shape():
    err = CallTimeoutError("超时", data={"attempts": 2})
    result = err.to_result()
    assert result["code"] == CODE_CALL_TIMEOUT
    assert result["message"] == "超时"
    assert result["data"] == {"attempts": 2}


# ---------------- 用户在位检测 ----------------
def test_call_skipped_when_user_active(monkeypatch):
    """auto 触发 + 用户正在电脑前 => 静默跳过，不产生真实拨打。"""
    monkeypatch.setattr(state_detector, "get_idle_seconds", lambda: 5.0)


    class _StubMutex:
        @classmethod
        def instance(cls):
            return _StubMutex()

        def acquire(self, quiet: bool = False) -> bool:
            return True

        def release(self) -> None:
            pass

    class _StubDebounce:
        def check(self, target, window_seconds):
            return None

        def mark(self, target):
            pass

    ex = CallExecutor.__new__(CallExecutor)  # 跳过 __init__（不构造 QQController）
    mgr = ConfigManager(config_path=None)
    mgr.config = NumAlarmConfig(default_target="张三", presence={"enabled": True, "idle_seconds": 180})
    ex.manager = mgr
    ex.config = mgr.config
    ex.mutex = _StubMutex()
    ex.debounce = _StubDebounce()

    result = ex.call(auto=True)
    assert result["code"] == 200
    assert result["data"]["skipped"] is True
    assert result["data"]["attempts"] == 0


def test_idle_seconds_non_windows_returns_negative():
    """非 Windows 平台（无 windll）get_idle_seconds 应返回 -1，调用方视为不启用检测。"""
    if not hasattr(ctypes, "windll"):
        assert state_detector.get_idle_seconds() == -1.0
    else:
        # Windows 上仅要求返回非负数值（当前会话必然有输入时间戳）
        assert state_detector.get_idle_seconds() >= 0.0
