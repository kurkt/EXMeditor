# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Minimal fake `bpy`/`bmesh` for testing blender_io/ logic without Blender.

NOT a real Blender emulation — just enough surface area (mesh/object
creation, bmesh vert/face building, custom properties, collection
linking) to catch structural bugs (wrong argument counts, wrong
attribute names, off-by-one loops) in blender_io/ code before it's
ever run inside real Blender. Import this module (for its side effect
of registering fake 'bpy'/'bmesh' in sys.modules) BEFORE importing
anything under blender_io/ or addon/.
"""

import os
import sys
import types


class _CustomPropsMixin:
    def __init__(self) -> None:
        self._custom: dict = {}

    def __setitem__(self, key, value):
        self._custom[key] = value

    def __getitem__(self, key):
        return self._custom[key]

    def __delitem__(self, key):
        # Real bpy: ``del obj["prop"]`` removes a custom property.
        del self._custom[key]

    def get(self, key, default=None):
        return self._custom.get(key, default)

    def __contains__(self, key) -> bool:
        """Support `"exm_class" in obj`, which real bpy objects allow
        for custom-property presence checks."""
        return key in self._custom


class FakeAttributeItem:
    """Stand-in for bpy.types.<Type>AttributeValue — the per-element
    record inside an Attribute's `.data` collection."""

    def __init__(self, value=0) -> None:
        self.value = value


class FakeAttribute:
    """Stand-in for bpy.types.Attribute (a single named custom data layer)."""

    def __init__(self, name: str, domain: str, data_type: str, size: int) -> None:
        self.name = name
        self.domain = domain
        self.data_type = data_type
        self.data = [FakeAttributeItem() for _ in range(size)]


class FakeAttributes:
    """Stand-in for bpy.types.Mesh.attributes (the generic custom-data
    layer collection). Real Blender: mesh.attributes.new(name, type,
    domain) -> Attribute, sized to the mesh's current element count for
    that domain at creation time — mirrored here via len(mesh.vertices)
    for domain='POINT', the only domain this SDK currently uses."""

    def __init__(self, mesh: "FakeMesh") -> None:
        self._mesh = mesh
        self._store: dict = {}

    def new(self, name: str, type: str, domain: str) -> FakeAttribute:  # noqa: A002 - matches real bpy signature
        size = len(self._mesh.vertices) if domain == "POINT" else 0
        attr = FakeAttribute(name, domain, type, size)
        self._store[name] = attr
        return attr

    def __contains__(self, name: str) -> bool:
        return name in self._store

    def __getitem__(self, name: str) -> FakeAttribute:
        return self._store[name]

    def get(self, name: str, default=None):
        return self._store.get(name, default)


class FakeUVLoop:
    def __init__(self) -> None:
        self.uv = (0.0, 0.0)


class _LayerData:
    """A live view of a layer's elements, exactly as bpy hands one out.

    A VIEW, not the list. Blender re-lays a mesh's custom data when a
    layer is created, and a ``.data`` collection obtained before that
    points at the old allocation: it then reads as EMPTY. Nothing
    raises — every write is simply skipped and the layer keeps its
    default value, which is how a terrain came back with a blend
    attribute of the right length, full of white, and an importer
    certain it had written to it.

    Modelled as a view precisely because the broken code did
    ``data = layer.data`` once and used it afterwards. A fake that
    returned the underlying list would keep that local working and the
    bug invisible — which it did, until this.

    Looking the layer up again by name gives a live one.
    """

    __slots__ = ("_layer",)

    def __init__(self, layer):
        self._layer = layer

    def _elements(self):
        return () if self._layer._stale else self._layer._elements

    def __len__(self):
        return len(self._elements())

    def __iter__(self):
        return iter(self._elements())

    def __getitem__(self, index):
        elements = self._elements()
        if not elements:
            raise IndexError("this layer reference is stale; look it up again")
        return elements[index]


class FakeUVLayer:
    def __init__(self, name: str, loop_count: int) -> None:
        self.name = name
        self._elements = [FakeUVLoop() for _ in range(loop_count)]
        self._stale = False
        # Real bpy exposes this; calc_tangents() uses the layer flagged
        # for render and fails without one.
        self.active_render = False

    @property
    def data(self):
        return _LayerData(self)


class FakeUVLayers:
    def __init__(self, mesh) -> None:
        self._mesh = mesh
        self._layers = []
        self.active = None
        #: Which layer Texture Paint and the viewport sample with.
        #: Real Blender makes the newest layer active, so a second one
        #: quietly takes over the display — modelled here because that
        #: is exactly how a lightmap unwrap came to be what the
        #: viewport painted through.
        self.active_index = 0

    @property
    def active_render(self):
        """Present because real Blender's collection has it.

        And it answers with a LAYER, not with "something is flagged" —
        which is what made an earlier version of ``_ensure_render_uv``
        skip every mesh and let ``calc_tangents()`` fail. Modelled
        faithfully here so that mistake cannot be made again without a
        test catching it.
        """
        return self._layers[0] if self._layers else None

    def new(self, name: str = "UVMap") -> FakeUVLayer:
        loop_count = sum(len(p.vertices) for p in self._mesh.polygons)
        _stale_everything(self._mesh)
        layer = FakeUVLayer(name, loop_count)
        self._layers.append(layer)
        # As Blender does: the newest layer becomes the active one.
        self.active = layer
        self.active_index = len(self._layers) - 1
        return layer

    def get(self, name, default=None):
        for layer in self._layers:
            if layer.name == name:
                layer._stale = False  # a fresh look-up is a live layer
                return layer
        return default

    def __iter__(self):
        return iter(self._layers)

    def __len__(self):
        return len(self._layers)

    def __getitem__(self, index):
        return self._layers[index]

    def __iter__(self):
        return iter(self._layers)


def _stale_everything(mesh) -> None:
    """Mark every layer reference already handed out for this mesh stale."""
    for collection in ("uv_layers", "color_attributes"):
        holder = getattr(mesh, collection, None)
        for layer in getattr(holder, "_layers", ()):
            layer._stale = True


class FakeColorLoop:
    def __init__(self) -> None:
        self.color = (1.0, 1.0, 1.0, 1.0)


class FakeMeshLoop:
    """mesh.loops[i] — a face corner and the vertex it uses."""

    def __init__(self, vertex_index: int, index: int) -> None:
        self.vertex_index = vertex_index
        self.index = index


