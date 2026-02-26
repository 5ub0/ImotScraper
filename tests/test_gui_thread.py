"""
Simulate the GUI's 'Run Scrape' button path (no tkinter needed)
to see whether controller.run_scraper() or send_email_reports() crashes.
"""
import sys, os, threading, traceback, logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

from scraper.imotBgScraper import ImotScraper
from email_service_module.email_service import ReportMailer
from scheduler.scheduler_service import ScraperScheduler
from controller.app_controller import AppController

scraper = ImotScraper(data_dir='data')
email_service = ReportMailer()
scheduler = ScraperScheduler(report_mailer=email_service, scraper_function=scraper.execute)
controller = AppController(gui=None, scraper=scraper, email_service=email_service, scheduler=scheduler)

print('[TEST] Simulating run_scraper in a background thread...')
result = {'value': None}
error  = {'value': None}

def run():
    try:
        result['value'] = controller.run_scraper()
        print(f'[TEST] run_scraper returned: {result["value"]}')
        controller.send_email_reports(result['value'])
        print('[TEST] send_email_reports done')
    except BaseException as e:
        error['value'] = e
        print(f'[TEST] EXCEPTION in thread: {e}')
        traceback.print_exc()

t = threading.Thread(target=run)
t.start()
t.join(timeout=120)
print(f'[TEST] Thread finished. result={result["value"]}, error={error["value"]}')
