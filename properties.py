"""Scene properties used by the mod importer/exporter."""

from __future__ import annotations

import json

import bpy

from .core.profiles import PROFILE_ITEMS, YIHUAN_PROFILE


_DRAW_PASS_MAP_TEXT_PROP = "modimp_draw_pass_map_text"
_TEXTURE_MARKS_TEXT_PROP = "modimp_texture_marks_text"


REGISTERED_PROPERTY_PATHS = (
    (bpy.types.Scene, "modimp_profile"),
    (bpy.types.Scene, "modimp_frame_dump_dir"),
    (bpy.types.Scene, "modimp_ib_hash"),
    (bpy.types.Scene, "modimp_ui_show_import_advanced"),
    (bpy.types.Scene, "modimp_ui_show_import_details"),
    (bpy.types.Scene, "modimp_ui_show_export_advanced"),
    (bpy.types.Scene, "modimp_ui_show_texture_marking"),
    (bpy.types.Scene, "modimp_texture_mark_region"),
    (bpy.types.Scene, "modimp_texture_mark_draw"),
    (bpy.types.Scene, "modimp_texture_mark_items"),
    (bpy.types.Scene, "modimp_texture_mark_index"),
    (bpy.types.Scene, "modimp_object_prefix"),
    (bpy.types.Scene, "modimp_collection_name"),
    (bpy.types.Scene, "modimp_export_collection_name"),
    (bpy.types.Scene, "modimp_use_pre_cs_source"),
    (bpy.types.Scene, "modimp_flip_v"),
    (bpy.types.Scene, "modimp_mirror_flip"),
    (bpy.types.Scene, "modimp_shade_smooth"),
    (bpy.types.Scene, "modimp_store_orig_vertex_id"),
    (bpy.types.Scene, "modimp_detected_model_name"),
    (bpy.types.Scene, "modimp_detected_slice_count"),
    (bpy.types.Scene, "modimp_resolved_ib_hash"),
    (bpy.types.Scene, "modimp_resolved_display_ib_hash"),
    (bpy.types.Scene, "modimp_resolved_import_variant"),
    (bpy.types.Scene, "modimp_pre_cs_vb0_path"),
    (bpy.types.Scene, "modimp_post_cs_vb0_path"),
    (bpy.types.Scene, "modimp_t5_buf_path"),
    (bpy.types.Scene, "modimp_pre_cs_weight_path"),
    (bpy.types.Scene, "modimp_pre_cs_frame_path"),
    (bpy.types.Scene, "modimp_root_vb0_path"),
    (bpy.types.Scene, "modimp_root_vb0_note"),
    (bpy.types.Scene, "modimp_resolved_first_index"),
    (bpy.types.Scene, "modimp_resolved_index_count"),
    (bpy.types.Scene, "modimp_frame_analysis_summary"),
    (bpy.types.Scene, "modimp_export_dir"),
    (bpy.types.Scene, "modimp_export_mode"),
    (bpy.types.Scene, "modimp_export_runtime_shapekeys"),
    (bpy.types.Scene, "modimp_runtime_shapekey_names"),
)


class MODIMP_TextureMarkItem(bpy.types.PropertyGroup):
    """One texture candidate row shown in the manual marking list."""

    slot: bpy.props.StringProperty(name="Slot", default="")
    hash_value: bpy.props.StringProperty(name="Hash", default="")
    source_path: bpy.props.StringProperty(name="Source", default="", subtype="FILE_PATH")
    filename: bpy.props.StringProperty(name="Filename", default="")
    semantic: bpy.props.StringProperty(name="Semantic", default="")
    semantic_index: bpy.props.IntProperty(name="Semantic Index", default=0, min=0)


def _read_text_json(text_name: str) -> object | None:
    text = bpy.data.texts.get(str(text_name or "").strip())
    if text is None:
        return None
    try:
        return json.loads(text.as_string())
    except json.JSONDecodeError:
        return None


