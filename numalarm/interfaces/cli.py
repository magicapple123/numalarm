"""命令行接口（CLI）。

命令总览：

    numalarm call [目标] [--timeout 30] [--silent/-s]     拨打（不传目标用 default_target）
    numalarm test [目标]                                  校准（不拨打）
    numalarm serve [--host H] [--port P]                  启动 HTTP 服务
    numalarm init                                         交互式初始化向导
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import click

from numalarm import __version__
from numalarm.common.config import CONFIG_ENV, ConfigManager, clear_hold, hold_active, set_hold
from numalarm.common.exceptions import CODE_SUCCESS
from numalarm.common.installations import (
    KNOWN_HOST_NAMES,
    collect_hook_refs,
    detect_skill_host,
    discover_installs,
    hook_kind,
    is_numalarm_command,
)


def _console_safe() -> None:
    """非 UTF-8 输出流兜底：不可编码的字符降级为 "?"，而不是抛 UnicodeEncodeError。

    英文区域 Windows 上 stdout/stderr 被管道或重定向时按 ANSI 代码页（cp1252 等）编码，
    打印中文会直接崩溃；真实控制台走 PEP 528 宽字符接口，不受影响。
    仅在入口（main）调用，避免影响被导入方（如测试的捕获流）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
        except Exception:  # 少数被替换过的流不支持 reconfigure
            pass


def _echo_result(result: dict, quiet: bool = False) -> None:
    """输出统一结果字典；静默模式不输出任何内容。"""
    if quiet:
        return
    click.echo(f"[{result.get('code')}] {result.get('message')}")
    data = result.get("data") or {}
    if data:
        click.echo(f"data: {data}")


@click.group()
@click.version_option(__version__, "--version", "-V", prog_name="numalarm")
def cli() -> None:
    """牛马铃（numalarm）：基于 UI 自动化的 QQ 语音通话提醒工具。"""


# ----------------------------------------------------------------------
# 基础拨打与校准
# ----------------------------------------------------------------------
@cli.command()
@click.argument("target", required=False)
@click.option("--timeout", default=None, type=click.IntRange(min=1), help="单次拨打建立阶段超时秒数（默认 30）")
@click.option("--silent", "-s", is_flag=True, help="静默执行：无控制台输出，完成自动退出")
@click.option("--reason", "-m", default=None, help="拨打原因（记录到日志与结果，用于 Agent 集成追溯）")
@click.option("--auto", is_flag=True, help="自动化触发：受用户在位检测控制（人在电脑前则跳过；人工命令默认不检测）")
@click.option("--force", is_flag=True, help="单次豁免防抖（默认防抖拦截窗口内重复拨打；互斥与在位检测不受影响）")
@click.option("--json", "as_json", is_flag=True, help="以单行 JSON 输出完整结构化结果（含失败原因，便于程序解析；失败仍退出码 1）")
def call(target: Optional[str], timeout: Optional[int], silent: bool, reason: Optional[str],
         auto: bool, force: bool, as_json: bool) -> None:
    """向目标好友拨打 QQ 语音通话（TARGET 支持昵称/备注/别名；不传则用 default_target）。"""
    from numalarm.interfaces.sdk import call_qq

    kwargs = {"timeout": timeout} if timeout is not None else {}
    if reason:
        kwargs["reason"] = reason
    # 人工命令默认不受在位检测限制；显式 --auto 才启用检测
    result = call_qq(target=target, silent=silent or as_json, auto=auto, force=force, **kwargs)
    if as_json:
        click.echo(json.dumps(result, ensure_ascii=False))
    else:
        _echo_result(result, quiet=silent)
    sys.exit(0 if result.get("code") == CODE_SUCCESS else 1)


@cli.command()
@click.argument("target", required=False)
def test(target: Optional[str]) -> None:
    """校准测试：搜索好友、打开聊天窗口、识别语音按钮（不拨打）。"""
    from numalarm.interfaces.sdk import test_call

    result = test_call(target=target)
    data = result.get("data") or {}
    for step in data.get("steps", []):
        mark = "PASS" if step.get("ok") else "FAIL"
        click.echo(f"[{mark}] {step.get('step')}  {step.get('detail', '')}")
    click.echo(f"[{result.get('code')}] {result.get('message')}")
    sys.exit(0 if result.get("code") == CODE_SUCCESS else 1)


@cli.command()
@click.option("--host", default=None, help="监听地址（默认读配置 127.0.0.1）")
@click.option("--port", default=None, type=click.IntRange(1, 65535), help="监听端口（默认读配置 18600）")
def serve(host: Optional[str], port: Optional[int]) -> None:
    """启动本地 HTTP 服务（供 Dify / Coze / n8n 等通过 HTTP 调用）。"""
    from numalarm.interfaces.api import run_server

    run_server(host=host, port=port)


# ----------------------------------------------------------------------
# 初始化向导
# ----------------------------------------------------------------------
@cli.command()
def init() -> None:
    """交互式初始化向导：生成 config.yaml，无需手动编辑配置文件。"""
    import yaml as pyyaml

    click.echo("== 牛马铃初始化向导（直接回车使用默认值）==")
    default_target = click.prompt("默认拨打目标（好友昵称/备注）", default="", show_default=False)

    alias: dict = {}
    click.echo("配置别名映射（别名回车留空即结束）。示例：老板 -> 张三")
    while True:
        key = click.prompt("别名", default="", show_default=False)
        if not key:
            break
        alias[key] = click.prompt(f"「{key}」对应的好友昵称/备注")

    wake = click.prompt("唤起 QQ 主面板快捷键", default="ctrl+alt+z")
    search = click.prompt("打开搜索框快捷键", default="ctrl+f")
    debounce = click.prompt(
        "防抖窗口秒数（同一目标 N 秒内只拨一次，0 为关闭）",
        type=click.IntRange(min=0),
        default=300,
    )
    port = click.prompt("HTTP 服务端口", type=click.IntRange(1, 65535), default=18600)
    log_level = click.prompt(
        "日志级别", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]), default="INFO"
    )

    config = {
        "default_target": default_target or None,
        "target_alias": alias,
        "hotkey": {"wake_panel": wake.lower(), "search": search.lower()},
        "debounce": {"enabled": debounce > 0, "window_seconds": float(debounce)},
        "server": {"port": port},
        "log": {"level": log_level},
    }
    out = Path.cwd() / "config.yaml"
    with open(out, "w", encoding="utf-8") as f:
        pyyaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    click.echo(f"配置已生成：{out}")
    click.echo("下一步：按 assets/README.md 截取语音按钮示例图，然后运行 numalarm test 校准。")


