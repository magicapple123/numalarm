#!/usr/bin/env python3
"""牛马铃（numalarm）一键安装器（仅标准库，Python >= 3.10，仅 Windows）。

用法（在仓库/技能目录内执行）：

    python install.py [--yes] [--dry-run] [--json]
                      [--host claude|workbuddy|codebuddy] [--settings <settings.json>]
                      [--hooks|--no-hooks] [--watchdog|--no-watchdog]
                      [--reuse-config <目录>] [--reuse-assets <目录>]
                      [--python <解释器>] [--strict]

设计原则：
- 幂等：重复执行只补缺失项（已完成的步骤自动跳过），不覆盖任何既有 config.yaml 与校准模板；
- 非交互（--yes 或非 TTY）默认不注册 hook / 看门狗（涉及真实拨打），仅打印可复制的命令；
- --dry-run 全程只读：只探测与打印计划，不建 venv、不安装、不写任何文件；
- 默认作用域仅当前宿主（由本脚本所在路径反推，如 ~/.claude/skills/numalarm → claude），
  不触碰其它宿主；各全机副作用详见 README「安装范围与全机副作用」。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

MIN_PYTHON = (3, 10)

installations = None  # 由 main() / _load_installations() 初始化（模块本身可安全导入）


class InstallError(Exception):
    """致命安装错误（退出码 1）。"""


@dataclass
class Step:
    """一个安装步骤（build_steps 计算得出，仅包含“有事要做”的步骤）。"""

    key: str
    desc: str
    mutate: bool


# ----------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------
def _console_safe() -> None:
    """非 UTF-8 输出流兜底：不可编码的字符降级为 "?"，而不是抛 UnicodeEncodeError。

    英文区域 Windows 上 stdout/stderr 被管道或重定向时按 ANSI 代码页（cp1252 等）编码，
    打印中文会直接崩溃（CI 与「被其它程序抓取输出」都会踩到）。该实现刻意与
    numalarm/interfaces/cli.py 的同名函数保持一致——本脚本按设计不 import 包内模块。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
        except Exception:  # 少数被替换过的流不支持 reconfigure
            pass


