# File Integrity Monitor — Phase 2.0 (Folder + USB)

A desktop File Integrity Monitoring (FIM) tool built with **Python + PySide6**.

- **Phase 1** establishes a trusted SHA-256 baseline of a local folder's
  files and folders, then rescans later to detect **MODIFIED**, **ADDED**,
  **DELETED**, and **UNCHANGED** items — with real content diffs and a full
  chronological timeline of every change.
- **Phase 2** extends the same engine to **USB / removable storage
  devices**: detect connected drives, select one, scan it, baseline it, and
  monitor its integrity exactly the same way as a folder.

Both workflows live side by side in the same application, as two tabs.

---

## Features

### Phase 1 — Core FIM (folders)

- Folder picker with recursive scanning (hidden files/folders skipped by default)
- SHA-256 hashing via `hashlib`, streamed in chunks (safe for large files)
- Folder-level tracking — detects folders added/removed, including empty ones
- Content diffs — for text files under 2 MB, real line-by-line diffs for
  MODIFIED files and content previews for ADDED/DELETED files. Double-click
  any row to view. Binary/oversized files still detect via hash, just
  without a line-by-line breakdown.
- Full timeline/history — every "Run Scan & Compare" appends to a running
  history log; **View Full Timeline** shows the whole story of a folder in
  order (created → modified → modified again → deleted), each with its own
  diff. History auto-saves after every scan.
- Baseline stored as a standalone JSON file, separate from the monitored
  folder.
- Load a previously saved baseline and keep building history on it.
- Background scanning via `QThread` — UI never freezes.
- Color-coded results table (amber = modified, green = added, red = deleted),
  with a Type column (File/Folder).
- Export reports as `.txt` (human-readable) or `.json` (machine-readable).

### Phase 2 — USB-Aware FIM (new)

- **USB / removable-drive detection** — Windows-native detection via
  `ctypes` bindings to `kernel32.dll` (`GetLogicalDrives`, `GetDriveTypeW`,
  `GetVolumeInformationW`, `GetDiskFreeSpaceExW`). No third-party dependency
  required. Reports drive letter, volume label, drive type, total capacity,
  and free space. Only genuinely removable drives are listed — fixed,
  network, CD-ROM, and RAM disks are filtered out (it does not blindly
  treat every drive as a USB).
- **Refresh button** to re-scan for connected/disconnected devices on demand.
- **Explicit USB selection** — nothing is scanned automatically on insertion;
  the user must select a detected drive and click into the workflow.
- **USB scanning** reuses the *exact* Phase 1 scanner/hasher (`core/scanner.py`)
  — no duplicated SHA-256 logic. Recursively scans, hashes, and handles
  inaccessible files gracefully. Never modifies or executes anything on the USB.
- **USB baseline** reuses the Phase 1 baseline engine, tagged with device
  metadata (label, type, capacity, free space) and saved separately under
  `data/usb_baselines/`, so it never collides with folder baselines.
- **USB integrity comparison** reuses the Phase 1 comparator — MODIFIED /
  ADDED / DELETED / UNCHANGED, same as folders. (This is still integrity
  monitoring, not malware detection — a MODIFIED file is not flagged as
  malicious.)
- **USB reports** extend the Phase 1 report generator with a "USB DEVICE
  INFORMATION" section (device label, type, capacity, free space) alongside
  the usual scan time, baseline info, and per-file MODIFIED/ADDED/DELETED/
  UNCHANGED details with old/new SHA-256 hashes. Saved under `reports/usb/`.
- **Full USB timeline** — the same history/timeline feature from Phase 1
  works identically for USB baselines.

---

## Project Structure

```
fim_tool/
├── main.py                 # entry point
├── requirements.txt
├── .gitignore
├── core/
│   ├── scanner.py          # folder/USB walk + SHA-256 hashing + text content capture (shared)
│   ├── baseline.py         # create / save / load baseline JSON + history init (shared)
│   ├── comparator.py       # diff engine: MODIFIED/ADDED/DELETED/UNCHANGED + history (shared)
│   ├── report.py           # .txt / .json report generation, incl. USB device info block (shared)
│   └── usb_detector.py     # Phase 2 — USB/removable-drive detection (Win32 ctypes)
├── ui/
│   └── main_window.py      # PySide6 GUI — Folder Monitoring tab + USB Monitoring tab
├── data/
│   └── usb_baselines/      # USB baselines saved here by default (kept separate from folder baselines)
└── reports/
    └── usb/                # USB reports saved here by default (kept separate from folder reports)
```

Nothing was renamed or moved from Phase 1 — Phase 2 only added
`core/usb_detector.py` and extended `ui/main_window.py`, `core/comparator.py`,
and `core/report.py` in backward-compatible ways (folder baselines/reports
are completely unaffected; the added fields are purely additive and only
populated when a baseline is USB-backed).

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

   No new dependency was added for Phase 2 — USB detection uses only the
   Python standard library (`ctypes`), per the requirement to prefer
   stdlib/Windows-native approaches.

3. **Run the app:**

   ```bash
   python main.py
   ```

---

## How to Test Phase 1 (Folder Monitoring tab)

1. Click **Browse...**, select a test folder, click **Create Baseline**, save it.
2. Modify/add/delete files outside the app.
3. Click **Run Scan & Compare** — verify MODIFIED / ADDED / DELETED / UNCHANGED.
4. Double-click a changed row to see its diff/preview.
5. Click **View Full Timeline** to see the full chronological history.
6. Export a `.txt` or `.json` report.

