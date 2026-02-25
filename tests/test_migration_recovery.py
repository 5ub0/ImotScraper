"""
Test that the DB manager recovers from a stranded properties_old table
left by an interrupted migration.
"""
import sys, os, sqlite3, shutil, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from database.db_manager import DatabaseManager

def make_broken_db(path):
    """Create a DB that looks like migration 3 was interrupted mid-way:
    - properties_old exists (renamed but not yet rebuilt)
    - properties does NOT exist
    """
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE searches (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            search_name TEXT NOT NULL UNIQUE,
            url         TEXT NOT NULL,
            emails      TEXT NOT NULL DEFAULT '',
            created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO searches (search_name, url) VALUES ('test', 'https://example.com');

        CREATE TABLE properties_old (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id   TEXT NOT NULL,
            search_name TEXT NOT NULL,
            title       TEXT,
            location    TEXT,
            link        TEXT,
            status      TEXT NOT NULL DEFAULT 'Active',
            first_seen  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO properties_old (record_id, search_name, title, link)
        VALUES ('abc123', 'test', 'Test prop', 'https://example.com/abc');

        CREATE TABLE price_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            property_id INTEGER NOT NULL,
            price       TEXT NOT NULL,
            price_status TEXT NOT NULL DEFAULT 'Current',
            is_new      INTEGER NOT NULL DEFAULT 0,
            recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE scrape_runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            search_name   TEXT NOT NULL,
            run_date      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            records_found INTEGER NOT NULL DEFAULT 0,
            new_records   INTEGER NOT NULL DEFAULT 0,
            changed_prices INTEGER NOT NULL DEFAULT 0,
            inactive_count INTEGER NOT NULL DEFAULT 0,
            success       INTEGER NOT NULL DEFAULT 1,
            error_message TEXT
        );
    """)
    conn.close()

def test_recovers_from_stranded_properties_old():
    tmp = tempfile.mktemp(suffix='.db')
    try:
        make_broken_db(tmp)

        # Verify the broken state
        conn = sqlite3.connect(tmp)
        tables_before = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert 'properties_old' in tables_before, "Setup failed: properties_old should exist"
        assert 'properties' not in tables_before, "Setup failed: properties should NOT exist yet"

        # Now open via DatabaseManager — it should recover cleanly
        db = DatabaseManager(tmp)

        conn = sqlite3.connect(tmp)
        tables_after = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        props_cols = [r[1] for r in conn.execute("PRAGMA table_info(properties)").fetchall()]
        conn.close()

        assert 'properties_old' not in tables_after, "properties_old should have been cleaned up"
        assert 'properties' in tables_after, "properties table should exist"
        assert 'search_id' in props_cols, "properties should have search_id column"
        assert 'search_name' not in props_cols, "properties should NOT have search_name column"

        print("PASS: DB manager recovered from stranded properties_old")
    finally:
        try:
            db.close_all_connections()
        except Exception:
            pass
        for ext in ('', '-wal', '-shm'):
            try:
                os.remove(tmp + ext)
            except OSError:
                pass

if __name__ == '__main__':
    test_recovers_from_stranded_properties_old()
