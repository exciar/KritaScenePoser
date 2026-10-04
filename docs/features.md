# KSP feature list

Version 0.0.10.

## Figures

- Body-chan figure
- Body-kun figure
- 52 joints per figure
- 15 finger joints per hand
- Figure selector
- Pose kept when you change figure
- Real scale (1.635 m, 1.750 m)
- Ground plane grid

## Body shape

- Height
- Head size
- Neck length
- Torso length
- Arm length
- Leg length
- Hand size
- Foot size
- Shoulder width
- Chest size
- Waist size
- Hip width
- Arm thickness
- Leg thickness
- Overall build
- Live slider readout in percent
- Rebuild after the slider settles (about 0.25 s)
- Pose, camera and history kept through a shape change
- Figure stays on the ground
- Reset Shape
- Shape stored in scene files and the restored workspace

## Your own figures

- `.glb` import
- `.vrm` import
- `.blend` import (classic and newer container layouts)
- Rigify bone names recognized
- Blender metarig bone names recognized
- Mixamo bone names recognized
- VRM humanoid names recognized
- VRM humanoid table read from the file
- Custom `<model>.ksp-map.json` bone map
- Import Figure… in the Scene tab
- Automatic axis conversion
- Automatic facing detection and correction
- Automatic size check, with scaling to human height when needed
- Automatic grounding
- Normals computed when a file has none, with hard edges kept
- Weights reduced to the four strongest per vertex
- Unmapped bones follow their nearest mapped parent
- Partial rigs supported (only hips required)
- Imported figures join the figure list
- Imported figures stored in Krita's application-data folder
- Original file never modified
- Size, vertex, triangle, part and bone caps
- Plain-language messages for compressed, split, or unsupported files

## Posing: drag mode

- Click to select a body part
- Limb drag
- Hand placement with IK
- Foot placement with IK
- Hand and foot angle kept during IK
- Twist drag (Shift)
- Hand and foot rotation drag (Ctrl)
- Whole-figure move (hips drag)
- Whole-figure turn (Shift + hips drag)
- Hint line for the selected joint

## Posing: rings mode

- Mode switch (T)
- Two bend rings
- One twist ring
- Edge-on ring support

## Joint limits

- On/off toggle
- Elbow hinge limit (150°)
- Knee hinge limit (155°)
- Straight-position allowance (5°)
- Cone limits on all other joints
- Twist limits
- Automatic hinge-direction detection per figure
- Limits applied to drags, IK, mirror, presets and files
- At-limit indicator in the hint line
- Hinge-aware IK solver
- Setting kept between sessions

## Camera

- Orbit (right-drag, Alt+drag)
- Pan (middle-drag, Alt+Shift+drag)
- Zoom (wheel)
- Frame figure (F)
- Perspective / orthographic switch (O)

## Edits and history

- Undo (Ctrl+Z)
- Redo (Ctrl+Shift+Z)
- 200-step pose history
- History separate from Krita's undo
- Cancel drag (Esc)
- Reset joint (R)
- Reset pose
- Mirror pose
- Mirror limb

## Canvas

- Show on Canvas
- Pose on Canvas
- Drag and rings posing on the canvas
- Camera orbit, pan and dolly from empty canvas space
- Krita wheel, middle-drag, Space and pop-up palette kept
- Brushes paused during canvas posing
- Docker and canvas always in sync
- Follows Krita zoom, rotation, mirror and pan
- Draft and final preview resolutions (1024 px / 2048 px)
- Automatic switch-off with a reason

## Poses and presets

- T-pose
- Relaxed stance
- Sitting
- Kneeling
- Walking
- Running
- Hands clasped
- Reaching up
- Crouching
- Apply Pose (one undo step)
- Save Pose…
- Load Pose…
- Poses keyed by joint name
- Poses portable between figures
- Report of joints not used
- Limits enforced on load
- Import Pose… from a posed `.glb` or `.vrm`
- Pose in Blender, finish in KSP

## Scenes and workspace

- Save Scene…
- Load Scene…
- Scene keeps pose, camera, view mode, line settings, opacities and output settings
- Automatic workspace save
- Automatic restore when the docker opens
- Fallback to defaults for a damaged file
- Versioned formats (`ksp-pose` v1, `ksp-scene` v1)

## Line art

- Outline lines
- Contour lines
- Crease lines
- Seam lines
- On/off per line type
- Outline width (0.5–20 px)
- Inner width (0.5–20 px)
- Crease angle (5–175°)
- Contour sensitivity (0–100)
- Line color
- Line opacity
- Antialiased lines
- Zoom-stable contours
- View switch: Shaded / Lines / Both
- Canvas preview at true layer line weight
- Automatic switch to Both when you edit a setting
- Settings kept between sessions

## Output

- Create Lineart Layer (`KSP Lineart`)
- Create Guide Layer (`KSP Figure Guide`)
- Document-size output
- Custom-size output (to 4096 px)
- Centered anchor
- Top-left anchor
- Pixels outside the canvas kept
- Quality 1× / 2× / 4×
- Oversize request refused before allocation
- Layer opacity
- Figure opacity
- Update the layer I made last
- Renamed or deleted layer protected
- Transparent background
- Your own layers never modified

## Krita integration

- Docker with four tabs: Pose, Line Art, Output, Scene
- Seven menu actions under Tools → Scripts
- No default keyboard shortcuts
- User-assignable shortcuts
- ANGLE and desktop OpenGL support
- No-document state handled
- Color-space check with conversion hint
- Self-Test
- Copy Diagnostics
- Diagnostic log, off by default, 512 KB with two backups
- No network use
- No third-party dependencies

# Next versions

## Later

- Separate outline, crease, shadow and tone layers
- Layer group output
- Tone controls
- Line art presets
- Hand and finger presets
- Foot lock
- Snap to ground
- Camera presets
- Light direction
- Cast shadows
- Pose browser
- More than one figure per scene
- Simple objects
- Visibility control per object
- Linux support
- Speed measurements
- Supported-version list
