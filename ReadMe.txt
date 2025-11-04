# ImotScraper

A GUI application for scraping and monitoring property listings from Imot.bg, featuring a clean interface and proper resource management.

## Features

- User-friendly graphical interface
- Real-time URL management
- Automated price change detection
- CSV file viewing capability
- Clickable links in the log output
- Adjustable panel layout
- Clean application shutdown
- Proper thread management
- Background process handling
- Automated data directory management

## Installation

1. Download the ImotScraper.exe file
2. Create a folder where you want to run the application
3. Run ImotScraper.exe - it will automatically create a `data` folder for storing files

## Usage Guide

### Adding New URLs to Monitor

1. Launch ImotScraper.exe
2. In the "Add New URL" section at the top:
   - Enter the URL from imot.bg in the "URL" field
   - Enter a filename (with or without .csv extension) in the "Filename" field
   - Click "Add URL" to add it to the list

### URL Requirements

- URLs should be copied from the Imot.bg search results page
- Supports both single and multiple region searches
- Example URL formats:
  - Basic search: https://www.imot.bg/obiavi/prodazhbi/grad-sofiya/boyana/kashta?raioni=16~69~104~110~
  - With pagination: https://www.imot.bg/obiavi/prodazhbi/grad-sofiya/boyana/kashta/p-2?raioni=16~69~104~110~

Note: When copying URLs from paginated results (p-2, p-3, etc.), make sure to use the base URL without pagination. The application will handle all pages automatically. The pagination marker (p-1, p-2) appears before the question mark in the URL.

### Starting the Scraper

1. After adding all desired URLs, click "Start Scraping"
2. The log output will show progress and any changes detected
3. Clickable links in the log allow direct access to property listings

### File Management

All files are stored in the `data` directory:
- inputURLS.csv: Stores your monitored URLs
- [your_filename].csv: Main output files for each URL
- NewRecords_[filename].csv: Created when changes are detected

### Viewing Results

- Use the "View CSV Files" buttons to open any CSV file in a table view
- The log window shows:
  - New properties added
  - Price changes detected
  - Missing/removed properties

### Tips

1. The interface can be resized, and panels can be adjusted using the divider
2. You can remove URLs from monitoring by selecting them and clicking "Remove Selected"
3. The application automatically saves your URL list
4. New records files are automatically created when changes are detected and removed when no changes exist

## Error Handling and Application Behavior

- Clean application shutdown when closing the window
- Proper termination of background scraping processes
- Automatic cleanup of resources on exit
- Graceful handling of network timeouts and retries
- Safe CSV file handling with proper locking mechanisms
- If a file is not found, an error message will be displayed
- Network errors will be shown in the log output
- The application will retry failed requests automatically

### System Requirements and Notes

- Windows operating system
- Internet connection for scraping
- No additional installation required - standalone executable
- Automatic data directory creation and management
- Thread-safe operations for reliable data collection

Note: On first run, Windows might show a security warning since it's an unrecognized application. Click "More info" and then "Run anyway" to proceed.