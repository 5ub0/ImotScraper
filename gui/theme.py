"""
gui/theme.py
============
All visual design tokens (colours, fonts, spacing) and ttk style setup for
ImotScraper.  Nothing in this file imports from other project modules —
it is a pure presentation layer that any GUI component can import safely.

Usage
-----
    from gui.theme import AppTheme, apply_theme, RoundedButton

    apply_theme(root)            # call once after root is created
    btn = RoundedButton(parent, text="Click me", command=...,
                        bg=AppTheme.BTN_GREEN, hover_bg=AppTheme.BTN_GREEN_H)
"""

import tkinter as tk
from tkinter import ttk


# ── Design tokens ─────────────────────────────────────────────────────────────

class AppTheme:
    """Immutable design-token namespace.  Import and reference as AppTheme.BG etc."""

    # ── Background layers ─────────────────────────────────────────────────────
    BG        = "#1e1e1e"   # main window background
    BG2       = "#2b2b2b"   # panel / entry / treeview fill
    BG3       = "#333333"   # subtle borders, button default bg

    # ── Text ──────────────────────────────────────────────────────────────────
    FG        = "#e8e8e8"   # primary text
    FG_DIM    = "#888888"   # secondary / placeholder text
    FG_WHITE  = "#ffffff"   # text on coloured buttons

    # ── Accent ────────────────────────────────────────────────────────────────
    ACCENT    = "#0d7aff"   # selection highlight, scrollbar hover

    # ── Status / feed colours ─────────────────────────────────────────────────
    GREEN     = "#4caf50"   # generic green indicator
    ORANGE    = "#ff8c42"   # generic orange indicator
    YELLOW    = "#f0c040"   # "running" status label

    # ── Button accent colours ─────────────────────────────────────────────────
    BTN_GREEN   = "#2e7d32"   # Add New Search, Run Scraping Now
    BTN_GREEN_H = "#388e3c"   # hover / active
    BTN_RED     = "#b71c1c"   # Remove Selected
    BTN_RED_H   = "#c62828"
    BTN_PURPLE  = "#6a1b9a"   # View:[name]
    BTN_PURPLE_H= "#7b1fa2"
    BTN_DEFAULT = "#3a3a3a"   # Edit Selected, neutral actions
    BTN_DEFAULT_H = "#4a4a4a"

    # ── Feed row backgrounds ──────────────────────────────────────────────────
    FEED_NEW_BG     = "#4da044"   # NEW listing  — green
    FEED_CHANGED_BG = "#bbcf46"   # price change — yellow-green
    FEED_DELETED_BG = "#a85a5a"   # inactive     — muted red
    # foreground for all feed rows is FG_WHITE (#ffffff)

    # ── Typography ────────────────────────────────────────────────────────────
    FONT    = ("Segoe UI", 9)
    FONT_B  = ("Segoe UI", 9,  "bold")
    FONT_LG = ("Segoe UI", 11, "bold")
    FONT_SM = ("Segoe UI", 8)

    # ── Spacing / geometry ────────────────────────────────────────────────────
    PAD      = 10    # standard outer padding (px)
    PAD_SM   = 5     # small padding
    RADIUS   = 10    # rounded-corner radius for RoundedButton / RoundedFrame (px)
    ROW_H    = 26    # default Treeview row height


# ── ttk style setup ───────────────────────────────────────────────────────────

