import tempfile

try:
    from PySide6.QtCore import QSettings
except ImportError:
    QSettings = None

if QSettings is not None:
    _SANDBOX = tempfile.mkdtemp(prefix="npigallery-test-settings-")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _SANDBOX)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.SystemScope, _SANDBOX)