class FakeColorAttribute:
    def __init__(self, name: str, loop_count: int) -> None:
        self.name = name
        self._elements = [FakeColorLoop() for _ in range(loop_count)]
        self._stale = False

    @property
    def data(self):
        return _LayerData(self)


class FakeColorAttributes:
    """mesh.color_attributes.

    Sized by domain: CORNER layers have one entry per loop, POINT
    layers one per vertex. The terrain colormap is POINT — one colour
    per heightfield sample — and a fake that always sized by loops
    would make an off-by-domain bug invisible.
    """

    def __init__(self, mesh) -> None:
        self._mesh = mesh
        self._layers = []

    def new(self, name: str, type: str = "FLOAT_COLOR", domain: str = "CORNER"):  # noqa: A002 - matches bpy
        if domain == "POINT":
            count = len(self._mesh.vertices)
        else:
            count = sum(len(p.vertices) for p in self._mesh.polygons)
        _stale_everything(self._mesh)
        layer = FakeColorAttribute(name, count)
        layer.domain = domain
        self._layers.append(layer)
        return layer

    def get(self, name, default=None):
        for layer in self._layers:
            if layer.name == name:
                layer._stale = False  # a fresh look-up is a live layer
                return layer
        return default

    def remove(self, layer):
        self._layers.remove(layer)

    def __len__(self):
        return len(self._layers)

    def __iter__(self):
        return iter(self._layers)


class FakeColorLayers:
    """bpy's mesh.vertex_colors — a named collection of colour layers."""

    def __init__(self, mesh) -> None:
        self._mesh = mesh
        self._layers = []

    def new(self, name="Col"):
        layer = types.SimpleNamespace(name=name)
        self._layers.append(layer)
        return layer

    def __len__(self):
        return len(self._layers)

    def __iter__(self):
        return iter(self._layers)


class FakeAssetTags(list):
    """bpy's AssetMetaData.tags.

    ``new`` raises on a duplicate, as Blender's does — code that adds a
    category tag twice has to survive that, and a fake that quietly
    accepted it would let the crash reach the user instead.
    """

    def new(self, name):
        if any(getattr(tag, "name", None) == name for tag in self):
            raise RuntimeError(f"tag {name!r} already exists")
        tag = types.SimpleNamespace(name=name)
        self.append(tag)
        return tag


class FakeAssetMetaData:
    def __init__(self) -> None:
        self.description = ""
        self.tags = FakeAssetTags()
        self.catalog_id = ""


