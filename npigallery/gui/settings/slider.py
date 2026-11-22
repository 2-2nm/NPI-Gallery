from PySide6.QtCore import QEasingCurve, Qt, QVariantAnimation
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider


class SmoothSlider(QSlider):
    GLIDE_MS = 140

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._scrubbing = False
        self._glide = QVariantAnimation(self)
        self._glide.setDuration(self.GLIDE_MS)
        self._glide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._glide.valueChanged.connect(self._on_glide)

    def _on_glide(self, value) -> None:
        self.setValue(round(value))

    def _option(self) -> QStyleOptionSlider:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        return option

    def _value_at(self, x: float) -> int:
        option = self._option()
        style = self.style()
        groove = style.subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderGroove, self)
        handle = style.subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self)
        span = max(1, groove.width() - handle.width())
        offset = round(x) - groove.x() - handle.width() // 2
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), offset, span, self.invertedAppearance())

    def _glide_to(self, target: int) -> None:
        self._glide.stop()
        if target == self.value():
            return
        self._glide.setStartValue(float(self.value()))
        self._glide.setEndValue(float(target))
        self._glide.start()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._glide.stop()
        option = self._option()
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self
        )
        if handle.contains(event.position().toPoint()):
            super().mousePressEvent(event)
            return
        self._scrubbing = True
        self.setSliderDown(True)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._glide_to(self._value_at(event.position().x()))
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if not self._scrubbing:
            super().mouseMoveEvent(event)
            return
        target = self._value_at(event.position().x())
        if self._glide.state() == QVariantAnimation.State.Running:
            self._glide.setEndValue(float(target))
        else:
            self.setValue(target)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if not self._scrubbing:
            super().mouseReleaseEvent(event)
            return
        self._scrubbing = False
        self.setSliderDown(False)
        event.accept()

    def wheelEvent(self, event) -> None:
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)