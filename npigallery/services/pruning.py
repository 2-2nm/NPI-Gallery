"""Housekeeping after a scan: forget things that belong to photos that are really gone."""

from collections.abc import Iterable
from pathlib import Path


def _under(path: Path, roots: Iterable[Path]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def missing_favorites(favorites: Iterable[str], present: set[str], roots: Iterable[Path]) -> set[str]:
    """Favourites whose file was deleted from a library folder that is still there.

    A favourite is only dropped when all of these hold, so nothing is lost by accident:
    the scan did not find it, it lives under a library folder that exists right now (an unplugged
    drive or a network share that is down keeps its favourites), and the file is truly gone.
    """
    usable = [root for root in roots if root.is_dir()]
    if not usable:
        return set()
    gone = set()
    for key in favorites:
        if key in present:
            continue
        path = Path(key)
        if _under(path, usable) and not path.exists():
            gone.add(key)
    return gone