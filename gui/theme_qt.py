"""
gui/theme_qt.py
===============
Design tokens and QSS stylesheet for the PyQt6 dark theme.
No imports from other project modules — pure presentation layer.

Usage
-----
    from gui.theme_qt import AppTheme, build_stylesheet

    app = QApplication(sys.argv)
    app.setStyleSheet(build_stylesheet())
"""

from __future__ import annotations


# ── Design tokens ─────────────────────────────────────────────────────────────

class AppTheme:
    """Immutable design-token namespace."""

    # ── Background layers ─────────────────────────────────────────────────────
    BG        = "#1e1e1e"   # main window background
    BG2       = "#2b2b2b"   # panel / entry / table fill
    BG3       = "#333333"   # subtle borders / header fill

    # ── Text ──────────────────────────────────────────────────────────────────
    FG        = "#e8e8e8"   # primary text
    FG_DIM    = "#888888"   # secondary / placeholder text
    FG_WHITE  = "#ffffff"   # text on coloured backgrounds

    # ── Accent ────────────────────────────────────────────────────────────────
    ACCENT    = "#0d7aff"   # selection / hover highlight

    # ── Status colours ────────────────────────────────────────────────────────
    GREEN     = "#4caf50"
    ORANGE    = "#ff8c42"
    YELLOW    = "#f0c040"   # "running" indicator

    # ── Button accent colours ─────────────────────────────────────────────────
    BTN_GREEN     = "#2e7d32"
    BTN_GREEN_H   = "#388e3c"
    BTN_RED       = "#b71c1c"
    BTN_RED_H     = "#c62828"
    BTN_PURPLE    = "#6a1b9a"
    BTN_PURPLE_H  = "#7b1fa2"
    BTN_DEFAULT   = "#3a3a3a"
    BTN_DEFAULT_H = "#4a4a4a"

    # ── Feed row backgrounds ──────────────────────────────────────────────────
    FEED_NEW_BG     = "#1e5c1a"   # new listing      — dark green, visible on dark bg
    FEED_CHANGED_BG = "#5c4a00"   # price change     — dark amber
    FEED_DELETED_BG = "#5c1a1a"   # inactive         — dark red

    # ── Typography ────────────────────────────────────────────────────────────
    FONT_FAMILY = "Segoe UI"
    FONT_SIZE   = 9       # pt  — base size
    FONT_LG     = 11      # pt  — section headings
    FONT_SM     = 8       # pt  — secondary labels

    # ── Spacing ───────────────────────────────────────────────────────────────
    PAD    = 10   # standard outer padding (px)
    PAD_SM = 5    # small padding
    RADIUS = 8    # border-radius for buttons (px)
    ROW_H  = 26   # default table row height (px)


# ── QSS stylesheet ────────────────────────────────────────────────────────────

