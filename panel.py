"""Sidebar panel for the mod importer/exporter."""

from __future__ import annotations

import bpy


class VIEW3D_PT_mod_importer(bpy.types.Panel):
    """Show the current profile workflow in the 3D View sidebar."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Mod Importer"
    bl_label = "Mod Importer"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        profile_box = layout.box()
        profile_box.label(text="Profile", icon="TOOL_SETTINGS")
        profile_box.prop(scene, "modimp_profile")
        profile_box.prop(scene, "modimp_frame_dump_dir")
        profile_box.prop(scene, "modimp_ib_hash")
        profile_box.operator("modimp.resolve_from_ib_hash", icon="FILE_REFRESH")

        resolved_box = layout.box()
        resolved_box.label(text="Resolved", icon="INFO")
        if scene.modimp_detected_model_name:
            resolved_box.label(text=f"Model: {scene.modimp_detected_model_name}")
            resolved_box.label(text=f"Slices: {scene.modimp_detected_slice_count}")
        if scene.modimp_resolved_ib_hash:
            resolved_box.label(text=f"IB: {scene.modimp_resolved_ib_hash}")
        if scene.modimp_resolved_display_ib_hash:
            resolved_box.label(text=f"Display: {scene.modimp_resolved_display_ib_hash}")
        if scene.modimp_resolved_index_count:
            resolved_box.label(
                text=f"Slice: first={scene.modimp_resolved_first_index} count={scene.modimp_resolved_index_count}"
            )
        if scene.modimp_last_cs_hash:
            resolved_box.label(text=f"Last CS Hash: {scene.modimp_last_cs_hash}")
        if scene.modimp_last_cs_cb0_hash:
            resolved_box.label(text=f"Last CS CB0 Hash: {scene.modimp_last_cs_cb0_hash}")
        if scene.modimp_producer_t0_hash:
            resolved_box.label(text=f"Producer T0 Hash: {scene.modimp_producer_t0_hash}")

        buffers_box = layout.box()
        buffers_box.label(text="Resolved Buffers", icon="FILE_FOLDER")
        buffers_box.prop(scene, "modimp_pre_cs_vb0_path")
        buffers_box.prop(scene, "modimp_post_cs_vb0_path")
        buffers_box.prop(scene, "modimp_t5_buf_path")
        buffers_box.prop(scene, "modimp_pre_cs_weight_path")
        buffers_box.prop(scene, "modimp_pre_cs_frame_path")
        buffers_box.prop(scene, "modimp_root_vb0_path")
        if scene.modimp_root_vb0_note:
            buffers_box.label(text=scene.modimp_root_vb0_note)

        import_box = layout.box()
        import_box.label(text="Import", icon="IMPORT")
        import_box.prop(scene, "modimp_object_prefix")
        import_box.prop(scene, "modimp_import_collection_name")
        import_box.prop(scene, "modimp_use_pre_cs_source")
        import_box.prop(scene, "modimp_flip_v")
        import_box.prop(scene, "modimp_shade_smooth")
        import_box.prop(scene, "modimp_store_orig_vertex_id")
        import_box.prop(scene, "modimp_create_section_vertex_groups")
        import_box.prop(scene, "modimp_apply_section_transform")
        import_box.operator("modimp.import_resolved_model", icon="MESH_DATA")

        export_box = layout.box()
        export_box.label(text="Export", icon="EXPORT")
        export_box.prop(scene, "modimp_export_collection_name")
        export_box.operator("modimp.create_export_collection", icon="OUTLINER_COLLECTION")
        export_box.operator("modimp.create_export_part", icon="GROUP")
        export_box.prop(scene, "modimp_export_dir")
        export_box.operator("modimp.export_collection_buffers", icon="PACKAGE")
        export_box.operator("modimp.import_exported_package", icon="IMPORT")
