import json
from dataclasses import asdict, dataclass, fields

from .formats import ImageFormat


@dataclass(frozen=True)
class NpiMeta:
    format_code: int
    original_name: str
    mtime_ns: int
    size: int
    width: int
    height: int
    sha256: str
    taken_at: float | None = None

    @property
    def image_format(self) -> ImageFormat:
        return ImageFormat.from_code(self.format_code)

    def to_json(self) -> bytes:
        text = json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))
        return text.encode("utf-8")

    @classmethod
    def from_json(cls, raw: bytes) -> "NpiMeta":
        data = json.loads(raw.decode("utf-8"))
        known = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})


@dataclass(frozen=True)
class NpiRecord:
    meta: NpiMeta
    exif: bytes
    icc: bytes
    thumbnail: bytes
    payload_offset: int
    payload_length: int
