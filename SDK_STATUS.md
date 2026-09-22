# EXMeditor — Status

**Version 0.52.0** · 1102 tests passing · 114 modules · ~35 000 lines

A snapshot of what works, what doesn't, and what is still unknown.
Kept honest deliberately: a status document that overstates readiness
costs more than one that admits gaps. The measurements behind every
line here are in `Editor_Experiment_Findings.md`.

---

## Formats

| Format | Read | Write | Notes |
|---|---|---|---|
| `displace.bin` (terrain) | yes | yes | lossless, byte-identical round trip |
| `world.xml` (nodes) | yes | yes | attributes absent in the source stay absent; new nodes named from `LastId` |
| `dynamicscene.xml` | yes | yes | records updated in place; new records for placed prototypes; `NodeScale`; nameless records preserved |
| `LevelRoads.xml` | yes | yes | chains editable as curves |
| `static_obstacles.xml` | yes | yes | oriented boxes |
| `.ssl` (manifest) | yes | yes | new maps only |
| `.gam` (models) | yes | via HTAToolchain | geometry, materials, textures, locators; skin chunk patched in place for texture edits |
| `.dds` | yes | yes | DXT1/DXT5 with mipmaps |
| `servers.xml`, `AnimModels.xml` | yes | yes | model catalogues; new models registered |
| `gameobjects/*.xml` | yes | — | prototypes, including composites (turrets, vehicles) and prefabs |
| `level.tile` (ground tiles) | yes | yes | blended on import; written for new maps |
| `lightmap_*.dds` | yes | — | terrain, road and grass lighting |
| raster layers (`.raw`) | yes | yes | opaque passthrough |
| `ecbnt,t` container | yes | yes | shared by six file types |

Everything else in a map folder survives export untouched: the
exporter copies the source folder and overwrites only what it
regenerates, with `.bak` copies when exporting in place.

---

## What works

**Import.** Terrain with its ground tiles and lightmap, `world.xml`
nodes, the dynamic layer with composite prototypes assembled from
their parts (a turret is its pillbox plus its gun on the pillbox's
locator) and prefabs expanded, roads, collision, grass, water, the
map's own sun. Models resolve through the catalogue chain to `.gam`
files with their textures.

**Export.** Move, rotate, scale, delete, add — in either layer — and
the change reaches the right file. Deleting most of a layer at once
is refused rather than obeyed. Nothing the editor does not understand
is rewritten.

**Placing objects.** *Assign Map Object* makes a Blender object part
of the map: a **game object** with a prototype (the barrel that
explodes, the turret that shoots) or a **static node** with a model.
Names come from the map's own sequence at export and stick to the
object. A palette model that is a *part* of something — a turret's
pillbox — is refused with the turrets it belongs to named.

**New maps.** *Create Map* writes an empty map the original editor
opens.

**New models.** *Create Model from Mesh* exports a `.gam` through
HTAToolchain, registers it in the catalogues, and assigns it. Object
transform and colour layer are baked; texture names over 39 characters
are refused rather than cut.

**Textures.** Edit a model's texture in place, fork it for one model,
save back as `.dds`.

**Assets.** The game's models as a Blender asset library with
rendered thumbnails (synchronous — no preview job, after seven crash
logs that all had one).

**Validation.** Missing models, implausible scales, buried and
floating objects, placements outside the playable area — reported
before export.

---

## Limitations

**`.gam` writing needs HTAToolchain** (a third-party add-on, not
bundled). Without it, *Create Model from Mesh* cannot write.

**Composite objects in Blender.** A turret placed from the palette
shows only its pillbox until the map is re-imported; the game and the
original editor show it whole immediately.

**New roads** cannot be created — there is no operator to give a new
curve its roadset and skin.

**Vehicle prototypes** inherit their part layout through
`ParentPrototype`; the importer does not follow that chain, so a
placed vehicle shows its cabin only.

**Roads from models** (preference, off by default): how the engine
spaces and orients road segments is not established.

**Water** floods every basin below `WATERLEVEL`; the original editor
shows water in fewer places. Which basins the engine floods is
unknown, so the surface can be switched off.

**`camera_paths.xml` / `external_paths.xml`** are not implemented.

---

## Open questions

- **Prefab `RelAngle` sign.** 33 of the game's values are 0 and 91
  are absent; the few non-zero ones have not been checked in the
  editor.
- **Does the game read a 40-character texture name?** The skin chunk
  field is 40 bytes; 39 plus NUL is what is written. A full 40 read
  correctly once, because the next field happened to be zero.
- **What does M3DEditor recalculate on save?** Byte-for-byte
  comparison of an untouched save has not been done.

---

## Version history

Condensed; the full account of each change and the measurement behind
it is in `Editor_Experiment_Findings.md`.

| Version | Change |
|---|---|
| 0.52.0 | renamed EXMeditor; repository metadata for a public release (`run_tests.py`, CI workflow, `.gitattributes`); GPL-3.0-or-later, SPDX headers, THIRD_PARTY.md, install archive without tests, developer docs or the research tools (`addon/research/`, registered only when present); size warning for written models; case-exact file resolution; release polish |
| 0.51.0 | turrets placed as StaticAutoGun composites, not their pillbox part |
| 0.50.0 | game objects placed in `dynamicscene.xml`; `NodeScale`; Euler rotations exported |
| 0.49.0 | nameless dynamic records preserved; node names stick; texture record layout corrected |
| 0.48.0 | synchronous asset thumbnails — no preview job |
| 0.47.0 | turrets and prefabs assembled from parts; road and grass lighting from the lightmap |
| 0.46.0 | new maps the editor opens; export folder guard; model shader and texture names preserved |
| 0.15 – 0.45 | textures, the model doctor, forensics, asset palette, ground tiles, water, grass, lighting |
| 0.5 – 0.14 | `.gam` import, both placement layers, roads, collision, diagnostics, object creation |
| 0.1 – 0.4 | terrain round trip, `world.xml`, preferences |