## How to Test Phase 2 (USB Monitoring tab)

1. Switch to the **USB Monitoring** tab.
2. Connect a USB/removable drive to your Windows machine.
3. Click **Refresh** — the drive should appear with its letter, label, type,
   capacity, and free space.
4. Select it in the list and click **Select USB** — its info appears below
   with status "Ready".
5. Click **Create USB Baseline** — the drive is recursively scanned and
   hashed; save the baseline when prompted (defaults to `data/usb_baselines/`).
6. On the USB itself, safely modify/add/delete a few harmless test files.
7. Click **Run USB Scan & Compare** — verify MODIFIED / ADDED / DELETED /
   UNCHANGED results appear, color-coded, same as the folder workflow.
8. Double-click a row to see what actually changed.
9. Click **View USB Timeline** to see the chronological history for that USB.
10. Click **Export Report (.txt)** or **(.json)** — verify the report
    includes a "USB DEVICE INFORMATION" section plus full file details.

You can also reload a previously saved USB baseline via **Load USB
Baseline** without needing to recreate it — useful across sessions as long
as the drive is reconnected with the same drive letter (see limitation below).

---

## Notes / Known Scope Limits

**Carried over from Phase 1:**
- No real-time/continuous monitoring — this is scan-on-demand.
- No rename detection — shows as one DELETED + one ADDED entry.
- Content diffing only works for text files under 2 MB
  (`MAX_DIFFABLE_FILE_SIZE` in `core/scanner.py`).
- Baselines saved before the diff/history features were added will show
  "content not available" for old entries.
- Hidden files/folders are skipped by default (`skip_hidden=True` in
  `core/scanner.py`).

**New in Phase 2:**
- **USB detection is Windows-native.** On macOS/Linux the app falls back to
  a best-effort mount-point scan (`/Volumes` on macOS, `/media` or `/mnt` on
  Linux) purely so the app remains runnable for development — it is not a
  substitute for the Win32 `GetDriveTypeW`-based detection used on Windows,
  which is what this phase specifically targets.
- **Drive letters are not guaranteed stable.** If a USB is baselined as
  `E:\` and later reconnected as `F:\`, the saved baseline still points at
  `E:\` and a scan will fail until reconnected under the same letter (or the
  baseline is recreated). Matching by volume serial number instead of drive
  letter would fix this and is a good candidate for a later phase.
- Nothing on the USB is ever modified or executed — scanning is strictly
  read-only (`open(..., "rb")` for hashing/content capture).

**Explicitly NOT implemented in Phase 2 (by design — reserved for a later phase):**
automatic malware detection, VirusTotal, YARA, threat intelligence, malware
classification, automatic quarantine or deletion, real-time USB security
monitoring, suspicious-file scoring, advanced threat analysis, network
monitoring, machine learning, automatic response. Phase 2 is integrity
monitoring only — a MODIFIED file is reported as MODIFIED, never as a threat.

---

## Changelog

**v2.0 — Phase 2.0: USB-Aware FIM**
- New `core/usb_detector.py`: Windows-native removable-drive detection via
  `ctypes` (no new dependency).
- New **USB Monitoring** tab in the existing UI: device list + Refresh,
  Select USB, Scan/Baseline/Compare/Timeline, results table, report export —
  fully parallel to the Folder Monitoring tab, reusing the same core engine.
- `core/comparator.py`: comparison results now carry baseline metadata
  through, so reports can show device info when relevant (backward-compatible).
- `core/report.py`: added a "USB DEVICE INFORMATION" section to both the
  single-scan and timeline reports, shown only when a baseline is USB-backed.
- USB baselines/reports are stored separately (`data/usb_baselines/`,
  `reports/usb/`) so they never collide with Phase 1 folder data.
- All Phase 1 folder-monitoring functionality is fully preserved and
  unaffected.

**v1.3 — Full timeline / history tracking**
- Every scan appends to a persistent history log inside the baseline.
- New `record_scan()` rolls the snapshot forward and logs each event.
- New **View Full Timeline** dialog shows the entire chronological story.
- History auto-saves back to the baseline JSON file after every scan.

**v1.2 — Content diffs**
- Scanner captures full text content (files under 2 MB, non-binary) so
  comparisons show real diffs, not just "the hash changed".
- MODIFIED files get a unified diff; ADDED/DELETED files get a content
  preview. Binary/oversized files fall back gracefully.
- New double-click Detail Dialog in the GUI.

**v1.1 — Folder tracking + report readability**
- Fixed: empty folders and folder add/remove are now tracked.
- Results table gained a Type column (File/Folder).
- Rewrote the text report for readability: plain-English summary, section
  headers, shortened hashes.

**v1.0 — Initial Phase 1.0 release**
- Folder selection, recursive SHA-256 scanning, baseline creation/save/load.
- MODIFIED / ADDED / DELETED / UNCHANGED detection via hash comparison.
- PySide6 desktop UI, background scanning, `.txt`/`.json` report export.

---

## Tech Stack

- Python 3.9+
- PySide6 (Qt for Python) — GUI
- `hashlib` — SHA-256 hashing
- `pathlib` — filesystem traversal
- `difflib` — unified diff generation
- `ctypes` — Windows Win32 API bindings for USB/removable-drive detection (Phase 2)
- `json` — baseline/history storage and report export
