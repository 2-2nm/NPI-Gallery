"""Immersive photo / video viewer (standalone window with floating, auto-hiding controls)."""

from .video import video_playback_available
from .window import ViewerHooks, ViewerWindow

__all__ = ["ViewerHooks", "ViewerWindow", "video_playback_available"]
