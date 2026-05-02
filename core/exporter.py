"""Collection export pipeline for the 异环 profile."""

from __future__ import annotations

import json
import math
import re
import struct
from collections import defaultdict
from pathlib import Path

import bpy
from mathutils import Vector

from .game_data import get_game_data_converter
from .hlsl_assets import export_profile_hlsl_assets
from .io import (
    write_float3_buffer,
    write_half2x4_buffer,
    write_snorm8x4_pairs_buffer,
    write_u16_buffer,
    write_u32_buffer,
    write_u8x4_buffer,
    write_weight_pairs_buffer,
)
from .profiles import YIHUAN_PROFILE


_REGION_HASH_RE = re.compile(r"^[0-9A-Fa-f]{8}$")
_REGION_COLLECTION_RE = re.compile(r"^(?P<hash>[0-9A-Fa-f]{8})(?:[-_](?P<count>\d+)(?:[-_](?P<first>\d+))?)?")
_PART_NAME_RE = re.compile(r"^part(?P<index>\d+)", re.IGNORECASE)
_COLLECTION_KIND_PROP = "modimp_kind"
_PROFILE_ID_PROP = "modimp_profile_id"
_SOURCE_IB_HASH_PROP = "modimp_source_ib_hash"
_REGION_HASH_PROP = "modimp_region_hash"
_REGION_INDEX_COUNT_PROP = "modimp_region_index_count"
_REGION_FIRST_INDEX_PROP = "modimp_region_first_index"
_PART_INDEX_PROP = "modimp_part_index"
_PRODUCER_DISPATCH_INDEX_PROP = "modimp_producer_dispatch_index"
_PRODUCER_CS_HASH_PROP = "modimp_producer_cs_hash"
_PRODUCER_T0_HASH_PROP = "modimp_producer_t0_hash"
_LAST_CS_HASH_PROP = "modimp_last_cs_hash"
_LAST_CS_CB0_HASH_PROP = "modimp_last_cs_cb0_hash"
_LAST_CONSUMER_DRAW_INDEX_PROP = "modimp_last_consumer_draw_index"
_DEPTH_VS_HASHES_PROP = "modimp_depth_vs_hashes"
_GBUFFER_VS_HASHES_PROP = "modimp_gbuffer_vs_hashes"
_STAGE_MAP_TEXT_PROP = "modimp_stage_map_text"
_BONE_MERGE_MAP_TEXT_PROP = "modimp_bone_merge_map_text"
_BMC_IB_HASH_PROP = "modimp_bmc_ib_hash"
_BMC_MATCH_INDEX_COUNT_PROP = "modimp_bmc_match_index_count"
_BMC_CHUNK_INDEX_PROP = "modimp_bmc_chunk_index"
_DRAW_TOGGLE_PROP = "modimp_draw_toggle"
_DRAW_TOGGLE_KEY_PROP = "modimp_draw_toggle_key"
_SHAPE_KEY_BAKE_POLICY = "bake_current_relative_mix_to_base_mesh_copy"
_YIHUAN_DEFAULT_PRODUCER_CS_HASH = "f33fea3cca2704e4"
_YIHUAN_DEFAULT_LAST_CS_HASH = "f33fea3cca2704e4"
_YIHUAN_DEFAULT_LAST_CS_CB0_HASH = "7816b819"
_YIHUAN_CS_FILTER_INDICES = {
    "f33fea3cca2704e4": 3300,
    "1e2a9061eadfeb6c": 3301,
}
_YIHUAN_BONESTORE_NAMESPACE = "YihuanBoneStore"
_YIHUAN_RUNTIME_HLSL_SUFFIX = "85b15a7f"
_YIHUAN_DEPTH_VS_FILTER_BASE = 4100
_YIHUAN_GBUFFER_VS_FILTER_BASE = 4200
_YIHUAN_REVERSE_EXPORT_WINDING = True
_YIHUAN_BODY_TEXTURE_RESOURCES = (
    ("ResourceT5", "Texture/NM.dds"),
    ("ResourceT7", "Texture/Body.dds"),
    ("ResourceT8", "Texture/t8.dds"),
    ("ResourceT18", "Texture/t18.dds"),
)
_YIHUAN_DEFAULT_DRAW_TOGGLES = {
    "4c512c5c-52407-0.005": ("bohe_draw_52407_0_005", "VK_NUMPAD8"),
    "4c512c5c-52407-0.006": ("bohe_draw_lower_group", "VK_NUMPAD2"),
    "4c512c5c-62346-52407.002": ("bohe_draw_lower_group", "VK_NUMPAD2"),
}


def _hash_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = re.split(r"[,;\s]+", str(value))
    hashes: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        normalized = str(item or "").strip().lower()
        if not normalized:
            continue
        if not re.fullmatch(r"[0-9a-f]{16}", normalized):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        hashes.append(normalized)
    return tuple(hashes)


def _yihuan_bonestore_namespace(region_packages: list[dict[str, object]]) -> str:
    source_ib_hash = ""
    if region_packages:
        source_ib_hash = str(region_packages[0].get("source_ib_hash", "") or "").strip().lower()
    if not _REGION_HASH_RE.fullmatch(source_ib_hash):
        return _YIHUAN_BONESTORE_NAMESPACE
    return f"{_YIHUAN_BONESTORE_NAMESPACE}_{source_ib_hash}"


def _yihuan_source_suffix(region_packages: list[dict[str, object]]) -> str:
    if region_packages:
        source_ib_hash = str(region_packages[0].get("source_ib_hash", "") or "").strip().lower()
        if _REGION_HASH_RE.fullmatch(source_ib_hash):
            return source_ib_hash
    return "shared"


def _yihuan_restore_resource(resource_suffix: str, slot_name: str) -> str:
    return f"ResourceYihuan_{resource_suffix}_Restore{slot_name}"


def _yihuan_runtime_resource(resource_suffix: str, name: str) -> str:
    return f"ResourceYihuan_{resource_suffix}_Runtime{name}"


def _yihuan_texture_resource(resource_suffix: str, resource_name: str) -> str:
    short_name = resource_name.removeprefix("Resource")
    return f"ResourceYihuan_{resource_suffix}_{short_name}"


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


def _optional_int_collection_prop(collection: bpy.types.Collection, key: str) -> int | None:
    if key not in collection:
        return None
    try:
        return int(collection[key])
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


def _triangulated_mesh_copy(
    obj: bpy.types.Object,
    *,
    depsgraph: bpy.types.Depsgraph | None = None,
) -> tuple[bpy.types.Mesh, list[str]]:
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
    baked_shape_keys = _active_shape_key_mix_names(obj)
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


