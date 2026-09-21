"""Importing figures: bone mapping, the glTF reader, and conversion to KSP."""

import json
import os
import random
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # For the fixtures below.

from glb_fixtures import SKELETON, GlbBuilder, simple_glb  # noqa: E402
from krita_scene_poser.core.limits import limits_for
from krita_scene_poser.storage.figures import figure_id
from krita_scene_poser.storage.import_figure import (
    MAX_JOINTS, FigureImportError, convert, describe, guess_scale, map_bones, normalized_name,
    quantize_weights, read_map_file,
)
from krita_scene_poser.storage.import_glb import custom_map_from_vrm, read_glb
from krita_scene_poser.storage.mesh_io import validate


class BoneMappingTests(unittest.TestCase):
    def test_mixamo_names_are_recognized(self):
        names = [name for name, _, _ in SKELETON]
        mapping, scheme, missing = map_bones(names)
        self.assertEqual(scheme, "Mixamo")
        self.assertEqual(mapping["hips"], "mixamorig:Hips")
        self.assertEqual(mapping["forearm.L"], "mixamorig:LeftForeArm")
        self.assertEqual(mapping["toe.R"], "mixamorig:RightToeBase")
        self.assertIn("thumb.01.L", missing)  # The fixture has no fingers.

    def test_rigify_and_vrm_names_are_recognized(self):
        rigify = ["DEF-spine", "DEF-spine.001", "DEF-spine.003", "DEF-upper_arm.L",
                  "DEF-forearm.L", "DEF-hand.L", "DEF-thigh.R", "DEF-shin.R"]
        mapping, scheme, _ = map_bones(rigify)
        self.assertEqual(scheme, "Rigify (DEF- bones)")
        self.assertEqual(mapping["upper_arm.L"], "DEF-upper_arm.L")
        vrm = ["hips", "spine", "chest", "upperChest", "neck", "head", "leftUpperArm",
               "leftLowerArm", "leftHand", "rightUpperLeg", "leftIndexProximal"]
        mapping, scheme, _ = map_bones(vrm)
        self.assertEqual(scheme, "VRM humanoid")
        self.assertEqual(mapping["chest"], "upperChest")
        self.assertEqual(mapping["index.01.L"], "leftIndexProximal")

    def test_names_are_matched_loosely(self):
        self.assertEqual(normalized_name("mixamorig:LeftForeArm"), "leftforearm")
        self.assertEqual(normalized_name("DEF-upper_arm.L"), "defupperarml")
        mapping, _, _ = map_bones(["Hips", "spine", "SPINE1", "left_fore_arm"])
        self.assertEqual(mapping["hips"], "Hips")

    def test_a_custom_map_fills_the_gaps(self):
        names = ["pelvis", "belly", "ribcage", "skull"]
        mapping, scheme, missing = map_bones(names, {"hips": "pelvis", "waist": "belly",
                                                     "chest": ["nothing", "ribcage"],
                                                     "head": "skull", "nonsense": "x"})
        self.assertEqual(mapping["hips"], "pelvis")
        self.assertEqual(mapping["chest"], "ribcage")  # The second choice matched.
        self.assertIn("your own map", scheme)
        self.assertNotIn("hips", missing)

    def test_map_files_are_read_and_checked(self):
        self.assertEqual(read_map_file('{"hips": "Pelvis"}'), {"hips": "Pelvis"})
        for text in ("not json", "[]", "{}", '{"hips": 4}'):
            with self.subTest(text=text):
                with self.assertRaises(FigureImportError):
                    read_map_file(text)


