# Asset licenses and provenance

Every model, icon, shader, and preset bundled with KSP, or used to produce a bundled asset, is listed here before it ships. KSP's own source and original assets are licensed GPL-3.0-or-later (see `LICENSE`).

## Source assets (not shipped)

### Body-chan and Body-kun — posable drawing figures

| Field | Record |
| --- | --- |
| Creator | vinchau |
| License | CC0 (public-domain dedication). This is the license field of the creator's [BlendSwap listing](https://blendswap.com/blend/23521), and the project owner confirmed it on 2026-09-18. CC0 requires no attribution; KSP will still credit the creator. |
| Source | BlendSwap #23521, "BodyChan - BodyKun \| Posable Drawing Figures". The same creator also publishes it on [Sketchfab](https://sketchfab.com/3d-models/poseable-drawing-figures-bodychan-bodykun-ed60f70cba9c4f14b837187d2129d8c9). |
| Local file | `bodychan-bodykun.blend` at the repository root: 2,575,937 bytes, SHA-256 `3ff15737528f32255544bf1c7689a084ecfc22c5ade5bc446bfc6bf2c185cdbe` |
| File format | zstd-compressed Blender file saved by Blender 3.4 (`BLENDER-v304`). The BlendSwap listing names Blender 2.8x, so this copy has been re-saved. It contains no embedded license or author text. |
| Contents | Rigify armatures (`metarig`, `rig`, `body_kun_rig`); segmented parts (Head, Neck, Torso, Hips, Thighs, Shins, Feet, Forearms, Hands); Rigify UI text blocks (`rig_ui.py`, `body_kun_rig_ui.py`) |
| Status | Source of both v0.1 figures. The `.blend` itself is not in the plugin ZIP; only the derived files listed below ship. |

Derived files, compiled by `tools/compile_assets.py`:

| Shipped file | Derived from |
| --- | --- |
| `krita_scene_poser/assets/figures/body_chan.rig.json`, `body_chan.mesh` | Armature `rig` and its 25 part meshes (Body-chan) |
| `krita_scene_poser/assets/figures/body_kun.rig.json`, `body_kun.mesh` | Armature `body_kun_rig` and its 12 part meshes (Body-kun) |

Each rig file records the source SHA-256, license, creator, and URL. The plugin manual credits vinchau.

Rules for derived assets:
- Any mesh or rig converted from this file goes in `krita_scene_poser/assets/`. Add it to this document by derived filename and the conversion tool used.
- Add a credit line for vinchau to `manual.html` when the first derived asset ships.
- The Blender text blocks are Blender-only scripts. KSP must never ship or execute them.
- CC0 material may be redistributed inside the GPL-3.0-or-later plugin.
