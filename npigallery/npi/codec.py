import hashlib
import io
import os
from pathlib import Path

from ..filesystem import unique_path, write_atomic
from .container import read_payload, read_record, write_npi
from .errors import NpiIntegrityError, UnsupportedImageError
from .formats import ImageFormat, detect_format, is_npi_path
from .imaging import build_thumbnail, inspect_image
from .records import NpiMeta


def pack_file(source: Path, destination: Path) -> NpiMeta:
    data = source.read_bytes()
    image_format = detect_format(data)
    if image_format is ImageFormat.UNKNOWN:
        raise UnsupportedImageError(f"Unrecognized image format: {source.name}")
    info = inspect_image(data)
    thumbnail = build_thumbnail(io.BytesIO(data))
    meta = NpiMeta(
        format_code=int(image_format),
        original_name=source.name,
        mtime_ns=source.stat().st_mtime_ns,
        size=len(data),
        width=info.width,
        height=info.height,
        sha256=hashlib.sha256(data).hexdigest(),
        taken_at=info.taken_at,
    )
    write_npi(destination, meta, info.exif, info.icc, thumbnail, data)
    return meta


def _check_payload(payload: bytes, meta: NpiMeta) -> None:
    if len(payload) != meta.size or hashlib.sha256(payload).hexdigest() != meta.sha256:
        raise NpiIntegrityError("Payload checksum mismatch")


def verify_npi(path: Path) -> NpiMeta:
    record = read_record(path)
    _check_payload(read_payload(path, record), record.meta)
    return record.meta


def read_image_bytes(path: Path) -> bytes:
    if is_npi_path(path):
        return read_payload(path, read_record(path))
    return path.read_bytes()


def restore_target(meta: NpiMeta, destination_dir: Path) -> Path:
    return destination_dir / Path(meta.original_name).name


def is_already_restored(meta: NpiMeta, target: Path) -> bool:
    try:
        if not target.is_file() or target.stat().st_size != meta.size:
            return False
        return hashlib.sha256(target.read_bytes()).hexdigest() == meta.sha256
    except OSError:
        return False


def unpack_file(npi_path: Path, destination_dir: Path, overwrite: bool = False) -> Path:
    record = read_record(npi_path)
    payload = read_payload(npi_path, record)
    _check_payload(payload, record.meta)
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = restore_target(record.meta, destination_dir)
    if not overwrite:
        target = unique_path(target)
    write_atomic(target, payload)
    os.utime(target, ns=(record.meta.mtime_ns, record.meta.mtime_ns))
    return target
