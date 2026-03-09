# GitHub Copilot Instructions — ImotScraper

## Project overview

ImotScraper is a Windows desktop application written in **Python 3.14**.
It scrapes property listings from **imot.bg**, stores everything in a local SQLite database,
and presents the results in a **PyQt6** GUI with a dark theme, a live scrape feed, an image gallery, price history, and analytics charts.
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

### Schema summary

| Table               | Key columns / purpose |
|---------------------|-----------------------|
| `searches`          | `id`, `search_name`, `url`, `emails` |
| `properties`        | `record_id`, `search_id`, `title`, `location`, `description`, `link`, `status`, `first_seen`, `last_seen`, `inactivated_at`, `price_per_sqm`, `area_sqm`, `floor`, `yard_sqm` |
| `price_history`     | `property_id`, `price`, `price_status` (Current/Previous/Older), `is_new`, `recorded_at` |
| `property_images`   | `property_id`, `url`, `image_data` BLOB, `position` |
| `search_area_stats` | Legacy daily avg €/m² snapshots — still written but charts now read from `scrape_runs` |
| `scrape_runs`       | `search_id`, `run_date`, `records_found`, `new_records`, `changed_prices`, `inactive_count`, `avg_price_per_sqm` REAL, `active_count` INTEGER, `success`, `error_message` |

---

## Scraper internals

- `execute()` creates one **persistent `requests.Session`** (3-retry adapter) for the entire run.
- Decision tree per listing:
  - **New** (`existing_price is None`) → fetch detail page, extract title/location/description/images/area/floor/yard, set `is_new=True`.
  - **Price changed** → reuse stored title/location, pass `description=None` (COALESCE keeps existing), skip images.
  - **Unchanged** → `continue` — no DB write at all.
- `_load_known_prices(search_id)` bulk-loads `(price, title, location)` keyed by `record_id` before the pagination loop — avoids per-listing DB round-trips.
- `_extract_title_and_location()` returns an 8-tuple: `(title, location, description, image_urls, price_per_sqm, area_sqm, floor, yard_sqm)`. Area/floor/yard parsed from `div.adParams` Bulgarian labels (Площ / Етаж / Двор).
- Image extraction: `soup.find_all("img", class_="carouselimg")` → `img["data-src"]`. Carousel clones are deduplicated with a `seen` set. Cap: **5 images per listing**.
- Pagination: appends `/p-{n}` before `?` in the URL; stops when no `<a class="saveSlink next">` is found.
- After each search, unseen listings are marked `Inactive` via `db.mark_inactive()`.
- After `mark_inactive`, a per-listing loop emits one `"Removed listing: …"` log line for each inactivated record (uses the pre-loaded `known` dict + `db.get_link_for_record()`).
- After each search: `record_area_stats_snapshot()` is called (legacy), then all active properties are iterated to compute `avg_price_per_sqm` and `active_count`, which are passed to `log_scrape_run()`.
- Delays: `REQUEST_DELAY = 1 s` between pages, `DETAIL_DELAY = 0.3 s` between detail fetches.
- Log line formats (parsed by `ResultsFeedHandler` in the GUI):
  - `"New listing: {title} | price: {price} | search: {search_name} | {link}"`
  - `"Price change: {title} | old: {old_price} | new: {new_price} | search: {search_name} | {link}"`
  - `"Removed listing: {title} | search: {search_name} | {link}"`

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
| Area / Floor / Yard | `div.adParams` → `strong` labels: Площ → `area_sqm`, Етаж → `floor`, Двор → `yard_sqm` |

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
- 11 columns: **Thumb** (0, 78 px, icon) | **Status** (1) | **Title** (2) | **Location** (3) | **Price** (4) | **€/m²** (5) | **First Seen** (6) | **Deactivated At** (7) | **Days on Market** (8) | **Images** (9) | **Link** (10, stretch).
- Column 0 shows a **thumbnail** (first image scaled to `THUMB_W×THUMB_H`) via `QTableWidgetItem.setIcon()`; `setIconSize(QSize(70, 52))` on the table. Row height: `T.THUMB_H` (56 px).
- `db.get_image_count()` used for the "🖼 N" label (no BLOB transfer); `db.get_first_image()` loads only the first image's bytes for the thumbnail.
- Prop dict stored in `item.setData(Qt.ItemDataRole.UserRole, enriched)` on col 0 (thumbnail cell); read back in `_on_double_click`.
- Active rows: `BG2` background, `FG_WHITE` foreground, normal font. Inactive: `BG` background, `FG_DIM` foreground, italic.
- Underpriced rows (active; `price_per_sqm` > 10 % below area avg): `FEED_UNDERPRICED_BG` teal tint.
- Single-click col 4 (Price) → `MortgageCalculatorDialog`; single-click col 10 (Link) → browser.
- Two chart buttons in the summary bar: **📊 Area Avg Chart** → `AreaAvgChartDialog`; **📈 Active Listings History** → `ListingsFoundChartDialog`.

