import json
import os
import plistlib
import stat

import pytest

from telegram4muse import cli, config, launchd, tailscale

GROUPS = [
    {"id": -1001234567890, "title": "Surf Crew", "type": "supergroup"},
    {"id": -1002, "title": "Surf Crew Archive", "type": "supergroup"},
    {"id": -1009, "title": "Family", "type": "group"},
    {"id": -1004497900442, "title": "Fake SurfGroup", "type": "supergroup"},
    {"id": -1005, "title": "Surf! (Bay Area)", "type": "supergroup"},
]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("T4M_HOME", str(tmp_path / "t4m"))
    cfg = config.Config(api_id=123, api_hash="a" * 32)
    cfg.save()
    return cfg


def titles(groups):
    return [g["title"] for g in groups]


def test_match_groups():
    assert titles(cli.match_groups(GROUPS, "-1009")) == ["Family"]
    assert titles(cli.match_groups(GROUPS, "surf crew")) == ["Surf Crew"]  # exact title wins
    assert titles(cli.match_groups(GROUPS, "Surf! (Bay Area)")) == ["Surf! (Bay Area)"]
    # Punctuation and case don't matter, so no shell quoting is needed.
    assert titles(cli.match_groups(GROUPS, "surf bay area")) == ["Surf! (Bay Area)"]
    assert titles(cli.match_groups(GROUPS, "bay")) == ["Surf! (Bay Area)"]
    assert titles(cli.match_groups(GROUPS, "fake surfgroup")) == ["Fake SurfGroup"]
    assert titles(cli.match_groups(GROUPS, "surf")) == ["Surf Crew", "Surf Crew Archive", "Fake SurfGroup", "Surf! (Bay Area)"]
    assert cli.match_groups(GROUPS, "nope") == []
    assert cli.match_groups(GROUPS, "!!!") == []


def test_config_round_trip_is_private(home):
    path = config.config_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    loaded = config.load()
    assert loaded.api_id == 123
    assert loaded.host == "auto"
    assert loaded.port == 8787
    assert loaded.allowed_peers == []


def test_config_from_the_token_version_still_loads(home):
    data = json.loads(config.config_path().read_text())
    data["agent_token"] = "left over from an older version"
    config.write_private(config.config_path(), json.dumps(data))
    assert config.load().api_id == 123
    config.load().save()
    assert "agent_token" not in config.config_path().read_text()


def test_allow_by_name_without_quotes(home, monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_groups", lambda cfg: GROUPS)
    # What zsh passes for: t4m allow Fake SurfGroup / t4m allow surf bay area
    cli.main(["allow", "Fake", "SurfGroup"])
    out = capsys.readouterr().out
    assert out == "Allowed: Fake SurfGroup (-1004497900442)\n"
    cli.main(["allow", "surf", "bay", "area"])
    assert config.load().allowed_chat_ids == {-1004497900442, -1005}
    cli.main(["allow", "fake", "surfgroup"])
    assert "Already allowed: Fake SurfGroup" in capsys.readouterr().out


def test_allow_by_ids(home, monkeypatch):
    monkeypatch.setattr(cli, "fetch_groups", lambda cfg: GROUPS)
    cli.main(["allow", "-1009", "-1001234567890"])
    assert config.load().allowed_chat_ids == {-1009, -1001234567890}


def test_ambiguous_or_unknown_names(home, monkeypatch):
    monkeypatch.setattr(cli, "fetch_groups", lambda cfg: GROUPS)
    with pytest.raises(SystemExit, match="matches more than one group"):
        cli.main(["allow", "surf"])
    with pytest.raises(SystemExit, match="without a name to pick from a list"):
        cli.main(["allow", "nope"])
    assert config.load().allowed_chats == []


def test_allow_and_disallow_from_a_list(home, monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_groups", lambda cfg: GROUPS)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers = iter(["2, 5"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    cli.main(["allow"])
    out = capsys.readouterr().out
    # Sorted by title: Fake SurfGroup, Family, Surf Crew, Surf Crew Archive, Surf! (Bay Area)
    assert "5.   Surf! (Bay Area)  (supergroup, -1005)" in out
    assert config.load().allowed_chat_ids == {-1009, -1005}

    answers = iter(["1"])
    cli.main(["disallow"])
    assert config.load().allowed_chat_ids == {-1005}
    assert "Removed: Family" in capsys.readouterr().out

    answers = iter(["9"])
    with pytest.raises(SystemExit, match="isn't a number from the list"):
        cli.main(["allow"])

    answers = iter([""])
    cli.main(["allow"])  # Enter cancels
    assert config.load().allowed_chat_ids == {-1005}


def test_picking_needs_a_terminal(home, monkeypatch):
    monkeypatch.setattr(cli, "fetch_groups", lambda cfg: GROUPS)
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    with pytest.raises(SystemExit, match="Give a group name or id"):
        cli.main(["allow"])


def test_peers(home, monkeypatch, capsys):
    cli.main(["peers"])
    assert "only this Mac" in capsys.readouterr().out
    cli.main(["peers", "100.64.0.9"])
    assert config.load().allowed_peers == ["100.64.0.9"]
    # By Tailscale device name.
    monkeypatch.setattr(tailscale, "_cli_ipv4", lambda *args: "100.70.1.2" if args == ("muse",) else None)
    monkeypatch.setattr(tailscale.socket, "gethostbyname", lambda name: (_ for _ in ()).throw(OSError(name)))
    cli.main(["peers", "muse"])
    assert config.load().allowed_peers == ["100.70.1.2"]
    with pytest.raises(SystemExit, match="isn't a Tailscale address"):
        cli.main(["peers", "8.8.8.8"])
    with pytest.raises(SystemExit, match='Can\'t find a tailnet device called "nobody"'):
        cli.main(["peers", "nobody"])
    assert config.load().allowed_peers == ["100.70.1.2"]
    cli.main(["peers", "--clear"])
    assert config.load().allowed_peers == []


def test_no_token_command_anymore(home):
    with pytest.raises(SystemExit):
        cli.main(["token"])


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
