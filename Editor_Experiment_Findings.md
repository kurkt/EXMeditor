# Editor integration experiment — findings

The game's own editor was used to integrate a model (`MSCV NOD`) into a
map. Comparing the result against what the SDK produces settled four
questions that byte-comparison alone could not, and **overturned one
conclusion the SDK had been acting on**.

Applied in version 0.24.0.

---

## 1. `servers.xml` registration is NOT required — correction

**Previous belief:** a model must be listed in the map's `servers.xml`
or the game never loads it. Basis: all 114 models the reference map
places appear there, and the file holds 814 entries against the
catalogue's 1036.

**What the editor did:** nothing. `MSCV NOD` is absent from
`servers.xml`, and the model works.

The earlier reasoning mistook correlation for cause — those 114 appear
there because they ship with the map, not because placement requires
it. The SDK was modifying a shipped file for no benefit, which is a
risk taken for nothing.

**Action:** the SDK no longer touches `servers.xml`. The writer is kept
but unused.

---

## 2. Absolute paths outside the game folder are accepted

```xml
file="D:\modProject\custom\MSCV NOD.gam"
```

The model sits outside the game tree entirely. The SDK's insistence on
`data/models/custom` and game-relative paths was self-imposed.

---

## 3. Catalogue entries carry four attributes, not one

| source | attributes |
|---|---|
| editor | `shadow="1" windwavy="0" trans="0" composite="0"` |
| shipped | `shadow="0" windwavy="0" trans="0"` |
| SDK before | `shadow="1"` |

`windwavy` (wind animation), `trans` (transparency) and `composite`
were absent from SDK entries, making them unlike anything either the
game or its editor produces. Whether the engine requires them is
untested, but matching the editor costs nothing.

**Action:** all four are written.

---

## 4. Spaces are valid in a model id

`id="MSCV NOD"` works. The SDK replaced spaces with underscores, so a
user's chosen name silently became something else.

**Action:** spaces are kept; only characters that would break XML or a
path are replaced.

---

## 5. The placed node matches what the SDK already writes

```xml
<Node name="Object76476807" class="SgAnimatedModelNode"
      org="1042.801 0.000 2737.913" orgRel="1"
      id="MSCV NOD" ndmAction="0" />
```

No `scale`, no `rot` — the editor omits both when placing. `ndmAction`
is present, confirming that addition was right. `LastId` advanced to
76476808.

This part of the SDK needs no change.

---

## Still open

**Why an SDK-created `.gam` does not render.** Unchanged by this
experiment: `MSCV NOD` was produced by other means, so it says nothing
about the SDK's export path. The export itself still fails before
writing, currently inside HTAToolchain at `calc_tangents()`.

**Which export path runs.** Version 0.23.3 added a line naming it and
the meshes that block an in-process run:

```
Export path: ... | N mesh(es) in the file | problem meshes: ...
```

That line has not yet been seen, and several fixes were attempted
without it. It should be the next thing collected.

**Vertex format.** `XYZNCT2` was forced on the reasoning that shipped
decorations use it. Not confirmed — `MSCV NOD`'s own format was not
checked against it, and the hypothesis has cost several iterations.

---

## Method note

This experiment produced more certainty in one exchange than the
preceding dozen iterations of comparison. The pattern is consistent
with earlier corrections in this project — `commonservers.xml`,
`model_names.xml`, chunk 240 — where a role was inferred from position
or statistics and turned out wrong.

The distinguishing feature of the wrong conclusions: each was
consistent with all available data, and each was settled immediately by
one observation of the real tool doing the real thing.

---

## 6. Why the editor died on a created map — `level.tile`

Applied in version 0.40.0. The second crash, after `NormalMap.bin` and
`ShoreLine.bin` had fixed the first one.

**The log.** `m3deditor.log` stops here:

```
world.cpp[0132]      ----------------------- World Loading
landscape.cpp[1677]   +- Enter function: Landscape::Load()
landscape.cpp[1914]   |  ----------------------- Normal map loaded in: 0
```

**What is next after the normal map.** Not guessed from the file list —
read out of `M3DEditor.exe`. The three log strings are referenced from
`Landscape::Load` at ascending code addresses:

| string | referenced at |
|---|---|
| `----------------------- Normal map loaded in: ` | `0x00545FA1` |
| `----------------------- Tiles loaded in: `      | `0x00546261` |
| `----------------------- ShoreLine loaded in: `  | `0x005464CB` |

So the load order is normal map → **tiles** → shoreline, and the editor
died inside the tile load.

**What it died of.** `0x80000003 BREAKPOINT at 0x0068B47E`. The
instruction there is an `int 3`, and the two instructions in front of
it are:

```asm
cmp  dword ptr [edx-4], 0DEADBEEFh
je   +1
int  3                              ; <- 0x0068B47E
```

followed by the same shape again against `0FEEBDAEDh` at the far end of
the block, then poison words written into the header. That is the
memory manager's own heap check on **free**: the block's leading guard
word was not `0xDEADBEEF`. The editor did not reject the file — it
walked off the end of something and noticed later, which is why there
is no error message.

**The difference in the file.** Census of every `level.tile` in the
game — 36 shipped maps and the five the editor wrote itself, 41 in all:

```
41 of 41   chunk 0x0BADF00D   the tile payload
41 of 41   chunk 0x0000F001   30 bytes, reading "TILEMAP"
41 of 41   chunk 0x0000F002   4 bytes, holding 1
```

The SDK wrote **one** chunk. Four of the five container writers in
`formats/exm/new_map.py` appended the `0xF001` / `0xF002` trailer —
`grass.xml`, `NormalMap.bin`, `ShoreLine.bin` — and `single_tile_map`
did not. A reader that expects three entries in a table holding one
reads the next 32 bytes as chunk descriptors; those bytes are the
payload, so the size and offset are whatever the map data happens to
say. That is the shape of an overrun, and an overrun is what the guard
word caught.

