"""
ui/main_window.py
--------------------------------------------------------------------
PySide6 desktop interface for the File Integrity Monitoring tool.

Phase 1.0 — Folder Monitoring tab (unchanged behavior):
    - Top bar: monitored folder path + Browse button
    - Action bar: Create Baseline / Load Baseline / Run Scan & Compare
    - Summary strip: live counts of MODIFIED / ADDED / DELETED / UNCHANGED
    - Results table: one row per file, color-coded by status
    - Bottom bar: Export TXT / Export JSON + progress bar

Phase 2.0 — USB Monitoring tab (new):
    - Detected removable-drive list + Refresh button
    - Select a USB, view its info
    - Scan / baseline / compare / timeline / report, reusing the same
      core.scanner / core.baseline / core.comparator / core.report
      modules as the folder workflow — just pointed at the USB's
      drive path instead of a user-picked folder.

Scanning/hashing runs on background QThreads so the UI never
freezes, even on large folders or USB drives.
--------------------------------------------------------------------
"""
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QAction, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QFileDialog, QMessageBox, QProgressBar, QHeaderView, QAbstractItemView,
    QDialog, QTextEdit, QTabWidget,
)

from core.baseline import create_baseline, save_baseline, load_baseline
from core.comparator import compare_to_baseline, record_scan
from core.report import (
    save_text_report, save_json_report,
    generate_timeline_report, save_timeline_text_report, save_timeline_json_report,
)
from core.usb_detector import detect_usb_devices

STATUS_COLORS = {
    "MODIFIED": QColor("#5a4a12"),   # amber-ish dark background
    "ADDED": QColor("#1e4620"),      # dark green
    "DELETED": QColor("#5a1f1f"),    # dark red
    "UNCHANGED": None,               # leave default row color
}
STATUS_TEXT_COLORS = {
    "MODIFIED": QColor("#ffc94d"),
    "ADDED": QColor("#7be08a"),
    "DELETED": QColor("#ff8a8a"),
    "UNCHANGED": QColor("#a0a0a0"),
}


class ScanWorker(QThread):
    """Runs a folder/USB scan (baseline creation or comparison) off the UI thread."""
    finished_ok = Signal(object)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, mode: str, folder_path: str = None, baseline: dict = None):
        super().__init__()
        self.mode = mode  # "baseline" or "compare"
        self.folder_path = folder_path
        self.baseline = baseline

    def run(self):
        try:
            if self.mode == "baseline":
                result = create_baseline(self.folder_path, progress_callback=self.progress.emit)
            elif self.mode == "compare":
                result = record_scan(self.baseline, progress_callback=self.progress.emit)
            else:
                raise ValueError(f"Unknown worker mode: {self.mode}")
            self.finished_ok.emit(result)
        except Exception as e:
            self.failed.emit(str(e))


