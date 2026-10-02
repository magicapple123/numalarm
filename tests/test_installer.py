"""install.py 一键安装器单元测试（纯逻辑，Ubuntu CI 可跑）。"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INSTALLER_PATH = _REPO_ROOT / "install.py"

_spec = importlib.util.spec_from_file_location("numalarm_installer_under_test", _INSTALLER_PATH)
assert _spec is not None and _spec.loader is not None
installer = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = installer  # dataclass 字符串注解解析需要模块已注册
_spec.loader.exec_module(installer)


def test_installer_detect_host_via_installations(tmp_path):
    """install.py 能按文件路径加载 installations 模块并复用宿主判定。"""
    mod = installer._load_installations()
    home = tmp_path / "home"
    inst = home / ".claude" / "skills" / "numalarm"
    inst.mkdir(parents=True)
    assert mod.detect_skill_host(inst, home=home) == "claude"


def test_build_steps_noninteractive_defaults():
    """非交互默认：建 venv/装依赖/生成配置；不注册 hook 与看门狗。"""
    steps = installer.build_steps(
        venv_ok=False, pkg_installed=False, config_exists=False,
        want_reuse_config=False, want_reuse_assets=False, reuse_available=False,
        want_hooks=False, hook_target_ok=True, want_watchdog=False, want_consolidate=False,
    )
    keys = [s.key for s in steps]
    assert "venv" in keys and "deps" in keys and "config" in keys
    assert "hooks" not in keys and "hooks-guide" not in keys and "watchdog" not in keys


def test_build_steps_idempotent_skip():
    """已安装且明确关闭注册时：没有任何会写盘的步骤。"""
    steps = installer.build_steps(
        venv_ok=True, pkg_installed=True, config_exists=True,
        want_reuse_config=False, want_reuse_assets=False, reuse_available=False,
        want_hooks=False, hook_target_ok=True, want_watchdog=False, want_consolidate=False,
    )
    assert all(not s.mutate for s in steps)
    assert [s.key for s in steps] == ["doctor", "next"]


def test_build_steps_with_hooks_watchdog_consolidate():
    steps = installer.build_steps(
        venv_ok=True, pkg_installed=True, config_exists=True,
        want_reuse_config=False, want_reuse_assets=False, reuse_available=False,
        want_hooks=True, hook_target_ok=True, want_watchdog=True, want_consolidate=True,
    )
    keys = [s.key for s in steps]
    assert "hooks" in keys and "watchdog" in keys and "consolidate" in keys


def test_build_steps_hooks_without_target_prints_guide():
    steps = installer.build_steps(
        venv_ok=True, pkg_installed=True, config_exists=True,
        want_reuse_config=False, want_reuse_assets=False, reuse_available=False,
        want_hooks=True, hook_target_ok=False, want_watchdog=False, want_consolidate=False,
    )
    keys = [s.key for s in steps]
    assert "hooks-guide" in keys and "hooks" not in keys


def test_installer_help_smoke():
    """--help 在任何平台可用（CI 冒烟）。"""
    r = subprocess.run([sys.executable, str(_INSTALLER_PATH), "--help"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0
    assert "--dry-run" in r.stdout
    assert "--reuse-assets" in r.stdout


def test_installer_help_survives_non_utf8_stream():
    """英文区域 Windows（cp1252 管道/重定向）下 --help 不得因中文输出崩溃（回归）。"""
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    r = subprocess.run([sys.executable, str(_INSTALLER_PATH), "--help"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    assert r.returncode == 0
    assert "--dry-run" in r.stdout