class FakeMesh(_CustomPropsMixin):
    """Real bpy meshes carry custom properties; the importer stamps the
    source .gam onto one so later edits can find it again."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.vertices: list = []
        self.polygons: list = []
        self.edges: list = []
        self.attributes = FakeAttributes(self)
        self.uv_layers = FakeUVLayers(self)
        self.vertex_colors = FakeColorLayers(self)
        self.materials = []
        self.color_attributes = FakeColorAttributes(self)
        self.custom_normals = None

    def from_pydata(self, vertices, edges, faces) -> None:
        """Mirrors bpy's Mesh.from_pydata: build geometry from lists.

        Faces that repeat a set of vertices already used are dropped,
        as Blender does. Models carry plenty of these — 386 of 4345 in
        bridge_concrete — and a fake that kept them all made the loop
        desynchronisation they cause impossible to reproduce.
        """
        self.vertices = [FakeMeshVertex(FakeVector(*v), i) for i, v in enumerate(vertices)]
        kept = []
        seen = set()
        for face in faces:
            key = tuple(sorted(face))
            if len(set(face)) < len(face) or key in seen:
                continue
            seen.add(key)
            kept.append(face)
        faces = kept
        polygons = []
        loop_start = 0
        for i, f in enumerate(faces):
            polygons.append(FakeMeshPolygon(tuple(f), i, loop_start))
            loop_start += len(f)
        self.polygons = polygons
        # mesh.loops — one entry per face corner, in polygon order.
        # The importer reads UVs and colours through these rather than
        # through the face list it supplied, precisely because the two
        # stop agreeing as soon as a face is dropped.
        self.loops = [
            FakeMeshLoop(vertex_index, index)
            for index, vertex_index in enumerate(
                v for face in faces for v in face
            )
        ]
        edge_set = set()
        for face in faces:
            n = len(face)
            for i in range(n):
                a, b = face[i], face[(i + 1) % n]
                edge_set.add((min(a, b), max(a, b)))
        self.edges = [FakeMeshEdge(pair, i) for i, pair in enumerate(sorted(edge_set))]

    def normals_split_custom_set_from_vertices(self, normals) -> None:
        self.custom_normals = list(normals)

    def update(self) -> None:
        pass


class FakeModifier:
    """An object modifier.

    Modelled because a road built with Array + Curve is LIVE: editing
    the chain reshapes the road, which a mesh baked once at import
    cannot do.
    """

    def __init__(self, name: str, type: str) -> None:  # noqa: A002
        self.name = name
        self.type = type
        self.show_expanded = True
        # Array
        self.fit_type = "FIXED_COUNT"
        self.count = 2
        self.curve = None
        self.use_relative_offset = True
        self.relative_offset_displace = [1.0, 0.0, 0.0]
        self.use_merge_vertices = False
        self.merge_threshold = 0.01
        # Curve
        self.object = None
        self.deform_axis = "POS_X"


class FakeModifiers:
    def __init__(self) -> None:
        self._items: list = []

    def new(self, name: str, type: str) -> FakeModifier:  # noqa: A002
        modifier = FakeModifier(name, type)
        self._items.append(modifier)
        return modifier

    def get(self, name, default=None):
        for modifier in self._items:
            if modifier.name == name:
                return modifier
        return default

    def remove(self, modifier):
        if modifier in self._items:
            self._items.remove(modifier)

    def __iter__(self):
        return iter(list(self._items))

    def __len__(self):
        return len(self._items)

    def __getitem__(self, index):
        return self._items[index]


class FakeObject(_CustomPropsMixin):
    def __init__(self, name: str, mesh) -> None:
        super().__init__()
        self.name = name
        #: None until asset_mark(), exactly as bpy reports it.
        self.asset_data = None
        self.preview_generated = False
        self.data = mesh
        # Real bpy: an object with data=None is an Empty; anything else
        # here is a mesh object.
        if mesh is None:
            self.type = "EMPTY"
        elif isinstance(mesh, FakeCurve):
            self.type = "CURVE"
        else:
            self.type = "MESH"

        #: Modelled because a road built with Array + Curve is LIVE:
        #: editing the chain reshapes the road, which a mesh baked once
        #: at import cannot do.
        self.modifiers = FakeModifiers()

        # Transform. Real bpy exposes these as mathutils types, but
        # plain tuples are enough for the SDK code under test — it only
        # ever reads/writes them as 3- and 4-element sequences.
        self.location = (0.0, 0.0, 0.0)
        self.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)  # (w, x, y, z), as in real bpy
        self.rotation_mode = "XYZ"
        self.rotation_euler = (0.0, 0.0, 0.0)
        self.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
        self.scale = (1.0, 1.0, 1.0)

        # Empty display settings (no-ops for mesh objects).
        self.empty_display_type = "PLAIN_AXES"
        self.empty_display_size = 1.0

        self._parent = None
        self.children = ()
        self.hide_viewport = False
        self.hide_render = False
        self.color = (1.0, 1.0, 1.0, 1.0)
        self._hidden = False

    def asset_mark(self):
        """Blender marks the datablock and creates its metadata.

        The metadata appearing is the whole signal that marking took —
        ``asset_data`` is None until then — so a fake that set a flag
        instead would let code that checks the wrong thing pass.
        """
        if self.asset_data is None:
            self.asset_data = FakeAssetMetaData()

    def asset_clear(self):
        self.asset_data = None
        self.preview_generated = False

    def asset_generate_preview(self):
        """Render the thumbnail — which needs an EVALUATED object.

        Raises for an object whose every collection is excluded from
        the view layer, because Blender has no evaluated object to
        render there. Modelled because the importer excluded the
        palette before filling it, and then rendered a preview per
        model into that hole.
        """
        if self.asset_data is None:
            raise RuntimeError("cannot preview a datablock that is not an asset")
        view_layer = getattr(sys.modules.get("bpy"), "context", None)
        view_layer = getattr(view_layer, "view_layer", None)
        if view_layer is not None and getattr(view_layer, "excludes", None):
            if view_layer.excludes(self):
                raise RuntimeError(
                    "cannot render a preview: this object's collection is "
                    "excluded from the view layer"
                )
        self.preview_generated = True

    def hide_get(self) -> bool:
        return self._hidden

    def hide_set(self, value: bool) -> None:
        self._hidden = bool(value)

    @property
    def dimensions(self):
        """Bounding-box size of the object's mesh, scaled.

        Real bpy computes this from the evaluated geometry; mirrored
        here so audit code that reads it behaves the same in tests.
        """
        data = self.data
        vertices = list(getattr(data, "vertices", []) or [])
        if not vertices:
            return (0.0, 0.0, 0.0)
        xs = [v.co[0] for v in vertices]
        ys = [v.co[1] for v in vertices]
        zs = [v.co[2] for v in vertices]
        return (
            (max(xs) - min(xs)) * self.scale[0],
            (max(ys) - min(ys)) * self.scale[1],
            (max(zs) - min(zs)) * self.scale[2],
        )

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, value):
        """Setting .parent updates the parent's .children, mirroring how
        real Blender keeps the relationship navigable in both
        directions (bpy exposes Object.children as a computed tuple)."""
        if self._parent is not None:
            self._parent.children = tuple(c for c in self._parent.children if c is not self)
        self._parent = value
        if value is not None:
            value.children = tuple(value.children) + (self,)


class FakeSplinePoint:
    """Real bpy poly control points are 4D (x, y, z, w)."""

    def __init__(self) -> None:
        self.co = (0.0, 0.0, 0.0, 1.0)


class FakeSplinePoints(list):
    def add(self, count: int) -> None:
        for _ in range(count):
            self.append(FakeSplinePoint())


class FakeSpline:
    def __init__(self, spline_type: str) -> None:
        self.type = spline_type
        # Real bpy: a new spline starts with exactly one point.
        self.points = FakeSplinePoints([FakeSplinePoint()])


class FakeSplines(list):
    def new(self, spline_type: str) -> FakeSpline:
        spline = FakeSpline(spline_type)
        self.append(spline)
        return spline


class FakeCurve(_CustomPropsMixin):
    """A curve.

    Carries the width and surface fields a road ribbon needs, since a
    road drawn as a bare spline says where it goes and nothing about
    what it looks like.
    """

    def __init__(self, name: str, curve_type: str) -> None:
        super().__init__()
        self.name = name
        self.type = curve_type
        self.dimensions = "2D"
        self.splines = FakeSplines()
        self.extrude = 0.0
        self.bevel_depth = 0.0
        self.offset = 0.0
        self.fill_mode = "FULL"
        self.use_fill_caps = False
        self.bevel_object = None
        self.twist_mode = "MINIMUM"
        self.use_uv_as_generated = False
        self.materials = []


class FakeCurvesCollection:
    def new(self, name: str, type: str) -> FakeCurve:  # noqa: A002 - matches real bpy
        return FakeCurve(name, type)


class FakeImage:
    """Enough of bpy.types.Image for the texture exporter.

    save_render writes a real file, because "did anything reach disk"
    is precisely what the exporter's tests are about.

    ``size`` and ``pixels`` exist because the exporter now encodes DDS
    itself rather than handing the file to Blender, so it reads pixels
    directly. The default is a small flat image: enough for the encoder
    to produce a real, parseable file.
    """

    def __init__(self, name, filepath="", size=(4, 4), pixels=None, depth=24,
                 is_dirty=False, has_data=True):
        #: The collection this datablock lives in, once it is in one.
        #: Real Blender keeps names unique per collection and renames a
        #: newcomer that collides; without a back-reference here the
        #: fake could not do that, and code that frees a name so a
        #: replacement can take it looked like it worked when it did
        #: not.
        self._collection = None
        self._name = name
        self.filepath = filepath
        self.file_format = "PNG"
        self.depth = depth
        #: Blender sets this when an image has unsaved edits. The
        #: exporter uses it to decide between copying the file on disk
        #: and re-encoding what is in memory.
        self.is_dirty = is_dirty
        #: Blender keeps a datablock for a file it could not read.
        #: has_data is False on those.
        self.has_data = has_data
        self.size = size
        #: Set for a file Blender opens and cannot decode. Such an
        #: image never gains pixels however often it is asked, which is
        #: what separates it from one merely not loaded yet.
        self._unreadable = False
        self._pixels = (
            pixels
            if pixels is not None
            else [0.5, 0.25, 0.75, 1.0] * (size[0] * size[1])
        )

    @property
    def pixels(self):
        """Reading this makes Blender fetch the buffer.

        The laziness is the point. ``has_data`` is False both for an
        image that is broken and for one nothing has looked at yet, and
        code that treats the second as the first condemns working
        textures — so the fake has to be lazy too, or that whole class
        of bug is untestable.
        """
        if not self.has_data and not self._unreadable:
            import os as _os

            if self.filepath and _os.path.isfile(self.filepath):
                self.has_data = True
                if tuple(self.size) == (0, 0):
                    self.size = (4, 4)
                    self._pixels = [0.5, 0.25, 0.75, 1.0] * 16
        return self._pixels

    @pixels.setter
    def pixels(self, value):
        self._pixels = value

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        collection = self._collection
        if collection is None:
            self._name = value
            return
        collection._rename(self, value)

    def filepath_from_user(self):
        return self.filepath

    def pack(self):
        self.packed = True

    def reload(self):
        """Re-read from filepath. Succeeds only if the file is there
        and Blender can actually decode it."""
        import os as _os

        if self._unreadable:
            return
        if self.filepath and _os.path.isfile(self.filepath):
            self.has_data = True
            if self.size == (0, 0):
                self.size = (4, 4)

    def save_render(self, path):
        with open(path, "wb") as handle:
            handle.write(b"saved by fake_bpy")


class _FakePixels(list):
    """image.pixels — a float buffer with bpy's bulk accessor."""

    def foreach_set(self, values):
        self[:] = list(values)


