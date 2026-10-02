"""numalarm 安装副本探测与 hook 命令解析（纯标准库）。

硬性约束：本模块只允许依赖 Python 标准库，禁止 import numalarm 包内其他模块
或任何第三方库——``install.py``（仓库根的一键安装器）会在依赖尚未安装的环境里
通过 importlib 按文件路径直接加载本模块；tests/test_installations.py 使用相同的
按文件路径加载方式作为回归守卫。

职责：
- 宿主（Agent 宿主）与其技能目录、settings.json 位置的推导；
- numalarm hook 命令的解析与分类（拨打 / 心跳），供 cli 的注册与卸载复用；
- 全机安装副本探测（当前安装 / 各宿主技能目录 / 各 settings.json 中被 hook 引用的目录）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

KNOWN_HOST_NAMES: Tuple[str, ...] = ("workbuddy", "claude", "codebuddy")

# 生成的 hook 命令里固定包含该模块路径，用于把 numalarm 自身命令与用户手写命令区分开
HOOK_MODULE_MARKER = "numalarm.interfaces.cli"

# 命令分类标记（与 cli._hook_cmdline 的生成格式保持一致）
_DIAL_MARKER = " call --silent --auto"
_HEARTBEAT_CLEAR_MARKER = " heartbeat --clear"
_HEARTBEAT_MARKER = " heartbeat"

# 需要用户自行校准的两张模板图（相对安装目录）
CALIBRATED_ASSETS: Tuple[str, ...] = (
    "assets/voice_button_sample.png",
    "assets/call_ringing_sample.png",
)

_GIT_TIMEOUT = 5.0

_CD_RE = re.compile(r"""(?:^|\s)cd\s+(?:"([^"]+)"|'([^']+)'|([^\s&;]+))\s*&&""")


# ----------------------------------------------------------------------
# 路径与宿主推导
# ----------------------------------------------------------------------
def host_settings_path(host: str, home: Optional[Path] = None) -> Path:
    """宿主 settings.json 位置：<home>/.<host>/settings.json。"""
    home = Path.home() if home is None else Path(home)
    return home / f".{host}" / "settings.json"


def known_settings_paths(home: Optional[Path] = None) -> Dict[str, Path]:
    """全部已知宿主的 settings.json 位置（不检查存在性）。"""
    return {name: host_settings_path(name, home) for name in KNOWN_HOST_NAMES}


def skill_dir(host: str, home: Optional[Path] = None) -> Path:
    """宿主技能目录约定位置：<settings 所在目录>/skills/numalarm。"""
    return host_settings_path(host, home).parent / "skills" / "numalarm"


def normalize_path_key(path: Path) -> str:
    """路径去重键：resolve + normcase，消除大小写与相对段差异（Windows 大小写不敏感）。"""
    try:
        p = Path(path).resolve()
    except OSError:
        p = Path(path)
    return os.path.normcase(str(p))


def detect_skill_host(install_dir: Path, home: Optional[Path] = None) -> Optional[str]:
    """安装目录形如 <home>/.<host>/skills/numalarm 时返回宿主名，否则 None。"""
    home = Path.home() if home is None else Path(home)
    try:
        resolved = Path(install_dir).resolve()
        grand = resolved.parent.parent
        home_resolved = home.resolve()
    except OSError:
        return None
    if resolved.parent.name.lower() != "skills":
        return None
    host_dir_name = grand.name.lower()
    if not host_dir_name.startswith("."):
        return None
    host = host_dir_name[1:]
    if host not in KNOWN_HOST_NAMES:
        return None
    try:
        if grand.parent.resolve() != home_resolved:
            return None
    except OSError:
        return None
    return host


# ----------------------------------------------------------------------
# hook 命令解析与分类
# ----------------------------------------------------------------------
def iter_hook_commands(settings_data: Any) -> Iterator[str]:
    """容错遍历 settings.json 结构中全部 hook 命令字符串（坏形状直接跳过）。"""
    hooks = settings_data.get("hooks") if isinstance(settings_data, dict) else None
    if not isinstance(hooks, dict):
        return
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            inner = entry.get("hooks")
            if not isinstance(inner, list):
                continue
            for h in inner:
                if isinstance(h, dict) and isinstance(h.get("command"), str):
                    yield h["command"]


