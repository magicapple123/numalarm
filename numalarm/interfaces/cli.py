"""命令行接口（CLI）与桌面快捷方式管理。

命令总览：

    numalarm call [目标] [--timeout 30] [--silent/-s]     拨打（不传目标用 default_target）
    numalarm test [目标]                                  校准（不拨打）
    numalarm serve [--host H] [--port P]                  启动 HTTP 服务
    numalarm init                                         交互式初始化向导
    numalarm shortcut create <目标> [--name "名称"] [--silent]   创建桌面快捷方式
    numalarm shortcut delete <名称>                       删除快捷方式
    numalarm shortcut list                                列出快捷方式

桌面快捷方式仅支持 Windows；其他平台自动禁用该命令组并给出提示，
不影响核心拨打功能。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, List, Optional

import click

from numalarm import __version__
from numalarm.common.config import CONFIG_ENV, ConfigManager
from numalarm.common.exceptions import CODE_SUCCESS, NumAlarmError


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
@click.option("--silent", "-s", is_flag=True, help="静默执行：无控制台输出，完成自动退出，适合桌面快捷方式")
@click.option("--reason", "-m", default=None, help="拨打原因（记录到日志与结果，用于 Agent 集成追溯）")
@click.option("--auto", is_flag=True, help="自动化触发：受用户在位检测控制（人在电脑前则跳过；人工命令默认不检测）")
def call(target: Optional[str], timeout: Optional[int], silent: bool, reason: Optional[str], auto: bool) -> None:
    """向目标好友拨打 QQ 语音通话（TARGET 支持昵称/备注/别名；不传则用 default_target）。"""
    from numalarm.interfaces.sdk import call_qq

    kwargs = {"timeout": timeout} if timeout is not None else {}
    if reason:
        kwargs["reason"] = reason
    if auto:
        kwargs["auto"] = True
    result = call_qq(target=target, silent=silent, **kwargs)
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


# ----------------------------------------------------------------------
# 桌面快捷方式（仅 Windows）
# ----------------------------------------------------------------------
def _check_windows_for_shortcut() -> bool:
    """快捷方式命令组的平台守卫：非 Windows 给出提示与手动教程。"""
    if sys.platform == "win32":
        return True
    click.echo("桌面快捷方式功能仅支持 Windows 平台，当前系统不可用（核心拨打功能不受影响）。")
    click.echo('手动创建方式：桌面右键 -> 新建 -> 快捷方式，目标填写：')
    click.echo('  "python.exe完整路径" -m numalarm.interfaces.cli call "目标昵称"')
    return False


def _desktop_dir() -> Path:
    """获取当前用户真实桌面目录（兼容 OneDrive 重定向）。"""
    import win32com.client  # type: ignore

    shell = win32com.client.Dispatch("WScript.Shell")
    return Path(shell.SpecialFolders("Desktop"))


def _resolve_python(silent: bool) -> Path:
    """解析执行器路径：静默快捷方式优先使用 pythonw.exe（无控制台窗口）。"""
    python_exe = Path(sys.executable)
    if silent:
        pythonw = python_exe.with_name("pythonw.exe")
        if pythonw.is_file():
            return pythonw
    return python_exe


def _build_call_args(target: str, silent: bool) -> str:
    """构造快捷方式命令参数。"""
    safe_target = target.replace('"', "")  # 防止目标名中的引号破坏参数
    args = f'-m numalarm.interfaces.cli call "{safe_target}"'
    if silent:
        args += " --silent"
    return args


@cli.group()
def shortcut() -> None:
    """桌面快捷方式管理（仅 Windows，其他平台自动禁用）。"""


@shortcut.command("create")
@click.argument("target")
@click.option("--name", default=None, help="快捷方式显示名称，默认「牛马铃-拨打{目标}」")
@click.option("--silent", is_flag=True, help="生成静默快捷方式：双击不弹出控制台窗口，后台执行拨打")
def shortcut_create(target: str, name: Optional[str], silent: bool) -> None:
    """在桌面创建一键拨打快捷方式（TARGET 支持昵称/备注/别名，多个目标可创建多个快捷方式）。"""
    if not _check_windows_for_shortcut():
        sys.exit(1)
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        click.echo("缺少可选依赖 pywin32，请执行：pip install pywin32")
        click.echo("或手动创建快捷方式，目标填写：")
        click.echo(f'  "{sys.executable}" -m numalarm.interfaces.cli call "{target}"')
        sys.exit(1)

    manager = ConfigManager.instance()
    try:
        manager.resolve_target(target)
    except NumAlarmError:
        click.echo("提示：该目标既不是 default_target 也未在 target_alias 中映射，请确认昵称/备注正确。", err=True)

    prefix = manager.config.shortcut.name_prefix
    display_name = name or f"{prefix}{target}"
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", display_name)
    lnk_path = _desktop_dir() / f"{safe_name}.lnk"

    shell = win32com.client.Dispatch("WScript.Shell")
    lnk = shell.CreateShortCut(str(lnk_path))
    lnk.TargetPath = str(_resolve_python(silent))
    lnk.Arguments = _build_call_args(target, silent)
    # 工作目录指向 config.yaml 所在目录，保证快捷方式双击时能找到配置
    lnk.WorkingDirectory = str(manager.config_path.parent if manager.config_path else Path.cwd())
    lnk.IconLocation = str(Path(sys.executable))
    lnk.Description = f"牛马铃一键拨打：{target}"
    lnk.WindowStyle = 1
    lnk.Save()

    click.echo(f"快捷方式已创建：{lnk_path}")
    if silent:
        click.echo("模式：静默（双击后无控制台窗口，后台执行拨打）")
    click.echo("可右键该快捷方式固定到任务栏 / 开始菜单。")


@shortcut.command("delete")
@click.argument("name")
def shortcut_delete(name: str) -> None:
    """删除指定名称的桌面快捷方式（可省略 .lnk 后缀）。"""
    if not _check_windows_for_shortcut():
        sys.exit(1)
    fname = name if name.lower().endswith(".lnk") else f"{name}.lnk"
    path = _desktop_dir() / fname
    if not path.is_file():
        click.echo(f"未找到快捷方式：{path}")
        sys.exit(1)
    if click.confirm(f"确认删除 {path.name}？", default=False):
        path.unlink()
        click.echo("已删除。")
    else:
        click.echo("已取消。")


@shortcut.command("list")
def shortcut_list() -> None:
    """列出由牛马铃创建的桌面快捷方式。"""
    if not _check_windows_for_shortcut():
        sys.exit(1)
    desktop = _desktop_dir()
    found = 0
    for lnk_path in sorted(desktop.glob("*.lnk")):
        try:
            import win32com.client

            shell = win32com.client.Dispatch("WScript.Shell")
            args = shell.CreateShortCut(str(lnk_path)).Arguments or ""
            if "numalarm" in args:
                found += 1
                click.echo(f"{lnk_path.stem}  ->  {args}")
        except Exception:  # noqa: BLE001 单个快捷方式解析失败不影响整体
            continue
    if not found:
        click.echo("未发现牛马铃快捷方式。")


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

    # 5. 快捷方式可选依赖
    if sys.platform == "win32":
        try:
            import win32com.client  # noqa: F401
            check("pywin32（桌面快捷方式）", True)
        except ImportError:
            check("pywin32（桌面快捷方式）", False, "pip install pywin32（不影响核心拨打）")

    # 汇总
    failed = results.count(False)
    if failed:
        click.echo(f"\n{failed} 项未通过，按上方提示修复后重试。")
        sys.exit(1)
    click.echo("\n全部通过：可以 numalarm test <目标> 校准后使用。")


def main() -> None:
    """CLI 入口（pyproject.toml console_scripts 指向此处）。"""
    cli()


# ----------------------------------------------------------------------
# 宿主 Hook 集成（跨 Agent 通用）
# ----------------------------------------------------------------------
# 兼容 Claude Code hooks schema 的宿主（WorkBuddy / Claude Code / CodeBuddy 等）
HOOK_EVENTS = ("Notification", "PermissionRequest")
KNOWN_HOSTS = {
    "workbuddy": Path.home() / ".workbuddy" / "settings.json",
    "claude": Path.home() / ".claude" / "settings.json",
    "codebuddy": Path.home() / ".codebuddy" / "settings.json",
}


def _hook_command() -> str:
    """生成 hook 触发命令：自动化静默拨打 default_target。

    路径由当前运行环境生成（pythonw 无黑框；NUMALARM_CONFIG 保证任意
    工作目录下都能找到配置；--auto 表示受用户在位检测控制，人在电脑前不打扰）。
    hook 由宿主经 shell 执行，采用 POSIX 写法。
    """
    cfg = ConfigManager.instance()
    config_path = (cfg.config_path or (Path.cwd() / "config.yaml")).resolve()
    python_exe = Path(sys.executable)
    pythonw = python_exe.with_name("pythonw.exe")
    py = (pythonw if pythonw.is_file() else python_exe).resolve()
    return (
        f"NUMALARM_CONFIG='{config_path.as_posix()}' '{py.as_posix()}' "
        f"-m numalarm.interfaces.cli call --silent --auto"
    )


def _hook_entry(cmd: str) -> dict:
    return {"matcher": "", "hooks": [{"type": "command", "command": cmd, "async": True}]}


def _entry_has_numalarm(entry: Any) -> bool:
    try:
        return "numalarm" in json.dumps(entry)
    except (TypeError, ValueError):
        return False


def _resolve_hook_targets(hosts: Optional[str]) -> List[Path]:
    if hosts and hosts != "all":
        if hosts not in KNOWN_HOSTS:
            raise click.BadParameter(f"未知宿主 {hosts}（可选：{', '.join(KNOWN_HOSTS)} / all）")
        return [KNOWN_HOSTS[hosts]]
    return [p for p in KNOWN_HOSTS.values() if p.is_file()]


@cli.group()
def hook() -> None:
    """宿主 Hook 集成：Agent 停下等待用户时自动拨打（Claude Code 兼容宿主）。"""


@hook.command("install")
@click.option("--host", "hosts", default=None, help="目标宿主：workbuddy / claude / codebuddy / all；默认自动探测")
def hook_install(hosts: Optional[str]) -> None:
    """向 Notification / PermissionRequest 事件注册/更新静默拨打 hook（幂等，保留既有配置）。"""
    targets = _resolve_hook_targets(hosts)
    if not targets:
        click.echo("未探测到已安装的兼容宿主（settings.json 不存在）；可用 --host <名称> 指定。")
        sys.exit(1)

    cmd = _hook_command()
    for settings_path in targets:
        try:
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
        for event in HOOK_EVENTS:
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
            if not updated and not any(_entry_has_numalarm(e) for e in entries):
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

    click.echo("重启对应宿主会话后生效。移除：numalarm hook uninstall")


@hook.command("uninstall")
@click.option("--host", "hosts", default=None, help="目标宿主：workbuddy / claude / codebuddy / all；默认自动探测")
def hook_uninstall(hosts: Optional[str]) -> None:
    """移除由 numalarm 注册的 hook（只删自身条目，其余配置原样保留）。"""
    targets = _resolve_hook_targets(hosts)
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
        for event in HOOK_EVENTS:
            entries = hooks_cfg.get(event) or []
            kept = [e for e in entries if not _entry_has_numalarm(e)]
            if len(kept) != len(entries):
                removed.append(event)
                if kept:
                    hooks_cfg[event] = kept
                else:
                    hooks_cfg.pop(event, None)
        if not removed:
            click.echo(f"[跳过] {settings_path} 无 numalarm hook")
            continue
        backup = settings_path.with_name(settings_path.name + ".numalarm-bak")
        try:
            backup.write_text(raw, encoding="utf-8")
            settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            click.echo(f"[失败] {settings_path} 写入失败：{exc}")
            continue
        click.echo(f"[OK] {settings_path} 已移除 hook：{', '.join(removed)}（备份：{backup.name}）")


@hook.command("status")
def hook_status() -> None:
    """查看各宿主的 numalarm hook 注册状态。"""
    for name, path in KNOWN_HOSTS.items():
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


if __name__ == "__main__":
    main()
