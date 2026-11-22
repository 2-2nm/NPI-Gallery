import unittest

from npigallery.services.albums import AlbumStore


class AlbumStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = AlbumStore()
        self.store.create("Trip", ["/a/1.jpg", "/b/2.jpg"])

    def test_names_are_unique_ignoring_case(self):
        self.assertFalse(self.store.create("trip"))
        self.assertFalse(self.store.create("   "))
        self.assertTrue(self.store.create("Family"))
        self.assertEqual(self.store.names(), ["Family", "Trip"])
        self.assertEqual(self.store.unique_name("Trip"), "Trip 2")
        self.assertEqual(self.store.unique_name("Pets"), "Pets")

    def test_add_counts_only_new_files(self):
        self.assertEqual(self.store.add("Trip", ["/a/1.jpg", "/c/3.jpg"]), 1)
        self.assertEqual(self.store.paths("Trip"), {"/a/1.jpg", "/b/2.jpg", "/c/3.jpg"})
        self.assertEqual(self.store.add("Missing", ["/x.jpg"]), 0)

    def test_remove_only_touches_the_album(self):
        self.store.create("Other", ["/a/1.jpg"])
        self.assertEqual(self.store.remove("Trip", ["/a/1.jpg", "/nope.jpg"]), 1)
        self.assertEqual(self.store.paths("Trip"), {"/b/2.jpg"})
        self.assertEqual(self.store.paths("Other"), {"/a/1.jpg"})

    def test_rename_and_delete(self):
        self.store.create("Family")
        self.assertFalse(self.store.rename("Trip", "family"))  # taken
        self.assertFalse(self.store.rename("Trip", "Trip"))
        self.assertTrue(self.store.rename("Trip", "Holiday"))
        self.assertEqual(self.store.paths("Holiday"), {"/a/1.jpg", "/b/2.jpg"})
        self.assertTrue(self.store.rename("Holiday", "holiday"))  # only the case changes
        self.assertTrue(self.store.delete("holiday"))
        self.assertFalse(self.store.delete("holiday"))

    def test_remap_and_discard_follow_file_operations(self):
        self.assertTrue(self.store.remap(lambda p: p.replace("/a/", "/z/")))
        self.assertEqual(self.store.paths("Trip"), {"/z/1.jpg", "/b/2.jpg"})
        self.assertFalse(self.store.remap(lambda p: p))
        self.assertTrue(self.store.discard(lambda p: p.startswith("/b/")))
        self.assertEqual(self.store.paths("Trip"), {"/z/1.jpg"})
        self.assertFalse(self.store.discard(lambda p: False))

    def test_counts_use_files_in_the_library(self):
        self.assertEqual(self.store.counts({"/a/1.jpg"}), [("Trip", 1)])
        self.assertEqual(self.store.all_paths(), {"/a/1.jpg", "/b/2.jpg"})

    def test_json_round_trip_and_bad_input(self):
        loaded = AlbumStore.from_json(self.store.to_json())
        self.assertEqual(loaded.paths("Trip"), self.store.paths("Trip"))
        for bad in ("", "not json", "[]", '{"A": "x"}', '{"A": [1, "/p.jpg"]}'):
            store = AlbumStore.from_json(bad)
            self.assertLessEqual(len(store.names()), 1)
        self.assertEqual(AlbumStore.from_json('{"A": [1, "/p.jpg"]}').paths("A"), {"/p.jpg"})


if __name__ == "__main__":
    unittest.main()
