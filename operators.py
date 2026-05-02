"""Operators used by the mod importer/exporter."""

from __future__ import annotations

from pathlib import Path
import json
import re

import bpy

from .core.discovery import analyze_yihuan_frame_stages, discover_yihuan_model, resolve_yihuan_bundle_from_ib_hash
from .core.exporter import export_collection_package
from .core.importer import import_detected_model
from .core.profiles import YIHUAN_PROFILE, get_profile


_HASH8_RE = re.compile(r"^[0-9A-Fa-f]{8}$")
_OBJECT_HASH_PREFIX_RE = re.compile(r"^(?P<hash>[0-9A-Fa-f]{8})(?:[-_](?P<count>\d+)(?:[-_](?P<first>\d+))?)?")
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
_BONE_MERGE_MAP_TEXT_PROP = "modimp_bone_merge_map_text"
_STAGE_MAP_TEXT_PROP = "modimp_stage_map_text"
_CS_COLLECT_MAP_TEXT_PROP = "modimp_cs_collect_map_text"
_DRAW_PASS_MAP_TEXT_PROP = "modimp_draw_pass_map_text"
_EXPORT_ROOT_COLLECTION_PROP = "modimp_export_root_collection"
_PRE_BONE_MERGE_VERTEX_GROUP_NAMES_PROP = "modimp_pre_bone_merge_vertex_group_names"
_BONE_MERGE_APPLIED_PROP = "modimp_bone_merge_groups_applied"
_EXPORT_SPLIT_INDEX_PROP = "modimp_export_split_index"
_EXPORT_SPLIT_PARENT_PROP = "modimp_export_split_parent"
_EXPORT_ROOT_KIND = "export_root"
_IB_SUBPART_KIND = "ib_part"
_REGION_RUNTIME_PROPS = (
    _PRODUCER_CS_HASH_PROP,
    _PRODUCER_T0_HASH_PROP,
    _LAST_CS_HASH_PROP,
    _LAST_CS_CB0_HASH_PROP,
)


def _ensure_supported_profile(scene: bpy.types.Scene):
    profile = get_profile(scene.modimp_profile)
    if profile.profile_id != YIHUAN_PROFILE.profile_id:
        raise ValueError(f"Profile is registered but not implemented yet: {profile.profile_id}")
    return profile


def _set_scene_collection_name(scene: bpy.types.Scene, collection_name: str):
    scene.modimp_collection_name = collection_name.strip()


def _scene_collection_name(scene: bpy.types.Scene) -> str:
    return scene.modimp_collection_name.strip()


def _apply_ib_collection_defaults(scene: bpy.types.Scene, ib_hash: str, export_hash: str | None = None):
    del export_hash
    if not _scene_collection_name(scene):
        _set_scene_collection_name(scene, ib_hash)


def _optional_int_prop(owner, key: str) -> int | None:
    if key not in owner:
        return None
    try:
        return int(owner[key])
    except (TypeError, ValueError):
        return None


def _optional_str_prop(owner, key: str) -> str:
    return str(owner.get(key, "") or "").strip()


def _set_optional_collection_prop(collection: bpy.types.Collection, key: str, value):
    if value is None:
        return
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return
        collection[key] = normalized
        return
    collection[key] = value


def _slice_runtime_contract(detected_slice) -> dict[str, object]:
    return {
        _PROFILE_ID_PROP: YIHUAN_PROFILE.profile_id,
        _PRODUCER_DISPATCH_INDEX_PROP: detected_slice.producer_dispatch_index,
        _PRODUCER_CS_HASH_PROP: detected_slice.producer_cs_hash,
        _PRODUCER_T0_HASH_PROP: detected_slice.producer_t0_hash,
        _LAST_CS_HASH_PROP: detected_slice.last_cs_hash,
        _LAST_CS_CB0_HASH_PROP: detected_slice.last_cs_cb0_hash,
        _LAST_CONSUMER_DRAW_INDEX_PROP: detected_slice.last_consumer_draw_index,
        _DEPTH_VS_HASHES_PROP: ",".join(detected_slice.depth_vs_hashes),
        _GBUFFER_VS_HASHES_PROP: ",".join(detected_slice.gbuffer_vs_hashes),
    }


def _object_runtime_contract(obj: bpy.types.Object) -> dict[str, object]:
    contract: dict[str, object] = {}
    for key in (
        _PROFILE_ID_PROP,
        _PRODUCER_CS_HASH_PROP,
        _PRODUCER_T0_HASH_PROP,
        _LAST_CS_HASH_PROP,
        _LAST_CS_CB0_HASH_PROP,
        _DEPTH_VS_HASHES_PROP,
        _GBUFFER_VS_HASHES_PROP,
    ):
        value = _optional_str_prop(obj, key)
        if value:
            contract[key] = value
    for key in (_PRODUCER_DISPATCH_INDEX_PROP, _LAST_CONSUMER_DRAW_INDEX_PROP):
        value = _optional_int_prop(obj, key)
        if value is not None:
            contract[key] = value
    return contract


def _scene_runtime_contract(
    scene: bpy.types.Scene,
    *,
    region_hash: str,
    index_count: int | None,
    first_index: int | None,
) -> dict[str, object]:
    resolved_hash = scene.modimp_resolved_display_ib_hash.strip().lower()
    if resolved_hash != region_hash.lower():
        return {}
    if index_count is not None and int(scene.modimp_resolved_index_count) not in {0, int(index_count)}:
        return {}
    if first_index is not None and int(scene.modimp_resolved_first_index) != int(first_index):
        return {}
    return {
        _PROFILE_ID_PROP: YIHUAN_PROFILE.profile_id,
        _PRODUCER_T0_HASH_PROP: scene.modimp_producer_t0_hash.strip().lower(),
        _LAST_CS_HASH_PROP: scene.modimp_last_cs_hash.strip().lower(),
        _LAST_CS_CB0_HASH_PROP: scene.modimp_last_cs_cb0_hash.strip().lower(),
    }


def _build_frame_runtime_lookup(scene: bpy.types.Scene, source_ib_hash: str) -> dict[tuple[str, int, int], dict[str, object]]:
    frame_dump_dir = scene.modimp_frame_dump_dir.strip() or None
    if not frame_dump_dir:
        return {}
    try:
        detected_model = discover_yihuan_model(frame_dump_dir, ib_hash=source_ib_hash)
    except Exception:
        return {}

    lookup: dict[tuple[str, int, int], dict[str, object]] = {}
    for detected_slice in detected_model.slices:
        region_hash = (detected_slice.display_ib_hash or detected_slice.raw_ib_hash or "").strip().lower()
        if not _HASH8_RE.fullmatch(region_hash):
            continue
        lookup[(region_hash, int(detected_slice.index_count), int(detected_slice.first_index))] = _slice_runtime_contract(
            detected_slice
        )
    return lookup


def _resolve_region_runtime_contract(
    scene: bpy.types.Scene,
    *,
    frame_runtime_lookup: dict[tuple[str, int, int], dict[str, object]],
    region_hash: str,
    index_count: int | None,
    first_index: int | None,
    objects: list[bpy.types.Object],
) -> dict[str, object]:
    contract: dict[str, object] = {_PROFILE_ID_PROP: YIHUAN_PROFILE.profile_id}
    for obj in objects:
        contract.update(_object_runtime_contract(obj))
    contract.update(
        _scene_runtime_contract(
            scene,
            region_hash=region_hash,
            index_count=index_count,
            first_index=first_index,
        )
    )
    if index_count is not None and first_index is not None:
        contract.update(frame_runtime_lookup.get((region_hash.lower(), int(index_count), int(first_index)), {}))
    return contract


