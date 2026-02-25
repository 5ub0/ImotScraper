"""
Main entry point for ImotScraper application.
Initializes all components and coordinates their execution.
"""

import logging
import sys
import os
import threading
import traceback

# Catch exceptions from background threads and print them visibly
def _thread_excepthook(args):
    print(f"\n[THREAD CRASH] Thread: {args.thread.name}", file=sys.stderr)
    traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)

threading.excepthook = _thread_excepthook

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Add parent directory to path to allow imports from submodules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from controller.app_controller import AppController
from scraper.imotBgScraper import ImotScraper
from email_service_module.email_service import ReportMailer
from scheduler.scheduler_service import ScraperScheduler
from gui.imot_gui import ImotScraperGUI
import tkinter as tk


def main():
    """
    Initialize all application components and start the GUI.
    """
    try:
        # Initialize core components
        scraper = ImotScraper(data_dir='data')

        # One-time migration: import searches from inputURLS.csv if DB is empty
        csv_path = os.path.join('data', 'inputURLS.csv')
        if not scraper.db.get_all_searches() and os.path.exists(csv_path):
            migrated = scraper.db.migrate_from_csv(csv_path)
            logging.info(f"Migrated {migrated} search(es) from inputURLS.csv into the database.")
        email_service = ReportMailer()
        scheduler = ScraperScheduler(
            report_mailer=email_service,
            scraper_function=scraper.execute
        )
        
        # Create controller to coordinate components
        controller = AppController(
            gui=None,
            scraper=scraper,
            email_service=email_service,
            scheduler=scheduler
        )
        
        # Initialize and run GUI
        root = tk.Tk()
        gui = ImotScraperGUI(root, controller=controller)
        controller.gui = gui
        
        logging.info("ImotScraper application started")
        root.mainloop()
        
    except Exception as e:
        logging.error(f"Failed to start application: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
