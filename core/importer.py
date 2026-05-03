"""Blender mesh creation helpers for the importer/exporter."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import bpy

from .game_data import get_game_data_converter
from .io import (
    build_compacted_geometry,
    read_half2x4_records,
    read_index_slice_txt,
    read_post_cs_frame_pairs,
    read_pre_cs_frame_pairs,
    read_u8x4_records,
    read_vb0_positions,
    read_weight_pairs,
)
from .models import DetectedModelBundle, PackedHalf2x4, ResolvedImportBundle


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


def _ensure_collection(scene: bpy.types.Scene, collection_name: str) -> bpy.types.Collection:
    if not collection_name:
        return scene.collection

    existing_collection = bpy.data.collections.get(collection_name)
    if existing_collection is None:
        existing_collection = bpy.data.collections.new(collection_name)

    if scene.collection.children.get(existing_collection.name) is None:
        scene.collection.children.link(existing_collection)

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


def _apply_uv_layers(mesh: bpy.types.Mesh, packed_uv_entries: list[PackedHalf2x4], *, flip_uv_v: bool):
    created_layers: list[bpy.types.MeshUVLoopLayer] = []
    for uv_index in range(4):
        uv_layer = mesh.uv_layers.new(name=f"UV{uv_index}")
        created_layers.append(uv_layer)
        for polygon in mesh.polygons:
            for loop_index in polygon.loop_indices:
                vertex_index = mesh.loops[loop_index].vertex_index
                u_coord, v_coord = packed_uv_entries[vertex_index][uv_index]
                uv_layer.data[loop_index].uv = (u_coord, 1.0 - v_coord if flip_uv_v else v_coord)

    if created_layers:
        mesh.uv_layers.active = created_layers[0]


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


def _store_outline_param_attributes(mesh: bpy.types.Mesh, values: list[tuple[int, int, int, int]]):
    for channel_index, channel_name in enumerate(("r", "g", "b", "a")):
        _store_int_attribute(
            mesh,
            f"modimp_outline_{channel_name}",
            [int(record[channel_index]) for record in values],
        )

    color_attributes = getattr(mesh, "color_attributes", None)
    if color_attributes is None:
        return
    try:
        attribute = color_attributes.new(name="NTMI_OutlineParam", type="BYTE_COLOR", domain="POINT")
    except TypeError:
        return
    for item, value in zip(attribute.data, values):
        item.color = tuple(max(0, min(255, int(component))) / 255.0 for component in value)


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


def _texture_slots_json(resolved_bundle: ResolvedImportBundle) -> str:
    return json.dumps(
        {
            slot: {
                "hash": binding.hash_value,
                "source_path": binding.source_path,
                "extension": binding.extension,
                "draw_index": binding.draw_index,
                "ps_hash": binding.ps_hash or "",
                "rt_count": binding.rt_count,
            }
            for slot, binding in sorted(resolved_bundle.selected_slice.texture_slots.items())
        },
        ensure_ascii=False,
    )


def _resolve_outline_param_buffer(resolved_bundle: ResolvedImportBundle) -> str | None:
    outline_hash = resolved_bundle.selected_slice.match_vs_outline_hash
    if not outline_hash:
        return None

    frame_dir = Path(resolved_bundle.frame_dump_dir)
    for draw_index in sorted(int(value) for value in resolved_bundle.selected_slice.draw_indices):
        prefix = f"{draw_index:06d}-vs-t"
        for candidate in sorted(frame_dir.glob(f"{prefix}*={outline_hash}*.buf")):
            return str(candidate)
    return None


def _bsdf_input(bsdf_node, *names: str):
    for name in names:
        if name in bsdf_node.inputs:
            return bsdf_node.inputs[name]
    return None


def _add_image_texture_node(nodes, source_path: str, label: str, *, color_space: str | None = None):
    image = bpy.data.images.load(source_path, check_existing=True)
    if color_space and hasattr(image, "colorspace_settings"):
        try:
            image.colorspace_settings.name = color_space
        except TypeError:
            pass
    node = nodes.new(type="ShaderNodeTexImage")
    node.label = label
    node.image = image
    return node


def _apply_material_from_texture_slots(imported_object: bpy.types.Object, resolved_bundle: ResolvedImportBundle):
    texture_slots = resolved_bundle.selected_slice.texture_slots
    if not texture_slots:
        return

    material_name = f"{imported_object.name}_Material"
    material = bpy.data.materials.new(material_name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    if bsdf is None:
        imported_object.data.materials.append(material)
        return

    base_binding = texture_slots.get("ps-t7")
    if base_binding is not None:
        base_node = _add_image_texture_node(nodes, base_binding.source_path, "ps-t7 base color")
        base_input = _bsdf_input(bsdf, "Base Color")
        if base_input is not None:
            links.new(base_node.outputs["Color"], base_input)

    normal_binding = texture_slots.get("ps-t5")
    if normal_binding is not None:
        normal_node = _add_image_texture_node(nodes, normal_binding.source_path, "ps-t5 normal", color_space="Non-Color")
        normal_map = nodes.new(type="ShaderNodeNormalMap")
        normal_input = _bsdf_input(bsdf, "Normal")
        if normal_input is not None:
            links.new(normal_node.outputs["Color"], normal_map.inputs["Color"])
            links.new(normal_map.outputs["Normal"], normal_input)

    material["modimp_texture_slots"] = _texture_slots_json(resolved_bundle)
    imported_object.data.materials.append(material)


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


def _slice_object_name(*, object_prefix: str, ib_hash: str, detected_slice) -> str:
    hash_value = (detected_slice.display_ib_hash or ib_hash or "unknown").lower()
    base_name = f"{hash_value}-{int(detected_slice.index_count)}-{int(detected_slice.first_index)}"
    prefix = object_prefix.strip()
    return f"{prefix}_{base_name}" if prefix else base_name


def _mirror_x_vector(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return (-float(vector[0]), float(vector[1]), float(vector[2]))


def _reverse_triangle_winding(triangle: tuple[int, int, int]) -> tuple[int, int, int]:
    return (triangle[0], triangle[2], triangle[1])


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


def _import_single_slice(
    context: bpy.types.Context,
    *,
    resolved_bundle: ResolvedImportBundle,
    loaded_resources: _LoadedImportResources | None,
    object_name: str,
    collection_name: str,
    flip_uv_v: bool,
    mirror_flip: bool,
    shade_smooth: bool,
    store_orig_vertex_id: bool,
    activate_object: bool,
) -> tuple[bpy.types.Object, dict[str, int]]:
    index_slice = read_index_slice_txt(resolved_bundle.selected_slice.ib_txt_path)
    converter = get_game_data_converter(resolved_bundle.profile_id)
    resource_cache = loaded_resources or _load_import_resources(resolved_bundle)
    positions = resource_cache.positions
    packed_uv_entries = resource_cache.packed_uv_entries
    geometry = build_compacted_geometry(positions, index_slice.triangles, packed_uv_entries)
    blender_positions = [converter.to_blender_position(position) for position in geometry.positions]
    if mirror_flip:
        blender_positions = [_mirror_x_vector(position) for position in blender_positions]

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
        if mirror_flip:
            decoded_tangents = [_mirror_x_vector(tangent) for tangent in decoded_tangents]
            decoded_normals = [_mirror_x_vector(normal) for normal in decoded_normals]
            decoded_bitangent_signs = [-sign for sign in decoded_bitangent_signs]
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
        if mirror_flip:
            decoded_tangents = [_mirror_x_vector(tangent) for tangent in decoded_tangents]
            decoded_normals = [_mirror_x_vector(normal) for normal in decoded_normals]
            decoded_bitangent_signs = [-sign for sign in decoded_bitangent_signs]
        compact_normals = decoded_normals

    compact_outline_params = None
    outline_param_path = _resolve_outline_param_buffer(resolved_bundle)
    if outline_param_path is not None:
        outline_records = read_u8x4_records(outline_param_path)
        max_vertex_id = max(geometry.original_vertex_ids, default=-1)
        if max_vertex_id >= len(outline_records):
            raise ValueError(
                f"Outline parameter buffer is shorter than the imported slice: {outline_param_path}"
            )
        compact_outline_params = [outline_records[vertex_id] for vertex_id in geometry.original_vertex_ids]

    target_collection = _ensure_collection(context.scene, collection_name)
    mesh = bpy.data.meshes.new(object_name)
    imported_object = bpy.data.objects.new(object_name, mesh)
    target_collection.objects.link(imported_object)

    # Mirror Flip is a real X reflection, so it already flips triangle handedness.
    # Without that mirror we must flip winding explicitly for Blender front faces.
    # Custom normals stay in their original direction either way.
    blender_triangles = (
        geometry.triangles
        if mirror_flip
        else [_reverse_triangle_winding(triangle) for triangle in geometry.triangles]
    )
    mesh.from_pydata(blender_positions, [], blender_triangles)
    mesh.validate(verbose=False, clean_customdata=False)
    mesh.update()

    if shade_smooth:
        for polygon in mesh.polygons:
            polygon.use_smooth = True

    _apply_uv_layers(mesh, geometry.packed_uv_entries, flip_uv_v=flip_uv_v)
    _store_packed_uv_attributes(mesh, geometry.packed_uv_entries)
    if compact_outline_params is not None:
        _store_outline_param_attributes(mesh, compact_outline_params)

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
    if resolved_bundle.selected_slice.match_vs_texcoord_hash is not None:
        imported_object["modimp_match_vs_texcoord_hash"] = resolved_bundle.selected_slice.match_vs_texcoord_hash
    if resolved_bundle.selected_slice.match_vs_position_hash is not None:
        imported_object["modimp_match_vs_position_hash"] = resolved_bundle.selected_slice.match_vs_position_hash
    if resolved_bundle.selected_slice.match_vs_outline_hash is not None:
        imported_object["modimp_match_vs_outline_hash"] = resolved_bundle.selected_slice.match_vs_outline_hash
    if resolved_bundle.selected_slice.texture_slots:
        imported_object["modimp_texture_slots"] = _texture_slots_json(resolved_bundle)
    if resolved_bundle.selected_slice.vb1_layout_path is not None:
        imported_object["modimp_vb1_layout_path"] = resolved_bundle.selected_slice.vb1_layout_path
    imported_object["modimp_mirror_flip"] = bool(mirror_flip)
    imported_object["modimp_root_vb0_path"] = resolved_bundle.vb0_origin_trace.closest_rest_pose_path
    imported_object["modimp_root_vb0_note"] = resolved_bundle.vb0_origin_trace.note
    _apply_material_from_texture_slots(imported_object, resolved_bundle)

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
    mirror_flip: bool,
    shade_smooth: bool,
    store_orig_vertex_id: bool,
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
            mirror_flip=mirror_flip,
            shade_smooth=shade_smooth,
            store_orig_vertex_id=store_orig_vertex_id,
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
    mirror_flip: bool,
    shade_smooth: bool,
    store_orig_vertex_id: bool,
) -> tuple[bpy.types.Object, dict[str, int]]:
    """Import one resolved slice bundle."""
    default_object_name = _slice_object_name(
        object_prefix="",
        ib_hash=resolved_bundle.ib_hash,
        detected_slice=resolved_bundle.selected_slice,
    )
    return _import_single_slice(
        context,
        resolved_bundle=resolved_bundle,
        loaded_resources=None,
        object_name=object_name or default_object_name,
        collection_name=collection_name,
        flip_uv_v=flip_uv_v,
        mirror_flip=mirror_flip,
        shade_smooth=shade_smooth,
        store_orig_vertex_id=store_orig_vertex_id,
        activate_object=True,
    )
