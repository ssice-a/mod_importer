"""
Build cross-check skinning previews for 85b15a7f from two frame sources.

Goal:
- compare "mod runtime model/data" against "original game model/data"
- generate four core previews in Blender:
  1. mod model + mod data
  2. game model + game data
  3. mod model + game data
  4. game model + mod data

Optional diagnostics:
- selected Blender export object + mod data
- direct mod ScratchPosition
- direct mod final GBuffer position

How to use:
1. Open Blender.
2. Open the Scripting workspace.
3. Load this file.
4. Optionally select the exported mesh object for the supplementary preview.
5. Run the script.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path

import bmesh
import bpy


MOD_FRAME_DIR = Path(r"E:\yh\FrameAnalysis-2026-04-27-163347")
GAME_FRAME_DIR = Path(r"E:\yh\FrameAnalysis-2026-04-27-163642")
BONESTORE_INI_PATH = Path(r"E:\yh\Mods\83527398-BoneStore.ini")
MOD_PALETTE_PATH = Path(r"E:\yh\Mods\BoneStore\Buffer\85b15a7f-115740-0-Palette.buf")

BODY_CS_HASH = "f33fea3cca2704e4"
BODY_CB0_RESOURCE_HASH = "7816b819"
BODY_DRAW_IB_HASH = "83527398"
MOD_FINAL_VS_HASH = "90e5f30bc8bfe0ae"
MOD_FINAL_PS_HASH = "041e69f919a26ea9"
BODY_GATE_CB0 = (7880, 7880, 24820, 15760, 8)
BODY_DRAW_FIRST_INDEX = 29448
BODY_DRAW_INDEX_COUNT = 115740

MOD_COMPACT_IB_PATH = Path(r"E:\yh\Mods\Buffer\85b15a7f_part00-ib.buf")
MOD_F33_POSITION_PATH = Path(r"E:\yh\Mods\Buffer\85b15a7f_part00-position.buf")
MOD_BLEND_PATH = Path(r"E:\yh\Mods\Buffer\85b15a7f_part00-blend.buf")

TARGET_OBJECT_NAME = ""
OUTPUT_COLLECTION_NAME = "modimp_pose_preview"
REPLACE_EXISTING = True
CORE_PREVIEW_SPACING = 1.5

# Yihuan profile conversion.
AXIS_SIGNS = (-1.0, -1.0, 1.0)
POSITION_SCALE = 0.01


@dataclass(frozen=True)
class ModelBundle:
    positions_game: list[tuple[float, float, float]]
    blend_indices: list[tuple[int, int, int, int]]
    blend_weights: list[tuple[int, int, int, int]]
    triangles: list[tuple[int, int, int]]
    label: str
    source_summary: str


@dataclass(frozen=True)
class DataBundle:
    local_t0_rows: list[tuple[float, float, float, float]]
    label: str
    source_summary: str


@dataclass(frozen=True)
class DispatchResolution:
    dispatch_id: int
    t0_path: Path
    t1_path: Path
    t3_path: Path
    t6_path: Path | None
    t9_path: Path | None
    cb0_path: Path


@dataclass(frozen=True)
class ModFinalResolution:
    final_vs_t4_path: Path
    dispatch: DispatchResolution


@dataclass(frozen=True)
class MeshSliceResolution:
    ib_txt_path: Path
    triangles: list[tuple[int, int, int]]
    candidate_count: int
    draw_event_id: int


@dataclass(frozen=True)
class CollectMeta:
    label: str
    expected_start: int
    expected_count: int
    global_bone_base: int
    bone_count: int


ZERO_BONE_ROWS = (
    (0.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 0.0),
)


def to_blender_position(position_game: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        AXIS_SIGNS[0] * position_game[0] * POSITION_SCALE,
        AXIS_SIGNS[1] * position_game[1] * POSITION_SCALE,
        AXIS_SIGNS[2] * position_game[2] * POSITION_SCALE,
    )


def from_blender_position(position_blender: tuple[float, float, float]) -> tuple[float, float, float]:
    inverse_scale = 1.0 / POSITION_SCALE
    return (
        AXIS_SIGNS[0] * position_blender[0] * inverse_scale,
        AXIS_SIGNS[1] * position_blender[1] * inverse_scale,
        AXIS_SIGNS[2] * position_blender[2] * inverse_scale,
    )


def parse_dispatch_id(path: Path) -> int:
    return int(path.name.split("-", 1)[0])


def try_resolve_target_object() -> bpy.types.Object | None:
    if TARGET_OBJECT_NAME:
        target = bpy.data.objects.get(TARGET_OBJECT_NAME)
        if target is None:
            raise ValueError(f"Target object not found: {TARGET_OBJECT_NAME}")
        if target.type != "MESH":
            raise ValueError(f"Target object is not a mesh: {target.name}")
        return target

    target = bpy.context.view_layer.objects.active
    if target is None or target.type != "MESH":
        return None
    return target


def ensure_collection(collection_name: str) -> bpy.types.Collection:
    scene = bpy.context.scene
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        collection = bpy.data.collections.new(collection_name)
    if collection.name not in scene.collection.children.keys():
        scene.collection.children.link(collection)
    return collection


def remove_existing_preview(object_name: str):
    existing_object = bpy.data.objects.get(object_name)
    if existing_object is None:
        return
    mesh_data = existing_object.data if existing_object.type == "MESH" else None
    bpy.data.objects.remove(existing_object, do_unlink=True)
    if mesh_data is not None and mesh_data.users == 0:
        bpy.data.meshes.remove(mesh_data, do_unlink=True)


def read_vb0_positions(path: Path) -> list[tuple[float, float, float]]:
    data = path.read_bytes()
    if not data:
        raise ValueError(f"Position buffer is empty: {path}")
    if len(data) % 12 != 0:
        raise ValueError(f"Position buffer size is not a multiple of 12 bytes: {path}")
    return [tuple(values) for values in struct.iter_unpack("<3f", data)]


def read_weight_pairs(path: Path) -> tuple[list[tuple[int, int, int, int]], list[tuple[int, int, int, int]]]:
    data = path.read_bytes()
    if not data:
        raise ValueError(f"Weight buffer is empty: {path}")
    if len(data) % 8 != 0:
        raise ValueError(f"Weight buffer size is not a multiple of 8 bytes: {path}")

    blend_indices: list[tuple[int, int, int, int]] = []
    blend_weights: list[tuple[int, int, int, int]] = []
    for packed_indices, packed_weights in struct.iter_unpack("<II", data):
        blend_indices.append(
            (
                packed_indices & 0xFF,
                (packed_indices >> 8) & 0xFF,
                (packed_indices >> 16) & 0xFF,
                (packed_indices >> 24) & 0xFF,
            )
        )
        blend_weights.append(
            (
                packed_weights & 0xFF,
                (packed_weights >> 8) & 0xFF,
                (packed_weights >> 16) & 0xFF,
                (packed_weights >> 24) & 0xFF,
            )
        )
    return blend_indices, blend_weights


def read_index_slice_txt(path: Path) -> list[tuple[int, int, int]]:
    triangles: list[tuple[int, int, int]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith(("byte offset:", "first index:", "index count:", "topology:", "format:")):
                continue
            parts = line.split()
            if len(parts) != 3:
                continue
            try:
                triangles.append((int(parts[0]), int(parts[1]), int(parts[2])))
            except ValueError:
                continue
    if not triangles:
        raise ValueError(f"No triangle data found in: {path}")
    return triangles


def read_index_slice_header(path: Path) -> tuple[int, int]:
    first_index: int | None = None
    index_count: int | None = None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip().lower()
            if line.startswith("first index:"):
                first_index = int(line.split(":", 1)[1].strip())
            elif line.startswith("index count:"):
                index_count = int(line.split(":", 1)[1].strip())
    if first_index is None or index_count is None:
        raise ValueError(f"Could not read first/index count header from: {path}")
    return first_index, index_count


def read_u16_triangle_buffer(path: Path) -> list[tuple[int, int, int]]:
    data = path.read_bytes()
    if not data:
        raise ValueError(f"Index buffer is empty: {path}")
    if len(data) % 6 != 0:
        raise ValueError(f"Index buffer size is not a multiple of 6 bytes: {path}")
    triangles: list[tuple[int, int, int]] = []
    for triangle in struct.iter_unpack("<3H", data):
        triangles.append((int(triangle[0]), int(triangle[1]), int(triangle[2])))
    return triangles


def compact_model(
    positions: list[tuple[float, float, float]],
    blend_indices: list[tuple[int, int, int, int]],
    blend_weights: list[tuple[int, int, int, int]],
    triangles: list[tuple[int, int, int]],
) -> tuple[
    list[tuple[float, float, float]],
    list[tuple[int, int, int, int]],
    list[tuple[int, int, int, int]],
    list[tuple[int, int, int]],
]:
    if len(positions) != len(blend_indices) or len(positions) != len(blend_weights):
        raise ValueError("Position/blend array length mismatch while compacting model data.")

    used_vertices = sorted({vertex_id for triangle in triangles for vertex_id in triangle})
    if not used_vertices:
        raise ValueError("Triangle list is empty while compacting model data.")
    if used_vertices[-1] >= len(positions):
        raise ValueError("Triangle indices exceed position buffer length while compacting model data.")

    remap = {old_index: new_index for new_index, old_index in enumerate(used_vertices)}
    compact_positions = [positions[vertex_id] for vertex_id in used_vertices]
    compact_blend_indices = [blend_indices[vertex_id] for vertex_id in used_vertices]
    compact_blend_weights = [blend_weights[vertex_id] for vertex_id in used_vertices]
    compact_triangles = [tuple(remap[vertex_id] for vertex_id in triangle) for triangle in triangles]
    return compact_positions, compact_blend_indices, compact_blend_weights, compact_triangles


def create_mesh_object(
    *,
    collection: bpy.types.Collection,
    object_name: str,
    positions: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
    metadata: dict[str, object] | None = None,
) -> bpy.types.Object:
    if REPLACE_EXISTING:
        remove_existing_preview(object_name)
    mesh = bpy.data.meshes.new(f"{object_name}_mesh")
    mesh.from_pydata(positions, [], triangles)
    mesh.update()
    obj = bpy.data.objects.new(object_name, mesh)
    collection.objects.link(obj)
    if metadata:
        for key, value in metadata.items():
            obj[key] = value
    return obj


def triangulated_mesh_copy(obj: bpy.types.Object) -> tuple[bpy.types.Mesh, list[str]]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated_obj = obj.evaluated_get(depsgraph)
    try:
        mesh_copy = bpy.data.meshes.new_from_object(
            evaluated_obj,
            preserve_all_data_layers=True,
            depsgraph=depsgraph,
        )
    except TypeError:
        mesh_copy = bpy.data.meshes.new_from_object(evaluated_obj, depsgraph=depsgraph)
    if mesh_copy is None:
        raise ValueError(f"{obj.name}: Blender could not create an evaluated export mesh.")

    mesh_copy.transform(evaluated_obj.matrix_world)
    mesh_copy.update()

    baked_shape_keys: list[str] = []
    shape_keys = obj.data.shape_keys
    if shape_keys is not None and getattr(shape_keys, "use_relative", True):
        key_blocks = getattr(shape_keys, "key_blocks", None)
        if key_blocks is not None and len(key_blocks) > 1:
            basis = key_blocks.get("Basis") or key_blocks[0]
            for key_block in key_blocks:
                if key_block == basis:
                    continue
                if bool(getattr(key_block, "mute", False)):
                    continue
                if abs(float(getattr(key_block, "value", 0.0))) <= 1.0e-8:
                    continue
                baked_shape_keys.append(str(key_block.name))

    if any(polygon.loop_total != 3 for polygon in mesh_copy.polygons):
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh_copy)
            bmesh.ops.triangulate(bm, faces=list(bm.faces))
            bm.to_mesh(mesh_copy)
        finally:
            bm.free()

    mesh_copy.update()
    mesh_copy.calc_loop_triangles()
    return mesh_copy, baked_shape_keys


def numeric_vertex_group_names(obj: bpy.types.Object) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for vertex_group in obj.vertex_groups:
        if vertex_group.name.isdigit():
            mapping[vertex_group.index] = int(vertex_group.name)
    return mapping


def normalized_top4_weights(
    obj: bpy.types.Object,
    mesh: bpy.types.Mesh,
) -> tuple[list[tuple[int, int, int, int]], list[tuple[int, int, int, int]], int]:
    numeric_groups = numeric_vertex_group_names(obj)
    if not numeric_groups:
        raise ValueError(f"{obj.name}: no numeric vertex groups were found.")

    per_vertex_indices: list[tuple[int, int, int, int]] = []
    per_vertex_weights: list[tuple[int, int, int, int]] = []
    max_palette_index = 0

    for vertex in mesh.vertices:
        weighted_groups: list[tuple[int, float]] = []
        for group_ref in vertex.groups:
            palette_index = numeric_groups.get(group_ref.group)
            if palette_index is None:
                continue
            if palette_index < 0 or palette_index > 0xFF:
                raise ValueError(
                    f"{obj.name}: vertex group {palette_index} does not fit uint8 BLENDINDICES."
                )
            weighted_groups.append((palette_index, float(group_ref.weight)))
            max_palette_index = max(max_palette_index, palette_index)

        weighted_groups.sort(key=lambda item: (-item[1], item[0]))
        weighted_groups = weighted_groups[:4]
        if not weighted_groups:
            per_vertex_indices.append((0, 0, 0, 0))
            per_vertex_weights.append((0, 0, 0, 0))
            continue

        total_weight = sum(weight for _, weight in weighted_groups)
        if total_weight <= 1.0e-12:
            per_vertex_indices.append((0, 0, 0, 0))
            per_vertex_weights.append((0, 0, 0, 0))
            continue

        normalized = [weight / total_weight for _, weight in weighted_groups]
        raw_values = [value * 255.0 for value in normalized]
        quantized = [int(math.floor(value)) for value in raw_values]
        remainder = 255 - sum(quantized)
        if remainder > 0:
            ranked = sorted(
                range(len(raw_values)),
                key=lambda item: (raw_values[item] - quantized[item], -item),
                reverse=True,
            )
            for item in ranked[:remainder]:
                quantized[item] += 1

        padded_indices = [palette_index for palette_index, _ in weighted_groups] + [0] * (4 - len(weighted_groups))
        padded_weights = quantized + [0] * (4 - len(quantized))
        per_vertex_indices.append(tuple(int(value) for value in padded_indices[:4]))
        per_vertex_weights.append(tuple(int(value) for value in padded_weights[:4]))

    return per_vertex_indices, per_vertex_weights, max_palette_index + 1


def read_local_t0_rows(path: Path) -> list[tuple[float, float, float, float]]:
    data = path.read_bytes()
    if not data:
        raise ValueError(f"LocalT0 buffer is empty: {path}")
    if len(data) % 16 != 0:
        raise ValueError(f"LocalT0 buffer size is not a multiple of 16 bytes: {path}")
    rows = [tuple(values) for values in struct.iter_unpack("<4f", data)]
    if len(rows) % 3 != 0:
        raise ValueError(f"LocalT0 row count is not divisible by 3: {path}")
    return rows


def read_u32_values(path: Path) -> list[int]:
    data = path.read_bytes()
    if not data:
        raise ValueError(f"UInt buffer is empty: {path}")
    if len(data) % 4 != 0:
        raise ValueError(f"UInt buffer size is not a multiple of 4 bytes: {path}")
    return [int(value[0]) for value in struct.iter_unpack("<I", data)]


def local_t0_bone_count(local_t0_rows: list[tuple[float, float, float, float]]) -> int:
    if len(local_t0_rows) % 3 != 0:
        raise ValueError("LocalT0 row list is not divisible by 3.")
    return len(local_t0_rows) // 3


def get_local_t0_bone_rows(
    local_t0_rows: list[tuple[float, float, float, float]],
    local_bone: int,
) -> tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]:
    row_base = local_bone * 3
    return (
        local_t0_rows[row_base + 0],
        local_t0_rows[row_base + 1],
        local_t0_rows[row_base + 2],
    )


def read_collect_metas(path: Path) -> list[CollectMeta]:
    section_label: str | None = None
    metas: list[CollectMeta] = []

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section_name = line[1:-1].strip()
                if section_name.startswith("ResourceCollectMeta_"):
                    section_label = section_name.removeprefix("ResourceCollectMeta_")
                else:
                    section_label = None
                continue
            if section_label is None:
                continue
            if not line.lower().startswith("data ="):
                continue
            values = [int(part) for part in line.split("=", 1)[1].split()]
            if len(values) != 4:
                raise ValueError(f"{path}: expected 4 uints in collect meta {section_label}, got {values}.")
            metas.append(
                CollectMeta(
                    label=section_label,
                    expected_start=values[0],
                    expected_count=values[1],
                    global_bone_base=values[2],
                    bone_count=values[3],
                )
            )
            section_label = None

    if not metas:
        raise ValueError(f"No collect metas found in BoneStore ini: {path}")
    return sorted(metas, key=lambda item: item.global_bone_base)


def resolve_body_collect_meta(collect_metas: list[CollectMeta]) -> CollectMeta:
    matches = [
        meta
        for meta in collect_metas
        if meta.expected_start == BODY_GATE_CB0[1] and meta.expected_count == BODY_GATE_CB0[2]
    ]
    if len(matches) != 1:
        rendered = ", ".join(meta.label for meta in matches) if matches else "none"
        raise ValueError(f"Expected exactly one body collect meta, found {len(matches)} ({rendered}).")
    return matches[0]


def select_collect_metas_for_global_bones(
    collect_metas: list[CollectMeta],
    required_global_bones: set[int],
) -> list[CollectMeta]:
    remaining = set(required_global_bones)
    selected: list[CollectMeta] = []

    for meta in collect_metas:
        covered = {
            global_bone
            for global_bone in remaining
            if meta.global_bone_base <= global_bone < meta.global_bone_base + meta.bone_count
        }
        if not covered:
            continue
        selected.append(meta)
        remaining -= covered

    if remaining:
        rendered = ", ".join(str(value) for value in sorted(remaining))
        raise ValueError(f"Collect metas do not cover required global bones: {rendered}")

    return selected


def reconstruct_global_bones_from_palette(
    local_t0_rows: list[tuple[float, float, float, float]],
    palette: list[int],
) -> dict[int, tuple[tuple[float, float, float, float], tuple[float, float, float, float], tuple[float, float, float, float]]]:
    bone_count = local_t0_bone_count(local_t0_rows)
    if bone_count != len(palette):
        raise ValueError(f"Palette/local bone count mismatch: palette has {len(palette)}, local rows have {bone_count}.")

    global_bones: dict[
        int,
        tuple[
            tuple[float, float, float, float],
            tuple[float, float, float, float],
            tuple[float, float, float, float],
        ],
    ] = {}
    for local_bone, global_bone in enumerate(palette):
        global_bones[global_bone] = get_local_t0_bone_rows(local_t0_rows, local_bone)
    return global_bones


def reconstruct_global_bones_from_contiguous_range(
    local_t0_rows: list[tuple[float, float, float, float]],
    *,
    global_bone_base: int,
    bone_count: int,
) -> dict[int, tuple[tuple[float, float, float, float], tuple[float, float, float, float], tuple[float, float, float, float]]]:
    actual_bone_count = local_t0_bone_count(local_t0_rows)
    if actual_bone_count < bone_count:
        raise ValueError(
            f"Contiguous collect source only has {actual_bone_count} bones, but {bone_count} are required from base {global_bone_base}."
        )

    global_bones: dict[
        int,
        tuple[
            tuple[float, float, float, float],
            tuple[float, float, float, float],
            tuple[float, float, float, float],
        ],
    ] = {}
    for local_bone in range(bone_count):
        global_bones[global_bone_base + local_bone] = get_local_t0_bone_rows(local_t0_rows, local_bone)
    return global_bones


def gather_local_rows_from_palette(
    global_bones: dict[int, tuple[tuple[float, float, float, float], tuple[float, float, float, float], tuple[float, float, float, float]]],
    palette: list[int],
) -> tuple[list[tuple[float, float, float, float]], list[int]]:
    gathered_rows: list[tuple[float, float, float, float]] = []
    missing_global_bones: list[int] = []

    for global_bone in palette:
        bone_rows = global_bones.get(global_bone)
        if bone_rows is None:
            missing_global_bones.append(global_bone)
            bone_rows = ZERO_BONE_ROWS
        gathered_rows.extend(bone_rows)

    return gathered_rows, missing_global_bones


def gather_local_rows_from_contiguous_range(
    global_bones: dict[int, tuple[tuple[float, float, float, float], tuple[float, float, float, float], tuple[float, float, float, float]]],
    *,
    global_bone_base: int,
    bone_count: int,
) -> tuple[list[tuple[float, float, float, float]], list[int]]:
    gathered_rows: list[tuple[float, float, float, float]] = []
    missing_global_bones: list[int] = []

    for global_bone in range(global_bone_base, global_bone_base + bone_count):
        bone_rows = global_bones.get(global_bone)
        if bone_rows is None:
            missing_global_bones.append(global_bone)
            bone_rows = ZERO_BONE_ROWS
        gathered_rows.extend(bone_rows)

    return gathered_rows, missing_global_bones


def diff_local_t0_bones(
    first_rows: list[tuple[float, float, float, float]],
    second_rows: list[tuple[float, float, float, float]],
    *,
    tolerance: float = 1.0e-6,
) -> list[int]:
    if len(first_rows) != len(second_rows):
        raise ValueError(f"Cannot diff LocalT0 rows of different lengths: {len(first_rows)} vs {len(second_rows)}.")
    if len(first_rows) % 3 != 0:
        raise ValueError("LocalT0 diff expects row counts divisible by 3.")

    differing_bones: list[int] = []
    bone_count = len(first_rows) // 3
    for bone_index in range(bone_count):
        row_base = bone_index * 3
        differs = False
        for row_offset in range(3):
            left_row = first_rows[row_base + row_offset]
            right_row = second_rows[row_base + row_offset]
            if any(abs(left_value - right_value) > tolerance for left_value, right_value in zip(left_row, right_row)):
                differs = True
                break
        if differs:
            differing_bones.append(bone_index)
    return differing_bones


def read_cb0_gate(path: Path) -> tuple[int, int, int, int, int]:
    data = path.read_bytes()
    if len(data) < 32:
        raise ValueError(f"CB0 buffer is too small to read gate values: {path}")
    values = struct.unpack("<8I", data[:32])
    return values[0], values[1], values[2], values[3], values[4]


def transform_point(
    local_t0_rows: list[tuple[float, float, float, float]],
    local_bone: int,
    position: tuple[float, float, float],
) -> tuple[float, float, float]:
    row0 = local_t0_rows[local_bone * 3 + 0]
    row1 = local_t0_rows[local_bone * 3 + 1]
    row2 = local_t0_rows[local_bone * 3 + 2]
    x_coord, y_coord, z_coord = position
    return (
        row0[0] * x_coord + row0[1] * y_coord + row0[2] * z_coord + row0[3],
        row1[0] * x_coord + row1[1] * y_coord + row1[2] * z_coord + row1[3],
        row2[0] * x_coord + row2[1] * y_coord + row2[2] * z_coord + row2[3],
    )


def apply_skin_to_positions_game(
    source_positions_game: list[tuple[float, float, float]],
    blend_indices: list[tuple[int, int, int, int]],
    blend_weights_u8: list[tuple[int, int, int, int]],
    local_t0_rows: list[tuple[float, float, float, float]],
) -> tuple[list[tuple[float, float, float]], float]:
    local_bone_count = len(local_t0_rows) // 3
    if len(source_positions_game) != len(blend_indices) or len(source_positions_game) != len(blend_weights_u8):
        raise ValueError("Position/weight count mismatch while skinning raw frame positions.")

    result_positions_game: list[tuple[float, float, float]] = []
    invalid_influences = 0.0

    for source_position_game, index_record, weight_record in zip(source_positions_game, blend_indices, blend_weights_u8):
        weights = [float(value) / 255.0 for value in weight_record]
        weight_sum = sum(weights)
        if weight_sum <= 1.0e-6:
            result_game = source_position_game
        else:
            weights = [value / weight_sum for value in weights]
            accum_x = 0.0
            accum_y = 0.0
            accum_z = 0.0
            for local_bone, weight in zip(index_record, weights):
                if weight <= 0.0:
                    continue
                if local_bone >= local_bone_count:
                    invalid_influences += weight
                    continue
                transformed = transform_point(local_t0_rows, local_bone, source_position_game)
                accum_x += transformed[0] * weight
                accum_y += transformed[1] * weight
                accum_z += transformed[2] * weight
            result_game = (accum_x, accum_y, accum_z)

        result_positions_game.append(result_game)

    return result_positions_game, invalid_influences


def apply_local_t0_skin(
    source_mesh: bpy.types.Mesh,
    blend_indices: list[tuple[int, int, int, int]],
    blend_weights_u8: list[tuple[int, int, int, int]],
    local_t0_rows: list[tuple[float, float, float, float]],
) -> tuple[list[tuple[float, float, float]], float, float]:
    local_bone_count = len(local_t0_rows) // 3
    if len(source_mesh.vertices) != len(blend_indices) or len(source_mesh.vertices) != len(blend_weights_u8):
        raise ValueError("Vertex/weight count mismatch while preview skinning.")

    result_positions: list[tuple[float, float, float]] = []
    displacement_sum = 0.0
    displacement_max = 0.0

    for vertex, index_record, weight_record in zip(source_mesh.vertices, blend_indices, blend_weights_u8):
        source_position_blender = (float(vertex.co.x), float(vertex.co.y), float(vertex.co.z))
        source_position_game = from_blender_position(source_position_blender)

        weights = [float(value) / 255.0 for value in weight_record]
        weight_sum = sum(weights)
        if weight_sum <= 1.0e-6:
            result_game = source_position_game
        else:
            weights = [value / weight_sum for value in weights]
            accum_x = 0.0
            accum_y = 0.0
            accum_z = 0.0
            for local_bone, weight in zip(index_record, weights):
                if weight <= 0.0 or local_bone >= local_bone_count:
                    continue
                transformed = transform_point(local_t0_rows, local_bone, source_position_game)
                accum_x += transformed[0] * weight
                accum_y += transformed[1] * weight
                accum_z += transformed[2] * weight
            result_game = (accum_x, accum_y, accum_z)

        result_blender = to_blender_position(result_game)
        result_positions.append(result_blender)

        dx = result_blender[0] - source_position_blender[0]
        dy = result_blender[1] - source_position_blender[1]
        dz = result_blender[2] - source_position_blender[2]
        displacement = math.sqrt(dx * dx + dy * dy + dz * dz)
        displacement_sum += displacement
        displacement_max = max(displacement_max, displacement)

    displacement_avg = displacement_sum / max(1, len(result_positions))
    return result_positions, displacement_avg, displacement_max


def find_unique_path(frame_dir: Path, pattern: str, description: str) -> Path:
    matches = sorted(frame_dir.glob(pattern))
    if len(matches) != 1:
        rendered = ", ".join(path.name for path in matches) if matches else "none"
        raise ValueError(f"{frame_dir.name}: expected exactly one {description}, found {len(matches)} ({rendered}).")
    return matches[0]


def resolve_hashed_cs_slot_path(frame_dir: Path, dispatch_id: int, slot: str) -> Path:
    return find_unique_path(
        frame_dir,
        f"{dispatch_id:06d}-cs-{slot}=*-cs={BODY_CS_HASH}.buf",
        f"dispatch {dispatch_id:06d} cs-{slot}",
    )


def resolve_plain_cs_slot_path(frame_dir: Path, dispatch_id: int, slot: str) -> Path:
    return find_unique_path(
        frame_dir,
        f"{dispatch_id:06d}-cs-{slot}-cs={BODY_CS_HASH}.buf",
        f"dispatch {dispatch_id:06d} cs-{slot}",
    )


def resolve_cb0_path(frame_dir: Path, dispatch_id: int) -> Path:
    return find_unique_path(
        frame_dir,
        f"{dispatch_id:06d}-cs-cb0=*-cs={BODY_CS_HASH}.buf",
        f"dispatch {dispatch_id:06d} cs-cb0",
    )


def build_dispatch_resolution(frame_dir: Path, dispatch_id: int, *, need_plain_slots: bool) -> DispatchResolution:
    t6_path = resolve_plain_cs_slot_path(frame_dir, dispatch_id, "t6") if need_plain_slots else None
    t9_path = resolve_plain_cs_slot_path(frame_dir, dispatch_id, "t9") if need_plain_slots else None
    return DispatchResolution(
        dispatch_id=dispatch_id,
        t0_path=resolve_hashed_cs_slot_path(frame_dir, dispatch_id, "t0"),
        t1_path=resolve_hashed_cs_slot_path(frame_dir, dispatch_id, "t1"),
        t3_path=resolve_hashed_cs_slot_path(frame_dir, dispatch_id, "t3"),
        t6_path=t6_path,
        t9_path=t9_path,
        cb0_path=resolve_cb0_path(frame_dir, dispatch_id),
    )


def resolve_mod_final_dispatch(frame_dir: Path) -> ModFinalResolution:
    final_vs_t4_path = find_unique_path(
        frame_dir,
        f"*-vs-t4-vs={MOD_FINAL_VS_HASH}-ps={MOD_FINAL_PS_HASH}.buf",
        "mod final vs-t4",
    )
    final_bytes = final_vs_t4_path.read_bytes()

    matched_dispatches: list[DispatchResolution] = []
    for t9_path in sorted(frame_dir.glob(f"*-cs-t9-cs={BODY_CS_HASH}.buf")):
        if t9_path.read_bytes() != final_bytes:
            continue
        dispatch_id = parse_dispatch_id(t9_path)
        matched_dispatches.append(build_dispatch_resolution(frame_dir, dispatch_id, need_plain_slots=True))

    if len(matched_dispatches) != 1:
        rendered = ", ".join(f"{item.dispatch_id:06d}" for item in matched_dispatches) if matched_dispatches else "none"
        raise ValueError(
            f"{frame_dir.name}: expected mod final vs-t4 to match exactly one cs-t9, found {len(matched_dispatches)} ({rendered})."
        )

    return ModFinalResolution(final_vs_t4_path=final_vs_t4_path, dispatch=matched_dispatches[0])


def resolve_game_body_dispatch(frame_dir: Path, *, before_draw_event_id: int) -> DispatchResolution:
    matches: list[DispatchResolution] = []
    for cb0_path in sorted(frame_dir.glob(f"*-cs-cb0=*-cs={BODY_CS_HASH}.buf")):
        if read_cb0_gate(cb0_path) != BODY_GATE_CB0:
            continue
        dispatch_id = parse_dispatch_id(cb0_path)
        if dispatch_id >= before_draw_event_id:
            continue
        matches.append(build_dispatch_resolution(frame_dir, dispatch_id, need_plain_slots=False))

    if not matches:
        raise ValueError(
            f"{frame_dir.name}: could not find any body dispatch with cb0={BODY_GATE_CB0} before draw {before_draw_event_id:06d}."
        )

    return max(matches, key=lambda item: item.dispatch_id)


def resolve_collect_dispatch_for_meta(
    frame_dir: Path,
    collect_meta: CollectMeta,
    *,
    before_draw_event_id: int,
) -> DispatchResolution:
    matches: list[DispatchResolution] = []
    for cb0_path in sorted(frame_dir.glob(f"*-cs-cb0=*-cs={BODY_CS_HASH}.buf")):
        dispatch_id = parse_dispatch_id(cb0_path)
        if dispatch_id >= before_draw_event_id:
            continue
        cb0_values = read_cb0_gate(cb0_path)
        primary_form = cb0_values[1] == collect_meta.expected_start and cb0_values[2] == collect_meta.expected_count
        final_form = cb0_values[2] == collect_meta.expected_start and cb0_values[3] == collect_meta.expected_count
        if not (primary_form or final_form):
            continue
        matches.append(build_dispatch_resolution(frame_dir, dispatch_id, need_plain_slots=False))

    if not matches:
        raise ValueError(
            f"{frame_dir.name}: could not resolve collect dispatch for meta {collect_meta.label} before draw {before_draw_event_id:06d}."
        )

    return max(matches, key=lambda item: item.dispatch_id)


def resolve_game_body_mesh(frame_dir: Path) -> MeshSliceResolution:
    candidates: list[tuple[Path, list[tuple[int, int, int]]]] = []
    for ib_txt_path in sorted(frame_dir.glob(f"*-ib={BODY_DRAW_IB_HASH}-*.txt")):
        try:
            first_index, index_count = read_index_slice_header(ib_txt_path)
        except ValueError:
            # Some dumped IB txt files only contain triangle rows and omit slice headers.
            # They are not useful for locating the body draw, so skip them.
            continue
        if first_index != BODY_DRAW_FIRST_INDEX or index_count != BODY_DRAW_INDEX_COUNT:
            continue
        candidates.append((ib_txt_path, read_index_slice_txt(ib_txt_path)))

    if not candidates:
        raise ValueError(
            f"{frame_dir.name}: could not find any body IB slice txt with first={BODY_DRAW_FIRST_INDEX} count={BODY_DRAW_INDEX_COUNT}."
        )

    canonical_triangles = candidates[0][1]
    differing_paths = [path for path, triangles in candidates[1:] if triangles != canonical_triangles]
    if differing_paths:
        rendered = ", ".join(path.name for path in differing_paths)
        raise ValueError(
            f"{frame_dir.name}: body IB slice candidates are not identical across draws ({rendered})."
        )

    return MeshSliceResolution(
        ib_txt_path=candidates[0][0],
        triangles=canonical_triangles,
        candidate_count=len(candidates),
        draw_event_id=parse_dispatch_id(candidates[0][0]),
    )


def build_mod_model_bundle() -> ModelBundle:
    positions_game = read_vb0_positions(MOD_F33_POSITION_PATH)
    blend_indices, blend_weights = read_weight_pairs(MOD_BLEND_PATH)
    triangles = read_u16_triangle_buffer(MOD_COMPACT_IB_PATH)
    positions_game, blend_indices, blend_weights, triangles = compact_model(
        positions_game,
        blend_indices,
        blend_weights,
        triangles,
    )
    return ModelBundle(
        positions_game=positions_game,
        blend_indices=blend_indices,
        blend_weights=blend_weights,
        triangles=triangles,
        label="mod_model",
        source_summary=(
            f"position={MOD_F33_POSITION_PATH.name}; blend={MOD_BLEND_PATH.name}; ib={MOD_COMPACT_IB_PATH.name}"
        ),
    )


def build_game_model_bundle(dispatch: DispatchResolution, mesh_slice: MeshSliceResolution) -> ModelBundle:
    positions_game = read_vb0_positions(dispatch.t3_path)
    blend_indices, blend_weights = read_weight_pairs(dispatch.t1_path)
    positions_game, blend_indices, blend_weights, triangles = compact_model(
        positions_game,
        blend_indices,
        blend_weights,
        mesh_slice.triangles,
    )
    return ModelBundle(
        positions_game=positions_game,
        blend_indices=blend_indices,
        blend_weights=blend_weights,
        triangles=triangles,
        label="game_model",
        source_summary=(
            f"dispatch={dispatch.dispatch_id:06d}; t1={dispatch.t1_path.name}; "
            f"t3={dispatch.t3_path.name}; ib={mesh_slice.ib_txt_path.name}"
        ),
    )


def build_data_bundle(path: Path, label: str) -> DataBundle:
    return DataBundle(
        local_t0_rows=read_local_t0_rows(path),
        label=label,
        source_summary=str(path),
    )


def build_game_data_from_mod_roundtrip(
    mod_data: DataBundle,
    *,
    mod_palette: list[int],
    body_collect_meta: CollectMeta,
) -> tuple[DataBundle, list[int]]:
    global_bones = reconstruct_global_bones_from_palette(mod_data.local_t0_rows, mod_palette)
    local_rows, missing_global_bones = gather_local_rows_from_contiguous_range(
        global_bones,
        global_bone_base=body_collect_meta.global_bone_base,
        bone_count=body_collect_meta.bone_count,
    )
    summary = (
        f"mod local -> global via {MOD_PALETTE_PATH.name} -> "
        f"game local [{body_collect_meta.global_bone_base}, {body_collect_meta.global_bone_base + body_collect_meta.bone_count - 1}] "
        f"(missing_global_bones={len(missing_global_bones)})"
    )
    return DataBundle(local_t0_rows=local_rows, label="mod_to_game_regathered", source_summary=summary), missing_global_bones


def build_mod_data_from_game_collect_roundtrip(
    *,
    frame_dir: Path,
    before_draw_event_id: int,
    collect_metas: list[CollectMeta],
    mod_palette: list[int],
) -> tuple[DataBundle, list[int], list[str]]:
    required_global_bones = set(mod_palette)
    selected_collect_metas = select_collect_metas_for_global_bones(collect_metas, required_global_bones)

    global_bones: dict[
        int,
        tuple[
            tuple[float, float, float, float],
            tuple[float, float, float, float],
            tuple[float, float, float, float],
        ],
    ] = {}
    collect_sources: list[str] = []

    for collect_meta in selected_collect_metas:
        dispatch = resolve_collect_dispatch_for_meta(
            frame_dir,
            collect_meta,
            before_draw_event_id=before_draw_event_id,
        )
        local_t0_rows = read_local_t0_rows(dispatch.t0_path)
        global_bones.update(
            reconstruct_global_bones_from_contiguous_range(
                local_t0_rows,
                global_bone_base=collect_meta.global_bone_base,
                bone_count=collect_meta.bone_count,
            )
        )
        collect_sources.append(f"{collect_meta.label}@{dispatch.dispatch_id:06d}")

    local_rows, missing_global_bones = gather_local_rows_from_palette(global_bones, mod_palette)
    summary = (
        "game collected -> global -> mod local via "
        + ", ".join(collect_sources)
        + f" (missing_global_bones={len(missing_global_bones)})"
    )
    return DataBundle(local_t0_rows=local_rows, label="game_to_mod_regathered", source_summary=summary), missing_global_bones, collect_sources


def build_preview_from_model_and_data(
    *,
    collection: bpy.types.Collection,
    object_name: str,
    model_bundle: ModelBundle,
    data_bundle: DataBundle,
) -> tuple[bpy.types.Object, float]:
    result_positions_game, invalid_influences = apply_skin_to_positions_game(
        model_bundle.positions_game,
        model_bundle.blend_indices,
        model_bundle.blend_weights,
        data_bundle.local_t0_rows,
    )
    positions_blender = [to_blender_position(position) for position in result_positions_game]
    obj = create_mesh_object(
        collection=collection,
        object_name=object_name,
        positions=positions_blender,
        triangles=model_bundle.triangles,
        metadata={
            "modimp_preview_kind": "crosscheck_skin",
            "modimp_model_label": model_bundle.label,
            "modimp_model_source": model_bundle.source_summary,
            "modimp_data_label": data_bundle.label,
            "modimp_data_source": data_bundle.source_summary,
            "modimp_invalid_influence_weight": float(invalid_influences),
        },
    )
    return obj, invalid_influences


def build_direct_positions_preview(
    *,
    collection: bpy.types.Collection,
    object_name: str,
    positions_game: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
    kind: str,
    source_summary: str,
) -> bpy.types.Object:
    positions_blender = [to_blender_position(position) for position in positions_game]
    return create_mesh_object(
        collection=collection,
        object_name=object_name,
        positions=positions_blender,
        triangles=triangles,
        metadata={
            "modimp_preview_kind": kind,
            "modimp_source": source_summary,
        },
    )


def build_selected_export_preview(
    *,
    collection: bpy.types.Collection,
    target_object: bpy.types.Object,
    data_bundle: DataBundle,
) -> tuple[bpy.types.Object, float, float, list[str]]:
    mesh_copy, baked_shape_keys = triangulated_mesh_copy(target_object)
    try:
        blend_indices, blend_weights_u8, local_palette_count = normalized_top4_weights(target_object, mesh_copy)
        if local_palette_count > len(data_bundle.local_t0_rows) // 3:
            raise ValueError(
                f"{target_object.name}: mesh needs {local_palette_count} local bones, but {data_bundle.label} only has "
                f"{len(data_bundle.local_t0_rows) // 3}."
            )

        result_positions, displacement_avg, displacement_max = apply_local_t0_skin(
            mesh_copy,
            blend_indices,
            blend_weights_u8,
            data_bundle.local_t0_rows,
        )
        triangles = [tuple(int(vertex_index) for vertex_index in polygon.vertices) for polygon in mesh_copy.polygons]
        preview_object = create_mesh_object(
            collection=collection,
            object_name=f"{target_object.name}_localt0_preview",
            positions=result_positions,
            triangles=triangles,
            metadata={
                "modimp_preview_kind": "selected_export_localt0",
                "modimp_data_label": data_bundle.label,
                "modimp_data_source": data_bundle.source_summary,
                "modimp_preview_source": target_object.name,
            },
        )
    finally:
        bpy.data.meshes.remove(mesh_copy, do_unlink=True)

    return preview_object, displacement_avg, displacement_max, baked_shape_keys


def layout_preview_objects(objects: list[bpy.types.Object], *, start_x: float) -> float:
    x_coord = start_x
    for obj in objects:
        obj.location = (x_coord, 0.0, 0.0)
        x_coord += CORE_PREVIEW_SPACING
    return x_coord


def main():
    target_object = try_resolve_target_object()
    base_name = target_object.name if target_object is not None else "85b15a7f_crosscheck"

    if not MOD_COMPACT_IB_PATH.is_file():
        raise ValueError(f"Missing mod compact IB buffer: {MOD_COMPACT_IB_PATH}")
    if not MOD_F33_POSITION_PATH.is_file():
        raise ValueError(f"Missing mod position buffer: {MOD_F33_POSITION_PATH}")
    if not MOD_BLEND_PATH.is_file():
        raise ValueError(f"Missing mod blend buffer: {MOD_BLEND_PATH}")
    if not MOD_PALETTE_PATH.is_file():
        raise ValueError(f"Missing mod palette buffer: {MOD_PALETTE_PATH}")
    if not BONESTORE_INI_PATH.is_file():
        raise ValueError(f"Missing BoneStore ini: {BONESTORE_INI_PATH}")

    collect_metas = read_collect_metas(BONESTORE_INI_PATH)
    body_collect_meta = resolve_body_collect_meta(collect_metas)
    mod_palette = read_u32_values(MOD_PALETTE_PATH)
    game_mesh = resolve_game_body_mesh(GAME_FRAME_DIR)
    mod_final = resolve_mod_final_dispatch(MOD_FRAME_DIR)
    mod_draw_event_id = parse_dispatch_id(mod_final.final_vs_t4_path)
    game_dispatch = resolve_game_body_dispatch(GAME_FRAME_DIR, before_draw_event_id=game_mesh.draw_event_id)

    mod_model = build_mod_model_bundle()
    mod_data = build_data_bundle(mod_final.dispatch.t6_path, "mod_data")
    game_model = build_game_model_bundle(game_dispatch, game_mesh)
    game_data = build_data_bundle(game_dispatch.t0_path, "game_data")
    mod_to_game_regathered_data, mod_to_game_missing = build_game_data_from_mod_roundtrip(
        mod_data,
        mod_palette=mod_palette,
        body_collect_meta=body_collect_meta,
    )
    game_to_mod_regathered_data, game_to_mod_missing, game_collect_sources = build_mod_data_from_game_collect_roundtrip(
        frame_dir=GAME_FRAME_DIR,
        before_draw_event_id=game_mesh.draw_event_id,
        collect_metas=collect_metas,
        mod_palette=mod_palette,
    )
    expected_mod_data_sameframe, sameframe_missing, sameframe_collect_sources = build_mod_data_from_game_collect_roundtrip(
        frame_dir=MOD_FRAME_DIR,
        before_draw_event_id=mod_draw_event_id,
        collect_metas=collect_metas,
        mod_palette=mod_palette,
    )
    sameframe_diff_bones = diff_local_t0_bones(
        mod_data.local_t0_rows,
        expected_mod_data_sameframe.local_t0_rows,
    )

    print(f"[CrossCheck] Base name: {base_name}")
    print(f"[CrossCheck] Mod final-producing dispatch: {mod_final.dispatch.dispatch_id:06d}")
    print(f"[CrossCheck] Mod final draw event: {mod_draw_event_id:06d}")
    print(f"[CrossCheck] Mod final vs-t4: {mod_final.final_vs_t4_path}")
    print(f"[CrossCheck] Mod matched cs-t9: {mod_final.dispatch.t9_path}")
    print(f"[CrossCheck] Mod LocalT0 data: {mod_final.dispatch.t6_path}")
    print(f"[CrossCheck] Game body dispatch: {game_dispatch.dispatch_id:06d}")
    print(f"[CrossCheck] Game body draw event: {game_mesh.draw_event_id:06d}")
    print(f"[CrossCheck] Game body LocalT0 data: {game_dispatch.t0_path}")
    print(f"[CrossCheck] Game body model t1: {game_dispatch.t1_path}")
    print(f"[CrossCheck] Game body model t3: {game_dispatch.t3_path}")
    print(f"[CrossCheck] Game body IB slice: {game_mesh.ib_txt_path}")
    if game_mesh.candidate_count > 1:
        print(
            f"[CrossCheck] Game body IB slice had {game_mesh.candidate_count} identical candidates; "
            f"using {game_mesh.ib_txt_path.name}."
        )
    print(f"[CrossCheck] Mod palette: {MOD_PALETTE_PATH} ({len(mod_palette)} bones)")
    print(
        f"[CrossCheck] Body collect meta: {body_collect_meta.label} "
        f"(base={body_collect_meta.global_bone_base}, bones={body_collect_meta.bone_count})"
    )
    print(
        f"[CrossCheck] mod->game regather missing global bones: {len(mod_to_game_missing)}"
        + (f" ({', '.join(str(value) for value in mod_to_game_missing[:16])})" if mod_to_game_missing else "")
    )
    print(
        f"[CrossCheck] game->mod collect sources: {', '.join(game_collect_sources)}"
    )
    print(
        f"[CrossCheck] game->mod regather missing global bones: {len(game_to_mod_missing)}"
        + (f" ({', '.join(str(value) for value in game_to_mod_missing[:16])})" if game_to_mod_missing else "")
    )
    print(
        f"[CrossCheck] same-frame expected mod collect sources: {', '.join(sameframe_collect_sources)}"
    )
    print(
        f"[CrossCheck] same-frame expected mod missing global bones: {len(sameframe_missing)}"
        + (f" ({', '.join(str(value) for value in sameframe_missing[:16])})" if sameframe_missing else "")
    )
    print(
        f"[CrossCheck] same-frame actual vs expected differing local bones: {len(sameframe_diff_bones)}"
        + (f" ({', '.join(str(value) for value in sameframe_diff_bones[:16])})" if sameframe_diff_bones else "")
    )

    collection = ensure_collection(OUTPUT_COLLECTION_NAME)

    core_specs = [
        ("mod_model_mod_data", mod_model, mod_data),
        ("mod_model_expected_sameframe", mod_model, expected_mod_data_sameframe),
        ("game_model_game_data", game_model, game_data),
        ("mod_model_game_data", mod_model, game_data),
        ("game_model_mod_data", game_model, mod_data),
        ("game_model_moddata_regathered", game_model, mod_to_game_regathered_data),
        ("mod_model_gamedata_regathered", mod_model, game_to_mod_regathered_data),
    ]

    core_objects: list[bpy.types.Object] = []
    for suffix, model_bundle, data_bundle in core_specs:
        object_name = f"{base_name}_{suffix}"
        preview_object, invalid_influences = build_preview_from_model_and_data(
            collection=collection,
            object_name=object_name,
            model_bundle=model_bundle,
            data_bundle=data_bundle,
        )
        core_objects.append(preview_object)
        print(
            f"[CrossCheck] Created core preview: {preview_object.name} "
            f"(model={model_bundle.label}, data={data_bundle.label}, invalid_influence_weight={invalid_influences:.6f})"
        )

    diagnostic_objects: list[bpy.types.Object] = []
    diagnostic_objects.append(
        build_direct_positions_preview(
            collection=collection,
            object_name=f"{base_name}_scratchposition_preview",
            positions_game=read_vb0_positions(mod_final.dispatch.t9_path),
            triangles=mod_model.triangles,
            kind="scratchposition",
            source_summary=str(mod_final.dispatch.t9_path),
        )
    )
    diagnostic_objects.append(
        build_direct_positions_preview(
            collection=collection,
            object_name=f"{base_name}_gbuffer_bound_preview",
            positions_game=read_vb0_positions(mod_final.final_vs_t4_path),
            triangles=mod_model.triangles,
            kind="gbuffer_bound_positions",
            source_summary=str(mod_final.final_vs_t4_path),
        )
    )

    selected_export_preview = None
    displacement_avg = None
    displacement_max = None
    baked_shape_keys: list[str] = []
    if target_object is not None:
        selected_export_preview, displacement_avg, displacement_max, baked_shape_keys = build_selected_export_preview(
            collection=collection,
            target_object=target_object,
            data_bundle=mod_data,
        )
        diagnostic_objects.insert(0, selected_export_preview)
        print(f"[CrossCheck] Created supplementary export preview: {selected_export_preview.name}")
        print(f"[CrossCheck] Selected export avg displacement: {displacement_avg:.6f}")
        print(f"[CrossCheck] Selected export max displacement: {displacement_max:.6f}")
        if baked_shape_keys:
            print(
                "[CrossCheck] Selected export preview baked active shape keys: "
                + ", ".join(baked_shape_keys)
            )
    else:
        print("[CrossCheck] Supplementary export preview skipped: no active Blender mesh object.")

    next_x = layout_preview_objects(core_objects, start_x=0.0)
    layout_preview_objects(diagnostic_objects, start_x=next_x)

    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    if core_objects:
        core_objects[0].select_set(True)
        bpy.context.view_layer.objects.active = core_objects[0]

    print("[CrossCheck] Core preview set complete.")
    print(f"[CrossCheck] mod model source: {mod_model.source_summary}")
    print(f"[CrossCheck] game model source: {game_model.source_summary}")
    print(f"[CrossCheck] mod data source: {mod_data.source_summary}")
    print(f"[CrossCheck] expected mod data same-frame source: {expected_mod_data_sameframe.source_summary}")
    print(f"[CrossCheck] game data source: {game_data.source_summary}")
    print(f"[CrossCheck] mod->game regathered data source: {mod_to_game_regathered_data.source_summary}")
    print(f"[CrossCheck] game->mod regathered data source: {game_to_mod_regathered_data.source_summary}")


if __name__ == "__main__":
    main()
