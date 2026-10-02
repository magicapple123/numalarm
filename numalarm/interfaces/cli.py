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
    # 人工命令默认不受在位检测限制；显式 --auto 才启用检测
    result = call_qq(target=target, silent=silent, auto=auto, **kwargs)
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
    if lnk_path.exists():
        if not click.confirm(f"快捷方式已存在：{lnk_path.name}，覆盖它？", default=False):
            click.echo("已取消，未做任何更改。")
            return

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


@cli.command()
@click.option("--yes", "-y", is_flag=True, help="跳过确认，直接清理运行时残留与快捷方式")
def uninstall(yes: bool) -> None:
    """完全卸载清理：移除 Hook 注册、桌面快捷方式与运行时残留，实现零残留移除。

    技能/仓库目录（含独立虚拟环境）与可选安装的虚拟声卡需按提示手动移除。
    """
    click.echo("== 牛马铃卸载清理 ==")

    # 1) 宿主 Hook 注册
    click.echo("\n[1/4] 宿主 Hook 注册")
    _remove_numalarm_hooks([p for p in KNOWN_HOSTS.values() if p.is_file()])

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
    click.echo("- Agent 长期记忆：numalarm 无法访问宿主的记忆系统，"
               "请在对话中让 Agent 删除其记忆里登记的 numalarm 约定与路径")
    click.echo("\n完成：除上述手动项外，设备无任何残留。")


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
    venv_python = venv_dir / ("Scripts" if sys.platform == "win32" else "bin") / "python.exe"
    if venv_python.is_file():
        click.echo("正在同步虚拟环境依赖…")
        r = subprocess.run([str(venv_python), "-m", "pip", "install", "-r", "requirements.txt"],
                           cwd=str(install_dir), capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        tail = (r.stdout or r.stderr or "").strip().splitlines()[-3:]
        for line in tail:
            click.echo(f"  {line}")
    else:
        click.echo("未检测到虚拟环境（.venv），跳过依赖同步")

    # 6. 刷新已有宿主上的 hook 命令（不扩大注册范围）
    _register_hooks({event: _hook_command() for event in HOOK_EVENTS},
                    [p for p in KNOWN_HOSTS.values() if p.is_file()], create_missing=False)

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
def watchdog_install() -> None:
    """注册每分钟一次的看门狗计划任务（Windows 计划任务 / 其他平台 crontab）。"""
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

    # 心跳来源注册：PreToolUse 刷新心跳 / SessionEnd 清心跳
    hb_cmd = _hook_cmdline("heartbeat")
    hb_clear_cmd = _hook_cmdline("heartbeat --clear")
    _register_hooks({"PreToolUse": hb_cmd, "SessionEnd": hb_clear_cmd},
                    [p for p in KNOWN_HOSTS.values() if p.is_file()], create_missing=True)
    click.echo("心跳来源：兼容宿主由 PreToolUse hook 自动刷新；"
               "无 hook 机制的宿主请在长任务中定期运行 numalarm heartbeat（见 SKILL.md）")
    click.echo("移除：numalarm watchdog uninstall")


@watchdog.command("uninstall")
def watchdog_uninstall() -> None:
    """移除看门狗计划任务与心跳 hook 注册（保留拨打类 hook）。"""
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
    _remove_numalarm_hooks([p for p in KNOWN_HOSTS.values() if p.is_file()])
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
    cli()


# ----------------------------------------------------------------------
# 宿主 Hook 集成（跨 Agent 通用）
# ----------------------------------------------------------------------
# 兼容 Claude Code hooks schema 的宿主（WorkBuddy / Claude Code / CodeBuddy 等）
# Stop = Agent 回合结束（任务交付时刻）：配合在位检测实现「人离开后任务完成自动响铃」
HOOK_EVENTS = ("Notification", "PermissionRequest", "Stop")
KNOWN_HOSTS = {
    "workbuddy": Path.home() / ".workbuddy" / "settings.json",
    "claude": Path.home() / ".claude" / "settings.json",
    "codebuddy": Path.home() / ".codebuddy" / "settings.json",
}


def _hook_cmdline(cli_args: str) -> str:
    """生成 hook 命令行：cd 固定到安装目录（保证 numalarm 包可导入），配置路径写死。"""
    cfg = ConfigManager.instance()
    config_path = (cfg.config_path or (Path.cwd() / "config.yaml")).resolve()
    python_exe = Path(sys.executable)
    pythonw = python_exe.with_name("pythonw.exe")
    py = (pythonw if pythonw.is_file() else python_exe).resolve()
    install_dir = Path(__file__).resolve().parents[2]
    return (
        f"cd '{install_dir.as_posix()}' && "
        f"NUMALARM_CONFIG='{config_path.as_posix()}' '{py.as_posix()}' "
        f"-m numalarm.interfaces.cli {cli_args}"
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


def _remove_numalarm_hooks(targets: List[Path]) -> None:
    """从指定 settings.json 中移除全部 numalarm 注册的 hook（扫描所有事件，只删自身条目）。"""
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
        for event in list(hooks_cfg.keys()):
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


@hook.command("uninstall")
@click.option("--host", "hosts", default=None, help="目标宿主：workbuddy / claude / codebuddy / all；默认自动探测")
@click.option("--settings", "settings_paths", multiple=True, type=click.Path(dir_okay=False, path_type=Path),
              help="自定义宿主的 settings.json 路径，可多次传入；优先于 --host")
def hook_uninstall(hosts: Optional[str], settings_paths: tuple) -> None:
    """移除由 numalarm 注册的 hook（只删自身条目，其余配置原样保留）。"""
    _remove_numalarm_hooks(_resolve_hook_targets(hosts, settings_paths))


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


if __name__ == "__main__":
    main()
