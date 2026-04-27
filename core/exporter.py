"""Collection export pipeline for the 异环 profile."""

from __future__ import annotations

import math
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import bpy
from mathutils import Vector

from .game_data import get_game_data_converter
from .discovery import discover_yihuan_model
from .hlsl_assets import export_profile_hlsl_assets
from .io import (
    write_float3_buffer,
    write_half2x4_buffer,
    write_snorm8x4_pairs_buffer,
    write_u16_buffer,
    write_u32_buffer,
    write_weight_pairs_buffer,
)
from .profiles import YIHUAN_PROFILE


_REGION_HASH_RE = re.compile(r"^[0-9A-Fa-f]{8}$")
_REGION_COLLECTION_RE = re.compile(r"^(?P<hash>[0-9A-Fa-f]{8})(?:[-_](?P<count>\d+)(?:[-_](?P<first>\d+))?)?")
_OBJECT_REGION_RE = re.compile(r"^(?P<hash>[0-9A-Fa-f]{8})(?:[-_](?P<count>\d+)(?:[-_](?P<first>\d+))?)?")
_CAPTURE_OBJECT_RE = re.compile(r"^(?P<hash>[0-9A-Fa-f]{8})-(?P<count>\d+)-(?P<first>\d+)")
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
_BMC_IB_HASH_PROP = "modimp_bmc_ib_hash"
_BMC_MATCH_INDEX_COUNT_PROP = "modimp_bmc_match_index_count"
_BMC_CHUNK_INDEX_PROP = "modimp_bmc_chunk_index"
_SHAPE_KEY_BAKE_POLICY = "bake_current_relative_mix_to_base_mesh_copy"
_YIHUAN_DEFAULT_PRODUCER_CS_HASH = "f33fea3cca2704e4"
_YIHUAN_DEFAULT_LAST_CS_HASH = "f33fea3cca2704e4"
_YIHUAN_DEFAULT_LAST_CS_CB0_HASH = "7816b819"
_YIHUAN_CS_FILTER_INDICES = {
    "f33fea3cca2704e4": 3300,
    "1e2a9061eadfeb6c": 3301,
}
_YIHUAN_BONESTORE_NAMESPACE = "YihuanBoneStore"
_YIHUAN_DEPTH_VS_FILTER_BASE = 4100
_YIHUAN_GBUFFER_VS_FILTER_BASE = 4200


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


def _find_capture_manifest(export_root: Path) -> Path:
    candidates = [
        export_root / "capture_manifest.json",
        export_root.parent / "capture_manifest.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError(
        "Missing capture_manifest.json. Generate/copy the BoneMerge capture manifest next to the export folder "
        "before exporting the Yihuan runtime BoneStore."
    )


def _parse_capture_record_identity(record: dict[str, object]) -> tuple[str, int, int]:
    object_name = str(record.get("object_name", "") or "")
    match = _CAPTURE_OBJECT_RE.match(object_name)
    if match:
        return match.group("hash").lower(), int(match.group("count")), int(match.group("first"))

    ib_hash = str(record.get("ib_hash", "") or "").strip().lower()
    index_count_raw = record.get("match_index_count")
    first_index_raw = record.get("first_index")
    if not ib_hash or index_count_raw is None or first_index_raw is None:
        raise ValueError(f"Cannot parse capture record identity from capture_manifest entry: {object_name or record!r}")
    return ib_hash, int(index_count_raw), int(first_index_raw)


def _load_yihuan_capture_ranges(
    export_root: Path,
    source_ib_hash: str,
    *,
    frame_dump_dir: str | None = None,
) -> list[dict[str, object]]:
    manifest_path = _find_capture_manifest(export_root)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid capture manifest JSON: {manifest_path}") from exc

    frameanalysis_dir = str(frame_dump_dir or "").strip() or str(manifest.get("frameanalysis_dir", "") or "").strip()
    if not frameanalysis_dir:
        raise ValueError(f"{manifest_path}: missing frameanalysis_dir.")
    frameanalysis_path = Path(frameanalysis_dir)
    if not frameanalysis_path.is_dir():
        raise ValueError(f"{manifest_path}: frameanalysis_dir does not exist: {frameanalysis_path}")

    detected_model = discover_yihuan_model(str(frameanalysis_path), ib_hash=source_ib_hash)
    slices_by_region_key: dict[tuple[str, int, int], object] = {}
    slices_by_count_first: dict[tuple[int, int], object] = {}
    for detected_slice in detected_model.slices:
        region_hash = str(detected_slice.display_ib_hash or detected_slice.raw_ib_hash or "").strip().lower()
        key = (int(detected_slice.index_count), int(detected_slice.first_index))
        slices_by_count_first[key] = detected_slice
        if region_hash:
            slices_by_region_key[(region_hash, key[0], key[1])] = detected_slice

    ranges: list[dict[str, object]] = []
    for record in manifest.get("part_records", []):
        if not isinstance(record, dict):
            continue
        region_hash, match_index_count, first_index = _parse_capture_record_identity(record)
        detected_slice = slices_by_region_key.get((region_hash, match_index_count, first_index))
        if detected_slice is None:
            detected_slice = slices_by_count_first.get((match_index_count, first_index))
        if detected_slice is None:
            raise ValueError(
                f"{manifest_path}: capture record {region_hash}-{match_index_count}-{first_index} "
                f"does not match any discovered CS-backed slice in {frameanalysis_path}."
            )

        start_vertex = detected_slice.producer_start_vertex
        vertex_count = detected_slice.producer_vertex_count
        if start_vertex is None or vertex_count is None:
            raise ValueError(
                f"{manifest_path}: capture record {region_hash}-{match_index_count}-{first_index} "
                "has no producer CS cb0 range in the FrameAnalysis dump."
            )

        ranges.append(
            {
                "region_hash": region_hash,
                "match_index_count": int(match_index_count),
                "first_index": int(first_index),
                "start_vertex": int(start_vertex),
                "vertex_count": int(vertex_count),
                "global_bone_base": int(record["global_bone_base"]),
                "capture_bone_count": int(record["capture_bone_count"]),
                "cb0_hash": str(detected_slice.last_cs_cb0_hash or _YIHUAN_DEFAULT_LAST_CS_CB0_HASH).lower(),
                "depth_vs_hashes": tuple(detected_slice.depth_vs_hashes),
                "gbuffer_vs_hashes": tuple(detected_slice.gbuffer_vs_hashes),
            }
        )

    if not ranges:
        raise ValueError(f"{manifest_path}: no usable part_records were found.")

    duplicates: dict[tuple[int, int], list[dict[str, object]]] = defaultdict(list)
    for item in ranges:
        duplicates[(int(item["start_vertex"]), int(item["vertex_count"]))].append(item)
    duplicate_ranges = {key: value for key, value in duplicates.items() if len(value) > 1}
    if duplicate_ranges:
        detail = ", ".join(f"{start}/{count}" for start, count in sorted(duplicate_ranges))
        raise ValueError(f"{manifest_path}: duplicate CS cb0 capture ranges are ambiguous: {detail}")

    return sorted(ranges, key=lambda item: (int(item["global_bone_base"]), int(item["first_index"])))