class FakeImages(dict):
    """bpy.data.images.

    Iterates over IMAGES, as the real collection does — not over their
    names. A dict subclass yields keys, and code that walks every image
    looking for unsaved edits then finds strings and reports nothing to
    save, which is a silent loss of exactly the work it was written to
    protect.
    """

    def __iter__(self):
        return iter(list(self.values()))

    def get(self, name, default=None):
        return dict.get(self, name, default)

    #: Filepaths Blender opens and cannot decode. Tests put a path
    #: here to reproduce a texture that loads without error and without
    #: pixels — the shape of the real failure.
    refuse: set = set()

    def _unique(self, name, ignore=None):
        """Blender's own rule: a taken name gets a numeric suffix."""
        held = self.get(name)
        if held is None or held is ignore:
            return name
        index = 1
        while True:
            candidate = "%s.%03d" % (name, index)
            held = self.get(candidate)
            if held is None or held is ignore:
                return candidate
            index += 1

    def _register(self, image):
        image._collection = self
        image._name = self._unique(image._name)
        self[image._name] = image
        return image

    def _rename(self, image, value):
        """Rename in place, keeping the collection keyed by name.

        Assigning a name that is already taken does NOT overwrite the
        holder in Blender — the newcomer is suffixed instead. Modelled
        because the texture bridge frees a name by renaming the dead
        datablock and then expects the replacement to get it.
        """
        dict.pop(self, image._name, None)
        image._name = self._unique(value, ignore=image)
        self[image._name] = image

    def remove(self, image):
        dict.pop(self, getattr(image, "_name", None), None)
        image._collection = None

    def new(self, name, width=0, height=0, alpha=False):
        image = FakeImage(name, size=(width, height))
        image.pixels = _FakePixels([0.0] * (width * height * 4))
        image.packed = False
        return self._register(image)

    def load(self, path, check_existing=False):
        """Return a real FakeImage, not a bare namespace.

        The importer asks a loaded image for its size and whether it
        holds data, so a stand-in without those attributes made an
        image that clearly worked look broken — and the checks that
        depend on it untestable.

        ``check_existing`` matches on FILEPATH, as Blender's does, and
        hands back the datablock already pointing there. A fake that
        ignored it always produced a fresh image, which made a recovery
        path that could never escape an empty datablock look like it
        worked: the code set the dead block's filepath and then asked
        for the same path back.
        """
        if check_existing:
            for image in list(self.values()):
                if getattr(image, "filepath", None) == path:
                    return image
        if not os.path.isfile(path):
            raise RuntimeError(f"cannot read {path}")
        if path in self.refuse:
            # Blender opens a DDS whose compression it does not support
            # and returns a datablock with nothing in it. No exception:
            # the only sign is an image that never gains pixels, and
            # importing believed the absence of an error.
            image = FakeImage(
                os.path.basename(path), filepath=path, depth=32,
                size=(0, 0), pixels=[], has_data=False,
            )
            image._unreadable = True
            return self._register(image)
        image = FakeImage(os.path.basename(path), filepath=path, depth=32)
        return self._register(image)


class FakeNode:
    """A shader node.

    Image nodes expose Color and Alpha, and the Principled node exposes
    the inputs the importer connects to, so a missing link shows up as
    a failed test rather than a silently swallowed KeyError.
    """

    def __init__(self, node_type, name=None):
        self.type = node_type
        self.name = name if name is not None else node_type
        self.label = ""
        self.location = (0, 0)
        self.image = None
        if node_type == "BACKGROUND":
            self.inputs = {
                k: FakeSocket(k, self) for k in ("Color", "Strength")
            }
            self.outputs = {"Background": FakeSocket("Background", self)}
        elif node_type == "BSDF_PRINCIPLED":
            self.inputs = {
                k: FakeSocket(k, self)
                for k in ("Base Color", "Alpha", "Metallic", "Roughness", "Specular")
            }
            self.outputs = {"BSDF": FakeSocket("BSDF", self)}
        elif node_type == "MIX_RGB":
            self.blend_type = "MIX"
            self.inputs = {
                k: FakeSocket(k, self) for k in ("Fac", "Color1", "Color2")
            }
            self.outputs = {"Color": FakeSocket("Color", self)}
        elif node_type == "UVMAP":
            self.uv_map = ""
            self.inputs = {}
            self.outputs = {"UV": FakeSocket("UV", self)}
        elif node_type == "INVERT":
            self.inputs = {
                k: FakeSocket(k, self) for k in ("Fac", "Color")
            }
            self.outputs = {"Color": FakeSocket("Color", self)}
        elif node_type == "SEPARATE_COLOR":
            self.inputs = {"Color": FakeSocket("Color", self)}
            self.outputs = {
                k: FakeSocket(k, self) for k in ("Red", "Green", "Blue")
            }
        elif node_type == "VERTEX_COLOR":
            self.layer_name = ""
            self.inputs = {}
            self.outputs = {"Color": FakeSocket("Color", self), "Alpha": FakeSocket("Alpha", self)}
        else:
            self.inputs = {"Vector": FakeSocket("Vector", self)}
            self.outputs = {
                "Color": FakeSocket("Color", self),
                "Alpha": FakeSocket("Alpha", self),
            }


