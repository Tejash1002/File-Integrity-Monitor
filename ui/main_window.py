"""
ui/main_window.py
--------------------------------------------------------------------
PySide6 desktop interface for the File Integrity Monitoring tool.

Layout:
    - Top bar: monitored folder path + Browse button
    - Action bar: Create Baseline / Load Baseline / Run Scan & Compare
    - Summary strip: live counts of MODIFIED / ADDED / DELETED / UNCHANGED
    - Results table: one row per file, color-coded by status
    - Bottom bar: Export TXT / Export JSON + progress bar
    - Menu bar + status bar

Scanning/hashing runs on a background QThread so the UI never
freezes, even on large folders.
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
    QDialog, QTextEdit,
)

from core.baseline import create_baseline, save_baseline, load_baseline
from core.comparator import compare_to_baseline, record_scan
from core.report import (
    save_text_report, save_json_report,
    generate_timeline_report, save_timeline_text_report, save_timeline_json_report,
)

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
    """Runs a folder scan (baseline creation or comparison) off the UI thread."""
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
    diff/preview — so you can follow the whole story of a folder
    (created -> modified -> modified again -> deleted) in one place.
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
        self.setWindowTitle("File Integrity Monitor — Phase 1.0")
        self.resize(1100, 650)

        self.selected_folder = None
        self.baseline = None            # currently loaded/created baseline dict
        self.baseline_path = None       # where the baseline was last saved/loaded from
        self.last_result = None         # last comparison result (for export)
        self.worker = None

        self._build_ui()
        self._build_menu()
        self._update_button_states()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
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

        self.statusBar().showMessage("Ready.")

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

        help_menu = menu.addMenu("&Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    # ------------------------------------------------------------ Handlers

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
            "File Integrity Monitor — Phase 1.0\n\n"
            "Establishes a SHA-256 baseline of a folder's files and detects "
            "MODIFIED / ADDED / DELETED / UNCHANGED changes on rescan.\n\n"
            "Built with Python + PySide6."
        )

    # -------------------------------------------------------- Worker glue

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

    def on_row_double_clicked(self, row, column):
        if not self.last_result:
            return
        details = self.last_result["details"]
        if 0 <= row < len(details):
            dialog = DetailDialog(details[row], self)
            dialog.exec()

    def _on_scan_complete(self, payload):
        result, updated_baseline = payload
        self._set_busy(False)
        self.baseline = updated_baseline
        self.last_result = result
        self._populate_table(result["details"])

        # Persist the updated history so it survives an app restart.
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

    def _populate_table(self, details):
        self.table.setRowCount(0)
        self.table.setRowCount(len(details))

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
                self.table.setItem(row, col, cell)

    @staticmethod
    def _short_hash(h):
        if not h:
            return "—"
        if h.startswith("ERROR"):
            return h
        return h[:12] + "…"


def run_app():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