def _active_work_collection(scene: bpy.types.Scene) -> bpy.types.Collection | None:
    for name in (scene.modimp_collection_name.strip(), scene.modimp_export_collection_name.strip()):
        if not name:
            continue
        collection = bpy.data.collections.get(name)
        if collection is not None:
            return collection
    return None


def _draw_rows_for_scene(scene: bpy.types.Scene) -> list[dict[str, object]]:
    collection = _active_work_collection(scene)
    if collection is None:
        return []
    draw_text_name = str(collection.get(_DRAW_PASS_MAP_TEXT_PROP, "") or "")
    payload = _read_text_json(draw_text_name)
    if not isinstance(payload, dict):
        return []
    draws = payload.get("draws", [])
    return [dict(item) for item in draws if isinstance(item, dict)]


def _texture_region_key(draw: dict[str, object]) -> str:
    raw_hash = str(draw.get("raw_ib_hash", "") or "").strip().lower()
    index_count = int(draw.get("index_count", 0) or 0)
    first_index = int(draw.get("first_index", 0) or 0)
    if not raw_hash or index_count <= 0:
        return ""
    return f"{raw_hash}_{index_count}_{first_index}"


def _texture_region_label(region_key: str) -> str:
    parts = region_key.split("_")
    if len(parts) != 3:
        return region_key
    return f"{parts[0]} count={parts[1]} first={parts[2]}"


def _texture_mark_region_items(self, context):  # pylint: disable=unused-argument
    seen: set[str] = set()
    items = []
    for draw in _draw_rows_for_scene(context.scene):
        key = _texture_region_key(draw)
        if not key or key in seen:
            continue
        seen.add(key)
        items.append((key, _texture_region_label(key), "FrameAnalysis draw region"))
    if not items:
        return [("__none__", "No analyzed regions", "Run Analyze first")]
    return items


def _draw_score(draw: dict[str, object]) -> tuple[int, int, int, int]:
    ps_hashes = draw.get("ps_resource_hashes", {})
    if not isinstance(ps_hashes, dict):
        ps_hashes = {}
    texture_count = sum(1 for key in ps_hashes if str(key).startswith("ps-t"))
    rt_count = int(draw.get("rt_count", 0) or 0)
    index_count = int(draw.get("index_count", 0) or 0)
    event_index = int(draw.get("event_index", 0) or 0)
    return texture_count, rt_count, index_count, event_index


def _texture_mark_draw_items(self, context):  # pylint: disable=unused-argument
    scene = context.scene
    region_key = str(scene.modimp_texture_mark_region or "")
    rows = [draw for draw in _draw_rows_for_scene(scene) if _texture_region_key(draw) == region_key]
    rows.sort(key=_draw_score, reverse=True)
    items = []
    for draw in rows:
        event_index = int(draw.get("event_index", 0) or 0)
        rt_count = int(draw.get("rt_count", 0) or 0)
        ps_hash = str(draw.get("ps_hash", "") or "")
        ps_hashes = draw.get("ps_resource_hashes", {})
        texture_count = len(ps_hashes) if isinstance(ps_hashes, dict) else 0
        label = f"{event_index:06d}  RT={rt_count}  textures={texture_count}"
        if ps_hash:
            label = f"{label}  ps={ps_hash[:8]}"
        items.append((str(event_index), label, "Draw event from FrameAnalysis"))
    if not items:
        return [("__none__", "No texture draw candidates", "No draw rows for this region")]
    return items


def _draw_texture_candidates(scene: bpy.types.Scene) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    collection = _active_work_collection(scene)
    if collection is None:
        return {}, {}
    payload = _read_text_json(str(collection.get(_TEXTURE_MARKS_TEXT_PROP, "") or ""))
    if not isinstance(payload, dict):
        return {}, {}
    region_key = str(scene.modimp_texture_mark_region or "")
    draw_key = str(scene.modimp_texture_mark_draw or "")
    candidates = payload.get("candidates", {})
    marks = payload.get("marks", {})
    if not isinstance(candidates, dict):
        candidates = {}
    if not isinstance(marks, dict):
        marks = {}
    region_candidates = candidates.get(region_key, {})
    region_marks = marks.get(region_key, {})
    if not isinstance(region_candidates, dict):
        region_candidates = {}
    if not isinstance(region_marks, dict):
        region_marks = {}
    draw_candidates = region_candidates.get(draw_key, {})
    draw_marks = region_marks.get(draw_key, {})
    if not isinstance(draw_candidates, dict):
        draw_candidates = {}
    if not isinstance(draw_marks, dict):
        draw_marks = {}
    return draw_candidates, draw_marks


