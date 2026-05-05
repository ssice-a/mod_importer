"""Sidebar panel for the mod importer/exporter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import bpy

from .core.texture_converter import TextureConversionError, convert_dds_to_png_preview, load_image_for_blender


_COLLECTOR_COLLECT_KEY_PROP = "modimp_collector_collect_key"
_COLLECTOR_FINISH_CONDITION_PROP = "modimp_collector_finish_condition"
_TEXTURE_MARKS_TEXT_PROP = "modimp_texture_marks_text"
_PREVIEW_COLLECTION = None

_SEMANTIC_LABELS = {
    "base_color": "基础色",
    "normal": "法线",
    "material": "材质",
    "effect": "特效",
}


def _preview_collection():
    global _PREVIEW_COLLECTION  # pylint: disable=global-statement
    if _PREVIEW_COLLECTION is None:
        import bpy.utils.previews

        _PREVIEW_COLLECTION = bpy.utils.previews.new()
    return _PREVIEW_COLLECTION


def unregister_preview_cache():
    global _PREVIEW_COLLECTION  # pylint: disable=global-statement
    if _PREVIEW_COLLECTION is None:
        return
    import bpy.utils.previews

    bpy.utils.previews.remove(_PREVIEW_COLLECTION)
    _PREVIEW_COLLECTION = None


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


def _active_texture_mark_payload(scene: bpy.types.Scene) -> dict[str, object]:
    collection = _active_work_collection(scene)
    if collection is None:
        return {}
    payload = _read_text_json(str(collection.get(_TEXTURE_MARKS_TEXT_PROP, "") or ""))
    return payload if isinstance(payload, dict) else {}


def _image_preview_icon(source_path: str) -> int | None:
    path = Path(str(source_path or ""))
    if not path.is_file():
        return None
    preview_path = path
    if path.suffix.lower() == ".dds":
        try:
            preview_path = convert_dds_to_png_preview(path)
        except (FileNotFoundError, TextureConversionError):
            preview_path = path

    try:
        stat = preview_path.stat()
        key_payload = f"{preview_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode(
            "utf-8", errors="ignore"
        )
        preview_key = hashlib.sha1(key_payload).hexdigest()
        previews = _preview_collection()
        if preview_key in previews:
            return int(previews[preview_key].icon_id)
        thumbnail = previews.load(preview_key, str(preview_path), "IMAGE")
        return int(thumbnail.icon_id)
    except Exception:
        pass

    try:
        image = load_image_for_blender(path)
        preview = image.preview_ensure()
        return int(preview.icon_id)
    except Exception:  # Blender cannot preview every DDS/BC format.
        return None


def _slot_sort_key(slot: str) -> int:
    tail = str(slot).split("-t")[-1]
    return int(tail) if tail.isdigit() else 999


def _draw_texture_candidates(scene: bpy.types.Scene) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    payload = _active_texture_mark_payload(scene)
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


class MODIMP_UL_texture_mark_candidates(bpy.types.UIList):
    """Native scrollable list of texture candidates."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):  # pylint: disable=unused-argument
        semantic = str(item.semantic or "")
        semantic_index = int(item.semantic_index or 0)
        semantic_suffix = f" {semantic_index}" if semantic in {"material", "effect"} else ""
        mark_label = _SEMANTIC_LABELS.get(semantic, "未标记") + semantic_suffix if semantic else "未标记"

        row = layout.row(align=True)
        icon_id = _image_preview_icon(item.source_path)
        if icon_id is not None:
            row.label(text="", icon_value=icon_id)
        else:
            row.label(text="", icon="TEXTURE")
        row.label(text=f"{item.slot}  {item.hash_value[:8]}  {mark_label}")
        row.label(text=item.filename or "missing source")


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
        row = import_box.row(align=True)
        row.operator("modimp.analyze_frame_stages", text="Analyze", icon="VIEWZOOM")
        row.operator("modimp.import_resolved_model", text="Import", icon="MESH_DATA")

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

        texture_box = layout.box()
        row = texture_box.row(align=True)
        row.prop(
            scene,
            "modimp_ui_show_texture_marking",
            text="贴图标记",
            emboss=False,
            icon="TRIA_DOWN" if scene.modimp_ui_show_texture_marking else "TRIA_RIGHT",
        )
        if scene.modimp_ui_show_texture_marking:
            texture_box.prop(scene, "modimp_texture_mark_region", text="子部件")
            texture_box.prop(scene, "modimp_texture_mark_draw", text="Draw")
            if not scene.modimp_texture_mark_items:
                texture_box.label(text="没有贴图候选；先 Analyze，或换一个子部件/Draw。", icon="INFO")
            texture_box.template_list(
                "MODIMP_UL_texture_mark_candidates",
                "",
                scene,
                "modimp_texture_mark_items",
                scene,
                "modimp_texture_mark_index",
                rows=4,
            )

            active_index = int(scene.modimp_texture_mark_index)
            if 0 <= active_index < len(scene.modimp_texture_mark_items):
                item = scene.modimp_texture_mark_items[active_index]
                detail_box = texture_box.box()
                row = detail_box.row(align=True)
                icon_id = _image_preview_icon(item.source_path)
                if icon_id is not None:
                    row.template_icon(icon_value=icon_id, scale=7.0)
                else:
                    row.label(text="", icon="TEXTURE")

                semantic = str(item.semantic or "")
                semantic_index = int(item.semantic_index or 0)
                semantic_suffix = f" {semantic_index}" if semantic in {"material", "effect"} else ""
                mark_label = _SEMANTIC_LABELS.get(semantic, "未标记") + semantic_suffix if semantic else "未标记"

                info = row.column(align=True)
                info.label(text=f"{item.slot}  {item.hash_value[:8]}  {mark_label}")
                info.label(text=item.filename or "missing source")
                buttons = detail_box.row(align=True)
                for mark_semantic, label in (
                    ("base_color", "基础"),
                    ("normal", "法线"),
                    ("material", "材质"),
                    ("effect", "特效"),
                ):
                    op = buttons.operator(
                        "modimp.mark_texture_semantic",
                        text=label,
                        depress=semantic == mark_semantic,
                    )
                    op.slot = item.slot
                    op.semantic = mark_semantic
                op = buttons.operator("modimp.mark_texture_semantic", text="清")
                op.slot = item.slot
                op.semantic = "clear"
            texture_box.operator("modimp.apply_texture_marks_to_models", text="应用贴图到当前模型", icon="MATERIAL")
            texture_box.label(text="基础色/法线每个子部件只保留一个，新标记会替换旧标记。")

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
            shapekey_box.label(text="Shapekey", icon="SHAPEKEY_DATA")
            shapekey_box.prop(scene, "modimp_export_runtime_shapekeys", text="导出 Shapekey")
            if scene.modimp_export_runtime_shapekeys:
                shapekey_box.prop(scene, "modimp_runtime_shapekey_names", text="Shapekey 名称")
                shapekey_box.label(text="留空则导出全部非 Basis shapekey")
