"""
Import one or more FrameAnalysis mesh dumps into Blender.

Usage:
1. Open Blender.
2. Open the Scripting workspace.
3. Load this file and adjust the paths below if needed.
4. Run the script.

This script now imports two groups for quick comparison:
- custom 000148 meshes from the current modded frame
- original 000144 meshes from the older unmodified frame

Each group contains:
- raw post-skin vb0
- raw pre-skin t4
- basis-applied replay of the early 9d62 position chain
- a simple t5-direction shell based on that basis replay
- an approximate t6-displacement shell using the dumped d77b480e texture

The basis-applied mesh is computed inside Blender from the same dump files, so
you no longer need to run a separate command-line replay script first.
"""

from __future__ import annotations

from pathlib import Path
import struct

import bpy


FRAME_DIR = Path(r"E:\yh\FrameAnalysis-2026-04-26-005519")
ORIGINAL_FRAME_DIR = Path(r"E:\yh\FrameAnalysis-2026-04-25-164449")
COLLECTION_NAME = "fa_9d62_compare"
DRAW_NAME = "000148"
ORIGINAL_DRAW_NAME = "000144"

IMPORT_SPECS = [
    {
        "name": "000148_post_skin_vb0",
        "ib_path": FRAME_DIR / f"{DRAW_NAME}-ib-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        "position_path": FRAME_DIR / f"{DRAW_NAME}-vb0-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        "index_start": 0,
        "index_count": None,
    },
    {
        "name": "000148_pre_skin_t4",
        "ib_path": FRAME_DIR / f"{DRAW_NAME}-ib-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        "position_path": FRAME_DIR / f"{DRAW_NAME}-vs-t4-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        "index_start": 0,
        "index_count": None,
    },
    {
        "name": "000144_original_post_skin_vb0",
        "ib_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-ib=83527398-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        "position_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-vb0=b1c65387-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        "index_start": 29448,
        "index_count": 115740,
    },
    {
        "name": "000144_original_pre_skin_t4",
        "ib_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-ib=83527398-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        "position_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-vs-t4=7fec12c0-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        "index_start": 29448,
        "index_count": 115740,
    },
]

REPLAY_9D62_SPEC = {
    "name": "000148_basis_applied",
    "ib_path": FRAME_DIR / f"{DRAW_NAME}-ib-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "vb0_path": FRAME_DIR / f"{DRAW_NAME}-vb0-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "vb1_path": FRAME_DIR / f"{DRAW_NAME}-vb1=1236657b-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "cb1_path": FRAME_DIR / f"{DRAW_NAME}-vs-cb1=6e5a5274-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "cb2_path": FRAME_DIR / f"{DRAW_NAME}-vs-cb2=0c8934aa-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "t0_path": FRAME_DIR / f"{DRAW_NAME}-vs-t0=81189244-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "t1_path": FRAME_DIR / f"{DRAW_NAME}-vs-t1=135e128a-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "t2_path": FRAME_DIR / f"{DRAW_NAME}-vs-t2=ab75cfe2-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "index_start": 0,
    "index_count": None,
}

ORIGINAL_REPLAY_9D62_SPEC = {
    "name": "000144_original_basis_applied",
    "ib_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-ib=83527398-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "vb0_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-vb0=b1c65387-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "vb1_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-vb1=1236657b-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "cb1_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-vs-cb1=6e5a5274-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "cb2_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-vs-cb2=0c8934aa-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "cb3_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-vs-cb3=743b92ec-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "t0_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-vs-t0=81189244-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "t1_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-vs-t1=135e128a-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "t2_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-vs-t2=ab75cfe2-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "t5_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-vs-t5=dc7f2baf-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
    "index_start": 29448,
    "index_count": 115740,
}

REPLAY_T5_SPECS = [
    {
        "name": "000148_t5_direction_shell",
        "ib_path": REPLAY_9D62_SPEC["ib_path"],
        "vb0_path": REPLAY_9D62_SPEC["vb0_path"],
        "vb1_path": REPLAY_9D62_SPEC["vb1_path"],
        "cb1_path": REPLAY_9D62_SPEC["cb1_path"],
        "cb2_path": REPLAY_9D62_SPEC["cb2_path"],
        "cb3_path": FRAME_DIR / f"{DRAW_NAME}-vs-cb3=aede39f6-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        "t0_path": REPLAY_9D62_SPEC["t0_path"],
        "t1_path": REPLAY_9D62_SPEC["t1_path"],
        "t2_path": REPLAY_9D62_SPEC["t2_path"],
        "t5_path": FRAME_DIR / f"{DRAW_NAME}-vs-t5-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        "index_start": REPLAY_9D62_SPEC["index_start"],
        "index_count": REPLAY_9D62_SPEC["index_count"],
    },
    {
        "name": "000144_original_t5_direction_shell",
        "ib_path": ORIGINAL_REPLAY_9D62_SPEC["ib_path"],
        "vb0_path": ORIGINAL_REPLAY_9D62_SPEC["vb0_path"],
        "vb1_path": ORIGINAL_REPLAY_9D62_SPEC["vb1_path"],
        "cb1_path": ORIGINAL_REPLAY_9D62_SPEC["cb1_path"],
        "cb2_path": ORIGINAL_REPLAY_9D62_SPEC["cb2_path"],
        "cb3_path": ORIGINAL_REPLAY_9D62_SPEC["cb3_path"],
        "t0_path": ORIGINAL_REPLAY_9D62_SPEC["t0_path"],
        "t1_path": ORIGINAL_REPLAY_9D62_SPEC["t1_path"],
        "t2_path": ORIGINAL_REPLAY_9D62_SPEC["t2_path"],
        "t5_path": ORIGINAL_REPLAY_9D62_SPEC["t5_path"],
        "index_start": ORIGINAL_REPLAY_9D62_SPEC["index_start"],
        "index_count": ORIGINAL_REPLAY_9D62_SPEC["index_count"],
    },
]

