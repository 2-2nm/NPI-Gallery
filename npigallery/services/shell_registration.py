import ctypes
import sys
from pathlib import Path

from ..config import APP_TITLE
from ..npi.formats import IMAGE_EXTENSIONS, NPI_EXTENSION, VIDEO_EXTENSIONS

PROG_ID = "NpiGallery.Media"
CLASSES_KEY = r"Software\Classes"
EXPLORER_EXTENSIONS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"
SHCNE_ASSOCCHANGED = 0x08000000


class RegistrationError(Exception):
    pass


def is_supported() -> bool:
    return sys.platform == "win32"


def plain_extensions() -> list[str]:
    """Photos and videos: always listed in Open with once NPI Gallery is registered."""
    return sorted(IMAGE_EXTENSIONS | VIDEO_EXTENSIONS)


def npi_extensions() -> list[str]:
    """NPI files are camouflage, so they are linked to the app only while the user asks for it."""
    return [NPI_EXTENSION]


def media_extensions() -> list[str]:
    return sorted(set(plain_extensions()) | set(npi_extensions()))


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def launch_arguments() -> list[str]:
    if _frozen():
        return [sys.executable]
    interpreter = Path(sys.executable)
    windowless = interpreter.with_name("pythonw.exe")
    if windowless.exists():
        interpreter = windowless
    entry = Path(__file__).resolve().parents[2] / "main.py"
    return [str(interpreter), str(entry)]


def command_line() -> str:
    return " ".join([f'"{part}"' for part in launch_arguments()] + ['"%1"'])


def icon_location() -> str | None:
    if _frozen():
        return f'"{sys.executable}",0'
    return None


def _application_name() -> str:
    return Path(launch_arguments()[0]).name


def _create(path: str):
    import winreg

    return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE)


def _set_text(path: str, name: str, value: str) -> None:
    import winreg

    with _create(path) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _delete_tree(path: str) -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(f"{path}\\{child}")
    except FileNotFoundError:
        return
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        pass


def _notify() -> None:
    try:
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, 0, None, None)
    except (OSError, AttributeError):
        pass


def _remove_value(path: str, name: str) -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


def _forget_history(extension: str) -> None:
    """Explorer remembers what opened a file type; drop our entries so nothing is left behind."""
    import winreg

    _remove_value(f"{EXPLORER_EXTENSIONS_KEY}\\{extension}\\OpenWithProgids", PROG_ID)
    if not _frozen():
        return
    name = _application_name().casefold()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            f"{EXPLORER_EXTENSIONS_KEY}\\{extension}\\OpenWithList",
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        ) as key:
            doomed = []
            index = 0
            while True:
                try:
                    value_name, data, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                if isinstance(data, str) and data.casefold() == name:
                    doomed.append(value_name)
                index += 1
            for value_name in doomed:
                winreg.DeleteValue(key, value_name)
    except FileNotFoundError:
        pass


def _write_program() -> None:
    command = command_line()
    program = f"{CLASSES_KEY}\\{PROG_ID}"
    _set_text(program, "", APP_TITLE)
    _set_text(program, "FriendlyTypeName", APP_TITLE)
    _set_text(program, "FriendlyAppName", APP_TITLE)
    _set_text(f"{program}\\shell\\open", "FriendlyAppName", APP_TITLE)
    _set_text(f"{program}\\shell\\open", "MUIVerb", APP_TITLE)
    _set_text(f"{program}\\shell\\open\\command", "", command)
    icon = icon_location()
    if icon:
        _set_text(f"{program}\\DefaultIcon", "", icon)


def _add_extensions(extensions: list[str]) -> None:
    import winreg

    for extension in extensions:
        with _create(f"{CLASSES_KEY}\\{extension}\\OpenWithProgids") as key:
            winreg.SetValueEx(key, PROG_ID, 0, winreg.REG_NONE, b"")


def _remove_legacy_application() -> None:
    """Older versions also wrote Applications\\<exe>, which made Open with list the app twice."""
    if _frozen():
        _delete_tree(f"{CLASSES_KEY}\\Applications\\{_application_name()}")


def _remove_extensions(extensions: list[str]) -> None:
    for extension in extensions:
        _remove_value(f"{CLASSES_KEY}\\{extension}\\OpenWithProgids", PROG_ID)
        if _frozen():
            _remove_value(f"{CLASSES_KEY}\\Applications\\{_application_name()}\\SupportedTypes", extension)
        _forget_history(extension)


def register(include_npi: bool = False) -> None:
    """List NPI Gallery in Open with for photos and videos. `.npi` is only touched when asked for."""
    if not is_supported():
        raise RegistrationError("Open with is only available on Windows.")
    try:
        _write_program()
        _remove_legacy_application()
        _add_extensions(plain_extensions() + (npi_extensions() if include_npi else []))
    except OSError as error:
        raise RegistrationError(str(error)) from error
    _notify()


def set_npi_registered(enabled: bool) -> None:
    """The NPI switch: on lists the app in Open with for `.npi`, off leaves `.npi` unlinked."""
    if not is_supported():
        raise RegistrationError("Open with is only available on Windows.")
    try:
        if enabled:
            _write_program()
            _remove_legacy_application()
            _add_extensions(npi_extensions())
        else:
            _remove_extensions(npi_extensions())
    except OSError as error:
        raise RegistrationError(str(error)) from error
    _notify()


def unregister() -> None:
    if not is_supported():
        raise RegistrationError("Open with is only available on Windows.")
    try:
        _remove_extensions(media_extensions())
        _delete_tree(f"{CLASSES_KEY}\\{PROG_ID}")
        _remove_legacy_application()
    except OSError as error:
        raise RegistrationError(str(error)) from error
    _notify()


def is_npi_registered() -> bool:
    if not is_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, f"{CLASSES_KEY}\\{NPI_EXTENSION}\\OpenWithProgids"
        ) as key:
            winreg.QueryValueEx(key, PROG_ID)
            return True
    except OSError:
        return False


def is_registered() -> bool:
    if not is_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, f"{CLASSES_KEY}\\{PROG_ID}\\shell\\open\\command"
        ) as key:
            return str(winreg.QueryValueEx(key, "")[0]) == command_line()
    except OSError:
        return False