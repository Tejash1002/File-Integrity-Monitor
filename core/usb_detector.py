"""
core/usb_detector.py
--------------------------------------------------------------------
Phase 2.0 — USB / removable-drive detection.

Windows-first implementation using ctypes bindings to the Win32 API
(kernel32.dll) — no third-party dependency required. On non-Windows
platforms, falls back to a best-effort psutil-free scan of mounted
filesystems so the app doesn't crash during development/testing on
macOS/Linux, though "removable" classification is Windows-only in
the strict sense requested by this phase.

Responsibilities (and ONLY these — detection/info, nothing else):
    - Enumerate connected drives
    - Identify which ones are removable
    - Report drive letter, label, type, capacity, free space

Does NOT scan files, hash anything, or touch USB contents — that
stays in core/scanner.py, reused as-is from Phase 1.
--------------------------------------------------------------------
"""
import platform
import string
from dataclasses import dataclass
from typing import List, Optional

IS_WINDOWS = platform.system() == "Windows"

# Win32 GetDriveType() return values
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

DRIVE_TYPE_NAMES = {
    DRIVE_UNKNOWN: "Unknown",
    DRIVE_NO_ROOT_DIR: "No Root Directory",
    DRIVE_REMOVABLE: "Removable",
    DRIVE_FIXED: "Fixed (Internal)",
    DRIVE_REMOTE: "Network",
    DRIVE_CDROM: "CD-ROM",
    DRIVE_RAMDISK: "RAM Disk",
}


@dataclass
class UsbDevice:
    """A detected removable/candidate drive and its basic info."""
    drive_letter: str          # e.g. "E:\\"
    label: str                 # volume label, or "(No Label)"
    drive_type: str            # human-readable, e.g. "Removable"
    is_removable: bool
    total_bytes: Optional[int]
    free_bytes: Optional[int]
    accessible: bool           # False if we couldn't query the drive (e.g. no media)
    error: Optional[str] = None

    @property
    def total_gb(self) -> Optional[float]:
        return round(self.total_bytes / (1024 ** 3), 2) if self.total_bytes else None

    @property
    def free_gb(self) -> Optional[float]:
        return round(self.free_bytes / (1024 ** 3), 2) if self.free_bytes else None

    def display_block(self) -> str:
        """A formatted block matching the requested report style."""
        lines = ["-" * 42]
        lines.append(f"Drive: {self.drive_letter}")
        lines.append(f"Label: {self.label}")
        lines.append(f"Type: {self.drive_type}")
        if self.accessible:
            lines.append(f"Capacity: {self.total_gb} GB" if self.total_gb is not None else "Capacity: Unknown")
            lines.append(f"Free Space: {self.free_gb} GB" if self.free_bytes is not None else "Free Space: Unknown")
        else:
            lines.append(f"Status: Not accessible ({self.error or 'no media / not ready'})")
        lines.append("-" * 42)
        return "\n".join(lines)


def _get_volume_label_windows(drive_letter: str) -> str:
    """Query the volume label for a drive using GetVolumeInformationW."""
    import ctypes
    kernel32 = ctypes.windll.kernel32
    volume_name_buf = ctypes.create_unicode_buffer(261)
    fs_name_buf = ctypes.create_unicode_buffer(261)
    serial = ctypes.c_uint(0)
    max_component_len = ctypes.c_uint(0)
    fs_flags = ctypes.c_uint(0)

    ok = kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(drive_letter),
        volume_name_buf, ctypes.sizeof(volume_name_buf),
        ctypes.byref(serial),
        ctypes.byref(max_component_len),
        ctypes.byref(fs_flags),
        fs_name_buf, ctypes.sizeof(fs_name_buf),
    )
    if ok and volume_name_buf.value:
        return volume_name_buf.value
    return "(No Label)"


def _get_disk_usage_windows(drive_letter: str):
    """Return (total_bytes, free_bytes) using GetDiskFreeSpaceExW, or (None, None)."""
    import ctypes
    kernel32 = ctypes.windll.kernel32
    free_bytes_avail = ctypes.c_ulonglong(0)
    total_bytes = ctypes.c_ulonglong(0)
    total_free_bytes = ctypes.c_ulonglong(0)

    ok = kernel32.GetDiskFreeSpaceExW(
        ctypes.c_wchar_p(drive_letter),
        ctypes.byref(free_bytes_avail),
        ctypes.byref(total_bytes),
        ctypes.byref(total_free_bytes),
    )
    if ok:
        return total_bytes.value, free_bytes_avail.value
    return None, None


