"""KSP: Krita Scene Poser. Pure modules remain importable outside Krita."""

__version__ = "0.0.8"

try:
    import krita
except ImportError:
    krita = None

if krita is not None and hasattr(krita, "DockWidgetFactory"):
    from .plugin import register
    register()
