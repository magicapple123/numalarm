"""CLI 层 hook 分类/卸载/作用域回归测试（纯逻辑，Ubuntu CI 可跑，不触碰真实宿主配置）。

重点回归：watchdog uninstall 曾误删拨打类 hook（与 hook uninstall 对称的 kinds 过滤修复）。
"""

import json
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from numalarm.interfaces import cli as cli_mod


def _dial_cmd(d="C:/x/numalarm"):
    return f"cd '{d}' && NUMALARM_CONFIG='{d}/config.yaml' 'py' -m numalarm.interfaces.cli call --silent --auto"


def _hb_cmd(d="C:/x/numalarm", clear=False):
    suffix = "heartbeat --clear" if clear else "heartbeat"
    return f"cd '{d}' && NUMALARM_CONFIG='{d}/config.yaml' 'py' -m numalarm.interfaces.cli {suffix}"


def _write_settings(path: Path, hooks: dict) -> Path:
    path.write_text(json.dumps({"hooks": hooks}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _mixed_settings(tmp_path: Path) -> Path:
    return _write_settings(tmp_path / "settings.json", {
        "Notification": [{"matcher": "", "hooks": [{"type": "command", "command": _dial_cmd()}]}],
        "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": _dial_cmd()}]}],
        "PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": _hb_cmd()}]}],
        "SessionEnd": [{"matcher": "", "hooks": [{"type": "command", "command": _hb_cmd(clear=True)}]}],
        "UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command", "command": "echo hi"}]}],
    })


def _load(p: Path) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _commands(data: dict, event: str):
    cmds = []
    for entry in data["hooks"].get(event) or []:
        for h in entry.get("hooks") or []:
            cmds.append(h.get("command"))
    return cmds


# ---------------- kinds 过滤（核心回归） ----------------

def test_remove_hooks_kind_heartbeat_keeps_dial(tmp_path):
    p = _mixed_settings(tmp_path)
    cli_mod._remove_numalarm_hooks([p], kinds={"heartbeat"})
    data = _load(p)
    assert _commands(data, "Notification") == [_dial_cmd()]
    assert _commands(data, "Stop") == [_dial_cmd()]
    assert "PreToolUse" not in data["hooks"]
    assert "SessionEnd" not in data["hooks"]
    assert _commands(data, "UserPromptSubmit") == ["echo hi"]
    assert (tmp_path / "settings.json.numalarm-bak").is_file()


def test_remove_hooks_kind_dial_keeps_heartbeat(tmp_path):
    p = _mixed_settings(tmp_path)
    cli_mod._remove_numalarm_hooks([p], kinds={"dial"})
    data = _load(p)
    assert "Notification" not in data["hooks"]
    assert "Stop" not in data["hooks"]
    assert _commands(data, "PreToolUse") == [_hb_cmd()]
    assert _commands(data, "SessionEnd") == [_hb_cmd(clear=True)]
    assert _commands(data, "UserPromptSubmit") == ["echo hi"]


def test_remove_hooks_all_kinds_none(tmp_path):
    p = _mixed_settings(tmp_path)
    cli_mod._remove_numalarm_hooks([p], kinds=None)
    data = _load(p)
    for event in ("Notification", "Stop", "PreToolUse", "SessionEnd"):
        assert event not in data["hooks"]
    assert _commands(data, "UserPromptSubmit") == ["echo hi"]


def test_remove_hooks_mixed_entry_filters_inner(tmp_path):
    p = _write_settings(tmp_path / "settings.json", {
        "Stop": [{"matcher": "", "hooks": [
            {"type": "command", "command": _dial_cmd(), "async": True},
            {"type": "command", "command": _hb_cmd()},
        ]}],
    })
    cli_mod._remove_numalarm_hooks([p], kinds={"dial"})
    data = _load(p)
    assert _commands(data, "Stop") == [_hb_cmd()]  # entry 保留，只剩心跳


def test_remove_hooks_unclassified_kept(tmp_path):
    manual = "cd 'C:/x' && 'py' -m numalarm.interfaces.cli call --silent"  # 旧式无 --auto
    p = _write_settings(tmp_path / "settings.json", {
        "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": manual}]}],
    })
    cli_mod._remove_numalarm_hooks([p], kinds={"dial"})
    data = _load(p)
    assert _commands(data, "Stop") == [manual]  # 宁留不误删
    assert not (tmp_path / "settings.json.numalarm-bak").is_file()  # 无变更则不动文件


# ---------------- 作用域解析 ----------------

def test_resolve_hook_targets_semantics(tmp_path, monkeypatch):
    claude_path = tmp_path / "claude-settings.json"
    claude_path.write_text("{}", encoding="utf-8")
    known = {"claude": claude_path, "workbuddy": tmp_path / "wb.json"}
    monkeypatch.setattr(cli_mod, "KNOWN_HOSTS", known)
    custom = tmp_path / "custom.json"

    assert cli_mod._resolve_hook_targets("claude", ()) == [claude_path]
    with pytest.raises(click.BadParameter):
        cli_mod._resolve_hook_targets("nope", ())
    assert cli_mod._resolve_hook_targets(None, (str(custom),)) == [custom]
    # None/all => 仅存在的宿主（HOOK_SCOPE_DEFAULT 默认 detected）
    assert cli_mod._resolve_hook_targets(None, ()) == [claude_path]
    assert cli_mod._resolve_hook_targets("all", ()) == [claude_path]


def test_watchdog_install_help_has_scope_options():
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["watchdog", "install", "--help"])
    assert result.exit_code == 0
    assert "--host" in result.output and "--settings" in result.output


def test_installs_json_smoke():
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["installs", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload["installs"], list) and payload["installs"]
    current_entries = [i for i in payload["installs"] if i["is_current"]]
    assert len(current_entries) == 1


# ---------------- 收拢改写（installs --apply 的核心） ----------------

def test_rewrite_hooks_for_install(tmp_path):
    p = _mixed_settings(tmp_path)
    target = tmp_path / "newinstall"
    (target / "numalarm").mkdir(parents=True)
    (target / "config.yaml").write_text("{}", encoding="utf-8")

    cli_mod._rewrite_hooks_for_install(
        [p],
        install_dir=target,
        config_path=target / "config.yaml",
        python_exe=Path("C:/py/pythonw.exe"),
    )
    data = _load(p)
    for event in ("Notification", "Stop"):
        cmds = _commands(data, event)
        assert cmds and all("newinstall" in c and "call --silent --auto" in c for c in cmds)
    for event in ("PreToolUse", "SessionEnd"):
        cmds = _commands(data, event)
        assert cmds and all("newinstall" in c and "heartbeat" in c for c in cmds)
    assert _commands(data, "UserPromptSubmit") == ["echo hi"]  # 非 numalarm 条目不动
    assert (tmp_path / "settings.json.numalarm-bak").is_file()