#: bpy's nodes.new() takes a bl_idname and the node reports a short
#: ``type``. The exporter matches on ``type``, so a fake that echoed
#: the bl_idname back made every image node invisible to it — and the
#: tests then proved nothing about a code path that never ran.
_NODE_TYPE_FROM_IDNAME = {
    "ShaderNodeTexImage": "TEX_IMAGE",
    "ShaderNodeBsdfPrincipled": "BSDF_PRINCIPLED",
    "ShaderNodeOutputMaterial": "OUTPUT_MATERIAL",
    "ShaderNodeUVMap": "UVMAP",
    "ShaderNodeVertexColor": "VERTEX_COLOR",
    "ShaderNodeMixRGB": "MIX_RGB",
    "ShaderNodeSeparateColor": "SEPARATE_COLOR",
    "ShaderNodeInvert": "INVERT",
    "ShaderNodeUVMap": "UVMAP",
}


class FakeNodes(list):
    def remove(self, node):
        list.remove(self, node)

    def new(self, node_type):
        node = FakeNode(_NODE_TYPE_FROM_IDNAME.get(node_type, node_type))
        self.append(node)
        return node

    def keys(self):
        return [n.name for n in self]

    def __contains__(self, name):
        return name in self.keys()


class FakeSocket:
    """A node socket.

    Carries its name and owning node, because bpy hands back a fresh
    wrapper on each access and the importer therefore has to compare by
    name — a fake with anonymous sockets made that impossible to test.
    """

    def __init__(self, name, node):
        self.name = name
        self.node = node
        self.default_value = 0.0


class FakeLink:
    def __init__(self, from_socket, to_socket):
        self.from_socket = from_socket
        self.to_socket = to_socket
        self.from_node = getattr(from_socket, "node", None)
        self.to_node = getattr(to_socket, "node", None)


class FakeLinks(list):
    def new(self, a, b):
        link = FakeLink(a, b)
        self.append(link)
        return link


class FakeNodeTree:
    def __init__(self):
        self.nodes = FakeNodes()
        self.links = FakeLinks()


class FakeMaterial(_CustomPropsMixin):
    """A material whose node tree appears when use_nodes is set.

    Real Blender creates an Output and a Principled node at that
    moment. Without it there is no Principled node to connect to and
    every wiring test passes vacuously.
    """

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.node_tree = FakeNodeTree()
        self.blend_method = "OPAQUE"
        self.shadow_method = "OPAQUE"
        self._use_nodes = False

    @property
    def use_nodes(self):
        return self._use_nodes

    @use_nodes.setter
    def use_nodes(self, value):
        self._use_nodes = bool(value)
        if value and not any(
            n.type == "BSDF_PRINCIPLED" for n in self.node_tree.nodes
        ):
            self.node_tree.nodes.append(FakeNode("BSDF_PRINCIPLED"))


class FakeMaterials(dict):
    """bpy.data.materials, iterating over materials rather than names."""

    def __iter__(self):
        return iter(list(self.values()))

    def new(self, name):
        material = FakeMaterial(name)
        self[name] = material
        return material

    def get(self, name, default=None):
        return dict.get(self, name, default)


class FakeMeshesCollection:
    def new(self, name: str) -> FakeMesh:
        return FakeMesh(name)


class FakeObjectsCollection:
    """bpy.data.objects.

    Iterable and indexable by name, as the real one is. Code that walks
    every object in the file — the export path does, to decide whether
    it can run in process — needs that, and a collection that only
    creates raised instead.
    """

    def __init__(self) -> None:
        self._objects: list[FakeObject] = []

    def new(self, name: str, mesh) -> FakeObject:
        obj = FakeObject(name, mesh)
        self._objects.append(obj)
        return obj

    def get(self, name, default=None):
        for obj in self._objects:
            if obj.name == name:
                return obj
        return default

    def remove(self, obj, **_kwargs):
        """Delete the datablock — and unlink it everywhere, as bpy does.

        Removing an object in Blender takes it out of every collection
        that held it; the datablock is gone, so nothing can still point
        at it. A fake that only dropped it from ``bpy.data.objects``
        left the collections holding freed objects, and code that
        clears a collection by removing its objects looked like it had
        done nothing.
        """
        if obj in self._objects:
            self._objects.remove(obj)
        for collection in getattr(self, "_collections", None) or ():
            holder = getattr(collection, "objects", None)
            linked = getattr(holder, "linked", None)
            if linked is not None and obj in linked:
                linked.remove(obj)

    def __iter__(self):
        return iter(self._objects)

    def __len__(self):
        return len(self._objects)

    def __contains__(self, item):
        return item in self._objects

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._objects[key]
        found = self.get(key)
        if found is None:
            raise KeyError(key)
        return found


class FakeViewSettings:
    """scene.view_settings — Blender 3.6 defaults to Filmic, which is
    built for photographic renders and washes out textures that are
    already authored for display."""

    def __init__(self) -> None:
        self.view_transform = "Filmic"
        self.look = "None"


class FakeLight:
    """bpy.types.Light — a lamp datablock."""

    def __init__(self, name: str, type: str = "POINT") -> None:  # noqa: A002
        self.name = name
        self.type = type
        self.color = (1.0, 1.0, 1.0)
        self.energy = 10.0
        self.angle = 0.0


class FakeLights(dict):
    def new(self, name: str, type: str = "POINT") -> FakeLight:  # noqa: A002
        light = FakeLight(name, type)
        self[name] = light
        return light

    def get(self, name, default=None):
        return dict.get(self, name, default)


