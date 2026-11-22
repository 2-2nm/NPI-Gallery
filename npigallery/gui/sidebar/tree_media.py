import calendar

from ...services.scanner import MediaItem
from ..common.scope import Scope, date_key, place_cell, place_label
from .metrics import COUNT_ROLE, SCOPE_ROLE


class NavMediaMixin:
    def set_media(self, items: list[MediaItem]) -> None:
        dates: dict[tuple[int, int], int] = {}
        places: dict[tuple[float, float], int] = {}
        for item in items:
            stamp = date_key(item.sort_time)
            if stamp is not None:
                dates[stamp] = dates.get(stamp, 0) + 1
            cell = place_cell(item.latitude, item.longitude)
            if cell is not None:
                places[cell] = places.get(cell, 0) + 1
        if dates == self._date_counts and places == self._place_counts:
            return
        same_rows = dates.keys() == self._date_counts.keys() and places.keys() == self._place_counts.keys()
        self._date_counts, self._place_counts = dates, places
        if same_rows:
            self._refresh_counts()
        else:
            self._rebuild_media()

    def set_albums(self, counts: list[tuple[str, int]], renamed: tuple[str, str] | None = None) -> None:
        """Show the albums as (name, item count) rows. `renamed` keeps the shown album selected after a rename."""
        if counts == self._album_counts:
            return
        same_rows = [name for name, _ in counts] == [name for name, _ in self._album_counts]
        self._album_counts = list(counts)
        if same_rows and renamed is None:
            for name, count in counts:
                self._album_items[name].setData(0, COUNT_ROLE, count)
            self.viewport().update()
            return
        self._finish_snap()
        shown = self.current_scope()
        target = shown
        if renamed is not None and shown.kind == "album" and shown.name == renamed[0]:
            target = Scope.album(renamed[1])
        self.blockSignals(True)
        try:
            self._albums.takeChildren()
            self._album_items = {}
            for name, count in counts:
                album_item = self._add(self._albums, name, "album", f"album:{name}")
                album_item.setData(0, SCOPE_ROLE, Scope.album(name))
                album_item.setData(0, COUNT_ROLE, count)
                album_item.setToolTip(0, name)
                self._album_items[name] = album_item
            self.sync_section_visibility()
            self.select_scope(target)
        finally:
            self.blockSignals(False)
        after = self.current_scope()
        if after != shown:
            self.scope_selected.emit(after)
        self.viewport().update()

    def sync_section_visibility(self) -> None:
        self._albums.setHidden(self.compact or not self._album_counts)
        self._date.setHidden(self.compact or not self._date_counts)
        self._places.setHidden(self.compact or not self._place_counts)

    def _refresh_counts(self) -> None:
        years: dict[int, int] = {}
        for (year, month), count in self._date_counts.items():
            years[year] = years.get(year, 0) + count
            self._month_items[(year, month)].setData(0, COUNT_ROLE, count)
        for year, count in years.items():
            self._year_items[year].setData(0, COUNT_ROLE, count)
        for cell, count in self._place_counts.items():
            self._place_items[cell].setData(0, COUNT_ROLE, count)
        self.viewport().update()

    def _rebuild_media(self) -> None:
        self._finish_snap()
        before = self.current_scope()
        opened = {year for year, item in self._year_items.items() if item.isExpanded()}
        years = sorted({year for year, _ in self._date_counts}, reverse=True)
        ordered = sorted(self._place_counts.items(), key=lambda entry: (-entry[1], entry[0]))
        self.blockSignals(True)
        try:
            self._date.takeChildren()
            self._places.takeChildren()
            self._year_items = {}
            self._month_items = {}
            self._place_items = {}
            for year in years:
                year_item = self._add(self._date, str(year), "year", f"date:{year}")
                year_item.setData(0, SCOPE_ROLE, Scope.date(year))
                self._year_items[year] = year_item
                months = sorted((month for other, month in self._date_counts if other == year), reverse=True)
                for month in months:
                    month_item = self._add(year_item, calendar.month_name[month], "month", f"date:{year}-{month:02d}")
                    month_item.setData(0, SCOPE_ROLE, Scope.date(year, month))
                    self._month_items[(year, month)] = month_item
                year_item.setExpanded(year in opened)
            for cell, _ in ordered:
                label = place_label(cell)
                place_item = self._add(self._places, label, "place", f"place:{label}")
                place_item.setData(0, SCOPE_ROLE, Scope.place(cell))
                self._place_items[cell] = place_item
            self.sync_section_visibility()
            self._refresh_counts()
            self.select_scope(before)
        finally:
            self.blockSignals(False)
        after = self.current_scope()
        if after != before:
            self.scope_selected.emit(after)
        self.viewport().update()