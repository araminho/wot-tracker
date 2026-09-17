from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

import psycopg2
from psycopg2.extras import Json


TABLE_NAME = "battle_results"

DB_COLUMNS = {
    "battle_datetime": "timestamp without time zone",
    "map_name": "text",
    "battle_mode": "text",
    "battle_duration_seconds": "integer",
    "battle_result": "text",
    "tank_name": "text",
    "tank_nation": "text",
    "tank_tier": "smallint",
    "tank_class": "text",
    "damage_dealt": "integer",
    "damage_rank": "smallint",
    "xp_rank": "smallint",
    "mastery_badge": "text",
    "battle_heroes_json": "jsonb",
    "damage_received": "integer",
    "kills": "integer",
    "assists": "integer",
    "spotting_damage": "integer",
    "blocked_damage": "integer",
    "blocked_shots": "integer",
    "shots_fired": "integer",
    "hits": "integer",
    "penetrations": "integer",
    "survived": "boolean",
    "critical_hits_dealt": "integer",
    "modules_damaged": "integer",
    "modules_destroyed": "integer",
    "crew_injuries": "integer",
    "destroyed_modules_json": "jsonb",
    "replay_version": "text",
    "parse_status": "text",
    "parse_error": "text",
    "imported_at": "timestamp with time zone",
}

INTEGER_COLUMNS = {
    name for name, sql_type in DB_COLUMNS.items()
    if sql_type in {"integer", "smallint"}
}


def _database_value(name: str, value: Any) -> Any:
    if value in (None, ""):
        return None
    if name in INTEGER_COLUMNS:
        return int(value)
    if name == "survived":
        return value if isinstance(value, bool) else str(value).lower() == "true"
    if name == "battle_datetime":
        return datetime.strptime(str(value), "%d.%m.%Y %H:%M:%S")
    if name == "imported_at":
        return datetime.fromisoformat(str(value))
    if name in {"battle_heroes_json", "destroyed_modules_json"}:
        return Json(json.loads(str(value)))
    return str(value)


class PostgresStore:
    def __init__(self, settings: dict[str, str], password: str):
        self.settings = settings
        self.password = password

    def _connect(self):
        password = self.password
        if not password:
            # The app may have been opened before credentials were configured.
            # Re-read Credential Manager for every connection until available.
            from .settings import load_database_password
            password = load_database_password()
        return psycopg2.connect(
            host=self.settings.get("postgres_host", "localhost"),
            port=int(self.settings.get("postgres_port", "5432")),
            dbname=self.settings.get("postgres_database", "wot_tracker"),
            user=self.settings.get("postgres_user", "postgres"),
            password=password,
            connect_timeout=5,
        )

    def ensure_table(self) -> None:
        definitions = ",\n".join(f'"{name}" {sql_type}' for name, sql_type in DB_COLUMNS.items())
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f'''CREATE TABLE IF NOT EXISTS "{TABLE_NAME}" (
                        id bigserial PRIMARY KEY,
                        replay_hash text NOT NULL UNIQUE,
                        {definitions}
                    )'''
                )
                cursor.execute(
                    f'''ALTER TABLE "{TABLE_NAME}"
                        ADD COLUMN IF NOT EXISTS damage_rank smallint,
                        ADD COLUMN IF NOT EXISTS xp_rank smallint,
                        ADD COLUMN IF NOT EXISTS mastery_badge text,
                        ADD COLUMN IF NOT EXISTS battle_heroes_json jsonb,
                        ADD COLUMN IF NOT EXISTS blocked_shots integer'''
                )

    def upsert_rows(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        names = list(DB_COLUMNS)
        quoted_names = ", ".join(f'"{name}"' for name in names)
        placeholders = ", ".join(["%s"] * (len(names) + 1))
        updates = ", ".join(f'"{name}" = EXCLUDED."{name}"' for name in names)
        sql = f'''INSERT INTO "{TABLE_NAME}" (replay_hash, {quoted_names})
                  VALUES ({placeholders})
                  ON CONFLICT (replay_hash) DO UPDATE SET {updates}'''
        with self._connect() as connection:
            with connection.cursor() as cursor:
                definitions = ",\n".join(f'"{name}" {sql_type}' for name, sql_type in DB_COLUMNS.items())
                cursor.execute(
                    f'''CREATE TABLE IF NOT EXISTS "{TABLE_NAME}" (
                        id bigserial PRIMARY KEY,
                        replay_hash text NOT NULL UNIQUE,
                        {definitions}
                    )'''
                )
                cursor.execute(
                    f'''ALTER TABLE "{TABLE_NAME}"
                        ADD COLUMN IF NOT EXISTS damage_rank smallint,
                        ADD COLUMN IF NOT EXISTS xp_rank smallint,
                        ADD COLUMN IF NOT EXISTS mastery_badge text,
                        ADD COLUMN IF NOT EXISTS battle_heroes_json jsonb,
                        ADD COLUMN IF NOT EXISTS blocked_shots integer'''
                )
                for row in rows:
                    digest = str(row.get("_replay_hash", ""))
                    if not digest:
                        continue
                    values = [digest, *(_database_value(name, row.get(name, "")) for name in names)]
                    battle_datetime = _database_value("battle_datetime", row.get("battle_datetime", ""))
                    if battle_datetime is not None:
                        cursor.execute(
                            f'DELETE FROM "{TABLE_NAME}" WHERE battle_datetime = %s AND replay_hash <> %s',
                            (battle_datetime, digest),
                        )
                    cursor.execute(sql, values)
        return len(rows)
