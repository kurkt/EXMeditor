# EXMeditor

A Blender 3.6 add-on for importing, editing and exporting map and model
data for **Ex Machina** (known outside Russia as *Hard Truck
Apocalypse*), Targem Games, 2005.

EXMeditor is a Blender-based companion to the game's original modding
tools. It gives a more convenient and stable workflow for editing
maps, models, textures and assets — importing a map, changing it in
Blender, exporting it back with everything it did not touch preserved
byte for byte — while staying compatible with M3DEditor for the
workflows it does not yet cover (see [Status](#status)).

EXMeditor is free software: © 2026 Kurkt, released under the GNU
General Public License v3.0 or later (**GPL-3.0-or-later**; see
[LICENSE](LICENSE)).

> This project is an unofficial community tool. It ships no game
> content and is not affiliated with or endorsed by the game's
> developers or publishers. All game names, logos and materials are
> the property of their respective owners. You need your own copy of
> the game.

## What it does

Import from a map folder:

- terrain heightfield and colormap
- `world.xml` node graph, with every node class and attribute accounted for
- `dynamicscene.xml`
- roads and collision
- models resolved through the catalogue chain to their `.gam` files
- textures

Edit in Blender, then export back — writing only what Blender actually
changed, and preserving bytes the add-on does not understand.

### Placing something new

Drag a model from the asset palette (or model your own), then
**Assign Map Object**. The dialog has two layers, and they are
different files:

- **Game Object (dynamicscene.xml)** — a *prototype* instance. The
  prototype is what carries physics, damage, effects: a barrel is
  `Breakable_Barrel1`, a fence `Breakable_WoodFence1`, a turret
  `staticAutoGun04`. When the model is one a prototype draws, the
  dialog defaults here and lists the candidates. A turret's palette
  model is its pillbox (`brick_dot1`, `sack_dot2`); the dialog offers
  the turrets built on it, never the pillbox part itself — a
  `VehiclePart` record puts nothing on the map.
- **Static Node (world.xml)** — a model at a position and nothing
  more: buildings, rocks, decoration. Nothing in the game can hit it.

Position, rotation and (uniform) scale are taken from the Blender
object; the record's name comes from the map's own sequence at export.

The measurement tools this editor was built with (censuses,
forensics, coverage reports) are part of the source repository, not
of the install archive — see *For developers* below.

## Requirements

- Blender **3.6**
- [HTAToolchain](https://github.com/ThePlain/HTAToolchain) by
  ThePlain (Alexander Fateev), MIT licence — a separate third-party
  add-on that performs the actual `.gam` writing. It is **not bundled
  here** and none of its code is copied into this project; install it
  yourself and enable it in Blender's preferences.
- A copy of Ex Machina / Hard Truck Apocalypse

## Installing

1. Download the release archive `EXMeditor-<version>.zip` (or build
   one from the source with `python build_release.py`).
2. Blender → `Edit ▸ Preferences ▸ Add-ons ▸ Install…` → select the zip.
3. Enable **EXMeditor**.
4. In the add-on preferences, set **Game Folder** to the folder that
   *contains* `data` — the game's root, not `data\models`.

The UI lives in the 3D viewport sidebar (`N`) under the **EXMeditor**
tab. If an earlier build was installed under the name *ExMachina SDK*,
remove it first (`Preferences ▸ Add-ons ▸ ExMachina SDK ▸ Remove`):
both register the same operators, and two copies cannot coexist.

## Status

Under active reverse engineering. Import is solid; export writes what
was edited and preserves the rest. What EXMeditor does not do yet, and
M3DEditor still does: create new roads, follow a vehicle's
`ParentPrototype` part chain, show a placed composite object whole
before re-import. The full list, with the measurements behind it, is in `SDK_STATUS.md` in
the source repository; what is planned, in `ROADMAP.md`.

## Support the project

EXMeditor is free and open-source.

If you find the project useful and would like to support its further development, you can do so on Boosty:

**https://boosty.to/kurkts**

Any support helps me spend more time developing EXMeditor, researching the game's formats, and adding new features.

Thank you to everyone who uses the project, reports issues, and helps it grow.

## Licence

Copyright © 2026 Kurkt. EXMeditor is distributed under the
**GNU General Public License, version 3 or (at your option) any later
version** — `GPL-3.0-or-later`. The full text is in [LICENSE](LICENSE);
every source file carries an SPDX header saying the same. Blender
requires published add-ons to carry a GPL-compatible licence; Blender
itself is GPL-2.0-or-later, with which GPL-3.0-or-later combines.

The add-on bundles no third-party code and imports only Python's
standard library and Blender's API; what it runs on, and the licence
of each, is in [THIRD_PARTY.md](THIRD_PARTY.md). What goes into the
install archive, and what must never, is in `CONTRIBUTING.md` in the
source repository.

## For developers

Everything below refers to the **source repository**. The install
archive carries only the add-on, this README, `CHANGELOG.md`,
`THIRD_PARTY.md` and `LICENSE`; the files and folders named in the
following sections are not in it.

### Architecture

Three layers, strictly separated. This is the most important convention
in the codebase.

```
core/            pure logic — never imports bpy
formats/exm/     file format readers and writers — never imports bpy
blender_io/      bridges between core and Blender data
addon/           operators, panels, preferences
addon/research/  measurement operators — source repository only
reverse/         developer scripts, not part of the shipped add-on
tests/           one test module per source module — not shipped
build_release.py the install archive: what ships, what stays, what is refused
```

A new feature is normally three pieces: logic in `core/`, a bridge in
`blender_io/` if it touches the scene, and an operator plus a panel
button in `addon/`.

### Research tools

The measurement tools this editor was built with. They describe the
game's files rather than edit a map, and every report goes to the
system console. **They are in the source repository only** — the
package `addon/research/` and the `core`/`blender_io` modules it
uses are left out of the install archive, and the add-on registers
them only when the package is present. Running from a source checkout
(the repository folder linked or copied into Blender's add-ons
directory) adds the **Show Research Tools** preference and, with it
on, the *Research* sub-panel. Standalone CLI scripts were tried and
rejected: everything runs from one panel.

| Tool | Answers |
|---|---|
| Scene Geometry Audit | which meshes look wrong |
| Model Resolution Diagnostics | a ten-stage chain per node, with `Focus On` |
| Map Coverage Report | expected versus imported instances |
| world.xml Census | node classes, attribute coverage, node id counter |
| dynamicscene Census | prototypes and how many resolve |
| Vertex Format Census | vertex layouts, shaders and texture slots across a folder of `.gam` files |
| Mesh Inspector | the real state of one imported mesh: faces, loops, UVs, colour layers |
| Model Forensics | `.gam` chunk table, undecoded header bytes, decoded materials; A/B compare; folder survey; comparison against a population of known-good models |
| Registration Audit | searches every XML for two model ids and reports which files name a working model and not yours |

Most operators take their inputs through Blender's *Adjust Last
Operation* panel (`F9`), and print full reports to the system console
(`Window ▸ Toggle System Console`).

### Development

```bash
python run_tests.py            # 1102 tests, no pytest, no Blender
python run_tests.py dynamic    # only modules whose name contains this
python build_release.py --out dist
```

`tests/fake_bpy.py` stands in for Blender, so `core/` and `formats/` are
testable without it. When a bug slips through a passing test, the fake
is usually the reason — extend it to reproduce the real behaviour
before fixing the code.

A few tests check the code against real game files, which are never in
this repository; they skip unless you point `EXM_CORPUS` at a folder
holding them, or `EXM_GAME_ROOT` at an installed copy of the game (see
`tests/corpus.py`).

#### Working principles

These are not style preferences. Each was learned the expensive way.

1. **Never rewrite existing game data.** Preserve unknown bytes. Only
   modify what Blender explicitly edits.
2. **Never debug by guessing. Always extend diagnostics first.** Prove
   a hypothesis with a tool before acting on it.
3. **Distinguish measured from assumed.** The docs mark findings as
   MEASURED or UNCONFIRMED, and that distinction has repeatedly been
   the difference between a day and a week.
4. **Never commit game data.** See `.gitignore`. Describe measurements
   in the docs instead of committing the files they came from.

#### How it was built

The formats here were worked out by measurement: reading shipped
files, driving the original editor and diffing what it wrote, counting
what the game actually contains. The documentation marks each finding
as confirmed, hypothesis, unknown or refuted, and the suite holds the
code to it.

### Documentation

The install archive carries this README, `CHANGELOG.md`,
`THIRD_PARTY.md` and `LICENSE`. The rest is in the source repository:

- `TECHNICAL_CONTEXT.md` — full technical context: the `.gam` format as
  currently decoded, the editor's own load sequence read from its logs,
  the registration chain, what has been ruled out and what has not.
  Start here.
- `Editor_Experiment_Findings.md` — results of driving the original
  editor by hand and diffing what it wrote.
- `SDK_STATUS.md` — feature status; `ROADMAP.md` — what is next;
  `ARCHITECTURE.md` — how the packages fit; `CONTRIBUTING.md` — how
  to work on it and how a release is built.