def _apply_detected_model_to_scene(scene: bpy.types.Scene, detected_model):
    scene.modimp_detected_model_name = detected_model.model_name
    scene.modimp_detected_slice_count = len(detected_model.slices)
    scene.modimp_resolved_ib_hash = detected_model.ib_hash
    scene.modimp_pre_cs_vb0_path = detected_model.pre_cs_vb0_buf_path
    scene.modimp_post_cs_vb0_path = detected_model.post_cs_vb0_buf_path
    scene.modimp_t5_buf_path = detected_model.t5_buf_path
    scene.modimp_pre_cs_weight_path = detected_model.pre_cs_weight_buf_path
    scene.modimp_pre_cs_frame_path = detected_model.pre_cs_frame_buf_path
    scene.modimp_root_vb0_path = detected_model.vb0_origin_trace.closest_rest_pose_path
    scene.modimp_root_vb0_note = detected_model.vb0_origin_trace.note
    _apply_ib_collection_defaults(scene, detected_model.ib_hash)


def _apply_resolved_bundle_to_scene(scene: bpy.types.Scene, resolved_bundle):
    scene.modimp_detected_model_name = resolved_bundle.model_name
    scene.modimp_detected_slice_count = resolved_bundle.model_slice_count
    scene.modimp_resolved_ib_hash = resolved_bundle.ib_hash
    scene.modimp_pre_cs_vb0_path = resolved_bundle.pre_cs_vb0_buf_path
    scene.modimp_post_cs_vb0_path = resolved_bundle.post_cs_vb0_buf_path
    scene.modimp_t5_buf_path = resolved_bundle.t5_buf_path
    scene.modimp_pre_cs_weight_path = resolved_bundle.pre_cs_weight_buf_path
    scene.modimp_pre_cs_frame_path = resolved_bundle.pre_cs_frame_buf_path
    scene.modimp_root_vb0_path = resolved_bundle.vb0_origin_trace.closest_rest_pose_path
    scene.modimp_root_vb0_note = resolved_bundle.vb0_origin_trace.note
    scene.modimp_resolved_display_ib_hash = resolved_bundle.selected_slice.display_ib_hash or ""
    scene.modimp_resolved_import_variant = resolved_bundle.import_variant
    scene.modimp_last_cs_hash = resolved_bundle.last_cs_hash or ""
    scene.modimp_last_cs_cb0_hash = resolved_bundle.last_cs_cb0_hash or ""
    scene.modimp_producer_t0_hash = resolved_bundle.selected_slice.producer_t0_hash or ""
    scene.modimp_resolved_first_index = int(resolved_bundle.selected_slice.first_index)
    scene.modimp_resolved_index_count = int(resolved_bundle.selected_slice.index_count)
    _apply_ib_collection_defaults(
        scene,
        resolved_bundle.ib_hash,
        resolved_bundle.selected_slice.display_ib_hash or resolved_bundle.ib_hash,
    )


def _ensure_scene_collection_linked(
    scene: bpy.types.Scene,
    collection_name: str,
) -> bpy.types.Collection:
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        collection = bpy.data.collections.new(collection_name)
    if collection.name not in scene.collection.children.keys():
        scene.collection.children.link(collection)
    return collection