def is_numalarm_command(cmd: str) -> bool:
    """是否是 numalarm 自身生成的 hook 命令（严格匹配模块路径，避免误伤用户手写命令）。"""
    return HOOK_MODULE_MARKER in (cmd or "")


def hook_kind(cmd: str) -> Optional[str]:
    """分类 numalarm hook 命令：``"dial"`` / ``"heartbeat"`` / None（无法分类）。

    无法分类（如旧版无 ``--auto`` 的 call、用户手改命令）时返回 None——
    卸载流程对这类条目一律保留并提示，宁留不误删。
    """
    if not is_numalarm_command(cmd):
        return None
    if _HEARTBEAT_CLEAR_MARKER in cmd or _HEARTBEAT_MARKER in cmd:
        return "heartbeat"
    if _DIAL_MARKER in cmd:
        return "dial"
    return None


def parse_hook_install_dir(cmd: str) -> Optional[Path]:
    """解析 hook 命令 ``cd '<dir>' && ...`` 中的安装目录。

    容忍单引号 / 双引号 / 无引号（无空格路径）。非 numalarm 命令或解析失败返回 None；
    不校验目录是否存在（解析与校验分离，便于报告坏引用）。
    """
    if not is_numalarm_command(cmd):
        return None
    m = _CD_RE.search(cmd)
    if not m:
        return None
    raw = next((g for g in m.groups() if g), None)
    if not raw:
        return None
    return Path(raw)


# ----------------------------------------------------------------------
# 安装副本探测
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class HookRef:
    """一条指向 numalarm 的 hook 命令引用。"""

    host: str
    settings_path: Path
    command: str
    kind: Optional[str]
    install_dir: Optional[Path]


@dataclass(frozen=True)
class InstallInfo:
    """一个 numalarm 安装副本的探测结果。"""

    path: Path
    sources: Tuple[str, ...] = ()          # current / skill-dir / hook-command
    hosts: Tuple[str, ...] = ()            # 与其关联的宿主名
    is_current: bool = False
    exists: bool = True
    is_git: bool = False
    git_head: Optional[str] = None         # 短 sha；非 git / 失败为 None
    git_dirty_assets: Tuple[str, ...] = ()  # 处于「已修改」状态的校准模板（可判定时）
    version: Optional[str] = None          # numalarm/__init__.py 中的 __version__
    has_config: bool = False
    has_assets: Tuple[str, ...] = ()
    has_venv: bool = False