class DetailDialog(QDialog):
    """
    Shows what actually changed inside a file: a color-coded unified
    diff for MODIFIED files, or a content preview for ADDED/DELETED
    files. Falls back to an explanation when no content is available
    (binary file, file too large, or baseline predates this feature).
    Used by both the Folder tab and the USB tab.
    """

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{item['status']} — {item['path']}")
        self.resize(800, 550)

        layout = QVBoxLayout(self)

        header = QLabel(f"<b>{item['path']}</b>  ({item['status']})")
        layout.addWidget(header)

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        mono = QFont("Consolas, Menlo, Monospace")
        mono.setStyleHint(QFont.Monospace)
        self.text.setFont(mono)
        layout.addWidget(self.text, stretch=1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._render(item)

    def _render(self, item):
        if item["type"] == "folder":
            self.text.setPlainText(
                f"This folder was {item['status'].lower()} since the baseline.\n"
                f"Folders don't have content to diff — only files do."
            )
            return

        if item["status"] == "MODIFIED":
            diff = item.get("diff")
            if diff:
                html = ['<pre style="margin:0;">']
                for line in diff:
                    line = line.rstrip("\n")
                    escaped = (line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
                    if line.startswith("+++") or line.startswith("---"):
                        html.append(f'<span style="color:#8a8a8a;">{escaped}</span>')
                    elif line.startswith("@@"):
                        html.append(f'<span style="color:#4aa3ff;">{escaped}</span>')
                    elif line.startswith("+"):
                        html.append(f'<span style="color:#7be08a;">{escaped}</span>')
                    elif line.startswith("-"):
                        html.append(f'<span style="color:#ff8a8a;">{escaped}</span>')
                    else:
                        html.append(f'<span style="color:#cfcfcf;">{escaped}</span>')
                if item.get("diff_truncated"):
                    html.append('<span style="color:#ffc94d;">... diff truncated (more changes exist) ...</span>')
                html.append("</pre>")
                self.text.setHtml("\n".join(html))
            else:
                note = item.get("diff_note") or "No diff available for this file."
                self.text.setPlainText(f"(No line-by-line diff available.)\n\nReason: {note}")

        elif item["status"] in ("ADDED", "DELETED"):
            preview = item.get("preview")
            if preview:
                label = "Content added:" if item["status"] == "ADDED" else "Content that was removed:"
                body = label + "\n\n" + "\n".join(preview)
                if item.get("preview_truncated"):
                    body += "\n\n... preview truncated (file has more lines) ..."
                self.text.setPlainText(body)
            else:
                note = item.get("preview_note") or "No preview available for this file."
                self.text.setPlainText(f"(No content preview available.)\n\nReason: {note}")
        else:
            self.text.setPlainText("This file is unchanged — nothing to show.")


class TimelineDialog(QDialog):
    """
    Shows the full chronological history of a baseline: every event
    from creation through every recorded scan, each with its own
    diff/preview. Used by both the Folder tab and the USB tab.
    """

    def __init__(self, baseline: dict, parent=None):
        super().__init__(parent)
        self.baseline = baseline
        self.setWindowTitle("Full Timeline")
        self.resize(900, 650)

        layout = QVBoxLayout(self)

        header = QLabel(f"<b>{baseline['metadata']['monitored_folder']}</b>")
        layout.addWidget(header)

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        mono = QFont("Consolas, Menlo, Monospace")
        mono.setStyleHint(QFont.Monospace)
        self.text.setFont(mono)
        self.text.setPlainText(generate_timeline_report(baseline))
        layout.addWidget(self.text, stretch=1)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save as .txt...")
        save_btn.clicked.connect(self.on_save)
        btn_row.addWidget(save_btn)
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def on_save(self):
        default_dir = str(Path.cwd() / "reports")
        path, _ = QFileDialog.getSaveFileName(self, "Save Timeline Report", default_dir, "Text Files (*.txt)")
        if path:
            try:
                save_timeline_text_report(self.baseline, path)
                QMessageBox.information(self, "Saved", f"Timeline saved to {path}")
            except Exception as e:
                QMessageBox.critical(self, "Save Failed", str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("File Integrity Monitor — Phase 2.0 (Folder + USB)")
        self.resize(1150, 700)

        # --- Folder monitoring state (Phase 1.0) ---
        self.selected_folder = None
        self.baseline = None            # currently loaded/created baseline dict
        self.baseline_path = None       # where the baseline was last saved/loaded from
        self.last_result = None         # last comparison result (for export)
        self.worker = None

        # --- USB monitoring state (Phase 2.0) ---
        self.usb_devices = []           # list[UsbDevice] from the last refresh
        self.usb_selected_device = None
        self.usb_baseline = None
        self.usb_baseline_path = None
        self.usb_last_result = None
        self.usb_worker = None

        self._build_ui()
        self._build_menu()
        self._update_button_states()
        self._update_usb_button_states()

    # ============================================================== UI

    def _build_ui(self):
        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        tabs.addTab(self._build_folder_tab(), "Folder Monitoring")
        tabs.addTab(self._build_usb_tab(), "USB Monitoring")
        self.statusBar().showMessage("Ready.")

    # ------------------------------------------------- Folder tab (Phase 1)

    def _build_folder_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)

        # --- Folder selection row ---
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Monitored Folder:"))
        self.folder_edit = QLineEdit()
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setPlaceholderText("No folder selected...")
        folder_row.addWidget(self.folder_edit, stretch=1)
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self.browse_folder)
        folder_row.addWidget(self.browse_btn)
        layout.addLayout(folder_row)

        # --- Action buttons row ---
        action_row = QHBoxLayout()
        self.create_baseline_btn = QPushButton("Create Baseline")
        self.create_baseline_btn.clicked.connect(self.on_create_baseline)
        action_row.addWidget(self.create_baseline_btn)

        self.load_baseline_btn = QPushButton("Load Existing Baseline")
        self.load_baseline_btn.clicked.connect(self.on_load_baseline)
        action_row.addWidget(self.load_baseline_btn)

        self.scan_btn = QPushButton("Run Scan && Compare")
        self.scan_btn.clicked.connect(self.on_run_scan)
        action_row.addWidget(self.scan_btn)

        self.timeline_btn = QPushButton("View Full Timeline")
        self.timeline_btn.clicked.connect(self.on_view_timeline)
        action_row.addWidget(self.timeline_btn)

        action_row.addStretch()

        self.export_txt_btn = QPushButton("Export Report (.txt)")
        self.export_txt_btn.clicked.connect(self.on_export_txt)
        action_row.addWidget(self.export_txt_btn)

        self.export_json_btn = QPushButton("Export Report (.json)")
        self.export_json_btn.clicked.connect(self.on_export_json)
        action_row.addWidget(self.export_json_btn)

        layout.addLayout(action_row)

        # --- Summary strip ---
        self.summary_label = QLabel("No scan performed yet.")
        self.summary_label.setStyleSheet("font-weight: bold; padding: 4px;")
        layout.addWidget(self.summary_label)

        # --- Results table ---
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Status", "Type", "Path", "Old Hash", "New Hash", "Last Modified"]
        )
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(self.on_row_double_clicked)
        layout.addWidget(self.table, stretch=1)

        hint = QLabel("Double-click a row to see exactly what changed inside a file.")
        hint.setStyleSheet("color: #888; font-style: italic;")
        layout.addWidget(hint)

        # --- Progress bar ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate by default
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        return tab

    # --------------------------------------------------- USB tab (Phase 2)

    def _build_usb_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)

        # --- Detected devices ---
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("<b>Detected USB / Removable Drives</b>"))
        header_row.addStretch()
        self.usb_refresh_btn = QPushButton("Refresh")
        self.usb_refresh_btn.clicked.connect(self.on_refresh_usb_devices)
        header_row.addWidget(self.usb_refresh_btn)
        layout.addLayout(header_row)

        self.usb_device_table = QTableWidget(0, 5)
        self.usb_device_table.setHorizontalHeaderLabels(
            ["Drive", "Label", "Type", "Capacity", "Free Space"]
        )
        self.usb_device_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.usb_device_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.usb_device_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.usb_device_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.usb_device_table.setAlternatingRowColors(True)
        self.usb_device_table.setMaximumHeight(160)
        self.usb_device_table.itemSelectionChanged.connect(self._update_usb_button_states)
        self.usb_device_table.cellDoubleClicked.connect(lambda r, c: self.on_select_usb())
        layout.addWidget(self.usb_device_table)

        select_row = QHBoxLayout()
        self.usb_select_btn = QPushButton("Select USB")
        self.usb_select_btn.clicked.connect(self.on_select_usb)
        select_row.addWidget(self.usb_select_btn)
        select_row.addStretch()
        layout.addLayout(select_row)

        # --- Selected device info ---
        self.usb_selected_label = QLabel("No USB selected yet. Refresh, then select a drive above.")
        self.usb_selected_label.setStyleSheet(
            "padding: 8px; border: 1px solid #444; border-radius: 4px; font-family: monospace;"
        )
        self.usb_selected_label.setWordWrap(True)
        layout.addWidget(self.usb_selected_label)

        # --- USB action buttons ---
        usb_action_row = QHBoxLayout()
        self.usb_create_baseline_btn = QPushButton("Create USB Baseline")
        self.usb_create_baseline_btn.clicked.connect(self.on_create_usb_baseline)
        usb_action_row.addWidget(self.usb_create_baseline_btn)

        self.usb_load_baseline_btn = QPushButton("Load USB Baseline")
        self.usb_load_baseline_btn.clicked.connect(self.on_load_usb_baseline)
        usb_action_row.addWidget(self.usb_load_baseline_btn)

        self.usb_scan_btn = QPushButton("Run USB Scan && Compare")
        self.usb_scan_btn.clicked.connect(self.on_run_usb_scan)
        usb_action_row.addWidget(self.usb_scan_btn)

        self.usb_timeline_btn = QPushButton("View USB Timeline")
        self.usb_timeline_btn.clicked.connect(self.on_view_usb_timeline)
        usb_action_row.addWidget(self.usb_timeline_btn)

        usb_action_row.addStretch()

        self.usb_export_txt_btn = QPushButton("Export Report (.txt)")
        self.usb_export_txt_btn.clicked.connect(self.on_export_usb_txt)
        usb_action_row.addWidget(self.usb_export_txt_btn)

        self.usb_export_json_btn = QPushButton("Export Report (.json)")
        self.usb_export_json_btn.clicked.connect(self.on_export_usb_json)
        usb_action_row.addWidget(self.usb_export_json_btn)

        layout.addLayout(usb_action_row)

        # --- USB summary strip ---
        self.usb_summary_label = QLabel("No USB scan performed yet.")
        self.usb_summary_label.setStyleSheet("font-weight: bold; padding: 4px;")
        layout.addWidget(self.usb_summary_label)

        # --- USB results table ---
        self.usb_table = QTableWidget(0, 6)
        self.usb_table.setHorizontalHeaderLabels(
            ["Status", "Type", "Path", "Old Hash", "New Hash", "Last Modified"]
        )
        self.usb_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.usb_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.usb_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.usb_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.usb_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.usb_table.setAlternatingRowColors(True)
        self.usb_table.cellDoubleClicked.connect(self.on_usb_row_double_clicked)
        layout.addWidget(self.usb_table, stretch=1)

        usb_hint = QLabel("Double-click a row to see exactly what changed inside a file.")
        usb_hint.setStyleSheet("color: #888; font-style: italic;")
        layout.addWidget(usb_hint)

        # --- USB progress bar ---
        self.usb_progress_bar = QProgressBar()
        self.usb_progress_bar.setRange(0, 0)
        self.usb_progress_bar.setVisible(False)
        layout.addWidget(self.usb_progress_bar)

        # Populate the device list once at startup for convenience.
        self.on_refresh_usb_devices()

        return tab

    def _build_menu(self):
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")

        new_baseline_action = QAction("New Baseline...", self)
        new_baseline_action.triggered.connect(self.on_create_baseline)
        file_menu.addAction(new_baseline_action)

        load_baseline_action = QAction("Load Baseline...", self)
        load_baseline_action.triggered.connect(self.on_load_baseline)
        file_menu.addAction(load_baseline_action)

        timeline_action = QAction("View Full Timeline...", self)
        timeline_action.triggered.connect(self.on_view_timeline)
        file_menu.addAction(timeline_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        usb_menu = menu.addMenu("&USB")
        refresh_usb_action = QAction("Refresh USB Devices", self)
        refresh_usb_action.triggered.connect(self.on_refresh_usb_devices)
        usb_menu.addAction(refresh_usb_action)

        usb_timeline_action = QAction("View USB Timeline...", self)
        usb_timeline_action.triggered.connect(self.on_view_usb_timeline)
        usb_menu.addAction(usb_timeline_action)

        help_menu = menu.addMenu("&Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    # ======================================================= Folder handlers
    # (Phase 1.0 — unchanged behavior)

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder to monitor")
        if folder:
            self.selected_folder = folder
            self.folder_edit.setText(folder)
            self._update_button_states()
            self.statusBar().showMessage(f"Selected folder: {folder}")

    def on_create_baseline(self):
        if not self.selected_folder:
            QMessageBox.warning(self, "No Folder Selected", "Please select a folder to monitor first.")
            return

        self._start_worker("baseline", folder_path=self.selected_folder)

    def on_load_baseline(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Baseline", str(Path.cwd() / "data"), "Baseline JSON (*.json)"
        )
        if not path:
            return
        try:
            self.baseline = load_baseline(path)
            self.baseline_path = path
            monitored = self.baseline["metadata"]["monitored_folder"]
            self.selected_folder = monitored
            self.folder_edit.setText(monitored)
            self.statusBar().showMessage(
                f"Loaded baseline ({self.baseline['metadata']['total_files']} files, "
                f"created {self.baseline['metadata']['created_at']})"
            )
            self.table.setRowCount(0)
            self.summary_label.setText("Baseline loaded. Run a scan to compare against it.")
            self._update_button_states()
        except Exception as e:
            QMessageBox.critical(self, "Failed to Load Baseline", str(e))

    def on_run_scan(self):
        if not self.baseline:
            QMessageBox.warning(self, "No Baseline", "Create or load a baseline before running a comparison scan.")
            return
        self._start_worker("compare", baseline=self.baseline)

    def on_view_timeline(self):
        if not self.baseline:
            QMessageBox.warning(self, "No Baseline", "Create or load a baseline first.")
            return
        dialog = TimelineDialog(self.baseline, self)
        dialog.exec()

    def on_export_txt(self):
        if not self.last_result:
            QMessageBox.warning(self, "Nothing to Export", "Run a comparison scan first.")
            return
        default_dir = str(Path.cwd() / "reports")
        path, _ = QFileDialog.getSaveFileName(self, "Export Text Report", default_dir, "Text Files (*.txt)")
        if path:
            try:
                save_text_report(self.last_result, path)
                self.statusBar().showMessage(f"Report saved to {path}")
            except Exception as e:
                QMessageBox.critical(self, "Export Failed", str(e))

    def on_export_json(self):
        if not self.last_result:
            QMessageBox.warning(self, "Nothing to Export", "Run a comparison scan first.")
            return
        default_dir = str(Path.cwd() / "reports")
        path, _ = QFileDialog.getSaveFileName(self, "Export JSON Report", default_dir, "JSON Files (*.json)")
        if path:
            try:
                save_json_report(self.last_result, path)
                self.statusBar().showMessage(f"Report saved to {path}")
            except Exception as e:
                QMessageBox.critical(self, "Export Failed", str(e))

    def show_about(self):
        QMessageBox.information(
            self, "About",
            "File Integrity Monitor — Phase 2.0\n\n"
            "Phase 1: establishes a SHA-256 baseline of a folder's files and "
            "detects MODIFIED / ADDED / DELETED / UNCHANGED changes on rescan, "
            "with content diffs and a full timeline history.\n\n"
            "Phase 2: extends the same engine to USB / removable drives — "
            "detect connected drives, select one, scan it, baseline it, and "
            "monitor its integrity the same way.\n\n"
            "Built with Python + PySide6."
        )

    def on_row_double_clicked(self, row, column):
        if not self.last_result:
            return
        details = self.last_result["details"]
        if 0 <= row < len(details):
            dialog = DetailDialog(details[row], self)
            dialog.exec()

    # ---------------------------------------------------------- Worker glue

    def _start_worker(self, mode, folder_path=None, baseline=None):
        self._set_busy(True, f"{'Creating baseline' if mode == 'baseline' else 'Scanning'}...")
        self.worker = ScanWorker(mode, folder_path=folder_path, baseline=baseline)
        self.worker.progress.connect(self._on_progress)
        if mode == "baseline":
            self.worker.finished_ok.connect(self._on_baseline_created)
        else:
            self.worker.finished_ok.connect(self._on_scan_complete)
        self.worker.failed.connect(self._on_worker_failed)
        self.worker.start()

    def _on_progress(self, count):
        self.statusBar().showMessage(f"Processed {count} file(s)...")

    def _on_baseline_created(self, baseline):
        self._set_busy(False)
        self.baseline = baseline
        self.baseline_path = None
        self.table.setRowCount(0)
        self.last_result = None

        default_path = str(Path.cwd() / "data" / "baseline.json")
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save Baseline As", default_path, "Baseline JSON (*.json)"
        )
        if save_path:
            try:
                save_baseline(baseline, save_path)
                self.baseline_path = save_path
                self.statusBar().showMessage(
                    f"Baseline created and saved: {baseline['metadata']['total_files']} files "
                    f"→ {save_path}"
                )
            except Exception as e:
                QMessageBox.critical(self, "Failed to Save Baseline", str(e))
        else:
            self.statusBar().showMessage(
                f"Baseline created in memory ({baseline['metadata']['total_files']} files) but not saved to disk."
            )

        self.summary_label.setText(
            f"Baseline ready: {baseline['metadata']['total_files']} file(s) recorded. "
            f"Run a scan to compare later changes."
        )
        self._update_button_states()

    def _on_scan_complete(self, payload):
        result, updated_baseline = payload
        self._set_busy(False)
        self.baseline = updated_baseline
        self.last_result = result
        self._populate_table(self.table, result["details"])

        if self.baseline_path:
            try:
                save_baseline(self.baseline, self.baseline_path)
            except Exception as e:
                QMessageBox.warning(self, "Could Not Save History",
                                     f"Scan completed, but saving history to disk failed: {e}")
        else:
            self.statusBar().showMessage(
                "Note: this baseline hasn't been saved to disk, so this scan's history "
                "won't persist after closing the app."
            )

        s = result["summary"]
        added_files = s["ADDED"] - s["ADDED_FOLDERS"]
        deleted_files = s["DELETED"] - s["DELETED_FOLDERS"]
        self.summary_label.setText(
            f"Scan complete — MODIFIED: {s['MODIFIED']}  |  ADDED (files): {added_files}  |  "
            f"DELETED (files): {deleted_files}  |  ADDED (folders): {s['ADDED_FOLDERS']}  |  "
            f"DELETED (folders): {s['DELETED_FOLDERS']}  |  UNCHANGED: {s['UNCHANGED']}"
        )
        self.statusBar().showMessage(f"Comparison complete at {result['scan_time']}")
        self._update_button_states()

    def _on_worker_failed(self, message):
        self._set_busy(False)
        QMessageBox.critical(self, "Operation Failed", message)
        self.statusBar().showMessage("Operation failed.")

    def _set_busy(self, busy: bool, message: str = ""):
        self.progress_bar.setVisible(busy)
        for btn in (self.browse_btn, self.create_baseline_btn, self.load_baseline_btn,
                    self.scan_btn, self.export_txt_btn, self.export_json_btn, self.timeline_btn):
            btn.setEnabled(not busy)
        if busy:
            self.statusBar().showMessage(message)

    def _update_button_states(self):
        self.scan_btn.setEnabled(self.baseline is not None)
        self.timeline_btn.setEnabled(self.baseline is not None)
        has_result = self.last_result is not None
        self.export_txt_btn.setEnabled(has_result)
        self.export_json_btn.setEnabled(has_result)

    # -------------------------------------------------------------- Table

    def _populate_table(self, table, details):
        table.setRowCount(0)
        table.setRowCount(len(details))

        for row, item in enumerate(details):
            status = item["status"]
            item_type = item.get("type", "file")

            status_item = QTableWidgetItem(status)
            type_item = QTableWidgetItem("Folder" if item_type == "folder" else "File")
            path_item = QTableWidgetItem(item["path"])
            old_hash_item = QTableWidgetItem(self._short_hash(item["old_hash"]))
            new_hash_item = QTableWidgetItem(self._short_hash(item["new_hash"]))
            modified_item = QTableWidgetItem(item["new_modified"] or item["old_modified"] or "")

            row_items = [status_item, type_item, path_item, old_hash_item, new_hash_item, modified_item]

            bg = STATUS_COLORS.get(status)
            fg = STATUS_TEXT_COLORS.get(status)
            for cell in row_items:
                if bg is not None:
                    cell.setBackground(bg)
                if fg is not None:
                    cell.setForeground(fg)

            for col, cell in enumerate(row_items):
                table.setItem(row, col, cell)

    @staticmethod
    def _short_hash(h):
        if not h:
            return "—"
        if h.startswith("ERROR"):
            return h
        return h[:12] + "…"

    # ========================================================== USB handlers
    # (Phase 2.0)

    def on_refresh_usb_devices(self):
        try:
            self.usb_devices = detect_usb_devices()
        except Exception as e:
            QMessageBox.critical(self, "USB Detection Failed", str(e))
            self.usb_devices = []

        self.usb_device_table.setRowCount(0)
        self.usb_device_table.setRowCount(len(self.usb_devices))

        for row, dev in enumerate(self.usb_devices):
            capacity = f"{dev.total_gb} GB" if dev.total_gb is not None else "—"
            free = f"{dev.free_gb} GB" if dev.free_gb is not None else "—"
            values = [dev.drive_letter, dev.label, dev.drive_type, capacity, free]
            for col, val in enumerate(values):
                self.usb_device_table.setItem(row, col, QTableWidgetItem(val))

        if self.usb_devices:
            self.statusBar().showMessage(f"Found {len(self.usb_devices)} removable drive(s).")
        else:
            self.statusBar().showMessage("No removable/USB drives detected. Connect one and click Refresh.")

        self._update_usb_button_states()

    def on_select_usb(self):
        selected_rows = self.usb_device_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "No Drive Selected", "Select a detected drive from the list first.")
            return

        row = selected_rows[0].row()
        if row < 0 or row >= len(self.usb_devices):
            return

        device = self.usb_devices[row]
        self.usb_selected_device = device

        # Selecting a new drive invalidates any in-progress baseline/results
        # tied to a previous drive, so the user doesn't accidentally compare
        # one USB's baseline against a different USB.
        self.usb_baseline = None
        self.usb_baseline_path = None
        self.usb_last_result = None
        self.usb_table.setRowCount(0)
        self.usb_summary_label.setText("No USB scan performed yet.")

        status = "Ready" if device.accessible else f"Not Ready ({device.error or 'no media'})"
        self.usb_selected_label.setText(
            "Selected USB:\n\n"
            f"Drive: {device.drive_letter}\n"
            f"Label: {device.label}\n"
            f"Type: {device.drive_type}\n"
            f"Status: {status}"
        )
        self.statusBar().showMessage(f"Selected USB: {device.drive_letter} ({device.label})")
        self._update_usb_button_states()

    def on_create_usb_baseline(self):
        if not self.usb_selected_device:
            QMessageBox.warning(self, "No USB Selected", "Select a USB drive first.")
            return
        if not self.usb_selected_device.accessible:
            QMessageBox.warning(self, "Drive Not Ready", "This drive isn't accessible right now (no media / not ready).")
            return

        self._start_usb_worker("baseline", folder_path=self.usb_selected_device.drive_letter)

    def on_load_usb_baseline(self):
        default_dir = Path.cwd() / "data" / "usb_baselines"
        default_dir.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self, "Load USB Baseline", str(default_dir), "Baseline JSON (*.json)"
        )
        if not path:
            return
        try:
            self.usb_baseline = load_baseline(path)
            self.usb_baseline_path = path
            meta = self.usb_baseline["metadata"]
            self.usb_selected_label.setText(
                "Loaded USB Baseline:\n\n"
                f"Drive: {meta['monitored_folder']}\n"
                f"Label: {meta.get('device_label', 'Unknown')}\n"
                f"Files Recorded: {meta['total_files']}\n"
                f"Baseline Created: {meta['created_at']}"
            )
            self.usb_table.setRowCount(0)
            self.usb_summary_label.setText("USB baseline loaded. Run a scan to compare against it.")
            self.statusBar().showMessage(f"Loaded USB baseline from {path}")
            self._update_usb_button_states()
        except Exception as e:
            QMessageBox.critical(self, "Failed to Load USB Baseline", str(e))

    def on_run_usb_scan(self):
        if not self.usb_baseline:
            QMessageBox.warning(self, "No USB Baseline", "Create or load a USB baseline before comparing.")
            return
        self._start_usb_worker("compare", baseline=self.usb_baseline)

    def on_view_usb_timeline(self):
        if not self.usb_baseline:
            QMessageBox.warning(self, "No USB Baseline", "Create or load a USB baseline first.")
            return
        dialog = TimelineDialog(self.usb_baseline, self)
        dialog.exec()

    def on_export_usb_txt(self):
        if not self.usb_last_result:
            QMessageBox.warning(self, "Nothing to Export", "Run a USB comparison scan first.")
            return
        default_dir = Path.cwd() / "reports" / "usb"
        default_dir.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Export USB Report", str(default_dir), "Text Files (*.txt)")
        if path:
            try:
                save_text_report(self.usb_last_result, path)
                self.statusBar().showMessage(f"USB report saved to {path}")
            except Exception as e:
                QMessageBox.critical(self, "Export Failed", str(e))

    def on_export_usb_json(self):
        if not self.usb_last_result:
            QMessageBox.warning(self, "Nothing to Export", "Run a USB comparison scan first.")
            return
        default_dir = Path.cwd() / "reports" / "usb"
        default_dir.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Export USB Report", str(default_dir), "JSON Files (*.json)")
        if path:
            try:
                save_json_report(self.usb_last_result, path)
                self.statusBar().showMessage(f"USB report saved to {path}")
            except Exception as e:
                QMessageBox.critical(self, "Export Failed", str(e))

    def on_usb_row_double_clicked(self, row, column):
        if not self.usb_last_result:
            return
        details = self.usb_last_result["details"]
        if 0 <= row < len(details):
            dialog = DetailDialog(details[row], self)
            dialog.exec()

    # ------------------------------------------------------ USB worker glue

    def _start_usb_worker(self, mode, folder_path=None, baseline=None):
        self._set_usb_busy(True, f"{'Creating USB baseline' if mode == 'baseline' else 'Scanning USB'}...")
        self.usb_worker = ScanWorker(mode, folder_path=folder_path, baseline=baseline)
        self.usb_worker.progress.connect(self._on_usb_progress)
        if mode == "baseline":
            self.usb_worker.finished_ok.connect(self._on_usb_baseline_created)
        else:
            self.usb_worker.finished_ok.connect(self._on_usb_scan_complete)
        self.usb_worker.failed.connect(self._on_usb_worker_failed)
        self.usb_worker.start()

    def _on_usb_progress(self, count):
        self.statusBar().showMessage(f"USB: processed {count} file(s)...")

    def _on_usb_baseline_created(self, baseline):
        self._set_usb_busy(False)

        # Tag this baseline as USB-backed and stamp it with device info so
        # reports/timeline can show "USB DEVICE INFORMATION" automatically.
        device = self.usb_selected_device
        baseline["metadata"]["is_usb"] = True
        if device:
            baseline["metadata"]["device_label"] = device.label
            baseline["metadata"]["device_type"] = device.drive_type
            baseline["metadata"]["device_total_gb"] = device.total_gb
            baseline["metadata"]["device_free_gb"] = device.free_gb

        self.usb_baseline = baseline
        self.usb_baseline_path = None
        self.usb_table.setRowCount(0)
        self.usb_last_result = None

        default_dir = Path.cwd() / "data" / "usb_baselines"
        default_dir.mkdir(parents=True, exist_ok=True)
        label = (device.label if device else "usb").replace(" ", "_")
        default_path = str(default_dir / f"{label}_baseline.json")
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save USB Baseline As", default_path, "Baseline JSON (*.json)"
        )
        if save_path:
            try:
                save_baseline(baseline, save_path)
                self.usb_baseline_path = save_path
                self.statusBar().showMessage(
                    f"USB baseline created and saved: {baseline['metadata']['total_files']} files "
                    f"→ {save_path}"
                )
            except Exception as e:
                QMessageBox.critical(self, "Failed to Save USB Baseline", str(e))
        else:
            self.statusBar().showMessage(
                f"USB baseline created in memory ({baseline['metadata']['total_files']} files) "
                f"but not saved to disk."
            )

        self.usb_summary_label.setText(
            f"USB baseline ready: {baseline['metadata']['total_files']} file(s) recorded. "
            f"Run a scan to compare later changes."
        )
        self._update_usb_button_states()

    def _on_usb_scan_complete(self, payload):
        result, updated_baseline = payload
        self._set_usb_busy(False)
        self.usb_baseline = updated_baseline
        self.usb_last_result = result
        self._populate_table(self.usb_table, result["details"])

        if self.usb_baseline_path:
            try:
                save_baseline(self.usb_baseline, self.usb_baseline_path)
            except Exception as e:
                QMessageBox.warning(self, "Could Not Save USB History",
                                     f"Scan completed, but saving history to disk failed: {e}")
        else:
            self.statusBar().showMessage(
                "Note: this USB baseline hasn't been saved to disk, so this scan's history "
                "won't persist after closing the app."
            )

        s = result["summary"]
        added_files = s["ADDED"] - s["ADDED_FOLDERS"]
        deleted_files = s["DELETED"] - s["DELETED_FOLDERS"]
        self.usb_summary_label.setText(
            f"USB scan complete — MODIFIED: {s['MODIFIED']}  |  ADDED: {added_files}  |  "
            f"DELETED: {deleted_files}  |  UNCHANGED: {s['UNCHANGED']}"
        )
        self.statusBar().showMessage(f"USB comparison complete at {result['scan_time']}")
        self._update_usb_button_states()

    def _on_usb_worker_failed(self, message):
        self._set_usb_busy(False)
        QMessageBox.critical(self, "USB Operation Failed", message)
        self.statusBar().showMessage("USB operation failed.")

    def _set_usb_busy(self, busy: bool, message: str = ""):
        self.usb_progress_bar.setVisible(busy)
        for btn in (self.usb_refresh_btn, self.usb_select_btn, self.usb_create_baseline_btn,
                    self.usb_load_baseline_btn, self.usb_scan_btn, self.usb_timeline_btn,
                    self.usb_export_txt_btn, self.usb_export_json_btn):
            btn.setEnabled(not busy)
        if busy:
            self.statusBar().showMessage(message)

    def _update_usb_button_states(self):
        has_selection = bool(self.usb_device_table.selectionModel() and
                              self.usb_device_table.selectionModel().selectedRows())
        self.usb_select_btn.setEnabled(has_selection)
        self.usb_create_baseline_btn.setEnabled(self.usb_selected_device is not None)
        self.usb_scan_btn.setEnabled(self.usb_baseline is not None)
        self.usb_timeline_btn.setEnabled(self.usb_baseline is not None)
        has_result = self.usb_last_result is not None
        self.usb_export_txt_btn.setEnabled(has_result)
        self.usb_export_json_btn.setEnabled(has_result)


def run_app():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