### Chart dialogs (`AreaAvgChartDialog`, `ListingsFoundChartDialog`)
Both use `matplotlib` with `backend_qtagg`, lazy-imported inside `__init__`.

**Shared module-level helpers:**
- `_bin_series(dates, values, bin_by="auto")` — bins a time series: ≤90 points → day, ≤365-day span → week, longer → month.
- `_rolling_avg(values, win)` — centred rolling mean.

**`AreaAvgChartDialog`:**
- Data source: `scrape_runs.avg_price_per_sqm` via `controller.get_scrape_history()` (rows with `None` filtered out).
- Line + markers for binned avg; dashed green rolling trend; green `axhspan` ±10 % band around latest avg.
- Active property scatter dots at `first_seen` date; orange if >10 % below latest avg; `pick_event` → `GalleryWindow.exec()`.
- Explicit axis padding: 5 % x-span each side, 15 % y-margin above/below combined data extent.

**`ListingsFoundChartDialog`** (button: "📈 Active Listings History"):
- Data source: `scrape_runs.active_count`; falls back to `records_found` for old rows without `active_count`.
- Line + markers; dashed green rolling trend (only if ≥3 points). No `fill_between`.
- Explicit axis padding: ±1 day for a single point, 5 % span otherwise; 15 % y-margin.

### GalleryWindow (`QDialog`)
- `QLabel` + `QPixmap` image display with `Qt.AspectRatioMode.KeepAspectRatio` scaling.
- `resizeEvent` rescales the current image to fill the available area.
- Keyboard `←`/`→` bindings for navigation.
- Info panel shows Area, Floor, Yard (conditional — only if data present) between price and price history.
- Price label is clickable (blue underline, 🏦 icon) — opens `MortgageCalculatorDialog`.

### MortgageCalculatorDialog (`QDialog`)
- Parses price from text like `"85 000 EUR | Без ДДС"`.
- Detects "Без ДДС" (without VAT) → applies ×1.20, shows orange warning with effective price.
- Three horizontal sliders: interest rate (0.10–15.00 %, step 0.05 %), loan term (1–30 yr), bank finances (10–90 %, step 5 %).
- Outputs: down payment, transfer taxes (3 % fixed), total initial payment (orange), loan amount, monthly payment (green, bold).
- Standard annuity formula: `rate_annual = slider.value() / 10_000`.

### SearchDialog (`QDialog`)
- Email fields are present but `setEnabled(False)` — do not remove them.

### Log panel
- `ResultsFeedHandler` intercepts `logging.INFO` lines that match the scraper feed patterns.
- Regexes: `_RE_NEW` (4 groups), `_RE_CHANGED` (5 groups, group 5 = link), `_RE_DELETED` (3 groups, group 3 = link, uses `\S*` not `\S+` to tolerate empty link).
- `_RE_DELETED` emits `kind="DEACTIVATED"` — the feed colour map key is `"DEACTIVATED": T.FEED_DELETED_BG`.
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
# Exit code 1 is expected — caused by the admin deprecation warning, not a real error.
# Confirm success with: "Build complete!" in the last lines of output.
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

