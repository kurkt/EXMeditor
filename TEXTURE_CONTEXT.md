# Texture & Material Context

Notes on textures and materials in Ex Machina. Companion to
`TECHNICAL_CONTEXT.md`, which covers the project as a whole — read
that first for architecture and working principles.

Everything here is **MEASURED** (observed in a real file, log or run)
or flagged **UNCONFIRMED**. Keep that distinction; this project has
lost days to plausible reasoning presented as fact.

---

## 1. The skin chunk — fully decoded

Chunk id **15** of a `.gam`. Layout derived from record sizes and
confirmed byte-exact on five real models.

```
uint32                     leading value — NOT a record count
per material, 172 bytes + 48 * texture_count:
    float[17]              D3DMATERIAL9: diffuse, ambient, specular,
                           emissive as RGBA, then power
    uint32                 texture_count
    char[100]              shader name
    per texture, 48 bytes:
        char[44]           filename
        uint32             slot
```

How the sizes were derived:

- `teeth_top.gam` declares two materials in a 348-byte chunk → 4 + 2×172
- `civilhouse1.gam` holds three materials with one texture each in 664
  bytes → 4 + 3×220, and 220 = 172 + 48

**The leading uint32 is not the record count.** `civilhouse1` holds `1`
there and carries three material records. Reading it as a count found
only the first — one texture instead of three. Records must be walked
to the end of the chunk. This mistake was made and corrected; do not
make it again.

**`slot` is the field that matters.** Filenames alone cannot say which
image is the diffuse map and which is the bump map, so without the slot
they can only be assigned in the order they happen to appear. This is
the mechanism behind textures landing on the wrong channels, and it
bites in both directions — import and export.

Between the shader name and the texture records sit what look like
leftover runtime pointers (`0x004015b2`, `0x0253cb00`) — the
uninitialised tail of a fixed buffer in the original 2005 exporter.
`civilhouse1`'s chunk 32 contains `cc cc cc cc`, the MSVC debug fill.
Do not try to reproduce these bytes; they carry no meaning.

### Reading it

`core/gam_forensics.py` decodes this. In Blender: **Model Forensics**
in the Diagnostics panel, `Model` = the `.gam`. Output:

```
1 material(s):
  [0] shader 'bumpdiffuse_envalphagloss_spec'
      slot 0: 'metal_elements.dds'
      slot 1: 'MininAO.dds'
```

or `NO TEXTURE REFERENCES` when the material names a shader and no
image at all.

---

## 2. Shaders — MEASURED

| Shader | Seen on | Vertex format |
|---|---|---|
| `diffuse_vc` | `civilhouse1` (shipped) | 8 / stride 36 |
| `specular_vc` | `civilhouse1` (shipped) | 8 / stride 36 |
| `bump` | `big_flag01` (shipped) | 15 / stride 48 |
| `bumpdiffuse_envalphagloss_spec` | everything HTAToolchain writes | 15 / stride 48 |

**The vertex format follows the shader.** `bump` needs tangents →
15/48. `diffuse_vc` runs on vertex colour → 8/36. There is no single
"correct" format, and forcing one was a category error that cost this
project several iterations.

HTAToolchain always writes `bumpdiffuse_envalphagloss_spec` — a shader
demanding bump, environment, gloss and specular maps — and, unless the
material is set up exactly right, writes **no textures at all**. That
is an unsatisfiable request by construction, and the most likely reason
generated models look wrong even when everything else is correct.

### D3DMATERIAL9 values — an unexplored lead

| Source | diffuse | rest |
|---|---|---|
| `civilhouse1` (shipped) | 0.8, 0.8, 0.8, 1.0 | ambient 0,0,0,1; specular 0,0,0,0 |
| HTAToolchain | **all 17 floats = 1.0** | — |

Nobody has checked what a fully-white D3DMATERIAL9 does to the shading
of a generated model. UNCONFIRMED, cheap to test, and a plausible
contributor to "the material looks wrong".

---

## 3. Where the engine looks for a texture — MEASURED

From M3DEditor's own log:

```
Error: Couldn't load texture data\models\custom\mininao.dds
       for model cube1111.gam
```

The model is `data\models\custom\cube1111.gam` and the texture was
sought **in that same folder**. The skin chunk stores a bare filename,
so the folder comes from the model.

Note the lowercasing: the chunk holds `MininAO.dds`, the log reports
`mininao.dds`. Windows filesystems do not care, but a future
case-sensitive check would.

**UNCONFIRMED:** whether the engine also searches shared texture
folders when the file is not beside the model. Shipped models reference
bare names like `brick+metall.dds` and clearly resolve them from
somewhere central, so a search path probably exists — but it has not
been located.