def apply_theme(root: tk.Tk) -> None:
    """
    Apply the dark theme to *root* via ttk.Style.
    Call once, immediately after creating the Tk root window.
    """
    t = AppTheme
    s = ttk.Style(root)
    s.theme_use("clam")

    # ── Global defaults ───────────────────────────────────────────────────────
    s.configure(".",
                background=t.BG, foreground=t.FG,
                font=t.FONT,
                troughcolor=t.BG2, bordercolor=t.BG3,
                darkcolor=t.BG2, lightcolor=t.BG3,
                insertcolor=t.FG, fieldbackground=t.BG2)

    # ── Structural frames ─────────────────────────────────────────────────────
    s.configure("TFrame",      background=t.BG)
    s.configure("TLabelframe", background=t.BG, bordercolor=t.BG3)
    s.configure("TLabelframe.Label",
                background=t.BG, foreground=t.FG_DIM, font=t.FONT_B)

    # ── Labels ────────────────────────────────────────────────────────────────
    s.configure("TLabel", background=t.BG, foreground=t.FG, font=t.FONT)

    # ── Entries ───────────────────────────────────────────────────────────────
    s.configure("TEntry",
                fieldbackground=t.BG2, foreground=t.FG,
                insertcolor=t.FG, bordercolor=t.BG3)

    # ── Buttons (ttk — flat, used for plain unstyled buttons) ─────────────────
    # NOTE: true rounded corners use RoundedButton (canvas-drawn).
    # ttk buttons remain here for dialogs / simple cases.
    s.configure("TButton",
                background=t.BTN_DEFAULT, foreground=t.FG,
                bordercolor=t.BTN_DEFAULT, focusthickness=0,
                relief="flat", padding=(10, 5), font=t.FONT_B)
    s.map("TButton",
          background=[("active", t.ACCENT), ("disabled", t.BG2)],
          foreground=[("disabled", t.FG_DIM)])

    for name, bg, hover in (
        ("Green",  t.BTN_GREEN,  t.BTN_GREEN_H),
        ("Red",    t.BTN_RED,    t.BTN_RED_H),
        ("Purple", t.BTN_PURPLE, t.BTN_PURPLE_H),
    ):
        s.configure(f"{name}.TButton",
                    background=bg, foreground=t.FG_WHITE,
                    bordercolor=bg, focusthickness=0,
                    relief="flat", padding=(10, 5), font=t.FONT_B)
        s.map(f"{name}.TButton",
              background=[("active", hover), ("disabled", t.BG2)],
              foreground=[("disabled", t.FG_DIM)])

    # ── Treeview ──────────────────────────────────────────────────────────────
    s.configure("Treeview",
                background=t.BG2, foreground=t.FG,
                fieldbackground=t.BG2, rowheight=t.ROW_H,
                bordercolor=t.BG3, font=t.FONT)
    s.configure("Treeview.Heading",
                background=t.BG3, foreground=t.FG,
                relief="flat", font=t.FONT_B)
    s.map("Treeview",
          background=[("selected", t.ACCENT)],
          foreground=[("selected", t.FG_WHITE)])
    s.map("Treeview.Heading",
          background=[("active", t.BG3)])

    # Feed treeview — extra row height + darker field background creates
    # a visible 2 px "border" gap between coloured rows
    s.configure("Feed.Treeview",
                background=t.BG2, foreground=t.FG,
                fieldbackground="#1a1a1a",   # darker than BG2 → gap colour
                rowheight=t.ROW_H + 4,       # extra space = gap shows around row
                bordercolor=t.BG3, font=t.FONT)
    s.configure("Feed.Treeview.Heading",
                background=t.BG3, foreground=t.FG,
                relief="flat", font=t.FONT_B)
    s.map("Feed.Treeview",
          background=[("selected", t.ACCENT)],
          foreground=[("selected", t.FG_WHITE)])

    # ── Slim scrollbars ───────────────────────────────────────────────────────
    for orient in ("Vertical", "Horizontal"):
        style_name = f"Slim.{orient}.TScrollbar"
        s.configure(style_name,
                    background=t.BG3, troughcolor=t.BG2,
                    arrowcolor=t.BG3, bordercolor=t.BG2,
                    width=8, arrowsize=0)
        s.map(style_name,
              background=[("active", t.ACCENT), ("pressed", t.ACCENT)])

    # ── PanedWindow ───────────────────────────────────────────────────────────
    s.configure("TPanedwindow", background=t.BG)

    # ── Tk option database (covers plain tk.* widgets and dialogs) ────────────
    root.option_add("*Background",         t.BG)
    root.option_add("*Foreground",         t.FG)
    root.option_add("*Font",               t.FONT)
    root.option_add("*Entry.Background",   t.BG2)
    root.option_add("*Entry.Foreground",   t.FG)
    root.option_add("*Text.Background",    t.BG2)
    root.option_add("*Text.Foreground",    t.FG)
    root.option_add("*Canvas.Background",  t.BG2)


# ── RoundedButton ─────────────────────────────────────────────────────────────

