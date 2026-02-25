# FILE: test_scheduler.py (Updated to use the REAL Scraper)

import logging
from email_service import ReportMailer
from scheduler_service import ScraperScheduler
from imotBgScraper import main as scraper_main_job # Import the real scraper

# --- Setup logging to see output ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def run_scheduler_test():
    """
    Initializes services and runs the actual scraper job instantly.
    """
    logging.info("--- Starting REAL Scraper & Scheduler Test ---")
    
    mailer = ReportMailer()
    scheduler = ScraperScheduler(report_mailer=mailer)
    
    # 1. Ensure the scheduler uses the real imported function
    # NOTE: You only need to set this if you previously ran the mock test, 
    # as the scheduler's __init__ should already use it, but this ensures it.
    scheduler.scraper_function = scraper_main_job 

    # 2. Execute the job immediately
    scheduler.run_job_now()
    
    logging.info("--- Scheduler Test Finished ---")

if __name__ == "__main__":
    run_scheduler_test()