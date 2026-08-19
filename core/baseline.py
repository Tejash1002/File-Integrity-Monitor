"""
core/baseline.py
--------------------------------------------------------------------
Handles creation, saving, and loading of the trusted baseline — and
the running history of every scan performed against it.

The baseline JSON document has these top-level keys:

    "metadata": {
        "monitored_folder": "<absolute path>",
        "created_at": "<ISO timestamp baseline was first created>",
        "total_files": <int, at creation time>,
        "total_folders": <int, at creation time>
    },
    "files": {
        "<relative path>": {
            "size": <int>, "modified_time": "<ISO timestamp>",
            "hash": "<sha256 hex digest>", "content": <str|null>,
            "content_note": <str|null>
        }, ...
    },
    "folders": ["<relative path>", ...],
    "history": [
        {
            "event": "BASELINE_CREATED",
            "timestamp": "<ISO timestamp>",
            "files": ["<relative path>", ...],
            "folders": ["<relative path>", ...]
        },
        {
            "event": "SCAN",
            "timestamp": "<ISO timestamp>",
            "summary": {...},
            "details": [...]   # what changed since the previous event
        },
        ...
    ]

IMPORTANT: "files" / "folders" always hold the MOST RECENT known
snapshot of the monitored folder (they roll forward every time a
scan is recorded), while "history" is an append-only log of every
event that has ever happened, in order. This is what lets the app
reconstruct a full timeline: created -> modified -> modified again
-> deleted, each with its own diff, instead of only ever comparing
back to the original baseline.

Storing the baseline as its own JSON file (rather than inside the
monitored folder) is intentional: a real FIM baseline should not
live where an attacker/tamperer could alter it alongside the files
it is meant to protect.
--------------------------------------------------------------------
"""
import json
from pathlib import Path
from datetime import datetime

from core.scanner import scan_folder


def create_baseline(folder_path: str, progress_callback=None) -> dict:
    """Scan a folder and build a fresh baseline dict, with history started."""
    scan = scan_folder(folder_path, progress_callback=progress_callback)
    timestamp = datetime.now().isoformat(timespec="seconds")

    baseline = {
        "metadata": {
            "monitored_folder": str(Path(folder_path).resolve()),
            "created_at": timestamp,
            "total_files": len(scan["files"]),
            "total_folders": len(scan["folders"]),
        },
        "files": scan["files"],
        "folders": scan["folders"],
        "history": [
            {
                "event": "BASELINE_CREATED",
                "timestamp": timestamp,
                "files": sorted(scan["files"].keys()),
                "folders": sorted(scan["folders"]),
            }
        ],
    }
    return baseline


def save_baseline(baseline: dict, save_path: str) -> None:
    """Save a baseline dict to a JSON file, creating parent folders as needed."""
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2)


def load_baseline(baseline_path: str) -> dict:
    """Load a baseline JSON file into a dict."""
    baseline_path = Path(baseline_path)
    if not baseline_path.exists():
        raise FileNotFoundError(f"Baseline file not found: {baseline_path}")

    with open(baseline_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "metadata" not in data or "files" not in data:
        raise ValueError("Selected file does not look like a valid FIM baseline.")

    # Backward-compatible defaults for baselines created before folder
    # tracking / history tracking were added.
    data.setdefault("folders", [])
    data.setdefault("history", [])

    return data
