"""The picture behind everything in the main window (sidebar margins, top bar, photo grid)."""

from .layer import Backdrop, backdrop, compose
from .root import BackdropRoot

__all__ = ["Backdrop", "BackdropRoot", "backdrop", "compose"]
