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
from typing import Optional

from PyQt6.QtCore import (
    Qt, QSize, QTimer, pyqtSignal, QObject, QRect, QUrl,
)
from PyQt6.QtGui import (
    QColor, QFont, QBrush, QPixmap, QImage, QIcon, QPainter, QDesktopServices,
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
    event_received  = pyqtSignal(dict)
    search_progress = pyqtSignal(str)   # emits search_name when scraper starts each search


class ResultsFeedHandler(logging.Handler):
    """
    Intercepts scraper log lines and forwards structured dicts to
    a FeedBridge signal (thread-safe — Qt handles cross-thread signals).

    Line formats:
      "New listing: <title> | price: <price> | search: <search> | <link>"
      "Price change: <title> | old: <old> | new: <new> | search: <search>"
    """

    _RE_NEW        = re.compile(r"New listing: (.+?) \| price: (.+?) \| search: (.+?) \| (https?://\S+)")
    _RE_CHANGED    = re.compile(r"Price change: (.+?) \| old: (.+?) \| new: (.+?) \| search: (.+?) \| (https?://\S+)")
    _RE_DELETED    = re.compile(r"Removed listing: (.+?) \| search: (.+?) \| (https?://\S*)")
    _RE_PROCESSING = re.compile(r"Processing: (.+)")

    def __init__(self, bridge: FeedBridge) -> None:
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        m = self._RE_NEW.search(msg)
        if m:
            self._bridge.event_received.emit({
                "kind":        "NEW",
                "title":       m.group(1).strip(),
                "price":       m.group(2).strip(),
                "search_name": m.group(3).strip(),
                "link":        m.group(4).strip(),
            })
            return
        m = self._RE_CHANGED.search(msg)
        if m:
            self._bridge.event_received.emit({
                "kind":        "CHANGED",
                "title":       m.group(1).strip(),
                "old_price":   m.group(2).strip(),
                "price":       m.group(3).strip(),
                "search_name": m.group(4).strip(),
                "link":        m.group(5).strip(),
            })
            return
        m = self._RE_DELETED.search(msg)
        if m:
            self._bridge.event_received.emit({
                "kind":        "DEACTIVATED",
                "title":       m.group(1).strip(),
                "price":       "—",
                "search_name": m.group(2).strip(),
                "link":        m.group(3).strip(),
            })
            return
        m = self._RE_PROCESSING.search(msg)
        if m:
            self._bridge.search_progress.emit(m.group(1).strip())


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

    # Emitted when the user toggles the favorite button
    favorite_toggled = pyqtSignal(str, bool)   # record_id, is_now_favorite

    def __init__(self, parent: QWidget, prop: dict, controller) -> None:
        super().__init__(parent)
        title_text = prop.get("title") or "—"
        self.setWindowTitle(f"Gallery  —  {title_text}")
        self.resize(880, 800)
        self.setMinimumSize(600, 500)
        _set_dark_titlebar(self)

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

        # Favorite toggle button
        is_fav = bool(prop.get("is_favorite"))
        self._fav_btn = _styled_btn(
            "🌟  Remove Favorite" if is_fav else "☆  Add to Favorites",
            style="purple" if is_fav else "default",
            min_width=160,
        )
        self._fav_btn.clicked.connect(self._toggle_favorite)

        nav_layout.addWidget(self._btn_prev)
        nav_layout.addStretch()
        nav_layout.addWidget(self._counter_lbl)
        nav_layout.addStretch()
        nav_layout.addWidget(self._fav_btn)
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
            def _open_link(_event, u=link_url) -> None:
                QDesktopServices.openUrl(QUrl(u))
            title_lbl.mousePressEvent = _open_link
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

        # ── Physical attributes ───────────────────────────────────────────────
        if prop.get("area_sqm"):
            _info_row("Area:",  prop["area_sqm"])
        if prop.get("floor"):
            _info_row("Floor:", prop["floor"])
        if prop.get("yard_sqm"):
            _info_row("Yard:",  prop["yard_sqm"])

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

    def _toggle_favorite(self) -> None:
        """Toggle favorite state, update button appearance, emit signal."""
        if not self._controller:
            return
        record_id = self._prop.get("record_id")
        search_id = self._prop.get("search_id")
        if not record_id or search_id is None:
            return

        is_now_fav = self._controller.toggle_favorite(record_id, search_id)
        self._prop["is_favorite"] = 1 if is_now_fav else 0

        # Update button label and colour
        if is_now_fav:
            self._fav_btn.setText("🌟  Remove Favorite")
            self._fav_btn.setStyleSheet(
                f"QPushButton {{ background: {T.BTN_PURPLE}; color: {T.FG_WHITE}; "
                f"border-radius: {T.RADIUS}px; padding: 4px 12px; font-weight: bold; }}"
                f"QPushButton:hover {{ background: {T.BTN_PURPLE_H}; }}"
            )
        else:
            self._fav_btn.setText("☆  Add to Favorites")
            self._fav_btn.setStyleSheet("")   # revert to global QSS default

        self.favorite_toggled.emit(record_id, is_now_fav)

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
                 properties: list[dict], controller,
                 search_id: int | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Results — {search_name}")
        self.resize(1300, 620)
        self.setMinimumSize(900, 400)
        _set_dark_titlebar(self)

        self._controller  = controller
        self._search_id   = search_id
        self._search_name = search_name

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Table
        cols = ["Status", "Title", "Location", "Price", "€/m²",
                "First Seen", "Deactivated At", "Days on Market", "Images", "Link"]
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

        # Column widths (0=Status, 1=Title, 2=Location, 3=Price, 4=€/m², 5=First Seen,
        #                6=Deactivated At, 7=Days on Market, 8=Images, 9=Link)
        self._table.setColumnWidth(0, 80)
        self._table.setColumnWidth(1, 220)
        self._table.setColumnWidth(2, 180)
        self._table.setColumnWidth(3, 130)
        self._table.setColumnWidth(4, 90)
        self._table.setColumnWidth(5, 130)
        self._table.setColumnWidth(6, 130)
        self._table.setColumnWidth(7, 110)
        self._table.setColumnWidth(8, 60)

        # Fetch latest area avg for underpriced highlighting
        self._area_avg: float | None = None
        if controller and search_id is not None:
            history = controller.get_area_stats_history(search_id, limit=1)
            if history:
                self._area_avg = history[-1].get("avg_price_per_sqm")

        self._populate(
            sorted(properties, key=lambda p: (0 if p["status"] == "Active" else 1)),
            controller,
        )
        layout.addWidget(self._table)

        # Summary bar
        active   = sum(1 for p in properties if p["status"] == "Active")
        inactive = len(properties) - active
        avg_txt  = f"  |  Area avg: {self._area_avg:.2f} €/m²" if self._area_avg else ""
        summary_lbl = _dim_label(
            f"Total: {len(properties)}  |  Active: {active}  |  "
            f"Inactive: {inactive}{avg_txt}  |  Double-click a row to view gallery"
        )

        summary_bar = QHBoxLayout()
        summary_bar.addWidget(summary_lbl, stretch=1)

        if search_id is not None:
            chart_btn = make_button(self, text="📊 Area Avg Chart",
                                    callback=self._open_area_chart)
            chart_btn.setFixedWidth(150)
            summary_bar.addWidget(chart_btn)

            found_btn = make_button(self, text="📈 Active Listings History",
                                    callback=self._open_listings_found_chart)
            found_btn.setFixedWidth(190)
            summary_bar.addWidget(found_btn)

        layout.addLayout(summary_bar)

        self._table.cellDoubleClicked.connect(self._on_double_click)
        self._table.cellClicked.connect(self._on_click)

    def _populate(self, properties: list[dict], controller) -> None:
        from datetime import datetime, date

        db = controller.db if controller else None
        self._table.setSortingEnabled(False)   # must stay off while inserting

        today = date.today()

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

            sqm_val = prop.get("price_per_sqm") or "—"
            is_active = (prop["status"] == "Active")

            # ── Days on Market ─────────────────────────────────────────────
            # Active  : today − first_seen
            # Inactive: inactivated_at − first_seen  (fallback: last_seen − first_seen)
            dom_str = "—"
            fs_raw = prop.get("first_seen")
            if fs_raw:
                try:
                    fs_date = datetime.strptime(fs_raw[:10], "%Y-%m-%d").date()
                    if is_active:
                        dom_str = str((today - fs_date).days)
                    else:
                        end_raw = prop.get("inactivated_at") or prop.get("last_seen")
                        if end_raw:
                            end_date = datetime.strptime(end_raw[:10], "%Y-%m-%d").date()
                            dom_str = str((end_date - fs_date).days)
                except (ValueError, TypeError):
                    pass

            cells = [
                prop["status"],
                ("🌟 " if bool(prop.get("is_favorite")) else "") + (prop.get("title") or "—"),
                prop.get("location") or "—",
                current_price,
                sqm_val,
                prop["first_seen"][:16] if prop.get("first_seen") else "—",
                prop["inactivated_at"][:16] if prop.get("inactivated_at") else "—",
                dom_str,
                img_label,
                prop.get("link") or "—",
            ]

            # Determine if this row is underpriced (active only; needs numeric sqm)
            is_underpriced = False
            if is_active and self._area_avg and sqm_val != "—":
                try:
                    sqm_float = float(sqm_val.split()[0].replace(",", "."))
                    is_underpriced = sqm_float < self._area_avg * 0.9
                except (ValueError, IndexError):
                    pass

            if is_underpriced:
                row_bg = QColor(T.FEED_UNDERPRICED_BG)
            elif is_active:
                row_bg = QColor(T.BG2)
            else:
                row_bg = QColor(T.BG)

            text_fg  = QColor(T.FG_WHITE) if is_active else QColor(T.FG_DIM)
            row_font = QFont(T.FONT_FAMILY, T.FONT_SIZE)
            if not is_active:
                row_font.setItalic(True)

            for col, val in enumerate(cells):
                item = QTableWidgetItem(str(val))
                item.setForeground(QBrush(text_fg))
                item.setBackground(QBrush(row_bg))
                item.setFont(row_font)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                    if col in (0, 4, 5, 6, 7, 8)
                    else Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                )
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, enriched)
                self._table.setItem(row_idx, col, item)

        # Enable sorting now that all rows + UserRole data are in place
        self._table.setSortingEnabled(True)

    def _on_click(self, row: int, col: int) -> None:
        """Single click on Link column opens URL in browser."""
        if col == 9:
            link = self._table.item(row, col)
            if link and link.text() != "—":
                QDesktopServices.openUrl(QUrl(link.text()))

    def _on_double_click(self, row: int, _col: int) -> None:
        # Prop is stored on the Status cell (col 0) via UserRole
        status_item = self._table.item(row, 0)
        if not status_item:
            return
        prop = status_item.data(Qt.ItemDataRole.UserRole)
        if not prop:
            return
        gw = GalleryWindow(self, prop, self._controller)
        gw.exec()
        # After gallery closes, re-read the favorite flag from DB and update the title cell
        record_id = prop.get("record_id")
        search_id = prop.get("search_id")
        if record_id and search_id is not None and self._controller:
            is_fav = self._controller.is_favorite(record_id, search_id)
            title_item = self._table.item(row, 1)
            if title_item:
                raw_title = prop.get("title") or "—"
                title_item.setText(("🌟 " if is_fav else "") + raw_title)

    def _on_favorite_changed(self, record_id: str, is_now_favorite: bool) -> None:
        pass  # kept for compat — actual update happens in _on_double_click after exec()

    def _open_area_chart(self) -> None:
        """Open the Area Average Price chart for this search."""
        dlg = AreaAvgChartDialog(self, self._search_name, self._search_id, self._controller)
        dlg.exec()

    def _open_listings_found_chart(self) -> None:
        """Open the Listings Found chart for this search."""
        dlg = ListingsFoundChartDialog(self, self._search_name, self._search_id, self._controller)
        dlg.exec()


