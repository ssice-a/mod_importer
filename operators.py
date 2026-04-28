"""Operators used by the mod importer/exporter."""

from __future__ import annotations

from pathlib import Path
import json
import re
import struct

import bpy

from .core.discovery import analyze_yihuan_frame_stages, discover_yihuan_model, resolve_yihuan_bundle_from_ib_hash
from .core.exporter import export_collection_package
from .core.importer import import_detected_model, import_exported_package
from .core.profiles import YIHUAN_PROFILE, get_profile


_LEGACY_IMPORT_COLLECTION_NAME = "Mod Importer Imports"
_LEGACY_EXPORT_COLLECTION_NAME = "Mod Importer Export"
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
_LEGACY_ORIGINAL_VERTEX_GROUP_NAMES_PROP = "modimp_original_vertex_group_names"
_PRE_CONVERSION_VERTEX_GROUP_NAMES_PROP = "modimp_pre_conversion_vertex_group_names"
_EXPORT_SPLIT_INDEX_PROP = "modimp_export_split_index"
_EXPORT_SPLIT_PARENT_PROP = "modimp_export_split_parent"
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


def _legacy_model_name(ib_hash: str) -> str:
    return f"{YIHUAN_PROFILE.profile_id}_ib_{ib_hash}"


def _should_replace_collection_name(value: str, ib_hash: str) -> bool:
    normalized = value.strip().lower()
    legacy_model_name = _legacy_model_name(ib_hash).lower()
    legacy_import_name = _LEGACY_IMPORT_COLLECTION_NAME.lower()
    legacy_export_name = _LEGACY_EXPORT_COLLECTION_NAME.lower()
    return normalized in {
        "",
        legacy_import_name,
        legacy_export_name,
        f"{legacy_import_name} export",
        f"{legacy_export_name} export",
        ib_hash.lower(),
        legacy_model_name,
        f"{legacy_model_name} export",
        f"{ib_hash.lower()} export",
    }


def _set_scene_collection_name(scene: bpy.types.Scene, collection_name: str):
    normalized = collection_name.strip()
    scene.modimp_collection_name = normalized
    scene.modimp_import_collection_name = normalized
    scene.modimp_export_collection_name = normalized


def _scene_collection_name(scene: bpy.types.Scene) -> str:
    for value in (
        scene.modimp_collection_name,
        scene.modimp_export_collection_name,
        scene.modimp_import_collection_name,
    ):
        normalized = value.strip()
        if normalized:
            _set_scene_collection_name(scene, normalized)
            return normalized
    return ""


def _scene_collection_candidates(scene: bpy.types.Scene) -> tuple[str, ...]:
    candidates: list[str] = []
    for value in (
        scene.modimp_collection_name,
        scene.modimp_export_collection_name,
        scene.modimp_import_collection_name,
        scene.modimp_resolved_ib_hash,
    ):
        normalized = value.strip().lower()
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return tuple(candidates)


def _apply_ib_collection_defaults(scene: bpy.types.Scene, ib_hash: str, export_hash: str | None = None):
    del export_hash
    preferred_collection = ib_hash.strip()
    if _should_replace_collection_name(_scene_collection_name(scene), ib_hash):
        _set_scene_collection_name(scene, preferred_collection)


def _clear_legacy_object_prefix(scene: bpy.types.Scene, ib_hash: str):
    normalized_prefix = scene.modimp_object_prefix.strip().lower()
    if normalized_prefix in {_legacy_model_name(ib_hash).lower(), ib_hash.lower()}:
        scene.modimp_object_prefix = ""


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
    _clear_legacy_object_prefix(scene, detected_model.ib_hash)


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
    _clear_legacy_object_prefix(scene, resolved_bundle.ib_hash)


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


def _record_original_vertex_group_names(obj: bpy.types.Object):
    if _PRE_CONVERSION_VERTEX_GROUP_NAMES_PROP in obj:
        return
    obj[_PRE_CONVERSION_VERTEX_GROUP_NAMES_PROP] = json.dumps(
        [vertex_group.name for vertex_group in obj.vertex_groups],
        ensure_ascii=True,
    )