def read_version(install_dir: Path) -> Optional[str]:
    """从 <安装目录>/numalarm/__init__.py 读取 __version__；失败返回 None。"""
    init = Path(install_dir) / "numalarm" / "__init__.py"
    try:
        text = init.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def _git(install_dir: Path, args: Sequence[str], timeout: float = _GIT_TIMEOUT) -> Optional[str]:
    """在指定目录执行 git 子命令；任何失败（含超时/无 git）返回 None。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(install_dir), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    # 只裁行尾换行：porcelain 格式的行首状态位含空格，不能用 strip() 吃掉
    return (r.stdout or "").strip("\n")


def git_head(install_dir: Path, timeout: float = _GIT_TIMEOUT) -> Optional[str]:
    """git 副本的短 HEAD；非 git / 失败返回 None。"""
    if not (Path(install_dir) / ".git").exists():
        return None
    return _git(install_dir, ["rev-parse", "--short", "HEAD"], timeout) or None


def dirty_calibrated_assets(install_dir: Path, timeout: float = _GIT_TIMEOUT) -> Tuple[str, ...]:
    """返回处于「已修改（未提交）」状态的校准模板相对路径；非 git 副本返回空。

    用户用自己机器截的模板替换仓库文件后即处于已修改状态——这是判定「已校准」的依据。
    """
    root = Path(install_dir)
    if not (root / ".git").exists():
        return ()
    out = _git(root, ["status", "--porcelain", "--", *CALIBRATED_ASSETS], timeout)
    if not out:
        return ()
    dirty: List[str] = []
    for line in out.splitlines():
        line = line.rstrip()
        if len(line) < 4:
            continue
        rel = line[3:].strip().strip('"')
        if rel in CALIBRATED_ASSETS and rel not in dirty:
            dirty.append(rel)
    return tuple(dirty)


def collect_hook_refs(settings_paths: Mapping[str, Path]) -> List[HookRef]:
    """扫描各宿主 settings.json，收集指向 numalarm 的 hook 引用。"""
    refs: List[HookRef] = []
    for host, path in settings_paths.items():
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for cmd in iter_hook_commands(data):
            if not is_numalarm_command(cmd):
                continue
            refs.append(
                HookRef(
                    host=host,
                    settings_path=Path(path),
                    command=cmd,
                    kind=hook_kind(cmd),
                    install_dir=parse_hook_install_dir(cmd),
                )
            )
    return refs


def discover_installs(
    current: Path,
    *,
    home: Optional[Path] = None,
    settings_paths: Optional[Mapping[str, Path]] = None,
    run_git: bool = True,
) -> List[InstallInfo]:
    """探测本机全部 numalarm 安装副本。

    来源：(a) 当前安装；(b) 各已知宿主的技能目录（存在才计入）；
    (c) 各 settings.json 中 numalarm hook 命令所引用的目录（目录不存在也计入，
    以 ``exists=False`` 标记——doctor 据此提示「hook 指向失效目录」）。
    按 ``normalize_path_key`` 去重合并；返回值以当前安装优先排序。

    只读：不创建任何目录、不写任何文件（--dry-run 安全）。
    """
    home = Path.home() if home is None else Path(home)
    if settings_paths is None:
        settings_paths = {name: p for name, p in known_settings_paths(home).items() if p.is_file()}

    path_by_key: Dict[str, Path] = {}
    sources: Dict[str, set] = {}
    hosts: Dict[str, set] = {}
    current_key = normalize_path_key(current)

    def _add(path: Path, source: str, host: Optional[str] = None) -> None:
        key = normalize_path_key(path)
        path_by_key.setdefault(key, Path(path))
        sources.setdefault(key, set()).add(source)
        if host:
            hosts.setdefault(key, set()).add(host)

    _add(current, "current")
    for name in KNOWN_HOST_NAMES:
        sd = skill_dir(name, home)
        if sd.is_dir():
            _add(sd, "skill-dir", name)
    for ref in collect_hook_refs(settings_paths):
        if ref.install_dir is not None:
            _add(ref.install_dir, "hook-command", ref.host)

    infos: List[InstallInfo] = []
    for key, path in path_by_key.items():
        exists = path.is_dir()
        is_git = exists and (path / ".git").exists()
        infos.append(
            InstallInfo(
                path=path,
                sources=tuple(sorted(sources.get(key, ()))),
                hosts=tuple(sorted(hosts.get(key, ()))),
                is_current=(key == current_key),
                exists=exists,
                is_git=is_git,
                git_head=git_head(path) if (run_git and is_git) else None,
                git_dirty_assets=dirty_calibrated_assets(path) if (run_git and is_git) else (),
                version=read_version(path) if exists else None,
                has_config=exists and (path / "config.yaml").is_file(),
                has_assets=tuple(rel for rel in CALIBRATED_ASSETS if (path / rel).is_file()) if exists else (),
                has_venv=exists and ((path / ".venv" / "Scripts").is_dir() or (path / ".venv" / "bin").is_dir()),
            )
        )
    infos.sort(key=lambda i: (not i.is_current, str(i.path)))
    return infos


def pick_reuse_source(installs: Iterable[InstallInfo]) -> Optional[InstallInfo]:
    """从副本列表里挑一个可复用配置/校准资产的来源；无合适者返回 None。

    优先级：有 config 且带已校准模板 > 有 config > 带已校准模板；排除当前安装与不存在者。
    两类信息都没有的副本视为不可复用。
    """
    candidates = [i for i in installs if i.exists and not i.is_current]
    if not candidates:
        return None
    candidates.sort(key=lambda i: (1 if i.git_dirty_assets else 0, 1 if i.has_config else 0, str(i.path)), reverse=True)
    best = candidates[0]
    if not best.has_config and not best.git_dirty_assets:
        return None
    return best
