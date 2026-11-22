"""Backend for the gallery's year rail: decides what every dot stands for.

The rail is a scroll index. Each dot is the start of one *bucket* of photos, and every dot on
screen represents the same unit at any given moment. The unit is adaptive: the finest of
day / week / month / quarter / year whose dots still fit the rail with a comfortable gap, so a
folder holding three weeks of photos gets a dot per day, a few years a dot per month, and
decades a dot per quarter. Nothing is dropped because it happens to be crowded.

This module has no Qt dependency.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Sequence

MIN_STOP_GAP = 12.0  # minimum average pixels between neighbouring dots
LABEL_GAP = 40.0  # minimum pixels between two year labels on the rail


class Unit(Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


@dataclass(frozen=True)
class RailStop:
    frac: float  # 0..1 position along the scroll range
    kind: str  # "major" = first bucket of a year (gets a year label), "minor" = plain dot
    label: str  # what the hover pill says, e.g. "November 2026"
    row: int  # first layout row of the bucket (jump target)
    caption: str = ""  # year text drawn on the rail, major stops only
    pos: float = 0.0  # 0..1 where the dot is drawn on the rail (see Rail.pos_of)


@dataclass(frozen=True)
class Rail:
    unit: Unit
    stops: tuple[RailStop, ...] = ()
    _fracs: tuple[float, ...] = field(default=(), repr=False)
    _poss: tuple[float, ...] = field(default=(), repr=False)
    # (scroll fraction, rail position) pairs, both increasing. The rail is not linear in the scroll
    # range: a library is lumpy (thousands of recent photos, a handful of old ones), so on a linear
    # rail every old year would be squeezed into a few pixels and lose its label. Instead every year
    # start gets at least LABEL_GAP pixels and the stretch in between is interpolated.
    _anchors: tuple[tuple[float, float], ...] = field(default=(), repr=False)

    def pos_of(self, frac: float) -> float:
        """Where on the rail (0..1) a scroll fraction is drawn."""
        if frac >= 1.0:
            return 1.0  # scrolled all the way down: the marker sits at the foot of the rail
        return _interpolate(self._anchors, frac, forward=True)

    def frac_of(self, pos: float) -> float:
        """The scroll fraction (0..1) under a rail position."""
        return _interpolate(self._anchors, pos, forward=False)

    @property
    def majors(self) -> list[RailStop]:
        return [stop for stop in self.stops if stop.kind == "major"]

    def stop_at(self, frac: float) -> RailStop | None:
        """The bucket that contains this scroll fraction."""
        if not self.stops:
            return None
        return self.stops[max(0, bisect_right(self._fracs, frac) - 1)]

    def nearest(self, frac: float, limit: float) -> RailStop | None:
        """The dot closest to ``frac`` if it lies within ``limit`` (in fraction units)."""
        if not self.stops:
            return None
        position = bisect_right(self._fracs, frac)
        candidates = self.stops[max(0, position - 1) : position + 1]
        best = min(candidates, key=lambda stop: abs(stop.frac - frac))
        return best if abs(best.frac - frac) <= limit else None

    def nearest_pos(self, pos: float, limit: float) -> RailStop | None:
        """Like ``nearest`` but in rail positions, which is what the pointer works in."""
        if not self.stops:
            return None
        position = bisect_right(self._poss, pos)
        candidates = self.stops[max(0, position - 1) : position + 1]
        best = min(candidates, key=lambda stop: abs(stop.pos - pos))
        return best if abs(best.pos - pos) <= limit else None


def _interpolate(anchors: Sequence[tuple[float, float]], value: float, forward: bool) -> float:
    """Piecewise-linear map through ``anchors`` (identity when there are none)."""
    value = max(0.0, min(1.0, value))
    if len(anchors) < 2:
        return value
    src, dst = (0, 1) if forward else (1, 0)
    keys = [anchor[src] for anchor in anchors]
    index = bisect_right(keys, value) - 1
    if index >= len(anchors) - 1:
        return anchors[-1][dst]
    index = max(0, index)
    k0, k1 = anchors[index][src], anchors[index + 1][src]
    v0, v1 = anchors[index][dst], anchors[index + 1][dst]
    if k1 <= k0:
        return v0
    return v0 + (v1 - v0) * (value - k0) / (k1 - k0)


def _spread_years(fracs: list[float], gap: float) -> list[float]:
    """Rail positions for the year starts: as close to their true place as possible, but at least
    ``gap`` apart (and inside 0..1)."""
    count = len(fracs)
    if count == 0:
        return []
    if count > 1:
        gap = min(gap, 1.0 / (count - 1))
    placed = list(fracs)
    for i in range(1, count):  # push later years down...
        placed[i] = max(placed[i], placed[i - 1] + gap)
    placed[-1] = min(placed[-1], 1.0)
    for i in range(count - 2, -1, -1):  # ...then pull earlier ones up where the end ran out of room
        placed[i] = min(placed[i], placed[i + 1] - gap)
    return [max(0.0, p) for p in placed]


def _moment(timestamp: float) -> datetime | None:
    try:
        return datetime.fromtimestamp(timestamp)
    except (OverflowError, OSError, ValueError):
        return None


def _day_label(moment: datetime) -> str:
    return f"{moment:%a}, {moment.day} {moment:%b} {moment.year}"


def _week_label(moment: datetime) -> str:
    monday = moment - timedelta(days=moment.weekday())
    return f"Week of {monday.day} {monday:%b} {monday.year}"


def _month_label(moment: datetime) -> str:
    return f"{moment:%B} {moment.year}"


def _quarter_label(moment: datetime) -> str:
    return f"Q{(moment.month - 1) // 3 + 1} {moment.year}"


def _year_label(moment: datetime) -> str:
    return str(moment.year)


# unit -> (bucket key, hover label). Keys include the year so a bucket never straddles New Year,
# which guarantees the first photo of every year starts a bucket (and so can carry the year label).
_UNITS: tuple[tuple[Unit, Callable[[datetime], tuple], Callable[[datetime], str]], ...] = (
    (Unit.DAY, lambda m: (m.year, m.month, m.day), _day_label),
    (Unit.WEEK, lambda m: (m.year, m.isocalendar()[1], m.isocalendar()[0]), _week_label),
    (Unit.MONTH, lambda m: (m.year, m.month), _month_label),
    (Unit.QUARTER, lambda m: (m.year, (m.month - 1) // 3), _quarter_label),
    (Unit.YEAR, lambda m: (m.year,), _year_label),
)


def build_rail(rows: Sequence[tuple[float, float]], max_scroll: float, span: float) -> Rail:
    """Build the rail for a laid-out gallery.

    ``rows`` holds ``(y, timestamp)`` for every layout row, in layout order; ``max_scroll`` is the
    scroll range in pixels and ``span`` the usable rail height in pixels.
    """
    moments = [(index, y, _moment(timestamp)) for index, (y, timestamp) in enumerate(rows)]
    moments = [(index, y, moment) for index, y, moment in moments if moment is not None]
    if not moments:
        return Rail(Unit.YEAR)
    max_scroll = max(1.0, max_scroll)

    def buckets(key_of: Callable[[datetime], tuple]) -> list[tuple[int, float, datetime]]:
        starts: list[tuple[int, float, datetime]] = []
        previous = None
        for index, y, moment in moments:
            key = key_of(moment)
            if key != previous:
                starts.append((index, y, moment))
                previous = key
        return starts

    def fits(starts: list[tuple[int, float, datetime]]) -> bool:
        # Only the count decides. Real libraries are lumpy (a pile of recent photos, a trickle of old
        # ones), so demanding even spacing would push almost every library down to years-only.
        # Dots that end up touching are merged when drawn, not by choosing a coarser unit.
        return len(starts) * MIN_STOP_GAP <= span

    chosen = _UNITS[-1]
    chosen_starts = buckets(chosen[1])
    for unit in _UNITS:
        starts = buckets(unit[1])
        if fits(starts):
            chosen, chosen_starts = unit, starts
            break

    unit, _key, make_label = chosen
    entries = []  # (index, frac, moment, new_year)
    last_year = None
    for index, y, moment in chosen_starts:
        entries.append((index, max(0.0, min(1.0, y / max_scroll)), moment, moment.year != last_year))
        last_year = moment.year

    gap = LABEL_GAP / max(1.0, span)
    major_fracs = [frac for _i, frac, _m, new_year in entries if new_year]
    major_poss = iter(_spread_years(major_fracs, gap))

    anchors: list[tuple[float, float]] = [(0.0, 0.0)]
    stop_poss: list[float] = []
    for _index, frac, _moment_, new_year in entries:
        if new_year:
            pos = next(major_poss)
            if frac > anchors[-1][0] and pos >= anchors[-1][1]:
                anchors.append((frac, pos))
            stop_poss.append(pos)
        else:
            stop_poss.append(-1.0)  # filled in below, once the anchors are known
    if anchors[-1][0] < 1.0:
        anchors.append((1.0, 1.0))
    anchors_t = tuple(anchors)

    stops: list[RailStop] = []
    running = 0.0
    for (index, frac, moment, new_year), pos in zip(entries, stop_poss):
        if pos < 0.0:
            pos = _interpolate(anchors_t, frac, forward=True)
        running = max(running, min(1.0, pos))  # keep positions in the same order as the stops
        stops.append(
            RailStop(
                frac=frac,
                kind="major" if new_year else "minor",
                label=make_label(moment),
                row=index,
                caption=str(moment.year) if new_year else "",
                pos=running,
            )
        )
    return Rail(
        unit,
        tuple(stops),
        tuple(stop.frac for stop in stops),
        tuple(stop.pos for stop in stops),
        anchors_t,
    )
