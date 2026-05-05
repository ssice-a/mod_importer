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

        import_box = layout.box()
        import_box.label(text="导入", icon="IMPORT")
        import_box.prop(scene, "modimp_frame_dump_dir", text="帧分析目录")
        import_box.prop(scene, "modimp_ib_hash", text="IB 哈希")
        import_box.operator("modimp.import_resolved_model", text="导入模型", icon="MESH_DATA")

        row = import_box.row(align=True)
        row.prop(
            scene,
            "modimp_ui_show_import_advanced",
            text="高级导入",
            emboss=False,
            icon="TRIA_DOWN" if scene.modimp_ui_show_import_advanced else "TRIA_RIGHT",
        )
        if scene.modimp_ui_show_import_advanced:
            advanced_box = import_box.box()
            advanced_box.prop(scene, "modimp_collection_name", text="工作集合")
            advanced_box.prop(scene, "modimp_object_prefix", text="物体前缀")
            advanced_box.prop(scene, "modimp_use_pre_cs_source", text="使用 Pre-CS 源")
            advanced_box.prop(scene, "modimp_flip_v", text="翻转 UV V")
            advanced_box.prop(scene, "modimp_mirror_flip", text="镜像翻转")
            advanced_box.prop(scene, "modimp_shade_smooth", text="平滑着色")
            advanced_box.prop(scene, "modimp_store_orig_vertex_id", text="保存原始顶点 ID")

        row = import_box.row(align=True)
        row.prop(
            scene,
            "modimp_ui_show_import_details",
            text="导入详情",
            emboss=False,
            icon="TRIA_DOWN" if scene.modimp_ui_show_import_details else "TRIA_RIGHT",
        )
        if scene.modimp_ui_show_import_details:
            details_box = import_box.box()
            if scene.modimp_frame_analysis_summary:
                details_box.label(text=scene.modimp_frame_analysis_summary)
            if scene.modimp_detected_model_name:
                details_box.label(text=f"模型：{scene.modimp_detected_model_name}")
                details_box.label(text=f"切片数：{scene.modimp_detected_slice_count}")
            if scene.modimp_resolved_ib_hash:
                details_box.label(text=f"IB：{scene.modimp_resolved_ib_hash}")
            if scene.modimp_resolved_display_ib_hash:
                details_box.label(text=f"显示：{scene.modimp_resolved_display_ib_hash}")
            if scene.modimp_resolved_index_count:
                details_box.label(
                    text=f"区间：first={scene.modimp_resolved_first_index} count={scene.modimp_resolved_index_count}"
                )

            working_collection = bpy.data.collections.get(scene.modimp_collection_name.strip())
            if working_collection is not None:
                collector_key = str(working_collection.get(_COLLECTOR_COLLECT_KEY_PROP, "") or "")
                collector_finish = str(working_collection.get(_COLLECTOR_FINISH_CONDITION_PROP, "") or "")
                if collector_key and collector_finish:
                    details_box.separator()
                    details_box.label(text=f"Collector：{collector_key}")
                    details_box.label(text=collector_finish)

        export_box = layout.box()
        export_box.label(text="导出", icon="EXPORT")
        export_box.prop(scene, "modimp_export_collection_name", text="导出集合")
        export_box.prop(scene, "modimp_export_dir", text="导出目录")
        export_box.prop(scene, "modimp_export_mode", text="导出模式")
        export_box.operator("modimp.export_collection_buffers", text="导出缓冲区", icon="PACKAGE")

        row = export_box.row(align=True)
        row.prop(
            scene,
            "modimp_ui_show_export_advanced",
            text="高级导出",
            emboss=False,
            icon="TRIA_DOWN" if scene.modimp_ui_show_export_advanced else "TRIA_RIGHT",
        )
        if scene.modimp_ui_show_export_advanced:
            advanced_box = export_box.box()

            bone_box = advanced_box.box()
            bone_box.label(text="骨骼组", icon="GROUP_BONE")
            bone_box.operator("modimp.apply_bone_merge_map_to_groups", text="应用 BoneMergeMap", icon="SORTBYEXT")
            bone_box.operator("modimp.restore_vertex_group_names", text="恢复顶点组", icon="LOOP_BACK")

            shapekey_box = advanced_box.box()
            shapekey_box.label(text="形态键", icon="SHAPEKEY_DATA")
            shapekey_box.prop(scene, "modimp_export_runtime_shapekeys", text="导出形态键")
            if scene.modimp_export_runtime_shapekeys:
                shapekey_box.prop(scene, "modimp_runtime_shapekey_names", text="形态键名称")
                shapekey_box.label(text="留空则导出全部未静音形态键")