class FakeWorld(_CustomPropsMixin):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.node_tree = FakeNodeTree()
        self._use_nodes = False

    @property
    def use_nodes(self):
        return self._use_nodes

    @use_nodes.setter
    def use_nodes(self, value):
        self._use_nodes = bool(value)
        if value and not any(
            n.type == "BACKGROUND" for n in self.node_tree.nodes
        ):
            self.node_tree.nodes.append(FakeNode("BACKGROUND"))


class FakeWorlds(dict):
    def new(self, name: str) -> FakeWorld:
        world = FakeWorld(name)
        self[name] = world
        return world

    def get(self, name, default=None):
        return dict.get(self, name, default)


class FakeData:
    def __init__(self) -> None:
        #: Where the .blend is saved, empty when it never has been.
        #: Blender keeps an asset library's catalogue file beside it,
        #: so "" is the difference between a catalogue tree and none.
        self.filepath = ""
        self.lights = FakeLights()
        self.worlds = FakeWorlds()
        self.meshes = FakeMeshesCollection()
        self.objects = FakeObjectsCollection()
        self.collections = FakeCollectionsCollection()
        # So removing an object can unlink it from every collection,
        # which is what Blender does and what the collections have to
        # be reachable for.
        self.objects._collections = self.collections
        self.curves = FakeCurvesCollection()
        self.images = FakeImages()
        self.materials = FakeMaterials()


class FakeCollectionObjects:
    """Real bpy exposes Collection.objects as an iterable collection with
    a .link() method — mirrored here, including iteration, which
    blender_io/addon code relies on to walk a built scene."""

    def __init__(self) -> None:
        self.linked: list = []

    def link(self, obj) -> None:
        self.linked.append(obj)

    def unlink(self, obj) -> None:
        if obj in self.linked:
            self.linked.remove(obj)

    def __iter__(self):
        return iter(self.linked)

    def __len__(self):
        return len(self.linked)

    def __contains__(self, obj):
        return obj in self.linked


class FakeCollectionChildren:
    """Real bpy exposes Collection.children as a collection supporting
    both iteration and .get(name) — mirrored here."""

    def __init__(self) -> None:
        self._items = []

    def link(self, collection) -> None:
        self._items.append(collection)

    def get(self, name, default=None):
        for c in self._items:
            if c.name == name:
                return c
        return default

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


class FakeCollection:
    def __init__(self, name: str = "Collection") -> None:
        self.name = name
        self.objects = FakeCollectionObjects()
        self.children = FakeCollectionChildren()


class FakeLayerCollection:
    """bpy's LayerCollection: a collection AS THE VIEW LAYER SEES IT.

    Two flags, and the difference between them is the whole point.

    ``exclude`` does not merely hide: Blender drops the objects from
    the depsgraph, so there is no evaluated object left to render, and
    anything that renders one (a preview, for instance) has nothing to
    work with.

    ``hide_viewport`` — the eye — hides and leaves them evaluated. That
    is what a palette wants, and modelling only ``exclude`` is what let
    the palette be torn out from under preview jobs that had not run
    yet.
    """

    def __init__(self, collection) -> None:
        self.name = collection.name
        self.collection = collection
        self.exclude = False
        self.hide_viewport = False


class FakeViewLayer:
    """bpy.context.view_layer, with just its layer-collection tree.

    The tree is built from the collections linked into the scene, so a
    collection linked after the view layer was made still appears —
    which is what Blender does and what code that links then excludes
    depends on.
    """

    def __init__(self, data) -> None:
        self._data = data
        self._layers: dict = {}

    @property
    def layer_collection(self):
        live = {c.name: c for c in self._data.collections}
        # A collection removed and remade under the same name is a
        # DIFFERENT datablock, and its layer entry has to follow it.
        # Holding the old one made the view layer answer about a
        # collection nobody could reach any more.
        for name, collection in live.items():
            held = self._layers.get(name)
            if held is None or held.collection is not collection:
                self._layers[name] = FakeLayerCollection(collection)
        for name in [n for n in self._layers if n not in live]:
            del self._layers[name]
        return types.SimpleNamespace(
            name="Scene Collection",
            children=[self._layers[n] for n in sorted(self._layers)],
        )

    def excludes(self, obj) -> bool:
        """Whether every collection holding ``obj`` is excluded."""
        self.layer_collection          # refresh against bpy.data first
        holders = [
            layer for layer in self._layers.values()
            if obj in getattr(layer.collection.objects, "linked", ())
        ]
        return bool(holders) and all(layer.exclude for layer in holders)

    def update(self) -> None:
        pass


class FakeCollectionsCollection:
    """bpy.data.collections.

    A REGISTRY, not a factory. The real one remembers what it made and
    answers ``get`` — which is how anything that keeps a named
    collection around, like the asset palette, finds the one it made
    last time instead of stacking up a new one per run. A stub that
    only built and forgot made "reuse what is there" untestable and
    ``.get`` an AttributeError waiting for Blender.
    """

    def __init__(self) -> None:
        self._store: dict = {}

    def new(self, name: str) -> FakeCollection:
        """Create one and remember it under the name given.

        NOT MODELLED: Blender suffixes a name that is already taken —
        a second import into the same file really does produce
        ``ExM_Terrain.001`` — and this hands back the plain name every
        time. Left as it is deliberately: modelling it broke 22 tests
        that create the same collection name repeatedly without
        clearing ``bpy.data`` between them, and the suffixing is a
        separate question from anything under test here. It is worth
        knowing that a re-import in Blender does not reuse the
        collection it made last time.
        """
        collection = FakeCollection(name)
        self._store[name] = collection
        return collection

    def get(self, name, default=None):
        return self._store.get(name, default)

    def remove(self, collection):
        self._store.pop(getattr(collection, "name", None), None)

    def keys(self):
        return list(self._store)

    def __getitem__(self, key):
        return self._store[key]

    def __delitem__(self, key):
        del self._store[key]

    def __contains__(self, name):
        return name in self._store

    def __iter__(self):
        return iter(list(self._store.values()))

    def __len__(self):
        return len(self._store)


class FakeLayout:
    """Minimal stand-in for bpy.types.UILayout — just enough surface
    (box/label/prop/operator) for draw() methods to run without
    crashing in tests. Records nothing; this is not meant to verify
    UI layout, only that draw() doesn't raise."""

    def box(self):
        return FakeLayout()

    def label(self, **kwargs) -> None:
        pass

    def prop(self, *args, **kwargs) -> None:
        pass

    def operator(self, *args, **kwargs) -> None:
        pass