**After the fix**, `single_tile_map` reproduces the editor's own
`level.tile` **byte for byte** at all five sizes the editor produced —
402, 1170, 4242, 16530 and 262290 bytes.

Two smaller defects were found in the same comparison and fixed:
`strings.xml` was rooted at `<strings>` where all ten maps checked use
`<resource>`, and the text files were written CRLF where all 41 `.ssl`
files and every editor-written map XML use bare LF.

**Method note.** The whole diagnosis is static: a log line, a code
address ordering, a disassembled guard, and a census. Nothing was
inferred from a file's name or from which maps happen to ship a file —
the mistake that produced the *previous* crash, recorded in
`core/map_template.REQUIRED_FILES`.

---

## 7. "The changes were not saved" — the export went one folder up

Applied in version 0.41.0. Diagnosed from file timestamps, not from a
description of what was clicked.

`data/maps/` — the folder that holds the map folders — contained a
loose copy of a whole map:

```
22:37:13   18 files, byte-identical to data/maps/NewMap2/
22:38:29   displace.bin, dynamicscene.xml   (the two the export rewrites)
```

`shutil.copy2` preserves mtimes, which is why the copies carry the
map's creation time rather than the copy's, and why the two files the
export actually wrote stand out with a later one.

**What happened.** Export is a snapshot: when the destination is not
the source, `copy_map_folder` copies the whole map there first, then
writes the edited files over it. The file browser's Accept takes the
folder it is *showing*, so accepting one level too high sent the
export into `data/maps`. It did exactly what it was told — copied the
map beside the other maps and wrote the edits there — and the map
folder itself was never touched. The editor then opened the map and
showed the terrain as it was.

**Two changes:**

1. The browser now opens **inside the map's own folder**
   (`self.directory = source_dir` in `invoke`), so accepting straight
   away saves in place, which is the normal case.
2. Exporting into a folder that *contains* the source is refused
   outright (`core.snapshot.contains_folder`), with a message saying
   which folder to go into. Writing map files into the folder of maps
   produces nothing the game reads, so there is no case to allow.

---

## 8. A model exported back out of Blender loses its shader

Applied in version 0.41.0.

The user's own two files, read with the SDK's skin-chunk reader:

```
data/models/nature/region3/crag/crag_boulder2.gam    (the original)
    shader = 'diffuse_detail_vc'
    textures = ['tropiccrag.dds', 'coverrock_detail.dds']

data/models/custom/Crag boulder2.001.gam             (exported from Blender)
    shader = 'bumpdiffuse_envalphagloss_spec'
    textures = ['tropiccrag.dds', 'coverrock_detail.dds']
```

Same textures. Different shader. `envalphagloss` gives the diffuse
alpha a meaning — gloss and environment — that `diffuse_detail_vc`,
which reads a detail map and a vertex colour, does not give it at all.
That is a model that looks right in Blender, where the material is a
Blender material and its name means nothing, and comes out part
transparent in the editor.

**Why.** HTAToolchain reads the shader from the **material name**, the
same way it reads the texture from the **node name** — the fault
already recorded in `_name_texture_nodes_for_export`. The importer
names materials for the outliner (`<prefix>_<shader>_<texture>_<hash>`),
so nothing matches a shader and the toolchain writes its own default.

Census of `data/models/custom` — 32 models exported so far, 31
readable: **27 carry `bumpdiffuse_envalphagloss_spec`** and 4 carry
`diffuse_detail_vc`, and those 4 are exactly the ones whose Blender
material happened to be *called* `diffuse_detail_vc`.

**Fix.** `_name_materials_for_export` renames each material to the
`exm_shader` the importer already stored on it, and restores the names
afterwards — the same shape as the node-name pass it sits beside. A
material with no recorded shader is left alone; inventing one would be
worse than the toolchain's default.

Blender cannot give two datablocks the same name and appends `.001`.
Harmless: two of the user's exports hold two materials both written as
`diffuse_detail_vc`, which is only possible if the toolchain strips the
suffix itself.

**Still unexplained (НЕИЗВЕСТНО).** The original carries chunk 16 and
the export does not. Chunk 16 is undecoded — `CHUNK_NAMES` has it as
"seen on civilhouse1" — so nothing can be said about what its absence
costs.

---

## 9. A dragged asset had to be re-registered as a new model

Applied in version 0.41.0.

The asset palette stamps `exm_asset_id` on its objects.
`EXM_OT_assign_node.invoke` read `exm_id`. So an object dragged out of
the Asset Browser opened the dialog with an **empty** model field,
although the docstring of `blender_io/asset_library.py` promises the
opposite.

The consequence is not the typing. The way out of an empty model field
is *Create Model from Mesh*, which writes a **new** `.gam` — and a new
`.gam` is where finding 8 applies. A dragged asset is a model the game
already has; it needs placing, not creating.

Fixed by falling back to `exm_asset_id`. The palette property is
deliberately *not* renamed to `exm_id`: `exm_id` alongside `exm_class`
is what marks a placed node, and stamping that on a palette of a
thousand models would put all of them into the export.

---

## 10. A model brought in as `.obj`: material lost, model on its side

Applied in version 0.42.0. Both faults measured on the file the user
exported, `data/models/custom/tripo_node_ea527cc5-…gam`.

### The material

```
Blender image   tripo_image_ea527cc5-57ee-420f-a393-85d62720b655.png   (51 chars)
written to .gam tripo_image_ea527cc5-57ee-420f-a393-85d6              (40 chars)
```

