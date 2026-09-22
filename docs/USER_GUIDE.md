# EXMeditor — user guide

Editing maps, objects, models and textures of *Ex Machina* / *Hard
Truck Apocalypse* in Blender 3.6. For what the add-on is and what it
does not do yet, see [README.md](README.md); for the state of each
file format, `SDK_STATUS.md` in the source repository.

---

## 1. Setting up

1. `Edit ▸ Preferences ▸ Add-ons ▸ Install…` → pick
   `EXMeditor-<version>.zip` → enable **EXMeditor**.
2. In the add-on's preferences, set **Game Folder** to the folder that
   *contains* `data` — the game's root, not `data\models`. Without it
   objects import as empties, because nothing can be resolved to a
   model.
3. The panel is in the 3D viewport sidebar: press `N`, open the
   **EXMeditor** tab.

Writing models (*Create Model from Mesh*) additionally needs
[HTAToolchain](https://github.com/ThePlain/HTAToolchain), a separate
add-on that is not bundled here. Everything else — importing, editing,
placing, textures, export — works without it.

If an earlier build was installed under the name *ExMachina SDK*, this
one disables it on first load; remove it from `Preferences ▸ Add-ons`
when convenient.

---

## 2. The round trip

**Import Map** → edit in Blender → **Export Map**.

*Import* asks for the map's `.ssl` file or its folder. *Export* asks
for an output folder and **copies the original map folder there first,
then writes the edited files over the copy** — the map you imported
from is not touched.

Three things to keep in mind:

- **Export somewhere new**, check the result, and only then copy it
  into the game. Back up the game's own map folder before the first
  time.
- **XY Scale and Height Scale on export must match the ones used on
  import.** They are how Blender units are converted back to game
  units; a mismatch moves the whole terrain.
- **Ground Level Offset** is not part of the file format. Leave it at
  0 unless you know why you are changing it.

Export writes only what Blender actually changed. Everything the
add-on does not understand — unknown attributes, records it cannot
interpret, nameless entries — is carried through unchanged.

### What lands in the scene

| Collection | Holds |
|---|---|
| `Terrain` | the heightfield, its ground tiles and lightmap |
| `Objects` | static nodes from `world.xml` |
| `DynamicObjects` | prototype instances from `dynamicscene.xml` |
| `Roads` | road chains as curves |
| `Collision` | collision volumes |
| `ExM_Grass` | grass fields |

Move, rotate and scale objects as usual. Scale is written back as the
game's single `NodeScale` value, so use *uniform* scale; a non-uniform
one cannot be represented and only the shared factor survives.

---

## 3. Placing something new

1. **Build Asset Previews** — imports the game's models into a hidden
   collection and marks them as assets, so Blender's own Asset Browser
   shows them with thumbnails. Built once, then stored in the `.blend`.
2. Drag a model from the Asset Browser into the viewport and put it
   where you want it.
3. **Assign Map Object** — this is what makes it part of the map. The
   dialog offers two layers, and they are two different files:

   - **Game Object (`dynamicscene.xml`)** — an instance of a
     *prototype*. The prototype is what carries physics, damage and
     effects: a barrel is `Breakable_Barrel1`, a turret
     `staticAutoGun04`. When the model is one a prototype draws, the
     dialog defaults to this layer and lists the candidates.
     **Belong** is the faction: `-1` for neutral objects, `1008` /
     `1002` / `1009` for the factions turrets are given in shipped
     maps.
   - **Static Node (`world.xml`)** — a model at a position, nothing
     more: buildings, rocks, decoration. Nothing in the game can
     interact with it.

4. Export. New records are named by the map's own sequence, the same
   way the original editor names them.

**Parts of composite objects cannot be placed on their own.** A
turret's palette model is its pillbox (`brick_dot1`, `sack_dot2`); a
vehicle's is its cabin. The add-on refuses them and names the
composites they belong to — place that instead, and the game assembles
the parts.

Two more tools in the *Objects* box:

- **Replace Model** — change which model the selected objects draw,
  keeping position, rotation, scale and map data.
- **Clear Map Object** — strip the map data. The object stays in the
  Blender scene but is no longer part of the map.

To see what is available before you place anything: **Browse Assets**
lists models by category or search, with their size and how often this
map already uses them (report goes to the system console).

---

## 4. Your own models

**Create Model from Mesh** names the selected mesh, writes it as a
`.gam` and registers it in the game's model catalogue, so it can be
placed like any shipped model. Needs HTAToolchain.

- Keep a model **larger than about 4 units** across. The add-on warns
  below that: of 1 336 shipped models the median is about 11 units
  across, and a terrain cell is 8 — anything much smaller is
  effectively invisible in play.
- **Model Doctor** checks a `.gam` for the defects that separate
  generated models from shipped ones — unnormalised normals, missing
  material settings, and the like. Run it before wondering why the
  game draws nothing.

---

## 5. Textures

The game shares one texture between many models, so painting on a
texture as imported repaints everything that uses it. The order that
avoids this:

1. **Edit Texture** — gives *this* model its own copy of the texture,
   and points the model at the copy.
2. Paint in Blender as usual.
3. **Save Texture As DDS** — writes the image back in the game's
   format. Blender cannot write DDS itself; this is the add-on's own
   encoder (DXT1 for opaque images, DXT5 where there is alpha), so no
   external converter is needed.
4. **Save All Edited Textures** — the same for every image with
   unsaved changes. Blender keeps paint in memory, so unsaved work is
   lost on close without this.

**Fork Texture** is the manual version of step 1: write the selected
object's texture under a new name beside its `.gam` and re-point the
model at it.

Texture file names are stored in a 40-byte field, so keep them to
**39 characters or fewer**, extension included.

---

## 6. A new map

**Create Map** writes an empty map under `data/maps`: flat terrain,
one ground texture, no objects. Pick a name and a grid size; it can
import itself straight away. The result opens in the original editor,
which is the check that it is a real map and not just plausible files.

---

## 7. Before you export

**Validate Map** checks every object for missing models, implausible
scales and placements outside the playable area, and prints the report
to the system console (`Window ▸ Toggle System Console`). It costs
seconds and catches the mistakes that are invisible in the viewport.

---

## 8. Preferences worth knowing

| Setting | Default | What it does |
|---|---|---|
| **Game Folder** | — | the game's root; nothing resolves without it |
| Terrain Lighting | daytime | which lightmap is applied to the terrain |
| Ground Detail Tiles | on | the detail tile layer of the ground |
| Blend Ground Tiles | on | smooth transitions between ground tiles |
| Textured Roads | on | roads drawn with their textures |
| Roads From Models | off | experimental: how the engine spaces road segments is not established |
| Import Lighting | on | the map's own sun and light settings |
| Import Grass | on | grass fields |
| Import Water | on | water surface — it currently fills every basin below the water level, which is more than the original editor shows |
| Standard Colours | on | colours as the game shows them, rather than Blender's filmic view transform |
| Object Names | `4762_house1` | how imported objects are named in Blender. Readability only — export always writes the node's real name |
| Log Level | INFO | how much the add-on prints to the system console |

---

## 9. Known limits

Things the original **M3DEditor** still does and EXMeditor does not:

- creating new roads (existing ones round-trip fine);
- following a vehicle's `ParentPrototype` part chain — a placed
  vehicle shows its cabin only;
- showing a placed composite object whole before the map is
  re-imported (the game and the original editor show it at once);
- `camera_paths.xml` and `external_paths.xml`.

---

## 10. When something goes wrong

- **Objects imported as empties** — Game Folder is not set, or points
  at `data` instead of the folder containing it.
- **The game does not show a new model** — run *Model Doctor*; check
  the model is bigger than ~4 units; check the texture name length.
- **Export moved the terrain** — the scales on export did not match
  the ones used on import.
- **A texture changed on objects you did not touch** — it was shared;
  use *Edit Texture* before painting, and *Fork Texture* to undo the
  sharing for one model.
- **Nothing obvious** — set Log Level to DEBUG and read the system
  console; every operator reports what it did and why it stopped.
