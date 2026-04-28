import os
import struct
import math

import bpy


FRAME_DIR = r"E:\yh\FrameAnalysis-2026-04-28-191819"
VB0_PATH = os.path.join(
    FRAME_DIR,
    "000191-vb0=b1c65387-vs=90e5f30bc8bfe0ae-ps=041e69f919a26ea9.buf",
)
VS_T4_PATH = os.path.join(
    FRAME_DIR,
    "000191-vs-t4=b1c65387-vs=90e5f30bc8bfe0ae-ps=041e69f919a26ea9.buf",
)
IB_PATH = os.path.join(
    FRAME_DIR,
    "000191-ib=83527398-vs=90e5f30bc8bfe0ae-ps=041e69f919a26ea9.buf",
)

# 000191-ib.txt says this draw uses first index 29448, index count 115740.
# Set IMPORT_INDEX_COUNT = None if you want the whole bound IB instead.
IMPORT_FIRST_INDEX = 29448
IMPORT_INDEX_COUNT = 115740

# Draw every Nth referenced vertex as a line from vb0 -> vs-t4. Use 1 for all.
LINE_STRIDE = 12

# Draw small spheres for the largest vb0 -> vs-t4 differences.
TOP_MARKER_COUNT = 80
MARKER_RADIUS = 0.035

# Scale line endpoints away from vb0 to make small deltas easier to see.
DELTA_SCALE = 12.0
EXAGGERATED_MESH_SCALE = 80.0

# Keep both meshes at the exact same transform. Toggle visibility in Blender to
# compare, or use the translucent red material over the blue current mesh.
CREATE_DELTA_LINES = True
CREATE_TOP_MARKERS = True
CREATE_EXAGGERATED_T4_MESH = True


