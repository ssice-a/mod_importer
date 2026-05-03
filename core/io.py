"""Raw parsers and packers for the resources we currently understand."""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

from .models import CompactedGeometry, IndexSlice, PackedHalf2x4, Snorm4


_INDEX_LINE_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s*$")


def _write_bytes_if_changed(path: str, data: bytes) -> str:
    output_path = Path(path)
    if output_path.is_file() and output_path.stat().st_size == len(data):
        try:
            if output_path.read_bytes() == data:
                return path
        except OSError:
            pass
    output_path.write_bytes(data)
    return path


def read_index_slice_txt(path: str) -> IndexSlice:
    """Parse a 3DMigoto index dump text file for a single draw slice."""
    first_index = None
    index_count = None
    format_name = None
    triangles: list[tuple[int, int, int]] = []
    used_vertex_ids: set[int] = set()

    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line.startswith("first index:"):
                first_index = int(line.split(":", 1)[1].strip())
                continue
            if line.startswith("index count:"):
                index_count = int(line.split(":", 1)[1].strip())
                continue
            if line.startswith("format:"):
                format_name = line.split(":", 1)[1].strip()
                continue

            match = _INDEX_LINE_RE.fullmatch(line)
            if match:
                triangle = tuple(int(group) for group in match.groups())
                triangles.append(triangle)
                used_vertex_ids.update(triangle)

    if first_index is None:
        raise ValueError(f"Missing 'first index' header in {path}")
    if index_count is None:
        raise ValueError(f"Missing 'index count' header in {path}")
    if format_name is None:
        raise ValueError(f"Missing 'format' header in {path}")
    if not triangles:
        raise ValueError(f"No triangle data found in {path}")
    if len(triangles) * 3 != index_count:
        raise ValueError(
            f"Index count mismatch in {path}: header says {index_count}, but parsed {len(triangles) * 3}"
        )

    return IndexSlice(
        source_path=path,
        format_name=format_name,
        first_index=first_index,
        index_count=index_count,
        triangles=triangles,
        used_vertex_ids=tuple(sorted(used_vertex_ids)),
    )


def read_vb0_positions(path: str) -> list[tuple[float, float, float]]:
    """Read the current base mesh positions from a raw VB0 buffer."""
    data = Path(path).read_bytes()
    if len(data) == 0:
        raise ValueError(f"VB0 buffer is empty: {path}")
    if len(data) % 12 != 0:
        raise ValueError(f"VB0 buffer size is not a multiple of 12 bytes: {path}")

    return [tuple(values) for values in struct.iter_unpack("<3f", data)]


def read_half2x4_records(path: str) -> list[PackedHalf2x4]:
    """Read one 16-byte packed UV record as four half2 entries per vertex."""
    data = Path(path).read_bytes()
    if len(data) == 0:
        raise ValueError(f"Packed half2x4 buffer is empty: {path}")
    if len(data) % 16 != 0:
        raise ValueError(f"Packed half2x4 buffer size is not a multiple of 16 bytes: {path}")

    records: list[PackedHalf2x4] = []
    for byte_offset in range(0, len(data), 16):
        values = struct.unpack_from("<8e", data, byte_offset)
        records.append(
            (
                (float(values[0]), float(values[1])),
                (float(values[2]), float(values[3])),
                (float(values[4]), float(values[5])),
                (float(values[6]), float(values[7])),
            )
        )
    return records


def _snorm16_to_float(value: int) -> float:
    if value == -32768:
        return -1.0
    return max(-1.0, float(value) / 32767.0)


def _snorm8_to_float(byte_value: int) -> float:
    signed_value = byte_value if byte_value < 128 else byte_value - 256
    if signed_value == -128:
        return -1.0
    return max(-1.0, float(signed_value) / 127.0)


def _float_to_snorm8(value: float) -> int:
    clamped = max(-1.0, min(1.0, float(value)))
    if clamped <= -1.0:
        signed_value = -128
    else:
        signed_value = int(round(clamped * 127.0))
        signed_value = max(-127, min(127, signed_value))
    return signed_value & 0xFF


def read_snorm8x4_records(path: str) -> list[Snorm4]:
    """Read a raw buffer as snorm8x4 records."""
    data = Path(path).read_bytes()
    if len(data) == 0:
        raise ValueError(f"Packed snorm8 buffer is empty: {path}")
    if len(data) % 4 != 0:
        raise ValueError(f"Packed snorm8 buffer size is not a multiple of 4 bytes: {path}")

    records: list[Snorm4] = []
    for x_value, y_value, z_value, w_value in struct.iter_unpack("<4B", data):
        records.append(
            (
                _snorm8_to_float(x_value),
                _snorm8_to_float(y_value),
                _snorm8_to_float(z_value),
                _snorm8_to_float(w_value),
            )
        )
    return records


