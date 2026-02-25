"""
gui/imot_gui_qt.py
==================
PyQt6 port of ImotScraper's user interface.

Architecture contract (identical to the Tkinter version):
  - GUI never imports from scraper / database / scheduler directly.
  - All business logic is delegated to AppController (self.controller).
  - Background work uses daemon threads; UI updates come back via
    QMetaObject.invokeMethod / Qt signals emitted on the main thread.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import threading
import webbrowser
from typing import Optional

from PyQt6.QtCore import (
    Qt, QSize, QTimer, pyqtSignal, QObject, QRect,
)
from PyQt6.QtGui import (
    QColor, QFont, QBrush, QPixmap, QImage, QIcon, QPainter,
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QSplitter, QGroupBox, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QListWidget, QListWidgetItem,
    QAbstractItemView, QScrollArea,
    QMessageBox, QSizePolicy, QFrame, QTextEdit,
    QDialogButtonBox, QStyledItemDelegate, QStyleOptionViewItem,
)

from gui.theme_qt import AppTheme as T, build_stylesheet, make_button
from dotenv import load_dotenv

load_dotenv()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _set_dark_titlebar(window: QWidget) -> None:
    """Apply Windows 10/11 immersive dark-mode title bar."""
    try:
        import ctypes
        import ctypes.wintypes
        hwnd = int(window.winId())
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value), ctypes.sizeof(value),
        )
    except Exception:
        pass


def _styled_btn(text: str, style: str = "default",
                min_width: int = 130) -> QPushButton:
    btn = QPushButton(text)
    if style != "default":
        btn.setProperty("btnStyle", style)
        btn.style().unpolish(btn)
        btn.style().polish(btn)
    if min_width:
        btn.setMinimumWidth(min_width)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def _bold_font(size: int = T.FONT_SIZE) -> QFont:
    f = QFont(T.FONT_FAMILY, size)
    f.setBold(True)
    return f


def _dim_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {T.FG_DIM};")
    return lbl


# ── Feed row delegate — bypasses QSS so setBackground() colours show ─────────

class _FeedDelegate(QStyledItemDelegate):
    """
    Paints feed table cells using each item's own background/foreground
    colours (set via setBackground / setForeground) without letting
    the global QSS override them.  Also suppresses the focus rectangle
    so no button-shaped frame appears when a cell is clicked.
    """

    def paint(self, painter: QPainter,
              option: QStyleOptionViewItem,
              index) -> None:
        # Suppress focus rectangle — clone the option so we don't mutate the original
        from PyQt6.QtWidgets import QStyle
        option = QStyleOptionViewItem(option)
        option.state = option.state & ~QStyle.StateFlag.State_HasFocus

        bg = index.data(Qt.ItemDataRole.BackgroundRole)
        fg = index.data(Qt.ItemDataRole.ForegroundRole)

        painter.save()

        # Fill background from item data (honours setBackground colour)
        if bg and isinstance(bg, QBrush) and bg.color().isValid():
            painter.fillRect(option.rect, bg)
        else:
            painter.fillRect(option.rect, QColor(T.BG))

        # Draw text with correct foreground
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if fg and isinstance(fg, QBrush):
            painter.setPen(fg.color())
        else:
            painter.setPen(QColor(T.FG_WHITE))

        font = index.data(Qt.ItemDataRole.FontRole)
        if font:
            painter.setFont(font)

        alignment = index.data(Qt.ItemDataRole.TextAlignmentRole)
        if alignment:
            align = Qt.AlignmentFlag(alignment)
        else:
            align = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft

        text_rect = option.rect.adjusted(6, 0, -4, 0)
        painter.drawText(text_rect, align, text)

        painter.restore()


class _ListDelegate(QStyledItemDelegate):
    """
    Suppresses the focus-rect 'rounded button' artefact on QListWidget rows.
    Uses the standard item rendering for everything except the focus indicator.
    """

    def paint(self, painter: QPainter,
              option: QStyleOptionViewItem,
              index) -> None:
        from PyQt6.QtWidgets import QStyle
        option = QStyleOptionViewItem(option)
        option.state = option.state & ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, option, index)


# ── Feed event bridge ─────────────────────────────────────────────────────────

class FeedBridge(QObject):
    """Emits feed events (dict) on the Qt main thread via a signal."""
    event_received = pyqtSignal(dict)


class ResultsFeedHandler(logging.Handler):
    """
    Intercepts scraper log lines and forwards structured dicts to
    a FeedBridge signal (thread-safe — Qt handles cross-thread signals).

    Line formats:
      "New listing: <title> | price: <price> | <link>"
      "Price change: <title> <old> → <new>"
    """

    _RE_NEW     = re.compile(r"New listing: (.+?) \| price: (.+?) \| (https?://\S+)")
    _RE_CHANGED = re.compile(r"Price change: (.+?) (\S+) → (\S+)")

    def __init__(self, bridge: FeedBridge) -> None:
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        m = self._RE_NEW.search(msg)
        if m:
            self._bridge.event_received.emit({
                "kind":  "NEW",
                "title": m.group(1).strip(),
                "price": m.group(2).strip(),
                "link":  m.group(3).strip(),
            })
            return
        m = self._RE_CHANGED.search(msg)
        if m:
            self._bridge.event_received.emit({
                "kind":      "CHANGED",
                "title":     m.group(1).strip(),
                "old_price": m.group(2).strip(),
                "price":     m.group(3).strip(),
                "link":      "",
            })


# ── Add / Edit Search dialog ───────────────────────────────────────────────────

class SearchDialog(QDialog):
    """
    Modal dialog for creating or editing a saved search.
    Email fields are present but disabled (coming soon).
    """

    def __init__(self, parent: QWidget, *,
                 action: str = "create",
                 url: str = "",
                 search_name: str = "",
                 emails: list[str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add New Search" if action == "create" else f"Edit  —  {search_name}")
        self.setMinimumSize(500, 320)
        self.resize(520, 340)
        self.setModal(True)
        _set_dark_titlebar(self)

        self._action = action
        self._emails: list[str] = emails or [""]

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 14, 16, 14)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # URL field (disabled on edit)
        self._url_edit = QLineEdit(url)
        if action == "edit":
            self._url_edit.setReadOnly(True)
            self._url_edit.setStyleSheet(
                f"color: {T.FG_DIM}; background-color: {T.BG}; border-color: {T.BG3};"
            )
        form.addRow("URL:", self._url_edit)

        # Search name
        self._name_edit = QLineEdit(search_name)
        form.addRow("Search Name:", self._name_edit)

        layout.addLayout(form)

        # Email section (disabled — coming soon)
        email_group = QGroupBox("📧  Email notifications  —  coming soon")
        email_group.setStyleSheet(f"color: {T.FG_DIM}; font-style: italic;")
        email_layout = QVBoxLayout(email_group)
        email_layout.setSpacing(4)

        self._email_edits: list[QLineEdit] = []
        for addr in self._emails:
            row = QHBoxLayout()
            lbl = QLabel(f"Email {len(self._email_edits) + 1}:")
            lbl.setStyleSheet(f"color: {T.FG_DIM};")
            edit = QLineEdit(addr)
            edit.setEnabled(False)
            row.addWidget(lbl)
            row.addWidget(edit)
            email_layout.addLayout(row)
            self._email_edits.append(edit)

        layout.addWidget(email_group)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        self._save_btn = _styled_btn(
            "Save Changes" if action == "edit" else "Save Search",
            style="green", min_width=120,
        )
        self._save_btn.clicked.connect(self.accept)
        cancel = _styled_btn("Cancel", min_width=80)
        cancel.clicked.connect(self.reject)
        btn_box.addWidget(cancel)
        btn_box.addWidget(self._save_btn)
        layout.addLayout(btn_box)

    # ── Public accessors ──────────────────────────────────────────────────────

    def get_url(self) -> str:
        return self._url_edit.text().strip()

    def get_search_name(self) -> str:
        return self._name_edit.text().strip()

    def get_emails(self) -> list[str]:
        return [e.text().strip() for e in self._email_edits if e.text().strip()]


# ── Gallery window ─────────────────────────────────────────────────────────────

class GalleryWindow(QDialog):
    """
    Image gallery for a single property.
    Shows nav buttons, full-size image, info panel, price history, description.
    """

    def __init__(self, parent: QWidget, prop: dict, controller) -> None:
        super().__init__(parent)
        title_text = prop.get("title") or "—"
        self.setWindowTitle(f"Gallery  —  {title_text}")
        self.resize(880, 800)
        self.setMinimumSize(600, 500)
        _set_dark_titlebar(self)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )

        self._prop       = prop
        self._controller = controller
        self._images     = []
        self._idx        = 0
        self._pixmap_cache: dict[int, QPixmap] = {}

        # Load data
        db = controller.db if controller else None
        if db:
            self._images       = db.get_images(prop["id"])
            self._price_history = db.get_price_history(prop["id"])
        else:
            self._price_history = []

        self._build_ui(prop, title_text)
        self._show_image(0)

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self, prop: dict, title_text: str) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Nav bar ──────────────────────────────────────────────────────────
        nav = QWidget()
        nav.setStyleSheet(f"background: {T.BG2};")
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(12, 6, 12, 6)

        self._btn_prev = _styled_btn("◀  Previous", min_width=110)
        self._btn_prev.clicked.connect(lambda: self._show_image(self._idx - 1))

        self._counter_lbl = QLabel("")
        self._counter_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._counter_lbl.setStyleSheet(f"color: {T.FG}; font-size: {T.FONT_SIZE + 1}pt;")

        self._btn_next = _styled_btn("Next  ▶", min_width=110)
        self._btn_next.clicked.connect(lambda: self._show_image(self._idx + 1))

        nav_layout.addWidget(self._btn_prev)
        nav_layout.addStretch()
        nav_layout.addWidget(self._counter_lbl)
        nav_layout.addStretch()
        nav_layout.addWidget(self._btn_next)
        root_layout.addWidget(nav)

        # ── Image area ───────────────────────────────────────────────────────
        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setStyleSheet("background: black;")
        self._img_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._img_label.setMinimumHeight(300)
        root_layout.addWidget(self._img_label, stretch=1)

        # ── Info panel (scrollable) ───────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(280)
        scroll.setStyleSheet(f"border: none; background: {T.BG};")

        info = QWidget()
        info.setStyleSheet(f"background: {T.BG};")
        info_layout = QGridLayout(info)
        info_layout.setSpacing(6)
        info_layout.setContentsMargins(14, 10, 14, 10)
        info_layout.setColumnStretch(1, 1)

        row = 0

        # Title as clickable hyperlink
        link_url = prop.get("link") or ""
        title_lbl = QLabel(title_text)
        title_lbl.setFont(_bold_font(T.FONT_SIZE))
        if link_url:
            title_lbl.setStyleSheet(
                f"color: {T.ACCENT}; text-decoration: underline;"
            )
            title_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            title_lbl.mousePressEvent = lambda _e: webbrowser.open(link_url)
        info_layout.addWidget(QLabel("Title:"), row, 0, Qt.AlignmentFlag.AlignTop)
        info_layout.addWidget(title_lbl, row, 1, Qt.AlignmentFlag.AlignTop)
        row += 1

        def _info_row(label: str, value: str) -> None:
            nonlocal row
            lbl = QLabel(label)
            lbl.setFont(_bold_font())
            val = QLabel(value)
            val.setWordWrap(True)
            info_layout.addWidget(lbl, row, 0, Qt.AlignmentFlag.AlignTop)
            info_layout.addWidget(val, row, 1, Qt.AlignmentFlag.AlignTop)
            row += 1

        _info_row("Location:", prop.get("location") or "—")
        _info_row("Price:",    prop.get("current_price") or "—")

        # ── Price history mini-table ──────────────────────────────────────────
        past = [r for r in self._price_history if r["price_status"] != "Current"]
        if past:
            ph_lbl = QLabel("Price history:")
            ph_lbl.setFont(_bold_font())
            ph_table = QTableWidget(min(len(past), 5), 2)
            ph_table.setHorizontalHeaderLabels(["Date", "Price"])
            ph_table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.Stretch
            )
            ph_table.verticalHeader().setVisible(False)
            ph_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            ph_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            ph_table.setMaximumHeight(T.ROW_H * min(len(past), 5) + 28)
            ph_table.setMinimumWidth(260)
            ph_table.setStyleSheet(
                f"background: {T.BG2}; border: 1px solid {T.BG3}; border-radius: 4px;"
            )
            for i, rec in enumerate(past[:5]):
                date_str = (rec["recorded_at"][:16] if rec.get("recorded_at") else "—")
                date_item = QTableWidgetItem(date_str)
                date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                price_item = QTableWidgetItem(rec["price"])
                price_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                ph_table.setItem(i, 0, date_item)
                ph_table.setItem(i, 1, price_item)

            info_layout.addWidget(ph_lbl,    row, 0, Qt.AlignmentFlag.AlignTop)
            info_layout.addWidget(ph_table,  row, 1, Qt.AlignmentFlag.AlignTop)
            row += 1

        # ── Description ───────────────────────────────────────────────────────
        desc_lbl = QLabel("Description:")
        desc_lbl.setFont(_bold_font())
        desc_box = QTextEdit()
        desc_box.setPlainText(prop.get("description") or "—")
        desc_box.setReadOnly(True)
        desc_box.setMaximumHeight(140)
        desc_box.setStyleSheet(
            f"background: {T.BG2}; border: 1px solid {T.BG3}; border-radius: 4px; padding: 4px;"
        )
        info_layout.addWidget(desc_lbl, row, 0, Qt.AlignmentFlag.AlignTop)
        info_layout.addWidget(desc_box, row, 1)

        scroll.setWidget(info)
        root_layout.addWidget(scroll)

        # Keyboard navigation
        self.keyPressEvent = self._key_press  # type: ignore[method-assign]

    # ── Image display ─────────────────────────────────────────────────────────

    def _show_image(self, idx: int) -> None:
        if not self._images:
            self._img_label.setText("No images stored.")
            return

        idx = max(0, min(idx, len(self._images) - 1))
        self._idx = idx

        total = len(self._images)
        self._counter_lbl.setText(f"  {idx + 1} / {total}  ")
        self._btn_prev.setEnabled(idx > 0)
        self._btn_next.setEnabled(idx < total - 1)

        # Use cached pixmap or decode from blob
        if idx not in self._pixmap_cache:
            raw = self._images[idx].get("image_data")
            if raw:
                qimg = QImage.fromData(raw)
                self._pixmap_cache[idx] = QPixmap.fromImage(qimg)
            else:
                self._pixmap_cache[idx] = QPixmap()

        px = self._pixmap_cache[idx]
        if px.isNull():
            self._img_label.setText(
                self._images[idx].get("url", "Image data unavailable")
            )
        else:
            available = self._img_label.size()
            scaled = px.scaled(
                available,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_label.setPixmap(scaled)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        # Re-scale current image on resize
        self._show_image(self._idx)

    def _key_press(self, event) -> None:
        if event.key() == Qt.Key.Key_Left:
            self._show_image(self._idx - 1)
        elif event.key() == Qt.Key.Key_Right:
            self._show_image(self._idx + 1)
        else:
            super().keyPressEvent(event)


# ── Results window ─────────────────────────────────────────────────────────────

class ResultsWindow(QDialog):
    """Shows all properties for a saved search in a sortable table."""

    def __init__(self, parent: QWidget, search_name: str,
                 properties: list[dict], controller) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Results — {search_name}")
        self.resize(1200, 620)
        self.setMinimumSize(900, 400)
        _set_dark_titlebar(self)

        self._controller = controller

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Table
        cols = ["Status", "Title", "Location", "Price",
                "First Seen", "Last Seen", "Images", "Link"]
        self._table = QTableWidget(0, len(cols))
        self._table.setHorizontalHeaderLabels(cols)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # no focus rect on click
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)   # enabled AFTER populate to avoid row scrambling
        self._table.setShowGrid(True)

        # Column widths
        self._table.setColumnWidth(0, 80)
        self._table.setColumnWidth(1, 220)
        self._table.setColumnWidth(2, 180)
        self._table.setColumnWidth(3, 130)
        self._table.setColumnWidth(4, 130)
        self._table.setColumnWidth(5, 130)
        self._table.setColumnWidth(6, 60)

        self._populate(properties, controller)
        layout.addWidget(self._table)

        # Summary bar
        active   = sum(1 for p in properties if p["status"] == "Active")
        inactive = len(properties) - active
        summary_lbl = _dim_label(
            f"Total: {len(properties)}  |  Active: {active}  |  "
            f"Inactive: {inactive}  |  Double-click a row to view gallery"
        )
        layout.addWidget(summary_lbl)

        self._table.cellDoubleClicked.connect(self._on_double_click)
        self._table.cellClicked.connect(self._on_click)

    def _populate(self, properties: list[dict], controller) -> None:
        db = controller.db if controller else None
        self._table.setSortingEnabled(False)   # must stay off while inserting

        for prop in properties:
            ph = db.get_price_history(prop["id"]) if db else []
            current_price = next(
                (r["price"] for r in ph if r["price_status"] == "Current"), "—"
            )
            img_count = len(db.get_images(prop["id"])) if db else 0
            img_label = f"🖼 {img_count}" if img_count else "—"

            enriched = {**prop, "current_price": current_price}

            row_idx = self._table.rowCount()
            self._table.insertRow(row_idx)
            self._table.setRowHeight(row_idx, T.ROW_H)

            cells = [
                prop["status"],
                prop.get("title") or "—",
                prop.get("location") or "—",
                current_price,
                prop["first_seen"][:16] if prop.get("first_seen") else "—",
                prop["last_seen"][:16]  if prop.get("last_seen")  else "—",
                img_label,
                prop.get("link") or "—",
            ]

            is_active = (prop["status"] == "Active")
            # Active rows = slightly lighter panel; Inactive = main bg
            row_bg    = QColor(T.BG2) if is_active else QColor(T.BG)
            # Active = normal weight white; Inactive = dimmed
            text_fg   = QColor(T.FG_WHITE) if is_active else QColor(T.FG_DIM)
            row_font  = QFont(T.FONT_FAMILY, T.FONT_SIZE)
            if not is_active:
                row_font.setItalic(True)

            for col, val in enumerate(cells):
                item = QTableWidgetItem(str(val))
                item.setForeground(QBrush(text_fg))
                item.setBackground(QBrush(row_bg))
                item.setFont(row_font)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                    if col in (0, 4, 5, 6)
                    else Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                )
                # Store enriched prop on the Status cell (col 0) for retrieval
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, enriched)
                self._table.setItem(row_idx, col, item)

        # Enable sorting now that all rows + UserRole data are in place
        self._table.setSortingEnabled(True)

    def _on_click(self, row: int, col: int) -> None:
        """Single click on Link column opens URL in browser."""
        if col == 7:
            link = self._table.item(row, col)
            if link and link.text() != "—":
                webbrowser.open(link.text())

    def _on_double_click(self, row: int, _col: int) -> None:
        # Prop is stored on the Status cell (col 0) via UserRole
        status_item = self._table.item(row, 0)
        if status_item:
            prop = status_item.data(Qt.ItemDataRole.UserRole)
            if prop:
                gw = GalleryWindow(self, prop, self._controller)
                gw.exec()


# ── Main window ────────────────────────────────────────────────────────────────

class ImotScraperMainWindow(QMainWindow):
    """
    Primary application window (PyQt6).
    Mirrors all features of the Tkinter ImotScraperGUI.
    """

    # Emitted from the scraper thread to update the status bar safely
    _scrape_finished = pyqtSignal(bool)   # success flag

    def __init__(self, controller=None) -> None:
        super().__init__()
        self.controller = controller
        self.setWindowTitle("Imot.bg Scraper")
        self.resize(800, 860)
        self.setMinimumSize(700, 600)
        _set_dark_titlebar(self)

        self._search_ids:     dict[str, int] = {}   # search_name → DB id
        self._gallery_win:    Optional[GalleryWindow] = None
        self._scraper_thread: Optional[threading.Thread] = None
        self._scheduler_running = False

        # Feed bridge: log handler → Qt signal → slot on main thread
        self._feed_bridge = FeedBridge()
        self._feed_bridge.event_received.connect(self._append_feed_row)
        self._feed_link_map: dict[int, str] = {}   # feed table row → link

        self._feed_handler = ResultsFeedHandler(self._feed_bridge)
        root_logger = logging.getLogger()
        if not any(isinstance(h, ResultsFeedHandler) for h in root_logger.handlers):
            root_logger.addHandler(self._feed_handler)
        root_logger.setLevel(logging.INFO)

        self._scrape_finished.connect(self._on_scrape_finished)

        self._build_ui()
        self._load_searches()
        self._load_view_buttons()

    # ── UI Construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        # ── Scheduler control bar ─────────────────────────────────────────────
        sched_group = QGroupBox("Scheduled Scraping Control")
        sched_layout = QHBoxLayout(sched_group)
        sched_layout.setSpacing(12)

        sched_layout.addWidget(QLabel("Daily Time (HH:MM):"))
        self._time_edit = QLineEdit("08:00")
        self._time_edit.setFixedWidth(72)
        sched_layout.addWidget(self._time_edit)

        self._sched_status_lbl = QLabel("Status: STOPPED")
        self._sched_status_lbl.setStyleSheet(f"color: {T.BTN_RED}; font-weight: bold;")
        sched_layout.addWidget(self._sched_status_lbl)

        sched_layout.addStretch()

        self._sched_btn = _styled_btn("Start Daily Schedule", min_width=160)
        self._sched_btn.clicked.connect(self.toggle_schedule)
        sched_layout.addWidget(self._sched_btn)

        root_layout.addWidget(sched_group)

        # ── Splitter: upper searches + lower feed ─────────────────────────────
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        root_layout.addWidget(splitter, stretch=1)

        # ── Upper pane ────────────────────────────────────────────────────────
        upper = QWidget()
        upper_layout = QVBoxLayout(upper)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.setSpacing(6)

        # Saved searches group
        searches_group = QGroupBox("Saved Searches")
        searches_layout = QHBoxLayout(searches_group)
        searches_layout.setSpacing(8)

        self._search_list = QListWidget()
        self._search_list.setAlternatingRowColors(True)
        self._search_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._search_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._search_list.setItemDelegate(_ListDelegate(self._search_list))
        searches_layout.addWidget(self._search_list, stretch=1)

        # Right-side action buttons
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)
        btn_col.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._add_btn = _styled_btn("Add New Search", style="green", min_width=140)
        self._add_btn.clicked.connect(lambda: self.show_search_dialog(action="create"))
        self._edit_btn = _styled_btn("Edit Selected", min_width=140)
        self._edit_btn.clicked.connect(self.edit_selected)
        self._remove_btn = _styled_btn("Remove Selected", style="red", min_width=140)
        self._remove_btn.clicked.connect(self.remove_selected)

        btn_col.addWidget(self._add_btn)
        btn_col.addWidget(self._edit_btn)
        btn_col.addWidget(self._remove_btn)
        searches_layout.addLayout(btn_col)

        upper_layout.addWidget(searches_group)

        # Run scraping now
        run_bar = QWidget()
        run_layout = QHBoxLayout(run_bar)
        run_layout.setContentsMargins(0, 0, 0, 0)
        run_layout.addStretch()
        self._run_btn = _styled_btn("▶  Run Scraping Now", style="green", min_width=180)
        self._run_btn.clicked.connect(self.start_scraping)
        run_layout.addWidget(self._run_btn)
        upper_layout.addWidget(run_bar)

        # View Results buttons area
        view_group = QGroupBox("View Search Results")
        view_layout = QVBoxLayout(view_group)
        self._view_btn_container = QWidget()
        self._view_btn_layout    = QHBoxLayout(self._view_btn_container)
        self._view_btn_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._view_btn_layout.setSpacing(8)
        view_layout.addWidget(self._view_btn_container)
        upper_layout.addWidget(view_group)

        splitter.addWidget(upper)

        # ── Lower pane: status bar + feed ─────────────────────────────────────
        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(4)

        # Status bar
        status_bar = QWidget()
        status_bar.setStyleSheet(f"background: {T.BG2}; border-radius: 4px;")
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(10, 5, 10, 5)

        self._status_lbl = QLabel("  No scrape run yet")
        self._status_lbl.setStyleSheet(f"color: {T.FG_DIM}; font-size: 12px;")
        status_layout.addWidget(self._status_lbl, stretch=1)

        self._status_counts_lbl = QLabel("")
        self._status_counts_lbl.setStyleSheet(f"color: {T.FG_DIM}; font-weight: bold;")
        self._status_counts_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        status_layout.addWidget(self._status_counts_lbl)

        lower_layout.addWidget(status_bar)

        # Feed table
        feed_group = QGroupBox("Scrape Results Feed")
        feed_layout = QVBoxLayout(feed_group)
        feed_layout.setContentsMargins(6, 12, 6, 6)   # top=12 keeps text clear of groupbox title

        self._feed_table = QTableWidget(0, 3)
        self._feed_table.setObjectName("feedTable")
        self._feed_table.setHorizontalHeaderLabels(["Type", "Title", "Price"])
        self._feed_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._feed_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._feed_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._feed_table.setColumnWidth(0, 72)
        self._feed_table.setColumnWidth(2, 180)
        self._feed_table.verticalHeader().setVisible(False)
        self._feed_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._feed_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._feed_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._feed_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # no focus rect on click
        self._feed_table.setShowGrid(True)
        self._feed_table.setGridStyle(Qt.PenStyle.SolidLine)
        self._feed_table.setItemDelegate(_FeedDelegate(self._feed_table))
        self._feed_table.cellDoubleClicked.connect(self._on_feed_double_click)

        feed_layout.addWidget(self._feed_table)
        lower_layout.addWidget(feed_group, stretch=1)

        splitter.addWidget(lower)
        splitter.setSizes([420, 380])

        # Placeholder row
        self._show_feed_placeholder()

    # ── Search list ───────────────────────────────────────────────────────────

    def _load_searches(self) -> None:
        self._search_list.clear()
        self._search_ids.clear()
        if not self.controller:
            return
        for s in self.controller.get_all_searches():
            item = QListWidgetItem(s["search_name"])
            self._search_list.addItem(item)
            self._search_ids[s["search_name"]] = s["id"]

    def _load_view_buttons(self) -> None:
        # Clear existing buttons
        while self._view_btn_layout.count():
            child = self._view_btn_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not self.controller:
            return
        for s in self.controller.get_all_searches():
            btn = _styled_btn(f"Properties: {s['search_name']}", style="purple", min_width=0)
            name = s["search_name"]
            btn.clicked.connect(lambda _checked, n=name: self._open_results(n))
            self._view_btn_layout.addWidget(btn)

    def _refresh(self) -> None:
        self._load_searches()
        self._load_view_buttons()

    # ── Search CRUD ───────────────────────────────────────────────────────────

    def show_search_dialog(self, action: str = "create", **kwargs) -> None:
        dlg = SearchDialog(self, action=action, **kwargs)
        _set_dark_titlebar(dlg)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        url         = dlg.get_url()
        search_name = dlg.get_search_name()
        emails      = dlg.get_emails()

        if not url or not search_name:
            QMessageBox.critical(self, "Error", "URL and Search Name are required.")
            return
        if not (url.startswith("http://") or url.startswith("https://")):
            QMessageBox.critical(
                self, "Invalid URL",
                "URL must start with http:// or https://\n\n"
                "Please check that you pasted the URL into the URL field."
            )
            return

        email_str = ";".join(emails)

        if action == "create":
            self.controller.add_search(search_name, url, email_str)
            logging.info(f"Added new search: {search_name}")
        else:
            old_name = kwargs.get("_old_name", search_name)
            search_id = self._search_ids.get(old_name)
            if search_id is not None:
                self.controller.update_search(search_id, search_name, url, email_str)
                logging.info(f"Updated search: {search_name}")
        self._refresh()

    def edit_selected(self) -> None:
        items = self._search_list.selectedItems()
        if not items:
            QMessageBox.warning(self, "Warning", "Please select a search to edit.")
            return
        item = items[0]
        name = item.text()

        record = None
        if self.controller:
            for s in self.controller.get_all_searches():
                if s["search_name"] == name:
                    record = s
                    break
        if not record:
            QMessageBox.critical(self, "Error", f"Could not find search '{name}' in database.")
            return

        emails = record.get("emails", "").split(";") if record.get("emails") else [""]
        self.show_search_dialog(
            action="edit",
            url=record.get("url", ""),
            search_name=name,
            emails=emails,
            _old_name=name,
        )

    def remove_selected(self) -> None:
        items = self._search_list.selectedItems()
        if not items:
            return
        if not self.controller:
            QMessageBox.critical(self, "Error", "Controller not available.")
            return
        for item in items:
            name = item.text()
            search_id = self._search_ids.get(name)
            if search_id is not None:
                self.controller.delete_search(search_id)
                logging.info(f"Deleted search id={search_id}")
            row = self._search_list.row(item)
            self._search_list.takeItem(row)
            self._search_ids.pop(name, None)
        self._load_view_buttons()

    # ── Results / Gallery ──────────────────────────────────────────────────────

    def _open_results(self, search_name: str) -> None:
        props = (
            self.controller.get_properties_for_search(search_name)
            if self.controller else []
        )
        win = ResultsWindow(self, search_name, props, self.controller)
        win.exec()

    def _open_gallery(self, prop: dict) -> None:
        if self._gallery_win and not self._gallery_win.isHidden():
            self._gallery_win.close()
        self._gallery_win = GalleryWindow(self, prop, self.controller)
        self._gallery_win.show()

    # ── Feed ──────────────────────────────────────────────────────────────────

    def _show_feed_placeholder(self) -> None:
        """Clear feed and show a single centred hint row."""
        self._feed_table.setRowCount(0)
        self._feed_link_map.clear()

        hint_row = 0
        self._feed_table.insertRow(hint_row)
        self._feed_table.setRowHeight(hint_row, T.ROW_H)
        hint_item = QTableWidgetItem("Run a scrape to see results here")
        hint_item.setForeground(QBrush(QColor(T.FG_DIM)))
        hint_item.setTextAlignment(
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
        )
        self._feed_table.setItem(hint_row, 0, hint_item)
        self._feed_table.setSpan(hint_row, 0, 1, 3)

    def _append_feed_row(self, event: dict) -> None:
        """Slot — called on main thread via FeedBridge signal."""
        kind  = event["kind"]
        title = event["title"]
        price = event["price"]
        link  = event.get("link", "")

        price_display = (
            f"{event['old_price']} → {price}"
            if kind == "CHANGED" else price
        )

        # Pick colours
        bg_map = {
            "NEW":     T.FEED_NEW_BG,
            "CHANGED": T.FEED_CHANGED_BG,
            "DELETED": T.FEED_DELETED_BG,
        }
        bg_color = QColor(bg_map.get(kind, T.BG2))
        fg_color = QColor(T.FG_WHITE)
        font     = _bold_font()

        row = self._feed_table.rowCount()
        self._feed_table.insertRow(row)
        self._feed_table.setRowHeight(row, T.ROW_H + 6)

        for col, text in enumerate((kind, title, price_display)):
            item = QTableWidgetItem(text)
            item.setBackground(QBrush(bg_color))
            item.setForeground(QBrush(fg_color))
            item.setFont(font)
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter
                if col in (0, 2)
                else Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
            )
            self._feed_table.setItem(row, col, item)

        self._feed_link_map[row] = link
        self._feed_table.scrollToBottom()

    def _on_feed_double_click(self, row: int, _col: int) -> None:
        link = self._feed_link_map.get(row, "")
        if not link or not self.controller or not self.controller.db:
            return
        prop = self.controller.db.get_property_by_link(link)
        if not prop:
            QMessageBox.information(
                self, "Not found",
                "Property details not in the database yet.\n"
                "It may still be saving — try again in a moment.",
            )
            return
        ph = self.controller.db.get_price_history(prop["id"])
        prop["current_price"] = next(
            (r["price"] for r in ph if r["price_status"] == "Current"), "—"
        )
        self._open_gallery(prop)

    # ── Scheduler ─────────────────────────────────────────────────────────────

    def toggle_schedule(self) -> None:
        if self._scheduler_running:
            self._stop_schedule()
        else:
            self._start_schedule()

    def _start_schedule(self) -> None:
        time_str = self._time_edit.text().strip()
        if not re.match(r"^\d{2}:\d{2}$", time_str):
            QMessageBox.critical(self, "Error", "Please enter the time in HH:MM format (e.g., 08:30).")
            return
        self._update_sched_status("STARTING...", T.YELLOW)
        self._sched_btn.setEnabled(False)
        threading.Thread(
            target=self._start_schedule_worker, args=(time_str,), daemon=True
        ).start()

    def _start_schedule_worker(self, time_str: str) -> None:
        try:
            if self.controller and self.controller.schedule_scraper(time_str):
                from PyQt6.QtCore import QMetaObject, Q_ARG
                QMetaObject.invokeMethod(
                    self, "_on_sched_started",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, time_str),
                )
            else:
                QMetaObject.invokeMethod(
                    self, "_on_sched_error",
                    Qt.ConnectionType.QueuedConnection,
                )
        except Exception as exc:
            logging.error(f"Scheduler startup failed: {exc}")
            from PyQt6.QtCore import QMetaObject
            QMetaObject.invokeMethod(
                self, "_on_sched_error",
                Qt.ConnectionType.QueuedConnection,
            )

    def _on_sched_started(self, time_str: str) -> None:
        self._scheduler_running = True
        self._update_sched_status(f"RUNNING daily at {time_str}", T.GREEN)
        self._sched_btn.setText("Stop Schedule")
        self._sched_btn.setEnabled(True)

    def _on_sched_error(self) -> None:
        self._update_sched_status("ERROR", T.BTN_RED)
        self._sched_btn.setText("Start Daily Schedule")
        self._sched_btn.setEnabled(True)

    def _stop_schedule(self) -> None:
        if self.controller:
            self.controller.stop_scheduler()
        self._scheduler_running = False
        self._update_sched_status("STOPPED", T.BTN_RED)
        self._sched_btn.setText("Start Daily Schedule")
        self._sched_btn.setEnabled(True)

    def _update_sched_status(self, text: str, color: str) -> None:
        self._sched_status_lbl.setText(f"Status: {text}")
        self._sched_status_lbl.setStyleSheet(f"color: {color}; font-weight: bold;")

    # ── Scraping ──────────────────────────────────────────────────────────────

    def start_scraping(self) -> None:
        if self._scheduler_running:
            QMessageBox.warning(
                self, "Warning",
                "The scheduler is currently running.\n"
                "Please stop it first before running an on-demand job."
            )
            return
        if self.controller:
            searches = self.controller.get_all_searches()
            if not searches:
                QMessageBox.warning(
                    self, "No Searches",
                    "No searches are configured.\n"
                    "Please add at least one search URL via 'Add New Search' first."
                )
                return

        # Clear feed
        self._feed_table.clearContents()
        self._feed_table.setRowCount(0)
        self._feed_link_map.clear()
        names = "  |  ".join(s["search_name"] for s in searches)
        self._status_lbl.setText(f"  ⏳  Running: {names}")
        self._status_lbl.setStyleSheet(f"color: {T.YELLOW}; font-size: 12px;")
        self._status_counts_lbl.setText("")

        logging.info(f"Starting scraper for {len(searches)} search(es)...")
        self._run_btn.setEnabled(False)
        self._run_btn.setText("Scraping...")

        self._scraper_thread = threading.Thread(
            target=self._scrape_worker, daemon=True
        )
        self._scraper_thread.start()

    def _scrape_worker(self) -> None:
        try:
            success = self.controller.run_scraper() if self.controller else False
        except Exception as exc:
            logging.error(f"Critical error during on-demand run: {exc}")
            success = False
        self._scrape_finished.emit(success)

    def _on_scrape_finished(self, success: bool) -> None:
        """Slot — runs on main thread."""
        if self.controller:
            try:
                self.controller.send_email_reports(success)
            except Exception as exc:
                logging.error(f"Error sending email reports: {exc}")

        row_count = self._feed_table.rowCount()
        n_new = n_chg = 0
        for r in range(row_count):
            kind_item = self._feed_table.item(r, 0)
            if kind_item:
                if kind_item.text() == "NEW":
                    n_new += 1
                elif kind_item.text() == "CHANGED":
                    n_chg += 1

        self._status_lbl.setText("  Last run finished")
        self._status_lbl.setStyleSheet(f"color: {T.FG_DIM}; font-size: 12px;")
        summary = f"✔  {n_new} new  |  {n_chg} changed"
        self._status_counts_lbl.setText(f"{summary}  ")

        if n_new == 0 and n_chg == 0:
            self._show_feed_placeholder()

        logging.info(f"Scraping completed — {n_new} new, {n_chg} changed.")
        self._run_btn.setEnabled(True)
        self._run_btn.setText("▶  Run Scraping Now")
        self._refresh()

    # ── Window close ──────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._scheduler_running:
            logging.info("Stopping scheduler before exit...")
            if self.controller:
                self.controller.stop_scheduler()
        event.accept()

    # ── Qt invokable slots for cross-thread calls ─────────────────────────────
    from PyQt6.QtCore import pyqtSlot

    @pyqtSlot(str)
    def _on_sched_started(self, time_str: str) -> None:  # type: ignore[override]
        self._scheduler_running = True
        self._update_sched_status(f"RUNNING daily at {time_str}", T.GREEN)
        self._sched_btn.setText("Stop Schedule")
        self._sched_btn.setEnabled(True)

    @pyqtSlot()
    def _on_sched_error(self) -> None:  # type: ignore[override]
        self._update_sched_status("ERROR", T.BTN_RED)
        self._sched_btn.setText("Start Daily Schedule")
        self._sched_btn.setEnabled(True)


# ── Entry point ───────────────────────────────────────────────────────────────

def main(controller=None) -> None:
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_stylesheet())
    win = ImotScraperMainWindow(controller=controller)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
