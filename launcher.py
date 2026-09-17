import sys

from wot_tracker.app import main


def self_test() -> None:
    """Packaged-build smoke test: initialize Tk and parse a supplied replay."""
    import tkinter as tk
    from pathlib import Path
    from wot_tracker.core import parse_replay

    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()
    root.destroy()
    if len(sys.argv) > 2:
        row = parse_replay(Path(sys.argv[2]))
        if row["parse_status"] != "complete":
            raise SystemExit(2)


def database_self_test() -> None:
    """Verify that a packaged build can load the saved password and connect."""
    from wot_tracker.database import PostgresStore
    from wot_tracker.settings import load_settings

    with PostgresStore(load_settings(), "")._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            if cursor.fetchone() != (1,):
                raise SystemExit(3)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        self_test()
    elif len(sys.argv) > 1 and sys.argv[1] == "--database-self-test":
        database_self_test()
    else:
        main()