def _try_load_yihuan_capture_ranges(
    export_root: Path,
    source_ib_hash: str,
    *,
    frame_dump_dir: str | None = None,
) -> list[dict[str, object]]:
    try:
        return _load_yihuan_capture_ranges(
            export_root,
            source_ib_hash,
            frame_dump_dir=frame_dump_dir,
        )
    except Exception as exc:  # pylint: disable=broad-except
        print(
            "[mod_importer] Skipping Yihuan runtime capture ranges; "
            f"export will continue without BoneStore/runtime skin support: {exc}"
        )
        return []


def _write_yihuan_collect_t0_hlsl(*, hlsl_dir: Path, capture_ranges: list[dict[str, object]]) -> Path:
    target_path = hlsl_dir / "yihuan_collect_t0_cs.hlsl"
    text = "\n".join(
        [
            "// =========================================================",
            "// yihuan_collect_t0_cs.hlsl",
            "//",
            "// Generated by mod_importer.",
            "// Collect is keyed by current cs-cb0 contents plus INI-provided collect meta.",
            "//",
            "// Expected bindings inherited from the original skin CS:",
            "//   cb0 = original skin dispatch params",
            "//   t0  = original local T0 palette for that dispatch",
            "// Expected bindings set by the INI:",
            "//   t2  = uint collect meta: expected_start expected_count global_bone_base bone_count",
            "//   u0  = global T0 store UAV",
            "// =========================================================",
            "",
            "cbuffer OriginalSkinCB0 : register(b0)",
            "{",
            "    uint4 SkinCB0_0;",
            "};",
            "",
            "StructuredBuffer<uint4> OriginalT0 : register(t0);",
            "Buffer<uint> CollectMeta : register(t2);",
            "RWStructuredBuffer<uint4> GlobalT0Store : register(u0);",
            "",
            "bool CurrentCB0Matches(uint expected_start, uint expected_count)",
            "{",
            "    bool primary_form = (SkinCB0_0.y == expected_start && SkinCB0_0.z == expected_count);",
            "    bool final_form = (SkinCB0_0.z == expected_start && SkinCB0_0.w == expected_count);",
            "    return primary_form || final_form;",
            "}",
            "",
            "[numthreads(64, 1, 1)]",
            "void main(uint3 tid : SV_DispatchThreadID)",
            "{",
            "    uint expected_start = CollectMeta[0];",
            "    uint expected_count = CollectMeta[1];",
            "    uint global_bone_base = CollectMeta[2];",
            "    uint bone_count = CollectMeta[3];",
            "    if (!CurrentCB0Matches(expected_start, expected_count))",
            "    {",
            "        return;",
            "    }",
            "",
            "    uint local_row = tid.x;",
            "    uint row_count = bone_count * 3u;",
            "    if (local_row >= row_count)",
            "    {",
            "        return;",
            "    }",
            "",
            "    uint local_bone = local_row / 3u;",
            "    uint row_in_bone = local_row % 3u;",
            "    uint global_bone = global_bone_base + local_bone;",
            "",
            "    uint src_row = local_bone * 3u + row_in_bone;",
            "    uint dst_row = global_bone * 3u + row_in_bone;",
            "    GlobalT0Store[dst_row] = OriginalT0[src_row];",
            "}",
            "",
        ]
    )
    target_path.write_text(text, encoding="utf-8")
    for stale_bin_path in hlsl_dir.glob("yihuan_collect_t0_cs*.bin"):
        if stale_bin_path.is_file():
            stale_bin_path.unlink()
    return target_path


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


def _optional_int_object_prop(obj: bpy.types.Object, key: str) -> int | None:
    if key not in obj:
        return None
    try:
        return int(obj[key])
    except (TypeError, ValueError):
        return None


def _optional_str_object_prop(obj: bpy.types.Object, key: str) -> str:
    return str(obj.get(key, "") or "").strip()


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


def _numeric_vertex_group_names(obj: bpy.types.Object) -> dict[int, int]:
    numeric_groups: dict[int, int] = {}
    for vertex_group in obj.vertex_groups:
        if vertex_group.name.isdigit():
            numeric_groups[vertex_group.index] = int(vertex_group.name)
    return numeric_groups


