"""
Test to verify GUI responsiveness during scraping
"""

import tkinter as tk
from tkinter import ttk
import threading
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

from controller.app_controller import AppController
from scraper.imotBgScraper import ImotScraper
from email_service_module.email_service import ReportMailer
from scheduler.scheduler_service import ScraperScheduler

root = tk.Tk()
root.title("Scraper Thread Test")
root.geometry("600x400")

log_text = tk.Text(root, height=20, width=70)
log_text.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

def log_message(msg):
    log_text.insert(tk.END, msg + "\n")
    log_text.see(tk.END)
    root.update()

log_message("Initializing components...")

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

log_message("Components initialized. Click 'Run Scraper' to test.")

def run_scraper_thread():
    """Run scraper in a thread"""
    try:
        log_message("[Thread] Starting scraper...")
        success = controller.run_scraper()
        root.after(0, lambda: log_message(f"[Thread] Scraper completed: {success}"))
        root.after(0, lambda: log_message("[Thread] Sending reports..."))
        controller.send_email_reports(success)
        root.after(0, lambda: log_message("[Thread] All tasks complete!"))
    except Exception as e:
        root.after(0, lambda: log_message(f"[Thread] ERROR: {e}"))
        import traceback
        traceback.print_exc()

def start_scraping():
    """Start scraping in background thread"""
    log_message("[GUI] Starting scraper thread...")
    log_message("[GUI] The GUI should remain responsive...")
    thread = threading.Thread(target=run_scraper_thread, daemon=True)
    thread.start()

# Test button
button = ttk.Button(root, text="Run Scraper (Background)", command=start_scraping)
button.pack(pady=10)

# Counter to show GUI is responsive
counter = 0
def update_counter():
    global counter
    counter += 1
    button.config(text=f"Run Scraper (Counter: {counter})")
    root.after(100, update_counter)

root.after(100, update_counter)

log_message("GUI Ready - click button to test threaded scraper")
root.mainloop()
