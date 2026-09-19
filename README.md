# KSP — Krita Scene Poser

KSP is an embedded posing studio for Krita. This is an early development build, version `0.0.4`. Pose Body-chan or Body-kun in the docker, or right on the canvas with **Pose on Canvas** (CSP-style). Drag body parts or rotate joints with rings, then press **Create Layer** to add the posed figure to your document as a transparent shaded guide. Scene and pose files, presets, joint limits, and line art are not implemented yet.

The plugin runs with Krita's bundled Python, PyQt5, and the Python standard library. It does not install packages, contact a server, or run Blender or other external tools. A separate Python installation is only useful for development tests and packaging.

**Compatibility is not yet certified.** Automated checks outside Krita cannot establish that a bundled PyQt build exposes the required GL operations, that a GPU driver works, or that Krita's canvas displays exported pixels correctly. Treat this as an early build, not a supported release.

## Install and enable

1. Build the ZIP below, or use the supplied `dist/ksp-0.0.4.zip`.
2. In Krita choose **Tools → Scripts → Import Python Plugin…** and select the ZIP. Restart Krita.
3. Open **Settings → Configure Krita → Python Plugin Manager**, enable **KSP — Krita Scene Poser**, and restart Krita again.
4. Open **Settings → Dockers → KSP — Krita Scene Poser**.

