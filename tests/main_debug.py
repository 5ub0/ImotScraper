"""
Debug entry point for ImotScraper application.
This version shows console output for debugging.
"""

import logging
import sys
import os

# Configure logging to BOTH file and console
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('data/app.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# Add parent directory to path to allow imports from submodules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger.info("Importing application modules...")

try:
    from controller.app_controller import AppController
    from scraper.imotBgScraper import ImotScraper
    from email_service_module.email_service import ReportMailer
    from scheduler.scheduler_service import ScraperScheduler
    from gui.imot_gui import ImotScraperGUI
    import tkinter as tk
    
    logger.info("All modules imported successfully")

except Exception as e:
    logger.critical(f"Failed to import modules: {e}", exc_info=True)
    sys.exit(1)


def main():
    """
    Initialize all application components and start the GUI.
    """
    try:
        logger.info("Initializing application components...")
        
        # Initialize core components
        scraper = ImotScraper(data_dir='data')
        logger.info("Scraper initialized")
        
        email_service = ReportMailer()
        logger.info("Email service initialized")
        
        scheduler = ScraperScheduler(
            report_mailer=email_service,
            scraper_function=scraper.execute
        )
        logger.info("Scheduler initialized")
        
        # Create controller to coordinate components
        controller = AppController(
            gui=None,
            scraper=scraper,
            email_service=email_service,
            scheduler=scheduler
        )
        logger.info("Controller initialized")
        
        # Initialize and run GUI
        logger.info("Creating GUI...")
        root = tk.Tk()
        gui = ImotScraperGUI(root, controller=controller)
        controller.gui = gui
        
        logger.info("ImotScraper application started successfully")
        root.mainloop()
        logger.info("Application closed normally")
        
    except Exception as e:
        logger.critical(f"Failed to start application: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
