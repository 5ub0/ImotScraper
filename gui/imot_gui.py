"""
GUI module for ImotScraper - handles the graphical user interface.
Delegates business logic to the controller module.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import logging
import threading
import queue
import webbrowser
import os
import re
from dotenv import load_dotenv

from gui.theme import AppTheme, apply_theme, RoundedButton

load_dotenv()



class ResultsFeedHandler(logging.Handler):
    """
    Intercepts scraper log lines that announce new listings or price changes
    and converts them into structured dicts posted to a thread-safe queue.

    Line formats emitted by the scraper:
      "New listing: <title> | price: <price> | <link>"
      "Price change: <title> <old> → <new>"

    Everything else is ignored — it continues to the TextHandler via the
    normal logger propagation chain.
    """

    # Patterns must match exactly what imotBgScraper logs
    _RE_NEW     = re.compile(r"New listing: (.+?) \| price: (.+?) \| (https?://\S+)")
    _RE_CHANGED = re.compile(r"Price change: (.+?) (\S+) → (\S+)")

    def __init__(self):
        super().__init__()
        self._queue: queue.Queue = queue.Queue()

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        m = self._RE_NEW.search(msg)
        if m:
            self._queue.put({
                "kind":  "NEW",
                "title": m.group(1).strip(),
                "price": m.group(2).strip(),
                "link":  m.group(3).strip(),
            })
            return
        m = self._RE_CHANGED.search(msg)
        if m:
            self._queue.put({
                "kind":      "CHANGED",
                "title":     m.group(1).strip(),
                "old_price": m.group(2).strip(),
                "price":     m.group(3).strip(),
                "link":      "",
            })


# --- Main GUI Class ---

class ImotScraperGUI:

    # ── Design tokens — sourced from gui.theme so the GUI can reference them
    #    as self.BG, self.FG etc. without importing AppTheme everywhere.
    BG            = AppTheme.BG
    BG2           = AppTheme.BG2
    BG3           = AppTheme.BG3
    FG            = AppTheme.FG
    FG_DIM        = AppTheme.FG_DIM
    ACCENT        = AppTheme.ACCENT
    GREEN         = AppTheme.GREEN
    ORANGE        = AppTheme.ORANGE
    YELLOW        = AppTheme.YELLOW
    BTN_GREEN     = AppTheme.BTN_GREEN
    BTN_GREEN_H   = AppTheme.BTN_GREEN_H
    BTN_RED       = AppTheme.BTN_RED
    BTN_RED_H     = AppTheme.BTN_RED_H
    BTN_PURPLE    = AppTheme.BTN_PURPLE
    BTN_PURPLE_H  = AppTheme.BTN_PURPLE_H
    FEED_NEW_BG     = AppTheme.FEED_NEW_BG
    FEED_CHANGED_BG = AppTheme.FEED_CHANGED_BG
    FEED_DELETED_BG = AppTheme.FEED_DELETED_BG
    FONT   = AppTheme.FONT
    FONT_B = AppTheme.FONT_B
    FONT_LG= AppTheme.FONT_LG

    def __init__(self, root, controller=None):
        self.root = root
        self.root.title("Imot.bg Scraper")
        self.root.geometry("780x850")
        self.root.configure(padx=10, pady=10, bg=self.BG)

        self.controller = controller
        self.urls = []
        self.data_dir = 'data'
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)

        self.scheduler_running = False
        self.file_view_button_frame = None
        self._search_ids = {}  # treeview item_id → DB search id
        self._gallery_win: tk.Toplevel | None = None   # single open gallery window

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        apply_theme(self.root)
        self._set_dark_titlebar(self.root)
        self.setup_gui()

    def _vsb(self, parent, command):
        """Return a slim dark vertical scrollbar."""
        return ttk.Scrollbar(parent, orient="vertical",
                             command=command, style="Slim.Vertical.TScrollbar")

    def _hsb(self, parent, command):
        """Return a slim dark horizontal scrollbar."""
        return ttk.Scrollbar(parent, orient="horizontal",
                             command=command, style="Slim.Horizontal.TScrollbar")

    @staticmethod
    def _set_dark_titlebar(window: tk.Wm) -> None:
        """Apply the Windows dark-mode title bar to *window* (root or Toplevel).

        Uses DWMWA_USE_IMMERSIVE_DARK_MODE (attribute 20) — available on
        Windows 10 build 18985+ and Windows 11.  Silently ignored elsewhere.
        """
        try:
            import ctypes
            import ctypes.wintypes
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
            if hwnd == 0:                       # winfo_id() *is* the HWND on Windows
                hwnd = window.winfo_id()
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
        except Exception:
            pass  # non-Windows or older Windows — ignore silently

    def setup_logging(self):
        """Attaches the feed handler so new/changed scraper events reach the results feed."""
        self._feed_handler = ResultsFeedHandler()
        root_logger = logging.getLogger()
        if not any(isinstance(h, ResultsFeedHandler) for h in root_logger.handlers):
            root_logger.addHandler(self._feed_handler)
        root_logger.setLevel(logging.INFO)
        self._flush_feed()   # start the recurring drain loop

    def _flush_feed(self):
        """Drain the ResultsFeedHandler queue and insert rows into the feed Treeview."""
        try:
            while True:
                try:
                    event = self._feed_handler._queue.get_nowait()
                except queue.Empty:
                    break

                kind  = event["kind"]
                title = event["title"]
                price = event["price"]
                link  = event.get("link", "")

                if kind == "CHANGED":
                    price_display = f"{event['old_price']} → {price}"
                else:
                    price_display = price

                iid = self._feed_tree.insert(
                    "", tk.END,
                    values=(kind, title, price_display),
                    tags=(kind,)
                )
                self._feed_link_map[iid] = link
                self._feed_tree.see(iid)
        except tk.TclError:
            return
        self.root.after(200, self._flush_feed)

    def setup_gui(self):
        
        # -----------------------------------------------
        # --- SCHEDULER CONTROL FRAME ---
        # -----------------------------------------------
        schedule_frame = ttk.LabelFrame(self.root, text="Scheduled Scraping Control", padding=10)
        schedule_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(schedule_frame, text="Daily Time (HH:MM):").grid(row=0, column=0, padx=5, pady=5, sticky='W')
        self.time_entry = ttk.Entry(schedule_frame, width=10)
        self.time_entry.insert(0, "08:00")
        self.time_entry.grid(row=0, column=1, padx=5, pady=5, sticky='W')
        
        self.schedule_status_label = ttk.Label(schedule_frame, text="Status: STOPPED", foreground="red")
        self.schedule_status_label.grid(row=0, column=2, padx=20, pady=5, sticky='W')
        
        self.schedule_btn = ttk.Button(
            schedule_frame, 
            text="Start Daily Schedule", 
            command=self.toggle_schedule
        )
        self.schedule_btn.grid(row=0, column=3, padx=5, pady=5, sticky='E')
        
        schedule_frame.grid_columnconfigure(5, weight=1) 
        
        # Create PanedWindow for adjustable sections
        paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)
        
        # Upper section (URLs and Files)
        upper_section = ttk.Frame(paned)
        
        # URLs List Frame
        urls_frame = ttk.LabelFrame(upper_section, text="Saved Searches", padding=10)
        urls_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Left: treeview — right: action buttons
        list_container = ttk.Frame(urls_frame)
        list_container.pack(fill=tk.BOTH, expand=True)
        list_container.columnconfigure(0, weight=1)
        list_container.rowconfigure(0, weight=1)

        # URLs Treeview with scrollbar — names only
        tree_frame = ttk.Frame(list_container)
        tree_frame.grid(row=0, column=0, sticky='nsew')
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(tree_frame, columns=('Search Name',), show='headings', height=6)
        self.tree.heading('Search Name', text='Search Name')
        self.tree.column('Search Name', stretch=True)

        tree_vsb = self._vsb(tree_frame, self.tree.yview)
        self.tree.configure(yscrollcommand=tree_vsb.set)

        self.tree.grid(row=0, column=0, sticky='nsew')
        tree_vsb.grid(row=0, column=1, sticky='ns')

        # Right: vertical button panel
        btn_panel = ttk.Frame(list_container)
        btn_panel.grid(row=0, column=1, sticky='ns', padx=(8, 0))

        RoundedButton(btn_panel, text="Add New Search",
                      bg=AppTheme.BTN_GREEN, hover_bg=AppTheme.BTN_GREEN_H,
                      command=lambda: self.show_add_url_dialog(action="create")
                      ).pack(fill=tk.X, pady=(0, 4))
        RoundedButton(btn_panel, text="Edit Selected",
                      bg=AppTheme.BTN_DEFAULT, hover_bg=AppTheme.BTN_DEFAULT_H,
                      command=self.edit_selected_url
                      ).pack(fill=tk.X, pady=(0, 4))
        RoundedButton(btn_panel, text="Remove Selected",
                      bg=AppTheme.BTN_RED, hover_bg=AppTheme.BTN_RED_H,
                      command=self.remove_url
                      ).pack(fill=tk.X, pady=(0, 0))

        # Control buttons frame (Run Scraping Now lives here)
        control_frame = ttk.Frame(upper_section)
        control_frame.pack(fill=tk.X, pady=(0, 10))

        self.scrape_btn = RoundedButton(control_frame, text="▶  Run Scraping Now",
                                        bg=AppTheme.BTN_GREEN, hover_bg=AppTheme.BTN_GREEN_H,
                                        command=self.start_scraping)
        self.scrape_btn.pack(side=tk.RIGHT, padx=5)
        
        # File view frame
        file_view_frame = ttk.LabelFrame(upper_section, text="View Search Results", padding=10)
        file_view_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.file_view_button_frame = ttk.Frame(file_view_frame)
        self.file_view_button_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # ── Lower section — scrape feed + raw log ────────────────────────────
        lower_section = ttk.Frame(paned)

        # Status bar: shows which search is running / last run summary
        self._status_bar = tk.Frame(lower_section, bg=self.BG2, pady=6)
        self._status_bar.pack(fill=tk.X)
        self._status_label = tk.Label(
            self._status_bar, text="  No scrape run yet",
            bg=self.BG2, fg=self.FG_DIM,
            font=self.FONT, anchor="w"
        )
        self._status_label.pack(side=tk.LEFT, padx=10)
        self._status_counts = tk.Label(
            self._status_bar, text="",
            bg=self.BG2, fg=self.FG_DIM,
            font=self.FONT_B, anchor="e"
        )
        self._status_counts.pack(side=tk.RIGHT, padx=10)

        # Results feed Treeview
        feed_frame = ttk.LabelFrame(lower_section, text="Scrape Results Feed", padding=(6, 4))
        feed_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 2))
        feed_frame.rowconfigure(0, weight=1)
        feed_frame.columnconfigure(0, weight=1)

        feed_cols = ("kind", "title", "price")
        self._feed_tree = ttk.Treeview(feed_frame, columns=feed_cols,
                                       show="headings", height=8,
                                       style="Feed.Treeview")
        self._feed_tree.heading("kind",  text="Type")
        self._feed_tree.heading("title", text="Title")
        self._feed_tree.heading("price", text="Price")

        self._feed_tree.column("kind",  width=70,  stretch=False, anchor="center")
        self._feed_tree.column("title", width=260, stretch=True)
        self._feed_tree.column("price", width=120, stretch=False, anchor="center")

        self._feed_tree.tag_configure("NEW",
                                      background=self.FEED_NEW_BG,
                                      foreground=AppTheme.FG_WHITE,
                                      font=self.FONT_B)
        self._feed_tree.tag_configure("CHANGED",
                                      background=self.FEED_CHANGED_BG,
                                      foreground=AppTheme.FG_WHITE,
                                      font=self.FONT_B)
        self._feed_tree.tag_configure("DELETED",
                                      background=self.FEED_DELETED_BG,
                                      foreground=AppTheme.FG_WHITE,
                                      font=self.FONT_B)
        self._feed_tree.tag_configure("EMPTY",    foreground=self.FG_DIM)

        feed_vsb = self._vsb(feed_frame, self._feed_tree.yview)
        self._feed_tree.configure(yscrollcommand=feed_vsb.set)
        self._feed_tree.grid(row=0, column=0, sticky="nsew")
        feed_vsb.grid(row=0, column=1, sticky="ns")

        # map feed iid → property link (so we can look up by link on click)
        self._feed_link_map: dict = {}

        self._feed_tree.bind("<Double-1>", self._on_feed_click)

        # Show placeholder until first scrape
        self._show_feed_placeholder()

        paned.add(upper_section, weight=40)
        paned.add(lower_section, weight=60)
        
        self.setup_logging()
        self._load_searches_from_db()
        self.load_file_view_buttons(self.file_view_button_frame)

    # --- Utility and UI Interaction Methods ---

    def view_search_results(self, search_name: str):
        """Opens a window showing all properties for a search directly from the DB."""
        properties = self.controller.get_properties_for_search(search_name) if self.controller else []

        view_window = tk.Toplevel(self.root)
        view_window.title(f"Results: {search_name}")
        view_window.geometry("1200x600")
        view_window.minsize(900, 400)
        view_window.rowconfigure(0, weight=1)
        view_window.columnconfigure(0, weight=1)
        self._set_dark_titlebar(view_window)

        main_frame = ttk.Frame(view_window)
        main_frame.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        main_frame.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)

        columns = ('Status', 'Title', 'Location', 'Price', 'First Seen', 'Last Seen', 'Images', 'Link')
        tree = ttk.Treeview(main_frame, columns=columns, show='headings')

        tree.heading('Status',     text='Status')
        tree.heading('Title',      text='Title')
        tree.heading('Location',   text='Location')
        tree.heading('Price',      text='Current Price')
        tree.heading('First Seen', text='First Seen')
        tree.heading('Last Seen',  text='Last Seen')
        tree.heading('Images',     text='📷')
        tree.heading('Link',       text='Link')

        tree.column('Status',     width=80,  anchor='center')
        tree.column('Title',      width=200)
        tree.column('Location',   width=200)
        tree.column('Price',      width=150)
        tree.column('First Seen', width=140, anchor='center')
        tree.column('Last Seen',  width=140, anchor='center')
        tree.column('Images',     width=50,  anchor='center')
        tree.column('Link',       width=250)

        # Colour-code by status
        tree.tag_configure('Active',   foreground='#ffffff', font=self.FONT_B)
        tree.tag_configure('Inactive', foreground='#ffffff', font=self.FONT_B)

        vsb = self._vsb(main_frame, tree.yview)
        hsb = self._hsb(main_frame, tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')

        # Build rows and keep a mapping: tree item → property dict
        prop_id_map = {}
        for prop in properties:
            ph = self.controller.db.get_price_history(prop["id"]) if self.controller and self.controller.db else []
            current_price = next((r["price"] for r in ph if r["price_status"] == "Current"), "—")

            img_count = len(self.controller.db.get_images(prop["id"])) if self.controller and self.controller.db else 0
            img_label = f"🖼 {img_count}" if img_count else "—"

            iid = tree.insert("", tk.END,
                values=(
                    prop["status"],
                    prop["title"] or "—",
                    prop.get("location") or "—",
                    current_price,
                    prop["first_seen"][:16],
                    prop["last_seen"][:16],
                    img_label,
                    prop["link"] or "—",
                ),
                tags=(prop["status"],)
            )
            prop_id_map[iid] = {**prop, "current_price": current_price}

        def on_click(event):
            item = tree.identify_row(event.y)
            col  = tree.identify_column(event.x)
            if not item:
                return
            values = tree.item(item)["values"]
            # Column #7 = Images, Column #8 = Link
            if col == "#7":
                prop = prop_id_map.get(item)
                if prop is not None:
                    self._open_gallery(view_window, prop)
            elif col == "#8":
                link = values[7]
                if link and link != "—":
                    webbrowser.open(link)

        tree.bind("<Button-1>", on_click)
        # Also open gallery on double-click anywhere on the row
        def on_double_click(event):
            item = tree.identify_row(event.y)
            if not item:
                return
            prop = prop_id_map.get(item)
            if prop is not None:
                self._open_gallery(view_window, prop)
        tree.bind("<Double-1>", on_double_click)

        # Summary label
        active_count   = sum(1 for p in properties if p["status"] == "Active")
        inactive_count = len(properties) - active_count
        ttk.Label(view_window,
                  text=f"Total: {len(properties)}  |  Active: {active_count}  |  Inactive: {inactive_count}  |  Click 📷 count or double-click a row to view images",
                  foreground="gray"
                 ).grid(row=1, column=0, pady=5)

    def _open_gallery(self, parent: tk.Toplevel, prop: dict):
        """Open a simple image gallery window for a property."""
        try:
            from PIL import Image, ImageTk
            import io
            pil_available = True
        except ImportError:
            pil_available = False

        if not self.controller or not self.controller.db:
            return

        property_id = prop["id"]
        title       = prop.get("title") or "—"
        location    = prop.get("location") or "—"
        price       = prop.get("current_price") or "—"
        description = prop.get("description") or "—"

        # Full price history for the inline table (newest first)
        price_history = self.controller.db.get_price_history(property_id)

        images = self.controller.db.get_images(property_id)
        if not images:
            messagebox.showinfo("No Images", f"No images stored for:\n{title}", parent=parent)
            return

        win = tk.Toplevel(parent)
        win.title(f"Gallery — {title}")
        win.geometry("860x780")
        win.resizable(True, True)
        # row 0 = nav bar, row 1 = image, row 2 = info panel
        win.rowconfigure(1, weight=1)
        win.columnconfigure(0, weight=1)

        # ── Single window — close any previously open gallery ─────────────────
        if self._gallery_win and self._gallery_win.winfo_exists():
            self._gallery_win.destroy()
        self._gallery_win = win

        # Dark OS title bar + stay on top
        self._set_dark_titlebar(win)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", lambda: self._close_gallery(win))

        idx = [0]

        # ── Nav bar ──────────────────────────────────────────────────────────
        nav = tk.Frame(win, bg="#2b2b2b", pady=6)
        nav.grid(row=0, column=0, sticky='ew')
        nav.columnconfigure(1, weight=1)

        btn_prev = tk.Button(
            nav, text="◀  Previous", width=12,
            bg="#444", fg="white", activebackground="#666", activeforeground="white",
            relief="flat", bd=0, padx=8, pady=4, cursor="hand2",
            font=("Segoe UI", 9, "bold")
        )
        btn_prev.grid(row=0, column=0, padx=(12, 6), pady=2)

        counter_lbl = tk.Label(nav, text="", bg="#2b2b2b", fg="white",
                               font=("Segoe UI", 10))
        counter_lbl.grid(row=0, column=1)

        btn_next = tk.Button(
            nav, text="Next  ▶", width=12,
            bg="#444", fg="white", activebackground="#666", activeforeground="white",
            relief="flat", bd=0, padx=8, pady=4, cursor="hand2",
            font=("Segoe UI", 9, "bold")
        )
        btn_next.grid(row=0, column=2, padx=(6, 12), pady=2)

        # ── Image area ───────────────────────────────────────────────────────
        img_frame = tk.Frame(win, bg="black")
        img_frame.grid(row=1, column=0, sticky='nsew')
        img_frame.rowconfigure(0, weight=1)
        img_frame.columnconfigure(0, weight=1)

        if pil_available:
            img_label = tk.Label(img_frame, bg="black", anchor='center')
            img_label.grid(row=0, column=0, sticky='nsew')
            img_label._photo = None

            def show(i):
                i = max(0, min(i, len(images) - 1))
                idx[0] = i
                rec = images[i]
                counter_lbl.config(text=f"  {i + 1} / {len(images)}  ")
                btn_prev.config(state=tk.NORMAL if i > 0 else tk.DISABLED,
                                bg="#444" if i > 0 else "#333",
                                fg="white" if i > 0 else "#888")
                btn_next.config(state=tk.NORMAL if i < len(images) - 1 else tk.DISABLED,
                                bg="#444" if i < len(images) - 1 else "#333",
                                fg="white" if i < len(images) - 1 else "#888")

                raw = rec["image_data"]
                pil_img = Image.open(io.BytesIO(raw))

                win.update_idletasks()
                max_w = max(img_frame.winfo_width()  - 4, 400)
                max_h = max(img_frame.winfo_height() - 4, 300)
                pil_img.thumbnail((max_w, max_h), Image.LANCZOS)

                photo = ImageTk.PhotoImage(pil_img)
                img_label._photo = photo
                img_label.config(image=photo)

        else:
            img_label = tk.Text(img_frame, wrap='word', state='disabled',
                                bg="#1e1e1e", fg="#ccc", height=4)
            img_label.grid(row=0, column=0, sticky='nsew')
            tk.Label(img_frame,
                     text="Install Pillow (pip install pillow) to see images.",
                     bg="black", fg="orange").grid(row=1, column=0)

            def show(i):
                i = max(0, min(i, len(images) - 1))
                idx[0] = i
                rec = images[i]
                counter_lbl.config(text=f"  {i + 1} / {len(images)}  ")
                btn_prev.config(state=tk.NORMAL if i > 0 else tk.DISABLED)
                btn_next.config(state=tk.NORMAL if i < len(images) - 1 else tk.DISABLED)
                img_label.config(state='normal')
                img_label.delete('1.0', tk.END)
                img_label.insert(tk.END, rec["url"])
                img_label.config(state='disabled')

        btn_prev.config(command=lambda: show(idx[0] - 1))
        btn_next.config(command=lambda: show(idx[0] + 1))
        win.bind("<Left>",  lambda e: show(idx[0] - 1))
        win.bind("<Right>", lambda e: show(idx[0] + 1))

        # ── Info panel ───────────────────────────────────────────────────────
        info_frame = ttk.Frame(win, padding=(12, 8))
        info_frame.grid(row=2, column=0, sticky='ew')
        info_frame.columnconfigure(1, weight=1)

        def _lbl(row, key, value, wrap=0):
            tk.Label(info_frame, text=key, font=("Segoe UI", 9, "bold"),
                     anchor='nw', justify='left').grid(
                row=row, column=0, sticky='nw', padx=(0, 8), pady=2)
            opts = dict(text=value, anchor='nw', justify='left', wraplength=wrap) if wrap else dict(text=value, anchor='nw', justify='left')
            tk.Label(info_frame, **opts).grid(row=row, column=1, sticky='nw', pady=2)

        # ── Title row — clickable hyperlink ──────────────────────────────────
        tk.Label(info_frame, text="Title:", font=("Segoe UI", 9, "bold"),
                 anchor='nw', justify='left').grid(
            row=0, column=0, sticky='nw', padx=(0, 8), pady=2)
        link_url = prop.get("link") or ""
        title_lbl = tk.Label(
            info_frame, text=title, anchor='nw', justify='left',
            fg=self.ACCENT, cursor="hand2",
            font=("Segoe UI", 9, "underline"), wraplength=500
        )
        title_lbl.grid(row=0, column=1, sticky='nw', pady=2)
        if link_url:
            title_lbl.bind("<Button-1>", lambda e: webbrowser.open(link_url))

        _lbl(1, "Location:", location)
        _lbl(2, "Price:",    price)

        # ── Price history mini-table ──────────────────────────────────────────
        past_prices = [r for r in price_history if r["price_status"] != "Current"]

        if past_prices:
            tk.Label(info_frame, text="Price history:", font=("Segoe UI", 9, "bold"),
                     anchor='nw').grid(row=3, column=0, sticky='nw', padx=(0, 8), pady=2)

            ph_frame = ttk.Frame(info_frame)
            ph_frame.grid(row=3, column=1, sticky='w', pady=2)

            # Match treeview background to the window background
            win_bg = win.cget("bg")
            ph_style = ttk.Style(win)
            ph_style.configure("PriceHistory.Treeview",
                               background=win_bg,
                               fieldbackground=win_bg,
                               rowheight=20)
            ph_style.configure("PriceHistory.Treeview.Heading",
                               background=win_bg,
                               relief="flat")

            visible_rows = min(len(past_prices), 4)
            ph_tree = ttk.Treeview(ph_frame, columns=("date", "price"),
                                   show="headings", height=visible_rows,
                                   style="PriceHistory.Treeview")
            ph_tree.heading("date",  text="Date")
            ph_tree.heading("price", text="Price")
            ph_tree.column("date",  width=130, stretch=False, anchor="center")
            ph_tree.column("price", width=100, stretch=False, anchor="center")

            for rec in past_prices:
                date_str = rec["recorded_at"][:16] if rec.get("recorded_at") else "—"
                ph_tree.insert("", tk.END, values=(date_str, rec["price"]))

            ph_tree.grid(row=0, column=0, sticky='ew')

            if len(past_prices) > 4:
                ph_vsb = self._vsb(ph_frame, ph_tree.yview)
                ph_tree.configure(yscrollcommand=ph_vsb.set)
                ph_vsb.grid(row=0, column=1, sticky='ns')

        # ── Description ──────────────────────────────────────────────────────
        tk.Label(info_frame, text="Description:", font=("Segoe UI", 9, "bold"),
                 anchor='nw').grid(row=4, column=0, sticky='nw', padx=(0, 8), pady=2)
        desc_box = tk.Text(info_frame, height=10, wrap='word',
                           relief='flat', bg=win.cget('bg'),
                           font=("Segoe UI", 9), state='normal')
        desc_box.insert('1.0', description)
        desc_box.config(state='disabled')
        desc_box.grid(row=4, column=1, sticky='ew', pady=2)

        show(0)
        win.focus_set()

    def _show_feed_placeholder(self):
        """Insert a dim placeholder row when the feed has no results."""
        self._feed_tree.insert("", tk.END,
                               values=("", "Run a scrape to see new listings and price changes here.", ""),
                               tags=("EMPTY",))

    def _on_feed_click(self, event):
        """Handle double-clicks on the scrape results feed to open the gallery."""
        item = self._feed_tree.identify_row(event.y)
        if not item:
            return
        # Ignore the placeholder row
        if "EMPTY" in self._feed_tree.item(item)["tags"]:
            return
        self._open_gallery_for_feed_item(item)

    def _open_gallery_for_feed_item(self, iid: str):
        """Look up property by link stored in _feed_link_map and open the gallery."""
        if not self.controller or not self.controller.db:
            return
        link = self._feed_link_map.get(iid, "")
        if not link:
            messagebox.showinfo("No link", "No URL stored for this entry.", parent=self.root)
            return
        prop = self.controller.db.get_property_by_link(link)
        if not prop:
            messagebox.showinfo("Not found",
                                "Property details not in the database yet.\n"
                                "It may still be saving — try again in a moment.",
                                parent=self.root)
            return
        ph = self.controller.db.get_price_history(prop["id"])
        prop["current_price"] = next(
            (r["price"] for r in ph if r["price_status"] == "Current"), "—"
        )
        self._open_gallery(self.root, prop)

    def _close_gallery(self, win: tk.Toplevel) -> None:
        """Destroy the gallery window and clear the singleton reference."""
        if self._gallery_win is win:
            self._gallery_win = None
        win.destroy()

    def load_file_view_buttons(self, button_frame):
        """Rebuild the 'View Results' buttons from the searches in the DB."""
        for widget in button_frame.winfo_children():
            widget.destroy()

        if not self.controller:
            return

        searches = self.controller.get_all_searches()
        for s in searches:
            RoundedButton(
                button_frame,
                text=f"Properties: {s['search_name']}",
                bg=AppTheme.BTN_PURPLE, hover_bg=AppTheme.BTN_PURPLE_H,
                command=lambda name=s['search_name']: self.view_search_results(name),
            ).pack(side=tk.LEFT, padx=5, pady=5)

    def refresh_file_view(self):
        """Reloads the result-view buttons and the search treeview from the DB."""
        self.load_file_view_buttons(self.file_view_button_frame)
        self._load_searches_from_db()

    def edit_selected_url(self):
        """Handles the 'Edit Selected' button click."""
        selected_items = self.tree.selection()
        if not selected_items:
            messagebox.showwarning("Warning", "Please select a search to edit.")
            return

        item_id = selected_items[0]
        search_name = self.tree.item(item_id)['values'][0]

        # Look up the full record from the DB (treeview now shows names only)
        search_record = None
        if self.controller:
            for s in self.controller.get_all_searches():
                if s['search_name'] == search_name:
                    search_record = s
                    break

        if not search_record:
            messagebox.showerror("Error", f"Could not find search '{search_name}' in database.")
            return

        emails = search_record.get('emails', '').split(';') if search_record.get('emails') else ['']

        self.show_add_url_dialog(
            action="edit",
            item_id=item_id,
            url=search_record.get('url', ''),
            search_name=search_name,
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
        self._set_dark_titlebar(dialog)
        
        dialog.rowconfigure(0, weight=1)
        dialog.columnconfigure(0, weight=1)

        main_frame = ttk.Frame(dialog, padding=10)
        main_frame.grid(row=0, column=0, sticky='nsew')
        main_frame.columnconfigure(1, weight=1) 
        
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

        ttk.Label(main_frame, text="Search Name:").grid(row=1, column=0, padx=5, pady=5, sticky='W')
        name_entry = ttk.Entry(main_frame, width=50)
        name_entry.grid(row=1, column=1, padx=5, pady=5, sticky='EW')
        name_entry.insert(0, search_name)
        name_entry.bind('<Control-v>', paste_from_clipboard)
        name_entry.bind('<Command-v>', paste_from_clipboard)
        name_entry.config(state=tk.NORMAL)
        
        email_frame_container = ttk.Frame(main_frame)
        email_frame_container.grid(row=2, column=0, columnspan=2, padx=5, pady=10, sticky='EW')

        # ── "Email notifications – coming soon" banner ────────────────────
        ttk.Label(
            email_frame_container,
            text="📧  Email notifications — coming soon",
            foreground="gray",
            font=("Segoe UI", 9, "italic"),
        ).pack(anchor='w', padx=4, pady=(0, 4))

        email_canvas = tk.Canvas(email_frame_container, height=100)
        email_canvas.pack(side="left", fill="both", expand=True)
        
        email_scrollbar = self._vsb(email_frame_container, email_canvas.yview)
        email_scrollbar.pack(side="right", fill="y")
        
        email_canvas.configure(yscrollcommand=email_scrollbar.set)
        
        email_frame = ttk.Frame(email_canvas, padding=5)
        email_canvas.create_window((0, 0), window=email_frame, anchor="nw", tags="email_frame")
        
        email_frame.bind("<Configure>", lambda e: email_canvas.configure(scrollregion = email_canvas.bbox("all")))
        email_frame_container.bind("<Configure>", lambda e: email_canvas.itemconfig("email_frame", width=e.width))
        
        email_frame.columnconfigure(1, weight=1)

        self.email_entries = []
        
        add_email_btn = ttk.Button(email_frame, text="+ Add Email",
                                   command=lambda: add_email_field(),
                                   state='disabled')  # not active yet
        
        def add_email_field(event=None, email_value=""):
            """Adds a new email entry field and binds paste."""
            row_num = len(self.email_entries)
            
            ttk.Label(email_frame, text=f"Email {row_num + 1}:",
                      foreground="gray").grid(row=row_num, column=0, padx=5, pady=2, sticky='W')
            
            email_entry = ttk.Entry(email_frame, width=40)
            email_entry.grid(row=row_num, column=1, padx=5, pady=2, sticky='EW')
            email_entry.insert(0, email_value)
            email_entry.config(state='disabled')  # greyed out — not active yet
            
            self.email_entries.append(email_entry)
            
            add_email_btn.grid(row=row_num + 1, column=0, columnspan=2, pady=5, sticky='W')
            
            email_frame.update_idletasks()
            email_canvas.configure(scrollregion=email_canvas.bbox("all"))

        for email in emails:
            add_email_field(email_value=email)
        
        if not self.email_entries:
             add_email_field(email_value="")
        
        save_btn = RoundedButton(main_frame,
                                 text="Save Changes" if action == "edit" else "Save Search",
                                 bg=AppTheme.BTN_GREEN, hover_bg=AppTheme.BTN_GREEN_H,
                                 command=lambda: self._save_dialog_data(dialog, url_entry.get(), name_entry.get(), item_id))
        save_btn.grid(row=3, column=1, pady=10, sticky='E')
        
        dialog.grab_set() 
        self.root.wait_window(dialog) 

    def _save_dialog_data(self, dialog, url, search_name_display, item_id=None):
        """Processes and saves data from the Add/Edit Search dialog."""

        url = url.strip()
        search_name_display = search_name_display.strip()

        if not url or not search_name_display:
            messagebox.showerror("Error", "URL and Search Name are required.", parent=dialog)
            return

        if not (url.startswith("http://") or url.startswith("https://")):
            messagebox.showerror(
                "Invalid URL",
                "URL must start with http:// or https://\n\nPlease check that you pasted the URL into the URL field.",
                parent=dialog,
            )
            return

        emails = []
        for entry in self.email_entries:
            email = entry.get().strip()
            if email:
                if re.match(r"[^@]+@[^@]+\.[^@]+", email):
                    emails.append(email)
                else:
                    messagebox.showerror("Error", f"Invalid email format: {email}", parent=dialog)
                    return

        email_string = ";".join(emails)

        if not self.controller:
            messagebox.showerror("Error", "Controller not available.", parent=dialog)
            return

        if item_id:
            # Edit: look up the DB id stored in our mapping
            search_id = self._search_ids.get(item_id)
            if search_id is None:
                messagebox.showerror("Error", "Cannot find DB record to update.", parent=dialog)
                return
            self.controller.update_search(search_id, search_name_display, url, email_string)
            logging.info(f"Updated search: {search_name_display}")
        else:
            self.controller.add_search(search_name_display, url, email_string)
            logging.info(f"Added new search: {search_name_display}")

        self.refresh_file_view()
        dialog.destroy()

    # --- CRUD Operations ---

    def remove_url(self):
        selected_items = self.tree.selection()
        if not selected_items:
            return

        if not self.controller:
            messagebox.showerror("Error", "Controller not available.")
            return

        for item in selected_items:
            search_id = self._search_ids.get(item)
            if search_id is not None:
                self.controller.delete_search(search_id)
                logging.info(f"Deleted search id={search_id} from DB.")
            self.tree.delete(item)
            self._search_ids.pop(item, None)

        self.load_file_view_buttons(self.file_view_button_frame)

    def _load_searches_from_db(self):
        """Load all searches from the DB into the treeview."""
        self._search_ids.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        if self.controller:
            for s in self.controller.get_all_searches():
                item_id = self.tree.insert('', tk.END,
                    values=(s['search_name'],))
                self._search_ids[item_id] = s['id']

    def load_existing_urls(self):
        """Backward-compat shim – delegates to _load_searches_from_db."""
        self._load_searches_from_db()

    def save_urls_to_csv(self):
        """No-op: persistence is now handled by the database."""
        pass

    def toggle_schedule(self):
        """Checks the current scheduler status and toggles the action (Start/Stop)."""
        if self.scheduler_running:
            self.stop_schedule()
        else:
            self.start_schedule()

    def start_schedule(self):
        """Starts the daily scheduled scraper job in a background thread."""
        time_str = self.time_entry.get().strip()
        
        if not re.match(r'^\d{2}:\d{2}$', time_str):
            messagebox.showerror("Error", "Please enter the time in HH:MM format (e.g., 08:30).")
            self.update_schedule_status("STOPPED", "red", button_text="Start Daily Schedule") 
            return
            
        self.update_schedule_status("STARTING...", "orange", button_text="Starting...")

        threading.Thread(target=self._start_schedule_thread, args=(time_str,), daemon=True).start()

    def _start_schedule_thread(self, time_str):
        """Internal worker function to start the scheduler in a background thread."""
        try:
            if self.controller and self.controller.schedule_scraper(time_str):
                self.root.after(0, lambda: self.update_schedule_status(
                    f"RUNNING daily at {time_str}", "green", button_text="Stop Schedule"
                ))
                self.scheduler_running = True
            else:
                self.root.after(0, lambda: self.update_schedule_status(
                    f"ERROR starting at {time_str}", "red", button_text="Start Daily Schedule"
                ))
        except Exception as e:
            self.root.after(0, lambda: logging.error(f"Scheduler startup failed: {e}"))
            self.root.after(0, lambda: self.update_schedule_status(
                "ERROR", "red", button_text="Start Daily Schedule"
            ))

    def stop_schedule(self):
        """Stops the daily scheduled scraper job."""
        
        if self.controller:
            self.controller.stop_scheduler()
        self.scheduler_running = False
        self.update_schedule_status("STOPPED", "red", button_text="Start Daily Schedule")
        
    def update_schedule_status(self, status_text, color, button_text=None):
        """Updates the scheduler status label and button text/state."""
        self.schedule_status_label.config(text=f"Status: {status_text}", foreground=color)
        
        if button_text:
            self.schedule_btn.config(text=button_text)

        if status_text == "STARTING...":
            self.schedule_btn.config(state=tk.DISABLED)
        else:
            self.schedule_btn.config(state=tk.NORMAL)

    def start_scraping(self):
        """Starts the scraping process in a new thread immediately."""
        if self.scheduler_running:
            messagebox.showwarning("Warning", "The scheduler is currently running. Please stop it first before running an on-demand job.")
            return

        if self.controller:
            searches = self.controller.get_all_searches()
            if not searches:
                messagebox.showwarning(
                    "No Searches",
                    "No searches are configured.\nPlease add at least one search URL via 'Add New Search' first."
                )
                return

        # Clear the feed and reset status bar
        self._feed_tree.delete(*self._feed_tree.get_children())
        self._feed_link_map.clear()
        names = "  |  ".join(s["search_name"] for s in searches)
        self._status_label.config(
            text=f"  ⏳  Running: {names}", fg=self.YELLOW
        )
        self._status_counts.config(text="")

        logging.info(f"Starting scraper for {len(searches)} search(es)...")
        self.scrape_btn.config(state=tk.DISABLED, text="Scraping...")
        self.scraper_thread = threading.Thread(target=self.run_scraper, daemon=True)
        self.scraper_thread.start()
        self.root.after(100, self._check_scraper_thread)

    def _check_scraper_thread(self):
        """Poll the scraper thread; re-enable the button when it finishes."""
        if self.scraper_thread.is_alive():
            self.root.after(500, self._check_scraper_thread)
        else:
            self.scrape_btn.config(state=tk.NORMAL, text="▶  Run Scraping Now")

    def run_scraper(self):
        """Executes the scraper job (on-demand)."""
        import traceback as _tb
        try:
            self.root.after(0, lambda: logging.info("Starting on-demand scraper run..."))

            if not self.controller:
                error_msg = "Controller not initialized - cannot run scraper"
                self.root.after(0, lambda: logging.error(error_msg))
                return

            success = self.controller.run_scraper()

            def finalize_scraper_run(result):
                try:
                    self.controller.send_email_reports(result)
                    # Count feed rows for the summary
                    rows  = self._feed_tree.get_children()
                    tags  = [self._feed_tree.item(r)["tags"][0] for r in rows]
                    n_new = tags.count("NEW")
                    n_chg = tags.count("CHANGED")
                    summary = f"✔  {n_new} new  |  {n_chg} changed"
                    self._status_label.config(text="  Last run finished", fg=self.FG_DIM)
                    self._status_counts.config(text=f"{summary}  ")
                    if n_new == 0 and n_chg == 0:
                        self._show_feed_placeholder()
                    logging.info(f"Scraping completed — {n_new} new, {n_chg} changed.")
                    self.refresh_file_view()
                except Exception as e:
                    logging.error(f"Error finalizing scraper run: {e}")
                    _tb.print_exc()

            self.root.after(0, lambda: finalize_scraper_run(success))

        except Exception as e:
            error_msg = f"Critical Error during on-demand run: {str(e)}"
            self.root.after(0, lambda msg=error_msg: logging.error(msg))
            _tb.print_exc()
            

    def on_closing(self):
        """Handle window closing event: ensures scheduler thread is terminated."""
        if self.scheduler_running:
            logging.info("Stopping scheduler before exit...")
            if self.controller:
                self.controller.stop_scheduler()

        self.root.after(500, self.root.destroy)


def main(controller=None):
    root = tk.Tk()
    app = ImotScraperGUI(root, controller=controller)
    root.mainloop()

if __name__ == "__main__":
    main()