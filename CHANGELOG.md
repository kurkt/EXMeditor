# Changelog

Notable changes, newest first. The measurement behind each change is
in `Editor_Experiment_Findings.md` and the current state of every
format in `SDK_STATUS.md` — both in the source repository, not in the
install archive. Versions before 0.46 are summarised by range; they
predate this file.

## 0.52.0 — 2026-09-20

- Renamed **EXMeditor** (was *ExMachina SDK*); the base exception is now `EXMeditorError`
  (`ExMachinaSDKError` stays as an alias). An older install under
  the previous name is disabled automatically on first registration.
- Licensed **GPL-3.0-or-later**, © 2026 Kurkt: `LICENSE` holds the
  verbatim FSF text, every source file carries an SPDX header.
- `THIRD_PARTY.md`: the add-on bundles no third-party code; what it
  runs on, and each licence, listed and held to by tests.
- Borrowing audit against HTAToolchain, every script shipped with
  Blender 3.6 and every installed add-on: no copied code. README now
  credits HTAToolchain at its real repository (the old link was dead).
- Repository prepared for publication: `run_tests.py` runs the suite in
  one command, a CI workflow runs it and builds the archive, and the
  tests reach real game files through `EXM_CORPUS` / `EXM_GAME_ROOT`
  instead of paths that existed on one machine.
- `build_release.py`: reproducible install archive; refuses to package
  a game file. The archive is the add-on plus `README`, `CHANGELOG`,
  `THIRD_PARTY` and `LICENSE`; tests, `reverse/`, the developer
  documentation and the research tools stay in the source repository.
- Labels that clipped in the sidebar shortened: *Assign Map Object*,
  *Clear Map Object*, *Fork Texture*.
- Warning when a written model is under 4 units across
  (`MINIMUM_VISIBLE_EXTENT`); file resolution returns the on-disk
  spelling of a name.

## 0.51.0

- Turrets are placed as `StaticAutoGun` composites with their
  `<Parts/>` child, not as the pillbox part; parts are refused as
  top-level objects and export warns about unplaceable records.

## 0.50.0

- Game objects (barrels, turrets, anything with a prototype) are placed
  in `dynamicscene.xml` through *Assign Map Object*, with `NodeScale`
  and Euler rotations exported in both layers.

## 0.49.0

- Nameless dynamic records (lights, camera points, items) preserved on
  export; node names assigned once stay put; the skin-chunk texture
  record corrected to `char[40] + uv_set + slot`.

## 0.48.0

- Asset thumbnails rendered synchronously; the preview job and its
  timeouts are gone.

## 0.47.0

- Turrets and prefabs assembled from their parts on import; road and
  grass lighting taken from the lightmap.

## 0.46.0

- New maps that the original editor opens; export-folder guard; model
  shader and texture names preserved through a round trip.

## 0.15 – 0.45

- Textures (DDS read and write without an external converter), the
  model doctor, model forensics, the asset palette, ground tiles,
  water, grass, lighting.

## 0.5 – 0.14

- `.gam` import, both placement layers, roads, collision, diagnostics,
  object creation.

## 0.1 – 0.4

- Terrain round trip, `world.xml`, preferences.
