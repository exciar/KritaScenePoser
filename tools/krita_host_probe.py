"""Headless Phase 0 host probe. Runs inside Krita; never shipped.

From a shell (Windows paths shown)::

    set PYTHONPATH=<repo>;<repo>\\tools
    kritarunner.com -s krita_host_probe -f main <report.json> [<image folder>]

It can also be pasted into Tools > Scripts > Scripter while the plugin is
installed; the report is then printed. Evidence from this probe covers
offscreen rendering, readback, and paint-layer insertion through Krita's
real API. It does not cover the docker widget or its lifecycle. With an image
folder, the line-art step also saves ``lineart-preview.png`` there.
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

        def render(pose, grid=False):
            return qimage_to_bgra(renderer.render_image(
                snapshot(skeleton, pose, camera, width / height, grid=grid), width, height))

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

    def render_lineart():
        # Line art on this GPU: G-buffers, edge pass, widths, and toggles.
        from krita_scene_poser.core.camera import OrbitCamera
        from krita_scene_poser.core.lineart import LineArtSettings, line_uniforms
        from krita_scene_poser.render.shaders import snapshot
        from krita_scene_poser.storage.figures import load_figure
        from PyQt5.QtGui import QColor, QImage, QPainter
        renderer = state["figure_renderer"]
        rig, mesh = load_figure("body_kun")
        renderer.set_mesh("body_kun", mesh)
        skeleton = rig.skeleton
        # A bundled pose holds both forearms across the body, which is exactly
        # what contour lines are for: one part passing in front of another.
        from krita_scene_poser.storage.presets import load_preset
        pose = load_preset("hands-clasped", skeleton).pose
        camera = OrbitCamera(pitch=0.05, yaw=0.35)
        camera.frame([j.position for j in rig.joints] + [j.tail for j in rig.joints])
        width, height = 360, 540
        shot = snapshot(skeleton, pose, camera, width / height, grid=False)
        images = {}

        def render(mode, **settings):
            start = time.perf_counter()
            image = renderer.render_image(shot, width, height, mode,
                                          line_uniforms(LineArtSettings(**settings)))
            seconds = time.perf_counter() - start
            pixels = qimage_to_bgra(image)
            return image, pixels, seconds

        def ink(pixels):
            return sum(pixels[3::4]) / 255.0

        shaded_image, shaded, _ = render("shaded")
        lines_image, lines, seconds = render("lines")
        both_image, both, _ = render("both")
        only_outline = dict(contours=False, creases=False, seams=False)
        thin = ink(render("lines", outline_width=2.0, **only_outline)[1])
        thin_default = ink(render("lines", **only_outline)[1])
        thick = ink(render("lines", outline_width=4.0, **only_outline)[1])
        # Seams already mark every part-to-part overlap, so isolate contours.
        with_contours = ink(render("lines", creases=False, seams=False)[1])
        red = render("lines", color="#ff0000")[1]
        images.update(shaded=shaded_image, lines=lines_image, both=both_image)

        covered = sum(1 for a in shaded[3::4] if a)
        colored = [lines[i:i + 3] for i in range(0, len(lines), 4) if lines[i + 3]]
        opaque_red = [red[i:i + 3] for i in range(0, len(red), 4) if red[i + 3] == 255]
        checks = {
            "corners_transparent": all(lines[(y * width + x) * 4 + 3] == 0 for x, y in (
                (0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))),
            "lines_present": ink(lines) > 500,
            "lines_are_black": bool(colored) and max(max(c) for c in colored) <= 2,
            "lines_sparser_than_figure": ink(lines) < 0.5 * covered,
            "width_doubles_outline": 1.5 < thick / max(thin, 1.0) < 2.6,
            "outline_only_is_sparser": thin < ink(lines),
            "contours_add_lines": with_contours > thin_default,
            "color_applies": bool(opaque_red) and all(
                c[2] >= 253 and c[0] <= 2 and c[1] <= 2 for c in opaque_red),
            "both_covers_the_figure": sum(1 for a in both[3::4] if a) >= covered,
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            raise RuntimeError("Line-art checks failed: {}; ink all={:.0f} outline={:.0f} "
                               "outline+contours={:.0f} thin={:.0f} thick={:.0f}".format(
                                   ", ".join(failed), ink(lines), thin_default, with_contours,
                                   thin, thick))

        start = time.perf_counter()
        large = renderer.render_image(snapshot(skeleton, pose, camera, 1.0, grid=False),
                                      2048, 2048, "lines", line_uniforms(LineArtSettings()))
        large_seconds = time.perf_counter() - start
        renderer.release_gbuffers()
        if large.isNull():
            raise RuntimeError("The 2048 x 2048 line-art render returned no image")

        saved = None
        if len(args) > 1:
            # Shaded, lines, and both side by side on paper, for visual review.
            sheet = QImage(width * 3, height, QImage.Format_ARGB32_Premultiplied)
            sheet.fill(QColor(255, 255, 255))
            painter = QPainter(sheet)
            for column, name in enumerate(("shaded", "lines", "both")):
                painter.drawImage(column * width, 0, images[name])
            painter.end()
            saved = os.path.join(args[1], "lineart-preview.png")
            if not sheet.save(saved):
                raise RuntimeError("Could not save " + saved)
        return {"checks": checks, "figure_coverage_px": covered,
                "ink_px": {"all": round(ink(lines)), "outline_2px": round(thin),
                           "outline_4px": round(thick), "outline_default": round(thin_default),
                           "outline_and_contours": round(with_contours)},
                "lines_render_s": round(seconds, 3), "lines_2048_s": round(large_seconds, 3),
                "preview": os.path.basename(saved) if saved else None}

    def render_opacity():
        # A half-opaque figure must read back half-transparent, with its colors
        # unchanged: proof the premultiplied contract survives the new uniform.
        from krita_scene_poser.core.camera import OrbitCamera
        from krita_scene_poser.render.shaders import snapshot
        from krita_scene_poser.storage.figures import load_figure
        renderer = state["figure_renderer"]
        rig, mesh = load_figure("body_kun")
        renderer.set_mesh("body_kun", mesh)
        skeleton = rig.skeleton
        camera = OrbitCamera(pitch=0.0)
        camera.frame([j.position for j in rig.joints] + [j.tail for j in rig.joints])
        width, height = 240, 360
        pose = skeleton.rest_pose()

        def render(opacity):
            shot = snapshot(skeleton, pose, camera, width / height, grid=False, opacity=opacity)
            return qimage_to_bgra(renderer.render_image(shot, width, height))

        solid, half, clear = render(1.0), render(0.5), render(0.0)
        torso = ((height * 35 // 100) * width + width // 2) * 4
        checks = {
            "opaque_figure_unchanged": solid[torso + 3] == 255,
            "half_alpha": 120 <= half[torso + 3] <= 136,
            # Straight alpha: un-premultiplying must give back the same color.
            "color_kept": all(abs(a - b) <= 3 for a, b in
                              zip(solid[torso:torso + 3], half[torso:torso + 3])),
            "zero_opacity_is_invisible": max(clear[3::4]) == 0,
            "corners_transparent": solid[3] == 0,
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            raise RuntimeError("Opacity checks failed: {}; torso alpha solid={} half={} "
                               "colors {} vs {}".format(
                                   ", ".join(failed), solid[torso + 3], half[torso + 3],
                                   list(solid[torso:torso + 3]), list(half[torso:torso + 3])))
        return {"checks": checks, "torso_alpha": [solid[torso + 3], half[torso + 3]],
                "torso_color": list(solid[torso:torso + 3])}

    def render_shape():
        # A reshaped figure must load, render, and stay on the ground.
        from krita_scene_poser.core.camera import OrbitCamera
        from krita_scene_poser.core.shape import BodyShape
        from krita_scene_poser.render.shaders import snapshot
        from krita_scene_poser.storage.figures import load_figure
        from krita_scene_poser.storage.shaping import figure_key, shaped_figure
        renderer = state["figure_renderer"]
        rig, mesh = load_figure("body_kun")
        width, height = 240, 360
        shapes = {
            "default": BodyShape(),
            "tall": BodyShape(height=1.3),
            "heavy": BodyShape(build=1.35, waist=1.5, chest=1.3, hips=1.4),
            "long_legs": BodyShape(leg_length=1.3),
        }
        results, coverage = {}, {}
        for name, shape in shapes.items():
            start = time.perf_counter()
            shaped_rig, shaped_mesh = shaped_figure(rig, mesh, shape)
            build_seconds = time.perf_counter() - start
            skeleton = shaped_rig.skeleton
            camera = OrbitCamera(pitch=0.0)
            camera.frame([j.position for j in shaped_rig.joints]
                         + [j.tail for j in shaped_rig.joints])
            renderer.set_mesh(figure_key("body_kun", shape), shaped_mesh)
            shot = snapshot(skeleton, skeleton.rest_pose(), camera, width / height, grid=False)
            pixels = qimage_to_bgra(renderer.render_image(shot, width, height))
            covered = sum(1 for a in pixels[3::4] if a)
            coverage[name] = covered
            results[name] = {
                "build_s": round(build_seconds, 3),
                "height_m": round(max(shaped_mesh.positions[1::3]), 3),
                "lowest_m": round(min(shaped_mesh.positions[1::3]), 4),
                "coverage_px": covered,
            }
        checks = {
            "all_shapes_render": all(value > 1000 for value in coverage.values()),
            "tall_is_taller": results["tall"]["height_m"] > results["default"]["height_m"] * 1.2,
            "heavy_covers_more": coverage["heavy"] > coverage["default"],
            "long_legs_is_taller": results["long_legs"]["height_m"] > results["default"]["height_m"],
            "always_on_the_ground": all(abs(value["lowest_m"]) < 0.001
                                        for value in results.values()),
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            raise RuntimeError("Body shape checks failed: {}; {}".format(
                ", ".join(failed), results))
        renderer.set_mesh("body_kun", mesh)  # Leave the cache on the plain figure.
        return {"checks": checks, "shapes": results}

    def import_figure():
        # A figure imported in Krita's own Python must convert, save, load back,
        # and render on the GPU exactly like a bundled one.
        import tempfile
        from krita_scene_poser.core.camera import OrbitCamera
        from krita_scene_poser.render.shaders import snapshot
        from krita_scene_poser.storage.figures import figure_id, load_figure as read_figure
        from krita_scene_poser.storage.figures import write_figure
        from krita_scene_poser.storage.import_figure import FigureImportError, convert, describe
        from krita_scene_poser.storage.import_glb import read_glb
        here = os.path.dirname(os.path.dirname(os.path.abspath(
            sys.modules["krita_scene_poser"].__file__)))
        fixtures = os.path.join(here, "tests")
        if fixtures not in sys.path:
            sys.path.insert(0, fixtures)
        from glb_fixtures import simple_glb  # A model built in the tests, not shipped.

        start = time.perf_counter()
        rig, mesh, report = convert(read_glb(simple_glb(), "probe.glb"), "probe_figure",
                                    "Probe figure")
        converted = time.perf_counter() - start
        folder = tempfile.mkdtemp(prefix="ksp-import-")
        try:
            name = figure_id("Probe figure")
            write_figure(folder, name, rig, mesh)
            stored_rig, stored_mesh = read_figure(name, folders=[folder])
            if len(stored_rig.joints) != len(rig.joints):
                raise RuntimeError("The saved figure did not read back with its joints")
            renderer = state["figure_renderer"]
            renderer.set_mesh("probe_figure", stored_mesh)
            skeleton = stored_rig.skeleton
            camera = OrbitCamera(pitch=0.0)
            camera.frame([joint.position for joint in stored_rig.joints])
            width, height = 200, 300
            shot = snapshot(skeleton, skeleton.rest_pose(), camera, width / height, grid=False)
            pixels = qimage_to_bgra(renderer.render_image(shot, width, height))
            covered = sum(1 for alpha in pixels[3::4] if alpha)
        finally:
            for entry in os.listdir(folder):
                os.remove(os.path.join(folder, entry))
            os.rmdir(folder)

        # A compressed .blend cannot be read by Krita's Python, and must say so.
        compressed = os.path.join(here, "bodychan-bodykun.blend")
        zstd_message = ""
        if os.path.isfile(compressed):
            from krita_scene_poser.storage.import_blend import read_blend
            try:
                read_blend(compressed, "bodychan-bodykun.blend")
            except FigureImportError as error:
                zstd_message = str(error)
            except Exception as error:  # noqa: BLE001  Anything else is a real fault.
                raise RuntimeError("A compressed .blend raised {}: {}".format(
                    type(error).__name__, error))
        checks = {
            "converts": report["joints"] >= 20,
            "saves_and_loads": True,
            "renders": covered > 500,
            "on_the_ground": abs(min(stored_mesh.positions[1::3])) < 1e-4,
            "compressed_blend_explained": (not zstd_message
                                           or "compress" in zstd_message.lower()
                                           or "3.14" in zstd_message),
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            raise RuntimeError("Import checks failed: {}; {}".format(", ".join(failed), report))
        return {"checks": checks, "report": {k: v for k, v in report.items()
                                             if k != "missing"},
                "convert_s": round(converted, 3), "coverage_px": covered,
                "message": describe(report, "Probe figure"),
                "compressed_blend": zstd_message}

    def apply_presets():
        # Every bundled pose loads in Krita's own Python, on both figures.
        from krita_scene_poser.storage.figures import load_figure
        from krita_scene_poser.storage.presets import available_presets, load_preset
        presets, problems = available_presets()
        if problems:
            raise RuntimeError("Presets could not be read: {}".format(problems))
        if not presets:
            raise RuntimeError("No bundled poses were found")
        result = {}
        for figure in ("body_chan", "body_kun"):
            rig, _ = load_figure(figure)
            applied = {}
            for preset, label in presets:
                outcome = load_preset(preset, rig.skeleton)
                if outcome.unknown:
                    raise RuntimeError("{} has joints {} lacks: {}".format(
                        preset, figure, outcome.unknown))
                applied[preset] = len(outcome.applied)
            result[figure] = applied
        return {"presets": [preset for preset, _ in presets], "joints_applied": result}

    def export_custom_size(target):
        # A layer that is not the document's size, placed by its anchor, with
        # pixels kept outside the canvas.
        from krita_scene_poser.core.output import OutputSettings
        from krita_scene_poser.integration.krita_document import export_layer as write_layer
        document = target["document"]
        snapshot = snapshot_document(document)
        outcomes = {}
        for label, settings in (
                ("smaller_centered", OutputSettings(mode="custom", width=120, height=90)),
                ("larger_topleft", OutputSettings(mode="custom", width=320, height=260,
                                                  anchor="topleft")),
                ("oversize_rejected", OutputSettings(mode="custom", width=4000, height=4000,
                                                     supersample=2))):
            try:
                plan = settings.resolve(snapshot.width, snapshot.height)
            except Exception as error:
                outcomes[label] = "rejected: {}".format(error)
                continue
            pixels = bytes(plan.width * plan.height * 4)
            node = write_layer(document, pixels, plan.width, plan.height,
                               name="KSP Figure Guide", snapshot=snapshot,
                               origin=plan.origin, opacity=0.6,
                               application=ProbeApplication(document))
            document.waitForDone()
            outcomes[label] = {"size": [plan.width, plan.height], "origin": list(plan.origin),
                               "opacity": node.opacity()}
        if "rejected" not in str(outcomes["oversize_rejected"]):
            raise RuntimeError("An oversized render was accepted: {}".format(outcomes))
        if outcomes["smaller_centered"]["origin"] != [(snapshot.width - 120) // 2,
                                                      (snapshot.height - 90) // 2]:
            raise RuntimeError("A centered layer landed at {}".format(outcomes))
        if outcomes["larger_topleft"]["opacity"] not in range(150, 157):
            raise RuntimeError("Layer opacity was not applied: {}".format(outcomes))
        return outcomes

    def export_update_layer(target):
        # The second render rewrites the same node instead of stacking layers.
        from krita_scene_poser.integration.krita_document import (
            layer_id, update_layer as rewrite_layer,
        )
        document = target["document"]
        snapshot = snapshot_document(document)
        width, height = 64, 48
        application = ProbeApplication(document)
        first = export_layer(document, bytes(width * height * 4), width, height,
                             name="KSP Lineart", snapshot=snapshot, application=application)
        document.waitForDone()
        before = len(document.rootNode().childNodes())
        target_id = layer_id(first)
        fresh = bytes([200, 100, 50, 255]) * (width * height)
        node = rewrite_layer(document, target_id, fresh, width, height, snapshot=snapshot,
                             application=application)
        document.waitForDone()
        if node is None:
            raise RuntimeError("The layer KSP had just created was not found")
        if len(document.rootNode().childNodes()) != before:
            raise RuntimeError("Updating added a layer instead of rewriting one")
        stored = bytes(node.pixelData(0, 0, width, height))
        if stored != fresh:
            raise RuntimeError("The updated layer does not hold the new pixels")
        # A layer the user renamed is left alone, and a new one is created instead.
        node.setName("My own lineart")
        document.waitForDone()
        refused = rewrite_layer(document, target_id, bytes(width * height * 4), width, height,
                                snapshot=snapshot, application=application)
        return {"same_node": layer_id(node) == target_id, "layer_count": before,
                "renamed_layer_refused": refused is None}

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
            rig.skeleton, rig.skeleton.rest_pose(), camera, width / height, grid=False),
            width, height)
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
        if _run(report, "render_figure", render_figure) is not None:
            _run(report, "render_lineart", render_lineart)
            _run(report, "render_opacity", render_opacity)
            _run(report, "render_shape", render_shape)
            _run(report, "import_figure", import_figure)
    _run(report, "offscreen_renderer", offscreen_renderer)
    state["documents"] = []
    for profile in EXPORT_PROFILES:
        target = {}
        if _run(report, "export_layer[{}]".format(profile),
                lambda: export_step(profile, target)) is not None:
            _run(report, "check_projection[{}]".format(profile),
                 lambda: check_projection(target))
    size_target = {}
    if _run(report, "export_layer_for_sizes", lambda: export_step(PROFILE, size_target)) is not None:
        _run(report, "export_custom_size", lambda: export_custom_size(size_target))
        _run(report, "export_update_layer", lambda: export_update_layer(size_target))
    _run(report, "reject_unsupported_documents", reject_unsupported_documents)
    _run(report, "load_figures", load_figures)
    _run(report, "apply_presets", apply_presets)

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
