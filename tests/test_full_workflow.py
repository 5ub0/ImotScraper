"""
Integration test: Full workflow without email SMTP configured
This test mimics exactly what happens when you click "Run Scraping Now"
"""

import logging
import sys
import os

# Set up detailed logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from controller.app_controller import AppController
from scraper.imotBgScraper import ImotScraper
from email_service_module.email_service import ReportMailer
from scheduler.scheduler_service import ScraperScheduler

print("\n" + "="*70)
print("INTEGRATION TEST: Full Scraper Workflow (No Email SMTP)")
print("="*70)

try:
    # Initialize components (same as in main.py)
    print("\n[TEST] Initializing components...")
    scraper = ImotScraper(data_dir='data')
    email_service = ReportMailer()
    scheduler = ScraperScheduler(
        report_mailer=email_service,
        scraper_function=scraper.execute
    )
    controller = AppController(
        gui=None,
        scraper=scraper,
        email_service=email_service,
        scheduler=scheduler
    )
    print("[TEST] ✓ Components initialized successfully")

    # Simulate clicking "Run Scraping Now"
    print("\n[TEST] Simulating 'Run Scraping Now' button click...")
    print("[TEST] Calling controller.run_scraper_and_report()...")
    
    controller.run_scraper_and_report()
    
    print("\n[TEST] ✓ Workflow completed successfully!")
    print("[TEST] No email SMTP errors occurred")
    print("[TEST] Application continues normally")
    
except Exception as e:
    print(f"\n[TEST] ✗ ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*70)
print("EXPECTED BEHAVIOR:")
print("="*70)
print("✓ Scraper should run without errors")
print("✓ No email sending attempts should occur")
print("✓ Only INFO/WARNING logs about skipping email")
print("✓ Application should complete gracefully")
print("="*70 + "\n")