def _normalized_top4_weights(
    obj: bpy.types.Object,
    *,
    mesh: bpy.types.Mesh | None = None,
) -> tuple[list[tuple[int, int, int, int]], list[tuple[int, int, int, int]], int]:
    mesh = mesh or obj.data
    numeric_groups = _numeric_vertex_group_names(obj)
    if not numeric_groups:
        raise ValueError(f"{obj.name}: no numeric vertex groups were found")

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
                    f"{obj.name}: vertex group {palette_index} cannot be written to uint8 BLENDINDICES. "
                    "Export the localized Bone Merge build copy or split the chunk to <= 256 local bones."
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
    fallback_profile_id: str | None = None,
    fallback_original_first_index: int | None = None,
    fallback_original_index_count: int | None = None,
    fallback_producer_dispatch_index: int | None = None,
    fallback_producer_cs_hash: str | None = None,
    fallback_producer_t0_hash: str | None = None,
    fallback_last_cs_hash: str | None = None,
    fallback_last_cs_cb0_hash: str | None = None,
    fallback_last_consumer_draw_index: int | None = None,
) -> dict[str, object]:
    if obj.type != "MESH":
        raise ValueError(f"{obj.name}: only mesh objects can be exported")
    profile_id = (fallback_profile_id or _optional_str_object_prop(obj, _PROFILE_ID_PROP) or YIHUAN_PROFILE.profile_id).strip()
    object_region_hash, object_index_count, object_first_index = _object_region_identity(obj)
    if profile_id != YIHUAN_PROFILE.profile_id:
        raise ValueError(f"{obj.name}: unsupported profile id")
    converter = get_game_data_converter(profile_id)

    depsgraph = bpy.context.evaluated_depsgraph_get()
    missing_optional_attributes: list[str] = []
    mesh_copy, baked_shape_keys = _triangulated_mesh_copy(obj, depsgraph=depsgraph)
    blend_indices, blend_weights, local_palette_count = _normalized_top4_weights(obj, mesh=mesh_copy)
    uv0_layer = _find_uv_layer(mesh_copy, "UV0") or mesh_copy.uv_layers.active
    if uv0_layer is None:
        bpy.data.meshes.remove(mesh_copy)
        raise ValueError(f"{obj.name}: UV0 or an active UV layer is required for export")
    uv1_layer = _find_uv_layer(mesh_copy, "UV1", "packed_uv1")
    uv2_layer = _find_uv_layer(mesh_copy, "UV2", "packed_uv2")
    uv3_layer = _find_uv_layer(mesh_copy, "UV3", "packed_uv3")
    packed_uv1, missing_uv1_attr = _optional_point_vector_attribute(mesh_copy, "packed_uv1")
    packed_uv2, missing_uv2_attr = _optional_point_vector_attribute(mesh_copy, "packed_uv2")
    packed_uv3, missing_uv3_attr = _optional_point_vector_attribute(mesh_copy, "packed_uv3")
    if uv1_layer is None and missing_uv1_attr:
        missing_optional_attributes.append("packed_uv1")
    if uv2_layer is None and missing_uv2_attr:
        missing_optional_attributes.append("packed_uv2")
    if uv3_layer is None and missing_uv3_attr:
        missing_optional_attributes.append("packed_uv3")
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
                uv1 = _to_game_uv_pair(_loop_uv_pair(loop_index, source_vertex_index, uv1_layer, packed_uv1))
                uv2 = _to_game_uv_pair(_loop_uv_pair(loop_index, source_vertex_index, uv2_layer, packed_uv2))
                uv3 = _to_game_uv_pair(_loop_uv_pair(loop_index, source_vertex_index, uv3_layer, packed_uv3))
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
                    out_vertex_index = len(positions) - 1
                    remap[key] = out_vertex_index
                triangle.append(out_vertex_index)
            triangles.append(tuple(triangle))
    finally:
        bpy.data.meshes.remove(mesh_copy)

    frame_a, frame_b = converter.encode_pre_cs_frames(decoded_tangents, decoded_normals, decoded_signs)
    original_first_index = fallback_original_first_index
    if original_first_index is None:
        original_first_index = _optional_int_object_prop(obj, "modimp_first_index")
    if original_first_index is None:
        original_first_index = object_first_index if object_first_index is not None else 0
    original_index_count = fallback_original_index_count
    if original_index_count is None:
        original_index_count = _optional_int_object_prop(obj, "modimp_index_count")
    if original_index_count is None:
        original_index_count = object_index_count if object_index_count is not None else len(triangles) * 3
    producer_dispatch_index = fallback_producer_dispatch_index
    if producer_dispatch_index is None:
        producer_dispatch_index = _optional_int_object_prop(obj, _PRODUCER_DISPATCH_INDEX_PROP)
    if producer_dispatch_index is None:
        producer_dispatch_index = 0
    producer_cs_hash = (fallback_producer_cs_hash or _optional_str_object_prop(obj, _PRODUCER_CS_HASH_PROP) or _YIHUAN_DEFAULT_PRODUCER_CS_HASH).strip()
    producer_t0_hash = (fallback_producer_t0_hash or _optional_str_object_prop(obj, _PRODUCER_T0_HASH_PROP)).strip()
    last_cs_hash = (fallback_last_cs_hash or _optional_str_object_prop(obj, _LAST_CS_HASH_PROP) or _YIHUAN_DEFAULT_LAST_CS_HASH).strip()
    last_cs_cb0_hash = (fallback_last_cs_cb0_hash or _optional_str_object_prop(obj, _LAST_CS_CB0_HASH_PROP) or _YIHUAN_DEFAULT_LAST_CS_CB0_HASH).strip()
    last_consumer_draw_index = fallback_last_consumer_draw_index
    if last_consumer_draw_index is None:
        last_consumer_draw_index = _optional_int_object_prop(obj, _LAST_CONSUMER_DRAW_INDEX_PROP) or 0

    return {
        "object_name": obj.name,
        "object_region_hash": object_region_hash,
        "positions": positions,
        "triangles": triangles,
        "packed_uv_entries": packed_uv_entries,
        "blend_indices": out_blend_indices,
        "blend_weights": out_blend_weights,
        "frame_a": frame_a,
        "frame_b": frame_b,
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
    """Read the export/runtime contract from a region collection.

    Object-level modimp_* properties are only legacy fallbacks in _extract_object_payload;
    the collection is the authoritative source for a replacement package.
    """
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


def _object_region_identity(obj: bpy.types.Object) -> tuple[str, int | None, int | None]:
    match = _OBJECT_REGION_RE.match(obj.name)
    if match:
        index_count = int(match.group("count")) if match.group("count") is not None else None
        first_index = int(match.group("first")) if match.group("first") is not None else None
        return match.group("hash").lower(), index_count, first_index
    region_hash = str(obj.get("modimp_region_hash", "") or "").strip().lower()
    if _REGION_HASH_RE.fullmatch(region_hash):
        index_count = int(obj["modimp_region_index_count"]) if "modimp_region_index_count" in obj else None
        first_index = int(obj["modimp_region_first_index"]) if "modimp_region_first_index" in obj else None
        return region_hash, index_count, first_index
    display_hash = str(obj.get("modimp_display_ib_hash", "") or "").strip().lower()
    if _REGION_HASH_RE.fullmatch(display_hash):
        index_count = int(obj["modimp_index_count"]) if "modimp_index_count" in obj else None
        first_index = int(obj["modimp_first_index"]) if "modimp_first_index" in obj else None
        return display_hash, index_count, first_index
    return "", None, None


def _source_root_hash(collection: bpy.types.Collection) -> str:
    kind = _collection_kind(collection)
    if kind and kind != "source_ib":
        raise ValueError(
            f"Export collection '{collection.name}' is marked as '{kind}'. Select the source-IB root collection instead."
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
    for child in sorted(root_collection.children, key=lambda item: item.name):
        child_kind = _collection_kind(child)
        child_region_hash = _collection_region_hash(child)
        is_region = child_kind == "region" or bool(child_region_hash) or bool(_REGION_COLLECTION_RE.match(child.name.strip().lower()))
        if is_region:
            _region_collection_hash(child)
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
    if direct_meshes:
        names = ", ".join(obj.name for obj in direct_meshes[:6])
        if len(direct_meshes) > 6:
            names += ", ..."
        raise ValueError(
            f"Meshes cannot be linked directly under region '{root_collection.name}'. "
            f"Move them into partNN first: {names}"
        )

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
    for child in child_collections:
        nested_meshes = [
            obj.name
            for nested in child.children
            for obj in nested.all_objects
            if obj.type == "MESH"
        ]
        if nested_meshes:
            raise ValueError(
                f"Part collection '{child.name}' cannot contain nested mesh collections. "
                f"Move meshes directly into the part: {', '.join(nested_meshes[:6])}"
            )

        mesh_objects = _sorted_mesh_objects(child.objects)
        if not mesh_objects:
            continue
        part_index = _part_collection_index(child)
        if part_index in used_part_indices:
            raise ValueError(f"Region '{region_hash}' has duplicate part index {part_index}.")
        used_part_indices.add(part_index)
        bmc_ib_hash, bmc_match_index_count, bmc_chunk_index, bmc_identity_source = _part_bmc_identity(
            child,
            region_hash=region_hash,
            region_index_count=region_index_count,
            part_index=part_index,
        )
        for obj in mesh_objects:
            previous_part = seen_objects.get(obj.name)
            if previous_part is not None:
                raise ValueError(
                    f"{obj.name}: object is linked into multiple export parts "
                    f"('{previous_part}' and '{child.name}')."
                )
            seen_objects[obj.name] = child.name
        parts.append(
            {
                "part_index": part_index,
                "part_name": f"{region_part_prefix}_part{part_index:02d}",
                "collection_name": child.name,
                "objects": mesh_objects,
                "implicit": False,
                "bmc_ib_hash": bmc_ib_hash,
                "bmc_match_index_count": bmc_match_index_count,
                "bmc_chunk_index": bmc_chunk_index,
                "bmc_identity_source": bmc_identity_source,
            }
        )
    if not parts:
        raise ValueError(f"Region collection '{root_collection.name}' has no non-empty part collections.")
    return sorted(parts, key=lambda item: (int(item["part_index"]), str(item["collection_name"])))


def _remove_legacy_manifest_files(export_root: Path):
    for pattern in ("draw_manifest*.json", "cs_batches*.json", "runtime_manifest*.json"):
        for manifest_path in export_root.glob(pattern):
            if manifest_path.is_file():
                manifest_path.unlink()


def _remove_legacy_runtime_buffers(buffer_dir: Path):
    for pattern in (
        "*_part*-draw*-PaletteMeta.buf",
        "*_part*-draw*-SkinMeta.buf",
        "*_part*-skinned-position.buf",
        "*_part*-skinned-frame.buf",
        "*_part*-7fec12c0.buf",
        "*_part*-9337f625.buf",
        "*_part*-d0b09bfb.buf",
        "*_part*-ad3c9baf.buf",
        "*_part*-cs-cb0.buf",
        "*_part*-pre-position.buf",
        "*_part*-blend-indices-weights.buf",
        "*_part*-pre-tangent-normal.buf",
        "*_part*-packed-uv.buf",
        "*_part*-skin-cb0.buf",
    ):
        for buffer_path in buffer_dir.glob(pattern):
            if buffer_path.is_file():
                buffer_path.unlink()


def _palette_entry_count(buffer_dir: Path, palette_file: str, *, part_name: str) -> int:
    palette_path = buffer_dir / palette_file
    if not palette_path.is_file():
        raise ValueError(
            f"{part_name}: missing bone palette '{palette_path}'. "
            "Generate/copy the matching *-Palette.buf from 3dmigoto_bone_merge before exporting this package."
        )
    byte_size = palette_path.stat().st_size
    if byte_size <= 0 or byte_size % 4 != 0:
        raise ValueError(f"{part_name}: palette file size must be a non-empty multiple of 4 bytes: {palette_path}")
    return byte_size // 4


def _most_common_int(values: list[int]) -> int | None:
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


def _resolve_original_match_index_count(
    parts: list[dict[str, object]],
    *,
    region_hash: str,
    region_index_count: int | None = None,
) -> tuple[int, str]:
    if region_index_count is not None:
        return int(region_index_count), "region_collection"

    matching_counts: list[int] = []
    all_counts: list[int] = []
    for part in parts:
        for draw in part["draws"]:
            original_index_count = int(draw["original_index_count"])
            all_counts.append(original_index_count)
            if str(draw.get("slice_hash", "")).lower() == region_hash:
                matching_counts.append(original_index_count)

    matched_count = _most_common_int(matching_counts)
    if matched_count is not None:
        return matched_count, "slice_hash"

    fallback_count = _most_common_int(all_counts)
    if fallback_count is not None:
        return fallback_count, "most_common_original_index_count"
    raise ValueError("Could not resolve original match_index_count from exported objects.")


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

    all_positions: list[tuple[float, float, float]] = []
    all_indices: list[int] = []
    all_packed_uv_entries: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    all_blend_indices: list[tuple[int, int, int, int]] = []
    all_blend_weights: list[tuple[int, int, int, int]] = []
    all_frame_a: list[tuple[float, float, float, float]] = []
    all_frame_b: list[tuple[float, float, float, float]] = []
    draw_records: list[dict[str, object]] = []

    vertex_cursor = 0
    index_cursor = 0
    required_local_palette_count = 0
    for draw_index, obj in enumerate(mesh_objects):
        payload = _extract_object_payload(
            obj,
            flip_uv_v=flip_uv_v,
            fallback_profile_id=str(region_runtime_contract.get("profile_id") or ""),
            fallback_original_first_index=region_first_index,
            fallback_original_index_count=region_index_count,
            fallback_producer_dispatch_index=region_runtime_contract.get("producer_dispatch_index"),
            fallback_producer_cs_hash=str(region_runtime_contract.get("producer_cs_hash") or ""),
            fallback_producer_t0_hash=str(region_runtime_contract.get("producer_t0_hash") or ""),
            fallback_last_cs_hash=str(region_runtime_contract.get("last_cs_hash") or ""),
            fallback_last_cs_cb0_hash=str(region_runtime_contract.get("last_cs_cb0_hash") or ""),
            fallback_last_consumer_draw_index=region_runtime_contract.get("last_consumer_draw_index"),
        )
        positions = payload["positions"]
        triangles = payload["triangles"]
        packed_uv_entries = payload["packed_uv_entries"]
        blend_indices = payload["blend_indices"]
        blend_weights = payload["blend_weights"]
        frame_a = payload["frame_a"]
        frame_b = payload["frame_b"]

        if len(positions) != len(packed_uv_entries):
            raise ValueError(f"{obj.name}: packed UV entry count does not match position count")
        if len(positions) != len(blend_indices) or len(positions) != len(blend_weights):
            raise ValueError(f"{obj.name}: blend payload count does not match position count")
        if len(positions) != len(frame_a) or len(positions) != len(frame_b):
            raise ValueError(f"{obj.name}: frame payload count does not match position count")

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

        # The collection tree is the export contract; object names/metadata are only fallback hints.
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

    palette_entry_count = _palette_entry_count(buffer_dir, bmc_palette_file, part_name=part_name)
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
        "skin_cb0": f"{part_name}-cb0.buf",
    }

    write_u16_buffer(str(buffer_dir / files["ib"]), all_indices)
    write_float3_buffer(str(buffer_dir / files["vb0_pre_cs"]), all_positions)
    write_weight_pairs_buffer(str(buffer_dir / files["weights"]), all_blend_indices, all_blend_weights)
    write_snorm8x4_pairs_buffer(str(buffer_dir / files["frame_pre_cs"]), all_frame_a, all_frame_b)
    write_half2x4_buffer(str(buffer_dir / files["packed_uv"]), all_packed_uv_entries)
    write_u32_buffer(str(buffer_dir / files["skin_cb0"]), [0, vertex_cursor, 0, palette_entry_count])

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
        "expected_palette_provider": "3dmigoto_bone_merge",
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


def _apply_capture_stage_hashes(
    region_packages: list[dict[str, object]],
    capture_ranges: list[dict[str, object]],
):
    stage_lookup: dict[tuple[str, int, int], dict[str, object]] = {}
    for item in capture_ranges:
        stage_lookup[
            (
                str(item["region_hash"]).lower(),
                int(item["match_index_count"]),
                int(item["first_index"]),
            )
        ] = item

    for package in region_packages:
        key = (
            str(package["region_hash"]).lower(),
            int(package["original_match_index_count"]),
            int(package["region_first_index"]),
        )
        item = stage_lookup.get(key)
        if item is None:
            continue
        if not _hash_tuple(package.get("depth_vs_hashes")):
            package["depth_vs_hashes"] = _hash_tuple(item.get("depth_vs_hashes"))
        if not _hash_tuple(package.get("gbuffer_vs_hashes")):
            package["gbuffer_vs_hashes"] = _hash_tuple(item.get("gbuffer_vs_hashes"))


def _yihuan_stage_filters(region_packages: list[dict[str, object]]) -> dict[str, dict[str, int]]:
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

    return {
        "depth": {
            hash_value: _YIHUAN_DEPTH_VS_FILTER_BASE + index
            for index, hash_value in enumerate(depth_unique)
        },
        "gbuffer": {
            hash_value: _YIHUAN_GBUFFER_VS_FILTER_BASE + index
            for index, hash_value in enumerate(gbuffer_unique)
        },
    }


def _append_yihuan_vs_stage_filters(lines: list[str], stage_filters: dict[str, dict[str, int]]):
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
                    f"[ShaderOverride_Yihuan{label}_{vs_hash[:8]}]",
                    f"hash = {vs_hash}",
                    f"filter_index = {filter_index}",
                    "allow_duplicate_hash = overrule",
                    "checktextureoverride = ib",
                    "",
                ]
            )


