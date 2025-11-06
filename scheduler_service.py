# FILE: scheduler_service.py (Simplified)

import schedule
import threading
import time
import logging
from imotBgScraper import main as scraper_main_job 
from email_service import ReportMailer

class ScraperScheduler:
    """
    Manages the scheduled execution of the scraper and passes the result to the 
    ReportMailer for appropriate email handling.
    """
    def __init__(self, report_mailer: ReportMailer):
        self.report_mailer = report_mailer
        self.scraper_function = scraper_main_job
        self._job = None
        self._running_thread = None
        self._stop_run_loop = threading.Event()

    def run_scheduled_job(self):
        """
        The complete task: Run Scraper -> Pass Result to Mailer.
        """
        logging.info("⭐ [Scheduler] Starting scheduled job: Scrape & Report...")
        scraper_succeeded = False
        
        try:
            logging.info("[Scheduler] Running scraper job...")
            # ASSUMPTION: scraper_main_job returns True on success, False on failure.
            scraper_succeeded = self.scraper_function() 
            logging.info(f"[Scheduler] Scraper finished. Success status: {scraper_succeeded}")

        except Exception as e:
            logging.error(f"[Scheduler] CRITICAL ERROR during scraper execution: {e}")
            scraper_succeeded = False # Force failure state on unexpected exception

        # --- Simplified Email Invocation ---
        # Delegate the decision (report vs. failure notification) entirely to the ReportMailer
        self.report_mailer.send_reports_or_failure_notification(scraper_succeeded)
            
        logging.info("✅ [Scheduler] Scheduled job complete.")

    # --- _scheduler_loop, start, and stop methods remain the same ---
    
    def _scheduler_loop(self):
        """Internal thread loop to run schedule.run_pending()"""
        logging.info("[Scheduler] Scheduler loop started in background thread.")
        while not self._stop_run_loop.is_set():
            schedule.run_pending()
            time.sleep(1)
        logging.info("[Scheduler] Scheduler loop stopped.")

    def start(self, time_str: str) -> bool:
        """
        Starts the scheduler to run at the specified time daily.
        """
        if self._job:
            logging.warning("Scheduler is already running. Please stop it first.")
            return False
            
        try:
            self._job = schedule.every().day.at(time_str).do(self.run_scheduled_job)
            self._stop_run_loop.clear()
            self._running_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
            self._running_thread.start()
            logging.info(f"Job scheduled successfully for {time_str} daily.")
            return True
        except Exception as e:
            logging.error(f"Failed to schedule job at {time_str}: {e}")
            return False

    def stop(self):
        """Stops the scheduler and cancels all jobs."""
        if self._job:
            schedule.cancel_job(self._job)
            self._job = None
            self._stop_run_loop.set()
            logging.info("Job unscheduled successfully.")
        else:
            logging.info("No job was scheduled. Nothing to stop.")

    def run_job_now(self):
        """
        Immediately executes the main scheduled job logic for testing purposes.
        This bypasses the time scheduler (schedule.every().day.at()).
        """
        logging.info("⚡ [Scheduler] Running job immediately for test.")
        self.run_scheduled_job()