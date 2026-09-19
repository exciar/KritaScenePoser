"""Headless Phase 0 host probe. Runs inside Krita; never shipped.

From a shell (Windows paths shown)::

    set PYTHONPATH=<repo>;<repo>\\tools
    kritarunner.com -s krita_host_probe -f main <report.json>

It can also be pasted into Tools > Scripts > Scripter while the plugin is
installed; the report is then printed. Evidence from this probe covers
offscreen rendering, readback, and paint-layer insertion through Krita's
real API. It does not cover the docker widget or its lifecycle.
"""

import json
import os
import sys
import time
import traceback

PROBE_SIZE = (257, 193)  # Odd dimensions on purpose.
TIMING_SIZE = (2048, 2048)
PROFILE = "sRGB-elle-V2-srgbtrc.icc"
# Krita's new-document default, and the profile it gives untagged images.
EXPORT_PROFILES = (PROFILE, "sRGB built-in")


class ProbeApplication:
    """Headless Krita has no view, so no document is active; stand in for it."""

    def __init__(self, document):
        self.document = document

    def activeDocument(self):
        return self.document

    def documents(self):
        return [self.document]


def synthetic_triangle(width, height):
    """Straight-alpha BGRA of the renderer's triangle, for when GL is unavailable.

    This lets the document path be verified independently of the renderer.
    """
    result = bytearray(width * height * 4)
    for y in range(height):
        red = (1 - 2 * (y + .5) / height + .65) / 1.4
        for x in range(width):
            blue = (1 - red + (2 * (x + .5) / width - 1) / .75) / 2
            green = 1 - red - blue
            if min(red, green, blue) >= 0:
                offset = (y * width + x) * 4
                result[offset:offset + 4] = bytes((
                    round(blue * 255), round(green * 255), round(red * 255), 191))
    return bytes(result)


def _ensure_importable():
    try:
        import krita_scene_poser  # noqa: F401
    except ImportError:
        here = globals().get("__file__")
        if not here:
            raise
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(here))))


def _run(report, name, function):
    try:
        detail = function()
    except Exception as error:
        report["steps"][name] = {
            "status": "fail",
            "error": "{}: {}".format(type(error).__name__, error),
            "trace": traceback.format_exc(limit=6),
        }
        return None
    report["steps"][name] = {"status": "pass", "detail": detail}
    return detail


def _close(document):
    try:
        document.setModified(False)
    except AttributeError:
        pass
    document.close()


