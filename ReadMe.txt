# ImotScraper

A Windows desktop application for scraping, monitoring, and browsing property listings from Imot.bg.
Results are stored in a local SQLite database and can be browsed directly inside the app — no spreadsheets needed.

---

## What's New (latest release)

### PyQt6 GUI — complete redesign
The user interface has been fully rebuilt using **PyQt6** with a professional dark theme:
- Dark `#1e1e1e` background with Segoe UI typography
- Live scrape results **feed** with colour-coded rows:
  - 🟢 **NEW** — brand new listing detected
  - 🟡 **CHANGED** — price updated since last run
  - 🔴 **DELETED** — listing no longer on the site
- Feed columns: **Search | Type | Title | Price** — rows persist until the next run
- Sortable results table with double-click to open the gallery
- Fully dark image gallery with keyboard navigation
- No external dependencies beyond the bundled `.exe`

### Package structure
The codebase is organised into dedicated packages:
- `scraper/`              — Imot.bg scraping logic
- `database/`             — SQLite database manager
- `gui/`                  — PyQt6 user interface (`imot_gui_qt.py`, `theme_qt.py`)
- `controller/`           — Wires GUI ↔ scraper ↔ DB
- `scheduler/`            — Background scheduling service
- `email_service_module/` — Email service (not yet active, see below)

### Scraping performance
- Persistent HTTP session reuse across all requests in a single run
- Detail pages fetched only for **new** listings — price changes reuse stored data
- WAL mode enabled on SQLite for fast concurrent reads/writes

### Data collected per listing
- **Title** and **Location** — parsed from the listing detail page
- **Description** — full text from the detail page (stored once, never overwritten)
- **Price history** — every observed price recorded with timestamp and status (Current / Previous / Older)
- **Images** — up to 10 images per listing, stored in the database

### Email notifications — coming soon
The email subscription fields are visible in the Add/Edit Search dialog but are currently **disabled**.
Email reporting will be enabled in a future release.

---

## Features

- Add and manage multiple Imot.bg search URLs
- Live feed showing NEW / CHANGED / DELETED events as the scraper runs
- Automatic background scraping on a configurable daily schedule
- "Run Scraping Now" button for on-demand runs
- New listing detection with full detail capture
- Price change detection with full price history log
- Inactive listing detection (property removed from site)
- In-app results browser with sortable columns and status colour coding
- Image gallery with price history table and description panel
- Standalone `.exe` — no Python installation required

---

## Installation

1. Download `ImotScraper.exe`
2. Place it in any folder you choose
3. Run it — a `data/` subfolder is created automatically on first launch
4. On first run Windows may show a security prompt; click **More info → Run anyway**

---

## Usage

### Adding a search

1. Click **"Add New Search"**
2. Paste a search results URL from imot.bg into the **URL** field
3. Enter a **Search Name** (e.g. "Sofia Houses")
4. Click **Save**

> **Tip:** Use the base URL without pagination (remove `/p-2`, `/p-3` etc.).
> The scraper handles all pages automatically.

### Running the scraper

- Click **"▶ Run Scraping Now"** for an immediate run, or
- Set a daily schedule time and click **"Start Daily Schedule"**

As the scraper runs, the feed panel updates in real time with colour-coded rows for every new, changed, or deleted listing. Feed rows persist after the run finishes — they are cleared only when you start the next run.

### Viewing results

1. Select a saved search from the list on the left
2. Click **"View Results"**
3. A table opens showing all collected listings with:
   - Status (Active / Inactive), colour-coded green / red
   - Title, Location, Current Price
   - First seen / Last seen dates
   - Image count
4. **Double-click** any row to open the image gallery for that listing

### Gallery navigation

| Action            | Effect                   |
|-------------------|--------------------------|
| Click ◀ Prev      | Go to previous image     |
| Click Next ▶      | Go to next image         |
| ← / → arrow keys  | Same as above            |

Below each image the panel shows Title, Location, Price, Description, and full price history.

---

## Data storage

All data is stored in a single SQLite database:

  data/
    imot_scraper.db

| Table             | Contents                                  |
|-------------------|-------------------------------------------|
| searches          | Saved search URLs and names               |
| properties        | All scraped listings                      |
| price_history     | Full price timeline per listing           |
| property_images   | Image BLOBs (up to 10 per listing)        |

No CSV files are written. The database file can be backed up by simply copying it.

---

## System requirements

- Windows 10 / 11 (64-bit)
- Internet connection
- ~500 MB free disk space (images accumulate over time)
- No Python installation required

---

## Error handling

- Network timeouts and failed requests are retried automatically (3-retry adapter)
- 404 / missing images are silently skipped
- All scraping runs in a background thread — the UI stays fully responsive
- The application shuts down cleanly when the window is closed
