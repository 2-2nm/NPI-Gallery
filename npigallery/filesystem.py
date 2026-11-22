import os
import shutil
from pathlib import Path

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None


def root_keys(roots) -> tuple[tuple[str, str], ...]:
    """Prepare folders for `is_under`: (folder, folder + separator), case-folded the way the OS compares paths."""
    keys = []
    for root in roots:
        folder = os.path.normcase(str(root))
        keys.append((folder, folder if folder.endswith(os.sep) else folder + os.sep))
    return tuple(keys)


def is_under(path: str, keys) -> bool:
    """True when `path` is one of the folders in `keys` or inside one (`keys` from `root_keys`).

    The same answer as `Path(path).is_relative_to(folder)` for ordinary paths, on plain strings: about
    50x faster, which matters when it runs once per photo of a 10,000+ photo library.
    """
    path = os.path.normcase(path)
    for folder, prefix in keys:
        if path == folder or path.startswith(prefix):
            return True
    return False


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 10000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(str(path))


def write_atomic(path: Path, *chunks: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        with open(temporary, "wb") as stream:
            for chunk in chunks:
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def remove_folder(path: Path) -> None:
    if send2trash is not None:
        send2trash(str(path))
    else:
        shutil.rmtree(path)


def moves_to_trash() -> bool:
    return send2trash is not None


def remove_file(path: Path) -> None:
    if send2trash is not None:
        send2trash(str(path))
    else:
        path.unlink()