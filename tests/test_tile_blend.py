# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for core/tile_blend.py — the ground tile blending algorithm."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.tile_blend import (  # noqa: E402
    CORNER_BITS,
    CORNER_BOTTOM_LEFT,
    CORNER_BOTTOM_RIGHT,
    CORNER_TOP_LEFT,
    CORNER_TOP_RIGHT,
    FULL_COVERAGE,
    MASK_ATLAS_CELL_SIZE,
    MASK_ATLAS_CELLS,
    MASK_ATLAS_GRID,
    MASK_ATLAS_SIZE,
    MASK_ATLAS_VARIANTS,
    atlas_cell_uv,
    atlas_is_available,
    bilinear_weights,
    blend_cell_of_quad,
    blend_statistics,
    combination_of,
    corner_cells,
    corner_tiles,
    offset_in_blend_cell,
    pass_alphas,
    passes_for,
    plan_quad,
    rotate_mask,
)


def _split_map(side: int = 4):
    """A tile grid whose left half is tile 0 and right half tile 1."""
    return bytes(
        0 if x < side // 2 else 1
        for y in range(side)
        for x in range(side)
    )


def _corner_map():
    """2x2 grid, a different tile in each cell — reads x and y apart."""
    #  0 1
    #  2 3
    return bytes([0, 1, 2, 3])


# --- passes -------------------------------------------------------------


def test_a_patch_of_one_tile_is_a_single_pass() -> None:
    """The common case, and it must not cost a second texture."""
    passes = passes_for((7, 7, 7, 7))
    assert len(passes) == 1
    assert passes[0].tile == 7
    assert passes[0].mask == FULL_COVERAGE


def test_passes_are_ordered_by_tile_index() -> None:
    """Measured: the engine draws them ascending, not in corner order."""
    passes = passes_for((9, 2, 9, 2))
    assert [p.tile for p in passes] == [2, 9]


def test_the_first_pass_covers_the_whole_patch() -> None:
    """It is the opaque base. Its own corner mask does not survive.

    Without this the base would be drawn with holes and whatever the
    engine had in the frame buffer would show through them.
    """
    passes = passes_for((3, 8, 8, 8))
    assert passes[0].tile == 3
    assert passes[0].mask == FULL_COVERAGE
    # The later pass keeps its real mask: three corners, not four.
    assert passes[1].tile == 8
    assert passes[1].mask == (
        CORNER_TOP_RIGHT | CORNER_BOTTOM_LEFT | CORNER_BOTTOM_RIGHT
    )


def test_a_mask_names_the_corners_its_tile_actually_holds() -> None:
    passes = passes_for((5, 1, 1, 5))
    by_tile = {p.tile: p.mask for p in passes}
    assert by_tile[1] == FULL_COVERAGE  # base, widened
    assert by_tile[5] == CORNER_TOP_LEFT | CORNER_BOTTOM_RIGHT


def test_four_different_tiles_give_four_passes() -> None:
    passes = passes_for((3, 1, 2, 0))
    assert [p.tile for p in passes] == [0, 1, 2, 3]
    assert [p.mask for p in passes] == [
        FULL_COVERAGE,
        CORNER_TOP_RIGHT,
        CORNER_BOTTOM_LEFT,
        CORNER_TOP_LEFT,
    ]


# --- alpha --------------------------------------------------------------


def test_the_corner_weights_sum_to_one_everywhere() -> None:
    """What makes the base pass opaque without a special case."""
    for u in (0.0, 0.25, 0.5, 1.0):
        for v in (0.0, 0.33, 0.75, 1.0):
            assert abs(sum(bilinear_weights(u, v)) - 1.0) < 1e-9


def test_the_base_pass_is_opaque_across_the_whole_patch() -> None:
    passes = passes_for((4, 6, 6, 4))
    for u, v in ((0.0, 0.0), (0.5, 0.5), (1.0, 0.25), (0.3, 1.0)):
        assert abs(pass_alphas(passes, u, v)[0] - 1.0) < 1e-9


