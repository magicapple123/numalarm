"""配置加载与管理模块。

查找顺序：
1. 环境变量 ``NUMALARM_CONFIG`` 指定的 yaml 文件
2. 当前工作目录下的 ``config.yaml``
3. 均不存在时使用内置默认值（可通过 ``numalarm init`` 生成，或复制 config.example.yaml）

所有可调参数集中在 :class:`NumAlarmConfig`；业务代码禁止硬编码任何快捷键/时长/置信度/端口。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

from numalarm.common.exceptions import ParameterError

CONFIG_ENV = "NUMALARM_CONFIG"
CONFIG_FILENAME = "config.yaml"
EXAMPLE_FILENAME = "config.example.yaml"


# --------------------------- 配置模型 ---------------------------
class HotkeyConfig(BaseModel):
    """快捷键配置（需与 QQ 内设置的快捷键保持一致）。"""

    wake_panel: str = Field("ctrl+alt+z", description="唤起 QQ 主面板全局快捷键")
    search: str = Field("ctrl+f", description="QQ 主面板打开搜索框快捷键")


class TimingConfig(BaseModel):
    """时序参数（单位：秒）。"""

    key_interval: float = Field(0.05, description="模拟按键间隔")
    action_wait: float = Field(0.6, description="每步操作后的基础等待")
    search_wait: float = Field(2.0, description="输入关键词后等待搜索结果")
    chat_open_wait: float = Field(1.5, description="回车后等待聊天窗口打开")
    click_confirm_wait: float = Field(2.0, description="点击语音按钮后的基础确认等待")
    verify_window_timeout: float = Field(8.0, description="通话窗口出现检测超时")


class MatchConfig(BaseModel):
    """图片匹配参数。"""

    confidence: float = Field(0.8, description="匹配置信度 0-1，误匹配升高、漏匹配降低")
    max_retry: int = Field(2, description="匹配失败自动重试次数")
    grayscale: bool = Field(True, description="灰度匹配加速")


class DebounceConfig(BaseModel):
    """防抖参数。"""

    enabled: bool = Field(True, description="同一目标防抖开关")
    window_seconds: float = Field(300.0, description="防抖窗口（秒），默认 5 分钟")


class RetryConfig(BaseModel):
    """无人接听自动重拨参数。"""

    enabled: bool = Field(True, description="无人接听自动重拨开关（需 verify_call_window: true）")
    max_attempts: int = Field(0, description="总拨打次数上限（含首次）；0=不限次，直至对方接听或拒绝")
    interval_seconds: float = Field(10.0, description="无人接听重拨间隔（秒）")
    ring_seconds: float = Field(
        45.0,
        description="单次响铃时长上限（秒）：超过则主动点击挂断并重拨；0=不主动挂断，等 QQ 自然结束（可能数分钟）",
    )
    natural_ringout_seconds: float = Field(
        120.0,
        description="自然响铃超时下限（秒）：实测 QQ 无人接听自然挂断需要数分钟，通话窗口存活低于该值即"
        "关闭时判定为对方已接听/拒绝（触达成功），防止快速拒绝被误判为无人接听而不停重拨",
    )
    no_answer_seconds: float = Field(
        60.0,
        description="兜底阈值（秒）：缺少响铃模板 assets/call_ringing_sample.png 时，按通话窗口"
        "从出现到关闭的历经时长判定（超阈值=无应答）。存在响铃模板时使用状态文本判定，不依赖该值",
    )


class MediaConfig(BaseModel):
    """接听后语音提醒：对方接听后自动向其播放一段固定语音（需虚拟声卡，见 README）。"""

    speak_on_answer: bool = Field(False, description="对方接听后自动播放语音提醒；需要安装虚拟声卡并在 QQ 中将麦克风设为其输入端")
    message: str = Field("任务有新进展，请前往电脑查看", description="要播放的提醒文本（Windows SAPI 离线语音合成，无需联网）")
    output_device_hint: str = Field("CABLE", description="语音输出设备名匹配关键字（虚拟声卡的播放端，如 CABLE Input）")
    hangup_after_speak: bool = Field(True, description="播放完成后自动挂断；关闭则保持通话")
    volume: int = Field(100, description="提醒语音音量 0-100")
    speak_timeout: float = Field(30.0, description="语音播放超时（秒）")


class WatchdogConfig(BaseModel):
    """看门狗：Agent 心跳超时（疑似硬崩溃）时自动拨打兜底。"""

    enabled: bool = Field(True, description="看门狗开关")
    stale_seconds: float = Field(
        300.0,
        description="心跳超时阈值（秒）：心跳文件超过该时长未刷新且 QQ 在运行，判定 Agent 异常并拨打；"
        "需大于 Agent 纯思考（不调工具）的最长间隙",
    )


class PresenceConfig(BaseModel):
    """用户在位检测：人在电脑前时跳过拨打（仅对自动化触发生效，手动命令不受限）。"""

    enabled: bool = Field(True, description="空闲检测开关（仅 auto 触发路径生效）")
    idle_seconds: float = Field(
        180.0,
        description="键鼠空闲超过该秒数才真正拨打；低于该值视为用户正在电脑前，静默跳过",
    )


def hold_file() -> Path:
    """提醒暂停标记文件路径（hold.json：{"until": 时间戳}）。"""
    return ConfigManager.state_dir() / "hold.json"


def hold_active() -> bool:
    """提醒是否处于暂停期（hold 未过期）。由 Agent 在「回合将自动继续」时设置。"""
    try:
        until = float(json.loads(hold_file().read_text(encoding="utf-8")).get("until", 0))
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return False
    return time.time() < until


def set_hold(minutes: float) -> None:
    """暂停自动提醒指定分钟数（到期自动恢复，防 Agent 忘记摘牌导致漏提醒）。"""
    p = hold_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"until": time.time() + minutes * 60}), encoding="utf-8")


def clear_hold() -> None:
    """立即恢复自动提醒（Agent 需要用户操作/最终交付时调用）。"""
    hold_file().unlink(missing_ok=True)


class LogConfig(BaseModel):
    """日志配置。"""

    level: str = Field("INFO", description="DEBUG / INFO / WARNING / ERROR")
    file: Optional[str] = Field(None, description="日志文件路径；为空不写文件")


class ServerConfig(BaseModel):
    """HTTP 服务配置。"""

    host: str = Field("127.0.0.1", description="监听地址")
    port: int = Field(18600, description="监听端口")


class NumAlarmConfig(BaseModel):
    """numalarm 顶层配置模型，未提供的字段一律使用内置默认值。"""

    default_target: Optional[str] = Field(None, description="默认拨打目标（昵称/备注）")
    target_alias: Dict[str, str] = Field(default_factory=dict, description="别名映射：别名 -> 真实昵称/备注")

    qq_process_name: str = Field("QQ.exe", description="QQ 进程名")
    qq_window_title: str = Field("QQ", description="QQ 主面板窗口标题匹配关键字")
    call_window_hint: str = Field("通话", description="拨打后确认通话窗口出现的关键字")
    verify_call_window: bool = Field(True, description="拨打后是否检测通话窗口（QQ 版本行为不同可改为 false）")
    failsafe: bool = Field(True, description="pyautogui 角落保护开关（鼠标移到屏幕左上角可中断）")

    search_method: str = Field(
        "click",
        description="搜索框打开方式：click 点击搜索框（推荐，QQ NT 不响应 Ctrl+F）/ hotkey 使用快捷键",
    )
    search_box_offset: List[int] = Field(
        default_factory=lambda: [225, 81],
        description="click 模式：搜索框点击点相对主面板左上角的偏移（物理像素，可用 numalarm init 或截图校准）",
    )
    search_select: str = Field(
        "click",
        description="搜索结果选中方式：click 点击第一条结果（推荐，部分 QQ 版本回车无效）/ enter 回车选中",
    )
    search_result_offset: List[int] = Field(
        default_factory=lambda: [178, 184],
        description="click 选中模式：第一条搜索结果点击点相对主面板左上角的偏移（物理像素）",
    )

    hotkey: HotkeyConfig = HotkeyConfig()
    timing: TimingConfig = TimingConfig()
    match: MatchConfig = MatchConfig()
    debounce: DebounceConfig = DebounceConfig()
    retry: RetryConfig = RetryConfig()
    presence: PresenceConfig = PresenceConfig()
    media: MediaConfig = MediaConfig()
    watchdog: WatchdogConfig = WatchdogConfig()
    log: LogConfig = LogConfig()
    server: ServerConfig = ServerConfig()


# --------------------------- 配置管理器 ---------------------------
class ConfigManager:
    """配置管理器：加载、解析、目标别名解析、状态文件路径。单例使用。"""

    _instance: Optional["ConfigManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self.config_path: Optional[Path] = config_path or self.find_config_file()
        self.config: NumAlarmConfig = self._load(self.config_path)

    @classmethod
    def instance(cls) -> "ConfigManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @staticmethod
    def find_config_file() -> Optional[Path]:
        """按优先级查找配置文件：环境变量 -> 当前工作目录 -> 安装目录（技能目录）。

        第三级让「作为技能安装到宿主技能目录」的用法无需设置环境变量即可生效。
        """
        env_path = os.environ.get(CONFIG_ENV)
        if env_path:
            p = Path(env_path)
            return p if p.is_file() else None
        local = Path.cwd() / CONFIG_FILENAME
        if local.is_file():
            return local
        # numalarm 包所在安装/克隆目录（如宿主技能目录）
        pkg_root = Path(__file__).resolve().parents[2] / CONFIG_FILENAME
        return pkg_root if pkg_root.is_file() else None

    @staticmethod
    def _load(path: Optional[Path]) -> NumAlarmConfig:
        if path is None:
            return NumAlarmConfig()
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            return NumAlarmConfig(**raw)
        except FileNotFoundError:
            return NumAlarmConfig()
        except (yaml.YAMLError, TypeError, ValueError):
            # 配置文件损坏时回退默认值，保证工具整体可用
            return NumAlarmConfig()

    def reload(self) -> NumAlarmConfig:
        """重新加载配置文件。"""
        self.config = self._load(self.config_path)
        return self.config

    def resolve_target(self, target: Optional[str]) -> str:
        """解析拨打目标：None/空 -> default_target；别名 -> 真实昵称/备注。

        :raises ParameterError: 无法解析出有效目标时抛出（code=400）
        """
        resolved = target
        if resolved is None or str(resolved).strip() == "":
            resolved = self.config.default_target
        if resolved is None or str(resolved).strip() == "":
            raise ParameterError("未指定拨打目标，且配置文件未设置 default_target")
        resolved = str(resolved).strip()
        # 别名映射（精确匹配）
        resolved = self.config.target_alias.get(resolved, resolved)
        return resolved

    # ---------------- 状态目录（防抖记录 / 跨进程锁） ----------------
    @staticmethod
    def state_dir() -> Path:
        """用户级状态目录 ~/.numalarm（与项目目录解耦）。"""
        d = Path.home() / ".numalarm"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def lock_file() -> Path:
        return ConfigManager.state_dir() / "numalarm.lock"

    @staticmethod
    def state_file() -> Path:
        return ConfigManager.state_dir() / "state.json"


def load_config() -> NumAlarmConfig:
    """便捷方法：获取当前生效配置。"""
    return ConfigManager.instance().config


def get_config_manager() -> ConfigManager:
    """便捷方法：获取配置管理器单例。"""
    return ConfigManager.instance()