T5_SHELL_SCALE = 10.0

REPLAY_T6_SPECS = [
    {
        "name": "000148_t6_displacement_shell",
        "ib_path": REPLAY_9D62_SPEC["ib_path"],
        "vb0_path": REPLAY_9D62_SPEC["vb0_path"],
        "vb1_path": REPLAY_9D62_SPEC["vb1_path"],
        "cb1_path": REPLAY_9D62_SPEC["cb1_path"],
        "cb2_path": REPLAY_9D62_SPEC["cb2_path"],
        "cb3_path": FRAME_DIR / f"{DRAW_NAME}-vs-cb3=aede39f6-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        "cb5_path": FRAME_DIR / f"{DRAW_NAME}-vs-cb5=2643e0c2-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        "t0_path": REPLAY_9D62_SPEC["t0_path"],
        "t1_path": REPLAY_9D62_SPEC["t1_path"],
        "t2_path": REPLAY_9D62_SPEC["t2_path"],
        "t5_path": FRAME_DIR / f"{DRAW_NAME}-vs-t5-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        "t6_path": FRAME_DIR / f"{DRAW_NAME}-vs-t6=d77b480e-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.dds",
        "index_start": REPLAY_9D62_SPEC["index_start"],
        "index_count": REPLAY_9D62_SPEC["index_count"],
    },
    {
        "name": "000144_original_t6_displacement_shell",
        "ib_path": ORIGINAL_REPLAY_9D62_SPEC["ib_path"],
        "vb0_path": ORIGINAL_REPLAY_9D62_SPEC["vb0_path"],
        "vb1_path": ORIGINAL_REPLAY_9D62_SPEC["vb1_path"],
        "cb1_path": ORIGINAL_REPLAY_9D62_SPEC["cb1_path"],
        "cb2_path": ORIGINAL_REPLAY_9D62_SPEC["cb2_path"],
        "cb3_path": ORIGINAL_REPLAY_9D62_SPEC["cb3_path"],
        "cb5_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-vs-cb5=2643e0c2-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        "t0_path": ORIGINAL_REPLAY_9D62_SPEC["t0_path"],
        "t1_path": ORIGINAL_REPLAY_9D62_SPEC["t1_path"],
        "t2_path": ORIGINAL_REPLAY_9D62_SPEC["t2_path"],
        "t5_path": ORIGINAL_REPLAY_9D62_SPEC["t5_path"],
        "t6_path": ORIGINAL_FRAME_DIR / f"{ORIGINAL_DRAW_NAME}-vs-t6=d77b480e-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.dds",
        "index_start": ORIGINAL_REPLAY_9D62_SPEC["index_start"],
        "index_count": ORIGINAL_REPLAY_9D62_SPEC["index_count"],
    },
]

T6_SAMPLE_MODE = "wrap"


def read_u16_indices(path: Path) -> list[int]:
    raw = path.read_bytes()
    if len(raw) % 2 != 0:
        raise ValueError(f"{path} size is not divisible by 2: {len(raw)}")
    return list(struct.unpack(f"<{len(raw) // 2}H", raw))


def read_float3_positions(path: Path) -> list[tuple[float, float, float]]:
    raw = path.read_bytes()
    if len(raw) % 12 != 0:
        raise ValueError(f"{path} size is not divisible by 12: {len(raw)}")
    values = struct.unpack(f"<{len(raw) // 4}f", raw)
    return [
        (values[index], values[index + 1], values[index + 2])
        for index in range(0, len(values), 3)
    ]


def read_u32_words(path: Path) -> list[int]:
    raw = path.read_bytes()
    if len(raw) % 4 != 0:
        raise ValueError(f"{path} size is not divisible by 4: {len(raw)}")
    return list(struct.unpack(f"<{len(raw) // 4}I", raw))


def read_f32_words(path: Path) -> list[float]:
    raw = path.read_bytes()
    if len(raw) % 4 != 0:
        raise ValueError(f"{path} size is not divisible by 4: {len(raw)}")
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def read_snorm4_words(path: Path) -> list[tuple[float, float, float, float]]:
    raw = path.read_bytes()
    if len(raw) % 8 != 0:
        raise ValueError(f"{path} size is not divisible by 8: {len(raw)}")
    values = struct.unpack(f"<{len(raw) // 2}h", raw)
    out: list[tuple[float, float, float, float]] = []
    for index in range(0, len(values), 4):
        record = values[index : index + 4]
        out.append(
            tuple(
                max(-1.0, component / 32767.0)
                for component in record
            )
        )
    return out


