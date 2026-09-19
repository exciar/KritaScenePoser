"""Draw interaction overlay primitives (selection, IK marker, rings) with QPainter."""

from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtGui import QColor, QPen

AXIS_COLORS = (QColor(235, 75, 75), QColor(90, 205, 95), QColor(85, 140, 245))
SELECTION_COLOR = QColor(255, 160, 60)


def _pen(color, width, style=Qt.SolidLine):
    pen = QPen(color, width, style)
    pen.setCosmetic(True)
    return pen


def paint_primitives(painter, primitives, to_widget=lambda point: point):
    """``to_widget`` maps camera-screen points to widget pixels; radii are display pixels."""
    painter.setBrush(Qt.NoBrush)
    for primitive in primitives:
        a = QPointF(*to_widget(primitive.a))
        if primitive.kind in ("line", "segment"):
            b = QPointF(*to_widget(primitive.b))
            if primitive.style == "ring":
                color = QColor(AXIS_COLORS[primitive.axis])
                color.setAlpha(255 if primitive.front else 80)
                painter.setPen(_pen(color, 2.5 if primitive.front else 1.5))
            else:
                painter.setPen(_pen(SELECTION_COLOR, 2))
            painter.drawLine(a, b)
        elif primitive.kind == "circle":
            style = Qt.DashLine if primitive.style == "ik" else Qt.SolidLine
            painter.setPen(_pen(SELECTION_COLOR, 1.5 if primitive.style == "ik" else 2, style))
            painter.drawEllipse(a, primitive.radius, primitive.radius)