def _filter_condition(indices: list[int]) -> str:
    return " || ".join(f"vs == {int(value)}" for value in sorted(set(indices)))


def _part_draw_lines(part: dict[str, object]) -> list[str]:
    lines = []
    for draw in part["draws"]:
        lines.append(f"  ; [mesh:{draw['object_name']}] [vertex_count:{draw['vertex_count']}]")
        lines.append(f"  drawindexed = {draw['index_count']},{draw['first_index']},0")
    return lines


def _append_part_stage_draw(
    lines: list[str],
    *,
    part: dict[str, object],
    stage: str,
    use_runtime_skin: bool,
):
    part_token = str(part["resource_token"])
    if stage == "depth":
        if use_runtime_skin:
            lines.extend(
                [
                    f"  ; [depth part:{part['part_name']}] [vertex_count:{part['vertex_count']}]",
                    f"  ib = ResourceYihuan_{part_token}_IB",
                    f"  vb0 = ResourceYihuan_{part_token}_SkinnedPositionVB",
                    f"  vs-t3 = ResourceYihuan_{part_token}_Texcoord",
                    f"  vs-t4 = ResourceYihuan_{part_token}_Position",
                    f"  vs-t5 = ResourceYihuan_{part_token}_SkinnedNormal",
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
                    f"  vb0 = ResourceYihuan_{part_token}_SkinnedPositionVB",
                    f"  vs-t4 = ResourceYihuan_{part_token}_SkinnedPosition",
                    f"  vs-t5 = ResourceYihuan_{part_token}_Texcoord",
                    f"  vs-t6 = ResourceYihuan_{part_token}_Position",
                    f"  vs-t7 = ResourceYihuan_{part_token}_SkinnedNormal",
                ]
            )
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
    lines.extend(_part_draw_lines(part))


