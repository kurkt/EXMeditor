# EXMeditor — Technical Context

Written for anyone joining this project cold. Everything below is
either **MEASURED** (observed in a real file, log or run) or flagged
**UNCONFIRMED**. That distinction is the single most important
convention in this document — the project has lost many days to
plausible reasoning that turned out to be wrong, and the entries marked
UNCONFIRMED are exactly where that happened.

---

## 1. What this project is

A Blender 3.6 add-on and SDK for **Ex Machina / Hard Truck Apocalypse**
(Targem, 2005). The goal is not another map editor: it is a full SDK to
import, edit and export game data with minimal information loss, and in
time to replace the original **M3DEditor** entirely and automate the
creation of new content — maps, models, textures, assets.

Scale: ~190 Python modules and just under 1 100 tests.

### Architecture — three layers, strictly

| Layer | Rule |
|---|---|
| `core/` | Pure logic. **No `bpy` import, ever.** |
| `blender_io/` | Bridges between `core` and Blender data |
| `addon/` | Operators, panels, preferences |
| `formats/exm/` | File format readers/writers (also bpy-free) |
| `reverse/` | Developer scripts, not shipped in the add-on |

A new diagnostic belongs in `core/` + an operator in `addon/` + a button
in the existing Diagnostics panel. **Not** a standalone CLI script —
that was tried and rejected: everything lives in one panel so a full
analysis can be run in one place.

### Non-negotiable working principles

1. **Never rewrite existing game data.** Preserve unknown bytes. Only
   modify what Blender explicitly edits.
2. **Never debug by guessing. Always extend diagnostics first.** Every
   new hypothesis is proven with a tool before being acted on.
3. Every module has a matching test file. Tests use `tests/fake_bpy.py`.

### The project's own hardest-won lesson

The biggest misconception across the whole reverse-engineering effort
was assuming missing objects were caused by the wrong parser. That was
false every single time:

- `commonservers.xml` contains no model catalogue
- `model_names.xml` is only localisation
- `servers.xml` is a per-map model list, not the master catalogue
- Missing cities were NOT caused by `SgGameUnitNode`
- Missing breakable objects were NOT caused by `world.xml`

Every assumption was eventually replaced by measurement.

---

## 2. How to work in this repo

```bash
# From the repository root. No pytest, no Blender: tests/fake_bpy.py
# stands in for it.
python run_tests.py
```

**Packaging.** `python build_release.py --out dist` builds the install archive from the tree and refuses to include a game file. Fixing a file in one place and packaging another cost this project hours twice; building from the repository is what prevents it.

### Test status

The suite passes completely (1071 tests, 0.52.0). Two long-standing
failures were resolved in 0.52.0: `MINIMUM_VISIBLE_EXTENT` now exists
(a size warning in `verify_written_model`, threshold 4.0 from a
census of 1336 shipped models) and `resolve_in_folder` returns the
on-disk spelling of a file even on Windows.

---

## 3. The `.gam` model format — MEASURED

Container:

```
char[7]   magic
uint8     subtype
uint32    chunk_count
per chunk, 16 bytes:  uint32 id, uint32 size, uint32 offset, uint32 reserved
```

### Chunk ids observed

| id | Meaning | Notes |
|---|---|---|
| 1 | header | 16 bytes |
| 2 | node hierarchy | node names; indexed by mesh header `+44` |
| 4 | mesh | geometry |
| 8 | animation | zero-length in every model measured |
| 15 | skin | materials + textures — **decoded, see below** |
| 16, 32 | UNCONFIRMED | present in `civilhouse1`, absent from all HTAToolchain output |
| 128, 256 | UNCONFIRMED | present in `big_flag01` (a `windwavy` flag model) |
| 240 | collection name | e.g. `'Main'`, `'Collection'` |
| 61441–61444 | HTATools metadata | version stamp, signature. **Absent from shipped models** |

Ids are mostly powers of two — plausibly an optional-block bitmask, but
that is UNCONFIRMED.

### Mesh header — 72 bytes

| Offset | Field | Status |
|---|---|---|
| 0 | name, 16 bytes | MEASURED |
| 16–39 | unknown | undecoded |
| 40 | constant `4` in every sample | UNCONFIRMED meaning |
| 44 | **index into the chunk-2 node table** | MEASURED |
| 56 | stride | MEASURED |
| 60 | component count | MEASURED |
| 64 | vertex count | MEASURED |
| 68 | triangle count | MEASURED |

`+44` was proven on `MSCV NOD`: its chunk 2 lists `Camera`(0),
`Light`(1), `tripo_part_0`(2)… and mesh[0] holds 2, mesh[1] holds 3, and
so on. On `big_flag01` the values run 1, 2, 3, 4, **6** — the gap is a
node that carries no geometry, which confirms it indexes nodes and not
meshes.

