# FILE: email_service.py (Complete and Final ReportMailer)

import smtplib
import logging
import csv
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Dict

# --- Constants for formatting ---
NEW_LINE = '\n'
REPORT_HEADER = "Hello," + NEW_LINE*2 + "Here are the results for your tracked property searches:" + NEW_LINE
REPORT_FOOTER = NEW_LINE + "Have a great day."
ADMIN_EMAIL = 'viktor.pavlov92@gmail.com' # Fixed Admin Email
FAILURE_REPORT_CONTENT = (
    "--- CRITICAL SCRAPER FAILURE ---\n\n"
    f"The scheduled scraping job failed to complete successfully. "
    f"Please check the scraper.log file in the 'data' directory for detailed errors."
)


class ReportMailer:
    """
    Handles reading the search data, generating customized reports, and sending 
    conditional emails (reports on success, admin notification on failure).
    """
    def __init__(self):
        """
        Initializes the mailer with hardcoded server credentials.
        """
        # --- Hardcoded Credentials ---
        self.smtp_server = 'smtp.gmail.com'
        self.smtp_port = 587
        self.smtp_user = 'viktor.pavlov92@gmail.com'
        self.smtp_pass = 'luzd ctfm gwfm gibl'
        # -----------------------------
        
        self.is_configured = bool(self.smtp_server and self.smtp_port and self.smtp_user and self.smtp_pass)
        
        if not self.is_configured:
            logging.warning("ReportMailer initialized with incomplete credentials. Will not be able to send emails.")

    
    def send_reports_or_failure_notification(self, success: bool, data_dir="data", input_csv="data/inputURLS.csv"):
        """
        Sends emails based on the scraper's run status.
        If success is True, sends customized reports to all relevant users. 
        If success is False, sends a failure email ONLY to the administrator.
        """
        if not self.is_configured:
            logging.error("Cannot send email: ReportMailer is not configured with SMTP credentials.")
            return

        if success:
            logging.info("Scraper succeeded. Preparing and sending customized reports.")
            self._send_success_reports(data_dir, input_csv)
        else:
            logging.warning("Scraper failed. Sending failure notification to administrator.")
            self._send_failure_notification()


    def _generate_report_summary(self, filename: str, data_dir: str) -> str:
        """
        Generates a text summary for a single search (FileName) by reading the NewRecords file.
        """
        search_name = filename.strip('.csv')
        new_records_file = f"NewRecords_{filename}"
        filepath = os.path.join(data_dir, new_records_file)
        
        summary = [f"  - **{search_name}:**"]
        
        if not os.path.exists(filepath):
            # Case 1: No new records found
            summary.append(f"    - No new records or price changes found.")
            return NEW_LINE.join(summary)
            
        # Case 2: New records found, read and format the data
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                for row in reader:
                    title = row.get('Title', 'Property')
                    price = row.get('Price', 'N/A')
                    old_value = row.get('oldValue', '')
                    link = row.get('Link', '#')
                    
                    if old_value.lower() == 'new':
                        # New add format
                        summary.append(f"    - **New Add:** {title} price: {price} ({link})")
                    else:
                        # Price update format
                        summary.append(f"    - **Price Update:** {title} price updated to: {price} from: {old_value} ({link})")
                        
        except Exception as e:
            logging.error(f"Error reading report file {filepath}: {e}")
            summary.append(f"    - Error generating report details.")

        return NEW_LINE.join(summary)


    def _send_success_reports(self, data_dir, input_csv):
        """
        Prepares and sends individualized reports based on search files.
        """
        logging.info("Preparing individualized reports based on search files.")
        recipient_searches: Dict[str, List[str]] = {}

        # --- 1. Map Emails to their Search Files ---
        try:
            with open(input_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    filename = row.get('FileName')
                    # Handle both 'Send to Emails' and 'Email' headers
                    emails_to_send_str = row.get('Send to Emails') or row.get('Email') 
                    
                    if not filename or not emails_to_send_str: continue
                        
                    # Handle both semicolon (;) and comma (,) separators
                    separator = ';' if ';' in emails_to_send_str else ','
                    recipients = [email.strip() for email in emails_to_send_str.split(separator) if email.strip()]
                    
                    for email in recipients:
                        if email:
                            if email not in recipient_searches:
                                recipient_searches[email] = []
                            recipient_searches[email].append(filename)
            
        except FileNotFoundError:
            logging.error(f"Could not find input file: {input_csv}")
            return
        except Exception as e:
            logging.error(f"Error mapping recipients: {e}")
            return

        if not recipient_searches:
             logging.warning("No email recipients found. Skipping email send.")
             return
             
        # --- 2. Generate and Send Report for Each Unique Recipient ---
        for recipient_email, filenames in recipient_searches.items():
            logging.info(f"Generating consolidated report for: {recipient_email}")
            
            full_report_content = []
            for filename in filenames:
                summary = self._generate_report_summary(filename, data_dir)
                full_report_content.append(summary)

            email_body = (
                REPORT_HEADER + NEW_LINE.join(full_report_content) + REPORT_FOOTER
            )
            
            self._send_email(
                to_emails_list=[recipient_email], 
                subject="Scraper Report: Your Property Watch Updates",
                body=email_body
            )

    
    def _send_failure_notification(self):
        """
        Sends a simple error email to the defined administrator.
        """
        self._send_email(
            to_emails_list=[ADMIN_EMAIL],
            subject="!! URGENT: Scraper Failure Notification !!",
            body=FAILURE_REPORT_CONTENT
        )


    def _send_email(self, to_emails_list: List[str], subject: str, body: str) -> bool:
        """
        Internal method to send a single email with only text body.
        """
        if not to_emails_list:
            logging.warning("No recipients specified, skipping email.")
            return False

        try:
            msg = MIMEMultipart()
            msg['From'] = self.smtp_user
            msg['To'] = ", ".join(to_emails_list)
            msg['Subject'] = subject
            
            # The body is attached as plain text
            msg.attach(MIMEText(body, 'plain'))
            
            # --- Connect and send ---
            logging.info(f"Connecting to {self.smtp_server}:{self.smtp_port}...")
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_pass)
                server.sendmail(self.smtp_user, to_emails_list, msg.as_string())
            
            logging.info(f"Email sent successfully to {', '.join(to_emails_list)}.")
            return True

        except Exception as e:
            logging.error(f"Failed to send email: {e}")
            return False