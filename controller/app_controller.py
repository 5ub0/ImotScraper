"""
Controller module for ImotScraper.
This module manages communication between GUI, scraper, email, and scheduler.
It acts as the intermediary coordinating the application flow.
"""

import logging


class AppController:
    """
    Central controller for the ImotScraper application.
    Manages communication between GUI, scraper, email service, and scheduler.
    """
    
    def __init__(self, gui=None, scraper=None, email_service=None, scheduler=None):
        self.gui = gui
        self.scraper = scraper
        self.email_service = email_service
        self.scheduler = scheduler
        self.logger = logging.getLogger(__name__)

        # Expose the database manager via the scraper so other components
        # (e.g. GUI) can query results without going through the scraper.
        self.db = scraper.db if scraper else None

    def run_scraper(self) -> bool:
        """
        Delegate scraper execution to the scraper module.
        
        Returns:
            bool: True if scraping succeeded, False otherwise
        """
        if not self.scraper:
            self.logger.error("Scraper component not initialized")
            return False
        
        try:
            return self.scraper.execute()
        except Exception as e:
            self.logger.error(f"Error during scraper execution: {e}")
            return False

    def send_email_reports(self, success: bool) -> None:
        """
        Delegate email report sending to the email service.
        
        Args:
            success: Whether the scraping job succeeded
        """
        if not self.email_service:
            self.logger.warning("Email service component not initialized")
            return
        
        try:
            self.email_service.send_reports_or_failure_notification(success)
        except Exception as e:
            self.logger.error(f"Error sending email reports: {e}")

    def schedule_scraper(self, time_str: str) -> bool:
        """
        Delegate scheduling of scraper jobs to the scheduler.
        
        Args:
            time_str: Time in HH:MM format (e.g., "08:30")
            
        Returns:
            bool: True if scheduling succeeded, False otherwise
        """
        if not self.scheduler:
            self.logger.error("Scheduler component not initialized")
            return False
        
        try:
            return self.scheduler.start(time_str)
        except Exception as e:
            self.logger.error(f"Error scheduling scraper: {e}")
            return False

    def stop_scheduler(self) -> None:
        """
        Delegate stopping the scheduler to the scheduler component.
        """
        if not self.scheduler:
            self.logger.warning("Scheduler component not initialized")
            return
        
        try:
            self.scheduler.stop()
        except Exception as e:
            self.logger.error(f"Error stopping scheduler: {e}")

    def run_scraper_and_report(self) -> None:
        """
        Execute the complete scraping workflow:
        1. Run the scraper
        2. Send reports or failure notification based on the result
        """
        self.logger.info("Starting complete scraper workflow...")
        try:
            success = self.run_scraper()
            self.logger.info(f"Scraper result: {'SUCCESS' if success else 'FAILED'}")
            self.send_email_reports(success)
            self.logger.info("Scraper workflow complete.")
        except Exception as e:
            self.logger.error(f"Error in scraper workflow: {e}", exc_info=True)
            try:
                self.send_email_reports(False)
            except Exception as e2:
                self.logger.error(f"Also failed to send failure notification: {e2}")

    # ------------------------------------------------------------------
    # Search management (delegates to db)
    # ------------------------------------------------------------------

    def get_all_searches(self):
        return self.db.get_all_searches() if self.db else []

    def add_search(self, search_name: str, url: str, emails: str = "") -> int:
        if not self.db:
            raise RuntimeError("Database not initialized")
        return self.db.add_search(search_name, url, emails)

    def update_search(self, search_id: int, search_name: str, url: str, emails: str = ""):
        if not self.db:
            raise RuntimeError("Database not initialized")
        self.db.update_search(search_id, search_name, url, emails)

    def delete_search(self, search_id: int):
        if not self.db:
            raise RuntimeError("Database not initialized")
        self.db.delete_search(search_id)

    def get_properties_for_search(self, search_name: str, status: str = None):
        if not self.db:
            return []
        searches = self.db.get_all_searches()
        match = next((s for s in searches if s["search_name"] == search_name), None)
        if not match:
            return []
        return self.db.get_properties(match["id"], status)