@cli.command()
def doctor() -> None:
    """环境自检：逐项检查依赖/配置/素材/进程，并给出修复建议（新用户先跑这个）。"""
    from numalarm.core.call_executor import ASSET_DIR, VOICE_BUTTON_SAMPLE
    from numalarm.core.state_detector import is_qq_running

    results = []

    def check(name: str, ok: bool, hint: str = "") -> bool:
        results.append(ok)
        mark = "PASS" if ok else "FAIL"
        suffix = "" if ok else (f"  -> {hint}" if hint else "")
        click.echo(f"[{mark}] {name}{suffix}")
        return ok

    def info(name: str, detail: str = "") -> None:
        """提示行：展示信息但不计入通过/失败（不影响退出码）。"""
        click.echo(f"[提示] {name}" + (f"  -> {detail}" if detail else ""))

    # 1. 必需依赖
    deps = {"pyautogui": "UI 自动化", "cv2": "图片匹配(opencv-python)", "PIL": "截图(Pillow)",
            "psutil": "进程检测", "yaml": "配置(PyYAML)", "pydantic": "参数校验",
            "click": "CLI", "fastapi": "HTTP 接口"}
    missing = []
    for mod, usage in deps.items():
        try:
            __import__(mod)
            check(f"依赖 {mod}（{usage}）", True)
        except ImportError:
            missing.append(mod)
            check(f"依赖 {mod}（{usage}）", False, "pip install -r requirements.txt")

    # 2. 配置文件
    cfg_path = ConfigManager.find_config_file()
    check("配置文件 config.yaml", bool(cfg_path),
          f"复制 config.example.yaml 为 config.yaml（或 numalarm init）；也可用环境变量 {CONFIG_ENV} 指定路径")
    try:
        cfg = ConfigManager.instance().config
        check("default_target 已设置", bool(cfg.default_target),
              "编辑 config.yaml 的 default_target 或 target_alias")
    except Exception as exc:  # noqa: BLE001
        cfg = None
        check("配置解析", False, str(exc))

    # 3. 素材
    ring_tpl = ASSET_DIR / "call_ringing_sample.png"
    check("语音按钮模板 assets/voice_button_sample.png", VOICE_BUTTON_SAMPLE.is_file(),
          "按 assets/README.md 截取聊天窗口「语音通话」按钮")
    check("响铃文案模板 assets/call_ringing_sample.png", ring_tpl.is_file(),
          "按 assets/README.md 截取通话窗口「等待对方接听」文字（缺失时回退低精度阈值判定）")

    # 4. QQ 进程
    try:
        qq = is_qq_running(cfg.qq_process_name if cfg else "QQ.exe")
        check("QQ 进程运行中", qq, "启动并登录 PC 版 QQ（工具不会自动登录）")
    except Exception:  # noqa: BLE001 psutil 缺失时跳过
        pass

    # 5. 语音提醒可选依赖（pywin32 提供 SAPI TTS）
    if sys.platform == "win32":
        try:
            import win32com.client  # noqa: F401
            check("pywin32（语音提醒）", True)
        except ImportError:
            check("pywin32（语音提醒）", False, "pip install pywin32（不影响核心拨打，仅影响接听后语音提醒）")

    # 6. 音频默认设备（启用语音提醒时：检查虚拟声卡抢占默认扬声器 / 默认录音指向）
    media_cfg = ConfigManager.instance().config.media
    if sys.platform == "win32" and media_cfg.speak_on_answer:
        try:
            from numalarm.core.audio_devices import get_default_devices

            playback, capture = get_default_devices()
            if playback is None and capture is None:
                check("默认音频设备读取", False, "COM 读取失败，跳过该项")
            else:
                occupied = bool(playback) and ("cable" in playback.lower() or "vb-audio" in playback.lower())
                check("默认扬声器未被虚拟声卡抢占", not occupied,
                      f"当前默认播放为「{playback}」——系统声音会全部进入虚拟声卡，"
                      "请在声音设置切回真实扬声器")
                if capture and "cable output" in capture.lower():
                    check("默认录音指向（语音提醒）", True, capture)
                else:
                    check("默认录音指向（语音提醒）", False,
                          f"当前为「{capture or '未知'}」——语音提醒需要设为 CABLE Output（真人通话时再切回）")
        except Exception as exc:  # noqa: BLE001 设备读取失败不影响其余检查
            check("默认音频设备读取", False, str(exc))

    # 6. 配置解析错误可见性（损坏的 config.yaml 会被静默回退默认值）
    load_err = getattr(ConfigManager.instance(), "load_error", None)
    check("配置文件可解析", not load_err,
          f"{load_err}（已回退默认值，所有配置项不生效；修复后重跑 doctor）")

    # 7. hook 引用完整性：命令指向的安装目录必须存在，否则 hook 会静默失效
    settings_map = {name: p for name, p in KNOWN_HOSTS.items() if p.is_file()}
    refs = collect_hook_refs(settings_map)
    broken = [r for r in refs if r.install_dir is None or not r.install_dir.is_dir()]
    check(f"hook 命令指向目录存在（{len(refs)} 条引用）", not broken,
          "存在指向缺失目录的 hook（会静默失效）；运行 numalarm hook install --host <宿主> 重新指向")

    # 8. 多副本体检与环境提示（提示行，不计入通过/失败）
    installs = discover_installs(_current_install_dir())
    others = [i for i in installs if not i.is_current]
    for one in others:
        state = "存在" if one.exists else "目录缺失"
        ver = f"v{one.version}" if one.version else "版本未知"
        head = f"，git {one.git_head}" if one.git_head else ""
        cfg_state = "，有 config" if one.has_config else ""
        calib = "，模板已校准" if one.git_dirty_assets else ""
        info(f"发现其他 numalarm 副本（{state}）", f"{one.path}（{ver}{head}{cfg_state}{calib}）")
    if others:
        info("多副本提示", "多份安装共享 ~/.numalarm（锁/防抖/hold/心跳）；"
                           "numalarm installs 查看详情，installs --apply 统一 hook 指向")
    info("运行环境", f"虚拟环境 {sys.prefix}" if sys.prefix != sys.base_prefix else "系统 Python（建议按 README 使用 .venv）")
    effective_cfg = ConfigManager.find_config_file()
    info("生效配置", str(effective_cfg) if effective_cfg else "未找到，使用内置默认值")

    # 汇总
    failed = results.count(False)
    if failed:
        click.echo(f"\n{failed} 项未通过，按上方提示修复后重试。")
        sys.exit(1)
    click.echo("\n全部通过：可以 numalarm test <目标> 校准后使用。")


