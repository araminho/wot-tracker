from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import win32cred
import win32crypt


APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "WoT Battle Tracker"
SETTINGS_FILE = APP_DIR / "settings.json"
LOG_DIR = APP_DIR / "logs"
DEFAULT_GAME = Path(r"T:\Games\World_of_Tanks_EU")
CREDENTIAL_TARGET = "WoT Battle Tracker PostgreSQL"
PASSWORD_FILE = APP_DIR / "postgres-password.dat"


def find_replay_dir() -> Path | None:
    candidates = [DEFAULT_GAME / "replays"]
    for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        candidates.extend([
            Path(f"{drive}:\\Games\\World_of_Tanks_EU\\replays"),
            Path(f"{drive}:\\Games\\World_of_Tanks\\replays"),
        ])
    return next((path for path in candidates if path.is_dir()), None)


def load_settings() -> dict[str, str]:
    defaults = {
        "replay_dir": str(find_replay_dir() or ""),
        "csv_path": str(APP_DIR / "battles.csv"),
        "postgres_enabled": "false",
        "postgres_host": "localhost",
        "postgres_port": "5432",
        "postgres_database": "wot_tracker",
        "postgres_user": "postgres",
    }
    try:
        loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        defaults.update({key: str(value) for key, value in loaded.items() if key in defaults})
    except (OSError, ValueError, TypeError):
        pass
    return defaults


def save_settings(settings: dict[str, str]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, SETTINGS_FILE)


def load_database_password() -> str:
    try:
        protected = base64.b64decode(PASSWORD_FILE.read_bytes(), validate=True)
        return win32crypt.CryptUnprotectData(protected, None, None, None, 0)[1].decode("utf-8")
    except Exception:
        pass
    try:
        value = win32cred.CredRead(CREDENTIAL_TARGET, win32cred.CRED_TYPE_GENERIC)["CredentialBlob"]
        return value.decode("utf-16-le") if isinstance(value, bytes) else str(value)
    except Exception:
        return ""


def save_database_password(user: str, password: str) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    protected = win32crypt.CryptProtectData(
        password.encode("utf-8"), "WoT Battle Tracker PostgreSQL", None, None, None, 0,
    )
    temporary = PASSWORD_FILE.with_suffix(".tmp")
    temporary.write_bytes(base64.b64encode(protected))
    os.replace(temporary, PASSWORD_FILE)
    try:
        win32cred.CredWrite({
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": CREDENTIAL_TARGET,
            "UserName": user,
            "CredentialBlob": password,
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
        }, 0)
    except Exception:
        # The DPAPI-protected file is the primary store. Credential Manager is
        # retained as a secondary copy where it is available.
        pass
