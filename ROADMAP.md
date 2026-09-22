# Roadmap

What is known to be missing, in the order it is likely to matter. No
dates: each item waits on a measurement, and measurements take as long
as they take. The full list of limitations and open questions, with
the numbers behind them, is in `SDK_STATUS.md`.

## Next

- **Composite objects shown whole on placement.** A turret placed from
  the palette shows only its pillbox until the map is re-imported; the
  game and the original editor show it complete at once. The parts and
  their locators (`LP_*`) are already known from import — placement
  should assemble them the same way.
- **New roads.** There is no operator to give a new curve its roadset
  and skin; only existing roads round-trip.
- **Vehicle prototypes.** Part layouts inherited through
  `ParentPrototype` are not followed, so a placed vehicle shows its
  cabin only.

## Measurements pending

- **Prefab `RelAngle` sign** — the few non-zero shipped values have
  not been checked in the editor.
- **40-character texture names** — the skin field is 40 bytes and 39
  are written; whether the engine reads a full 40 is unproven.
- **What M3DEditor recalculates on save** — a byte-for-byte comparison
  of an untouched save has not been done.
- **Which basins the engine floods** — water currently fills every
  basin below `WATERLEVEL`; the original editor shows fewer.
- **Road segments from models** — spacing and orientation are not
  established; the preference stays off by default until they are.

## Later

- `camera_paths.xml` and `external_paths.xml`.
- Release as 1.0 once the items under *Next* are done and the round
  trip of a shipped map is byte-identical apart from what was edited.

## Not planned

- **Bundling HTAToolchain.** It stays a separate install; the `.gam`
  writer is its author's work.
- **Shipping game data**, sample maps or models from the game — see
  `.gitignore` and `build_release.py`, which refuses them.