def _ensure_child_collection(parent: bpy.types.Collection, child_name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.get(child_name)
    if collection is None:
        collection = bpy.data.collections.new(child_name)
    if collection.name not in parent.children.keys():
        parent.children.link(collection)
    return collection


def _mark_source_collection(collection: bpy.types.Collection, source_ib_hash: str):
    collection[_COLLECTION_KIND_PROP] = "source_ib"
    collection[_PROFILE_ID_PROP] = YIHUAN_PROFILE.profile_id
    collection[_SOURCE_IB_HASH_PROP] = source_ib_hash.lower()


def _export_root_name(source_ib_hash: str) -> str:
    return f"{source_ib_hash.lower()}_Export"


def _mark_export_root_collection(collection: bpy.types.Collection, source_ib_hash: str):
    collection[_COLLECTION_KIND_PROP] = _EXPORT_ROOT_KIND
    collection[_PROFILE_ID_PROP] = YIHUAN_PROFILE.profile_id
    collection[_SOURCE_IB_HASH_PROP] = source_ib_hash.lower()


def _ensure_export_root_collection(
    scene: bpy.types.Scene,
    working_collection: bpy.types.Collection,
    source_ib_hash: str,
) -> bpy.types.Collection:
    export_root = bpy.data.collections.get(_export_root_name(source_ib_hash))
    if export_root is None:
        export_root = bpy.data.collections.new(_export_root_name(source_ib_hash))
    if export_root.name not in working_collection.children.keys():
        working_collection.children.link(export_root)
    _mark_export_root_collection(export_root, source_ib_hash)
    working_collection[_EXPORT_ROOT_COLLECTION_PROP] = export_root.name
    _set_scene_collection_name(scene, working_collection.name)
    return export_root


def _find_export_root_collection(working_collection: bpy.types.Collection) -> bpy.types.Collection | None:
    if _optional_str_prop(working_collection, _COLLECTION_KIND_PROP) == _EXPORT_ROOT_KIND:
        return working_collection
    stored_name = _optional_str_prop(working_collection, _EXPORT_ROOT_COLLECTION_PROP)
    if stored_name:
        stored_collection = bpy.data.collections.get(stored_name)
        if stored_collection is not None:
            return stored_collection
    source_hash = _optional_str_prop(working_collection, _SOURCE_IB_HASH_PROP) or working_collection.name
    named_collection = bpy.data.collections.get(_export_root_name(source_hash))
    if named_collection is not None:
        return named_collection
    return None


def _copy_export_text_props(source: bpy.types.Collection, target: bpy.types.Collection):
    for key in (
        _BONE_MERGE_MAP_TEXT_PROP,
        _STAGE_MAP_TEXT_PROP,
        _CS_COLLECT_MAP_TEXT_PROP,
        _DRAW_PASS_MAP_TEXT_PROP,
    ):
        value = _optional_str_prop(source, key)
        if value:
            target[key] = value


def _mark_region_collection(
    collection: bpy.types.Collection,
    *,
    source_ib_hash: str,
    region_hash: str,
    index_count: int | None = None,
    first_index: int | None = None,
    runtime_contract: dict[str, object] | None = None,
):
    collection[_COLLECTION_KIND_PROP] = "region"
    collection[_PROFILE_ID_PROP] = YIHUAN_PROFILE.profile_id
    collection[_SOURCE_IB_HASH_PROP] = source_ib_hash.lower()
    collection[_REGION_HASH_PROP] = region_hash.lower()
    if index_count is not None:
        collection[_REGION_INDEX_COUNT_PROP] = int(index_count)
    if first_index is not None:
        collection[_REGION_FIRST_INDEX_PROP] = int(first_index)
    for key, value in (runtime_contract or {}).items():
        _set_optional_collection_prop(collection, key, value)


def _mark_part_collection(
    collection: bpy.types.Collection,
    *,
    source_ib_hash: str,
    region_hash: str,
    part_index: int,
    region_index_count: int | None = None,
):
    collection[_COLLECTION_KIND_PROP] = "part"
    collection[_PROFILE_ID_PROP] = YIHUAN_PROFILE.profile_id
    collection[_SOURCE_IB_HASH_PROP] = source_ib_hash.lower()
    collection[_REGION_HASH_PROP] = region_hash.lower()
    collection[_PART_INDEX_PROP] = int(part_index)
    if _BMC_IB_HASH_PROP not in collection:
        collection[_BMC_IB_HASH_PROP] = region_hash.lower()
    if region_index_count is not None and _BMC_MATCH_INDEX_COUNT_PROP not in collection:
        collection[_BMC_MATCH_INDEX_COUNT_PROP] = int(region_index_count)
    if _BMC_CHUNK_INDEX_PROP not in collection:
        collection[_BMC_CHUNK_INDEX_PROP] = int(part_index)


def _iter_collection_tree(root: bpy.types.Collection):
    yield root
    for child in root.children:
        yield from _iter_collection_tree(child)


def _object_source_ib_hash(obj: bpy.types.Object) -> str:
    return str(obj.get("modimp_ib_hash", "") or "").strip().lower()


def _object_region_identity(obj: bpy.types.Object) -> tuple[str, int | None, int | None]:
    # Prefer the visible Outliner object name so edited/BMC export copies can be routed by their current name.
    match = _OBJECT_HASH_PREFIX_RE.match(obj.name)
    if match:
        index_count = int(match.group("count")) if match.group("count") is not None else None
        first_index = int(match.group("first")) if match.group("first") is not None else None
        return match.group("hash").lower(), index_count, first_index
    region_hash = str(obj.get("modimp_region_hash", "") or "").strip().lower()
    if _HASH8_RE.fullmatch(region_hash):
        index_count = int(obj["modimp_region_index_count"]) if "modimp_region_index_count" in obj else None
        first_index = int(obj["modimp_region_first_index"]) if "modimp_region_first_index" in obj else None
        return region_hash, index_count, first_index
    display_hash = str(obj.get("modimp_display_ib_hash", "") or "").strip().lower()
    if _HASH8_RE.fullmatch(display_hash):
        index_count = int(obj["modimp_index_count"]) if "modimp_index_count" in obj else None
        first_index = int(obj["modimp_first_index"]) if "modimp_first_index" in obj else None
        return display_hash, index_count, first_index
    return "", None, None


def _region_collection_name(region_hash: str, index_count: int | None, first_index: int | None) -> str:
    if index_count is not None and first_index is not None:
        return f"{region_hash}-{int(index_count)}-{int(first_index)}"
    if index_count is not None:
        return f"{region_hash}-{int(index_count)}"
    return region_hash


def _ensure_export_region_collection_for_object(
    export_root: bpy.types.Collection,
    obj: bpy.types.Object,
    *,
    source_ib_hash: str,
    link_object: bool,
) -> bpy.types.Collection:
    region_hash, index_count, first_index = _object_region_identity(obj)
    if not region_hash:
        raise ValueError(f"{obj.name}: cannot resolve local/region hash for export submesh collection.")
    region_collection = _ensure_child_collection(export_root, obj.name)
    _mark_region_collection(
        region_collection,
        source_ib_hash=source_ib_hash,
        region_hash=region_hash,
        index_count=index_count,
        first_index=first_index,
        runtime_contract=_object_runtime_contract(obj),
    )
    if link_object and obj.name not in region_collection.objects.keys():
        region_collection.objects.link(obj)
    return region_collection


def _collection_region_identity(collection: bpy.types.Collection) -> tuple[str, int | None, int | None]:
    region_hash = _optional_str_prop(collection, _REGION_HASH_PROP).lower()
    index_count = _optional_int_prop(collection, _REGION_INDEX_COUNT_PROP)
    first_index = _optional_int_prop(collection, _REGION_FIRST_INDEX_PROP)
    if not _HASH8_RE.fullmatch(region_hash):
        match = _OBJECT_HASH_PREFIX_RE.match(collection.name)
        if not match:
            return "", index_count, first_index
        region_hash = match.group("hash").lower()
        if index_count is None and match.group("count") is not None:
            index_count = int(match.group("count"))
        if first_index is None and match.group("first") is not None:
            first_index = int(match.group("first"))
    return region_hash, index_count, first_index


def _part_collection_index(collection: bpy.types.Collection) -> int | None:
    part_index = _optional_int_prop(collection, _PART_INDEX_PROP)
    if part_index is not None:
        return part_index
    match = re.match(r"^part(?P<index>\d+)", collection.name.lower())
    if match:
        return int(match.group("index"))
    return None


def _mesh_objects_in_collection_tree(collection: bpy.types.Collection) -> list[bpy.types.Object]:
    objects: dict[str, bpy.types.Object] = {}
    for item in _iter_collection_tree(collection):
        for obj in item.objects:
            if obj.type == "MESH":
                objects[obj.name] = obj
    return list(objects.values())


def _missing_region_contract_fields(
    collection: bpy.types.Collection,
    *,
    index_count: int | None,
    first_index: int | None,
) -> list[str]:
    missing: list[str] = []
    if index_count is None:
        missing.append(_REGION_INDEX_COUNT_PROP)
    if first_index is None:
        missing.append(_REGION_FIRST_INDEX_PROP)
    for key in _REGION_RUNTIME_PROPS:
        if not _optional_str_prop(collection, key):
            missing.append(key)
    return missing


def _common_source_ib_hash(objects: list[bpy.types.Object]) -> str:
    hashes = {_object_source_ib_hash(obj) for obj in objects if _object_source_ib_hash(obj)}
    if len(hashes) > 1:
        joined = ", ".join(sorted(hashes))
        raise ValueError(f"Selected objects come from multiple source IB hashes: {joined}")
    return next(iter(hashes), "")


def _next_part_collection(
    parent: bpy.types.Collection,
    *,
    source_ib_hash: str,
    region_hash: str,
    region_index_count: int | None = None,
) -> bpy.types.Collection:
    existing_indices: set[int] = set()
    for child in parent.children:
        try:
            existing_indices.add(int(child.get(_PART_INDEX_PROP)))
        except (TypeError, ValueError):
            match = re.match(r"^part(?P<index>\d+)", child.name.lower())
            if match:
                existing_indices.add(int(match.group("index")))

    part_index = 0
    while part_index in existing_indices:
        part_index += 1

    part_collection = bpy.data.collections.new(f"part{part_index:02d}")
    parent.children.link(part_collection)
    _mark_part_collection(
        part_collection,
        source_ib_hash=source_ib_hash,
        region_hash=region_hash,
        part_index=part_index,
        region_index_count=region_index_count,
    )
    return part_collection


def _ensure_part_collection(
    parent: bpy.types.Collection,
    *,
    source_ib_hash: str,
    region_hash: str,
    part_index: int,
    region_index_count: int | None = None,
) -> bpy.types.Collection:
    for child in parent.children:
        try:
            child_part_index = int(child.get(_PART_INDEX_PROP))
        except (TypeError, ValueError):
            match = re.match(r"^part(?P<index>\d+)", child.name.lower())
            child_part_index = int(match.group("index")) if match else -1
        if child_part_index == part_index:
            _mark_part_collection(
                child,
                source_ib_hash=source_ib_hash,
                region_hash=region_hash,
                part_index=part_index,
                region_index_count=region_index_count,
            )
            return child

    part_collection = bpy.data.collections.new(f"part{part_index:02d}")
    parent.children.link(part_collection)
    _mark_part_collection(
        part_collection,
        source_ib_hash=source_ib_hash,
        region_hash=region_hash,
        part_index=part_index,
        region_index_count=region_index_count,
    )
    return part_collection


def _move_object_within_export_tree(
    obj: bpy.types.Object,
    *,
    export_root: bpy.types.Collection,
    target_collection: bpy.types.Collection,
) -> int:
    """Link obj to target and unlink it from other collections inside the export tree."""
    if obj.name not in target_collection.objects.keys():
        target_collection.objects.link(obj)

    unlinked_count = 0
    for collection in _iter_collection_tree(export_root):
        if collection == target_collection:
            continue
        if obj.name in collection.objects.keys():
            collection.objects.unlink(obj)
            unlinked_count += 1
    return unlinked_count


def _used_numeric_vertex_group_ids(obj: bpy.types.Object) -> set[int]:
    group_ids_by_index = {
        vertex_group.index: int(vertex_group.name)
        for vertex_group in obj.vertex_groups
        if vertex_group.name.isdigit()
    }
    used: set[int] = set()
    for vertex in obj.data.vertices:
        for group_ref in vertex.groups:
            group_id = group_ids_by_index.get(group_ref.group)
            if group_id is not None and float(group_ref.weight) > 0.0:
                used.add(group_id)
    return used


def _record_pre_bone_merge_vertex_group_names(obj: bpy.types.Object):
    if _PRE_BONE_MERGE_VERTEX_GROUP_NAMES_PROP in obj:
        return
    obj[_PRE_BONE_MERGE_VERTEX_GROUP_NAMES_PROP] = json.dumps(
        [vertex_group.name for vertex_group in obj.vertex_groups],
        ensure_ascii=False,
    )


def _working_collection(scene: bpy.types.Scene) -> bpy.types.Collection:
    collection_name = _scene_collection_name(scene)
    if not collection_name:
        raise ValueError("Fill Collection first.")
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        raise ValueError(f"Collection does not exist: {collection_name}")
    return collection


def _export_root_for_scene(scene: bpy.types.Scene, *, create: bool = False) -> bpy.types.Collection:
    working_collection = _working_collection(scene)
    source_ib_hash = _optional_str_prop(working_collection, _SOURCE_IB_HASH_PROP) or working_collection.name
    if not _HASH8_RE.fullmatch(source_ib_hash):
        raise ValueError("Collection must be the source IB hash, for example 83527398.")
    export_root = _find_export_root_collection(working_collection)
    if export_root is None and create:
        export_root = _ensure_export_root_collection(scene, working_collection, source_ib_hash)
    if export_root is None:
        raise ValueError(f"Export logic root does not exist. Click Create Collection to create {_export_root_name(source_ib_hash)}.")
    _mark_export_root_collection(export_root, source_ib_hash)
    _copy_export_text_props(working_collection, export_root)
    return export_root


def _find_export_collection_for_object(obj: bpy.types.Object) -> bpy.types.Collection | None:
    for collection in obj.users_collection:
        if _optional_str_prop(collection, _COLLECTION_KIND_PROP) in {"region", "part", _IB_SUBPART_KIND}:
            return collection
    return None


def _write_text_json(text_name: str, payload: object) -> bpy.types.Text:
    text = bpy.data.texts.get(text_name) or bpy.data.texts.new(text_name)
    text.clear()
    text.write(json.dumps(payload, indent=2, ensure_ascii=False))
    return text


def _read_text_json(text_name: str) -> object:
    text = bpy.data.texts.get(text_name)
    if text is None:
        raise ValueError(f"Missing Blender text block: {text_name}")
    return json.loads(text.as_string())


def _bone_count_from_t0_path(t0_buf_path: str) -> int:
    path = Path(str(t0_buf_path or ""))
    if not path.is_file():
        return 0
    byte_size = path.stat().st_size
    if byte_size <= 0 or byte_size % (16 * 3) != 0:
        return 0
    return byte_size // (16 * 3)


def _rename_object_with_suffix(obj: bpy.types.Object, suffix: str):
    if obj.name.endswith(suffix):
        return
    if "modimp_original_object_name" not in obj:
        obj["modimp_original_object_name"] = obj.name
    obj.name = f"{obj.name}{suffix}"


def _link_only_to_child_inside_part(
    obj: bpy.types.Object,
    *,
    part_collection: bpy.types.Collection,
    child_collection: bpy.types.Collection,
):
    if obj.name not in child_collection.objects.keys():
        child_collection.objects.link(obj)
    if obj.name in part_collection.objects.keys():
        part_collection.objects.unlink(obj)
    for sibling in part_collection.children:
        if sibling == child_collection:
            continue
        if obj.name in sibling.objects.keys():
            sibling.objects.unlink(obj)


def _make_ib_subcollection(
    part_collection: bpy.types.Collection,
    *,
    source_ib_hash: str,
    region_hash: str,
    parent_part_index: int,
    region_index_count: int | None,
    split_index: int,
) -> bpy.types.Collection:
    child_name = f"{part_collection.name}_ib{split_index:02d}"
    child = bpy.data.collections.get(child_name)
    if child is None:
        child = bpy.data.collections.new(child_name)
    if child.name not in part_collection.children.keys():
        part_collection.children.link(child)
    child[_COLLECTION_KIND_PROP] = _IB_SUBPART_KIND
    child[_PROFILE_ID_PROP] = YIHUAN_PROFILE.profile_id
    child[_SOURCE_IB_HASH_PROP] = source_ib_hash.lower()
    child[_REGION_HASH_PROP] = region_hash.lower()
    child[_PART_INDEX_PROP] = int((parent_part_index + 1) * 1000 + split_index)
    child[_EXPORT_SPLIT_INDEX_PROP] = int(split_index)
    child[_EXPORT_SPLIT_PARENT_PROP] = part_collection.name
    child[_BMC_IB_HASH_PROP] = region_hash.lower()
    if region_index_count is not None:
        child[_BMC_MATCH_INDEX_COUNT_PROP] = int(region_index_count)
    child[_BMC_CHUNK_INDEX_PROP] = int(split_index)
    return child


def _partition_objects_by_limits(objects: list[bpy.types.Object]) -> list[list[bpy.types.Object]]:
    buckets: list[list[bpy.types.Object]] = []
    current_bucket: list[bpy.types.Object] = []
    current_vertices = 0
    current_bones: set[int] = set()
    for obj in sorted(objects, key=lambda item: item.name):
        vertex_count = len(obj.data.vertices)
        object_bones = _used_numeric_vertex_group_ids(obj)
        if vertex_count > 0xFFFF:
            raise ValueError(
                f"{obj.name}: has {vertex_count} vertices, exceeding one R16 IB. "
                "Split this object manually; this pass only splits by object."
            )
        if len(object_bones) > 0x100:
            raise ValueError(
                f"{obj.name}: uses {len(object_bones)} bones, exceeding one uint8 palette. "
                "Split this object manually; this pass only splits by object."
            )
        merged_bones = current_bones | object_bones
        would_exceed = current_bucket and (
            current_vertices + vertex_count > 0xFFFF or len(merged_bones) > 0x100
        )
        if would_exceed:
            buckets.append(current_bucket)
            current_bucket = []
            current_vertices = 0
            current_bones = set()
            merged_bones = set(object_bones)
        current_bucket.append(obj)
        current_vertices += vertex_count
        current_bones = merged_bones
    if current_bucket:
        buckets.append(current_bucket)
    return buckets


def _auto_split_export_root_by_limits(export_root: bpy.types.Collection) -> tuple[int, int, int]:
    source_ib_hash = _optional_str_prop(export_root, _SOURCE_IB_HASH_PROP)
    changed_parts = 0
    created_children = 0
    moved_objects = 0
    for region_collection in sorted(export_root.children, key=lambda item: item.name):
        region_hash, region_index_count, _first_index = _collection_region_identity(region_collection)
        if not region_hash:
            continue

        direct_region_meshes = [obj for obj in region_collection.objects if obj.type == "MESH"]
        if direct_region_meshes:
            buckets = _partition_objects_by_limits(direct_region_meshes)
            if len(buckets) > 1:
                changed_parts += 1
                part_collection = _ensure_part_collection(
                    region_collection,
                    source_ib_hash=source_ib_hash,
                    region_hash=region_hash,
                    part_index=0,
                    region_index_count=region_index_count,
                )
                for split_index, bucket in enumerate(buckets):
                    child = _make_ib_subcollection(
                        part_collection,
                        source_ib_hash=source_ib_hash,
                        region_hash=region_hash,
                        parent_part_index=0,
                        region_index_count=region_index_count,
                        split_index=split_index,
                    )
                    created_children += 1
                    for obj in bucket:
                        _move_object_within_export_tree(obj, export_root=export_root, target_collection=child)
                        obj[_EXPORT_SPLIT_INDEX_PROP] = int(split_index)
                        _rename_object_with_suffix(obj, f"__ib{split_index:02d}")
                        moved_objects += 1

        for part_collection in sorted(region_collection.children, key=lambda item: item.name):
            parent_part_index = _part_collection_index(part_collection)
            if parent_part_index is None:
                continue
            direct_meshes = [obj for obj in part_collection.objects if obj.type == "MESH"]
            if not direct_meshes:
                continue
            buckets = _partition_objects_by_limits(direct_meshes)
            if len(buckets) <= 1:
                continue
            changed_parts += 1
            for split_index, bucket in enumerate(buckets):
                child = _make_ib_subcollection(
                    part_collection,
                    source_ib_hash=source_ib_hash,
                    region_hash=region_hash,
                    parent_part_index=parent_part_index,
                    region_index_count=region_index_count,
                    split_index=split_index,
                )
                created_children += 1
                for obj in bucket:
                    _link_only_to_child_inside_part(
                        obj,
                        part_collection=part_collection,
                        child_collection=child,
                    )
                    obj[_EXPORT_SPLIT_INDEX_PROP] = int(split_index)
                    _rename_object_with_suffix(obj, f"__ib{split_index:02d}")
                    moved_objects += 1
    return changed_parts, created_children, moved_objects


def _build_bone_merge_map(summary: dict[str, object], detected_model) -> dict[str, object]:
    dispatches_by_event = {
        int(dispatch["event_index"]): dispatch
        for dispatch in summary.get("dispatches", [])
        if isinstance(dispatch, dict)
    }
    global_bone_cursor = 0
    entries: list[dict[str, object]] = []
    dispatch_entries: list[dict[str, object]] = []

    for detected_slice in sorted(
        detected_model.slices,
        key=lambda item: (
            int(item.producer_dispatch_index or 0),
            int(item.first_index),
            int(item.index_count),
        ),
    ):
        dispatch_index = detected_slice.producer_dispatch_index
        if dispatch_index is None:
            continue
        dispatch = dispatches_by_event.get(int(dispatch_index), {})
        bone_count = _bone_count_from_t0_path(str(dispatch.get("t0_buf_path", "")))
        if bone_count <= 0:
            continue

        region_hash = str(detected_slice.display_ib_hash or detected_slice.raw_ib_hash or "").strip().lower()
        if not _HASH8_RE.fullmatch(region_hash):
            region_hash = str(detected_slice.raw_ib_hash or "").strip().lower()
        if not _HASH8_RE.fullmatch(region_hash):
            continue

        producer_start_vertex = dispatch.get("start_vertex")
        if producer_start_vertex is None:
            producer_start_vertex = detected_slice.producer_start_vertex or 0
        producer_vertex_count = dispatch.get("vertex_count")
        if producer_vertex_count is None:
            producer_vertex_count = detected_slice.producer_vertex_count or 0

        dispatch_entry = {
            "source_ib_hash": str(detected_model.ib_hash).lower(),
            "region_hash": region_hash,
            "first_index": int(detected_slice.first_index),
            "index_count": int(detected_slice.index_count),
            "used_vertex_start": int(detected_slice.used_vertex_start),
            "used_vertex_end": int(detected_slice.used_vertex_end),
            "producer_dispatch_index": int(dispatch_index),
            "producer_start_vertex": int(producer_start_vertex or 0),
            "producer_vertex_count": int(producer_vertex_count or 0),
            "producer_cs_hash": str(detected_slice.producer_cs_hash or dispatch.get("cs_hash", "") or "").lower(),
            "producer_t0_hash": str(detected_slice.producer_t0_hash or dispatch.get("t0_hash", "") or "").lower(),
            "last_cs_hash": str(detected_slice.last_cs_hash or "").lower(),
            "last_cs_cb0_hash": str(detected_slice.last_cs_cb0_hash or "").lower(),
            "global_bone_base": int(global_bone_cursor),
            "bone_count": int(bone_count),
        }
        dispatch_entries.append(dispatch_entry)
        for local_bone_index in range(bone_count):
            global_bone_index = global_bone_cursor + local_bone_index
            entries.append(
                {
                    "source_ib_hash": str(detected_model.ib_hash).lower(),
                    "region_hash": region_hash,
                    "first_index": int(detected_slice.first_index),
                    "index_count": int(detected_slice.index_count),
                    "local_bone_index": int(local_bone_index),
                    "global_bone_index": int(global_bone_index),
                    "display_name": f"{region_hash}_bone_{local_bone_index:03d}",
                }
            )
        global_bone_cursor += bone_count

    return {
        "profile_id": YIHUAN_PROFILE.profile_id,
        "source_ib_hash": str(detected_model.ib_hash).lower(),
        "global_bone_count": int(global_bone_cursor),
        "dispatches": dispatch_entries,
        "entries": entries,
    }


def _map_by_region_and_local(bone_merge_map: dict[str, object]) -> dict[tuple[str, int | None, int | None, int], int]:
    lookup: dict[tuple[str, int | None, int | None, int], int] = {}
    for raw_entry in bone_merge_map.get("entries", []):
        if not isinstance(raw_entry, dict):
            continue
        region_hash = str(raw_entry.get("region_hash", "") or "").strip().lower()
        local_bone_index = int(raw_entry["local_bone_index"])
        global_bone_index = int(raw_entry["global_bone_index"])
        first_index = raw_entry.get("first_index")
        index_count = raw_entry.get("index_count")
        lookup[(region_hash, int(first_index), int(index_count), local_bone_index)] = global_bone_index
        lookup[(region_hash, None, None, local_bone_index)] = global_bone_index
    return lookup


def _bone_merge_text_name_for_collection(collection: bpy.types.Collection) -> str:
    existing_name = _optional_str_prop(collection, _BONE_MERGE_MAP_TEXT_PROP)
    if existing_name:
        return existing_name
    return "modimp_bone_merge_map.json"


def _stage_text_names_for_collection(collection: bpy.types.Collection) -> dict[str, str]:
    source_hash = _optional_str_prop(collection, _SOURCE_IB_HASH_PROP) or collection.name
    safe_hash = source_hash.lower() if _HASH8_RE.fullmatch(source_hash.lower()) else "working"
    return {
        _STAGE_MAP_TEXT_PROP: f"modimp_{safe_hash}_stage_map.json",
        _CS_COLLECT_MAP_TEXT_PROP: f"modimp_{safe_hash}_cs_collect_map.json",
        _DRAW_PASS_MAP_TEXT_PROP: f"modimp_{safe_hash}_draw_pass_map.json",
    }


def _write_frame_analysis_maps_to_collection(
    collection: bpy.types.Collection,
    *,
    source_ib_hash: str,
    detected_model,
    summary: dict[str, object],
) -> dict[str, object]:
    bone_merge_map = _build_bone_merge_map(summary, detected_model)
    bone_merge_text = _write_text_json("modimp_bone_merge_map.json", bone_merge_map)
    collection[_BONE_MERGE_MAP_TEXT_PROP] = bone_merge_text.name

    text_names = _stage_text_names_for_collection(collection)
    stage_text = _write_text_json(
        text_names[_STAGE_MAP_TEXT_PROP],
        {
            "profile_id": YIHUAN_PROFILE.profile_id,
            "source_ib_hash": source_ib_hash,
            "stage_map": summary.get("stage_map", {}),
            "stage_candidates": summary.get("stage_candidates", {}),
            "draws": summary.get("draws", []),
        },
    )
    cs_text = _write_text_json(
        text_names[_CS_COLLECT_MAP_TEXT_PROP],
        {
            "profile_id": YIHUAN_PROFILE.profile_id,
            "source_ib_hash": source_ib_hash,
            "dispatches": bone_merge_map.get("dispatches", []),
        },
    )
    draw_text = _write_text_json(
        text_names[_DRAW_PASS_MAP_TEXT_PROP],
        {
            "profile_id": YIHUAN_PROFILE.profile_id,
            "source_ib_hash": source_ib_hash,
            "draws": summary.get("draws", []),
        },
    )
    collection[_STAGE_MAP_TEXT_PROP] = stage_text.name
    collection[_CS_COLLECT_MAP_TEXT_PROP] = cs_text.name
    collection[_DRAW_PASS_MAP_TEXT_PROP] = draw_text.name
    return bone_merge_map


def _object_or_collection_region_identity(obj: bpy.types.Object) -> tuple[str, int | None, int | None]:
    export_collection = _find_export_collection_for_object(obj)
    if export_collection is not None:
        region_hash, index_count, first_index = _collection_region_identity(export_collection)
        if region_hash:
            return region_hash, index_count, first_index
    return _object_region_identity(obj)


def _apply_bone_merge_map_to_objects(
    objects: list[bpy.types.Object],
    bone_merge_map: dict[str, object],
    *,
    skip_already_applied: bool = True,
) -> int:
    lookup = _map_by_region_and_local(bone_merge_map)
    if not lookup:
        raise ValueError("BoneMergeMap has no usable entries.")

    renamed_count = 0
    for obj in objects:
        if skip_already_applied and bool(obj.get(_BONE_MERGE_APPLIED_PROP, False)):
            continue
        region_hash, index_count, first_index = _object_or_collection_region_identity(obj)
        if not region_hash:
            raise ValueError(f"{obj.name}: cannot resolve region identity for BoneMergeMap lookup.")

        _record_pre_bone_merge_vertex_group_names(obj)
        object_renamed = 0
        for vertex_group in obj.vertex_groups:
            if not vertex_group.name.isdigit():
                continue
            local_index = int(vertex_group.name)
            global_index = lookup.get((region_hash, first_index, index_count, local_index))
            if global_index is None:
                global_index = lookup.get((region_hash, None, None, local_index))
            if global_index is None:
                raise ValueError(
                    f"{obj.name}: BoneMergeMap has no entry for "
                    f"{region_hash} first={first_index} count={index_count} local bone {local_index}."
                )
            global_name = str(int(global_index))
            if vertex_group.name != global_name:
                vertex_group.name = global_name
                renamed_count += 1
                object_renamed += 1
        obj[_BONE_MERGE_APPLIED_PROP] = True
        obj["modimp_bone_merge_region_hash"] = region_hash
        if first_index is not None:
            obj["modimp_bone_merge_first_index"] = int(first_index)
        if index_count is not None:
            obj["modimp_bone_merge_index_count"] = int(index_count)
        obj["modimp_bone_merge_renamed_count"] = int(object_renamed)
    return renamed_count


class MODIMP_OT_resolve_from_ib_hash(bpy.types.Operator):
    """Resolve one model profile bundle from an IB hash."""

    bl_idname = "modimp.resolve_from_ib_hash"
    bl_label = "Resolve From IB Hash"
    bl_description = "Resolve the current profile model from one IB hash"
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        _ensure_supported_profile(scene)

        if not scene.modimp_ib_hash.strip():
            self.report({"ERROR"}, "Fill IB Hash first.")
            return {"CANCELLED"}

        try:
            resolved_bundle = resolve_yihuan_bundle_from_ib_hash(
                scene.modimp_ib_hash.strip(),
                frame_dump_dir=scene.modimp_frame_dump_dir.strip() or None,
                use_pre_cs_source=scene.modimp_use_pre_cs_source,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        _apply_resolved_bundle_to_scene(scene, resolved_bundle)
        self.report(
            {"INFO"},
            (
                f"Resolved {resolved_bundle.model_name}: "
                f"slice {resolved_bundle.selected_slice.first_index}/{resolved_bundle.selected_slice.index_count}, "
                f"last CS {scene.modimp_last_cs_hash or 'unknown'}, "
                f"last CB0 {scene.modimp_last_cs_cb0_hash or 'unknown'}."
            ),
        )
        return {"FINISHED"}


class MODIMP_OT_import_resolved_model(bpy.types.Operator):
    """Import the resolved profile model into Blender."""

    bl_idname = "modimp.import_resolved_model"
    bl_label = "Import Resolved Model"
    bl_description = "Import all detected slices for the current IB hash"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        _ensure_supported_profile(scene)

        if not scene.modimp_ib_hash.strip():
            self.report({"ERROR"}, "Fill IB Hash first.")
            return {"CANCELLED"}

        try:
            detected_model = discover_yihuan_model(
                scene.modimp_frame_dump_dir.strip() or None,
                ib_hash=scene.modimp_ib_hash.strip(),
            )
            summary = analyze_yihuan_frame_stages(
                scene.modimp_frame_dump_dir.strip() or None,
                ib_hash=scene.modimp_ib_hash.strip(),
            )
            resolved_bundle = resolve_yihuan_bundle_from_ib_hash(
                scene.modimp_ib_hash.strip(),
                frame_dump_dir=scene.modimp_frame_dump_dir.strip() or None,
                use_pre_cs_source=scene.modimp_use_pre_cs_source,
            )
            _apply_detected_model_to_scene(scene, detected_model)
            _apply_resolved_bundle_to_scene(scene, resolved_bundle)

            object_prefix = scene.modimp_object_prefix.strip()
            collection_name = _scene_collection_name(scene) or detected_model.ib_hash
            _set_scene_collection_name(scene, collection_name)
            imported_objects, import_stats = import_detected_model(
                context,
                detected_model=detected_model,
                object_prefix=object_prefix,
                collection_name=collection_name,
                flip_uv_v=scene.modimp_flip_v,
                shade_smooth=scene.modimp_shade_smooth,
                store_orig_vertex_id=scene.modimp_store_orig_vertex_id,
                create_section_vertex_group=scene.modimp_create_section_vertex_groups,
                apply_section_transform=scene.modimp_apply_section_transform,
                use_pre_cs_source=scene.modimp_use_pre_cs_source,
            )
            working_collection = _ensure_scene_collection_linked(scene, collection_name)
            _mark_source_collection(working_collection, detected_model.ib_hash)
            bone_merge_map = _write_frame_analysis_maps_to_collection(
                working_collection,
                source_ib_hash=detected_model.ib_hash,
                detected_model=detected_model,
                summary=summary,
            )
            renamed_groups = _apply_bone_merge_map_to_objects(imported_objects, bone_merge_map)
            export_root = _ensure_export_root_collection(scene, working_collection, detected_model.ib_hash)
            _copy_export_text_props(working_collection, export_root)
            for imported_object in imported_objects:
                _ensure_export_region_collection_for_object(
                    export_root,
                    imported_object,
                    source_ib_hash=detected_model.ib_hash,
                    link_object=False,
                )
        except Exception as exc:  # pylint: disable=broad-except
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Imported {import_stats['slice_count']} slices, "
                f"{import_stats['vertex_count']} compacted verts, "
                f"{import_stats['triangle_count']} tris into "
                f"{scene.modimp_collection_name.strip()}; converted {renamed_groups} vertex groups to BoneStore ids."
            ),
        )
        return {"FINISHED"}