The skin chunk stores a filename in a `char[44]` (see
`formats/exm/skin.py`, byte-exact against five models), so 43
characters is the most that fits — and the toolchain cut at 40,
extension and all. The game looks for a file of the cut name, finds
nothing, draws the model with no material, and says nothing.

The longest of the 1136 texture names the game itself uses is 37
(`commonwealth_of independent_towns.dds`); only two names in the whole
corpus reach 36.

`verify_texture_names` now reads the written file back and compares
each name against the images the materials actually point at, so the
cut point never has to be guessed. A cut name is an error, not a
warning: the result is a broken material with no other symptom.

### The orientation

```
tripo_node…gam   min=(-0.50, -0.46,  0.00)   max=(0.50, 0.46, 0.58)
```

Z rests exactly on zero and rises; Y is symmetric about zero. That is
a Z-up model in a Y-up format.

Census of 698 shipped models, by which axis has its minimum nearest
zero — the axis the model rests on:

| axis | models | median minimum |
|---|---|---|
| **Y** | **596** (85%) | −1.09 |
| Z | 56 | −5.56 |
| X | 46 | −6.29 |

The round trip itself is sound: `Crag boulder2.001.gam` comes back with
bounds **identical** to `crag_boulder2.gam`, to the centimetre. So the
export writes faithfully what the Blender object was — and this object
was lying down in Blender.

`_report_orientation` now says which axis the written model rests on
and warns when it is not Y. A warning, not an error: 102 of the 698
shipped models genuinely rest on X or Z.

Scale is worth watching too, though it is not an error: this model is
1.0 game units across, where the median shipped model is 16.5 and a
terrain cell is 32. 56 of 698 are under 2 units.

---

## 11. Build Asset Previews crashed Blender

Applied in version 0.42.0. From `TESTmap.crash.txt`, Blender 3.6.1,
`EXCEPTION_ACCESS_VIOLATION`:

```
do_job_thread
  icon_preview_startjob_all_sizes
    object_preview_render
      scene_graph_update_tagged
        BKE_callback_exec_id_depsgraph
          bpy_app_generic_callback        <- a PYTHON handler
            PyEval_EvalFrameDefault …
              pyrna_struct_setattro       <- it sets an RNA property
                RNA_property_update
                  WM_event_add_notifier_ex
                    note_cmp_for_queue_fn <- crash
```

A preview renders on a **worker thread**. Blender evaluates the
depsgraph there, which fires every registered Python depsgraph handler
**on that thread**. One of them sets an RNA property; setting one
queues a UI notifier; the notifier queue is not thread-safe.

Asked of the user's Blender directly — seven are registered, none of
them this SDK's (it registers no handlers at all):

```
depsgraph_update_pre   DirectUpdateNPRNodes       toonkit
depsgraph_update_pre   on_depsgraph_update_pre    psoft_pencil4_line
depsgraph_update_post  sync_dg_handler            goo_engine_light_groups
depsgraph_update_post  scene_update_post          flip_fluids_addon
depsgraph_update_post  update_material_utility    extreme_pbr
depsgraph_update_post  ypaint_last_object_update  ucupaint
depsgraph_update_post  sbs_depsgraph_update_post  Substance3DInBlender
```

**The count was a red herring.** The log shows the first run rendering
24 previews successfully, then the limit raised to 25, then a second
run — which builds exactly **one** new preview — crashing. It is a
race, not a capacity limit.

`_depsgraph_handlers_suspended` takes those handlers off for the
duration of the render and puts them back in a `finally`. They exist
to react to the user editing something; rendering a thumbnail is not
that.

---

## 12. What the toolchain keeps and what it drops — measured, not read

Applied in version 0.43.0. One asymmetric box, 1 x 2 x 4, upright and
resting on Z=0, exported four ways through the real toolchain in a
headless Blender:

| exported | came out as |
|---|---|
| colour attribute `Col`, FLOAT_COLOR | **export failed** |
| colour layer `color`, BYTE_COLOR | X 0..1  Y 0..4  Z 0..2 |
| + OBJECT rotation 90° X and scale 10 | X 0..1  Y 0..4  Z 0..2 |
| + the same baked into the mesh | X 0..10 Y 0..20 Z −40..0 |

Three facts, none of them inferred:

**The axis swap is right.** Blender Z → game Y. The upright box came
out standing on game Y. The suspect was innocent.

**The object's rotation and scale are thrown away.** Row three is
identical to row two. `HTAToolchain/__init__.py` reads `vert.co` —
local mesh data — and only reaches for `matrix_world` when
`mesh.type == 4`, which a model is not. So a mesh rotated and scaled
*as an object*, which is what everyone does after importing an `.obj`,
exports at its original size and orientation. That is the whole of
"much smaller, and on its side".

The SDK made it worse: `_move_to_origin` **reset** scale and rotation
before export, on the stated belief that "scale and rotation are baked
into the vertices by the exporter AND carried on the map node, so
leaving them applied means they are applied twice". The first half of
that is false, so the reset did not prevent a double application — it
discarded the transform outright.

**A vertex-colour layer named `color` is required, and it must be
BYTE_COLOR.** The SDK asks for vertex type XYZNCT2, whose writer
unpacks `*vertex.color`; that is `None` unless
`'color' in data.vertex_colors`, and `mesh.vertex_colors` exposes only
BYTE_COLOR corner layers. The importer's own layer is `Col` and
FLOAT_COLOR — invisible on both counts. With no such layer the export
raises `TypeError: Value after * must be an iterable, not NoneType`
and writes nothing.

`_prepare_meshes_for_export` now swaps in a **copy** of each mesh with
the object's rotation and scale baked in and a `color` layer present,
and swaps it back afterwards. Re-running the same probe against the
fixed path: the first row now exports, and the third row now matches
the fourth.

