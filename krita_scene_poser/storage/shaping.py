"""Build a reshaped figure as ordinary RigData and MeshData (see core.shape)."""

from array import array

from ..core.math3d import Vec3
from ..core.shape import BodyShape, deform, ground_offset, joint_matrices
from .mesh_io import MeshData
from .rig_io import RigData, RigJoint, build_skeleton


def shaped_figure(rig, mesh, shape):
    """``(rig, mesh)`` for ``shape``. The default shape returns the originals."""
    shape = (shape or BodyShape()).validated()
    if shape.is_default():
        return rig, mesh
    skeleton = rig.skeleton
    matrices, positions, normal_matrices = joint_matrices(skeleton, shape)
    new_positions, new_normals = deform(mesh.positions, mesh.normals, mesh.joints,
                                        mesh.weights, matrices, normal_matrices)
    lift = ground_offset(new_positions)
    for index in range(1, len(new_positions), 3):
        new_positions[index] += lift

    joints = []
    for index, joint in enumerate(rig.joints):
        matrix = matrices[index]
        place = positions[index]
        joints.append(RigJoint(
            name=joint.name,
            parent=joint.parent,
            position=Vec3(place[0], place[1] + lift, place[2]),
            rotation=joint.rotation,  # Shaping scales; it never turns a bone.
            tail=_moved(matrix, joint.tail, lift)))
    shaped_rig = RigData(figure=rig.figure, display_name=rig.display_name,
                         joints=tuple(joints), source=dict(rig.source),
                         skeleton=build_skeleton(joints))
    shaped_mesh = MeshData(positions=array("f", new_positions), normals=array("f", new_normals),
                           joints=mesh.joints, weights=mesh.weights, parts=mesh.parts,
                           indices=mesh.indices, part_names=mesh.part_names,
                           joint_count=mesh.joint_count)
    return shaped_rig, shaped_mesh


def figure_key(figure_id, shape):
    shape = (shape or BodyShape()).validated()
    return figure_id if shape.is_default() else "{}#{}".format(figure_id, shape.to_json())


def _moved(matrix, point, lift):
    return Vec3(
        matrix[0] * point.x + matrix[1] * point.y + matrix[2] * point.z + matrix[3],
        matrix[4] * point.x + matrix[5] * point.y + matrix[6] * point.z + matrix[7] + lift,
        matrix[8] * point.x + matrix[9] * point.y + matrix[10] * point.z + matrix[11])