def main(args=None):
    args = list(args or [])
    _ensure_importable()
    from krita import Krita
    from PyQt5.QtCore import QCoreApplication, Qt
    from PyQt5.QtGui import QOffscreenSurface, QOpenGLContext, QSurfaceFormat

    from krita_scene_poser import diagnostics
    from krita_scene_poser.integration.krita_document import (
        PROBE_LAYER_NAME, DocumentExportError, export_layer, snapshot_document,
    )
    from krita_scene_poser.render.gl_renderer import TriangleRenderer
    from krita_scene_poser.render.pixel_transfer import (
        qimage_to_bgra, validate_probe_pixels,
    )

    report = {"environment": diagnostics.collect()["environment"], "steps": {}}
    state = {}

    def import_docker():
        import krita_scene_poser.ui.actions as actions
        import krita_scene_poser.ui.viewport  # noqa: F401  The posing viewport.
        import krita_scene_poser.ui.canvas_overlay  # noqa: F401  Canvas posing.
        import krita_scene_poser.ui.canvas_bridge as bridge
        import krita_scene_poser.ui.session  # noqa: F401
        import krita_scene_poser.ui.docker as docker
        from krita_scene_poser.integration import settings
        from PyQt5.QtGui import QIcon
        icon = QIcon(actions.ICON)
        if icon.isNull() or not icon.availableSizes() and icon.pixmap(24, 24).isNull():
            raise RuntimeError("The action icon did not load: " + os.path.basename(actions.ICON))
        return {"docker": docker.KSPDocker.__name__,
                "canvas_classes": list(bridge.CANVAS_CLASSES),
                "extension": actions.KSPExtension.__name__,
                "settings": vars(settings.load()),
                "icon_loaded": True}

    def create_context():
        fmt = QSurfaceFormat.defaultFormat()
        surface = QOffscreenSurface()
        surface.setFormat(fmt)
        surface.create()
        if not surface.isValid():
            raise RuntimeError("QOffscreenSurface is invalid")
        context = QOpenGLContext()
        context.setFormat(fmt)
        share = QOpenGLContext.globalShareContext()
        if share is not None:
            context.setShareContext(share)
        if not context.create() or not context.makeCurrent(surface):
            raise RuntimeError("Cannot create or make current an OpenGL context")
        state.update(surface=surface, context=context)
        actual = context.format()
        return {
            "requested": "{}.{} profile={}".format(
                fmt.majorVersion(), fmt.minorVersion(), int(fmt.profile())),
            "actual": "{}.{} profile={}".format(
                actual.majorVersion(), actual.minorVersion(), int(actual.profile())),
            "is_opengl_es": context.isOpenGLES(),
            "aa_use_opengl_es": QCoreApplication.testAttribute(Qt.AA_UseOpenGLES),
        }

    def render_probe():
        renderer = TriangleRenderer()
        renderer.initialize(state["context"])
        state["renderer"] = renderer
        width, height = PROBE_SIZE
        image = renderer.render_image(width, height)
        pixels = qimage_to_bgra(image)
        probe = validate_probe_pixels(pixels, width, height)
        state.update(pixels=pixels, probe=probe)
        return {"gl": renderer.details, "readback_qimage_format": int(image.format()),
                "probe": probe}

    def time_large_render():
        width, height = TIMING_SIZE
        start = time.perf_counter()
        image = state["renderer"].render_image(width, height)
        rendered = time.perf_counter()
        pixels = qimage_to_bgra(image)
        converted = time.perf_counter()
        validate_probe_pixels(pixels, width, height)
        return {"size": list(TIMING_SIZE),
                "render_readback_s": round(rendered - start, 3),
                "convert_s": round(converted - rendered, 3)}

    def export_step(profile, target):
        width, height = PROBE_SIZE
        if "pixels" not in state:
            state["pixels"] = synthetic_triangle(width, height)
            state["probe"] = validate_probe_pixels(state["pixels"], width, height)
        document = Krita.instance().createDocument(
            width, height, "KSP host probe", "RGBA", "U8", profile, 300.0)
        state["documents"].append(document)
        target["document"] = document
        snapshot = snapshot_document(document)
        node = export_layer(document, state["pixels"], width, height, name=PROBE_LAYER_NAME,
                               snapshot=snapshot, application=ProbeApplication(document))
        document.waitForDone()
        stored = bytes(node.pixelData(0, 0, width, height))
        if stored != state["pixels"]:
            raise RuntimeError("Layer pixelData differs from the exported bytes")
        target["node"] = node
        return {"pixel_source": "synthetic" if "renderer" not in state else "opengl",
                "document_profile": document.colorProfile(),
                "layer": node.name(), "type": node.type(),
                "color": [node.colorModel(), node.colorDepth(), node.colorProfile()],
                "layer_count": len(document.rootNode().childNodes()),
                "pixel_data_round_trip": True}

    def check_projection(target):
        # Qt's own reading of Krita's composite verifies Krita's byte order,
        # row orientation, and alpha interpretation independently of KSP.
        width, height = PROBE_SIZE
        document, layer = target["document"], target["node"]
        samples = state["probe"]["samples"]
        corners = ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))

        def rgba(image, x, y):
            color = image.pixelColor(x, y)
            return [color.red(), color.green(), color.blue(), color.alpha()]

        def compare(label, actual, expected):
            if any(abs(a - e) > 3 for a, e in zip(actual, expected)):
                raise RuntimeError("{}: Krita shows {}; expected {}".format(label, actual, expected))

        composite = document.projection(0, 0, width, height)
        below = rgba(composite, 0, 0)  # The KSP layer is transparent at corners.
        result = {"background": below, "over_background": {}, "isolated": {}}
        if below[3] == 255:
            # Straight alpha composites as c*a + bg*(1-a); premultiplied would not.
            for name, sample in samples.items():
                x, y = sample["xy"]
                alpha = sample["rgba"][3] / 255
                expected = [round(c * alpha + b * (1 - alpha))
                            for c, b in zip(sample["rgba"][:3], below[:3])] + [255]
                actual = rgba(composite, x, y)
                compare("Over background " + name, actual, expected)
                result["over_background"][name] = actual

        layer_id = layer.uniqueId()
        for node in document.rootNode().childNodes():
            if node.uniqueId() != layer_id:
                node.setVisible(False)
        document.refreshProjection()
        document.waitForDone()
        isolated = document.projection(0, 0, width, height)
        for name, sample in samples.items():
            actual = rgba(isolated, *sample["xy"])
            compare("Isolated " + name, actual, sample["rgba"])
            result["isolated"][name] = actual
        if not result["isolated"]["top_red"][0] > result["isolated"]["top_red"][2] + 100:
            raise RuntimeError("The top sample is not red in Krita's projection")
        for x, y in corners:
            if isolated.pixelColor(x, y).alpha() != 0:
                raise RuntimeError("Isolated corner ({}, {}) is not transparent".format(x, y))
        result["transparent_corners"] = True
        return result

    def render_figure():
        # The viewport's skinning shader on this GPU: compile, upload, render.
        from krita_scene_poser.core.camera import OrbitCamera
        from krita_scene_poser.core.math3d import Quat, Z_AXIS
        from krita_scene_poser.render.figure_renderer import FigureRenderer
        from krita_scene_poser.render.shaders import snapshot
        from krita_scene_poser.storage.figures import load_figure
        renderer = FigureRenderer()
        state["figure_renderer"] = renderer
        renderer.initialize(state["context"])
        rig, mesh = load_figure("body_kun")
        renderer.set_mesh("body_kun", mesh)
        skeleton = rig.skeleton
        camera = OrbitCamera(pitch=0.0)
        camera.frame([j.position for j in rig.joints] + [j.tail for j in rig.joints])
        width, height = 240, 360
        view_projection = camera.view_projection(width / height)

        def render(pose, grid=False):
            return qimage_to_bgra(renderer.render_image(
                snapshot(skeleton, pose, view_projection, grid=grid), width, height))

        start = time.perf_counter()
        rest = render(skeleton.rest_pose())
        seconds = time.perf_counter() - start
        raised = skeleton.rotate_world(skeleton.rest_pose(), skeleton.index("upper_arm.L"),
                                       Quat.from_axis_angle(Z_AXIS, 1.3))
        posed, gridded = render(raised), render(skeleton.rest_pose(), grid=True)

        def alpha(pixels, x, y):
            return pixels[(y * width + x) * 4 + 3]

        covered = sum(1 for i in range(3, len(rest), 4) if rest[i])
        changed = [(i // 4) % width for i in range(3, len(rest), 4) if rest[i] != posed[i]]
        right = sum(1 for x in changed if x >= width // 2)
        grid_pixels = sum(1 for i in range(3, len(gridded), 4) if gridded[i]) - covered
        checks = {
            "corners_transparent": all(alpha(rest, x, y) == 0 for x, y in (
                (0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))),
            "torso_opaque": alpha(rest, width // 2, int(height * 0.35)) == 255,
            "coverage_plausible": 0.05 < covered / (width * height) < 0.6,
            # Raising the figure's left arm changes the screen's right side.
            "left_arm_on_screen_right": len(changed) > 100 and right > 3 * (len(changed) - right),
            "grid_drawn": grid_pixels > 200,
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            raise RuntimeError("Figure render checks failed: " + ", ".join(failed))
        return {"details": renderer.details, "checks": checks,
                "coverage": round(covered / (width * height), 3), "changed_pixels": len(changed),
                "render_s": round(seconds, 3)}

    def offscreen_renderer():
        # KSP's own context, as used by the canvas overlay, Create Layer, and Self-Test.
        from krita_scene_poser.core.camera import OrbitCamera
        from krita_scene_poser.render.offscreen import OffscreenRenderer
        from krita_scene_poser.render.shaders import snapshot
        from krita_scene_poser.storage.figures import load_figure
        from PyQt5.QtGui import QOpenGLContext
        renderer = OffscreenRenderer()
        state["offscreen"] = renderer
        rig, mesh = load_figure("body_chan")
        camera = OrbitCamera(pitch=0.1)
        camera.frame([j.position for j in rig.joints] + [j.tail for j in rig.joints])
        width, height = 300, 400
        start = time.perf_counter()
        image = renderer.render("body_chan", mesh, snapshot(
            rig.skeleton, rig.skeleton.rest_pose(), camera.view_projection(width / height),
            grid=False), width, height)
        seconds = time.perf_counter() - start
        pixels = qimage_to_bgra(image)
        covered = sum(1 for i in range(3, len(pixels), 4) if pixels[i])
        probe = renderer.self_test()
        if not 0.05 < covered / (width * height) < 0.6:
            raise RuntimeError("The offscreen figure render covers {:.1%}".format(covered / (width * height)))
        if QOpenGLContext.currentContext() is not None:
            raise RuntimeError("The offscreen renderer left a context current")
        return {"coverage": round(covered / (width * height), 3), "first_render_s": round(seconds, 3),
                "self_test": bool(probe["transparent_corners"]), "no_context_left_current": True}

    def load_figures():
        # Krita runs its own Python; unit tests run elsewhere, so load the
        # shipped assets here too.
        from krita_scene_poser.core.math3d import Quat, Z_AXIS
        from krita_scene_poser.storage.mesh_io import read_mesh
        from krita_scene_poser.storage.rig_io import read_rig
        folder = os.path.join(os.path.dirname(os.path.abspath(
            sys.modules["krita_scene_poser"].__file__)), "assets", "figures")
        result = {}
        for figure in ("body_chan", "body_kun"):
            start = time.perf_counter()
            with open(os.path.join(folder, figure + ".rig.json"), encoding="utf-8") as handle:
                rig = read_rig(handle.read())
            with open(os.path.join(folder, figure + ".mesh"), "rb") as handle:
                mesh = read_mesh(handle.read(), joint_count=len(rig.joints))
            loaded = time.perf_counter()
            skeleton = rig.skeleton
            pose = skeleton.rotate_world(skeleton.rest_pose(), skeleton.index("upper_arm.L"),
                                         Quat.from_axis_angle(Z_AXIS, 1.0))
            matrices = skeleton.skinning_matrices(pose)
            posed = time.perf_counter()
            result[figure] = {
                "display_name": rig.display_name, "joints": len(rig.joints),
                "vertices": mesh.vertex_count, "triangles": mesh.triangle_count,
                "parts": len(mesh.part_names), "skinning_matrices": len(matrices),
                "load_s": round(loaded - start, 3), "pose_s": round(posed - loaded, 4)}
        return result

    def reject_unsupported_documents():
        outcomes = {}
        for label, arguments in (("graya", (64, 64, "p", "GRAYA", "U8", "", 72.0)),
                                 ("rgba_u16", (64, 64, "p", "RGBA", "U16", "", 72.0)),
                                 ("linear_srgb", (64, 64, "p", "RGBA", "U8",
                                                  "sRGB-elle-V2-g10.icc", 72.0)),
                                 ("oversize", (4097, 8, "p", "RGBA", "U8", PROFILE, 72.0))):
            document = Krita.instance().createDocument(*arguments)
            try:
                snapshot_document(document)
                outcomes[label] = "accepted"
            except DocumentExportError as error:
                outcomes[label] = "rejected: {}".format(error)
            finally:
                _close(document)
        if any(value == "accepted" for value in outcomes.values()):
            raise RuntimeError("An unsupported document was accepted: {}".format(outcomes))
        return outcomes

    _run(report, "import_docker", import_docker)
    if _run(report, "create_context", create_context) is not None:
        if _run(report, "render_probe", render_probe) is not None:
            _run(report, "time_large_render", time_large_render)
        _run(report, "render_figure", render_figure)
    _run(report, "offscreen_renderer", offscreen_renderer)
    state["documents"] = []
    for profile in EXPORT_PROFILES:
        target = {}
        if _run(report, "export_layer[{}]".format(profile),
                lambda: export_step(profile, target)) is not None:
            _run(report, "check_projection[{}]".format(profile),
                 lambda: check_projection(target))
    _run(report, "reject_unsupported_documents", reject_unsupported_documents)
    _run(report, "load_figures", load_figures)

    for document in state["documents"]:
        _close(document)
    if "context" in state:
        state["context"].makeCurrent(state["surface"])  # The offscreen step releases it.
    for key in ("renderer", "figure_renderer"):
        if key in state and state[key].functions is not None:
            state[key].destroy()
    if "offscreen" in state:
        state["offscreen"].destroy()
    if "context" in state:
        state["context"].doneCurrent()

    report["overall"] = "pass" if all(
        step["status"] == "pass" for step in report["steps"].values()) else "fail"
    text = json.dumps(report, indent=2, sort_keys=True)
    if args:
        with open(args[0], "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    return report["overall"]


if __name__ == "__main__":
    main()