---

## 13. The preview crash, second half: previews are asynchronous

Applied in version 0.43.0.

Version 0.42.0 closed the worker-thread path (finding 11). The next
crash log came from the **main** thread:

```
note_cmp_for_queue_fn
BLI_gset_ensure_p_ex
WM_event_add_notifier_ex
wm_jobs_timer            <- the job timer, long after the operator
wm_window_timer             had returned
wm_window_process_events
WM_main
```

`wm_jobs_timer` running at all is the proof: `asset_generate_preview()`
does not render, it **queues a job and returns**. The jobs outlive the
operator.

And the operator called `hide_palette()` the moment its loop finished
— which excluded the layer collection, and excluding takes the objects
out of the depsgraph. The queued jobs were left rendering objects that
had been pulled out from under them.

`hide_palette()` now uses the viewport eye (`hide_viewport`), which
hides the palette and leaves it evaluated. `asset_collection()` also
clears `exclude` on the way in, so a file saved by an older version
does not start the next session with the palette outside the
depsgraph.

---

## 14. The preview crash, named — and two fixes that were not fixes

Applied in version 0.45.0.

**ОПРОВЕРГНУТО:** "one of the seven add-ons' handlers". The
experiment was run — all seven disabled — and the crash came back by
the identical path. Asked of that Blender again, exactly one
depsgraph handler remained:

```
depsgraph_update_post   sync_dg_handler   from goo_engine_light_groups
```

It is not an add-on. It ships in Goo Engine's own `scripts/startup/`
and is not on the add-on list, so disabling add-ons could not touch
it. Its source (`goo_engine_light_groups.py`):

```python
def sync_dg_handler(scn, dg):
    for update in dg.updates:
        if isinstance(uid, Material) or isinstance(uid, Light):
            sync_light_groups()               # on ANY material update

def sync_light_groups():
    for data in iter_light_group_owners():    # EVERY material in the file
        map_bits(data, bit_mapping)

def map_bits(data, mapping):
    data.light_group_bits = (0, 0, 0, 0)      # RNA write -> UI notifier
    data.light_group_shadow_bits = ...        # RNA write -> UI notifier
```

One material evaluated by a preview job, and it rewrites two
properties on every material in the file — hundreds, on a map with 147
models — each write queueing a notifier from inside the depsgraph
update the preview is running. That is every one of the four crash
logs: `bpy_app_generic_callback` → Python → `pyrna_struct_setattro` →
`RNA_property_update` → `WM_event_add_notifier_ex` →
`note_cmp_for_queue_fn`.

**Why 0.43.0 did not fix it:** the handler was suspended only across
the loop that *queues* the jobs. The jobs run later.

**Why 0.44.0 did not fix it:** the handler was suspended until every
preview "had a size". MEASURED in a background Blender:
`obj.preview.image_size` reads `(128, 128)` the instant
`asset_generate_preview()` returns, before anything can have rendered.
So the handler went back after one second.

**Now:** the handler stays off until Blender's own job signal —
`bpy.app.is_job_running("RENDER_PREVIEW")` — says the previews are
over, with a ten-second floor beneath that (the job type is taken from
Blender's source; a background Blender cannot run the job, so the
probe could not confirm it on this build) and a sixty-second ceiling
above it. The palette is excluded from the depsgraph on the tick the
jobs end, and the handler goes back one tick later, so the depsgraph
update the exclusion causes also runs without it.

Taking the handler off is safe: it recomputes derived light-group bit
masks that its own `load_post` and property-update hooks recompute
anyway.

If it still crashes, *Render Thumbnails* in the operator's redo panel
(F9) builds the palette with no preview jobs at all — names and
drag-and-drop work, pictures do not — and the next crash log will say
whether the path is still this one.

---

## 15. "Turrets do not load" — they were never one model

Applied in version 0.46.0. Read off the user's autosave
(`TESTmap_15068_autosave.blend`, 79 106 objects), not off a description:

```
SgAnimatedModelNode   1525 / 1524 with geometry
SgGameUnitNode          38 /   38 with geometry     <- the pillboxes are fine
dynamic objects       4443, 53 prototypes, 18 of them with NO geometry
   staticAutoGun02  x4   Empty
   staticAutoGun04  x7   Empty
   staticAutoGun07  x2   Empty
   staticAutoGun08  x1   Empty
```

The turrets are **dynamic objects**, and their prototype has no model:

```xml
<Prototype Class="StaticAutoGun" Name="staticAutoGun04">
    <MainPartDescription id="DOT">
        <PartDescription id="CANNON" lpName="LP_CANNON01" />
    </MainPartDescription>
    <Parts>
        <Part id="DOT"    Prototype="heavy_dot4" />
        <Part id="CANNON" Prototype="vulcan01" />
    </Parts>
</Prototype>
<Prototype Class="VehiclePart"    Name="heavy_dot4" ModelFile="heavy_dot4" />
<Prototype Class="BulletLauncher" Name="vulcan01"   ModelFile="vulcan01" />
```

A turret is a pillbox **part** plus a gun **part**, the gun mounted on
the pillbox's locator node `LP_CANNON01`. `resolve_model_id` knew
three sources — `ModelName`, `ModelFile`, the name itself — and a
composite prototype has none of them. 233 of the game's 976 prototypes
are composite.

**The node table.** Chunk 2 of a `.gam` is 136-byte records —
`char[40]` name, `int32` parent, `float[3]` location, `float[4]`
rotation, `float[16]` matrix; heavy_dot4's is 544 bytes = 4 nodes, and
`LP_CANNON01` sits at (−0.19, 8.75, 0.03) on a body whose Y runs
−2.65 .. 8.77. `formats.exm.gam.read_locators` reads it.

