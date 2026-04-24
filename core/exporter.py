"""Collection export pipeline for the 异环 profile."""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

import bpy

from .game_data import DecodedTangentFrame, get_game_data_converter
from .hlsl_assets import export_profile_hlsl_assets
from .io import (
    write_float3_buffer,
    write_half2x4_buffer,
    write_json,
    write_snorm8x4_pairs_buffer,
    write_u16_buffer,
    write_u32_buffer,
    write_weight_pairs_buffer,
)
from .profiles import YIHUAN_PROFILE


def _ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_collection(collection_name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        raise ValueError(f"Collection does not exist: {collection_name}")
    return collection


def _require_object_prop(obj: bpy.types.Object, key: str):
    if key not in obj:
        raise ValueError(f"{obj.name}: missing required imported metadata '{key}'")
    return obj[key]


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


def _triangulated_mesh_copy(obj: bpy.types.Object) -> bpy.types.Mesh:
    import bmesh

    mesh_copy = obj.data.copy()
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh_copy)
        bmesh.ops.triangulate(bm, faces=list(bm.faces))
        bm.to_mesh(mesh_copy)
    finally:
        bm.free()
    mesh_copy.calc_loop_triangles()
    return mesh_copy


def _normalized_vector(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = (vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2]) ** 0.5
    if length <= 1e-8:
        return (0.0, 0.0, 0.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _vector_key(vector: tuple[float, float, float]) -> tuple[int, int, int]:
    return tuple(int(round(float(component) * 1_000_000.0)) for component in vector)


def _decode_legacy_frames(
    mesh: bpy.types.Mesh,
    *,
    profile_id: str,
) -> list[DecodedTangentFrame] | None:
    frame0_xyz = _find_point_vector_attribute(mesh, "pre_cs_frame0_xyz")
    frame0_w = _find_point_float_attribute(mesh, "pre_cs_frame0_w")
    frame1_xyz = _find_point_vector_attribute(mesh, "pre_cs_frame1_xyz")
    frame1_w = _find_point_float_attribute(mesh, "pre_cs_frame1_w")
    if frame0_xyz is None or frame0_w is None or frame1_xyz is None or frame1_w is None:
        return None

    converter = get_game_data_converter(profile_id)
    raw_frame_a = [
        (float(x_value), float(y_value), float(z_value), float(w_value))
        for (x_value, y_value, z_value), w_value in zip(frame0_xyz, frame0_w)
    ]
    raw_frame_b = [
        (float(x_value), float(y_value), float(z_value), float(w_value))
        for (x_value, y_value, z_value), w_value in zip(frame1_xyz, frame1_w)
    ]
    return converter.decode_pre_cs_frames(raw_frame_a, raw_frame_b)


def _decoded_point_frames(
    mesh: bpy.types.Mesh,
    *,
    profile_id: str,
) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]], list[float]]:
    tangents = _find_point_vector_attribute(mesh, "modimp_tangent")
    normals = _find_point_vector_attribute(mesh, "modimp_normal")
    bitangent_signs = _find_point_float_attribute(mesh, "modimp_bitangent_sign")
    if tangents is not None and normals is not None and bitangent_signs is not None:
        return tangents, normals, bitangent_signs

    legacy_frames = _decode_legacy_frames(mesh, profile_id=profile_id)
    if legacy_frames is None:
        raise ValueError(
            f"{mesh.name}: missing decoded tangent-space attributes. "
            "Expected modimp_tangent/modimp_normal/modimp_bitangent_sign."
        )
    return (
        [frame.tangent for frame in legacy_frames],
        [frame.normal for frame in legacy_frames],
        [frame.bitangent_sign for frame in legacy_frames],
    )


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


def _normalized_top4_weights(obj: bpy.types.Object) -> tuple[list[tuple[int, int, int, int]], list[tuple[int, int, int, int]], int]:
    mesh = obj.data
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


