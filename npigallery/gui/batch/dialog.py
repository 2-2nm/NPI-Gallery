from enum import Enum
from functools import partial
from pathlib import Path
from textwrap import shorten

from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...services.batch import BatchResult, dump_folder, restore_folder
from ..common.dialogs import (
    DialogCheck,
    FloatDialog,
    button_row,
    confirm,
    dialog_label,
    make_button,
    make_input,
    warn,
)
from .worker import BatchWorker

_IDLE = "Waiting to start"
_BLANK = "\u00a0"  # keeps the stats panel the same height before and after a run
_MAX_LISTED_FAILURES = 4


class BatchMode(Enum):
    DUMP = "dump"
    RESTORE = "restore"


class BatchDialog(FloatDialog):
    def __init__(self, mode: BatchMode, initial_folder: Path | None, parent=None):
        dumping = mode is BatchMode.DUMP
        super().__init__("Dump folder to .npi" if dumping else "Restore folder from .npi", parent)
        self._mode = mode
        self._dumping = dumping
        self._worker: BatchWorker | None = None
        self._finished = False
        self._done = self._total = self._processed = self._skipped = self._failed = 0
        self.changed = False

        self._source = make_input(str(initial_folder) if initial_folder else "")
        self._source_browse = make_button("Browse...")
        self._source_browse.clicked.connect(lambda: self._browse(self._source))

        self._recursive = DialogCheck("Include subfolders")
        self._recursive.setChecked(True)
        self._custom_output = DialogCheck("Write to a different folder")
        self._custom_output.toggled.connect(self._apply_output_state)
        self._delete = DialogCheck(
            "Delete original images after they are verified" if dumping else "Delete .npi files after restoring"
        )

        self._output = make_input()
        self._output.setPlaceholderText("Output folder")
        self._output_browse = make_button("Browse...")
        self._output_browse.clicked.connect(lambda: self._browse(self._output))
        self._output_row = QWidget()
        output_layout = self._row(self._output, self._output_browse)
        output_layout.setContentsMargins(0, 4, 0, 4)
        self._output_row.setLayout(output_layout)

        self._progress = QProgressBar()
        self._progress.setObjectName("DialogProgress")
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._line_done = dialog_label(_IDLE, "text")
        self._line_ok = dialog_label(_BLANK, "text")
        self._line_skipped = dialog_label(_BLANK, "text")
        self._line_failed = dialog_label("", "danger")
        self._line_failed.hide()
        self._failure_details = dialog_label("", "detail", wrap=True)
        self._failure_details.hide()

        self._cancel = make_button("Cancel")
        self._cancel.clicked.connect(self._close_or_cancel)
        self._start = make_button("Start", "primary")
        self._start.clicked.connect(self._start_batch)

        source_group = QVBoxLayout()
        source_group.setSpacing(6)
        source_group.addWidget(dialog_label("Source folder"))
        source_group.addLayout(self._row(self._source, self._source_browse))
        self.body.addLayout(source_group)

        options = QVBoxLayout()
        options.setSpacing(8)
        options.addWidget(self._recursive)
        options.addWidget(self._custom_output)
        options.addWidget(self._output_row)
        options.addWidget(self._delete)
        self.body.addLayout(options)

        panel = QFrame()
        panel.setObjectName("DialogPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(14, 14, 14, 14)
        panel_layout.setSpacing(12)
        panel_layout.addWidget(self._progress)
        stats = QVBoxLayout()
        stats.setSpacing(3)
        for line in (self._line_done, self._line_ok, self._line_skipped, self._line_failed, self._failure_details):
            stats.addWidget(line)
        panel_layout.addLayout(stats)
        self.body.addWidget(panel)

        self.body.addLayout(button_row(self._cancel, self._start))
        self._apply_output_state()
        self.finish()

    # // layout helpers
    @staticmethod
    def _row(edit: QLineEdit, button: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(edit, 1)
        row.addWidget(button)
        return row

    def _browse(self, edit: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder", edit.text())
        if folder:
            edit.setText(folder)

    def _apply_output_state(self, *_args) -> None:
        custom = self._custom_output.isChecked()
        self._output_row.setVisible(custom)
        enabled = custom and not self._is_running()
        self._output.setEnabled(enabled)
        self._output_browse.setEnabled(enabled)
        self.refit()

    # // state
    def _is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _set_running(self, running: bool) -> None:
        for widget in (
            self._source,
            self._source_browse,
            self._custom_output,
            self._recursive,
            self._delete,
            self._start,
        ):
            widget.setEnabled(not running)
        self._apply_output_state()
        self._cancel.setEnabled(True)
        self._cancel.setText("Cancel" if running or not self._finished else "Close")

    def _build_job(self, source: Path, destination: Path | None):
        common = {"destination_dir": destination, "recursive": self._recursive.isChecked()}
        if self._mode is BatchMode.DUMP:
            return partial(dump_folder, source, delete_originals=self._delete.isChecked(), **common)
        return partial(restore_folder, source, delete_npi=self._delete.isChecked(), **common)

    def _start_batch(self) -> None:
        source = Path(self._source.text().strip())
        if not source.is_dir():
            warn(self, self.windowTitle(), "Select an existing source folder.")
            return
        destination = None
        if self._custom_output.isChecked():
            output_text = self._output.text().strip()
            if not output_text:
                warn(self, self.windowTitle(), "Select an output folder.")
                return
            destination = Path(output_text)
        if self._delete.isChecked() and not confirm(
            self,
            self.windowTitle(),
            "Source files will be deleted after each one is verified. Continue?",
            ok_text="Continue",
            danger=True,
        ):
            return
        self._finished = False
        self._done = self._total = self._processed = self._skipped = self._failed = 0
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._line_failed.hide()
        self._failure_details.hide()
        self._render_stats(cancelled=False)
        self._worker = BatchWorker(self._build_job(source, destination))
        self._worker.progress.connect(self._on_progress)
        self._worker.stats.connect(self._on_stats)
        self._worker.completed.connect(self._on_completed)
        self._set_running(True)
        self._worker.start()

    # // progress
    def _render_stats(self, cancelled: bool, nothing_to_do: bool = False) -> None:
        if nothing_to_do:
            self._line_done.setText("No matching files found")
            self._line_ok.setText(_BLANK)
            self._line_skipped.setText(_BLANK)
            return
        total = f"{self._total:,}"
        if self._dumping:
            head = f"Packed {self._done:,} of {total}"
            ok = f"Verified {self._processed:,}"
            skipped = f"Skipped {self._skipped:,} (already .npi)"
        else:
            head = f"Processed {self._done:,} of {total}"
            ok = f"Restored {self._processed:,}"
            skipped = f"Skipped {self._skipped:,} (already restored)"
        if cancelled:
            head += " - cancelled"
        self._line_done.setText(head)
        self._line_ok.setText(ok)
        self._line_skipped.setText(skipped)

    def _on_progress(self, done: int, total: int) -> None:
        self._done, self._total = done, total
        self._progress.setMaximum(max(total, 1))
        self._progress.setValue(done)
        self._render_stats(cancelled=False)

    def _on_stats(self, processed: int, skipped: int, failed: int) -> None:
        self._processed, self._skipped, self._failed = processed, skipped, failed
        self._render_stats(cancelled=False)

    def _show_failures(self, failures: list[tuple[Path, str]]) -> None:
        if not failures:
            self._line_failed.hide()
            self._failure_details.hide()
            return
        self._line_failed.setText(f"Failed {len(failures):,}")
        self._line_failed.show()
        lines = [f"{path.name}: {shorten(message, 80, placeholder='...')}" for path, message in failures[:_MAX_LISTED_FAILURES]]
        if len(failures) > _MAX_LISTED_FAILURES:
            lines.append(f"and {len(failures) - _MAX_LISTED_FAILURES:,} more")
        self._failure_details.setText("\n".join(lines))
        self._failure_details.show()

    def _on_completed(self, result: BatchResult) -> None:
        if self._worker is not None:
            self._worker.wait()
        if result.processed > 0:
            self.changed = True
        self._processed, self._skipped, self._failed = result.processed, result.skipped, len(result.failures)
        handled = result.processed + result.skipped + len(result.failures)
        if not result.cancelled:
            self._done = max(self._done, handled)
            self._total = max(self._total, handled)
            self._progress.setMaximum(max(self._total, 1))
            self._progress.setValue(self._done)
        nothing = handled == 0 and not result.cancelled
        self._render_stats(cancelled=result.cancelled, nothing_to_do=nothing)
        self._show_failures(result.failures)
        self._finished = True
        self._set_running(False)
        self.refit()

    def _close_or_cancel(self) -> None:
        if self._is_running():
            self._worker.requestInterruption()
            self._cancel.setText("Cancelling...")
            self._cancel.setEnabled(False)
        else:
            self.accept()

    def reject(self) -> None:
        if self._is_running():
            self._worker.requestInterruption()
            return
        super().reject()
