# Using your own figures in KSP

KSP ships with Body-chan and Body-kun. This guide explains how your own rigged models will work, and how to prepare them now so they import cleanly later.

> **Status:** the **Import Figure…** button is planned, not built. This version (0.0.5) cannot load your own models yet. The design is recorded in the design notes. Everything below describes that design, so a model you prepare today will be ready for it.

## What you will be able to import

| Format | Where it comes from | Notes |
| --- | --- | --- |
| **`.glb`** (glTF 2.0 binary) | Blender (**File → Export → glTF 2.0**), VRoid Studio, many other 3D tools | The most dependable choice. A VRoid `.vrm` file is a `.glb` with extra data, so it can be imported too. |
| **`.blend`** | Blender, saved directly | It must be saved **uncompressed**; see below. |

FBX files, including Mixamo downloads, are not supported directly. Open them in Blender and export a `.glb`.

Both formats go through the same converter. It produces the same `ksp-rig` and `KSPMESH` files as the built-in figures, so imported figures work with everything else in KSP: posing, IK, mirroring, line art, and canvas posing.

Import runs entirely inside Krita, in pure Python. Blender is not needed to import, and nothing is uploaded anywhere.

## Preparing a model

This checklist applies to both formats.

1. **One armature, weighted meshes.**
   - Every mesh that should move must be deformed by that armature: an Armature modifier plus vertex groups named after its bones, the usual Blender setup.
   - Unweighted vertices stay behind when you pose, and the importer reports how many there are.
2. **Apply transforms.** Select the armature and the meshes, then **Object → Apply → All Transforms** (Ctrl+A). Scale should read 1.0 everywhere.
3. **Real size, standing on the floor.**
   - Model in meters. The built-in figures are 1.64 m and 1.75 m tall.
   - The feet should rest at height 0, centered at the origin.
   - The figure should face Blender's front view (−Y). KSP converts axes itself.
4. **Rest pose: T-pose or A-pose.** KSP's rest pose is whatever the armature's rest position is. Reset Pose returns there.
5. **Bake or remove generating modifiers.**
   - The importer reads the base mesh and its weights; it does not evaluate modifiers.
   - For `.blend` files, apply Mirror, Subdivision, Solidify, and similar modifiers, or remove them. Keep only the Armature modifier.
   - For `.glb` files, the exporter's **Apply Modifiers** option does this for you.
6. **Name sides consistently.** Use `.L`/`.R`, `_L`/`_R`, or `Left`/`Right`, so KSP can mirror poses and limbs.
7. **Separate parts are optional.** Each mesh object becomes a *part*. Line art draws **seams** where parts meet, which is how the mannequin gets its segment lines.
   - A one-piece model works too; it relies on outlines, contours, and creases instead.
   - The limit is 254 parts.
8. **Keep it reasonably light.**
   - Picking a body part runs in Python. The built-in figures have about 25,000–29,000 vertices; much denser meshes make clicking slower. Consider a Decimate pass for very heavy sculpts.
   - Up to 4 bone influences per vertex are kept. Extra influences are dropped and the rest renormalized.
9. **Materials and textures are ignored.** KSP draws a neutral mannequin shader and line art.
10. **Use a model you have the rights to.** Imported figures stay on your computer. They are never bundled with KSP or sent anywhere.

### Saving a `.blend` for KSP

- Save with **compression off**: in **File → Save As…**, open the options (⚙) and clear **Compress**.
- Krita's bundled Python cannot unpack Blender's zstd compression. A compressed file will be rejected with a message saying so.
- The development reader in this repository handles the classic `.blend` header. Blender 5.x saves use a newer header (`BLENDER17-01v0500`, with 64-bit block sizes). The importer adds support for it, along with the attribute-based mesh storage used by recent Blender versions.
- Keep only one figure's armature in the file, or tell the importer which armature to use.

### Exporting a `.glb` from Blender

In **File → Export → glTF 2.0 (.glb/.gltf)**:
- **Format:** glTF Binary (`.glb`).
- **Include:** Selected Objects. Select the armature and its meshes first.
- **Mesh:** turn on Apply Modifiers.
- **Data → Armature:** turn on Export Deformation Bones Only, which leaves out Rigify's control bones.
- **Animation:** can be off; KSP only needs the rest pose.
- Keep **+Y Up** on (the default).

