"""
core/scanner.py
--------------------------------------------------------------------
Handles filesystem walking, SHA-256 hashing, and (for text files
under a size limit) content capture so later comparisons can show
an actual line-by-line diff, not just "the hash changed".

scan_folder() recursively walks a directory tree and returns a
dictionary describing every file found: its relative path, size,
last-modified timestamp, SHA-256 hash, and — for small text files —
its full text content.
--------------------------------------------------------------------
"""
import hashlib
from pathlib import Path
from datetime import datetime

# Read files in 64KB chunks so large files don't blow up memory usage.
CHUNK_SIZE = 65536

# Text content is only captured (for diffing later) up to this size.
# Larger files, and anything that looks binary, are still hashed
# normally — they just won't have a line-by-line diff available.
MAX_DIFFABLE_FILE_SIZE = 2 * 1024 * 1024  # 2 MB


def compute_sha256(file_path: Path) -> str:
    """
    Compute the SHA-256 hash of a single file.

    Returns the hex digest string, or an "ERROR: ..." string if the
    file could not be read (e.g. permission denied).
    """
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                sha256.update(chunk)
        return sha256.hexdigest()
    except (PermissionError, OSError) as e:
        return f"ERROR: {e}"


def read_text_content(file_path: Path, file_size: int):
    """
    Try to read a file's full text content for later diffing.

    Returns (content, note):
        - content: the decoded text, or None if it couldn't be captured
        - note: None on success, otherwise a short human-readable
                reason it wasn't captured (shown in reports/UI)
    """
    if file_size > MAX_DIFFABLE_FILE_SIZE:
        return None, f"file too large to diff (> {MAX_DIFFABLE_FILE_SIZE // (1024 * 1024)} MB)"

    try:
        raw = file_path.read_bytes()
    except (PermissionError, OSError) as e:
        return None, f"could not read file: {e}"

    if b"\x00" in raw:
        return None, "binary file (not diffable)"

    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "binary or non-UTF-8 file (not diffable)"


def scan_folder(folder_path: str, skip_hidden: bool = True, progress_callback=None) -> dict:
    """
    Recursively scan a folder and return a dict with two keys:

        "files":   {relative_path: {size, modified_time, hash,
                                     content, content_note}, ...}
        "folders": sorted list of relative folder paths (every
                   directory in the tree, including empty ones)

    "content" holds the full text of the file if it's small enough
    and looks like text; otherwise it's None and "content_note"
    explains why (e.g. binary, too large).

    Paths use POSIX-style separators ("/") so results are consistent
    across Windows/macOS/Linux.

    progress_callback, if given, is called as progress_callback(count)
    after each filesystem entry is processed, so a UI can show live
    progress.
    """
    folder = Path(folder_path)
    files = {}
    folders = []

    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"'{folder_path}' is not a valid directory")

    count = 0
    for path in sorted(folder.rglob("*")):
        rel_parts = path.relative_to(folder).parts

        if skip_hidden and any(part.startswith(".") for part in rel_parts):
            continue

        if path.is_dir():
            folders.append(path.relative_to(folder).as_posix())
            count += 1
            if progress_callback:
                progress_callback(count)
            continue

        if not path.is_file():
            continue

        try:
            stat = path.stat()
            rel_path = path.relative_to(folder).as_posix()
            file_hash = compute_sha256(path)
            content, content_note = read_text_content(path, stat.st_size)

            files[rel_path] = {
                "size": stat.st_size,
                "modified_time": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "hash": file_hash,
                "content": content,
                "content_note": content_note,
            }
        except (PermissionError, OSError):
            # Skip entries we can't stat/read rather than crashing the scan.
            continue

        count += 1
        if progress_callback:
            progress_callback(count)

    return {"files": files, "folders": sorted(folders)}