def _normalized_vector(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = (vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2]) ** 0.5
    if length <= 1e-8:
        return (0.0, 0.0, 0.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


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
    producer_dispatch_index: int | None = None,
    producer_cs_hash: str | None = None,
    producer_t0_hash: str | None = None,
    last_cs_hash: str | None = None,
    last_cs_cb0_hash: str | None = None,
    last_consumer_draw_index: int | None = None,
) -> dict[str, object]:
    if obj.type != "MESH":
        raise ValueError(f"{obj.name}: only mesh objects can be exported")
    profile_id = (profile_id or YIHUAN_PROFILE.profile_id).strip()
    if profile_id != YIHUAN_PROFILE.profile_id:
        raise ValueError(f"{obj.name}: unsupported profile id")
    converter = get_game_data_converter(profile_id)

    depsgraph = bpy.context.evaluated_depsgraph_get()
    missing_optional_attributes: list[str] = []
    mesh_copy, baked_shape_keys = _triangulated_mesh_copy(obj, depsgraph=depsgraph)
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

    positions: list[tuple[float, float, float]] = []
    packed_uv_entries: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    out_blend_indices: list[tuple[int, int, int, int]] = []
    out_blend_weights: list[tuple[int, int, int, int]] = []
    decoded_tangents: list[tuple[float, float, float]] = []
    decoded_normals: list[tuple[float, float, float]] = []
    decoded_signs: list[float] = []
    outline_params: list[tuple[int, int, int, int]] = []
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
                    positions.append(
                        converter.from_blender_position(
                            (float(source_position.x), float(source_position.y), float(source_position.z))
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
                    out_vertex_index = len(positions) - 1
                    remap[key] = out_vertex_index
                triangle.append(out_vertex_index)
            # The profile transform mirrors handedness relative to Blender's
            # viewport. Reverse only the index winding so culling/front-face
            # matches the game, while keeping custom normal/tangent data intact.
            if _YIHUAN_REVERSE_EXPORT_WINDING:
                triangles.append((triangle[0], triangle[2], triangle[1]))
            else:
                triangles.append(tuple(triangle))
    finally:
        bpy.data.meshes.remove(mesh_copy)

    frame_a, frame_b = converter.encode_pre_cs_frames(decoded_tangents, decoded_normals, decoded_signs)
    if original_first_index is None:
        original_first_index = 0
    if original_index_count is None:
        original_index_count = len(triangles) * 3
    if producer_dispatch_index is None:
        producer_dispatch_index = 0
    producer_cs_hash = (producer_cs_hash or _YIHUAN_DEFAULT_PRODUCER_CS_HASH).strip()
    producer_t0_hash = (producer_t0_hash or "").strip()
    last_cs_hash = (last_cs_hash or _YIHUAN_DEFAULT_LAST_CS_HASH).strip()
    last_cs_cb0_hash = (last_cs_cb0_hash or _YIHUAN_DEFAULT_LAST_CS_CB0_HASH).strip()
    if last_consumer_draw_index is None:
        last_consumer_draw_index = 0

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
        "local_palette_count": local_palette_count,
        "baked_shape_keys": baked_shape_keys,
        "missing_optional_attributes": sorted(set(missing_optional_attributes)),
        "original_first_index": int(original_first_index),
        "original_index_count": int(original_index_count),
        "producer_dispatch_index": int(producer_dispatch_index),
        "producer_cs_hash": producer_cs_hash,
        "producer_t0_hash": producer_t0_hash,
        "last_cs_hash": last_cs_hash,
        "last_cs_cb0_hash": last_cs_cb0_hash,
        "last_consumer_draw_index": int(last_consumer_draw_index),
    }


def _ceil_div(value: int, divisor: int) -> int:
    return max(1, (int(value) + int(divisor) - 1) // int(divisor))


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
    producer_dispatch_index = _optional_int_collection_prop(collection, _PRODUCER_DISPATCH_INDEX_PROP)
    last_consumer_draw_index = _optional_int_collection_prop(collection, _LAST_CONSUMER_DRAW_INDEX_PROP)
    return {
        "profile_id": profile_id,
        "original_first_index": region_first_index,
        "original_index_count": region_index_count,
        "producer_dispatch_index": producer_dispatch_index,
        "producer_cs_hash": _optional_str_collection_prop(collection, _PRODUCER_CS_HASH_PROP),
        "producer_t0_hash": _optional_str_collection_prop(collection, _PRODUCER_T0_HASH_PROP),
        "last_cs_hash": _optional_str_collection_prop(collection, _LAST_CS_HASH_PROP),
        "last_cs_cb0_hash": _optional_str_collection_prop(collection, _LAST_CS_CB0_HASH_PROP),
        "last_consumer_draw_index": last_consumer_draw_index,
        "depth_vs_hashes": _optional_str_collection_prop(collection, _DEPTH_VS_HASHES_PROP),
        "gbuffer_vs_hashes": _optional_str_collection_prop(collection, _GBUFFER_VS_HASHES_PROP),
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
            _PRODUCER_CS_HASH_PROP,
            _PRODUCER_T0_HASH_PROP,
            _LAST_CS_HASH_PROP,
            _LAST_CS_CB0_HASH_PROP,
        ):
            if not _optional_str_collection_prop(collection, key):
                missing.append(key)
    if missing:
        raise ValueError(
            f"Region collection '{collection.name}' is missing export contract field(s): {', '.join(missing)}. "
            "Use Create Export Collection/Part after resolving the source IB from FrameAnalysis, "
            "or set these custom properties on the region collection."
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
        f"Part collection '{collection.name}' must be created by Create Export Part or named like part00."
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
            "Use Create Export Part to create <sourceIB>/<regionHash>/partNN."
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
                "Split this object before export; the first bridge pass does not cut one object by triangles."
            )
        global_bone_ids.update(object_bones)

    if not global_bone_ids:
        raise ValueError(f"{part_name}: no numeric vertex groups were found in this export IB.")
    if len(global_bone_ids) > 0x100:
        raise ValueError(
            f"{part_name}: objects in this export IB use {len(global_bone_ids)} unique bones. "
            "Run the collection split tool so each IB sub-collection uses <= 256 bones."
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
    draw_records: list[dict[str, object]] = []

    vertex_cursor = 0
    index_cursor = 0
    required_local_palette_count = 0
    for draw_index, obj in enumerate(mesh_objects):
        payload = _extract_object_payload(
            obj,
            flip_uv_v=flip_uv_v,
            bone_to_local=bone_to_local,
            profile_id=str(region_runtime_contract.get("profile_id") or ""),
            original_first_index=region_first_index,
            original_index_count=region_index_count,
            producer_dispatch_index=region_runtime_contract.get("producer_dispatch_index"),
            producer_cs_hash=str(region_runtime_contract.get("producer_cs_hash") or ""),
            producer_t0_hash=str(region_runtime_contract.get("producer_t0_hash") or ""),
            last_cs_hash=str(region_runtime_contract.get("last_cs_hash") or ""),
            last_cs_cb0_hash=str(region_runtime_contract.get("last_cs_cb0_hash") or ""),
            last_consumer_draw_index=region_runtime_contract.get("last_consumer_draw_index"),
        )
        positions = payload["positions"]
        triangles = payload["triangles"]
        packed_uv_entries = payload["packed_uv_entries"]
        blend_indices = payload["blend_indices"]
        blend_weights = payload["blend_weights"]
        frame_a = payload["frame_a"]
        frame_b = payload["frame_b"]
        outline_params = payload["outline_params"]

        if len(positions) != len(packed_uv_entries):
            raise ValueError(f"{obj.name}: packed UV entry count does not match position count")
        if len(positions) != len(blend_indices) or len(positions) != len(blend_weights):
            raise ValueError(f"{obj.name}: blend payload count does not match position count")
        if len(positions) != len(frame_a) or len(positions) != len(frame_b):
            raise ValueError(f"{obj.name}: frame payload count does not match position count")
        if len(positions) != len(outline_params):
            raise ValueError(f"{obj.name}: outline parameter count does not match position count")

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
                "producer_dispatch_index": int(payload["producer_dispatch_index"]),
                "producer_cs_hash": str(payload["producer_cs_hash"]),
                "producer_t0_hash": str(payload["producer_t0_hash"]),
                "last_cs_hash": str(payload["last_cs_hash"]),
                "last_cs_cb0_hash": str(payload["last_cs_cb0_hash"]),
                "last_consumer_draw_index": int(payload["last_consumer_draw_index"]),
                "shape_key_policy": _SHAPE_KEY_BAKE_POLICY,
                "baked_shape_keys": list(payload["baked_shape_keys"]),
                "missing_optional_attributes": list(payload["missing_optional_attributes"]),
                "local_palette_count": int(payload["local_palette_count"]),
                "local_t0_rows": int(payload["local_palette_count"]) * 3,
            }
        )

    if vertex_cursor > 0xFFFF:
        raise ValueError(
            f"Export part '{part_name}' has {vertex_cursor} vertices, exceeding the R16_UINT index window "
            "(65535 vertices for this exporter). Split this part into smaller export child collections."
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
        "skin_cb0": f"{part_name}-cb0.buf",
    }

    write_u16_buffer(str(buffer_dir / files["ib"]), all_indices)
    write_float3_buffer(str(buffer_dir / files["vb0_pre_cs"]), all_positions)
    write_weight_pairs_buffer(str(buffer_dir / files["weights"]), all_blend_indices, all_blend_weights)
    write_snorm8x4_pairs_buffer(str(buffer_dir / files["frame_pre_cs"]), all_frame_a, all_frame_b)
    write_half2x4_buffer(str(buffer_dir / files["packed_uv"]), all_packed_uv_entries)
    write_u8x4_buffer(str(buffer_dir / files["outline_param"]), all_outline_params)
    write_u32_buffer(str(buffer_dir / files["skin_cb0"]), [0, vertex_cursor, 0, palette_entry_count])
    write_u32_buffer(str(buffer_dir / bmc_palette_file), palette_entries)

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
        "triangle_count": len(all_indices) // 3,
        "buffers": files,
        "draws": draw_records,
        "producer_t0_hash": str(region_runtime_contract.get("producer_t0_hash") or ""),
        "last_cs_cb0_hash": str(region_runtime_contract.get("last_cs_cb0_hash") or ""),
        "last_cs_hash": str(region_runtime_contract.get("last_cs_hash") or ""),
        "expected_palette_file": bmc_palette_file,
        "expected_palette_provider": "exported_vertex_groups",
        "palette_entries": palette_entries,
        "bmc_resource_suffix": f"{bmc_ib_hash}_{bmc_match_index_count}_{bmc_chunk_index}",
        "bmc_chunk_collection_name": f"{bmc_ib_hash}-{int(bmc_match_index_count)}-{bmc_chunk_index}",
        "bmc_identity_source": bmc_identity_source,
        "bmc_match_index_count": int(bmc_match_index_count),
        "bmc_chunk_index": int(bmc_chunk_index),
        "local_palette_count": palette_entry_count,
        "local_t0_rows": palette_entry_count * 3,
        "required_local_palette_count": required_local_palette_count,
    }


def _draws_for_ini(parts: list[dict[str, object]]) -> list[dict[str, object]]:
    draws: list[dict[str, object]] = []
    for part in parts:
        draws.extend(part["draws"])
    return draws


