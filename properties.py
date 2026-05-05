"""Scene properties used by the mod importer/exporter."""

from __future__ import annotations

import bpy

from .core.profiles import PROFILE_ITEMS, YIHUAN_PROFILE


REGISTERED_PROPERTY_PATHS = (
    (bpy.types.Scene, "modimp_profile"),
    (bpy.types.Scene, "modimp_frame_dump_dir"),
    (bpy.types.Scene, "modimp_ib_hash"),
    (bpy.types.Scene, "modimp_ui_show_import_advanced"),
    (bpy.types.Scene, "modimp_ui_show_import_details"),
    (bpy.types.Scene, "modimp_ui_show_export_advanced"),
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


def register_addon_properties():
    """Register the scene properties exposed by the add-on."""
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