class FakeOperator:
    bl_idname = ""
    bl_label = ""

    # Property-type markers returned by FakeProps; used to recognise
    # which class annotations are bpy properties.
    _PROP_MARKERS = ("STRING_PROP", "ENUM_PROP", "FLOAT_PROP", "BOOL_PROP", "INT_PROP")

    def __init__(self) -> None:
        self.layout = FakeLayout()
        self._apply_property_defaults()

    def _apply_property_defaults(self) -> None:
        """Initialise annotated bpy properties to their defaults.

        Real Blender does this at registration: a class annotated with
        `foo: StringProperty(...)` gets a real `foo` attribute on every
        instance. Without mirroring it here, a test could set a missing
        property by hand and pass while the add-on crashes in Blender
        with AttributeError — which is exactly what happened once.

        The add-on modules use `from __future__ import annotations`, so
        every annotation arrives as a source string rather than an
        evaluated object; each is evaluated against its defining
        module's namespace to recover the property descriptor.
        """
        import sys as _sys

        defaults = {
            "STRING_PROP": "",
            "ENUM_PROP": "",
            "FLOAT_PROP": 0.0,
            "BOOL_PROP": False,
            "INT_PROP": 0,
        }

        for klass in reversed(type(self).__mro__):
            annotations = getattr(klass, "__annotations__", {})
            if not annotations:
                continue
            namespace = getattr(_sys.modules.get(klass.__module__, None), "__dict__", {})
            for name, annotation in annotations.items():
                if isinstance(annotation, str):
                    try:
                        annotation = eval(annotation, dict(namespace))  # noqa: S307
                    except Exception:  # noqa: BLE001 - not a property annotation
                        continue
                if not (isinstance(annotation, tuple) and len(annotation) == 2):
                    continue
                marker, kwargs = annotation
                if marker not in self._PROP_MARKERS:
                    continue
                setattr(self, name, kwargs.get("default", defaults.get(marker)))

    def report(self, level_set, message) -> None:
        print(f"REPORT {level_set}: {message}")


class FakePanel:
    def __init__(self) -> None:
        self.layout = FakeLayout()


class FakeAddonPreferences(_CustomPropsMixin):
    """Stand-in for bpy.types.AddonPreferences.

    Property annotations are applied the same way FakeOperator does it,
    so a preference that was never declared fails here too rather than
    only in Blender.
    """

    _PROP_MARKERS = ("STRING_PROP", "ENUM_PROP", "FLOAT_PROP", "BOOL_PROP", "INT_PROP")

    def __init__(self) -> None:
        super().__init__()
        FakeOperator._apply_property_defaults(self)


class FakeAddonEntry:
    def __init__(self, preferences) -> None:
        self.preferences = preferences


class FakeAddons(dict):
    """bpy.context.preferences.addons — keyed by add-on module name."""


class FakePreferences:
    def __init__(self) -> None:
        self.addons = FakeAddons()


def make_preferences_context(addon_name: str, preferences) -> FakePreferences:
    """Build a preferences object exposing ``preferences`` under ``addon_name``."""
    prefs = FakePreferences()
    prefs.addons[addon_name] = FakeAddonEntry(preferences)
    return prefs


def _register_class(cls) -> None:
    pass


def _unregister_class(cls) -> None:
    pass


class FakeProps:
    @staticmethod
    def StringProperty(**kwargs):
        return ("STRING_PROP", kwargs)

    @staticmethod
    def EnumProperty(**kwargs):
        return ("ENUM_PROP", kwargs)

    @staticmethod
    def BoolProperty(**kwargs):
        return ("BOOL_PROP", kwargs)

    @staticmethod
    def IntProperty(**kwargs):
        return ("INT_PROP", kwargs)

    @staticmethod
    def FloatProperty(**kwargs):
        return ("FLOAT_PROP", kwargs)


# --- bmesh ---


class FakeVector:
    """Minimal stand-in for mathutils.Vector — supports .x/.y/.z,
    indexing, equality and iteration against a plain (x, y, z) tuple,
    which is all blender_io code and tests need.

    Indexing matters: real Vector supports v[0], and audit code reads
    coordinates that way, so omitting it would make the stub diverge
    from Blender in exactly the place being tested."""

    __slots__ = ("x", "y", "z")

    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = x
        self.y = y
        self.z = z

    def __iter__(self):
        yield self.x
        yield self.y
        yield self.z

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]

    def __len__(self) -> int:
        return 3

    def __eq__(self, other) -> bool:
        try:
            ox, oy, oz = other
        except (TypeError, ValueError):
            return NotImplemented
        return (self.x, self.y, self.z) == (ox, oy, oz)

    def __repr__(self) -> str:
        return f"FakeVector({self.x}, {self.y}, {self.z})"


class FakeBMVert:
    def __init__(self, co, index: int) -> None:
        self.co = FakeVector(*co)
        self.index = index


class FakeBMVertsSeq(list):
    def new(self, co) -> FakeBMVert:
        v = FakeBMVert(co, len(self))
        self.append(v)
        return v

    def ensure_lookup_table(self) -> None:
        pass


class FakeBMFacesSeq(list):
    def new(self, verts) -> tuple:
        f = tuple(verts)
        self.append(f)
        return f


class FakeMeshVertex:
    def __init__(self, co: FakeVector, index: int) -> None:
        self.co = co
        self.index = index


class FakeMeshPolygon:
    def __init__(self, vertex_indices: tuple, index: int, loop_start: int = 0) -> None:
        # Real bpy: polygon.vertices is an index array, NOT vertex objects.
        self.vertices = vertex_indices
        self.index = index
        # Real bpy: where this face's corners start in mesh.loops, and
        # how many there are. Code that writes a per-corner layer has to
        # go through these — walking mesh.loops and dividing by four is
        # right only while every face is a quad, and stops being right
        # the moment one is dropped as degenerate.
        self.loop_start = loop_start
        self.loop_total = len(vertex_indices)


class FakeMeshEdge:
    def __init__(self, vertex_indices: tuple, index: int) -> None:
        self.vertices = vertex_indices  # (index_a, index_b)
        self.index = index