def _detect_windows() -> List[UsbDevice]:
    """Enumerate all drive letters on Windows and classify each one."""
    import ctypes
    kernel32 = ctypes.windll.kernel32

    devices = []
    bitmask = kernel32.GetLogicalDrives()

    for i, letter in enumerate(string.ascii_uppercase):
        if not (bitmask & (1 << i)):
            continue

        drive_letter = f"{letter}:\\"
        drive_type_code = kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive_letter))
        drive_type_name = DRIVE_TYPE_NAMES.get(drive_type_code, "Unknown")
        is_removable = drive_type_code == DRIVE_REMOVABLE

        # Only surface removable drives + unknowns as "candidates"; fixed/
        # network/CD-ROM/ramdisk are not what Phase 2 is asking us to list,
        # per "Do NOT blindly treat every drive as a USB."
        if drive_type_code not in (DRIVE_REMOVABLE,):
            continue

        try:
            total_bytes, free_bytes = _get_disk_usage_windows(drive_letter)
            label = _get_volume_label_windows(drive_letter)
            accessible = total_bytes is not None
            error = None if accessible else "drive not ready (no media inserted?)"
        except OSError as e:
            total_bytes, free_bytes, label = None, None, "(No Label)"
            accessible = False
            error = str(e)

        devices.append(UsbDevice(
            drive_letter=drive_letter,
            label=label,
            drive_type=drive_type_name,
            is_removable=is_removable,
            total_bytes=total_bytes,
            free_bytes=free_bytes,
            accessible=accessible,
            error=error,
        ))

    return devices


def _detect_non_windows() -> List[UsbDevice]:
    """
    Best-effort fallback for macOS/Linux so the app is runnable during
    development/testing off Windows. Not a substitute for the Win32
    detection above — removable-media semantics differ per OS and are
    out of scope here per the brief (Windows-compatible detection).
    """
    import os
    candidates = []

    if platform.system() == "Darwin":
        volumes_dir = "/Volumes"
        if os.path.isdir(volumes_dir):
            for name in os.listdir(volumes_dir):
                path = os.path.join(volumes_dir, name)
                if name == "Macintosh HD" or not os.path.ismount(path):
                    continue
                try:
                    usage = os.statvfs(path)
                    total = usage.f_frsize * usage.f_blocks
                    free = usage.f_frsize * usage.f_bavail
                    candidates.append(UsbDevice(
                        drive_letter=path, label=name, drive_type="Removable (assumed)",
                        is_removable=True, total_bytes=total, free_bytes=free,
                        accessible=True,
                    ))
                except OSError as e:
                    candidates.append(UsbDevice(
                        drive_letter=path, label=name, drive_type="Removable (assumed)",
                        is_removable=True, total_bytes=None, free_bytes=None,
                        accessible=False, error=str(e),
                    ))
    else:
        # Linux: common auto-mount locations for removable media.
        for base in (f"/media/{os.environ.get('USER', '')}", "/media", "/mnt"):
            if not os.path.isdir(base):
                continue
            for name in os.listdir(base):
                path = os.path.join(base, name)
                if not os.path.ismount(path):
                    continue
                try:
                    usage = os.statvfs(path)
                    total = usage.f_frsize * usage.f_blocks
                    free = usage.f_frsize * usage.f_bavail
                    candidates.append(UsbDevice(
                        drive_letter=path, label=name, drive_type="Removable (assumed)",
                        is_removable=True, total_bytes=total, free_bytes=free,
                        accessible=True,
                    ))
                except OSError as e:
                    candidates.append(UsbDevice(
                        drive_letter=path, label=name, drive_type="Removable (assumed)",
                        is_removable=True, total_bytes=None, free_bytes=None,
                        accessible=False, error=str(e),
                    ))

    return candidates


def detect_usb_devices() -> List[UsbDevice]:
    """
    Public entry point: return all currently detected removable
    drives. Windows uses the real Win32 drive-type API; other
    platforms use a best-effort mount-point scan (see docstring
    above) so the app still runs for development/testing.
    """
    if IS_WINDOWS:
        return _detect_windows()
    return _detect_non_windows()
