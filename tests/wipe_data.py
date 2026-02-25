"""Wipe scraped data tables, preserving searches configuration."""
import sqlite3

conn = sqlite3.connect('data/imot_scraper.db')
conn.execute("PRAGMA foreign_keys = OFF")
conn.execute("DELETE FROM price_history")
conn.execute("DELETE FROM properties")
conn.execute("DELETE FROM scrape_runs")
conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('price_history','properties','scrape_runs')")
conn.execute("PRAGMA foreign_keys = ON")
conn.commit()

print("Done. Rows remaining:")
print("  searches:     ", conn.execute("SELECT COUNT(*) FROM searches").fetchone()[0])
print("  properties:   ", conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0])
print("  price_history:", conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0])
print("  scrape_runs:  ", conn.execute("SELECT COUNT(*) FROM scrape_runs").fetchone()[0])
conn.close()