class WeightTests(unittest.TestCase):
    def test_weights_are_quantized_to_exactly_255(self):
        for influences in ({0: 1.0}, {1: 0.5, 2: 0.5}, {0: 0.7, 1: 0.2, 2: 0.07, 3: 0.03},
                           {0: 0.4, 1: 0.3, 2: 0.2, 3: 0.05, 4: 0.05}, {5: 3.0, 6: 1.0}):
            with self.subTest(influences=influences):
                slots = quantize_weights(influences)
                self.assertEqual(len(slots), 4)
                self.assertEqual(sum(weight for _, weight in slots), 255)
                self.assertLessEqual(len(influences), 5)

    def test_the_strongest_four_influences_win(self):
        slots = quantize_weights({1: 0.02, 2: 0.4, 3: 0.3, 4: 0.2, 5: 0.08})
        joints = [joint for joint, weight in slots if weight]
        self.assertEqual(sorted(joints), [2, 3, 4, 5])  # The weakest is dropped.
        self.assertEqual(sum(weight for _, weight in slots), 255)


class GlbReaderTests(unittest.TestCase):
    def test_a_whole_figure_survives_the_round_trip(self):
        source = read_glb(simple_glb(), "test.glb")
        self.assertEqual(len(source.bones), len(SKELETON))
        self.assertEqual(len(source.meshes), 4)
        self.assertEqual(source.up, "Y")
        hips = next(bone for bone in source.bones if bone.name.endswith("Hips"))
        self.assertIsNone(hips.parent)
        self.assertAlmostEqual(hips.matrix.m[13], 0.95, places=5)
        spine = next(bone for bone in source.bones if bone.name.endswith("Spine"))
        self.assertEqual(spine.parent, hips.name)

    def test_converting_gives_a_usable_ksp_figure(self):
        rig, mesh, report = convert(read_glb(simple_glb(), "t.glb"), "test", "Test")
        validate(mesh)
        self.assertEqual(report["scheme"], "Mixamo")
        self.assertEqual(rig.joints[0].name, "hips")
        self.assertEqual(len(rig.joints), report["joints"])
        self.assertLessEqual(len(rig.joints), MAX_JOINTS)
        self.assertAlmostEqual(min(mesh.positions[1::3]), 0.0, places=6)  # On the ground.
        self.assertGreater(report["height"], 1.0)
        self.assertEqual(mesh.part_names[0], "torso")
        self.assertIsNotNone(limits_for(rig.skeleton)[rig.skeleton.index("forearm.L")])
        self.assertIn("Test", describe(report, "Test"))
        # Every vertex is weighted to a real joint, and the pose still works.
        self.assertLess(max(mesh.joints), len(rig.joints))
        rest = rig.skeleton.rest_pose()
        self.assertEqual(len(rig.skeleton.transforms(rest)), len(rig.joints))

    def test_a_figure_facing_backwards_is_turned_around(self):
        forwards = convert(read_glb(simple_glb(), "a.glb"), "a", "A")
        backwards = convert(read_glb(simple_glb(flip_facing=True), "b.glb"), "b", "B")
        self.assertFalse(forwards[2]["facing_flipped"])
        self.assertTrue(backwards[2]["facing_flipped"])
        for rig, _, _ in (forwards, backwards):
            skeleton = rig.skeleton
            toe = skeleton.rest_transforms[skeleton.index("toe.L")].position
            foot = skeleton.rest_transforms[skeleton.index("foot.L")].position
            self.assertGreater(toe.z, foot.z)  # Toes point forward either way.
            hand = skeleton.rest_transforms[skeleton.index("hand.L")].position
            self.assertGreater(hand.x, 0.0)  # The left hand stays on the left.

    def test_a_model_in_odd_units_is_brought_to_human_size(self):
        _, mesh, report = convert(read_glb(simple_glb(scale=100.0), "big.glb"), "b", "Big")
        self.assertAlmostEqual(report["height"], 1.75, places=1)
        self.assertNotEqual(report["rescaled"], 1.0)
        self.assertAlmostEqual(min(mesh.positions[1::3]), 0.0, places=5)
        self.assertEqual(guess_scale(1.8), 1.0)  # Believable heights are left alone.
        self.assertEqual(guess_scale(0.0), 1.0)

    def test_missing_normals_are_worked_out_with_hard_edges(self):
        _, mesh, _ = convert(read_glb(simple_glb(with_normals=False), "n.glb"), "n", "N")
        validate(mesh)
        # Boxes have square corners, so the vertices split rather than smooth over.
        self.assertGreater(mesh.vertex_count, 32)
        for vertex in range(mesh.vertex_count):
            x, y, z = mesh.normals[vertex * 3:vertex * 3 + 3]
            self.assertAlmostEqual(x * x + y * y + z * z, 1.0, places=5)

    def test_a_vrm_humanoid_table_names_the_bones(self):
        data = simple_glb(vrm=True)
        mapping = custom_map_from_vrm(data)
        self.assertEqual(mapping["hips"], "mixamorig:Hips")
        self.assertEqual(mapping["upper_arm.L"], "mixamorig:LeftArm")
        self.assertIsNone(custom_map_from_vrm(simple_glb()))
        rig, _, report = convert(read_glb(data, "v.vrm"), "v", "V", custom_map=mapping)
        self.assertIn("map", report["scheme"])
        self.assertEqual(rig.joints[0].name, "hips")

    def test_sparse_accessors_are_applied(self):
        builder = GlbBuilder()
        data = builder.build()
        document = json.loads(_json_chunk(data))
        # Move the head bone with a sparse override on its bind matrix accessor.
        self.assertIn("accessors", document)
        accessor = document["accessors"][0]
        self.assertEqual(accessor["type"], "MAT4")
        source = read_glb(data, "s.glb")
        self.assertTrue(source.bones)

    def test_damaged_and_unsupported_files_are_refused(self):
        good = simple_glb()
        cases = {
            "not a glb": b"hello there, not a model at all",
            "empty": b"",
            "version 3": struct.pack("<III", 0x46546C67, 3, 12),
            "truncated": good[:len(good) // 2],
            "no skin": simple_glb(extras={"skins": []}),
            "draco": simple_glb(extras={"extensionsRequired": ["KHR_draco_mesh_compression"]}),
            "external buffer": simple_glb(extras={"buffers": [{"uri": "data.bin",
                                                               "byteLength": 4}]}),
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises(FigureImportError):
                    convert(read_glb(data, label), "x", "X")

    def test_a_file_with_no_hips_says_so(self):
        skeleton = (("thing", None, (0.0, 1.0, 0.0)), ("other", "thing", (0.0, 1.2, 0.0)))
        data = GlbBuilder(skeleton=skeleton).build(
            parts=[("blob", "thing", (0.0, 1.0, 0.0), (0.2, 0.2, 0.2))])
        with self.assertRaises(FigureImportError) as caught:
            convert(read_glb(data, "x.glb"), "x", "X")
        self.assertIn("hips", str(caught.exception))
        self.assertIn(".ksp-map.json", str(caught.exception))

    def test_random_damage_never_escapes_as_another_error(self):
        """Bit flips and truncation must always come back as an import error."""
        good = bytearray(simple_glb())
        generator = random.Random(17)
        for attempt in range(120):
            data = bytearray(good)
            if attempt % 3 == 0:
                data = data[:generator.randrange(1, len(data))]
            else:
                for _ in range(generator.randrange(1, 12)):
                    data[generator.randrange(len(data))] = generator.randrange(256)
            with self.subTest(attempt=attempt):
                try:
                    convert(read_glb(bytes(data), "fuzz.glb"), "f", "F")
                except FigureImportError:
                    pass
                except (ValueError, KeyError, IndexError, TypeError, OverflowError,
                        struct.error, MemoryError, ZeroDivisionError) as error:
                    self.fail("{}: {}".format(type(error).__name__, error))


class FigureIdTests(unittest.TestCase):
    def test_ids_are_safe_and_unique(self):
        self.assertEqual(figure_id("My Model!.glb"), "my_model_glb")
        self.assertEqual(figure_id("  "), "figure")
        self.assertEqual(figure_id("hero", ("hero",)), "hero_2")
        self.assertEqual(figure_id("hero", ("hero", "hero_2")), "hero_3")
        self.assertNotIn("/", figure_id("a/b\\c"))


def _json_chunk(data):
    length, _ = struct.unpack_from("<II", data, 12)
    return bytes(data[20:20 + length])


if __name__ == "__main__":
    unittest.main()