def test_each_corner_shows_its_own_tile_at_full_strength() -> None:
    """The one property the analytic ramp is claimed to have.

    The ramp between corners is an approximation of the mask atlas. At
    the corners it is not an approximation, and this pins that down:
    composite the passes the way the engine does — each over the last,
    by its own alpha — and the corner comes out as its own tile.
    """
    corners = (3, 8, 1, 8)
    passes = passes_for(corners)
    positions = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0))

    for corner_index, (u, v) in enumerate(positions):
        alphas = pass_alphas(passes, u, v)
        # "over" compositing, base first, exactly as the engine draws.
        shown = passes[0].tile
        for render_pass, alpha in list(zip(passes, alphas))[1:]:
            if alpha > 0.5:
                shown = render_pass.tile
        assert shown == corners[corner_index], (corner_index, u, v)


def test_a_pass_is_absent_from_the_corners_it_does_not_cover() -> None:
    passes = passes_for((0, 0, 0, 5))
    top_left = pass_alphas(passes, 0.0, 0.0)
    assert abs(top_left[1]) < 1e-9
    bottom_right = pass_alphas(passes, 1.0, 1.0)
    assert abs(bottom_right[1] - 1.0) < 1e-9


def test_the_ramp_is_partial_between_two_corners() -> None:
    """A gradient is the whole point — a step would be the chessboard."""
    passes = passes_for((0, 5, 0, 5))
    middle = pass_alphas(passes, 0.5, 0.5)[1]
    assert 0.4 < middle < 0.6


# --- the blend grid is the dual of the tile grid -------------------------


def test_the_blend_patch_is_offset_half_a_cell_from_the_tile_cell() -> None:
    """The measurement this whole module rests on.

    Four cells meet at a blend patch's centre, so the patch straddles
    the boundary between tile cells and the gradient lands ON that
    boundary. If the patch were the cell, the boundary would still be
    a hard edge and nothing would have been gained.

    Grid: 4 tile cells across, 2 quads per cell, left half tile 0 and
    right half tile 1, so the tile boundary sits at quad 4. Quads 3 and
    4 — the two either side of it — are the ones that must blend.
    """
    indices, side, per_cell = _split_map(4), 4, 2

    blending = [
        quad
        for quad in range(8)
        if len(plan_quad(quad, 0, indices, side, per_cell)[1]) > 1
    ]
    assert blending == [3, 4]


def test_a_quads_four_corners_agree_on_which_patch_they_are_in() -> None:
    """The patch comes from the quad's centre for exactly this reason.

    Taken from a corner instead, a quad on a patch boundary would put
    two of its corners in one patch and two in the next, and the two
    halves would be shaded from different tile sets.
    """
    per_cell = 2
    for quad in range(8):
        cell = blend_cell_of_quad(quad, per_cell, 4)
        left = offset_in_blend_cell(quad, cell, per_cell)
        right = offset_in_blend_cell(quad + 1, cell, per_cell)
        assert 0.0 <= left <= 1.0 and 0.0 <= right <= 1.0, quad
        assert right > left, quad


def test_the_offset_runs_a_full_ramp_across_the_blended_band() -> None:
    """0 to 1 over the two quads either side of a tile boundary."""
    per_cell = 2
    cell = blend_cell_of_quad(3, per_cell, 4)
    assert blend_cell_of_quad(4, per_cell, 4) == cell
    assert offset_in_blend_cell(3, cell, per_cell) == 0.0
    assert offset_in_blend_cell(4, cell, per_cell) == 0.5
    assert offset_in_blend_cell(5, cell, per_cell) == 1.0


# --- reading the grid ----------------------------------------------------


def test_the_corners_are_read_row_major_not_transposed() -> None:
    """A 2x2 map with four different tiles catches a swapped axis.

    ``H[y * side + x]``, the same indexing the heightfield uses. A
    transposed read swaps the top-right and bottom-left corners and
    nothing else — invisible on any symmetric fixture.
    """
    assert corner_tiles(_corner_map(), 2, 1, 1) == (0, 1, 2, 3)