class MODIMP_OT_create_export_collection(bpy.types.Operator):
    """Create the export logic root and one empty submesh collection per mesh."""

    bl_idname = "modimp.create_export_collection"
    bl_label = "Create Collection"
    bl_description = "Create the working collection plus an empty export logic root with per-submesh children"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        collection_name = _scene_collection_name(scene)
        selected_mesh_objects = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not collection_name:
            try:
                selected_source_ib_hash = _common_source_ib_hash(selected_mesh_objects) if selected_mesh_objects else ""
            except ValueError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            collection_name = (
                scene.modimp_resolved_ib_hash.strip()
                or selected_source_ib_hash
                or scene.modimp_ib_hash.strip()
            )
            _set_scene_collection_name(scene, collection_name)

        source_ib_hash = collection_name.strip().lower()
        if not _HASH8_RE.fullmatch(source_ib_hash):
            try:
                selected_source_ib_hash = _common_source_ib_hash(selected_mesh_objects) if selected_mesh_objects else ""
            except ValueError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            for candidate in (
                selected_source_ib_hash,
                scene.modimp_resolved_ib_hash.strip().lower(),
                scene.modimp_collection_name.strip().lower(),
            ):
                if _HASH8_RE.fullmatch(candidate):
                    source_ib_hash = candidate
                    break
        if not _HASH8_RE.fullmatch(source_ib_hash):
            self.report({"ERROR"}, "Collection must be a source IB hash, for example 83527398.")
            return {"CANCELLED"}

        working_collection = _ensure_scene_collection_linked(scene, source_ib_hash)
        _set_scene_collection_name(scene, working_collection.name)
        _mark_source_collection(working_collection, source_ib_hash)
        export_root = _ensure_export_root_collection(scene, working_collection, source_ib_hash)
        _copy_export_text_props(working_collection, export_root)

        seed_objects = selected_mesh_objects or [obj for obj in working_collection.objects if obj.type == "MESH"]
        created_submeshes: list[str] = []
        for obj in sorted(seed_objects, key=lambda item: item.name):
            submesh_collection = _ensure_export_region_collection_for_object(
                export_root,
                obj,
                source_ib_hash=source_ib_hash,
                link_object=False,
            )
            created_submeshes.append(submesh_collection.name)

        if not seed_objects:
            self.report(
                {"INFO"},
                (
                    f"Collection '{working_collection.name}' is ready. "
                    f"Export logic root '{export_root.name}' is ready; no mesh objects were seeded."
                ),
            )
            return {"FINISHED"}

        self.report(
            {"INFO"},
            (
                f"Collection '{working_collection.name}' is ready. "
                f"Export logic root '{export_root.name}' has {len(created_submeshes)} submesh collection(s). "
                "Objects were not moved or linked into the export tree."
            ),
        )
        return {"FINISHED"}