class FakeBMesh:
    def __init__(self) -> None:
        self.verts = FakeBMVertsSeq()
        self.faces = FakeBMFacesSeq()

    def normal_update(self) -> None:
        pass

    def to_mesh(self, mesh: FakeMesh) -> None:
        # Real bpy stores mesh.vertices as MeshVertex objects (.co, .index),
        # mesh.polygons[i].vertices as an index tuple (not vertex objects),
        # and mesh.edges as MeshEdge objects (.vertices = index pair) —
        # derived from face topology, deduplicated, the same way Blender
        # derives edges from a bmesh's faces.
        mesh.vertices = [FakeMeshVertex(v.co, v.index) for v in self.verts]

        polygons = []
        edge_set: set = set()
        loop_start = 0
        for face_index, face in enumerate(self.faces):
            idxs = tuple(v.index for v in face)
            polygons.append(FakeMeshPolygon(idxs, face_index, loop_start))
            loop_start += len(idxs)
            n = len(idxs)
            for i in range(n):
                a, b = idxs[i], idxs[(i + 1) % n]
                edge_set.add((min(a, b), max(a, b)))
        mesh.polygons = polygons
        mesh.edges = [FakeMeshEdge(pair, i) for i, pair in enumerate(sorted(edge_set))]
        # ...and mesh.loops, one per face corner in polygon order. Real
        # Blender builds these too; without them a mesh made through
        # bmesh looked like it had no face corners at all, and anything
        # writing per-loop data — UVs — could not be tested on it.
        mesh.loops = [
            FakeMeshLoop(vertex_index, index)
            for index, vertex_index in enumerate(
                v for polygon in polygons for v in polygon.vertices
            )
        ]

    def free(self) -> None:
        pass


class FakeTimers:
    """bpy.app.timers: functions that return a delay to run again or
    None to stop. Nothing fires on its own here — a test calls tick()
    for each event-loop turn it wants to simulate."""

    def __init__(self) -> None:
        self.pending: list = []

    def register(self, func, first_interval=0.0, persistent=False) -> None:
        self.pending.append(func)

    def is_registered(self, func) -> bool:
        return func in self.pending

    def tick(self) -> None:
        """Run every registered timer once; drop the ones that return None."""
        keep = []
        for func in list(self.pending):
            again = func()
            if again is not None:
                keep.append(func)
        self.pending = keep


def install() -> None:
    """Register fake 'bpy' and 'bmesh' modules in sys.modules.

    Idempotent. Every test file calls this at import, and building a
    NEW module object each time silently splits the suite: a module
    that did ``import bpy`` before the latest call keeps the older
    object, so a test setting ``bpy.data.objects`` changes something
    the code under test is no longer reading. That failure looks like
    an unrelated assertion in an unrelated file and depends on import
    order, which is the worst kind to chase — one showed up the moment
    a new test file imported the add-on earlier than before.
    """
    existing = sys.modules.get("bpy")
    if existing is not None and getattr(existing, "_exm_fake", False):
        return

    bpy_module = types.ModuleType("bpy")
    bpy_module._exm_fake = True
    bpy_module.types = types.SimpleNamespace(
        Operator=FakeOperator,
        Panel=FakePanel,
        AddonPreferences=FakeAddonPreferences,
        Object=FakeObject,
        Collection=FakeCollection,
    )
    bpy_module.utils = types.SimpleNamespace(
        register_class=_register_class,
        unregister_class=_unregister_class,
    )

    # bpy.app.handlers — plain lists, which is what Blender's are. The
    # SDK registers none of its own; it takes OTHER add-ons' depsgraph
    # handlers off while previews render, because Blender fires them on
    # the preview worker thread and one of them setting an RNA property
    # crashes the process. Modelled so that suspend-and-restore is
    # testable at all.
    bpy_module.app = types.SimpleNamespace(
        handlers=types.SimpleNamespace(
            depsgraph_update_pre=[],
            depsgraph_update_post=[],
            frame_change_pre=[],
            frame_change_post=[],
            load_post=[],
            save_pre=[],
            render_init=[],
        ),
        # bpy.app.is_job_running(kind) and bpy.app.timers, modelled so
        # that "wait for the preview jobs, THEN tidy up" is testable:
        # tests set `running_jobs` and drive `timers.tick()` by hand.
        running_jobs=set(),
        timers=FakeTimers(),
    )
    bpy_module.app.is_job_running = (
        lambda kind: kind in bpy_module.app.running_jobs
    )

    # A real 'from bpy.props import StringProperty' needs bpy.props to
    # be an actual registered submodule (sys.modules["bpy.props"]), not
    # just an attribute on a plain (non-package) module object.
    props_module = types.ModuleType("bpy.props")
    props_module.StringProperty = FakeProps.StringProperty
    props_module.EnumProperty = FakeProps.EnumProperty
    props_module.FloatProperty = FakeProps.FloatProperty
    # bpy.path — only abspath is used, to resolve Blender's '//' prefix.
    path_module = types.ModuleType("bpy.path")
    path_module.abspath = lambda p, **_kw: p
    bpy_module.path = path_module
    sys.modules["bpy.path"] = path_module

    props_module.BoolProperty = FakeProps.BoolProperty
    props_module.IntProperty = FakeProps.IntProperty
    bpy_module.props = props_module

    bpy_module.data = FakeData()

    # Real bpy ALWAYS has a context. The fake had none, so any code
    # reaching for bpy.context raised AttributeError on the MODULE —
    # a different failure from "there is no scene yet", and one that
    # only ever showed up inside Blender.
    #
    # Deliberately empty: a test that needs a scene or a view layer
    # sets one on this object, and code that must cope without either
    # has to prove it does.
    data = bpy_module.data
    bpy_module.context = types.SimpleNamespace(
        scene=None, view_layer=FakeViewLayer(data), collection=None,
        preferences=types.SimpleNamespace(addons=FakeAddons()),
    )

    bmesh_module = types.ModuleType("bmesh")
    bmesh_module.new = lambda: FakeBMesh()

    sys.modules["bpy"] = bpy_module
    sys.modules["bpy.props"] = props_module
    sys.modules["bmesh"] = bmesh_module


def uninstall() -> None:
    sys.modules.pop("bpy", None)
    sys.modules.pop("bpy.props", None)
    sys.modules.pop("bmesh", None)