def test_off_the_edge_the_border_cell_repeats() -> None:
    """The outermost patches are half outside the map."""
    assert corner_cells(0, 0, 4) == ((0, 0), (0, 0), (0, 0), (0, 0))
    assert corner_cells(4, 4, 4) == ((3, 3), (3, 3), (3, 3), (3, 3))
    # And so the map's corner patch is a single pass, not a blend
    # towards a tile that is not there.
    assert len(passes_for(corner_tiles(_corner_map(), 2, 0, 0))) == 1


def test_every_blend_patch_of_a_map_reads_cells_that_exist() -> None:
    indices, side = _split_map(8), 8
    for blend_y in range(side + 1):
        for blend_x in range(side + 1):
            for x, y in corner_cells(blend_x, blend_y, side):
                assert 0 <= x < side and 0 <= y < side
            assert corner_tiles(indices, side, blend_x, blend_y)


# --- bookkeeping ---------------------------------------------------------


def test_the_combination_key_identifies_the_material() -> None:
    """Same tiles in the same order share one material; order matters."""
    assert combination_of(passes_for((0, 0, 1, 1))) == (0, 1)
    assert combination_of(passes_for((1, 1, 0, 0))) == (0, 1)
    assert combination_of(passes_for((2, 2, 2, 2))) == (2,)


def test_the_statistics_count_every_patch_once() -> None:
    stats = blend_statistics(_split_map(4), 4)
    assert stats["patches"] == 25
    assert stats["single"] + stats["two"] + stats["three"] + stats["four"] == 25
    # One column of patches straddles the halves: blend x == 2.
    assert stats["two"] == 5
    assert stats["three"] == 0 and stats["four"] == 0


def test_a_uniform_map_reports_no_blending_at_all() -> None:
    """The check that says "this is doing nothing" out loud."""
    stats = blend_statistics(bytes(16), 4)
    assert stats["single"] == stats["patches"]


# --- the atlas that is not here ------------------------------------------


def test_the_mask_atlas_table_covers_every_mask_exactly_once() -> None:
    """What makes the table a measurement rather than a fit.

    Five shapes were read off the shipped ``mask.dds``. Closed under
    90-degree rotation they have to reach all fifteen drawn masks, once
    each, with nothing left over — and they do. A table assembled by
    eye would have no reason to come out exact.
    """
    assert MASK_ATLAS_SIZE == (512, 128)
    assert MASK_ATLAS_GRID[0] * MASK_ATLAS_GRID[1] == 16
    assert MASK_ATLAS_GRID[0] * MASK_ATLAS_CELL_SIZE == MASK_ATLAS_SIZE[0]
    assert MASK_ATLAS_GRID[1] * MASK_ATLAS_CELL_SIZE == MASK_ATLAS_SIZE[1]

    assert atlas_is_available() is True
    # Mask 0 covers no corner and is never drawn, so it has no cell.
    assert sorted(MASK_ATLAS_CELLS) == list(range(1, 16))
    for cell, rotation in MASK_ATLAS_CELLS.values():
        assert 0 <= cell < 16
        assert rotation in (0, 1, 2, 3)

    # Only the five shapes actually in the atlas are named.
    assert sorted({cell for cell, _ in MASK_ATLAS_CELLS.values()}) == [0, 1, 2, 3, 4]


def test_rotating_a_mask_four_times_returns_it() -> None:
    for mask in range(16):
        assert rotate_mask(mask, 4) == mask
        assert rotate_mask(rotate_mask(mask), 3) == mask


def test_each_mask_is_its_own_cells_shape_turned() -> None:
    """The derivation, checked rather than trusted.

    Cell 0 holds mask 15, cell 1 mask 5, cell 2 mask 7, cell 3 mask 1
    and cell 4 mask 9, measured from the file. Turning a cell shape by
    the rotation the table gives must produce the mask that asked for
    it, for all fifteen.
    """
    shape_of_cell = {0: 15, 1: 5, 2: 7, 3: 1, 4: 9}
    for mask, (cell, turns) in MASK_ATLAS_CELLS.items():
        assert rotate_mask(shape_of_cell[cell], turns) == mask, (mask, cell, turns)


