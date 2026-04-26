"""Blender mesh creation helpers for the importer/exporter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import bpy

from .game_data import get_game_data_converter
from .io import (
    build_compacted_geometry,
    read_half2x4_records,
    read_index_slice_txt,
    read_post_cs_frame_pairs,
    read_pre_cs_frame_pairs,
    read_u16_buffer,
    read_vb0_positions,
    read_weight_pairs,
)
from .models import DetectedModelBundle, PackedHalf2x4, ResolvedImportBundle, SectionTransform
from .profiles import YIHUAN_PROFILE


_PART_COMMENT_RE = re.compile(r"^;\s*\[part:(?P<part>[^\]]+)\]")
_STAGE_PART_COMMENT_RE = re.compile(r"^;\s*\[(?:depth|gbuffer)(?:-static)?\s+part:(?P<part>[^\]]+)\]")
_MESH_COMMENT_RE = re.compile(r"^;\s*\[mesh:(?P<name>[^\]]+)\](?:\s*\[vertex_count:(?P<count>\d+)\])?")
_RESOURCE_SECTION_RE = re.compile(
    r"^\[ResourceYihuan_(?P<token>.+?)_(?P<kind>IB|Position|Blend|Normal|Texcoord)\]$"
)
_DRAWINDEXED_RE = re.compile(r"^drawindexed\s*=\s*(?P<count>\d+)\s*,\s*(?P<first>\d+)\s*,\s*(?P<base>\d+)\s*$", re.IGNORECASE)
_REGION_HASH_PREFIX_RE = re.compile(r"^(?P<hash>[0-9A-Fa-f]{8})")


@dataclass(frozen=True)
class _LoadedImportResources:
    positions: list[tuple[float, float, float]]
    packed_uv_entries: list[PackedHalf2x4]
    blend_indices: list[tuple[int, int, int, int]] | None = None
    blend_weights_u8: list[tuple[int, int, int, int]] | None = None
    pre_frame_a: list[tuple[float, float, float, float]] | None = None
    pre_frame_b: list[tuple[float, float, float, float]] | None = None
    post_frame_a: list[tuple[float, float, float, float]] | None = None
    post_frame_b: list[tuple[float, float, float, float]] | None = None


@dataclass(frozen=True)
class _ExportedDrawRecord:
    part_name: str
    object_name: str
    first_index: int
    index_count: int


@dataclass
class _ExportedPartResources:
    part_name: str
    resource_token: str
    ib_path: Path | None = None
    position_path: Path | None = None
    blend_path: Path | None = None
    normal_path: Path | None = None
    texcoord_path: Path | None = None
    draws: list[_ExportedDrawRecord] | None = None

    def __post_init__(self):
        if self.draws is None:
            self.draws = []


def _ensure_collection(scene: bpy.types.Scene, collection_name: str) -> bpy.types.Collection:
    if not collection_name:
        return scene.collection

    existing_collection = bpy.data.collections.get(collection_name)
    if existing_collection is None:
        existing_collection = bpy.data.collections.new(collection_name)

    if scene.collection.children.get(existing_collection.name) is None:
        scene.collection.children.link(existing_collection)

    return existing_collection


def _ensure_child_collection(parent: bpy.types.Collection, collection_name: str) -> bpy.types.Collection:
    existing_collection = bpy.data.collections.get(collection_name)
    if existing_collection is None:
        existing_collection = bpy.data.collections.new(collection_name)
    if existing_collection.name not in parent.children.keys():
        parent.children.link(existing_collection)
    return existing_collection


def _select_imported_objects(
    context: bpy.types.Context,
    imported_objects: list[bpy.types.Object],
    *,
    active_object: bpy.types.Object,
):
    for selected_object in context.selected_objects:
        selected_object.select_set(False)
    for imported_object in imported_objects:
        imported_object.select_set(True)
    context.view_layer.objects.active = active_object


def _apply_uv_layer(mesh: bpy.types.Mesh, packed_uv_entries: list[PackedHalf2x4], *, flip_uv_v: bool):
    uv_layer = mesh.uv_layers.new(name="UV0")
    for polygon in mesh.polygons:
        for loop_index in polygon.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            u_coord, v_coord = packed_uv_entries[vertex_index][0]
            uv_layer.data[loop_index].uv = (u_coord, 1.0 - v_coord if flip_uv_v else v_coord)


def _store_vector_attribute(mesh: bpy.types.Mesh, name: str, values: list[tuple[float, float, float]]):
    attribute = mesh.attributes.new(name=name, type="FLOAT_VECTOR", domain="POINT")
    for item, value in zip(attribute.data, values):
        item.vector = value


def _store_float_attribute(mesh: bpy.types.Mesh, name: str, values: list[float]):
    attribute = mesh.attributes.new(name=name, type="FLOAT", domain="POINT")
    for item, value in zip(attribute.data, values):
        item.value = float(value)


def _store_int_attribute(mesh: bpy.types.Mesh, name: str, values: list[int]):
    attribute = mesh.attributes.new(name=name, type="INT", domain="POINT")
    for item, value in zip(attribute.data, values):
        item.value = int(value)


def _store_original_vertex_ids(mesh: bpy.types.Mesh, original_vertex_ids: list[int]):
    _store_int_attribute(mesh, "orig_vertex_id", [int(value) for value in original_vertex_ids])


def _store_packed_uv_attributes(mesh: bpy.types.Mesh, packed_uv_entries: list[PackedHalf2x4]):
    for entry_index in range(1, 4):
        _store_vector_attribute(
            mesh,
            f"packed_uv{entry_index}",
            [
                (
                    float(record[entry_index][0]),
                    float(record[entry_index][1]),
                    0.0,
                )
                for record in packed_uv_entries
            ],
        )


def _store_decoded_tangent_frame_attributes(
    mesh: bpy.types.Mesh,
    tangents: list[tuple[float, float, float]],
    normals: list[tuple[float, float, float]],
    bitangent_signs: list[float],
):
    _store_vector_attribute(mesh, "modimp_tangent", tangents)
    _store_vector_attribute(mesh, "modimp_normal", normals)
    _store_float_attribute(mesh, "modimp_bitangent_sign", bitangent_signs)


def _apply_custom_normals(mesh: bpy.types.Mesh, normals: list[tuple[float, float, float]]):
    if len(normals) != len(mesh.vertices):
        raise ValueError(
            f"Normal count mismatch: got {len(normals)} custom normals for {len(mesh.vertices)} mesh vertices."
        )

    if hasattr(mesh, "use_auto_smooth"):
        mesh.use_auto_smooth = True
    mesh.normals_split_custom_set_from_vertices(normals)


def _assign_palette_groups(
    imported_object: bpy.types.Object,
    blend_indices: list[tuple[int, int, int, int]],
    blend_weights: list[tuple[float, float, float, float]],
):
    vertex_groups: dict[int, bpy.types.VertexGroup] = {}
    for vertex_index, (index_record, weight_record) in enumerate(zip(blend_indices, blend_weights)):
        for palette_index, bone_weight in zip(index_record, weight_record):
            if bone_weight <= 0.0:
                continue
            vertex_group = vertex_groups.get(palette_index)
            if vertex_group is None:
                vertex_group = imported_object.vertex_groups.new(name=str(palette_index))
                vertex_groups[palette_index] = vertex_group
            vertex_group.add([vertex_index], bone_weight, "ADD")


def _assign_section_vertex_group(imported_object: bpy.types.Object, section_selector: int):
    vertex_group = imported_object.vertex_groups.new(name=f"section_{section_selector:03d}")
    vertex_group.add(list(range(len(imported_object.data.vertices))), 1.0, "REPLACE")


def _slice_object_name(*, object_prefix: str, ib_hash: str, detected_slice) -> str:
    hash_value = (detected_slice.display_ib_hash or ib_hash or "unknown").lower()
    base_name = f"{hash_value}-{int(detected_slice.index_count)}-{int(detected_slice.first_index)}"
    prefix = object_prefix.strip()
    return f"{prefix}_{base_name}" if prefix else base_name


def _roundtrip_region_hash(part_name: str, source_ib_hash: str) -> str:
    match = _REGION_HASH_PREFIX_RE.match(part_name)
    if match:
        return match.group("hash").lower()
    return source_ib_hash.lower()


def _apply_section_transform(
    imported_object: bpy.types.Object,
    section_transform: SectionTransform,
    *,
    profile_id: str,
):
    from mathutils import Matrix

    converter = get_game_data_converter(profile_id)
    converted_transform = converter.to_blender_section_transform(section_transform)
    basis_x = converted_transform.basis_x
    basis_y = converted_transform.basis_y
    basis_z = converted_transform.basis_z
    translation = converted_transform.translation
    imported_object.matrix_world = Matrix(
        (
            (basis_x[0], basis_y[0], basis_z[0], translation[0]),
            (basis_x[1], basis_y[1], basis_z[1], translation[1]),
            (basis_x[2], basis_y[2], basis_z[2], translation[2]),
            (0.0, 0.0, 0.0, 1.0),
        )
    )


def _load_import_resources(resolved_bundle: ResolvedImportBundle) -> _LoadedImportResources:
    positions = read_vb0_positions(resolved_bundle.vb0_buf_path)
    packed_uv_entries = read_half2x4_records(resolved_bundle.t5_buf_path)

    if resolved_bundle.import_variant == "pre_cs":
        blend_indices, blend_weights_u8 = read_weight_pairs(
            resolved_bundle.pre_cs_weight_buf_path,
            vertex_count=len(positions),
        )
        frame_a, frame_b = read_pre_cs_frame_pairs(
            resolved_bundle.pre_cs_frame_buf_path,
            vertex_count=len(positions),
        )
        return _LoadedImportResources(
            positions=positions,
            packed_uv_entries=packed_uv_entries,
            blend_indices=blend_indices,
            blend_weights_u8=blend_weights_u8,
            pre_frame_a=frame_a,
            pre_frame_b=frame_b,
        )

    if resolved_bundle.t7_buf_path:
        frame_a, frame_b = read_post_cs_frame_pairs(
            resolved_bundle.t7_buf_path,
            vertex_count=len(positions),
        )
        return _LoadedImportResources(
            positions=positions,
            packed_uv_entries=packed_uv_entries,
            post_frame_a=frame_a,
            post_frame_b=frame_b,
        )

    return _LoadedImportResources(
        positions=positions,
        packed_uv_entries=packed_uv_entries,
    )


def _load_exported_package_layout(export_root: Path, source_ib_hash: str) -> list[_ExportedPartResources]:
    ini_path = export_root / f"{source_ib_hash}.ini"
    if not ini_path.is_file():
        raise ValueError(f"Missing exported package INI: {ini_path}")

    parts_by_name: dict[str, _ExportedPartResources] = {}
    current_section: tuple[str, str] | None = None
    current_part_name: str | None = None
    current_stage_part_name: str | None = None
    pending_mesh_name: str | None = None
    seen_draws: set[tuple[str, int, int]] = set()

    for raw_line in ini_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            current_section = None
            pending_mesh_name = None
            continue

        part_match = _PART_COMMENT_RE.match(line)
        if part_match:
            current_part_name = part_match.group("part").strip()
            continue

        stage_match = _STAGE_PART_COMMENT_RE.match(line)
        if stage_match:
            current_stage_part_name = stage_match.group("part").strip()
            pending_mesh_name = None
            continue

        mesh_match = _MESH_COMMENT_RE.match(line)
        if mesh_match:
            pending_mesh_name = mesh_match.group("name").strip()
            continue

        section_match = _RESOURCE_SECTION_RE.match(line)
        if section_match:
            token = section_match.group("token").strip()
            kind = section_match.group("kind").strip()
            current_section = (token, kind)
            part_name = current_part_name or token
            parts_by_name.setdefault(part_name, _ExportedPartResources(part_name=part_name, resource_token=token))
            continue

        if current_section is not None and line.lower().startswith("filename ="):
            token, kind = current_section
            part_name = current_part_name or token
            part = parts_by_name.setdefault(part_name, _ExportedPartResources(part_name=part_name, resource_token=token))
            filename = line.split("=", 1)[1].strip()
            file_path = (export_root / filename).resolve()
            if kind == "IB":
                part.ib_path = file_path
            elif kind == "Position":
                part.position_path = file_path
            elif kind == "Blend":
                part.blend_path = file_path
            elif kind == "Normal":
                part.normal_path = file_path
            elif kind == "Texcoord":
                part.texcoord_path = file_path
            continue

        draw_match = _DRAWINDEXED_RE.match(line)
        if draw_match and current_stage_part_name and pending_mesh_name:
            index_count = int(draw_match.group("count"))
            first_index = int(draw_match.group("first"))
            dedupe_key = (current_stage_part_name, first_index, index_count)
            if dedupe_key in seen_draws:
                continue
            seen_draws.add(dedupe_key)
            part = parts_by_name.setdefault(
                current_stage_part_name,
                _ExportedPartResources(part_name=current_stage_part_name, resource_token=current_stage_part_name),
            )
            part.draws.append(
                _ExportedDrawRecord(
                    part_name=current_stage_part_name,
                    object_name=pending_mesh_name,
                    first_index=first_index,
                    index_count=index_count,
                )
            )
            continue

    ordered_parts = sorted(parts_by_name.values(), key=lambda item: item.part_name)
    if not ordered_parts:
        raise ValueError(f"{ini_path}: no exported parts were found.")
    return ordered_parts


def _build_triangle_slice(indices: list[int], *, first_index: int, index_count: int) -> list[tuple[int, int, int]]:
    if first_index < 0 or index_count <= 0:
        raise ValueError(f"Invalid drawindexed range: first={first_index} count={index_count}")
    end_index = first_index + index_count
    if end_index > len(indices):
        raise ValueError(
            f"Drawindexed range {first_index}+{index_count} exceeds IB size {len(indices)}."
        )
    if index_count % 3 != 0:
        raise ValueError(f"Drawindexed count must be a multiple of 3, got {index_count}.")
    draw_indices = indices[first_index:end_index]
    return [
        (draw_indices[offset], draw_indices[offset + 1], draw_indices[offset + 2])
        for offset in range(0, len(draw_indices), 3)
    ]


def _import_exported_draw(
    context: bpy.types.Context,
    *,
    target_collection: bpy.types.Collection,
    object_name: str,
    source_ib_hash: str,
    part_name: str,
    draw_record: _ExportedDrawRecord,
    positions: list[tuple[float, float, float]],
    packed_uv_entries: list[PackedHalf2x4],
    blend_indices: list[tuple[int, int, int, int]],
    blend_weights_u8: list[tuple[int, int, int, int]],
    frame_a: list[tuple[float, float, float, float]],
    frame_b: list[tuple[float, float, float, float]],
    triangles: list[tuple[int, int, int]],
    profile_id: str,
    flip_uv_v: bool,
    shade_smooth: bool,
    store_orig_vertex_id: bool,
) -> tuple[bpy.types.Object, dict[str, int]]:
    converter = get_game_data_converter(profile_id)
    geometry = build_compacted_geometry(positions, triangles, packed_uv_entries)
    blender_positions = [converter.to_blender_position(position) for position in geometry.positions]
    compact_blend_indices = [blend_indices[vertex_id] for vertex_id in geometry.original_vertex_ids]
    compact_blend_weights = [
        tuple(component / 255.0 for component in blend_weights_u8[vertex_id])
        for vertex_id in geometry.original_vertex_ids
    ]
    compact_frame_a = [frame_a[vertex_id] for vertex_id in geometry.original_vertex_ids]
    compact_frame_b = [frame_b[vertex_id] for vertex_id in geometry.original_vertex_ids]
    decoded_frames = converter.decode_pre_cs_frames(compact_frame_a, compact_frame_b)
    decoded_tangents = [frame.tangent for frame in decoded_frames]
    decoded_normals = [frame.normal for frame in decoded_frames]
    decoded_bitangent_signs = [frame.bitangent_sign for frame in decoded_frames]

    mesh = bpy.data.meshes.new(object_name)
    imported_object = bpy.data.objects.new(object_name, mesh)
    target_collection.objects.link(imported_object)

    mesh.from_pydata(blender_positions, [], geometry.triangles)
    mesh.validate(verbose=False, clean_customdata=False)
    mesh.update()

    if shade_smooth:
        for polygon in mesh.polygons:
            polygon.use_smooth = True

    _apply_uv_layer(mesh, geometry.packed_uv_entries, flip_uv_v=flip_uv_v)
    _store_packed_uv_attributes(mesh, geometry.packed_uv_entries)
    _apply_custom_normals(mesh, decoded_normals)
    _store_decoded_tangent_frame_attributes(mesh, decoded_tangents, decoded_normals, decoded_bitangent_signs)

    for slot_index in range(4):
        _store_int_attribute(
            mesh,
            f"blend_index_{slot_index}",
            [record[slot_index] for record in compact_blend_indices],
        )
        _store_float_attribute(
            mesh,
            f"blend_weight_{slot_index}",
            [record[slot_index] for record in compact_blend_weights],
        )
    _assign_palette_groups(imported_object, compact_blend_indices, compact_blend_weights)

    if store_orig_vertex_id:
        _store_original_vertex_ids(mesh, geometry.original_vertex_ids)

    imported_object["modimp_profile_id"] = profile_id
    imported_object["modimp_source_ib_hash"] = source_ib_hash.lower()
    imported_object["modimp_region_hash"] = _roundtrip_region_hash(part_name, source_ib_hash)
    imported_object["modimp_roundtrip_part_name"] = part_name
    imported_object["modimp_roundtrip_first_index"] = int(draw_record.first_index)
    imported_object["modimp_roundtrip_index_count"] = int(draw_record.index_count)
    imported_object["modimp_roundtrip_import"] = True

    return imported_object, {
        "vertex_count": len(mesh.vertices),
        "triangle_count": len(geometry.triangles),
    }


def import_exported_package(
    context: bpy.types.Context,
    *,
    export_dir: str,
    source_ib_hash: str,
    collection_name: str,
    flip_uv_v: bool,
    shade_smooth: bool,
    store_orig_vertex_id: bool,
) -> tuple[list[bpy.types.Object], dict[str, int]]:
    export_root = Path(export_dir).resolve()
    if not export_root.is_dir():
        raise ValueError(f"Export Dir does not exist: {export_root}")

    parts = _load_exported_package_layout(export_root, source_ib_hash)
    root_collection = _ensure_collection(context.scene, collection_name)
    imported_objects: list[bpy.types.Object] = []
    total_vertex_count = 0
    total_triangle_count = 0

    for part in parts:
        missing_paths = [
            label
            for label, path in (
                ("IB", part.ib_path),
                ("Position", part.position_path),
                ("Blend", part.blend_path),
                ("Normal", part.normal_path),
                ("Texcoord", part.texcoord_path),
            )
            if path is None or not path.is_file()
        ]
        if missing_paths:
            raise ValueError(f"{part.part_name}: missing exported buffer(s): {', '.join(missing_paths)}")

        indices = read_u16_buffer(str(part.ib_path))
        positions = read_vb0_positions(str(part.position_path))
        packed_uv_entries = read_half2x4_records(str(part.texcoord_path))
        blend_indices, blend_weights_u8 = read_weight_pairs(str(part.blend_path), vertex_count=len(positions))
        frame_a, frame_b = read_pre_cs_frame_pairs(str(part.normal_path), vertex_count=len(positions))

        draw_records = list(part.draws)
        if not draw_records:
            draw_records = [
                _ExportedDrawRecord(
                    part_name=part.part_name,
                    object_name=part.part_name,
                    first_index=0,
                    index_count=len(indices),
                )
            ]

        part_collection = _ensure_child_collection(root_collection, part.part_name)
        for draw_record in draw_records:
            triangles = _build_triangle_slice(
                indices,
                first_index=draw_record.first_index,
                index_count=draw_record.index_count,
            )
            imported_object, import_stats = _import_exported_draw(
                context,
                target_collection=part_collection,
                object_name=draw_record.object_name,
                source_ib_hash=source_ib_hash,
                part_name=part.part_name,
                draw_record=draw_record,
                positions=positions,
                packed_uv_entries=packed_uv_entries,
                blend_indices=blend_indices,
                blend_weights_u8=blend_weights_u8,
                frame_a=frame_a,
                frame_b=frame_b,
                triangles=triangles,
                profile_id=YIHUAN_PROFILE.profile_id,
                flip_uv_v=flip_uv_v,
                shade_smooth=shade_smooth,
                store_orig_vertex_id=store_orig_vertex_id,
            )
            imported_objects.append(imported_object)
            total_vertex_count += import_stats["vertex_count"]
            total_triangle_count += import_stats["triangle_count"]

    if not imported_objects:
        raise ValueError("No exported package draws were imported.")

    _select_imported_objects(context, imported_objects, active_object=imported_objects[0])
    return imported_objects, {
        "vertex_count": total_vertex_count,
        "triangle_count": total_triangle_count,
        "slice_count": len(imported_objects),
    }


def _import_single_slice(
    context: bpy.types.Context,
    *,
    resolved_bundle: ResolvedImportBundle,
    loaded_resources: _LoadedImportResources | None,
    object_name: str,
    collection_name: str,
    flip_uv_v: bool,
    shade_smooth: bool,
    store_orig_vertex_id: bool,
    create_section_vertex_group: bool,
    apply_section_transform: bool,
    activate_object: bool,
) -> tuple[bpy.types.Object, dict[str, int]]:
    index_slice = read_index_slice_txt(resolved_bundle.selected_slice.ib_txt_path)
    converter = get_game_data_converter(resolved_bundle.profile_id)
    resource_cache = loaded_resources or _load_import_resources(resolved_bundle)
    positions = resource_cache.positions
    packed_uv_entries = resource_cache.packed_uv_entries
    geometry = build_compacted_geometry(positions, index_slice.triangles, packed_uv_entries)
    blender_positions = [converter.to_blender_position(position) for position in geometry.positions]

    compact_blend_indices = None
    compact_blend_weights = None
    decoded_tangents = None
    decoded_normals = None
    decoded_bitangent_signs = None
    compact_normals = None

    if resolved_bundle.import_variant == "pre_cs":
        if resource_cache.blend_indices is None or resource_cache.blend_weights_u8 is None:
            raise ValueError("Pre-CS import expected cached weight data but none was loaded.")
        blend_indices = resource_cache.blend_indices
        blend_weights_u8 = resource_cache.blend_weights_u8
        compact_blend_indices = [blend_indices[vertex_id] for vertex_id in geometry.original_vertex_ids]
        compact_blend_weights = [
            tuple(component / 255.0 for component in blend_weights_u8[vertex_id])
            for vertex_id in geometry.original_vertex_ids
        ]

        if resource_cache.pre_frame_a is None or resource_cache.pre_frame_b is None:
            raise ValueError("Pre-CS import expected cached frame data but none was loaded.")
        frame_a = resource_cache.pre_frame_a
        frame_b = resource_cache.pre_frame_b
        compact_frame_a = [frame_a[vertex_id] for vertex_id in geometry.original_vertex_ids]
        compact_frame_b = [frame_b[vertex_id] for vertex_id in geometry.original_vertex_ids]
        decoded_frames = converter.decode_pre_cs_frames(compact_frame_a, compact_frame_b)
        decoded_tangents = [frame.tangent for frame in decoded_frames]
        decoded_normals = [frame.normal for frame in decoded_frames]
        decoded_bitangent_signs = [frame.bitangent_sign for frame in decoded_frames]
        compact_normals = decoded_normals
    elif resolved_bundle.t7_buf_path:
        if resource_cache.post_frame_a is None or resource_cache.post_frame_b is None:
            raise ValueError("Post-CS import expected cached frame data but none was loaded.")
        t7_frame_a = resource_cache.post_frame_a
        t7_frame_b = resource_cache.post_frame_b
        compact_t7_frame_a = [t7_frame_a[vertex_id] for vertex_id in geometry.original_vertex_ids]
        compact_t7_frame_b = [t7_frame_b[vertex_id] for vertex_id in geometry.original_vertex_ids]
        decoded_frames = converter.decode_post_cs_frames(compact_t7_frame_a, compact_t7_frame_b)
        decoded_tangents = [frame.tangent for frame in decoded_frames]
        decoded_normals = [frame.normal for frame in decoded_frames]
        decoded_bitangent_signs = [frame.bitangent_sign for frame in decoded_frames]
        compact_normals = decoded_normals

    target_collection = _ensure_collection(context.scene, collection_name)
    mesh = bpy.data.meshes.new(object_name)
    imported_object = bpy.data.objects.new(object_name, mesh)
    target_collection.objects.link(imported_object)

    mesh.from_pydata(blender_positions, [], geometry.triangles)
    mesh.validate(verbose=False, clean_customdata=False)
    mesh.update()

    if shade_smooth:
        for polygon in mesh.polygons:
            polygon.use_smooth = True

    _apply_uv_layer(mesh, geometry.packed_uv_entries, flip_uv_v=flip_uv_v)
    _store_packed_uv_attributes(mesh, geometry.packed_uv_entries)

    if compact_normals is not None:
        _apply_custom_normals(mesh, compact_normals)
    if decoded_tangents is not None and decoded_normals is not None and decoded_bitangent_signs is not None:
        _store_decoded_tangent_frame_attributes(mesh, decoded_tangents, decoded_normals, decoded_bitangent_signs)
    if compact_blend_indices is not None and compact_blend_weights is not None:
        for slot_index in range(4):
            _store_int_attribute(
                mesh,
                f"blend_index_{slot_index}",
                [record[slot_index] for record in compact_blend_indices],
            )
            _store_float_attribute(
                mesh,
                f"blend_weight_{slot_index}",
                [record[slot_index] for record in compact_blend_weights],
            )
        _assign_palette_groups(imported_object, compact_blend_indices, compact_blend_weights)

    if store_orig_vertex_id:
        _store_original_vertex_ids(mesh, geometry.original_vertex_ids)
    if resolved_bundle.selected_slice.section_selector is not None:
        _store_int_attribute(
            mesh,
            "section_selector",
            [int(resolved_bundle.selected_slice.section_selector)] * len(mesh.vertices),
        )
    if create_section_vertex_group and resolved_bundle.selected_slice.section_selector is not None:
        _assign_section_vertex_group(imported_object, resolved_bundle.selected_slice.section_selector)
    if apply_section_transform and resolved_bundle.selected_slice.section_transform is not None:
        _apply_section_transform(
            imported_object,
            resolved_bundle.selected_slice.section_transform,
            profile_id=resolved_bundle.profile_id,
        )

    imported_object["modimp_profile_id"] = resolved_bundle.profile_id
    imported_object["modimp_ib_hash"] = resolved_bundle.ib_hash
    imported_object["modimp_source_ib_hash"] = resolved_bundle.ib_hash
    imported_object["modimp_region_hash"] = (
        resolved_bundle.selected_slice.display_ib_hash or resolved_bundle.selected_slice.raw_ib_hash
    )
    imported_object["modimp_region_index_count"] = int(resolved_bundle.selected_slice.index_count)
    imported_object["modimp_region_first_index"] = int(resolved_bundle.selected_slice.first_index)
    if resolved_bundle.selected_slice.display_ib_hash is not None:
        imported_object["modimp_display_ib_hash"] = resolved_bundle.selected_slice.display_ib_hash
    imported_object["modimp_ib_txt_path"] = resolved_bundle.selected_slice.ib_txt_path
    imported_object["modimp_vb0_buf_path"] = resolved_bundle.vb0_buf_path
    imported_object["modimp_t5_buf_path"] = resolved_bundle.t5_buf_path
    imported_object["modimp_weight_buf_path"] = resolved_bundle.pre_cs_weight_buf_path
    imported_object["modimp_frame_buf_path"] = resolved_bundle.pre_cs_frame_buf_path
    imported_object["modimp_import_variant"] = resolved_bundle.import_variant
    imported_object["modimp_first_index"] = int(resolved_bundle.selected_slice.first_index)
    imported_object["modimp_index_count"] = int(resolved_bundle.selected_slice.index_count)
    imported_object["modimp_slice_order"] = int(resolved_bundle.selected_slice.first_index)
    imported_object["modimp_used_vertex_start"] = int(resolved_bundle.selected_slice.used_vertex_start)
    imported_object["modimp_used_vertex_end"] = int(resolved_bundle.selected_slice.used_vertex_end)
    imported_object["modimp_draw_indices"] = ",".join(str(value) for value in resolved_bundle.selected_slice.draw_indices)
    if resolved_bundle.selected_slice.last_consumer_draw_index is not None:
        imported_object["modimp_last_consumer_draw_index"] = int(resolved_bundle.selected_slice.last_consumer_draw_index)
    if resolved_bundle.selected_slice.depth_vs_hashes:
        imported_object["modimp_depth_vs_hashes"] = ",".join(resolved_bundle.selected_slice.depth_vs_hashes)
    if resolved_bundle.selected_slice.gbuffer_vs_hashes:
        imported_object["modimp_gbuffer_vs_hashes"] = ",".join(resolved_bundle.selected_slice.gbuffer_vs_hashes)
    if resolved_bundle.selected_slice.producer_dispatch_index is not None:
        imported_object["modimp_producer_dispatch_index"] = int(resolved_bundle.selected_slice.producer_dispatch_index)
    if resolved_bundle.selected_slice.producer_cs_hash is not None:
        imported_object["modimp_producer_cs_hash"] = resolved_bundle.selected_slice.producer_cs_hash
    if resolved_bundle.selected_slice.producer_t0_hash is not None:
        imported_object["modimp_producer_t0_hash"] = resolved_bundle.selected_slice.producer_t0_hash
    if resolved_bundle.last_cs_hash is not None:
        imported_object["modimp_last_cs_hash"] = resolved_bundle.last_cs_hash
    if resolved_bundle.last_cs_cb0_hash is not None:
        imported_object["modimp_last_cs_cb0_hash"] = resolved_bundle.last_cs_cb0_hash
    if resolved_bundle.selected_slice.vb1_layout_path is not None:
        imported_object["modimp_vb1_layout_path"] = resolved_bundle.selected_slice.vb1_layout_path
    if resolved_bundle.selected_slice.section_selector is not None:
        imported_object["modimp_section_selector"] = int(resolved_bundle.selected_slice.section_selector)
    if resolved_bundle.selected_slice.section_transform is not None:
        imported_object["modimp_transform_source"] = resolved_bundle.selected_slice.section_transform.source_label
        imported_object["modimp_transform_applied"] = bool(apply_section_transform)
    else:
        imported_object["modimp_transform_applied"] = False
    imported_object["modimp_root_vb0_path"] = resolved_bundle.vb0_origin_trace.closest_rest_pose_path
    imported_object["modimp_root_vb0_note"] = resolved_bundle.vb0_origin_trace.note

    if activate_object:
        _select_imported_objects(context, [imported_object], active_object=imported_object)

    return imported_object, {
        "vertex_count": len(mesh.vertices),
        "triangle_count": len(geometry.triangles),
    }


def import_detected_model(
    context: bpy.types.Context,
    *,
    detected_model: DetectedModelBundle,
    object_prefix: str,
    collection_name: str,
    flip_uv_v: bool,
    shade_smooth: bool,
    store_orig_vertex_id: bool,
    create_section_vertex_group: bool,
    apply_section_transform: bool,
    use_pre_cs_source: bool,
) -> tuple[list[bpy.types.Object], dict[str, int]]:
    """Import every detected slice for the profile model."""
    imported_objects: list[bpy.types.Object] = []
    total_vertex_count = 0
    total_triangle_count = 0
    shared_resources = _load_import_resources(
        ResolvedImportBundle(
            profile_id=detected_model.profile_id,
            frame_dump_dir=detected_model.frame_dump_dir,
            ib_hash=detected_model.ib_hash,
            model_name=detected_model.model_name,
            model_slice_count=len(detected_model.slices),
            selected_slice=detected_model.slices[0],
            import_variant="pre_cs" if use_pre_cs_source else "post_cs",
            vb0_buf_path=detected_model.pre_cs_vb0_buf_path if use_pre_cs_source else detected_model.post_cs_vb0_buf_path,
            pre_cs_vb0_buf_path=detected_model.pre_cs_vb0_buf_path,
            post_cs_vb0_buf_path=detected_model.post_cs_vb0_buf_path,
            t5_buf_path=detected_model.t5_buf_path,
            vb1_buf_path=detected_model.vb1_buf_path,
            t0_buf_path=detected_model.t0_buf_path,
            t1_buf_path=detected_model.t1_buf_path,
            t2_buf_path=detected_model.t2_buf_path,
            t3_buf_path=detected_model.t3_buf_path,
            t7_buf_path=None if use_pre_cs_source else detected_model.t7_buf_path,
            pre_cs_weight_buf_path=detected_model.pre_cs_weight_buf_path,
            pre_cs_frame_buf_path=detected_model.pre_cs_frame_buf_path,
            vb0_origin_trace=detected_model.vb0_origin_trace,
            last_cs_hash=detected_model.slices[0].last_cs_hash,
            last_cs_cb0_hash=detected_model.slices[0].last_cs_cb0_hash,
        )
    )

    for detected_slice in detected_model.slices:
        slice_name = _slice_object_name(
            object_prefix=object_prefix,
            ib_hash=detected_model.ib_hash,
            detected_slice=detected_slice,
        )

        resolved_bundle = ResolvedImportBundle(
            profile_id=detected_model.profile_id,
            frame_dump_dir=detected_model.frame_dump_dir,
            ib_hash=detected_model.ib_hash,
            model_name=detected_model.model_name,
            model_slice_count=len(detected_model.slices),
            selected_slice=detected_slice,
            import_variant="pre_cs" if use_pre_cs_source else "post_cs",
            vb0_buf_path=detected_model.pre_cs_vb0_buf_path if use_pre_cs_source else detected_model.post_cs_vb0_buf_path,
            pre_cs_vb0_buf_path=detected_model.pre_cs_vb0_buf_path,
            post_cs_vb0_buf_path=detected_model.post_cs_vb0_buf_path,
            t5_buf_path=detected_model.t5_buf_path,
            vb1_buf_path=detected_model.vb1_buf_path,
            t0_buf_path=detected_model.t0_buf_path,
            t1_buf_path=detected_model.t1_buf_path,
            t2_buf_path=detected_model.t2_buf_path,
            t3_buf_path=detected_model.t3_buf_path,
            t7_buf_path=None if use_pre_cs_source else detected_model.t7_buf_path,
            pre_cs_weight_buf_path=detected_model.pre_cs_weight_buf_path,
            pre_cs_frame_buf_path=detected_model.pre_cs_frame_buf_path,
            vb0_origin_trace=detected_model.vb0_origin_trace,
            last_cs_hash=detected_slice.last_cs_hash,
            last_cs_cb0_hash=detected_slice.last_cs_cb0_hash,
        )

        imported_object, import_stats = _import_single_slice(
            context,
            resolved_bundle=resolved_bundle,
            loaded_resources=shared_resources,
            object_name=slice_name,
            collection_name=collection_name,
            flip_uv_v=flip_uv_v,
            shade_smooth=shade_smooth,
            store_orig_vertex_id=store_orig_vertex_id,
            create_section_vertex_group=create_section_vertex_group,
            apply_section_transform=apply_section_transform,
            activate_object=False,
        )
        imported_objects.append(imported_object)
        total_vertex_count += import_stats["vertex_count"]
        total_triangle_count += import_stats["triangle_count"]

    if not imported_objects:
        raise ValueError("No slices were imported.")

    _select_imported_objects(context, imported_objects, active_object=imported_objects[0])
    return imported_objects, {
        "vertex_count": total_vertex_count,
        "triangle_count": total_triangle_count,
        "slice_count": len(imported_objects),
    }


def import_resolved_slice(
    context: bpy.types.Context,
    *,
    resolved_bundle: ResolvedImportBundle,
    object_name: str,
    collection_name: str,
    flip_uv_v: bool,
    shade_smooth: bool,
    store_orig_vertex_id: bool,
    create_section_vertex_group: bool,
    apply_section_transform: bool,
) -> tuple[bpy.types.Object, dict[str, int]]:
    """Import one resolved slice bundle."""
    fallback_object_name = _slice_object_name(
        object_prefix="",
        ib_hash=resolved_bundle.ib_hash,
        detected_slice=resolved_bundle.selected_slice,
    )
    return _import_single_slice(
        context,
        resolved_bundle=resolved_bundle,
        loaded_resources=None,
        object_name=object_name or fallback_object_name,
        collection_name=collection_name,
        flip_uv_v=flip_uv_v,
        shade_smooth=shade_smooth,
        store_orig_vertex_id=store_orig_vertex_id,
        create_section_vertex_group=create_section_vertex_group,
        apply_section_transform=apply_section_transform,
        activate_object=True,
    )