**Prefabs** are a third shape: a `Barricade` is `<ObjInfos>` — whole
prototypes at `RelPos`, turned by `RelAngle` — and `barricade4_wGw`
holds two sack walls and a `staticAutoGun08`. 22 prefabs in the game.
Expanded recursively, so the turret inside a barricade gets its gun.
The sign of `RelAngle` is UNVERIFIED (ГИПОТЕЗА): 91 of 142 entries omit
it and 33 are 0, so nothing on r1m1 exercises it.

**After**, importing r1m1 with the working copy:

```
staticAutoGun04  x7  body=heavy_dot4  child CANNON at (-0.19, 0.03, 8.75)
barricade4_wGw   x1  walls at x = -6, +6; staticAutoGun08 at 0 with its CANNON
prototypes with no geometry: 9 of 53 — Human/Human2/Human3 (skinned NPCs),
four r1m1_*Village, caravanFormation, genericLocation (nothing to draw)
```

4385 of 4443 dynamic objects resolve to a model; the rest have none by
nature. Parts and prefab contents are children with no `exm_dynamic`,
so the exporter never writes them as objects of their own — the game
re-assembles them from the prototype.

A vehicle (`Sml301`) inherits its `MainPartDescription` from a
`ParentPrototype`, which this reader does not follow: it shows its
cabin (the first part) and no guns. Left for when vehicles matter.

---

## 16. Road and grass colour: the lightmap, sampled by position

Applied in version 0.47.0. The user was right that lighting had nothing
to do with it. The game's own shader sources, `data/shaders/`:

```hlsl
// road.fx
Out.Tex1 = mul( float4( In.Pos, 1 ), mWorld ).xz * lightmapScale;
return tex2D( DiffSampler, In.Tex0 ) * tex2D( LightmapSampler, In.Tex1 ) * 2;

// grasstest_vs11.vs / grasstest_ps11.ps
o.Tex1 = worldPos.xz * lightmapScale;
lightmap = 2 * tex2D( lightmapTex, i.Tex1 );
diff.xyz *= lightmap.xyz;
```

A road is its texture times **the terrain's lightmap at the road's
world XZ**, times two. Grass is the same. `g_Ambient` and `g_Diffuse`
are declared in `road.fx` and never used; `MODEL_AMBIENT` / `LS_COLOR`
from the manifest do not reach a road at all.

`blender_io/world_lightmap.py` inserts one node chain into a COPY of
each road and grass material: world position → mapped onto the
terrain's 0..1 UV grid → the lightmap image → MODULATE2X with whatever
fed Base Color. The grid's world origin and extent are read off the
terrain object's own corner vertices, so centring and scale are
included by construction.

Verified on a live import of r1m1: 13 road and 5 grass materials
tinted; at three road vertices and two grass tufts the computed
lightmap UV matched the terrain's own stored UV at the nearest vertex
to within 0.001 (one cell is 0.002).

---

## 17. "New objects lose their scale and disappear" — applied twice

Applied in version 0.47.0. Census of every `world.xml` in the game:
24 202 of 36 338 nodes carry `scale`, every value in **0.6 .. 2.4**.
The SDK-written maps:

```
NewMap4     scale="258.550 258.550 258.550"   tripo_node
r1m1        scale="345.826 345.826 161.062"   Icosphere
r1m12       scale="1890.182 ..."               PlaneSKY
r1m1-1-1    scale="5352.797 ..."               teeth_top
16x16       scale="-137.955 ..."  x36          SgNode, an .obj hierarchy
```

The mechanism: a 1-unit `.obj` is scaled 258x in Blender to be
map-sized; since 0.43.0 *Create Model* bakes that scale into the
`.gam`; the object is then put back exactly as it was — still scaled
258x — and the next map export writes `scale="258.55"` on the node.
The game applies both. 258 x 258 = the model is the size of the map,
and from inside it is invisible.

**Fix:** after a successful write, `apply_baked_transform` gives the
object the baked mesh (a copy, so a shared mesh is untouched) and an
identity rotation and scale — Blender's Apply Transform, done for the
user. Verified: an object rotated 90° and scaled 10x ends with scale
(1, 1, 1), rotation (0, 0, 0), and a mesh whose extents equal the
file's.

The map export now also names every node whose scale lies outside
0.25 .. 4, with the total at the end.

---

## 18. Texture editing — verified end to end, no defect

Version 0.46.0, driven in a background Blender on the user's own
custom model (`custom/` backed up and restored):

```
Edit Texture     tropiccrag.dds is shared -> fork written beside the model:
                 tropiccrag_Crag boulder2.001.dds; the .gam skin now names it;
                 the Blender image points at it
paint            a red square at Blender rows 0 .. h/4
Save As DDS      wrote the fork: DXT1, 512 x 512, 10 mip levels
read back        bottom-left texel (255, 0, 0)  <- the square, right way up
```

The fork name is already fitted to the 44-byte field
(`fork_texture_operator._fit`).

---

## 19. Asset previews: the ceiling was flat

Applied in version 0.47.0. Crash log of 2026-09-19 14:53, Goo Engine,
SDK 0.46.0: `build_asset_previews(limit=0)` queued **156** previews;
then an asset was dragged in, then "linked" toggled in the redo
panel; the crash came on the preview worker thread, in the Goo
handler — the path of the very first crash. The handler was on again
while previews still rendered.

