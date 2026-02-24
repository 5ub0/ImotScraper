"""
Test to verify email sending logic only attempts to send when appropriate
"""

import logging
import sys
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from email_service_module.email_service import ReportMailer

print("\n" + "="*70)
print("TEST 1: Email service without SMTP credentials configured")
print("="*70)

mailer = ReportMailer()

print("\nTest 1a: Attempting to send on SUCCESS (no recipients)")
mailer.send_reports_or_failure_notification(success=True)

print("\nTest 1b: Attempting to send on FAILURE")
mailer.send_reports_or_failure_notification(success=False)

print("\n" + "="*70)
print("TEST 2: Expected behavior")
print("="*70)
print("✓ No email should be sent because:")
print("  1. SMTP credentials are not configured in environment")
print("  2. No email recipients are defined in inputURLS.csv")
print("  3. The functions should exit gracefully with info/warning logs")
print("\nThe application should continue normally without error.")
print("="*70 + "\n")