def _append_yihuan_main_resource_sections(lines: list[str], parts: list[dict[str, object]]):
    lines.extend(
        [
            "; MARK: Original binding restore slots",
            "[ResourceYihuanRestoreIB]",
            "[ResourceYihuanRestoreVB0]",
            "[ResourceYihuanRestoreVST3]",
            "[ResourceYihuanRestoreVST4]",
            "[ResourceYihuanRestoreVST5]",
            "[ResourceYihuanRestoreVST6]",
            "[ResourceYihuanRestoreVST7]",
            "[ResourceYihuanRestoreCSCB0]",
            "[ResourceYihuanRestoreCST0]",
            "[ResourceYihuanRestoreCST1]",
            "[ResourceYihuanRestoreCST2]",
            "[ResourceYihuanRestoreCST3]",
            "[ResourceYihuanRestoreCSU0]",
            "[ResourceYihuanRestoreCSU1]",
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
                f"[ResourceYihuan_{token}_Blend]",
                "type = StructuredBuffer",
                "stride = 8",
                f"filename = Buffer/{buffers['weights']}",
                "",
                f"[ResourceYihuan_{token}_PreFrame]",
                "type = StructuredBuffer",
                "stride = 8",
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


def _append_yihuan_bonestore_resource_sections(lines: list[str], parts: list[dict[str, object]]):
    lines.extend(
        [
            "; MARK: Shared bone store resources",
            "[ResourceGlobalT0Store_UAV]",
            "type = RWStructuredBuffer",
            "stride = 16",
            "array = 200000",
            "",
            "[ResourceGlobalT0Store]",
            "type = StructuredBuffer",
            "stride = 16",
            "array = 200000",
            "",
        ]
    )
    for part in parts:
        token = str(part["resource_token"])
        local_palette_count = int(part["local_palette_count"])
        local_t0_rows = int(part.get("local_t0_rows") or (local_palette_count * 3))
        lines.extend(
            [
                f"[ResourcePalette_{token}]",
                "type = Buffer",
                "format = R32_UINT",
                f"filename = BoneStore/Buffer/{part['expected_palette_file']}",
                "",
                f"[ResourcePaletteMeta_{token}]",
                "type = Buffer",
                "format = R32_FLOAT",
                f"data = {float(local_palette_count):.1f}",
                "",
                f"[ResourceLocalT0_{token}_UAV]",
                "type = RWStructuredBuffer",
                "stride = 16",
                f"array = {local_t0_rows}",
                "",
                f"[ResourceLocalT0_{token}]",
                "type = StructuredBuffer",
                "stride = 16",
                f"array = {local_t0_rows}",
                "",
            ]
        )


def _write_yihuan_main_ini(
    *,
    export_root: Path,
    ini_name: str,
    region_packages: list[dict[str, object]],
    include_runtime_skin: bool,
) -> Path:
    if not region_packages:
        raise ValueError("Cannot generate INI without region packages.")
    ini_path = export_root / ini_name
    lines: list[str] = []
    all_parts = [part for package in region_packages for part in package["parts"]]
    stage_filters = _yihuan_stage_filters(region_packages)
    _append_yihuan_main_resource_sections(lines, all_parts)
    _append_yihuan_vs_stage_filters(lines, stage_filters)
    lines.append("")
    if include_runtime_skin:
        lines.append("; MARK: Skin dispatch. Main INI gathers the requested local palette, then skins that part.")
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
                    f"[TextureOverride_YihuanSkinPart_{last_cs_cb0_hash}]",
                    f"hash = {last_cs_cb0_hash}",
                    "match_priority = 500",
                    f"if {filter_condition}",
                    "  ResourceYihuanRestoreCSCB0 = reference cs-cb0",
                    "  ResourceYihuanRestoreCST0 = reference cs-t0",
                    "  ResourceYihuanRestoreCST1 = reference cs-t1",
                    "  ResourceYihuanRestoreCST2 = reference cs-t2",
                    "  ResourceYihuanRestoreCST3 = reference cs-t3",
                    "  ResourceYihuanRestoreCSU0 = reference cs-u0",
                    "  ResourceYihuanRestoreCSU1 = reference cs-u1",
                ]
            )
            for part in cb0_parts:
                token = str(part["resource_token"])
                lines.extend(
                    [
                        f"  cs-t0 = Resource\\{_YIHUAN_BONESTORE_NAMESPACE}\\GlobalT0Store",
                        f"  cs-t1 = Resource\\{_YIHUAN_BONESTORE_NAMESPACE}\\Palette_{token}",
                        f"  cs-t2 = Resource\\{_YIHUAN_BONESTORE_NAMESPACE}\\PaletteMeta_{token}",
                        f"  cs-u0 = Resource\\{_YIHUAN_BONESTORE_NAMESPACE}\\LocalT0_{token}_UAV",
                        f"  run = CustomShader\\{_YIHUAN_BONESTORE_NAMESPACE}\\_GatherT0",
                        f"  Resource\\{_YIHUAN_BONESTORE_NAMESPACE}\\LocalT0_{token} = copy Resource\\{_YIHUAN_BONESTORE_NAMESPACE}\\LocalT0_{token}_UAV",
                        f"  cs-cb0 = ResourceYihuan_{token}_CB0",
                        f"  cs-t0 = Resource\\{_YIHUAN_BONESTORE_NAMESPACE}\\LocalT0_{token}",
                        f"  cs-t1 = ResourceYihuan_{token}_Position",
                        f"  cs-t2 = ResourceYihuan_{token}_Blend",
                        f"  cs-t3 = ResourceYihuan_{token}_PreFrame",
                        f"  cs-u0 = ResourceYihuan_{token}_SkinnedNormal_UAV",
                        f"  cs-u1 = ResourceYihuan_{token}_SkinnedPosition_UAV",
                        f"  run = CustomShader\\{_YIHUAN_BONESTORE_NAMESPACE}\\_SkinPart",
                        f"  ResourceYihuan_{token}_SkinnedNormal = copy ResourceYihuan_{token}_SkinnedNormal_UAV",
                        f"  ResourceYihuan_{token}_SkinnedPosition = copy ResourceYihuan_{token}_SkinnedPosition_UAV",
                        f"  ResourceYihuan_{token}_SkinnedPositionVB = copy ResourceYihuan_{token}_SkinnedPosition_UAV",
                    ]
                )
            lines.extend(
                [
                    "  cs-cb0 = reference ResourceYihuanRestoreCSCB0",
                    "  cs-t0 = reference ResourceYihuanRestoreCST0",
                    "  cs-t1 = reference ResourceYihuanRestoreCST1",
                    "  cs-t2 = reference ResourceYihuanRestoreCST2",
                    "  cs-t3 = reference ResourceYihuanRestoreCST3",
                    "  cs-u0 = reference ResourceYihuanRestoreCSU0",
                    "  cs-u1 = reference ResourceYihuanRestoreCSU1",
                    "endif",
                    "",
                ]
            )
    else:
        lines.extend(
            [
                "; MARK: Skin dispatch omitted.",
                "; Runtime BoneStore generation was skipped because no usable FrameAnalysis/capture manifest was found.",
                "",
            ]
        )

    lines.append("; MARK: Draw replacement")
    for package in region_packages:
        region_hash = str(package["region_hash"])
        source_ib_hash = str(package["source_ib_hash"])
        lines.extend(
            [
                f"[TextureOverride_IB_{region_hash}]",
                f"hash = {source_ib_hash}",
            ]
        )
        if package.get("region_first_index") is not None:
            lines.append(f"match_first_index = {int(package['region_first_index'])}")
        lines.extend(
            [
                f"match_index_count = {int(package['original_match_index_count'])}",
                "handling = skip",
                "ResourceYihuanRestoreIB = reference ib",
                "ResourceYihuanRestoreVB0 = reference vb0",
                "ResourceYihuanRestoreVST3 = reference vs-t3",
                "ResourceYihuanRestoreVST4 = reference vs-t4",
                "ResourceYihuanRestoreVST5 = reference vs-t5",
                "ResourceYihuanRestoreVST6 = reference vs-t6",
                "ResourceYihuanRestoreVST7 = reference vs-t7",
            ]
        )
        depth_indices = [
            stage_filters["depth"][hash_value]
            for hash_value in _hash_tuple(package.get("depth_vs_hashes"))
            if hash_value in stage_filters["depth"]
        ]
        gbuffer_indices = [
            stage_filters["gbuffer"][hash_value]
            for hash_value in _hash_tuple(package.get("gbuffer_vs_hashes"))
            if hash_value in stage_filters["gbuffer"]
        ]
        if depth_indices:
            lines.append(f"if {_filter_condition(depth_indices)}")
            for part in package["parts"]:
                _append_part_stage_draw(lines, part=part, stage="depth", use_runtime_skin=include_runtime_skin)
            lines.append("endif")
        if gbuffer_indices:
            lines.append(f"if {_filter_condition(gbuffer_indices)}")
            for part in package["parts"]:
                _append_part_stage_draw(lines, part=part, stage="gbuffer", use_runtime_skin=include_runtime_skin)
            lines.append("endif")
        if not depth_indices and not gbuffer_indices:
            # Last-resort fallback for old collection metadata. Keeping a visible
            # fallback is safer than silently exporting a skip-only override.
            for part in package["parts"]:
                _append_part_stage_draw(lines, part=part, stage="gbuffer", use_runtime_skin=include_runtime_skin)
        lines.extend(
            [
                "ib = reference ResourceYihuanRestoreIB",
                "vb0 = reference ResourceYihuanRestoreVB0",
                "vs-t3 = reference ResourceYihuanRestoreVST3",
                "vs-t4 = reference ResourceYihuanRestoreVST4",
                "vs-t5 = reference ResourceYihuanRestoreVST5",
                "vs-t6 = reference ResourceYihuanRestoreVST6",
                "vs-t7 = reference ResourceYihuanRestoreVST7",
            ]
        )
        lines.append("")

    ini_path.write_text("\n".join(lines), encoding="utf-8")
    return ini_path


