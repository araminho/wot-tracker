# World of Tanks Local Battle Tracker

A small Windows desktop application that scans World of Tanks `.wotreplay` files and stores battle results in **CSV and, optionally, PostgreSQL**. CSV output is always maintained; enabling PostgreSQL stores the same battle results in a database as well.

The tracker reads replay files without modifying World of Tanks, installing a mod, or inspecting game memory. CSV-only use needs no database server. PostgreSQL storage requires a running server at the host you configure.

## Use the Windows app

No Python installation is needed when using a packaged executable. If you have built the project, it is located at `dist\WoT Battle Tracker.exe`; the source checkout does not include this generated file.

1. Open `WoT Battle Tracker.exe`.
2. Confirm the **Replay directory**, or select it with **Browse…**. The tracker checks common game installation locations; select the game's `replays` folder manually if detection fails.
3. Confirm the **CSV destination**. The default is `%LOCALAPPDATA%\WoT Battle Tracker\battles.csv`.
4. For database storage, complete the PostgreSQL setup below. Otherwise, leave **Store battle results in PostgreSQL** unchecked.
5. Leave the tracker running while you play. It scans existing replays at startup and monitors the folder for new files. Use **Rescan existing replays** to scan again and **Open CSV** to view the exported results.

The executable is portable rather than an installer. You may copy it to the Desktop or another convenient folder. Windows SmartScreen may show a warning because the locally built executable is unsigned.

In the game settings, recording **all battles** is recommended. Recording only the latest battle also works while the tracker remains running, but retaining all replays allows missed records to be recovered later.

## PostgreSQL setup

PostgreSQL is optional and is installed separately from the tracker. The app creates the results table, **not the database or database user**.

1. Start your PostgreSQL server and create a database named `wot_tracker` (or choose an existing database).
2. Use a database account that can connect to that database, create and alter the results table in its target schema, and read and write its rows.
3. Enter the connection details in the app's **PostgreSQL** section:

   | Field | Value |
   | --- | --- |
   | Host | `localhost` for a server on the same computer, or your server's hostname |
   | Port | `5432` by default |
   | Database | `wot_tracker`, or the database you created |
   | User | Your PostgreSQL account; the app initially displays `postgres` |
   | Password | That account's password |

4. Select **Connect and create table**. This also enables PostgreSQL storage, saves the connection settings, creates `battle_results` if needed, and starts a rescan after a successful connection.

The table stores typed numbers, booleans, and timestamps. `battle_heroes_json` and `destroyed_modules_json` use PostgreSQL `jsonb`. Reimporting a replay updates its existing database row using a unique replay fingerprint. The importer also replaces a row with a different fingerprint at the same battle timestamp, so use a separate database for each player's history.

Connecting later can populate PostgreSQL from replay files still present in the selected folder. The tracker does not import historical rows directly from the CSV. Keep your replays if you want to rescan them later.

If database synchronization fails, CSV output is retained and the error is recorded in the logs. Fix the connection and select **Rescan existing replays** to retry from the available replay files. The displayed battle counts reflect the CSV and do not confirm that PostgreSQL synchronization succeeded.

The saved password is protected with Windows DPAPI under `%LOCALAPPDATA%\WoT Battle Tracker`, with Windows Credential Manager retained as a secondary copy. It is not stored in `settings.json`. Enter the password again when moving to another computer or Windows account.

## Battle data and CSV notes

`battle_mode` uses World of Tanks' internal gameplay identifiers:

- `ctf` means a Standard Battle with one base for each team. Despite the internal name, tanks do not carry flags.
- `domination` means an Encounter Battle with one neutral base.
- `assault` means an Assault battle, where one team attacks and the other defends a base.

Tank names, tiers, and classes are read from the installed client's own localization and packed vehicle definitions. `damage_rank` and `xp_rank` are the player's numeric positions within their own team; tied players share a rank. `mastery_badge` is `class_3`, `class_2`, `class_1`, or `ace_tanker`, and is empty/NULL when no badge was earned. `battle_heroes_json` is a compact JSON list containing only Battle Heroes such as `top_gun` and `high_caliber`; it is `[]` when none were earned. `blocked_shots` counts direct hits received without damage or penetration. Critical hits dealt are split into damaged modules, destroyed modules, and crew injuries using the per-enemy critical-effect masks in the detailed results. `destroyed_modules_json` contains a compact JSON list of the destroyed module names; repeated names mean that the same module type was destroyed on multiple enemies. Rows are always written in ascending battle-date order. Duplicate replay fingerprints are kept in a neighboring `.wot-index.json` sidecar file rather than exposed as CSV columns.

CSV rows are sorted by battle date; when querying PostgreSQL, use `ORDER BY battle_datetime` to request chronological results.

Columns that cannot be filled reliably—including equipment, consumables, consumable activation counts, and critical hits received—are not included in either storage format.

Metadata-only replays are stored as `incomplete`; unsupported files are stored as `unsupported`, with details in `parse_error`. Detailed results cannot be recovered if the replay was saved before the battle ended.

## Run from source (developers only)

Windows and Python 3.11+ with Tkinter are required. From the project directory, create a virtual environment and install the runtime dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install psycopg2-binary pywin32
.\.venv\Scripts\python.exe -m wot_tracker
```

On first launch you can choose another replay directory and CSV destination. Settings and rotating diagnostic logs are stored under `%LOCALAPPDATA%\WoT Battle Tracker`.

In the game settings, enable replay recording for **all battles**. The tracker intentionally does not edit game preferences.

## Build the executable

After installing the source dependencies above, install PyInstaller and build using the virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name "WoT Battle Tracker" launcher.py
```

The packaged app is written to `dist\WoT Battle Tracker.exe`.

Alternatively, run `.\build.ps1` with an environment where `python` has the runtime dependencies and PyInstaller installed.

## Troubleshooting

- **No battles appear:** check the replay directory and the game's replay-recording setting, then select **Rescan existing replays**.
- **PostgreSQL connection fails:** check that the server is running, the database already exists, and the host, port, account permissions, and password are correct.
- **CSV has battles but PostgreSQL does not:** confirm database storage is enabled, inspect **Open log directory**, and reconnect and rescan once the database issue is resolved.
- **CSV cannot be updated:** close applications that may be locking the file and verify that the destination folder is writable.

Settings and logs are stored under `%LOCALAPPDATA%\WoT Battle Tracker`, independently of the executable's location.
