"""Blender add-on entry point for the mod importer/exporter."""

from __future__ import annotations

bl_info = {
    "name": "Mod Importer",
    "author": "OpenAI Codex",
    "version": (0, 6, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Mod Importer",
    "description": "Import and export profile-driven 3DMigoto model buffers and HLSL assets.",
    "category": "Import-Export",
}

try:
    import bpy
except ModuleNotFoundError:  # pragma: no cover - used for parser tests outside Blender.
    bpy = None

if bpy is not None:
    from . import operators, panel, properties

    REGISTERED_CLASSES = (
        operators.MODIMP_OT_resolve_from_ib_hash,
        operators.MODIMP_OT_import_resolved_model,
        operators.MODIMP_OT_create_export_collection,
        operators.MODIMP_OT_create_export_part,
        operators.MODIMP_OT_analyze_frame_stages,
        operators.MODIMP_OT_rename_vertex_groups_from_palette,
        operators.MODIMP_OT_restore_vertex_group_names,
        operators.MODIMP_OT_split_export_parts,
        operators.MODIMP_OT_export_collection_buffers,
        operators.MODIMP_OT_import_exported_package,
        panel.VIEW3D_PT_mod_importer,
    )
else:
    operators = None
    panel = None
    properties = None
    REGISTERED_CLASSES = ()


def register():
    """Register properties, operators, and panels."""
    if bpy is None:
        raise RuntimeError("This add-on can only be registered inside Blender.")
    properties.register_addon_properties()
    for blender_class in REGISTERED_CLASSES:
        bpy.utils.register_class(blender_class)


def unregister():
    """Unregister everything added by the add-on."""
    if bpy is None:
        return
    for blender_class in reversed(REGISTERED_CLASSES):
        bpy.utils.unregister_class(blender_class)
    properties.unregister_addon_properties()


if __name__ == "__main__":
    register()
