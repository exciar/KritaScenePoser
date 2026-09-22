# Using your own figures in KSP

KSP ships with Body-chan and Body-kun. This guide explains how to bring in your own rigged models, and how to prepare them so they import cleanly.

Importing works from version 0.0.8. Open the KSP docker, go to the **Scene** tab, and click **Import Figure…**.

`.blend` files saved by Blender 5 use a newer layout. KSP reads it, but it has not been tested against a file Blender 5 actually wrote. If a Blender 5 file gives you trouble, export `.glb` instead, and consider opening an issue with the file attached so the reader can be checked against it.

## What you can import

| Format | Where it comes from | Notes |
| --- | --- | --- |
| `.glb` (glTF 2.0 binary) | Blender (**File → Export → glTF 2.0**), VRoid Studio, many other 3D tools | The most dependable choice. A VRoid `.vrm` file is a `.glb` with extra data, so it can be imported too. |
| `.blend` | Blender, saved directly | It must be saved uncompressed; see below. |

FBX files, including Mixamo downloads, are not supported directly. Open them in Blender and export a `.glb`.

### What happens on import

- It recognizes Rigify, Mixamo, VRM and Blender metarig bone names, and reads a VRM file's own humanoid table when it has one.
- It converts axes, so a Blender figure (Z up, facing −Y) arrives upright and facing the camera.
- It checks which way the toes point and turns the figure around if it faces away.
- It keeps the size the model was authored at, unless that size is not believable (under 0.3 m or over 4 m), in which case it scales the figure to 1.75 m and tells you.
- It stands the figure on the ground.
- It works out normals when a file has none, keeping hard edges sharp.
- It reports how many joints it matched, which ones it could not, and how many vertices had no weights.

Both formats go through the same converter. It produces the same `ksp-rig` and `KSPMESH` files as the built-in figures, so imported figures work with everything else in KSP: posing, IK, mirroring, line art, and canvas posing.

Import runs entirely inside Krita, in pure Python. Blender is not needed to import, and nothing is uploaded anywhere.

## Preparing a model

This checklist applies to both formats.

1. One armature, weighted meshes.
   - Every mesh that should move must be deformed by that armature: an Armature modifier plus vertex groups named after its bones, the usual Blender setup.
   - Unweighted vertices stay behind when you pose, and the importer reports how many there are.
2. Apply transforms. Select the armature and the meshes, then **Object → Apply → All Transforms** (Ctrl+A). Scale should read 1.0 everywhere.
3. Real size, standing on the floor.
   - Model in meters. The built-in figures are 1.64 m and 1.75 m tall.
   - The feet should rest at height 0, centered at the origin.
   - The figure should face Blender's front view (−Y). KSP converts axes itself.
4. Rest pose: T-pose or A-pose. KSP's rest pose is whatever the armature's rest position is. Reset Pose returns there.
5. Bake or remove generating modifiers.
   - The importer reads the base mesh and its weights; it does not evaluate modifiers.
   - For `.blend` files, apply Mirror, Subdivision, Solidify, and similar modifiers, or remove them. Keep only the Armature modifier.
   - For `.glb` files, the exporter's **Apply Modifiers** option does this for you.
6. Name sides consistently. Use `.L`/`.R`, `_L`/`_R`, or `Left`/`Right`, so KSP can mirror poses and limbs.
7. Separate parts are optional. Each mesh object becomes a part. Line art draws seams where parts meet, which is how the mannequin gets its segment lines.
   - A one-piece model works too; it relies on outlines, contours, and creases instead.
   - The limit is 254 parts.
8. Keep it reasonably light.
   - Picking a body part runs in Python. The built-in figures have about 25,000–29,000 vertices; much denser meshes make clicking slower. Consider a Decimate pass for very heavy sculpts.
   - Up to 4 bone influences per vertex are kept. Extra influences are dropped and the rest renormalized.
9. Materials and textures are ignored. KSP draws a neutral mannequin shader and line art.
10. Use a model you have the rights to. Imported figures stay on your computer. They are never bundled with KSP or sent anywhere.

### Saving a `.blend` for KSP

- Save with compression off: in **File → Save As…**, open the options (⚙) and clear **Compress**.
- Krita's bundled Python cannot unpack Blender's zstd compression. A compressed file will be rejected with a message saying so.
- KSP reads the classic `.blend` container (Blender 2.8 through 4.x) and the newer one Blender 5 writes (`BLENDER17-01v0500`, with 64-bit block sizes). The newer container is read but has not yet met a file Blender 5 actually saved, so export `.glb` if it gives you trouble.
- Meshes saved in Blender's newer attribute layout are read by layer name. If KSP cannot find what it needs, it says so and asks for a `.glb`.
- Keep only one figure's armature in the file. If there are several, KSP uses the one that deforms the most meshes.

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
- Center: hips, waist, torso, chest, neck, head.
- Each side: shoulder, upper arm, forearm, hand, three segments for each of five fingers, thigh, shin, foot, toe.

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

Only `hips` is required. Everything that is mapped can be posed with Drag and Rings, but some features need specific joints:

| Feature | Needs |
| --- | --- |
| Hand IK (dragging a hand places it) | upper_arm, forearm, and hand on that side |
| Foot IK | thigh, shin, and foot on that side |
| Mirror Pose / Mirror Limb | both sides of a joint |
| Finger posing | the finger's segments |

A model without fingers, for example, poses normally; its hands just can't curl.

## Where imported figures live

Imported figures are saved as `krita_scene_poser/figures/<name>.rig.json` and `<name>.mesh` in Krita's application-data folder, next to KSP's diagnostic log. They appear in the figure list next to Body-chan and Body-kun, they survive KSP updates, and deleting those two files removes a figure.

KSP converts your model once, on import. The original file is never read again, and never modified.

## When an import fails

| Message | Cause | Fix |
| --- | --- | --- |
| The file is compressed | A `.blend` saved with Compress on | Save As with Compress off |
| No armature found | Meshes are not parented to or deformed by an armature | Add an Armature modifier, or export with skinning |
| N vertices have no weights | Parts that are not weight-painted | Weight-paint them, or parent the part to a bone with automatic weights |
| hips is not mapped | Unrecognized bone names | Add a `.ksp-map.json` file |
| Compressed mesh data | A `.glb` exported with Draco or meshopt | Export again with compression off |
| Data in a separate file | A `.gltf` with a `.bin` beside it | Export as glTF **Binary** (`.glb`) |
| This model has N vertices | Over 150,000 vertices | Decimate the mesh in Blender |
| Faces in a layout KSP cannot read | A newer Blender mesh layout | Export `.glb` instead |