def _read_u32_palette(path: Path) -> list[int]:
    data = path.read_bytes()
    if not data or len(data) % 4 != 0:
        raise ValueError(f"Palette file must be a non-empty uint32 buffer: {path}")
    return [value[0] for value in struct.iter_unpack("<I", data)]


def _find_export_collection_for_object(obj: bpy.types.Object) -> bpy.types.Collection | None:
    for collection in obj.users_collection:
        if _optional_str_prop(collection, _COLLECTION_KIND_PROP) in {"part", _IB_SUBPART_KIND}:
            return collection
    return None


def _palette_path_for_object(scene: bpy.types.Scene, obj: bpy.types.Object) -> Path:
    collection = _find_export_collection_for_object(obj)
    if collection is None:
        raise ValueError(f"{obj.name}: object is not linked directly to an export part or IB sub-collection.")
    bmc_ib_hash = _optional_str_prop(collection, _BMC_IB_HASH_PROP)
    bmc_match_index_count = _optional_int_prop(collection, _BMC_MATCH_INDEX_COUNT_PROP)
    bmc_chunk_index = _optional_int_prop(collection, _BMC_CHUNK_INDEX_PROP)
    if not bmc_ib_hash or bmc_match_index_count is None or bmc_chunk_index is None:
        raise ValueError(f"{obj.name}: export collection '{collection.name}' has no BMC palette identity.")
    return (
        Path(scene.modimp_export_dir.strip())
        / "Buffer"
        / f"{bmc_ib_hash.lower()}-{int(bmc_match_index_count)}-{int(bmc_chunk_index)}-Palette.buf"
    )


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
        except Exception as exc:  # pylint: disable=broad-except
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Imported {import_stats['slice_count']} slices, "
                f"{import_stats['vertex_count']} compacted verts, "
                f"{import_stats['triangle_count']} tris into "
                f"{scene.modimp_collection_name.strip()}."
            ),
        )
        return {"FINISHED"}