def load_float3_buffer(path):
    with open(path, "rb") as f:
        data = f.read()
    if len(data) % 12 != 0:
        raise ValueError(f"{path} size is not divisible by float3 stride: {len(data)}")
    values = struct.unpack("<%df" % (len(data) // 4), data)
    return [(values[i], values[i + 1], values[i + 2]) for i in range(0, len(values), 3)]


def load_u16_index_buffer(path, first_index=0, index_count=None):
    with open(path, "rb") as f:
        data = f.read()
    if len(data) % 2 != 0:
        raise ValueError(f"{path} size is not divisible by uint16 stride: {len(data)}")
    indices = struct.unpack("<%dH" % (len(data) // 2), data)
    if index_count is None:
        sliced = indices[first_index:]
    else:
        sliced = indices[first_index : first_index + index_count]
    if len(sliced) % 3 != 0:
        raise ValueError(f"index count must be divisible by 3 for trianglelist: {len(sliced)}")
    return sliced


def compact_vertices(points, indices):
    used = sorted(set(indices))
    remap = {old: new for new, old in enumerate(used)}
    compacted = [points[i] for i in used]
    faces = [
        (remap[indices[i]], remap[indices[i + 1]], remap[indices[i + 2]])
        for i in range(0, len(indices), 3)
    ]
    return compacted, faces, used


def ensure_collection(name):
    old = bpy.data.collections.get(name)
    if old:
        for obj in list(old.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(old)
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    return col


def make_material(name, color):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    if color[3] < 1.0:
        mat.use_nodes = True
        mat.blend_method = "BLEND"
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Alpha"].default_value = color[3]
    return mat


def link_object(collection, obj):
    collection.objects.link(obj)
    bpy.context.collection.objects.unlink(obj)


def create_mesh_object(collection, name, points, faces, material, show_wire=False):
    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(points, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.data.materials.append(material)
    obj.show_wire = show_wire
    obj.show_in_front = show_wire
    bpy.context.collection.objects.link(obj)
    link_object(collection, obj)
    return obj


def create_delta_lines(collection, vb0, t4, used_indices, material):
    verts = []
    edges = []
    lengths = []
    for draw_i, i in enumerate(used_indices):
        a = vb0[i]
        b = t4[i]
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        dz = b[2] - a[2]
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        lengths.append((length, i, dx, dy, dz))
        if draw_i % LINE_STRIDE != 0:
            continue
        start = a
        end = (
            a[0] + dx * DELTA_SCALE,
            a[1] + dy * DELTA_SCALE,
            a[2] + dz * DELTA_SCALE,
        )
        base = len(verts)
        verts.extend([start, end])
        edges.append((base, base + 1))

    mesh = bpy.data.meshes.new("vb0_to_vs_t4_delta_lines_mesh")
    mesh.from_pydata(verts, edges, [])
    mesh.update()
    obj = bpy.data.objects.new("vb0 -> vs-t4 delta lines", mesh)
    obj.data.materials.append(material)
    bpy.context.collection.objects.link(obj)
    link_object(collection, obj)
    return sorted(lengths, reverse=True)


def create_top_markers(collection, vb0, t4, sorted_lengths, mat_vb0, mat_t4):
    for rank, (length, i, dx, dy, dz) in enumerate(sorted_lengths[:TOP_MARKER_COUNT], start=1):
        for name, pos, mat in (
            (f"top_{rank:03d}_vb0_i{i}", vb0[i], mat_vb0),
            (f"top_{rank:03d}_vs_t4_i{i}", t4[i], mat_t4),
        ):
            bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=4, radius=MARKER_RADIUS, location=pos)
            obj = bpy.context.object
            obj.name = name
            obj.data.materials.append(mat)
            link_object(collection, obj)


def create_sample_points(collection, points, name, material, stride):
    verts = [p for i, p in enumerate(points) if i % stride == 0]
    mesh = bpy.data.meshes.new(name + "_mesh")
    mesh.from_pydata(verts, [], [])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.show_name = False
    obj.data.materials.append(material)
    bpy.context.collection.objects.link(obj)
    link_object(collection, obj)
    return obj


def main():
    vb0 = load_float3_buffer(VB0_PATH)
    t4 = load_float3_buffer(VS_T4_PATH)
    if len(vb0) != len(t4):
        raise ValueError(f"vertex count mismatch: vb0={len(vb0)} vs_t4={len(t4)}")

    indices = load_u16_index_buffer(IB_PATH, IMPORT_FIRST_INDEX, IMPORT_INDEX_COUNT)
    vb0_mesh_verts, faces, used_indices = compact_vertices(vb0, indices)
    t4_mesh_verts = [t4[i] for i in used_indices]

    collection = ensure_collection("Yihuan 000191 vb0 vs-t4 compare")
    mat_lines = make_material("delta lines orange", (1.0, 0.45, 0.05, 1.0))
    mat_vb0 = make_material("vb0 current blue", (0.15, 0.35, 1.0, 0.75))
    mat_t4 = make_material("vs-t4 red translucent", (1.0, 0.1, 0.05, 0.45))
    mat_t4_exaggerated = make_material("vs-t4 exaggerated yellow", (1.0, 0.85, 0.05, 0.55))

    create_mesh_object(collection, "vb0 current mesh", vb0_mesh_verts, faces, mat_vb0)
    create_mesh_object(collection, "vs-t4 mesh", t4_mesh_verts, faces, mat_t4, show_wire=True)
    if CREATE_EXAGGERATED_T4_MESH:
        exaggerated_verts = []
        for i in used_indices:
            a = vb0[i]
            b = t4[i]
            exaggerated_verts.append(
                (
                    a[0] + (b[0] - a[0]) * EXAGGERATED_MESH_SCALE,
                    a[1] + (b[1] - a[1]) * EXAGGERATED_MESH_SCALE,
                    a[2] + (b[2] - a[2]) * EXAGGERATED_MESH_SCALE,
                )
            )
        create_mesh_object(
            collection,
            f"vs-t4 exaggerated x{EXAGGERATED_MESH_SCALE:g} mesh",
            exaggerated_verts,
            faces,
            mat_t4_exaggerated,
            show_wire=True,
        )

    if CREATE_DELTA_LINES:
        sorted_lengths = create_delta_lines(collection, vb0, t4, used_indices, mat_lines)
    else:
        sorted_lengths = []
        for i in used_indices:
            a = vb0[i]
            b = t4[i]
            dx = b[0] - a[0]
            dy = b[1] - a[1]
            dz = b[2] - a[2]
            sorted_lengths.append((math.sqrt(dx * dx + dy * dy + dz * dz), i, dx, dy, dz))
        sorted_lengths.sort(reverse=True)

    if CREATE_TOP_MARKERS:
        create_top_markers(collection, vb0, t4, sorted_lengths, mat_vb0, mat_t4)

    max_len, max_i, *_ = sorted_lengths[0]
    avg_len = sum(x[0] for x in sorted_lengths) / len(sorted_lengths)
    print(f"Imported draw mesh: {len(used_indices)} referenced vertices, {len(faces)} triangles")
    print(f"Source buffers: {len(vb0)} float3 vertices each")
    print(f"IB range: first_index={IMPORT_FIRST_INDEX}, index_count={len(indices)}")
    print(f"Delta length: avg={avg_len:.6f}, max={max_len:.6f} at vertex {max_i}")
    print(
        f"LINE_STRIDE={LINE_STRIDE}, DELTA_SCALE={DELTA_SCALE}, "
        f"EXAGGERATED_MESH_SCALE={EXAGGERATED_MESH_SCALE}, TOP_MARKER_COUNT={TOP_MARKER_COUNT}"
    )


if __name__ == "__main__":
    main()
