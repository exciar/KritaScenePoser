"""Document integration safety tests with Krita API test doubles.

These establish the export contract only; real Krita behavior is recorded in
the compatibility notes.
"""

import unittest

from krita_scene_poser.integration.krita_document import (
    GUIDE_LAYER_NAME, DocumentExportError, export_layer, find_layer, layer_id, read_document,
    snapshot_document, update_layer,
)

PROFILE = "sRGB-elle-V2-srgbtrc.icc"


class FakeNode:
    counter = 0

    def __init__(self, name, kind="paintlayer", color=("RGBA", "U8", PROFILE)):
        self.layer_name, self.kind, self.color = name, kind, color
        self.write_ok = True
        self.pixels = None
        self.parent = None
        self.opacity = None
        FakeNode.counter += 1
        self.identity = "{{node-{}}}".format(FakeNode.counter)

    def uniqueId(self):
        return self.identity

    def setOpacity(self, value):
        self.opacity = value

    def type(self):
        return self.kind

    def colorModel(self):
        return self.color[0]

    def colorDepth(self):
        return self.color[1]

    def colorProfile(self):
        return self.color[2]

    def setPixelData(self, data, x, y, width, height):
        if not self.write_ok:
            return False
        self.pixels = (data, x, y, width, height)
        return True

    def name(self):  # Krita's Node.name() is a method, not an attribute.
        return self.layer_name

    def parentNode(self):
        return self.parent

    def remove(self):
        self.parent.children.remove(self)
        self.parent = None
        return True


class FakeRoot:
    def __init__(self, identity):
        self.identity = identity
        self.children = []
        self.attach_result = True

    def uniqueId(self):
        return self.identity

    def childNodes(self):
        return list(self.children)

    def addChildNode(self, node, above):
        # Attach even when reporting failure so cleanup is exercised.
        index = self.children.index(above) + 1 if above is not None else 0
        self.children.insert(index, node)
        node.parent = self
        return self.attach_result


class FakeDocument:
    def __init__(self, width=40, height=33, model="RGBA", depth="U8",
                 profile=PROFILE, identity="{doc-1}"):
        self.size = [width, height]
        self.color = (model, depth, profile)
        self.root = FakeRoot(identity)
        self.root.children.append(FakeNode("Background"))
        self.root.children[0].parent = self.root
        self.created = []
        self.refreshes = 0
        self.next_node_color = None

    def width(self):
        return self.size[0]

    def height(self):
        return self.size[1]

    def colorModel(self):
        return self.color[0]

    def colorDepth(self):
        return self.color[1]

    def colorProfile(self):
        return self.color[2]

    def rootNode(self):
        return self.root

    def createNode(self, name, kind):
        node = FakeNode(name, kind, self.next_node_color or self.color)
        self.created.append(node)
        return node

    def refreshProjection(self):
        self.refreshes += 1