def _write_yihuan_runtime_ini(
    *,
    export_root: Path,
    ini_name: str,
    region_packages: list[dict[str, object]],
    capture_ranges: list[dict[str, object]],
) -> Path:
    if not region_packages:
        raise ValueError("Cannot generate runtime INI without region packages.")
    for package in region_packages:
        if not str(package.get("last_cs_cb0_hash", "")):
            raise ValueError(
                f"{package.get('region_hash', 'unknown')}: cannot generate runtime INI without last_cs_cb0_hash."
            )

    runtime_path = export_root / ini_name
    all_parts = [part for package in region_packages for part in package["parts"]]
    lines: list[str] = [
        f"namespace = {_YIHUAN_BONESTORE_NAMESPACE}",
        "",
        "; MARK: Shader filters for the original skinning CS chain",
    ]
    for cs_hash, filter_index in _YIHUAN_CS_FILTER_INDICES.items():
        lines.extend(
            [
                f"[ShaderOverride_YihuanCS_{cs_hash[:8]}]",
                f"hash = {cs_hash}",
                f"filter_index = {filter_index}",
                "allow_duplicate_hash = overrule",
                "checktextureoverride = cs-cb0",
                "",
            ]
        )

    lines.append("; MARK: Runtime resources")
    _append_yihuan_bonestore_resource_sections(lines, all_parts)
    lines.append("")

    lines.extend(
        [
            "; MARK: Original CS binding restore slots",
            "[ResourceRestoreCollectCST2]",
            "[ResourceRestoreCollectCSU0]",
            "",
            "; MARK: Explicit collect rules.",
            "; Each meta says where this original cs-cb0 range's current cs-t0 rows go in GlobalT0Store.",
            "; data = expected_start expected_count global_bone_base bone_count",
        ]
    )
    for item in capture_ranges:
        token = _resource_token(
            f"{item['region_hash']}_{item['match_index_count']}_{item['first_index']}"
        )
        lines.extend(
            [
                f"[ResourceCollectMeta_{token}]",
                "type = Buffer",
                "format = R32_UINT",
                (
                    "data = "
                    f"{int(item['start_vertex'])} "
                    f"{int(item['vertex_count'])} "
                    f"{int(item['global_bone_base'])} "
                    f"{int(item['capture_bone_count'])}"
                ),
                "",
            ]
        )

    lines.extend(
        [
            "",
            "; MARK: Shared custom shaders. BoneStore auto-runs only collect; main INI explicitly runs gather/skin.",
            "[CustomShader_CollectT0]",
            "cs = BoneStore\\hlsl\\yihuan_collect_t0_cs.hlsl",
            "cs-u0 = ResourceGlobalT0Store_UAV",
            "dispatch = 64, 1, 1",
            "ResourceGlobalT0Store = copy ResourceGlobalT0Store_UAV",
            "",
            "[CustomShader_GatherT0]",
            "cs = BoneStore\\hlsl\\yihuan_gather_t0_cs.hlsl",
            "cs-t0 = ResourceGlobalT0Store",
            "dispatch = 64, 1, 1",
            "",
        ]
    )

    lines.append("; MARK: Bone collection hooks")
    filter_condition = (
        f"cs == {_YIHUAN_CS_FILTER_INDICES['f33fea3cca2704e4']} || "
        f"cs == {_YIHUAN_CS_FILTER_INDICES['1e2a9061eadfeb6c']}"
    )
    for priority_offset, item in enumerate(capture_ranges):
        token = _resource_token(
            f"{item['region_hash']}_{item['match_index_count']}_{item['first_index']}"
        )
        cb0_hash = str(item.get("cb0_hash") or _YIHUAN_DEFAULT_LAST_CS_CB0_HASH).strip().lower()
        lines.extend(
            [
                f"[TextureOverride_CollectT0_{token}]",
                f"hash = {cb0_hash}",
                f"match_priority = {-500 + priority_offset}",
                f"if {filter_condition}",
                "  ResourceRestoreCollectCST2 = reference cs-t2",
                "  ResourceRestoreCollectCSU0 = reference cs-u0",
                f"  cs-t2 = ResourceCollectMeta_{token}",
                "  run = CustomShader_CollectT0",
                "  cs-t2 = reference ResourceRestoreCollectCST2",
                "  cs-u0 = reference ResourceRestoreCollectCSU0",
            ]
        )
        lines.extend(["endif", ""])

    runtime_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return runtime_path


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
    frame_dump_dir: str | None = None,
    flip_uv_v: bool = False,
) -> dict[str, object]:
    """Export one strict sourceIB -> region -> part collection tree into runtime replacement assets."""
    collection = _get_collection(collection_name)
    export_root = _ensure_directory(Path(export_dir).resolve())
    buffer_dir = _ensure_directory(export_root / "Buffer")
    source_ib_hash = _source_root_hash(collection)
    capture_ranges = _try_load_yihuan_capture_ranges(
        export_root,
        source_ib_hash,
        frame_dump_dir=frame_dump_dir,
    )
    runtime_skin_enabled = bool(capture_ranges)
    hlsl_dir = export_profile_hlsl_assets(YIHUAN_PROFILE.profile_id, export_root)
    _remove_legacy_manifest_files(export_root)
    _remove_legacy_runtime_buffers(buffer_dir)

    legacy_runtime_ini = export_root / f"{source_ib_hash}-runtime.ini"
    if legacy_runtime_ini.is_file():
        legacy_runtime_ini.unlink()
    bonestore_ini = export_root / f"{source_ib_hash}-BoneStore.ini"
    if bonestore_ini.is_file() and not runtime_skin_enabled:
        bonestore_ini.unlink()
    region_collections = _resolve_region_collections(collection)
    region_packages = [
        _export_region_package(
            region_collection=region_collection,
            source_ib_hash=source_ib_hash,
            buffer_dir=buffer_dir,
            require_runtime_contract=runtime_skin_enabled,
            flip_uv_v=flip_uv_v,
        )
        for region_collection in region_collections
    ]
    if capture_ranges:
        _apply_capture_stage_hashes(region_packages, capture_ranges)
        _write_yihuan_collect_t0_hlsl(hlsl_dir=hlsl_dir, capture_ranges=capture_ranges)

    ini_name = f"{source_ib_hash}.ini"
    ini_file = _write_yihuan_main_ini(
        export_root=export_root,
        ini_name=ini_name,
        region_packages=region_packages,
        include_runtime_skin=runtime_skin_enabled,
    )
    runtime_ini_file: Path | None = None
    if runtime_skin_enabled:
        runtime_ini_file = _write_yihuan_runtime_ini(
            export_root=export_root,
            ini_name=f"{source_ib_hash}-BoneStore.ini",
            region_packages=region_packages,
            capture_ranges=capture_ranges,
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
        "hlsl_dir": str(hlsl_dir),
        "ini_path": str(ini_file),
        "runtime_ini_path": str(runtime_ini_file) if runtime_ini_file is not None else "",
    }