ГИПОТЕЗА, strongly supported: the 60-second ceiling on the handler's
suspension expired mid-render. 24 previews took 8.5 s with the handler
off and 23–34 s with it on; 156 is past a minute either way. The
ceiling now scales with the queue — five seconds per preview, never
under a minute — so 156 previews get thirteen minutes. The "linked"
toggle is, on this evidence, a bystander: it re-ran the drop while the
storm was already on.

---

## 20. Asset previews: no job at all

Applied in version 0.48.0. The seventh crash log settled the shape of
the thing:

```
wm_event_do_notifiers
  BLI_gset_remove
    ghash_remove_ex
      note_cmp_for_queue_fn      <- EXCEPTION_ACCESS_VIOLATION
```

**No Python on the stack. No preview thread alive.** Blender's own
main loop popped a notifier and died removing it from its own
dedup set — the set held a dangling entry. The Goo handler in the
six earlier logs was the *detector*: it queues a thousand notifiers
per depsgraph update, so it was always first to hit the bad bucket.
Three suspension windows around it were therefore never going to
hold.

What every log shares is the **icon-preview job**
(`asset_generate_preview()`): asynchronous, outliving the operator,
evaluating the depsgraph on a worker thread, posting notifiers about
IDs that undo/redo and the Asset Browser's own redraws may have
replaced before they are processed — and the browser starts such
jobs itself, on a drag, whenever it wants a thumbnail.

**So the SDK no longer uses it.** `_render_preview_now` renders each
thumbnail synchronously: a temporary scene with the one object and a
camera framed on its bounding box, Workbench through `render.render`
(runs to completion on the main thread), pixels into `obj.preview`.
MEASURED live on r1m1 (79 017 objects, 588 materials):

```
24 thumbnails    8.3 s     custom images 24 / 24    preview job: never
157 thumbnails  38.7 s     custom images 157 / 157  preview job: never
```

against 23–34 s for 24 through the job. With nothing queued, the
palette is excluded and the handlers put back before the operator
returns; the job remains only as a fallback for an object the
synchronous render cannot frame, with the old wait behind it.

Goo's `render_init` hook fires per synchronous render and does the
full light-group sync (25 syncs, 14 750 writes for 24 thumbnails), so
it is suspended for the build alongside the depsgraph hooks.

`handler_guard` stays: every Python depsgraph handler is skipped off
the main thread, and Goo's during any preview job, for the session.

## 21. The barrel experiment: "linked", Assign, and a map the editor would not open

Applied in version 0.49.0. Three observations from one experiment on
r1m1: a barrel dragged from the asset palette with *Linked* ticked,
registered with Assign, exported; afterwards M3DEditor died opening
the map.

### 21.1 What the "Linked" checkbox is