def as_f32(u32_value: int) -> float:
    return struct.unpack("<f", struct.pack("<I", u32_value & 0xFFFFFFFF))[0]


def grouped(words: list[int], width: int) -> list[tuple[int, ...]]:
    if len(words) % width != 0:
        raise ValueError(f"Word count {len(words)} is not divisible by {width}")
    return [tuple(words[index : index + width]) for index in range(0, len(words), width)]


def ensure_collection(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def remove_existing_object(name: str):
    existing = bpy.data.objects.get(name)
    if existing is None:
        return
    mesh = existing.data if existing.type == "MESH" else None
    bpy.data.objects.remove(existing, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh, do_unlink=True)


def build_triangles(indices: list[int], vertex_count: int) -> list[tuple[int, int, int]]:
    if len(indices) % 3 != 0:
        raise ValueError(f"Index buffer length is not divisible by 3: {len(indices)}")
    if not indices:
        return []

    max_index = max(indices)
    if max_index >= vertex_count:
        raise ValueError(
            f"Index buffer references vertex {max_index}, but only {vertex_count} positions exist."
        )

    return [
        (indices[index], indices[index + 1], indices[index + 2])
        for index in range(0, len(indices), 3)
    ]


def slice_indices(indices: list[int], index_start: int, index_count: int | None) -> list[int]:
    if index_start < 0:
        raise ValueError(f"index_start must be >= 0, got {index_start}")
    if index_start > len(indices):
        raise ValueError(f"index_start {index_start} exceeds index buffer length {len(indices)}")
    if index_count is None:
        return indices[index_start:]
    index_end = index_start + index_count
    if index_end > len(indices):
        raise ValueError(
            f"index range [{index_start}, {index_end}) exceeds index buffer length {len(indices)}"
        )
    return indices[index_start:index_end]


def create_mesh_object(
    *,
    name: str,
    collection: bpy.types.Collection,
    positions: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
):
    remove_existing_object(name)

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(positions, [], triangles)
    mesh.update(calc_edges=True)
    mesh.validate(verbose=False)

    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)

    for polygon in mesh.polygons:
        polygon.use_smooth = True

    return obj


def import_mesh_object(
    *,
    name: str,
    collection: bpy.types.Collection,
    ib_path: Path,
    position_path: Path,
    index_start: int = 0,
    index_count: int | None = None,
):
    if not ib_path.exists():
        raise FileNotFoundError(f"IB dump not found: {ib_path}")
    if not position_path.exists():
        raise FileNotFoundError(f"Position dump not found: {position_path}")

    indices = read_u16_indices(ib_path)
    positions = read_float3_positions(position_path)
    sliced_indices = slice_indices(indices, index_start, index_count)
    triangles = build_triangles(sliced_indices, len(positions))

    obj = create_mesh_object(
        name=name,
        collection=collection,
        positions=positions,
        triangles=triangles,
    )

    obj["frameanalysis_ib_path"] = str(ib_path)
    obj["frameanalysis_position_path"] = str(position_path)
    obj["frameanalysis_vertex_count"] = len(positions)
    obj["frameanalysis_triangle_count"] = len(triangles)
    obj["frameanalysis_index_start"] = index_start
    obj["frameanalysis_index_count"] = -1 if index_count is None else index_count

    return obj


def decode_section_selector(
    vb1_words: list[int],
    t0_records: list[tuple[int, ...]],
    t1_records: list[tuple[int, ...]],
    cb2_words: list[int],
) -> dict[str, int]:
    selector = vb1_words[0]
    packed_selector = t0_records[selector][0]
    low24 = packed_selector & 0x00FFFFFF
    shift = cb2_words[0]
    mask = cb2_words[1]
    stride = cb2_words[2]
    page = low24 >> shift
    slot = low24 & mask
    t1_index = stride * page + slot
    t1_entry = t1_records[t1_index][0]
    section_index = t1_entry & 0x000FFFFF

    return {
        "instance_selector": selector,
        "shift": shift,
        "mask": mask,
        "stride": stride,
        "page": page,
        "slot": slot,
        "t1_index": t1_index,
        "section_index": section_index,
    }


