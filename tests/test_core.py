import csv
import json
import struct
from pathlib import Path

from wot_tracker.core import (
    COLUMNS, CsvStore, _battle_heroes, _mastery_badge, count_csv,
    import_replays, parse_replay,
)


def write_replay(path: Path, blocks):
    with path.open("wb") as stream:
        stream.write(b"\x12\x32\x34\x11")
        stream.write(struct.pack("<I", len(blocks)))
        for block in blocks:
            data = json.dumps(block).encode()
            stream.write(struct.pack("<I", len(data)))
            stream.write(data)


def metadata():
    return {
        "dateTime": "17.08.2026 10:00:00", "mapName": "map_id", "mapDisplayName": "Карта",
        "gameplayID": "ctf", "playerID": 42, "playerName": "Игрок",
        "playerVehicle": "ussr-R45_IS-7", "clientVersionFromExe": "2.0",
        "vehicles": {"7": {"name": "Игрок", "team": 1}},
    }


def results():
    return [{
        "arenaUniqueID": 999, "common": {"duration": 300, "winnerTeam": 1},
        "vehicles": {
            "7": {"accountDBID": 42, "team": 1, "damageDealt": 1200, "xp": 500},
            "8": {"accountDBID": 43, "team": 1, "damageDealt": 1500, "xp": 400},
            "9": {"accountDBID": 44, "team": 1, "damageDealt": 1000, "xp": 600},
            "10": {"accountDBID": 45, "team": 2, "damageDealt": 2000, "xp": 700},
        },
        "personal": {"123": {
            "accountDBID": 42, "team": 1, "damageDealt": 1200, "damageReceived": 500,
            # Personal XP contains multipliers; scoreboard XP in vehicles is 500.
            "xp": 1500, "markOfMastery": 4,
            "achievements": [521, 228, 34, 34],
            "kills": 2, "damageAssistedRadio": 100, "damageAssistedTrack": 50,
            "damageBlockedByArmor": 700, "noDamageDirectHitsReceived": 3,
            "shots": 6, "directEnemyHits": 5,
            "piercings": 4, "health": 10,
            "details": {
                "enemy1": {"crits": 1 | (1 << 12) | (1 << 24)},
                "enemy2": {"crits": 3 | (1 << 16) | (1 << 27)},
            },
        }, "avatar": {"isPrematureLeave": False}},
    }]


def test_complete_replay_and_utf8(tmp_path):
    replay = tmp_path / "бой.wotreplay"
    write_replay(replay, [metadata(), results()])
    row = parse_replay(replay)
    assert row["parse_status"] == "complete"
    assert row["tank_name"] == "IS-7"
    assert row["tank_nation"] == "ussr"
    assert row["damage_rank"] == 2
    assert row["xp_rank"] == 2
    assert row["mastery_badge"] == "ace_tanker"
    assert json.loads(row["battle_heroes_json"]) == ["top_gun", "high_caliber"]
    assert row["blocked_shots"] == 3
    assert COLUMNS.index("blocked_shots") == COLUMNS.index("blocked_damage") + 1
    assert row["assists"] == 150
    assert row["battle_result"] == "victory"
    assert row["survived"] is True
    assert row["tank_tier"] == ""
    assert row["critical_hits_dealt"] == 7
    assert row["modules_damaged"] == 3
    assert row["modules_destroyed"] == 2
    assert row["crew_injuries"] == 2
    assert json.loads(row["destroyed_modules_json"]) == ["engine", "track"]
    assert all(column not in COLUMNS for column in ("battle_id", "map_id", "tank_id", "source_replay", "replay_hash"))
    CsvStore(tmp_path / "battles.csv").append_atomic(row)
    text = (tmp_path / "battles.csv").read_text(encoding="utf-8-sig")
    assert "Карта" in text and "бой.wotreplay" not in text


def test_metadata_only_is_incomplete(tmp_path):
    replay = tmp_path / "incomplete.wotreplay"
    write_replay(replay, [metadata()])
    assert parse_replay(replay)["parse_status"] == "incomplete"


def test_missing_critical_details_stays_blank(tmp_path):
    replay = tmp_path / "no-details.wotreplay"
    result = results()
    del result[0]["personal"]["123"]["details"]
    write_replay(replay, [metadata(), result])
    row = parse_replay(replay)
    assert row["modules_damaged"] == ""
    assert row["modules_destroyed"] == ""
    assert row["crew_injuries"] == ""
    assert row["destroyed_modules_json"] == ""