class MODIMP_OT_create_export_part(bpy.types.Operator):
    """Create R16-safe part collections, grouped by local/region hash."""

    bl_idname = "modimp.create_export_part"
    bl_label = "Create Part"
    bl_description = "Create local-hash part collections under the working collection and move selected meshes into them"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        selected_mesh_objects = [obj for obj in context.selected_objects if obj.type == "MESH"]
        try:
            selected_source_ib_hash = _common_source_ib_hash(selected_mesh_objects) if selected_mesh_objects else ""
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        source_ib_hash = ""
        for candidate in (
            scene.modimp_collection_name.strip().lower(),
            scene.modimp_resolved_ib_hash.strip().lower(),
            selected_source_ib_hash,
        ):
            if _HASH8_RE.fullmatch(candidate):
                source_ib_hash = candidate
                break
        if not _HASH8_RE.fullmatch(source_ib_hash):
            self.report({"ERROR"}, "Cannot resolve source IB hash for Collection.")
            return {"CANCELLED"}

        working_collection = _ensure_scene_collection_linked(scene, source_ib_hash)
        _set_scene_collection_name(scene, working_collection.name)
        _mark_source_collection(working_collection, source_ib_hash)
        export_root = _ensure_export_root_collection(scene, working_collection, source_ib_hash)
        _copy_export_text_props(working_collection, export_root)
        frame_runtime_lookup = _build_frame_runtime_lookup(scene, source_ib_hash)

        grouped_objects: dict[tuple[str, int | None, int | None], list[bpy.types.Object]] = {}
        if selected_mesh_objects:
            for obj in selected_mesh_objects:
                region_hash, index_count, first_index = _object_region_identity(obj)
                if not region_hash:
                    self.report({"ERROR"}, f"{obj.name}: cannot resolve local/region hash for export part.")
                    return {"CANCELLED"}
                grouped_objects.setdefault((region_hash, index_count, first_index), []).append(obj)
        else:
            target_region_hash = scene.modimp_resolved_display_ib_hash.strip().lower()
            if not _HASH8_RE.fullmatch(target_region_hash):
                self.report({"ERROR"}, "Select mesh objects or resolve a local/region hash before creating an empty part.")
                return {"CANCELLED"}
            index_count = int(scene.modimp_resolved_index_count) if scene.modimp_resolved_index_count else None
            first_index = int(scene.modimp_resolved_first_index) if scene.modimp_resolved_first_index else None
            grouped_objects[(target_region_hash, index_count, first_index)] = []

        created_parts: list[str] = []
        linked_count = 0
        unlinked_count = 0
        for (region_hash, index_count, first_index), objects in sorted(grouped_objects.items()):
            if objects:
                region_collection = _ensure_export_region_collection_for_object(
                    export_root,
                    objects[0],
                    source_ib_hash=source_ib_hash,
                    link_object=False,
                )
            else:
                region_collection_name = _region_collection_name(region_hash, index_count, first_index)
                region_collection = _ensure_child_collection(export_root, region_collection_name)
            runtime_contract = _resolve_region_runtime_contract(
                scene,
                frame_runtime_lookup=frame_runtime_lookup,
                region_hash=region_hash,
                index_count=index_count,
                first_index=first_index,
                objects=objects,
            )
            _mark_region_collection(
                region_collection,
                source_ib_hash=source_ib_hash,
                region_hash=region_hash,
                index_count=index_count,
                first_index=first_index,
                runtime_contract=runtime_contract,
            )
            part_collection = _next_part_collection(
                region_collection,
                source_ib_hash=source_ib_hash,
                region_hash=region_hash,
                region_index_count=index_count,
            )
            created_parts.append(f"{region_hash}/{part_collection.name}")
            for obj in objects:
                already_in_target = obj.name in part_collection.objects.keys()
                unlinked_count += _move_object_within_export_tree(
                    obj,
                    export_root=export_root,
                    target_collection=part_collection,
                )
                if not already_in_target:
                    linked_count += 1

        self.report(
            {"INFO"},
            (
                f"Created export parts: {', '.join(created_parts)}. "
                f"Linked {linked_count} mesh objects, unlinked {unlinked_count} stale collection-tree links."
            ),
        )
        return {"FINISHED"}