def _float_to_u32(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", float(value)))[0]


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


def _part_original_identity(part: dict[str, object]) -> tuple[int | None, int | None]:
    draws = list(part.get("draws", []))
    if not draws:
        return None, None
    first_draw = draws[0]
    try:
        first_index = int(first_draw["original_first_index"])
        index_count = int(first_draw["original_index_count"])
    except (KeyError, TypeError, ValueError):
        return None, None
    return first_index, index_count


def _dispatch_matches_part(dispatch: dict[str, object], part: dict[str, object]) -> bool:
    first_index, index_count = _part_original_identity(part)
    if first_index is None or index_count is None:
        return False
    try:
        return int(dispatch.get("first_index")) == first_index and int(dispatch.get("index_count")) == index_count
    except (TypeError, ValueError):
        return False


def _global_bone_count(parts: list[dict[str, object]], source_collection: bpy.types.Collection | None = None) -> int:
    max_from_palette = max(
        (max((int(value) for value in part.get("palette_entries", [])), default=-1) for part in parts),
        default=-1,
    ) + 1
    max_from_dispatch = max(
        (
            int(dispatch["global_bone_base"]) + int(dispatch["bone_count"])
            for dispatch in _bone_merge_dispatches(source_collection)
        ),
        default=0,
    )
    return max(1, max_from_palette, max_from_dispatch)


def _character_meta_rows(
    parts: list[dict[str, object]],
    source_collection: bpy.types.Collection | None = None,
) -> list[tuple[int, int, int, int]]:
    pose_slots = 16
    max_vertex_count = max((int(part["vertex_count"]) for part in parts), default=1)
    global_bone_count = _global_bone_count(parts, source_collection)
    global_row_count = global_bone_count * 3
    dispatches = _bone_merge_dispatches(source_collection)
    if source_collection is not None and not dispatches:
        raise ValueError(
            f"Collection '{source_collection.name}' has no BoneMergeMap dispatch rows. "
            "Run FrameAnalysis/Profile first so CharacterMetaTable can collect native bones."
        )
    max_palette_global = max(
        (max((int(value) for value in part.get("palette_entries", [])), default=-1) for part in parts),
        default=-1,
    )
    if dispatches and max_palette_global >= 0:
        dispatches = [
            dispatch for dispatch in dispatches
            if int(dispatch["global_bone_base"]) <= max_palette_global
        ]

    feature_dispatch = next(
        (
            dispatch for dispatch in dispatches
            for part in parts
            if _dispatch_matches_part(dispatch, part)
        ),
        dispatches[0] if dispatches else {},
    )
    feature_start = int(feature_dispatch.get("producer_start_vertex") or 0)
    feature_count = int(feature_dispatch.get("producer_vertex_count") or max_vertex_count)
    feature_count = max(1, feature_count)
    feature_samples = min(4096, feature_count)
    feature_threshold = _float_to_u32(0.001)

    feature_row = 4
    collect_row_base = 5
    rows: list[tuple[int, int, int, int]] = [
        (0, 0, 0, 0),
        (pose_slots, max_vertex_count, 0, 0),
        (global_bone_count, global_row_count, 0, len(dispatches)),
        (feature_row, 0, collect_row_base, 0),
        (feature_start, feature_count, feature_samples, feature_threshold),
    ]
    def fallback_vertex_window(dispatch: dict[str, object]) -> tuple[int, int]:
        for part in parts:
            if not _dispatch_matches_part(dispatch, part):
                continue
            return 0, max(1, int(part.get("vertex_count") or max_vertex_count))
        return 0, max(1, max_vertex_count)

    for dispatch in dispatches:
        start_vertex = int(dispatch.get("producer_start_vertex") or dispatch.get("start_vertex") or 0)
        vertex_count = int(dispatch.get("producer_vertex_count") or dispatch.get("vertex_count") or 0)
        if vertex_count <= 0:
            start_vertex, vertex_count = fallback_vertex_window(dispatch)
        rows.append(
            (
                start_vertex,
                vertex_count,
                int(dispatch["global_bone_base"]),
                int(dispatch["bone_count"]),
            )
        )
    return rows


def _yihuan_stage_filters(
    source_collection: bpy.types.Collection,
    region_packages: list[dict[str, object]],
) -> dict[str, dict[str, int]]:
    depth_hashes: list[str] = []
    gbuffer_hashes: list[str] = []
    for package in region_packages:
        depth_hashes.extend(_hash_tuple(package.get("depth_vs_hashes")))
        gbuffer_hashes.extend(_hash_tuple(package.get("gbuffer_vs_hashes")))

    def unique_sorted(values: list[str]) -> list[str]:
        return sorted(set(values))

    # If the same VS hash appears with both slot layouts in a frame, 3DMigoto can
    # only assign one filter_index. Prefer the fuller GBuffer layout because it
    # is the one that needs vs-t6/vs-t7 and avoids binding the shader as a
    # depth-only pass.
    gbuffer_hash_set = set(gbuffer_hashes)
    depth_unique = [value for value in unique_sorted(depth_hashes) if value not in gbuffer_hash_set]
    gbuffer_unique = unique_sorted(gbuffer_hashes)

    stage_filters = {
        "depth": {
            hash_value: _YIHUAN_DEPTH_VS_FILTER_BASE + index
            for index, hash_value in enumerate(depth_unique)
        },
        "gbuffer": {
            hash_value: _YIHUAN_GBUFFER_VS_FILTER_BASE + index
            for index, hash_value in enumerate(gbuffer_unique)
        },
    }
    stage_map_text_name = _optional_str_collection_prop(source_collection, _STAGE_MAP_TEXT_PROP)
    if not stage_map_text_name:
        return stage_filters

    stage_map_text = bpy.data.texts.get(stage_map_text_name)
    if stage_map_text is None:
        return stage_filters
    try:
        stage_map_payload = json.loads(stage_map_text.as_string())
    except json.JSONDecodeError:
        return stage_filters
    raw_stage_map = stage_map_payload.get("stage_map", {})
    if not isinstance(raw_stage_map, dict):
        return stage_filters

    known_hashes = {"depth": set(depth_unique), "gbuffer": set(gbuffer_unique)}
    for raw_key, raw_filter_index in raw_stage_map.items():
        try:
            filter_index = int(raw_filter_index)
        except (TypeError, ValueError):
            continue
        key = str(raw_key or "").strip().lower()
        if ":" in key:
            stage_name, vs_hash = key.split(":", 1)
        else:
            vs_hash = key
            if _YIHUAN_DEPTH_VS_FILTER_BASE <= filter_index < _YIHUAN_GBUFFER_VS_FILTER_BASE:
                stage_name = "depth"
            elif _YIHUAN_GBUFFER_VS_FILTER_BASE <= filter_index < _YIHUAN_GBUFFER_VS_FILTER_BASE + 100:
                stage_name = "gbuffer"
            else:
                continue
        if stage_name not in stage_filters or vs_hash not in known_hashes[stage_name]:
            continue
        stage_filters[stage_name][vs_hash] = filter_index
    return stage_filters


def _append_yihuan_vs_stage_filters(
    lines: list[str],
    stage_filters: dict[str, dict[str, int]],
    *,
    resource_suffix: str,
):
    lines.extend(
        [
            "; MARK: VS stage filters.",
            "; These ShaderOverrides make 3DMigoto check the currently bound IB against the",
            "; TextureOverride_IB_* sections below. The actual draw selection is still done",
            "; by hash + match_first_index + match_index_count in those TextureOverrides.",
        ]
    )
    for stage_name, label in (("depth", "DepthVS"), ("gbuffer", "GBufferVS")):
        for vs_hash, filter_index in sorted(stage_filters[stage_name].items(), key=lambda item: item[1]):
            lines.extend(
                [
                    f"[ShaderOverride_Yihuan_{resource_suffix}_{label}_{vs_hash[:8]}]",
                    f"hash = {vs_hash}",
                    f"filter_index = {filter_index}",
                    "allow_duplicate_hash = overrule",
                    "checktextureoverride = ib",
                    "",
                ]
            )

def _draw_toggle_for_draw(draw: dict[str, object]) -> tuple[str, str] | None:
    explicit_name = str(draw.get("draw_toggle", "") or "").strip()
    explicit_key = str(draw.get("draw_toggle_key", "") or "").strip()
    if explicit_name:
        return explicit_name.lstrip("$"), explicit_key or "VK_F10"

    object_name = str(draw.get("object_name", "") or "").strip()
    default_toggle = _YIHUAN_DEFAULT_DRAW_TOGGLES.get(object_name)
    if default_toggle is not None:
        return default_toggle
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


def _yihuan_uses_default_body_textures(parts: list[dict[str, object]]) -> bool:
    for part in parts:
        for draw in part.get("draws", []):
            if "body" in str(draw.get("object_name", "")).lower():
                return True
    return False


def _append_yihuan_default_texture_sections(
    lines: list[str],
    parts: list[dict[str, object]],
    *,
    resource_suffix: str,
):
    if not _yihuan_uses_default_body_textures(parts):
        return
    for resource_name, filename in _YIHUAN_BODY_TEXTURE_RESOURCES:
        lines.extend([f"[{_yihuan_texture_resource(resource_suffix, resource_name)}]", f"filename = {filename}"])
    lines.append("")


def _key_section_suffix(variable_name: str) -> str:
    chunks = re.split(r"[^0-9A-Za-z]+", variable_name.strip("$"))
    return "".join(chunk[:1].upper() + chunk[1:] for chunk in chunks if chunk) or "DrawToggle"


def _append_yihuan_draw_toggle_sections(
    lines: list[str],
    parts: list[dict[str, object]],
    *,
    resource_suffix: str,
):
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
                f"[KeyYihuan_{resource_suffix}_Toggle{_key_section_suffix(variable_name)}]",
                f"key = no_modifiers {key_name}",
                "type = cycle",
                "smart = true",
                f"${variable_name} = 1, 0",
                "",
            ]
        )