@cli.command()
@click.option("--yes", "-y", is_flag=True, help="跳过确认，直接清理运行时残留与快捷方式")
def uninstall(yes: bool) -> None:
    """完全卸载清理：移除 Hook 注册、桌面快捷方式与运行时残留，实现零残留移除。

    技能/仓库目录（含独立虚拟环境）与可选安装的虚拟声卡需按提示手动移除。
    """
    click.echo("== 牛马铃卸载清理 ==")

    # 1) 宿主 Hook 注册（拨打 + 心跳，全量移除）
    click.echo("\n[1/4] 宿主 Hook 注册（拨打 + 心跳）")
    _remove_numalarm_hooks([p for p in KNOWN_HOSTS.values() if p.is_file()], kinds=None)

    # 2) 桌面快捷方式（仅 Windows）
    click.echo("\n[2/4] 桌面快捷方式")
    if sys.platform == "win32":
        try:
            import win32com.client

            shell = win32com.client.Dispatch("WScript.Shell")
            desktop = Path(shell.SpecialFolders("Desktop"))
            found = []
            for lnk in sorted(desktop.glob("*.lnk")):
                try:
                    args = shell.CreateShortCut(str(lnk)).Arguments or ""
                    if "numalarm" in args:
                        found.append(lnk)
                except Exception:  # noqa: BLE001 单个解析失败跳过
                    continue
            if not found:
                click.echo("未发现相关快捷方式")
            for lnk in found:
                if yes or click.confirm(f"删除快捷方式 {lnk.name}？", default=False):
                    lnk.unlink()
                    click.echo(f"已删除 {lnk.name}")
        except ImportError:
            click.echo("pywin32 未安装，跳过快捷方式清理")
    else:
        click.echo("非 Windows 平台，跳过")

    # 3) 运行时残留（~/.numalarm：防抖状态与跨进程锁文件）
    click.echo("\n[3/4] 运行时残留（~/.numalarm：防抖状态与锁文件）")
    state_dir = ConfigManager.state_dir()
    if any(state_dir.iterdir()):
        if yes or click.confirm(f"删除 {state_dir}？", default=False):
            shutil.rmtree(state_dir, ignore_errors=True)
            click.echo("已清理")
    else:
        click.echo("无残留")

    # 4) 手动步骤指引（均为可选安装项，未安装可忽略）
    click.echo("\n[4/4] 以下组件请按需手动移除（删除后设备即零残留）：")
    click.echo("- 技能/仓库目录：含独立虚拟环境（.venv）与配置，直接删除整个目录即可（建议最后删）")
    click.echo("- 若曾执行 pip install -e .：运行 pip uninstall numalarm")
    click.echo("- 若曾安装虚拟声卡 VB-Cable：先在声音设置切回真实麦克风/扬声器，"
               "再到 Windows「设置 - 应用」卸载 VB-Audio Virtual Cable")
    click.echo("- 看门狗计划任务（若装过）：numalarm watchdog uninstall")
    click.echo("- Agent 长期记忆：numalarm 无法访问宿主的记忆系统，"
               "请在对话中让 Agent 删除其记忆里登记的 numalarm 约定与路径")
    click.echo("\n完成：除上述手动项外，设备无任何残留。")


@cli.command()
@click.option("--minutes", "-m", "minutes_", default=30, show_default=True, type=click.IntRange(min=1),
              help="暂停时长（分钟），到期自动恢复提醒（防 Agent 忘记摘牌导致漏提醒）")
@click.option("--clear", is_flag=True, help="清除暂停：立即恢复 Stop/看门狗拨打")
def hold(minutes_: int, clear: bool) -> None:
    """暂停/恢复自动提醒（供 Agent 在「回合将自动继续」时使用）。

    回合结束后将自动继续（等待子代理汇报/CI/定时恢复等，无需用户操作）时运行
    `numalarm hold` 暂停拨打；需要用户操作或任务最终交付时运行 `numalarm hold --clear`。
    """
    if clear:
        clear_hold()
        click.echo("提醒已恢复")
        return
    set_hold(minutes_)
    click.echo(f"自动提醒已暂停 {minutes_} 分钟（到期自动恢复）")


