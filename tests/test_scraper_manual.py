"""
Test script to isolate and debug the scraper crash issue
"""

import logging
import sys
import os

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from controller.app_controller import AppController
from scraper.imotBgScraper import ImotScraper
from email_service_module.email_service import ReportMailer
from scheduler.scheduler_service import ScraperScheduler

print("[TEST] Starting scraper test...")

try:
    # Initialize components
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
    
    print("[TEST] Components initialized successfully")
    print("[TEST] Checking if inputURLS.csv exists...")
    
    if not os.path.exists('data/inputURLS.csv'):
        print("[TEST] ERROR: data/inputURLS.csv does not exist")
        print("[TEST] Creating a sample CSV file...")
        
        os.makedirs('data', exist_ok=True)
        with open('data/inputURLS.csv', 'w', newline='', encoding='utf-8') as f:
            f.write('URL,FileName,Send to Emails\n')
            f.write('https://www.imot.bg/obiavi/prodazhbi/grad-sofiya/boyana/kashta,test_search.csv,test@example.com\n')
        
        print("[TEST] Sample CSV created")
    
    print("[TEST] Calling run_scraper_and_report()...")
    controller.run_scraper_and_report()
    print("[TEST] SUCCESS: run_scraper_and_report() completed without error")
    
except Exception as e:
    print(f"[TEST] FAILED: {e}")
    import traceback
    traceback.print_exc()
