import os
import plistlib
import stat

import pytest

from telegram4muse import cli, config, launchd

GROUPS = [
    {"id": -1001234567890, "title": "Surf Crew", "type": "supergroup"},
    {"id": -1002, "title": "Surf Crew Archive", "type": "supergroup"},
    {"id": -1009, "title": "Family", "type": "group"},
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("T4M_HOME", str(tmp_path / "t4m"))
    cfg = config.Config(api_id=123, api_hash="a" * 32)
    cfg.save()
    return cfg


def test_match_groups():
    assert cli.match_groups(GROUPS, "-1009") == [GROUPS[2]]
    assert cli.match_groups(GROUPS, "surf crew") == [GROUPS[0]]  # exact title wins over substring
    assert cli.match_groups(GROUPS, "surf") == GROUPS[:2]
    assert cli.match_groups(GROUPS, "nope") == []


def test_config_round_trip_is_private(home):
    path = config.config_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    loaded = config.load()
    assert loaded.api_id == 123
    assert loaded.host == "auto"
    assert loaded.port == 8787
    assert len(loaded.agent_token) >= 40


def test_allow_and_disallow(home, monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_groups", lambda cfg: GROUPS)
    cli.main(["allow", "Surf Crew", "-1009"])
    assert config.load().allowed_chat_ids == {-1001234567890, -1009}
    cli.main(["allow", "family"])
    assert "Already allowed: Family" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="more than one group"):
        cli.main(["allow", "surf c"])
    cli.main(["disallow", "Family"])
    assert config.load().allowed_chat_ids == {-1001234567890}


def test_token_rotation(home, capsys):
    old = config.load().agent_token
    cli.main(["token"])
    assert capsys.readouterr().out.strip() == old
    cli.main(["token", "--rotate"])
    new = capsys.readouterr().out.strip()
    assert new != old
    assert config.load().agent_token == new


def test_peers(home):
    cli.main(["peers", "100.64.0.9"])
    assert config.load().allowed_peers == ["100.64.0.9"]
    cli.main(["peers", "--clear"])
    assert config.load().allowed_peers == []


def test_not_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("T4M_HOME", str(tmp_path / "empty"))
    with pytest.raises(SystemExit, match="t4m setup"):
        cli.main(["status"])


def test_launchd_plist():
    plain = plistlib.loads(launchd.plist(python="/venv/bin/python"))
    assert plain["Label"] == "com.telegram4muse.server"
    assert plain["ProgramArguments"] == ["/venv/bin/python", "-m", "telegram4muse", "serve"]
    assert plain["KeepAlive"] is True
    assert plain["RunAtLoad"] is True
    assert plain["StandardErrorPath"].endswith("Library/Logs/telegram4muse.log")
    awake = plistlib.loads(launchd.plist(keep_awake=True, python="/venv/bin/python"))
    assert awake["ProgramArguments"][:2] == ["/usr/bin/caffeinate", "-s"]
