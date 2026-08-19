# File Integrity Monitor — Phase 1.0

A desktop File Integrity Monitoring (FIM) tool built with **Python + PySide6**.
It establishes a trusted SHA-256 baseline of a folder's files, then rescans
later to detect **MODIFIED**, **ADDED**, **DELETED**, and **UNCHANGED** files.

## Features

- Folder picker with recursive scanning (hidden files/folders skipped by default)
- SHA-256 hashing via `hashlib`, streamed in chunks (safe for large files)
- **Folder-level tracking**: detects folders added/removed, including empty ones
  — not just file changes
- **Content diffs**: for text files under 2 MB, the tool captures full content
  and shows a real line-by-line diff for MODIFIED files, and a content preview
  for ADDED/DELETED files. Double-click any row in the results table to see it.
  Binary or oversized files still get full MODIFIED/ADDED/DELETED detection via
  hash — they just won't have a line-by-line breakdown.
- **Full timeline / history**: every time you click "Run Scan & Compare", the
  result is appended to the baseline's history log (not just compared against
  the original baseline). Click "View Full Timeline" to see the entire story
  of a folder in order — created, then modified, then modified again, then
  deleted — each event with its own diff. History is auto-saved back to the
  baseline file after every scan, so it survives closing and reopening the app.
- Baseline stored as a standalone JSON file (not inside the monitored folder,
  so it isn't sitting next to the files it's meant to protect)
- Load a previously saved baseline and re-run comparisons anytime
- Background scanning via `QThread` — UI never freezes during a scan
- Color-coded results table (amber = modified, green = added, red = deleted),
  with a Type column distinguishing files from folders
- Live summary counts
- Export report as `.txt` (human-readable, includes diffs/previews) or `.json`
  (machine-readable, full untruncated data)

## Project Structure

```
fim_tool/
├── main.py                 # entry point
├── requirements.txt
├── core/
│   ├── scanner.py          # folder walk + SHA-256 hashing
│   ├── baseline.py         # create / save / load baseline JSON
│   ├── comparator.py       # diff logic: MODIFIED/ADDED/DELETED/UNCHANGED
│   └── report.py           # .txt / .json report generation
├── ui/
│   └── main_window.py      # PySide6 GUI
├── data/                   # default location for saved baselines
└── reports/                # default location for exported reports
```

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

## How to Demo It (baseline → change → rescan → detection)

1. Click **Browse...** and select a test folder (create a throwaway folder
   with a few files in it if you don't have one handy).
2. Click **Create Baseline**. You'll be prompted to save the baseline JSON
   (defaults to `data/baseline.json`).
3. Outside the app (in File Explorer / Finder / terminal):
   - Edit one file's contents → will show as **MODIFIED**
   - Add a new file → will show as **ADDED**
   - Delete an existing file → will show as **DELETED**
   - Leave the rest untouched → will show as **UNCHANGED**
4. Back in the app, click **Run Scan & Compare**.
5. Review the color-coded results table and summary counts.
6. **Double-click any changed row** to see exactly what changed — a red/green
   line-by-line diff for modified files, or a content preview for added/deleted
   files.
7. Click **Export Report (.txt)** or **Export Report (.json)** to save a report
   for this specific scan (defaults to the `reports/` folder). Both include
   diffs/previews.
8. Click **View Full Timeline** (top toolbar, or File menu) at any point to see
   the complete chronological history of every scan you've run against this
   baseline — created, modified, modified again, deleted — with a "Save as
   .txt" button inside that dialog to export it.

You can also reload a saved baseline later via **Load Existing Baseline**
(or File → Load Baseline...) without needing to recreate it.

## Notes / Known Scope Limits (Phase 1)

- No real-time/continuous monitoring — this is scan-on-demand.
- No rename detection — a rename currently shows as one DELETED + one ADDED
  entry (same hash, two different paths). This is a natural Phase 2 feature.
- Content diffing only works for text files under 2 MB (see
  `MAX_DIFFABLE_FILE_SIZE` in `core/scanner.py` to adjust). Binary files and
  larger files are still fully detected via hash, just without a diff view.
- Baselines saved before this diff feature was added won't have file content
  stored, so old MODIFIED entries will show "content not available" until you
  recreate the baseline.
- No malware/YARA/VirusTotal/threat-intel integration — hashing and
  comparison only.
- Hidden files/folders (dotfiles) are skipped by default; this is a
  hard-coded setting in `core/scanner.py` (`skip_hidden=True`) that can be
  exposed as a UI toggle later.

## Tech Stack

- Python 3.9+
- PySide6 (Qt for Python) — GUI
- `hashlib` — SHA-256 hashing
- `pathlib` — filesystem traversal
- `json` — baseline storage and report export
