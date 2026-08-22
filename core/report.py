"""
core/report.py
--------------------------------------------------------------------
Generates human-readable (.txt) and machine-readable (.json)
integrity reports from a comparison result produced by
core.comparator.compare_to_baseline().

The text report leads with a plain-English summary, then breaks
changes into clearly labeled sections, shows the actual line-by-line
diff for modified text files (and a content preview for added/
deleted text files), and shortens hashes for readability. Full,
untruncated hashes are always available in the JSON report.
--------------------------------------------------------------------
"""
import json
from pathlib import Path


def _short(h):
    """Shorten a hash for display; leave None/errors as-is."""
    if not h:
        return "—"
    if h.startswith("ERROR"):
        return h
    return h[:16] + "…"


def _plain_english_summary(s: dict) -> str:
    added_files = s["ADDED"] - s["ADDED_FOLDERS"]
    deleted_files = s["DELETED"] - s["DELETED_FOLDERS"]
    file_changes = s["MODIFIED"] + added_files + deleted_files
    folder_changes = s["ADDED_FOLDERS"] + s["DELETED_FOLDERS"]

    if file_changes == 0 and folder_changes == 0:
        return "No changes detected. Everything matches the baseline."

    parts = []
    if s["MODIFIED"]:
        parts.append(f"{s['MODIFIED']} file(s) modified")
    if added_files:
        parts.append(f"{added_files} file(s) added")
    if deleted_files:
        parts.append(f"{deleted_files} file(s) deleted")
    if s["ADDED_FOLDERS"]:
        parts.append(f"{s['ADDED_FOLDERS']} folder(s) added")
    if s["DELETED_FOLDERS"]:
        parts.append(f"{s['DELETED_FOLDERS']} folder(s) deleted")

    return "Changes detected: " + ", ".join(parts) + "."


def _append_diff_block(lines, item):
    """Append the unified diff (or an explanation why none exists) for a MODIFIED file."""
    diff = item.get("diff")
    if diff:
        lines.append("           --- what changed ---")
        for dl in diff:
            dl = dl.rstrip("\n")
            if dl.startswith("+++") or dl.startswith("---"):
                lines.append(f"           {dl}")
            elif dl.startswith("+"):
                lines.append(f"           + {dl[1:]}")
            elif dl.startswith("-"):
                lines.append(f"           - {dl[1:]}")
            elif dl.startswith("@@"):
                lines.append(f"           {dl}")
            else:
                lines.append(f"             {dl}")
        if item.get("diff_truncated"):
            lines.append("           ... diff truncated (file has more changes than shown) ...")
    else:
        note = item.get("diff_note") or "content changed, but no line-by-line diff is available."
        lines.append(f"           (No diff available: {note})")


def _append_preview_block(lines, item, label):
    """Append a short content preview for an ADDED or DELETED text file."""
    preview = item.get("preview")
    if preview:
        lines.append(f"           --- {label} (preview) ---")
        for pl in preview:
            lines.append(f"           | {pl}")
        if item.get("preview_truncated"):
            lines.append("           ... preview truncated (file has more lines) ...")
    elif item.get("preview_note"):
        lines.append(f"           (No preview available: {item['preview_note']})")


def _device_info_block(lines, metadata):
    """Append a USB device information block if this baseline is USB-backed."""
    if not metadata.get("is_usb"):
        return
    lines.append("-" * 70)
    lines.append("USB DEVICE INFORMATION")
    lines.append("-" * 70)
    lines.append(f"  Device Label : {metadata.get('device_label', 'Unknown')}")
    lines.append(f"  Drive Type   : {metadata.get('device_type', 'Removable')}")
    if metadata.get("device_total_gb") is not None:
        lines.append(f"  Capacity     : {metadata['device_total_gb']} GB")
    if metadata.get("device_free_gb") is not None:
        lines.append(f"  Free Space   : {metadata['device_free_gb']} GB")
    lines.append("")


