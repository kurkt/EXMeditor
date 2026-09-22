# EXMeditor — Architecture

Version 0.15.1 · 101 modules · 21 600 lines · 432 tests

A Blender add-on for editing Ex Machina (Hard Truck Apocalypse) maps.
This document describes how it is built and, more importantly, why —
several decisions here look arbitrary until you know which failure
produced them.

---

## 1. Layers

The single most valuable structural property: **`core`, `formats` and
`utils` never import `bpy`**. Verified mechanically, not by convention.

```
addon/           Blender operators, panels, preferences     (imports bpy)
addon/research/  measurement operators — repository only    (imports bpy)
blender_io/      domain model <-> Blender datablocks        (imports bpy)
formats/         file codecs, one per format                (no bpy)
core/            domain model, geometry, coordinates        (no bpy)
utils/           maths, binary IO, errors, logging          (no bpy)
```

A second boundary, orthogonal to the layers: **the install archive is
a subset of the repository.** `build_release.py` leaves out
`addon/research/` and the ten `core`/`blender_io` modules only it
imports (`RESEARCH_ONLY_MODULES`), plus tests, `reverse/` and the
developer documentation; the add-on's `__init__` registers the
research package only when it is present. `tests/test_release_archive.py`
proves the archive is closed under import and registers without the
research tools.

Every format bug found in this project was caught by a plain-Python
test with no Blender running. That is the return on the discipline, and
it is why the rule is worth keeping even when a shortcut would be
convenient.

Adding a format touches exactly four places: `core/<x>.py`,
`formats/exm/<x>.py`, `blender_io/<x>_bridge.py`, and registration in
the plugin.

---

## 2. Coordinates — one source of truth

`core/coordinates.py` is the only module that converts between game and
Blender space. Nothing else may do it.

This rule exists because it was once broken. Terrain and objects each
did their own conversion, both looked correct in isolation, and nothing
compared them — the result was terrain on one plane and every object on
another. Two separate bugs were tangled together:

* **Axis convention.** The game is Y-up, Blender is Z-up. Terrain put
  height on Z; objects put it on Y.
* **Cell size.** A placeholder of 1.0 meant terrain covered an eighth
  of the map's real footprint.

```
game (X, Y, Z)  ->  blender (X, Z, Y)
```

**Rotations need the same treatment, and this is easy to miss.**
Positions were converted while quaternions were passed through
untouched, so a turn about the game's vertical axis became a turn about
a horizontal one. Invisible while objects were Empties; obvious the
moment real geometry appeared. The correct mapping is
`(w, -x, -z, -y)` — found by exhaustive search against a reference,
after a derivation on paper produced a formula that was right for one
axis and wrong for the other two.

An exact rotation conversion exists **only when `xy_scale` equals
`height_scale`**. Under a non-uniform scale, a rotation is not a
rotation any more and no quaternion can express it. The importer warns
rather than silently shearing geometry.

### Calibration

`TERRAIN_CELL_SIZE = 8.0`, confirmed by three independent
measurements. `DEFAULT_XY_SCALE = 1.25`, `DEFAULT_HEIGHT_SCALE = 1.5`.

The invariant that matters is not the scale number but the **spacing
per grid cell**:

```
TERRAIN_CELL_SIZE * DEFAULT_XY_SCALE == 10.0
```

XY was originally calibrated by eye as 10.0 while cell size was still a
placeholder 1.0. Correcting cell size to 8.0 without adjusting XY made
the terrain eight times wider with unchanged heights — a visibly flat
map. A test now pins the invariant so the pair cannot drift apart
again.

### Origin offset

Maps are ~4000 units across, so importing one places its centre
thousands of units from Blender's origin. The transform carries an
optional offset that centres it, applied to **points but never to
deltas** — a nested child's position is an offset from its parent, and
shifting it too would displace every child by the centring amount.

---

## 3. Two placement layers

A map places objects in **two** files, which took a long investigation
to establish because every report honestly said "100% imported" while
meaning "100% of `world.xml`".

```
world.xml          1734 nodes, scene graph, identified by model id
dynamicscene.xml   4756 objects, identified by Prototype name
```

The second layer resolves through the game's **global** data, which no
map manifest references:

```
dynamicscene.xml   Prototype="Breakable_WoodFence1"
      -> data/gamedata/gameobjects/breakableobjects.xml   ModelFile="wood_fence1"
      -> data/models/AnimModels.xml                       file="...wood_fence1.gam"
      -> the .gam itself
```

57 of 67 prototypes resolve this way. The remaining 10 are gameplay
entities (`genericLocation`, `settlementTeam`) with no geometry by
design.

The two layers are kept as separate types. They share nothing but the
idea of a position: different attribute spellings (`org`/`rot` versus
`Pos`/`Rot`), different identification, different export files. Merging
them would produce a model where half the fields are meaningless for
any given object.

---

## 4. Lossless round-trips

Three rules, each learned from a specific failure.

**Absent means unchanged, not unchangeable.** 522 of 1734 nodes have no
`scale` attribute. Treating "absent in the source" as "never write"
silently discarded users' edits — cloning an object worked, stretching
it did nothing. Attributes are now written when they differ from the
identity, even if the source lacked them.

