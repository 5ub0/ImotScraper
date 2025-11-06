import tkinter as tk
from tkinter import ttk, scrolledtext
from tkinter import filedialog, messagebox
import logging
import threading
import csv
import webbrowser
import os
import re
import time
from imotBgScraper import main as scraper_main_job
from email_service import ReportMailer
from scheduler_service import ScraperScheduler

# --- CustomText and TextHandler classes remain unchanged ---
class CustomText(scrolledtext.ScrolledText):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tag_config("url", foreground="blue", underline=1)
        self.bind("<Button-1>", self._click)
        
    def _click(self, event):
        for tag in self.tag_names("@%d,%d" % (event.x, event.y)):
            if tag == "url":
                start = "@%d,%d" % (event.x, event.y)
                url = self.get(f"{start} linestart", f"{start} lineend")
                match = re.search(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', url)
                if match:
                    webbrowser.open(match.group(0))
                break

class TextHandler(logging.Handler):
    def __init__(self, text_widget):
        logging.Handler.__init__(self)
        self.text_widget = text_widget
    
    def emit(self, record):
        msg = self.format(record) + '\n'
        self.text_widget.insert(tk.END, msg)
        
        if 'http' in msg:
            line_start = self.text_widget.get("end-2c linestart", "end-2c lineend")
            start_idx = f"end-2c linestart+{line_start.find('http')}c"
            url_end = len(line_start)
            end_idx = f"end-2c linestart+{url_end}c"
            self.text_widget.tag_add("url", start_idx, end_idx)
        
        self.text_widget.see(tk.END)

# --- Main GUI Class ---

class ImotScraperGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Imot.bg Scraper")
        self.root.geometry("950x850") 
        self.root.configure(padx=10, pady=10)
        
        self.urls = []
        self.data_dir = 'data'
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
            
        self.report_mailer = ReportMailer()
        self.scheduler = ScraperScheduler(report_mailer=self.report_mailer)
        self.scheduler_running = False
        
        # Instance variable to hold the button frame for reliable refreshing
        self.file_view_button_frame = None 

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.setup_gui()
        
    def setup_gui(self):
        
        # -----------------------------------------------
        # --- SCHEDULER CONTROL FRAME ---
        # -----------------------------------------------
        schedule_frame = ttk.LabelFrame(self.root, text="Scheduled Scraping Control", padding=10)
        schedule_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Time Entry
        ttk.Label(schedule_frame, text="Daily Time (HH:MM):").grid(row=0, column=0, padx=5, pady=5, sticky='W')
        self.time_entry = ttk.Entry(schedule_frame, width=10)
        self.time_entry.insert(0, "08:00") # Default time
        self.time_entry.grid(row=0, column=1, padx=5, pady=5, sticky='W')
        
        # Status Label
        self.schedule_status_label = ttk.Label(schedule_frame, text="Status: STOPPED", foreground="red")
        self.schedule_status_label.grid(row=0, column=2, padx=20, pady=5, sticky='W')
        
        # Control Buttons
        self.start_schedule_btn = ttk.Button(schedule_frame, text="Start Daily Schedule", command=self.start_schedule)
        self.start_schedule_btn.grid(row=0, column=3, padx=5, pady=5, sticky='E')
        
        self.stop_schedule_btn = ttk.Button(schedule_frame, text="Stop Schedule", command=self.stop_schedule, state=tk.DISABLED)
        self.stop_schedule_btn.grid(row=0, column=4, padx=5, pady=5, sticky='E')
        
        self.check_status_btn = ttk.Button(schedule_frame, text="Check Status", command=self.check_schedule_status_log)
        self.check_status_btn.grid(row=0, column=5, padx=5, pady=5, sticky='E')
        
        schedule_frame.grid_columnconfigure(5, weight=1) 
        # -----------------------------------------------
        
        # Create PanedWindow for adjustable sections
        paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)
        
        # Upper section (URLs and Files)
        upper_section = ttk.Frame(paned)
        
        # URLs List Frame
        urls_frame = ttk.LabelFrame(upper_section, text="Search URL List", padding=10)
        urls_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # URLs Treeview with scrollbars
        tree_frame = ttk.Frame(urls_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        # Treeview Columns
        self.tree = ttk.Treeview(tree_frame, columns=('Search Name', 'Emails', 'URL'), show='headings', height=6)
        
        # Headings
        self.tree.heading('Search Name', text='Search Name')
        self.tree.heading('Emails', text='Subscribed Emails')
        self.tree.heading('URL', text='URL')

        # Column widths 
        self.tree.column('Search Name', width=150)
        self.tree.column('Emails', width=200)
        self.tree.column('URL', width=350)
        
        # Add scrollbars to tree
        tree_vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        tree_hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_vsb.set, xscrollcommand=tree_hsb.set)
        
        # Grid layout for tree and scrollbars
        self.tree.grid(row=0, column=0, sticky='nsew')
        tree_vsb.grid(row=0, column=1, sticky='ns')
        tree_hsb.grid(row=1, column=0, sticky='ew')
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)
        
        # Control buttons frame
        control_frame = ttk.Frame(upper_section)
        control_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Control buttons
        ttk.Button(control_frame, text="Add New Search", command=lambda: self.show_add_url_dialog(action="create")).pack(side=tk.LEFT, padx=5)
        # --- NEW EDIT BUTTON ---
        ttk.Button(control_frame, text="Edit Selected", command=self.edit_selected_url).pack(side=tk.LEFT, padx=5)
        # -----------------------
        ttk.Button(control_frame, text="Remove Selected", command=self.remove_url).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Run Scraping Now", command=self.start_scraping).pack(side=tk.RIGHT, padx=5)
        
        # File view frame
        file_view_frame = ttk.LabelFrame(upper_section, text="View CSV Files", padding=10)
        file_view_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Create a sub-frame for buttons and save it to an instance variable
        self.file_view_button_frame = ttk.Frame(file_view_frame)
        self.file_view_button_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Load buttons
        input_file = os.path.join(self.data_dir, 'inputURLS.csv')
        self.load_file_view_buttons(self.file_view_button_frame, input_file)
        
        # Lower section (Log output)
        lower_section = ttk.Frame(paned)
        
        # Log output
        log_frame = ttk.LabelFrame(lower_section, text="Log Output", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_text = CustomText(log_frame, height=15)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # Add both sections to the PanedWindow with weights
        paned.add(upper_section, weight=40) 
        paned.add(lower_section, weight=60)
        
        self.setup_logging()
        self.load_existing_urls()

    # --- Utility Methods ---
    def setup_logging(self):
        """Configures Python's logging to route messages to the GUI's log text widget."""
        handler = TextHandler(self.log_text)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        
        root_logger = logging.getLogger()
        if not any(isinstance(h, TextHandler) for h in root_logger.handlers):
            root_logger.addHandler(handler)
        
        root_logger.setLevel(logging.INFO)

    def view_csv_file(self, filename):
        # Implementation remains the same
        filepath = os.path.join(self.data_dir, filename)
        if not os.path.exists(filepath):
            messagebox.showerror("Error", f"File not found: {filepath}")
            return
            
        view_window = tk.Toplevel(self.root)
        view_window.title(f"Viewing {filename}")
        view_window.geometry("800x600")
        view_window.minsize(600, 400)
        
        view_window.rowconfigure(0, weight=1)
        view_window.columnconfigure(0, weight=1)

        main_frame = ttk.Frame(view_window)
        main_frame.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        main_frame.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)

        tree = ttk.Treeview(main_frame)
        tree.grid(row=0, column=0, sticky='nsew')

        vsb = ttk.Scrollbar(main_frame, orient="vertical", command=tree.yview)
        vsb.grid(row=0, column=1, sticky='ns')
        hsb = ttk.Scrollbar(main_frame, orient="horizontal", command=tree.xview)
        hsb.grid(row=1, column=0, sticky='ew')
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        try:
            with open(filepath, 'r', encoding='utf-8') as file:
                csv_reader = csv.reader(file)
                headers = next(csv_reader)
                tree['columns'] = headers
                tree['show'] = 'headings'

                for header in headers:
                    tree.heading(header, text=header)
                    tree.column(header, width=100)

                for row in csv_reader:
                    tree.insert("", tk.END, values=row)

        except Exception as e:
            messagebox.showerror("Error", f"Error reading file: {str(e)}")

    def load_file_view_buttons(self, button_frame, input_file):
        """Helper to create or destroy file view buttons."""
        
        # Clear existing buttons
        for widget in button_frame.winfo_children():
            widget.destroy()
            
        if os.path.exists(input_file):
            try:
                with open(input_file, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        filename = row['FileName']
                        filepath = os.path.join(self.data_dir, filename)
                        if os.path.exists(filepath):
                            ttk.Button(
                                button_frame, 
                                text=f"Open {filename.replace('.csv', '')}",
                                command=lambda f=filename: self.view_csv_file(f),
                                width=20
                            ).pack(side=tk.LEFT, padx=5, pady=5)
            except Exception as e:
                # The exception log will now report a clean message, if any other error occurs
                logging.error(f"Error loading file view buttons: {e}")

    # --- BUG FIX: Simplified Refresh ---
    def refresh_file_view(self):
        """Triggers a reload of the file view buttons after an add/remove operation."""
        input_file = os.path.join(self.data_dir, 'inputURLS.csv')
        # Use the stored instance variable for the button frame
        self.load_file_view_buttons(self.file_view_button_frame, input_file)

    # --- New/Updated Dialog Methods ---

    def edit_selected_url(self):
        """Handles the 'Edit Selected' button click."""
        selected_items = self.tree.selection()
        if not selected_items:
            messagebox.showwarning("Warning", "Please select a search to edit.")
            return
        
        # Only edit the first selected item
        item_id = selected_items[0]
        # values: ('Search Name', 'Emails', 'URL')
        values = self.tree.item(item_id)['values']
        
        # Split emails for populating the fields
        emails = values[1].split(';')
        
        # Call the dialog in "edit" mode
        self.show_add_url_dialog(
            action="edit",
            item_id=item_id,
            url=values[2],
            search_name=values[0],
            emails=emails
        )

    def show_add_url_dialog(self, action="create", item_id=None, url="", search_name="", emails=None):
        """Shows a modal dialog to input/edit URL, Search Name, and multiple Emails."""
        if emails is None:
            emails = [""] 

        dialog = tk.Toplevel(self.root)
        dialog.title("Add New Search" if action == "create" else f"Edit {search_name}")
        dialog.geometry("500x350")
        dialog.minsize(450, 300) 
        dialog.transient(self.root) 
        
        dialog.rowconfigure(0, weight=1)
        dialog.columnconfigure(0, weight=1)

        main_frame = ttk.Frame(dialog, padding=10)
        main_frame.grid(row=0, column=0, sticky='nsew')
        main_frame.columnconfigure(1, weight=1) 
        
        # --- NEW PASTE HELPER FUNCTION ---
        def paste_from_clipboard(event):
            """Inserts clipboard content into the focused widget."""
            try:
                # Use standard Tkinter clipboard retrieval
                clipboard_content = self.root.clipboard_get()
                widget = dialog.focus_get()
                if isinstance(widget, ttk.Entry) or isinstance(widget, tk.Entry):
                    widget.insert(tk.INSERT, clipboard_content)
                return "break" # Prevent default key handling
            except tk.TclError:
                # Handle cases where clipboard is empty or inaccessible
                pass
        # ---------------------------------

        # 1. URL Input
        ttk.Label(main_frame, text="URL:").grid(row=0, column=0, padx=5, pady=5, sticky='W')
        url_entry = ttk.Entry(main_frame, width=50)
        url_entry.grid(row=0, column=1, padx=5, pady=5, sticky='EW')
        url_entry.insert(0, url)
        # Bind paste to the URL field
        url_entry.bind('<Control-v>', paste_from_clipboard)
        url_entry.bind('<Command-v>', paste_from_clipboard) 
        
        if action == "edit":
            url_entry.config(state=tk.DISABLED) 
        else:
            url_entry.config(state=tk.NORMAL) 

        # 2. Search Name Input
        ttk.Label(main_frame, text="Search Name:").grid(row=1, column=0, padx=5, pady=5, sticky='W')
        name_entry = ttk.Entry(main_frame, width=50)
        name_entry.grid(row=1, column=1, padx=5, pady=5, sticky='EW')
        name_entry.insert(0, search_name)
        # Bind paste to the Search Name field
        name_entry.bind('<Control-v>', paste_from_clipboard)
        name_entry.bind('<Command-v>', paste_from_clipboard)
        
        name_entry.config(state=tk.NORMAL)
        
        # 3. Email Input Frame (Scrollable area for emails)
        email_frame_container = ttk.Frame(main_frame)
        email_frame_container.grid(row=2, column=0, columnspan=2, padx=5, pady=10, sticky='EW')
        
        email_canvas = tk.Canvas(email_frame_container, height=100)
        email_canvas.pack(side="left", fill="both", expand=True)
        
        email_scrollbar = ttk.Scrollbar(email_frame_container, orient="vertical", command=email_canvas.yview)
        email_scrollbar.pack(side="right", fill="y")
        
        email_canvas.configure(yscrollcommand=email_scrollbar.set)
        
        email_frame = ttk.Frame(email_canvas, padding=5)
        email_canvas.create_window((0, 0), window=email_frame, anchor="nw", tags="email_frame")
        
        email_frame.bind("<Configure>", lambda e: email_canvas.configure(scrollregion = email_canvas.bbox("all")))
        email_frame_container.bind("<Configure>", lambda e: email_canvas.itemconfig("email_frame", width=e.width))
        
        email_frame.columnconfigure(1, weight=1)

        self.email_entries = []
        
        add_email_btn = ttk.Button(email_frame, text="+ Add Email", command=lambda: add_email_field())
        
# FILE: imot_gui.py (Replace your entire show_add_url_dialog method)

    def show_add_url_dialog(self, action="create", item_id=None, url="", search_name="", emails=None):
        """Shows a modal dialog to input/edit URL, Search Name, and multiple Emails."""
        if emails is None:
            emails = [""] 

        dialog = tk.Toplevel(self.root)
        dialog.title("Add New Search" if action == "create" else f"Edit {search_name}")
        dialog.geometry("500x350")
        dialog.minsize(450, 300) 
        dialog.transient(self.root) 
        
        dialog.rowconfigure(0, weight=1)
        dialog.columnconfigure(0, weight=1)

        main_frame = ttk.Frame(dialog, padding=10)
        main_frame.grid(row=0, column=0, sticky='nsew')
        main_frame.columnconfigure(1, weight=1) 
        
        # --- NEW PASTE HELPER FUNCTION ---
        def paste_from_clipboard(event):
            """Inserts clipboard content into the focused widget."""
            try:
                clipboard_content = self.root.clipboard_get()
                widget = dialog.focus_get()
                if isinstance(widget, ttk.Entry) or isinstance(widget, tk.Entry):
                    widget.insert(tk.INSERT, clipboard_content)
                return "break"
            except tk.TclError:
                pass
        # ---------------------------------

        # 1. URL Input
        ttk.Label(main_frame, text="URL:").grid(row=0, column=0, padx=5, pady=5, sticky='W')
        url_entry = ttk.Entry(main_frame, width=50)
        url_entry.grid(row=0, column=1, padx=5, pady=5, sticky='EW')
        url_entry.insert(0, url)
        url_entry.bind('<Control-v>', paste_from_clipboard)
        url_entry.bind('<Command-v>', paste_from_clipboard) 
        
        if action == "edit":
            url_entry.config(state=tk.DISABLED) 
        else:
            url_entry.config(state=tk.NORMAL) 

        # 2. Search Name Input
        ttk.Label(main_frame, text="Search Name:").grid(row=1, column=0, padx=5, pady=5, sticky='W')
        name_entry = ttk.Entry(main_frame, width=50)
        name_entry.grid(row=1, column=1, padx=5, pady=5, sticky='EW')
        name_entry.insert(0, search_name)
        name_entry.bind('<Control-v>', paste_from_clipboard)
        name_entry.bind('<Command-v>', paste_from_clipboard)
        
        name_entry.config(state=tk.NORMAL)
        
        # 3. Email Input Frame (Scrollable area for emails)
        email_frame_container = ttk.Frame(main_frame)
        email_frame_container.grid(row=2, column=0, columnspan=2, padx=5, pady=10, sticky='EW')
        
        email_canvas = tk.Canvas(email_frame_container, height=100)
        email_canvas.pack(side="left", fill="both", expand=True)
        
        email_scrollbar = ttk.Scrollbar(email_frame_container, orient="vertical", command=email_canvas.yview)
        email_scrollbar.pack(side="right", fill="y")
        
        email_canvas.configure(yscrollcommand=email_scrollbar.set)
        
        email_frame = ttk.Frame(email_canvas, padding=5)
        email_canvas.create_window((0, 0), window=email_frame, anchor="nw", tags="email_frame")
        
        email_frame.bind("<Configure>", lambda e: email_canvas.configure(scrollregion = email_canvas.bbox("all")))
        email_frame_container.bind("<Configure>", lambda e: email_canvas.itemconfig("email_frame", width=e.width))
        
        email_frame.columnconfigure(1, weight=1)

        self.email_entries = []
        
        # --- FIX: Define add_email_btn BEFORE the function that uses it ---
        add_email_btn = ttk.Button(email_frame, text="+ Add Email", command=lambda: add_email_field())
        
        def add_email_field(event=None, email_value=""):
            """Adds a new email entry field and binds paste."""
            row_num = len(self.email_entries)
            
            ttk.Label(email_frame, text=f"Email {row_num + 1}:").grid(row=row_num, column=0, padx=5, pady=2, sticky='W')
            
            email_entry = ttk.Entry(email_frame, width=40)
            email_entry.grid(row=row_num, column=1, padx=5, pady=2, sticky='EW')
            email_entry.insert(0, email_value)
            
            email_entry.bind('<Control-v>', paste_from_clipboard)
            email_entry.bind('<Command-v>', paste_from_clipboard) 
            
            self.email_entries.append(email_entry)
            
            # Position the button after the new row
            add_email_btn.grid(row=row_num + 1, column=0, columnspan=2, pady=5, sticky='W')
            
            email_frame.update_idletasks()
            email_canvas.configure(scrollregion=email_canvas.bbox("all"))

        # Load existing emails or start fresh
        for email in emails:
            add_email_field(email_value=email)
        
        # Ensure at least one field and the button are drawn on creation/edit load
        if not self.email_entries:
             add_email_field(email_value="")
        
        # 4. Save Button
        save_btn = ttk.Button(main_frame, text="Save Changes" if action == "edit" else "Save Search", 
                              command=lambda: self._save_dialog_data(dialog, url_entry.get(), name_entry.get(), item_id))
        save_btn.grid(row=3, column=1, pady=10, sticky='E')
        
        dialog.grab_set() 
        self.root.wait_window(dialog)

    def _save_dialog_data(self, dialog, url, search_name_display, item_id=None):
        """Processes and saves data from the Add/Edit Search dialog."""
        
        url = url.strip()
        search_name_display = search_name_display.strip()
        
        # 1. Basic Validation
        if not url or not search_name_display:
            messagebox.showerror("Error", "URL and Search Name are required.", parent=dialog)
            return

        # Backend filename must retain the .csv extension
        filename = search_name_display + '.csv'
        
        # 2. Collect Emails and Concatenate
        emails = []
        for entry in self.email_entries:
            email = entry.get().strip()
            if email:
                # Simple email validation
                if re.match(r"[^@]+@[^@]+\.[^@]+", email): 
                    emails.append(email)
                else:
                    messagebox.showerror("Error", f"Invalid email format: {email}", parent=dialog)
                    return
        
        email_string = ";".join(emails) 
        
        # 3. Update/Insert Treeview and Save
        # Treeview column order: ('Search Name', 'Emails', 'URL')
        treeview_values = (search_name_display, email_string, url)
        
        if item_id:
            # Edit existing record
            self.tree.item(item_id, values=treeview_values)
            logging.info(f"Edited search: {search_name_display}")
        else:
            # Insert new record
            self.tree.insert('', tk.END, values=treeview_values)
            logging.info(f"Added new search: {search_name_display}")
            
        self.save_urls_to_csv()
        
        # Refresh the File View buttons
        self.refresh_file_view() 
        
        # 4. Close the modal dialog
        dialog.destroy()


    # --- CRUD Operations ---
    
    def remove_url(self):
        selected_items = self.tree.selection()
        if not selected_items:
            return 

        for item in selected_items:
            values = self.tree.item(item)['values']
            
            search_name_display = values[0] # Search Name (without .csv)
            filename_to_delete = f"{search_name_display}.csv" 
            
            # 1. Delete primary file
            filepath = os.path.join(self.data_dir, filename_to_delete)
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    logging.info(f"Successfully deleted data file: {filepath}")
                except OSError as e:
                    logging.error(f"Error deleting file {filepath}: {e}")

            # 2. Delete NewRecords file
            filepath_2 = os.path.join(self.data_dir, 'NewRecords_'+filename_to_delete)
            if os.path.exists(filepath_2):
                try:
                    os.remove(filepath_2)
                    logging.info(f"Successfully deleted data file: {filepath_2}")
                except OSError as e:
                    logging.error(f"Error deleting file {filepath_2}: {e}")
                
            self.tree.delete(item)
            
        self.save_urls_to_csv()
        self.refresh_file_view()

    def load_existing_urls(self):
        input_file = os.path.join(self.data_dir, 'inputURLS.csv')
        if os.path.exists(input_file):
            with open(input_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    url = row.get('URL', '')
                    filename = row.get('FileName', '') # e.g., "MySearch.csv"
                    emails = row.get('Send to Emails', '')
                    
                    if url and filename:
                         # Strip .csv for display in the 'Search Name' column
                         search_name_display = filename.replace('.csv', '')
                         
                         # Treeview column order: ('Search Name', 'Emails', 'URL')
                         self.tree.insert('', tk.END, values=(search_name_display, emails, url))

    def save_urls_to_csv(self):
        """Saves all current Treeview data to inputURLS.csv."""
        input_file = os.path.join(self.data_dir, 'inputURLS.csv')
        
        final_data = []
        for item in self.tree.get_children():
            # Treeview items are read in the order they were specified in 'columns'
            # ('Search Name', 'Emails', 'URL')
            search_name_display, emails, url = self.tree.item(item)['values']
            
            # Reconstruct the filename needed for the scraper
            filename = f"{search_name_display}.csv"
            
            final_data.append({
                'URL': url,
                'FileName': filename,
                'Send to Emails': emails 
            })

        fieldnames = ['URL', 'FileName', 'Send to Emails']
        
        # --- THIS IS THE CRITICAL LINE THAT MUST BE CORRECTED ---
        # Note the 'encoding="utf-8"' argument here.
        with open(input_file, 'w', newline='', encoding='utf-8') as f:
             writer = csv.DictWriter(f, fieldnames=fieldnames)
             writer.writeheader()
             writer.writerows(final_data)
        
        logging.info("URL list saved to inputURLS.csv.")


    # --- Scheduler/Threading Methods ---

    def check_schedule_status_log(self):
        # Implementation remains the same
        if not self.scheduler_running:
            logging.info("ℹ️ Scheduler is currently STOPPED.")
            return

        import schedule
        
        jobs = schedule.get_jobs()
        if not jobs:
            logging.warning("⚠️ Scheduler is RUNNING, but no jobs are currently registered.")
            return

        logging.info(f"✅ Scheduler is RUNNING with {len(jobs)} registered job(s):")
        
        for job in jobs:
            next_run_time = job.next_run.strftime("%Y-%m-%d %H:%M:%S") if job.next_run else "N/A"
            
            logging.info(f"   - Job Function: {job.job_func.__name__}")
            logging.info(f"   - Run Interval: {job.interval} {job.unit} (every {job.unit})")
            logging.info(f"   - Next Run Time: {next_run_time}")
            logging.info("   -----------------------------------")


    def start_schedule(self):
        # Implementation remains the same
        time_str = self.time_entry.get().strip()
        
        if not re.match(r'^\d{2}:\d{2}$', time_str):
            messagebox.showerror("Error", "Please enter the time in HH:MM format (e.g., 08:30).")
            return
            
        self.update_schedule_status("STARTING...", "orange", start_enabled=False, stop_enabled=False)

        threading.Thread(target=self._start_schedule_thread, args=(time_str,), daemon=True).start()

    def _start_schedule_thread(self, time_str):
        # Implementation remains the same
        try:
            if self.scheduler.start(time_str):
                self.root.after(0, lambda: self.update_schedule_status(
                    f"RUNNING daily at {time_str}", "green", start_enabled=False
                ))
                self.scheduler_running = True
            else:
                self.root.after(0, lambda: self.update_schedule_status(
                    f"ERROR starting at {time_str}", "red", stop_enabled=False
                ))
        except Exception as e:
            self.root.after(0, lambda: logging.error(f"Scheduler startup failed: {e}"))
            self.root.after(0, lambda: self.update_schedule_status(
                "ERROR", "red", stop_enabled=False
            ))

    def stop_schedule(self):
        # Implementation remains the same
        self.scheduler.stop()
        self.scheduler_running = False
        self.update_schedule_status("STOPPED", "red", stop_enabled=False)
        
    def update_schedule_status(self, status_text, color, start_enabled=True, stop_enabled=True):
        # Implementation remains the same
        self.schedule_status_label.config(text=f"Status: {status_text}", foreground=color)
        
        if start_enabled:
            self.start_schedule_btn.config(state=tk.NORMAL)
        else:
            self.start_schedule_btn.config(state=tk.DISABLED)

        if stop_enabled:
            self.stop_schedule_btn.config(state=tk.NORMAL)
        else:
            self.stop_schedule_btn.config(state=tk.DISABLED)

    def start_scraping(self):
        # Implementation remains the same
        if self.scheduler_running:
            messagebox.showwarning("Warning", "The scheduler is currently running. Please stop it first before running an on-demand job.")
            return

        self.log_text.delete(1.0, tk.END)
        self.scraper_thread = threading.Thread(target=self.run_scraper, daemon=True)
        self.scraper_thread.start()
    
    def run_scraper(self):
        # Implementation remains the same
        try:
            self.root.after(0, lambda: logging.info("Starting on-demand scraper run..."))
            
            scraper_succeeded = scraper_main_job() 
            
            if scraper_succeeded:
                log_msg = "\nScraping completed successfully!\n"
            else:
                log_msg = "\nScraping failed! Check logs for details.\n"
                
            self.root.after(0, lambda: logging.info(log_msg))
            self.root.after(0, self.refresh_file_view)
            
        except Exception as e:
            self.root.after(0, lambda: logging.error(f"\nCritical Error during on-demand run: {str(e)}\n"))
            

    def on_closing(self):
        # Implementation remains the same
        if self.scheduler_running:
            logging.info("Stopping scheduler before exit...")
            self.scheduler.stop()
        
        time.sleep(0.5) 
        
        self.root.quit()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ImotScraperGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()