def _slot_sort_key(slot: str) -> int:
    tail = str(slot).split("-t")[-1]
    return int(tail) if tail.isdigit() else 999


def sync_texture_mark_items(scene: bpy.types.Scene):
    """Refresh the UIList rows from the active analyzed draw and mark cache."""
    if not hasattr(scene, "modimp_texture_mark_items"):
        return
    draw_candidates, draw_marks = _draw_texture_candidates(scene)
    scene.modimp_texture_mark_items.clear()
    for slot, binding in sorted(draw_candidates.items(), key=lambda item: _slot_sort_key(item[0])):
        item = scene.modimp_texture_mark_items.add()
        item.slot = str(slot)
        item.hash_value = str(binding.get("hash", "") or "")
        item.source_path = str(binding.get("source_path", "") or "")
        item.filename = str(item.source_path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1])
        mark = draw_marks.get(slot, {})
        if isinstance(mark, dict):
            item.semantic = str(mark.get("semantic", "") or "")
            item.semantic_index = int(mark.get("semantic_index", 0) or 0)
    if scene.modimp_texture_mark_index >= len(scene.modimp_texture_mark_items):
        scene.modimp_texture_mark_index = max(0, len(scene.modimp_texture_mark_items) - 1)


def _update_texture_mark_draw(self, context):  # pylint: disable=unused-argument
    sync_texture_mark_items(context.scene)


def _update_texture_mark_region(self, context):  # pylint: disable=unused-argument
    scene = context.scene
    collection = _active_work_collection(scene)
    if collection is None:
        return
    payload = _read_text_json(str(collection.get(_TEXTURE_MARKS_TEXT_PROP, "") or ""))
    if not isinstance(payload, dict):
        return
    default_draws = payload.get("default_draws", {})
    if not isinstance(default_draws, dict):
        return
    draw_key = str(default_draws.get(str(scene.modimp_texture_mark_region or ""), "") or "")
    if not draw_key:
        return
    try:
        scene.modimp_texture_mark_draw = draw_key
    except TypeError:
        sync_texture_mark_items(scene)


