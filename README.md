# File Integrity Monitor — Phase 1.0

A desktop File Integrity Monitoring (FIM) tool built with **Python + PySide6**.
It establishes a trusted SHA-256 baseline of a folder's files and folders,
then rescans later to detect **MODIFIED**, **ADDED**, **DELETED**, and
**UNCHANGED** items — with real content diffs and a full chronological
timeline of every change, not just a single before/after snapshot.

---

## Features

- **Folder picker with recursive scanning** — walks the entire directory tree;
  hidden files/folders (dotfiles) are skipped by default.
- **SHA-256 hashing** via `hashlib`, streamed in 64KB chunks so large files
  don't blow up memory.
- **Folder-level tracking** — detects folders added/removed, including empty
  ones, not just file changes. Empty folders are explicitly labeled `(empty)`
  in reports.
- **Content diffs** — for text files under 2 MB, the tool captures full
  content and shows a real **line-by-line diff** (`- old line` / `+ new line`)
  for MODIFIED files, and a **content preview** for ADDED/DELETED files.
  Double-click any row in the results table to see it. Binary or oversized
  files still get full MODIFIED/ADDED/DELETED detection via hash — they just
  won't have a line-by-line breakdown (the tool tells you why: "binary file",
  "too large to diff", etc.).
- **Full timeline / history** — every time you click "Run Scan & Compare",
  the result is appended to the baseline's history log instead of only being
  compared against the original snapshot. Click **View Full Timeline** to see
  the entire story of a folder in order: created → modified → modified again
  → deleted, each event with its own diff/preview. History auto-saves back to
  the baseline file after every scan, so it survives closing and reopening
  the app.
- **Baseline stored as a standalone JSON file**, separate from the monitored
  folder — a real FIM baseline shouldn't sit next to the files it protects.
- **Load a previously saved baseline** and keep scanning/building history on
  it across sessions.
- **Background scanning via `QThread`** — the UI never freezes during a scan,
  even on large folders.
- **Color-coded results table** (amber = modified, green = added, red =
  deleted) with a **Type** column distinguishing files from folders.
- **Live summary counts** after every scan.
- **Export reports** as `.txt` (human-readable, includes diffs/previews) or
  `.json` (machine-readable, full untruncated data) — both for a single scan
  and for the full timeline.

---

## Project Structure

```
fim_tool/
├── main.py                 # entry point
├── requirements.txt
├── .gitignore
├── core/
│   ├── scanner.py          # folder walk + SHA-256 hashing + text content capture
│   ├── baseline.py         # create / save / load baseline JSON + history init
│   ├── comparator.py       # diff engine: MODIFIED/ADDED/DELETED/UNCHANGED + history recording
│   └── report.py           # .txt / .json report generation (single-scan + full timeline)
├── ui/
│   └── main_window.py      # PySide6 GUI (table, dialogs, timeline view)
├── data/                   # default location for saved baselines
└── reports/                # default location for exported reports
```

---

## Setup (VS Code / any machine)

1. **Create a virtual environment** (recommended):

   ```bash
   python -m venv venv
   ```

   Activate it:
   - Windows: `venv\Scripts\activate`
   - macOS/Linux: `source venv/bin/activate`

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Run the app:**

   ```bash
   python main.py
   ```

---

## How to Demo It (baseline → changes over time → full timeline)