**Unmodelled data is preserved verbatim.** Anything the SDK doesn't
interpret goes into `raw_attrs` and comes back out untouched. This is
what lets an export be safe despite the SDK understanding only part of
each format.

**The snapshot strategy.** Export copies the entire source folder, then
overwrites only what the SDK regenerates. Some 25 files it cannot parse
— grass, tiles, lightmaps, scripts — survive byte-identically because
they are copied rather than rebuilt. `verify_preserved()` checks this
afterwards.

**Export scans the whole collection tree.** Objects are found by their
`exm_class` property, wherever they sit. An earlier version walked only
the `Objects` sub-collection created at import — which silently lost
every object the user created, because Blender links a new mesh into
the *active* collection. Children of assigned nodes are skipped during
the walk and reached through their parent instead, so a nested node is
not exported twice.

**Node names are not Blender names.** Blender renames duplicates and
users rename objects, so the real node name lives in a custom property.
Viewport names are readable labels (`4762_house1`); export always uses
the stored name, because other map files and scripts reference nodes by
name.

---

## 5. Diagnostics

Four tools, built because guessing at causes was costing more than
measuring them.

| Tool | Question it answers |
|---|---|
| Model Resolution Diagnostics | why is *this* model missing — which stage of the chain broke |
| Scene Geometry Audit | does what loaded make sense — sizes, orientation, degenerate meshes |
| Map Coverage Report | does the scene account for the whole map, per model id |
| World Census | what is in the file, including what the SDK skips |

The census is the one that matters most structurally. Every other
report counts parsed results and is therefore **incapable of mentioning
what it skipped** — a node class the parser rejects never reaches the
object model. The census reads the XML directly, deliberately not
sharing the parser, because the parser's decisions are exactly what
needs auditing.

Two principles run through all four:

* **Diagnostics never fix anything.** A tool that quietly compensates
  for a problem cannot describe it.
* **False positives are worse than silence.** A triage tool that flags
  healthy assets trains you to ignore it. Every anomaly threshold is
  paired with a test asserting that real, known-good models stay clean.

---

## 6. Unknown data is skipped, not fatal

An unrecognised node class was once a hard error, on the reasoning that
guessing its meaning risked writing something wrong. That was the wrong
half of the trade: aborting the read discarded 1500 valid objects
alongside the one unknown node, turning a working map into "0 models
loaded".

Unknown nodes are now skipped and **recorded** — attribute names, value
samples, hierarchy position, references — so a new class becomes a
dataset rather than a failure. Support gets added from evidence, not
from a plausible guess.

The same reasoning narrowed the export validator. It once rejected
duplicate node names; a real shipped map contains 84 nodes called
`noise` and loads fine. Refusing to write back a map the game itself
accepts is a worse failure than the one being guarded against.

---

## 7. Asset library

`core/assets.py` exists because three features depend on it: browsing
models to place, replacing a model, and validating that an id exists.
Building it separately in each would have produced three partial
catalogues to reconcile later.

Categories are derived from the game's own directory structure rather
than a fixed list, so a mod that adds folders is categorised sensibly.
Search orders by usage count, not alphabetically — on a real map,
"stone" returns dozens of hits and the one placed 132 times is likelier
to be wanted than the one placed once.

Geometry is measured **on demand**. A full game has over a thousand
models; parsing them all to open a browser would be slow for a benefit
most users never need. An unmeasured asset has `geometry is None`,
which is deliberately distinct from zero — a consumer must be able to
tell "not read yet" from "has no vertices".

---

## 8. The `.gam` model format

Container (`ecbnt,t`) fully solved and shared across six file types —
the extension does not predict the format. Vertex layouts are
**detected from the data**, not declared: the header's component-count
field takes values (7, 9, 15) that describe no field count.

Detection is safe because every mesh chunk stores its own bounding box.
If the positions read reproduce it, the layout is right; if not, the
mesh is refused rather than imported as garbage. That check is what
makes handling unknown layouts sound rather than reckless.

Three layouts confirmed: 32 bytes (position, normal, uv), 44 (adds
colour and a second uv), 48 (adds a tangent). A chunk may hold many
meshes — a vehicle cab holds 28 — and some meshes carry two vertex
buffers, so the index buffer is located by validation rather than by a
fixed offset.

**Export of `.gam` is not implemented.** The container is understood,
but the material, shadow and tool-metadata chunks are not understood
well enough to generate. Import is one-way; moving, rotating and
deleting objects round-trips fully.

---

## 9. Testing

427 tests, all runnable without Blender via `tests/fake_bpy.py`.

The stub is held to one standard: **it must fail where Blender would
fail**. This was learned expensively. A missing `bpy.props` declaration
once passed 232 tests because the stub didn't apply property
annotations and the tests set the property by hand — the test was
creating the very attribute whose absence it should have caught. The
stub now evaluates annotations the way registration does.

Tests are named for the behaviour they protect, and regression tests
carry a comment explaining the failure that produced them. That comment
is the difference between a test someone deletes as redundant and one
they leave alone.