def decode_basis(
    *,
    section_index: int,
    t1_records: list[tuple[int, ...]],
    t2_records: list[tuple[int, ...]],
    cb1_words: list[float],
    selector_info: dict[str, int],
) -> dict[str, object]:
    shift = selector_info["shift"]
    stride = selector_info["stride"]
    page = selector_info["page"]
    slot = selector_info["slot"]

    section_entry = t2_records[section_index * 44 + 1]

    secondary_left_index = slot + stride * page + (1 << shift)
    secondary_right_index = slot + stride * page + (2 << shift)

    left_entry = t1_records[secondary_left_index]
    right_entry = t1_records[secondary_right_index]

    r2_x, r2_y, r2_z, r2_w = left_entry
    r0_x_raw, r0_z_raw, _ = right_entry[:3]

    r3_x_u = (r2_x >> 16) & 0xFFFFFFFF
    r3_y_u = (r2_z >> 16) & 0xFFFFFFFF
    r3_z_u = (r2_w >> 16) & 0xFFFFFFFF

    r4_x_u = r2_x & 0xFFFF
    r4_y_u = r2_y & 0x7FFF
    r4_z_flag = r2_y & 0x8000

    qx = (-32768.0 + float(r4_x_u)) * 3.05185094e-05
    qy = (-32768.0 + float(r3_x_u)) * 3.05185094e-05
    qw = (-16384.0 + float(r4_y_u)) * 4.3161006e-05

    v5_x = qx + qy
    v5_y = qx - qy
    v5_z = 2.0 - (abs(v5_x) + abs(v5_y))
    inv_length = 1.0 / ((v5_x * v5_x + v5_y * v5_y + v5_z * v5_z) ** 0.5)
    v5_x *= inv_length
    v5_y *= inv_length
    v5_z *= inv_length

    inv_one_plus_z = 1.0 / (1.0 + v5_z)
    tmp_y = (-v5_x * v5_y) * inv_one_plus_z

    r7_x = 1.0 - (v5_x * v5_x) * inv_one_plus_z
    r7_y = 1.0 - (v5_y * v5_y) * inv_one_plus_z
    r7_z = tmp_y
    r7_w = -v5_x

    alt = (1.0 - qw * qw) ** 0.5
    if r4_z_flag != 0:
        left_scale = alt
        right_scale = qw
    else:
        left_scale = qw
        right_scale = alt

    r4_x = r7_x * left_scale + r7_z * right_scale
    r4_y = r7_w * left_scale + r7_y * right_scale
    r4_z = r7_z * left_scale + r7_w * right_scale

    r6_x = v5_y * r4_z - v5_z * r4_y
    r6_y = v5_z * r4_x - v5_x * r4_z
    r6_z = v5_x * r4_y - v5_y * r4_x

    scale_bits = ((r3_z_u << 23) + 0xF8800000) & 0xFFFFFFFF
    scale = as_f32(scale_bits)

    scale_x = (float(r3_x_u & 0xFFFF) - 32768.0) * scale
    scale_y = (float(r3_y_u & 0xFFFF) - 32768.0) * scale
    scale_z = (float(r3_z_u & 0xFFFF) - 32768.0) * scale

    basis_x = (r4_x * scale_x, r4_y * scale_x, r4_z * scale_x)
    basis_y = (r6_x * scale_y, r6_y * scale_y, r6_z * scale_y)
    basis_z = (v5_x * scale_z, v5_y * scale_z, v5_z * scale_z)

    basis_dir_x = normalize_vector(basis_x)
    basis_dir_y = normalize_vector(basis_y)
    basis_dir_z = normalize_vector(basis_z)

    cb1_84 = tuple(cb1_words[84 * 4 : 84 * 4 + 3])
    cb1_85 = tuple(cb1_words[85 * 4 : 85 * 4 + 3])
    t2_offset = tuple(as_f32(value) for value in section_entry[:3])
    t1_offset = (as_f32(r0_x_raw), as_f32(r0_z_raw), 0.0)

    offset = (
        cb1_84[0] + cb1_85[0] + t2_offset[0] + t1_offset[0],
        cb1_84[1] + cb1_85[1] + t2_offset[1] + t1_offset[1],
        cb1_84[2] + cb1_85[2] + t2_offset[2] + t1_offset[2],
    )

    return {
        "basis_x": basis_x,
        "basis_y": basis_y,
        "basis_z": basis_z,
        "basis_dir_x": basis_dir_x,
        "basis_dir_y": basis_dir_y,
        "basis_dir_z": basis_dir_z,
        "offset": offset,
        "secondary_left_index": secondary_left_index,
        "secondary_right_index": secondary_right_index,
    }


def apply_basis(
    positions: list[tuple[float, float, float]],
    basis_x: tuple[float, float, float],
    basis_y: tuple[float, float, float],
    basis_z: tuple[float, float, float],
    offset: tuple[float, float, float],
) -> list[tuple[float, float, float]]:
    out: list[tuple[float, float, float]] = []
    for x, y, z in positions:
        out.append(
            (
                offset[0] + x * basis_x[0] + y * basis_y[0] + z * basis_z[0],
                offset[1] + x * basis_x[1] + y * basis_y[1] + z * basis_z[1],
                offset[2] + x * basis_x[2] + y * basis_y[2] + z * basis_z[2],
            )
        )
    return out