MEASURED (Blender's own RNA, Goo Engine 3.6):

```
OBJECT_OT_add_named.linked
  "Duplicate object but not object data, linking to the original data"
```

The checkbox in the *Adjust Last Operation* panel after a drop from
the Asset Browser belongs to Blender's drop operator, not to the SDK.
Ticked, the new object shares the palette object's **mesh datablock**;
unticked, it gets a copy. It says nothing about the map: neither
variant carries `exm_class` / `exm_id`, because a palette object only
carries `exm_asset_id` (so that Assign can pre-fill the model id). The
node properties are stamped by **Assign** — by design, since the drop
is Blender's and the SDK is not told about it. So: *Linked* saves
memory and keeps the copies in sync when the palette mesh changes;
Assign is what makes the object a node. Both behaved as intended.

### 21.2 The exported dynamicscene.xml lost 21 records — CONFIRMED defect

`data/maps/r1m1/dynamicscene.xml` after the export, against the
untouched `r1m1_22` copy, structurally (5711 records against 5732):
**sixteen placed records without a `Name` attribute are missing, and
five children with them** —

```
11  <Object Prototype="LightObject2" Pos=…>          top level
 4  <CameraPoint Pos=…>                              TheTown/EntryPath, TheTown/ExitPath
 1  <Item Prototype="BugForSale" Pos="0 369.722 0">  TheTown/TheTown_Workshop/Vehicles
      + its Parts (BASKET, CABIN, CHASSIS) and Repository
```

Nothing else differs. The export matched Blender objects back to
records **by name**. A record with no Name had no Blender object
called `""`, so the export took each of the sixteen for a deletion,
subtree included. 16 of 4459 is under the wholesale-loss guard
(50 %), so it went through silently.

Fix (`blender_io/dynamic_bridge.py`): every built object carries
`exm_dynamic_key` — the record's Name, or `#<walk index>` when it
has none — and export matches on that key. A nameless record is
never counted as deleted: nothing in Blender can be said to have
been it; the wholesale-loss share is taken over named records only.
Objects built by an earlier release (no key) still match by name.
MEASURED with the fix: r1m1_22 built, exported with nothing deleted,
written and read back — 5732 records in, 5732 out, 0 lost. Tests:
`tests/test_nameless_dynamic_objects.py`; the same scene through the
v109 bridge reports *"3 of 5 placed dynamic objects are absent"*.

### 21.3 The new node was renumbered on every export — CONFIRMED defect

`world.xml` diff between the two exports: one node, the barrel, went
out as `Object76476763` and then as `Object76476764`, `LastId`
climbing with it. The name was allocated during extraction and never
written back onto the Blender object, so each export saw an unnamed
node and allocated afresh.

Fix (`core/node_naming.py`, `blender_io/world_bridge.py`): the
allocated name is stamped into `exm_original_name`; a duplicated
object (Shift+D copies the stamp) is detected by `allocator.claim()`
and gets a name of its own; stamps not yet in the file (an export
that failed after allocating) are passed to the allocator as
`reserved` so they cannot be handed to another object. Tests:
`tests/test_node_name_stamping.py`.

### 21.4 Why the editor died — what the logs say

`exceptions/m3deditor.exe0063.log`: `0xC0000005` at `0x00657C84`,
EBX = ECX = EDX = 0. Disassembly of the function: a `DraftModel`
texture-load path doing `mov ecx,[eax+0x304]; mov edx,[ecx]` —
the renderer's texture manager pointer is NULL. `m3deditor.log`
before it, in order:

```
Couldn't load texture data\models\custom\tripo_image_ea527cc5-…  for model 1.gam
cannot read file: AnimModels.xml id = CubeSky
invalid DurCoeffsForDamageTypes for 'tripotankCab01'
Assertion failed VehiclePart.cpp:110 "Critical error"
collision geom in vehicle part 'heavy_dot1' is not BOX
DynamicScene.cpp[1470] Scene loading begin
<crash>
```

- `tripotankCab01`: `DurCoeffsForDamageTypes="30 20 20"` in the
  user's own vehicle prototypes (made today); every shipped value is
  ≤ 20. The editor asserts on it and continues in a state its own
  code calls a critical error. **ГИПОТЕЗА: the strongest single
  cause.**
- `1.gam` names an extension-less texture: MEASURED in the file,
  `tripo_image_ea527cc5-57ee-420f-a393-85d6`, exactly 40 bytes, the
  toolchain's `<40s` cut of a 52-character name (see 21.5). The load
  fails, the model is drawn without it. Not fatal on its own — the
  editor logs it and goes on — but it is the one texture failure
  right before a texture-load crash. The current exporter refuses
  such a name (`verify_texture_names`); this `.gam` predates it.
- `CubeSky`: a stale registration in `AnimModels.xml`. Logged, not
  fatal.
- The 21 dropped records (21.2): the map loaded by the editor had
  no lights, no camera path points for TheTown and no `BugForSale`.
  Whether their absence reaches the crash site is **НЕИЗВЕСТНО** —
  the crash is in a texture path, not in scene parsing.

**Measurement that decides it:** restore `r1m1/dynamicscene.xml`
from `r1m1_22` (a strict superset, no other differences), open the
map in M3DEditor. If it still dies, fix `tripotankCab01`'s
`DurCoeffsForDamageTypes` (≤ 20) and open again. The order isolates
the two candidates.

### 21.5 The texture record — a layout correction, ОПРОВЕРГНУТО → ПОДТВЕРЖДЕНО

Chasing the 40-byte name above: the SDK held the skin chunk's texture
record as `char[44] filename + uint32 slot`. HTAToolchain's own parser
(`htaparser.py:1450`) packs `<40s` name, `<I` uv, `<I` type. A census
over every shipped model decides it:

```
1373 models, 11 062 texture records
bytes 40..43 non-zero while the name is < 40 chars:  191 records, 107 models
   value 1: 155     value 2: 36
   road_detail       slot 4  uv 1   63
   lightmap_detail   slot 2  uv 1   41
   lightmap_detail   slot 4  uv 2   36
   DiffuseAO         slot 2  uv 1   23
   diffuse_detail    slot 4  uv 1   15   …
longest shipped name: 37 (commonwealth_of independent_towns.dds)
```

Bytes 40..43 are the **UV set** the map is read through — a second
set for lightmap/AO and detail maps — not name. The record is
`char[40] + uint32 uv_set + uint32 slot`. The old reading got every
name and slot right by the accident of NUL padding, and `set_texture`
then wrote 44 bytes of name over the UV set: MEASURED on
`bridge_stone.gam` material 1 slot 4 (`coverrock_detail.dds`, UV set
1) → after the edit, UV set 0. The detail map would have been mapped
through the wrong UV set on any of those 107 models whose lightmap,
AO or detail slot was retargeted.

Fixed in `formats/exm/skin.py` (`TEXTURE_NAME_SIZE = 40`,
`SkinTexture.uv_set`, read, written and preserved) and
`core/gam_forensics.py`; the same edit on `bridge_stone` now changes
20 bytes, all inside the name field. The name limit in
`verify_texture_names` and the fork operator is 39 characters, not
43. Tests: `tests/test_skin_uv_set.py`. Does the game require the
NUL inside the 40 bytes? НЕИЗВЕСТНО — `1.gam` with a full 40 read
correctly because its UV set is 0 and served as the terminator.

## 22. "The barrel lost its properties" — it was in the wrong file

Applied in version 0.50.0. The barrel placed from the palette and
exported was visible in the editor and inert in the game: no physics,
no damage, no explosion.

### 22.1 Where a barrel lives — ПОДТВЕРЖДЕНО

`r1m1/dynamicscene.xml`:

```
<Object Name="barrel12782" Belong="-1" Prototype="Breakable_Barrel1"
        Pos="3383.955 371.381 3332.991" Rot="0.000 -0.507 0.000 0.862" />
```

`gamedata/gameobjects/breakableobjects.xml`:

```
Name="Breakable_Barrel1"  ModelFile="barrel1"  BrokenModel="barrel_1_brocken"
DestroyedModel="barrel_1_destroy"  Mass="20.5"  Destroyable="1"
BlastWave="smallBlastWave"  BreakEffect="ET_PS_VEH_EXP1_SMALL_11"
```

Everything the user calls "properties" is on the **prototype**; the
map record only says where an instance stands. `world.xml` cannot
express any of it — a `SgAnimatedModelNode` is a model at a
position. Assign wrote the barrel there, so the game drew a barrel
that was not a `Breakable_Barrel1`. Not one barrel on any shipped map
is a world node.

Census of top-level placed `<Object>` prototypes over the eleven
shipped maps: BreakableObject 35 262, Location 658, PhysicUnit 234,
ParticleSplinter 135, Barricade 133, Town 87, Chest 76, LightObj 39,
ObjPrefab 37, StaticAutoGun 37. 379 models are named by 547
prototypes; `barrel1` by four (Breakable_Barrel1,
Breakable_Barrel_Explosive2/3, and the Town `CrazyBase`).

### 22.2 What changed

- **Assign has two layers.** *Game Object (dynamicscene.xml)* with a
  prototype dropdown (candidates that draw the model, BreakableObject
  first) and a Belong field pre-filled from the map's own records of
  that prototype; *Static Node (world.xml)* as before. A model some
  prototype draws defaults to the dynamic layer; the world layer warns
  when it is chosen for such a model. The object is moved into the
  Dynamic collection.
- **The dynamic export writes new records.** `Object<N>` above the
  file's `LastId` and every `Object<N>` in use, unique against every
  name (the editor's own scheme on the maps it made: `t`, `mainmenu`,
  `zoo`). Pos, Rot when turned, `NodeScale` when scaled, Belong. The
  name is stamped on the object; `exm_dynamic_new` is cleared once
  the file is written, `exm_dynamic_created` never — so an export to
  a folder other than the source, twice, still writes the object on
  the second pass (MEASURED to drop it before the flag).
- **NodeScale.** 11 112 shipped records carry it — one float, 0.2 ..
  3.694, `"%.3f"` — and the SDK neither applied nor wrote it. Now
  applied on import as the object's uniform scale and written on
  export when the scale changed; untouched records keep their own
  text.
- **Rotation of Euler-mode objects — both bridges.** MEASURED:
  `rotation_mode="XYZ"`, `rotation_euler=(0,0,90°)` leaves
  `rotation_quaternion` at `(1,0,0,0)`. Both exports read that
  property, so every object created or dropped in Blender and turned
  exported unturned. `blender_io/object_rotation.py` reads the mode
  and converts (pure-Python Euler→quaternion checked against
  mathutils to 6 decimals).

### 22.3 Verified live (background Goo Engine 3.6, map `t`)

```
assign_node(layer=DYNAMIC, model_id=barrel1, prototype=Breakable_Barrel1)
  -> moved into DynamicObjects, snapped to the terrain
export_map -> +1 record, 0 lost:
  <Object Name="Object359" Belong="-1" Prototype="Breakable_Barrel1"
          Pos="148.662 261.919 150.169" Rot="-0.000 -0.707 -0.000 0.707"
          NodeScale="1.300" />
second export_map -> same record, moved; still 63 records
```

Whether the game accepts the record is what the user's run will
show; its shape is the shipped one attribute for attribute.

## 23. "The turrets disappear" — a pillbox is not a turret

Applied in version 0.51.0. The user's r1m1 export of 2026-09-19 23:50,
against the restored file: nothing lost, four records gained —

```
Object4376  Breakable_Barrel1   ok
Object4377  Breakable_Barrel1   ok
Object4378  sack_dot2           class VehiclePart
Object4379  brick_dot1          class VehiclePart
```

`sack_dot2` and `brick_dot1` are the DOT (pillbox) *parts* of the
StaticAutoGun composites (`staticAutoGun01/02/05/07`,
`sack_dot2_vulcan`, …). No shipped map places a VehiclePart at top
level (census of 11 maps: none). The palette offers the pillbox
MODEL, `prototypes_for_model` offered every prototype naming it — the
part included, first — and the game, asked for a part, showed
nothing. `exmachina.log` of that run says nothing about either
record: silently absent. ПОДТВЕРЖДЕНО for the records; that a
VehiclePart record is what the game ignores is the ГИПОТЕЗА with no
counter-example.

What a shipped turret record looks like (328 across the maps):

```
<Object Name="staticAutoGun043" Belong="1008" Prototype="staticAutoGun04"
        Pos="…" Rot="…">
    <Parts/>                      293 of 328 — the game fills the parts
</Object>                         24: CANNON+DOT listed, 7: CANNON, 4: DOT
```

Belong is the faction: r1m1's turrets carry 1008 and 1002, its
barrels -1.

### 23.1 What changed

- `core/prototype_placement.py`: `PLACEABLE_CLASSES` — the classes
  seen placed on the shipped maps (BreakableObject … StaticAutoGun,
  Vehicle, Boss*). `prototypes_for_model` offers only placeable
  prototypes and adds the composites whose MAIN part draws the model:
  `brick_dot1` → `brick_dot1_vulcan`, `staticAutoGun02`; `sack_dot2`
  → `sack_dot2_flag/kord/omega/vulcan`, `staticAutoGun01/05/07`.
  `record_children` gives a composite `("Parts",)`, a Vehicle
  `("Parts", "Repository")`.
- Assign refuses a non-placeable prototype by name and class and
  names the turrets it is the body of. A new turret's Belong defaults
  to what the map's other turrets carry (siblings of the same class),
  not the barrels' -1.
- The dynamic export writes `<Parts/>` under a new composite record,
  and rewrites Prototype/Belong of a record re-assigned in Blender
  (adding `<Parts/>` when it became a composite) — the way to repair
  the two records above without touching the file by hand.
- Export warns per top-level record naming a non-placeable
  prototype, with what to do.

### 23.2 Verified live (background Goo Engine 3.6, map `t`)

```
assign_node(prototype="brick_dot1")     -> refused: "'brick_dot1' is a
    VehiclePart … It is the body of: staticAutoGun02, brick_dot1_vulcan"
assign_node(prototype="staticAutoGun02", belong="1008") -> FINISHED
export_map ->
    <Object Name="Object359" Belong="1008" Prototype="staticAutoGun02"
            Pos="151.662 261.253 153.169">
        <Parts />
    </Object>
second export -> same record, moved, no duplicate; 0 lost
```
