# NPI Gallery

![VERSION](https://img.shields.io/badge/VERSION-0.1.271-ff6b6b?style=for-the-badge&labelColor=555555)
![PYTHON](https://img.shields.io/badge/PYTHON-3.10%2B-3776ab?style=for-the-badge&logo=python&logoColor=white&labelColor=555555)
![UI](https://img.shields.io/badge/UI-PYSIDE6-41cd52?style=for-the-badge&labelColor=555555)
![PLATFORM](https://img.shields.io/badge/PLATFORM-WINDOWS%2010%20%2F%2011-0078d4?style=for-the-badge&labelColor=555555)

A fast, modern photo and video gallery for the folders you already have.

<p align="center">
  <img width="1920" height="1200" alt="image" src="https://github.com/user-attachments/assets/c30d0587-b89f-4dd8-9be6-72de52384d5a" />
<img width="1920" height="1200" alt="Screenshot (1895)" src="https://github.com/user-attachments/assets/cce6a41a-8da5-4684-afc2-0d9d1cbb48b0" />

</p>

</div>

---

## What is it?

NPI Gallery shows the photos and videos in the folders you pick, grouped by day in one smooth grid. Nothing is imported or copied: it reads your folders as they are, and picks up changes when you refresh. Open anything in an immersive viewer, sort things out with favourites and albums, and pack images into the compact `.npi` format when you want to save space.

## Features

- Justified grid grouped by day, with a year timeline, search, sort, filter and thumbnail size
- Browse by Photos, Videos, Favourites, Albums, Folders, Dates and Locations (from GPS info)
- **Albums**: your own groups of photos and videos, from any folder. They only exist inside the app, so adding to or removing from an album never moves or deletes a file
- Select mode for bulk delete, copy, move and add to album
- Viewer for photos and videos with zoom, slideshow, filmstrip, info / EXIF, rotate, print and a built-in editor
- Duplicate finder
- Dump a folder to `.npi` and restore it back to the original files
- JPEG, PNG, WebP, GIF, BMP, TIFF, HEIC and AVIF; MP4, MOV, MKV, WebM and more for video
- Dark and light themes with accent colours, and a window background of your choice: Mica, Acrylic or a picture (see below)

## Mica and Acrylic

The whole window (around the sidebar, the top bar and behind the photos) can show a see-through background instead of a flat colour. Pick one in **Settings > Personalization > Window background**:

- **Mica (wallpaper)**: your desktop wallpaper, blurred, like Windows 11 Mica. Drawn by the app, so it works on Windows 10 too.
- **Acrylic (manual)**: the frosted-glass look of Windows Acrylic, also drawn by the app.
- **Acrylic (system)**: the real Windows Acrylic blur. It needs Windows 11 with *Transparency effects* turned on; when Windows cannot provide it, the app switches to the manual Acrylic.
- **Picture**: any image of your own.

The blur of Mica and manual Acrylic and how much of the window colour is laid over them can be adjusted with sliders. The sidebar has its own background setting as well.

## Install

Run from source (Python 3.10+):

1. `pip install -r requirements.txt`
2. `python main.py [folder]`
3. Click **Add a Folder** in the sidebar to add your photo folders

Build a Windows app:

1. Run `build_windows.bat`
2. Open `dist\NpiGallery\NpiGallery.exe`

## Command line

```
python -m npigallery dump <folder> [-o OUT] [--no-recursive] [--delete-originals]
python -m npigallery restore <folder> [-o OUT] [--no-recursive] [--delete-npi]
python -m npigallery verify <file-or-folder>...
python -m npigallery info <file>
```

## Keys

| Gallery | |
| --- | --- |
| Arrows / Enter | Move / open |
| Space or the circle | Select |
| Ctrl+A / Esc | Select all / clear selection |
| Ctrl+wheel | Thumbnail size |

| Viewer | |
| --- | --- |
| Arrows / Space / Backspace | Previous / next |
| + / - / F / 1 | Zoom in / out / fit / 100% |
| S / I | Slideshow / info |
| Ctrl+C / Ctrl+P / Delete | Copy / print / delete |
| F11 / Esc | Full screen / close |

## Tests

```
python -m unittest discover -s tests -t .
```
