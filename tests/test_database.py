import json
from datetime import datetime

from psycopg2.extras import Json

from wot_tracker.database import DB_COLUMNS, PostgresStore, _database_value


class FakeCursor:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, values=None):
        self.calls.append((sql, values))


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.cursor_instance


def test_database_values_are_typed():
    assert _database_value("damage_dealt", "1200") == 1200
    assert _database_value("damage_rank", "1") == 1
    assert _database_value("blocked_shots", "3") == 3
    assert _database_value("mastery_badge", "ace_tanker") == "ace_tanker"
    assert _database_value("mastery_badge", "") is None
    assert _database_value("survived", "True") is True
    assert _database_value("battle_datetime", "17.08.2026 10:00:00") == datetime(2026, 8, 17, 10)
    value = _database_value("destroyed_modules_json", '["engine","track"]')
    assert isinstance(value, Json) and value.adapted == ["engine", "track"]
    heroes = _database_value("battle_heroes_json", '["top_gun","high_caliber"]')
    assert isinstance(heroes, Json) and heroes.adapted == ["top_gun", "high_caliber"]


def test_upsert_creates_table_and_uses_replay_hash(monkeypatch):
    connection = FakeConnection()
    store = PostgresStore({}, "secret")
    monkeypatch.setattr(store, "_connect", lambda: connection)
    row = {name: "" for name in DB_COLUMNS}
    row.update(_replay_hash="abc", destroyed_modules_json=json.dumps([]))

    assert store.upsert_rows([row]) == 1
    assert "CREATE TABLE IF NOT EXISTS" in connection.cursor_instance.calls[0][0]
    assert "ADD COLUMN IF NOT EXISTS damage_rank" in connection.cursor_instance.calls[1][0]
    assert "ADD COLUMN IF NOT EXISTS blocked_shots" in connection.cursor_instance.calls[1][0]
    assert "ADD COLUMN IF NOT EXISTS mastery_badge" in connection.cursor_instance.calls[1][0]
    assert "ADD COLUMN IF NOT EXISTS battle_heroes_json" in connection.cursor_instance.calls[1][0]
    sql, values = connection.cursor_instance.calls[2]
    assert "ON CONFLICT (replay_hash) DO UPDATE" in sql
    assert values[0] == "abc"
