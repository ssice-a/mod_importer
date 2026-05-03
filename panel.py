"""Sidebar panel for the mod importer/exporter."""

from __future__ import annotations

import bpy


_COLLECTOR_COLLECT_KEY_PROP = "modimp_collector_collect_key"
_COLLECTOR_FINISH_CONDITION_PROP = "modimp_collector_finish_condition"


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
        profile_box.label(text="FrameAnalysis / Profile", icon="TOOL_SETTINGS")
        profile_box.prop(scene, "modimp_profile")
        profile_box.prop(scene, "modimp_frame_dump_dir")
        profile_box.prop(scene, "modimp_ib_hash")
        profile_box.operator("modimp.resolve_from_ib_hash", icon="FILE_REFRESH")
        profile_box.operator("modimp.analyze_frame_stages", icon="VIEWZOOM")
        if scene.modimp_frame_analysis_summary:
            profile_box.label(text=scene.modimp_frame_analysis_summary)

        if scene.modimp_detected_model_name:
            profile_box.separator()
            profile_box.label(text=f"Model: {scene.modimp_detected_model_name}")
            profile_box.label(text=f"Slices: {scene.modimp_detected_slice_count}")
        if scene.modimp_resolved_ib_hash:
            profile_box.label(text=f"IB: {scene.modimp_resolved_ib_hash}")
        if scene.modimp_resolved_display_ib_hash:
            profile_box.label(text=f"Display: {scene.modimp_resolved_display_ib_hash}")
        if scene.modimp_resolved_index_count:
            profile_box.label(
                text=f"Slice: first={scene.modimp_resolved_first_index} count={scene.modimp_resolved_index_count}"
            )

        working_collection = bpy.data.collections.get(scene.modimp_collection_name.strip())
        if working_collection is not None:
            collector_key = str(working_collection.get(_COLLECTOR_COLLECT_KEY_PROP, "") or "")
            collector_finish = str(working_collection.get(_COLLECTOR_FINISH_CONDITION_PROP, "") or "")
            if collector_key and collector_finish:
                profile_box.separator()
                profile_box.label(text=f"Collector: {collector_key}")
                profile_box.label(text=collector_finish)

        import_box = layout.box()
        import_box.label(text="Collection / Import", icon="IMPORT")
        import_box.prop(scene, "modimp_collection_name")
        import_box.prop(scene, "modimp_object_prefix")
        import_box.prop(scene, "modimp_use_pre_cs_source")
        import_box.prop(scene, "modimp_flip_v")
        import_box.prop(scene, "modimp_mirror_flip")
        import_box.prop(scene, "modimp_shade_smooth")
        import_box.prop(scene, "modimp_store_orig_vertex_id")
        import_box.operator("modimp.import_resolved_model", icon="MESH_DATA")

        export_box = layout.box()
        export_box.label(text="Export", icon="EXPORT")

        bone_box = export_box.box()
        bone_box.label(text="Export Bone Groups", icon="GROUP_BONE")
        bone_box.operator("modimp.apply_bone_merge_map_to_groups", icon="SORTBYEXT")
        bone_box.operator("modimp.restore_vertex_group_names", icon="LOOP_BACK")

        outline_box = export_box.box()
        outline_box.label(text="Outline / Rim Export", icon="LIGHT")
        outline_box.label(text="Uses profile defaults, object attrs, or vertex color data")

        shapekey_box = export_box.box()
        shapekey_box.label(text="Runtime Shapekey", icon="SHAPEKEY_DATA")
        shapekey_box.prop(scene, "modimp_export_runtime_shapekeys")
        if scene.modimp_export_runtime_shapekeys:
            shapekey_box.prop(scene, "modimp_runtime_shapekey_names")
            shapekey_box.label(text="Blank means all non-muted shapekeys")

        export_box.prop(scene, "modimp_export_dir")
        export_box.prop(scene, "modimp_export_mode")
        export_box.operator("modimp.export_collection_buffers", icon="PACKAGE")
