"""
The Stokes choice in the top bar: I, Q, U and V as a segmented control,
the derived products (L, Lfrac, Q/I...) in a dropdown beside it, and after
them the correlation planes the image holds (RR, LL, XX...), if any.

The dropdown is the one QComboBox the rest of the tab reads and sets
(``stokes_combo``): it holds every product, and the segmented buttons
follow it. While a base product is chosen the dropdown just says "More".
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QWidget,
)

from ..processing.stokes import CORRELATIONS
from .item_delegates import DisabledItemDelegate
from .widgets import segmented

BASE = ("I", "Q", "U", "V")
DERIVED = ("L", "Lfrac", "Vfrac", "Q/I", "U/I", "U/V", "PANG")
PRODUCTS = BASE + DERIVED


def offer_correlations(combo, fixed, available):
    """Put the correlation planes among ``available`` (RR, LL, XX..., as
    get_available_stokes lists them) in ``combo`` after its ``fixed`` first
    items, in place of the previous image's. Quietly: the caller sets the
    product shown afterwards. Returns whether the items changed."""
    wanted = [label for label in available if label in CORRELATIONS]
    held = [combo.itemText(i) for i in range(fixed, combo.count())]
    if wanted == held:
        return False
    was_blocked = combo.blockSignals(True)
    try:
        for index in range(combo.count() - 1, fixed - 1, -1):
            combo.removeItem(index)
        combo.addItems(wanted)
    finally:
        combo.blockSignals(was_blocked)
    return True


class _StokesCombo(QComboBox):
    """The combo of every product, labelled "More" while a base one is
    current. ``on_set`` is called with the current text after every set of
    the current item, even one made with signals blocked (the loaders set
    the shown product that way, and the buttons must still follow; a
    signal of this combo's own would be blocked too)."""

    def __init__(self, on_set, parent=None):
        super().__init__(parent)
        self._on_set = on_set

    def setCurrentText(self, text):
        super().setCurrentText(text)
        self._on_set(self.currentText())

    def setCurrentIndex(self, index):
        super().setCurrentIndex(index)
        self._on_set(self.currentText())

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        if option.currentText in BASE:
            option.currentText = "More"
        painter.drawComplexControl(QStyle.CC_ComboBox, option)
        painter.drawControl(QStyle.CE_ComboBoxLabel, option)


class StokesPicker(QWidget):
    """The segmented I / Q / U / V and the dropdown. ``combo`` is the
    QComboBox to read and set."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.combo = _StokesCombo(self._follow)
        self.combo.addItems(list(PRODUCTS))
        self.combo.setItemDelegate(DisabledItemDelegate(self.combo))
        self.combo.setToolTip("Derived Stokes products")
        width = self.combo.fontMetrics().horizontalAdvance("Lfrac") + 36
        self.combo.setFixedWidth(width)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = {}
        for name in BASE:
            button = QPushButton(name)
            button.setCheckable(True)
            button.setToolTip(f"Stokes {name}")
            button.setFixedWidth(button.fontMetrics().horizontalAdvance("W") + 16)
            button.clicked.connect(lambda _=False, n=name: self.combo.setCurrentText(n))
            self._group.addButton(button)
            self._buttons[name] = button
        layout.addWidget(segmented(list(self._buttons.values())))
        layout.addWidget(self.combo)

        self.combo.currentTextChanged.connect(self._follow)
        self.combo.model().dataChanged.connect(lambda *_: self._follow_enabled())
        self._follow(self.combo.currentText())
        self._follow_enabled()

    def offer_correlations(self, available):
        """The dropdown offers the correlation planes among ``available``
        after the derived products (see the module's offer_correlations)."""
        if offer_correlations(self.combo, len(PRODUCTS), available):
            self._follow(self.combo.currentText())

    def _follow(self, text):
        """The button of the current product is the one on."""
        self._group.setExclusive(False)
        for name, button in self._buttons.items():
            button.setChecked(name == text)
        self._group.setExclusive(True)

    def _follow_enabled(self):
        """A button is enabled while its item in the combo is."""
        model = self.combo.model()
        for index in range(self.combo.count()):
            item = model.item(index)
            name = self.combo.itemText(index)
            if item is not None and name in self._buttons:
                self._buttons[name].setEnabled(bool(item.flags() & Qt.ItemIsEnabled))
