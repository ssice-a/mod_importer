"""Small UI localization layer for the Blender add-on.

The add-on logic should use stable translation keys instead of embedding UI
strings in importer/exporter code. Keep this module UI-only.
"""

from __future__ import annotations

try:
    import bpy
except ModuleNotFoundError:  # pragma: no cover - used outside Blender.
    bpy = None


LANGUAGE_ITEMS = (
    ("ZH", "中文", "使用中文界面"),
    ("EN", "English", "Use English UI"),
)


_ZH = {
    "app.title": "Mod Importer",
    "common.missing_source": "缺少源文件",
    "common.unmarked": "未标记",
    "semantic.base_color": "基础色",
    "semantic.normal": "法线",
    "semantic.material": "材质",
    "semantic.effect": "特效",
    "semantic.clear": "清",
    "import.title": "导入",
    "import.frame_dir": "帧分析目录",
    "import.ib_hash": "IB 哈希",
    "import.analyze": "Analyze",
    "import.import": "Import",
    "import.advanced": "高级导入",
    "import.collection": "工作集合",
    "import.object_prefix": "物体前缀",
    "import.use_pre_cs": "使用 Pre-CS 源",
    "import.flip_uv_v": "翻转 UV V",
    "import.mirror_flip": "镜像翻转",
    "import.shade_smooth": "平滑着色",
    "import.store_orig_vertex_id": "保存原始顶点 ID",
    "import.details": "导入详情",
    "import.model": "模型：{value}",
    "import.slice_count": "切片数：{value}",
    "import.ib": "IB：{value}",
    "import.display": "显示：{value}",
    "import.range": "区间：first={first} count={count}",
    "import.collector": "Collector：{value}",
    "texture.title": "贴图标记",
    "texture.region": "子部件",
    "texture.draw": "Draw",
    "texture.no_candidates": "没有贴图候选；先 Analyze，或换一个子部件/Draw。",
    "texture.apply": "应用贴图到当前模型",
    "texture.unique_note": "基础色/法线每个子部件只保留一个，新标记会替换旧标记。",
    "export.title": "导出",
    "export.collection": "导出集合",
    "export.dir": "导出目录",
    "export.mode": "导出模式",
    "export.button": "导出缓冲区",
    "export.advanced": "高级导出",
    "bone.title": "骨骼组",
    "bone.apply_map": "应用 BoneMergeMap",
    "bone.restore_groups": "恢复顶点组",
    "shapekey.title": "Shapekey",
    "shapekey.export": "导出 Shapekey",
    "shapekey.names": "Shapekey 名称",
    "shapekey.empty_means_all": "留空则导出全部非 Basis shapekey",
    "settings.language": "界面语言",
}


_EN = {
    "app.title": "Mod Importer",
    "common.missing_source": "missing source",
    "common.unmarked": "Unmarked",
    "semantic.base_color": "Base",
    "semantic.normal": "Normal",
    "semantic.material": "Material",
    "semantic.effect": "Effect",
    "semantic.clear": "Clear",
    "import.title": "Import",
    "import.frame_dir": "FrameAnalysis Dir",
    "import.ib_hash": "IB Hash",
    "import.analyze": "Analyze",
    "import.import": "Import",
    "import.advanced": "Advanced Import",
    "import.collection": "Working Collection",
    "import.object_prefix": "Object Prefix",
    "import.use_pre_cs": "Use Pre-CS Source",
    "import.flip_uv_v": "Flip UV V",
    "import.mirror_flip": "Mirror Flip",
    "import.shade_smooth": "Shade Smooth",
    "import.store_orig_vertex_id": "Store Original Vertex ID",
    "import.details": "Import Details",
    "import.model": "Model: {value}",
    "import.slice_count": "Slices: {value}",
    "import.ib": "IB: {value}",
    "import.display": "Display: {value}",
    "import.range": "Range: first={first} count={count}",
    "import.collector": "Collector: {value}",
    "texture.title": "Texture Marking",
    "texture.region": "Region",
    "texture.draw": "Draw",
    "texture.no_candidates": "No texture candidates. Run Analyze, or choose another region/draw.",
    "texture.apply": "Apply Textures To Current Models",
    "texture.unique_note": "Base color and normal are unique per region. A new mark replaces the old one.",
    "export.title": "Export",
    "export.collection": "Export Collection",
    "export.dir": "Export Dir",
    "export.mode": "Export Mode",
    "export.button": "Export Buffers",
    "export.advanced": "Advanced Export",
    "bone.title": "Bone Groups",
    "bone.apply_map": "Apply BoneMergeMap",
    "bone.restore_groups": "Restore Vertex Groups",
    "shapekey.title": "Shapekey",
    "shapekey.export": "Export Shapekey",
    "shapekey.names": "Shapekey Names",
    "shapekey.empty_means_all": "Leave blank to export every non-Basis shapekey.",
    "settings.language": "UI Language",
}


_TABLES = {
    "ZH": _ZH,
    "EN": _EN,
}


def language(scene=None) -> str:
    value = ""
    if scene is not None:
        value = str(getattr(scene, "modimp_ui_language", "") or "")
    if value in _TABLES:
        return value
    return "ZH"


def t(scene, key: str, **kwargs) -> str:
    table = _TABLES.get(language(scene), _ZH)
    text = table.get(key, _EN.get(key, key))
    return text.format(**kwargs) if kwargs else text


def semantic_label(scene, semantic: str) -> str:
    return t(scene, f"semantic.{semantic}") if semantic else t(scene, "common.unmarked")