### Vertex formats — MEASURED on shipped models

| components / stride | Used by | Shader |
|---|---|---|
| 8 / 36 | `civilhouse1.gam` (shipped) | `diffuse_vc`, `specular_vc` |
| 15 / 48 | `big_flag01.gam` (shipped), `MSCV NOD` | `bump` |
| 9 / 44 | **nothing measured** | SDK forced this for a long time |

**The format follows the shader.** `bump` needs tangents → 15/48.
`diffuse_vc` runs on vertex colour → 8/36. There is no single "correct"
format; forcing one was a category error that cost several iterations.

HTAToolchain always writes shader `bumpdiffuse_envalphagloss_spec`,
which demands bump, environment, gloss and specular maps — and writes
no textures at all. That is an unsatisfiable request by construction.

### Skin chunk (id 15) — DECODED

```
uint32                     leading value — NOT a record count (see below)
per material, 172 bytes + 48 * texture_count:
    float[17]              D3DMATERIAL9: diffuse, ambient, specular,
                           emissive as RGBA, then power
    uint32                 texture_count
    char[100]              shader name
    per texture, 48 bytes:
        char[44]           filename
        uint32             slot
```

Derived from record sizes: `teeth_top.gam` declares two materials in a
348-byte chunk (4 + 2×172); `civilhouse1.gam` holds three materials with
one texture each in 664 bytes (4 + 3×220). Confirmed byte-exact on five
files.

**The leading uint32 is not the record count.** `civilhouse1` holds `1`
there and carries three material records. Reading it as a count found
only the first material — one texture instead of three. Records must be
walked to the end of the chunk.

**`slot` is the field that matters.** Filenames alone cannot say which
image is the diffuse map and which is the bump map, so without it they
can only be assigned in the order they appear. This is the mechanism
behind "textures land in the wrong places", and it bites in both
directions — import and export.

The bytes between the shader name and the texture records contain what
look like leftover runtime pointers (`0x004015b2`, `0x0253cb00`) —
uninitialised tail of a fixed buffer in the original exporter.

### Model size — MEASURED

Shipped models are small: `big_flag01` is 2.1 units across,
`civilhouse1` 13.7, `MSCV NOD` 14.2. Generated cubes have come out at
40, 375 and 2015 units. A model whose geometry carries its world
position shows a large bounding-box centre offset while its size stays
ordinary — `teeth_top` sits 1008 units from the origin.

---

## 4. M3DEditor architecture — READ FROM ITS OWN LOG

This was the single highest-value source in the project and it was
consulted far too late. `m3deditor.log` names what the editor opens.

Build: `ExMachina - release version release build v1.03 (Feb 10 2006)`.
Codepage **1251**. Renderer `dxrender9.dll`.

### What the editor loads, verbatim

```
DataServer.cpp  Loading Servers: data\maps\r1m1\servers.xml
DataServer.cpp  Loading Servers: data\models\commonservers.xml
```

**Two files. `data\models\servers.xml` is not one of them.** The model
catalogue for a map is the `servers.xml` **inside that map's folder**.

This invalidates a whole line of investigation: a registration audit
found a genuine gap in `data\models\servers.xml`, entries were written
there, and nothing changed — because nothing reads it.

### Load sequence

```
Quest.cpp        local quests (falls back to global)
world.cpp        ----------------------- World Loading
landscape.cpp    Landscape::Load()  -> colormap.raw, displace.bin,
                                       normal map, tiles, shoreline
DataServer.cpp   Loading Servers (the two files above)
world.cpp        LoadWorld: read/parse xml -> read from xml -> link nodes
world.cpp        Prefabs, Roads
server.cpp       AI: Relationship, SoilProps
```

A map folder therefore needs **binary terrain files** — `colormap.raw`
and `displace.bin` — not just XML. "Save as new map" in the editor
produced a folder without them, and the editor crashed after
`Landscape::Load()` failed and it continued into AI loading.

### Texture loading is broken in this data set generally

```
DraftModel.cpp  Error: Couldn't load texture
                data\models\vehicles\small_car\color1.dds for model cab01.gam
```

Hundreds of these, for **shipped** vehicle models. The "textures are a
mess / a random one is used" complaint is not caused by the SDK or by
model transfer — it is the state of this install. Worth separating from
any SDK-side texture question.

---

## 5. Registration chain — what is actually required

MEASURED, by comparing a model the editor offers against one it does
not, across every XML in the game folder:

- `data\models\animmodels.xml` — the master catalogue. `civilhouse1`,
  a model the editor displays, has **exactly one** mention in the whole
  game and it is here.