1. Click **Browse...** and select a test folder (create a throwaway folder
   with a few files and a subfolder if you don't have one handy).
2. Click **Create Baseline**. You'll be prompted to save the baseline JSON
   (defaults to `data/baseline.json`) — **save it**, so history persists.
3. Outside the app (File Explorer / Finder / terminal), make some changes:
   - Edit a file's contents → will show as **MODIFIED**, with a real diff
   - Add a new file → will show as **ADDED**, with a content preview
   - Delete an existing file → will show as **DELETED**, with its last known content
   - Add or remove a folder (even an empty one) → tracked as a structural change
   - Leave the rest untouched → will show as **UNCHANGED**
4. Back in the app, click **Run Scan & Compare**. Review the color-coded
   results table, the Type column (File/Folder), and the summary counts.
5. **Double-click any changed row** to see exactly what changed inside that
   file — a red/green line-by-line diff, or a content preview.
6. Click **Export Report (.txt)** or **Export Report (.json)** to save a
   report for just this scan (defaults to `reports/`).
7. Repeat steps 3–4 a few more times — modify, add, modify again, delete —
   to build up a real history.
8. Click **View Full Timeline** (toolbar or File menu) to see the complete
   chronological story: `BASELINE CREATED → SCAN (modified) → SCAN (added) →
   SCAN (modified again) → SCAN (deleted)`, each with its own diff. Use
   **Save as .txt** inside that dialog to export the whole timeline.

You can reload a saved baseline anytime via **Load Existing Baseline** (or
File → Load Baseline...) and keep adding to its history without recreating it.

---

## Notes / Known Scope Limits (Phase 1)

- **No real-time/continuous monitoring** — this is scan-on-demand. Every
  change is only detected when you click "Run Scan & Compare". True live
  monitoring would need OS-level filesystem event hooks (`inotify` on Linux,
  `ReadDirectoryChangesW` on Windows, `FSEvents` on macOS) running as a
  background service — a natural **Phase 2** scope, not a Phase 1 addition.
- **No rename detection** — a rename currently shows as one DELETED + one
  ADDED entry (same hash, two different paths).
- **Content diffing only works for text files under 2 MB** (see
  `MAX_DIFFABLE_FILE_SIZE` in `core/scanner.py` to adjust). Binary files and
  larger files are still fully detected via hash, just without a diff view.
- **Baselines saved before the diff/history features were added** won't have
  file content or a history log stored. Old baselines still load fine
  (backward-compatible defaults kick in), but MODIFIED entries from before
  the update will show "content not available" until you recreate the
  baseline, and the timeline will start fresh from whenever history tracking
  began.
- **No malware/YARA/VirusTotal/threat-intel integration** — hashing and
  comparison only.
- **Hidden files/folders (dotfiles) are skipped by default** — hard-coded as
  `skip_hidden=True` in `core/scanner.py`; could be exposed as a UI toggle
  later.

---

## Changelog

**v1.3 — Full timeline / history tracking**
- Every scan now appends to a persistent history log inside the baseline
  (instead of only ever comparing against the original snapshot).
- New `record_scan()` in `comparator.py` rolls the snapshot forward after
  each scan and logs the diff as a new event.
- New **View Full Timeline** button/dialog shows the entire chronological
  story of a folder, event by event, with diffs/previews included.
- History auto-saves back to the baseline JSON file after every scan.
- New `generate_timeline_report()` / `save_timeline_text_report()` /
  `save_timeline_json_report()` in `report.py`.

**v1.2 — Content diffs**
- Scanner now captures full text content (files under 2 MB, non-binary) so
  later comparisons can show what actually changed, not just that the hash
  differs.
- MODIFIED files get a real unified diff; ADDED/DELETED files get a content
  preview. Binary/oversized files fall back gracefully with an explanation.
- New double-click **Detail Dialog** in the GUI to view diffs/previews
  per-file.
- Reports (.txt and .json) now include diffs and previews inline.

**v1.1 — Folder tracking + report readability**
- Fixed: empty folders, and folders added/removed entirely, are now tracked
  (previously only files were scanned).
- Results table gained a **Type** column (File vs Folder).
- Rewrote the text report to lead with a plain-English summary, group
  changes under clear section headers, and shorten hashes for readability.

**v1.0 — Initial Phase 1.0 release**
- Folder selection, recursive SHA-256 scanning, baseline creation/save/load.
- MODIFIED / ADDED / DELETED / UNCHANGED detection via hash comparison.
- PySide6 desktop UI with color-coded results table, background scanning via
  `QThread`, and `.txt`/`.json` report export.

---

## Tech Stack

- Python 3.9+
- PySide6 (Qt for Python) — GUI
- `hashlib` — SHA-256 hashing
- `pathlib` — filesystem traversal
- `difflib` — unified diff generation
- `json` — baseline/history storage and report export