def _yihuan_body_texture_lines_for_draw(
    draw: dict[str, object],
    *,
    normalized_vs_hash: str,
    filter_index: int | None,
    resource_suffix: str,
) -> list[str]:
    object_name = str(draw.get("object_name", "")).lower()
    if "body" not in object_name:
        return []
    # In this profile, the visible body material restore is needed on the 90e5
    # GBuffer/velocity pass. Other passes should keep the caller's PS bindings.
    if normalized_vs_hash != "90e5f30bc8bfe0ae" and filter_index != 4210:
        return []
    return [
        f"  ps-t5 = {_yihuan_texture_resource(resource_suffix, 'ResourceT5')}",
        f"  ps-t7 = {_yihuan_texture_resource(resource_suffix, 'ResourceT7')}",
        f"  ps-t8 = {_yihuan_texture_resource(resource_suffix, 'ResourceT8')}",
        f"  ps-t18 = {_yihuan_texture_resource(resource_suffix, 'ResourceT18')}",
    ]


def _part_draw_lines(
    part: dict[str, object],
    *,
    normalized_vs_hash: str = "",
    filter_index: int | None = None,
    resource_suffix: str,
) -> list[str]:
    lines = []
    for draw in part["draws"]:
        lines.extend(
            _yihuan_body_texture_lines_for_draw(
                draw,
                normalized_vs_hash=normalized_vs_hash,
                filter_index=filter_index,
                resource_suffix=resource_suffix,
            )
        )
        lines.append(f"  ; [mesh:{draw['object_name']}] [vertex_count:{draw['vertex_count']}]")
        draw_line = f"drawindexed = {draw['index_count']},{draw['first_index']},0"
        toggle = _draw_toggle_for_draw(draw)
        if toggle is None:
            lines.append(f"  {draw_line}")
        else:
            variable_name, _ = toggle
            lines.append(f"  if ${variable_name} == 1")
            lines.append(f"    {draw_line}")
            lines.append("  endif")
    return lines


def _append_part_stage_draw(
    lines: list[str],
    *,
    part: dict[str, object],
    stage: str,
    use_runtime_skin: bool,
    filter_index: int | None = None,
    vs_hash: str | None = None,
    resource_suffix: str,
):
    part_token = str(part["resource_token"])
    normalized_vs_hash = (vs_hash or "").strip().lower()
    if stage == "depth":
        if use_runtime_skin:
            lines.extend(
                [
                    f"  ; [depth part:{part['part_name']}] [vertex_count:{part['vertex_count']}]",
                    f"  ib = ResourceYihuan_{part_token}_IB",
                    f"  run = CommandList_YihuanSelectAndPublishPose_{part_token}",
                    f"  vs-t3 = ResourceYihuan_{part_token}_Texcoord",
                    f"  vs-t4 = ResourceYihuan_{part_token}_Position",
                    f"  vs-t5 = ResourceYihuan_{part_token}_RuntimeSkinnedNormal",
                ]
            )
        else:
            lines.extend(
                [
                    f"  ; [depth-static part:{part['part_name']}] [vertex_count:{part['vertex_count']}]",
                    f"  ib = ResourceYihuan_{part_token}_IB",
                    f"  vb0 = ResourceYihuan_{part_token}_PositionVB",
                    f"  vs-t3 = ResourceYihuan_{part_token}_Texcoord",
                    f"  vs-t4 = ResourceYihuan_{part_token}_Position",
                    f"  vs-t5 = ResourceYihuan_{part_token}_Normal",
                ]
            )
    else:
        if use_runtime_skin:
            lines.extend(
                [
                    f"  ; [gbuffer part:{part['part_name']}] [vertex_count:{part['vertex_count']}]",
                    f"  ib = ResourceYihuan_{part_token}_IB",
                    f"  run = CommandList_YihuanSelectAndPublishPose_{part_token}",
                ]
            )
            if normalized_vs_hash == "83b4d27352a7b440":
                lines.extend(
                    [
                        f"  vs-t3 = ResourceYihuan_{part_token}_Texcoord",
                        f"  vs-t4 = ResourceYihuan_{part_token}_Position",
                        f"  vs-t5 = ResourceYihuan_{part_token}_RuntimeSkinnedNormal",
                        f"  vs-t6 = ResourceYihuan_{part_token}_OutlineParam",
                    ]
                )
            else:
                skinned_position_resource = (
                    f"ResourceYihuan_{part_token}_RuntimePrevSkinnedPosition"
                    if normalized_vs_hash in {"90e5f30bc8bfe0ae", "95c1180ad8070a67"} or filter_index in {4202, 4203}
                    else f"ResourceYihuan_{part_token}_RuntimeSkinnedPosition"
                )
                lines.extend(
                    [
                        f"  vs-t4 = {skinned_position_resource}",
                        f"  vs-t5 = ResourceYihuan_{part_token}_Texcoord",
                        f"  vs-t6 = ResourceYihuan_{part_token}_Position",
                        f"  vs-t7 = ResourceYihuan_{part_token}_RuntimeSkinnedNormal",
                    ]
                )
                if normalized_vs_hash == "95c1180ad8070a67":
                    lines.append(f"  vs-t8 = ResourceYihuan_{part_token}_OutlineParam")
        else:
            lines.extend(
                [
                    f"  ; [gbuffer-static part:{part['part_name']}] [vertex_count:{part['vertex_count']}]",
                    f"  ib = ResourceYihuan_{part_token}_IB",
                    f"  vb0 = ResourceYihuan_{part_token}_PositionVB",
                    f"  vs-t4 = ResourceYihuan_{part_token}_Position",
                    f"  vs-t5 = ResourceYihuan_{part_token}_Texcoord",
                    f"  vs-t6 = ResourceYihuan_{part_token}_Position",
                    f"  vs-t7 = ResourceYihuan_{part_token}_Normal",
                ]
            )
    lines.extend(
        _part_draw_lines(
            part,
            normalized_vs_hash=normalized_vs_hash,
            filter_index=filter_index,
            resource_suffix=resource_suffix,
        )
    )


