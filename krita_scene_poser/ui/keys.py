"""Pose shortcuts for the docker viewport and the canvas overlay.

The views claim these keys through ShortcutOverride, or Krita's window shortcuts
(Ctrl+Z for document undo) would take them first.
"""

from PyQt5.QtCore import Qt

CTRL, SHIFT = int(Qt.ControlModifier), int(Qt.ShiftModifier)
MODIFIERS = CTRL | SHIFT | int(Qt.AltModifier) | int(Qt.MetaModifier)
SHORTCUTS = {
    (Qt.Key_Escape, 0): "cancel", (Qt.Key_F, 0): "frame", (Qt.Key_R, 0): "reset_joint",
    (Qt.Key_O, 0): "ortho", (Qt.Key_T, 0): "mode", (Qt.Key_Z, CTRL): "undo",
    (Qt.Key_Z, CTRL | SHIFT): "redo", (Qt.Key_Y, CTRL): "redo",
}


def shortcut(event):
    return SHORTCUTS.get((event.key(), int(event.modifiers()) & MODIFIERS))