class MODIMP_OT_create_export_collection(bpy.types.Operator):
    """Create the single source-IB collection tree and seed part00 from selected meshes."""

    bl_idname = "modimp.create_export_collection"
    bl_label = "Create Collection"
    bl_description = "Create the source-IB working collection and region/part00 children for selected meshes"
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
                or _LEGACY_IMPORT_COLLECTION_NAME
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
                *_scene_collection_candidates(scene),
            ):
                if _HASH8_RE.fullmatch(candidate):
                    source_ib_hash = candidate
                    break
        if not _HASH8_RE.fullmatch(source_ib_hash):
            self.report({"ERROR"}, "Collection must be a source IB hash, for example 83527398.")
            return {"CANCELLED"}

        export_collection = _ensure_scene_collection_linked(scene, source_ib_hash)
        _set_scene_collection_name(scene, export_collection.name)
        _mark_source_collection(export_collection, source_ib_hash)
        frame_runtime_lookup = _build_frame_runtime_lookup(scene, source_ib_hash)

        grouped_objects: dict[tuple[str, int | None, int | None], list[bpy.types.Object]] = {}
        for obj in selected_mesh_objects:
            region_hash, index_count, first_index = _object_region_identity(obj)
            if not region_hash:
                self.report({"ERROR"}, f"{obj.name}: cannot resolve local/region hash for export part.")
                return {"CANCELLED"}
            grouped_objects.setdefault((region_hash, index_count, first_index), []).append(obj)

        created_parts: list[str] = []
        linked_count = 0
        unlinked_count = 0
        for (region_hash, index_count, first_index), objects in sorted(grouped_objects.items()):
            region_collection_name = _region_collection_name(region_hash, index_count, first_index)
            region_collection = _ensure_child_collection(export_collection, region_collection_name)
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
            part_collection = _ensure_part_collection(
                region_collection,
                source_ib_hash=source_ib_hash,
                region_hash=region_hash,
                part_index=0,
                region_index_count=index_count,
            )
            created_parts.append(f"{region_hash}/{part_collection.name}")
            for obj in objects:
                already_in_target = obj.name in part_collection.objects.keys()
                unlinked_count += _move_object_within_export_tree(
                    obj,
                    export_root=export_collection,
                    target_collection=part_collection,
                )
                if not already_in_target:
                    linked_count += 1

        if not selected_mesh_objects:
            self.report(
                {"INFO"},
                (
                    f"Collection '{export_collection.name}' is ready. "
                    "Select mesh objects and click Create Collection again to seed region/part00."
                ),
            )
            return {"FINISHED"}

        self.report(
            {"INFO"},
            (
                f"Collection '{export_collection.name}' is ready. "
                f"Seeded {', '.join(created_parts)}; linked {linked_count} mesh objects, "
                f"unlinked {unlinked_count} stale export-tree links."
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
            *_scene_collection_candidates(scene),
            scene.modimp_resolved_ib_hash.strip().lower(),
            selected_source_ib_hash,
        ):
            if _HASH8_RE.fullmatch(candidate):
                source_ib_hash = candidate
                break
        if not _HASH8_RE.fullmatch(source_ib_hash):
            self.report({"ERROR"}, "Cannot resolve source IB hash for Collection.")
            return {"CANCELLED"}

        export_collection = _ensure_scene_collection_linked(scene, source_ib_hash)
        _set_scene_collection_name(scene, export_collection.name)
        _mark_source_collection(export_collection, source_ib_hash)
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
            fallback_region_hash = scene.modimp_resolved_display_ib_hash.strip().lower()
            if not _HASH8_RE.fullmatch(fallback_region_hash):
                self.report({"ERROR"}, "Select mesh objects or resolve a local/region hash before creating an empty part.")
                return {"CANCELLED"}
            index_count = int(scene.modimp_resolved_index_count) if scene.modimp_resolved_index_count else None
            first_index = int(scene.modimp_resolved_first_index) if scene.modimp_resolved_first_index else None
            grouped_objects[(fallback_region_hash, index_count, first_index)] = []

        created_parts: list[str] = []
        linked_count = 0
        unlinked_count = 0
        for (region_hash, index_count, first_index), objects in sorted(grouped_objects.items()):
            region_collection_name = _region_collection_name(region_hash, index_count, first_index)
            region_collection = _ensure_child_collection(export_collection, region_collection_name)
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
                    export_root=export_collection,
                    target_collection=part_collection,
                )
                if not already_in_target:
                    linked_count += 1

        self.report(
            {"INFO"},
            (
                f"Created export parts: {', '.join(created_parts)}. "
                f"Linked {linked_count} mesh objects, unlinked {unlinked_count} stale export-tree links."
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
        scene.modimp_frame_analysis_summary = (
            f"IB {raw_ib_hash or 'mixed'}: {draw_count} draws, {dispatch_count} dispatches"
        )
        text_name = "modimp_frame_analysis_report.json"
        text = bpy.data.texts.get(text_name) or bpy.data.texts.new(text_name)
        text.clear()
        text.write(json.dumps(summary, indent=2, ensure_ascii=False))
        self.report({"INFO"}, f"Analyzed FrameAnalysis: {scene.modimp_frame_analysis_summary}. See {text_name}.")
        return {"FINISHED"}


class MODIMP_OT_rename_vertex_groups_from_palette(bpy.types.Operator):
    """Convert selected export objects' numeric local vertex groups to global bone ids."""

    bl_idname = "modimp.rename_vertex_groups_from_palette"
    bl_label = "Convert Export Groups From Palette"
    bl_description = (
        "For selected export objects, rename numeric local vertex groups to global bone ids using their export palette"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        selected_mesh_objects = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not selected_mesh_objects:
            self.report({"ERROR"}, "Select mesh objects first.")
            return {"CANCELLED"}

        renamed_count = 0
        missing_palettes: list[str] = []
        try:
            for obj in selected_mesh_objects:
                palette_path = _palette_path_for_object(scene, obj)
                if not palette_path.is_file():
                    missing_palettes.append(f"{obj.name}: {palette_path.name}")
                    continue
                palette = _read_u32_palette(palette_path)
                _record_original_vertex_group_names(obj)
                for vertex_group in obj.vertex_groups:
                    if not vertex_group.name.isdigit():
                        continue
                    local_index = int(vertex_group.name)
                    if local_index >= len(palette):
                        raise ValueError(
                            f"{obj.name}: vertex group {local_index} exceeds palette size {len(palette)} "
                            f"from {palette_path.name}."
                        )
                    global_name = str(int(palette[local_index]))
                    if vertex_group.name != global_name:
                        vertex_group.name = global_name
                        renamed_count += 1
        except Exception as exc:  # pylint: disable=broad-except
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        if missing_palettes:
            self.report({"WARNING"}, f"Skipped missing palettes: {', '.join(missing_palettes[:4])}")
        self.report({"INFO"}, f"Renamed {renamed_count} vertex groups on {len(selected_mesh_objects)} object(s).")
        return {"FINISHED"}


class MODIMP_OT_restore_vertex_group_names(bpy.types.Operator):
    """Restore selected export objects' vertex group names saved before palette conversion."""

    bl_idname = "modimp.restore_vertex_group_names"
    bl_label = "Restore Pre-Conversion Group Names"
    bl_description = "Restore selected export objects' vertex group names saved before Convert Export Groups From Palette"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        selected_mesh_objects = [obj for obj in context.selected_objects if obj.type == "MESH"]
        if not selected_mesh_objects:
            self.report({"ERROR"}, "Select mesh objects first.")
            return {"CANCELLED"}

        restored = 0
        skipped: list[str] = []
        for obj in selected_mesh_objects:
            raw_names = obj.get(_PRE_CONVERSION_VERTEX_GROUP_NAMES_PROP) or obj.get(
                _LEGACY_ORIGINAL_VERTEX_GROUP_NAMES_PROP
            )
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
            for prop_name in (
                _PRE_CONVERSION_VERTEX_GROUP_NAMES_PROP,
                _LEGACY_ORIGINAL_VERTEX_GROUP_NAMES_PROP,
            ):
                if prop_name in obj:
                    del obj[prop_name]

        if skipped:
            self.report({"WARNING"}, f"No pre-conversion group-name history on: {', '.join(skipped[:4])}")
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
        source_ib_hash = _scene_collection_name(scene).strip().lower()
        if not _HASH8_RE.fullmatch(source_ib_hash):
            self.report({"ERROR"}, "Collection must be the source IB hash before splitting.")
            return {"CANCELLED"}
        export_collection = bpy.data.collections.get(source_ib_hash)
        if export_collection is None:
            self.report({"ERROR"}, f"Export collection does not exist: {source_ib_hash}")
            return {"CANCELLED"}

        changed_parts = 0
        created_children = 0
        moved_objects = 0
        try:
            for region_collection in sorted(export_collection.children, key=lambda item: item.name):
                region_hash, region_index_count, _first_index = _collection_region_identity(region_collection)
                if not region_hash:
                    continue
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

    source_ib_hash = ""
    for candidate in (
        *_scene_collection_candidates(scene),
        scene.modimp_resolved_ib_hash.strip().lower(),
    ):
        if _HASH8_RE.fullmatch(candidate):
            source_ib_hash = candidate
            break
    if not source_ib_hash:
        raise ValueError("Fill Collection with the source IB hash, for example 83527398.")

    export_collection = bpy.data.collections.get(source_ib_hash)
    if export_collection is None:
        raise ValueError(f"Export collection does not exist: {source_ib_hash}")

    _set_scene_collection_name(scene, export_collection.name)
    _mark_source_collection(export_collection, source_ib_hash)

    region_infos: list[tuple[bpy.types.Collection, str, int | None, int | None, list[bpy.types.Object]]] = []
    for region_collection in sorted(export_collection.children, key=lambda item: item.name):
        region_hash, index_count, first_index = _collection_region_identity(region_collection)
        if not region_hash:
            continue
        region_infos.append(
            (
                region_collection,
                region_hash,
                index_count,
                first_index,
                _mesh_objects_in_collection_tree(region_collection),
            )
        )

    if not region_infos:
        raise ValueError(f"{source_ib_hash}: no region collections found under export root.")

    def apply_metadata(frame_runtime_lookup: dict[tuple[str, int, int], dict[str, object]]) -> tuple[int, list[str]]:
        part_count = 0
        missing_runtime: list[str] = []
        for region_collection, region_hash, index_count, first_index, mesh_objects in region_infos:
            runtime_contract = _resolve_region_runtime_contract(
                scene,
                frame_runtime_lookup=frame_runtime_lookup,
                region_hash=region_hash,
                index_count=index_count,
                first_index=first_index,
                objects=mesh_objects,
            )
            _mark_region_collection(
                region_collection,
                source_ib_hash=source_ib_hash,
                region_hash=region_hash,
                index_count=index_count,
                first_index=first_index,
                runtime_contract=runtime_contract,
            )

            missing_fields = _missing_region_contract_fields(
                region_collection,
                index_count=index_count,
                first_index=first_index,
            )
            if missing_fields:
                missing_runtime.append(region_collection.name)

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
        return part_count, missing_runtime

    part_count, missing_runtime = apply_metadata({})
    if missing_runtime and scene.modimp_frame_dump_dir.strip():
        part_count, missing_runtime = apply_metadata(_build_frame_runtime_lookup(scene, source_ib_hash))
    return len(region_infos), part_count, missing_runtime


def _resolve_export_package_hash(scene: bpy.types.Scene) -> str:
    for candidate in (
        *_scene_collection_candidates(scene),
        scene.modimp_resolved_ib_hash.strip().lower(),
    ):
        if _HASH8_RE.fullmatch(candidate):
            return candidate

    export_dir = scene.modimp_export_dir.strip()
    if export_dir:
        export_root = Path(export_dir)
        if export_root.is_dir():
            ini_hashes = sorted(
                {
                    ini_path.stem.lower()
                    for ini_path in export_root.glob("*.ini")
                    if ini_path.is_file()
                    and not ini_path.stem.lower().endswith("-bonestore")
                    and _HASH8_RE.fullmatch(ini_path.stem)
                }
            )
            if len(ini_hashes) == 1:
                return ini_hashes[0]

    raise ValueError("Cannot resolve exported package hash. Set Collection to the source IB hash, for example 83527398.")


class MODIMP_OT_export_collection_buffers(bpy.types.Operator):
    """Export the working collection into runtime buffers, INI, and referenced HLSL assets."""

    bl_idname = "modimp.export_collection_buffers"
    bl_label = "Export Package"
    bl_description = "Rebuild runtime buffers, INI, and profile HLSL for the working collection"
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
            region_count, part_count, missing_runtime = _sync_export_collection_metadata(context)
            export_stats = export_collection_package(
                collection_name=_scene_collection_name(scene),
                export_dir=scene.modimp_export_dir.strip(),
                frame_dump_dir=scene.modimp_frame_dump_dir.strip() or None,
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
                f"({scene.modimp_export_mode})."
            ),
        )
        if missing_runtime:
            self.report(
                {"WARNING"},
                f"Runtime metadata was incomplete before export sync on: {', '.join(missing_runtime[:4])}.",
            )
        return {"FINISHED"}


class MODIMP_OT_import_exported_package(bpy.types.Operator):
    """Import the generated export package back into Blender for round-trip verification."""

    bl_idname = "modimp.import_exported_package"
    bl_label = "Import Exported Package"
    bl_description = "Import the generated buffers back into Blender using the exported INI draw layout"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        _ensure_supported_profile(scene)

        if not scene.modimp_export_dir.strip():
            self.report({"ERROR"}, "Fill Export Dir first.")
            return {"CANCELLED"}

        try:
            source_ib_hash = _resolve_export_package_hash(scene)
            collection_name = f"{source_ib_hash}_roundtrip"
            imported_objects, import_stats = import_exported_package(
                context,
                export_dir=scene.modimp_export_dir.strip(),
                source_ib_hash=source_ib_hash,
                collection_name=collection_name,
                flip_uv_v=scene.modimp_flip_v,
                shade_smooth=scene.modimp_shade_smooth,
                store_orig_vertex_id=scene.modimp_store_orig_vertex_id,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Imported {import_stats['slice_count']} exported draw objects, "
                f"{import_stats['vertex_count']} verts, {import_stats['triangle_count']} tris into {collection_name}."
            ),
        )
        return {"FINISHED"}