class MODIMP_OT_analyze_frame_stages(bpy.types.Operator):
    """Scan FrameAnalysis and summarize draw/dispatch stage candidates."""

    bl_idname = "modimp.analyze_frame_stages"
    bl_label = "Analyze Frame Stages"
    bl_description = "Scan FrameAnalysis log/dumps and create a stage candidate report"
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        _ensure_supported_profile(scene)
        if not scene.modimp_frame_dump_dir.strip():
            self.report({"ERROR"}, "Fill Frame Dump Dir first.")
            return {"CANCELLED"}
        try:
            detected_model = discover_yihuan_model(
                scene.modimp_frame_dump_dir.strip(),
                ib_hash=scene.modimp_ib_hash.strip() or scene.modimp_resolved_ib_hash.strip() or None,
            )
            summary = analyze_yihuan_frame_stages(
                scene.modimp_frame_dump_dir.strip(),
                ib_hash=scene.modimp_ib_hash.strip() or scene.modimp_resolved_ib_hash.strip() or None,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        draw_count = int(summary["draw_count"])
        dispatch_count = int(summary["dispatch_count"])
        raw_ib_hash = str(summary.get("raw_ib_hash", ""))
        source_ib_hash = str(raw_ib_hash or detected_model.ib_hash or scene.modimp_ib_hash).strip().lower()
        collection = _ensure_scene_collection_linked(scene, source_ib_hash)
        _set_scene_collection_name(scene, collection.name)
        _mark_source_collection(collection, source_ib_hash)
        export_root = _ensure_export_root_collection(scene, collection, source_ib_hash)

        bone_merge_map = _write_frame_analysis_maps_to_collection(
            collection,
            source_ib_hash=source_ib_hash,
            detected_model=detected_model,
            summary=summary,
        )
        _copy_export_text_props(collection, export_root)

        scene.modimp_frame_analysis_summary = (
            f"IB {source_ib_hash or 'mixed'}: {draw_count} draws, {dispatch_count} dispatches, "
            f"{len(bone_merge_map.get('entries', []))} bone map rows"
        )
        report_text = _write_text_json("modimp_frame_analysis_report.json", summary)
        self.report(
            {"INFO"},
            f"Analyzed FrameAnalysis: {scene.modimp_frame_analysis_summary}. See {report_text.name}.",
        )
        return {"FINISHED"}


class MODIMP_OT_apply_bone_merge_map_to_groups(bpy.types.Operator):
    """Convert selected objects' numeric local vertex groups through the BoneMergeMap."""

    bl_idname = "modimp.apply_bone_merge_map_to_groups"
    bl_label = "Apply BoneMergeMap To Groups"
    bl_description = "Rename selected objects' numeric local vertex groups to global BoneStore ids using the current collection BoneMergeMap"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        selected_mesh_objects = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not selected_mesh_objects:
            self.report({"ERROR"}, "Select mesh objects first.")
            return {"CANCELLED"}

        try:
            collection = _working_collection(scene)
            bone_merge_map = _read_text_json(_bone_merge_text_name_for_collection(collection))
            if not isinstance(bone_merge_map, dict):
                raise ValueError("BoneMergeMap text block must contain a JSON object.")
            renamed_count = _apply_bone_merge_map_to_objects(selected_mesh_objects, bone_merge_map)
        except Exception as exc:  # pylint: disable=broad-except
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        self.report({"INFO"}, f"Applied BoneMergeMap to {renamed_count} vertex groups on {len(selected_mesh_objects)} object(s).")
        return {"FINISHED"}


class MODIMP_OT_restore_vertex_group_names(bpy.types.Operator):
    """Restore selected export objects' vertex group names saved before BoneMergeMap conversion."""

    bl_idname = "modimp.restore_vertex_group_names"
    bl_label = "Restore Pre-BoneMerge Group Names"
    bl_description = "Restore selected export objects' vertex group names saved before Apply BoneMergeMap To Groups"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        selected_mesh_objects = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not selected_mesh_objects:
            self.report({"ERROR"}, "Select mesh objects first.")
            return {"CANCELLED"}

        restored = 0
        skipped: list[str] = []
        for obj in selected_mesh_objects:
            raw_names = obj.get(_PRE_BONE_MERGE_VERTEX_GROUP_NAMES_PROP)
            if not raw_names:
                skipped.append(obj.name)
                continue
            try:
                names = json.loads(str(raw_names))
            except json.JSONDecodeError:
                skipped.append(obj.name)
                continue
            for vertex_group, original_name in zip(obj.vertex_groups, names):
                if vertex_group.name != str(original_name):
                    vertex_group.name = str(original_name)
                    restored += 1
            del obj[_PRE_BONE_MERGE_VERTEX_GROUP_NAMES_PROP]
            if _BONE_MERGE_APPLIED_PROP in obj:
                del obj[_BONE_MERGE_APPLIED_PROP]

        if skipped:
            self.report({"WARNING"}, f"No pre-BoneMerge group-name history on: {', '.join(skipped[:4])}")
        self.report({"INFO"}, f"Restored {restored} vertex group names.")
        return {"FINISHED"}


class MODIMP_OT_split_export_parts(bpy.types.Operator):
    """Create visible IB sub-collections when a part exceeds vertex or palette limits."""

    bl_idname = "modimp.split_export_parts"
    bl_label = "Split Export Parts"
    bl_description = "Split part collections into IB sub-collections by object, respecting R16 vertex and uint8 palette limits"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        try:
            export_root = _export_root_for_scene(scene)
        except Exception as exc:  # pylint: disable=broad-except
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        try:
            changed_parts, created_children, moved_objects = _auto_split_export_root_by_limits(export_root)
        except Exception as exc:  # pylint: disable=broad-except
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Split {changed_parts} part collection(s), created/touched {created_children} IB sub-collections, "
                f"moved {moved_objects} object(s)."
            ),
        )
        return {"FINISHED"}


