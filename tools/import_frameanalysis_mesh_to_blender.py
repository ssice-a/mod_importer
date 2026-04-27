"""
Replay the position-producing portion of the 9d62 depth VS inside Blender.

Usage:
1. Open Blender.
2. Open the Scripting workspace.
3. Load this file.
4. Adjust the frame paths if needed.
5. Run the script.

The script is self-contained:
- no external helper script is required
- custom and original draws are replayed by the same code path
- when NumPy is available in Blender, it uses a faster vectorized path
- otherwise it falls back to a pure Python reference interpreter

The replay only covers the VS logic that affects final depth position (`o0`).
It intentionally does not rebuild the other VS outputs.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import math
from pathlib import Path
import struct
from typing import Any

import bpy

try:
    import numpy as NP  # type: ignore[import-not-found]
except Exception:
    NP = None


CUSTOM_FRAME_DIR = Path(r"E:\yh\FrameAnalysis-2026-04-26-005519")
ORIGINAL_FRAME_DIR = Path(r"E:\yh\FrameAnalysis-2026-04-25-164449")
CUSTOM_DRAW_NAME = "000148"
ORIGINAL_DRAW_NAME = "000144"
COLLECTION_NAME = "fa_9d62_live_replay"

# Keep the default import small so Blender stays responsive.
ENABLED_LAYERS = {
    "raw_post_skin",
    "final_preclip",
}

# Optional:
# "pre_skin_t4"
# "basis_applied"
# "final_clip"
REPLAY_MODE = "auto"  # "auto" | "numpy" | "python"
T6_SAMPLE_MODE = "wrap"  # "wrap" | "clamp"
VALIDATION_SAMPLE_VERTICES = 64
VALIDATION_ABS_EPSILON = 1.0e-2
VALIDATION_REL_EPSILON = 1.0e-6

NP_REAL = NP.float64 if NP is not None else None


@dataclass(frozen=True)
class DrawSpec:
    label: str
    frame_dir: Path
    draw_name: str
    ib_path: Path
    vb0_path: Path
    vb1_path: Path
    cb0_path: Path
    cb1_path: Path
    cb2_path: Path
    cb3_path: Path
    cb5_path: Path
    t0_path: Path
    t1_path: Path
    t2_path: Path
    t3_path: Path
    t4_path: Path
    t5_path: Path
    t6_path: Path
    index_start: int
    index_count: int | None


@dataclass
class DrawReplayContext:
    draw: DrawSpec
    raw_positions: list[tuple[float, float, float]]
    raw_position_scalars: list[float]
    texcoord_scalars: list[float]
    t5_records: list[tuple[float, float, float, float]]
    indices: list[int]
    sliced_indices: list[int]
    triangles: list[tuple[int, int, int]]
    cb0_u32: list[int]
    cb1_f32: list[float]
    cb2_u32: list[int]
    cb3_u32: list[int]
    cb5_f32: list[float]
    t0_u32: list[int]
    t1_u32_records: list[tuple[int, int, int, int]]
    t2_u32_records: list[tuple[int, int, int, int]]
    t6_size: tuple[int, int]
    t6_pixels: list[float]
    t6_pixels_np: Any
    vertex_count: int


@dataclass
class SectionState:
    section_index: int
    selector_index: int
    initial_flag: bool
    has_section: bool
    gate_secondary: bool
    branch_enter: bool
    basis_x: tuple[float, float, float]
    basis_y: tuple[float, float, float]
    basis_z: tuple[float, float, float]
    basis_dir_x: tuple[float, float, float]
    basis_dir_y: tuple[float, float, float]
    basis_dir_z: tuple[float, float, float]
    basis_offset: tuple[float, float, float]
    t4_scalar_base: int
    t3_scalar_stride: int
    vertex_id_offset: int
    t5_record_offset: int
    scale_tex: tuple[float, float, float]
    scale_tex_dir: tuple[float, float, float]
    coeff5: tuple[float, float, float]
    coeff13: tuple[float, float, float]
    coeff16: tuple[float, float, float]
    r17: tuple[float, float, float]
    round_base: tuple[float, float, float]
    cb1_124: tuple[float, float, float]
    add_scalar: float
    clamp_radius: float
    cb0_x_nonzero: bool


@dataclass
class ReplayResult:
    draw: DrawSpec
    mode: str
    section_state: SectionState
    raw_post_skin: list[tuple[float, float, float]]
    pre_skin_t4: list[tuple[float, float, float]]
    basis_applied: list[tuple[float, float, float]]
    final_preclip: list[tuple[float, float, float]]
    final_clip_ndc: list[tuple[float, float, float]]
    clip_w: list[float]
    section_id: list[float]
    gate_main: list[float]
    gate_secondary: list[float]
    t4_branch_value: list[float]
    t6_atten: list[float]
    sample_u: list[float]
    sample_v: list[float]


BYTE_CACHE: dict[str, bytes] = {}
U16_CACHE: dict[str, list[int]] = {}
U32_CACHE: dict[str, list[int]] = {}
F32_CACHE: dict[str, list[float]] = {}
FLOAT3_CACHE: dict[str, list[tuple[float, float, float]]] = {}
SNORM4_CACHE: dict[str, list[tuple[float, float, float, float]]] = {}
IMAGE_CACHE: dict[str, tuple[tuple[int, int], list[float], Any]] = {}


def make_custom_draw() -> DrawSpec:
    draw = CUSTOM_DRAW_NAME
    frame = CUSTOM_FRAME_DIR
    return DrawSpec(
        label="custom",
        frame_dir=frame,
        draw_name=draw,
        ib_path=frame / f"{draw}-ib-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        vb0_path=frame / f"{draw}-vb0-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        vb1_path=frame / f"{draw}-vb1=1236657b-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        cb0_path=frame / f"{draw}-vs-cb0=7816b819-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        cb1_path=frame / f"{draw}-vs-cb1=6e5a5274-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        cb2_path=frame / f"{draw}-vs-cb2=0c8934aa-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        cb3_path=frame / f"{draw}-vs-cb3=aede39f6-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        cb5_path=frame / f"{draw}-vs-cb5=2643e0c2-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        t0_path=frame / f"{draw}-vs-t0=81189244-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        t1_path=frame / f"{draw}-vs-t1=135e128a-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        t2_path=frame / f"{draw}-vs-t2=ab75cfe2-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        t3_path=frame / f"{draw}-vs-t3-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        t4_path=frame / f"{draw}-vs-t4-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        t5_path=frame / f"{draw}-vs-t5-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        t6_path=frame / f"{draw}-vs-t6=d77b480e-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.dds",
        index_start=0,
        index_count=None,
    )


def make_original_draw() -> DrawSpec:
    draw = ORIGINAL_DRAW_NAME
    frame = ORIGINAL_FRAME_DIR
    return DrawSpec(
        label="original",
        frame_dir=frame,
        draw_name=draw,
        ib_path=frame / f"{draw}-ib=83527398-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        vb0_path=frame / f"{draw}-vb0=b1c65387-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        vb1_path=frame / f"{draw}-vb1=1236657b-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        cb0_path=frame / f"{draw}-vs-cb0=7816b819-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        cb1_path=frame / f"{draw}-vs-cb1=6e5a5274-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        cb2_path=frame / f"{draw}-vs-cb2=0c8934aa-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        cb3_path=frame / f"{draw}-vs-cb3=743b92ec-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        cb5_path=frame / f"{draw}-vs-cb5=2643e0c2-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        t0_path=frame / f"{draw}-vs-t0=81189244-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        t1_path=frame / f"{draw}-vs-t1=135e128a-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        t2_path=frame / f"{draw}-vs-t2=ab75cfe2-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        t3_path=frame / f"{draw}-vs-t3=ad3c9baf-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        t4_path=frame / f"{draw}-vs-t4=7fec12c0-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        t5_path=frame / f"{draw}-vs-t5=dc7f2baf-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.buf",
        t6_path=frame / f"{draw}-vs-t6=d77b480e-vs=9d62ac15f0b2cf93-ps=f1a15881ff8cc63c.dds",
        index_start=29448,
        index_count=115740,
    )


def as_f32(value: int) -> float:
    return struct.unpack("<f", struct.pack("<I", value & 0xFFFFFFFF))[0]


def as_i32(value: int) -> int:
    return struct.unpack("<i", struct.pack("<I", value & 0xFFFFFFFF))[0]


def sat(value: float) -> float:
    return 0.0 if value <= 0.0 else 1.0 if value >= 1.0 else value


def hlsl_round_scalar(value: float) -> float:
    return float(math.floor(value + 0.5) if value >= 0.0 else math.ceil(value - 0.5))


def normalize_vector(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = vector
    length_sq = x * x + y * y + z * z
    if length_sq <= 1.0e-20:
        return (0.0, 0.0, 0.0)
    inv_length = 1.0 / math.sqrt(length_sq)
    return (x * inv_length, y * inv_length, z * inv_length)


def dot3(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def bytes_for(path: Path) -> bytes:
    key = str(path)
    cached = BYTE_CACHE.get(key)
    if cached is None:
        cached = path.read_bytes()
        BYTE_CACHE[key] = cached
    return cached


def u16_words_for(path: Path) -> list[int]:
    key = str(path)
    cached = U16_CACHE.get(key)
    if cached is None:
        raw = bytes_for(path)
        if len(raw) % 2 != 0:
            raise ValueError(f"{path} size is not divisible by 2: {len(raw)}")
        cached = list(struct.unpack(f"<{len(raw) // 2}H", raw))
        U16_CACHE[key] = cached
    return cached


def u32_words_for(path: Path) -> list[int]:
    key = str(path)
    cached = U32_CACHE.get(key)
    if cached is None:
        raw = bytes_for(path)
        if len(raw) % 4 != 0:
            raise ValueError(f"{path} size is not divisible by 4: {len(raw)}")
        cached = list(struct.unpack(f"<{len(raw) // 4}I", raw))
        U32_CACHE[key] = cached
    return cached


def f32_words_for(path: Path) -> list[float]:
    key = str(path)
    cached = F32_CACHE.get(key)
    if cached is None:
        raw = bytes_for(path)
        if len(raw) % 4 != 0:
            raise ValueError(f"{path} size is not divisible by 4: {len(raw)}")
        cached = list(struct.unpack(f"<{len(raw) // 4}f", raw))
        F32_CACHE[key] = cached
    return cached


def float3_positions_for(path: Path) -> list[tuple[float, float, float]]:
    key = str(path)
    cached = FLOAT3_CACHE.get(key)
    if cached is None:
        values = f32_words_for(path)
        if len(values) % 3 != 0:
            raise ValueError(f"{path} float count is not divisible by 3: {len(values)}")
        cached = [
            (values[index], values[index + 1], values[index + 2])
            for index in range(0, len(values), 3)
        ]
        FLOAT3_CACHE[key] = cached
    return cached


def snorm4_records_for(path: Path) -> list[tuple[float, float, float, float]]:
    key = str(path)
    cached = SNORM4_CACHE.get(key)
    if cached is None:
        raw = bytes_for(path)
        if len(raw) % 8 != 0:
            raise ValueError(f"{path} size is not divisible by 8: {len(raw)}")
        values = struct.unpack(f"<{len(raw) // 2}h", raw)
        records: list[tuple[float, float, float, float]] = []
        for index in range(0, len(values), 4):
            block = values[index : index + 4]
            records.append(tuple(max(-1.0, component / 32767.0) for component in block))
        cached = records
        SNORM4_CACHE[key] = cached
    return cached


def grouped_u32(words: list[int], width: int) -> list[tuple[int, int, int, int]]:
    if len(words) % width != 0:
        raise ValueError(f"Word count {len(words)} is not divisible by {width}")
    return [tuple(words[index : index + width]) for index in range(0, len(words), width)]


def load_image_pixels(path: Path) -> tuple[tuple[int, int], list[float], Any]:
    key = str(path)
    cached = IMAGE_CACHE.get(key)
    if cached is not None:
        return cached

    image_path = str(path)
    image = bpy.data.images.get(path.name)
    if image is None or bpy.path.abspath(image.filepath) != image_path:
        image = bpy.data.images.load(image_path, check_existing=True)
    image.colorspace_settings.name = "Non-Color"
    image.reload()
    size = (int(image.size[0]), int(image.size[1]))
    pixels = list(image.pixels[:])

    pixels_np = None
    if NP is not None:
        pixels_np = NP.asarray(pixels, dtype=NP_REAL).reshape(size[1], size[0], 4)

    cached = (size, pixels, pixels_np)
    IMAGE_CACHE[key] = cached
    return cached


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


def build_triangles(indices: list[int], vertex_count: int) -> list[tuple[int, int, int]]:
    if len(indices) % 3 != 0:
        raise ValueError(f"Index buffer length is not divisible by 3: {len(indices)}")
    if indices and max(indices) >= vertex_count:
        raise ValueError(f"Index buffer references vertex {max(indices)}, but only {vertex_count} exist.")
    return [
        (indices[index], indices[index + 1], indices[index + 2])
        for index in range(0, len(indices), 3)
    ]


def sample_image_rgba_python(
    *,
    pixels: list[float],
    width: int,
    height: int,
    u: float,
    v: float,
    mode: str,
) -> tuple[float, float, float, float]:
    if mode == "wrap":
        u = u - math.floor(u)
        v = v - math.floor(v)
    else:
        u = 0.0 if u < 0.0 else 1.0 if u > 1.0 else u
        v = 0.0 if v < 0.0 else 1.0 if v > 1.0 else v

    x = u * (width - 1)
    y = v * (height - 1)
    x0 = int(math.floor(x))
    y0 = int(math.floor(y))
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


def create_mesh_object(
    *,
    name: str,
    collection: bpy.types.Collection,
    positions: list[tuple[float, float, float]],
    indices: list[int],
) -> bpy.types.Object:
    remove_existing_object(name)

    mesh = bpy.data.meshes.new(name)
    vertex_count = len(positions)
    loop_count = len(indices)
    tri_count = loop_count // 3

    mesh.vertices.add(vertex_count)
    mesh.loops.add(loop_count)
    mesh.polygons.add(tri_count)

    vertex_values = array("f")
    for x, y, z in positions:
        vertex_values.extend((x, y, z))
    mesh.vertices.foreach_set("co", vertex_values)
    mesh.loops.foreach_set("vertex_index", indices)
    mesh.polygons.foreach_set("loop_start", list(range(0, loop_count, 3)))
    mesh.polygons.foreach_set("loop_total", [3] * tri_count)
    mesh.polygons.foreach_set("use_smooth", [True] * tri_count)
    mesh.update(calc_edges=True)
    mesh.validate(verbose=False)

    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    return obj


def attach_point_float_attribute(mesh: bpy.types.Mesh, name: str, values: list[float]):
    if len(values) != len(mesh.vertices):
        raise ValueError(f"Attribute {name} length {len(values)} != vertex count {len(mesh.vertices)}")
    attribute = mesh.attributes.new(name=name, type="FLOAT", domain="POINT")
    attribute.data.foreach_set("value", values)


def build_draw_context(draw: DrawSpec) -> DrawReplayContext:
    indices = u16_words_for(draw.ib_path)
    sliced_indices = slice_indices(indices, draw.index_start, draw.index_count)
    raw_positions = float3_positions_for(draw.vb0_path)
    triangles = build_triangles(sliced_indices, len(raw_positions))
    t6_size, t6_pixels, t6_pixels_np = load_image_pixels(draw.t6_path)

    return DrawReplayContext(
        draw=draw,
        raw_positions=raw_positions,
        raw_position_scalars=f32_words_for(draw.t4_path),
        texcoord_scalars=f32_words_for(draw.t3_path),
        t5_records=snorm4_records_for(draw.t5_path),
        indices=indices,
        sliced_indices=sliced_indices,
        triangles=triangles,
        cb0_u32=u32_words_for(draw.cb0_path),
        cb1_f32=f32_words_for(draw.cb1_path),
        cb2_u32=u32_words_for(draw.cb2_path),
        cb3_u32=u32_words_for(draw.cb3_path),
        cb5_f32=f32_words_for(draw.cb5_path),
        t0_u32=u32_words_for(draw.t0_path),
        t1_u32_records=grouped_u32(u32_words_for(draw.t1_path), 4),
        t2_u32_records=grouped_u32(u32_words_for(draw.t2_path), 4),
        t6_size=t6_size,
        t6_pixels=t6_pixels,
        t6_pixels_np=t6_pixels_np,
        vertex_count=len(raw_positions),
    )


def decode_basis_state(
    section_index: int,
    selector_index: int,
    initial_flag: bool,
    has_section: bool,
    ctx: DrawReplayContext,
) -> SectionState:
    cb1 = ctx.cb1_f32
    cb2 = ctx.cb2_u32
    cb3 = ctx.cb3_u32
    t1 = ctx.t1_u32_records
    t2 = ctx.t2_u32_records

    basis_x = (0.0, 0.0, 0.0)
    basis_y = (0.0, 0.0, 0.0)
    basis_z = (0.0, 0.0, 0.0)
    basis_dir_x = (0.0, 0.0, 0.0)
    basis_dir_y = (0.0, 0.0, 0.0)
    basis_dir_z = (0.0, 0.0, 0.0)
    basis_offset = (0.0, 0.0, 0.0)
    scale_tex = (0.0, 0.0, 0.0)
    scale_tex_dir = (0.0, 0.0, 0.0)
    coeff5 = (0.0, 0.0, 0.0)
    coeff13 = (0.0, 0.0, 0.0)
    coeff16 = (0.0, 0.0, 0.0)
    r17 = (0.0, 0.0, 0.0)
    round_base = (0.0, 0.0, 0.0)
    clamp_radius = 0.0
    gate_secondary = True
    branch_enter = False

    shift = cb2[0]
    stride = cb2[2]
    page = selector_index // stride if stride != 0 else 0
    slot = selector_index - page * stride

    if has_section:
        base = section_index * 44
        section_entry = t2[base + 1]

        secondary_left_index = slot + stride * page + (1 << shift)
        secondary_right_index = slot + stride * page + (2 << shift)
        left_entry = t1[secondary_left_index]
        right_entry = t1[secondary_right_index]

        r2_x, r2_y, r2_z, r2_w = left_entry
        raw_offset = (
            as_f32(right_entry[0]),
            as_f32(right_entry[1]),
            as_f32(right_entry[2]),
        )

        # The packed basis record mixes quaternion and scale words across z/w;
        # mirror the shader swizzles exactly instead of assuming simple hi/lo pairs.
        quat_hi_x_u = (r2_x >> 16) & 0xFFFF
        scale_hi_y_u = (r2_z >> 16) & 0xFFFF
        scale_exp_u = (r2_w >> 16) & 0xFFFF
        quat_lo_x_u = r2_x & 0xFFFF
        quat_lo_y_u = r2_y & 0x7FFF
        quat_z_flag = r2_y & 0x8000
        scale_lo_x_u = r2_z & 0xFFFF
        scale_lo_z_u = r2_w & 0xFFFF

        qx = (-32768.0 + float(quat_lo_x_u)) * 3.05185094e-05
        qy = (-32768.0 + float(quat_hi_x_u)) * 3.05185094e-05
        qw = (-16384.0 + float(quat_lo_y_u)) * 4.3161006e-05

        v5_x = qx + qy
        v5_y = qx - qy
        v5_z = 2.0 - (abs(v5_x) + abs(v5_y))
        inv_length = 1.0 / math.sqrt(v5_x * v5_x + v5_y * v5_y + v5_z * v5_z)
        v5_x *= inv_length
        v5_y *= inv_length
        v5_z *= inv_length

        inv_one_plus_z = 1.0 / (1.0 + v5_z)
        tmp_y = (-v5_x * v5_y) * inv_one_plus_z
        r7_x = 1.0 - (v5_x * v5_x) * inv_one_plus_z
        r7_y = 1.0 - (v5_y * v5_y) * inv_one_plus_z
        r7_z = tmp_y
        r7_w = -v5_x

        alt = math.sqrt(max(0.0, 1.0 - qw * qw))
        if quat_z_flag != 0:
            left_scale = alt
            right_scale = qw
        else:
            left_scale = qw
            right_scale = alt

        rot_x = r7_x * left_scale + r7_z * right_scale
        rot_y = r7_w * left_scale + r7_y * right_scale
        rot_z = r7_z * left_scale + r7_w * right_scale

        ortho_x = v5_y * rot_z - v5_z * rot_y
        ortho_y = v5_z * rot_x - v5_x * rot_z
        ortho_z = v5_x * rot_y - v5_y * rot_x

        scale_bits = ((scale_exp_u << 23) + 0xF8800000) & 0xFFFFFFFF
        scale = as_f32(scale_bits)
        scale_x = (float(scale_lo_x_u) - 32768.0) * scale
        scale_y = (float(scale_hi_y_u) - 32768.0) * scale
        scale_z = (float(scale_lo_z_u) - 32768.0) * scale

        basis_x = (rot_x * scale_x, rot_y * scale_x, rot_z * scale_x)
        basis_y = (ortho_x * scale_y, ortho_y * scale_y, ortho_z * scale_y)
        basis_z = (v5_x * scale_z, v5_y * scale_z, v5_z * scale_z)
        basis_dir_x = normalize_vector(basis_x)
        basis_dir_y = normalize_vector(basis_y)
        basis_dir_z = normalize_vector(basis_z)

        section_offset = (
            as_f32(section_entry[0]),
            as_f32(section_entry[1]),
            as_f32(section_entry[2]),
        )
        cb1_84 = tuple(cb1[84 * 4 : 84 * 4 + 3])
        cb1_85 = tuple(cb1[85 * 4 : 85 * 4 + 3])
        basis_offset = (
            cb1_84[0] + cb1_85[0] + section_offset[0] + raw_offset[0],
            cb1_84[1] + cb1_85[1] + section_offset[1] + raw_offset[1],
            cb1_84[2] + cb1_85[2] + section_offset[2] + raw_offset[2],
        )

        section_flags_u32 = t2[base][0]
        t2_18 = (
            as_f32(t2[base + 18][0]),
            as_f32(t2[base + 18][1]),
            as_f32(t2[base + 18][2]),
        )
        t2_19 = (
            as_f32(t2[base + 19][0]),
            as_f32(t2[base + 19][1]),
            as_f32(t2[base + 19][2]),
        )
        center_vec = (
            cb1_84[0] + cb1_85[0] + t2_19[0] + 2097152.0 * t2_18[0],
            cb1_84[1] + cb1_85[1] + t2_19[1] + 2097152.0 * t2_18[1],
            cb1_84[2] + cb1_85[2] + t2_19[2] + 2097152.0 * t2_18[2],
        )
        radius_threshold = as_f32(t2[base + 31][2])
        radius_gate = (section_flags_u32 & 0x40000) != 0 and dot3(center_vec, center_vec) >= radius_threshold
        bit8000_clear = (section_flags_u32 & 0x8000) == 0
        gate_secondary = bit8000_clear or radius_gate
        branch_enter = initial_flag and not gate_secondary

        t2_2 = tuple(as_f32(value) for value in t2[base + 2][:3])
        t2_3 = tuple(as_f32(value) for value in t2[base + 3][:3])
        t2_4 = tuple(as_f32(value) for value in t2[base + 4][:3])
        t2_5 = tuple(as_f32(value) for value in t2[base + 5])
        t2_6 = tuple(as_f32(value) for value in t2[base + 6])
        t2_7 = tuple(as_f32(value) for value in t2[base + 7])

        coeff5 = (t2_5[0], t2_6[0], t2_7[0])
        coeff13 = (t2_5[1], t2_6[1], t2_7[1])
        coeff16 = (t2_5[2], t2_6[2], t2_7[2])

        section_origin = tuple(as_f32(value) for value in t2[base + 1][:3])
        round_origin = tuple(hlsl_round_scalar(component / 2097152.0) for component in section_origin)
        round_base = round_origin
        remainder = (
            2097152.0 * round_origin[0] - section_origin[0],
            2097152.0 * round_origin[1] - section_origin[1],
            2097152.0 * round_origin[2] - section_origin[2],
        )
        r17 = (
            remainder[0] * t2_5[0] + remainder[1] * t2_5[1] + remainder[2] * t2_5[2] + t2_5[3],
            remainder[0] * t2_6[0] + remainder[1] * t2_6[1] + remainder[2] * t2_6[2] + t2_6[3],
            remainder[0] * t2_7[0] + remainder[1] * t2_7[1] + remainder[2] * t2_7[2] + t2_7[3],
        )

        cb5 = ctx.cb5_f32
        scale_tex = (
            cb5[0] * t2_2[0] + cb5[1] * t2_2[1] + cb5[2] * t2_2[2],
            cb5[0] * t2_3[0] + cb5[1] * t2_3[1] + cb5[2] * t2_3[2],
            cb5[0] * t2_4[0] + cb5[1] * t2_4[1] + cb5[2] * t2_4[2],
        )
        scale_tex_dir = normalize_vector(scale_tex)

        frac_seed = cb1[163 * 4 + 2] / 10.0
        frac_part = abs(frac_seed) - math.floor(abs(frac_seed))
        signed_frac = frac_part if frac_seed >= 0.0 else -frac_part
        add_scalar = 30.0 * signed_frac
        clamp_radius = as_f32(t2[base + 32][0])
    else:
        add_scalar = 0.0

    return SectionState(
        section_index=section_index,
        selector_index=selector_index,
        initial_flag=initial_flag,
        has_section=has_section,
        gate_secondary=gate_secondary,
        branch_enter=branch_enter,
        basis_x=basis_x,
        basis_y=basis_y,
        basis_z=basis_z,
        basis_dir_x=basis_dir_x,
        basis_dir_y=basis_dir_y,
        basis_dir_z=basis_dir_z,
        basis_offset=basis_offset,
        t4_scalar_base=as_i32(cb3[4]) + 2,
        t3_scalar_stride=as_i32(cb3[1]),
        vertex_id_offset=as_i32(cb3[3]),
        t5_record_offset=as_i32(cb3[3]),
        scale_tex=scale_tex,
        scale_tex_dir=scale_tex_dir,
        coeff5=coeff5,
        coeff13=coeff13,
        coeff16=coeff16,
        r17=r17,
        round_base=round_base,
        cb1_124=tuple(cb1[124 * 4 : 124 * 4 + 3]),
        add_scalar=add_scalar,
        clamp_radius=clamp_radius,
        cb0_x_nonzero=ctx.cb0_u32[0] != 0,
    )


def decode_section_state(ctx: DrawReplayContext) -> SectionState:
    vb1_first = u32_words_for(ctx.draw.vb1_path)[0]
    selector_low = 0
    initial_nibble = 0

    if vb1_first & 0x80000000:
        lookup = (vb1_first & 0x7FFFFFFF) * 44
        selector_low = as_i32(ctx.t2_u32_records[lookup][1])
        initial_nibble = 1
    else:
        packed = ctx.t0_u32[vb1_first]
        selector_low = packed & 0x00FFFFFF
        initial_nibble = (packed >> 24) & 0xF

    shift = ctx.cb2_u32[0]
    mask = ctx.cb2_u32[1]
    stride = ctx.cb2_u32[2]
    page = selector_low >> shift
    slot = selector_low & mask
    selector_index = stride * page + slot
    selector_entry = ctx.t1_u32_records[selector_index][0]
    section_index = selector_entry & 0x000FFFFF
    selector_high = selector_entry >> 20
    initial_flag = bool(initial_nibble & 1)
    has_section = section_index != 0x000FFFFF and (selector_high & 1024) == 0

    return decode_basis_state(
        section_index=section_index,
        selector_index=selector_index,
        initial_flag=initial_flag,
        has_section=has_section,
        ctx=ctx,
    )


def final_clip_from_preclip(position: tuple[float, float, float], cb1: list[float]) -> tuple[float, float, float, float]:
    row0 = cb1[0:4]
    row1 = cb1[4:8]
    row2 = cb1[8:12]
    row3 = cb1[12:16]
    x, y, z = position
    return (
        row0[0] * x + row1[0] * y + row2[0] * z + row3[0],
        row0[1] * x + row1[1] * y + row2[1] * z + row3[1],
        row0[2] * x + row1[2] * y + row2[2] * z + row3[2],
        row0[3] * x + row1[3] * y + row2[3] * z + row3[3],
    )


def replay_reference(ctx: DrawReplayContext, section: SectionState) -> ReplayResult:
    raw_post_skin = ctx.raw_positions
    pre_skin_t4: list[tuple[float, float, float]] = []
    basis_applied: list[tuple[float, float, float]] = []
    final_preclip: list[tuple[float, float, float]] = []
    final_clip_ndc: list[tuple[float, float, float]] = []
    clip_w: list[float] = []
    section_id = [float(section.section_index)] * ctx.vertex_count
    gate_main = [1.0 if section.branch_enter else 0.0] * ctx.vertex_count
    gate_secondary = [1.0 if section.gate_secondary else 0.0] * ctx.vertex_count
    t4_branch_value: list[float] = []
    t6_atten: list[float] = []
    sample_u: list[float] = []
    sample_v: list[float] = []

    scale_tex_dir = section.scale_tex_dir
    scale_tex = section.scale_tex
    coeff5 = section.coeff5
    coeff13 = section.coeff13
    coeff16 = section.coeff16
    r17 = section.r17
    cb1_124 = section.cb1_124
    round_base = section.round_base
    cb5_48_z = ctx.cb5_f32[48 * 4 + 2]
    t6_width, t6_height = ctx.t6_size
    t6_pixels = ctx.t6_pixels

    for vertex_id, raw_position in enumerate(raw_post_skin):
        x, y, z = raw_position

        t4_value = ctx.raw_position_scalars[vertex_id * 3 + section.t4_scalar_base]
        t4_branch_value.append(t4_value)
        pre_skin_t4.append((ctx.raw_position_scalars[vertex_id * 3 + 0], ctx.raw_position_scalars[vertex_id * 3 + 1], ctx.raw_position_scalars[vertex_id * 3 + 2]))

        base_position = (
            section.basis_offset[0] + x * section.basis_x[0] + y * section.basis_y[0] + z * section.basis_z[0],
            section.basis_offset[1] + x * section.basis_x[1] + y * section.basis_y[1] + z * section.basis_z[1],
            section.basis_offset[2] + x * section.basis_x[2] + y * section.basis_y[2] + z * section.basis_z[2],
        )
        basis_applied.append(base_position)

        if section.branch_enter:
            t5_index = ((vertex_id + section.vertex_id_offset) << 1) | 1
            t5 = ctx.t5_records[t5_index] if 0 <= t5_index < len(ctx.t5_records) else (0.0, 0.0, 0.0, 0.0)
            direction = (
                t5[0] * section.basis_dir_x[0] + t5[1] * section.basis_dir_y[0] + t5[2] * section.basis_dir_z[0],
                t5[0] * section.basis_dir_x[1] + t5[1] * section.basis_dir_y[1] + t5[2] * section.basis_dir_z[1],
                t5[0] * section.basis_dir_x[2] + t5[1] * section.basis_dir_y[2] + t5[2] * section.basis_dir_z[2],
            )

            atten = sat((dot3(direction, scale_tex_dir) - ctx.cb5_f32[3]) / (1.00100005 - ctx.cb5_f32[3]))
            atten = (3.0 - 2.0 * atten) * atten * atten

            r10 = (
                base_position[0] - cb1_124[0],
                base_position[1] - cb1_124[1],
                base_position[2] - cb1_124[2],
            )
            work = (
                cb5_48_z * (section.add_scalar + r10[0]) + 2097152.0 * (cb5_48_z * ctx.cb1_f32[121 * 4 + 0] - round_base[0]),
                cb5_48_z * (section.add_scalar + r10[1]) + 2097152.0 * (cb5_48_z * ctx.cb1_f32[121 * 4 + 1] - round_base[1]),
                cb5_48_z * (section.add_scalar + r10[2]) + 2097152.0 * (cb5_48_z * ctx.cb1_f32[121 * 4 + 2] - round_base[2]),
            )
            sample_coord = (
                work[0] * coeff5[0] + work[1] * coeff13[0] + work[2] * coeff16[0] + r17[0],
                work[0] * coeff5[1] + work[1] * coeff13[1] + work[2] * coeff16[1] + r17[1],
                work[0] * coeff5[2] + work[1] * coeff13[2] + work[2] * coeff16[2] + r17[2],
            )

            u = sample_coord[0]
            v = sample_coord[1] + 0.300000012 * sample_coord[2]
            sampled = sample_image_rgba_python(
                pixels=t6_pixels,
                width=t6_width,
                height=t6_height,
                u=u,
                v=v,
                mode=T6_SAMPLE_MODE,
            )

            displacement = (
                scale_tex[0] * sampled[0] * atten,
                scale_tex[1] * sampled[1] * atten,
                scale_tex[2] * sampled[2] * atten,
            )
            if section.clamp_radius > 0.0:
                displacement = (
                    max(-section.clamp_radius, min(section.clamp_radius, displacement[0])),
                    max(-section.clamp_radius, min(section.clamp_radius, displacement[1])),
                    max(-section.clamp_radius, min(section.clamp_radius, displacement[2])),
                )

            final_position = (
                base_position[0] + displacement[0],
                base_position[1] + displacement[1],
                base_position[2] + displacement[2],
            )
            t6_atten.append(atten)
            sample_u.append(u)
            sample_v.append(v)
        else:
            final_position = base_position
            t6_atten.append(0.0)
            sample_u.append(0.0)
            sample_v.append(0.0)

        final_preclip.append(final_position)
        clip = final_clip_from_preclip(final_position, ctx.cb1_f32)
        w = clip[3]
        clip_w.append(w)
        inv_w = 0.0 if abs(w) <= 1.0e-20 else 1.0 / w
        final_clip_ndc.append((clip[0] * inv_w, clip[1] * inv_w, clip[2] * inv_w))

    return ReplayResult(
        draw=ctx.draw,
        mode="python",
        section_state=section,
        raw_post_skin=raw_post_skin,
        pre_skin_t4=pre_skin_t4,
        basis_applied=basis_applied,
        final_preclip=final_preclip,
        final_clip_ndc=final_clip_ndc,
        clip_w=clip_w,
        section_id=section_id,
        gate_main=gate_main,
        gate_secondary=gate_secondary,
        t4_branch_value=t4_branch_value,
        t6_atten=t6_atten,
        sample_u=sample_u,
        sample_v=sample_v,
    )


def sample_image_rgba_numpy(
    *,
    pixels_np: Any,
    width: int,
    height: int,
    u: Any,
    v: Any,
    mode: str,
) -> Any:
    if mode == "wrap":
        u = NP.mod(u, 1.0)
        v = NP.mod(v, 1.0)
    else:
        u = NP.clip(u, 0.0, 1.0)
        v = NP.clip(v, 0.0, 1.0)

    x = u * float(width - 1)
    y = v * float(height - 1)
    x0 = NP.floor(x).astype(NP.int32)
    y0 = NP.floor(y).astype(NP.int32)
    x1 = NP.minimum(x0 + 1, width - 1)
    y1 = NP.minimum(y0 + 1, height - 1)
    tx = (x - x0).astype(pixels_np.dtype)
    ty = (y - y0).astype(pixels_np.dtype)

    p00 = pixels_np[y0, x0]
    p10 = pixels_np[y0, x1]
    p01 = pixels_np[y1, x0]
    p11 = pixels_np[y1, x1]
    top = p00 + (p10 - p00) * tx[:, None]
    bottom = p01 + (p11 - p01) * tx[:, None]
    return top + (bottom - top) * ty[:, None]


def replay_numpy(ctx: DrawReplayContext, section: SectionState) -> ReplayResult:
    if NP is None:
        raise RuntimeError("NumPy is not available in this Blender environment.")

    raw = NP.asarray(ctx.raw_positions, dtype=NP_REAL)
    raw_post_skin = [tuple(map(float, point)) for point in raw.tolist()]

    basis_x = NP.asarray(section.basis_x, dtype=NP_REAL)
    basis_y = NP.asarray(section.basis_y, dtype=NP_REAL)
    basis_z = NP.asarray(section.basis_z, dtype=NP_REAL)
    basis_offset = NP.asarray(section.basis_offset, dtype=NP_REAL)
    basis_applied_np = basis_offset + raw[:, 0:1] * basis_x + raw[:, 1:2] * basis_y + raw[:, 2:3] * basis_z

    scalar_positions = NP.asarray(ctx.raw_position_scalars, dtype=NP_REAL)
    vertex_ids = NP.arange(ctx.vertex_count, dtype=NP.int32)
    t4_scalar_index = vertex_ids * 3 + section.t4_scalar_base
    t4_values_np = scalar_positions[t4_scalar_index]
    pre_skin_np = scalar_positions.reshape((-1, 3))

    final_preclip_np = basis_applied_np.copy()
    t6_atten_np = NP.zeros(ctx.vertex_count, dtype=NP_REAL)
    sample_u_np = NP.zeros(ctx.vertex_count, dtype=NP_REAL)
    sample_v_np = NP.zeros(ctx.vertex_count, dtype=NP_REAL)

    if section.branch_enter:
        t5_np = NP.asarray(ctx.t5_records, dtype=NP_REAL)
        t5_indices = ((vertex_ids + section.vertex_id_offset) << 1) | 1
        t5_xyz = t5_np[t5_indices, 0:3]

        basis_dir_x = NP.asarray(section.basis_dir_x, dtype=NP_REAL)
        basis_dir_y = NP.asarray(section.basis_dir_y, dtype=NP_REAL)
        basis_dir_z = NP.asarray(section.basis_dir_z, dtype=NP_REAL)
        direction = (
            t5_xyz[:, 0:1] * basis_dir_x
            + t5_xyz[:, 1:2] * basis_dir_y
            + t5_xyz[:, 2:3] * basis_dir_z
        )

        scale_tex_dir = NP.asarray(section.scale_tex_dir, dtype=NP_REAL)
        atten = NP.sum(direction * scale_tex_dir[None, :], axis=1)
        atten = (atten - ctx.cb5_f32[3]) / (1.00100005 - ctx.cb5_f32[3])
        atten = NP.clip(atten, 0.0, 1.0)
        atten = (3.0 - 2.0 * atten) * atten * atten

        cb1_124 = NP.asarray(section.cb1_124, dtype=NP_REAL)
        round_base = NP.asarray(section.round_base, dtype=NP_REAL)
        work = (
            ctx.cb5_f32[48 * 4 + 2] * (section.add_scalar + (basis_applied_np - cb1_124[None, :]))
            + 2097152.0
            * (
                ctx.cb5_f32[48 * 4 + 2] * NP.asarray(ctx.cb1_f32[121 * 4 : 121 * 4 + 3], dtype=NP_REAL)[None, :]
                - round_base[None, :]
            )
        )

        coeff5 = NP.asarray(section.coeff5, dtype=NP_REAL)
        coeff13 = NP.asarray(section.coeff13, dtype=NP_REAL)
        coeff16 = NP.asarray(section.coeff16, dtype=NP_REAL)
        r17 = NP.asarray(section.r17, dtype=NP_REAL)
        sample_vec = (
            work[:, 0:1] * coeff5[None, :]
            + work[:, 1:2] * coeff13[None, :]
            + work[:, 2:3] * coeff16[None, :]
            + r17[None, :]
        )

        u = sample_vec[:, 0]
        v = sample_vec[:, 1] + 0.300000012 * sample_vec[:, 2]
        sampled = sample_image_rgba_numpy(
            pixels_np=ctx.t6_pixels_np,
            width=ctx.t6_size[0],
            height=ctx.t6_size[1],
            u=u,
            v=v,
            mode=T6_SAMPLE_MODE,
        )[:, 0:3]

        scale_tex = NP.asarray(section.scale_tex, dtype=NP_REAL)
        displacement = sampled * scale_tex[None, :] * atten[:, None]
        if section.clamp_radius > 0.0:
            displacement = NP.clip(displacement, -section.clamp_radius, section.clamp_radius)

        final_preclip_np = basis_applied_np + displacement
        t6_atten_np = atten.astype(NP_REAL)
        sample_u_np = u.astype(NP_REAL)
        sample_v_np = v.astype(NP_REAL)

    row0 = NP.asarray(ctx.cb1_f32[0:4], dtype=NP_REAL)
    row1 = NP.asarray(ctx.cb1_f32[4:8], dtype=NP_REAL)
    row2 = NP.asarray(ctx.cb1_f32[8:12], dtype=NP_REAL)
    row3 = NP.asarray(ctx.cb1_f32[12:16], dtype=NP_REAL)
    clip = (
        final_preclip_np[:, 0:1] * row0[None, :]
        + final_preclip_np[:, 1:2] * row1[None, :]
        + final_preclip_np[:, 2:3] * row2[None, :]
        + row3[None, :]
    )
    w = clip[:, 3]
    ndc = NP.zeros((ctx.vertex_count, 3), dtype=NP_REAL)
    nonzero = NP.abs(w) > 1.0e-20
    ndc[nonzero] = clip[nonzero, 0:3] / w[nonzero, None]

    return ReplayResult(
        draw=ctx.draw,
        mode="numpy",
        section_state=section,
        raw_post_skin=raw_post_skin,
        pre_skin_t4=[tuple(map(float, point)) for point in pre_skin_np.tolist()],
        basis_applied=[tuple(map(float, point)) for point in basis_applied_np.tolist()],
        final_preclip=[tuple(map(float, point)) for point in final_preclip_np.tolist()],
        final_clip_ndc=[tuple(map(float, point)) for point in ndc.tolist()],
        clip_w=[float(value) for value in w.tolist()],
        section_id=[float(section.section_index)] * ctx.vertex_count,
        gate_main=[1.0 if section.branch_enter else 0.0] * ctx.vertex_count,
        gate_secondary=[1.0 if section.gate_secondary else 0.0] * ctx.vertex_count,
        t4_branch_value=[float(value) for value in t4_values_np.tolist()],
        t6_atten=[float(value) for value in t6_atten_np.tolist()],
        sample_u=[float(value) for value in sample_u_np.tolist()],
        sample_v=[float(value) for value in sample_v_np.tolist()],
    )


def validate_numpy_against_reference(ctx: DrawReplayContext, section: SectionState):
    reference = replay_reference(ctx, section)
    fast = replay_numpy(ctx, section)
    sample_count = min(VALIDATION_SAMPLE_VERTICES, ctx.vertex_count)
    if sample_count <= 0:
        return
    if sample_count == ctx.vertex_count:
        indices = list(range(ctx.vertex_count))
    else:
        step = max(1, ctx.vertex_count // sample_count)
        indices = list(range(0, ctx.vertex_count, step))[:sample_count]

    def max_component_error(a: list[tuple[float, float, float]], b: list[tuple[float, float, float]]) -> tuple[float, float]:
        abs_delta = 0.0
        rel_delta = 0.0
        for vertex_id in indices:
            for lhs, rhs in zip(a[vertex_id], b[vertex_id]):
                delta = abs(lhs - rhs)
                scale = max(1.0, abs(lhs), abs(rhs))
                abs_delta = max(abs_delta, delta)
                rel_delta = max(rel_delta, delta / scale)
        return abs_delta, rel_delta

    checks = {
        "basis_applied": max_component_error(reference.basis_applied, fast.basis_applied),
        "final_preclip": max_component_error(reference.final_preclip, fast.final_preclip),
        "final_clip": max_component_error(reference.final_clip_ndc, fast.final_clip_ndc),
    }
    for name, (abs_delta, rel_delta) in checks.items():
        if abs_delta > VALIDATION_ABS_EPSILON and rel_delta > VALIDATION_REL_EPSILON:
            raise ValueError(
                f"NumPy replay validation failed for {ctx.draw.label}:{name}, "
                f"abs delta {abs_delta:.6g} / rel delta {rel_delta:.6g} exceed "
                f"{VALIDATION_ABS_EPSILON:.6g} and {VALIDATION_REL_EPSILON:.6g}"
            )


def replay_draw(ctx: DrawReplayContext) -> ReplayResult:
    section = decode_section_state(ctx)
    mode = REPLAY_MODE
    if mode == "auto":
        mode = "numpy" if NP is not None else "python"
    if mode == "numpy":
        if NP is None:
            print(f"[9d62] NumPy unavailable, falling back to pure Python for {ctx.draw.label}.")
            mode = "python"
        else:
            validate_numpy_against_reference(ctx, section)
            return replay_numpy(ctx, section)
    return replay_reference(ctx, section)


def make_object_name(draw: DrawSpec, layer: str) -> str:
    return f"{draw.label}_{layer}"


def maybe_create_layer_object(
    *,
    collection: bpy.types.Collection,
    draw: DrawSpec,
    layer: str,
    positions: list[tuple[float, float, float]],
    indices: list[int],
):
    if layer not in ENABLED_LAYERS:
        return None
    obj = create_mesh_object(
        name=make_object_name(draw, layer),
        collection=collection,
        positions=positions,
        indices=indices,
    )
    obj["frameanalysis_draw_name"] = draw.draw_name
    obj["frameanalysis_layer"] = layer
    obj["frameanalysis_index_start"] = draw.index_start
    obj["frameanalysis_index_count"] = -1 if draw.index_count is None else draw.index_count
    return obj


def attach_final_attributes(obj: bpy.types.Object, replay: ReplayResult):
    mesh = obj.data
    attach_point_float_attribute(mesh, "section_id", replay.section_id)
    attach_point_float_attribute(mesh, "gate_main", replay.gate_main)
    attach_point_float_attribute(mesh, "gate_secondary", replay.gate_secondary)
    attach_point_float_attribute(mesh, "t4_branch_value", replay.t4_branch_value)
    attach_point_float_attribute(mesh, "t6_atten", replay.t6_atten)
    attach_point_float_attribute(mesh, "sample_u", replay.sample_u)
    attach_point_float_attribute(mesh, "sample_v", replay.sample_v)
    attach_point_float_attribute(mesh, "clip_w", replay.clip_w)


def import_draw(collection: bpy.types.Collection, ctx: DrawReplayContext) -> list[str]:
    replay = replay_draw(ctx)
    created: list[str] = []

    raw_obj = maybe_create_layer_object(
        collection=collection,
        draw=ctx.draw,
        layer="raw_post_skin",
        positions=replay.raw_post_skin,
        indices=ctx.sliced_indices,
    )
    if raw_obj is not None:
        created.append(raw_obj.name)

    pre_skin_obj = maybe_create_layer_object(
        collection=collection,
        draw=ctx.draw,
        layer="pre_skin_t4",
        positions=replay.pre_skin_t4,
        indices=ctx.sliced_indices,
    )
    if pre_skin_obj is not None:
        created.append(pre_skin_obj.name)

    basis_obj = maybe_create_layer_object(
        collection=collection,
        draw=ctx.draw,
        layer="basis_applied",
        positions=replay.basis_applied,
        indices=ctx.sliced_indices,
    )
    if basis_obj is not None:
        created.append(basis_obj.name)

    final_preclip_obj = maybe_create_layer_object(
        collection=collection,
        draw=ctx.draw,
        layer="final_preclip",
        positions=replay.final_preclip,
        indices=ctx.sliced_indices,
    )
    if final_preclip_obj is not None:
        attach_final_attributes(final_preclip_obj, replay)
        final_preclip_obj["frameanalysis_mode"] = replay.mode
        final_preclip_obj["frameanalysis_section_index"] = replay.section_state.section_index
        final_preclip_obj["frameanalysis_branch_enter"] = int(replay.section_state.branch_enter)
        final_preclip_obj["frameanalysis_gate_secondary"] = int(replay.section_state.gate_secondary)
        created.append(final_preclip_obj.name)

    final_clip_obj = maybe_create_layer_object(
        collection=collection,
        draw=ctx.draw,
        layer="final_clip",
        positions=replay.final_clip_ndc,
        indices=ctx.sliced_indices,
    )
    if final_clip_obj is not None:
        attach_final_attributes(final_clip_obj, replay)
        created.append(final_clip_obj.name)

    print(
        f"[9d62] {ctx.draw.label}: mode={replay.mode}, section={replay.section_state.section_index}, "
        f"branch_enter={replay.section_state.branch_enter}, gate_secondary={replay.section_state.gate_secondary}, "
        f"vertex_count={ctx.vertex_count}, index_count={len(ctx.sliced_indices)}"
    )
    return created


def main():
    collection = ensure_collection(COLLECTION_NAME)
    created: list[str] = []

    for draw in (make_custom_draw(), make_original_draw()):
        ctx = build_draw_context(draw)
        created.extend(import_draw(collection, ctx))

    print("Imported objects:")
    for name in created:
        print(f"  - {name}")


if __name__ == "__main__":
    main()
