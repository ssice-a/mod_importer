"""Operators used by the mod importer/exporter."""

from __future__ import annotations

import bpy

from .core.discovery import discover_yihuan_model, resolve_yihuan_bundle_from_ib_hash
from .core.exporter import export_collection_package
from .core.importer import import_detected_model
from .core.profiles import YIHUAN_PROFILE, get_profile


def _ensure_supported_profile(scene: bpy.types.Scene):
    profile = get_profile(scene.modimp_profile)
    if profile.profile_id != YIHUAN_PROFILE.profile_id:
        raise ValueError(f"Profile is registered but not implemented yet: {profile.profile_id}")
    return profile


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
    if not scene.modimp_object_prefix.strip():
        scene.modimp_object_prefix = resolved_bundle.model_name


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

            object_prefix = scene.modimp_object_prefix.strip() or detected_model.model_name
            imported_objects, import_stats = import_detected_model(
                context,
                detected_model=detected_model,
                object_prefix=object_prefix,
                collection_name=scene.modimp_import_collection_name.strip(),
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

        if not scene.modimp_export_collection_name.strip():
            scene.modimp_export_collection_name = scene.modimp_import_collection_name

        self.report(
            {"INFO"},
            (
                f"Imported {import_stats['slice_count']} slices, "
                f"{import_stats['vertex_count']} compacted verts, "
                f"{import_stats['triangle_count']} tris into "
                f"{scene.modimp_import_collection_name.strip() or 'the scene collection'}."
            ),
        )
        return {"FINISHED"}


class MODIMP_OT_create_export_collection(bpy.types.Operator):
    """Create one export collection and link the current working objects into it."""

    bl_idname = "modimp.create_export_collection"
    bl_label = "Create Export Collection"
    bl_description = "Create the export collection and link selected mesh objects into it"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        export_collection_name = scene.modimp_export_collection_name.strip()
        if not export_collection_name:
            import_collection_name = scene.modimp_import_collection_name.strip()
            if import_collection_name:
                export_collection_name = f"{import_collection_name} Export"
            else:
                export_collection_name = "Mod Importer Export"
            scene.modimp_export_collection_name = export_collection_name

        export_collection = _ensure_scene_collection_linked(scene, export_collection_name)

        selected_mesh_objects = [obj for obj in context.selected_objects if obj.type == "MESH"]
        source_objects = selected_mesh_objects
        if not source_objects:
            import_collection_name = scene.modimp_import_collection_name.strip()
            import_collection = bpy.data.collections.get(import_collection_name) if import_collection_name else None
            if import_collection is not None:
                source_objects = [obj for obj in import_collection.all_objects if obj.type == "MESH"]

        if not source_objects:
            self.report(
                {"ERROR"},
                "No mesh objects selected and the import collection is empty or missing.",
            )
            return {"CANCELLED"}

        linked_count = 0
        for obj in source_objects:
            if obj.name not in export_collection.objects.keys():
                export_collection.objects.link(obj)
                linked_count += 1

        self.report(
            {"INFO"},
            (
                f"Export collection '{export_collection.name}' is ready. "
                f"Linked {linked_count} new mesh objects, total {len(export_collection.objects)}."
            ),
        )
        return {"FINISHED"}


class MODIMP_OT_export_collection_buffers(bpy.types.Operator):
    """Export one collection into shared buffers and runtime HLSL assets."""

    bl_idname = "modimp.export_collection_buffers"
    bl_label = "Export Collection Package"
    bl_description = "Rebuild shared buffers, manifests, and profile HLSL for one collection"
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        _ensure_supported_profile(scene)

        if not scene.modimp_export_collection_name.strip():
            self.report({"ERROR"}, "Fill Export Collection first.")
            return {"CANCELLED"}
        if not scene.modimp_export_dir.strip():
            self.report({"ERROR"}, "Fill Export Dir first.")
            return {"CANCELLED"}

        try:
            export_stats = export_collection_package(
                collection_name=scene.modimp_export_collection_name.strip(),
                export_dir=scene.modimp_export_dir.strip(),
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            (
                f"Exported {export_stats['slice_count']} slices, "
                f"{export_stats['vertex_count']} verts, "
                f"{export_stats['triangle_count']} tris to {export_stats['buffer_dir']}."
            ),
        )
        return {"FINISHED"}