Compressed meshes (the Draco or meshopt options) are not supported; leave them off.

## How your bones become KSP joints

KSP poses a fixed set of 52 joints:
- **Center:** hips, waist, torso, chest, neck, head.
- **Each side:** shoulder, upper arm, forearm, hand, three segments for each of five fingers, thigh, shin, foot, toe.

Your bones are mapped onto these joints. A deform bone without its own joint is merged into its nearest mapped parent, as the mannequin's twist bones are. Extra bones, such as hair, tails, or skirt bones, therefore follow their parent (usually the head or hips); they cannot be posed on their own yet.

These naming schemes are recognized automatically:

| KSP joint | Rigify (`DEF-`) | Mixamo (`mixamorig:`) | VRM / VRoid humanoid |
| --- | --- | --- | --- |
| hips | `spine`, `pelvis.L/R` | `Hips` | `hips` |
| waist | `spine.001` | `Spine` | `spine` |
| torso | `spine.002` | `Spine1` | `chest` |
| chest | `spine.003` | `Spine2` | `upperChest` |
| neck | `spine.004`, `spine.005` | `Neck` | `neck` |
| head | `spine.006` | `Head` | `head` |
| shoulder.L | `shoulder.L` | `LeftShoulder` | `leftShoulder` |
| upper_arm.L | `upper_arm.L` (+ `.001`) | `LeftArm` | `leftUpperArm` |
| forearm.L | `forearm.L` (+ `.001`) | `LeftForeArm` | `leftLowerArm` |
| hand.L | `hand.L`, `palm.01–04.L` | `LeftHand` | `leftHand` |
| index.01–03.L | `f_index.01–03.L` | `LeftHandIndex1–3` | `leftIndexProximal`, `…Intermediate`, `…Distal` |
| thigh.L | `thigh.L` (+ `.001`) | `LeftUpLeg` | `leftUpperLeg` |
| shin.L | `shin.L` (+ `.001`) | `LeftLeg` | `leftLowerLeg` |
| foot.L | `foot.L` | `LeftFoot` | `leftFoot` |
| toe.L | `toe.L` | `LeftToeBase` | `leftToes` |

The right side mirrors these names. The other fingers follow the index finger's pattern: thumb, middle, ring, and pinky.

### Any other rig: a small map file

If your bones use other names, put a JSON file next to the model with the same name plus `.ksp-map.json`, for example `hero.glb` and `hero.ksp-map.json`. List KSP joints and your bone names:

```json
{
  "hips": "Pelvis",
  "waist": "Spine_01",
  "chest": "Spine_02",
  "neck": "Neck",
  "head": "Head",
  "upper_arm.L": "Arm_Upper_L",
  "forearm.L": ["Arm_Lower_L", "Arm_Twist_L"],
  "hand.L": "Hand_L"
}
```

- A list merges several of your bones into one KSP joint.
- The importer shows which joints were matched and which were not, before anything is saved.

### Partial rigs still work

Only **hips** is required. Everything that is mapped can be posed with Drag and Rings, but some features need specific joints:

| Feature | Needs |
| --- | --- |
| Hand IK (dragging a hand places it) | upper_arm, forearm, and hand on that side |
| Foot IK | thigh, shin, and foot on that side |
| Mirror Pose / Mirror Limb | both sides of a joint |
| Finger posing | the finger's segments |

A model without fingers, for example, poses normally; its hands just can't curl.

## Where imported figures live

Imported figures are saved in KSP's folder inside Krita's resource folder (**Settings → Manage Resources → Open Resource Folder**), as `krita_scene_poser/figures/<name>.rig.json` and `<name>.mesh`. They appear in the figure list next to Body-chan and Body-kun. They survive KSP updates, and deleting those two files removes a figure.

## When an import fails

| Message | Cause | Fix |
| --- | --- | --- |
| The file is compressed | A `.blend` saved with Compress on | Save As with Compress off |
| No armature found | Meshes are not parented to or deformed by an armature | Add an Armature modifier, or export with skinning |
| N vertices have no weights | Parts that are not weight-painted | Weight-paint them, or parent the part to a bone with automatic weights |
| hips is not mapped | Unrecognized bone names | Add a `.ksp-map.json` file |
| Compressed mesh data | A `.glb` exported with Draco or meshopt | Export again with compression off |