class RoundedButton(tk.Canvas):
    """
    A canvas-drawn button with true rounded corners (like CSS border-radius).

    Parameters
    ----------
    parent      : tk parent widget
    text        : button label
    command     : callable invoked on click
    bg          : normal background colour  (default: AppTheme.BTN_DEFAULT)
    hover_bg    : background on mouse-over  (default: AppTheme.BTN_DEFAULT_H)
    fg          : text colour               (default: AppTheme.FG_WHITE)
    font        : font tuple                (default: AppTheme.FONT_B)
    radius      : corner radius in px       (default: AppTheme.RADIUS)
    width       : canvas width  (0 = auto-size to text)
    height      : canvas height (0 = auto-size to text)
    padx        : horizontal internal padding
    pady        : vertical internal padding
    state       : "normal" | "disabled"
    """

    def __init__(self, parent, *,
                 text: str = "",
                 command=None,
                 bg: str         = AppTheme.BTN_DEFAULT,
                 hover_bg: str   = AppTheme.BTN_DEFAULT_H,
                 disabled_bg: str= AppTheme.BG2,
                 fg: str         = AppTheme.FG_WHITE,
                 disabled_fg: str= AppTheme.FG_DIM,
                 font=None,
                 radius: int     = AppTheme.RADIUS,
                 width: int      = 0,
                 height: int     = 0,
                 padx: int       = 14,
                 pady: int       = 7,
                 state: str      = "normal",
                 **kwargs):

        self._text       = text
        self._command    = command
        self._bg_normal  = bg
        self._bg_hover   = hover_bg
        self._bg_disabled= disabled_bg
        self._fg_normal  = fg
        self._fg_disabled= disabled_fg
        self._font       = font or AppTheme.FONT_B
        self._radius     = radius
        self._padx       = padx
        self._pady       = pady
        self._state      = state   # "normal" | "disabled"
        self._hover      = False

        # Measure text to auto-size the canvas if no explicit size given
        tmp = tk.Label(font=self._font)
        tmp.config(text=self._text)
        tw = tmp.winfo_reqwidth()
        th = tmp.winfo_reqheight()
        tmp.destroy()

        cw = width  or (tw + padx * 2)
        ch = height or (th + pady * 2)

        # Strip conflicting kwargs before passing to Canvas
        kwargs.pop("background", None)
        kwargs.pop("relief", None)
        kwargs.pop("borderwidth", None)
        kwargs.pop("bd", None)

        super().__init__(parent,
                         width=cw, height=ch,
                         background=AppTheme.BG,   # canvas bg = parent bg
                         highlightthickness=0,
                         **kwargs)

        self._draw()

        self.bind("<Enter>",          self._on_enter)
        self.bind("<Leave>",          self._on_leave)
        self.bind("<ButtonPress-1>",  self._on_press)
        self.bind("<ButtonRelease-1>",self._on_release)
        self.bind("<Configure>",      self._on_resize)

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _current_bg(self) -> str:
        if self._state == "disabled":
            return self._bg_disabled
        return self._bg_hover if self._hover else self._bg_normal

    def _current_fg(self) -> str:
        return self._fg_disabled if self._state == "disabled" else self._fg_normal

    def _draw(self) -> None:
        self.delete("all")
        w = self.winfo_width()  or int(self["width"])
        h = self.winfo_height() or int(self["height"])
        r = self._radius

        bg = self._current_bg()
        fg = self._current_fg()

        # Rounded rectangle via two overlapping rectangles + four arcs
        self.create_arc(0,       0,       r*2,   r*2,   start=90,  extent=90,  fill=bg, outline=bg)
        self.create_arc(w-r*2,   0,       w,     r*2,   start=0,   extent=90,  fill=bg, outline=bg)
        self.create_arc(0,       h-r*2,   r*2,   h,     start=180, extent=90,  fill=bg, outline=bg)
        self.create_arc(w-r*2,   h-r*2,   w,     h,     start=270, extent=90,  fill=bg, outline=bg)
        self.create_rectangle(r, 0,       w-r,   h,     fill=bg, outline=bg)
        self.create_rectangle(0, r,       w,     h-r,   fill=bg, outline=bg)

        self.create_text(w // 2, h // 2,
                         text=self._text, fill=fg,
                         font=self._font, anchor="center")

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_enter(self, _event=None):
        if self._state != "disabled":
            self._hover = True
            self._draw()
            self.config(cursor="hand2")

    def _on_leave(self, _event=None):
        self._hover = False
        self._draw()
        self.config(cursor="")

    def _on_press(self, _event=None):
        if self._state != "disabled" and self._command:
            self._command()

    def _on_release(self, _event=None):
        pass   # visual feedback handled by _on_enter / _on_leave

    def _on_resize(self, _event=None):
        self._draw()

    # ── Public API ────────────────────────────────────────────────────────────

    def config(self, **kwargs):          # noqa: D401
        """Support .config(text=, state=, command=) like a normal widget."""
        if "text" in kwargs:
            self._text = kwargs.pop("text")
        if "state" in kwargs:
            self._state = kwargs.pop("state")
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if kwargs:
            super().config(**kwargs)
        self._draw()

    # Make .config and .configure equivalent
    configure = config

    def cget(self, key: str):
        if key == "text":   return self._text
        if key == "state":  return self._state
        return super().cget(key)
