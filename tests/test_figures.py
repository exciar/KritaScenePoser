"""The shipped Body-chan and Body-kun assets: loadable, consistent, and posable."""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

from krita_scene_poser.core.math3d import Mat4, Quat, Vec3, Z_AXIS
from krita_scene_poser.core.skeleton import mirror_vector
from krita_scene_poser.storage.mesh_io import read_mesh
from krita_scene_poser.storage.rig_io import read_rig

ROOT = Path(__file__).resolve().parents[1]
FIGURES = ROOT / "krita_scene_poser" / "assets" / "figures"
SOURCE = ROOT / "bodychan-bodykun.blend"


def load(figure):
    rig = read_rig((FIGURES / (figure + ".rig.json")).read_text(encoding="utf-8"))
    mesh = read_mesh((FIGURES / (figure + ".mesh")).read_bytes(), joint_count=len(rig.joints))
    return rig, mesh


class FigureAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.figures = {name: load(name) for name in ("body_chan", "body_kun")}

    def test_both_figures_share_one_52_joint_rig_layout(self):
        names = [[j.name for j in rig.joints] for rig, _ in self.figures.values()]
        self.assertEqual(names[0], names[1])
        self.assertEqual(len(names[0]), 52)
        for required in ("hips", "head", "hand.L", "foot.R", "index.03.L", "thumb.01.R"):
            self.assertIn(required, names[0])
        display = {rig.figure: rig.display_name for rig, _ in self.figures.values()}
        self.assertEqual(display, {"body_chan": "Body-chan", "body_kun": "Body-kun"})

    def test_provenance_is_recorded(self):
        for rig, _ in self.figures.values():
            self.assertEqual(rig.source["license"], "CC0")
            self.assertEqual(rig.source["creator"], "vinchau")
            self.assertEqual(len(rig.source["sha256"]), 64)

    def test_figures_stand_on_the_ground_facing_positive_z(self):
        for name, (rig, mesh) in self.figures.items():
            with self.subTest(name):
                low, high = mesh.bounds()
                self.assertAlmostEqual(low[1], 0.0, places=5)
                self.assertTrue(1.5 < high[1] < 1.9, high[1])
                joints = {j.name: j for j in rig.joints}
                self.assertGreater(joints["head"].tail.y, joints["hips"].position.y)
                # Toes point forward (+Z); the left hand is on +X.
                self.assertGreater(joints["toe.L"].tail.z, joints["foot.L"].position.z)
                self.assertGreater(joints["hand.L"].position.x, 0.3)

    def test_rest_rig_is_mirror_symmetric(self):
        for name, (rig, _) in self.figures.items():
            rest = rig.skeleton.transforms(rig.skeleton.rest_pose())
            for index, counterpart in enumerate(rig.skeleton.mirror_indices):
                with self.subTest(figure=name, joint=rig.joints[index].name):
                    self.assertTrue(rest[index].position.is_close(
                        mirror_vector(rest[counterpart].position), 2e-3))

    def test_skinning_is_identity_at_rest_and_moves_the_arm_when_posed(self):
        for name, (rig, mesh) in self.figures.items():
            skeleton = rig.skeleton
            for matrix in skeleton.skinning_matrices(skeleton.rest_pose()):
                self.assertTrue(matrix.is_close(Mat4.identity(), 1e-9))
            upper = skeleton.index("upper_arm.L")
            pose = skeleton.rotate_world(skeleton.rest_pose(), upper,
                                         Quat.from_axis_angle(Z_AXIS, 1.0))
            matrices = skeleton.skinning_matrices(pose)
            hand = skeleton.index("hand.L")
            unaffected = {skeleton.index("hips"), skeleton.index("head")}
            moved = still = 0
            for v in range(mesh.vertex_count):
                if mesh.joints[4 * v] not in unaffected | {hand} or mesh.weights[4 * v] != 255:
                    continue
                p = Vec3(*mesh.positions[3 * v:3 * v + 3])
                q = matrices[mesh.joints[4 * v]].transform_point(p)
                if (q - p).length() > 0.05:
                    moved += 1
                else:
                    still += 1
            with self.subTest(name):
                self.assertGreater(moved, 50)  # Hand vertices follow the raised arm.
                self.assertGreater(still, 50)  # Hip and head vertices stay put.

    def test_ik_reaches_with_each_figure(self):
        for name, (rig, _) in self.figures.items():
            skeleton = rig.skeleton
            rest = skeleton.transforms(skeleton.rest_pose())
            target = rest[skeleton.index("hand.L")].position + Vec3(-0.15, 0.25, 0.2)
            pose, reached = skeleton.solve_ik(skeleton.rest_pose(), "hand.L", target,
                                              pole=Vec3(0.5, 1.2, -1.0))
            with self.subTest(name):
                self.assertTrue(reached)
                self.assertTrue(skeleton.transforms(pose)[skeleton.index("hand.L")].position
                                .is_close(target, 1e-9))

    @unittest.skipUnless(SOURCE.is_file(), "the .blend source is not in this checkout")
    def test_compiler_reproduces_the_shipped_assets(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import compile_assets
        with tempfile.TemporaryDirectory() as output:
            compile_assets.main(["--output", output])
            for path in sorted(FIGURES.iterdir()):
                with self.subTest(path.name):
                    rebuilt = (Path(output) / path.name).read_bytes()
                    self.assertEqual(hashlib.sha256(rebuilt).hexdigest(),
                                     hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
