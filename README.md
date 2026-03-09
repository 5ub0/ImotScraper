# ImotScraper

A Windows desktop application for scraping, monitoring, and browsing property listings from **imot.bg**.  
Results are stored in a local SQLite database and browsed entirely inside the app — no spreadsheets needed.

---

## Features

### Core scraping
- Add and manage multiple imot.bg search URLs
- Full pagination — all pages of a search are scraped automatically
- **New listing detection** — title, location, description, and up to 5 images captured on first sight
- **Price change detection** — full price history recorded with timestamp and status (Current / Previous / Older)
- **Inactive listing detection** — properties no longer on the site are marked automatically
- Persistent HTTP session with 3-retry adapter per run; detail pages fetched only for new listings

### Live feed
- Real-time feed panel updates as the scraper runs
- 🟢 **NEW** — brand new listing detected
- 🟡 **CHANGED** — price updated since last run
- 🔴 **DELETED** — listing removed from site
- Feed columns: **Search | Type | Title | Price** — rows persist until the next run starts

### Results browser
- Sortable table with **thumbnail preview** (first image), Status, Title, Location, Price, First Seen, Last Seen, Image Count
- Active listings shown in white; inactive in dimmed italic
- **Underpriced** listings (>10 % below the area average €/m²) highlighted with a teal tint
- Double-click any row to open the full image gallery for that listing
- Single-click on the **Price** column to open the **mortgage calculator**

### Mortgage calculator
- Opens from any price cell in the results table, or from the price label in the gallery
- Automatically parses the listing price; detects **"Без ДДС"** (no VAT) and applies ×1.20
- Three sliders: **interest rate** (0.10–15.00 %), **loan term** (1–30 years), **bank finances** (10–90 %)
- Shows: down payment, 3 % transfer taxes, **total initial payment**, loan amount, **monthly payment**

### Property details
- **Floor**, **area (m²)**, and **yard (m²)** extracted from detail pages (Bulgarian labels: Площ / Етаж / Двор)
- Shown in the gallery info panel alongside price and location

### Analytics charts
Two charts available inside the Results window per search:

**📊 Area Avg Chart**
- Line graph of average €/m² across all active listings, recorded per scrape run
- Rolling trend line overlaid in dashed green
- Green ±10 % band around the latest average for quick reference
- Individual listing scatter dots at their `first_seen` date — orange if >10 % below average
- Click any dot to open that listing's gallery
- Long histories binned automatically (daily → weekly → monthly)

**📈 Active Listings History**
- Line graph of the active listing count over time
- Rolling trend line (3+ points)
- Axis ranges padded around actual data

### Price per m² tracking
- `price_per_sqm` extracted and stored for every listing that includes a floor-area figure
- Area average snapshot saved after every scrape run

### Backup & restore
- **Automatic backup** created after every successful scrape run
- Up to **3 local backups** kept in `data/backups/` (oldest rotated out automatically)
- **⏪ Restore DB** button in the status bar opens the Restore dialog
- Restore dialog lists all available local backups (Source, File Name, Size, Date)
- Restoring takes a safety backup of the current DB first, then restores and restarts the app
- The `data/backups/` folder can be synced to Google Drive (or any cloud) using the desktop sync app of your choice — no extra configuration needed

### Scheduling
- Set a daily schedule time and click **"Start Daily Schedule"** for fully automatic runs
- Runs in a background thread — the UI stays fully responsive throughout

### Run history
- **📋 Run History** button in the status bar shows a per-run summary table:
  - Date, listings found, new, price changes, inactive, avg €/m², active count

---

## Installation

1. Download `ImotScraper.exe`
2. Place it in any folder you choose
3. Run it — a `data/` subfolder is created automatically on first launch

> **Note:** On first launch Windows SmartScreen may show a warning.  
> Click **"More info → Run anyway"** to proceed.

---

## Usage

### Adding a search

1. Click **"Add New Search"**
2. Paste a search results URL from imot.bg into the **URL** field
3. Enter a **Search Name** (e.g. `Sofia Houses`)
4. Click **Save**

> **Tip:** Use the base URL without a page suffix — remove `/p-2`, `/p-3`, etc.  
> The scraper handles all pages automatically.

### Running the scraper

- Click **"▶ Run Scraping Now"** for an immediate on-demand run, or
- Set a time in the scheduler panel and click **"▶ Start Daily Schedule"** for automatic daily runs

### Viewing results

1. Select a saved search from the list on the left
2. Click **"View Results"**
3. A table opens with all collected listings — sort by any column header
4. Double-click any row to open the **image gallery** for that listing

### Gallery navigation

| Action           | Effect                  |
|------------------|-------------------------|
| Click ◀ Prev     | Go to previous image    |
| Click Next ▶     | Go to next image        |
| ← / → arrow keys | Same as above           |

Below each image: Title, Location, Price, Description, and full price history table.

### Backup & restore

Backups are created automatically after every successful scrape. To manually restore:

1. Click **"⏪ Restore DB"** in the bottom status bar
2. Select a backup from the list
3. Click **"⏪ Restore Selected"**
4. Confirm — the app takes a safety backup, restores, and restarts automatically

To sync backups to the cloud, point your Google Drive (or OneDrive / Dropbox) desktop app at the `data\backups\` folder next to the exe.

---

## Data storage

All data lives in a single SQLite database next to the exe:

```
data/
  imot_scraper.db
  backups/
    imot_scraper_YYYYMMDD_HHMMSS.db   ← up to 3 kept
```

| Table              | Contents                                                              |
|--------------------|-----------------------------------------------------------------------|
| `searches`         | Saved search URLs and names                                           |
| `properties`       | All scraped listings (title, location, description, price_per_sqm, area_sqm, floor, yard_sqm, …) |
| `price_history`    | Full price timeline per listing (Current / Previous / Older)          |
| `property_images`  | Image BLOBs — up to 5 per listing                                     |
| `search_area_stats`| Daily avg €/m² snapshots per search (legacy)                         |
| `scrape_runs`      | Per-run summary: found / new / changed / inactive / avg €/m² / active count |

No CSV files are written. The database can be backed up by copying it.

---

## System requirements

- Windows 10 / 11 (64-bit)
- Internet connection
- ~500 MB free disk space (images accumulate over time)
- No Python installation required

---

## Error handling

- Network timeouts and connection failures are retried automatically (3-retry adapter)
- Missing or failed images are silently skipped
- All scraping runs in a background thread — the UI never freezes
- The application shuts down cleanly when the window is closed

---

## Email notifications — coming soon

The email subscription fields are visible in the Add / Edit Search dialog but are currently **disabled**.  
Email reporting will be enabled in a future release.