def _append_yihuan_main_resource_sections(
    lines: list[str],
    parts: list[dict[str, object]],
    *,
    source_collection: bpy.types.Collection | None = None,
    resource_suffix: str,
):
    max_vertex_count = max((int(part["vertex_count"]) for part in parts), default=1)
    max_palette_count = max((int(part["local_palette_count"]) for part in parts), default=1)
    max_global_bone = _global_bone_count(parts, source_collection)
    pose_slots = 16
    global_t0_rows = max(1, max_global_bone * 3)
    lines.extend(
        [
            "; MARK: Original binding restore slots",
            f"[{_yihuan_restore_resource(resource_suffix, 'IB')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'VB0')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'VST3')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'VST4')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'VST5')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'VST6')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'VST7')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'VST8')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'PST5')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'PST6')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'PST7')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'PST8')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'PST18')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'CSCB0')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'CST0')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'CST1')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'CST2')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'CST3')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'CST6')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'CST7')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'CSU0')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'CSU1')}]",
            f"[{_yihuan_restore_resource(resource_suffix, 'CSU2')}]",
            "",
        ]
    )
    _append_yihuan_default_texture_sections(lines, parts, resource_suffix=resource_suffix)
    _append_yihuan_draw_toggle_sections(lines, parts, resource_suffix=resource_suffix)
    lines.extend(
        [
            "; MARK: Shared runtime buffers",
            f"[{_yihuan_runtime_resource(resource_suffix, 'PoseState_UAV')}]",
            "type = RWBuffer",
            "format = R32_UINT",
            "array = 64",
            "",
            f"[{_yihuan_runtime_resource(resource_suffix, 'PoseFeature_UAV')}]",
            "type = RWBuffer",
            "format = R32_FLOAT",
            "array = 12288",
            "",
            f"[{_yihuan_runtime_resource(resource_suffix, 'PoseFeature')}]",
            "type = Buffer",
            "format = R32_FLOAT",
            "array = 12288",
            "",
            f"[{_yihuan_runtime_resource(resource_suffix, 'NativeFeaturePosition')}]",
            "type = Buffer",
            "format = R32_FLOAT",
            f"array = {max_vertex_count * 3}",
            "",
            f"[{_yihuan_runtime_resource(resource_suffix, 'PoseGlobalT0_UAV')}]",
            "type = RWStructuredBuffer",
            "stride = 16",
            f"array = {pose_slots * global_t0_rows}",
            "",
            f"[{_yihuan_runtime_resource(resource_suffix, 'PoseGlobalT0')}]",
            "type = StructuredBuffer",
            "stride = 16",
            f"array = {pose_slots * global_t0_rows}",
            "",
            f"[{_yihuan_runtime_resource(resource_suffix, 'PoseSelectedLocalT0_UAV')}]",
            "type = RWStructuredBuffer",
            "stride = 16",
            f"array = {max_palette_count * 3}",
            "",
            f"[{_yihuan_runtime_resource(resource_suffix, 'PoseSelectedLocalT0')}]",
            "type = StructuredBuffer",
            "stride = 16",
            f"array = {max_palette_count * 3}",
            "",
        ]
    )
    for part in parts:
        token = str(part["resource_token"])
        vertex_count = int(part["vertex_count"])
        buffers = part["buffers"]
        lines.extend(
            [
                f"; [part:{part['part_name']}]",
                f"[ResourceYihuan_{token}_RuntimeSkinnedPosition_UAV]",
                "type = RWBuffer",
                "format = R32_FLOAT",
                f"array = {vertex_count * 3}",
                "",
                f"[ResourceYihuan_{token}_RuntimeSkinnedPosition]",
                "type = Buffer",
                "format = R32_FLOAT",
                f"array = {vertex_count * 3}",
                "",
                f"[ResourceYihuan_{token}_RuntimeSkinnedPositionVB]",
                "type = Buffer",
                "stride = 12",
                "",
                f"[ResourceYihuan_{token}_RuntimeSkinnedNormal_UAV]",
                "type = RWBuffer",
                "format = R16G16B16A16_SNORM",
                f"array = {vertex_count * 2}",
                "",
                f"[ResourceYihuan_{token}_RuntimeSkinnedNormal]",
                "type = Buffer",
                "format = R16G16B16A16_SNORM",
                f"array = {vertex_count * 2}",
                "",
                f"[ResourceYihuan_{token}_RuntimeScratchPosition_UAV]",
                "type = RWBuffer",
                "format = R32_FLOAT",
                f"array = {vertex_count * 3}",
                "",
                f"[ResourceYihuan_{token}_RuntimeScratchPosition]",
                "type = Buffer",
                "format = R32_FLOAT",
                f"array = {vertex_count * 3}",
                "",
                f"[ResourceYihuan_{token}_RuntimeScratchFrame_UAV]",
                "type = RWBuffer",
                "format = R16G16B16A16_SNORM",
                f"array = {vertex_count * 2}",
                "",
                f"[ResourceYihuan_{token}_RuntimeScratchFrame]",
                "type = Buffer",
                "format = R16G16B16A16_SNORM",
                f"array = {vertex_count * 2}",
                "",
                f"[ResourceYihuan_{token}_RuntimePoseSkinnedPosition_UAV]",
                "type = RWBuffer",
                "format = R32_FLOAT",
                f"array = {pose_slots * vertex_count * 3}",
                "",
                f"[ResourceYihuan_{token}_RuntimePoseSkinnedPosition]",
                "type = Buffer",
                "format = R32_FLOAT",
                f"array = {pose_slots * vertex_count * 3}",
                "",
                f"[ResourceYihuan_{token}_RuntimePoseSkinnedNormal_UAV]",
                "type = RWBuffer",
                "format = R16G16B16A16_SNORM",
                f"array = {pose_slots * vertex_count * 2}",
                "",
                f"[ResourceYihuan_{token}_RuntimePoseSkinnedNormal]",
                "type = Buffer",
                "format = R16G16B16A16_SNORM",
                f"array = {pose_slots * vertex_count * 2}",
                "",
                f"[ResourceYihuan_{token}_RuntimePrevSkinnedPosition]",
                "type = Buffer",
                "format = R32_FLOAT",
                f"array = {vertex_count * 3}",
                "",
                f"[ResourceYihuan_{token}_IB]",
                "type = Buffer",
                "format = DXGI_FORMAT_R16_UINT",
                f"filename = Buffer/{buffers['ib']}",
                "",
                f"[ResourceYihuan_{token}_Position]",
                "type = Buffer",
                "format = R32_FLOAT",
                f"array = {vertex_count * 3}",
                f"filename = Buffer/{buffers['vb0_pre_cs']}",
                "",
                f"[ResourceYihuan_{token}_PositionVB]",
                "type = Buffer",
                "stride = 12",
                f"filename = Buffer/{buffers['vb0_pre_cs']}",
                "",
                f"[ResourceYihuan_{token}_F33Position]",
                "type = Buffer",
                "format = R32_FLOAT",
                f"array = {vertex_count * 3}",
                f"filename = Buffer/{buffers['vb0_pre_cs']}",
                "",
                f"[ResourceYihuan_{token}_Blend]",
                "type = StructuredBuffer",
                "stride = 8",
                f"filename = Buffer/{buffers['weights']}",
                "",
                f"[ResourceYihuan_{token}_BlendTyped]",
                "type = Buffer",
                "format = R32_UINT",
                f"array = {vertex_count * 2}",
                f"filename = Buffer/{buffers['weights']}",
                "",
                f"[ResourceYihuan_{token}_PreFrame]",
                "type = StructuredBuffer",
                "stride = 8",
                f"filename = Buffer/{buffers['frame_pre_cs']}",
                "",
                f"[ResourceYihuan_{token}_F33Frame]",
                "type = Buffer",
                "format = R8G8B8A8_SNORM",
                f"array = {vertex_count * 2}",
                f"filename = Buffer/{buffers['frame_pre_cs']}",
                "",
                f"[ResourceYihuan_{token}_Normal]",
                "type = Buffer",
                "format = R8G8B8A8_SNORM",
                f"array = {vertex_count * 2}",
                f"filename = Buffer/{buffers['frame_pre_cs']}",
                "",
                f"[ResourceYihuan_{token}_Texcoord]",
                "type = Buffer",
                "format = R16G16_FLOAT",
                f"array = {vertex_count * 4}",
                f"filename = Buffer/{buffers['packed_uv']}",
                "",
                f"[ResourceYihuan_{token}_OutlineParam]",
                "type = Buffer",
                "format = R8G8B8A8_UNORM",
                f"array = {vertex_count}",
                f"filename = Buffer/{buffers['outline_param']}",
                "",
                f"[ResourceYihuan_{token}_CB0]",
                "type = Buffer",
                "stride = 16",
                "format = R32G32B32A32_UINT",
                f"filename = Buffer/{buffers['skin_cb0']}",
                "",
                f"[ResourceYihuan_{token}_SkinnedPosition_UAV]",
                "type = RWBuffer",
                "format = R32_FLOAT",
                f"array = {vertex_count * 3}",
                "",
                f"[ResourceYihuan_{token}_SkinnedPosition]",
                "type = Buffer",
                "format = R32_FLOAT",
                f"array = {vertex_count * 3}",
                "",
                f"[ResourceYihuan_{token}_SkinnedPositionVB]",
                "type = Buffer",
                "stride = 12",
                "",
                f"[ResourceYihuan_{token}_SkinnedNormal_UAV]",
                "type = RWBuffer",
                "format = R16G16B16A16_SNORM",
                f"array = {vertex_count * 2}",
                "",
                f"[ResourceYihuan_{token}_SkinnedNormal]",
                "type = Buffer",
                "format = R16G16B16A16_SNORM",
                f"array = {vertex_count * 2}",
                "",
            ]
        )


def _append_yihuan_bonestore_resource_sections(
    lines: list[str],
    parts: list[dict[str, object]],
    *,
    source_collection: bpy.types.Collection | None = None,
):
    character_meta_rows = max(1, len(_character_meta_rows(parts, source_collection)))
    palette_table_entries = max(1, sum(len(part.get("palette_entries", [])) for part in parts))
    lines.extend(
        [
            "; MARK: Shared bone store resources",
            "[ResourceGlobalT0Store_UAV]",
            "type = RWStructuredBuffer",
            "stride = 16",
            "array = 4096",
            "",
            "[ResourceGlobalT0Store]",
            "type = StructuredBuffer",
            "stride = 16",
            "array = 4096",
            "",
            "[ResourceCharacterMetaTable]",
            "type = Buffer",
            "format = R32G32B32A32_UINT",
            f"array = {character_meta_rows}",
            "filename = BoneStore/Buffer/CharacterMetaTable.buf",
            "",
            "[ResourcePaletteTable]",
            "type = Buffer",
            "format = R32_UINT",
            f"array = {palette_table_entries}",
            "filename = BoneStore/Buffer/PaletteTable.buf",
            "",
        ]
    )
    for part in parts:
        token = str(part["resource_token"])
        lines.extend(
            [
                f"[ResourcePalette_{token}]",
                "type = Buffer",
                "format = R32_UINT",
                f"filename = BoneStore/Buffer/{part['expected_palette_file']}",
                "",
            ]
        )


