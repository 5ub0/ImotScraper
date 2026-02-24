# FILE: test_email.py

import logging
from email_service import ReportMailer

# Configure logging so you can see the progress in the console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

if __name__ == "__main__":
    logging.info("--- Starting ReportMailer Test ---")
    
    # 1. Instantiate the ReportMailer (no parameters needed now)
    mailer = ReportMailer()

    # 2. Call the main method
    mailer.check_and_send_all_reports(
        data_dir="data", 
        input_csv="data/inputURLS.csv"
    )
    
    logging.info("--- Email Test Finished ---")