def _load_installations():
    """按文件路径加载 numalarm/common/installations.py。

    不 import numalarm 包本体（避免在依赖尚未安装时拉起第三方库）；模块需要注册进
    sys.modules 再 exec（dataclass 的字符串注解解析依赖它）。
    """
    path = Path(__file__).resolve().parent / "numalarm" / "common" / "installations.py"
    spec = importlib.util.spec_from_file_location("numalarm_installations", path)
    if spec is None or spec.loader is None:
        raise InstallError(f"无法加载探测模块：{path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _venv_python(install_dir: Path) -> Path:
    if sys.platform == "win32":
        return install_dir / ".venv" / "Scripts" / "python.exe"
    return install_dir / ".venv" / "bin" / "python"


def _venv_ok(install_dir: Path) -> bool:
    return _venv_python(install_dir).is_file()


def _pkg_installed(install_dir: Path) -> bool:
    if sys.platform == "win32":
        return (install_dir / ".venv" / "Scripts" / "numalarm.exe").is_file()
    return (install_dir / ".venv" / "bin" / "numalarm").is_file()


def _cli_cmd(install_dir: Path, *args: str) -> List[str]:
    return [str(_venv_python(install_dir)), "-m", "numalarm.interfaces.cli", *args]


def _ask_yes_no(question: str, *, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        ans = input(f"{question} {suffix} ").strip().lower()
    except EOFError:
        return default
    if not ans:
        return default
    return ans in ("y", "yes", "是", "1")


def _run_cmd(cmd: List[str], *, cwd: Optional[Path] = None, desc: str) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        tail = "\n".join(((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-6:])
        raise InstallError(f"{desc}失败（退出码 {r.returncode}）：\n{tail}")
    return r


# ----------------------------------------------------------------------
# 步骤计算（纯函数，可单测）
# ----------------------------------------------------------------------
def build_steps(*, venv_ok: bool, pkg_installed: bool, config_exists: bool,
                want_reuse_config: bool, want_reuse_assets: bool, reuse_available: bool,
                want_hooks: bool, hook_target_ok: bool,
                want_watchdog: bool, want_consolidate: bool) -> List[Step]:
    """按现状与用户选择计算需要执行的步骤（只返回“有事要做”的步骤）。"""
    steps: List[Step] = []
    if not venv_ok:
        steps.append(Step("venv", "创建隔离虚拟环境 .venv", mutate=True))
    if not (venv_ok and pkg_installed):
        steps.append(Step("deps", "安装 numalarm 与依赖（pip install -e .）", mutate=True))
    if not config_exists:
        steps.append(Step("config", "生成 config.yaml", mutate=True))
    if want_reuse_config and reuse_available:
        steps.append(Step("reuse-config", "从已有副本复用 config.yaml", mutate=True))
    if want_reuse_assets and reuse_available:
        steps.append(Step("reuse-assets", "从已有副本复用已校准的截图模板", mutate=True))
    steps.append(Step("doctor", "环境自检（numalarm doctor）", mutate=False))
    if want_hooks:
        if hook_target_ok:
            steps.append(Step("hooks", "注册拨打 hook（仅当前宿主）", mutate=True))
        else:
            steps.append(Step("hooks-guide", "打印 hook 注册指引（未能判定宿主）", mutate=False))
    if want_watchdog:
        steps.append(Step("watchdog", "注册看门狗（全机计划任务 + 当前宿主心跳 hook）", mutate=True))
    if want_consolidate:
        steps.append(Step("consolidate", "将各宿主 hook 统一指向本安装", mutate=True))
    steps.append(Step("next", "输出下一步指引", mutate=False))
    return steps


# ----------------------------------------------------------------------
# 步骤实现
# ----------------------------------------------------------------------
def _step_venv(install_dir: Path, args: argparse.Namespace, log) -> str:
    python = args.python or sys.executable
    log(f"    使用解释器：{python}")
    _run_cmd([python, "-m", "venv", str(install_dir / ".venv")], cwd=install_dir, desc="创建虚拟环境")
    log("    .venv 已创建")
    return "created"


def _step_deps(install_dir: Path, log) -> str:
    py = _venv_python(install_dir)
    probe = subprocess.run([str(py), "-m", "pip", "--version"], capture_output=True, text=True)
    if probe.returncode != 0:
        _run_cmd([str(py), "-m", "ensurepip", "--upgrade"], cwd=install_dir, desc="ensurepip")
    log("    安装 numalarm 与依赖（pip install -e .）…")
    r = subprocess.run([str(py), "-m", "pip", "install", "-e", "."], cwd=str(install_dir),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log("    pip install -e . 失败，回退 pip install -r requirements.txt …")
        r = subprocess.run([str(py), "-m", "pip", "install", "-r", "requirements.txt"], cwd=str(install_dir),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            tail = "\n".join(((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-8:])
            raise InstallError(f"依赖安装失败（可尝试 --python 指定其它解释器）：\n{tail}")
    log("    依赖就绪")
    return "installed"


def _step_config(install_dir: Path, args: argparse.Namespace, log) -> str:
    target = install_dir / "config.yaml"
    if target.is_file():
        log("    已存在，跳过")
        return "exists"
    interactive = sys.stdin.isatty() and not args.yes
    if interactive and _ask_yes_no("未找到 config.yaml：运行交互式向导（numalarm init）？"
                                   "否=直接复制示例配置", default=False):
        # init 向导写入 cwd，这里固定到安装目录
        r = subprocess.run(_cli_cmd(install_dir, "init"), cwd=str(install_dir))
        if r.returncode != 0 or not target.is_file():
            raise InstallError("交互式向导未生成 config.yaml")
        return "init"
    shutil.copy2(install_dir / "config.example.yaml", target)
    log("    已复制 config.example.yaml → config.yaml；请修改 default_target（或重跑交互式安装）")
    return "example"


def _step_reuse_config(install_dir: Path, src_root: Optional[Path], log) -> str:
    if src_root is None:
        return "skip"
    target = install_dir / "config.yaml"
    if target.is_file():
        log(f"    目标已存在 config.yaml，不覆盖（源：{src_root}）")
        return "exists"
    src = Path(src_root) / "config.yaml"
    if not src.is_file():
        raise InstallError(f"源副本没有 config.yaml：{src_root}")
    shutil.copy2(src, target)
    log(f"    已复用 {src_root} 的 config.yaml")
    return "reused"


def _step_reuse_assets(install_dir: Path, src_root: Optional[Path], log) -> str:
    if src_root is None:
        return "skip"
    src_root = Path(src_root)
    if (install_dir / ".git").exists():
        dirty = set(installations.dirty_calibrated_assets(install_dir))
    else:
        dirty = set()  # 非 git 无法判定，按未校准处理（覆盖前仍会备份）
    backup_root = Path.home() / ".numalarm" / "backups" / ("install-" + time.strftime("%Y%m%d-%H%M%S"))
    copied = []
    for rel in installations.CALIBRATED_ASSETS:
        src = src_root / rel
        dst = install_dir / rel
        if not src.is_file():
            continue
        if rel in dirty:
            log(f"    本目录 {rel} 已有本地修改（视为已校准），跳过")
            continue
        if dst.is_file():
            bdst = backup_root / rel
            bdst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, bdst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel)
    if copied:
        log(f"    已复用模板：{'、'.join(copied)}（原文件备份于 {backup_root}）")
    else:
        log("    没有可复用的模板（源缺失或本目录已校准）")
    return f"copied={len(copied)}"


def _step_doctor(install_dir: Path, log) -> str:
    r = subprocess.run(_cli_cmd(install_dir, "doctor"), cwd=str(install_dir))
    if r.returncode == 0:
        log("    doctor 全部通过")
        return "ok"
    log("    doctor 有未通过项（见上方输出；--strict 时将以非零退出码结束）")
    return "fail"


def _step_hooks(install_dir: Path, host: Optional[str], settings: Optional[Path], log) -> str:
    cmd = _cli_cmd(install_dir, "hook", "install")
    cmd += ["--settings", str(settings)] if settings is not None else ["--host", host or "all"]
    r = subprocess.run(cmd, cwd=str(install_dir))
    if r.returncode != 0:
        raise InstallError("hook 注册失败")
    log("    已注册（重启对应宿主会话后生效）")
    return "registered"


def _step_hooks_guide(install_dir: Path, log) -> str:
    py = _venv_python(install_dir)
    log("    未能判定当前宿主（本安装不在 ~/.<宿主>/skills/ 目录下）。")
    log(f"    手动注册：\"{py}\" -m numalarm.interfaces.cli hook install --host claude"
        f"  或  --settings <你的 settings.json>")
    return "guide"


def _step_watchdog(install_dir: Path, host: Optional[str], settings: Optional[Path], log) -> str:
    cmd = _cli_cmd(install_dir, "watchdog", "install")
    if settings is not None:
        cmd += ["--settings", str(settings)]
    elif host:
        cmd += ["--host", host]
    r = subprocess.run(cmd, cwd=str(install_dir))
    if r.returncode != 0:
        raise InstallError("看门狗注册失败")
    log("    已注册（计划任务为全机唯一）")
    return "registered"


def _step_consolidate(install_dir: Path, log) -> str:
    r = subprocess.run(_cli_cmd(install_dir, "installs", "--apply", "--dir", str(install_dir), "--yes"),
                       cwd=str(install_dir))
    if r.returncode != 0:
        raise InstallError("收拢失败（可稍后手动运行 numalarm installs --apply）")
    return "applied"


def _step_next(install_dir: Path, log) -> str:
    py = _venv_python(install_dir)
    log("")
    log("下一步：")
    log(f"  1. 编辑 {install_dir / 'config.yaml'} 的 default_target（要拨打的好友昵称/备注）")
    log(f"  2. 校准：\"{py}\" -m numalarm.interfaces.cli test <目标>")
    log(f"  3. 体检与状态：\"{py}\" -m numalarm.interfaces.cli doctor / installs / hook status / watchdog status")
    log("  4. 技能/宿主未生效时重启对应宿主会话；卸载见 README「卸载与零残留」")
    return "ok"


_STEP_FUNCS = {
    "venv": lambda idir, args, host, settings, rc, ra, log: _step_venv(idir, args, log),
    "deps": lambda idir, args, host, settings, rc, ra, log: _step_deps(idir, log),
    "config": lambda idir, args, host, settings, rc, ra, log: _step_config(idir, args, log),
    "reuse-config": lambda idir, args, host, settings, rc, ra, log: _step_reuse_config(idir, rc, log),
    "reuse-assets": lambda idir, args, host, settings, rc, ra, log: _step_reuse_assets(idir, ra, log),
    "doctor": lambda idir, args, host, settings, rc, ra, log: _step_doctor(idir, log),
    "hooks": lambda idir, args, host, settings, rc, ra, log: _step_hooks(idir, host, settings, log),
    "hooks-guide": lambda idir, args, host, settings, rc, ra, log: _step_hooks_guide(idir, log),
    "watchdog": lambda idir, args, host, settings, rc, ra, log: _step_watchdog(idir, host, settings, log),
    "consolidate": lambda idir, args, host, settings, rc, ra, log: _step_consolidate(idir, log),
    "next": lambda idir, args, host, settings, rc, ra, log: _step_next(idir, log),
}


# ----------------------------------------------------------------------
# 参数与主流程
# ----------------------------------------------------------------------
def _parse_args(argv: Optional[List[str]], installations_mod) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="install.py",
        description="牛马铃（numalarm）一键安装器（仅 Windows 10/11 + PC 版 QQ）。",
    )
    p.add_argument("--yes", "-y", action="store_true",
                   help="非交互模式：使用安全默认值（默认不注册 hook/看门狗）")
    p.add_argument("--dry-run", action="store_true", help="演练模式：只探测与打印计划，不写盘")
    p.add_argument("--json", action="store_true", help="以 JSON 输出最终摘要（步骤日志走 stderr）")
    p.add_argument("--host", choices=list(installations_mod.KNOWN_HOST_NAMES), default=None,
                   help="hook/心跳注册的目标宿主（默认由安装路径自动判定）")
    p.add_argument("--settings", default=None,
                   help="自定义宿主 settings.json 路径（优先于 --host）")
    p.add_argument("--hooks", dest="hooks", action="store_true", default=None, help="强制注册拨打 hook")
    p.add_argument("--no-hooks", dest="hooks", action="store_false", default=None, help="强制不注册拨打 hook")
    p.add_argument("--watchdog", dest="watchdog", action="store_true", default=None, help="强制注册看门狗")
    p.add_argument("--no-watchdog", dest="watchdog", action="store_false", default=None, help="强制不注册看门狗")
    p.add_argument("--reuse-config", metavar="目录", default=None,
                   help="从指定安装目录复用 config.yaml（仅在目标缺失时）")
    p.add_argument("--reuse-assets", metavar="目录", default=None,
                   help="从指定安装目录复用已校准的截图模板（覆盖前自动备份）")
    p.add_argument("--python", default=None, help="创建 .venv 使用的解释器（默认当前解释器）")
    p.add_argument("--strict", action="store_true", help="doctor 未全通过时以非零退出码结束")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    global installations
    _console_safe()
    try:
        installations = _load_installations()
    except InstallError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    args = _parse_args(argv, installations)

    def log(msg: str) -> None:
        print(msg, file=sys.stderr if args.json else sys.stdout, flush=True)

    if sys.version_info < MIN_PYTHON:
        log(f"[失败] 需要 Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}（当前 {sys.version.split()[0]}）。")
        return 2
    if sys.platform != "win32":
        log("[失败] 牛马铃仅支持 Windows 10/11 + PC 版 QQ（见 README 环境要求）；已中止。")
        return 2

    install_dir = Path(__file__).resolve().parent

    # ---- 只读探测 ----
    venv_ok = _venv_ok(install_dir)
    pkg_ok = _pkg_installed(install_dir)
    config_exists = (install_dir / "config.yaml").is_file()
    infos = installations.discover_installs(install_dir)
    others = [i for i in infos if not i.is_current]
    reuse_src = installations.pick_reuse_source(infos)
    host = args.host or installations.detect_skill_host(install_dir)
    settings = Path(args.settings).resolve() if args.settings else None
    hook_target_ok = settings is not None or host is not None
    interactive = sys.stdin.isatty() and not args.yes and not args.dry_run

    log(f"== 牛马铃一键安装（{'演练模式 --dry-run' if args.dry_run else '正式执行'}）==")
    log(f"安装目录：{install_dir}")
    if others:
        log(f"[提示] 发现 {len(others)} 个其他 numalarm 副本：")
        for one in others:
            state = "存在" if one.exists else "目录缺失"
            ver = f"v{one.version}" if one.version else "版本未知"
            head = f"，git {one.git_head}" if one.git_head else ""
            cfg_state = "，有 config" if one.has_config else ""
            calib = "，模板已校准" if one.git_dirty_assets else ""
            log(f"    - {one.path}（{state}，{ver}{head}{cfg_state}{calib}）")
    if host:
        log(f"[提示] 当前宿主：{host}（hook 默认只注册到这里）")
    elif settings is not None:
        log(f"[提示] hook 目标：{settings}")
    else:
        log("[提示] 未能从安装路径判定宿主（非宿主技能目录）；hook 步骤将只打印指引")

    # ---- 决策（交互询问；非交互一律取安全默认：不注册） ----
    hooks_flag = args.hooks
    if hooks_flag is None and interactive and hook_target_ok:
        target_desc = f"宿主 {host}" if host else str(settings)
        hooks_flag = _ask_yes_no(
            f"是否注册拨打 hook（{target_desc}；对方不接听会自动重拨，涉及真实电话）？", default=False)
    want_hooks = bool(hooks_flag)

    wd_flag = args.watchdog
    if wd_flag is None and interactive and hook_target_ok:
        wd_flag = _ask_yes_no("是否注册看门狗（全机计划任务 + 心跳 hook，可选）？", default=False)
    want_watchdog = bool(wd_flag)

    reuse_config_src: Optional[Path] = None
    if args.reuse_config:
        reuse_config_src = Path(args.reuse_config).resolve()
    elif reuse_src is not None and not config_exists and interactive:
        if _ask_yes_no(f"检测到已有副本 {reuse_src.path}，复用其 config.yaml？", default=True):
            reuse_config_src = reuse_src.path

    reuse_assets_src: Optional[Path] = None
    if args.reuse_assets:
        reuse_assets_src = Path(args.reuse_assets).resolve()
    elif reuse_src is not None and reuse_src.git_dirty_assets and interactive:
        if _ask_yes_no(f"检测到已有副本带已校准模板（{reuse_src.path}），复用？", default=True):
            reuse_assets_src = reuse_src.path

    want_consolidate = False
    if interactive and others and any("hook-command" in one.sources for one in others):
        want_consolidate = _ask_yes_no("检测到其他副本的 hook 指向别处，是否把各宿主 hook 统一指向本安装？",
                                       default=False)

    steps = build_steps(
        venv_ok=venv_ok, pkg_installed=pkg_ok, config_exists=config_exists,
        want_reuse_config=reuse_config_src is not None,
        want_reuse_assets=reuse_assets_src is not None,
        reuse_available=bool(reuse_src) or bool(args.reuse_config) or bool(args.reuse_assets),
        want_hooks=want_hooks, hook_target_ok=hook_target_ok,
        want_watchdog=want_watchdog, want_consolidate=want_consolidate,
    )

    # ---- 执行 ----
    results: List[Dict[str, Any]] = []
    exit_code = 0
    for step in steps:
        log(f"\n[{step.key}] {step.desc}")
        if args.dry_run and step.key != "next":
            log("    [dry-run] 未执行（演练模式）")
            results.append({"key": step.key, "status": "planned"})
            continue
        try:
            detail = _STEP_FUNCS[step.key](install_dir, args, host, settings,
                                           reuse_config_src, reuse_assets_src, log)
            results.append({"key": step.key, "status": "done", "detail": detail})
        except InstallError as exc:
            log(f"    [失败] {exc}")
            results.append({"key": step.key, "status": "failed", "detail": str(exc)})
            _finish(args, install_dir, results, 1, log)
            return 1

    # 非交互默认不注册 hook/看门狗（涉及真实拨打）：打印可复制的开启命令
    if not args.dry_run and (not want_hooks or not want_watchdog):
        py = _venv_python(install_dir)
        if settings is not None:
            target_arg = f'--settings "{settings}"'
        elif host:
            target_arg = f"--host {host}"
        else:
            target_arg = None
        if not want_hooks:
            if target_arg:
                log(f"\n[提示] 未注册拨打 hook（涉及真实拨打）。如需开启："
                    f"\"{py}\" -m numalarm.interfaces.cli hook install {target_arg}")
            else:
                log(f"\n[提示] 未注册拨打 hook，且未能判定宿主。如需开启："
                    f"\"{py}\" -m numalarm.interfaces.cli hook install --settings <你的 settings.json>")
        if not want_watchdog:
            if target_arg:
                log(f"[提示] 未注册看门狗（可选）。如需开启："
                    f"\"{py}\" -m numalarm.interfaces.cli watchdog install {target_arg}")
            else:
                log(f"[提示] 未注册看门狗（可选）。如需开启："
                    f"\"{py}\" -m numalarm.interfaces.cli watchdog install --host claude"
                    f"  或 --settings <你的 settings.json>")

    if args.strict and any(r["key"] == "doctor" and r.get("detail") == "fail" for r in results):
        exit_code = 1
    _finish(args, install_dir, results, exit_code, log)
    return exit_code


def _finish(args: argparse.Namespace, install_dir: Path, results: List[Dict[str, Any]], exit_code: int, log) -> None:
    if args.json:
        # ensure_ascii=True：机器可读输出不依赖 stdout 编码（非 UTF-8 流下中文会变 "?"）
        print(json.dumps({"install_dir": str(install_dir), "steps": results, "exit": exit_code},
                         ensure_ascii=True, indent=2))
    log(f"\n== {'安装完成' if exit_code == 0 else '安装未完成'}（退出码 {exit_code}）=="
        if not args.dry_run else "\n== 演练结束（未做任何改动）==")


if __name__ == "__main__":
    sys.exit(main())