def _write_yihuan_bonestore_tables(
    *,
    export_root: Path,
    parts: list[dict[str, object]],
    source_collection: bpy.types.Collection | None = None,
) -> Path:
    buffer_dir = _ensure_directory(export_root / "BoneStore" / "Buffer")
    character_meta: list[int] = []
    palette_table: list[int] = []
    palette_offset = 0
    for part in parts:
        palette_entries = [int(value) for value in part.get("palette_entries", [])]
        palette_table.extend(palette_entries)
        palette_offset += len(palette_entries)
        write_u32_buffer(str(buffer_dir / str(part["expected_palette_file"])), palette_entries)
    for row in _character_meta_rows(parts, source_collection):
        character_meta.extend([int(value) for value in row])
    if not palette_table:
        palette_table = [0]
    write_u32_buffer(str(buffer_dir / "CharacterMetaTable.buf"), character_meta)
    write_u32_buffer(str(buffer_dir / "PaletteTable.buf"), palette_table)
    return buffer_dir


def _append_yihuan_custom_shader_sections(
    lines: list[str],
    *,
    source_suffix: str,
    hlsl_suffix: str,
    parts: list[dict[str, object]],
    bonestore_namespace: str,
):
    max_vertex_count = max((int(part["vertex_count"]) for part in parts), default=1)
    max_palette_count = max((int(part["local_palette_count"]) for part in parts), default=1)
    skin_dispatch = _ceil_div(max_vertex_count, 64)
    palette_dispatch = _ceil_div(max_palette_count * 3, 64)
    lines.extend(
        [
            f"[CustomShader_YihuanClearPoseSlots_{source_suffix}]",
            f"cs = hlsl\\yihuan_clear_pose_slots_{hlsl_suffix}_cs.hlsl",
            f"cs-u0 = {_yihuan_runtime_resource(source_suffix, 'PoseState_UAV')}",
            "dispatch = 1, 1, 1",
            "",
            f"[CustomShader_YihuanFindOrAllocPoseSlot_{source_suffix}]",
            f"cs = hlsl\\yihuan_find_or_alloc_pose_slot_{hlsl_suffix}_cs.hlsl",
            f"cs-cb0 = reference {_yihuan_restore_resource(source_suffix, 'CSCB0')}",
            f"{_yihuan_runtime_resource(source_suffix, 'NativeFeaturePosition')} = copy {_yihuan_restore_resource(source_suffix, 'CSU1')}",
            f"cs-t0 = {_yihuan_runtime_resource(source_suffix, 'NativeFeaturePosition')}",
            f"cs-t2 = Resource\\{bonestore_namespace}\\CharacterMetaTable",
            f"cs-u0 = {_yihuan_runtime_resource(source_suffix, 'PoseState_UAV')}",
            f"cs-u1 = {_yihuan_runtime_resource(source_suffix, 'PoseFeature_UAV')}",
            "dispatch = 1, 1, 1",
            f"{_yihuan_runtime_resource(source_suffix, 'PoseFeature')} = copy {_yihuan_runtime_resource(source_suffix, 'PoseFeature_UAV')}",
            "",
            f"[CustomShader_YihuanSelectPoseSlot_{source_suffix}]",
            f"cs = hlsl\\yihuan_select_pose_slot_{hlsl_suffix}_cs.hlsl",
            f"cs-t0 = {_yihuan_restore_resource(source_suffix, 'VB0')}",
            f"cs-t1 = {_yihuan_runtime_resource(source_suffix, 'PoseFeature')}",
            f"cs-t2 = Resource\\{bonestore_namespace}\\CharacterMetaTable",
            f"cs-u0 = {_yihuan_runtime_resource(source_suffix, 'PoseState_UAV')}",
            "dispatch = 1, 1, 1",
            "",
            f"[CustomShader_YihuanStoreGlobalT0PoseSlot_{source_suffix}]",
            f"cs = BoneStore\\hlsl\\yihuan_store_global_t0_pose_slot_{hlsl_suffix}_cs.hlsl",
            f"cs-cb0 = reference {_yihuan_restore_resource(source_suffix, 'CSCB0')}",
            f"cs-t0 = reference {_yihuan_restore_resource(source_suffix, 'CST0')}",
            f"cs-t1 = Resource\\{bonestore_namespace}\\CharacterMetaTable",
            f"cs-u0 = {_yihuan_runtime_resource(source_suffix, 'PoseState_UAV')}",
            f"cs-u1 = {_yihuan_runtime_resource(source_suffix, 'PoseGlobalT0_UAV')}",
            "dispatch = 64, 1, 1",
            f"{_yihuan_runtime_resource(source_suffix, 'PoseGlobalT0')} = copy {_yihuan_runtime_resource(source_suffix, 'PoseGlobalT0_UAV')}",
            "",
            f"[CustomShader_YihuanBuildLocalT0PoseSlot_{source_suffix}]",
            f"cs = BoneStore\\hlsl\\yihuan_build_local_t0_pose_slot_{hlsl_suffix}_cs.hlsl",
            f"cs-t0 = {_yihuan_runtime_resource(source_suffix, 'PoseGlobalT0')}",
            f"cs-t2 = Resource\\{bonestore_namespace}\\CharacterMetaTable",
            f"cs-u0 = {_yihuan_runtime_resource(source_suffix, 'PoseState_UAV')}",
            f"cs-u1 = {_yihuan_runtime_resource(source_suffix, 'PoseSelectedLocalT0_UAV')}",
            f"dispatch = {palette_dispatch}, 1, 1",
            f"{_yihuan_runtime_resource(source_suffix, 'PoseSelectedLocalT0')} = copy {_yihuan_runtime_resource(source_suffix, 'PoseSelectedLocalT0_UAV')}",
            "",
            f"[CustomShader_YihuanSkinScratch_{source_suffix}]",
            f"cs = BoneStore\\hlsl\\yihuan_skin_scratch_{hlsl_suffix}_cs.hlsl",
            f"cs-t0 = {_yihuan_runtime_resource(source_suffix, 'PoseSelectedLocalT0')}",
            f"cs-u2 = {_yihuan_runtime_resource(source_suffix, 'PoseState_UAV')}",
            f"dispatch = {skin_dispatch}, 1, 1",
            "",
            f"[CustomShader_YihuanStoreScratchPoseSlot_{source_suffix}]",
            f"cs = hlsl\\yihuan_store_scratch_pose_slot_{hlsl_suffix}_cs.hlsl",
            f"cs-t2 = Resource\\{bonestore_namespace}\\CharacterMetaTable",
            f"cs-u0 = {_yihuan_runtime_resource(source_suffix, 'PoseState_UAV')}",
            f"dispatch = {skin_dispatch}, 1, 1",
            "",
            f"[CustomShader_YihuanPublishPoseSlot_{source_suffix}]",
            f"cs = hlsl\\yihuan_publish_pose_slot_{hlsl_suffix}_cs.hlsl",
            f"cs-t2 = Resource\\{bonestore_namespace}\\CharacterMetaTable",
            f"cs-u0 = {_yihuan_runtime_resource(source_suffix, 'PoseState_UAV')}",
            f"dispatch = {skin_dispatch}, 1, 1",
            "",
            "[Present]",
            f"post run = CommandList_YihuanCapturePrevSkinnedPositions_{source_suffix}",
            f"post run = CustomShader_YihuanClearPoseSlots_{source_suffix}",
            "",
            f"[CommandList_YihuanStoreResourceSlots_{source_suffix}]",
            f"{_yihuan_restore_resource(source_suffix, 'IB')} = reference ib",
            f"{_yihuan_restore_resource(source_suffix, 'VB0')} = reference vb0",
            f"{_yihuan_restore_resource(source_suffix, 'VST3')} = reference vs-t3",
            f"{_yihuan_restore_resource(source_suffix, 'VST4')} = reference vs-t4",
            f"{_yihuan_restore_resource(source_suffix, 'VST5')} = reference vs-t5",
            f"{_yihuan_restore_resource(source_suffix, 'VST6')} = reference vs-t6",
            f"{_yihuan_restore_resource(source_suffix, 'VST7')} = reference vs-t7",
            f"{_yihuan_restore_resource(source_suffix, 'VST8')} = reference vs-t8",
            f"{_yihuan_restore_resource(source_suffix, 'PST5')} = reference ps-t5",
            f"{_yihuan_restore_resource(source_suffix, 'PST6')} = reference ps-t6",
            f"{_yihuan_restore_resource(source_suffix, 'PST7')} = reference ps-t7",
            f"{_yihuan_restore_resource(source_suffix, 'PST8')} = reference ps-t8",
            f"{_yihuan_restore_resource(source_suffix, 'PST18')} = reference ps-t18",
            f"{_yihuan_restore_resource(source_suffix, 'CSCB0')} = reference cs-cb0",
            f"{_yihuan_restore_resource(source_suffix, 'CST0')} = reference cs-t0",
            f"{_yihuan_restore_resource(source_suffix, 'CST1')} = reference cs-t1",
            f"{_yihuan_restore_resource(source_suffix, 'CST2')} = reference cs-t2",
            f"{_yihuan_restore_resource(source_suffix, 'CST3')} = reference cs-t3",
            f"{_yihuan_restore_resource(source_suffix, 'CST6')} = reference cs-t6",
            f"{_yihuan_restore_resource(source_suffix, 'CST7')} = reference cs-t7",
            f"{_yihuan_restore_resource(source_suffix, 'CSU0')} = reference cs-u0",
            f"{_yihuan_restore_resource(source_suffix, 'CSU1')} = reference cs-u1",
            f"{_yihuan_restore_resource(source_suffix, 'CSU2')} = reference cs-u2",
            "",
            f"[CommandList_YihuanRestoreResourceSlots_{source_suffix}]",
            f"ib = reference {_yihuan_restore_resource(source_suffix, 'IB')}",
            f"vb0 = reference {_yihuan_restore_resource(source_suffix, 'VB0')}",
            f"vs-t3 = reference {_yihuan_restore_resource(source_suffix, 'VST3')}",
            f"vs-t4 = reference {_yihuan_restore_resource(source_suffix, 'VST4')}",
            f"vs-t5 = reference {_yihuan_restore_resource(source_suffix, 'VST5')}",
            f"vs-t6 = reference {_yihuan_restore_resource(source_suffix, 'VST6')}",
            f"vs-t7 = reference {_yihuan_restore_resource(source_suffix, 'VST7')}",
            f"vs-t8 = reference {_yihuan_restore_resource(source_suffix, 'VST8')}",
            f"ps-t5 = reference {_yihuan_restore_resource(source_suffix, 'PST5')}",
            f"ps-t6 = reference {_yihuan_restore_resource(source_suffix, 'PST6')}",
            f"ps-t7 = reference {_yihuan_restore_resource(source_suffix, 'PST7')}",
            f"ps-t8 = reference {_yihuan_restore_resource(source_suffix, 'PST8')}",
            f"ps-t18 = reference {_yihuan_restore_resource(source_suffix, 'PST18')}",
            f"cs-cb0 = reference {_yihuan_restore_resource(source_suffix, 'CSCB0')}",
            f"cs-t0 = reference {_yihuan_restore_resource(source_suffix, 'CST0')}",
            f"cs-t1 = reference {_yihuan_restore_resource(source_suffix, 'CST1')}",
            f"cs-t2 = reference {_yihuan_restore_resource(source_suffix, 'CST2')}",
            f"cs-t3 = reference {_yihuan_restore_resource(source_suffix, 'CST3')}",
            f"cs-t6 = reference {_yihuan_restore_resource(source_suffix, 'CST6')}",
            f"cs-t7 = reference {_yihuan_restore_resource(source_suffix, 'CST7')}",
            f"cs-u0 = reference {_yihuan_restore_resource(source_suffix, 'CSU0')}",
            f"cs-u1 = reference {_yihuan_restore_resource(source_suffix, 'CSU1')}",
            f"cs-u2 = reference {_yihuan_restore_resource(source_suffix, 'CSU2')}",
            "",
        ]
    )
    lines.extend([f"[CommandList_YihuanCapturePrevSkinnedPositions_{source_suffix}]"])
    for part in parts:
        token = str(part["resource_token"])
        lines.append(
            f"ResourceYihuan_{token}_RuntimePrevSkinnedPosition = copy "
            f"ResourceYihuan_{token}_RuntimeSkinnedPosition_UAV"
        )
    lines.append("")

    for part in parts:
        token = str(part["resource_token"])
        lines.extend(
            [
                f"[CommandList_YihuanSkinAndStore_{token}]",
                f"cs-t1 = ResourceYihuan_{token}_BlendTyped",
                f"cs-t2 = ResourceYihuan_{token}_F33Frame",
                f"cs-t3 = ResourceYihuan_{token}_F33Position",
                f"cs-u0 = ResourceYihuan_{token}_RuntimeScratchFrame_UAV",
                f"cs-u1 = ResourceYihuan_{token}_RuntimeScratchPosition_UAV",
                f"run = CustomShader_YihuanSkinScratch_{source_suffix}",
                f"ResourceYihuan_{token}_RuntimeScratchFrame = copy ResourceYihuan_{token}_RuntimeScratchFrame_UAV",
                f"ResourceYihuan_{token}_RuntimeScratchPosition = copy ResourceYihuan_{token}_RuntimeScratchPosition_UAV",
                f"cs-t0 = ResourceYihuan_{token}_RuntimeScratchPosition",
                f"cs-t1 = ResourceYihuan_{token}_RuntimeScratchFrame",
                f"cs-u1 = ResourceYihuan_{token}_RuntimePoseSkinnedPosition_UAV",
                f"cs-u2 = ResourceYihuan_{token}_RuntimePoseSkinnedNormal_UAV",
                f"run = CustomShader_YihuanStoreScratchPoseSlot_{source_suffix}",
                "",
                f"[CommandList_YihuanSelectAndPublishPose_{token}]",
                f"run = CustomShader_YihuanSelectPoseSlot_{source_suffix}",
                f"ResourceYihuan_{token}_RuntimePoseSkinnedPosition = copy "
                f"ResourceYihuan_{token}_RuntimePoseSkinnedPosition_UAV",
                f"ResourceYihuan_{token}_RuntimePoseSkinnedNormal = copy "
                f"ResourceYihuan_{token}_RuntimePoseSkinnedNormal_UAV",
                f"cs-t0 = ResourceYihuan_{token}_RuntimePoseSkinnedPosition",
                f"cs-t1 = ResourceYihuan_{token}_RuntimePoseSkinnedNormal",
                f"cs-u1 = ResourceYihuan_{token}_RuntimeSkinnedPosition_UAV",
                f"cs-u2 = ResourceYihuan_{token}_RuntimeSkinnedNormal_UAV",
                f"run = CustomShader_YihuanPublishPoseSlot_{source_suffix}",
                f"ResourceYihuan_{token}_RuntimeSkinnedPosition = copy ResourceYihuan_{token}_RuntimeSkinnedPosition_UAV",
                f"ResourceYihuan_{token}_RuntimeSkinnedPositionVB = copy ResourceYihuan_{token}_RuntimeSkinnedPosition_UAV",
                f"ResourceYihuan_{token}_RuntimeSkinnedNormal = copy ResourceYihuan_{token}_RuntimeSkinnedNormal_UAV",
                f"vb0 = ResourceYihuan_{token}_RuntimeSkinnedPositionVB",
                f"cs-t0 = reference {_yihuan_restore_resource(source_suffix, 'CST0')}",
                f"cs-t1 = reference {_yihuan_restore_resource(source_suffix, 'CST1')}",
                f"cs-t2 = reference {_yihuan_restore_resource(source_suffix, 'CST2')}",
                f"cs-t3 = reference {_yihuan_restore_resource(source_suffix, 'CST3')}",
                f"cs-u0 = reference {_yihuan_restore_resource(source_suffix, 'CSU0')}",
                f"cs-u1 = reference {_yihuan_restore_resource(source_suffix, 'CSU1')}",
                f"cs-u2 = reference {_yihuan_restore_resource(source_suffix, 'CSU2')}",
                "",
            ]
        )


