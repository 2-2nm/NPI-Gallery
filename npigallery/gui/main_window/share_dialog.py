import shutil
from pathlib import Path

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout

from ...filesystem import unique_path, write_atomic
from ...formatting import human_size
from ...npi import read_image_bytes, read_meta
from ..common.dialogs import ElidedLabel, FloatDialog, RoundedThumb, button_row, dialog_label, make_button, warn
from ..common.imageconv import qimage_from_bytes


class ShareDialog(FloatDialog):
    """Share sheet for copying, revealing, or exporting the chosen photos."""

    def __init__(self, items: list, copy_files_fn, copy_paths_fn, reveal_fn, parent=None):
        super().__init__("Share", parent)
        self._items = items
        self._copy_files_fn = copy_files_fn
        self._copy_paths_fn = copy_paths_fn
        self._reveal_fn = reveal_fn

        header = QHBoxLayout()
        header.setSpacing(14)
        if len(items) == 1:
            item = items[0]
            thumb = RoundedThumb(58, 12)
            try:
                image = qimage_from_bytes(read_image_bytes(item.path))
                if not image.isNull():
                    thumb.set_pixmap(QPixmap.fromImage(image))
            except Exception:
                pass
            header.addWidget(thumb)

            meta_col = QVBoxLayout()
            meta_col.setSpacing(3)
            meta_col.addStretch(1)
            meta_col.addWidget(ElidedLabel(item.name, "DialogName"))
            meta_col.addWidget(ElidedLabel(f"{human_size(item.file_size)} \u2022 {item.path.parent}", "DialogDetail"))
            meta_col.addStretch(1)
            header.addLayout(meta_col, 1)
        else:
            total_bytes = sum(item.file_size for item in items)
            header.addWidget(
                dialog_label(f"Share {len(items)} selected items ({human_size(total_bytes)})", "name", wrap=True), 1
            )
        self.body.addLayout(header)

        self._status = dialog_label("Choose how you want to share:", "label")
        self.body.addWidget(self._status)

        actions = QVBoxLayout()
        actions.setSpacing(6)

        def action_btn(icon_name: str, label: str, slot) -> None:
            button = make_button(f"  {label}", "action", icon_name)
            button.clicked.connect(slot)
            actions.addWidget(button)

        action_btn("copy", "Copy photo to clipboard", self._do_copy_files)
        action_btn("copy-path", "Copy file path", self._do_copy_paths)
        action_btn("folder", "Reveal in File Explorer", self._do_reveal)
        action_btn("export", "Export shareable copy to folder...", self._do_export)
        self.body.addLayout(actions)

        close_btn = make_button("Close", "primary")
        close_btn.clicked.connect(self.accept)
        self.body.addLayout(button_row(close_btn))
        self.finish()

    def _do_copy_files(self) -> None:
        self._copy_files_fn(self._items)
        self._status.setText("Copied to clipboard.")

    def _do_copy_paths(self) -> None:
        self._copy_paths_fn(self._items)
        self._status.setText("Copied file path to clipboard.")

    def _do_reveal(self) -> None:
        self._reveal_fn(self._items[0])
        self.accept()

    def _do_export(self) -> None:
        start = str(self._items[0].path.parent)
        chosen = QFileDialog.getExistingDirectory(self, "Export shareable copy", start)
        if not chosen:
            return
        target_dir = Path(chosen)
        exported = 0
        failures = []
        for item in self._items:
            try:
                if item.is_npi:
                    meta = read_meta(item.path)
                    data = read_image_bytes(item.path)
                    dest = unique_path(target_dir / meta.original_name)
                    write_atomic(dest, data)
                else:
                    dest = unique_path(target_dir / item.path.name)
                    shutil.copy2(str(item.path), str(dest))
                exported += 1
            except OSError as error:
                failures.append(f"{item.name}: {error}")
        if failures:
            warn(self, "Share", "\n".join(failures))
        if exported:
            self._status.setText(f"Exported {exported} item{'s' if exported != 1 else ''} to {target_dir.name}.")