def build_stylesheet() -> str:
    """Return the full application QSS stylesheet."""
    t = AppTheme
    r = t.RADIUS
    fs = t.FONT_SIZE

    return f"""
/* ── Global ──────────────────────────────────────────────────────────────── */
QWidget {{
    background-color: {t.BG};
    color: {t.FG};
    font-family: "{t.FONT_FAMILY}";
    font-size: {fs}pt;
}}

QMainWindow, QDialog {{
    background-color: {t.BG};
}}

/* ── Group boxes / labels ─────────────────────────────────────────────────── */
QGroupBox {{
    border: 1px solid {t.BG3};
    border-radius: 4px;
    margin-top: 8px;
    font-weight: bold;
    color: {t.FG_DIM};
    font-size: {fs}pt;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    left: 8px;
}}

QLabel {{
    background: transparent;
    color: {t.FG};
}}

/* ── Inputs ───────────────────────────────────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {t.BG2};
    color: {t.FG};
    border: 1px solid {t.BG3};
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: {t.ACCENT};
}}
QLineEdit:focus, QTextEdit:focus {{
    border: 1px solid {t.ACCENT};
}}
QLineEdit:disabled {{
    color: {t.FG_DIM};
    background-color: {t.BG};
    border-color: {t.BG3};
}}

/* ── ComboBox ─────────────────────────────────────────────────────────────── */
QComboBox {{
    background-color: {t.BG2};
    color: {t.FG};
    border: 1px solid {t.BG3};
    border-radius: 4px;
    padding: 4px 6px;
}}
QComboBox::drop-down {{
    border: none;
}}
QComboBox QAbstractItemView {{
    background-color: {t.BG2};
    color: {t.FG};
    selection-background-color: {t.ACCENT};
}}

/* ── Buttons (default) ────────────────────────────────────────────────────── */
QPushButton {{
    background-color: {t.BTN_DEFAULT};
    color: {t.FG_WHITE};
    border: none;
    border-radius: {r}px;
    padding: 6px 14px;
    font-weight: bold;
    font-size: {fs}pt;
}}
QPushButton:hover {{
    background-color: {t.BTN_DEFAULT_H};
}}
QPushButton:pressed {{
    background-color: {t.BG3};
}}
QPushButton:disabled {{
    background-color: {t.BG2};
    color: {t.FG_DIM};
}}

/* ── Named button variants ────────────────────────────────────────────────── */
QPushButton[btnStyle="green"] {{
    background-color: {t.BTN_GREEN};
}}
QPushButton[btnStyle="green"]:hover {{
    background-color: {t.BTN_GREEN_H};
}}

QPushButton[btnStyle="red"] {{
    background-color: {t.BTN_RED};
}}
QPushButton[btnStyle="red"]:hover {{
    background-color: {t.BTN_RED_H};
}}

QPushButton[btnStyle="purple"] {{
    background-color: {t.BTN_PURPLE};
}}
QPushButton[btnStyle="purple"]:hover {{
    background-color: {t.BTN_PURPLE_H};
}}

/* ── Scrollbars (slim dark) ───────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: {t.BG2};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {t.BG3};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t.ACCENT};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background: {t.BG2};
    height: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal {{
    background: {t.BG3};
    border-radius: 4px;
    min-width: 20px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {t.ACCENT};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ── Tables / Tree views ──────────────────────────────────────────────────── */
QTableWidget, QTableView, QTreeView, QListWidget {{
    background-color: {t.BG2};
    alternate-background-color: {t.BG};
    color: {t.FG};
    gridline-color: {t.BG3};
    border: 1px solid {t.BG3};
    border-radius: 0px;
    selection-background-color: {t.ACCENT};
    selection-color: {t.FG_WHITE};
}}
QHeaderView::section {{
    background-color: {t.BG3};
    color: {t.FG};
    padding: 4px 8px;
    border: none;
    border-right: 1px solid {t.BG2};
    font-weight: bold;
}}
QHeaderView::section:hover {{
    background-color: {t.BG2};
}}
QTableWidget::item {{
    /* Absolutely no border, no radius — prevents Qt from rendering a
       button-shaped focus frame around each cell on click */
    border: none;
    border-radius: 0px;
    outline: 0px;
    padding: 2px 6px;
}}
QTableWidget::item:selected {{
    background-color: {t.ACCENT};
    color: {t.FG_WHITE};
    border: none;
    border-radius: 0px;
}}
QListWidget::item {{
    border: none;
    border-radius: 0px;
    padding: 3px 6px;
}}
QListWidget::item:selected {{
    background-color: {t.ACCENT};
    color: {t.FG_WHITE};
}}

/* ── Feed table — row separator via gridline colour ──────────────────────── */
QTableWidget#feedTable {{
    gridline-color: {t.BG};
    border: 1px solid {t.BG3};
    background-color: {t.BG};
}}
QTableWidget#feedTable::item {{
    border: none;
    border-radius: 0px;
    outline: 0px;
    padding: 2px 8px;
}}

/* ── Splitter ─────────────────────────────────────────────────────────────── */
QSplitter::handle {{
    background-color: {t.BG3};
}}
QSplitter::handle:vertical {{
    height: 3px;
}}
QSplitter::handle:horizontal {{
    width: 3px;
}}

/* ── Tool tips ────────────────────────────────────────────────────────────── */
QToolTip {{
    background-color: {t.BG3};
    color: {t.FG};
    border: 1px solid {t.ACCENT};
    padding: 4px;
}}

/* ── Message boxes ────────────────────────────────────────────────────────── */
QMessageBox {{
    background-color: {t.BG};
}}
QMessageBox QLabel {{
    color: {t.FG};
}}
"""


def make_button(parent=None, *, text: str, style: str = "default",
                callback=None, min_width: int = 0) -> "QPushButton":
    """
    Convenience factory: create a styled QPushButton without importing Qt
    everywhere.  style ∈ {"default", "green", "red", "purple"}.

    Import is deferred so this module stays importable even when PyQt6
    is not yet installed (e.g. during spec file analysis).
    """
    from PyQt6.QtWidgets import QPushButton  # local import
    btn = QPushButton(text, parent)
    if style != "default":
        btn.setProperty("btnStyle", style)
        # Force QSS re-evaluation after setting dynamic property
        btn.style().unpolish(btn)
        btn.style().polish(btn)
    if callback:
        btn.clicked.connect(callback)
    if min_width:
        btn.setMinimumWidth(min_width)
    return btn
