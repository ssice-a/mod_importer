"""Collection export pipeline for the 异环 profile."""

from __future__ import annotations

import json
import math
import re
import shutil
import hashlib
from time import perf_counter
from functools import lru_cache
from pathlib import Path

import bpy
from mathutils import Vector

from .game_data import get_game_data_converter
from .io import (
    write_f32_buffer,
    write_float3_buffer,
    write_float4_buffer,
    write_half2x4_buffer,
    write_snorm8x4_pairs_buffer,
    write_u16_buffer,
    write_u32_buffer,
    write_u8x4_buffer,
    write_weight_pairs_buffer,
)
from .profiles import YIHUAN_PROFILE


_REGION_HASH_RE = re.compile(r"^[0-9A-Fa-f]{8}$")
_RESOURCE_HASH_RE = re.compile(r"^[0-9A-Fa-f]{8,16}$")
_REGION_COLLECTION_RE = re.compile(r"^(?P<hash>[0-9A-Fa-f]{8})(?:[-_](?P<count>\d+)(?:[-_](?P<first>\d+))?)?")
_PART_NAME_RE = re.compile(r"^part(?P<index>\d+)", re.IGNORECASE)
_COLLECT_KEY_RE = re.compile(r"^cs-cb0\[(?P<lane>\d+)\]$")
_COLLECT_FINISH_TERM_RE = re.compile(r"^cs-cb0\[(?P<lane>\d+)\]\s*==\s*(?P<value>\d+)$")
_COLLECTION_KIND_PROP = "modimp_kind"
_PROFILE_ID_PROP = "modimp_profile_id"
_SOURCE_IB_HASH_PROP = "modimp_source_ib_hash"
_REGION_HASH_PROP = "modimp_region_hash"
_REGION_INDEX_COUNT_PROP = "modimp_region_index_count"
_REGION_FIRST_INDEX_PROP = "modimp_region_first_index"
_PART_INDEX_PROP = "modimp_part_index"
_BONE_MERGE_MAP_TEXT_PROP = "modimp_bone_merge_map_text"
_BMC_IB_HASH_PROP = "modimp_bmc_ib_hash"
_BMC_MATCH_INDEX_COUNT_PROP = "modimp_bmc_match_index_count"
_BMC_CHUNK_INDEX_PROP = "modimp_bmc_chunk_index"
_DRAW_TOGGLE_PROP = "modimp_draw_toggle"
_DRAW_TOGGLE_KEY_PROP = "modimp_draw_toggle_key"
_MIRROR_FLIP_PROP = "modimp_mirror_flip"
_COLLECTOR_GROUP_SLOT_PROP = "modimp_collector_group_slot"
_COLLECTOR_T0_HASH_PROP = "modimp_collector_t0_hash"
_COLLECTOR_U0_HASH_PROP = "modimp_collector_u0_hash"
_COLLECTOR_U1_HASH_PROP = "modimp_collector_u1_hash"
_COLLECTOR_COLLECT_KEY_PROP = "modimp_collector_collect_key"
_COLLECTOR_FINISH_CONDITION_PROP = "modimp_collector_finish_condition"
_MATCH_VS_TEXCOORD_HASH_PROP = "modimp_match_vs_texcoord_hash"
_MATCH_VS_POSITION_HASH_PROP = "modimp_match_vs_position_hash"
_MATCH_VS_OUTLINE_HASH_PROP = "modimp_match_vs_outline_hash"
_TEXTURE_SLOTS_PROP = "modimp_texture_slots"
_SHAPE_KEY_BAKE_POLICY = "bake_current_relative_mix_to_base_mesh_copy"
_NTMI_CORE_GLOBAL_T0_RESOURCE = r"Resource\NTMIv1\RuntimeGlobalT0"
_NTMI_CORE_SKIN_COMMAND = r"CommandList\NTMIv1\SkinFromBoundSlots"
_NTMI_CORE_SKIN_SHAPEKEY_COMMAND = r"CommandList\NTMIv1\SkinWithShapekeyFromBoundSlots"
_NTMI_CORE_VERTEX_COUNT = r"$\NTMIv1\vertex_count"
_NTMI_DEFAULT_DYNAMIC_SLOTS = 16
_NTMI_SKIN_T_GLOBAL_T0 = "cs-t64"
_NTMI_SKIN_T_PALETTE = "cs-t65"
_NTMI_SKIN_T_BLEND = "cs-t66"
_NTMI_SKIN_T_FRAME = "cs-t67"
_NTMI_SKIN_T_POSITION = "cs-t68"
_NTMI_SKIN_T_SHAPEKEY_STATIC = "cs-t69"
_NTMI_SKIN_T_SHAPEKEY_RUNTIME = "cs-t70"
_NTMI_SKIN_U_NORMAL = "cs-u6"
_NTMI_SKIN_U_POSITION = "cs-u7"
_SHAPEKEY_EPSILON = 1.0e-7


def _yihuan_source_suffix(region_packages: list[dict[str, object]]) -> str:
    if region_packages:
        source_ib_hash = str(region_packages[0].get("source_ib_hash", "") or "").strip().lower()
        if _REGION_HASH_RE.fullmatch(source_ib_hash):
            return source_ib_hash
    return "shared"


def _region_override_name(package: dict[str, object]) -> str:
    region_hash = str(package.get("region_hash", "") or "").strip().lower()
    first_index = package.get("region_first_index")
    index_count = package.get("original_match_index_count")
    if first_index is None or index_count is None:
        return f"TextureOverride_IB_{region_hash}"
    return f"TextureOverride_IB_{region_hash}_{int(index_count)}_{int(first_index)}"


def _ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _build_export_logger():
    started = perf_counter()

    def log(message: str):
        print(f"[{perf_counter() - started:8.3f}s] [modimp-export] {message}")

    def flush(*, success: bool):
        status = "succeeded" if success else "failed"
        print(f"[{perf_counter() - started:8.3f}s] [modimp-export] Export {status}")

    return log, flush


def _resolve_mirror_flip_for_object(obj: bpy.types.Object, *, default_value: bool = False) -> bool:
    if _MIRROR_FLIP_PROP in obj:
        return bool(obj.get(_MIRROR_FLIP_PROP, False))
    return bool(default_value)


def _format_matrix_trace(matrix: bpy.types.Matrix) -> str:
    location = matrix.to_translation()
    scale = matrix.to_scale()
    det = matrix.to_3x3().determinant()
    return (
        f"loc=({location.x:.4f},{location.y:.4f},{location.z:.4f}) "
        f"scale=({scale.x:.4f},{scale.y:.4f},{scale.z:.4f}) "
        f"det3x3={det:.6f}"
    )


