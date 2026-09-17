from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import struct
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .game_data import VehicleCatalog


REPLAY_MAGIC = b"\x12\x32\x34\x11"
MAX_JSON_BLOCK = 64 * 1024 * 1024

COLUMNS = [
    "battle_datetime", "map_name", "battle_mode",
    "battle_duration_seconds", "battle_result", "tank_name",
    "tank_nation", "tank_tier", "tank_class", "damage_dealt",
    "damage_rank", "xp_rank", "mastery_badge", "battle_heroes_json",
    "damage_received", "kills", "assists", "spotting_damage",
    "blocked_damage", "blocked_shots", "shots_fired", "hits", "penetrations", "survived",
    "critical_hits_dealt", "modules_damaged", "modules_destroyed",
    "crew_injuries", "destroyed_modules_json",
    "replay_version", "parse_status", "parse_error", "imported_at",
]

MODULE_NAMES = (
    "engine", "ammo_bay", "fuel_tank", "radio", "track", "gun",
    "turret_rotator", "surveying_device", None, "wheel",
)
CREW_ROLE_COUNT = 5
MASTERY_BADGES = {
    1: "class_3",
    2: "class_2",
    3: "class_1",
    4: "ace_tanker",
}
BATTLE_HERO_IDS = (
    (34, "top_gun"),
    (228, "high_caliber"),
    (39, "confederate"),
    (227, "tank_sniper"),
    (36, "tank_sniper"),  # Legacy Sniper award used the older dossier record.
    (41, "patrol_duty"),
    (38, "steel_wall"),
    (37, "defender"),
    (35, "invader"),
    (40, "scout"),
)


class ReplayError(Exception):
    status = "error"


class UnsupportedReplay(ReplayError):
    status = "unsupported"


class IncompleteReplay(ReplayError):
    status = "incomplete"


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def replay_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _blank_row(path: Path, digest: str) -> dict[str, Any]:
    row = {name: "" for name in COLUMNS}
    row.update(_source_replay=path.name, _replay_hash=digest, imported_at=utc_now())
    return row