def test_an_atlas_cell_maps_onto_its_own_eighth_of_the_image() -> None:
    u0, v0, u1, v1 = atlas_cell_uv(0)
    assert (u0, v0) == (0.0, 0.0)
    assert abs(u1 - 0.125) < 1e-9 and abs(v1 - 0.5) < 1e-9

    u0, v0, u1, v1 = atlas_cell_uv(15)
    assert abs(u0 - 0.875) < 1e-9 and abs(v0 - 0.5) < 1e-9
    assert (u1, v1) == (1.0, 1.0)


def test_the_three_variant_groups_are_where_they_were_measured() -> None:
    """Same four shapes at three softnesses. Which one a map uses is
    not established; the constant records the question, not an answer."""
    assert MASK_ATLAS_VARIANTS == (1, 5, 9)
    for start in MASK_ATLAS_VARIANTS:
        assert start + 3 <= 12  # all four fit before the empty cells


def test_the_corner_bits_are_the_engines_own_numbering() -> None:
    assert CORNER_BITS == (1, 2, 4, 8)
    assert (
        CORNER_TOP_LEFT
        | CORNER_TOP_RIGHT
        | CORNER_BOTTOM_LEFT
        | CORNER_BOTTOM_RIGHT
    ) == FULL_COVERAGE


# --- reading the atlas ---------------------------------------------------


def test_a_turn_reads_the_cell_from_where_the_shape_came_from() -> None:
    """One clockwise turn takes a shape's top-left to the top-right, so
    the patch's top-right has to read the cell's top-left."""
    from core.tile_blend import rotate_uv

    assert rotate_uv(0.0, 0.0, 0) == (0.0, 0.0)
    assert rotate_uv(1.0, 0.0, 1) == (0.0, 0.0)
    assert rotate_uv(1.0, 1.0, 2) == (0.0, 0.0)
    assert rotate_uv(0.0, 1.0, 3) == (0.0, 0.0)
    # Four turns is no turn.
    for u, v in ((0.0, 0.0), (0.25, 0.75), (1.0, 0.5)):
        assert rotate_uv(u, v, 4) == (u, v)


def test_the_lookup_stays_inside_its_own_cell() -> None:
    """Cells sit edge to edge in one image. A coordinate that reached
    the next one would fringe every blended patch with the wrong shape.
    """
    from core.tile_blend import MASK_ATLAS_CELLS, atlas_uv

    for mask, (cell, _turns) in MASK_ATLAS_CELLS.items():
        column, row = cell % 8, cell // 8
        for u in (0.0, 0.5, 1.0):
            for v in (0.0, 0.5, 1.0):
                across, up = atlas_uv(mask, u, v)
                # V comes back in Blender's convention, measured up.
                down = 1.0 - up
                assert column / 8 < across < (column + 1) / 8, (mask, u, v)
                assert row / 2 < down < (row + 1) / 2, (mask, u, v)


def test_the_full_cover_mask_reads_the_full_cover_cell() -> None:
    from core.tile_blend import atlas_uv

    across, up = atlas_uv(15, 0.5, 0.5)
    assert 0.0 < across < 0.125          # cell 0 is the first column
    assert 0.5 < up < 1.0                # and the TOP row, so V is high


def test_a_mask_with_no_cell_asks_for_nothing() -> None:
    """Mask 0 covers no corner and is never drawn."""
    from core.tile_blend import atlas_uv

    assert atlas_uv(0, 0.5, 0.5) == (0.0, 0.0)


def test_the_corner_of_a_patch_reads_the_matching_corner_of_the_cell() -> None:
    """Verified against the shipped file separately; this pins the
    arithmetic that got it there.

    Mask 2 is mask 1 turned once, so the patch's TOP-RIGHT must read
    cell 3's TOP-LEFT — the same texel mask 1 reads at its top-left.
    """
    from core.tile_blend import atlas_uv

    assert atlas_uv(1, 0.0, 0.0) == atlas_uv(2, 1.0, 0.0)
    assert atlas_uv(1, 0.0, 0.0) == atlas_uv(8, 1.0, 1.0)
    assert atlas_uv(1, 0.0, 0.0) == atlas_uv(4, 0.0, 1.0)
