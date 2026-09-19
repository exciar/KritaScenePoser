"""Krita registration only."""
from krita import DockWidgetFactory, DockWidgetFactoryBase, Krita

from .ui.actions import KSPExtension
from .ui.docker import DOCKER_ID, KSPDocker


def register():
    application = Krita.instance()
    application.addDockWidgetFactory(DockWidgetFactory(
        DOCKER_ID, DockWidgetFactoryBase.DockRight, KSPDocker))
    application.addExtension(KSPExtension(application))