class FakeApplication:
    def __init__(self, active, documents=None):
        self.active = active
        self.open_documents = [active] if documents is None else documents

    def activeDocument(self):
        return self.active

    def documents(self):
        return list(self.open_documents)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.document = FakeDocument()
        self.application = FakeApplication(self.document)
        self.pixels = bytes(range(256)) * (40 * 33 * 4 // 256) + bytes(40 * 33 * 4 % 256)

    def export(self, pixels=None, width=40, height=33, **options):
        options.setdefault("application", self.application)
        options.setdefault("name", GUIDE_LAYER_NAME)
        return export_layer(
            self.document, self.pixels if pixels is None else pixels, width, height, **options)

    def test_success_adds_one_named_layer_above_existing_artwork(self):
        background = self.document.root.children[0]
        node = self.export()
        self.assertEqual(self.document.root.children, [background, node])
        self.assertEqual(node.name(), GUIDE_LAYER_NAME)
        self.assertEqual(node.pixels, (self.pixels, 0, 0, 40, 33))
        self.assertIsNone(background.pixels)
        self.assertEqual(self.document.refreshes, 1)

    def test_repeat_export_creates_separate_layers(self):
        first, second = self.export(), self.export()
        self.assertIsNot(first, second)
        self.assertEqual(len(self.document.root.children), 3)

    def test_missing_document_is_rejected(self):
        with self.assertRaisesRegex(DocumentExportError, "Open a document"):
            snapshot_document(None)

    def test_krita_builtin_srgb_profile_is_accepted(self):
        # Krita assigns LCMS's standard sRGB profile to untagged images.
        self.document.color = ("RGBA", "U8", "sRGB built-in")
        node = self.export()
        self.assertEqual(node.pixels, (self.pixels, 0, 0, 40, 33))

    def test_unsupported_color_spaces_are_rejected_before_node_creation(self):
        for color in (("CMYKA", "U8", PROFILE), ("RGBA", "U16", PROFILE),
                      ("RGBA", "U8", "AdobeRGB.icc"), ("RGBA", "U8", ""),
                      ("RGBA", "U8", "sRGB-elle-V2-g10.icc"),
                      ("RGBA", "U8", "krita25_lcms-builtin-sRGB_g100-truegamma.icc")):
            with self.subTest(color=color):
                self.document.color = color
                with self.assertRaisesRegex(DocumentExportError, "Convert Image Color Space"):
                    self.export()
                self.assertEqual(self.document.created, [])

    def test_profile_rejection_names_current_and_accepted_profiles(self):
        self.document.color = ("RGBA", "U8", "AdobeRGB.icc")
        with self.assertRaises(DocumentExportError) as caught:
            snapshot_document(self.document)
        for text in ("AdobeRGB.icc", "sRGB built-in", PROFILE):
            self.assertIn(text, str(caught.exception))

    def test_read_document_describes_unsupported_documents(self):
        self.document.color = ("CMYKA", "U16", "Coated.icc")
        self.document.size = [5000, 9]
        description = read_document(self.document)
        self.assertEqual(
            (description.width, description.height, description.color_model,
             description.color_depth, description.color_profile),
            (5000, 9, "CMYKA", "U16", "Coated.icc"))
        with self.assertRaisesRegex(DocumentExportError, "Open a document"):
            read_document(None)

    def test_oversized_document_is_rejected_before_rendering(self):
        self.document.size = [4097, 10]
        with self.assertRaises(DocumentExportError):
            snapshot_document(self.document)

    def test_incorrect_pixel_buffers_are_rejected(self):
        for pixels in (self.pixels[:-1], bytearray(self.pixels), None):
            with self.subTest(length=None if pixels is None else len(pixels)):
                with self.assertRaises(DocumentExportError):
                    export_layer(self.document, pixels, 40, 33, name=GUIDE_LAYER_NAME, application=self.application)
        self.assertEqual(self.document.created, [])

    def test_a_render_may_be_a_different_size_than_the_document(self):
        """Custom output sizes: the layer keeps pixels outside the canvas."""
        pixels = bytes(41 * 20 * 4)
        node = self.export(pixels, 41, 20, origin=(-3, 7))
        self.assertEqual(node.pixels, (pixels, -3, 7, 41, 20))

    def test_the_origin_must_be_whole_numbers(self):
        for origin in ((1.5, 0), ("x", 0), (1, 2, 3), None):
            with self.subTest(origin=origin):
                with self.assertRaises(DocumentExportError):
                    self.export(origin=origin)

    def test_layer_opacity_is_applied_only_when_it_is_not_full(self):
        node = self.export(opacity=0.5)
        self.assertEqual(node.opacity, 128)
        self.assertIsNone(self.export(opacity=1.0).opacity)  # Krita's own default stands.
        self.assertEqual(self.export(opacity=-3.0).opacity, 0)
        self.assertEqual(self.export(opacity=9.0).opacity, None)  # Clamped back to full.

    def test_changed_snapshot_is_rejected(self):
        snapshot = snapshot_document(self.document)
        self.document.color = ("RGBA", "U8", "sRGB-elle-V4-srgbtrc.icc")
        with self.assertRaisesRegex(DocumentExportError, "Render again"):
            self.export(snapshot=snapshot)
        self.assertEqual(self.document.created, [])

    def test_changed_active_document_is_rejected(self):
        other = FakeDocument(identity="{doc-2}")
        self.application.active = other
        with self.assertRaisesRegex(DocumentExportError, "active document changed"):
            self.export()
        self.assertEqual(self.document.created, [])

    def test_closed_document_is_rejected(self):
        self.application.open_documents = []
        with self.assertRaisesRegex(DocumentExportError, "no longer open"):
            self.export()

    def test_unexpected_layer_color_space_is_never_written(self):
        self.document.next_node_color = ("RGBA", "U16", PROFILE)
        with self.assertRaisesRegex(DocumentExportError, "unexpected color space"):
            self.export()
        self.assertIsNone(self.document.created[0].pixels)
        self.assertEqual(len(self.document.root.children), 1)

    def test_pixel_write_failure_never_attaches_layer(self):
        original = self.document.createNode

        def failing_node(name, kind):
            node = original(name, kind)
            node.write_ok = False
            return node

        self.document.createNode = failing_node
        with self.assertRaisesRegex(DocumentExportError, "could not write"):
            self.export()
        self.assertEqual(len(self.document.root.children), 1)
        self.assertIsNone(self.document.created[0].parent)

    def test_attachment_failure_removes_only_the_new_layer(self):
        background = self.document.root.children[0]
        self.document.root.attach_result = False
        with self.assertRaisesRegex(DocumentExportError, "could not add"):
            self.export()
        self.assertEqual(self.document.root.children, [background])
        self.assertEqual(self.document.refreshes, 1)


class UpdateLayerTests(unittest.TestCase):
    """Rewriting a layer KSP made earlier, instead of stacking up new ones."""

    def setUp(self):
        self.document = FakeDocument()
        self.application = FakeApplication(self.document)
        self.pixels = bytes(40 * 33 * 4)
        self.node = export_layer(self.document, self.pixels, 40, 33, name=GUIDE_LAYER_NAME,
                                 application=self.application)
        self.target = layer_id(self.node)

    def update(self, pixels=None, width=40, height=33, **options):
        options.setdefault("application", self.application)
        return update_layer(self.document, self.target,
                            self.pixels if pixels is None else pixels, width, height, **options)

    def test_updating_rewrites_the_same_layer_and_adds_none(self):
        fresh = bytes(range(256)) * (40 * 33 * 4 // 256) + bytes(40 * 33 * 4 % 256)
        node = self.update(fresh)
        self.assertIs(node, self.node)
        self.assertEqual(node.pixels, (fresh, 0, 0, 40, 33))
        self.assertEqual(len(self.document.root.children), 2)  # Background plus the one layer.
        self.assertEqual(self.document.refreshes, 2)

    def test_a_new_size_and_origin_replace_the_old_pixels(self):
        pixels = bytes(20 * 10 * 4)
        node = self.update(pixels, 20, 10, origin=(5, -4), opacity=0.25)
        self.assertEqual(node.pixels, (pixels, 5, -4, 20, 10))
        self.assertEqual(node.opacity, 64)

    def test_a_missing_layer_asks_the_caller_to_create_one(self):
        self.node.remove()
        self.assertIsNone(self.update())
        self.assertIsNone(update_layer(self.document, "", self.pixels, 40, 33,
                                       application=self.application))
        self.assertIsNone(update_layer(self.document, "{node-999}", self.pixels, 40, 33,
                                       application=self.application))

    def test_a_layer_the_user_renamed_is_left_alone(self):
        self.node.layer_name = "My lineart"
        self.assertIsNone(self.update())
        self.assertEqual(self.node.pixels, (self.pixels, 0, 0, 40, 33))  # Untouched.

    def test_only_ksp_paint_layers_are_matched(self):
        other = FakeNode("KSP Figure Guide", kind="grouplayer")
        self.document.root.children.append(other)
        self.assertIsNone(find_layer(self.document, layer_id(other)))

    def test_document_checks_still_apply(self):
        self.document.color = ("RGBA", "U8", "AdobeRGB.icc")
        with self.assertRaisesRegex(DocumentExportError, "Convert Image Color Space"):
            self.update()
        self.document.color = ("RGBA", "U8", PROFILE)
        with self.assertRaises(DocumentExportError):
            self.update(self.pixels[:-4])  # Wrong byte count for the size.

    def test_a_failed_write_is_reported_and_nothing_is_removed(self):
        self.node.write_ok = False
        with self.assertRaisesRegex(DocumentExportError, "could not write"):
            self.update()
        self.assertIn(self.node, self.document.root.children)


if __name__ == "__main__":
    unittest.main()