def read_pre_cs_frame_pairs(path: str, *, vertex_count: int | None = None) -> tuple[list[Snorm4], list[Snorm4]]:
    """Read the pre-CS frame source as two snorm8x4 records per vertex."""
    records = read_snorm8x4_records(path)
    if len(records) % 2 != 0:
        raise ValueError(f"Expected an even number of snorm8x4 records in {path}")

    pair_count = len(records) // 2
    if vertex_count is not None and pair_count < vertex_count:
        raise ValueError(
            f"Pre-CS frame source is shorter than VB0: {pair_count} vertex pairs for {vertex_count} vertices."
        )

    frame_a = records[0::2]
    frame_b = records[1::2]
    if vertex_count is not None:
        frame_a = frame_a[:vertex_count]
        frame_b = frame_b[:vertex_count]
    return frame_a, frame_b


def read_post_cs_frame_pairs(path: str, *, vertex_count: int | None = None) -> tuple[list[Snorm4], list[Snorm4]]:
    """Read the post-CS packed frame source as two snorm16x4 records per vertex."""
    data = Path(path).read_bytes()
    if len(data) == 0:
        raise ValueError(f"Packed post-CS frame buffer is empty: {path}")
    if len(data) % 16 != 0:
        raise ValueError(f"Packed post-CS frame buffer size is not a multiple of 16 bytes: {path}")

    frame_a: list[Snorm4] = []
    frame_b: list[Snorm4] = []
    for values in struct.iter_unpack("<8h", data):
        frame_a.append(tuple(_snorm16_to_float(component) for component in values[:4]))
        frame_b.append(tuple(_snorm16_to_float(component) for component in values[4:8]))

    if vertex_count is not None and len(frame_a) < vertex_count:
        raise ValueError(
            f"Post-CS frame source is shorter than VB0: {len(frame_a)} vertex pairs for {vertex_count} vertices."
        )
    if vertex_count is not None:
        frame_a = frame_a[:vertex_count]
        frame_b = frame_b[:vertex_count]
    return frame_a, frame_b


def read_u8x4_records(path: str) -> list[tuple[int, int, int, int]]:
    """Read a raw buffer as packed uint8x4 records."""
    data = Path(path).read_bytes()
    if len(data) == 0:
        raise ValueError(f"Packed uint8 buffer is empty: {path}")
    if len(data) % 4 != 0:
        raise ValueError(f"Packed uint8 buffer size is not a multiple of 4 bytes: {path}")

    return [tuple(values) for values in struct.iter_unpack("<4B", data)]


def read_weight_pairs(path: str, *, vertex_count: int | None = None) -> tuple[list[tuple[int, int, int, int]], list[tuple[int, int, int, int]]]:
    """Read the pre-CS weights as two uint8x4 records per vertex."""
    records = read_u8x4_records(path)
    if len(records) % 2 != 0:
        raise ValueError(f"Expected an even number of uint8x4 records in {path}")

    pair_count = len(records) // 2
    if vertex_count is not None and pair_count < vertex_count:
        raise ValueError(f"Weight buffer is shorter than VB0: {pair_count} vertex pairs for {vertex_count} vertices.")

    indices = records[0::2]
    weights = records[1::2]
    if vertex_count is not None:
        indices = indices[:vertex_count]
        weights = weights[:vertex_count]
    return indices, weights


def read_u32_buffer(path: str) -> list[int]:
    """Read a raw buffer as little-endian uint32 values."""
    data = Path(path).read_bytes()
    if len(data) == 0:
        raise ValueError(f"Buffer is empty: {path}")
    if len(data) % 4 != 0:
        raise ValueError(f"Buffer size is not a multiple of 4 bytes: {path}")
    return [value[0] for value in struct.iter_unpack("<I", data)]


def read_u16_buffer(path: str) -> list[int]:
    """Read a raw buffer as little-endian uint16 values."""
    data = Path(path).read_bytes()
    if len(data) == 0:
        raise ValueError(f"Buffer is empty: {path}")
    if len(data) % 2 != 0:
        raise ValueError(f"Buffer size is not a multiple of 2 bytes: {path}")
    return [value[0] for value in struct.iter_unpack("<H", data)]


