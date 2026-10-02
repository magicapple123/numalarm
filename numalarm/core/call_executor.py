"""拨打流程编排与执行。

标准流程（每步异常均捕获并转换为统一状态码）：
    前置校验(QQ进程) -> 唤起面板 -> 搜索好友 -> 定位语音按钮(重试2次)
    -> 执行拨打 -> 监控通话结果 -> 结果返回

无人接听自动重拨：通话窗口从出现到关闭超过 ``retry.no_answer_seconds`` 视为无应答，
自动间隔重拨，直至对方接听或拒绝（均视为触达成功）；``retry.max_attempts`` 限制总
拨打次数（0=不限次）。

资源释放：互斥锁在 finally 中保证释放（无论成功失败）；重拨全程持锁，视为一路会话。

本模块同时提供 ``test()`` 校准模式：仅执行到「识别语音按钮」，不点击拨打。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from numalarm.common.config import ConfigManager, NumAlarmConfig, hold_active
from numalarm.common.exceptions import (
    CODE_QQ_NOT_RUNNING,
    CODE_SUCCESS,
    BusyError,
    CallTimeoutError,
    NumAlarmError,
    ParameterError,
    QQNotRunningError,
    RateLimitedError,
    VoiceButtonNotFoundError,
)
from numalarm.common.lock import CallMutex
from numalarm.common.logger import get_logger, setup_logging
from numalarm.core import state_detector
from numalarm.core.qq_controller import QQController

logger = get_logger("executor")

# 项目根目录：numalarm/numalarm/core/call_executor.py -> 上溯 2 级
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = PROJECT_ROOT / "assets"
VOICE_BUTTON_SAMPLE = ASSET_DIR / "voice_button_sample.png"

# ---------------- 拨打结果判定（观测「语音通话」窗口生命周期） ----------------
OUTCOME_ANSWERED = "answered"          # 对方已接听（通话窗口持续存在超过阈值，通话中）
OUTCOME_TERMINAL = "terminal"          # 通话已结束：接听后挂断或对方拒绝/取消（均视为触达成功）
OUTCOME_NO_ANSWER = "no_answer"        # 无人接听（响铃超时 QQ 自动结束）-> 触发重拨
OUTCOME_CLICK_FAILED = "click_failed"  # 点击后通话窗口未出现 -> 触发重拨

# 结果监控轮询间隔（秒）
_OUTCOME_POLL = 0.5


class CallOutcome(NamedTuple):
    """单次拨打的最终观测结果。"""

    kind: str                    # OUTCOME_* 常量
    elapsed: float               # 通话窗口从出现到出结果历经的秒数
    extra: Dict[str, Any]        # 附加数据（按钮坐标、聊天窗口标题等）


class DebounceStore:
    """防抖记录：同一目标在窗口期内重复调用仅执行第一次。

    状态持久化到用户目录 ~/.numalarm/state.json，跨进程生效。
    """

    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file
        self._lock = threading.Lock()

    def _read(self) -> Dict[str, float]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: Dict[str, float]) -> None:
        try:
            self.state_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            logger.warning("防抖状态文件写入失败：%s", self.state_file)

    def check(self, target: str, window_seconds: float) -> Optional[float]:
        """若目标处于防抖窗口内，返回剩余秒数；否则返回 None。"""
        with self._lock:
            last = self._read().get(target)
            if last is None:
                return None
            remain = window_seconds - (time.time() - float(last))
            return remain if remain > 0 else None

    def mark(self, target: str) -> None:
        """记录目标的本次成功拨打时间戳。"""
        with self._lock:
            data = self._read()
            data[target] = time.time()
            self._write(data)


class CallExecutor:
    """核心拨打执行器：SDK / HTTP / CLI 共用同一套流程。"""

    def __init__(
        self,
        config: Optional[NumAlarmConfig] = None,
        silent: bool = False,
        configure_logging: bool = True,
    ) -> None:
        self.manager = ConfigManager.instance()
        self.config = config or self.manager.config
        self.silent = silent
        self.controller = QQController(self.config)
        self.mutex = CallMutex.instance()
        self.debounce = DebounceStore(ConfigManager.state_file())
        # 静默模式：关闭所有标准输出；HTTP 服务应传 configure_logging=False
        if configure_logging:
            setup_logging(
                level=self.config.log.level, log_file=self.config.log.file, silent=silent
            )

    # ------------------------------------------------------------------
    # 主流程（含无人接听自动重拨）
    # ------------------------------------------------------------------
    def call(
        self,
        target: Optional[str] = None,
        timeout: float = 30.0,
        reason: str = "",
        auto: bool = False,
    ) -> Dict[str, Any]:
        """执行完整拨打流程，返回统一结果字典（所有异常在内部转换为状态码）。

        无人接听处理：对方接听或拒绝均视为触达成功（停止拨打）；无人接听（QQ 响铃
        超时自动结束）则按 ``retry`` 配置自动重拨。``timeout`` 为单次拨打建立阶段
        （进程检测/唤起/搜索/定位/点击）的预算，不含结果监控时长。

        :param reason: 拨打原因（记录到日志与结果 data，便于 Agent 集成时追溯）
        :param auto: 自动化触发标记。True 时启用「用户在位检测」——键鼠空闲低于
            ``presence.idle_seconds`` 视为用户正在电脑前，跳过拨打（每轮重拨前复检，
            用户中途回来自动停止重拨）；手动命令传 False 不受检测限制。
        """
        start = time.time()
        logger.info("拨打流程开始：target=%r timeout=%ss reason=%r", target, timeout, reason)
        try:
            resolved = self.manager.resolve_target(target)
            if timeout is None or float(timeout) <= 0:
                raise ParameterError("timeout 必须为正数")
            timeout = float(timeout)

            # 全局互斥：并发请求直接返回占线，不做排队（重拨全程持锁，视为一路会话）
            if not self.mutex.acquire():
                raise BusyError()
            dialed = False
            try:
                # 1. 防抖：同一目标窗口期内仅执行第一次
                if self.config.debounce.enabled:
                    remain = self.debounce.check(resolved, self.config.debounce.window_seconds)
                    if remain is not None:
                        raise RateLimitedError(
                            f"目标「{resolved}」处于防抖窗口内，约 {int(remain)} 秒后可再次拨打",
                            data={"target": resolved, "retry_after_seconds": round(remain, 1)},
                        )

                # 2. 重拨策略：未启用通话窗口验证时无法观测结果，自动退化为单次拨打
                retry_cfg = self.config.retry
                if not self.config.verify_call_window and retry_cfg.enabled:
                    logger.warning(
                        "verify_call_window=false 时无法观测通话结果，无人接听重拨自动停用（仅拨打一次）"
                    )
                cap = retry_cfg.max_attempts if (retry_cfg.enabled and self.config.verify_call_window) else 1

                # 3. 拨打循环
                attempt = 0
                while True:
                    # 用户在位检测 + 提醒暂停（hold）：人在电脑前或 Agent 挂了免打扰牌时不打扰
                    if auto and (self._user_active() or hold_active()):
                        idle = state_detector.get_idle_seconds()
                        skipped = {
                            "skipped": True,
                            "outcome_kind": "skipped",
                            "outcome": "已跳过（用户在电脑前）",
                            "attempts": attempt,
                            "user_idle_seconds": round(idle, 1) if idle >= 0 else None,
                        }
                        if reason:
                            skipped["reason"] = reason
                        if attempt == 0:
                            logger.info("用户正在电脑前（键鼠空闲 %.0fs），跳过拨打", max(idle, 0))
                            return {
                                "code": CODE_SUCCESS,
                                "message": "已跳过拨打（用户正在电脑前）",
                                "data": skipped,
                            }
                        logger.info("用户回到电脑前，停止剩余重拨（已拨打 %d 次）", attempt)
                        return {
                            "code": CODE_SUCCESS,
                            "message": f"已拨打 {attempt} 次未触达，检测到用户在电脑前，停止重拨",
                            "data": skipped,
                        }

                    attempt += 1
                    outcome = self._dial_once(resolved, timeout)
                    dialed = True

                    if outcome.kind in (OUTCOME_ANSWERED, OUTCOME_TERMINAL):
                        # 接听 / 拒绝（或取消）：触达成功，停止拨打
                        default_label = "对方已接听" if outcome.kind == OUTCOME_ANSWERED else "对方已接听或已拒绝"
                        label = str(outcome.extra.get("outcome") or default_label)
                        elapsed = round(time.time() - start, 2)
                        logger.info(
                            "拨打流程完成：target=%s 结果=%s 第 %d 次拨打 触达耗时=%ss",
                            resolved, label, attempt, elapsed,
                        )
                        data = {
                            "target": resolved,
                            "attempts": attempt,
                            "outcome": label,
                            "outcome_kind": outcome.kind,
                            "call_elapsed_seconds": round(outcome.elapsed, 1),
                            "elapsed_seconds": elapsed,
                        }
                        if reason:
                            data["reason"] = reason
                        data.update(outcome.extra)
                        return {
                            "code": CODE_SUCCESS,
                            "message": f"拨打成功（{label}）",
                            "data": data,
                        }

                    # NO_ANSWER / CLICK_FAILED：按配置决定是否重拨
                    if cap > 0 and attempt >= cap:
                        if outcome.kind == OUTCOME_CLICK_FAILED:
                            raise CallTimeoutError(
                                f"点击语音按钮后 {self.config.timing.verify_window_timeout:g}s "
                                f"内未出现通话窗口（共尝试 {attempt} 次）",
                                data={"target": resolved, "attempts": attempt},
                            )
                        raise CallTimeoutError(
                            f"拨打 {attempt} 次均无人接听，已停止重拨",
                            data={"target": resolved, "attempts": attempt},
                        )

                    logger.info(
                        "第 %d 次拨打%s，%.0f 秒后重拨（次数上限：%s）",
                        attempt,
                        "无人接听" if outcome.kind == OUTCOME_NO_ANSWER else "未检测到通话窗口",
                        retry_cfg.interval_seconds,
                        f"{cap} 次" if cap > 0 else "不限",
                    )
                    time.sleep(max(float(retry_cfg.interval_seconds), 0.0))
            finally:
                # 资源释放：无论成功失败必须释放互斥锁；本会话执行过拨打则记录防抖
                if dialed:
                    self.debounce.mark(resolved)
                self.mutex.release()
        except NumAlarmError as exc:
            logger.error("拨打流程失败 [code=%s]：%s", exc.code, exc.message)
            return exc.to_result()
        except Exception as exc:  # noqa: BLE001 未知错误兜底
            logger.error("拨打流程出现未预期异常：%s", exc, exc_info=True)
            return NumAlarmError(f"未预期异常：{exc}").to_result()

    # ------------------------------------------------------------------
    # 校准模式（不拨打）
    # ------------------------------------------------------------------
    def test(self, target: Optional[str] = None) -> Dict[str, Any]:
        """校准测试：执行「QQ进程 -> 唤起面板 -> 搜索好友 -> 识别语音按钮」，不点击拨打。

        :return: data.steps 为每一步检测结果；校准不记录防抖时间戳
        """
        start = time.time()
        steps: List[Dict[str, Any]] = []

        def step(name: str, ok: bool, code: int, detail: str = "") -> None:
            steps.append({"step": name, "ok": ok, "code": code, "detail": detail})
            logger.info("[校准] %s -> %s %s", name, "通过" if ok else "失败", detail)

        try:
            resolved = self.manager.resolve_target(target)
        except NumAlarmError as exc:
            return exc.to_result()

        logger.info("校准流程开始：target=%s", resolved)
        if not self.mutex.acquire():
            return BusyError().to_result()
        try:
            # 步骤 1：QQ 进程
            qq_ok = state_detector.is_qq_running(self.config.qq_process_name)
            step("QQ 进程检测", qq_ok, CODE_QQ_NOT_RUNNING, self.config.qq_process_name)
            if not qq_ok:
                return self._test_result(resolved, steps)

            # 步骤 2：唤起主面板
            try:
                self.controller.wake_panel()
                step("唤起主面板", True, CODE_SUCCESS, self.config.qq_window_title)
            except NumAlarmError as exc:
                step("唤起主面板", False, exc.code, exc.message)
                return self._test_result(resolved, steps)

            # 步骤 3：搜索好友 / 打开聊天窗口
            try:
                chat = self.controller.search_friend(resolved)
                step("搜索好友/打开聊天窗口", True, CODE_SUCCESS, str(chat.get("title", "")))
            except NumAlarmError as exc:
                step("搜索好友/打开聊天窗口", False, exc.code, exc.message)
                return self._test_result(resolved, steps)

            # 步骤 4：语音按钮匹配（不点击）
            try:
                region = (int(chat["left"]), int(chat["top"]), int(chat["width"]), int(chat["height"]))
                button = self._locate_voice_button(region=region)
                step("语音按钮匹配（不点击）", True, CODE_SUCCESS, f"坐标 ({button[0]}, {button[1]})")
            except NumAlarmError as exc:
                step("语音按钮匹配（不点击）", False, exc.code, exc.message)

            logger.info("校准流程结束，耗时 %.1fs", time.time() - start)
            return self._test_result(resolved, steps)
        finally:
            self.mutex.release()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _locate_voice_button(self, region: Tuple[int, int, int, int]) -> Tuple[int, int]:
        """在指定窗口区域内图片匹配「语音通话」按钮，失败自动重试。

        :raises VoiceButtonNotFoundError: 缺少示例截图 / 匹配组件缺失 / 重试后仍未命中
        """
        pag = QQController.pag()
        m = self.config.match
        if not VOICE_BUTTON_SAMPLE.is_file():
            raise VoiceButtonNotFoundError(
                f"缺少语音按钮示例截图：{VOICE_BUTTON_SAMPLE}；"
                "请按 assets/README.md 的说明截取聊天窗口「语音通话」按钮并保存为该文件"
            )
        attempts = max(int(m.max_retry), 0) + 1
        for i in range(1, attempts + 1):
            # 未命中视为一次失败（可重试）；组件异常才直接判定为配置问题
            img_not_found = getattr(pag, "ImageNotFoundException", None)
            try:
                box = pag.locateCenterOnScreen(
                    str(VOICE_BUTTON_SAMPLE),
                    region=region,
                    confidence=float(m.confidence),
                    grayscale=bool(m.grayscale),
                )
            except Exception as exc:  # noqa: BLE001
                if img_not_found is not None and isinstance(exc, img_not_found):
                    # 新版 pyscreeze 用空消息异常表示「未找到」——按未命中处理
                    logger.warning("语音按钮未匹配（第 %d/%d 次）", i, attempts)
                    time.sleep(self.config.timing.action_wait)
                    continue
                raise VoiceButtonNotFoundError(
                    f"图片匹配执行失败（通常为缺少 opencv-python）：{exc}"
                ) from exc
            if box is not None:
                logger.info("语音按钮匹配成功（第 %d/%d 次尝试）：%s", i, attempts, (box.x, box.y))
                return int(box.x), int(box.y)
            logger.warning("语音按钮匹配失败（第 %d/%d 次）", i, attempts)
            time.sleep(self.config.timing.action_wait)
        raise VoiceButtonNotFoundError(
            f"语音按钮匹配失败（已尝试 {attempts} 次）。常见原因：分辨率/DPI 变化、界面遮挡、QQ 版本界面差异；"
            "请重新截取 assets/voice_button_sample.png 或调整 match.confidence"
        )

    def _user_active(self) -> bool:
        """用户是否正在电脑前（键鼠空闲低于 presence.idle_seconds）。"""
        p = self.config.presence
        if not p.enabled:
            return False
        idle = state_detector.get_idle_seconds()
        if idle < 0:  # 非 Windows / 查询失败：不拦截
            return False
        return idle < float(p.idle_seconds)

    def _dial_once(self, resolved: str, timeout: float) -> CallOutcome:
        """执行一次完整拨打（校验->唤起->搜索->定位->点击->观测结果）。

        :param timeout: 单次拨打建立阶段预算（秒）
        :raises QQNotRunningError / TargetNotFoundError / VoiceButtonNotFoundError: 建立阶段失败（不重拨）
        """
        setup_start = time.time()
        # 前置校验：QQ 进程（不自动启动 QQ、不存储账号密码）
        self._check_budget(setup_start, timeout, "前置校验")
        if not state_detector.is_qq_running(self.config.qq_process_name):
            raise QQNotRunningError(
                f"QQ 进程（{self.config.qq_process_name}）未启动；"
                "本工具不会自动登录 QQ，请先手动启动并登录"
            )
        logger.info("QQ 进程检测通过")

        # 唤起主面板
        self._check_budget(setup_start, timeout, "唤起面板")
        self.controller.wake_panel()

        # 搜索好友并打开聊天窗口
        self._check_budget(setup_start, timeout, "搜索好友")
        chat = self.controller.search_friend(resolved)

        # 定位语音通话按钮（失败自动重试）
        self._check_budget(setup_start, timeout, "定位语音按钮")
        region = (int(chat["left"]), int(chat["top"]), int(chat["width"]), int(chat["height"]))
        button = self._locate_voice_button(region=region)

        # 执行拨打：模拟左键点击
        self._check_budget(setup_start, timeout, "执行拨打")
        pag = QQController.pag()
        pag.click(int(button[0]), int(button[1]))
        logger.info("已点击语音通话按钮 %s，等待拨打触发", button)
        extra = {
            "button_position": [int(button[0]), int(button[1])],
            "chat_window": str(chat.get("title", "")),
        }

        if not self.config.verify_call_window:
            time.sleep(self.config.timing.click_confirm_wait)
            logger.info("已按配置跳过通话窗口验证，视为拨打成功")
            return CallOutcome(OUTCOME_TERMINAL, 0.0, extra)
        return self._monitor_call_outcome(extra)

    def _monitor_call_outcome(self, extra: Dict[str, Any]) -> CallOutcome:
        """监控「语音通话」窗口直至出现结果。

        主判定（响铃状态文本模板，确定性）：
        - 响铃期间通话窗口头部始终显示「等待对方接听」（模板 assets/call_ringing_sample.png）
        - 窗口存在但头部文案变化（变为计时器/拒绝提示等）-> 对方已响应：接听或拒绝，触达成功
        - 响铃超过 retry.ring_seconds -> 主动点击挂断 -> 无人接听 -> 触发重拨
        - 窗口关闭且全程仍为响铃状态 -> 无人接听（QQ 自然超时）-> 触发重拨
        - 点击后 verify_window_timeout 内窗口未出现 -> 触发失败 -> 触发重拨
        模板缺失或预热持续未命中时，回退为时长阈值启发式（retry.no_answer_seconds）。
        """
        win = self._wait_call_window(float(self.config.timing.verify_window_timeout))
        if win is None:
            return CallOutcome(OUTCOME_CLICK_FAILED, 0.0, extra)
        logger.info("通话窗口已出现（关键字「%s」），开始监控通话结果", self.config.call_window_hint)

        ring_tpl = ASSET_DIR / "call_ringing_sample.png"
        if ring_tpl.is_file():
            outcome = self._monitor_by_ringing_template(ring_tpl, extra)
            if outcome is not None:
                return outcome
            logger.warning("响铃模板持续未命中（QQ 界面差异或窗口异常），回退为时长阈值判定")
        else:
            logger.warning("缺少响铃模板 assets/call_ringing_sample.png，回退为时长阈值判定")
        return self._monitor_by_threshold(extra)

    def _wait_call_window(self, timeout: float) -> Optional[Dict[str, Any]]:
        """等待通话窗口出现并返回其窗口信息；超时返回 None。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            wins = state_detector.find_windows(self.config.call_window_hint)
            if wins:
                return wins[0]
            time.sleep(_OUTCOME_POLL)
        return None

    def _find_call_window(self) -> Optional[Dict[str, Any]]:
        wins = state_detector.find_windows(self.config.call_window_hint)
        return wins[0] if wins else None

    def _ringing_header_matches(self, ring_tpl: Path, win: Dict[str, Any]) -> bool:
        """检查通话窗口头部是否仍显示「等待对方接听」响铃文案。

        通话窗口为深色主题：先采样窗口左上角亮度判断是否被遮挡，
        被浅色窗口覆盖时自动前置通话窗口再截图匹配。
        """
        win = self._ensure_call_window_visible(win)
        pag = QQController.pag()
        region = (int(win["left"]), int(win["top"]), int(win["width"]), int(win["height"]))
        try:
            haystack = pag.screenshot(region=region)
            return pag.locate(str(ring_tpl), haystack, confidence=0.75, grayscale=True) is not None
        except Exception as exc:  # noqa: BLE001 未找到/组件异常均视为「非响铃状态」
            logger.debug("响铃模板匹配未命中：%s", exc)
            return False

    def _ensure_call_window_visible(self, win: Dict[str, Any]) -> Dict[str, Any]:
        """若通话窗口被遮挡（左上角采样为浅色），前置窗口并返回最新窗口信息。"""
        pag = QQController.pag()
        x, y = int(win["left"]) + 6, int(win["top"]) + 6
        try:
            sample = pag.screenshot(region=(x, y, 2, 2))
            px = sample.getpixel((0, 0))
        except Exception:  # noqa: BLE001 采样失败时不做前置动作
            return win
        if isinstance(px, (tuple, list)) and len(px) >= 3:
            bright = 0.299 * px[0] + 0.587 * px[1] + 0.114 * px[2]
        else:
            bright = float(px)
        if bright <= 120:  # 深色 -> 通话窗口可见
            return win
        info = state_detector.activate_window(self.config.call_window_hint)
        if info is not None:
            logger.debug("通话窗口被遮挡，已自动前置")
            time.sleep(0.3)
            return info
        return win

    def _monitor_by_ringing_template(self, ring_tpl: Path, extra: Dict[str, Any]) -> Optional[CallOutcome]:
        """模板法监控。

        返回 None 表示模板持续未命中（预热失败），调用方应回退阈值法。
        判定规则：
        - 头部文案偏离「等待对方接听」且经快速复检确认 -> 对方已响应（接听或拒绝），触达成功
        - 窗口关闭：曾观测到文案变化、或窗口存活时长小于自然响铃超时下限
          （retry.natural_ringout_seconds）-> 对方已接听/拒绝，触达成功
          （QQ 自然超时实测长达数分钟，快速关闭不可能是自然超时——防止对方
          「秒拒」在两次轮询间完成而被误判为无人接听导致不停重拨）
        - 窗口关闭、全程响铃且存活超过自然超时下限 -> 无人接听
        状态变化需快速复检确认，排除瞬时闪烁/动画误判。
        """
        appeared_at = time.time()

        # 预热：等待模板首次命中（窗口弹出动画期间可能未渲染完成），最多约 3s
        ring_started: Optional[float] = None
        for _ in range(6):
            win = self._find_call_window()
            if win is None:
                # 窗口出现后秒关：自然超时不可能这么快，按「已响应（接听/拒绝）」处理
                logger.info("通话窗口在预热期关闭，判定：对方已接听或已拒绝")
                return CallOutcome(OUTCOME_TERMINAL, 0.0, extra)
            if self._ringing_header_matches(ring_tpl, win):
                ring_started = time.time()
                logger.info("响铃状态确认（匹配「等待对方接听」模板）")
                break
            time.sleep(_OUTCOME_POLL)
        if ring_started is None:
            return None

        ring_limit = float(self.config.retry.ring_seconds)
        natural = max(float(self.config.retry.natural_ringout_seconds), 1.0)
        miss = 0
        while True:
            win = self._find_call_window()
            if win is None:
                miss += 1
                if miss >= 2:
                    elapsed = time.time() - appeared_at
                    if elapsed < natural:
                        logger.info(
                            "通话窗口仅存活 %.0fs（< 自然响铃超时下限 %.0fs），判定：对方已接听或已拒绝",
                            elapsed, natural,
                        )
                        return CallOutcome(OUTCOME_TERMINAL, elapsed, extra)
                    logger.info("通话窗口关闭且全程响铃、存活 %.0fs，判定：无人接听", elapsed)
                    return CallOutcome(OUTCOME_NO_ANSWER, elapsed, extra)
            else:
                miss = 0
                if not self._ringing_header_matches(ring_tpl, win):
                    # 头部状态变化：立即快速复检（不睡眠等待，拒绝弹窗存活可能 <1s）
                    confirmed = False
                    for _ in range(3):
                        win2 = self._find_call_window()
                        if win2 is None:
                            confirmed = True  # 变化后窗口关闭（典型：对方拒绝）
                            break
                        if not self._ringing_header_matches(ring_tpl, win2):
                            confirmed = True  # 连续非响铃文案（计时器/拒绝提示）
                            break
                        time.sleep(0.3)
                    if confirmed:
                        return self._handle_state_change(extra)
                    # 复检恢复响铃 → 判定为瞬时闪烁，继续监控
                else:
                    # 仍在响铃：响铃超时主动挂断，以便快速进入下一轮（QQ 自然超时可能数分钟）
                    if ring_limit > 0 and time.time() - ring_started > ring_limit:
                        if self._click_hangup(win):
                            for _ in range(12):
                                if self._find_call_window() is None:
                                    break
                                time.sleep(_OUTCOME_POLL)
                            logger.info("响铃超过 %.0fs 已主动挂断，判定：无人接听", ring_limit)
                            return CallOutcome(OUTCOME_NO_ANSWER, 0.0, extra)
                        # 挂断按钮定位失败：稍后再试（推迟 2s 避免密集重试）
                        ring_started = time.time() - ring_limit + 2.0
            time.sleep(_OUTCOME_POLL)

    def _handle_state_change(self, extra: Dict[str, Any]) -> CallOutcome:
        """头部状态变化（离开响铃态）后的处理：区分接听/拒绝，按配置播放语音提醒。

        区分方法（无 OCR 的稳定启发式）：
        - 对方拒绝：通话窗口在数秒内关闭
        - 对方接听：通话窗口保持存在（通话中），可自动播放语音提醒后挂断
        """
        m = self.config.media
        # 最多等 3.5s：窗口仍存在 => 已接听（通话中）；消失 => 已拒绝/取消
        deadline = time.time() + 3.5
        win = None
        while time.time() < deadline:
            win = self._find_call_window()
            if win is not None:
                break
            time.sleep(0.3)

        if win is None:
            logger.info("状态变化后通话窗口已关闭，判定：对方已拒绝（已触达）")
            extra = {**extra, "outcome": "对方已拒绝（已触达）"}
            return CallOutcome(OUTCOME_TERMINAL, 0.0, extra)

        # 对方已接听
        if not m.speak_on_answer:
            logger.info("对方已接听（通话窗口保持），触达成功")
            extra = {**extra, "outcome": "对方已接听（通话中）", "answered": True}
            return CallOutcome(OUTCOME_TERMINAL, 0.0, extra)

        spoke = self._speak_reminder()
        if spoke and m.hangup_after_speak:
            hang_win = self._find_call_window()
            if hang_win is not None:
                self._click_hangup(hang_win)
                for _ in range(16):
                    if self._find_call_window() is None:
                        break
                    time.sleep(_OUTCOME_POLL)
            logger.info("语音提醒播放完成，已自动挂断")
            extra = {**extra, "outcome": "对方已接听，语音提醒已播放", "answered": True, "voice_played": True}
            return CallOutcome(OUTCOME_TERMINAL, 0.0, extra)

        if spoke:
            logger.info("语音提醒播放完成，按配置保持通话")
            extra = {**extra, "outcome": "对方已接听，语音提醒已播放（通话保持）", "answered": True, "voice_played": True}
            return CallOutcome(OUTCOME_TERMINAL, 0.0, extra)

        # 播放失败（通常是未安装虚拟声卡）：保持通话，如实报告
        logger.warning("语音提醒播放失败（未检测到虚拟声卡输出设备？），通话保持")
        extra = {**extra, "outcome": "对方已接听，语音提醒播放失败", "answered": True, "voice_played": False}
        return CallOutcome(OUTCOME_TERMINAL, 0.0, extra)

    def _speak_reminder(self) -> bool:
        """向对方播放配置的提醒语音（SAPI 离线合成 -> 虚拟声卡 -> QQ 麦克风）。"""
        m = self.config.media
        try:
            from numalarm.core.voice import speak_to_device
        except NumAlarmError as exc:
            logger.warning("%s", exc.message)
            return False
        try:
            ok = speak_to_device(
                m.message,
                device_hint=m.output_device_hint,
                volume=m.volume,
                timeout=float(m.speak_timeout),
            )
        except NumAlarmError as exc:
            logger.warning("%s", exc.message)
            return False
        if ok:
            logger.info("语音提醒播放完成：%s", m.message)
        return ok

    def _click_hangup(self, win: Dict[str, Any]) -> bool:
        """点击通话窗口中的红色挂断按钮（按红色像素簇重心定位）。

        :return: 是否成功定位并点击
        """
        pag = QQController.pag()
        win = self._ensure_call_window_visible(win)
        box = (int(win["left"]), int(win["top"]), int(win["width"]), int(win["height"]))
        try:
            hay = pag.screenshot(region=box)
        except Exception as exc:  # noqa: BLE001
            logger.debug("挂断按钮截图失败：%s", exc)
            return False
        px = hay.load()
        sx = sy = n = 0
        for yy in range(0, hay.height, 2):
            for xx in range(0, hay.width, 2):
                p = px[xx, yy]
                r, g, b = p[0], p[1], p[2]
                if r > 170 and g < 90 and b < 90:  # 红色挂断按钮
                    sx += xx
                    sy += yy
                    n += 1
        if n < 30:  # 未找到足够的红色像素（可能已被遮挡/状态变化）
            logger.debug("未定位到挂断按钮（红色像素 %d 个）", n)
            return False
        cx, cy = sx / n, sy / n
        pag.click(int(win["left"] + cx), int(win["top"] + cy))
        logger.info("已点击挂断按钮（窗口内相对位置 %.2f, %.2f）", cx / box[2], cy / box[3])
        return True

    def _monitor_by_threshold(self, extra: Dict[str, Any]) -> CallOutcome:
        """阈值法兜底（无模板时）：基于窗口从出现到关闭的历经时长判定。"""
        threshold = max(float(self.config.retry.no_answer_seconds), 1.0)
        appeared_at = time.time()  # 窗口刚出现（误差 <= 轮询间隔）
        while True:
            elapsed = time.time() - appeared_at
            if self._find_call_window() is None:
                if elapsed >= threshold:
                    logger.info("通话窗口于 %.1fs 后关闭，判定：无人接听", elapsed)
                    return CallOutcome(OUTCOME_NO_ANSWER, elapsed, extra)
                logger.info("通话窗口于 %.1fs 内关闭，判定：对方已接听或已拒绝", elapsed)
                return CallOutcome(OUTCOME_TERMINAL, elapsed, extra)
            if elapsed >= threshold:
                logger.info("通话窗口持续 %.1fs 仍在，判定：对方已接听（通话中）", elapsed)
                return CallOutcome(OUTCOME_ANSWERED, elapsed, extra)
            time.sleep(_OUTCOME_POLL)

    @staticmethod
    def _check_budget(start: float, timeout: float, step_name: str) -> None:
        """总超时预算检查：超出即抛出 1004。"""
        if time.time() - start > timeout:
            raise CallTimeoutError(f"步骤「{step_name}」超出总超时预算（{timeout:g}s）")

    @staticmethod
    def _test_result(target: str, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        """汇总校准结果：全部通过返回 200，否则返回第一个失败步骤对应的状态码。"""
        failed = next((s for s in steps if not s["ok"]), None)
        if failed is None:
            return {
                "code": CODE_SUCCESS,
                "message": "校准通过：目标与语音按钮均匹配成功",
                "data": {"target": target, "steps": steps},
            }
        return {
            "code": int(failed["code"]),
            "message": f"校准发现问题：「{failed['step']}」{failed['detail']}",
            "data": {"target": target, "steps": steps},
        }
