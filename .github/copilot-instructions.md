# GitHub Copilot Instructions — ImotScraper

## Project overview

ImotScraper is a Windows desktop application written in **Python 3.14**.
It scrapes property listings from **imot.bg**, stores everything in a local SQLite database,
and presents the results in a **PyQt6** GUI with a dark theme, a live scrape feed, an image gallery, and price history.
Distributed as a single self-contained Windows executable built with PyInstaller.

---

## Package structure

```
ImotScraper/
├── main.py                                # Entry point — wires all components together
├── controller/app_controller.py           # Central coordinator (GUI ↔ scraper ↔ DB ↔ scheduler)
├── database/db_manager.py                 # All SQLite operations (DatabaseManager)
├── gui/imot_gui_qt.py                     # PyQt6 UI (ImotScraperMainWindow) — active
├── gui/theme_qt.py                        # AppTheme design tokens + build_stylesheet() QSS
├── gui/imot_gui.py                        # Legacy Tkinter UI — kept for reference, not used
├── gui/theme.py                           # Legacy Tkinter theme — kept for reference, not used
├── scraper/imotBgScraper.py               # Imot.bg scraper (ImotScraper)
├── scheduler/scheduler_service.py         # Daily scheduled runs (ScraperScheduler)
├── email_service_module/email_service.py  # Email — NOT active (early-return stub)
├── data/imot_scraper.db                   # SQLite DB (auto-created; never commit)
├── tests/                                 # Manual and automated tests
├── dist/ImotScraper.exe                   # Built executable (never commit)
└── ImotScraper.spec                       # PyInstaller build spec
```

---

## Architecture — strict layer separation

```
GUI  →  Controller  →  Scraper / DB / Scheduler
```

- **GUI** (`imot_gui_qt.py`) never imports from `scraper`, `database`, or `scheduler` directly — always via `AppController`.
- **Scraper** is fully self-contained: no GUI or PyQt6 imports.
- **`AppController`** is the only component holding references to all others. `controller.db` exposes the `DatabaseManager` to the GUI.
- New features: add DB method → expose via `AppController` → call from GUI.

---

## Thread safety

- Scraping runs in a **`threading.Thread(daemon=True)`** — never on the Qt main thread.
- GUI updates from background threads use **`pyqtSignal`** — `FeedBridge.event_received` carries feed row dicts cross-thread; `_scrape_finished` carries the success bool. Qt's signal/slot mechanism handles the thread hop automatically.
- `DatabaseManager` uses **`threading.local`** — one SQLite connection per thread. Never pass a connection between threads.
- SQLite runs in `WAL` mode with `busy_timeout = 5000 ms` — GUI and scraper threads can read/write simultaneously.
- Never call Qt widget methods directly from a background thread — always emit a signal.

---

## Database conventions

- **All SQL lives in `db_manager.py`**. The one exception is `_load_known_prices()` in the scraper, which calls `self.db._get_connection()` directly for a bulk pre-load — do not expand this pattern.
- Upserts use `INSERT … ON CONFLICT … DO UPDATE` (not `INSERT OR REPLACE`) to preserve `first_seen` and existing `description`.
- `description` is stored on first fetch and **never overwritten**: `COALESCE(excluded.description, properties.description)` in `upsert_property`.
- Price history uses a rolling status cascade on every new price write:
  `Current → Previous → Older` (see `upsert_property` in `db_manager.py`).
- Schema migrations live in `_migrate()`, guarded by `PRAGMA table_info` column-presence checks so existing databases upgrade automatically on launch.
- Foreign keys: `PRAGMA foreign_keys = ON`. New tables must declare `FOREIGN KEY` constraints.
- DB file: `data/imot_scraper.db` — in `.gitignore`, never commit.

---

## Scraper internals

- `execute()` creates one **persistent `requests.Session`** (3-retry adapter) for the entire run.
- Decision tree per listing:
  - **New** (`existing_price is None`) → fetch detail page, extract title/location/description/images, set `is_new=True`.
  - **Price changed** → reuse stored title/location, pass `description=None` (COALESCE keeps existing), skip images.
  - **Unchanged** → `continue` — no DB write at all.
- `_load_known_prices(search_id)` bulk-loads `(price, title, location)` keyed by `record_id` before the pagination loop — avoids per-listing DB round-trips.
- Image extraction: `soup.find_all("img", class_="carouselimg")` → `img["data-src"]`. Carousel clones are deduplicated with a `seen` set. Cap: **10 images per listing**.
- Pagination: appends `/p-{n}` before `?` in the URL; stops when no `<a class="saveSlink next">` is found.
- After each search, unseen listings are marked `Inactive` via `db.mark_inactive()`.
- Delays: `REQUEST_DELAY = 1 s` between pages, `DETAIL_DELAY = 0.3 s` between detail fetches.
- Log line formats (parsed by `ResultsFeedHandler` in the GUI):
  - `"New listing: {title} | price: {price} | search: {search_name} | {link}"`
  - `"Price change: {title} {old} → {new} | search: {search_name}"`

---

## HTML selectors (imot.bg)