def _sync_export_collection_metadata(context) -> tuple[int, int, list[str]]:
    scene = context.scene
    working_collection = _working_collection(scene)
    source_ib_hash = _optional_str_prop(working_collection, _SOURCE_IB_HASH_PROP) or working_collection.name.lower()
    if not _HASH8_RE.fullmatch(source_ib_hash):
        raise ValueError("Fill Collection with the source IB hash, for example 83527398.")
    _mark_source_collection(working_collection, source_ib_hash)
    export_collection = _export_root_for_scene(scene, create=True)

    region_infos: list[tuple[bpy.types.Collection, str, int | None, int | None]] = []
    for region_collection in sorted(export_collection.children, key=lambda item: item.name):
        region_hash, index_count, first_index = _collection_region_identity(region_collection)
        if not region_hash:
            continue
        region_infos.append((region_collection, region_hash, index_count, first_index))

    if not region_infos:
        raise ValueError(f"{source_ib_hash}: no region collections found under Collection.")

    part_count = 0
    missing_runtime: list[str] = []
    for region_collection, region_hash, index_count, first_index in region_infos:
        # 导出同步只维护集合树身份；runtime/hash 合同必须已经写在集合属性上。
        _mark_region_collection(
            region_collection,
            source_ib_hash=source_ib_hash,
            region_hash=region_hash,
            index_count=index_count,
            first_index=first_index,
        )

        missing_fields = _missing_region_contract_fields(
            region_collection,
            index_count=index_count,
            first_index=first_index,
        )
        if missing_fields:
            missing_runtime.append(region_collection.name)

        if any(obj.type == "MESH" for obj in region_collection.objects):
            part_count += 1
        for part_collection in sorted(region_collection.children, key=lambda item: item.name):
            part_index = _part_collection_index(part_collection)
            if part_index is None:
                continue
            _mark_part_collection(
                part_collection,
                source_ib_hash=source_ib_hash,
                region_hash=region_hash,
                part_index=part_index,
                region_index_count=index_count,
            )
            part_count += 1
    return len(region_infos), part_count, missing_runtime


