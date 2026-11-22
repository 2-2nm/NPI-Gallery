from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..filesystem import remove_file
from ..npi import (
    NPI_EXTENSION,
    NpiError,
    is_already_restored,
    is_image_path,
    is_npi_path,
    pack_file,
    read_meta,
    restore_target,
    unpack_file,
    verify_npi,
)
from .scanner import iter_media_paths

ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], bool]
StatsCallback = Callable[["BatchResult"], None]


@dataclass
class BatchResult:
    processed: int = 0
    skipped: int = 0
    failures: list[tuple[Path, str]] = field(default_factory=list)
    cancelled: bool = False


def _target_directory(source: Path, root: Path, destination: Path | None) -> Path:
    if destination is None:
        return source.parent
    return destination / source.parent.relative_to(root)


def _npi_candidates(directory: Path, source: Path) -> list[Path]:
    return [
        directory / (source.stem + NPI_EXTENSION),
        directory / (source.name + NPI_EXTENSION),
    ]


def _find_packed(candidates: list[Path], source: Path) -> Path | None:
    stat = source.stat()
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            meta = read_meta(candidate)
        except NpiError:
            continue
        if (
            meta.original_name == source.name
            and meta.size == stat.st_size
            and meta.mtime_ns == stat.st_mtime_ns
        ):
            return candidate
    return None


def _free_candidate(candidates: list[Path]) -> Path:
    for candidate in candidates:
        if not candidate.exists():
            return candidate
    stem = candidates[-1].with_suffix("")
    for index in range(1, 10000):
        candidate = stem.with_name(f"{stem.name} ({index}){NPI_EXTENSION}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(str(candidates[-1]))


def _dump_one(source: Path, directory: Path, delete_original: bool) -> bool:
    directory.mkdir(parents=True, exist_ok=True)
    candidates = _npi_candidates(directory, source)
    existing = _find_packed(candidates, source)
    if existing is not None:
        if delete_original:
            verify_npi(existing)
            remove_file(source)
        return False
    target = _free_candidate(candidates)
    try:
        pack_file(source, target)
        verify_npi(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if delete_original:
        remove_file(source)
    return True


def _restore_one(source: Path, directory: Path, delete_npi: bool) -> bool:
    meta = read_meta(source)
    if is_already_restored(meta, restore_target(meta, directory)):
        if delete_npi:
            remove_file(source)
        return False
    unpack_file(source, directory)
    if delete_npi:
        remove_file(source)
    return True


def dump_folder(
    source_dir: Path,
    destination_dir: Path | None = None,
    recursive: bool = True,
    delete_originals: bool = False,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
    on_stats: StatsCallback | None = None,
) -> BatchResult:
    files = [path for path in iter_media_paths(source_dir, recursive) if is_image_path(path)]
    result = BatchResult()
    for index, source in enumerate(files, 1):
        if should_cancel is not None and should_cancel():
            result.cancelled = True
            break
        try:
            directory = _target_directory(source, source_dir, destination_dir)
            if _dump_one(source, directory, delete_originals):
                result.processed += 1
            else:
                result.skipped += 1
        except Exception as error:
            result.failures.append((source, str(error)))
        if on_progress is not None:
            on_progress(index, len(files))
        if on_stats is not None:
            on_stats(result)
    return result


def restore_folder(
    source_dir: Path,
    destination_dir: Path | None = None,
    recursive: bool = True,
    delete_npi: bool = False,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
    on_stats: StatsCallback | None = None,
) -> BatchResult:
    files = [path for path in iter_media_paths(source_dir, recursive) if is_npi_path(path)]
    result = BatchResult()
    for index, source in enumerate(files, 1):
        if should_cancel is not None and should_cancel():
            result.cancelled = True
            break
        try:
            directory = _target_directory(source, source_dir, destination_dir)
            if _restore_one(source, directory, delete_npi):
                result.processed += 1
            else:
                result.skipped += 1
        except Exception as error:
            result.failures.append((source, str(error)))
        if on_progress is not None:
            on_progress(index, len(files))
        if on_stats is not None:
            on_stats(result)
    return result
