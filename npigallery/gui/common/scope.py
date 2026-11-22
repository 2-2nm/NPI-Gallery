import calendar
import time
from dataclasses import dataclass
from pathlib import Path

PLACE_DECIMALS = 2


def date_key(timestamp: float) -> tuple[int, int] | None:
    try:
        moment = time.localtime(timestamp)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.tm_year, moment.tm_mon


def place_cell(latitude: float | None, longitude: float | None) -> tuple[float, float] | None:
    if latitude is None or longitude is None:
        return None
    return round(latitude, PLACE_DECIMALS) + 0.0, round(longitude, PLACE_DECIMALS) + 0.0


def place_label(cell: tuple[float, float]) -> str:
    return f"{cell[0]:.{PLACE_DECIMALS}f}, {cell[1]:.{PLACE_DECIMALS}f}"


def date_label(year: int, month: int = 0) -> str:
    return f"{calendar.month_name[month]} {year}" if month else str(year)


@dataclass(frozen=True)
class Scope:
    kind: str = "gallery"
    path: Path | None = None
    year: int = 0
    month: int = 0
    cell: tuple[float, float] | None = None
    name: str = ""

    @staticmethod
    def gallery() -> "Scope":
        return Scope("gallery")

    @staticmethod
    def favorites() -> "Scope":
        return Scope("favorites")

    @staticmethod
    def album(name: str) -> "Scope":
        return Scope("album", name=name)

    @staticmethod
    def photos() -> "Scope":
        return Scope("photos")

    @staticmethod
    def videos() -> "Scope":
        return Scope("videos")

    @staticmethod
    def pc() -> "Scope":
        return Scope("pc")

    @staticmethod
    def folder(path: Path) -> "Scope":
        return Scope("folder", path)

    @staticmethod
    def date(year: int, month: int = 0) -> "Scope":
        return Scope("date", year=year, month=month)

    @staticmethod
    def place(cell: tuple[float, float]) -> "Scope":
        return Scope("place", cell=cell)