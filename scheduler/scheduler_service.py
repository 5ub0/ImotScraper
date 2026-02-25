"""
Scheduler module for ImotScraper - handles scheduled execution of scraper jobs.
Manages the scheduled execution of the scraper and passes the result to the 
ReportMailer for appropriate email handling.
"""

import schedule
import threading
import time
import logging
from typing import Callable, Optional


class ScraperScheduler:
    """
    Manages the scheduled execution of the scraper and passes the result to the 
    ReportMailer for appropriate email handling.
    """
    def __init__(self, report_mailer, scraper_function: Optional[Callable] = None):
        self.report_mailer = report_mailer
        self.scraper_function = scraper_function
        self._job = None
        self._running_thread = None
        self._stop_run_loop = threading.Event()
        self.logger = logging.getLogger(__name__)

    def run_scheduled_job(self):
        """
        The complete task: Run Scraper -> Pass Result to Mailer.
        """
        self.logger.info("⭐ [Scheduler] Starting scheduled job: Scrape & Report...")
        scraper_succeeded = False
        
        try:
            self.logger.info("[Scheduler] Running scraper job...")
            scraper_succeeded = self.scraper_function() 
            self.logger.info(f"[Scheduler] Scraper finished. Success status: {scraper_succeeded}")

        except Exception as e:
            self.logger.error(f"[Scheduler] CRITICAL ERROR during scraper execution: {e}")
            scraper_succeeded = False

        # Delegate the decision (report vs. failure notification) entirely to the ReportMailer
        self.report_mailer.send_reports_or_failure_notification(scraper_succeeded)
            
        self.logger.info("✅ [Scheduler] Scheduled job complete.")

    def _scheduler_loop(self):
        """Internal thread loop to run schedule.run_pending()"""
        self.logger.info("[Scheduler] Scheduler loop started in background thread.")
        while not self._stop_run_loop.is_set():
            schedule.run_pending()
            time.sleep(1)
        self.logger.info("[Scheduler] Scheduler loop stopped.")

    def start(self, time_str: str) -> bool:
        """
        Starts the scheduler to run at the specified time daily.
        """
        if self._job:
            self.logger.warning("Scheduler is already running. Please stop it first.")
            return False
            
        try:
            self._job = schedule.every().day.at(time_str).do(self.run_scheduled_job)
            self._stop_run_loop.clear()
            self._running_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
            self._running_thread.start()
            self.logger.info(f"Job scheduled successfully for {time_str} daily.")
            return True
        except Exception as e:
            self.logger.error(f"Failed to schedule job at {time_str}: {e}")
            return False

    def stop(self):
        """Stops the scheduler and cancels all jobs."""
        if self._job:
            schedule.cancel_job(self._job)
            self._job = None
            self._stop_run_loop.set()
            self.logger.info("Job unscheduled successfully.")
        else:
            self.logger.info("No job was scheduled. Nothing to stop.")

    def run_job_now(self):
        """
        Immediately executes the main scheduled job logic for testing purposes.
        This bypasses the time scheduler (schedule.every().day.at()).
        """
        self.logger.info("⚡ [Scheduler] Running job immediately for test.")
        self.run_scheduled_job()