- `data\maps\<map>\servers.xml` — the per-map list the editor loads.
- `data\models\commonservers.xml` — loaded, but indexes non-model
  catalogue files; contains no model entries.

`servers.xml` entry shape, as `register_in_servers` writes it:

```xml
		<Item
		id="Cube9"
		file="data\models\animmodels.xml" />
```

inserted before `</AnimatedModelsServer>`.

### world.xml node — CONFIRMED IDENTICAL

A node the editor wrote itself, beside one the SDK wrote:

```xml
<Node name="Object76476811" class="SgAnimatedModelNode"
      org="2610.620 0.000 1607.786" orgRel="1" id="NODB" ndmAction="0" />
```

Same class, same attribute set, same nesting depth (1, directly under
`<World>`, like all 1527 model nodes). `scale` and `rot` are optional —
the editor omits them itself.

`LastId` on the working map is `76476763`, and the highest `ObjectN`
actually in use is also `76476763`. The counter is healthy; the editor
continues numbering from it. Large node numbers are this map's own
convention, not an SDK defect.

---

## 6. HTAToolchain — the third-party exporter

**The SDK does not write `.gam` itself.** `blender_io/gam_export.py`
locates the HTAToolchain add-on and drives its export operator. The SDK
only prepares the mesh.

Quirks, all MEASURED and all expensive to rediscover:

- Import it via `importlib.util.spec_from_file_location`, **not**
  through `__init__.py` — that imports the removed `imp` module.
- It iterates `bpy.data.objects` — every mesh in the FILE, not the
  scene. A map with 5889 imported meshes therefore poisons an
  in-process export.
- Isolation is a background Blender with `--factory-startup` and a
  `bpy.data.libraries.write` bundle.
- The toolchain's module name must be discovered in the parent process
  via `addon_utils.modules()` and passed as an argument. Guessing
  spellings fails on case sensitivity.
- **Under `--factory-startup` its preferences entry must be created
  BEFORE the module is imported.** HTAToolchain reads
  `bpy.context.preferences.addons[__package__].preferences` at module
  level, and `addon_utils.enable()` imports first and creates the entry
  afterwards. Without this the background export dies with
  `KeyError: "HTAToolchain" not found`, produces no file, and the caller
  silently falls back in-process — where it fails on the terrain's
  missing UV map and reports a UV error for a path that should never
  have run.
- It matches texture nodes by **node name** (`Diffuse`, `Bump`,
  `Lightmap`, `Cube`, `Detail`), not node type. Blender's default
  `Image Texture` is invisible to it and exports no texture.
- A background export using freshly created default preferences may not
  match the user's configured settings. UNCONFIRMED whether a scale
  setting exists there; a default Blender cube came out at 40 units,
  suggesting a ×20 factor somewhere.

---

## 7. Diagnostics inventory

All in the Diagnostics panel, all read-only.

| Tool | What it answers |
|---|---|
| Scene Geometry Audit | which meshes are suspect |
| Model Resolution Diagnostics | a 10-stage chain per node, with `Focus On` |
| Map Coverage Report | expected vs imported instances |
| world.xml Census | node classes, attribute coverage, **node id counter** |
| dynamicscene Census | prototypes and resolvability |
| Map Validator | — |
| Asset Browser | — |
| **Model Forensics** | chunk table, undecoded header bytes, decoded skin chunk; A/B compare; folder survey; comparison against a population of known-good models |
| **Registration Audit** | searches every XML for two model ids and reports which files name a working model and not yours; compares `world.xml` nodes |

The last two produced most of the real answers here.

### Resolution stages (Model Resolution Diagnostics)

```
node has a model id -> id found in catalogue -> catalogue gives a .gam path
-> file found on disk -> file parsed -> contains geometry
-> Blender object created -> linked into a collection -> visible
```

---

## 8. The placement blocker — SOLVED

A model created in Blender now appears in the game's editor and on the
map. The cause was one line in the map's `.ssl` manifest.

`data\maps\<map>.ssl` — beside the folder, not inside it — holds a
`LEVEL` section naming every file the map uses. The map under test had
been copied from a shipped one, and its manifest still read::

    <Key name="SERVERS">data\maps\r1m1\servers.xml</Key>

So the editor loaded its landscape from the map's own folder and its
model catalogue from a different map entirely. Every registration
written to `data\maps\<map>\servers.xml` — by the SDK and by hand —
went into a file nothing opened. Pointing the key at the map's own
`servers.xml` fixed it immediately.

**The manifest is the only source of truth for a map's file paths.**
Do not infer them from folder layout. The SDK originally resolved
`SERVERS` through the manifest and was right; it was changed twice on
the strength of symptoms and was wrong both times.