def generate_text_report(result: dict) -> str:
    s = result["summary"]
    details = result["details"]
    metadata = result.get("metadata", {})

    lines = []
    lines.append("=" * 70)
    lines.append("USB INTEGRITY REPORT" if metadata.get("is_usb") else "FILE INTEGRITY MONITORING REPORT")
    lines.append("=" * 70)
    lines.append(f"Monitored {'Drive' if metadata.get('is_usb') else 'Folder'} : {result['folder']}")
    lines.append(f"Baseline Created  : {result['baseline_created_at']}")
    lines.append(f"Scan Time         : {result['scan_time']}")
    lines.append("")
    _device_info_block(lines, metadata)
    lines.append(_plain_english_summary(s))
    lines.append("")
    lines.append("-" * 70)
    lines.append("SUMMARY COUNTS")
    lines.append("-" * 70)
    lines.append(f"  Modified files   : {s['MODIFIED']}")
    lines.append(f"  Added files      : {s['ADDED'] - s['ADDED_FOLDERS']}")
    lines.append(f"  Deleted files    : {s['DELETED'] - s['DELETED_FOLDERS']}")
    lines.append(f"  Added folders    : {s['ADDED_FOLDERS']}")
    lines.append(f"  Deleted folders  : {s['DELETED_FOLDERS']}")
    lines.append(f"  Unchanged files  : {s['UNCHANGED']}")

    def section(title, predicate):
        items = [d for d in details if predicate(d)]
        if not items:
            return
        lines.append("")
        lines.append("-" * 70)
        lines.append(title)
        lines.append("-" * 70)
        for item in items:
            marker = "[FOLDER]" if item["type"] == "folder" else "[FILE]  "
            lines.append(f"{marker} {item['path']}")
            if item["type"] == "file":
                if item["status"] == "MODIFIED":
                    lines.append(f"           Old hash : {_short(item['old_hash'])}")
                    lines.append(f"           New hash : {_short(item['new_hash'])}")
                    lines.append(f"           Old modified : {item['old_modified']}")
                    lines.append(f"           New modified : {item['new_modified']}")
                    _append_diff_block(lines, item)
                elif item["status"] == "ADDED":
                    lines.append(f"           Hash     : {_short(item['new_hash'])}")
                    lines.append(f"           Modified : {item['new_modified']}")
                    _append_preview_block(lines, item, "content added")
                elif item["status"] == "DELETED":
                    lines.append(f"           Last known hash     : {_short(item['old_hash'])}")
                    lines.append(f"           Last known modified : {item['old_modified']}")
                    _append_preview_block(lines, item, "content that was removed")
            lines.append("")

    section("MODIFIED FILES  (content changed since baseline)", lambda d: d["status"] == "MODIFIED")
    section("ADDED  (new since baseline)", lambda d: d["status"] == "ADDED")
    section("DELETED  (present at baseline, now missing)", lambda d: d["status"] == "DELETED")

    lines.append("-" * 70)
    lines.append(f"{s['UNCHANGED']} file(s) unchanged and matched their baseline hash exactly.")
    lines.append("(Not listed individually - they need no attention.)")
    lines.append("")
    lines.append("Note: hashes above are shortened for readability. Full SHA-256 hashes,")
    lines.append("full diffs, and full previews are available in the .json report.")
    lines.append("Diffs/previews are only available for text files under 2 MB; binary")
    lines.append("or larger files still get MODIFIED/ADDED/DELETED detection via hash,")
    lines.append("just without a line-by-line breakdown.")
    lines.append("=" * 70)
    lines.append("END OF REPORT")
    lines.append("=" * 70)

    return "\n".join(lines)


def save_text_report(result: dict, save_path: str) -> None:
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        f.write(generate_text_report(result))


def save_json_report(result: dict, save_path: str) -> None:
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


