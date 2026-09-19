"""KSP-local pose undo/redo, separate from Krita's document undo.

Poses are immutable, so each entry stores the pose before and after one
completed gesture or command.
"""


class PoseHistory:
    def __init__(self, limit=200):
        self.limit = limit
        self._undo, self._redo = [], []

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    def record(self, before, after):
        """Add one entry; returns False when nothing changed."""
        if before == after:
            return False
        self._undo.append((before, after))
        del self._undo[:-self.limit]
        self._redo.clear()
        return True

    def undo(self):
        """The pose to restore, or None."""
        if not self._undo:
            return None
        entry = self._undo.pop()
        self._redo.append(entry)
        return entry[0]

    def redo(self):
        if not self._redo:
            return None
        entry = self._redo.pop()
        self._undo.append(entry)
        return entry[1]

    def clear(self):
        self._undo.clear()
        self._redo.clear()