def _write_yihuan_main_ini(
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
    stage_filters = _yihuan_stage_filters(source_collection, region_packages)
    runtime_suffix = _yihuan_source_suffix(region_packages)
    hlsl_suffix = _YIHUAN_RUNTIME_HLSL_SUFFIX
    bonestore_namespace = _yihuan_bonestore_namespace(region_packages)
    _append_yihuan_main_resource_sections(
        lines,
        all_parts,
        source_collection=source_collection,
        resource_suffix=runtime_suffix,
    )
    _append_yihuan_vs_stage_filters(lines, stage_filters, resource_suffix=runtime_suffix)
    _append_yihuan_custom_shader_sections(
        lines,
        source_suffix=runtime_suffix,
        hlsl_suffix=hlsl_suffix,
        parts=all_parts,
        bonestore_namespace=bonestore_namespace,
    )
    lines.append("")
    if include_runtime_skin:
        lines.append("; MARK: Skin dispatch. Main INI selects a pose slot and skins custom buffers.")
        filter_condition = (
            f"cs == {_YIHUAN_CS_FILTER_INDICES['f33fea3cca2704e4']} || "
            f"cs == {_YIHUAN_CS_FILTER_INDICES['1e2a9061eadfeb6c']}"
        )
        parts_by_cb0: dict[str, list[dict[str, object]]] = defaultdict(list)
        for part in sorted(all_parts, key=lambda item: (str(item["region_hash"]), int(item["part_index"]))):
            last_cs_cb0_hash = str(part.get("last_cs_cb0_hash", "")).strip().lower()
            if not last_cs_cb0_hash:
                continue
            parts_by_cb0[last_cs_cb0_hash].append(part)
        for last_cs_cb0_hash, cb0_parts in sorted(parts_by_cb0.items()):
            lines.extend(
                [
                    f"[TextureOverride_YihuanSkinPart_{runtime_suffix}_{last_cs_cb0_hash}]",
                    f"hash = {last_cs_cb0_hash}",
                    "match_priority = 500",
                    f"if {filter_condition}",
                    f"  run = CommandList_YihuanStoreResourceSlots_{runtime_suffix}",
                    f"  post run = CustomShader_YihuanFindOrAllocPoseSlot_{runtime_suffix}",
                    f"  post run = CustomShader_YihuanStoreGlobalT0PoseSlot_{runtime_suffix}",
                ]
            )
            for part in cb0_parts:
                token = str(part["resource_token"])
                lines.extend(
                    [
                        f"  post cs-t1 = Resource\\{bonestore_namespace}\\Palette_{token}",
                        f"  post run = CustomShader_YihuanBuildLocalT0PoseSlot_{runtime_suffix}",
                        f"  post run = CommandList_YihuanSkinAndStore_{token}",
                    ]
                )
            lines.extend(
                [
                    f"  post run = CommandList_YihuanRestoreResourceSlots_{runtime_suffix}",
                    "endif",
                    "",
                ]
            )

    lines.append("; MARK: Draw replacement")
    for package in region_packages:
        region_hash = str(package["region_hash"])
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
                f"run = CommandList_YihuanStoreResourceSlots_{runtime_suffix}",
            ]
        )
        depth_entries = [
            (hash_value, stage_filters["depth"][hash_value])
            for hash_value in _hash_tuple(package.get("depth_vs_hashes"))
            if hash_value in stage_filters["depth"]
        ]
        gbuffer_entries = [
            (hash_value, stage_filters["gbuffer"][hash_value])
            for hash_value in _hash_tuple(package.get("gbuffer_vs_hashes"))
            if hash_value in stage_filters["gbuffer"]
        ]
        if depth_entries:
            for hash_value, filter_index in sorted(set(depth_entries), key=lambda item: item[1]):
                lines.append(f"if vs == {filter_index}")
                lines.append("  handling = skip")
                for part in package["parts"]:
                    _append_part_stage_draw(
                        lines,
                        part=part,
                        stage="depth",
                        use_runtime_skin=include_runtime_skin,
                        filter_index=filter_index,
                        vs_hash=hash_value,
                        resource_suffix=runtime_suffix,
                    )
                lines.append("endif")
        if gbuffer_entries:
            for hash_value, filter_index in sorted(set(gbuffer_entries), key=lambda item: item[1]):
                lines.append(f"if vs == {filter_index}")
                lines.append("  handling = skip")
                for part in package["parts"]:
                    _append_part_stage_draw(
                        lines,
                        part=part,
                        stage="gbuffer",
                        use_runtime_skin=include_runtime_skin,
                        filter_index=filter_index,
                        vs_hash=hash_value,
                        resource_suffix=runtime_suffix,
                    )
                lines.append("endif")
        if not depth_entries and not gbuffer_entries:
            raise ValueError(
                f"{region_hash}: cannot generate draw override without explicit depth/gbuffer VS hash metadata."
            )
        lines.extend(
            [
                "if vs == 1",
                "  ; Unknown shadow/extra pass: hand it back to the game.",
                "  draw = from_caller",
                "endif",
                f"run = CommandList_YihuanRestoreResourceSlots_{runtime_suffix}",
            ]
        )
        lines.append("")

    ini_path.write_text("\n".join(lines), encoding="utf-8")
    return ini_path


