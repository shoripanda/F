from fomo_follower.database import Database
from fomo_follower.storage import SQLiteStore


def test_initialize_is_idempotent(tmp_path):
    database = Database(tmp_path / "store.sqlite3")
    store = SQLiteStore(database)

    store.initialize()
    store.initialize()

    with database.connect() as conn:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert row["value"] == "1"


def test_transaction_rolls_back_on_error(tmp_path):
    database = Database(tmp_path / "rollback.sqlite3")
    database.initialize()

    try:
        with database.transaction() as conn:
            conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES (?, ?)",
                ("rollback_probe", "written"),
            )
            raise RuntimeError("force rollback")
    except RuntimeError:
        pass

    with database.connect() as conn:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?",
            ("rollback_probe",),
        ).fetchone()
    assert row is None
