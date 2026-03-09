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
from gui.imot_gui_qt import ImotScraperMainWindow, build_stylesheet
from PyQt6.QtWidgets import QApplication


def main():
    """
    Initialize all application components and start the GUI.
    """
    try:
        # Resolve data directory relative to the exe (or script) so the DB is
        # always written next to the executable, not in a temp / CWD folder.
        if getattr(sys, 'frozen', False):
            # Running as a PyInstaller exe — place data/ beside the .exe
            base_dir = os.path.dirname(sys.executable)
        else:
            # Running from source — use the project root
            base_dir = os.path.dirname(os.path.abspath(__file__))

        data_dir = os.path.join(base_dir, 'data')

        # Initialize core components
        scraper = ImotScraper(data_dir=data_dir)

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

        # Initialize and run Qt GUI
        app = QApplication(sys.argv)
        app.setStyleSheet(build_stylesheet())
        win = ImotScraperMainWindow(controller=controller)
        controller.gui = win

        # Wrap the scheduler's scraper_function so that it notifies the GUI
        # (clear feed, reset status label) before and after each scheduled run.
        _original_execute = scheduler.scraper_function

        def _gui_aware_execute() -> bool:
            win._scrape_starting.emit()           # clear feed (cross-thread safe)
            try:
                success = _original_execute()
            except Exception as exc:
                logging.error(f"Critical error during scheduled run: {exc}")
                success = False
            win._scrape_finished.emit(success)     # reset status label
            return success

        scheduler.scraper_function = _gui_aware_execute

        win.show()

        logging.info("ImotScraper application started")
        sys.exit(app.exec())

    except Exception as e:
        logging.error(f"Failed to start application: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