| Data | Selector |
|---|---|
| Listing cards | `div[class^="item"]` (skip if second class is `"fakti"`) |
| Listing title | `a.title.saveSlink` → `get_text(separator=' ', strip=True)` |
| Price | `div[class^="price"]` → `div` child → first `\n`-split token |
| Record ID | `listing["id"][3:]` (strips `"adv"` prefix) |
| Detail title | `div.advHeader > div.title` (decompose inner `div.btns` first) |
| Detail location | `div.advHeader > div.location` descendants text nodes |
| Description | `div.moreInfo > div.text` → `get_text(separator='\n', strip=True)` |
| Images | `img.carouselimg[data-src]` (full-size CDN URL) |

---

## GUI patterns (PyQt6)

### Theme
- All design tokens live in `gui/theme_qt.py` → `AppTheme` dataclass.
- `build_stylesheet()` returns a QSS string applied at `QApplication` level.
- Key colours: `BG="#1e1e1e"`, `BG2="#2b2b2b"`, `ACCENT="#0d7aff"`, feed colours `FEED_NEW_BG`, `FEED_CHANGED_BG`, `FEED_DELETED_BG`.

### Main window (`ImotScraperMainWindow`)
- `self._search_ids: dict[str, int]` — search name → DB id (NOT `QListWidgetItem` objects).
- `self._feed_bridge: FeedBridge` — `QObject` with `event_received = pyqtSignal(dict)` for thread-safe feed updates.
- `self._feed_handler: ResultsFeedHandler` — `logging.Handler` that parses scraper log lines and emits structured dicts via the bridge.
- Feed table has 4 columns: **Search** (160 px fixed) | **Type** (110 px fixed) | **Title** (stretch) | **Price** (220 px fixed).
- Feed rows persist after the scrape finishes — cleared only when **Run** is pressed again.

### Custom delegates
- **`_FeedDelegate(QStyledItemDelegate)`** — full custom `paint()`: reads `BackgroundRole`/`ForegroundRole`/`FontRole` directly from item data, strips `State_HasFocus`. This bypasses QSS `background-color` on `::item` which would override `setBackground()`.
- **`_ListDelegate(QStyledItemDelegate)`** — strips `State_HasFocus` then delegates to `super().paint()`. Applied to `self._search_list` to eliminate the rounded-button focus artefact.
- Both delegates + `setFocusPolicy(Qt.FocusPolicy.NoFocus)` are required together to fully suppress focus rect rendering.

### ResultsWindow (`QDialog`)
- `setSortingEnabled(False)` during `_populate()` — re-enable after all rows inserted.
- Prop dict stored in `item.setData(Qt.ItemDataRole.UserRole, enriched)` on col 0; read back in `_on_double_click` via `status_item.data(Qt.ItemDataRole.UserRole)`.
- Active rows: `BG2` background, `FG_WHITE` foreground, normal font. Inactive: `BG` background, `FG_DIM` foreground, italic.

### GalleryWindow (`QDialog`)
- `QLabel` + `QPixmap` image display with `Qt.AspectRatioMode.KeepAspectRatio` scaling.
- `resizeEvent` rescales the current image to fill the available area.
- Keyboard `←`/`→` bindings for navigation.

### SearchDialog (`QDialog`)
- Email fields are present but `setEnabled(False)` — do not remove them.

### Log panel
- `ResultsFeedHandler` intercepts `logging.INFO` lines that match the scraper feed patterns.
- All other log output goes to a `QTextEdit` log panel via a standard `logging.Handler`.
- Use `logger.debug` for anything that should not appear in the GUI log panel.

---

## Email service

`ReportMailer.send_reports_or_failure_notification()` has an **early `return`** at the top — email is fully disabled. Env vars for re-enabling: `IMOT_SENDER_EMAIL`, `IMOT_SENDER_PASSWORD`, `IMOT_SMTP_SERVER`, `IMOT_SMTP_PORT`. Do not change the method signature — the scheduler calls it.

---

## Developer workflows

**Run in development:**
```powershell
.venv\Scripts\python.exe main.py
```

**Build the exe:**
```powershell
.venv\Scripts\python.exe -m PyInstaller ImotScraper.spec --noconfirm
# Output: dist/ImotScraper.exe (~46 MB, no console window)
```

When adding a new third-party package, add it to both `requirements.txt` **and** `hiddenimports` in `ImotScraper.spec`.

---

## New feature checklist

1. **DB change** → add method to `DatabaseManager`; add migration guard in `_migrate()`.
2. **Scraper change** → modify `ImotScraper`; keep HTTP and DB concerns separate.
3. **Business logic** → expose via a new `AppController` method.
4. **UI change** → call controller method from `ImotScraperMainWindow`; no direct DB/scraper imports.
5. **Background work** → `threading.Thread(daemon=True)` + `pyqtSignal` for the result callback.
6. **Feed event** → add log line in scraper matching `ResultsFeedHandler` regex patterns; update regexes if format changes.
7. **Logging** → `self.logger`; pick the right level (`debug` hides from GUI, `info` shows).
8. **New dependency** → `requirements.txt` + `hiddenimports` in `ImotScraper.spec`.
9. **Rebuild exe** → `.venv\Scripts\python.exe -m PyInstaller ImotScraper.spec --noconfirm`.

---

## Hard rules

- No `gui`/`PyQt6` imports in `scraper`, `database`, `scheduler`, or `email_service_module`.
- No raw SQL outside `db_manager.py` (exception: `_load_known_prices` bulk pre-load only).
- Never call Qt widget methods from a background thread — use signals.
- No `print()` for logging — use `self.logger`.
- Do not commit `data/imot_scraper.db`, `dist/`, or `build/`.
