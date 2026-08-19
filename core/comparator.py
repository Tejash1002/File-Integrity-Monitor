"""
core/comparator.py
--------------------------------------------------------------------
Compares two folder snapshots (old vs new) and classifies each
item as UNCHANGED, MODIFIED, ADDED, or DELETED, with diffs/previews
for text files.

Two entry points:

    compare_to_baseline(baseline)
        One-off comparison: current disk state vs the baseline's
        stored snapshot. Does NOT modify the baseline or record
        history. Useful for a quick "what's different right now"
        check.

    record_scan(baseline)
        Does the same comparison, but ALSO appends the result to
        baseline["history"] and rolls baseline["files"]/["folders"]
        forward to the current state, so the NEXT scan compares
        against this point in time. This is what builds up a full
        timeline: created -> modified -> modified again -> deleted.
        Returns (result, updated_baseline) — the caller is
        responsible for saving updated_baseline to disk if it wants
        the history to persist.
--------------------------------------------------------------------
"""
import difflib
from datetime import datetime

from core.scanner import scan_folder

# Cap how many diff lines we keep per file, so one huge file can't
# blow up the report. The UI/report notes when a diff was truncated.
MAX_DIFF_LINES = 200
PREVIEW_LINES = 10


def _build_diff(old_content, new_content):
    """Return a capped list of unified-diff lines between two texts."""
    diff = list(difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile="before", tofile="after", lineterm="",
    ))
    truncated = len(diff) > MAX_DIFF_LINES
    return diff[:MAX_DIFF_LINES], truncated


def _preview(content):
    """Return the first few lines of a file's content for ADDED/DELETED previews."""
    lines = content.splitlines()
    truncated = len(lines) > PREVIEW_LINES
    return lines[:PREVIEW_LINES], truncated


def _diff_snapshots(old_files, old_folders, new_files, new_folders):
    """
    Core diff engine: compares two (files, folders) snapshots and
    returns (summary, details). Used by both compare_to_baseline and
    record_scan so "compare against baseline" and "compare against
    last scan" behave identically.
    """
    details = []
    old_paths = set(old_files.keys())
    new_paths = set(new_files.keys())

    for path in old_paths & new_paths:
        old_rec = old_files[path]
        new_rec = new_files[path]
        old_hash = old_rec["hash"]
        new_hash = new_rec["hash"]
        status = "UNCHANGED" if old_hash == new_hash else "MODIFIED"

        item = {
            "type": "file", "path": path, "status": status,
            "old_hash": old_hash, "new_hash": new_hash,
            "old_size": old_rec["size"], "new_size": new_rec["size"],
            "old_modified": old_rec["modified_time"], "new_modified": new_rec["modified_time"],
            "diff": None, "diff_truncated": False, "diff_note": None,
        }

        if status == "MODIFIED":
            old_content = old_rec.get("content")
            new_content = new_rec.get("content")
            if old_content is not None and new_content is not None:
                diff_lines, truncated = _build_diff(old_content, new_content)
                item["diff"] = diff_lines
                item["diff_truncated"] = truncated
            else:
                item["diff_note"] = old_rec.get("content_note") or new_rec.get("content_note") or \
                    "content not available for diffing (baseline may predate this feature)"

        details.append(item)

    for path in new_paths - old_paths:
        new_rec = new_files[path]
        content = new_rec.get("content")
        preview, preview_truncated = (_preview(content) if content is not None else (None, False))
        details.append({
            "type": "file", "path": path, "status": "ADDED",
            "old_hash": None, "new_hash": new_rec["hash"],
            "old_size": None, "new_size": new_rec["size"],
            "old_modified": None, "new_modified": new_rec["modified_time"],
            "preview": preview, "preview_truncated": preview_truncated,
            "preview_note": None if content is not None else new_rec.get("content_note"),
        })

    for path in old_paths - new_paths:
        old_rec = old_files[path]
        content = old_rec.get("content")
        preview, preview_truncated = (_preview(content) if content is not None else (None, False))
        details.append({
            "type": "file", "path": path, "status": "DELETED",
            "old_hash": old_rec["hash"], "new_hash": None,
            "old_size": old_rec["size"], "new_size": None,
            "old_modified": old_rec["modified_time"], "new_modified": None,
            "preview": preview, "preview_truncated": preview_truncated,
            "preview_note": None if content is not None else old_rec.get("content_note"),
        })

    for path in new_folders - old_folders:
        details.append({
            "type": "folder", "path": path, "status": "ADDED",
            "old_hash": None, "new_hash": None, "old_size": None, "new_size": None,
            "old_modified": None, "new_modified": None,
        })

    for path in old_folders - new_folders:
        details.append({
            "type": "folder", "path": path, "status": "DELETED",
            "old_hash": None, "new_hash": None, "old_size": None, "new_size": None,
            "old_modified": None, "new_modified": None,
        })

    status_priority = {"MODIFIED": 0, "ADDED": 1, "DELETED": 2, "UNCHANGED": 3}
    type_priority = {"folder": 0, "file": 1}
    details.sort(key=lambda d: (status_priority[d["status"]], type_priority[d["type"]], d["path"]))

    def count(status, type_=None):
        return sum(1 for d in details if d["status"] == status and (type_ is None or d["type"] == type_))

    summary = {
        "UNCHANGED": count("UNCHANGED"), "MODIFIED": count("MODIFIED"),
        "ADDED": count("ADDED"), "DELETED": count("DELETED"),
        "ADDED_FOLDERS": count("ADDED", "folder"), "DELETED_FOLDERS": count("DELETED", "folder"),
    }
    return summary, details


def compare_to_baseline(baseline: dict, progress_callback=None) -> dict:
    """
    One-off comparison of the current disk state against the
    baseline's stored snapshot. Does not modify the baseline.
    """
    folder_path = baseline["metadata"]["monitored_folder"]
    scan = scan_folder(folder_path, progress_callback=progress_callback)

    summary, details = _diff_snapshots(
        baseline["files"], set(baseline.get("folders", [])),
        scan["files"], set(scan["folders"]),
    )

    return {
        "folder": folder_path,
        "baseline_created_at": baseline["metadata"]["created_at"],
        "scan_time": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "details": details,
    }


def record_scan(baseline: dict, progress_callback=None):
    """
    Compare current disk state against the baseline's last known
    snapshot, append the result to baseline["history"], and roll the
    snapshot forward to the current state.

    Returns (result, baseline) — result has the same shape as
    compare_to_baseline()'s return value; baseline is the same dict,
    mutated in place (also returned for convenience).
    """
    folder_path = baseline["metadata"]["monitored_folder"]
    scan = scan_folder(folder_path, progress_callback=progress_callback)

    summary, details = _diff_snapshots(
        baseline["files"], set(baseline.get("folders", [])),
        scan["files"], set(scan["folders"]),
    )

    timestamp = datetime.now().isoformat(timespec="seconds")
    result = {
        "folder": folder_path,
        "baseline_created_at": baseline["metadata"]["created_at"],
        "scan_time": timestamp,
        "summary": summary,
        "details": details,
    }

    baseline.setdefault("history", [])
    baseline["history"].append({
        "event": "SCAN",
        "timestamp": timestamp,
        "summary": summary,
        "details": details,
    })

    # Roll the snapshot forward so the next scan compares from here.
    baseline["files"] = scan["files"]
    baseline["folders"] = scan["folders"]

    return result, baseline