@cli.command()
@click.option("--dir", "install_dir_opt", type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=None, help="要更新的 numalarm 安装目录（默认为当前代码所在安装目录）")
def update(install_dir_opt: Optional[Path]) -> None:
    """检查并更新 numalarm：有新版本才更新（保留 config.yaml 与校准模板），否则提示已最新。"""
    install_dir = install_dir_opt.resolve() if install_dir_opt else Path(__file__).resolve().parents[2]
    if not (install_dir / ".git").exists():
        click.echo(f"目录 {install_dir} 不是 git 克隆安装，无法自动更新。")
        click.echo("- pip 安装的用户：pip install --upgrade numalarm")
        click.echo("- 手动下载的用户：重新下载覆盖（保留 config.yaml 与 assets 下你校准的模板）")
        sys.exit(1)

    def _git(*args: str) -> str:
        try:
            r = subprocess.run(["git", *args], cwd=str(install_dir), capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
        except FileNotFoundError as exc:  # git 不在 PATH
            raise RuntimeError("未找到 git 命令") from exc
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or "git 命令失败").strip())
        return (r.stdout or "").strip()

    click.echo("正在检查更新…")
    try:
        _git("fetch", "origin")
        local = _git("rev-parse", "HEAD")
        branch = _git("rev-parse", "--abbrev-ref", "HEAD") or "main"
        remote = _git("rev-parse", f"origin/{branch}")
    except RuntimeError as exc:
        click.echo(f"检查更新失败：{exc}")
        sys.exit(1)

    if local == remote:
        click.echo(f"已是最新版本（v{__version__}），无需更新。")
        return

    click.echo(f"发现新版本（{local[:7]} -> {remote[:7]}），开始更新…")

    # 1. 备份用户资产（config.yaml 与校准模板）
    backup_root = ConfigManager.state_dir() / "backups" / time.strftime("%Y%m%d-%H%M%S")
    backup_root.mkdir(parents=True, exist_ok=True)
    protected = ["config.yaml", "assets/voice_button_sample.png", "assets/call_ringing_sample.png"]
    for rel in protected:
        src = install_dir / rel
        if src.is_file():
            dst = backup_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    click.echo(f"用户配置与模板已备份：{backup_root}")

    # 2. 还原 tracked 文件的本地改动（已备份），避免 pull 冲突
    try:
        if _git("status", "--porcelain"):
            _git("checkout", "--", ".")
    except RuntimeError as exc:
        click.echo(f"更新失败（无法恢复本地改动）：{exc}")
        sys.exit(1)

    # 3. 拉取最新代码
    try:
        _git("pull", "--ff-only", "origin", branch)
    except RuntimeError as exc:
        click.echo(f"更新失败：{exc}")
        click.echo(f"你的 config.yaml 与校准模板已备份于：{backup_root}")
        sys.exit(1)

    # 4. 恢复用户资产（校准版优先），上游模板与用户版本不一致时另存备份供对比
    restored = []
    for rel in ("config.yaml", "assets/voice_button_sample.png", "assets/call_ringing_sample.png"):
        user_copy = backup_root / rel
        if not user_copy.is_file():
            continue
        r = subprocess.run(["git", "show", f"{remote}:{rel}"], cwd=str(install_dir), capture_output=True)
        upstream_bytes = r.stdout if r.returncode == 0 and r.stdout else None
        user_bytes = user_copy.read_bytes()
        if upstream_bytes is not None and upstream_bytes != user_bytes:
            upstream_copy = backup_root / "upstream" / rel
            upstream_copy.parent.mkdir(parents=True, exist_ok=True)
            upstream_copy.write_bytes(upstream_bytes)
            click.echo(f"上游「{rel}」与你的版本不同：已保留你的校准版，上游版本另存于 {upstream_copy}")
        dst = install_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(user_copy, dst)
        restored.append(rel)
    if restored:
        click.echo("已恢复你的配置与校准模板：" + "、".join(restored))

    # 5. 同步虚拟环境依赖
    venv_dir = install_dir / ".venv"
    venv_python = venv_dir / ("Scripts" if sys.platform == "win32" else "bin") / (
        "python.exe" if sys.platform == "win32" else "python")
    if venv_python.is_file():
        click.echo("正在同步虚拟环境依赖…")
        r = subprocess.run([str(venv_python), "-m", "pip", "install", "-r", "requirements.txt"],
                           cwd=str(install_dir), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        tail = (r.stdout or r.stderr or "").strip().splitlines()[-3:]
        for line in tail:
            click.echo(f"  {line}")
        if r.returncode != 0:
            click.echo(f"[失败] 依赖同步失败（退出码 {r.returncode}），请手动运行："
                       f"\"{venv_python}\" -m pip install -r requirements.txt")
            sys.exit(1)
    else:
        click.echo("未检测到虚拟环境（.venv），跳过依赖同步")

    # 6. 刷新已有宿主上的 hook 命令（拨打 + 心跳，均不扩大注册范围）
    existing_hosts = [p for p in KNOWN_HOSTS.values() if p.is_file()]
    _register_hooks({event: _hook_command() for event in HOOK_EVENTS}, existing_hosts, create_missing=False)
    _register_hooks({"PreToolUse": _hook_cmdline("heartbeat"), "SessionEnd": _hook_cmdline("heartbeat --clear")},
                    existing_hosts, create_missing=False)

    # 7. 报告新版本
    try:
        init_text = (install_dir / "numalarm" / "__init__.py").read_text(encoding="utf-8")
        m = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
        new_version = m.group(1) if m else "未知"
    except OSError:
        new_version = "未知"
    click.echo(f"更新完成：v{__version__} -> v{new_version}")
    click.echo("建议：运行 numalarm test <目标> 校准；重启宿主会话使技能更新生效。")


# ----------------------------------------------------------------------
# 看门狗：Agent 硬崩溃兜底（心跳超时自动拨打）
# ----------------------------------------------------------------------
WATCHDOG_TASK_NAME = "numalarm-watchdog"


@cli.command()
@click.option("--clear", is_flag=True, help="清除心跳（任务正常结束/宿主正常关闭时调用）")
def heartbeat(clear: bool) -> None:
    """刷新/清除任务心跳：看门狗据此判断 Agent 是否硬崩溃（跨宿主通用）。"""
    hb = ConfigManager.state_dir() / "heartbeat.json"
    if clear:
        hb.unlink(missing_ok=True)
        click.echo("心跳已清除")
        return
    hb.parent.mkdir(parents=True, exist_ok=True)
    hb.write_text(json.dumps({"ts": time.time(), "pid": os.getpid()}), encoding="utf-8")
    click.echo("心跳已刷新")


@cli.command("watchdog-run")
@click.option("--silent", "-s", is_flag=True, help="静默执行（计划任务调用时使用）")
def watchdog_run(silent: bool) -> None:
    """内部命令：看门狗单次检查（由计划任务每分钟调用，勿手动运行）。"""
    cfg = ConfigManager.instance().config
    w = cfg.watchdog
    if not w.enabled:
        return
    hb_path = ConfigManager.state_dir() / "heartbeat.json"
    if not hb_path.is_file():
        return  # 无任务在跑
    try:
        ts = float(json.loads(hb_path.read_text(encoding="utf-8")).get("ts", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        return
    age = time.time() - ts
    if age < float(w.stale_seconds):
        return  # 心跳新鲜：Agent 正常
    if hold_active():
        return  # Agent 已挂「免打扰牌」（回合将自动继续），暂停拨打
    from numalarm.core.state_detector import is_qq_running

    if not is_qq_running(cfg.qq_process_name):
        return  # QQ 都没开（设备关机/未登录），打了也没意义
    from numalarm.interfaces.sdk import call_qq

    result = call_qq(
        reason=f"看门狗：Agent 心跳超时 {int(age)} 秒，疑似任务异常中断",
        auto=True,  # 人在电脑前自动跳过
    )
    if result.get("code") == CODE_SUCCESS:
        # 已触达 / 已跳过 / 已达上限：清心跳，防止每分钟重复触发
        hb_path.unlink(missing_ok=True)
    if not silent:
        click.echo(f"[{result.get('code')}] {result.get('message')}")


@cli.group()
def watchdog() -> None:
    """看门狗：Agent 硬崩溃兜底（计划任务每分钟检查心跳）。"""


def _watchdog_script_path() -> Path:
    return ConfigManager.state_dir() / "watchdog-task.cmd"


def _watchdog_script_text() -> str:
    install_dir = Path(__file__).resolve().parents[2]
    python_exe = Path(sys.executable).resolve()
    return (
        "@echo off\r\n"
        f'cd /d "{install_dir}"\r\n'
        f'"{python_exe}" -m numalarm.interfaces.cli watchdog-run --silent\r\n'
    )


@watchdog.command("install")
@click.option("--host", "hosts", default=None,
              help="心跳 hook 目标宿主：workbuddy / claude / codebuddy / all；默认自动探测（计划任务本身始终全机注册）")
@click.option("--settings", "settings_paths", multiple=True, type=click.Path(dir_okay=False, path_type=Path),
              help="自定义宿主的 settings.json 路径（心跳 hook 写入目标），可多次传入；优先于 --host")
def watchdog_install(hosts: Optional[str], settings_paths: tuple) -> None:
    """注册每分钟一次的看门狗计划任务（全机唯一），并向目标宿主注册心跳 hook。"""
    if sys.platform == "win32":
        script = _watchdog_script_path()
        script.write_text(_watchdog_script_text(), encoding="utf-8")
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", WATCHDOG_TASK_NAME],
                       capture_output=True)  # 幂等：先删旧任务（不存在时报错忽略）
        r = subprocess.run(
            ["schtasks", "/Create", "/F", "/TN", WATCHDOG_TASK_NAME, "/SC", "MINUTE", "/MO", "1",
             "/TR", f'"{script}"'],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            click.echo(f"[失败] 计划任务注册失败：{(r.stderr or r.stdout).strip()}")
            sys.exit(1)
        click.echo(f"[OK] 计划任务 {WATCHDOG_TASK_NAME} 已注册（每分钟检查一次，脚本：{script}）")
    else:
        cron_line = f"* * * * * cd '{Path(__file__).resolve().parents[2]}' && '{sys.executable}' -m numalarm.interfaces.cli watchdog-run --silent"
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        lines = (r.stdout or "").splitlines() if r.returncode == 0 else []
        if any("numalarm" in line for line in lines):
            click.echo("[跳过] crontab 中已存在 numalarm 看门狗")
        else:
            new_crontab = "\n".join(lines + [cron_line]) + "\n"
            p = subprocess.run(["crontab", "-"], input=new_crontab, text=True, capture_output=True)
            if p.returncode != 0:
                click.echo(f"[失败] crontab 写入失败：{(p.stderr or '').strip()}")
                sys.exit(1)
            click.echo("[OK] crontab 看门狗已注册（每分钟检查一次）")

    # 心跳来源注册：PreToolUse 刷新心跳 / SessionEnd 清心跳（作用域由 --host/--settings 决定）
    hb_cmd = _hook_cmdline("heartbeat")
    hb_clear_cmd = _hook_cmdline("heartbeat --clear")
    targets = _resolve_hook_targets(hosts, settings_paths)
    if targets:
        _register_hooks({"PreToolUse": hb_cmd, "SessionEnd": hb_clear_cmd}, targets, create_missing=True)
    else:
        click.echo("[注意] 未确定心跳 hook 目标宿主（可用 --host/--settings 指定）；"
                   "无 hook 机制的宿主可在长任务中定期运行 numalarm heartbeat（见 SKILL.md）")
    click.echo("计划任务为全机唯一（--host/--settings 只影响心跳 hook 写入范围）")
    click.echo("移除：numalarm watchdog uninstall")


@watchdog.command("uninstall")
@click.option("--host", "hosts", default=None, help="心跳 hook 目标宿主：workbuddy / claude / codebuddy / all；默认自动探测")
@click.option("--settings", "settings_paths", multiple=True, type=click.Path(dir_okay=False, path_type=Path),
              help="自定义宿主的 settings.json 路径，可多次传入；优先于 --host")
def watchdog_uninstall(hosts: Optional[str], settings_paths: tuple) -> None:
    """移除看门狗计划任务（全机唯一）与心跳 hook（只删心跳类，拨打类 hook 不受影响）。"""
    if sys.platform == "win32":
        r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", WATCHDOG_TASK_NAME],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        click.echo("[OK] 计划任务已移除" if r.returncode == 0 else "[跳过] 计划任务不存在")
        _watchdog_script_path().unlink(missing_ok=True)
    else:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if r.returncode == 0:
            kept = [line for line in (r.stdout or "").splitlines() if "numalarm" not in line]
            subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n", text=True, capture_output=True)
        click.echo("[OK] crontab 看门狗已移除")
    _remove_numalarm_hooks(_resolve_hook_targets(hosts, settings_paths), kinds={"heartbeat"})
    click.echo("心跳 hook 已移除（拨打类 hook 不受影响）")


@watchdog.command("status")
def watchdog_status() -> None:
    """查看看门狗计划任务与心跳 hook 注册状态。"""
    if sys.platform == "win32":
        r = subprocess.run(["schtasks", "/Query", "/TN", WATCHDOG_TASK_NAME],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        click.echo(f"计划任务 {WATCHDOG_TASK_NAME}: " + ("已注册" if r.returncode == 0 else "未注册"))
    else:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        found = r.returncode == 0 and any("numalarm" in line for line in (r.stdout or "").splitlines())
        click.echo("crontab 看门狗: " + ("已注册" if found else "未注册"))
    for settings_path in (p for p in KNOWN_HOSTS.values() if p.is_file()):
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        hb_found = []
        for event in ("PreToolUse", "SessionEnd"):
            for entry in (data.get("hooks") or {}).get(event) or []:
                if _entry_has_numalarm(entry):
                    hb_found.append(event)
                    break
        if hb_found:
            click.echo(f"{settings_path.parent.name}: 心跳 hook 已注册（{'、'.join(hb_found)}）")


def main() -> None:
    """CLI 入口（pyproject.toml console_scripts 指向此处）。"""
    _console_safe()
    cli()


# ----------------------------------------------------------------------
# 宿主 Hook 集成（跨 Agent 通用）
# ----------------------------------------------------------------------
# 兼容 Claude Code hooks schema 的宿主（WorkBuddy / Claude Code / CodeBuddy 等）
# Stop = Agent 回合结束（任务交付时刻）：配合在位检测实现「人离开后任务完成自动响铃」
HOOK_EVENTS = ("Notification", "PermissionRequest", "Stop")
KNOWN_HOSTS = {name: Path.home() / f".{name}" / "settings.json" for name in KNOWN_HOST_NAMES}

# hook/watchdog 的默认作用域：detected=所有已探测宿主（默认，兼容既有行为）；
# current=仅当前安装所在宿主（设置环境变量 NUMALARM_HOOK_SCOPE=current 切换）
HOOK_SCOPE_DEFAULT = os.environ.get("NUMALARM_HOOK_SCOPE", "detected")


def _current_install_dir() -> Path:
    """当前代码所在的安装目录。"""
    return Path(__file__).resolve().parents[2]


def _resolve_embed_python(install_dir: Path) -> Path:
    """hook 内嵌解释器：优先安装目录内 .venv 的 pythonw（免黑框）/python，缺则回退当前解释器。"""
    scripts = install_dir / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
    names = ("pythonw.exe", "python.exe") if sys.platform == "win32" else ("python",)
    for name in names:
        candidate = scripts / name
        if candidate.is_file():
            return candidate.resolve()
    python_exe = Path(sys.executable)
    pythonw = python_exe.with_name("pythonw.exe")
    return (pythonw if pythonw.is_file() else python_exe).resolve()


def _hook_cmdline_for(cli_args: str, *, install_dir: Path, config_path: Path, python_exe: Path) -> str:
    """按指定安装目录/配置/解释器生成 hook 命令行（cd 固定安装目录，配置路径写死）。"""
    return (
        f"cd '{install_dir.as_posix()}' && "
        f"NUMALARM_CONFIG='{config_path.as_posix()}' '{python_exe.as_posix()}' "
        f"-m numalarm.interfaces.cli {cli_args}"
    )


def _hook_cmdline(cli_args: str) -> str:
    """生成 hook 命令行：cd 固定到安装目录（保证 numalarm 包可导入），配置路径写死。"""
    cfg = ConfigManager.instance()
    config_path = (cfg.config_path or (Path.cwd() / "config.yaml")).resolve()
    install_dir = _current_install_dir()
    return _hook_cmdline_for(
        cli_args,
        install_dir=install_dir,
        config_path=config_path,
        python_exe=_resolve_embed_python(install_dir),
    )


def _hook_command() -> str:
    """生成拨打类 hook（Notification/PermissionRequest/Stop）命令：静默自动拨打 default_target。"""
    return _hook_cmdline("call --silent --auto")


def _hook_entry(cmd: str) -> dict:
    return {"matcher": "", "hooks": [{"type": "command", "command": cmd, "async": True}]}


def _entry_has_numalarm(entry: Any) -> bool:
    try:
        return "numalarm" in json.dumps(entry)
    except (TypeError, ValueError):
        return False


def _resolve_hook_targets(hosts: Optional[str], settings_paths: tuple) -> List[Path]:
    if settings_paths:
        return [Path(p) for p in settings_paths]
    if hosts and hosts != "all":
        if hosts not in KNOWN_HOSTS:
            raise click.BadParameter(f"未知宿主 {hosts}（可选：{', '.join(KNOWN_HOSTS)} / all）")
        return [KNOWN_HOSTS[hosts]]
    if hosts is None and HOOK_SCOPE_DEFAULT == "current":
        # 仅当前安装所在宿主（安装器与「严格单宿主」用户使用；由安装目录反推宿主名）
        host = detect_skill_host(_current_install_dir())
        return [KNOWN_HOSTS[host]] if host else []
    return [p for p in KNOWN_HOSTS.values() if p.is_file()]


@cli.group()
def hook() -> None:
    """宿主 Hook 集成：Agent 停下等待用户时自动拨打（Claude Code 兼容宿主）。"""


@hook.command("install")
@click.option("--host", "hosts", default=None, help="目标宿主：workbuddy / claude / codebuddy / all；默认自动探测")
@click.option("--settings", "settings_paths", multiple=True, type=click.Path(dir_okay=False, path_type=Path),
              help="自定义宿主的 settings.json 路径（Claude Code 兼容 schema），可多次传入；优先于 --host")
def hook_install(hosts: Optional[str], settings_paths: tuple) -> None:
    """向 Notification / PermissionRequest 事件注册/更新静默拨打 hook（幂等，保留既有配置）。"""
    targets = _resolve_hook_targets(hosts, settings_paths)
    if not targets:
        click.echo("未探测到已安装的兼容宿主（settings.json 不存在）；可用 --host 或 --settings 指定。")
        sys.exit(1)
    _register_hooks({event: _hook_command() for event in HOOK_EVENTS}, targets, create_missing=True)
    click.echo("重启对应宿主会话后生效。移除：numalarm hook uninstall")


def _register_hooks(event_cmds: Dict[str, str], targets: List[Path], create_missing: bool) -> None:
    """向目标 settings.json 注册/更新 numalarm hook。

    :param event_cmds: 事件名 -> hook 命令 的映射
    :param create_missing: True 时在无 numalarm 条目的事件上新增注册；
        False 时仅更新已存在条目的命令（供版本更新刷新使用，不扩大注册范围）。
    """
    for settings_path in targets:
        raw = ""
        data: dict = {}
        try:
            if settings_path.is_file():
                raw = settings_path.read_text(encoding="utf-8")
                data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            click.echo(f"[跳过] {settings_path} 不可读：{exc}")
            continue
        if not isinstance(data, dict):
            click.echo(f"[跳过] {settings_path} 结构异常（顶层非对象）")
            continue

        hooks_cfg = data.setdefault("hooks", {})
        if not isinstance(hooks_cfg, dict):
            click.echo(f"[跳过] {settings_path} 的 hooks 字段结构异常")
            continue

        changed = []
        for event, cmd in event_cmds.items():
            entries = hooks_cfg.setdefault(event, [])
            if not isinstance(entries, list):
                continue
            updated = False
            for entry in entries:
                # 已有 numalarm 条目：原地更新命令（保持其余字段），实现升级
                if _entry_has_numalarm(entry) and isinstance(entry, dict):
                    for h in entry.get("hooks", []) or []:
                        if isinstance(h, dict) and "numalarm" in h.get("command", ""):
                            h["command"] = cmd
                            updated = True
            if not updated and create_missing and not any(_entry_has_numalarm(e) for e in entries):
                entries.append(_hook_entry(cmd))
                updated = True
            if updated:
                changed.append(event)

        if not changed:
            click.echo(f"[跳过] {settings_path} 无变更")
            continue

        backup = settings_path.with_name(settings_path.name + ".numalarm-bak")
        try:
            backup.write_text(raw, encoding="utf-8")
            settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            click.echo(f"[失败] {settings_path} 写入失败：{exc}")
            continue
        click.echo(f"[OK] {settings_path} 已注册/更新 hook：{', '.join(changed)}（备份：{backup.name}）")


def _entry_numalarm_kinds(entry: Any) -> set:
    """entry 内部 numalarm 命令的种类集合；无法分类的命令记入 ``"unknown"``。"""
    kinds = set()
    if not isinstance(entry, dict):
        return kinds
    for h in entry.get("hooks") or []:
        if not isinstance(h, dict):
            continue
        cmd = h.get("command")
        if isinstance(cmd, str) and is_numalarm_command(cmd):
            kinds.add(hook_kind(cmd) or "unknown")
    return kinds


def _remove_numalarm_hooks(targets: List[Path], kinds: Optional[set] = None) -> None:
    """从指定 settings.json 中移除 numalarm 注册的 hook（只删自身条目，其余配置原样保留）。

    :param kinds: None=整条移除全部 numalarm 条目（numalarm uninstall 使用）；
        ``{"dial"}``/``{"heartbeat"}``=只移除对应类型的内部 hook，条目中其余 hooks
        原样保留（hook uninstall 只删拨打类、watchdog uninstall 只删心跳类）；
        无法分类的 numalarm 命令一律保留并提示（宁留不误删）。
    """
    for settings_path in targets:
        if not settings_path.is_file():
            continue
        try:
            raw = settings_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            click.echo(f"[跳过] {settings_path} 不可读：{exc}")
            continue
        hooks_cfg = data.get("hooks") or {}
        removed = []
        unclassified = 0
        for event in list(hooks_cfg.keys()):
            entries = hooks_cfg.get(event) or []
            if not isinstance(entries, list):
                continue
            kept_entries = []
            event_changed = False
            for entry in entries:
                if not (isinstance(entry, dict) and _entry_has_numalarm(entry)):
                    kept_entries.append(entry)
                    continue
                if kinds is None:
                    event_changed = True  # 整条移除
                    continue
                inner = entry.get("hooks")
                if not isinstance(inner, list):
                    kept_entries.append(entry)  # 形状异常：保守保留
                    continue
                kept_inner = []
                for h in inner:
                    cmd = h.get("command") if isinstance(h, dict) else None
                    if isinstance(cmd, str) and is_numalarm_command(cmd):
                        k = hook_kind(cmd)
                        if k in kinds:
                            event_changed = True
                            continue  # 命中目标类型：移除该条
                        if k is None:
                            unclassified += 1  # 无法分类：保留并统计
                    kept_inner.append(h)
                if kept_inner:
                    new_entry = dict(entry)
                    new_entry["hooks"] = kept_inner
                    kept_entries.append(new_entry)
                # kept_inner 为空：整条 entry 不再保留
            if event_changed:
                removed.append(event)
                if kept_entries:
                    hooks_cfg[event] = kept_entries
                else:
                    hooks_cfg.pop(event, None)
        if unclassified:
            click.echo(f"[注意] {settings_path} 有 {unclassified} 条无法分类的 numalarm 条目已保留，请手动检查")
        if not removed:
            click.echo(f"[跳过] {settings_path} 无匹配的 numalarm hook")
            continue
        backup = settings_path.with_name(settings_path.name + ".numalarm-bak")
        try:
            backup.write_text(raw, encoding="utf-8")
            settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            click.echo(f"[失败] {settings_path} 写入失败：{exc}")
            continue
        click.echo(f"[OK] {settings_path} 已移除 hook：{', '.join(removed)}（备份：{backup.name}）")


@hook.command("uninstall")
@click.option("--host", "hosts", default=None, help="目标宿主：workbuddy / claude / codebuddy / all；默认自动探测")
@click.option("--settings", "settings_paths", multiple=True, type=click.Path(dir_okay=False, path_type=Path),
              help="自定义宿主的 settings.json 路径，可多次传入；优先于 --host")
def hook_uninstall(hosts: Optional[str], settings_paths: tuple) -> None:
    """移除 numalarm 注册的拨打类 hook（心跳 hook 与其余配置原样保留）。"""
    _remove_numalarm_hooks(_resolve_hook_targets(hosts, settings_paths), kinds={"dial"})


@hook.command("status")
@click.option("--settings", "settings_paths", multiple=True, type=click.Path(dir_okay=False, path_type=Path),
              help="额外检查的自定义宿主 settings.json 路径，可多次传入")
def hook_status(settings_paths: tuple) -> None:
    """查看各宿主的 numalarm hook 注册状态。"""
    targets = {name: path for name, path in KNOWN_HOSTS.items()}
    for i, p in enumerate(settings_paths):
        targets[f"自定义-{i + 1}"] = p
    for name, path in targets.items():
        if not path.is_file():
            click.echo(f"{name:<10} 未安装宿主（无 settings.json）")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            click.echo(f"{name:<10} 配置不可读：{exc}")
            continue
        found = []
        for event in HOOK_EVENTS:
            for entry in (data.get("hooks") or {}).get(event) or []:
                if _entry_has_numalarm(entry):
                    found.append(event)
                    break
        state = "已注册：" + "、".join(found) if found else "未注册"
        click.echo(f"{name:<10} {state}")


def _rewrite_hooks_for_install(targets: List[Path], *, install_dir: Path, config_path: Path, python_exe: Path) -> None:
    """把目标 settings.json 中全部 numalarm hook 命令改写为指向指定安装（先备份）。

    拨打类与心跳类分别按生成器重写；无法分类的 numalarm 命令保守跳过。
    """
    new_dial = _hook_cmdline_for("call --silent --auto", install_dir=install_dir,
                                 config_path=config_path, python_exe=python_exe)
    new_hb = _hook_cmdline_for("heartbeat", install_dir=install_dir,
                               config_path=config_path, python_exe=python_exe)
    new_hb_clear = _hook_cmdline_for("heartbeat --clear", install_dir=install_dir,
                                     config_path=config_path, python_exe=python_exe)
    for settings_path in targets:
        if not settings_path.is_file():
            continue
        try:
            raw = settings_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            click.echo(f"[跳过] {settings_path} 不可读：{exc}")
            continue
        hooks_cfg = data.get("hooks") or {}
        changed = []
        for event, entries in hooks_cfg.items():
            if not isinstance(entries, list):
                continue
            event_changed = False
            for entry in entries:
                if not (isinstance(entry, dict) and _entry_has_numalarm(entry)):
                    continue
                for h in entry.get("hooks") or []:
                    if not isinstance(h, dict):
                        continue
                    cmd = h.get("command")
                    if not (isinstance(cmd, str) and is_numalarm_command(cmd)):
                        continue
                    if hook_kind(cmd) is None:
                        continue  # 无法分类：保留原样
                    if "heartbeat --clear" in cmd:
                        new_cmd = new_hb_clear
                    elif hook_kind(cmd) == "heartbeat":
                        new_cmd = new_hb
                    else:
                        new_cmd = new_dial
                    if new_cmd != cmd:
                        h["command"] = new_cmd
                        event_changed = True
            if event_changed:
                changed.append(event)
        if not changed:
            click.echo(f"[跳过] {settings_path} 无需改写")
            continue
        backup = settings_path.with_name(settings_path.name + ".numalarm-bak")
        try:
            backup.write_text(raw, encoding="utf-8")
            settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            click.echo(f"[失败] {settings_path} 写入失败：{exc}")
            continue
        click.echo(f"[OK] {settings_path} 已改指：{', '.join(changed)}（备份：{backup.name}）")


@cli.command("installs")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出（供 Agent 解析）")
@click.option("--apply", "apply_", is_flag=True, help="将各宿主 numalarm hook 统一改指到目标安装（先备份）")
@click.option("--dir", "target_dir_opt", type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=None, help="--apply 的目标安装目录（默认为当前代码所在安装目录）")
@click.option("--yes", "-y", is_flag=True, help="--apply 时跳过风险确认")
def installs_cmd(as_json: bool, apply_: bool, target_dir_opt: Optional[Path], yes: bool) -> None:
    """多副本体检：列出本机全部 numalarm 安装副本；--apply 可统一各宿主 hook 指向。"""
    current = _current_install_dir()
    infos = discover_installs(current)
    if as_json:
        payload = [
            {
                "path": str(i.path),
                "sources": list(i.sources),
                "hosts": list(i.hosts),
                "is_current": i.is_current,
                "exists": i.exists,
                "version": i.version,
                "git_head": i.git_head,
                "calibrated_assets": list(i.git_dirty_assets),
                "has_config": i.has_config,
                "has_venv": i.has_venv,
            }
            for i in infos
        ]
        click.echo(json.dumps({"current": str(current), "installs": payload}, ensure_ascii=False, indent=2))
    else:
        click.echo(f"共发现 {len(infos)} 个 numalarm 安装副本（当前：{current}）")
        for i in infos:
            flags = []
            if i.is_current:
                flags.append("当前")
            if not i.exists:
                flags.append("目录缺失")
            if i.version:
                flags.append(f"v{i.version}")
            if i.git_head:
                flags.append(f"git {i.git_head}")
            if i.has_config:
                flags.append("有 config")
            if i.git_dirty_assets:
                flags.append("模板已校准")
            if i.has_venv:
                flags.append(".venv")
            host_part = f"  ← {('、'.join(i.hosts))}" if i.hosts else ""
            click.echo(f"  {i.path}{host_part}")
            click.echo(f"      [{(' | '.join(flags)) or '无附加信息'}]  来源：{'+'.join(i.sources)}")
        if len(infos) > 1:
            click.echo("提示：多份安装共享 ~/.numalarm 状态（锁/防抖/hold/心跳）；")
            click.echo("可用 numalarm installs --apply --dir <目录> 将各宿主 hook 统一指向一份安装。")

    if not apply_:
        return
    target = (target_dir_opt or current).resolve()
    if not (target / "numalarm").is_dir():
        click.echo(f"[失败] {target} 不是 numalarm 安装目录")
        sys.exit(1)
    warnings = []
    if not (target / "config.yaml").is_file():
        warnings.append(f"{target} 缺少 config.yaml（hook 触发将使用内置默认值）")
    if not (target / ".venv").exists():
        warnings.append(f"{target} 缺少 .venv（hook 命令将回退系统解释器）")
    if warnings:
        for w in warnings:
            click.echo(f"[注意] {w}")
        if not yes and not click.confirm(f"仍要将各宿主 hook 改指到 {target}？", default=False):
            click.echo("已取消")
            return
    config_path = (target / "config.yaml").resolve()
    python_exe = _resolve_embed_python(target)
    targets = [p for p in KNOWN_HOSTS.values() if p.is_file()]
    _rewrite_hooks_for_install(targets, install_dir=target, config_path=config_path, python_exe=python_exe)
    click.echo("完成：重启对应宿主会话后生效。")


if __name__ == "__main__":
    main()
