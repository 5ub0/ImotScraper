# ImotScraper

A Windows desktop application for scraping, monitoring, and browsing property listings from Imot.bg.
Results are stored in a local SQLite database and can be browsed directly inside the app — no spreadsheets needed.

---

## What's New (latest release)

### Project restructure
The codebase has been reorganised into dedicated packages:
- `scraper/`            — Imot.bg scraping logic
- `database/`           — SQLite database manager
- `gui/`                — Tkinter user interface
- `controller/`         — Wires GUI ↔ scraper ↔ DB
- `scheduler/`          — Background scheduling service
- `email_service_module/` — Email service (not yet active, see below)

### Improved scraping speed
- Persistent HTTP session reuse across all requests in a single scraping run
- Detail pages are only fetched for **new** listings (not re-fetched on price changes)
- WAL mode enabled on SQLite for faster concurrent reads/writes

### Additional data collected
Each listing now stores:
- **Description** — full text from the detail page
- **Location** — parsed from the listing header
- **Price history** — every price observed is recorded with a timestamp and status (Current / Historical)

### Image collection
- Up to **10 images** per listing are downloaded and stored as BLOBs in the database
- Images are only downloaded once, for **new** listings
- No external image folder needed — everything lives in `data/imot_scraper.db`

### Image gallery preview
- Each row in the results window shows an image count (🖼 N)
- Click the count **or** double-click any row to open the gallery viewer
- Gallery features:
  - Full-size images rendered with Pillow
  - ◀ Previous / Next ▶ navigation buttons + keyboard arrow keys
  - Property **Title**, **Location**, **Price**, and **Description** shown below the image

### Visual status indicators
- **Active** listings are shown in green
- **Inactive** (removed/expired) listings are shown in red

### Email notifications — coming soon
The email subscription fields are visible in the Add/Edit Search dialog but are currently **disabled**.
Email reporting will be enabled in a future release.

---

## Features

- Add and manage multiple Imot.bg search URLs
- Automatic background scraping on a configurable schedule
- "Run Scraping Now" button for on-demand runs
- New listing detection with full detail capture
- Price change detection with full history log
- Inactive listing detection (property removed from site)
- In-app results browser with sortable columns
- Image gallery with property details panel
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
4. Click **Save Search**

> **Tip:** Use the base URL without pagination (remove `/p-2`, `/p-3` etc.).
> The scraper handles all pages automatically.

### Running the scraper

- Click **"Run Scraping Now"** for an immediate run, or
- Configure the schedule and let it run automatically in the background

Progress and detected changes are shown in the log panel in real time.

### Viewing results

1. Click **"View Results"** next to any saved search
2. A table opens showing all collected listings with:
   - Status (Active / Inactive)
   - Title, Location, Current Price
   - First seen / Last seen dates
   - Image count
3. Click the 🖼 image count or **double-click** any row to open the image gallery

### Gallery navigation

| Action           | Effect                  |
|------------------|-------------------------|
| Click ◀ Previous | Go to previous image    |
| Click Next ▶     | Go to next image        |
| ← / → arrow keys | Same as above           |

Below each image the panel shows Title, Location, Price, and Description.

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
- ~1 GB free disk space (images accumulate over time)
- No Python installation required

---

## Error handling

- Network timeouts and failed requests are retried automatically
- 404 / missing images are silently skipped (normal for expired listings)
- Background scraping runs in a separate thread — the UI stays responsive at all times
- The application shuts down cleanly when the window is closed