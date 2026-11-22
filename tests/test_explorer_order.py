import unittest
from pathlib import Path
from unittest import mock

from npigallery.services import explorer_order
from npigallery.services.scanner import MediaItem


def item(name: str, mtime: int = 0, size: int = 0) -> MediaItem:
    return MediaItem(
        path=Path("C:/photos") / name, name=name, is_npi=False, size=size, sort_time=0.0,
        format_label="", mtime_ns=mtime, file_size=size,
    )


def names(items):
    return [entry.path.name for entry in items]


class ExplorerOrderTests(unittest.TestCase):
    def test_sort_columns_are_parsed_with_their_direction(self):
        self.assertEqual(
            explorer_order.parse_sort_columns("prop:-System.DateModified;+System.ItemNameDisplay;"),
            [("System.DateModified", True), ("System.ItemNameDisplay", False)],
        )
        self.assertEqual(explorer_order.parse_sort_columns(""), [])

    def test_name_order_is_natural(self):
        items = [item("img10.jpg"), item("img2.jpg"), item("img1.jpg")]
        ordered = explorer_order.sort_like_explorer(items, [("System.ItemNameDisplay", False)])
        self.assertEqual(names(ordered), ["img1.jpg", "img2.jpg", "img10.jpg"])

    def test_date_modified_descending_breaks_ties_by_name(self):
        items = [item("b.jpg", 5), item("a.jpg", 5), item("c.jpg", 9)]
        ordered = explorer_order.sort_like_explorer(items, [("System.DateModified", True)])
        self.assertEqual(names(ordered), ["c.jpg", "a.jpg", "b.jpg"])

    def test_a_column_that_cannot_be_reproduced_gives_nothing(self):
        self.assertIsNone(explorer_order.sort_like_explorer([item("a.jpg")], [("System.Rating", False)]))

    def test_without_an_explorer_window_nothing_is_reordered(self):
        with mock.patch.object(explorer_order, "is_supported", return_value=False):
            self.assertIsNone(explorer_order.view_sort(Path("C:/photos"), Path("C:/photos/a.jpg")))


if __name__ == "__main__":
    unittest.main()