import sys
import unittest
from unittest import mock

from npigallery.services import shell_registration


class ShellRegistrationTests(unittest.TestCase):
    def test_every_media_extension_is_listed_once(self):
        extensions = shell_registration.media_extensions()
        self.assertEqual(len(extensions), len(set(extensions)))
        for expected in (".jpg", ".png", ".heic", ".mp4", ".mkv", ".npi"):
            self.assertIn(expected, extensions)

    def test_npi_is_kept_apart_from_photos_and_videos(self):
        self.assertNotIn(".npi", shell_registration.plain_extensions())
        self.assertEqual(shell_registration.npi_extensions(), [".npi"])
        self.assertEqual(
            sorted(shell_registration.plain_extensions() + shell_registration.npi_extensions()),
            shell_registration.media_extensions(),
        )

    def test_command_line_quotes_every_part_and_ends_with_the_file(self):
        with mock.patch.object(shell_registration, "launch_arguments", return_value=["C:\\Program Files\\Npi\\npi.exe"]):
            self.assertEqual(shell_registration.command_line(), '"C:\\Program Files\\Npi\\npi.exe" "%1"')

    def test_a_packed_app_launches_its_own_executable(self):
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", "C:\\npi.exe"):
            self.assertEqual(shell_registration.launch_arguments(), ["C:\\npi.exe"])
            self.assertEqual(shell_registration.icon_location(), '"C:\\npi.exe",0')

    @unittest.skipIf(sys.platform == "win32", "these are the answers off Windows")
    def test_off_windows_nothing_is_registered(self):
        self.assertFalse(shell_registration.is_supported())
        self.assertFalse(shell_registration.is_registered())
        with self.assertRaises(shell_registration.RegistrationError):
            shell_registration.register()
        with self.assertRaises(shell_registration.RegistrationError):
            shell_registration.unregister()
        self.assertFalse(shell_registration.is_npi_registered())
        with self.assertRaises(shell_registration.RegistrationError):
            shell_registration.set_npi_registered(True)


if __name__ == "__main__":
    unittest.main()