### Everything ruled out along the way — do not re-investigate

All of these were suspected, tested and eliminated by measurement:

- `.gam` structural validity, chunk set, vertex format (8/36, 15/48 and
  9/44 all appear in files that work)
- chunks 16, 32, 128, 256 — each is absent from one of two shipped
  models that render, so none can be required
- the `-6666` root sentinel in the node hierarchy — files with `-1`
  work
- HTAToolchain as a whole — it produces models the editor places
  correctly
- registration in `animmodels.xml`
- node id numbers, `LastId`, node attributes, element name, nesting
  depth, id letter case, geometry scale

### Two further defects found while chasing it

**CRLF multiplication.** `register_model` and `register_in_servers`
opened their files with `open(path, "w")`, which translates `\n` to
`\r\n` on Windows. The text had been read from a CRLF file, so every
write turned `\r\n` into `\r\r\n`. A shipped `servers.xml` reached 13
carriage returns per line — 34070 CR against 2636 LF. Fixed with
`newline=""`; a repair pass collapses `\r+\n` to `\r\n`.

**Buried objects.** `org` Y on a top-level node is a height *above the
ground*, and the terrain sits at 227..541 in Blender units. An object
created in Blender near Z=0 is therefore some four hundred units
underground, and the export recorded that faithfully. An icosphere came
out at `org="2044.000 -396.415 2044.000"` and was invisible. The export
now warns when a node lands more than 150 units from the surface; all
1626 nodes of the shipped map lie within 82.

### Still open

- Textures. See `TEXTURE_CONTEXT.md`.
- `formats/exm/model_catalog.py` still makes no backups before editing
  `animmodels.xml` and `servers.xml`. `world.xml` is protected by
  snapshots; the catalogues are not. This violates principle 1.
- The exporter allocates a new node name on every export instead of
  reusing the one already on the object, so `world.xml` accumulates
  duplicate nodes for the same Blender object.

## 9. Defects worth remembering

Listed because each was subtle, each had a passing test that could not
have caught it, and each is the kind of thing that regresses.

| Defect | Why the test missed it |
|---|---|
| `_ensure_render_uv` read `active_render` from the uv_layers **collection** instead of the layers | the fake collection had no such attribute, so the stand-in could not exhibit the bug |
| the isolation gate checked UV **presence**, not whether one is flagged for render | imported models all have UVs; only the flag was missing |
| imported UV layers were never flagged for render | — |
| background export died before importing HTAToolchain | the failure was logged to a console nobody had open |
| the background failure reason was lost on fallback | the in-process error was true and misleading |
| a truncated `.gam` written by this run was registered as a fallback "from an earlier run" | — |
| `rotation_quaternion` reset on Euler objects (Blender's default) does nothing | the test set `rotation_quaternion` on an Euler object |
| `bl_options` lacked `UNDO`, so no "Adjust Last Operation" panel and operator fields were unreachable | affects `diagnose_models` too — `Focus On` never worked |
| `invoke_props_dialog` + `FILE_PATH` browse button crashed Blender (`EXCEPTION_ACCESS_VIOLATION` in `file_browse_exec`) | — |
| Model Forensics could not open a malformed container — the exact file it exists for | it used the strict importer |
| skin chunk leading uint32 read as a record count | — |
| diagnostics indexed scene objects by `obj.name` instead of the imported node name | every test named its object exactly like its node — the one case that cannot occur in practice. This reported 0/1562 models resolved in a scene where coverage counted 5888 imported |
| `register_in_servers` was written, tested, documented as necessary — and never called | — |
| then pointed at `data\models\servers.xml`, which nothing reads | — |

**`formats/exm/model_catalog.py` still makes no backups.** It edits
`animmodels.xml` and `servers.xml` in place. `world.xml` is protected by
snapshots; the catalogues are not. This violates principle 1 and should
be fixed before any further write-side work.

---

## 10. Where to start

1. **Do not begin by writing code.** Read `Editor_Experiment_Findings.md`,
   `SDK_STATUS.md` and this file, and note that they have historically
   disagreed about the project's state. Trust measurements over
   documents.
2. **Read `m3deditor.log` before theorising about the editor.** It
   names every file it opens. This was available the whole time and
   would have saved several days.
3. Reach for the two audit tools before forming a hypothesis. The
   pattern that works is: take something that demonstrably works, find
   every way it differs from the thing that does not, and eliminate the
   agreements.
4. When a fix does not change the observed behaviour, treat that as
   evidence against the hypothesis rather than as a reason to fix
   harder. Three consecutive rounds were lost to that.