def register_addon_properties():
    """Register the scene properties exposed by the add-on."""
    try:
        bpy.utils.register_class(MODIMP_TextureMarkItem)
    except ValueError:
        pass
    bpy.types.Scene.modimp_profile = bpy.props.EnumProperty(
        name="Profile",
        items=PROFILE_ITEMS,
        default=YIHUAN_PROFILE.profile_id,
        description="Choose which game profile should resolve and export the current model",
    )
    bpy.types.Scene.modimp_frame_dump_dir = bpy.props.StringProperty(
        name="Frame Dump Dir",
        default=YIHUAN_PROFILE.default_frame_dump_dir,
        subtype="DIR_PATH",
        description="FrameAnalysis directory containing log.txt and deduped",
    )
    bpy.types.Scene.modimp_ib_hash = bpy.props.StringProperty(
        name="IB Hash",
        default="",
        description="Input one raw or display IB hash and let the current profile resolve the model from log.txt",
    )
    bpy.types.Scene.modimp_ui_show_import_advanced = bpy.props.BoolProperty(
        name="Show Import Advanced",
        default=False,
        description="Expand extra import settings",
    )
    bpy.types.Scene.modimp_ui_show_import_details = bpy.props.BoolProperty(
        name="Show Import Details",
        default=False,
        description="Expand resolved model and analysis details",
    )
    bpy.types.Scene.modimp_ui_show_export_advanced = bpy.props.BoolProperty(
        name="Show Export Advanced",
        default=False,
        description="Expand extra export settings",
    )
    bpy.types.Scene.modimp_ui_show_texture_marking = bpy.props.BoolProperty(
        name="Show Texture Marking",
        default=False,
        description="Expand manual texture semantic marking tools",
    )
    bpy.types.Scene.modimp_texture_mark_region = bpy.props.EnumProperty(
        name="Texture Region",
        items=_texture_mark_region_items,
        update=_update_texture_mark_region,
        description="Choose which analyzed draw region to mark textures for",
    )
    bpy.types.Scene.modimp_texture_mark_draw = bpy.props.EnumProperty(
        name="Texture Draw",
        items=_texture_mark_draw_items,
        update=_update_texture_mark_draw,
        description="Choose which draw candidate supplies the texture list. Analyze defaults this to the g-buffer-like draw.",
    )
    bpy.types.Scene.modimp_texture_mark_items = bpy.props.CollectionProperty(type=MODIMP_TextureMarkItem)
    bpy.types.Scene.modimp_texture_mark_index = bpy.props.IntProperty(
        name="Texture Mark Index",
        default=0,
        min=0,
        description="Active texture candidate row",
    )
    bpy.types.Scene.modimp_object_prefix = bpy.props.StringProperty(
        name="Object Prefix",
        default="",
        description="Optional imported object name prefix; leave blank for hash-indexcount-firstindex names",
    )
    bpy.types.Scene.modimp_collection_name = bpy.props.StringProperty(
        name="Collection",
        default="",
        description="Working collection created during import; blank uses the resolved IB hash",
    )
    bpy.types.Scene.modimp_export_collection_name = bpy.props.StringProperty(
        name="Export Collection",
        default="",
        description="Collection to export from; blank falls back to the working collection",
    )
    bpy.types.Scene.modimp_use_pre_cs_source = bpy.props.BoolProperty(
        name="Use Pre-CS Source",
        default=True,
        description="Import from pre-CS buffers instead of the post-CS visible-draw buffers",
    )
    bpy.types.Scene.modimp_flip_v = bpy.props.BoolProperty(
        name="Flip UV V",
        default=True,
        description="Flip the active UV V coordinate during import",
    )
    bpy.types.Scene.modimp_mirror_flip = bpy.props.BoolProperty(
        name="Mirror Flip",
        default=True,
        description="Mirror imported positions and tangent frames on Blender X so the imported model matches the in-game left/right orientation",
    )
    bpy.types.Scene.modimp_shade_smooth = bpy.props.BoolProperty(
        name="Shade Smooth",
        default=True,
        description="Mark imported polygons as smooth shaded so imported custom normals are visible and exported as seen",
    )
    bpy.types.Scene.modimp_store_orig_vertex_id = bpy.props.BoolProperty(
        name="Store Original Vertex Id",
        default=True,
        description="Store the original global vertex id as the orig_vertex_id point attribute",
    )
    bpy.types.Scene.modimp_detected_model_name = bpy.props.StringProperty(
        name="Detected Model",
        default="",
        description="Resolved model name for the current IB hash",
    )
    bpy.types.Scene.modimp_detected_slice_count = bpy.props.IntProperty(
        name="Slice Count",
        default=0,
        min=0,
        description="How many slices were detected for the current IB hash",
    )
    bpy.types.Scene.modimp_resolved_ib_hash = bpy.props.StringProperty(
        name="Resolved IB Hash",
        default="",
        description="Raw IB hash resolved by the current profile",
    )
    bpy.types.Scene.modimp_resolved_display_ib_hash = bpy.props.StringProperty(
        name="Resolved Display Hash",
        default="",
        description="Display slice hash that matched the current import target, when applicable",
    )
    bpy.types.Scene.modimp_resolved_import_variant = bpy.props.StringProperty(
        name="Import Variant",
        default="",
        description="Current import mode: pre_cs or post_cs",
    )
    bpy.types.Scene.modimp_pre_cs_vb0_path = bpy.props.StringProperty(
        name="Pre-CS VB0",
        default="",
        subtype="FILE_PATH",
        description="Resolved pre-CS source position buffer",
    )
    bpy.types.Scene.modimp_post_cs_vb0_path = bpy.props.StringProperty(
        name="Post-CS VB0",
        default="",
        subtype="FILE_PATH",
        description="Resolved post-CS visible-draw position buffer",
    )
    bpy.types.Scene.modimp_t5_buf_path = bpy.props.StringProperty(
        name="Packed UV Buffer",
        default="",
        subtype="FILE_PATH",
        description="Resolved packed UV/extra-parameter buffer",
    )
    bpy.types.Scene.modimp_pre_cs_weight_path = bpy.props.StringProperty(
        name="Pre-CS Weights",
        default="",
        subtype="FILE_PATH",
        description="Resolved packed weight index/weight buffer",
    )
    bpy.types.Scene.modimp_pre_cs_frame_path = bpy.props.StringProperty(
        name="Pre-CS Frame Source",
        default="",
        subtype="FILE_PATH",
        description="Resolved packed pre-CS frame/normal source buffer",
    )
    bpy.types.Scene.modimp_root_vb0_path = bpy.props.StringProperty(
        name="Root VB0",
        default="",
        subtype="FILE_PATH",
        description="Closest bind/rest-like pre-CS source buffer currently traced",
    )
    bpy.types.Scene.modimp_root_vb0_note = bpy.props.StringProperty(
        name="Root VB0 Note",
        default="",
        description="Short trace summary for the current VB0 source chain",
    )
    bpy.types.Scene.modimp_resolved_first_index = bpy.props.IntProperty(
        name="Resolved First Index",
        default=0,
        min=0,
        description="First index of the currently resolved slice",
    )
    bpy.types.Scene.modimp_resolved_index_count = bpy.props.IntProperty(
        name="Resolved Index Count",
        default=0,
        min=0,
        description="Index count of the currently resolved slice",
    )
    bpy.types.Scene.modimp_frame_analysis_summary = bpy.props.StringProperty(
        name="Frame Analysis Summary",
        default="",
        description="Short summary generated by the FrameAnalysis resource scanner",
    )

    bpy.types.Scene.modimp_export_dir = bpy.props.StringProperty(
        name="Export Dir",
        default=r"E:\vscode\mod_importer\out",
        subtype="DIR_PATH",
        description="Directory that will receive exported buffers and optional INI files",
    )
    bpy.types.Scene.modimp_export_mode = bpy.props.EnumProperty(
        name="Export Mode",
        items=(
            ("BUFFERS_ONLY", "Buffers Only", "Export only game buffer resources"),
            ("BUFFERS_AND_INI", "Buffers + INI", "Export buffers and an NTMI fast-path INI"),
        ),
        default="BUFFERS_ONLY",
        description="Choose whether export should write only buffers or also generate an INI",
    )
    bpy.types.Scene.modimp_export_runtime_shapekeys = bpy.props.BoolProperty(
        name="Runtime Shapekey",
        default=False,
        description=(
            "Export selected shapekeys as runtime-adjustable data. Static buffers still use the current "
            "visible Blender result; runtime applies weight minus exported initial weight"
        ),
    )
    bpy.types.Scene.modimp_runtime_shapekey_names = bpy.props.StringProperty(
        name="Shapekey Names",
        default="",
        description="Comma-separated shapekey names to export at runtime. Leave blank to export all non-muted shapekeys",
    )


def unregister_addon_properties():
    """Remove all properties added by the add-on."""
    for owner_type, property_name in reversed(REGISTERED_PROPERTY_PATHS):
        if hasattr(owner_type, property_name):
            delattr(owner_type, property_name)
    try:
        bpy.utils.unregister_class(MODIMP_TextureMarkItem)
    except RuntimeError:
        pass
