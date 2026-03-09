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

    @staticmethod
    def _local_now() -> str:
        """Return the current local wall-clock time as 'YYYY-MM-DD HH:MM:SS'.
        Used everywhere instead of SQLite's CURRENT_TIMESTAMP (which is UTC)."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id      TEXT    NOT NULL,
                    search_id      INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
                    title          TEXT,
                    location       TEXT,
                    description    TEXT,
                    link           TEXT,
                    status         TEXT    NOT NULL DEFAULT 'Active',
                    first_seen     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    inactivated_at DATETIME,
                    price_per_sqm  TEXT,
                    UNIQUE (record_id, search_id)
                );

                CREATE TABLE IF NOT EXISTS search_area_stats (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    search_id        INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
                    snapshot_date    DATETIME NOT NULL,
                    avg_price_per_sqm REAL    NOT NULL,
                    sample_count     INTEGER  NOT NULL DEFAULT 0
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

        # ── Migration 4: add inactivated_at column to properties ─────────────
        prop_cols = [r[1] for r in conn.execute("PRAGMA table_info(properties)").fetchall()]
        if "inactivated_at" not in prop_cols:
            logger.info("Migrating properties: adding inactivated_at column...")
            conn.execute("ALTER TABLE properties ADD COLUMN inactivated_at DATETIME")
            logger.info("Migration 4 complete.")

        # ── Migration 5: add price_per_sqm column to properties ──────────────
        prop_cols = [r[1] for r in conn.execute("PRAGMA table_info(properties)").fetchall()]
        if "price_per_sqm" not in prop_cols:
            logger.info("Migrating properties: adding price_per_sqm column...")
            conn.execute("ALTER TABLE properties ADD COLUMN price_per_sqm TEXT")
            logger.info("Migration 5 complete.")

        # ── Migration 6: create search_area_stats table ───────────────────────
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "search_area_stats" not in tables:
            logger.info("Migrating: creating search_area_stats table...")
            conn.execute("""
                CREATE TABLE search_area_stats (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    search_id         INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
                    snapshot_date     DATETIME NOT NULL,
                    avg_price_per_sqm REAL     NOT NULL,
                    sample_count      INTEGER  NOT NULL DEFAULT 0
                )
            """)
            logger.info("Migration 6 complete.")

        # ── Migration 7: add avg_price_per_sqm + active_count to scrape_runs ─
        sr_cols = [r[1] for r in conn.execute("PRAGMA table_info(scrape_runs)").fetchall()]
        if "avg_price_per_sqm" not in sr_cols:
            logger.info("Migrating scrape_runs: adding avg_price_per_sqm column...")
            conn.execute("ALTER TABLE scrape_runs ADD COLUMN avg_price_per_sqm REAL")
            logger.info("Migration 7 (avg_price_per_sqm) complete.")
        if "active_count" not in sr_cols:
            logger.info("Migrating scrape_runs: adding active_count column...")
            conn.execute("ALTER TABLE scrape_runs ADD COLUMN active_count INTEGER")
            logger.info("Migration 7 (active_count) complete.")

        # ── Migration 8: add is_favorite column to properties ─────────────────
        prop_cols = [r[1] for r in conn.execute("PRAGMA table_info(properties)").fetchall()]
        if "is_favorite" not in prop_cols:
            logger.info("Migrating properties: adding is_favorite column...")
            conn.execute("ALTER TABLE properties ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")
            logger.info("Migration 8 (is_favorite) complete.")

        # ── Migration 9: add area_sqm, floor, yard_sqm columns to properties ─
        prop_cols = [r[1] for r in conn.execute("PRAGMA table_info(properties)").fetchall()]
        if "area_sqm" not in prop_cols:
            logger.info("Migrating properties: adding area_sqm column...")
            conn.execute("ALTER TABLE properties ADD COLUMN area_sqm TEXT")
        if "floor" not in prop_cols:
            logger.info("Migrating properties: adding floor column...")
            conn.execute("ALTER TABLE properties ADD COLUMN floor TEXT")
        if "yard_sqm" not in prop_cols:
            logger.info("Migrating properties: adding yard_sqm column...")
            conn.execute("ALTER TABLE properties ADD COLUMN yard_sqm TEXT")
        if any(c not in prop_cols for c in ("area_sqm", "floor", "yard_sqm")):
            logger.info("Migration 9 (area_sqm / floor / yard_sqm) complete.")

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
        price_per_sqm: Optional[str] = None,
        area_sqm: Optional[str] = None,
        floor: Optional[str] = None,
        yard_sqm: Optional[str] = None,
    ) -> int:
        """
        Insert a new property or update an existing one.
        Records a price_history row ONLY when the property is new or the price changed.
        Description is stored on first fetch and never overwritten (changes are rare
        and the detail page is only fetched for new listings).
        price_per_sqm is always updated when provided (e.g. "10.43 €/m²").
        area_sqm, floor, yard_sqm are stored on first fetch and never overwritten.
        Returns the property id.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = self._local_now()

            cursor.execute("""
                INSERT INTO properties (record_id, search_id, title, location, description, link, status, first_seen, last_seen, price_per_sqm, area_sqm, floor, yard_sqm)
                VALUES (?, ?, ?, ?, ?, ?, 'Active', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id, search_id) DO UPDATE SET
                    title          = excluded.title,
                    location       = excluded.location,
                    description    = COALESCE(excluded.description,  properties.description),
                    link           = excluded.link,
                    status         = 'Active',
                    last_seen      = excluded.last_seen,
                    inactivated_at = NULL,
                    price_per_sqm  = COALESCE(excluded.price_per_sqm, properties.price_per_sqm),
                    area_sqm       = COALESCE(excluded.area_sqm,      properties.area_sqm),
                    floor          = COALESCE(excluded.floor,         properties.floor),
                    yard_sqm       = COALESCE(excluded.yard_sqm,      properties.yard_sqm)
            """, (record_id, search_id, title, location, description, link, now, now,
                  price_per_sqm, area_sqm, floor, yard_sqm))

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
                    INSERT INTO price_history (property_id, price, price_status, is_new, recorded_at)
                    VALUES (?, ?, 'Current', ?, ?)
                """, (property_id, price, 1 if is_new else 0, now))

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

    def get_image_count(self, property_id: int) -> int:
        """Return the number of stored images for a property (no BLOB transfer)."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM property_images WHERE property_id = ?",
                (property_id,),
            ).fetchone()
        return row["cnt"] if row else 0

    def get_first_image(self, property_id: int) -> Optional[bytes]:
        """Return the image_data of the first image (position 0), or None."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT image_data
                FROM   property_images
                WHERE  property_id = ?
                ORDER  BY position
                LIMIT  1
                """,
                (property_id,),
            ).fetchone()
        return row["image_data"] if row else None

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
            now = self._local_now()
            if active_record_ids:
                placeholders = ",".join("?" * len(active_record_ids))
                cursor.execute(f"""
                    UPDATE properties
                    SET    status         = 'Inactive',
                           inactivated_at = ?
                    WHERE  search_id = ?
                      AND  status    = 'Active'
                      AND  record_id NOT IN ({placeholders})
                """, [now, search_id] + active_record_ids)
            else:
                cursor.execute("""
                    UPDATE properties
                    SET    status         = 'Inactive',
                           inactivated_at = ?
                    WHERE  search_id = ?
                      AND  status    = 'Active'
                """, (now, search_id))
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
        avg_price_per_sqm: Optional[float] = None,
        active_count: Optional[int] = None,
    ):
        """Persist a summary row for one scrape run."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO scrape_runs
                    (search_id, search_name, run_date, records_found, new_records, changed_prices,
                     inactive_count, success, error_message, avg_price_per_sqm, active_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (search_id, search_name, self._local_now(), records_found, new_records,
                  changed_prices, inactive_count, 1 if success else 0, error_message,
                  avg_price_per_sqm, active_count))

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

    def get_link_for_record(self, record_id: str, search_id: int) -> Optional[str]:
        """Return the listing URL for a given record_id / search_id pair, or None."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT link FROM properties WHERE record_id = ? AND search_id = ? LIMIT 1",
                (record_id, search_id)
            ).fetchone()
            return row["link"] if row else None

    def get_property_by_link(self, link: str) -> Optional[Dict]:
        """Return a single property dict looked up by its listing URL, or None."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM properties WHERE link = ? LIMIT 1",
                (link,)
            ).fetchone()
            return dict(row) if row else None

    def is_favorite(self, record_id: str, search_id: int) -> bool:
        """Return True if the property is marked as a favorite."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT is_favorite FROM properties WHERE record_id = ? AND search_id = ? LIMIT 1",
                (record_id, search_id)
            ).fetchone()
            return bool(row["is_favorite"]) if row else False

    def toggle_favorite(self, record_id: str, search_id: int) -> bool:
        """
        Flip is_favorite for the given property.
        Returns the NEW state (True = now a favorite, False = removed).
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE properties
                SET    is_favorite = CASE WHEN is_favorite = 1 THEN 0 ELSE 1 END
                WHERE  record_id = ? AND search_id = ?
                """,
                (record_id, search_id),
            )
            row = conn.execute(
                "SELECT is_favorite FROM properties WHERE record_id = ? AND search_id = ? LIMIT 1",
                (record_id, search_id)
            ).fetchone()
            return bool(row["is_favorite"]) if row else False

    def get_price_history(self, property_id: int) -> List[Dict]:
        """Return full price history for a single property, newest first."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM price_history WHERE property_id = ? ORDER BY recorded_at DESC",
                (property_id,)
            ).fetchall()
            return [dict(r) for r in rows]

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

    def get_scrape_history(self, search_id: int, limit: int = 365) -> List[Dict]:
        """Return recent scrape run summaries, newest first."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM scrape_runs WHERE search_id = ? ORDER BY run_date DESC LIMIT ?",
                (search_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_scrape_runs(self, limit: int = 200) -> List[Dict]:
        """Return the most recent scrape runs across all searches, newest first."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM scrape_runs ORDER BY run_date DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def backfill_price_per_sqm(self, record_id: str, search_id: int,
                                price_per_sqm: str) -> None:
        """
        Set price_per_sqm on an existing row ONLY when the column is currently
        NULL.  Called for unchanged listings so existing rows get backfilled on
        the next scrape run without triggering a full upsert.
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE properties
                SET    price_per_sqm = ?
                WHERE  record_id = ? AND search_id = ?
                  AND  (price_per_sqm IS NULL OR price_per_sqm = '')
                """,
                (price_per_sqm, record_id, search_id),
            )

    def record_area_stats_snapshot(self, search_id: int) -> Optional[float]:
        """
        Compute the average price_per_sqm (numeric EUR value) across all
        *Active* properties in this search that have a price_per_sqm stored,
        insert a snapshot row into search_area_stats, and return the average.
        Returns None if no numeric values are available.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """SELECT price_per_sqm FROM properties
                   WHERE search_id = ? AND status = 'Active' AND price_per_sqm IS NOT NULL""",
                (search_id,)
            ).fetchall()

        values: List[float] = []
        for row in rows:
            raw = row["price_per_sqm"]
            try:
                # stored as "10.43 €/m²" — extract the leading float
                numeric = float(raw.split()[0].replace(",", "."))
                values.append(numeric)
            except (ValueError, AttributeError, IndexError):
                pass

        if not values:
            return None

        avg = round(sum(values) / len(values), 2)
        with self._get_connection() as conn:
            conn.execute(
                """INSERT INTO search_area_stats (search_id, snapshot_date, avg_price_per_sqm, sample_count)
                   VALUES (?, ?, ?, ?)""",
                (search_id, self._local_now(), avg, len(values))
            )
        return avg

    def get_area_stats_history(self, search_id: int, limit: int = 365) -> List[Dict]:
        """Return avg price/m² snapshots for a search, oldest first (for charting)."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """SELECT snapshot_date, avg_price_per_sqm, sample_count
                   FROM search_area_stats
                   WHERE search_id = ?
                   ORDER BY snapshot_date ASC
                   LIMIT ?""",
                (search_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Backup & Restore
    # ------------------------------------------------------------------

    _BACKUP_PREFIX = "imot_scraper_"
    _BACKUP_SUFFIX = ".db"
    _GDRIVE_FOLDER_NAME = "ImotScraperBackups"

    def _backup_dir(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(self.db_path)), "backups")

    def _gdrive_creds_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(self.db_path)), "gdrive_credentials.json")

    def _gdrive_token_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(self.db_path)), "gdrive_token.json")

    def _gdrive_service(self):
        """
        Return an authenticated Google Drive service, or None if credentials
        are not configured.  Uses OAuth2 with a token cached in
        data/gdrive_token.json.  On first run (or if the token expires) a
        browser window is opened for the user to grant access.
        """
        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            SCOPES = ["https://www.googleapis.com/auth/drive.file"]
            creds_path  = self._gdrive_creds_path()
            token_path  = self._gdrive_token_path()

            if not os.path.exists(creds_path):
                logger.debug("GDrive: credentials file not found — Drive upload disabled.")
                return None

            creds = None
            if os.path.exists(token_path):
                creds = Credentials.from_authorized_user_file(token_path, SCOPES)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
                    creds = flow.run_local_server(port=0)
                with open(token_path, "w") as fh:
                    fh.write(creds.to_json())

            return build("drive", "v3", credentials=creds)
        except Exception as exc:
            logger.warning(f"GDrive: could not create service: {exc}")
            return None

    def _gdrive_get_or_create_folder(self, service) -> str | None:
        """Return the Drive folder ID for _GDRIVE_FOLDER_NAME, creating it if needed."""
        try:
            resp = service.files().list(
                q=f"name='{self._GDRIVE_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
                fields="files(id)",
            ).execute()
            files = resp.get("files", [])
            if files:
                return files[0]["id"]
            meta = {
                "name": self._GDRIVE_FOLDER_NAME,
                "mimeType": "application/vnd.google-apps.folder",
            }
            folder = service.files().create(body=meta, fields="id").execute()
            return folder["id"]
        except Exception as exc:
            logger.warning(f"GDrive: could not get/create folder: {exc}")
            return None

    def _gdrive_upload(self, backup_path: str, keep: int = 1) -> bool:
        """
        Upload *backup_path* to the ImotScraperBackups Drive folder.
        Keeps only the newest *keep* backup(s) on Drive; older ones are deleted.
        Returns True on success.
        """
        try:
            from googleapiclient.http import MediaFileUpload

            service = self._gdrive_service()
            if service is None:
                return False

            folder_id = self._gdrive_get_or_create_folder(service)
            if folder_id is None:
                return False

            filename = os.path.basename(backup_path)
            media    = MediaFileUpload(backup_path, mimetype="application/x-sqlite3", resumable=False)
            meta     = {"name": filename, "parents": [folder_id]}
            service.files().create(body=meta, media_body=media, fields="id").execute()
            logger.info(f"GDrive: uploaded {filename}")

            # Rotate — keep only newest *keep* files in the folder
            resp = service.files().list(
                q=f"'{folder_id}' in parents and trashed=false and name contains '{self._BACKUP_PREFIX}'",
                fields="files(id, name, createdTime)",
                orderBy="createdTime desc",
            ).execute()
            all_drive = resp.get("files", [])
            for old in all_drive[keep:]:
                service.files().delete(fileId=old["id"]).execute()
                logger.debug(f"GDrive: deleted old backup {old['name']}")

            return True
        except Exception as exc:
            logger.warning(f"GDrive: upload failed: {exc}")
            return False

    def gdrive_list_backups(self) -> List[Dict]:
        """
        Return list of backup dicts stored on Google Drive.
        Each dict has: name, size (int bytes or None), modified_time (str), drive_id (str).
        Returns [] if Drive is not configured or unreachable.
        """
        try:
            service = self._gdrive_service()
            if service is None:
                return []
            folder_id = self._gdrive_get_or_create_folder(service)
            if folder_id is None:
                return []
            resp = service.files().list(
                q=f"'{folder_id}' in parents and trashed=false and name contains '{self._BACKUP_PREFIX}'",
                fields="files(id, name, size, modifiedTime)",
                orderBy="createdTime desc",
            ).execute()
            results = []
            for f in resp.get("files", []):
                results.append({
                    "name":          f.get("name", ""),
                    "size":          int(f["size"]) if f.get("size") else None,
                    "modified_time": f.get("modifiedTime", ""),
                    "drive_id":      f["id"],
                    "source":        "gdrive",
                })
            return results
        except Exception as exc:
            logger.warning(f"GDrive: could not list backups: {exc}")
            return []

    def gdrive_download_backup(self, drive_id: str, dest_dir: str | None = None) -> str | None:
        """
        Download a backup from Drive by its file ID into *dest_dir*
        (defaults to the local backups folder).  Returns the local path.
        """
        try:
            import io
            from googleapiclient.http import MediaIoBaseDownload

            service = self._gdrive_service()
            if service is None:
                return None

            meta = service.files().get(fileId=drive_id, fields="name").execute()
            filename = meta["name"]
            target_dir = dest_dir or self._backup_dir()
            os.makedirs(target_dir, exist_ok=True)
            dest_path = os.path.join(target_dir, filename)

            request = service.files().get_media(fileId=drive_id)
            buf = io.FileIO(dest_path, "wb")
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            buf.close()
            logger.info(f"GDrive: downloaded {filename} to {dest_path}")
            return dest_path
        except Exception as exc:
            logger.warning(f"GDrive: download failed: {exc}")
            return None

    def backup(self, backup_dir: str = None, keep_local: int = 3,
               keep_drive: int = 1, every_n_days: int = 5) -> str:
        """
        Create a timestamped local backup using SQLite's online backup API
        (safe while the DB is open in WAL mode), then upload a copy to
        Google Drive (if credentials are configured).

        Frequency: only one backup per *every_n_days* period.  If a backup
                   already exists that is less than *every_n_days* old the
                   call returns the path of that existing backup immediately.
        Local:     keeps the newest backup per calendar day, retaining the
                   last *keep_local* such days (default 3).
        Drive:     keeps newest *keep_drive* files  (default 1).

        Returns the path of the backup file (new or existing).
        """
        target_dir = backup_dir if backup_dir else self._backup_dir()
        os.makedirs(target_dir, exist_ok=True)

        # ── Skip if a backup already exists within the last every_n_days ─────
        prefix_len = len(self._BACKUP_PREFIX)
        existing = sorted(
            f for f in os.listdir(target_dir)
            if f.startswith(self._BACKUP_PREFIX) and f.endswith(self._BACKUP_SUFFIX)
        )
        if existing:
            newest_name = existing[-1]
            date_str = newest_name[prefix_len:prefix_len + 8]   # YYYYMMDD
            try:
                newest_date = datetime.strptime(date_str, "%Y%m%d").date()
                days_since = (datetime.now().date() - newest_date).days
                if days_since < every_n_days:
                    logger.debug(
                        f"Backup skipped — last backup is {days_since} day(s) old "
                        f"(threshold: {every_n_days} days)."
                    )
                    return os.path.join(target_dir, newest_name)
            except ValueError:
                pass   # malformed filename — proceed with backup

        timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(target_dir, f"{self._BACKUP_PREFIX}{timestamp}{self._BACKUP_SUFFIX}")

        src_conn = self._get_connection()
        dst_conn = sqlite3.connect(backup_path)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()

        logger.info(f"Database backed up to: {backup_path}")

        # ── Rotate local backups: one file per day, keep last keep_local days ─
        all_local = sorted(
            f for f in os.listdir(target_dir)
            if f.startswith(self._BACKUP_PREFIX) and f.endswith(self._BACKUP_SUFFIX)
        )

        # Group by date prefix (first 8 chars after the backup prefix: YYYYMMDD)
        by_day: dict[str, list[str]] = {}
        for fname in all_local:
            day_key = fname[prefix_len:prefix_len + 8]
            by_day.setdefault(day_key, []).append(fname)

        # Within each day keep only the newest file; delete the rest
        latest_per_day: list[str] = []
        for day_key in sorted(by_day):
            day_files = sorted(by_day[day_key])
            for old_file in day_files[:-1]:
                try:
                    os.remove(os.path.join(target_dir, old_file))
                    logger.debug(f"Removed same-day duplicate backup: {old_file}")
                except OSError as exc:
                    logger.warning(f"Could not remove duplicate backup {old_file}: {exc}")
            latest_per_day.append(day_files[-1])

        # Trim to keep_local days
        for old_file in latest_per_day[:-keep_local]:
            try:
                os.remove(os.path.join(target_dir, old_file))
                logger.debug(f"Removed old local backup: {old_file}")
            except OSError as exc:
                logger.warning(f"Could not remove old backup {old_file}: {exc}")

        # Upload to Drive (best-effort — never raises)
        self._gdrive_upload(backup_path, keep=keep_drive)

        return backup_path

    def list_local_backups(self) -> List[Dict]:
        """
        Return local backup files sorted newest-first.
        Each dict has: name, path, size (bytes), modified_time (str), source='local'.
        """
        target_dir = self._backup_dir()
        if not os.path.isdir(target_dir):
            return []
        results = []
        for fname in sorted(os.listdir(target_dir), reverse=True):
            if fname.startswith(self._BACKUP_PREFIX) and fname.endswith(self._BACKUP_SUFFIX):
                full = os.path.join(target_dir, fname)
                stat = os.stat(full)
                results.append({
                    "name":          fname,
                    "path":          full,
                    "size":          stat.st_size,
                    "modified_time": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "source":        "local",
                })
        return results

    def restore_from_backup(self, backup_path: str) -> None:
        """
        Overwrite the live database with *backup_path*.
        Closes the thread-local connection first, copies the file, then
        re-opens the database so the instance is usable again.
        Raises on any error so the caller can show a message to the user.
        """
        import shutil

        if not os.path.isfile(backup_path):
            raise FileNotFoundError(f"Backup file not found: {backup_path}")

        # Close this thread's connection before overwriting
        conn = getattr(self._local, "conn", None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

        shutil.copy2(backup_path, self.db_path)
        logger.info(f"Database restored from: {backup_path}")

        # Re-initialize so the instance works normally after restore
        self._init_database()

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