def _json_cell(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _mastery_badge(value: Any) -> str:
    try:
        return MASTERY_BADGES.get(int(value), "")
    except (TypeError, ValueError):
        return ""


def _battle_heroes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    earned: set[int] = set()
    for item in value:
        try:
            earned.add(int(item))
        except (TypeError, ValueError):
            continue
    labels: list[str] = []
    for achievement_id, label in BATTLE_HERO_IDS:
        if achievement_id in earned and label not in labels:
            labels.append(label)
    return labels


def _critical_effects(details: dict[str, Any]) -> tuple[int, int, int, list[str]]:
    modules_damaged = 0
    modules_destroyed = 0
    crew_injuries = 0
    destroyed_modules: list[str] = []
    for value in details.values():
        if not isinstance(value, dict) or not isinstance(value.get("crits"), int):
            continue
        mask = value["crits"]
        damaged_mask = mask & 0xFFF
        destroyed_mask = (mask >> 12) & 0xFFF
        crew_mask = (mask >> 24) & 0xFF
        for index, name in enumerate(MODULE_NAMES):
            if name is None:
                continue
            modules_damaged += (damaged_mask >> index) & 1
            if destroyed_mask & (1 << index):
                modules_destroyed += 1
                destroyed_modules.append(name)
        crew_injuries += (crew_mask & ((1 << CREW_ROLE_COUNT) - 1)).bit_count()
    return modules_damaged, modules_destroyed, crew_injuries, destroyed_modules


def _read_blocks(path: Path) -> list[Any]:
    with path.open("rb") as stream:
        if stream.read(4) != REPLAY_MAGIC:
            raise UnsupportedReplay("Unrecognized replay signature")
        count_raw = stream.read(4)
        if len(count_raw) != 4:
            raise IncompleteReplay("Replay header is still incomplete")
        count = struct.unpack("<I", count_raw)[0]
        if count < 1 or count > 16:
            raise UnsupportedReplay(f"Unsupported JSON block count: {count}")
        blocks: list[Any] = []
        for index in range(count):
            length_raw = stream.read(4)
            if len(length_raw) != 4:
                raise IncompleteReplay(f"JSON block {index + 1} has no length")
            length = struct.unpack("<I", length_raw)[0]
            if length > MAX_JSON_BLOCK:
                raise UnsupportedReplay(f"JSON block {index + 1} is too large")
            payload = stream.read(length)
            if len(payload) != length:
                raise IncompleteReplay(f"JSON block {index + 1} is truncated")
            try:
                blocks.append(json.loads(payload.decode("utf-8-sig")))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise UnsupportedReplay(f"JSON block {index + 1} is unsupported: {exc}") from exc
        return blocks


def _result_root(block: Any) -> dict[str, Any] | None:
    if isinstance(block, dict) and "common" in block:
        return block
    if isinstance(block, list):
        for item in block:
            if isinstance(item, dict) and "common" in item:
                return item
    return None


def _value(data: dict[str, Any] | None, *names: str) -> Any:
    if not data:
        return ""
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return ""


def _vehicle_identity(meta: dict[str, Any]) -> tuple[str, str, str]:
    raw = str(meta.get("playerVehicle") or "")
    raw = raw.replace("-", ":", 1) if ":" not in raw and "-" in raw else raw
    nation, _, code = raw.partition(":")
    # The replay contains the stable code, not a localized tank display name.
    label = re.sub(r"^[A-Za-z]{1,3}[0-9]+_", "", code).replace("_", " ").strip()
    return raw, label or code, nation


def _own_vehicle(meta: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    player_id = str(meta.get("playerID", ""))
    player_name = meta.get("playerName")
    vehicles = meta.get("vehicles") if isinstance(meta.get("vehicles"), dict) else {}
    for vehicle_id, vehicle in vehicles.items():
        if not isinstance(vehicle, dict):
            continue
        if str(vehicle.get("accountDBID", "")) == player_id:
            return str(vehicle_id), vehicle
        if vehicle.get("name") == player_name or vehicle.get("fakeName") == player_name:
            return str(vehicle_id), vehicle
    return None, {}


def _personal_result(root: dict[str, Any], meta: dict[str, Any], vehicle_id: str | None) -> dict[str, Any] | None:
    personal = root.get("personal")
    if not isinstance(personal, dict):
        return None
    candidates = [v for k, v in personal.items() if k != "avatar" and isinstance(v, dict)]
    if len(candidates) == 1:
        return candidates[0]
    account_id = str(meta.get("playerID", ""))
    for result in candidates:
        if str(result.get("accountDBID", "")) == account_id:
            return result
    vehicles = root.get("vehicles")
    if vehicle_id and isinstance(vehicles, dict):
        item = vehicles.get(vehicle_id)
        if isinstance(item, list) and item and isinstance(item[0], dict):
            return item[0]
        if isinstance(item, dict):
            return item
    return None


def _team_rank(root: dict[str, Any], result: dict[str, Any], field: str) -> int | str:
    """Return competition rank among the recording player's teammates."""
    team = _value(result, "team")
    vehicles = root.get("vehicles")
    if team == "" or not isinstance(vehicles, dict):
        return ""

    teammates: list[dict[str, Any]] = []
    for item in vehicles.values():
        candidates = item if isinstance(item, list) else [item]
        teammates.extend(
            candidate for candidate in candidates
            if isinstance(candidate, dict) and str(candidate.get("team", "")) == str(team)
        )
    values = [candidate[field] for candidate in teammates if candidate.get(field) is not None]
    account_id = str(result.get("accountDBID", ""))
    own_values = [
        candidate[field] for candidate in teammates
        if candidate.get(field) is not None
        and account_id
        and str(candidate.get("accountDBID", "")) == account_id
    ]
    if not values or not own_values:
        return ""
    try:
        # The personal XP result includes premium, daily, and other multipliers.
        # Team-score rows contain the comparable base XP shown on the scoreboard.
        own_value = sum(own_values)
        return 1 + sum(value > own_value for value in values)
    except TypeError:
        return ""


def parse_replay(path: str | Path, catalog: VehicleCatalog | None = None) -> dict[str, Any]:
    path = Path(path)
    digest = replay_hash(path)
    row = _blank_row(path, digest)
    try:
        blocks = _read_blocks(path)
        meta = blocks[0] if blocks and isinstance(blocks[0], dict) else {}
        if not meta:
            raise UnsupportedReplay("Replay metadata block is missing")

        root = next((_result_root(block) for block in blocks[1:] if _result_root(block)), None)
        vehicle_id, lobby_vehicle = _own_vehicle(meta)
        tank_id, tank_name, nation = _vehicle_identity(meta)
        vehicle_info = catalog.lookup(tank_id) if catalog else None
        version = _value(meta, "clientVersionFromExe", "clientVersionFromXml")
        row.update(
            battle_datetime=_value(meta, "dateTime"),
            map_name=_value(meta, "mapDisplayName"),
            battle_mode=_value(meta, "gameplayID"),
            tank_name=vehicle_info.name if vehicle_info and vehicle_info.name else tank_name,
            tank_nation=nation,
            tank_tier=vehicle_info.tier if vehicle_info else "",
            tank_class=vehicle_info.vehicle_class if vehicle_info else "",
            replay_version=version,
        )

        if not root:
            row.update(parse_status="incomplete", parse_error="Detailed battle results are not present")
            return row

        common = root.get("common") if isinstance(root.get("common"), dict) else {}
        result = _personal_result(root, meta, vehicle_id)
        row["battle_duration_seconds"] = _value(common, "duration")
        team = _value(result, "team") if result else _value(lobby_vehicle, "team")
        winner = _value(common, "winnerTeam")
        if winner != "":
            row["battle_result"] = "draw" if int(winner) == 0 else ("victory" if str(winner) == str(team) else "defeat")

        if result is None:
            row.update(parse_status="incomplete", parse_error="Recording player's detailed result is missing")
            return row

        radio = _value(result, "damageAssistedRadio")
        assist_fields = (
            "damageAssistedRadio", "damageAssistedTrack", "damageAssistedStun",
            "damageAssistedSmoke", "damageAssistedInspire",
        )
        available_assists = [result[name] for name in assist_fields if name in result and result[name] is not None]
        details = result.get("details") if isinstance(result.get("details"), dict) else None
        critical_hits: Any = ""
        modules_damaged: Any = ""
        modules_destroyed: Any = ""
        crew_injuries: Any = ""
        destroyed_modules_json = ""
        if details is not None:
            (
                modules_damaged, modules_destroyed, crew_injuries,
                destroyed_modules,
            ) = _critical_effects(details)
            critical_hits = modules_damaged + modules_destroyed + crew_injuries
            destroyed_modules_json = _json_cell(destroyed_modules)
        health = _value(result, "health")
        death_count = _value(result, "deathCount")
        survived: Any = ""
        if health != "":
            survived = bool(health > 0)
        elif death_count != "":
            survived = int(death_count) == 0

        row.update(
            damage_dealt=_value(result, "damageDealt"),
            damage_rank=_team_rank(root, result, "damageDealt"),
            xp_rank=_team_rank(root, result, "xp"),
            mastery_badge=_mastery_badge(_value(result, "markOfMastery")),
            battle_heroes_json=_json_cell(_battle_heroes(result.get("achievements"))),
            damage_received=_value(result, "damageReceived"),
            kills=_value(result, "kills"),
            assists=sum(available_assists) if available_assists else "",
            spotting_damage=radio,
            blocked_damage=_value(result, "damageBlockedByArmor"),
            blocked_shots=_value(result, "noDamageDirectHitsReceived"),
            shots_fired=_value(result, "shots"),
            hits=_value(result, "directEnemyHits", "directHits"),
            penetrations=_value(result, "piercings", "piercingEnemyHits"),
            survived=survived,
            critical_hits_dealt=critical_hits,
            modules_damaged=modules_damaged,
            modules_destroyed=modules_destroyed,
            crew_injuries=crew_injuries,
            destroyed_modules_json=destroyed_modules_json,
            parse_status="complete",
            parse_error="",
        )
        avatar = root.get("personal", {}).get("avatar", {}) if isinstance(root.get("personal"), dict) else {}
        if isinstance(avatar, dict) and avatar.get("isPrematureLeave"):
            row.update(parse_status="incomplete", parse_error="Player left before the battle results completed")
        return row
    except ReplayError as exc:
        row.update(parse_status=exc.status, parse_error=str(exc))
        return row
    except Exception as exc:  # A bad replay must never terminate monitoring.
        row.update(parse_status="error", parse_error=f"{type(exc).__name__}: {exc}")
        return row


class CsvStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @property
    def index_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".wot-index.json")

    def rows(self) -> list[dict[str, str]]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return []
        with self.path.open("r", encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    def hashes(self) -> set[str]:
        known: set[str] = set()
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                known.update(str(value) for value in payload if value)
        except (OSError, ValueError, TypeError):
            pass
        # Version 1.0 stored fingerprints visibly. Import them once before the
        # schema migration removes that column.
        legacy = {row.get("replay_hash", "") for row in self.rows() if row.get("replay_hash")}
        if legacy - known:
            known.update(legacy)
            self._write_index(known)
        return known

    def _write_index(self, hashes: set[str]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
        temporary.write_text(json.dumps(sorted(hashes), indent=2), encoding="utf-8")
        os.replace(temporary, self.index_path)

    def migrate(self, catalog: VehicleCatalog | None = None) -> None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        with self.path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            old_columns = reader.fieldnames or []
            rows = list(reader)
        cleaned_rows = [
            row for row in rows
            if row.get("battle_datetime") or row.get("parse_error") != "Unrecognized replay signature"
        ]
        changed = old_columns != COLUMNS or len(cleaned_rows) != len(rows)
        rows = cleaned_rows
        for row in rows:
            tank_id = row.get("tank_id", "")
            if catalog and tank_id:
                info = catalog.lookup(tank_id)
                updates = {
                    "tank_name": info.name or row.get("tank_name", ""),
                    "tank_tier": info.tier if info.tier != "" else row.get("tank_tier", ""),
                    "tank_class": info.vehicle_class or row.get("tank_class", ""),
                }
                if any(str(row.get(key, "")) != str(value) for key, value in updates.items()):
                    row.update(updates)
                    changed = True
        if changed:
            self._write_rows_atomic(rows)

    def refresh_existing(self, replay_rows: list[dict[str, Any]]) -> None:
        """Backfill newly supported fields in rows already protected by the hash index."""
        if not replay_rows or not self.path.exists():
            return
        rows = self.rows()
        by_datetime = {row.get("battle_datetime", ""): row for row in rows if row.get("battle_datetime")}
        changed = False
        for fresh in replay_rows:
            current = by_datetime.get(str(fresh.get("battle_datetime", "")))
            if current is None:
                continue
            for key in COLUMNS:
                if key == "imported_at":
                    continue
                value = fresh.get(key, "")
                if value != "" and str(current.get(key, "")) != str(value):
                    current[key] = value
                    changed = True
        if changed or rows != self._sort_rows(rows):
            self._write_rows_atomic(rows)

    def append_atomic(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.rows()
        battle_datetime = str(row.get("battle_datetime", ""))
        replaced = False
        if battle_datetime:
            for index, current in enumerate(existing):
                if current.get("battle_datetime") != battle_datetime:
                    continue
                # Never downgrade a complete result if an earlier replay form
                # is encountered later. Otherwise replace it with fresher data.
                if current.get("parse_status") != "complete" or row.get("parse_status") == "complete":
                    existing[index] = row
                replaced = True
                break
        self._write_rows_atomic(existing if replaced else [*existing, row])
        digest = str(row.get("_replay_hash", ""))
        if digest:
            known = self.hashes()
            known.add(digest)
            self._write_index(known)

    def _write_rows_atomic(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=COLUMNS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows({key: row.get(key, "") for key in COLUMNS} for row in self._sort_rows(rows))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    @staticmethod
    def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def key(row: dict[str, Any]) -> tuple[datetime, str]:
            raw = str(row.get("battle_datetime", ""))
            try:
                moment = datetime.strptime(raw, "%d.%m.%Y %H:%M:%S")
            except ValueError:
                try:
                    moment = datetime.fromisoformat(raw).replace(tzinfo=None)
                except ValueError:
                    moment = datetime.max
            return moment, str(row.get("imported_at", ""))
        return sorted(rows, key=key)


@dataclass
class ImportSummary:
    imported: int = 0
    duplicates: int = 0
    failed: int = 0
    latest: dict[str, Any] | None = None


def import_replays(
    replay_dir: str | Path,
    csv_path: str | Path,
    logger: logging.Logger,
    callback: Callable[[dict[str, Any]], None] | None = None,
    files: Iterable[Path] | None = None,
    database_store: Any = None,
) -> ImportSummary:
    directory = Path(replay_dir)
    store = CsvStore(csv_path)
    game_root = directory.parent
    catalog = VehicleCatalog(game_root) if (game_root / "res" / "packages" / "scripts.pkg").is_file() else None
    known = store.hashes()
    store.migrate(catalog)
    summary = ImportSummary()
    refresh_rows: list[dict[str, Any]] = []
    database_rows: list[dict[str, Any]] = []
    targets = list(files) if files is not None else sorted(directory.glob("*.wotreplay"), key=lambda p: p.stat().st_mtime)
    for path in targets:
        try:
            if path.name.lower() == "temp.wotreplay" or path.stat().st_size == 0:
                logger.info("Ignored temporary replay file: %s", path.name)
                continue
            logger.info("Discovered replay: %s", path.name)
            digest = replay_hash(path)
            if digest in known:
                summary.duplicates += 1
                logger.info("Skipped duplicate: %s", path.name)
                refreshed = parse_replay(path, catalog)
                refresh_rows.append(refreshed)
                database_rows.append(refreshed)
                continue
            row = parse_replay(path, catalog)
            database_rows.append(row)
            store.append_atomic(row)
            known.add(digest)
            summary.imported += 1
            summary.latest = row
            if row["parse_status"] != "complete":
                summary.failed += 1
                logger.warning("Imported %s replay %s: %s", row["parse_status"], path.name, row["parse_error"])
            else:
                logger.info("Imported replay: %s", path.name)
            if callback:
                callback(row)
        except Exception:
            summary.failed += 1
            logger.exception("Failed to import replay file %s", path.name)
    store.refresh_existing(refresh_rows)
    if database_store is not None:
        try:
            database_store.upsert_rows(database_rows)
            logger.info("Synchronized %d replay rows to PostgreSQL", len(database_rows))
        except Exception:
            logger.exception("Could not synchronize replay rows to PostgreSQL; CSV data is unaffected")
    return summary


def count_csv(path: str | Path) -> tuple[int, int, dict[str, str] | None]:
    rows = CsvStore(path).rows()
    failed = sum(row.get("parse_status") != "complete" for row in rows)
    latest = next((row for row in reversed(rows) if row.get("battle_datetime")), None)
    return len(rows), failed, latest
