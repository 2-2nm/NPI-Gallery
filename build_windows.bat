@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ============================================================================
rem  NPI Gallery - Windows build (PyInstaller)
rem
rem  Result: dist\NpiGallery\NpiGallery.exe   (or dist\NpiGallery.exe with ONEFILE=1)
rem
rem  Keeps the size down by
rem    1. building in a fresh virtual environment that holds ONLY requirements.txt
rem       (so nothing else installed on this PC, like numpy or matplotlib, can get in),
rem    2. leaving out every Qt module and Python module the app never imports,
rem    3. leaving out PyAV (a second FFmpeg, ~60 MB) unless WITH_AV=1,
rem    4. deleting Qt files that are never needed (software OpenGL, translations).
rem
rem  Options (set them before running, e.g.  set ONEFILE=1 ^& build_windows.bat):
rem    ONEFILE=1   one .exe instead of a folder (smaller download, slower start)
rem    WITH_AV=1   keep PyAV: video thumbnails in the gallery (video playback in
rem                the viewer uses Qt Multimedia and does not need it)
rem ================================================================================

cd /d "%~dp0"

set "APP_NAME=NPI Gallery"
set "VENV=.buildvenv"
if not defined ONEFILE set "ONEFILE=0"
if not defined WITH_AV set "WITH_AV=1"

rem // Python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [!] Python 3.10+ was not found. Install it from python.org and run this again.
    goto :fail
)

rem // clean build environment
if not exist "%VENV%\Scripts\python.exe" (
    echo [1/4] Creating build environment...
    %PY% -m venv "%VENV%" || goto :fail
)
set "VPY=%VENV%\Scripts\python.exe"

echo [2/4] Installing requirements...
"%VPY%" -m pip install --disable-pip-version-check -q --upgrade pip || goto :fail
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller || goto :fail
if "%WITH_AV%"=="1" (
    "%VPY%" -m pip install --disable-pip-version-check -q av || goto :fail
)

rem // what is left out
rem Qt modules the app does not use. Used and therefore kept: QtCore, QtGui, QtWidgets,
rem QtSvg, QtPrintSupport, QtMultimedia, QtMultimediaWidgets (and QtNetwork, which
rem Qt Multimedia needs).
set "EXCL="
for %%m in (
    PySide6.QtQml PySide6.QtQuick PySide6.QtQuickWidgets PySide6.QtQuickControls2 PySide6.QtQuick3D
    PySide6.QtWebEngineCore PySide6.QtWebEngineWidgets PySide6.QtWebEngineQuick PySide6.QtWebChannel
    PySide6.QtWebSockets PySide6.QtWebView PySide6.QtPdf PySide6.QtPdfWidgets
    PySide6.Qt3DCore PySide6.Qt3DRender PySide6.Qt3DInput PySide6.Qt3DLogic PySide6.Qt3DAnimation PySide6.Qt3DExtras
    PySide6.QtCharts PySide6.QtDataVisualization PySide6.QtGraphs PySide6.QtGraphsWidgets
    PySide6.QtSql PySide6.QtTest PySide6.QtDesigner PySide6.QtHelp PySide6.QtUiTools PySide6.QtXml
    PySide6.QtBluetooth PySide6.QtNfc PySide6.QtPositioning PySide6.QtLocation PySide6.QtSensors
    PySide6.QtSerialPort PySide6.QtSerialBus PySide6.QtRemoteObjects PySide6.QtScxml PySide6.QtStateMachine
    PySide6.QtTextToSpeech PySide6.QtSpatialAudio PySide6.QtHttpServer PySide6.QtNetworkAuth
    PySide6.QtDBus PySide6.QtConcurrent PySide6.QtAxContainer PySide6.QtSvgWidgets
    PySide6.QtOpenGLWidgets PySide6.QtVirtualKeyboard PySide6.QtShaderTools
    PyQt5 PyQt6 PySide2 tkinter unittest pytest numpy matplotlib scipy pandas setuptools pkg_resources
    PIL.ImageTk PIL.ImageQt
) do set "EXCL=!EXCL! --exclude-module %%m"
if not "%WITH_AV%"=="1" set "EXCL=!EXCL! --exclude-module av"

rem // build
set "MODE=--onedir"
if "%ONEFILE%"=="1" set "MODE=--onefile"

echo [3/4] Building %APP_NAME% (this takes a few minutes)...
"%VPY%" -m PyInstaller --noconfirm --clean --noupx --windowed %MODE% ^
    --name "%APP_NAME%" ^
    --icon "npigallery\assets\npi-gallery.ico" ^
    --add-data "npigallery\assets\fonts;npigallery\assets\fonts" ^
    --add-data "npigallery\assets\icon.png;npigallery\assets" ^
    --add-data "npigallery\assets\npi-gallery.ico;npigallery\assets" ^
    --copy-metadata PySide6 --copy-metadata Pillow --copy-metadata pillow-heif --copy-metadata Send2Trash ^
    !EXCL! ^
    main.py || goto :fail

rem // trim Qt files that are never loaded-
rem (only possible for the folder build; with ONEFILE=1 they stay inside the .exe)
if not "%ONEFILE%"=="1" (
    echo [4/4] Removing unused Qt files...
    for /r "dist\%APP_NAME%" %%f in (opengl32sw.dll) do if exist "%%f" del /q "%%f"
    for /d /r "dist\%APP_NAME%" %%d in (translations) do if exist "%%d" rd /s /q "%%d"
) else (
    echo [4/4] ONEFILE build: nothing to trim afterwards.
)

rem // report
if "%ONEFILE%"=="1" (set "OUT=dist\%APP_NAME%.exe") else (set "OUT=dist\%APP_NAME%")
echo.
echo Done: %OUT%
if "%ONEFILE%"=="1" (
    powershell -NoProfile -Command "'{0:N1} MB' -f ((Get-Item '%OUT%').Length/1MB)"
) else (
    powershell -NoProfile -Command "'{0:N1} MB' -f ((Get-ChildItem '%OUT%' -Recurse | Measure-Object Length -Sum).Sum/1MB)"
)
endlocal
exit /b 0

:fail
echo.
echo Build failed.
endlocal
exit /b 1
