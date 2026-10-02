"""installations 模块单元测试（纯逻辑，Ubuntu CI 可跑）。

通过 importlib 按文件路径加载 numalarm/common/installations.py —— 与 install.py
的加载方式一致，同时作为「该模块不得引入包内/第三方依赖」的回归守卫。
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_MOD_PATH = Path(__file__).resolve().parents[1] / "numalarm" / "common" / "installations.py"
_spec = importlib.util.spec_from_file_location("numalarm_installations_under_test", _MOD_PATH)
assert _spec is not None and _spec.loader is not None
installations = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = installations  # dataclass 字符串注解解析需要模块已注册（importlib 规范做法）
_spec.loader.exec_module(installations)


# ---------------- hook 命令解析与分类 ----------------

def test_parse_hook_install_dir_single_quote():
    cmd = ("cd 'C:/Users/x/.claude/skills/numalarm' && NUMALARM_CONFIG='C:/x/config.yaml' "
           "'C:/py/pythonw.exe' -m numalarm.interfaces.cli call --silent --auto")
    assert installations.parse_hook_install_dir(cmd) == Path("C:/Users/x/.claude/skills/numalarm")


def test_parse_hook_install_dir_double_quote_and_plain():
    dq = 'cd "D:/some dir/numalarm" && \'x\' -m numalarm.interfaces.cli heartbeat'
    assert installations.parse_hook_install_dir(dq) == Path("D:/some dir/numalarm")
    plain = "cd /opt/numalarm && 'x' -m numalarm.interfaces.cli heartbeat --clear"
    assert installations.parse_hook_install_dir(plain) == Path("/opt/numalarm")


def test_parse_hook_install_dir_none():
    assert installations.parse_hook_install_dir("echo hi") is None
    assert installations.parse_hook_install_dir("cd 'x' && run-other-thing") is None
    assert installations.parse_hook_install_dir("'py' -m numalarm.interfaces.cli heartbeat") is None


def test_hook_kind():
    prefix = "cd 'x' && 'py' -m numalarm.interfaces.cli"
    assert installations.hook_kind(prefix + " call --silent --auto") == "dial"
    assert installations.hook_kind(prefix + " heartbeat") == "heartbeat"
    assert installations.hook_kind(prefix + " heartbeat --clear") == "heartbeat"
    assert installations.hook_kind(prefix + " call --silent") is None   # 旧式/无法分类
    assert installations.hook_kind("git status") is None


def test_iter_hook_commands_tolerates_bad_shapes():
    data = {
        "hooks": {
            "Stop": "not-a-list",
            "PreToolUse": [None, {"hooks": None}, {"hooks": [{"command": 1}, {"command": "ok"}]}],
            "Notification": [{"hooks": [{"command": "cmd-a"}]}],
        }
    }
    assert sorted(installations.iter_hook_commands(data)) == ["cmd-a", "ok"]
    assert list(installations.iter_hook_commands(None)) == []
    assert list(installations.iter_hook_commands({"hooks": []})) == []
    assert list(installations.iter_hook_commands({"hooks": {"Stop": [{"matcher": ""}]}})) == []


# ---------------- 宿主 / 路径推导 ----------------

def test_detect_skill_host(tmp_path):
    home = tmp_path / "home"
    inst = home / ".claude" / "skills" / "numalarm"
    inst.mkdir(parents=True)
    assert installations.detect_skill_host(inst, home=home) == "claude"

    other = tmp_path / "somewhere" / "numalarm"
    other.mkdir(parents=True)
    assert installations.detect_skill_host(other, home=home) is None

    weird = home / ".claude" / "worktrees" / "numalarm"
    weird.mkdir(parents=True)
    assert installations.detect_skill_host(weird, home=home) is None

    upper = home / ".CLAUDE" / "skills" / "numalarm"
    upper.mkdir(parents=True, exist_ok=True)  # Windows 下与 .claude 同目录（大小写不敏感）
    assert installations.detect_skill_host(upper, home=home) == "claude"


def test_normalize_path_key(tmp_path):
    a = tmp_path / "X"
    a.mkdir()
    assert installations.normalize_path_key(a / ".") == installations.normalize_path_key(a)
    assert installations.normalize_path_key(a / ".." / "X") == installations.normalize_path_key(a)


# ---------------- 探测 ----------------

def test_read_version(tmp_path):
    d = tmp_path / "inst"
    (d / "numalarm").mkdir(parents=True)
    (d / "numalarm" / "__init__.py").write_text('# x\n__version__ = "1.2.3"\n', encoding="utf-8")
    assert installations.read_version(d) == "1.2.3"
    assert installations.read_version(tmp_path / "none") is None


def test_discover_installs_sources_and_dedup(tmp_path):
    home = tmp_path / "home"
    copy_a = tmp_path / "copyA"
    copy_a.mkdir()
    copy_b = tmp_path / "copyB"
    (copy_b / "numalarm").mkdir(parents=True)
    (copy_b / "numalarm" / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")

    skill = home / ".codebuddy" / "skills" / "numalarm"
    skill.mkdir(parents=True)

    gone = tmp_path / "gone"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    dial_cmd = f"cd '{copy_a.as_posix()}' && 'py' -m numalarm.interfaces.cli call --silent --auto"
    hb_cmd = f"cd '{gone.as_posix()}' && 'py' -m numalarm.interfaces.cli heartbeat"
    settings.write_text(
        json.dumps({
            "hooks": {
                "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": dial_cmd}]}],
                "PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": hb_cmd}]}],
            }
        }),
        encoding="utf-8",
    )

    infos = installations.discover_installs(copy_b, home=home, run_git=False)
    by_key = {installations.normalize_path_key(i.path): i for i in infos}

    assert by_key[installations.normalize_path_key(copy_b)].is_current
    assert by_key[installations.normalize_path_key(copy_b)].version == "9.9.9"

    info_a = by_key[installations.normalize_path_key(copy_a)]
    assert info_a.exists and "hook-command" in info_a.sources and "claude" in info_a.hosts

    info_skill = by_key[installations.normalize_path_key(skill)]
    assert info_skill.exists and "skill-dir" in info_skill.sources

    info_gone = by_key[installations.normalize_path_key(gone)]
    assert info_gone.exists is False

    # 排序：当前安装优先
    assert infos[0].is_current


def test_dirty_calibrated_assets(tmp_path):
    if not shutil.which("git"):
        pytest.skip("环境无 git")
    repo = tmp_path / "repo"
    (repo / "assets").mkdir(parents=True)
    for rel in installations.CALIBRATED_ASSETS:
        (repo / rel).write_bytes(b"placeholder")
    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=git_env)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=git_env)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, env=git_env)

    assert installations.dirty_calibrated_assets(repo) == ()
    (repo / installations.CALIBRATED_ASSETS[0]).write_bytes(b"calibrated-by-user")
    assert installations.dirty_calibrated_assets(repo) == (installations.CALIBRATED_ASSETS[0],)
    assert installations.dirty_calibrated_assets(tmp_path / "not-a-repo") == ()
    assert installations.git_head(repo) is not None


def test_pick_reuse_source(tmp_path):
    cur = installations.InstallInfo(path=tmp_path / "cur", is_current=True, exists=True, has_config=True)
    plain = installations.InstallInfo(path=tmp_path / "o1", exists=True, has_config=True)
    calibrated = installations.InstallInfo(
        path=tmp_path / "o2", exists=True, has_config=True,
        git_dirty_assets=(installations.CALIBRATED_ASSETS[0],),
    )
    empty = installations.InstallInfo(path=tmp_path / "o3", exists=True)
    gone = installations.InstallInfo(path=tmp_path / "o4", exists=False, has_config=True)

    assert installations.pick_reuse_source([cur, plain, calibrated, empty, gone]).path == calibrated.path
    assert installations.pick_reuse_source([cur, plain]).path == plain.path
    assert installations.pick_reuse_source([cur, empty, gone]) is None
    assert installations.pick_reuse_source([]) is None
