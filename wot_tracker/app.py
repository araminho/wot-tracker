from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .core import count_csv, import_replays
from .database import PostgresStore
from .settings import (
    APP_DIR, LOG_DIR, load_database_password, load_settings,
    save_database_password, save_settings,
)


def configure_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("wot_tracker")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(LOG_DIR / "tracker.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


class TrackerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"WoT Battle Tracker {__version__}")
        self.geometry("800x680")
        self.minsize(740, 650)
        self.settings = load_settings()
        self.logger = configure_logging()
        self.logger.info("Application startup")
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_event = threading.Event()
        self.scan_lock = threading.Lock()
        self.file_state: dict[Path, tuple[int, float, int]] = {}
        self.replay_var = tk.StringVar(value=self.settings["replay_dir"])
        self.csv_var = tk.StringVar(value=self.settings["csv_path"])
        self.postgres_enabled_var = tk.BooleanVar(value=self.settings.get("postgres_enabled") == "true")
        self.postgres_host_var = tk.StringVar(value=self.settings.get("postgres_host", "localhost"))
        self.postgres_port_var = tk.StringVar(value=self.settings.get("postgres_port", "5432"))
        self.postgres_database_var = tk.StringVar(value=self.settings.get("postgres_database", "wot_tracker"))
        self.postgres_user_var = tk.StringVar(value=self.settings.get("postgres_user", "postgres"))
        self.database_password = load_database_password()
        self.postgres_password_var = tk.StringVar(value=self.database_password)
        self.status_var = tk.StringVar(value="Starting…")
        self.imported_var = tk.StringVar(value="0")
        self.latest_var = tk.StringVar(value="—")
        self.failed_var = tk.StringVar(value="0")
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(150, self._drain_events)
        self.after(300, self.rescan)
        threading.Thread(target=self._monitor, name="replay-monitor", daemon=True).start()

    def _build(self) -> None:
        root = ttk.Frame(self, padding=(18, 14))
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="World of Tanks Local Battle Tracker", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(root, text="Reads replay files only; it never changes the game or your replays.", foreground="#555").pack(anchor="w", pady=(2, 12))

        paths = ttk.LabelFrame(root, text="Locations", padding=12)
        paths.pack(fill="x")
        self._path_row(paths, 0, "Replay directory", self.replay_var, self.choose_replays)
        self._path_row(paths, 1, "CSV destination", self.csv_var, self.choose_csv)

        database = ttk.LabelFrame(root, text="PostgreSQL", padding=12)
        database.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(database, text="Store battle results in PostgreSQL", variable=self.postgres_enabled_var).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 6)
        )
        fields = (
            ("Host", self.postgres_host_var, 18, None),
            ("Port", self.postgres_port_var, 8, None),
            ("Database", self.postgres_database_var, 18, None),
            ("User", self.postgres_user_var, 18, None),
            ("Password", self.postgres_password_var, 18, "•"),
        )
        for column, (label, variable, width, show) in enumerate(fields):
            ttk.Label(database, text=label).grid(row=1, column=column, sticky="w", padx=(0, 6))
            entry = ttk.Entry(database, textvariable=variable, width=width, show=show or "")
            entry.grid(row=2, column=column, sticky="ew", padx=(0, 8))
        ttk.Button(database, text="Connect and create table", command=self.connect_database).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )

        stats = ttk.LabelFrame(root, text="Status", padding=12)
        stats.pack(fill="x", pady=10)
        labels = [
            ("Monitoring", self.status_var), ("Imported battles", self.imported_var),
            ("Latest imported battle", self.latest_var), ("Files with parse issues", self.failed_var),
        ]
        for row, (label, variable) in enumerate(labels):
            ttk.Label(stats, text=label + ":", width=23).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Label(stats, textvariable=variable).grid(row=row, column=1, sticky="w", pady=3)

        ttk.Label(
            root,
            text="In World of Tanks, set replay recording to ‘All battles’. The tracker cannot recover detailed results if a replay was saved before the battle ended.",
            wraplength=710,
            foreground="#7a4b00",
        ).pack(anchor="w", pady=(0, 10))
        buttons = ttk.Frame(root)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Rescan existing replays", command=self.rescan).pack(side="left")
        ttk.Button(buttons, text="Open CSV", command=self.open_csv).pack(side="left", padx=8)
        ttk.Button(buttons, text="Open log directory", command=lambda: self._open(LOG_DIR)).pack(side="left")

    def _path_row(self, parent: ttk.LabelFrame, row: int, label: str, variable: tk.StringVar, command) -> None:
        ttk.Label(parent, text=label, width=18).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=8, pady=5)
        ttk.Button(parent, text="Browse…", command=command).grid(row=row, column=2, pady=5)
        parent.columnconfigure(1, weight=1)

    def choose_replays(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.replay_var.get() or None, title="Select World of Tanks replay directory")
        if chosen:
            self.replay_var.set(chosen)
            self._save()
            self.file_state.clear()
            self.rescan()

    def choose_csv(self) -> None:
        chosen = filedialog.asksaveasfilename(
            initialfile=Path(self.csv_var.get()).name or "battles.csv",
            initialdir=str(Path(self.csv_var.get()).parent),
            defaultextension=".csv", filetypes=[("CSV files", "*.csv")], title="Select battle CSV",
        )
        if chosen:
            self.csv_var.set(chosen)
            self._save()
            self.rescan()

    def _save(self) -> None:
        self.settings = {
            "replay_dir": self.replay_var.get(), "csv_path": self.csv_var.get(),
            "postgres_enabled": str(self.postgres_enabled_var.get()).lower(),
            "postgres_host": self.postgres_host_var.get(),
            "postgres_port": self.postgres_port_var.get(),
            "postgres_database": self.postgres_database_var.get(),
            "postgres_user": self.postgres_user_var.get(),
        }
        save_settings(self.settings)
        if self.postgres_password_var.get():
            self.database_password = self.postgres_password_var.get()
            save_database_password(self.postgres_user_var.get(), self.database_password)

    def _database_store(self) -> PostgresStore | None:
        if self.settings.get("postgres_enabled") != "true":
            return None
        return PostgresStore(self.settings, self.database_password)

    def connect_database(self) -> None:
        self.postgres_enabled_var.set(True)
        self._save()
        self.status_var.set("Connecting to PostgreSQL…")
        threading.Thread(target=self._connect_database, name="postgres-connect", daemon=True).start()

    def _connect_database(self) -> None:
        try:
            store = self._database_store()
            if store is None:
                return
            store.ensure_table()
            self.logger.info("PostgreSQL table is ready")
            self.events.put(("database_ready", None))
        except Exception as exc:
            self.logger.exception("Could not connect to PostgreSQL")
            self.events.put(("database_error", str(exc)))

    def rescan(self) -> None:
        replay_dir = Path(self.replay_var.get())
        if not replay_dir.is_dir():
            self.status_var.set("Replay directory not found — choose it above")
            return
        if not self.csv_var.get():
            self.status_var.set("Choose a CSV destination")
            return
        self._save()
        self.status_var.set("Scanning…")
        threading.Thread(target=self._scan, args=(None,), name="manual-scan", daemon=True).start()

    def _scan(self, files: list[Path] | None) -> None:
        if not self.scan_lock.acquire(blocking=False):
            return
        try:
            replay_dir = self.settings["replay_dir"]
            csv_path = self.settings["csv_path"]
            if files is None:
                # A just-created file may still be owned by the game. The monitor will
                # pick it up after two unchanged observations.
                cutoff = time.time() - 3.0
                files = [p for p in Path(replay_dir).glob("*.wotreplay") if p.stat().st_mtime <= cutoff]
            summary = import_replays(
                replay_dir, csv_path, self.logger, files=files,
                database_store=self._database_store(),
            )
            self.events.put(("scan_done", summary))
        finally:
            self.scan_lock.release()

    def _monitor(self) -> None:
        while not self.stop_event.wait(2.0):
            directory = Path(self.settings["replay_dir"])
            if not directory.is_dir():
                continue
            ready: list[Path] = []
            current: set[Path] = set()
            try:
                for path in directory.glob("*.wotreplay"):
                    current.add(path)
                    stat = path.stat()
                    old = self.file_state.get(path)
                    stable = old[2] + 1 if old and old[:2] == (stat.st_size, stat.st_mtime) else 0
                    self.file_state[path] = (stat.st_size, stat.st_mtime, stable)
                    if stable == 1:  # unchanged for two polling observations
                        ready.append(path)
                for vanished in set(self.file_state) - current:
                    del self.file_state[vanished]
            except OSError:
                self.logger.exception("Could not inspect replay directory")
            if ready:
                threading.Thread(target=self._scan, args=(ready,), name="monitor-import", daemon=True).start()

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "scan_done":
                    self._refresh_counts()
                elif kind == "database_ready":
                    self.status_var.set("PostgreSQL connected — table ready")
                    self.rescan()
                elif kind == "database_error":
                    self.status_var.set("PostgreSQL connection failed — see logs")
                    messagebox.showerror("PostgreSQL connection failed", str(payload))
        except queue.Empty:
            pass
        if not self.stop_event.is_set():
            self.after(250, self._drain_events)

    def _refresh_counts(self) -> None:
        try:
            total, failed, latest = count_csv(self.csv_var.get())
            self.imported_var.set(str(total))
            self.failed_var.set(str(failed))
            if latest:
                title = latest.get("tank_name") or latest.get("source_replay") or "—"
                map_name = latest.get("map_name") or latest.get("map_id") or ""
                self.latest_var.set(f"{title} — {map_name}".strip(" —"))
            else:
                self.latest_var.set("—")
            self.status_var.set("Monitoring for new replays")
        except Exception as exc:
            self.status_var.set(f"CSV error: {exc}")
            self.logger.exception("Could not refresh CSV status")

    def _open(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True) if path.suffix == "" else None
        try:
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror("Could not open", str(exc))

    def open_csv(self) -> None:
        path = Path(self.csv_var.get())
        if not path.exists():
            messagebox.showinfo("CSV not created yet", "Rescan replays to create the CSV first.")
            return
        self._open(path)

    def close(self) -> None:
        self.logger.info("Application shutdown")
        self.stop_event.set()
        self.destroy()


def main() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    TrackerApp().mainloop()


if __name__ == "__main__":
    main()