def build_compacted_geometry(
    positions: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
    packed_uv_entries: list[PackedHalf2x4],
) -> CompactedGeometry:
    """Compact the vertex ids referenced by the selected IB slice."""
    if not positions:
        raise ValueError("Position buffer is empty.")
    if not triangles:
        raise ValueError("Triangle list is empty.")
    if len(packed_uv_entries) < len(positions):
        raise ValueError(
            f"Packed UV buffer is shorter than VB0: {len(packed_uv_entries)} entries for {len(positions)} vertices."
        )

    remap: dict[int, int] = {}
    original_vertex_ids: list[int] = []
    compact_positions: list[tuple[float, float, float]] = []
    compact_uv_entries: list[PackedHalf2x4] = []
    compact_triangles: list[tuple[int, int, int]] = []

    for triangle in triangles:
        remapped_triangle = []
        for original_vertex_id in triangle:
            if original_vertex_id < 0 or original_vertex_id >= len(positions):
                raise ValueError(
                    f"Triangle references vertex {original_vertex_id}, but VB0 only has {len(positions)} vertices."
                )

            if original_vertex_id not in remap:
                new_vertex_id = len(original_vertex_ids)
                remap[original_vertex_id] = new_vertex_id
                original_vertex_ids.append(original_vertex_id)
                compact_positions.append(positions[original_vertex_id])
                compact_uv_entries.append(packed_uv_entries[original_vertex_id])

            remapped_triangle.append(remap[original_vertex_id])

        compact_triangles.append(tuple(remapped_triangle))

    return CompactedGeometry(
        positions=compact_positions,
        triangles=compact_triangles,
        packed_uv_entries=compact_uv_entries,
        original_vertex_ids=original_vertex_ids,
    )


def write_u16_buffer(path: str, values: list[int]) -> str:
    """Write a little-endian uint16 buffer."""
    data = bytearray()
    for value in values:
        if value < 0 or value > 0xFFFF:
            raise ValueError(f"Index value outside R16_UINT range: {value}")
        data.extend(struct.pack("<H", value))
    return _write_bytes_if_changed(path, bytes(data))


def write_float3_buffer(path: str, values: list[tuple[float, float, float]]) -> str:
    """Write a little-endian float3 buffer."""
    data = bytearray()
    for x_value, y_value, z_value in values:
        data.extend(struct.pack("<3f", float(x_value), float(y_value), float(z_value)))
    return _write_bytes_if_changed(path, bytes(data))


def write_float4_buffer(path: str, values: list[tuple[float, float, float, float]]) -> str:
    """Write a little-endian float4 buffer."""
    data = bytearray()
    for x_value, y_value, z_value, w_value in values:
        data.extend(struct.pack("<4f", float(x_value), float(y_value), float(z_value), float(w_value)))
    return _write_bytes_if_changed(path, bytes(data))


def write_f32_buffer(path: str, values: list[float]) -> str:
    """Write a little-endian float32 buffer."""
    data = bytearray()
    for value in values:
        data.extend(struct.pack("<f", float(value)))
    return _write_bytes_if_changed(path, bytes(data))


def write_weight_pairs_buffer(
    path: str,
    indices: list[tuple[int, int, int, int]],
    weights: list[tuple[int, int, int, int]],
) -> str:
    """Write the pre-CS weight buffer as alternating uint8x4 index/weight records."""
    if len(indices) != len(weights):
        raise ValueError("Weight index and weight record counts do not match.")

    data = bytearray()
    for index_record, weight_record in zip(indices, weights):
        data.extend(struct.pack("<4B", *[int(value) & 0xFF for value in index_record]))
        data.extend(struct.pack("<4B", *[int(value) & 0xFF for value in weight_record]))
    return _write_bytes_if_changed(path, bytes(data))


def write_u8x4_buffer(path: str, records: list[tuple[int, int, int, int]]) -> str:
    """Write one packed uint8x4 record per vertex."""
    data = bytearray()
    for record in records:
        if len(record) != 4:
            raise ValueError("Expected exactly four uint8 values per record.")
        data.extend(struct.pack("<4B", *[int(value) & 0xFF for value in record]))
    return _write_bytes_if_changed(path, bytes(data))


def write_half2x4_buffer(path: str, records: list[PackedHalf2x4]) -> str:
    """Write the packed half2x4 UV buffer."""
    data = bytearray()
    for record in records:
        flat_values = [float(component) for pair in record for component in pair]
        if len(flat_values) != 8:
            raise ValueError("Expected exactly 8 half values per packed UV record.")
        data.extend(struct.pack("<8e", *flat_values))
    return _write_bytes_if_changed(path, bytes(data))


def write_snorm8x4_pairs_buffer(path: str, frame_a: list[Snorm4], frame_b: list[Snorm4]) -> str:
    """Write the pre-CS frame source as alternating snorm8x4 records."""
    if len(frame_a) != len(frame_b):
        raise ValueError("Frame A and Frame B record counts do not match.")

    data = bytearray()
    for record_a, record_b in zip(frame_a, frame_b):
        for record in (record_a, record_b):
            data.extend(bytes(_float_to_snorm8(value) for value in record))
    return _write_bytes_if_changed(path, bytes(data))


def write_u32_buffer(path: str, values: list[int]) -> str:
    """Write a little-endian uint32 buffer."""
    data = bytearray()
    for value in values:
        data.extend(struct.pack("<I", int(value) & 0xFFFFFFFF))
    return _write_bytes_if_changed(path, bytes(data))


def write_json(path: str, payload: object) -> str:
    """Write JSON with UTF-8 encoding."""
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
