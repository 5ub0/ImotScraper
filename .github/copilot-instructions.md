# GitHub Copilot Instructions — ImotScraper

## Project overview

ImotScraper is a Windows desktop application written in **Python 3.14**.
It scrapes property listings from **imot.bg**, stores everything in a local SQLite database,
and presents the results in a Tkinter GUI with an image gallery and price history.
Distributed as a single self-contained Windows executable built with PyInstaller.

---

## Package structure

```
ImotScraper/
├── main.py                            # Entry point — wires all components together
├── controller/app_controller.py       # Central coordinator (GUI ↔ scraper ↔ DB ↔ scheduler)
├── database/db_manager.py             # All SQLite operations (DatabaseManager)
├── gui/imot_gui.py                    # Tkinter UI (ImotScraperGUI)
├── scraper/imotBgScraper.py           # Imot.bg scraper (ImotScraper)
├── scheduler/scheduler_service.py     # Daily scheduled runs (ScraperScheduler)
├── email_service_module/email_service.py  # Email — NOT active (early-return stub)
├── data/imot_scraper.db               # SQLite DB (auto-created; never commit)
├── tests/                             # Manual and automated tests
├── dist/ImotScraper.exe               # Built executable (never commit)
└── ImotScraper.spec                   # PyInstaller build spec
```

---

## Architecture — strict layer separation

```
GUI  →  Controller  →  Scraper / DB / Scheduler
```

- **GUI** (`imot_gui.py`) never imports from `scraper`, `database`, or `scheduler` directly — always via `AppController`.
- **Scraper** is fully self-contained: no GUI or email imports.
- **`AppController`** is the only component holding references to all others. `controller.db` exposes the `DatabaseManager` to the GUI.
- New features: add DB method → expose via `AppController` → call from GUI.

---

## Thread safety

- Scraping runs in a **`threading.Thread(daemon=True)`** — never on the Tkinter main thread.
- GUI updates from background threads go through **`queue.Queue` + `root.after(100, _flush)`** — see `TextHandler` in `gui/imot_gui.py`. The `_flush_scheduled` flag prevents thousands of queued `after()` callbacks during a fast scrape.
- `DatabaseManager` uses **`threading.local`** — one SQLite connection per thread. Never pass a connection between threads.
- SQLite runs in `WAL` mode with `busy_timeout = 5000 ms` — GUI and scraper threads can read/write simultaneously.
- Never call `root.update()` or touch Tkinter widgets from a background thread.

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

## GUI patterns

- **Results window** (`view_search_results`): `ttk.Treeview` with columns Status / Title / Location / Price / First Seen / Last Seen / Images / Link. `prop_id_map` maps `iid → {**prop, "current_price": price}`. Active = green, Inactive = red.
- **Gallery** (`_open_gallery(self, parent, prop_dict)`): dark nav bar (row 0) → black image area (row 1, `weight=1`) → info panel (row 2). Uses `PIL.Image` + `PIL.ImageTk`; falls back to URL text. Flat `tk.Button` nav + `←`/`→` key bindings.
- **Add/Edit Search dialog**: email fields are `state='disabled'` with a "coming soon" banner — do not remove them, they will be re-enabled with the email feature.
- **Log panel**: `TextHandler` batches records via `queue.Queue`; flushes every 100 ms. Log level `INFO` and above is visible in the GUI; use `logger.debug` for anything that should stay out of the panel.

---

## Email service

`ReportMailer.send_reports_or_failure_notification()` has an **early `return`** at the top — email is fully disabled. Env vars for re-enabling: `IMOT_SENDER_EMAIL`, `IMOT_SENDER_PASSWORD`, `IMOT_SMTP_SERVER`, `IMOT_SMTP_PORT`. Do not change the method signature — the scheduler calls it.

---

## Developer workflows

**Run in development:**
```powershell
python main.py
```

**Build the exe:**
```powershell
python -m PyInstaller ImotScraper.spec --noconfirm
# Output: dist/ImotScraper.exe (~600 MB, no console window)
```

When adding a new third-party package, add it to both `requirements.txt` **and** `hiddenimports` in `ImotScraper.spec`.

---

## New feature checklist

1. **DB change** → add method to `DatabaseManager`; add migration guard in `_migrate()`.
2. **Scraper change** → modify `ImotScraper`; keep HTTP and DB concerns separate.
3. **Business logic** → expose via a new `AppController` method.
4. **UI change** → call controller method from `ImotScraperGUI`; no direct DB/scraper imports.
5. **Background work** → `threading.Thread(daemon=True)` + `queue.Queue` + `root.after()`.
6. **Logging** → `self.logger`; pick the right level (`debug` hides from GUI, `info` shows).
7. **New dependency** → `requirements.txt` + `hiddenimports` in `ImotScraper.spec`.
8. **Rebuild exe** → `python -m PyInstaller ImotScraper.spec --noconfirm`.

---

## Hard rules

- No `gui`/`tkinter` imports in `scraper`, `database`, `scheduler`, or `email_service_module`.
- No raw SQL outside `db_manager.py` (exception: `_load_known_prices` bulk pre-load only).
- No `root.update()` or widget manipulation from non-main threads.
- No `print()` for logging — use `self.logger`.
- Do not commit `data/imot_scraper.db`, `dist/`, or `build/`.
