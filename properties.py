"""Scene properties used by the mod importer/exporter."""

from __future__ import annotations

import bpy

from .core.profiles import PROFILE_ITEMS, YIHUAN_PROFILE


REGISTERED_PROPERTY_PATHS = (
    (bpy.types.Scene, "modimp_profile"),
    (bpy.types.Scene, "modimp_frame_dump_dir"),
    (bpy.types.Scene, "modimp_ib_hash"),
    (bpy.types.Scene, "modimp_object_prefix"),
    (bpy.types.Scene, "modimp_import_collection_name"),
    (bpy.types.Scene, "modimp_use_pre_cs_source"),
    (bpy.types.Scene, "modimp_flip_v"),
    (bpy.types.Scene, "modimp_shade_smooth"),
    (bpy.types.Scene, "modimp_store_orig_vertex_id"),
    (bpy.types.Scene, "modimp_create_section_vertex_groups"),
    (bpy.types.Scene, "modimp_apply_section_transform"),
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
    (bpy.types.Scene, "modimp_last_cs_hash"),
    (bpy.types.Scene, "modimp_last_cs_cb0_hash"),
    (bpy.types.Scene, "modimp_producer_t0_hash"),
    (bpy.types.Scene, "modimp_root_vb0_path"),
    (bpy.types.Scene, "modimp_root_vb0_note"),
    (bpy.types.Scene, "modimp_resolved_first_index"),
    (bpy.types.Scene, "modimp_resolved_index_count"),
    (bpy.types.Scene, "modimp_export_collection_name"),
    (bpy.types.Scene, "modimp_export_dir"),
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
    bpy.types.Scene.modimp_object_prefix = bpy.props.StringProperty(
        name="Object Prefix",
        default="",
        description="Optional imported object name prefix; leave blank to use the resolved model name",
    )
    bpy.types.Scene.modimp_import_collection_name = bpy.props.StringProperty(
        name="Import Collection",
        default="Mod Importer Imports",
        description="Collection that will receive imported objects",
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
    bpy.types.Scene.modimp_shade_smooth = bpy.props.BoolProperty(
        name="Shade Smooth",
        default=True,
        description="Mark imported polygons as smooth shaded",
    )
    bpy.types.Scene.modimp_store_orig_vertex_id = bpy.props.BoolProperty(
        name="Store Original Vertex Id",
        default=True,
        description="Store the original global vertex id as the orig_vertex_id point attribute",
    )
    bpy.types.Scene.modimp_create_section_vertex_groups = bpy.props.BoolProperty(
        name="Create Section Groups",
        default=False,
        description="Create section_xxx vertex groups from the detected rigid section selector",
    )
    bpy.types.Scene.modimp_apply_section_transform = bpy.props.BoolProperty(
        name="Apply Section Transform",
        default=False,
        description="Apply the currently decoded rigid section transform to imported objects",
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
    bpy.types.Scene.modimp_last_cs_hash = bpy.props.StringProperty(
        name="Last CS Hash",
        default="",
        description="Last relevant compute shader hash for the resolved slice",
    )
    bpy.types.Scene.modimp_last_cs_cb0_hash = bpy.props.StringProperty(
        name="Last CS CB0 Hash",
        default="",
        description="Last relevant cs-cb0 hash used as runtime judgment key",
    )
    bpy.types.Scene.modimp_producer_t0_hash = bpy.props.StringProperty(
        name="Producer T0 Hash",
        default="",
        description="Producer dispatch local palette hash when currently known",
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

    bpy.types.Scene.modimp_export_collection_name = bpy.props.StringProperty(
        name="Export Collection",
        default="Mod Importer Imports",
        description="Collection to rebuild into shared buffers during export",
    )
    bpy.types.Scene.modimp_export_dir = bpy.props.StringProperty(
        name="Export Dir",
        default=r"E:\vscode\mod_importer\out",
        subtype="DIR_PATH",
        description="Directory that will receive exported buffers, manifests, and HLSL assets",
    )


def unregister_addon_properties():
    """Remove all properties added by the add-on."""
    for owner_type, property_name in reversed(REGISTERED_PROPERTY_PATHS):
        if hasattr(owner_type, property_name):
            delattr(owner_type, property_name)
