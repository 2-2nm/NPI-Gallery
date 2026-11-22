"""The order File Explorer shows a folder in, for photos opened from Explorer.

Explorer only passes the clicked file to the app, so the neighbours in the viewer would follow the
app's own order (newest first) instead of what the person sees. When an Explorer window is showing
that folder, its sort columns are read through the Shell COM object and the folder's photos are put
in the same order. Anything unexpected returns None and the caller keeps its own order.
"""

import functools
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from .scanner import MediaItem

QUERY_TIMEOUT = 2.0
CREATE_NO_WINDOW = 0x08000000

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$folder = $env:NPI_FOLDER
$file = $env:NPI_FILE
$best = $null
foreach ($window in (New-Object -ComObject Shell.Application).Windows()) {
    try {
        $view = $window.Document
        if ($view -eq $null -or $view.Folder -eq $null) { continue }
        if ($view.Folder.Self.Path -ne $folder) { continue }
        if ($best -eq $null) { $best = $view }
        if ($view.FocusedItem -ne $null -and $view.FocusedItem.Path -eq $file) { $best = $view; break }
    } catch { }
}
if ($best -ne $null) { [Console]::Out.Write($best.SortColumns) }
"""


def is_supported() -> bool:
    return sys.platform == "win32"


def parse_sort_columns(text: str) -> list[tuple[str, bool]]:
    """'prop:-System.DateModified;System.ItemNameDisplay;' -> [('System.DateModified', True), ('System.ItemNameDisplay', False)]"""
    text = text.strip()
    if text.lower().startswith("prop:"):
        text = text[5:]
    columns = []
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        descending = part.startswith("-")
        columns.append((part.lstrip("+-"), descending))
    return columns


def view_sort(folder: Path, clicked: Path) -> list[tuple[str, bool]] | None:
    """Sort columns of the Explorer window showing `folder` (the one with `clicked` focused, if several)."""
    if not is_supported():
        return None
    environment = dict(os.environ, NPI_FOLDER=str(folder), NPI_FILE=str(clicked))
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
            capture_output=True,
            text=True,
            timeout=QUERY_TIMEOUT,
            env=environment,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    columns = parse_sort_columns(result.stdout)
    return columns or None


def _natural_compare() -> Callable[[str, str], int]:
    if is_supported():
        try:
            import ctypes

            compare = ctypes.windll.shlwapi.StrCmpLogicalW
            compare.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
            return lambda a, b: int(compare(a, b))
        except (OSError, AttributeError):
            pass

    def split(text: str) -> list:
        return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", text)]

    def fallback(a: str, b: str) -> int:
        left, right = split(a), split(b)
        try:
            return (left > right) - (left < right)
        except TypeError:  # a number against text at the same spot
            return (str(left) > str(right)) - (str(left) < str(right))

    return fallback


def _creation_time(item: MediaItem) -> int:
    try:
        return item.path.stat().st_ctime_ns
    except OSError:
        return 0


def _key_for(column: str) -> Callable[[MediaItem], object] | None:
    natural = functools.cmp_to_key(_natural_compare())
    name = column.casefold()
    if name in ("system.itemnamedisplay", "system.filename", "system.itemname"):
        return lambda item: natural(item.path.name)
    if name == "system.datemodified":
        return lambda item: item.mtime_ns
    if name == "system.datecreated":
        return _creation_time
    if name == "system.size":
        return lambda item: item.file_size
    if name in ("system.itemtypetext", "system.itemtype", "system.fileextension"):
        return lambda item: item.path.suffix.casefold()
    return None


def sort_like_explorer(items: list[MediaItem], columns: list[tuple[str, bool]]) -> list[MediaItem] | None:
    """`items` in the order of the sort `columns`; None when a column is not one we can reproduce."""
    keys = []
    for column, descending in columns:
        key = _key_for(column)
        if key is None:
            return None
        keys.append((key, descending))
    natural = functools.cmp_to_key(_natural_compare())
    ordered = sorted(items, key=lambda item: natural(item.path.name))  # ties fall back to the name
    for key, descending in reversed(keys):  # stable sorts, least important column first
        ordered.sort(key=key, reverse=descending)
    return ordered