# ----------------------------------------------------------------------
# TIMELINE REPORT
# Walks the full history of a baseline (every scan ever recorded) and
# produces one chronological narrative: created -> modified -> modified
# again -> deleted, each event showing its own diff/preview, so you can
# follow exactly what happened to a folder over time.
# ----------------------------------------------------------------------

def _structure_block(lines, files, folders):
    """Describe a folder structure snapshot: files, and folders (marking empty ones)."""
    files = sorted(files)
    folders = sorted(folders)

    if not files and not folders:
        lines.append("  (empty — no files or folders)")
        return

    if folders:
        lines.append(f"  Folders ({len(folders)}):")
        for folder in folders:
            has_contents = any(f.startswith(folder + "/") for f in files)
            tag = "" if has_contents else "  (empty)"
            lines.append(f"    - {folder}/{tag}")

    if files:
        lines.append(f"  Files ({len(files)}):")
        for f in files:
            lines.append(f"    - {f}")


def generate_timeline_report(baseline: dict) -> str:
    """
    Build a full chronological narrative from baseline["history"]:
    one event per scan (plus the original baseline creation), each
    showing exactly what changed since the previous event, with real
    diffs for modified text files and previews for added/deleted ones.
    """
    meta = baseline["metadata"]
    history = baseline.get("history", [])

    lines = []
    lines.append("=" * 70)
    lines.append("USB — FULL TIMELINE REPORT" if meta.get("is_usb") else "FILE INTEGRITY — FULL TIMELINE REPORT")
    lines.append("=" * 70)
    lines.append(f"Monitored {'Drive' if meta.get('is_usb') else 'Folder'} : {meta['monitored_folder']}")
    lines.append(f"Baseline Created  : {meta['created_at']}")
    lines.append(f"Total Events      : {len(history)}")
    lines.append("")
    _device_info_block(lines, meta)

    if not history:
        lines.append("No history recorded yet. Run a scan to start building a timeline.")
        lines.append("=" * 70)
        return "\n".join(lines)

    for i, event in enumerate(history, start=1):
        lines.append("=" * 70)
        if event["event"] == "BASELINE_CREATED":
            lines.append(f"EVENT {i} — BASELINE CREATED  ({event['timestamp']})")
            lines.append("=" * 70)
            lines.append("Initial structure captured:")
            _structure_block(lines, event.get("files", []), event.get("folders", []))
        else:
            lines.append(f"EVENT {i} — SCAN  ({event['timestamp']})")
            lines.append("=" * 70)
            s = event["summary"]
            lines.append(_plain_english_summary(s))
            details = event["details"]

            def sub_section(title, predicate):
                items = [d for d in details if predicate(d)]
                if not items:
                    return
                lines.append("")
                lines.append(f"  {title}")
                for item in items:
                    marker = "[FOLDER]" if item["type"] == "folder" else "[FILE]  "
                    lines.append(f"  {marker} {item['path']}")
                    if item["type"] == "file":
                        if item["status"] == "MODIFIED":
                            _append_diff_block(lines, item)
                        elif item["status"] == "ADDED":
                            _append_preview_block(lines, item, "content added")
                        elif item["status"] == "DELETED":
                            _append_preview_block(lines, item, "content that was removed")
                    lines.append("")

            sub_section("MODIFIED:", lambda d: d["status"] == "MODIFIED")
            sub_section("ADDED:", lambda d: d["status"] == "ADDED")
            sub_section("DELETED:", lambda d: d["status"] == "DELETED")

        lines.append("")

    lines.append("=" * 70)
    lines.append("END OF TIMELINE")
    lines.append("=" * 70)

    return "\n".join(lines)


def save_timeline_text_report(baseline: dict, save_path: str) -> None:
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        f.write(generate_timeline_report(baseline))


def save_timeline_json_report(baseline: dict, save_path: str) -> None:
    """Export the raw history log as JSON (metadata + every event, full detail)."""
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"metadata": baseline["metadata"], "history": baseline.get("history", [])}
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