For a manual install, use **Settings → Manage Resources → Open Resource Folder**. Copy the ZIP's `krita_scene_poser.desktop` file and `krita_scene_poser` directory directly into that resource folder's `pykrita` directory, then enable the plugin and restart. The archive must not be nested inside another enclosing directory. These steps follow [Krita's plugin installation guide](https://docs.krita.org/en/user_manual/python_scripting/install_custom_python_plugin.html).

## Pose a figure

![Posed figures rendered by KSP on the GPU](docs/images/gpu-posed-figures.png)

Choose **Body-chan** or **Body-kun** at the top of the docker; switching keeps the pose. Click a body part to select it; the hint line says what dragging it does.

**Drag mode** (default):
- Drag a limb to swing it toward the cursor.
- Drag a hand or foot to place it; the arm or leg follows (IK), and the hand or foot keeps its angle.
- **Shift**+drag twists a part around its bone. **Ctrl**+drag rotates a hand or foot itself.
- Drag the hips to move the whole figure. **Shift**+drag the hips to turn it.

**Rings mode** (the **Rings** button or **T**): click a part, then drag one of its rings. The red and blue rings bend the joint; the green ring twists it along the bone.

**View:**
- Right-drag or **Alt**+drag orbits. Middle-drag or **Alt+Shift**+drag pans. The mouse wheel zooms.
- **F** frames the figure. **O** switches between perspective and orthographic.

**Edits:**
- **Esc** cancels a drag in progress.
- **Ctrl+Z** and **Ctrl+Shift+Z** undo and redo pose changes while the viewport has focus. This history is separate from Krita's document undo.
- **R** resets the selected joint. The buttons also reset the whole pose, mirror it left to right, or copy the selected limb to the other side.

**Create Layer** renders the posed figure from the current view into a new transparent **KSP Figure Guide** paint layer at the document's size. The vertical framing matches the viewport; the width follows the document's shape.

## Pose on the canvas

- **Show on Canvas** draws the figure on the canvas exactly where **Create Layer** would put it. It follows Krita's zoom, rotation, mirroring, and panning, and your brushes keep working.
- **Pose on Canvas** (also the **Tools → Scripts → KSP: Pose on Canvas** action, which you can give a shortcut) works like CSP's Object tool:
  - Drag the figure on the canvas with the same Drag and Rings modes as the docker.
  - Drag empty space to orbit the 3D camera. **Shift**+drag pans the camera; **Ctrl**+drag moves it closer or farther.
  - The mouse wheel, middle-drag, Space, and the right-click palette still control Krita's canvas.
  - Brushes are paused until you turn Pose on Canvas off. The figure stays visible for drawing over.

The docker viewport and the canvas show the same pose; a change in one updates the other. Canvas posing relies on Krita's internal canvas widget, which is not part of Krita's documented plugin API. If a Krita update changes it, canvas posing turns itself off and explains why, and the docker keeps working. **Copy Diagnostics** includes a `[canvas]` section for reporting problems.

## Documents and diagnostics

Use a disposable test document in **RGB/Alpha, 8-bit integer/channel, sRGB**. The prototype only exports to `RGBA` / `U8` documents with one of these sRGB profiles:
- `sRGB-elle-V2-srgbtrc.icc`, Krita's new-document default;
- `sRGB-elle-V4-srgbtrc.icc`;
- `sRGB built-in`, which Krita assigns to images opened or pasted without an embedded profile.

Other color models and profiles, including linear sRGB, are rejected with a hint to use **Image → Convert Image Color Space**. KSP never converts them itself.

Each export creates a fresh paint layer, and your existing artwork is never the target. New documents have a white background layer; hide it to inspect the guide's transparency. **Self-Test** renders the Phase 0 color triangle offscreen and checks channel order, orientation, and transparency without touching the document.

Press **Copy Diagnostics** to copy the local versions, GPU, and self-test results when reporting a failure. The report contains no file paths or document names, and nothing is sent automatically. The docker lifecycle (resize, hide/show, float/dock, close/reopen, restart) is not verified yet.

KSP works with Krita's default Windows renderer (ANGLE/Direct3D) and with desktop OpenGL; no Krita setting needs changing. On hybrid-GPU laptops, the diagnostics `renderer` line shows which GPU is active. If the docker reports a missing OpenGL capability, keep that diagnostic and stop testing that build. Do not add PyOpenGL, NumPy, or a helper executable to work around it. If the plugin does not appear, confirm it is enabled, restart Krita, and check the importer placed both archive entries in `pykrita`.

## Menu actions and shortcuts

KSP adds four actions under **Tools → Scripts**:

- **KSP: Show Scene Poser** opens and raises the KSP docker.
- **KSP: Create Layer** opens the docker and creates a layer, like the docker button.
- **KSP: Pose on Canvas** switches canvas posing on or off.
- **KSP: Diagnostic Log** turns the diagnostic log on or off.

The actions ship without default shortcuts, so they never clash with Krita's own. To assign keys, open **Settings → Configure Krita → Keyboard Shortcuts** and search for "KSP". If no document is open, **Create Layer** is disabled and its tooltip says why.

## Diagnostic log

The log is off by default. When **KSP: Diagnostic Log** is checked, KSP appends one JSON line per event to `krita_scene_poser/logs/ksp.log` in Krita's resource folder (**Settings → Manage Resources → Open Resource Folder**). Events include viewport initialization, context release, and export start, finish, or failure. The log is capped at 512 KB plus two rotated files.

The log records GPU strings, sizes, and timings. It never records file paths, document names, pixel data, or tracebacks. Nothing is sent anywhere, and the setting is saved with Krita's other settings. Diagnostics show whether the log is on.

## Develop and package

From the repository root with Python 3.10 or newer (on Windows, `py -3` avoids other applications' bundled interpreters on `PATH`):

```console
python -m unittest discover -s tests -v
python tools/package_plugin.py --output dist/ksp-0.0.4.zip
```

These commands require no third-party packages. Tests cover:
- the posing core: vectors, quaternions, matrices, projections, forward kinematics, skinning, two-bone IK, and mirroring;
- the viewport logic: the orbit camera, surface picking (checked against a brute-force test of every triangle), rotation rings, every drag gesture, cancel, and undo/redo;
- the figure shaders' dialects and bone packing (checked against CPU skinning), plus the figure assets and file formats;
- pixel transfer and the premultiplied readback contract;
- the ctypes GL binding, using native callbacks;
- document integration with test doubles;
- settings, the diagnostic log, and diagnostics;
- the plan's architecture rules: `core/` and `storage/` never import Krita or Qt, and only `integration/` edits documents;
- archive boundaries.

They run outside Krita. Actual Krita/OpenGL acceptance is separate. `tools/krita_host_probe.py` runs the renderer and a real paint-layer export inside Krita without the GUI; the compatibility notes explains how to launch it. The packager uses an explicit runtime allowlist, sorted entries, fixed timestamps and permissions, and a bundled copy of `LICENSE`. Rebuilding unchanged inputs with the same Python/zlib toolchain produces the same ZIP bytes. Development files, tests, caches, and logs do not ship.

The renderer and export assumptions are recorded in the design notes. GL functions are resolved through `ctypes` so ANGLE works; see the design notes. Rendering and layer export are verified in the Krita 5.3.3 GUI on Windows. The docker lifecycle checks are the one open Phase 0 item. Both figures, Body-chan and Body-kun, are compiled into versioned rig and mesh files by a pure-Python `.blend` reader (see the design notes and `docs/images/figures-preview.png`). Viewport rendering, picking, and posing are described in the design notes, and canvas posing in the design notes. Next: joint limits, proportions, and saving poses and scenes (Phase 4). The overall roadmap is in the plan.

## Update, disable, or uninstall

To disable KSP, uncheck it in Python Plugin Manager and restart Krita. To update this prototype, close Krita, replace its two entries in `pykrita` with the new version, and restart. To uninstall, disable it, close Krita, and remove only `krita_scene_poser.desktop` and the `krita_scene_poser` directory from `pykrita`. Generated paint layers remain part of their documents.

## License

KSP source and original bundled assets are licensed under **GPL-3.0-or-later**. See [LICENSE](LICENSE). The bundled Body-chan and Body-kun figures are derived from vinchau's CC0 "BodyChan - BodyKun" drawing figures. Provenance and the derived files are listed in [docs/asset-licenses.md](docs/asset-licenses.md). No proprietary assets are bundled.
