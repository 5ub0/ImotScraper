"""
Database module for ImotScraper - handles all SQLite persistence.
Replaces the CSV-based storage approach with a proper relational database.
"""

import sqlite3
import threading
import logging
import os
import time
import requests
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Manages all SQLite database operations for ImotScraper.
    Stores properties, price history, and scrape run logs.

    Uses one connection per thread (thread-local storage) with WAL journal
    mode so the scraper thread and the GUI thread can both access the DB
    simultaneously without locking each other out.
    """

    def __init__(self, db_path: str = "data/imot_scraper.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._local = threading.local()   # one conn per thread
        self._init_database()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        """Return a thread-local SQLite connection, creating it if needed."""
        if not getattr(self._local, "conn", None):
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")   # non-blocking concurrent access
            conn.execute("PRAGMA busy_timeout = 5000")  # wait up to 5 s instead of failing
            self._local.conn = conn
        return self._local.conn

    def _init_database(self):
        """Create tables if they do not exist yet, and run any needed migrations."""
        with self._get_connection() as conn:
            # searches must exist before properties (FK dependency)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS searches (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    search_name TEXT    NOT NULL UNIQUE,
                    url         TEXT    NOT NULL,
                    emails      TEXT    NOT NULL DEFAULT '',
                    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS properties (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id   TEXT    NOT NULL,
                    search_id   INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
                    title       TEXT,
                    location    TEXT,
                    description TEXT,
                    link        TEXT,
                    status      TEXT    NOT NULL DEFAULT 'Active',
                    first_seen  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (record_id, search_id)
                );

                CREATE TABLE IF NOT EXISTS price_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    property_id INTEGER NOT NULL,
                    price       TEXT    NOT NULL,
                    price_status TEXT   NOT NULL DEFAULT 'Current',
                    is_new      INTEGER NOT NULL DEFAULT 0,
                    recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (property_id) REFERENCES properties(id)
                );

                CREATE TABLE IF NOT EXISTS scrape_runs (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    search_id      INTEGER REFERENCES searches(id) ON DELETE CASCADE,
                    search_name    TEXT    NOT NULL,
                    run_date       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    records_found  INTEGER NOT NULL DEFAULT 0,
                    new_records    INTEGER NOT NULL DEFAULT 0,
                    changed_prices INTEGER NOT NULL DEFAULT 0,
                    inactive_count INTEGER NOT NULL DEFAULT 0,
                    success        INTEGER NOT NULL DEFAULT 1,
                    error_message  TEXT
                );

                CREATE TABLE IF NOT EXISTS property_images (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
                    url         TEXT    NOT NULL,
                    image_data  BLOB    NOT NULL,
                    position    INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (property_id, url)
                );
            """)
            self._migrate(conn)
        logger.info(f"Database ready at: {self.db_path}")

    def _migrate(self, conn: sqlite3.Connection):
        """Apply schema migrations for existing databases."""
        # ── Migration 1: price_history old_price → price_status ──────────────
        ph_cols = [r[1] for r in conn.execute("PRAGMA table_info(price_history)").fetchall()]
        if "old_price" in ph_cols:
            logger.info("Migrating price_history: replacing old_price with price_status column...")
            conn.executescript("""
                ALTER TABLE price_history RENAME TO price_history_old;
                CREATE TABLE price_history (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    property_id  INTEGER NOT NULL,
                    price        TEXT    NOT NULL,
                    price_status TEXT    NOT NULL DEFAULT 'Current',
                    is_new       INTEGER NOT NULL DEFAULT 0,
                    recorded_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (property_id) REFERENCES properties(id)
                );
                INSERT INTO price_history (property_id, price, price_status, is_new, recorded_at)
                SELECT property_id, price, 'Current', is_new, recorded_at
                FROM price_history_old;
                DROP TABLE price_history_old;
            """)
            self._recalculate_price_statuses(conn)
            logger.info("Migration 1 complete.")

        # ── Migration 2: add location column ─────────────────────────────────
        prop_cols = [r[1] for r in conn.execute("PRAGMA table_info(properties)").fetchall()]
        if "location" not in prop_cols:
            logger.info("Migrating properties: adding location column...")
            conn.execute("ALTER TABLE properties ADD COLUMN location TEXT")

        # ── Migration 3: switch properties from search_name → search_id FK ───
        # Clean up any stranded properties_old left by a previously interrupted migration.
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "properties_old" in tables:
            logger.warning("Found stranded properties_old table from interrupted migration — dropping it.")
            conn.execute("DROP TABLE properties_old")

        # Re-read columns in case migration 2 just altered the table.
        prop_cols = [r[1] for r in conn.execute("PRAGMA table_info(properties)").fetchall()]
        if "search_name" in prop_cols or "search_id" not in prop_cols:
            logger.info("Migrating properties: switching search_name → search_id FK...")
            conn.executescript("""
                PRAGMA foreign_keys = OFF;

                -- Remove exact duplicates first (same record_id + search_name),
                -- keeping the row with the lowest id (oldest / first inserted).
                DELETE FROM price_history
                WHERE property_id IN (
                    SELECT id FROM properties
                    WHERE id NOT IN (
                        SELECT MIN(id) FROM properties GROUP BY record_id, search_name
                    )
                );
                DELETE FROM properties
                WHERE id NOT IN (
                    SELECT MIN(id) FROM properties GROUP BY record_id, search_name
                );

                -- Rebuild the properties table with search_id instead of search_name.
                ALTER TABLE properties RENAME TO properties_old;

                CREATE TABLE properties (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id   TEXT    NOT NULL,
                    search_id   INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
                    title       TEXT,
                    location    TEXT,
                    link        TEXT,
                    status      TEXT    NOT NULL DEFAULT 'Active',
                    first_seen  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (record_id, search_id)
                );

                INSERT INTO properties (id, record_id, search_id, title, location, link,
                                        status, first_seen, last_seen)
                SELECT p.id,
                       p.record_id,
                       s.id,
                       p.title,
                       p.location,
                       p.link,
                       p.status,
                       p.first_seen,
                       p.last_seen
                FROM   properties_old p
                JOIN   searches s ON s.search_name = p.search_name;

                DROP TABLE properties_old;

                PRAGMA foreign_keys = ON;
            """)

            # Add search_id to scrape_runs if it's missing
            sr_cols = [r[1] for r in conn.execute("PRAGMA table_info(scrape_runs)").fetchall()]
            if "search_id" not in sr_cols:
                conn.execute("ALTER TABLE scrape_runs ADD COLUMN search_id INTEGER REFERENCES searches(id) ON DELETE CASCADE")
                conn.execute("""
                    UPDATE scrape_runs
                    SET search_id = (SELECT id FROM searches WHERE searches.search_name = scrape_runs.search_name)
                """)

            logger.info("Migration 3 complete.")

    def _recalculate_price_statuses(self, conn: sqlite3.Connection):
        """
        After a migration, set price_status correctly for all rows:
          newest row per property  → Current
          second newest            → Previous
          all older rows           → Older
        """
        property_ids = [r[0] for r in conn.execute("SELECT DISTINCT property_id FROM price_history").fetchall()]
        for pid in property_ids:
            rows = conn.execute(
                "SELECT id FROM price_history WHERE property_id = ? ORDER BY recorded_at DESC",
                (pid,)
            ).fetchall()
            for i, row in enumerate(rows):
                if i == 0:
                    status = "Current"
                elif i == 1:
                    status = "Previous"
                else:
                    status = "Older"
                conn.execute("UPDATE price_history SET price_status = ? WHERE id = ?", (status, row[0]))

    # ------------------------------------------------------------------
    # Core write operations
    # ------------------------------------------------------------------

    def upsert_property(
        self,
        record_id: str,
        search_id: int,
        title: str,
        location: str,
        description: str,
        link: str,
        price: str,
        is_new: bool,
    ) -> int:
        """
        Insert a new property or update an existing one.
        Records a price_history row ONLY when the property is new or the price changed.
        Description is stored on first fetch and never overwritten (changes are rare
        and the detail page is only fetched for new listings).
        Returns the property id.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO properties (record_id, search_id, title, location, description, link, status, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, 'Active', CURRENT_TIMESTAMP)
                ON CONFLICT(record_id, search_id) DO UPDATE SET
                    title       = excluded.title,
                    location    = excluded.location,
                    description = COALESCE(excluded.description, properties.description),
                    link        = excluded.link,
                    status      = 'Active',
                    last_seen   = CURRENT_TIMESTAMP
            """, (record_id, search_id, title, location, description, link))

            cursor.execute(
                "SELECT id FROM properties WHERE record_id = ? AND search_id = ?",
                (record_id, search_id)
            )
            property_id = cursor.fetchone()["id"]

            if is_new or self._price_changed(cursor, property_id, price):
                cursor.execute("""
                    UPDATE price_history SET price_status = 'Older'
                    WHERE property_id = ? AND price_status = 'Previous'
                """, (property_id,))
                cursor.execute("""
                    UPDATE price_history SET price_status = 'Previous'
                    WHERE property_id = ? AND price_status = 'Current'
                """, (property_id,))
                cursor.execute("""
                    INSERT INTO price_history (property_id, price, price_status, is_new)
                    VALUES (?, ?, 'Current', ?)
                """, (property_id, price, 1 if is_new else 0))

            return property_id

    def upsert_images(
        self,
        property_id: int,
        urls: List[str],
        session: "requests.Session | None" = None,
    ) -> int:
        """
        Download each image URL and store the binary data as a BLOB.

        - Uses INSERT OR IGNORE so re-scraping a property never overwrites or
          duplicates existing images.
        - A small delay (0.1 s) is inserted between downloads to be polite.
        - If a download fails the URL is skipped and a warning is logged.
        - Returns the number of images actually saved.

        Args:
            property_id: DB id of the owning property.
            urls:        Ordered list of image URLs to download.
            session:     Optional requests.Session to reuse (headers / retries).
                         Falls back to a plain requests.get if not supplied.
        """
        if not urls:
            return 0

        saved = 0
        for pos, url in enumerate(urls):
            try:
                time.sleep(0.1)
                if session is not None:
                    resp = session.get(url, timeout=15)
                else:
                    resp = requests.get(url, timeout=15)
                resp.raise_for_status()
                image_data = resp.content

                with self._get_connection() as conn:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO property_images
                            (property_id, url, image_data, position)
                        VALUES (?, ?, ?, ?)
                        """,
                        (property_id, url, image_data, pos),
                    )
                saved += 1
            except Exception as exc:
                logger.warning(f"Could not download image {url}: {exc}")

        return saved

    def get_images(self, property_id: int) -> List[Dict]:
        """
        Return ordered list of image records for a property.
        Each dict has keys: id, url, image_data (bytes), position.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, url, image_data, position
                FROM   property_images
                WHERE  property_id = ?
                ORDER  BY position
                """,
                (property_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def _price_changed(self, cursor: sqlite3.Cursor, property_id: int, new_price: str) -> bool:
        """Return True if the new price differs from the current recorded price."""
        row = cursor.execute(
            "SELECT price FROM price_history WHERE property_id = ? AND price_status = 'Current'",
            (property_id,)
        ).fetchone()
        return row is not None and row["price"] != new_price

    def mark_inactive(self, search_id: int, active_record_ids: List[str]) -> int:
        """
        Set status = 'Inactive' for every property in this search
        whose record_id is NOT in active_record_ids.
        Returns the number of rows marked inactive.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if active_record_ids:
                placeholders = ",".join("?" * len(active_record_ids))
                cursor.execute(f"""
                    UPDATE properties
                    SET    status    = 'Inactive',
                           last_seen = CURRENT_TIMESTAMP
                    WHERE  search_id = ?
                      AND  status    = 'Active'
                      AND  record_id NOT IN ({placeholders})
                """, [search_id] + active_record_ids)
            else:
                cursor.execute("""
                    UPDATE properties
                    SET    status    = 'Inactive',
                           last_seen = CURRENT_TIMESTAMP
                    WHERE  search_id = ?
                      AND  status    = 'Active'
                """, (search_id,))
            count = cursor.rowcount
            if count:
                logger.info(f"Marked {count} propert{'y' if count == 1 else 'ies'} as Inactive for search_id={search_id}")
            return count

    def log_scrape_run(
        self,
        search_id: int,
        search_name: str,
        records_found: int,
        new_records: int,
        changed_prices: int,
        inactive_count: int,
        success: bool,
        error_message: Optional[str] = None,
    ):
        """Persist a summary row for one scrape run."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO scrape_runs
                    (search_id, search_name, records_found, new_records, changed_prices,
                     inactive_count, success, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (search_id, search_name, records_found, new_records, changed_prices,
                  inactive_count, 1 if success else 0, error_message))

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_properties(self, search_id: int, status: Optional[str] = None) -> List[Dict]:
        """Return properties for a search by its id, optionally filtered by status."""
        with self._get_connection() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM properties WHERE search_id = ? AND status = ? ORDER BY last_seen DESC",
                    (search_id, status)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM properties WHERE search_id = ? ORDER BY status, last_seen DESC",
                    (search_id,)
                ).fetchall()
            return [dict(r) for r in rows]

    def get_property_by_link(self, link: str) -> Optional[Dict]:
        """Return a single property dict looked up by its listing URL, or None."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM properties WHERE link = ? LIMIT 1",
                (link,)
            ).fetchone()
            return dict(row) if row else None

    def get_price_history(self, property_id: int) -> List[Dict]:
        """Return full price history for a single property, newest first."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM price_history WHERE property_id = ? ORDER BY recorded_at DESC",
                (property_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_property_by_link(self, link: str) -> Optional[Dict]:
        """Return the property row matching the given listing URL, or None."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM properties WHERE link = ? LIMIT 1", (link,)
            ).fetchone()
            return dict(row) if row else None

    def get_new_and_changed_since_last_run(self, search_id: int) -> Dict[str, List[Dict]]:
        """
        Return new listings and price changes recorded in the most recent scrape run.
        For changed prices, also fetches the previous price for display.
        Useful for building email reports.
        """
        with self._get_connection() as conn:
            last_run = conn.execute("""
                SELECT run_date FROM scrape_runs
                WHERE search_id = ? AND success = 1
                ORDER BY run_date DESC LIMIT 1
            """, (search_id,)).fetchone()

            if not last_run:
                return {"new": [], "changed": []}

            since = last_run["run_date"]

            new_records = conn.execute("""
                SELECT p.record_id, p.title, p.link, ph.price, ph.recorded_at
                FROM   price_history ph
                JOIN   properties    p ON p.id = ph.property_id
                WHERE  p.search_id  = ?
                  AND  ph.is_new    = 1
                  AND  ph.recorded_at >= ?
                ORDER  BY ph.recorded_at DESC
            """, (search_id, since)).fetchall()

            changed_records = conn.execute("""
                SELECT p.record_id, p.title, p.link,
                       cur.price    AS current_price,
                       prev.price   AS previous_price,
                       cur.recorded_at
                FROM   price_history cur
                JOIN   properties    p    ON p.id  = cur.property_id
                LEFT JOIN price_history prev
                       ON prev.property_id = cur.property_id
                      AND prev.price_status = 'Previous'
                WHERE  p.search_id      = ?
                  AND  cur.price_status = 'Current'
                  AND  cur.is_new       = 0
                  AND  cur.recorded_at >= ?
                ORDER  BY cur.recorded_at DESC
            """, (search_id, since)).fetchall()

            return {
                "new":     [dict(r) for r in new_records],
                "changed": [dict(r) for r in changed_records],
            }

    def get_scrape_history(self, search_id: int, limit: int = 10) -> List[Dict]:
        """Return recent scrape run summaries."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM scrape_runs WHERE search_id = ? ORDER BY run_date DESC LIMIT ?",
                (search_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Search management (replaces inputURLS.csv)
    # ------------------------------------------------------------------

    def close_all_connections(self):
        """Close any thread-local connection held by the calling thread."""
        conn = getattr(self._local, "connection", None)
        if conn:
            conn.close()
            self._local.connection = None

    def get_all_searches(self) -> List[Dict]:
        """Return all saved searches."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM searches ORDER BY created_at ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def add_search(self, search_name: str, url: str, emails: str = "") -> int:
        """Add a new search. Raises ValueError if search_name already exists."""
        with self._get_connection() as conn:
            try:
                cursor = conn.execute(
                    "INSERT INTO searches (search_name, url, emails) VALUES (?, ?, ?)",
                    (search_name, url, emails)
                )
                return cursor.lastrowid
            except sqlite3.IntegrityError:
                raise ValueError(f"A search named '{search_name}' already exists.")

    def update_search(self, search_id: int, search_name: str, url: str, emails: str = ""):
        """Update an existing search by its id."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE searches SET search_name = ?, url = ?, emails = ? WHERE id = ?",
                (search_name, url, emails, search_id)
            )

    def delete_search(self, search_id: int):
        """
        Delete a search and all its associated properties, price history,
        and scrape run logs.
        """
        with self._get_connection() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            row = conn.execute(
                "SELECT search_name FROM searches WHERE id = ?", (search_id,)
            ).fetchone()
            if not row:
                return
            search_name = row["search_name"]

            # Manually delete price_history via property_id (no direct FK to searches)
            conn.execute("""
                DELETE FROM price_history
                WHERE property_id IN (
                    SELECT id FROM properties WHERE search_id = ?
                )
            """, (search_id,))

            # properties and scrape_runs both have search_id now
            conn.execute("DELETE FROM properties  WHERE search_id = ?",   (search_id,))
            conn.execute("DELETE FROM scrape_runs WHERE search_id = ? OR search_name = ?",
                         (search_id, search_name))
            conn.execute("DELETE FROM searches    WHERE id = ?",          (search_id,))
            logger.info(f"Deleted search '{search_name}' and all associated data.")

    def migrate_from_csv(self, csv_path: str):
        """
        One-time migration: import searches from the legacy inputURLS.csv
        into the searches table. Skips rows that already exist.
        """
        import csv as csv_module
        if not os.path.exists(csv_path):
            return
        imported = 0
        with open(csv_path, "r", encoding="utf-8") as f:
            for row in csv_module.DictReader(f):
                url         = row.get("URL", "").strip()
                filename    = row.get("FileName", "").strip()
                emails      = row.get("Send to Emails", "").strip()
                search_name = filename.replace(".csv", "").strip()
                if url and search_name:
                    try:
                        self.add_search(search_name, url, emails)
                        imported += 1
                    except ValueError:
                        pass  # already exists – skip
        if imported:
            logger.info(f"Migrated {imported} search(es) from {csv_path} into the database.")