def test_no_mastery_badge_is_blank(tmp_path):
    replay = tmp_path / "no-mastery.wotreplay"
    result = results()
    result[0]["personal"]["123"]["markOfMastery"] = 0
    write_replay(replay, [metadata(), result])

    assert parse_replay(replay)["mastery_badge"] == ""


def test_mastery_badge_labels():
    assert [_mastery_badge(value) for value in range(5)] == [
        "", "class_3", "class_2", "class_1", "ace_tanker",
    ]


def test_battle_hero_labels_ignore_other_achievements_and_deduplicate():
    assert _battle_heroes([40, 521, 34, 228, 34, "bad"]) == [
        "top_gun", "high_caliber", "scout",
    ]
    assert _battle_heroes(None) == []


def test_team_ranks_share_places_for_ties_and_ignore_enemy_team(tmp_path):
    replay = tmp_path / "ranks.wotreplay"
    result = results()
    result[0]["vehicles"]["8"].update(damageDealt=1200, xp=500)
    write_replay(replay, [metadata(), result])

    row = parse_replay(replay)

    assert row["damage_rank"] == 1
    assert row["xp_rank"] == 2


def test_bad_signature_is_unsupported(tmp_path):
    replay = tmp_path / "bad.wotreplay"
    replay.write_bytes(b"not a replay")
    assert parse_replay(replay)["parse_status"] == "unsupported"


def test_duplicate_hash_only_imported_once(tmp_path):
    replay_dir = tmp_path / "replays"
    replay_dir.mkdir()
    write_replay(replay_dir / "one.wotreplay", [metadata(), results()])
    (replay_dir / "copy.wotreplay").write_bytes((replay_dir / "one.wotreplay").read_bytes())
    csv_path = tmp_path / "battles.csv"
    import logging
    summary = import_replays(replay_dir, csv_path, logging.getLogger("test"))
    assert summary.imported == 1 and summary.duplicates == 1
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1 and list(rows[0]) == COLUMNS
    assert (tmp_path / "battles.csv.wot-index.json").exists()


def test_rows_are_sorted_chronologically(tmp_path):
    store = CsvStore(tmp_path / "sorted.csv")
    later = {name: "" for name in COLUMNS}
    later.update(battle_datetime="18.08.2026 10:00:00", _replay_hash="later")
    earlier = {name: "" for name in COLUMNS}
    earlier.update(battle_datetime="17.08.2026 10:00:00", _replay_hash="earlier")
    store.append_atomic(later)
    store.append_atomic(earlier)
    assert [row["battle_datetime"] for row in store.rows()] == [
        "17.08.2026 10:00:00", "18.08.2026 10:00:00"
    ]


def test_latest_battle_ignores_undated_parse_issue(tmp_path):
    store = CsvStore(tmp_path / "latest.csv")
    battle = {name: "" for name in COLUMNS}
    battle.update(
        battle_datetime="17.08.2026 10:00:00", tank_name="IS-7",
        map_name="Карта", parse_status="complete", _replay_hash="battle",
    )
    issue = {name: "" for name in COLUMNS}
    issue.update(parse_status="unsupported", parse_error="Bad replay", _replay_hash="issue")
    store.append_atomic(battle)
    store.append_atomic(issue)

    total, failed, latest = count_csv(store.path)

    assert total == 2
    assert failed == 1
    assert latest is not None and latest["tank_name"] == "IS-7"


def test_complete_result_replaces_incomplete_same_battle(tmp_path):
    store = CsvStore(tmp_path / "battles.csv")
    incomplete = {name: "" for name in COLUMNS}
    incomplete.update(
        battle_datetime="17.08.2026 10:00:00", parse_status="incomplete",
        _replay_hash="early",
    )
    complete = {name: "" for name in COLUMNS}
    complete.update(
        battle_datetime="17.08.2026 10:00:00", parse_status="complete",
        damage_dealt=1234, _replay_hash="final",
    )
    store.append_atomic(incomplete)
    store.append_atomic(complete)
    assert len(store.rows()) == 1
    assert store.rows()[0]["damage_dealt"] == "1234"


def test_zero_byte_temp_replay_is_ignored(tmp_path):
    replay_dir = tmp_path / "replays"
    replay_dir.mkdir()
    (replay_dir / "temp.wotreplay").touch()
    summary = import_replays(replay_dir, tmp_path / "battles.csv", __import__("logging").getLogger("test"))
    assert summary.imported == 0
    assert not (tmp_path / "battles.csv").exists()