def _get_collection(collection_name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        raise ValueError(f"Collection does not exist: {collection_name}")
    return collection


def _export_object_sort_key(obj: bpy.types.Object) -> tuple[int, str]:
    try:
        slice_order = int(obj.get("modimp_slice_order", 0))
    except (TypeError, ValueError):
        slice_order = 0
    return slice_order, obj.name


def _active_constraint_labels(obj: bpy.types.Object) -> list[str]:
    labels: list[str] = []
    for constraint in getattr(obj, "constraints", []):
        if bool(getattr(constraint, "mute", False)):
            continue
        labels.append(f"{constraint.type}:{constraint.name}")
    return labels


def _optional_int_collection_prop(collection: bpy.types.Collection, key: str) -> int | None:
    if key not in collection:
        return None
    try:
        return int(collection[key])
    except (TypeError, ValueError):
        return None


def _optional_str_object_prop(obj: bpy.types.Object, key: str) -> str:
    return str(obj.get(key, "") or "").strip().lower()


def _optional_int_object_prop(obj: bpy.types.Object, key: str) -> int | None:
    if key not in obj:
        return None
    try:
        return int(obj[key])
    except (TypeError, ValueError):
        return None


def _optional_str_collection_prop(collection: bpy.types.Collection, key: str) -> str:
    return str(collection.get(key, "") or "").strip()


def _read_point_vector_attribute(mesh: bpy.types.Mesh, name: str) -> list[tuple[float, float, float]]:
    attribute = mesh.attributes.get(name)
    if attribute is None:
        raise ValueError(f"{mesh.name}: missing required point vector attribute '{name}'")
    return [tuple(item.vector) for item in attribute.data]


def _find_point_vector_attribute(mesh: bpy.types.Mesh, *names: str) -> list[tuple[float, float, float]] | None:
    for name in names:
        attribute = mesh.attributes.get(name)
        if attribute is not None:
            return [tuple(item.vector) for item in attribute.data]
    return None


def _optional_point_vector_attribute(
    mesh: bpy.types.Mesh,
    name: str,
    *,
    default: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[list[tuple[float, float, float]], bool]:
    values = _find_point_vector_attribute(mesh, name)
    if values is not None:
        return values, False
    return [default] * len(mesh.vertices), True


def _find_uv_layer(mesh: bpy.types.Mesh, *names: str) -> bpy.types.MeshUVLoopLayer | None:
    wanted = {name.casefold() for name in names if name}
    if not wanted:
        return None
    for uv_layer in mesh.uv_layers:
        if uv_layer.name.casefold() in wanted:
            return uv_layer
    return None


def _read_point_float_attribute(mesh: bpy.types.Mesh, name: str) -> list[float]:
    attribute = mesh.attributes.get(name)
    if attribute is None:
        raise ValueError(f"{mesh.name}: missing required point float attribute '{name}'")
    return [float(item.value) for item in attribute.data]


def _find_point_float_attribute(mesh: bpy.types.Mesh, *names: str) -> list[float] | None:
    for name in names:
        attribute = mesh.attributes.get(name)
        if attribute is not None:
            return [float(item.value) for item in attribute.data]
    return None


def _shape_key_group_weights(
    obj: bpy.types.Object,
    key_block: bpy.types.ShapeKey,
    vertex_count: int,
) -> list[float] | None:
    group_name = str(getattr(key_block, "vertex_group", "") or "")
    if not group_name:
        return None

    vertex_group = obj.vertex_groups.get(group_name)
    if vertex_group is None:
        return [0.0] * vertex_count

    weights: list[float] = []
    group_index = int(vertex_group.index)
    for vertex in obj.data.vertices:
        weight = 0.0
        for group_ref in vertex.groups:
            if int(group_ref.group) == group_index:
                weight = float(group_ref.weight)
                break
        weights.append(weight)
    return weights


def _active_shape_key_mix_names(obj: bpy.types.Object) -> list[str]:
    shape_keys = obj.data.shape_keys
    if shape_keys is None or not getattr(shape_keys, "use_relative", True):
        return []

    key_blocks = getattr(shape_keys, "key_blocks", None)
    if key_blocks is None or len(key_blocks) <= 1:
        return []

    basis = key_blocks.get("Basis") or key_blocks[0]
    baked_names: list[str] = []
    for key_block in key_blocks:
        if key_block == basis:
            continue
        if bool(getattr(key_block, "mute", False)):
            continue
        value = float(getattr(key_block, "value", 0.0))
        if abs(value) <= 1e-8:
            continue
        baked_names.append(str(key_block.name))
    return baked_names


def _bake_current_shape_key_mix(mesh_copy: bpy.types.Mesh, obj: bpy.types.Object) -> list[str]:
    shape_keys = obj.data.shape_keys
    if shape_keys is None or not getattr(shape_keys, "use_relative", True):
        return []

    key_blocks = getattr(shape_keys, "key_blocks", None)
    if key_blocks is None or len(key_blocks) <= 1:
        return []

    basis = key_blocks.get("Basis") or key_blocks[0]
    vertex_count = len(mesh_copy.vertices)
    if len(basis.data) != vertex_count:
        return []

    mixed_coords = [basis.data[index].co.copy() for index in range(vertex_count)]
    baked_names: list[str] = []

    for key_block in key_blocks:
        if key_block == basis:
            continue
        if bool(getattr(key_block, "mute", False)):
            continue

        value = float(getattr(key_block, "value", 0.0))
        if abs(value) <= 1e-8:
            continue
        if len(key_block.data) != vertex_count:
            continue

        relative_key = getattr(key_block, "relative_key", None) or basis
        if len(relative_key.data) != vertex_count:
            relative_key = basis

        group_weights = _shape_key_group_weights(obj, key_block, vertex_count)
        changed = False
        for vertex_index in range(vertex_count):
            influence = value
            if group_weights is not None:
                influence *= group_weights[vertex_index]
            if abs(influence) <= 1e-8:
                continue
            delta = key_block.data[vertex_index].co - relative_key.data[vertex_index].co
            if delta.length <= 1e-12:
                continue
            mixed_coords[vertex_index] = mixed_coords[vertex_index] + delta * influence
            changed = True
        if changed:
            baked_names.append(str(key_block.name))

    if not baked_names:
        return []

    for vertex, baked_co in zip(mesh_copy.vertices, mixed_coords):
        vertex.co = Vector((float(baked_co.x), float(baked_co.y), float(baked_co.z)))
    mesh_copy.update()
    return baked_names


def _evaluated_triangulated_mesh_copy(
    obj: bpy.types.Object,
    *,
    depsgraph: bpy.types.Depsgraph | None = None,
) -> bpy.types.Mesh:
    import bmesh

    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
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

    # Export the final visible result, including object/world transforms such as section transforms.
    mesh_copy.transform(evaluated_obj.matrix_world)
    mesh_copy.update()
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
    return mesh_copy


def _triangulated_mesh_copy(
    obj: bpy.types.Object,
    *,
    depsgraph: bpy.types.Depsgraph | None = None,
) -> tuple[bpy.types.Mesh, list[str]]:
    mesh_copy = _evaluated_triangulated_mesh_copy(obj, depsgraph=depsgraph)
    baked_shape_keys = _active_shape_key_mix_names(obj)
    return mesh_copy, baked_shape_keys


def _normalized_vector(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = (vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2]) ** 0.5
    if length <= 1e-8:
        return (0.0, 0.0, 0.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _mirror_x_vector(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return (-float(vector[0]), float(vector[1]), float(vector[2]))


def _reverse_triangle_winding(triangle: tuple[int, int, int]) -> tuple[int, int, int]:
    return (triangle[0], triangle[2], triangle[1])


def _vector_key(vector: tuple[float, float, float]) -> tuple[int, int, int]:
    return tuple(int(round(float(component) * 1_000_000.0)) for component in vector)


def _prepare_loop_tangent_frames(
    mesh: bpy.types.Mesh,
    *,
    uv_layer_name: str,
) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]], list[float]] | None:
    try:
        if hasattr(mesh, "calc_normals_split"):
            mesh.calc_normals_split()
        mesh.calc_tangents(uvmap=uv_layer_name)
    except Exception:
        return None

    try:
        loop_normals = [_normalized_vector(tuple(loop.normal)) for loop in mesh.loops]
        loop_tangents = [_normalized_vector(tuple(loop.tangent)) for loop in mesh.loops]
        loop_signs = [1.0 if float(loop.bitangent_sign) >= 0.0 else -1.0 for loop in mesh.loops]
        return loop_tangents, loop_normals, loop_signs
    finally:
        if hasattr(mesh, "free_tangents"):
            mesh.free_tangents()


def _parse_runtime_shapekey_names(value: str | None) -> list[str] | None:
    if value is None:
        return None
    names = [item.strip() for item in re.split(r"[,;\n]+", value) if item.strip()]
    if not names:
        return None
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(name)
    return ordered


def _iter_mesh_objects(collection: bpy.types.Collection) -> list[bpy.types.Object]:
    objects: list[bpy.types.Object] = []
    seen: set[str] = set()

    def visit(current: bpy.types.Collection):
        for obj in sorted(current.objects, key=lambda item: item.name):
            if obj.type != "MESH" or obj.name in seen:
                continue
            seen.add(obj.name)
            objects.append(obj)
        for child in sorted(current.children, key=lambda item: item.name):
            visit(child)

    visit(collection)
    return objects


def _runtime_shapekey_order(
    collection: bpy.types.Collection,
    requested_names: list[str] | None,
) -> list[str]:
    if requested_names is not None:
        return requested_names

    names: list[str] = []
    seen: set[str] = set()
    for obj in _iter_mesh_objects(collection):
        shape_keys = obj.data.shape_keys
        if shape_keys is None or not getattr(shape_keys, "use_relative", True):
            continue
        key_blocks = getattr(shape_keys, "key_blocks", None)
        if key_blocks is None or len(key_blocks) <= 1:
            continue
        basis = key_blocks.get("Basis") or key_blocks[0]
        for key_block in key_blocks:
            if key_block == basis or bool(getattr(key_block, "mute", False)):
                continue
            name = str(key_block.name)
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
    return names


def _shape_vector_length_sq(vector: tuple[float, ...]) -> float:
    return sum(float(component) * float(component) for component in vector)


def _row_delta(
    after: tuple[float, float, float, float],
    before: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    return tuple(float(a) - float(b) for a, b in zip(after, before))


def _restore_shape_key_state(
    key_block: bpy.types.ShapeKey,
    *,
    value: float,
    slider_min: float,
    slider_max: float,
):
    key_block.slider_min = slider_min
    key_block.slider_max = slider_max
    key_block.value = value


def _evaluate_shape_key_frame_delta_by_loop(
    obj: bpy.types.Object,
    key_block: bpy.types.ShapeKey,
    *,
    initial_value: float,
    current_mesh: bpy.types.Mesh,
    current_loop_frames: tuple[list[tuple[float, float, float]], list[tuple[float, float, float]], list[float]],
    converter,
    mirror_flip: bool,
    uv_layer_name: str,
    depsgraph: bpy.types.Depsgraph,
) -> list[tuple[tuple[float, float, float, float], tuple[float, float, float, float]]] | None:
    original_value = float(key_block.value)
    original_min = float(key_block.slider_min)
    original_max = float(key_block.slider_max)
    target_value = initial_value + 1.0
    altered_mesh: bpy.types.Mesh | None = None
    try:
        key_block.slider_min = min(original_min, target_value)
        key_block.slider_max = max(original_max, target_value)
        key_block.value = target_value
        bpy.context.view_layer.update()
        altered_mesh = _evaluated_triangulated_mesh_copy(obj, depsgraph=depsgraph)
        if len(altered_mesh.loops) != len(current_mesh.loops):
            return None
        altered_loop_frames = _prepare_loop_tangent_frames(altered_mesh, uv_layer_name=uv_layer_name)
        if altered_loop_frames is None:
            return None

        current_tangents, current_normals, current_signs = current_loop_frames
        altered_tangents, altered_normals, altered_signs = altered_loop_frames
        deltas: list[tuple[tuple[float, float, float, float], tuple[float, float, float, float]]] = []
        for loop_index in range(len(current_mesh.loops)):
            current_tangent = current_tangents[loop_index]
            current_normal = current_normals[loop_index]
            current_sign = current_signs[loop_index]
            altered_tangent = altered_tangents[loop_index]
            altered_normal = altered_normals[loop_index]
            altered_sign = altered_signs[loop_index]
            if mirror_flip:
                current_tangent = _mirror_x_vector(current_tangent)
                current_normal = _mirror_x_vector(current_normal)
                current_sign = -current_sign
                altered_tangent = _mirror_x_vector(altered_tangent)
                altered_normal = _mirror_x_vector(altered_normal)
                altered_sign = -altered_sign

            current_a, current_b = converter.encode_pre_cs_frames(
                [current_tangent],
                [current_normal],
                [current_sign],
            )
            altered_a, altered_b = converter.encode_pre_cs_frames(
                [altered_tangent],
                [altered_normal],
                [altered_sign],
            )
            deltas.append((_row_delta(altered_a[0], current_a[0]), _row_delta(altered_b[0], current_b[0])))
        return deltas
    finally:
        if altered_mesh is not None:
            bpy.data.meshes.remove(altered_mesh)
        _restore_shape_key_state(
            key_block,
            value=original_value,
            slider_min=original_min,
            slider_max=original_max,
        )
        bpy.context.view_layer.update()


def _prepare_runtime_shapekey_sources(
    obj: bpy.types.Object,
    *,
    mesh: bpy.types.Mesh,
    loop_frames: tuple[list[tuple[float, float, float]], list[tuple[float, float, float]], list[float]] | None,
    converter,
    mirror_flip: bool,
    runtime_shapekey_names: list[str],
    depsgraph: bpy.types.Depsgraph,
) -> tuple[list[dict[str, object]], list[str]]:
    if not runtime_shapekey_names:
        return [], []
    shape_keys = obj.data.shape_keys
    if shape_keys is None or not getattr(shape_keys, "use_relative", True):
        return [], []
    key_blocks = getattr(shape_keys, "key_blocks", None)
    if key_blocks is None or len(key_blocks) <= 1:
        return [], []
    if len(obj.data.vertices) != len(mesh.vertices):
        return [], [f"{obj.name}: runtime shapekey skipped because evaluated vertex count differs from source mesh"]

    basis = key_blocks.get("Basis") or key_blocks[0]
    object_linear = obj.matrix_world.to_3x3()
    selected = {name.casefold(): name for name in runtime_shapekey_names}
    sources: list[dict[str, object]] = []
    warnings: list[str] = []
    for key_block in key_blocks:
        if key_block == basis:
            continue
        name = str(key_block.name)
        if name.casefold() not in selected:
            continue
        if bool(getattr(key_block, "mute", False)):
            warnings.append(f"{obj.name}: runtime shapekey '{name}' is muted and was skipped")
            continue
        if len(key_block.data) != len(obj.data.vertices):
            warnings.append(f"{obj.name}: runtime shapekey '{name}' has incompatible vertex count")
            continue
        relative_key = getattr(key_block, "relative_key", None) or basis
        if relative_key != basis:
            warnings.append(f"{obj.name}: runtime shapekey '{name}' is not relative to Basis and was baked only")
            continue
        group_weights = _shape_key_group_weights(obj, key_block, len(obj.data.vertices))
        position_deltas: list[tuple[float, float, float]] = []
        for vertex_index in range(len(obj.data.vertices)):
            influence = 1.0 if group_weights is None else float(group_weights[vertex_index])
            delta = (key_block.data[vertex_index].co - basis.data[vertex_index].co) * influence
            world_delta = object_linear @ delta
            export_delta = (float(world_delta.x), float(world_delta.y), float(world_delta.z))
            if mirror_flip:
                export_delta = _mirror_x_vector(export_delta)
            position_deltas.append(converter.from_blender_position(export_delta))

        frame_deltas = None
        if loop_frames is not None:
            frame_deltas = _evaluate_shape_key_frame_delta_by_loop(
                obj,
                key_block,
                initial_value=float(key_block.value),
                current_mesh=mesh,
                current_loop_frames=loop_frames,
                converter=converter,
                mirror_flip=mirror_flip,
                uv_layer_name=(mesh.uv_layers.active.name if mesh.uv_layers.active else ""),
                depsgraph=depsgraph,
            )
        if frame_deltas is None:
            warnings.append(f"{obj.name}: runtime shapekey '{name}' frame delta defaulted to zero")

        sources.append(
            {
                "name": name,
                "initial_weight": float(key_block.value),
                "position_deltas": position_deltas,
                "frame_deltas": frame_deltas,
            }
        )
    return sources, warnings


def _numeric_vertex_group_ids(obj: bpy.types.Object) -> dict[int, int]:
    numeric_groups: dict[int, int] = {}
    for vertex_group in obj.vertex_groups:
        if vertex_group.name.isdigit():
            numeric_groups[vertex_group.index] = int(vertex_group.name)
    return numeric_groups


def _used_numeric_bone_ids(obj: bpy.types.Object) -> set[int]:
    numeric_groups = _numeric_vertex_group_ids(obj)
    used: set[int] = set()
    for vertex in obj.data.vertices:
        for group_ref in vertex.groups:
            bone_id = numeric_groups.get(group_ref.group)
            if bone_id is not None and float(group_ref.weight) > 0.0:
                used.add(bone_id)
    return used


def _normalized_top4_weights(
    obj: bpy.types.Object,
    *,
    mesh: bpy.types.Mesh | None = None,
    bone_to_local: dict[int, int] | None = None,
) -> tuple[list[tuple[int, int, int, int]], list[tuple[int, int, int, int]], int]:
    mesh = mesh or obj.data
    numeric_groups = _numeric_vertex_group_ids(obj)
    if not numeric_groups:
        raise ValueError(f"{obj.name}: no numeric vertex groups were found")

    per_vertex_indices: list[tuple[int, int, int, int]] = []
    per_vertex_weights: list[tuple[int, int, int, int]] = []
    max_palette_index = 0

    for vertex in mesh.vertices:
        weighted_groups: list[tuple[int, float]] = []
        for group_ref in vertex.groups:
            bone_id = numeric_groups.get(group_ref.group)
            if bone_id is None:
                continue
            palette_index = bone_to_local[bone_id] if bone_to_local is not None else bone_id
            if palette_index < 0 or palette_index > 0xFF:
                raise ValueError(
                    f"{obj.name}: vertex group {palette_index} cannot be written to uint8 BLENDINDICES. "
                    "Split the export IB so this chunk uses <= 256 local bones."
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
        if total_weight <= 1e-12:
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


def _fallback_vertex_frame(mesh: bpy.types.Mesh, vertex_index: int) -> tuple[tuple[float, float, float], tuple[float, float, float], float]:
    normal = _normalized_vector(tuple(mesh.vertices[vertex_index].normal))
    if normal == (0.0, 0.0, 0.0):
        normal = (0.0, 0.0, 1.0)
    tangent = (1.0, 0.0, 0.0)
    if abs(normal[0]) > 0.95:
        tangent = (0.0, 1.0, 0.0)
    return tangent, normal, 1.0


def _extract_object_payload(
    obj: bpy.types.Object,
    *,
    flip_uv_v: bool = False,
    bone_to_local: dict[int, int] | None = None,
    profile_id: str | None = None,
    original_first_index: int | None = None,
    original_index_count: int | None = None,
    export_runtime_shapekeys: bool = False,
    runtime_shapekey_names: list[str] | None = None,
    default_mirror_flip: bool = False,
    log=None,
) -> dict[str, object]:
    if obj.type != "MESH":
        raise ValueError(f"{obj.name}: only mesh objects can be exported")
    profile_id = (profile_id or YIHUAN_PROFILE.profile_id).strip()
    if profile_id != YIHUAN_PROFILE.profile_id:
        raise ValueError(f"{obj.name}: unsupported profile id")
    converter = get_game_data_converter(profile_id)
    mirror_flip = _resolve_mirror_flip_for_object(obj, default_value=default_mirror_flip)

    depsgraph = bpy.context.evaluated_depsgraph_get()
    missing_optional_attributes: list[str] = []
    mesh_copy, baked_shape_keys = _triangulated_mesh_copy(obj, depsgraph=depsgraph)
    if log is not None:
        mirror_flip = _resolve_mirror_flip_for_object(obj, default_value=default_mirror_flip)
        log(
            f"{obj.name}: evaluated mesh -> source_verts={len(obj.data.vertices)}, "
            f"source_polys={len(obj.data.polygons)}, eval_verts={len(mesh_copy.vertices)}, "
            f"loops={len(mesh_copy.loops)}, polys={len(mesh_copy.polygons)}, "
            f"{_format_matrix_trace(obj.matrix_world)}, mirror_flip={mirror_flip}"
        )
    blend_indices, blend_weights, local_palette_count = _normalized_top4_weights(
        obj,
        mesh=mesh_copy,
        bone_to_local=bone_to_local,
    )
    uv0_layer = mesh_copy.uv_layers.active or _find_uv_layer(mesh_copy, "UV0")
    if uv0_layer is None:
        bpy.data.meshes.remove(mesh_copy)
        raise ValueError(f"{obj.name}: an active UV layer is required for export")
    uv1_layer = _find_uv_layer(mesh_copy, "UV1")
    uv3_layer = _find_uv_layer(mesh_copy, "UV3", "packed_uv2")
    uv4_layer = _find_uv_layer(mesh_copy, "UV4", "packed_uv3")
    packed_uv2, missing_uv2_attr = _optional_point_vector_attribute(mesh_copy, "packed_uv2")
    packed_uv3, missing_uv3_attr = _optional_point_vector_attribute(mesh_copy, "packed_uv3")
    if uv3_layer is None and missing_uv2_attr:
        missing_optional_attributes.append("UV3_or_packed_uv2_defaulted")
    if uv4_layer is None and missing_uv3_attr:
        missing_optional_attributes.append("UV4_or_packed_uv3_defaulted")
    loop_frames = _prepare_loop_tangent_frames(mesh_copy, uv_layer_name=uv0_layer.name)
    if loop_frames is None:
        missing_optional_attributes.append("rebuild_tangent_frame_failed")
    runtime_shapekey_sources: list[dict[str, object]] = []
    if export_runtime_shapekeys and runtime_shapekey_names:
        runtime_shapekey_sources, shapekey_warnings = _prepare_runtime_shapekey_sources(
            obj,
            mesh=mesh_copy,
            loop_frames=loop_frames,
            converter=converter,
            mirror_flip=mirror_flip,
            runtime_shapekey_names=runtime_shapekey_names,
            depsgraph=depsgraph,
        )
        missing_optional_attributes.extend(shapekey_warnings)

    positions: list[tuple[float, float, float]] = []
    packed_uv_entries: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    out_blend_indices: list[tuple[int, int, int, int]] = []
    out_blend_weights: list[tuple[int, int, int, int]] = []
    decoded_tangents: list[tuple[float, float, float]] = []
    decoded_normals: list[tuple[float, float, float]] = []
    decoded_signs: list[float] = []
    outline_params: list[tuple[int, int, int, int]] = []
    shapekey_vertex_records: list[list[dict[str, object]]] = []
    shapekey_initial_weights: dict[str, float] = {}
    triangles: list[tuple[int, int, int]] = []
    remap: dict[tuple[object, ...], int] = {}

    def _uv_key(uv_pair: tuple[float, float]) -> tuple[int, int]:
        return (int(round(float(uv_pair[0]) * 1_000_000.0)), int(round(float(uv_pair[1]) * 1_000_000.0)))

    def _to_game_uv_pair(uv_pair: tuple[float, float]) -> tuple[float, float]:
        u_coord, v_coord = uv_pair
        return (float(u_coord), 1.0 - float(v_coord) if flip_uv_v else float(v_coord))

    def _loop_uv_pair(
        loop_index: int,
        source_vertex_index: int,
        loop_uv_layer: bpy.types.MeshUVLoopLayer | None,
        fallback_values: list[tuple[float, float, float]],
    ) -> tuple[float, float]:
        if loop_uv_layer is not None:
            uv_value = loop_uv_layer.data[loop_index].uv
            return (float(uv_value[0]), float(uv_value[1]))
        fallback = fallback_values[source_vertex_index]
        return (float(fallback[0]), float(fallback[1]))

    try:
        for polygon in mesh_copy.polygons:
            if polygon.loop_total != 3:
                raise ValueError(f"{obj.name}: triangulation failed; found a polygon with {polygon.loop_total} corners")
            triangle: list[int] = []
            for loop_index in polygon.loop_indices:
                source_vertex_index = mesh_copy.loops[loop_index].vertex_index
                uv0 = _to_game_uv_pair(tuple(float(value) for value in uv0_layer.data[loop_index].uv))
                uv1 = uv0 if uv1_layer is None else _to_game_uv_pair(
                    tuple(float(value) for value in uv1_layer.data[loop_index].uv)
                )
                uv2 = _to_game_uv_pair(_loop_uv_pair(loop_index, source_vertex_index, uv3_layer, packed_uv2))
                uv3 = _to_game_uv_pair(_loop_uv_pair(loop_index, source_vertex_index, uv4_layer, packed_uv3))
                if loop_frames is not None:
                    loop_tangents, loop_normals, loop_signs = loop_frames
                    decoded_tangent = loop_tangents[loop_index]
                    decoded_normal = loop_normals[loop_index]
                    decoded_sign = loop_signs[loop_index]
                else:
                    decoded_tangent, decoded_normal, decoded_sign = _fallback_vertex_frame(
                        mesh_copy,
                        source_vertex_index,
                    )
                if mirror_flip:
                    decoded_tangent = _mirror_x_vector(decoded_tangent)
                    decoded_normal = _mirror_x_vector(decoded_normal)
                    decoded_sign = -decoded_sign

                key = (
                    source_vertex_index,
                    *_uv_key(uv0),
                    *_uv_key(uv1),
                    *_uv_key(uv2),
                    *_uv_key(uv3),
                    *_vector_key(decoded_normal),
                    *_vector_key(decoded_tangent),
                    int(decoded_sign >= 0.0),
                )
                out_vertex_index = remap.get(key)
                if out_vertex_index is None:
                    source_position = mesh_copy.vertices[source_vertex_index].co
                    export_position = (float(source_position.x), float(source_position.y), float(source_position.z))
                    if mirror_flip:
                        export_position = _mirror_x_vector(export_position)
                    positions.append(
                        converter.from_blender_position(
                            export_position
                        )
                    )
                    packed_uv_entries.append(
                        (
                            (uv0[0], uv0[1]),
                            uv1,
                            uv2,
                            uv3,
                        )
                    )
                    out_blend_indices.append(blend_indices[source_vertex_index])
                    out_blend_weights.append(blend_weights[source_vertex_index])
                    decoded_tangents.append(decoded_tangent)
                    decoded_normals.append(decoded_normal)
                    decoded_signs.append(decoded_sign)
                    outline_params.append((255, 255, 255, 255))
                    vertex_shapekey_records: list[dict[str, object]] = []
                    for shapekey_source in runtime_shapekey_sources:
                        shapekey_name = str(shapekey_source["name"])
                        shapekey_initial_weights[shapekey_name] = float(shapekey_source["initial_weight"])
                        position_delta = shapekey_source["position_deltas"][source_vertex_index]
                        frame_deltas = shapekey_source.get("frame_deltas")
                        if frame_deltas is None:
                            frame_a_delta = (0.0, 0.0, 0.0, 0.0)
                            frame_b_delta = (0.0, 0.0, 0.0, 0.0)
                        else:
                            frame_a_delta, frame_b_delta = frame_deltas[loop_index]
                        if (
                            _shape_vector_length_sq(position_delta) <= _SHAPEKEY_EPSILON
                            and _shape_vector_length_sq(frame_a_delta) <= _SHAPEKEY_EPSILON
                            and _shape_vector_length_sq(frame_b_delta) <= _SHAPEKEY_EPSILON
                        ):
                            continue
                        vertex_shapekey_records.append(
                            {
                                "name": shapekey_name,
                                "position_delta": position_delta,
                                "frame_a_delta": frame_a_delta,
                                "frame_b_delta": frame_b_delta,
                            }
                        )
                    shapekey_vertex_records.append(vertex_shapekey_records)
                    out_vertex_index = len(positions) - 1
                    remap[key] = out_vertex_index
                triangle.append(out_vertex_index)
            # If import used Mirror Flip, the X reflection already handled
            # winding both ways. Otherwise write the inverse order back to the
            # game IB while preserving custom normals.
            blender_triangle = tuple(triangle)
            triangles.append(
                blender_triangle
                if mirror_flip
                else _reverse_triangle_winding(blender_triangle)
            )
    finally:
        bpy.data.meshes.remove(mesh_copy)

    frame_a, frame_b = converter.encode_pre_cs_frames(decoded_tangents, decoded_normals, decoded_signs)
    if original_first_index is None:
        original_first_index = 0
    if original_index_count is None:
        original_index_count = len(triangles) * 3

    return {
        "object_name": obj.name,
        "draw_toggle": str(obj.get(_DRAW_TOGGLE_PROP, "") or "").strip(),
        "draw_toggle_key": str(obj.get(_DRAW_TOGGLE_KEY_PROP, "") or "").strip(),
        "positions": positions,
        "triangles": triangles,
        "packed_uv_entries": packed_uv_entries,
        "blend_indices": out_blend_indices,
        "blend_weights": out_blend_weights,
        "frame_a": frame_a,
        "frame_b": frame_b,
        "outline_params": outline_params,
        "shapekey_vertex_records": shapekey_vertex_records,
        "shapekey_initial_weights": shapekey_initial_weights,
        "local_palette_count": local_palette_count,
        "baked_shape_keys": baked_shape_keys,
        "missing_optional_attributes": sorted(set(missing_optional_attributes)),
        "original_first_index": int(original_first_index),
        "original_index_count": int(original_index_count),
    }


def _resource_token(value: str) -> str:
    token = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip())
    token = token.strip("_")
    return token or "part"


def _validate_hash8(value: str, label: str) -> str:
    hash_value = str(value or "").strip().lower()
    if not _REGION_HASH_RE.fullmatch(hash_value):
        raise ValueError(
            f"{label} must be an 8-digit hash, got '{value}'."
        )
    return hash_value


def _collection_kind(collection: bpy.types.Collection) -> str:
    return str(collection.get(_COLLECTION_KIND_PROP, "") or "").strip().lower()


def _collection_source_ib_hash(collection: bpy.types.Collection) -> str:
    return str(collection.get(_SOURCE_IB_HASH_PROP, "") or "").strip().lower()


def _collection_region_hash(collection: bpy.types.Collection) -> str:
    return str(collection.get(_REGION_HASH_PROP, "") or "").strip().lower()


def _collection_region_index_count(collection: bpy.types.Collection) -> int | None:
    try:
        return int(collection.get(_REGION_INDEX_COUNT_PROP))
    except (TypeError, ValueError):
        match = _REGION_COLLECTION_RE.match(collection.name)
        if match and match.group("count") is not None:
            return int(match.group("count"))
    return None


def _collection_region_first_index(collection: bpy.types.Collection) -> int | None:
    try:
        return int(collection.get(_REGION_FIRST_INDEX_PROP))
    except (TypeError, ValueError):
        match = _REGION_COLLECTION_RE.match(collection.name)
        if match and match.group("first") is not None:
            return int(match.group("first"))
    return None


def _collection_runtime_contract(
    collection: bpy.types.Collection,
    *,
    region_index_count: int | None,
    region_first_index: int | None,
) -> dict[str, object]:
    """Read the export/runtime contract from the region collection."""
    profile_id = _optional_str_collection_prop(collection, _PROFILE_ID_PROP) or YIHUAN_PROFILE.profile_id
    return {
        "profile_id": profile_id,
        "original_first_index": region_first_index,
        "original_index_count": region_index_count,
        "collector_group_slot": _optional_str_collection_prop(collection, _COLLECTOR_GROUP_SLOT_PROP),
        "collector_t0_hash": _optional_str_collection_prop(collection, _COLLECTOR_T0_HASH_PROP),
        "collector_u0_hash": _optional_str_collection_prop(collection, _COLLECTOR_U0_HASH_PROP),
        "collector_u1_hash": _optional_str_collection_prop(collection, _COLLECTOR_U1_HASH_PROP),
        "collector_collect_key": _optional_str_collection_prop(collection, _COLLECTOR_COLLECT_KEY_PROP),
        "collector_finish_condition": _optional_str_collection_prop(collection, _COLLECTOR_FINISH_CONDITION_PROP),
        "match_vs_texcoord_hash": _optional_str_collection_prop(collection, _MATCH_VS_TEXCOORD_HASH_PROP),
        "match_vs_position_hash": _optional_str_collection_prop(collection, _MATCH_VS_POSITION_HASH_PROP),
        "match_vs_outline_hash": _optional_str_collection_prop(collection, _MATCH_VS_OUTLINE_HASH_PROP),
        "texture_slots": _optional_str_collection_prop(collection, _TEXTURE_SLOTS_PROP),
    }


def _validate_region_collection_contract(
    collection: bpy.types.Collection,
    *,
    region_index_count: int | None,
    region_first_index: int | None,
    require_runtime_contract: bool = True,
):
    missing: list[str] = []
    if region_index_count is None:
        missing.append(_REGION_INDEX_COUNT_PROP)
    if region_first_index is None:
        missing.append(_REGION_FIRST_INDEX_PROP)
    if require_runtime_contract:
        for key in (
            _MATCH_VS_TEXCOORD_HASH_PROP,
            _MATCH_VS_POSITION_HASH_PROP,
        ):
            if not _optional_str_collection_prop(collection, key):
                missing.append(key)
    if missing:
        raise ValueError(
            f"Region collection '{collection.name}' is missing export contract field(s): {', '.join(missing)}. "
            "Re-run FrameAnalysis/Profile with the updated analyzer or set these custom properties "
            "on the region collection."
        )


def _part_bmc_identity(
    collection: bpy.types.Collection,
    *,
    region_hash: str,
    region_index_count: int | None,
    part_index: int,
) -> tuple[str, int | None, int, str]:
    bmc_hash = _optional_str_collection_prop(collection, _BMC_IB_HASH_PROP)
    bmc_count = _optional_int_collection_prop(collection, _BMC_MATCH_INDEX_COUNT_PROP)
    bmc_chunk = _optional_int_collection_prop(collection, _BMC_CHUNK_INDEX_PROP)
    if bmc_hash:
        identity_source = "part_collection"
    else:
        bmc_hash = region_hash
        identity_source = "region_default"
    if bmc_count is None:
        bmc_count = region_index_count
    if bmc_chunk is None:
        bmc_chunk = part_index
    return _validate_hash8(bmc_hash, f"BMC IB hash for part collection '{collection.name}'"), bmc_count, int(bmc_chunk), identity_source


def _source_root_hash(collection: bpy.types.Collection) -> str:
    kind = _collection_kind(collection)
    if kind and kind not in {"source_ib", "export_root"}:
        raise ValueError(
            f"Export collection '{collection.name}' is marked as '{kind}'. Select the source-IB export root collection instead."
        )
    source_hash = _collection_source_ib_hash(collection) or collection.name
    return _validate_hash8(source_hash, "Source IB collection")


def _region_collection_hash(collection: bpy.types.Collection) -> str:
    kind = _collection_kind(collection)
    if kind and kind != "region":
        raise ValueError(f"Collection '{collection.name}' is marked as '{kind}', expected a region collection.")
    region_hash = _collection_region_hash(collection)
    if not region_hash:
        match = _REGION_COLLECTION_RE.match(collection.name)
        region_hash = match.group("hash").lower() if match else collection.name
    return _validate_hash8(region_hash, "Region collection")


def _part_collection_index(collection: bpy.types.Collection) -> int:
    kind = _collection_kind(collection)
    if kind and kind != "part":
        raise ValueError(f"Collection '{collection.name}' is marked as '{kind}', expected a part collection.")
    try:
        return int(collection.get(_PART_INDEX_PROP))
    except (TypeError, ValueError):
        match = _PART_NAME_RE.match(collection.name)
        if match:
            return int(match.group("index"))
    raise ValueError(
        f"Part collection '{collection.name}' must define modimp_part_index or be named like part00."
    )


def _sorted_mesh_objects(objects) -> list[bpy.types.Object]:
    unique: dict[str, bpy.types.Object] = {}
    for obj in objects:
        if obj.type == "MESH":
            unique[obj.name] = obj
    return sorted(unique.values(), key=_export_object_sort_key)


def _resolve_region_collections(root_collection: bpy.types.Collection) -> list[bpy.types.Collection]:
    """Resolve region collections from the strict sourceIB -> region -> part tree."""
    source_hash = _source_root_hash(root_collection)
    direct_meshes = _sorted_mesh_objects(root_collection.objects)
    if direct_meshes:
        names = ", ".join(obj.name for obj in direct_meshes[:6])
        if len(direct_meshes) > 6:
            names += ", ..."
        raise ValueError(
            "Meshes cannot be linked directly under the source-IB export root. "
            f"Move them into {source_hash}/<region>/partNN first: {names}"
        )

    region_collections: list[bpy.types.Collection] = []
    bad_children: list[str] = []
    empty_region_names: list[str] = []
    for child in sorted(root_collection.children, key=lambda item: item.name):
        child_kind = _collection_kind(child)
        child_region_hash = _collection_region_hash(child)
        is_region = child_kind == "region" or bool(child_region_hash) or bool(_REGION_COLLECTION_RE.match(child.name.strip().lower()))
        if is_region:
            _region_collection_hash(child)
            if not any(obj.type == "MESH" for obj in child.all_objects):
                empty_region_names.append(child.name)
                continue
            region_collections.append(child)
            continue
        if any(obj.type == "MESH" for obj in child.all_objects):
            bad_children.append(child.name)

    if bad_children:
        raise ValueError(
            "Only region collections are allowed directly under the source-IB export root. "
            f"Unexpected mesh-bearing children: {', '.join(bad_children[:6])}"
        )
    if not region_collections:
        if empty_region_names:
            raise ValueError(
                f"Export root '{root_collection.name}' only has empty region template collections. "
                "Put mesh objects into one or more region collections before exporting."
            )
        raise ValueError(
            f"Export root '{root_collection.name}' has no region children. "
            "Import/analyze the source IB to seed region collections, or create region collections manually."
        )
    return region_collections


def _resolve_export_parts(
    root_collection: bpy.types.Collection,
    *,
    source_ib_hash: str,
    region_hash: str,
    region_index_count: int | None,
    region_first_index: int | None,
) -> list[dict[str, object]]:
    child_collections = sorted(root_collection.children, key=lambda item: item.name)
    direct_meshes = _sorted_mesh_objects(root_collection.objects)

    region_part_prefix = region_hash
    if (
        region_hash == source_ib_hash.lower()
        and region_index_count is not None
        and region_first_index is not None
    ):
        region_part_prefix = f"{region_hash}-{int(region_index_count)}-{int(region_first_index)}"

    parts: list[dict[str, object]] = []
    seen_objects: dict[str, str] = {}
    used_part_indices: set[int] = set()

    def append_part_definition(
        *,
        part_collection: bpy.types.Collection,
        mesh_objects: list[bpy.types.Object],
        part_index: int,
        part_name: str,
    ):
        if part_index in used_part_indices:
            raise ValueError(f"Region '{region_hash}' has duplicate export part index {part_index}.")
        used_part_indices.add(part_index)
        bmc_ib_hash, bmc_match_index_count, bmc_chunk_index, bmc_identity_source = _part_bmc_identity(
            part_collection,
            region_hash=region_hash,
            region_index_count=region_index_count,
            part_index=part_index,
        )
        for obj in mesh_objects:
            previous_part = seen_objects.get(obj.name)
            if previous_part is not None:
                raise ValueError(
                    f"{obj.name}: object is linked into multiple export parts "
                    f"('{previous_part}' and '{part_collection.name}')."
                )
            seen_objects[obj.name] = part_collection.name
        parts.append(
            {
                "part_index": part_index,
                "part_name": part_name,
                "collection_name": part_collection.name,
                "objects": mesh_objects,
                "implicit": False,
                "bmc_ib_hash": bmc_ib_hash,
                "bmc_match_index_count": bmc_match_index_count,
                "bmc_chunk_index": bmc_chunk_index,
                "bmc_identity_source": bmc_identity_source,
            }
        )

    for child in child_collections:
        direct_part_meshes = _sorted_mesh_objects(child.objects)
        nested_export_collections = [
            nested
            for nested in sorted(child.children, key=lambda item: item.name)
            if _sorted_mesh_objects(nested.objects)
        ]
        if direct_part_meshes and nested_export_collections:
            raise ValueError(
                f"Part collection '{child.name}' mixes direct meshes and IB sub-collections. "
                "Move all meshes into sub-collections or keep them all directly in the part."
            )

        parent_part_index = _part_collection_index(child)
        if nested_export_collections:
            for split_index, nested in enumerate(nested_export_collections):
                mesh_objects = _sorted_mesh_objects(nested.objects)
                export_part_index = _optional_int_collection_prop(nested, _PART_INDEX_PROP)
                if export_part_index is None:
                    export_part_index = (parent_part_index + 1) * 1000 + split_index
                append_part_definition(
                    part_collection=nested,
                    mesh_objects=mesh_objects,
                    part_index=export_part_index,
                    part_name=f"{region_part_prefix}_part{parent_part_index:02d}_ib{split_index:02d}",
                )
            continue

        if not direct_part_meshes:
            continue
        append_part_definition(
            part_collection=child,
            mesh_objects=direct_part_meshes,
            part_index=parent_part_index,
            part_name=f"{region_part_prefix}_part{parent_part_index:02d}",
        )
    if direct_meshes:
        if parts:
            names = ", ".join(obj.name for obj in direct_meshes[:6])
            if len(direct_meshes) > 6:
                names += ", ..."
            raise ValueError(
                f"Region '{root_collection.name}' mixes direct meshes with explicit part collections: {names}. "
                "Move direct meshes into partNN or keep the region as one implicit part."
            )
        append_part_definition(
            part_collection=root_collection,
            mesh_objects=direct_meshes,
            part_index=0,
            part_name=f"{region_part_prefix}_part00",
        )
    if not parts:
        raise ValueError(f"Region collection '{root_collection.name}' has no non-empty part collections.")
    return sorted(parts, key=lambda item: (int(item["part_index"]), str(item["collection_name"])))


def _build_part_palette(mesh_objects: list[bpy.types.Object], *, part_name: str) -> tuple[list[int], dict[int, int]]:
    global_bone_ids: set[int] = set()
    for obj in mesh_objects:
        object_bones = _used_numeric_bone_ids(obj)
        if len(object_bones) > 0x100:
            raise ValueError(
                f"{obj.name}: uses {len(object_bones)} numeric vertex groups, exceeding the uint8 BLENDINDICES limit. "
                "Split this object before export; this pass does not cut one object by triangles."
            )
        global_bone_ids.update(object_bones)

    if not global_bone_ids:
        raise ValueError(f"{part_name}: no numeric vertex groups were found in this export IB.")
    if len(global_bone_ids) > 0x100:
        raise ValueError(
            f"{part_name}: objects in this export IB use {len(global_bone_ids)} unique bones. "
            "Keep each export sub-collection within 256 bones or let export auto-split by object."
        )

    palette = sorted(global_bone_ids)
    return palette, {global_bone_id: local_index for local_index, global_bone_id in enumerate(palette)}


def _resolve_original_match_index_count(
    parts: list[dict[str, object]],
    *,
    region_hash: str,
    region_index_count: int | None = None,
) -> tuple[int, str]:
    if region_index_count is not None:
        return int(region_index_count), "region_collection"
    raise ValueError(f"{region_hash}: region collection must define modimp_region_index_count.")


def _write_index_buffer_auto(path: Path, indices: list[int]) -> str:
    if not indices:
        raise ValueError(f"{path.name}: cannot write an empty index buffer.")
    min_index = min(indices)
    max_index = max(indices)
    if min_index < 0:
        raise ValueError(f"{path.name}: index buffer contains a negative index: {min_index}")
    if max_index <= 0xFFFF:
        write_u16_buffer(str(path), indices)
        return "DXGI_FORMAT_R16_UINT"
    write_u32_buffer(str(path), indices)
    return "DXGI_FORMAT_R32_UINT"


def _register_runtime_shapekey_initial(
    initial_weights: dict[str, float],
    key_name: str,
    value: float,
    *,
    source_name: str,
):
    existing = initial_weights.get(key_name)
    if existing is None:
        initial_weights[key_name] = float(value)
        return
    if abs(existing - float(value)) > 1.0e-5:
        raise ValueError(
            f"Runtime shapekey '{key_name}' has conflicting initial weights "
            f"({existing:.6f} vs {float(value):.6f}) at {source_name}. "
            "Use distinct shapekey names or align their Blender values before export."
        )


def _write_shapekey_static_buffer(
    path: Path,
    *,
    vertex_records: list[list[dict[str, object]]],
    key_index_by_name: dict[str, int],
    initial_weights: dict[str, float],
) -> tuple[int, int]:
    key_count = len(key_index_by_name)
    flattened_records: list[dict[str, object]] = []
    vertex_headers: list[tuple[int, int]] = []
    for records in vertex_records:
        offset = len(flattened_records)
        for record in records:
            if str(record.get("name", "")) not in key_index_by_name:
                continue
            flattened_records.append(record)
        vertex_headers.append((offset, len(flattened_records) - offset))

    if not flattened_records or key_count <= 0:
        return 0, 0

    rows: list[tuple[float, float, float, float]] = [
        (float(len(vertex_records)), float(key_count), float(len(flattened_records)), 0.0)
    ]
    rows.extend((float(offset), float(count), 0.0, 0.0) for offset, count in vertex_headers)

    key_names_by_index = sorted(key_index_by_name, key=lambda name: key_index_by_name[name])
    for key_name in key_names_by_index:
        rows.append((float(initial_weights.get(key_name, 0.0)), 0.0, 1.0, 0.0))

    for record in flattened_records:
        key_index = key_index_by_name[str(record["name"])]
        position_delta = tuple(float(value) for value in record["position_delta"])
        frame_a_delta = tuple(float(value) for value in record["frame_a_delta"])
        frame_b_delta = tuple(float(value) for value in record["frame_b_delta"])
        rows.append((float(key_index), position_delta[0], position_delta[1], position_delta[2]))
        rows.append((frame_a_delta[0], frame_a_delta[1], frame_a_delta[2], frame_a_delta[3]))
        rows.append((frame_b_delta[0], frame_b_delta[1], frame_b_delta[2], frame_b_delta[3]))

    write_float4_buffer(str(path), rows)
    return len(flattened_records), len(rows)


def _export_part_buffers(
    *,
    part_definition: dict[str, object],
    ib_hash: str,
    region_hash: str,
    region_index_count: int | None,
    region_first_index: int | None,
    region_runtime_contract: dict[str, object],
    buffer_dir: Path,
    flip_uv_v: bool,
    default_mirror_flip: bool,
    export_runtime_shapekeys: bool,
    runtime_shapekey_names: list[str],
    runtime_shapekey_initial_weights: dict[str, float],
    log=None,
) -> dict[str, object]:
    part_name = str(part_definition["part_name"])
    part_token = _resource_token(part_name)
    mesh_objects = list(part_definition["objects"])
    bmc_ib_hash = str(part_definition["bmc_ib_hash"]).lower()
    bmc_match_index_count = part_definition.get("bmc_match_index_count")
    bmc_chunk_index = int(part_definition["bmc_chunk_index"])
    if bmc_match_index_count is None:
        bmc_match_index_count = region_index_count
    if bmc_match_index_count is None:
        # This path should normally be unreachable because region contracts require index_count.
        bmc_match_index_count = 0
        bmc_identity_source = "generated_without_region_index_count"
    else:
        bmc_match_index_count = int(bmc_match_index_count)
        bmc_identity_source = str(part_definition["bmc_identity_source"])
    if not bmc_match_index_count:
        raise ValueError(f"{part_name}: cannot resolve the BMC palette match_index_count.")
    bmc_palette_file = f"{bmc_ib_hash}-{bmc_match_index_count}-{bmc_chunk_index}-Palette.buf"
    palette_entries, bone_to_local = _build_part_palette(mesh_objects, part_name=part_name)

    all_positions: list[tuple[float, float, float]] = []
    all_indices: list[int] = []
    all_packed_uv_entries: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    all_blend_indices: list[tuple[int, int, int, int]] = []
    all_blend_weights: list[tuple[int, int, int, int]] = []
    all_frame_a: list[tuple[float, float, float, float]] = []
    all_frame_b: list[tuple[float, float, float, float]] = []
    all_outline_params: list[tuple[int, int, int, int]] = []
    all_shapekey_vertex_records: list[list[dict[str, object]]] = []
    draw_records: list[dict[str, object]] = []

    vertex_cursor = 0
    index_cursor = 0
    required_local_palette_count = 0
    for draw_index, obj in enumerate(mesh_objects):
        if log is not None:
            constraint_labels = _active_constraint_labels(obj)
            parent_name = obj.parent.name if getattr(obj, "parent", None) is not None else "-"
            modifier_labels = [f"{mod.type}:{mod.name}" for mod in obj.modifiers[:6]]
            used_bones = sorted(_used_numeric_bone_ids(obj))
            log(
                f"{part_name}: bake {obj.name} "
                f"(parent={parent_name}, constraints={len(constraint_labels)}, "
                f"modifiers={len(obj.modifiers)}, source_verts={len(obj.data.vertices)}, "
                f"source_polys={len(obj.data.polygons)}, used_bones={len(used_bones)}, "
                f"mirror_flip={bool(obj.get(_MIRROR_FLIP_PROP, False))})"
            )
            log(f"{obj.name}: object transform -> {_format_matrix_trace(obj.matrix_world)}")
            if used_bones:
                sample = ", ".join(str(value) for value in used_bones[:12])
                if len(used_bones) > 12:
                    sample += ", ..."
                log(f"{obj.name}: numeric bones sample -> [{sample}]")
            if modifier_labels:
                log(f"{obj.name}: modifiers -> {', '.join(modifier_labels)}")
            if constraint_labels:
                log(f"{obj.name}: active constraints -> {', '.join(constraint_labels[:4])}")
        payload = _extract_object_payload(
            obj,
            flip_uv_v=flip_uv_v,
            bone_to_local=bone_to_local,
            profile_id=str(region_runtime_contract.get("profile_id") or ""),
            original_first_index=region_first_index,
            original_index_count=region_index_count,
            export_runtime_shapekeys=export_runtime_shapekeys,
            runtime_shapekey_names=runtime_shapekey_names,
            default_mirror_flip=default_mirror_flip,
            log=log,
        )
        positions = payload["positions"]
        triangles = payload["triangles"]
        packed_uv_entries = payload["packed_uv_entries"]
        blend_indices = payload["blend_indices"]
        blend_weights = payload["blend_weights"]
        frame_a = payload["frame_a"]
        frame_b = payload["frame_b"]
        outline_params = payload["outline_params"]
        shapekey_vertex_records = payload["shapekey_vertex_records"]
        if log is not None:
            log(
                f"{obj.name}: {len(positions)} verts, {len(triangles)} tris, "
                f"palette={int(payload['local_palette_count'])}, "
                f"shapekey_records={sum(len(records) for records in shapekey_vertex_records)}, "
                f"drawindexed=({len(triangles) * 3},{index_cursor},0), "
                f"orig=({int(payload['original_index_count'])},{int(payload['original_first_index'])},0)"
            )

        if len(positions) != len(packed_uv_entries):
            raise ValueError(f"{obj.name}: packed UV entry count does not match position count")
        if len(positions) != len(blend_indices) or len(positions) != len(blend_weights):
            raise ValueError(f"{obj.name}: blend payload count does not match position count")
        if len(positions) != len(frame_a) or len(positions) != len(frame_b):
            raise ValueError(f"{obj.name}: frame payload count does not match position count")
        if len(positions) != len(outline_params):
            raise ValueError(f"{obj.name}: outline parameter count does not match position count")
        if len(positions) != len(shapekey_vertex_records):
            raise ValueError(f"{obj.name}: shapekey record count does not match position count")
        for key_name, initial_weight in dict(payload["shapekey_initial_weights"]).items():
            _register_runtime_shapekey_initial(
                runtime_shapekey_initial_weights,
                str(key_name),
                float(initial_weight),
                source_name=obj.name,
            )

        vertex_start = vertex_cursor
        first_index = index_cursor
        for triangle in triangles:
            remapped = tuple(vertex_start + int(vertex_id) for vertex_id in triangle)
            all_indices.extend(remapped)
        index_count = len(triangles) * 3
        index_cursor += index_count
        vertex_cursor += len(positions)

        all_positions.extend(positions)
        all_packed_uv_entries.extend(packed_uv_entries)
        all_blend_indices.extend(blend_indices)
        all_blend_weights.extend(blend_weights)
        all_frame_a.extend(frame_a)
        all_frame_b.extend(frame_b)
        all_outline_params.extend(outline_params)
        all_shapekey_vertex_records.extend(shapekey_vertex_records)

        # The collection tree is the export contract; object metadata is not used for draw identity.
        slice_hash = region_hash

        draw_token = _resource_token(f"{part_name}_draw{draw_index:02d}")
        required_local_palette_count = max(required_local_palette_count, int(payload["local_palette_count"]))
        draw_records.append(
            {
                "part_name": part_name,
                "part_index": int(part_definition["part_index"]),
                "draw_index": draw_index,
                "draw_token": draw_token,
                "object_name": payload["object_name"],
                "draw_toggle": payload.get("draw_toggle", ""),
                "draw_toggle_key": payload.get("draw_toggle_key", ""),
                "region_hash": region_hash,
                "slice_hash": slice_hash,
                "first_index": first_index,
                "index_count": index_count,
                "base_vertex": 0,
                "vertex_start": vertex_start,
                "vertex_count": len(positions),
                "drawindexed": [index_count, first_index, 0],
                "original_first_index": int(payload["original_first_index"]),
                "original_index_count": int(payload["original_index_count"]),
                "shape_key_policy": _SHAPE_KEY_BAKE_POLICY,
                "baked_shape_keys": list(payload["baked_shape_keys"]),
                "missing_optional_attributes": list(payload["missing_optional_attributes"]),
                "local_palette_count": int(payload["local_palette_count"]),
            }
        )

    if not all_indices:
        raise ValueError(f"Export part '{part_name}' produced no indices.")

    palette_entry_count = len(palette_entries)
    if required_local_palette_count > palette_entry_count:
        raise ValueError(
            f"{part_name}: exported vertex groups require {required_local_palette_count} local bones, "
            f"but palette '{bmc_palette_file}' only has {palette_entry_count} entries."
        )

    files = {
        "ib": f"{part_name}-ib.buf",
        "vb0_pre_cs": f"{part_name}-position.buf",
        "weights": f"{part_name}-blend.buf",
        "frame_pre_cs": f"{part_name}-normal.buf",
        "packed_uv": f"{part_name}-texcoord.buf",
        "outline_param": f"{part_name}-outline.buf",
    }
    shapekey_record_count = 0
    shapekey_static_row_count = 0
    if export_runtime_shapekeys and runtime_shapekey_names:
        files["shapekey_static"] = f"{part_name}-shapekey-static.buf"
        key_index_by_name = {name: index for index, name in enumerate(runtime_shapekey_names)}
        shapekey_record_count, shapekey_static_row_count = _write_shapekey_static_buffer(
            buffer_dir / files["shapekey_static"],
            vertex_records=all_shapekey_vertex_records,
            key_index_by_name=key_index_by_name,
            initial_weights=runtime_shapekey_initial_weights,
        )
        if shapekey_record_count <= 0:
            del files["shapekey_static"]

    ib_format = _write_index_buffer_auto(buffer_dir / files["ib"], all_indices)
    write_float3_buffer(str(buffer_dir / files["vb0_pre_cs"]), all_positions)
    write_weight_pairs_buffer(str(buffer_dir / files["weights"]), all_blend_indices, all_blend_weights)
    write_snorm8x4_pairs_buffer(str(buffer_dir / files["frame_pre_cs"]), all_frame_a, all_frame_b)
    write_half2x4_buffer(str(buffer_dir / files["packed_uv"]), all_packed_uv_entries)
    write_u8x4_buffer(str(buffer_dir / files["outline_param"]), all_outline_params)
    write_u32_buffer(str(buffer_dir / bmc_palette_file), palette_entries)

    if log is not None:
        log(
            f"{part_name}: wrote {len(all_positions)} verts, {len(all_indices)} indices, "
            f"palette entries={len(palette_entries)}"
        )

    return {
        "part_index": int(part_definition["part_index"]),
        "part_name": part_name,
        "collection_name": str(part_definition["collection_name"]),
        "implicit": bool(part_definition["implicit"]),
        "resource_token": part_token,
        "region_hash": region_hash,
        "source_ib_hash": ib_hash,
        "vertex_count": vertex_cursor,
        "index_count": len(all_indices),
        "ib_format": ib_format,
        "triangle_count": len(all_indices) // 3,
        "buffers": files,
        "draws": draw_records,
        "expected_palette_file": bmc_palette_file,
        "expected_palette_provider": "exported_vertex_groups",
        "palette_entries": palette_entries,
        "bmc_resource_suffix": f"{bmc_ib_hash}_{bmc_match_index_count}_{bmc_chunk_index}",
        "bmc_chunk_collection_name": f"{bmc_ib_hash}-{int(bmc_match_index_count)}-{bmc_chunk_index}",
        "bmc_identity_source": bmc_identity_source,
        "bmc_match_index_count": int(bmc_match_index_count),
        "bmc_chunk_index": int(bmc_chunk_index),
        "local_palette_count": palette_entry_count,
        "required_local_palette_count": required_local_palette_count,
        "shapekey_record_count": shapekey_record_count,
        "shapekey_static_row_count": shapekey_static_row_count,
    }


def _collection_json_text(collection: bpy.types.Collection, key: str) -> dict[str, object]:
    text_name = _optional_str_collection_prop(collection, key)
    if not text_name:
        return {}
    text = bpy.data.texts.get(text_name)
    if text is None:
        return {}
    try:
        payload = json.loads(text.as_string())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _bone_merge_dispatches(source_collection: bpy.types.Collection | None) -> list[dict[str, object]]:
    if source_collection is None:
        return []
    payload = _collection_json_text(source_collection, _BONE_MERGE_MAP_TEXT_PROP)
    raw_dispatches = payload.get("dispatches", [])
    if not isinstance(raw_dispatches, list):
        return []
    dispatches: list[dict[str, object]] = []
    for raw_dispatch in raw_dispatches:
        if not isinstance(raw_dispatch, dict):
            continue
        try:
            global_bone_base = int(raw_dispatch["global_bone_base"])
            bone_count = int(raw_dispatch["bone_count"])
        except (KeyError, TypeError, ValueError):
            continue
        if bone_count <= 0:
            continue
        dispatch = dict(raw_dispatch)
        dispatch["global_bone_base"] = global_bone_base
        dispatch["bone_count"] = bone_count
        dispatches.append(dispatch)
    return sorted(
        dispatches,
        key=lambda item: (
            int(item.get("producer_dispatch_index") or 0),
            int(item.get("first_index") or 0),
            int(item.get("index_count") or 0),
        ),
    )


def _draw_toggle_for_draw(draw: dict[str, object]) -> tuple[str, str] | None:
    explicit_name = str(draw.get("draw_toggle", "") or "").strip()
    explicit_key = str(draw.get("draw_toggle_key", "") or "").strip()
    if explicit_name:
        return explicit_name.lstrip("$"), explicit_key or "VK_F10"
    return None


def _yihuan_draw_toggles(parts: list[dict[str, object]]) -> dict[str, str]:
    toggles: dict[str, str] = {}
    for part in parts:
        for draw in part.get("draws", []):
            toggle = _draw_toggle_for_draw(draw)
            if toggle is None:
                continue
            variable_name, key_name = toggle
            toggles.setdefault(variable_name, key_name)
    return toggles


def _key_section_suffix(variable_name: str) -> str:
    chunks = re.split(r"[^0-9A-Za-z]+", variable_name.strip("$"))
    return "".join(chunk[:1].upper() + chunk[1:] for chunk in chunks if chunk) or "DrawToggle"


def _ntmi_part_resource(part: dict[str, object], role: str) -> str:
    return f"ResourcePart_{part['resource_token']}_{role}"


def _ntmi_runtime_shapekey_resource(source_suffix: str, role: str = "") -> str:
    suffix = f"_{role}" if role else ""
    return f"ResourceShapekeyRuntime_{_resource_token(source_suffix)}{suffix}"


def _ntmi_texture_slots(package: dict[str, object]) -> dict[str, dict[str, str]]:
    runtime_contract = dict(package.get("runtime_contract", {}))
    raw_slots = str(runtime_contract.get("texture_slots", "") or "").strip()
    if not raw_slots:
        return {}
    try:
        payload = json.loads(raw_slots)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{package.get('region_hash', 'region')}: invalid texture slot JSON") from exc
    if not isinstance(payload, dict):
        return {}

    slots: dict[str, dict[str, str]] = {}
    for slot, raw_binding in payload.items():
        if slot not in {"ps-t5", "ps-t7", "ps-t8", "ps-t18"}:
            continue
        if not isinstance(raw_binding, dict):
            continue
        source_path = str(raw_binding.get("source_path", "") or "").strip()
        hash_value = str(raw_binding.get("hash", "") or "").strip().lower()
        extension = str(raw_binding.get("extension", "") or "").strip().lower().lstrip(".")
        if not source_path or not hash_value:
            continue
        if not extension:
            extension = Path(source_path).suffix.lower().lstrip(".") or "dds"
        slots[slot] = {
            "hash": hash_value,
            "source_path": source_path,
            "extension": extension,
            "draw_index": str(raw_binding.get("draw_index", "") or "").strip(),
            "ps_hash": str(raw_binding.get("ps_hash", "") or "").strip().lower(),
            "rt_count": str(raw_binding.get("rt_count", "") or "").strip(),
        }
    return slots


@lru_cache(maxsize=256)
def _texture_file_hash(path: str) -> str:
    digest = hashlib.sha1()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:8]


def _object_used_material_indices(obj: bpy.types.Object) -> set[int]:
    mesh = getattr(obj, "data", None)
    if mesh is None or not hasattr(mesh, "polygons"):
        return set()
    return {
        int(polygon.material_index)
        for polygon in mesh.polygons
        if 0 <= int(polygon.material_index) < len(obj.material_slots)
    }


def _image_source_path(image: bpy.types.Image | None) -> str:
    if image is None:
        return ""
    filepath = str(getattr(image, "filepath", "") or getattr(image, "filepath_raw", "") or "").strip()
    if not filepath:
        return ""
    try:
        return bpy.path.abspath(filepath)
    except Exception:  # pragma: no cover - Blender path handling differs across versions.
        return filepath


def _image_node_from_socket(socket, visited: set[int] | None = None):
    if socket is None:
        return None
    if visited is None:
        visited = set()
    for link in getattr(socket, "links", []):
        node = link.from_node
        node_id = id(node)
        if node_id in visited:
            continue
        visited.add(node_id)
        if getattr(node, "bl_idname", "") == "ShaderNodeTexImage":
            return node
        for input_socket in getattr(node, "inputs", []):
            nested = _image_node_from_socket(input_socket, visited)
            if nested is not None:
                return nested
    return None


def _material_input(material: bpy.types.Material, input_name: str):
    if not material or not material.use_nodes or material.node_tree is None:
        return None
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        return None
    return bsdf.inputs.get(input_name)


def _material_texture_paths(material: bpy.types.Material) -> dict[str, str]:
    paths: dict[str, str] = {}
    base_node = _image_node_from_socket(_material_input(material, "Base Color"))
    if base_node is not None:
        source_path = _image_source_path(getattr(base_node, "image", None))
        if source_path:
            paths["ps-t7"] = source_path

    normal_node = _image_node_from_socket(_material_input(material, "Normal"))
    if normal_node is not None:
        source_path = _image_source_path(getattr(normal_node, "image", None))
        if source_path:
            paths["ps-t5"] = source_path
    return paths


def _package_material_texture_candidates(package: dict[str, object]) -> dict[str, dict[str, str]]:
    candidates: dict[str, dict[str, str]] = {}
    for obj in _package_draw_objects(package):
        used_indices = _object_used_material_indices(obj)
        for material_index in sorted(used_indices):
            material = obj.material_slots[material_index].material
            for slot, path in _material_texture_paths(material).items():
                normalized_path = str(Path(path).resolve()).lower()
                candidates.setdefault(slot, {})[normalized_path] = path
    return candidates


def _ntmi_effective_texture_slots(package: dict[str, object]) -> dict[str, dict[str, str]]:
    slots = _ntmi_texture_slots(package)
    for slot, path_by_key in _package_material_texture_candidates(package).items():
        if len(path_by_key) != 1:
            continue
        source_path = next(iter(path_by_key.values()))
        source = Path(source_path)
        if not source.is_file():
            continue

        inherited = dict(slots.get(slot, {}))
        old_path = str(inherited.get("source_path", "") or "")
        if old_path and Path(old_path).resolve() == source.resolve():
            continue

        inherited.update(
            {
                "hash": _texture_file_hash(str(source)),
                "source_path": str(source),
                "extension": source.suffix.lower().lstrip(".") or "dds",
                "source_kind": "material",
            }
        )
        slots[slot] = inherited
    return slots


def _package_draw_objects(package: dict[str, object]) -> list[bpy.types.Object]:
    objects: dict[str, bpy.types.Object] = {}
    for part in package.get("parts", []):
        for draw in part.get("draws", []):
            object_name = str(draw.get("object_name", "") or "")
            obj = bpy.data.objects.get(object_name)
            if obj is not None and obj.type == "MESH":
                objects[obj.name] = obj
    return sorted(objects.values(), key=lambda item: item.name)


def _copy_texture_as_is(source_path: Path, destination: Path):
    _ensure_directory(destination.parent)
    shutil.copy2(source_path, destination)


def _preflight_ntmi_textures(region_packages: list[dict[str, object]]) -> list[str]:
    warnings: list[str] = []
    required_visible_slots = ("ps-t5", "ps-t7")

    for package in region_packages:
        region_label = _ntmi_region_resource_token(package)
        slots = _ntmi_effective_texture_slots(package)
        if not slots:
            warnings.append(
                f"{region_label}: no texture slots recorded; generated INI will not bind ps-t5/7/8/18 explicitly."
            )
        else:
            for slot in required_visible_slots:
                if slot not in slots:
                    warnings.append(f"{region_label}: missing {slot}; material may rely on native PS bindings.")

        for slot, binding in sorted(slots.items()):
            source_path = Path(binding["source_path"])
            if not source_path.is_file():
                raise ValueError(f"{region_label} {slot}: texture source file is missing: {source_path}")
            if not binding.get("draw_index") or not binding.get("ps_hash") or not binding.get("rt_count"):
                warnings.append(
                    f"{region_label} {slot}: texture metadata is incomplete; re-run FrameAnalysis/Profile "
                    "before relying on material draw grouping."
                )

        for slot, path_by_key in sorted(_package_material_texture_candidates(package).items()):
            if len(path_by_key) > 1:
                warnings.append(
                    f"{region_label} {slot}: multiple material textures are used in one region. "
                    "Material draw grouping is pending, so exporter will keep the region-level texture binding for now."
                )

        for obj in _package_draw_objects(package):
            used_indices = _object_used_material_indices(obj)
            if len(used_indices) > 1:
                warnings.append(
                    f"{region_label}: object '{obj.name}' uses {len(used_indices)} material slots. "
                    "Current exporter still emits one draw order per object; material draw grouping is pending."
                )

    # Keep UI reports readable; repeated objects can otherwise spam the same warning many times.
    return list(dict.fromkeys(warnings))


def _ntmi_region_resource_token(package: dict[str, object]) -> str:
    region_hash = str(package["region_hash"])
    index_count = package.get("original_match_index_count")
    first_index = package.get("region_first_index")
    if index_count is None or first_index is None:
        return _resource_token(region_hash)
    return _resource_token(f"{region_hash}_{int(index_count)}_{int(first_index)}")


def _ntmi_texture_resource_name(package: dict[str, object], slot: str) -> str:
    slot_token = slot.replace("ps-", "").replace("-", "_").upper()
    return f"ResourceTexture_{_ntmi_region_resource_token(package)}_{slot_token}"


def _ntmi_texture_filename(package: dict[str, object], slot: str, binding: dict[str, str]) -> str:
    slot_token = slot.replace("ps-", "").replace("-", "_")
    hash_value = _resource_token(binding["hash"])
    source_path = Path(str(binding.get("source_path", "") or ""))
    extension = str(binding.get("extension", "") or "").strip().lower().lstrip(".")
    if not extension:
        extension = source_path.suffix.lower().lstrip(".") or "dds"
    extension = re.sub(r"[^0-9a-z]+", "", extension) or "dds"
    return f"Texture/{_ntmi_region_resource_token(package)}-{slot_token}-{hash_value}.{extension}"


def _ntmi_palette_resource(part: dict[str, object]) -> str:
    return f"ResourcePalette_{part['resource_token']}"


def _parse_collector_key(collect_key: str) -> int:
    match = _COLLECT_KEY_RE.fullmatch(str(collect_key or "").strip())
    if not match:
        raise ValueError(
            f"Invalid NTMI collector collect key '{collect_key}'. "
            "Expected a flat uint cb0 key like cs-cb0[1]. Re-run FrameAnalysis/Profile."
        )
    return int(match.group("lane"))


def _parse_finish_condition_terms(finish_condition: str) -> dict[int, int]:
    raw_terms = [term.strip() for term in str(finish_condition or "").split("&&") if term.strip()]
    if len(raw_terms) < 2:
        raise ValueError(
            f"Invalid NTMI collector finish condition '{finish_condition}'. "
            "Expected at least start and count guards, for example cs-cb0[1] == start && cs-cb0[3] == count."
        )

    terms: dict[int, int] = {}
    for raw_term in raw_terms:
        match = _COLLECT_FINISH_TERM_RE.fullmatch(raw_term)
        if not match:
            raise ValueError(
                f"Invalid NTMI collector finish condition term '{raw_term}'. "
                "Only flat uint cb0 comparisons like cs-cb0[3] == 386 are supported."
            )
        lane = int(match.group("lane"))
        value = int(match.group("value"))
        existing_value = terms.get(lane)
        if existing_value is not None and existing_value != value:
            raise ValueError(
                f"Invalid NTMI collector finish condition '{finish_condition}': "
                f"cs-cb0[{lane}] is compared to multiple values."
            )
        terms[lane] = value
    return terms


def _validate_ntmi_collector_config(collector: dict[str, str]):
    group_slot = str(collector["group_slot"]).strip()
    if group_slot not in {"cs-u0", "cs-u1"}:
        raise ValueError(f"Invalid NTMI collector group slot '{group_slot}'. Expected cs-u0 or cs-u1.")

    for label, key in (
        ("collector u0 hash", "match_cs_u0_hash"),
        ("collector u1 hash", "match_cs_u1_hash"),
    ):
        value = str(collector[key]).strip()
        if not _RESOURCE_HASH_RE.fullmatch(value):
            raise ValueError(
                f"Invalid NTMI {label} '{value}'. Re-run FrameAnalysis/Profile with the current analyzer."
            )

    t0_hash = str(collector.get("match_cs_t0_hash", "") or "").strip()
    if t0_hash and not _RESOURCE_HASH_RE.fullmatch(t0_hash):
        raise ValueError(
            f"Invalid NTMI collector t0 hash '{t0_hash}'. Re-run FrameAnalysis/Profile with the current analyzer."
        )

    collect_lane = _parse_collector_key(collector["collect_key"])
    finish_terms = _parse_finish_condition_terms(collector["finish_condition"])
    if collect_lane not in finish_terms:
        raise ValueError(
            f"Invalid NTMI collector finish condition '{collector['finish_condition']}': "
            f"it must include the collect key lane cs-cb0[{collect_lane}]."
        )

    collect_value = finish_terms[collect_lane]
    if not any(lane != collect_lane and value != collect_value for lane, value in finish_terms.items()):
        raise ValueError(
            f"Invalid NTMI collector finish condition '{collector['finish_condition']}'. "
            "It looks like duplicated start-lane data from an old analyzer. "
            "Re-run FrameAnalysis/Profile; the condition must include a real count/disambiguation lane."
        )


def _preflight_source_collector_config(collection: bpy.types.Collection):
    def required_prop(key: str, label: str) -> str:
        value = _optional_str_collection_prop(collection, key)
        if not value:
            raise ValueError(
                f"Collection '{collection.name}' is missing NTMI collector field '{label}'. "
                "Run Analyze Frame Resources before exporting INI."
            )
        return value

    collector = {
        "group_slot": required_prop(_COLLECTOR_GROUP_SLOT_PROP, "collector group slot"),
        "match_cs_t0_hash": _optional_str_collection_prop(collection, _COLLECTOR_T0_HASH_PROP),
        "match_cs_u0_hash": required_prop(_COLLECTOR_U0_HASH_PROP, "collector u0 hash"),
        "match_cs_u1_hash": required_prop(_COLLECTOR_U1_HASH_PROP, "collector u1 hash"),
        "collect_key": required_prop(_COLLECTOR_COLLECT_KEY_PROP, "collector collect key"),
        "finish_condition": required_prop(_COLLECTOR_FINISH_CONDITION_PROP, "collector finish condition"),
    }
    _validate_ntmi_collector_config(collector)


def _ntmi_collector_config(
    source_collection: bpy.types.Collection,
    region_packages: list[dict[str, object]],
) -> dict[str, str]:
    contracts = [dict(package.get("runtime_contract", {})) for package in region_packages]

    def first_contract_value(key: str, default: str = "") -> str:
        for contract in contracts:
            value = str(contract.get(key, "") or "").strip()
            if value:
                return value
        return default

    def required_value(label: str, source_key: str, contract_key: str, fallback_contract_key: str = "") -> str:
        value = _optional_str_collection_prop(source_collection, source_key)
        if not value:
            value = first_contract_value(contract_key)
        if not value and fallback_contract_key:
            value = first_contract_value(fallback_contract_key)
        if not value:
            raise ValueError(
                f"Missing NTMI collector field '{label}'. Re-run FrameAnalysis/Profile with the current analyzer "
                "or set the corresponding collection custom property before exporting INI."
            )
        return value

    def optional_value(source_key: str, contract_key: str, fallback_contract_key: str = "") -> str:
        value = _optional_str_collection_prop(source_collection, source_key)
        if not value:
            value = first_contract_value(contract_key)
        if not value and fallback_contract_key:
            value = first_contract_value(fallback_contract_key)
        return value

    finish_condition = required_value(
        "collector finish condition",
        _COLLECTOR_FINISH_CONDITION_PROP,
        "collector_finish_condition",
    )

    collector = {
        "group_slot": required_value("collector group slot", _COLLECTOR_GROUP_SLOT_PROP, "collector_group_slot"),
        "match_cs_t0_hash": optional_value(_COLLECTOR_T0_HASH_PROP, "collector_t0_hash"),
        "match_cs_u0_hash": required_value("collector u0 hash", _COLLECTOR_U0_HASH_PROP, "collector_u0_hash"),
        "match_cs_u1_hash": required_value("collector u1 hash", _COLLECTOR_U1_HASH_PROP, "collector_u1_hash"),
        "collect_key": required_value("collector collect key", _COLLECTOR_COLLECT_KEY_PROP, "collector_collect_key"),
        "finish_condition": finish_condition,
    }
    _validate_ntmi_collector_config(collector)
    return collector


def _ntmi_region_match_hash(package: dict[str, object], key: str) -> str:
    runtime_contract = dict(package.get("runtime_contract", {}))
    value = str(runtime_contract.get(key, "") or "").strip().lower()
    if value:
        return value
    raise ValueError(
        f"{package.get('region_hash', 'region')}: missing NTMI draw match field '{key}'. "
        "FrameAnalysis must provide the static resource hash used by the fast draw override."
    )


def _ntmi_optional_region_match_hash(package: dict[str, object], key: str) -> str:
    runtime_contract = dict(package.get("runtime_contract", {}))
    return str(runtime_contract.get(key, "") or "").strip().lower()


def _append_ntmi_draw_toggle_sections(lines: list[str], parts: list[dict[str, object]], *, source_suffix: str):
    toggles = _yihuan_draw_toggles(parts)
    if not toggles:
        return
    lines.append("[Constants]")
    for variable_name in sorted(toggles):
        lines.append(f"global ${variable_name} = 1")
    lines.append("")
    for variable_name, key_name in sorted(toggles.items()):
        lines.extend(
            [
                f"[KeyNTMI_{source_suffix}_Toggle{_key_section_suffix(variable_name)}]",
                f"key = no_modifiers {key_name}",
                "type = cycle",
                "smart = true",
                f"${variable_name} = 1, 0",
                "",
            ]
        )


def _append_ntmi_texture_sections(
    lines: list[str],
    region_packages: list[dict[str, object]],
    *,
    export_root: Path,
):
    _ensure_directory(export_root / "Texture")
    wrote_any = False
    for package in region_packages:
        for slot, binding in sorted(_ntmi_effective_texture_slots(package).items()):
            source_path = Path(binding["source_path"])
            if not source_path.is_file():
                raise ValueError(
                    f"{package['region_hash']} {slot}: texture dump file is missing: {source_path}"
            )
            filename = _ntmi_texture_filename(package, slot, binding)
            destination = export_root / filename
            _copy_texture_as_is(source_path, destination)
            lines.extend(
                [
                    f"[{_ntmi_texture_resource_name(package, slot)}]",
                    f"filename = {filename}",
                    "",
                ]
            )
            wrote_any = True
    if wrote_any and lines and lines[-1] != "":
        lines.append("")


def _append_ntmi_resource_sections(lines: list[str], parts: list[dict[str, object]], *, source_suffix: str):
    runtime_shapekey_file = ""
    runtime_shapekey_count = 0
    for part in parts:
        if part.get("runtime_shapekey_file"):
            runtime_shapekey_file = str(part["runtime_shapekey_file"])
            runtime_shapekey_count = int(part.get("runtime_shapekey_count") or 0)
            break
    if runtime_shapekey_file and runtime_shapekey_count > 0:
        lines.extend(
            [
                f"[{_ntmi_runtime_shapekey_resource(source_suffix, 'UAV')}]",
                "type = RWBuffer",
                "format = R32_FLOAT",
                f"array = {runtime_shapekey_count}",
                "",
                f"[{_ntmi_runtime_shapekey_resource(source_suffix)}]",
                "type = Buffer",
                "format = R32_FLOAT",
                f"array = {runtime_shapekey_count}",
                f"filename = Buffer/{runtime_shapekey_file}",
                "",
            ]
        )

    for part in sorted(parts, key=lambda item: str(item["resource_token"])):
        token = str(part["resource_token"])
        buffers = dict(part["buffers"])
        vertex_count = int(part["vertex_count"])
        position_float_count = vertex_count * 3
        normal_row_count = vertex_count * 2
        dynamic_slots = int(part.get("dynamic_slots") or _NTMI_DEFAULT_DYNAMIC_SLOTS)

        lines.extend(
            [
                f"[{_ntmi_palette_resource(part)}]",
                "type = Buffer",
                "format = R32_UINT",
                f"filename = Buffer/{part['expected_palette_file']}",
                "",
                f"; [part:{part['part_name']}]",
                f"[{_ntmi_part_resource(part, 'RuntimeSkinnedPosition_UAV')}]",
                f"dynamic_slots = {dynamic_slots}",
                "type = RWBuffer",
                "format = R32_FLOAT",
                f"array = {position_float_count}",
                "",
                f"[{_ntmi_part_resource(part, 'RuntimeSkinnedPosition')}]",
                f"dynamic_slots = {dynamic_slots}",
                "type = Buffer",
                "format = R32_FLOAT",
                f"array = {position_float_count}",
                "",
                f"[{_ntmi_part_resource(part, 'RuntimeSkinnedPositionVB')}]",
                f"dynamic_slots = {dynamic_slots}",
                "type = Buffer",
                "stride = 12",
                "",
                f"[{_ntmi_part_resource(part, 'RuntimeSkinnedNormal_UAV')}]",
                f"dynamic_slots = {dynamic_slots}",
                "type = RWBuffer",
                "format = R16G16B16A16_SNORM",
                f"array = {normal_row_count}",
                "",
                f"[{_ntmi_part_resource(part, 'RuntimeSkinnedNormal')}]",
                f"dynamic_slots = {dynamic_slots}",
                "type = Buffer",
                "format = R16G16B16A16_SNORM",
                f"array = {normal_row_count}",
                "",
                f"[{_ntmi_part_resource(part, 'RuntimePrevSkinnedPosition')}]",
                f"dynamic_slots = {dynamic_slots}",
                f"dynamic_prev_of = {_ntmi_part_resource(part, 'RuntimeSkinnedPosition')}",
                "type = Buffer",
                "format = R32_FLOAT",
                f"array = {position_float_count}",
                "",
                f"[{_ntmi_part_resource(part, 'IB')}]",
                "type = Buffer",
                f"format = {part.get('ib_format', 'DXGI_FORMAT_R16_UINT')}",
                f"filename = Buffer/{buffers['ib']}",
                "",
                f"[{_ntmi_part_resource(part, 'Position')}]",
                "type = Buffer",
                "format = R32_FLOAT",
                f"filename = Buffer/{buffers['vb0_pre_cs']}",
                "",
                f"[{_ntmi_part_resource(part, 'PositionVB')}]",
                "type = Buffer",
                "stride = 12",
                f"filename = Buffer/{buffers['vb0_pre_cs']}",
                "",
                f"[{_ntmi_part_resource(part, 'Blend')}]",
                "type = StructuredBuffer",
                "stride = 8",
                f"filename = Buffer/{buffers['weights']}",
                "",
                f"[{_ntmi_part_resource(part, 'BlendTyped')}]",
                "type = Buffer",
                "format = R32_UINT",
                f"filename = Buffer/{buffers['weights']}",
                "",
                f"[{_ntmi_part_resource(part, 'Normal')}]",
                "type = Buffer",
                "format = R8G8B8A8_SNORM",
                f"filename = Buffer/{buffers['frame_pre_cs']}",
                "",
                f"[{_ntmi_part_resource(part, 'Texcoord')}]",
                "type = Buffer",
                "format = R16G16_FLOAT",
                f"filename = Buffer/{buffers['packed_uv']}",
                "",
                f"[{_ntmi_part_resource(part, 'OutlineParam')}]",
                "type = Buffer",
                "format = R8G8B8A8_UNORM",
                f"filename = Buffer/{buffers['outline_param']}",
                "",
            ]
        )
        if buffers.get("shapekey_static"):
            lines.extend(
                [
                    f"[{_ntmi_part_resource(part, 'ShapekeyStatic')}]",
                    "type = Buffer",
                    "format = R32G32B32A32_FLOAT",
                    f"filename = Buffer/{buffers['shapekey_static']}",
                    "",
                ]
            )


def _append_ntmi_collector(
    lines: list[str],
    *,
    source_suffix: str,
    source_collection: bpy.types.Collection,
    region_packages: list[dict[str, object]],
    parts: list[dict[str, object]],
):
    collector = _ntmi_collector_config(source_collection, region_packages)
    lines.extend(
        [
            "; MARK: Skin dispatch. Collector gathers BoneAtlas pieces, builds RuntimeGlobalT0, then runs skin.",
            f"[CollectorSkinPart_{source_suffix}]",
            f"group = {collector['group_slot']}",
        ]
    )
    if collector["match_cs_t0_hash"]:
        lines.append(f"match_cs_t0_hash = {collector['match_cs_t0_hash']}")
    lines.extend(
        [
            f"match_cs_u0_hash = {collector['match_cs_u0_hash']}",
            f"match_cs_u1_hash = {collector['match_cs_u1_hash']}",
            f"collect = write, cs-t0, {collector['collect_key']}",
            "build = Resource\\NTMIv1\\RuntimeGlobalT0",
        ]
    )
    for part in sorted(parts, key=lambda item: str(item["resource_token"])):
        lines.append(
            "map = "
            f"cs-u1:{_ntmi_part_resource(part, 'RuntimeSkinnedPosition')}, "
            f"cs-u1:{_ntmi_part_resource(part, 'RuntimeSkinnedPositionVB')}, "
            f"cs-u0:{_ntmi_part_resource(part, 'RuntimeSkinnedNormal')}"
        )
    lines.append(f"run = CommandList_SkinParts_{source_suffix}")
    lines.append("")


def _append_ntmi_skin_commandlist(lines: list[str], *, source_suffix: str, parts: list[dict[str, object]]):
    lines.extend(
        [
            f"[CommandList_SkinParts_{source_suffix}]",
            f"{_NTMI_SKIN_T_GLOBAL_T0} = {_NTMI_CORE_GLOBAL_T0_RESOURCE}",
            "",
        ]
    )
    for part in sorted(parts, key=lambda item: str(item["resource_token"])):
        buffers = dict(part.get("buffers", {}))
        use_shapekey = bool(buffers.get("shapekey_static"))
        lines.extend(
            [
                f"{_NTMI_SKIN_T_PALETTE} = {_ntmi_palette_resource(part)}",
                f"{_NTMI_CORE_VERTEX_COUNT} = {int(part['vertex_count'])}",
                f"{_NTMI_SKIN_T_BLEND} = {_ntmi_part_resource(part, 'BlendTyped')}",
                f"{_NTMI_SKIN_T_FRAME} = {_ntmi_part_resource(part, 'Normal')}",
                f"{_NTMI_SKIN_T_POSITION} = {_ntmi_part_resource(part, 'Position')}",
                f"{_NTMI_SKIN_U_NORMAL} = {_ntmi_part_resource(part, 'RuntimeSkinnedNormal_UAV')}",
                f"{_NTMI_SKIN_U_POSITION} = {_ntmi_part_resource(part, 'RuntimeSkinnedPosition_UAV')}",
            ]
        )
        if use_shapekey:
            lines.extend(
                [
                    f"{_NTMI_SKIN_T_SHAPEKEY_STATIC} = {_ntmi_part_resource(part, 'ShapekeyStatic')}",
                    f"{_NTMI_SKIN_T_SHAPEKEY_RUNTIME} = {_ntmi_runtime_shapekey_resource(source_suffix)}",
                    f"run = {_NTMI_CORE_SKIN_SHAPEKEY_COMMAND}",
                ]
            )
        else:
            lines.append(f"run = {_NTMI_CORE_SKIN_COMMAND}")
        lines.extend(
            [
                f"{_ntmi_part_resource(part, 'RuntimeSkinnedPosition')} = copy {_ntmi_part_resource(part, 'RuntimeSkinnedPosition_UAV')}",
                f"{_ntmi_part_resource(part, 'RuntimeSkinnedPositionVB')} = copy {_ntmi_part_resource(part, 'RuntimeSkinnedPosition_UAV')}",
                f"{_ntmi_part_resource(part, 'RuntimeSkinnedNormal')} = copy {_ntmi_part_resource(part, 'RuntimeSkinnedNormal_UAV')}",
                "",
            ]
        )
    lines.extend(
        [
            f"{_NTMI_SKIN_T_GLOBAL_T0} = null",
            f"{_NTMI_SKIN_T_PALETTE} = null",
            f"{_NTMI_SKIN_T_BLEND} = null",
            f"{_NTMI_SKIN_T_FRAME} = null",
            f"{_NTMI_SKIN_T_POSITION} = null",
            f"{_NTMI_SKIN_T_SHAPEKEY_STATIC} = null",
            f"{_NTMI_SKIN_T_SHAPEKEY_RUNTIME} = null",
            f"{_NTMI_SKIN_U_NORMAL} = null",
            f"{_NTMI_SKIN_U_POSITION} = null",
            "",
        ]
    )


def _append_ntmi_draw_lines(lines: list[str], part: dict[str, object]):
    for draw in part["draws"]:
        lines.append(f"; [mesh:{draw['object_name']}] [vertex_count:{draw['vertex_count']}]")
        draw_line = f"drawindexed = {draw['index_count']},{draw['first_index']},0"
        toggle = _draw_toggle_for_draw(draw)
        if toggle is None:
            lines.append(draw_line)
        else:
            variable_name, _ = toggle
            lines.append(f"if ${variable_name} == 1")
            lines.append(f"  {draw_line}")
            lines.append("endif")


def _append_ntmi_texture_bindings_for_package(lines: list[str], package: dict[str, object]):
    for slot in ("ps-t5", "ps-t7", "ps-t8", "ps-t18"):
        if slot not in _ntmi_effective_texture_slots(package):
            continue
        lines.append(f"{slot} = {_ntmi_texture_resource_name(package, slot)}")


def _append_ntmi_draw_overrides(
    lines: list[str],
    region_packages: list[dict[str, object]],
    *,
    include_runtime_skin: bool,
):
    lines.append("; MARK: Draw replacement")
    source_suffix = _yihuan_source_suffix(region_packages)
    for package in region_packages:
        texcoord_hash = _ntmi_region_match_hash(package, "match_vs_texcoord_hash")
        position_hash = _ntmi_region_match_hash(package, "match_vs_position_hash")
        outline_hash = _ntmi_optional_region_match_hash(package, "match_vs_outline_hash")
        source_ib_hash = str(package["source_ib_hash"])
        lines.extend(
            [
                f"[{_region_override_name(package)}]",
                f"hash = {source_ib_hash}",
            ]
        )
        if package.get("region_first_index") is not None:
            lines.append(f"match_first_index = {int(package['region_first_index'])}")
        lines.extend(
            [
                f"match_index_count = {int(package['original_match_index_count'])}",
                "handling = skip",
            ]
        )
        if include_runtime_skin:
            lines.append(f"collector = CollectorSkinPart_{source_suffix}, vb0")
        for part in sorted(package["parts"], key=lambda item: int(item["part_index"])):
            lines.extend(
                [
                    f"; [part:{part['part_name']}] [vertex_count:{part['vertex_count']}]",
                    f"ib = {_ntmi_part_resource(part, 'IB')}",
                    f"match = vb, dynamic, {_ntmi_part_resource(part, 'RuntimeSkinnedPositionVB')}",
                    f"match = vs, dynamic_prev, {_ntmi_part_resource(part, 'RuntimePrevSkinnedPosition')}",
                    f"match = vs, {texcoord_hash}, {_ntmi_part_resource(part, 'Texcoord')}",
                    f"match = vs, {position_hash}, {_ntmi_part_resource(part, 'Position')}",
                    f"match = vs, dynamic, {_ntmi_part_resource(part, 'RuntimeSkinnedNormal')}",
                ]
            )
            if outline_hash:
                lines.append(f"match = vs, {outline_hash}, {_ntmi_part_resource(part, 'OutlineParam')}")
            _append_ntmi_texture_bindings_for_package(lines, package)
            _append_ntmi_draw_lines(lines, part)
        lines.append("")


def _write_ntmi_main_ini(
    *,
    export_root: Path,
    ini_name: str,
    source_collection: bpy.types.Collection,
    region_packages: list[dict[str, object]],
    include_runtime_skin: bool,
) -> Path:
    if not region_packages:
        raise ValueError("Cannot generate INI without region packages.")
    ini_path = export_root / ini_name
    lines: list[str] = []
    all_parts = [part for package in region_packages for part in package["parts"]]
    runtime_suffix = _yihuan_source_suffix(region_packages)
    _append_ntmi_texture_sections(lines, region_packages, export_root=export_root)
    _append_ntmi_draw_toggle_sections(lines, all_parts, source_suffix=runtime_suffix)
    _append_ntmi_resource_sections(lines, all_parts, source_suffix=runtime_suffix)
    if include_runtime_skin:
        _append_ntmi_collector(
            lines,
            source_suffix=runtime_suffix,
            source_collection=source_collection,
            region_packages=region_packages,
            parts=all_parts,
        )
        _append_ntmi_skin_commandlist(lines, source_suffix=runtime_suffix, parts=all_parts)
    _append_ntmi_draw_overrides(lines, region_packages, include_runtime_skin=include_runtime_skin)

    ini_path.write_text("\n".join(lines), encoding="utf-8")
    return ini_path


def _export_region_package(
    *,
    region_collection: bpy.types.Collection,
    source_ib_hash: str,
    buffer_dir: Path,
    require_runtime_contract: bool,
    flip_uv_v: bool,
    default_mirror_flip: bool,
    export_runtime_shapekeys: bool,
    runtime_shapekey_names: list[str],
    runtime_shapekey_initial_weights: dict[str, float],
    log=None,
) -> dict[str, object]:
    region_hash = _region_collection_hash(region_collection)
    region_index_count = _collection_region_index_count(region_collection)
    region_first_index = _collection_region_first_index(region_collection)
    collection_source_hash = _collection_source_ib_hash(region_collection)
    if collection_source_hash and collection_source_hash != source_ib_hash:
        raise ValueError(
            f"Region collection '{region_collection.name}' belongs to source IB {collection_source_hash}, "
            f"but export root is {source_ib_hash}."
        )
    _validate_region_collection_contract(
        region_collection,
        region_index_count=region_index_count,
        region_first_index=region_first_index,
        require_runtime_contract=require_runtime_contract,
    )
    region_runtime_contract = _collection_runtime_contract(
        region_collection,
        region_index_count=region_index_count,
        region_first_index=region_first_index,
    )
    part_definitions = _resolve_export_parts(
        region_collection,
        source_ib_hash=source_ib_hash,
        region_hash=region_hash,
        region_index_count=region_index_count,
        region_first_index=region_first_index,
    )
    if log is not None:
        log(
            f"Region {region_collection.name}: {len(part_definitions)} part(s), "
            f"first_index={region_first_index}, index_count={region_index_count}"
        )

    exported_parts = [
        _export_part_buffers(
            part_definition=part_definition,
            ib_hash=source_ib_hash,
            region_hash=region_hash,
            region_index_count=region_index_count,
            region_first_index=region_first_index,
            region_runtime_contract=region_runtime_contract,
            buffer_dir=buffer_dir,
            flip_uv_v=flip_uv_v,
            default_mirror_flip=default_mirror_flip,
            export_runtime_shapekeys=export_runtime_shapekeys,
            runtime_shapekey_names=runtime_shapekey_names,
            runtime_shapekey_initial_weights=runtime_shapekey_initial_weights,
            log=log,
        )
        for part_definition in part_definitions
    ]
    if not any(
        str(draw.get("slice_hash", "")).lower() == region_hash
        for part in exported_parts
        for draw in part["draws"]
    ):
        raise ValueError(
            f"Export region collection '{region_collection.name}' does not match any imported local/region hash. "
            "The collection name must be the local hash of the objects it contains."
        )

    original_match_index_count, match_index_source = _resolve_original_match_index_count(
        exported_parts,
        region_hash=region_hash,
        region_index_count=region_index_count,
    )
    if log is not None:
        log(
            f"Region {region_collection.name}: done, {sum(int(part['vertex_count']) for part in exported_parts)} verts, "
            f"{sum(int(part['index_count']) for part in exported_parts)} indices"
        )
    return {
        "profile_id": YIHUAN_PROFILE.profile_id,
        "region_hash": region_hash,
        "region_first_index": region_first_index,
        "source_ib_hash": source_ib_hash,
        "original_match_index_count": original_match_index_count,
        "match_index_source": match_index_source,
        "runtime_contract": region_runtime_contract,
        "shape_key_policy": _SHAPE_KEY_BAKE_POLICY,
        "parts": exported_parts,
    }


def export_collection_package(
    *,
    collection_name: str,
    export_dir: str,
    flip_uv_v: bool = False,
    default_mirror_flip: bool = False,
    generate_ini: bool = True,
    export_runtime_shapekeys: bool = False,
    runtime_shapekey_names: str | None = None,
) -> dict[str, object]:
    """Export one strict sourceIB -> region -> part collection tree into runtime replacement assets."""
    collection = _get_collection(collection_name)
    if generate_ini:
        _preflight_source_collector_config(collection)
    export_root = _ensure_directory(Path(export_dir).resolve())
    buffer_dir = _ensure_directory(export_root / "Buffer")
    log, flush_log = _build_export_logger()
    source_ib_hash = _source_root_hash(collection)
    requested_runtime_shapekeys = _parse_runtime_shapekey_names(runtime_shapekey_names)
    runtime_shapekey_order = (
        _runtime_shapekey_order(collection, requested_runtime_shapekeys)
        if export_runtime_shapekeys
        else []
    )
    runtime_shapekey_initial_weights: dict[str, float] = {}
    region_collections = _resolve_region_collections(collection)
    region_packages: list[dict[str, object]] = []
    shapekey_runtime_file = ""
    has_runtime_shapekey_records = False
    texture_warnings: list[str] = []
    ini_file: Path | None = None
    succeeded = False
    try:
        log(
            f"Export start: collection={collection_name}, source_ib={source_ib_hash}, "
            f"regions={len(region_collections)}, shapekeys={'on' if export_runtime_shapekeys else 'off'}"
        )
        for region_collection in region_collections:
            region_packages.append(
                _export_region_package(
                    region_collection=region_collection,
                    source_ib_hash=source_ib_hash,
                    buffer_dir=buffer_dir,
                    require_runtime_contract=generate_ini,
                    flip_uv_v=flip_uv_v,
                    default_mirror_flip=default_mirror_flip,
                    export_runtime_shapekeys=export_runtime_shapekeys,
                    runtime_shapekey_names=runtime_shapekey_order,
                    runtime_shapekey_initial_weights=runtime_shapekey_initial_weights,
                    log=log,
                )
            )

        has_runtime_shapekey_records = any(
            int(part.get("shapekey_record_count") or 0) > 0
            for package in region_packages
            for part in package["parts"]
        )
        if export_runtime_shapekeys and runtime_shapekey_order and has_runtime_shapekey_records:
            shapekey_runtime_file = f"{source_ib_hash}-shapekey-runtime.buf"
            log(
                f"Write runtime shapekey table: {shapekey_runtime_file} "
                f"({len(runtime_shapekey_order)} keys)"
            )
            write_f32_buffer(
                str(buffer_dir / shapekey_runtime_file),
                [runtime_shapekey_initial_weights.get(name, 0.0) for name in runtime_shapekey_order],
            )
            for package in region_packages:
                package["runtime_shapekey_names"] = runtime_shapekey_order
                package["runtime_shapekey_file"] = shapekey_runtime_file
                package["runtime_shapekey_initial_weights"] = dict(runtime_shapekey_initial_weights)
                for part in package["parts"]:
                    part["runtime_shapekey_file"] = shapekey_runtime_file
                    part["runtime_shapekey_count"] = len(runtime_shapekey_order)
        texture_warnings = _preflight_ntmi_textures(region_packages) if generate_ini else []

        if generate_ini:
            log("Write INI")
            ini_file = _write_ntmi_main_ini(
                export_root=export_root,
                ini_name=f"{source_ib_hash}.ini",
                source_collection=collection,
                region_packages=region_packages,
                include_runtime_skin=True,
            )

        total_vertices = sum(int(part["vertex_count"]) for package in region_packages for part in package["parts"])
        total_indices = sum(int(part["index_count"]) for package in region_packages for part in package["parts"])
        total_draws = sum(len(part["draws"]) for package in region_packages for part in package["parts"])
        total_parts = sum(len(package["parts"]) for package in region_packages)
        log(
            f"Export done: {len(region_packages)} region(s), {total_parts} part(s), "
            f"{total_vertices} verts, {total_indices // 3} tris, {total_draws} draws"
        )
        result = {
            "profile_id": YIHUAN_PROFILE.profile_id,
            "collection_name": collection_name,
            "region_hash": str(region_packages[0]["region_hash"]) if len(region_packages) == 1 else "",
            "region_count": len(region_packages),
            "source_ib_hash": source_ib_hash,
            "original_match_index_count": int(region_packages[0]["original_match_index_count"]) if len(region_packages) == 1 else 0,
            "vertex_count": total_vertices,
            "triangle_count": total_indices // 3,
            "slice_count": total_draws,
            "draw_count": total_draws,
            "part_count": total_parts,
            "buffer_dir": str(buffer_dir),
            "hlsl_dir": "",
            "ini_path": "" if ini_file is None else str(ini_file),
            "runtime_ini_path": "",
            "texture_warnings": texture_warnings,
            "runtime_shapekey_count": len(runtime_shapekey_order) if has_runtime_shapekey_records else 0,
        }
        succeeded = True
        return result
    finally:
        flush_log(success=succeeded)