### This install's textures are broken generally

Hundreds of these, for **shipped** vehicle models:

```
DraftModel.cpp Error: Couldn't load texture
   data\models\vehicles\small_car\color1.dds for model cab01.gam
```

The "textures are a mess / a random one shows" complaint is therefore
**not** caused by the SDK or by model transfer. It is the state of this
data set. Separate this from any SDK-side texture question before
concluding anything, or you will chase a fault that is not yours.

---

## 4. How HTAToolchain picks textures — MEASURED

It matches nodes **by node NAME**, not by node type:

```
Diffuse, Bump, Lightmap, Cube, Detail
```

Blender's default `Image Texture` is invisible to it, and the material
exports with a shader and no image. This warned to the system console
for a long time, which nobody had open, so the export "succeeded" and
the fault surfaced much later as a texture nobody chose.

`blender_io/gam_export.py::_name_texture_nodes_for_export` now renames
the node automatically for the duration of the export and puts the name
back afterwards. It acts only when there is exactly one image node and
none is already named for the exporter — with several, which one is the
diffuse map is the user's decision and guessing would silently pick
wrong.

---

## 5. What the SDK does today

### Import — `blender_io/texture_bridge.py`

Indexes every image file under the game's texture folders, resolves a
model's texture names against that index, builds Blender materials.
Reports names it could not find.

### Export — `blender_io/texture_export.py`

Called from Create Model, after the `.gam` is written:

- **Copies** the image file beside the `.gam` whenever it exists on
  disk, whatever the format — a game `.dds` arrives byte-identical.
- Writes only nodes HTAToolchain will actually read, so a roughness map
  the skin chunk never mentions is not copied.
- **Never overwrites** an existing file: a game texture already there
  is used by every other model referencing it.
- An image that exists only inside the `.blend` is saved as `.tga`
  and reported, because **Blender 3.6 cannot write `.dds` at all**.

---

## 6. Open questions, in the order worth attacking

1. **Does the engine load `.tga` for models?** Every shipped model
   references `.dds`. If Targa works, the export story is complete
   today. If not, the SDK needs a DDS writer — DXT1/DXT5 compression,
   or uncompressed A8R8G8B8 which is simpler and may be enough.
2. **What do the slot numbers mean?** Only `cube1111` carries more than
   one texture (slots 0 and 1). Two more multi-texture models would
   settle whether slot maps to the `Diffuse`/`Bump`/`Lightmap`/`Cube`/
   `Detail` order HTAToolchain uses.
3. **Do texture records follow each material, or all of them?** Only
   one sample carries textures at all, and it has a single material, so
   the two layouts are indistinguishable in the evidence available. One
   model with two textured materials answers it. `core/gam_forensics.py`
   currently assumes the first and says so.
4. **Is the all-1.0 D3DMATERIAL9 a problem?** See §2.
5. **Is there a central texture search path?** See §3.
6. **Should the SDK write `diffuse_vc` instead?** HTAToolchain's fixed
   heavy shader may simply be wrong for simple static props. Changing
   it means writing the skin chunk ourselves rather than delegating.

---

## 7. The measurement corpus

The files every claim above was measured on. Every claim above can be re-verified in a
minute with these:

| File | Why |
|---|---|
| `civilhouse1.gam` | shipped, renders, 3 materials + 3 textures, `diffuse_vc`/`specular_vc`, 8/36 |
| `big_flag01.gam` | shipped, renders, 9 materials, `bump`, 15/48 |
| `cube1111.gam` | HTAToolchain, renders, **the only model with 2 textures on one material** |
| `Cube44.gam` | HTAToolchain, no textures at all — the failure case |
| `teeth_top.gam` | HTAToolchain, 2 materials, no textures |
| `m3deditor.log` | the texture-load errors and the path the engine tries |

Plus the add-on itself, and `TECHNICAL_CONTEXT.md`.

---

## 8. Method that has actually worked here

Not advice in general — this is what produced every real answer in this
project, and what its absence cost.

1. **Compare something that works against something that does not**,
   and eliminate the agreements. Half the suspects fall away for free.
2. **Read the editor's log before theorising about the editor.** It
   names every file it opens. It was available the whole time and would
   have saved days.
3. **When a fix does not change the observed behaviour, that is
   evidence against the hypothesis** — not a reason to fix harder.
   Three consecutive rounds were lost to that.
4. **Distinguish the model from its environment.** The final blocker
   turned out to be one line in a copied map manifest pointing
   `SERVERS` at the wrong map. Everything about the model format was
   already correct.