class MODIMP_OT_export_collection_buffers(bpy.types.Operator):
    """Export the working collection into game buffers and optional INI/HLSL assets."""

    bl_idname = "modimp.export_collection_buffers"
    bl_label = "Export Buffers"
    bl_description = "Export game buffers and optionally generate INI/HLSL for the working collection"
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        _ensure_supported_profile(scene)

        if not _scene_collection_name(scene):
            self.report({"ERROR"}, "Fill Collection first.")
            return {"CANCELLED"}
        if not scene.modimp_export_dir.strip():
            self.report({"ERROR"}, "Fill Export Dir first.")
            return {"CANCELLED"}

        try:
            export_root = _export_root_for_scene(scene)
            split_stats = _auto_split_export_root_by_limits(export_root)
            region_count, part_count, missing_runtime = _sync_export_collection_metadata(context)
            export_stats = export_collection_package(
                collection_name=export_root.name,
                export_dir=scene.modimp_export_dir.strip(),
                flip_uv_v=scene.modimp_flip_v,
                generate_ini=scene.modimp_export_mode == "BUFFERS_AND_INI",
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Synced {region_count} regions / {part_count} parts. "
                f"Exported {export_stats['region_count']} regions, "
                f"{export_stats['part_count']} parts / {export_stats['draw_count']} draws, "
                f"{export_stats['vertex_count']} verts, "
                f"{export_stats['triangle_count']} tris to {export_stats['buffer_dir']} "
                f"({scene.modimp_export_mode}); auto-split {split_stats[0]} collection(s)."
            ),
        )
        if missing_runtime:
            self.report(
                {"WARNING"},
                f"Collection export contract is incomplete on: {', '.join(missing_runtime[:4])}.",
            )
        return {"FINISHED"}
