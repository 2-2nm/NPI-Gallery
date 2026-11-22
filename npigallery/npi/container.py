import os
import struct
from pathlib import Path
from typing import BinaryIO

from ..filesystem import write_atomic
from .errors import NpiFormatError
from .records import NpiMeta, NpiRecord

MAGIC = b"NPI1"
VERSION = 1
HEADER = struct.Struct("<4sHHIIIIQ")


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    data = stream.read(length)
    if len(data) != length:
        raise NpiFormatError("Unexpected end of file")
    return data


def _read_lengths(stream: BinaryIO) -> tuple[int, int, int, int, int]:
    (
        magic,
        version,
        _flags,
        meta_length,
        exif_length,
        icc_length,
        thumbnail_length,
        payload_length,
    ) = HEADER.unpack(_read_exact(stream, HEADER.size))
    if magic != MAGIC:
        raise NpiFormatError("Not an NPI file")
    if version > VERSION:
        raise NpiFormatError(f"Unsupported NPI version {version}")
    return meta_length, exif_length, icc_length, thumbnail_length, payload_length


def _parse_meta(raw: bytes) -> NpiMeta:
    try:
        return NpiMeta.from_json(raw)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise NpiFormatError("Invalid metadata") from error


def write_npi(
    path: Path,
    meta: NpiMeta,
    exif: bytes,
    icc: bytes,
    thumbnail: bytes,
    payload: bytes,
) -> None:
    meta_raw = meta.to_json()
    header = HEADER.pack(
        MAGIC, VERSION, 0, len(meta_raw), len(exif), len(icc), len(thumbnail), len(payload)
    )
    write_atomic(path, header, meta_raw, exif, icc, thumbnail, payload)


def read_meta(path: Path) -> NpiMeta:
    with open(path, "rb") as stream:
        meta_length, *_ = _read_lengths(stream)
        return _parse_meta(_read_exact(stream, meta_length))


def read_record(path: Path) -> NpiRecord:
    with open(path, "rb") as stream:
        meta_length, exif_length, icc_length, thumbnail_length, payload_length = _read_lengths(stream)
        meta = _parse_meta(_read_exact(stream, meta_length))
        exif = _read_exact(stream, exif_length)
        icc = _read_exact(stream, icc_length)
        thumbnail = _read_exact(stream, thumbnail_length)
        offset = stream.tell()
        stream.seek(0, os.SEEK_END)
        if stream.tell() - offset != payload_length:
            raise NpiFormatError("Payload length mismatch")
    return NpiRecord(meta, exif, icc, thumbnail, offset, payload_length)


def read_payload(path: Path, record: NpiRecord) -> bytes:
    with open(path, "rb") as stream:
        stream.seek(record.payload_offset)
        return _read_exact(stream, record.payload_length)