# ── Shared charting helper ─────────────────────────────────────────────────────

def _bin_series(dates: list, values: list, bin_by: str = "auto") -> tuple[list, list]:
    """
    Aggregate (dates, values) into fewer points for readability.

    bin_by:
      "auto"  – chooses based on span: <90 pts → day, <365 pts → week, else → month
      "day"   – average per calendar day
      "week"  – average per ISO week
      "month" – average per calendar month

    Returns (binned_dates, binned_values) with the bin's midpoint date and mean value.
    """
    from datetime import datetime, timedelta
    from collections import defaultdict

    if not dates:
        return [], []

    span_days = (dates[-1] - dates[0]).days if len(dates) > 1 else 0

    if bin_by == "auto":
        if len(dates) <= 90:
            bin_by = "day"
        elif span_days <= 365:
            bin_by = "week"
        else:
            bin_by = "month"

    buckets: dict[str, list] = defaultdict(list)
    for d, v in zip(dates, values):
        if v is None:
            continue
        if bin_by == "day":
            key = d.strftime("%Y-%m-%d")
        elif bin_by == "week":
            key = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        else:  # month
            key = d.strftime("%Y-%m")
        buckets[key].append((d, v))

    result_dates, result_values = [], []
    for key in sorted(buckets.keys()):
        pairs = buckets[key]
        mid_d = pairs[len(pairs) // 2][0]   # median date in bucket
        mean_v = sum(p[1] for p in pairs) / len(pairs)
        result_dates.append(mid_d)
        result_values.append(mean_v)

    return result_dates, result_values


def _rolling_avg(values: list[float], win: int) -> list[float]:
    """Centred rolling mean with the given window size."""
    roll = []
    for i in range(len(values)):
        lo = max(0, i - win // 2)
        hi = min(len(values), lo + win)
        roll.append(sum(values[lo:hi]) / (hi - lo))
    return roll


# ── Area Average Price Chart ───────────────────────────────────────────────────

class AreaAvgChartDialog(QDialog):
    """Line chart of the daily area-average price-per-m² stored in scrape_runs.

    • Each scrape run stores avg_price_per_sqm; multiple runs per day are averaged.
    • Long histories are binned automatically (day / week / month).
    • Active property dots are overlaid at their first_seen date.
    • Click any dot to open that property's gallery.
    • Green shaded band shows the ±10 % range around the most recent avg.
    """

    def __init__(self, parent: QWidget, search_name: str,
                 search_id: int | None, controller) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Area Avg Price/m² — {search_name}")
        self.resize(960, 540)
        self.setMinimumSize(500, 320)
        _set_dark_titlebar(self)

        self._controller = controller
        self._search_id  = search_id
        self._dot_props: dict = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        runs: list[dict] = []
        if controller and search_id is not None:
            runs = controller.get_scrape_history(search_id, limit=365)

        # Filter to runs that actually have an avg stored
        runs_with_avg = [r for r in runs if r.get("avg_price_per_sqm") is not None]

        if not runs_with_avg:
            layout.addWidget(_dim_label(
                "No area average data yet. Run a scrape first — avg €/m² is stored per run."
            ))
            return

        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
            import matplotlib.dates as mdates
            from datetime import datetime

            # Build raw series (one point per run, oldest first)
            raw_dates: list[datetime] = []
            raw_values: list[float]   = []
            for r in reversed(runs_with_avg):   # runs are newest-first from DB
                try:
                    raw_dates.append(datetime.strptime(r["run_date"][:16], "%Y-%m-%d %H:%M"))
                    raw_values.append(float(r["avg_price_per_sqm"]))
                except (ValueError, TypeError):
                    pass

            # Dynamic binning
            dates, values = _bin_series(raw_dates, raw_values, bin_by="auto")

            if not dates:
                layout.addWidget(_dim_label("No valid area average data to display."))
                return

            # ── Scatter dots for active properties ────────────────────────────
            prop_dots: list[dict] = []
            if controller and search_id is not None:
                db = controller.db
                if db:
                    active_props = db.get_properties(search_id, status="Active")
                    fallback_x = dates[-1]
                    for p in active_props:
                        raw = p.get("price_per_sqm")
                        if not raw or raw == "—":
                            continue
                        try:
                            val = float(raw.split()[0].replace(",", "."))
                        except (ValueError, IndexError):
                            continue
                        dot_x = fallback_x
                        fs = p.get("first_seen")
                        if fs:
                            try:
                                dot_x = datetime.strptime(fs[:16], "%Y-%m-%d %H:%M")
                            except ValueError:
                                pass
                        ph = db.get_price_history(p["id"])
                        p["current_price"] = next(
                            (r["price"] for r in ph if r["price_status"] == "Current"), "—"
                        )
                        prop_dots.append({"x": dot_x, "y": val, "prop": p})

            # ── Figure ────────────────────────────────────────────────────────
            fig = Figure(figsize=(9.5, 4.8), tight_layout=True, facecolor="#1e1e1e")
            ax  = fig.add_subplot(111)
            ax.set_facecolor("#2b2b2b")

            # Main avg line
            ax.plot(dates, values, color="#0d7aff", linewidth=2.0, marker="o",
                    markersize=4, label="Avg €/m²", zorder=2)

            # Rolling trend line (7-pt centred window, or fewer if little data)
            if len(values) >= 2:
                win = min(7, len(values))
                roll = _rolling_avg(values, win)
                ax.plot(dates, roll, color="#4caf50", linewidth=1.8,
                        linestyle="--", alpha=0.85, label=f"{win}-pt trend", zorder=3)

            # Green ±10 % band around the most-recent avg value
            last_avg = values[-1]
            ax.axhspan(last_avg * 0.9, last_avg * 1.1, alpha=0.07,
                       color="#4caf50",
                       label=f"±10 % of latest ({last_avg:.0f} €/m²)")

            # Annotate latest point
            ax.annotate(f"{last_avg:.2f}",
                        xy=(dates[-1], last_avg),
                        xytext=(10, 6), textcoords="offset points",
                        color="#ffffff", fontsize=8)

            # Property scatter dots
            if prop_dots:
                xs = [d["x"] for d in prop_dots]
                ys = [d["y"] for d in prop_dots]
                colors = [
                    "#ff9800" if d["y"] < last_avg * 0.9 else "#e0e0e0"
                    for d in prop_dots
                ]
                sc = ax.scatter(xs, ys, c=colors, s=55, zorder=4,
                                picker=8, edgecolors="#555555", linewidths=0.5,
                                label=f"Active listings ({len(prop_dots)})")
                self._dot_props[sc] = prop_dots

            # ── Axis padding so sparse data isn't crammed in a corner ────────
            from datetime import timedelta as _td
            all_x = dates + ([d["x"] for d in prop_dots] if prop_dots else [])
            all_y = values + ([d["y"] for d in prop_dots] if prop_dots else [])
            if all_x:
                x_lo, x_hi = min(all_x), max(all_x)
                span = x_hi - x_lo if x_hi != x_lo else _td(days=1)
                x_pad = max(_td(days=1), span * 0.05)
                ax.set_xlim(x_lo - x_pad, x_hi + x_pad)
            if all_y:
                y_lo, y_hi = min(all_y), max(all_y)
                y_margin = max(1.0, (y_hi - y_lo) * 0.15) if y_hi != y_lo else max(1.0, y_hi * 0.10)
                ax.set_ylim(y_lo - y_margin, y_hi + y_margin)

            # Axes formatting
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b '%y"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            fig.autofmt_xdate(rotation=30)
            ax.set_ylabel("€/m²", color="#e8e8e8")
            ax.set_xlabel("Date", color="#e8e8e8")
            ax.tick_params(colors="#888888")
            ax.spines[:].set_color("#333333")
            ax.legend(facecolor="#2b2b2b", labelcolor="#e8e8e8",
                      framealpha=0.8, fontsize=8)

            canvas = FigureCanvasQTAgg(fig)
            layout.addWidget(canvas)

            if prop_dots:
                hint = _dim_label(
                    "Click a dot to open the property gallery  •  orange = >10 % below latest avg"
                )
                hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(hint)

                def _on_pick(event) -> None:
                    artist = event.artist
                    if artist not in self._dot_props:
                        return
                    idx = event.ind[0]
                    prop = self._dot_props[artist][idx]["prop"]
                    gw = GalleryWindow(self, prop, self._controller)
                    gw.exec()

                fig.canvas.mpl_connect("pick_event", _on_pick)

        except ImportError:
            layout.addWidget(_dim_label(
                "matplotlib is not installed. Run:  pip install matplotlib"
            ))


# ── Active Listings History Chart ──────────────────────────────────────────────

class ListingsFoundChartDialog(QDialog):
    """Line chart of active listing counts over time, sourced from scrape_runs.

    Multiple runs on the same day are averaged.
    Long histories are binned automatically (day / week / month).
    """

    def __init__(self, parent: QWidget, search_name: str,
                 search_id: int | None, controller) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Active Listings History — {search_name}")
        self.resize(960, 500)
        self.setMinimumSize(500, 300)
        _set_dark_titlebar(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        runs: list[dict] = []
        if controller and search_id is not None:
            runs = controller.get_scrape_history(search_id, limit=365)

        if not runs:
            layout.addWidget(_dim_label("No scrape runs yet for this search."))
            return

        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
            import matplotlib.dates as mdates
            from datetime import datetime

            # Build raw series oldest-first; prefer active_count, fall back to records_found
            raw_dates: list[datetime] = []
            raw_values: list[float]   = []
            for r in reversed(runs):
                try:
                    raw_dates.append(datetime.strptime(r["run_date"][:16], "%Y-%m-%d %H:%M"))
                    v = r.get("active_count")
                    if v is None:
                        v = r.get("records_found", 0)
                    raw_values.append(float(v))
                except (ValueError, TypeError):
                    pass

            dates, values = _bin_series(raw_dates, raw_values, bin_by="auto")

            if not dates:
                layout.addWidget(_dim_label("No valid data to display."))
                return

            fig = Figure(figsize=(9.5, 4.5), tight_layout=True, facecolor="#1e1e1e")
            ax  = fig.add_subplot(111)
            ax.set_facecolor("#2b2b2b")

            # Line + markers (no fill_between — it fills from y=0 and looks wrong
            # with only a few data points)
            ax.plot(dates, values, color="#0d7aff", linewidth=2.0, marker="o",
                    markersize=6, label="Active listings", zorder=2)

            # Rolling trend line (only meaningful with 3+ points)
            if len(values) >= 3:
                win = min(7, len(values))
                roll = _rolling_avg(values, win)
                ax.plot(dates, roll, color="#4caf50", linewidth=1.8,
                        linestyle="--", alpha=0.9, label=f"{win}-pt trend", zorder=3)

            # Annotate latest point
            ax.annotate(f"{values[-1]:.0f}",
                        xy=(dates[-1], values[-1]),
                        xytext=(0, 10), textcoords="offset points",
                        ha="center", color="#ffffff", fontsize=9)

            # ── Axis padding so a single point isn't crammed in a corner ─────
            from datetime import timedelta
            if len(dates) == 1:
                # Single point: centre it with ±1 day x-margin and ±10 % y-margin
                ax.set_xlim(dates[0] - timedelta(days=1), dates[0] + timedelta(days=1))
            else:
                span = dates[-1] - dates[0]
                pad = max(timedelta(days=1), span * 0.05)
                ax.set_xlim(dates[0] - pad, dates[-1] + pad)

            if values:
                lo, hi = min(values), max(values)
                margin = max(1.0, (hi - lo) * 0.15) if hi != lo else max(1.0, hi * 0.15)
                ax.set_ylim(max(0, lo - margin), hi + margin)

            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b '%y"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            fig.autofmt_xdate(rotation=30)
            ax.set_ylabel("Active listings", color="#e8e8e8")
            ax.set_xlabel("Date", color="#e8e8e8")
            ax.tick_params(colors="#888888")
            ax.spines[:].set_color("#333333")
            ax.legend(facecolor="#2b2b2b", labelcolor="#e8e8e8",
                      framealpha=0.8, fontsize=8)

            canvas = FigureCanvasQTAgg(fig)
            layout.addWidget(canvas)

        except ImportError:
            layout.addWidget(_dim_label(
                "matplotlib is not installed. Run:  pip install matplotlib"
            ))



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
        self._scraper_thread: Optional[threading.Thread] = None
        self._scheduler_running = False

        # Feed bridge: log handler → Qt signal → slot on main thread
        self._feed_bridge = FeedBridge()
        self._feed_bridge.event_received.connect(self._append_feed_row)
        self._feed_bridge.search_progress.connect(self._on_search_progress)
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
        self._view_btn_layout    = QGridLayout(self._view_btn_container)
        self._view_btn_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._view_btn_layout.setSpacing(8)
        self._view_btn_layout.setContentsMargins(0, 0, 0, 0)
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

        self._history_btn = _styled_btn("📋  Run History", min_width=0)
        self._history_btn.setFixedHeight(26)
        self._history_btn.clicked.connect(self._open_run_history)
        status_layout.addWidget(self._history_btn)

        self._restore_btn = _styled_btn("⏪  Restore DB", min_width=0)
        self._restore_btn.setFixedHeight(26)
        self._restore_btn.clicked.connect(self._open_restore_dialog)
        status_layout.addWidget(self._restore_btn)

        lower_layout.addWidget(status_bar)

        # Feed table
        feed_group = QGroupBox("Scrape Results Feed")
        feed_layout = QVBoxLayout(feed_group)
        feed_layout.setContentsMargins(6, 12, 6, 6)   # top=12 keeps text clear of groupbox title

        self._feed_table = QTableWidget(0, 4)
        self._feed_table.setObjectName("feedTable")
        self._feed_table.setHorizontalHeaderLabels(["Search", "Type", "Title", "Price"])
        self._feed_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._feed_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._feed_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._feed_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._feed_table.setColumnWidth(0, 160)
        self._feed_table.setColumnWidth(1, 110)
        self._feed_table.setColumnWidth(3, 220)
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
        COLS = 3
        for idx, s in enumerate(self.controller.get_all_searches()):
            btn = _styled_btn(s['search_name'], style="purple", min_width=0)
            name = s["search_name"]
            btn.clicked.connect(lambda _checked, n=name: self._open_results(n))
            row, col = divmod(idx, COLS)
            self._view_btn_layout.addWidget(btn, row, col)

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
        search_id = self._search_ids.get(search_name)
        win = ResultsWindow(self, search_name, props, self.controller, search_id=search_id)
        win.exec()

    def _open_gallery(self, prop: dict) -> None:
        gw = GalleryWindow(self, prop, self.controller)
        gw.exec()

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
        self._feed_table.setSpan(hint_row, 0, 1, 4)

    def _append_feed_row(self, event: dict) -> None:
        """Slot — called on main thread via FeedBridge signal."""
        kind        = event["kind"]
        title       = event["title"]
        price       = event["price"]
        search_name = event.get("search_name", "")
        link        = event.get("link", "")

        price_display = (
            f"{event['old_price']} → {price}"
            if kind == "CHANGED" else price
        )

        # Pick colours — favorites override the kind-based colour
        bg_map = {
            "NEW":         T.FEED_NEW_BG,
            "CHANGED":     T.FEED_CHANGED_BG,
            "DEACTIVATED": T.FEED_DELETED_BG,
        }
        kind_color = QColor(bg_map.get(kind, T.BG2))

        # Check if this listing is a favorite
        is_fav = False
        if link and self.controller and self.controller.db:
            prop = self.controller.db.get_property_by_link(link)
            if prop:
                is_fav = bool(prop.get("is_favorite"))

        bg_color = kind_color
        fg_color = QColor(T.FG_WHITE)
        font     = _bold_font()

        # Prefix title with star if favorite
        display_title = ("🌟 " + title) if is_fav else title

        row = self._feed_table.rowCount()
        self._feed_table.insertRow(row)
        self._feed_table.setRowHeight(row, T.ROW_H + 6)

        for col, text in enumerate((search_name, kind, display_title, price_display)):
            item = QTableWidgetItem(text)
            item.setBackground(QBrush(bg_color))
            item.setForeground(QBrush(fg_color))
            item.setFont(font)
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter
                if col in (0, 1, 2, 3)
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

    def _on_search_progress(self, search_name: str) -> None:
        """Update status label with the currently running search name."""
        self._status_lbl.setText(f"  ⏳  Scraping: {search_name}")
        self._status_lbl.setStyleSheet(f"color: {T.YELLOW}; font-size: 12px;")

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
        self._status_lbl.setText(f"  ⏳  Starting scrape…")
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
            kind_item = self._feed_table.item(r, 1)   # col 1 = "Type"
            if kind_item:
                if kind_item.text() == "NEW":
                    n_new += 1
                elif kind_item.text() == "CHANGED":
                    n_chg += 1

        self._status_lbl.setText("  Last run finished")
        self._status_lbl.setStyleSheet(f"color: {T.FG_DIM}; font-size: 12px;")
        summary = f"✔  {n_new} new  |  {n_chg} changed"
        self._status_counts_lbl.setText(f"{summary}  ")

        logging.info(f"Scraping completed — {n_new} new, {n_chg} changed.")
        self._run_btn.setEnabled(True)
        self._run_btn.setText("▶  Run Scraping Now")

        if success and self.controller:
            def _do_backup():
                try:
                    path = self.controller.backup_database()
                    if path:
                        logging.debug(f"Auto-backup created: {path}")
                except Exception as exc:
                    logging.warning(f"Auto-backup failed: {exc}")

            threading.Thread(target=_do_backup, daemon=True).start()

        self._refresh()

    # ── Run history ───────────────────────────────────────────────────────────

    def _open_run_history(self) -> None:
        dlg = RunHistoryDialog(self, self.controller)
        _set_dark_titlebar(dlg)
        dlg.exec()

    def _open_restore_dialog(self) -> None:
        dlg = RestoreDialog(self, self.controller)
        _set_dark_titlebar(dlg)
        dlg.exec()

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


# ── Run History dialog ────────────────────────────────────────────────────────

class RestoreDialog(QDialog):
    """
    Shows available database backups (local + Google Drive) and lets the
    user restore the database from any of them.
    After a successful restore the application restarts automatically.
    """

    _COLS = ["Source", "File name", "Size", "Date"]

    def __init__(self, parent: QWidget, controller) -> None:
        super().__init__(parent)
        self._controller = controller
        self.setWindowTitle("Restore Database")
        self.setMinimumSize(760, 380)
        _set_dark_titlebar(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Info label
        info = QLabel(
            "Select a backup to restore from.  "
            "<b>This will overwrite the current database</b> — "
            "a backup of the current state is created first."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {T.FG_DIM}; font-size: 11px;")
        layout.addWidget(info)

        # Table
        self._table = QTableWidget(0, len(self._COLS))
        self._table.setHorizontalHeaderLabels(self._COLS)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)     # Source
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)   # Name
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)     # Size
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)     # Date
        self._table.setColumnWidth(0, 80)
        self._table.setColumnWidth(2, 90)
        self._table.setColumnWidth(3, 148)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._table.setAlternatingRowColors(False)
        self._table.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self._table, stretch=1)

        # Bottom bar
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)

        self._refresh_btn = _styled_btn("🔄  Refresh", min_width=100)
        self._refresh_btn.clicked.connect(self._load)
        btn_row.addWidget(self._refresh_btn)

        btn_row.addStretch()

        close_btn = _styled_btn("Close", min_width=80)
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        self._restore_btn = _styled_btn("⏪  Restore Selected", style="green", min_width=160)
        self._restore_btn.setEnabled(False)
        self._restore_btn.clicked.connect(self._do_restore)
        btn_row.addWidget(self._restore_btn)

        layout.addLayout(btn_row)

        # Backup metadata stored per row
        self._backups: list[dict] = []
        self._load()

    def _load(self) -> None:
        self._refresh_btn.setEnabled(False)
        self._table.setRowCount(0)
        self._backups = []
        try:
            backups = self._controller.list_backups() if self._controller else []
        except Exception as exc:
            logging.error(f"RestoreDialog: failed to list backups: {exc}")
            backups = []

        self._backups = backups
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(backups))
        for row, b in enumerate(backups):
            source = b.get("source", "local")
            name   = b.get("name", "")
            size   = b.get("size")
            mtime  = b.get("modified_time", "")

            size_str = f"{size / 1024 / 1024:.1f} MB" if size else "—"
            mtime_str = mtime[:16] if mtime else "—"

            bg = QColor(T.BG2)
            fg = QColor(T.FG_WHITE)

            source_item = QTableWidgetItem("☁ Drive" if source == "gdrive" else "💾 Local")
            source_item.setForeground(QBrush(QColor(T.ACCENT) if source == "gdrive" else fg))
            source_item.setBackground(QBrush(bg))

            for col, text in enumerate([None, name, size_str, mtime_str]):
                item = QTableWidgetItem(text or "")
                item.setForeground(QBrush(fg))
                item.setBackground(QBrush(bg))
                if col == 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
                self._table.setItem(row, col, item)
            self._table.setItem(row, 0, source_item)

        self._table.setSortingEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._restore_btn.setEnabled(False)

    def _on_selection(self) -> None:
        self._restore_btn.setEnabled(bool(self._table.selectedItems()))

    def _do_restore(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        if idx >= len(self._backups):
            return
        b = self._backups[idx]
        name = b.get("name", "unknown")

        confirm = QMessageBox(self)
        confirm.setWindowTitle("Confirm Restore")
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText(
            f"<b>Restore from:</b><br>{name}<br><br>"
            "The current database will be backed up first, then replaced.<br>"
            "The application will restart automatically."
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return

        # Back up current DB before overwriting
        try:
            if self._controller:
                self._controller.backup_database()
        except Exception:
            pass

        self._restore_btn.setEnabled(False)
        self._refresh_btn.setEnabled(False)

        ok = self._controller.restore_database(
            source   = b.get("source", "local"),
            path     = b.get("path"),
            drive_id = b.get("drive_id"),
        ) if self._controller else False

        if ok:
            QMessageBox.information(
                self, "Restore Complete",
                "Database restored successfully.\nThe application will now restart."
            )
            self.accept()
            # Restart: frozen exe → relaunch the exe; dev → relaunch main.py
            import sys
            import subprocess
            if getattr(sys, "frozen", False):
                # Running as PyInstaller exe — re-exec the exe itself
                cmd = [sys.executable]
            else:
                # Running from source — re-exec python main.py
                import os
                main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "main.py")
                cmd = [sys.executable, os.path.normpath(main_py)]
            subprocess.Popen(cmd)
            QApplication.quit()
        else:
            QMessageBox.critical(
                self, "Restore Failed",
                "The restore operation failed.\nSee the log for details."
            )
            self._restore_btn.setEnabled(True)
            self._refresh_btn.setEnabled(True)


class RunHistoryDialog(QDialog):
    """
    Shows all scrape_runs rows (newest first) in a table.
    Failed runs are tinted red; the Error column shows the message inline.
    """

    _COLS = ["Date", "Search", "Found", "New", "Changed", "Inactive", "Status", "Error"]

    def __init__(self, parent: QWidget, controller) -> None:
        super().__init__(parent)
        self.setWindowTitle("Run History")
        self.setMinimumSize(900, 480)
        _set_dark_titlebar(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self._table = QTableWidget(0, len(self._COLS))
        self._table.setHorizontalHeaderLabels(self._COLS)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)         # Date
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)        # Search
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)          # Found
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)          # New
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)          # Changed
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)          # Inactive
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)          # Status
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)        # Error
        self._table.setColumnWidth(0, 148)
        self._table.setColumnWidth(2, 58)
        self._table.setColumnWidth(3, 48)
        self._table.setColumnWidth(4, 72)
        self._table.setColumnWidth(5, 68)
        self._table.setColumnWidth(6, 72)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.setAlternatingRowColors(False)
        layout.addWidget(self._table, stretch=1)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._populate(controller)

    def _populate(self, controller) -> None:
        runs = controller.get_all_scrape_runs() if controller else []
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(runs))
        for row, run in enumerate(runs):
            failed = not bool(run.get("success", 1))
            err    = run.get("error_message") or ""

            row_bg = QColor(T.FEED_DELETED_BG) if failed else QColor(T.BG2)
            row_fg = QColor(T.FG_WHITE)

            # Format the run_date nicely (strip microseconds if present)
            raw_date = run.get("run_date", "")
            try:
                date_str = raw_date[:16]   # "YYYY-MM-DD HH:MM"
            except Exception:
                date_str = str(raw_date)

            values = [
                date_str,
                run.get("search_name", ""),
                str(run.get("records_found", "")),
                str(run.get("new_records", "")),
                str(run.get("changed_prices", "")),
                str(run.get("inactive_count", "")),
                "FAILED" if failed else "OK",
                err,
            ]

            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setBackground(QBrush(row_bg))

                # Error column: red text when there is an error message
                if col == 7 and err:
                    item.setForeground(QBrush(QColor(T.BTN_RED_H)))
                elif col == 6 and failed:
                    item.setForeground(QBrush(QColor(T.BTN_RED_H)))
                else:
                    item.setForeground(QBrush(row_fg))

                # Right-align numeric columns
                if col in (2, 3, 4, 5):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
                    )
                else:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                    )
                self._table.setItem(row, col, item)

        self._table.setSortingEnabled(True)


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