def _write_yihuan_bonestore_ini(
    *,
    export_root: Path,
    ini_name: str,
    source_collection: bpy.types.Collection,
    region_packages: list[dict[str, object]],
) -> Path:
    if not region_packages:
        raise ValueError("Cannot generate BoneStore INI without region packages.")
    for package in region_packages:
        if not str(package.get("last_cs_cb0_hash", "")):
            raise ValueError(
                f"{package.get('region_hash', 'unknown')}: cannot generate BoneStore INI without last_cs_cb0_hash."
            )

    bonestore_path = export_root / ini_name
    all_parts = [part for package in region_packages for part in package["parts"]]
    bonestore_namespace = _yihuan_bonestore_namespace(region_packages)
    source_suffix = _yihuan_source_suffix(region_packages)
    _write_yihuan_bonestore_tables(
        export_root=export_root,
        parts=all_parts,
        source_collection=source_collection,
    )
    lines: list[str] = [
        f"namespace = {bonestore_namespace}",
        "",
        "; MARK: Shader filters for the original skinning CS chain",
    ]
    for cs_hash, filter_index in _YIHUAN_CS_FILTER_INDICES.items():
        lines.extend(
            [
                f"[ShaderOverride_Yihuan_{source_suffix}_CS_{cs_hash[:8]}]",
                f"hash = {cs_hash}",
                f"filter_index = {filter_index}",
                "allow_duplicate_hash = overrule",
                "checktextureoverride = cs-cb0",
                "",
            ]
        )

    lines.append("; MARK: Runtime resources")
    _append_yihuan_bonestore_resource_sections(
        lines,
        all_parts,
        source_collection=source_collection,
    )
    lines.append("")

    lines.extend(
        [
            "; MARK: Bone collection",
            "; Main INI copies the current native cs-t0 directly into the selected pose slot.",
            "; No standalone TextureOverride collect hook is emitted here, avoiding duplicate CS work.",
            "",
        ]
    )

    bonestore_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return bonestore_path


def _export_region_package(
    *,
    region_collection: bpy.types.Collection,
    source_ib_hash: str,
    buffer_dir: Path,
    require_runtime_contract: bool,
    flip_uv_v: bool,
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
    all_draws = _draws_for_ini(exported_parts)
    main_draw = max(all_draws, key=lambda item: (int(item["original_index_count"]), int(item["last_consumer_draw_index"])))
    last_cs_cb0_hash = str(main_draw.get("last_cs_cb0_hash", ""))
    last_cs_hash = str(main_draw.get("last_cs_hash", ""))
    if require_runtime_contract and not last_cs_cb0_hash:
        raise ValueError(
            f"{region_hash}: cannot resolve the final CS cb0 hash. "
            "The INI needs this hash to trigger gather/skin at the end of the original CS chain."
        )
    return {
        "profile_id": YIHUAN_PROFILE.profile_id,
        "region_hash": region_hash,
        "region_first_index": region_first_index,
        "source_ib_hash": source_ib_hash,
        "original_match_index_count": original_match_index_count,
        "match_index_source": match_index_source,
        "runtime_contract": region_runtime_contract,
        "last_cs_hash": last_cs_hash,
        "last_cs_cb0_hash": last_cs_cb0_hash,
        "depth_vs_hashes": _hash_tuple(region_runtime_contract.get("depth_vs_hashes")),
        "gbuffer_vs_hashes": _hash_tuple(region_runtime_contract.get("gbuffer_vs_hashes")),
        "shape_key_policy": _SHAPE_KEY_BAKE_POLICY,
        "parts": exported_parts,
    }


def export_collection_package(
    *,
    collection_name: str,
    export_dir: str,
    flip_uv_v: bool = False,
    generate_ini: bool = True,
) -> dict[str, object]:
    """Export one strict sourceIB -> region -> part collection tree into runtime replacement assets."""
    collection = _get_collection(collection_name)
    export_root = _ensure_directory(Path(export_dir).resolve())
    buffer_dir = _ensure_directory(export_root / "Buffer")
    source_ib_hash = _source_root_hash(collection)
    hlsl_dir: Path | None = None
    if generate_ini:
        hlsl_dir = export_profile_hlsl_assets(YIHUAN_PROFILE.profile_id, export_root)
    region_collections = _resolve_region_collections(collection)
    region_packages = [
        _export_region_package(
            region_collection=region_collection,
            source_ib_hash=source_ib_hash,
            buffer_dir=buffer_dir,
            require_runtime_contract=generate_ini,
            flip_uv_v=flip_uv_v,
        )
        for region_collection in region_collections
    ]

    ini_file: Path | None = None
    bonestore_ini_file: Path | None = None
    if generate_ini:
        ini_file = _write_yihuan_main_ini(
            export_root=export_root,
            ini_name=f"{source_ib_hash}.ini",
            source_collection=collection,
            region_packages=region_packages,
            include_runtime_skin=True,
        )
        bonestore_ini_file = _write_yihuan_bonestore_ini(
            export_root=export_root,
            ini_name=f"{source_ib_hash}-BoneStore.ini",
            source_collection=collection,
            region_packages=region_packages,
        )

    total_vertices = sum(int(part["vertex_count"]) for package in region_packages for part in package["parts"])
    total_indices = sum(int(part["index_count"]) for package in region_packages for part in package["parts"])
    total_draws = sum(len(part["draws"]) for package in region_packages for part in package["parts"])
    total_parts = sum(len(package["parts"]) for package in region_packages)
    return {
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
        "hlsl_dir": "" if hlsl_dir is None else str(hlsl_dir),
        "ini_path": "" if ini_file is None else str(ini_file),
        "runtime_ini_path": str(bonestore_ini_file) if bonestore_ini_file is not None else "",
    }
