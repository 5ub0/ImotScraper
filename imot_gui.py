import tkinter as tk
from tkinter import ttk, scrolledtext
from tkinter import filedialog, messagebox
import logging
from imotBgScraper import main as scraper_main
import threading
import csv
import webbrowser
import os
import re

class CustomText(scrolledtext.ScrolledText):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tag_config("url", foreground="blue", underline=1)
        self.bind("<Button-1>", self._click)
        
    def _click(self, event):
        for tag in self.tag_names("@%d,%d" % (event.x, event.y)):
            if tag == "url":
                # Get the URL from the clicked position
                start = "@%d,%d" % (event.x, event.y)
                url = self.get(f"{start} linestart", f"{start} lineend")
                # Extract URL from the log line using regex
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
        
        # If message contains URL, tag it
        if 'http' in msg:
            line_start = self.text_widget.get("end-2c linestart", "end-2c lineend")
            start_idx = f"end-2c linestart+{line_start.find('http')}c"
            url_end = len(line_start)
            end_idx = f"end-2c linestart+{url_end}c"
            self.text_widget.tag_add("url", start_idx, end_idx)
        
        self.text_widget.see(tk.END)

class ImotScraperGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Imot.bg Scraper")
        self.root.geometry("900x800")
        self.root.configure(padx=10, pady=10)
        
        self.urls = []
        # Create data directory if it doesn't exist
        self.data_dir = 'data'
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
        
        self.setup_gui()
        
    def setup_gui(self):
        # Input Frame
        input_frame = ttk.LabelFrame(self.root, text="Add New URL", padding=10)
        input_frame.pack(fill=tk.X, pady=(0, 10))
        
        # URL input
        ttk.Label(input_frame, text="URL:").grid(row=0, column=0, padx=5, pady=5)
        self.url_entry = ttk.Entry(input_frame, width=70)
        self.url_entry.grid(row=0, column=1, padx=5, pady=5)
        
        # Filename input
        ttk.Label(input_frame, text="Filename:").grid(row=1, column=0, padx=5, pady=5)
        self.filename_entry = ttk.Entry(input_frame, width=70)
        self.filename_entry.grid(row=1, column=1, padx=5, pady=5)
        
        # Add URL button
        add_btn = ttk.Button(input_frame, text="Add URL", command=self.add_url)
        add_btn.grid(row=2, column=1, sticky='E', pady=10)
        
        # Create PanedWindow for adjustable sections
        paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)
        
        # Upper section (URLs and Files)
        upper_section = ttk.Frame(paned)
        
        # URLs List Frame
        urls_frame = ttk.LabelFrame(upper_section, text="URL List", padding=10)
        urls_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # URLs Treeview with scrollbars
        tree_frame = ttk.Frame(urls_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        self.tree = ttk.Treeview(tree_frame, columns=('URL', 'Filename'), show='headings', height=6)
        self.tree.heading('URL', text='URL')
        self.tree.heading('Filename', text='Filename')
        
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
        ttk.Button(control_frame, text="Remove Selected", command=self.remove_url).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Start Scraping", command=self.start_scraping).pack(side=tk.RIGHT, padx=5)
        
        # File view frame
        file_view_frame = ttk.LabelFrame(upper_section, text="View CSV Files", padding=10)
        file_view_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Create a sub-frame for buttons
        button_frame = ttk.Frame(file_view_frame)
        button_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Get filenames from inputURLS.csv
        input_file = os.path.join(self.data_dir, 'inputURLS.csv')
        if os.path.exists(input_file):
            with open(input_file, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    filename = row['FileName']
                    filepath = os.path.join(self.data_dir, filename)
                    if os.path.exists(filepath):
                        ttk.Button(
                            button_frame, 
                            text=f"Open {filename}",
                            command=lambda f=filename: self.view_csv_file(f),
                            width=20
                        ).pack(side=tk.LEFT, padx=5, pady=5)
        
        # Lower section (Log output)
        lower_section = ttk.Frame(paned)
        
        # Log output
        log_frame = ttk.LabelFrame(lower_section, text="Log Output", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_text = CustomText(log_frame, height=15)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # Add both sections to the PanedWindow with weights
        paned.add(upper_section, weight=40)  # 40% of space
        paned.add(lower_section, weight=60)  # 60% of space
        
        # Setup logging to text widget
        self.setup_logging()
        
        # Load existing URLs from inputURLS.csv
        self.load_existing_urls()

    def view_csv_file(self, filename):
        # Ensure we're looking in the data directory
        filepath = os.path.join(self.data_dir, filename)
        if not os.path.exists(filepath):
            messagebox.showerror("Error", f"File not found: {filepath}")
            return
            
        # Create a new window
        view_window = tk.Toplevel(self.root)
        view_window.title(f"Viewing {filename}")
        view_window.geometry("800x600")

        # Create a treeview
        tree = ttk.Treeview(view_window)
        tree.pack(fill=tk.BOTH, expand=True)

        # Add scrollbars
        vsb = ttk.Scrollbar(view_window, orient="vertical", command=tree.yview)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb = ttk.Scrollbar(view_window, orient="horizontal", command=tree.xview)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        try:
            with open(filepath, 'r', encoding='utf-8') as file:
                csv_reader = csv.reader(file)
                # Get headers
                headers = next(csv_reader)
                tree['columns'] = headers
                tree['show'] = 'headings'

                # Set column headings
                for header in headers:
                    tree.heading(header, text=header)
                    tree.column(header, width=100)

                # Add data
                for row in csv_reader:
                    tree.insert("", tk.END, values=row)

        except Exception as e:
            messagebox.showerror("Error", f"Error reading file: {str(e)}")

    def setup_logging(self):        
        handler = TextHandler(self.log_text)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
    
    def add_url(self):
        url = self.url_entry.get().strip()
        filename = self.filename_entry.get().strip()
        
        if url and filename:
            if not filename.endswith('.csv'):
                filename += '.csv'
            self.tree.insert('', tk.END, values=(url, filename))
            self.save_urls_to_csv()
            self.url_entry.delete(0, tk.END)
            self.filename_entry.delete(0, tk.END)
    
    def remove_url(self):
        selected_items = self.tree.selection()
        for item in selected_items:
            self.tree.delete(item)
        if selected_items:
            self.save_urls_to_csv()
    
    def load_existing_urls(self):
        input_file = os.path.join(self.data_dir, 'inputURLS.csv')
        if os.path.exists(input_file):
            with open(input_file, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    self.tree.insert('', tk.END, values=(row['URL'], row['FileName']))

    def save_urls_to_csv(self):
        input_file = os.path.join(self.data_dir, 'inputURLS.csv')
        with open(input_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['URL', 'FileName'])
            for item in self.tree.get_children():
                writer.writerow(self.tree.item(item)['values'])
    
    def start_scraping(self):
        self.log_text.delete(1.0, tk.END)
        threading.Thread(target=self.run_scraper, daemon=True).start()
    
    def run_scraper(self):
        try:
            scraper_main()
            
            def update_log(msg):
                self.log_text.insert(tk.END, msg)
                self.log_text.see(tk.END)            
            self.root.after(0, lambda: update_log("\nScraping completed successfully!\n"))
        except Exception as e:
            self.root.after(0, lambda: update_log(f"\nError occurred: {str(e)}\n"))

def main():
    root = tk.Tk()
    app = ImotScraperGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()