def _extract_object_payload(obj: bpy.types.Object) -> dict[str, object]:
    mesh = obj.data
    if obj.type != "MESH":
        raise ValueError(f"{obj.name}: only mesh objects can be exported")
    profile_id = _require_object_prop(obj, "modimp_profile_id")
    if profile_id != YIHUAN_PROFILE.profile_id:
        raise ValueError(f"{obj.name}: unsupported profile id")
    converter = get_game_data_converter(profile_id)

    blend_indices, blend_weights, local_palette_count = _normalized_top4_weights(obj)
    packed_uv1 = _read_point_vector_attribute(mesh, "packed_uv1")
    packed_uv2 = _read_point_vector_attribute(mesh, "packed_uv2")
    packed_uv3 = _read_point_vector_attribute(mesh, "packed_uv3")
    fallback_tangents, fallback_normals, fallback_signs = _decoded_point_frames(mesh, profile_id=profile_id)

    mesh_copy = _triangulated_mesh_copy(obj)
    active_uv_layer = mesh_copy.uv_layers.active
    if active_uv_layer is None:
        bpy.data.meshes.remove(mesh_copy)
        raise ValueError(f"{obj.name}: active UV layer is required for export")
    loop_frames = _prepare_loop_tangent_frames(mesh_copy, uv_layer_name=active_uv_layer.name)

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

    try:
        for polygon in mesh_copy.polygons:
            if polygon.loop_total != 3:
                raise ValueError(f"{obj.name}: triangulation failed; found a polygon with {polygon.loop_total} corners")
            triangle: list[int] = []
            for loop_index in polygon.loop_indices:
                source_vertex_index = mesh_copy.loops[loop_index].vertex_index
                uv0 = tuple(float(value) for value in active_uv_layer.data[loop_index].uv)
                if loop_frames is not None:
                    loop_tangents, loop_normals, loop_signs = loop_frames
                    decoded_tangent = loop_tangents[loop_index]
                    decoded_normal = loop_normals[loop_index]
                    decoded_sign = loop_signs[loop_index]
                else:
                    decoded_tangent = _normalized_vector(fallback_tangents[source_vertex_index])
                    decoded_normal = _normalized_vector(fallback_normals[source_vertex_index])
                    decoded_sign = 1.0 if float(fallback_signs[source_vertex_index]) >= 0.0 else -1.0

                key = (
                    source_vertex_index,
                    *_uv_key(uv0),
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
                            (float(packed_uv1[source_vertex_index][0]), float(packed_uv1[source_vertex_index][1])),
                            (float(packed_uv2[source_vertex_index][0]), float(packed_uv2[source_vertex_index][1])),
                            (float(packed_uv3[source_vertex_index][0]), float(packed_uv3[source_vertex_index][1])),
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

    return {
        "object_name": obj.name,
        "positions": positions,
        "triangles": triangles,
        "packed_uv_entries": packed_uv_entries,
        "blend_indices": out_blend_indices,
        "blend_weights": out_blend_weights,
        "frame_a": frame_a,
        "frame_b": frame_b,
        "local_palette_count": local_palette_count,
        "original_first_index": int(_require_object_prop(obj, "modimp_first_index")),
        "original_index_count": int(_require_object_prop(obj, "modimp_index_count")),
        "producer_dispatch_index": int(_require_object_prop(obj, "modimp_producer_dispatch_index")),
        "producer_cs_hash": str(_require_object_prop(obj, "modimp_producer_cs_hash")),
        "producer_t0_hash": str(obj.get("modimp_producer_t0_hash", "")),
        "last_cs_hash": str(obj.get("modimp_last_cs_hash", "")),
        "last_cs_cb0_hash": str(obj.get("modimp_last_cs_cb0_hash", "")),
        "last_consumer_draw_index": int(obj.get("modimp_last_consumer_draw_index", 0)),
    }


def _build_cb0_values(cs_hash: str, vertex_start: int, vertex_count: int) -> list[int]:
    if cs_hash == "f33fea3cca2704e4":
        return [vertex_start, vertex_start, vertex_count, vertex_start * 2, 8, 0, 0, 0]
    if cs_hash == "1e2a9061eadfeb6c":
        return [vertex_start, vertex_start, vertex_start, vertex_count, vertex_start * 2, 8, 0, 0]
    raise ValueError(f"Unsupported producer CS hash for cb0 rebuild: {cs_hash}")


def export_collection_package(
    *,
    collection_name: str,
    export_dir: str,
) -> dict[str, object]:
    """Export one collection into shared big buffers, manifests, and runtime HLSL assets."""
    collection = _get_collection(collection_name)
    mesh_objects = [obj for obj in collection.all_objects if obj.type == "MESH"]
    if not mesh_objects:
        raise ValueError(f"No mesh objects found in collection: {collection_name}")

    mesh_objects.sort(key=lambda obj: (int(obj.get("modimp_slice_order", 0)), obj.name))
    ib_hashes = {str(_require_object_prop(obj, "modimp_ib_hash")).lower() for obj in mesh_objects}
    if len(ib_hashes) != 1:
        raise ValueError("Export collection must contain objects from exactly one IB hash")
    ib_hash = next(iter(ib_hashes))

    export_root = _ensure_directory(Path(export_dir).resolve())
    buffer_dir = _ensure_directory(export_root / "buffers")
    hlsl_dir = export_profile_hlsl_assets(YIHUAN_PROFILE.profile_id, export_root)

    all_positions: list[tuple[float, float, float]] = []
    all_indices: list[int] = []
    all_packed_uv_entries: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    all_blend_indices: list[tuple[int, int, int, int]] = []
    all_blend_weights: list[tuple[int, int, int, int]] = []
    all_frame_a: list[tuple[float, float, float, float]] = []
    all_frame_b: list[tuple[float, float, float, float]] = []
    draw_manifest: list[dict[str, object]] = []
    runtime_slices: list[dict[str, object]] = []
    batch_buckets: dict[int, list[dict[str, object]]] = defaultdict(list)
    main_slice: dict[str, object] | None = None

    vertex_cursor = 0
    index_cursor = 0
    for slice_order, obj in enumerate(mesh_objects):
        payload = _extract_object_payload(obj)
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

        slice_hash = str(obj.get("modimp_display_ib_hash", "")).strip().lower()
        if not slice_hash:
            slice_hash = f"{first_index}-{index_count}"

        palette_meta_file = f"{ib_hash}-{slice_hash}-PaletteMeta.buf"
        palette_meta_path = buffer_dir / palette_meta_file
        write_u32_buffer(str(palette_meta_path), [int(payload["local_palette_count"])])

        draw_record = {
            "slice_order": slice_order,
            "object_name": payload["object_name"],
            "slice_hash": slice_hash,
            "first_index": first_index,
            "index_count": index_count,
            "vertex_start": vertex_start,
            "vertex_count": len(positions),
            "original_first_index": int(payload["original_first_index"]),
            "original_index_count": int(payload["original_index_count"]),
            "producer_dispatch_index": int(payload["producer_dispatch_index"]),
            "producer_cs_hash": str(payload["producer_cs_hash"]),
            "producer_t0_hash": str(payload["producer_t0_hash"]),
            "last_cs_hash": str(payload["last_cs_hash"]),
            "last_cs_cb0_hash": str(payload["last_cs_cb0_hash"]),
            "last_consumer_draw_index": int(payload["last_consumer_draw_index"]),
            "local_palette_count": int(payload["local_palette_count"]),
            "expected_palette_file": f"{ib_hash}-{slice_hash}-Palette.buf",
            "palette_meta_file": palette_meta_file,
            "palette_meta_path": str(palette_meta_path),
        }
        draw_manifest.append(draw_record)
        runtime_slices.append(
            {
                "object_name": payload["object_name"],
                "slice_hash": slice_hash,
                "first_index": first_index,
                "index_count": index_count,
                "last_cs_hash": str(payload["last_cs_hash"]),
                "last_cs_cb0_hash": str(payload["last_cs_cb0_hash"]),
                "producer_dispatch_index": int(payload["producer_dispatch_index"]),
                "producer_cs_hash": str(payload["producer_cs_hash"]),
                "producer_t0_hash": str(payload["producer_t0_hash"]),
                "bind_after_draw_index": int(payload["last_consumer_draw_index"]),
                "bind_slot": "cs-t0",
                "expected_palette_file": f"{ib_hash}-{slice_hash}-Palette.buf",
                "palette_meta_file": palette_meta_file,
            }
        )
        batch_buckets[int(payload["producer_dispatch_index"])].append(draw_record)
        if main_slice is None or index_count > int(main_slice["index_count"]):
            main_slice = draw_record

    if vertex_cursor > 0x10000:
        raise ValueError(
            f"Exported vertex count {vertex_cursor} exceeds the current R16_UINT index window (65536 vertices)."
        )

    cs_batches: list[dict[str, object]] = []
    for producer_dispatch_index in sorted(batch_buckets):
        records = sorted(batch_buckets[producer_dispatch_index], key=lambda item: int(item["vertex_start"]))
        batch_start = int(records[0]["vertex_start"])
        batch_end = max(int(item["vertex_start"]) + int(item["vertex_count"]) for item in records)
        batch_count = batch_end - batch_start
        producer_cs_hash = str(records[0]["producer_cs_hash"])
        cb0_values = _build_cb0_values(producer_cs_hash, batch_start, batch_count)
        cs_batches.append(
            {
                "producer_dispatch_index": producer_dispatch_index,
                "cs_hash": producer_cs_hash,
                "last_cs_cb0_hash": str(records[0]["last_cs_cb0_hash"]),
                "vertex_start": batch_start,
                "vertex_count": batch_count,
                "cb0_u32": cb0_values,
                "slice_count": len(records),
                "slice_first_indices": [int(item["first_index"]) for item in records],
            }
        )

    ib_file = buffer_dir / f"{ib_hash}-ib.buf"
    vb_file = buffer_dir / f"{ib_hash}-7fec12c0.buf"
    weights_file = buffer_dir / f"{ib_hash}-9337f625.buf"
    frame_file = buffer_dir / f"{ib_hash}-d0b09bfb.buf"
    uv_file = buffer_dir / f"{ib_hash}-ad3c9baf.buf"
    draw_manifest_file = export_root / "draw_manifest.json"
    cs_batches_file = export_root / "cs_batches.json"
    runtime_manifest_file = export_root / "runtime_manifest.json"

    write_u16_buffer(str(ib_file), all_indices)
    write_float3_buffer(str(vb_file), all_positions)
    write_weight_pairs_buffer(str(weights_file), all_blend_indices, all_blend_weights)
    write_snorm8x4_pairs_buffer(str(frame_file), all_frame_a, all_frame_b)
    write_half2x4_buffer(str(uv_file), all_packed_uv_entries)
    write_json(str(draw_manifest_file), draw_manifest)
    write_json(str(cs_batches_file), cs_batches)
    write_json(
        str(runtime_manifest_file),
        {
            "profile_id": YIHUAN_PROFILE.profile_id,
            "ib_hash": ib_hash,
            "last_cs_hash": str(main_slice["last_cs_hash"]) if main_slice else "",
            "last_cs_cb0_hash": str(main_slice["last_cs_cb0_hash"]) if main_slice else "",
            "bind_slot": "cs-t0",
            "hlsl": {
                "collect": "yihuan_collect_t0_cs.hlsl",
                "gather": "yihuan_gather_t0_cs.hlsl",
            },
            "buffers": {
                "ib": ib_file.name,
                "vb0_pre_cs": vb_file.name,
                "weights": weights_file.name,
                "frame_pre_cs": frame_file.name,
                "packed_uv": uv_file.name,
            },
            "external_palette_provider": "3dmigoto_bone_merge",
            "slices": runtime_slices,
        },
    )

    return {
        "profile_id": YIHUAN_PROFILE.profile_id,
        "collection_name": collection_name,
        "ib_hash": ib_hash,
        "vertex_count": vertex_cursor,
        "triangle_count": len(all_indices) // 3,
        "slice_count": len(draw_manifest),
        "buffer_dir": str(buffer_dir),
        "hlsl_dir": str(hlsl_dir),
        "draw_manifest_path": str(draw_manifest_file),
        "cs_batches_path": str(cs_batches_file),
        "runtime_manifest_path": str(runtime_manifest_file),
    }