def normalize_vector(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = vector
    length_sq = x * x + y * y + z * z
    if length_sq <= 1.0e-20:
        return (0.0, 0.0, 0.0)
    inv_length = 1.0 / (length_sq ** 0.5)
    return (x * inv_length, y * inv_length, z * inv_length)


def apply_t5_shell(
    base_positions: list[tuple[float, float, float]],
    basis_dir_x: tuple[float, float, float],
    basis_dir_y: tuple[float, float, float],
    basis_dir_z: tuple[float, float, float],
    t5_records: list[tuple[float, float, float, float]],
    vertex_id_offset: int,
    shell_scale: float,
) -> list[tuple[float, float, float]]:
    out: list[tuple[float, float, float]] = []
    for vertex_index, base in enumerate(base_positions):
        t5_index = ((vertex_index + vertex_id_offset) << 1) | 1
        if t5_index >= len(t5_records):
            t5 = (0.0, 0.0, 0.0, 0.0)
        else:
            t5 = t5_records[t5_index]

        direction = (
            t5[0] * basis_dir_x[0] + t5[1] * basis_dir_y[0] + t5[2] * basis_dir_z[0],
            t5[0] * basis_dir_x[1] + t5[1] * basis_dir_y[1] + t5[2] * basis_dir_z[1],
            t5[0] * basis_dir_x[2] + t5[1] * basis_dir_y[2] + t5[2] * basis_dir_z[2],
        )
        out.append(
            (
                base[0] + shell_scale * direction[0],
                base[1] + shell_scale * direction[1],
                base[2] + shell_scale * direction[2],
            )
        )
    return out


def import_basis_applied_object(
    *,
    name: str,
    collection: bpy.types.Collection,
    spec: dict[str, Path],
):
    indices = read_u16_indices(spec["ib_path"])
    raw_positions = read_float3_positions(spec["vb0_path"])
    sliced_indices = slice_indices(indices, spec.get("index_start", 0), spec.get("index_count"))
    triangles = build_triangles(sliced_indices, len(raw_positions))

    vb1_words = read_u32_words(spec["vb1_path"])
    cb1_words = read_f32_words(spec["cb1_path"])
    cb2_words = read_u32_words(spec["cb2_path"])
    t0_records = grouped(read_u32_words(spec["t0_path"]), 4)
    t1_records = grouped(read_u32_words(spec["t1_path"]), 4)
    t2_records = grouped(read_u32_words(spec["t2_path"]), 4)

    selector_info = decode_section_selector(vb1_words, t0_records, t1_records, cb2_words)
    basis_info = decode_basis(
        section_index=selector_info["section_index"],
        t1_records=t1_records,
        t2_records=t2_records,
        cb1_words=cb1_words,
        selector_info=selector_info,
    )
    basis_positions = apply_basis(
        raw_positions,
        basis_x=basis_info["basis_x"],
        basis_y=basis_info["basis_y"],
        basis_z=basis_info["basis_z"],
        offset=basis_info["offset"],
    )

    obj = create_mesh_object(
        name=name,
        collection=collection,
        positions=basis_positions,
        triangles=triangles,
    )
    obj["frameanalysis_replay"] = "9d62_basis_applied"
    obj["frameanalysis_section_index"] = selector_info["section_index"]
    obj["frameanalysis_basis_x"] = list(basis_info["basis_x"])
    obj["frameanalysis_basis_y"] = list(basis_info["basis_y"])
    obj["frameanalysis_basis_z"] = list(basis_info["basis_z"])
    obj["frameanalysis_offset"] = list(basis_info["offset"])
    obj["frameanalysis_vertex_count"] = len(basis_positions)
    obj["frameanalysis_triangle_count"] = len(triangles)
    obj["frameanalysis_index_start"] = spec.get("index_start", 0)
    obj["frameanalysis_index_count"] = -1 if spec.get("index_count") is None else spec["index_count"]
    return obj


def import_t5_shell_object(
    *,
    name: str,
    collection: bpy.types.Collection,
    spec: dict[str, Path],
    shell_scale: float,
):
    indices = read_u16_indices(spec["ib_path"])
    raw_positions = read_float3_positions(spec["vb0_path"])
    sliced_indices = slice_indices(indices, spec.get("index_start", 0), spec.get("index_count"))
    triangles = build_triangles(sliced_indices, len(raw_positions))

    vb1_words = read_u32_words(spec["vb1_path"])
    cb1_words = read_f32_words(spec["cb1_path"])
    cb2_words = read_u32_words(spec["cb2_path"])
    cb3_words = read_u32_words(spec["cb3_path"])
    t0_records = grouped(read_u32_words(spec["t0_path"]), 4)
    t1_records = grouped(read_u32_words(spec["t1_path"]), 4)
    t2_records = grouped(read_u32_words(spec["t2_path"]), 4)
    t5_records = read_snorm4_words(spec["t5_path"])

    selector_info = decode_section_selector(vb1_words, t0_records, t1_records, cb2_words)
    basis_info = decode_basis(
        section_index=selector_info["section_index"],
        t1_records=t1_records,
        t2_records=t2_records,
        cb1_words=cb1_words,
        selector_info=selector_info,
    )
    basis_positions = apply_basis(
        raw_positions,
        basis_x=basis_info["basis_x"],
        basis_y=basis_info["basis_y"],
        basis_z=basis_info["basis_z"],
        offset=basis_info["offset"],
    )

    vertex_id_offset = cb3_words[3]
    shell_positions = apply_t5_shell(
        basis_positions,
        basis_dir_x=basis_info["basis_dir_x"],
        basis_dir_y=basis_info["basis_dir_y"],
        basis_dir_z=basis_info["basis_dir_z"],
        t5_records=t5_records,
        vertex_id_offset=vertex_id_offset,
        shell_scale=shell_scale,
    )

    obj = create_mesh_object(
        name=name,
        collection=collection,
        positions=shell_positions,
        triangles=triangles,
    )
    obj["frameanalysis_replay"] = "9d62_t5_direction_shell"
    obj["frameanalysis_section_index"] = selector_info["section_index"]
    obj["frameanalysis_t5_shell_scale"] = shell_scale
    obj["frameanalysis_vertex_id_offset"] = int(vertex_id_offset)
    obj["frameanalysis_basis_dir_x"] = list(basis_info["basis_dir_x"])
    obj["frameanalysis_basis_dir_y"] = list(basis_info["basis_dir_y"])
    obj["frameanalysis_basis_dir_z"] = list(basis_info["basis_dir_z"])
    obj["frameanalysis_vertex_count"] = len(shell_positions)
    obj["frameanalysis_triangle_count"] = len(triangles)
    obj["frameanalysis_index_start"] = spec.get("index_start", 0)
    obj["frameanalysis_index_count"] = -1 if spec.get("index_count") is None else spec["index_count"]
    return obj


def load_image_pixels(path: Path) -> tuple[bpy.types.Image, tuple[int, int], list[float]]:
    image_path = str(path)
    image = bpy.data.images.get(path.name)
    if image is None or bpy.path.abspath(image.filepath) != image_path:
        image = bpy.data.images.load(image_path, check_existing=True)
    image.colorspace_settings.name = "Non-Color"
    image.reload()
    size = tuple(image.size)
    pixels = list(image.pixels[:])
    return image, size, pixels


def sample_image_rgba(
    *,
    pixels: list[float],
    width: int,
    height: int,
    u: float,
    v: float,
    mode: str,
) -> tuple[float, float, float, float]:
    if mode == "wrap":
        u = u - int(u)
        if u < 0.0:
            u += 1.0
        v = v - int(v)
        if v < 0.0:
            v += 1.0
    else:
        u = min(1.0, max(0.0, u))
        v = min(1.0, max(0.0, v))

    x = u * (width - 1)
    y = v * (height - 1)

    x0 = int(x)
    y0 = int(y)
    x1 = min(width - 1, x0 + 1)
    y1 = min(height - 1, y0 + 1)
    tx = x - x0
    ty = y - y0

    def pixel(px: int, py: int) -> tuple[float, float, float, float]:
        base = (py * width + px) * 4
        return (
            pixels[base + 0],
            pixels[base + 1],
            pixels[base + 2],
            pixels[base + 3],
        )

    p00 = pixel(x0, y0)
    p10 = pixel(x1, y0)
    p01 = pixel(x0, y1)
    p11 = pixel(x1, y1)

    def lerp(a: float, b: float, t: float) -> float:
        return a + (b - a) * t

    top = tuple(lerp(a, b, tx) for a, b in zip(p00, p10))
    bottom = tuple(lerp(a, b, tx) for a, b in zip(p01, p11))
    return tuple(lerp(a, b, ty) for a, b in zip(top, bottom))


def decode_t6_context(
    *,
    section_index: int,
    t2_records: list[tuple[int, ...]],
    cb1_words: list[float],
    cb5_words: list[float],
) -> dict[str, object]:
    r6 = [as_f32(value) for value in t2_records[section_index * 44 + 1][:3]]
    r11 = [as_f32(value) for value in t2_records[section_index * 44 + 2][:3]]
    r12 = [as_f32(value) for value in t2_records[section_index * 44 + 3][:3]]
    r14 = [as_f32(value) for value in t2_records[section_index * 44 + 4][:3]]
    r8 = [as_f32(value) for value in t2_records[section_index * 44 + 5]]
    r9 = [as_f32(value) for value in t2_records[section_index * 44 + 6]]
    r15 = [as_f32(value) for value in t2_records[section_index * 44 + 7]]
    clamp_radius = as_f32(t2_records[section_index * 44 + 32][0])

    r6_rounded = [round(component / 2097152.0) for component in r6]
    r4_rem = [-(component - rounded * (-2097152.0)) for component, rounded in zip(r6, r6_rounded)]

    coeff5 = (r8[0], r9[0], r15[0])
    coeff13 = (r8[1], r9[1], r15[1])
    coeff16 = (r8[2], r9[2], r15[2])

    r17 = (
        r4_rem[0] * r8[0] + r4_rem[1] * r8[1] + r4_rem[2] * r8[2] + r8[3],
        r4_rem[0] * r9[0] + r4_rem[1] * r9[1] + r4_rem[2] * r9[2] + r9[3],
        r4_rem[0] * r15[0] + r4_rem[1] * r15[1] + r4_rem[2] * r15[2] + r15[3],
    )

    cb5_0 = tuple(cb5_words[0:4])
    cb5_48 = tuple(cb5_words[48 * 4 : 48 * 4 + 4])
    scale_tex = (
        cb5_0[0] * r11[0] + cb5_0[1] * r11[1] + cb5_0[2] * r11[2],
        cb5_0[0] * r12[0] + cb5_0[1] * r12[1] + cb5_0[2] * r12[2],
        cb5_0[0] * r14[0] + cb5_0[1] * r14[1] + cb5_0[2] * r14[2],
    )
    scale_tex_dir = normalize_vector(scale_tex)

    frac_seed = cb1_words[163 * 4 + 2] / 10.0
    frac_part = abs(frac_seed) - int(abs(frac_seed))
    signed_frac = frac_part if frac_seed >= 0.0 else -frac_part
    add_scalar = 30.0 * signed_frac

    return {
        "cb1_121": tuple(cb1_words[121 * 4 : 121 * 4 + 3]),
        "cb1_124": tuple(cb1_words[124 * 4 : 124 * 4 + 3]),
        "cb5_0": cb5_0,
        "cb5_48": cb5_48,
        "coeff5": coeff5,
        "coeff13": coeff13,
        "coeff16": coeff16,
        "r17": r17,
        "scale_tex": scale_tex,
        "scale_tex_dir": scale_tex_dir,
        "add_scalar": add_scalar,
        "clamp_radius": clamp_radius,
    }


def apply_t6_displacement(
    *,
    base_positions: list[tuple[float, float, float]],
    basis_dir_x: tuple[float, float, float],
    basis_dir_y: tuple[float, float, float],
    basis_dir_z: tuple[float, float, float],
    t5_records: list[tuple[float, float, float, float]],
    vertex_id_offset: int,
    t6_context: dict[str, object],
    t6_pixels: list[float],
    t6_width: int,
    t6_height: int,
    sample_mode: str,
) -> list[tuple[float, float, float]]:
    out: list[tuple[float, float, float]] = []

    cb1_124 = t6_context["cb1_124"]
    cb1_121 = t6_context["cb1_121"]
    coeff5 = t6_context["coeff5"]
    coeff13 = t6_context["coeff13"]
    coeff16 = t6_context["coeff16"]
    r17 = t6_context["r17"]
    scale_tex = t6_context["scale_tex"]
    scale_tex_dir = t6_context["scale_tex_dir"]
    add_scalar = t6_context["add_scalar"]
    cb5_0 = t6_context["cb5_0"]
    cb5_48 = t6_context["cb5_48"]
    clamp_radius = t6_context["clamp_radius"]

    for vertex_index, base in enumerate(base_positions):
        t5_index = ((vertex_index + vertex_id_offset) << 1) | 1
        if t5_index >= len(t5_records):
            t5 = (0.0, 0.0, 0.0, 0.0)
        else:
            t5 = t5_records[t5_index]

        direction = (
            t5[0] * basis_dir_x[0] + t5[1] * basis_dir_y[0] + t5[2] * basis_dir_z[0],
            t5[0] * basis_dir_x[1] + t5[1] * basis_dir_y[1] + t5[2] * basis_dir_z[1],
            t5[0] * basis_dir_x[2] + t5[1] * basis_dir_y[2] + t5[2] * basis_dir_z[2],
        )

        atten = direction[0] * scale_tex_dir[0] + direction[1] * scale_tex_dir[1] + direction[2] * scale_tex_dir[2]
        atten = (atten - cb5_0[3]) / (1.00100005 - cb5_0[3])
        atten = min(1.0, max(0.0, atten))
        atten = (3.0 - 2.0 * atten) * atten * atten

        r10 = (
            base[0] - cb1_124[0],
            base[1] - cb1_124[1],
            base[2] - cb1_124[2],
        )

        t2_point = (
            cb5_48[2] * (add_scalar + r10[0]),
            cb5_48[2] * (add_scalar + r10[1]),
            cb5_48[2] * (add_scalar + r10[2]),
        )

        t3_point = (
            t2_point[0] * coeff5[0] + t2_point[1] * coeff13[0] + t2_point[2] * coeff16[0] + r17[0],
            t2_point[0] * coeff5[1] + t2_point[1] * coeff13[1] + t2_point[2] * coeff16[1] + r17[1],
            t2_point[0] * coeff5[2] + t2_point[1] * coeff13[2] + t2_point[2] * coeff16[2] + r17[2],
        )

        sample_u = t3_point[0]
        sample_v = t3_point[1] + 0.3 * t3_point[2]
        sampled = sample_image_rgba(
            pixels=t6_pixels,
            width=t6_width,
            height=t6_height,
            u=sample_u,
            v=sample_v,
            mode=sample_mode,
        )

        displacement = (
            scale_tex[0] * sampled[0] * atten,
            scale_tex[1] * sampled[1] * atten,
            scale_tex[2] * sampled[2] * atten,
        )

        if clamp_radius > 0.0:
            displacement = (
                max(-clamp_radius, min(clamp_radius, displacement[0])),
                max(-clamp_radius, min(clamp_radius, displacement[1])),
                max(-clamp_radius, min(clamp_radius, displacement[2])),
            )

        out.append(
            (
                base[0] + displacement[0],
                base[1] + displacement[1],
                base[2] + displacement[2],
            )
        )

    return out


def import_t6_displacement_object(
    *,
    name: str,
    collection: bpy.types.Collection,
    spec: dict[str, Path],
    sample_mode: str,
):
    indices = read_u16_indices(spec["ib_path"])
    raw_positions = read_float3_positions(spec["vb0_path"])
    sliced_indices = slice_indices(indices, spec.get("index_start", 0), spec.get("index_count"))
    triangles = build_triangles(sliced_indices, len(raw_positions))

    vb1_words = read_u32_words(spec["vb1_path"])
    cb1_words = read_f32_words(spec["cb1_path"])
    cb2_words = read_u32_words(spec["cb2_path"])
    cb3_words = read_u32_words(spec["cb3_path"])
    cb5_words = read_f32_words(spec["cb5_path"])
    t0_records = grouped(read_u32_words(spec["t0_path"]), 4)
    t1_records = grouped(read_u32_words(spec["t1_path"]), 4)
    t2_records = grouped(read_u32_words(spec["t2_path"]), 4)
    t5_records = read_snorm4_words(spec["t5_path"])

    selector_info = decode_section_selector(vb1_words, t0_records, t1_records, cb2_words)
    basis_info = decode_basis(
        section_index=selector_info["section_index"],
        t1_records=t1_records,
        t2_records=t2_records,
        cb1_words=cb1_words,
        selector_info=selector_info,
    )
    basis_positions = apply_basis(
        raw_positions,
        basis_x=basis_info["basis_x"],
        basis_y=basis_info["basis_y"],
        basis_z=basis_info["basis_z"],
        offset=basis_info["offset"],
    )

    _, image_size, image_pixels = load_image_pixels(spec["t6_path"])
    t6_context = decode_t6_context(
        section_index=selector_info["section_index"],
        t2_records=t2_records,
        cb1_words=cb1_words,
        cb5_words=cb5_words,
    )
    vertex_id_offset = cb3_words[3]

    displaced_positions = apply_t6_displacement(
        base_positions=basis_positions,
        basis_dir_x=basis_info["basis_dir_x"],
        basis_dir_y=basis_info["basis_dir_y"],
        basis_dir_z=basis_info["basis_dir_z"],
        t5_records=t5_records,
        vertex_id_offset=vertex_id_offset,
        t6_context=t6_context,
        t6_pixels=image_pixels,
        t6_width=image_size[0],
        t6_height=image_size[1],
        sample_mode=sample_mode,
    )

    obj = create_mesh_object(
        name=name,
        collection=collection,
        positions=displaced_positions,
        triangles=triangles,
    )
    obj["frameanalysis_replay"] = "9d62_t6_displacement_shell_approx"
    obj["frameanalysis_section_index"] = selector_info["section_index"]
    obj["frameanalysis_vertex_id_offset"] = int(vertex_id_offset)
    obj["frameanalysis_t6_sample_mode"] = sample_mode
    obj["frameanalysis_vertex_count"] = len(displaced_positions)
    obj["frameanalysis_triangle_count"] = len(triangles)
    obj["frameanalysis_index_start"] = spec.get("index_start", 0)
    obj["frameanalysis_index_count"] = -1 if spec.get("index_count") is None else spec["index_count"]
    return obj


def main():
    collection = ensure_collection(COLLECTION_NAME)
    created = []

    for spec in IMPORT_SPECS:
        obj = import_mesh_object(
            name=spec["name"],
            collection=collection,
            ib_path=Path(spec["ib_path"]),
            position_path=Path(spec["position_path"]),
            index_start=spec.get("index_start", 0),
            index_count=spec.get("index_count"),
        )
        created.append(obj.name)

    replay_obj = import_basis_applied_object(
        name=REPLAY_9D62_SPEC["name"],
        collection=collection,
        spec=REPLAY_9D62_SPEC,
    )
    created.append(replay_obj.name)

    original_replay_obj = import_basis_applied_object(
        name=ORIGINAL_REPLAY_9D62_SPEC["name"],
        collection=collection,
        spec=ORIGINAL_REPLAY_9D62_SPEC,
    )
    created.append(original_replay_obj.name)

    for spec in REPLAY_T5_SPECS:
        t5_obj = import_t5_shell_object(
            name=spec["name"],
            collection=collection,
            spec=spec,
            shell_scale=T5_SHELL_SCALE,
        )
        created.append(t5_obj.name)

    for spec in REPLAY_T6_SPECS:
        t6_obj = import_t6_displacement_object(
            name=spec["name"],
            collection=collection,
            spec=spec,
            sample_mode=T6_SAMPLE_MODE,
        )
        created.append(t6_obj.name)

    print("Imported objects:")
    for name in created:
        print(f"  - {name}")


if __name__ == "__main__":
    main()
