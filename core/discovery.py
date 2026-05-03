"""Dynamic 异环 profile discovery and frame-analysis helpers."""

from __future__ import annotations

import re
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .io import read_index_slice_txt, read_u32_buffer
from .models import (
    DetectedModelBundle,
    DetectedSlice,
    DispatchBatch,
    ResolvedImportBundle,
    TextureBinding,
    Vb0OriginStage,
    Vb0OriginTrace,
)
from .profiles import YIHUAN_PROFILE


_DRAW_INDEXED_RE = re.compile(
    r"^(?P<event>\d{6}) DrawIndexed\(IndexCount:(?P<count>\d+), "
    r"StartIndexLocation:(?P<first>\d+), BaseVertexLocation:(?P<base>-?\d+)\)"
)
_DISPATCH_RE = re.compile(
    r"^(?P<event>\d{6}) Dispatch\(ThreadGroupCountX:(?P<x>\d+), "
    r"ThreadGroupCountY:(?P<y>\d+), ThreadGroupCountZ:(?P<z>\d+)\)"
)
_DUMP_RE = re.compile(
    r"^(?P<event>\d{6}) 3DMigoto Dumping (?:Buffer|Texture\w*) (?P<source>.+?) -> (?P<dest>.+)$"
)
_DRAW_DUMP_SOURCE_RE = re.compile(
    r"^(?P<event>\d{6})-(?P<label>[^=]+)=(?P<value>.+)-vs=(?P<vs>[0-9a-f]{16})(?:-gs=[0-9a-f]{16})?-ps=(?P<ps>[0-9a-f]{16})\.(?P<ext>buf|txt|dds|jpg|png)$",
    re.IGNORECASE,
)
_CS_INPUT_DUMP_SOURCE_RE = re.compile(
    r"^(?P<event>\d{6})-cs-(?P<label>cb0|cb1|cb2|cb3|cb4|t0|t1|t2|t3|t4)=(?P<value>.+)-cs=(?P<cs>[0-9a-f]{16})\.(?P<ext>buf|txt)$",
    re.IGNORECASE,
)
_CS_OUTPUT_DUMP_SOURCE_RE = re.compile(
    r"^(?P<event>\d{6})-(?P<label>u0|u1)=(?P<value>.+)-cs=(?P<cs>[0-9a-f]{16})\.(?P<ext>buf|txt)$",
    re.IGNORECASE,
)
_RAW_HASH_RE = re.compile(r"^[0-9a-f]{8,16}$", re.IGNORECASE)

@dataclass(frozen=True)
class _DumpArtifact:
    event_index: int
    label: str
    hash_value: str | None
    raw_hash: str | None
    resource_identity: str | None
    input_path: str
    output_path: str
    extension: str
    cs_hash: str | None = None
    vs_hash: str | None = None
    ps_hash: str | None = None


@dataclass
class _DrawEventRecord:
    event_index: int
    first_index: int | None = None
    index_count: int | None = None
    base_vertex: int | None = None
    resources: dict[str, dict[str, _DumpArtifact]] = field(default_factory=lambda: defaultdict(dict))


@dataclass
class _DispatchEventRecord:
    event_index: int
    thread_group_count_x: int | None = None
    thread_group_count_y: int | None = None
    thread_group_count_z: int | None = None
    cs_hash: str | None = None
    resources: dict[str, dict[str, _DumpArtifact]] = field(default_factory=lambda: defaultdict(dict))


@dataclass(frozen=True)
class _ResolvedDrawRecord:
    event_index: int
    raw_ib_hash: str
    display_ib_hash: str | None
    first_index: int
    index_count: int
    base_vertex: int
    ib_txt_path: str
    ib_buf_path: str | None
    vb0_hash: str | None
    vb0_identity: str | None
    vb0_buf_path: str | None
    vb1_hash: str | None
    vb1_buf_path: str | None
    vb1_layout_path: str | None
    t5_buf_path: str | None
    t0_buf_path: str | None
    t1_buf_path: str | None
    t2_buf_path: str | None
    t3_buf_path: str | None
    t7_buf_path: str | None
    cb1_buf_path: str | None
    cb2_buf_path: str | None
    vs_hash: str | None
    ps_hash: str | None
    vs_resource_labels: tuple[str, ...]
    vs_resource_hashes: dict[str, str]
    vs_resource_extensions: dict[str, str]
    ps_resource_hashes: dict[str, str]
    ps_resource_paths: dict[str, str]
    rt_count: int


@dataclass(frozen=True)
class _FrameScanResult:
    frame_dump_dir: str
    draw_records: tuple[_ResolvedDrawRecord, ...]
    dispatch_records: dict[int, _DispatchEventRecord]
    raw_ib_hashes: tuple[str, ...]
    display_to_raw_ib: dict[str, str]


def _normalize_hash(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized.startswith("!u!="):
        normalized = normalized[4:]
    if not normalized:
        return None
    return normalized


def _u32_to_float(value: int) -> float:
    return struct.unpack("<f", struct.pack("<I", int(value) & 0xFFFFFFFF))[0]


def _float3_vertex_count(path: str | None) -> int | None:
    if not path:
        return None
    try:
        size = Path(path).stat().st_size
    except OSError:
        return None
    if size <= 0 or size % 12 != 0:
        return None
    return size // 12


def _infer_dispatch_vertex_range(
    cb0_values: list[int] | tuple[int, ...],
    *,
    output_vertex_count: int | None,
) -> tuple[int, int]:
    """Infer the written vertex range from observed cb0 layouts.

    Different game CS variants encode start/count in different cb0 lanes. The
    importer should follow the dumped resources, not a fixed shader hash, so we
    score the known lane pairs and keep the one that fits the output buffer.
    This is analysis-only. Runtime collector keys and finish conditions are
    inferred later from the same observed cb0 values instead of hard-coded lanes.
    """
    if len(cb0_values) < 4:
        return 0, int(output_vertex_count or 0)

    primary_candidate = (int(cb0_values[1]), int(cb0_values[2]))
    chained_candidate = (int(cb0_values[2]), int(cb0_values[3]))
    if int(cb0_values[1]) == int(cb0_values[2]) and int(cb0_values[3]) > 0:
        candidates = [chained_candidate, primary_candidate]
    else:
        candidates = [primary_candidate, chained_candidate]

    for start_vertex, vertex_count in candidates:
        if start_vertex < 0 or vertex_count <= 0:
            continue
        if output_vertex_count is not None and start_vertex + vertex_count > output_vertex_count:
            continue
        return start_vertex, vertex_count
    return 0, int(output_vertex_count or 0)


def _ensure_frame_dump_dir(frame_dump_dir: str | None) -> Path:
    requested_dir = (frame_dump_dir or YIHUAN_PROFILE.default_frame_dump_dir or "").strip()
    if not requested_dir:
        raise ValueError("Frame dump directory is required for the 异环 profile.")

    resolved_dir = Path(requested_dir).expanduser().resolve()
    log_path = resolved_dir / "log.txt"
    deduped_dir = resolved_dir / "deduped"
    if not log_path.is_file():
        raise ValueError(f"Could not find log.txt inside frame dump directory: {resolved_dir}")
    if not deduped_dir.is_dir():
        raise ValueError(f"Could not find deduped directory inside frame dump directory: {resolved_dir}")
    return resolved_dir


def _parse_hash_value(value: str) -> tuple[str | None, str | None, str | None]:
    normalized = _normalize_hash(value)
    if normalized is None:
        return None, None, None

    resource_identity = normalized
    stable_value = normalized.split("@", 1)[0] if "@" in normalized else normalized

    if stable_value.endswith(")") and "(" in stable_value:
        display_hash, raw_hash = stable_value.split("(", 1)
        return _normalize_hash(display_hash), _normalize_hash(raw_hash[:-1]), resource_identity
    return stable_value, None, resource_identity


def _artifact_output(resources: dict[str, dict[str, _DumpArtifact]], label: str, extension: str) -> str | None:
    artifact = resources.get(label, {}).get(extension)
    return None if artifact is None else artifact.output_path


def _artifact_first_output(
    resources: dict[str, dict[str, _DumpArtifact]],
    label: str,
    extensions: tuple[str, ...],
) -> tuple[str, str] | None:
    artifact_group = resources.get(label, {})
    for extension in extensions:
        artifact = artifact_group.get(extension)
        if artifact is not None:
            return artifact.output_path, extension
    return None


def _artifact_hash(resources: dict[str, dict[str, _DumpArtifact]], label: str) -> str | None:
    artifact_group = resources.get(label, {})
    for extension in ("buf", "txt", "dds", "jpg", "png"):
        artifact = artifact_group.get(extension)
        if artifact is None:
            continue
        return artifact.raw_hash or artifact.hash_value
    return None


def _artifact_identity(resources: dict[str, dict[str, _DumpArtifact]], label: str) -> str | None:
    artifact_group = resources.get(label, {})
    for extension in ("buf", "txt", "dds", "jpg", "png"):
        artifact = artifact_group.get(extension)
        if artifact is None:
            continue
        return artifact.resource_identity or artifact.raw_hash or artifact.hash_value
    return None


def _artifact_display_hash(resources: dict[str, dict[str, _DumpArtifact]], label: str) -> str | None:
    artifact_group = resources.get(label, {})
    for extension in ("buf", "txt", "dds", "jpg", "png"):
        artifact = artifact_group.get(extension)
        if artifact is None:
            continue
        return artifact.hash_value
    return None


def _draw_rt_count(resources: dict[str, dict[str, _DumpArtifact]]) -> int:
    return sum(
        1
        for label, artifact_group in resources.items()
        if re.fullmatch(r"o\d+", label)
        and any(extension in {"dds", "jpg", "png"} for extension in artifact_group)
    )


def _parse_dump_artifact(input_path: str, output_path: str) -> _DumpArtifact | None:
    basename = Path(input_path).name
    for regex in (_DRAW_DUMP_SOURCE_RE, _CS_INPUT_DUMP_SOURCE_RE, _CS_OUTPUT_DUMP_SOURCE_RE):
        match = regex.match(basename)
        if not match:
            continue

        groups = match.groupdict()
        event_index = int(groups["event"])
        label = groups["label"]
        hash_value, raw_hash, resource_identity = _parse_hash_value(groups["value"])
        return _DumpArtifact(
            event_index=event_index,
            label=label,
            hash_value=hash_value,
            raw_hash=raw_hash,
            resource_identity=resource_identity,
            input_path=input_path,
            output_path=output_path,
            extension=groups["ext"].lower(),
            cs_hash=_normalize_hash(groups.get("cs")),
            vs_hash=_normalize_hash(groups.get("vs")),
            ps_hash=_normalize_hash(groups.get("ps")),
        )
    return None


def _store_draw_artifact(draw_events: dict[int, _DrawEventRecord], artifact: _DumpArtifact):
    event = draw_events.setdefault(artifact.event_index, _DrawEventRecord(event_index=artifact.event_index))
    event.resources[artifact.label][artifact.extension] = artifact


def _store_dispatch_artifact(dispatch_events: dict[int, _DispatchEventRecord], artifact: _DumpArtifact):
    event = dispatch_events.setdefault(artifact.event_index, _DispatchEventRecord(event_index=artifact.event_index))
    if artifact.cs_hash is not None:
        event.cs_hash = artifact.cs_hash
    event.resources[artifact.label][artifact.extension] = artifact


def _build_draw_records(draw_events: dict[int, _DrawEventRecord]) -> tuple[_ResolvedDrawRecord, ...]:
    draw_records: list[_ResolvedDrawRecord] = []
    for event_index, event in sorted(draw_events.items()):
        if event.first_index is None or event.index_count is None or event.base_vertex is None:
            continue

        raw_ib_hash = _artifact_hash(event.resources, "ib")
        ib_txt_path = _artifact_output(event.resources, "ib", "txt")
        if raw_ib_hash is None or ib_txt_path is None:
            continue

        artifacts = [
            artifact
            for artifact_group in event.resources.values()
            for artifact in artifact_group.values()
        ]
        vs_hash = next((artifact.vs_hash for artifact in artifacts if artifact.vs_hash), None)
        ps_hash = next((artifact.ps_hash for artifact in artifacts if artifact.ps_hash), None)
        vs_resource_labels = tuple(sorted(event.resources.keys()))
        vs_resource_hashes = {
            label: hash_value
            for label in sorted(event.resources.keys())
            if label.startswith("vs-t")
            for hash_value in [_artifact_hash(event.resources, label)]
            if hash_value
        }
        vs_resource_extensions = {
            label: extension
            for label in sorted(event.resources.keys())
            if label.startswith("vs-t")
            for output in [_artifact_first_output(event.resources, label, ("buf", "dds", "jpg", "png"))]
            if output is not None
            for _output_path, extension in [output]
        }
        ps_resource_hashes = {
            label: hash_value
            for label in sorted(event.resources.keys())
            if label.startswith("ps-t")
            for hash_value in [_artifact_hash(event.resources, label)]
            if hash_value
        }
        ps_resource_paths = {
            label: output_path
            for label in sorted(event.resources.keys())
            if label.startswith("ps-t")
            for output in [_artifact_first_output(event.resources, label, ("dds", "jpg", "png"))]
            if output is not None
            for output_path, _extension in [output]
        }

        draw_records.append(
            _ResolvedDrawRecord(
                event_index=event_index,
                raw_ib_hash=raw_ib_hash,
                display_ib_hash=_artifact_display_hash(event.resources, "ib"),
                first_index=int(event.first_index),
                index_count=int(event.index_count),
                base_vertex=int(event.base_vertex),
                ib_txt_path=ib_txt_path,
                ib_buf_path=_artifact_output(event.resources, "ib", "buf"),
                vb0_hash=_artifact_hash(event.resources, "vb0"),
                vb0_identity=_artifact_identity(event.resources, "vb0"),
                vb0_buf_path=_artifact_output(event.resources, "vb0", "buf"),
                vb1_hash=_artifact_hash(event.resources, "vb1"),
                vb1_buf_path=_artifact_output(event.resources, "vb1", "buf"),
                vb1_layout_path=_artifact_output(event.resources, "vb1", "txt"),
                t5_buf_path=_artifact_output(event.resources, "vs-t5", "buf")
                or _artifact_output(event.resources, "vs-t3", "buf"),
                t0_buf_path=_artifact_output(event.resources, "vs-t0", "buf"),
                t1_buf_path=_artifact_output(event.resources, "vs-t1", "buf"),
                t2_buf_path=_artifact_output(event.resources, "vs-t2", "buf"),
                t3_buf_path=_artifact_output(event.resources, "vs-t3", "buf"),
                t7_buf_path=_artifact_output(event.resources, "vs-t7", "buf")
                or _artifact_output(event.resources, "vs-t5", "buf"),
                cb1_buf_path=_artifact_output(event.resources, "vs-cb1", "buf"),
                cb2_buf_path=_artifact_output(event.resources, "vs-cb2", "buf"),
                vs_hash=vs_hash,
                ps_hash=ps_hash,
                vs_resource_labels=vs_resource_labels,
                vs_resource_hashes=vs_resource_hashes,
                vs_resource_extensions=vs_resource_extensions,
                ps_resource_hashes=ps_resource_hashes,
                ps_resource_paths=ps_resource_paths,
                rt_count=_draw_rt_count(event.resources),
            )
        )
    return tuple(draw_records)


def analyze_yihuan_frame_stages(frame_dump_dir: str | None, ib_hash: str | None = None) -> dict[str, object]:
    """Return a compact, UI-friendly FrameAnalysis resource report for the Yihuan profile."""
    scan_result = _scan_yihuan_frame_dump(frame_dump_dir)
    requested_hash = _normalize_hash(ib_hash)
    if requested_hash:
        raw_ib_hash = scan_result.display_to_raw_ib.get(requested_hash, requested_hash)
        draw_records = [record for record in scan_result.draw_records if record.raw_ib_hash == raw_ib_hash]
    else:
        raw_ib_hash = scan_result.raw_ib_hashes[0] if len(scan_result.raw_ib_hashes) == 1 else ""
        draw_records = list(scan_result.draw_records if not raw_ib_hash else [
            record for record in scan_result.draw_records if record.raw_ib_hash == raw_ib_hash
        ])

    draw_rows: list[dict[str, object]] = []
    for record in sorted(draw_records, key=lambda item: item.event_index):
        draw_rows.append(
            {
                "event_index": record.event_index,
                "raw_ib_hash": record.raw_ib_hash,
                "display_ib_hash": record.display_ib_hash or "",
                "first_index": record.first_index,
                "index_count": record.index_count,
                "base_vertex": record.base_vertex,
                "vs_hash": record.vs_hash or "",
                "ps_hash": record.ps_hash or "",
                "rt_count": record.rt_count,
                "vb0_hash": record.vb0_hash or "",
                "vb0_identity": record.vb0_identity or "",
                "resource_labels": list(record.vs_resource_labels),
                "resource_hashes": dict(record.vs_resource_hashes),
                "ps_resource_hashes": dict(record.ps_resource_hashes),
                "ps_resource_paths": dict(record.ps_resource_paths),
            }
        )

    dispatch_rows: list[dict[str, object]] = []
    for event_index, record in sorted(scan_result.dispatch_records.items()):
        cb0_hash = _artifact_hash(record.resources, "cb0") or ""
        cb0_buf_path = _artifact_output(record.resources, "cb0", "buf") or ""
        output_path = _artifact_output(record.resources, "u1", "buf") or _artifact_output(record.resources, "u0", "buf")
        output_vertex_count = _float3_vertex_count(output_path)
        start_vertex, vertex_count = _infer_dispatch_vertex_range(
            read_u32_buffer(str(cb0_buf_path)) if cb0_buf_path else [],
            output_vertex_count=output_vertex_count,
        )
        dispatch_rows.append(
            {
                "event_index": event_index,
                "cs_hash": record.cs_hash or "",
                "cb0_hash": cb0_hash,
                "t0_hash": _artifact_hash(record.resources, "t0") or "",
                "u0_hash": _artifact_hash(record.resources, "u0") or "",
                "u1_hash": _artifact_hash(record.resources, "u1") or "",
                "u0_identity": _artifact_identity(record.resources, "u0") or "",
                "u1_identity": _artifact_identity(record.resources, "u1") or "",
                "cb0_buf_path": cb0_buf_path,
                "t0_buf_path": _artifact_output(record.resources, "t0", "buf") or "",
                "u0_buf_path": _artifact_output(record.resources, "u0", "buf") or "",
                "u1_buf_path": _artifact_output(record.resources, "u1", "buf") or "",
                "start_vertex": start_vertex,
                "vertex_count": vertex_count,
                "thread_group_count": [
                    int(record.thread_group_count_x or 0),
                    int(record.thread_group_count_y or 0),
                    int(record.thread_group_count_z or 0),
                ],
                "resource_labels": sorted(record.resources.keys()),
            }
        )

    return {
        "profile_id": YIHUAN_PROFILE.profile_id,
        "frame_dump_dir": scan_result.frame_dump_dir,
        "raw_ib_hash": raw_ib_hash,
        "draw_count": len(draw_rows),
        "dispatch_count": len(dispatch_rows),
        "draw_pass_map": draw_rows,
        "cs_collect_map": dispatch_rows,
        "draws": draw_rows,
        "dispatches": dispatch_rows,
    }


@lru_cache(maxsize=64)
def _cached_index_slice(slice_path: str):
    return read_index_slice_txt(slice_path)


@lru_cache(maxsize=8)
def _scan_yihuan_frame_dump(frame_dump_dir: str | None) -> _FrameScanResult:
    resolved_dir = _ensure_frame_dump_dir(frame_dump_dir)
    draw_events: dict[int, _DrawEventRecord] = {}
    dispatch_events: dict[int, _DispatchEventRecord] = {}

    with open(resolved_dir / "log.txt", "r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")

            draw_match = _DRAW_INDEXED_RE.match(line)
            if draw_match:
                event_index = int(draw_match.group("event"))
                draw_event = draw_events.setdefault(event_index, _DrawEventRecord(event_index=event_index))
                draw_event.first_index = int(draw_match.group("first"))
                draw_event.index_count = int(draw_match.group("count"))
                draw_event.base_vertex = int(draw_match.group("base"))
                continue

            dispatch_match = _DISPATCH_RE.match(line)
            if dispatch_match:
                event_index = int(dispatch_match.group("event"))
                dispatch_event = dispatch_events.setdefault(event_index, _DispatchEventRecord(event_index=event_index))
                dispatch_event.thread_group_count_x = int(dispatch_match.group("x"))
                dispatch_event.thread_group_count_y = int(dispatch_match.group("y"))
                dispatch_event.thread_group_count_z = int(dispatch_match.group("z"))
                continue

            dump_match = _DUMP_RE.match(line)
            if not dump_match:
                continue

            artifact = _parse_dump_artifact(dump_match.group("source"), dump_match.group("dest"))
            if artifact is None:
                continue

            if artifact.vs_hash is not None:
                _store_draw_artifact(draw_events, artifact)
            else:
                _store_dispatch_artifact(dispatch_events, artifact)

    draw_records = _build_draw_records(draw_events)
    raw_ib_hashes = sorted({record.raw_ib_hash for record in draw_records if record.raw_ib_hash})
    display_to_raw_ib = {
        record.display_ib_hash: record.raw_ib_hash
        for record in draw_records
        if record.display_ib_hash is not None
    }
    return _FrameScanResult(
        frame_dump_dir=str(resolved_dir),
        draw_records=draw_records,
        dispatch_records=dispatch_events,
        raw_ib_hashes=tuple(raw_ib_hashes),
        display_to_raw_ib=display_to_raw_ib,
    )


def _build_dispatch_batches(
    dispatch_records: dict[int, _DispatchEventRecord],
    *,
    target_u1_hash: str,
    target_u1_identity: str | None = None,
) -> list[DispatchBatch]:
    batches: list[DispatchBatch] = []
    normalized_target_hash = _normalize_hash(target_u1_hash)
    normalized_target_identity = _normalize_hash(target_u1_identity)
    use_identity = bool(normalized_target_identity and "@" in normalized_target_identity)
    for dispatch_index, record in sorted(dispatch_records.items()):
        cs_hash = _normalize_hash(record.cs_hash)
        u1_hash = _artifact_hash(record.resources, "u1")
        u1_identity = _artifact_identity(record.resources, "u1")
        if use_identity:
            if _normalize_hash(u1_identity) != normalized_target_identity:
                continue
        elif _normalize_hash(u1_hash) != normalized_target_hash:
            continue

        cb0_buf_path = _artifact_output(record.resources, "cb0", "buf")
        cb0_hash = _artifact_hash(record.resources, "cb0")
        if cb0_buf_path is None or cb0_hash is None:
            continue

        cb0_values = read_u32_buffer(str(cb0_buf_path))
        if len(cb0_values) < 8:
            raise ValueError(f"Expected at least 8 uint32 values in cb0 buffer: {cb0_buf_path}")

        start_vertex, vertex_count = _infer_dispatch_vertex_range(
            cb0_values,
            output_vertex_count=_float3_vertex_count(_artifact_output(record.resources, "u1", "buf")),
        )

        batches.append(
            DispatchBatch(
                dispatch_index=dispatch_index,
                cs_hash=cs_hash or "",
                cb0_hash=_normalize_hash(cb0_hash) or "",
                cb0_buf_path=str(cb0_buf_path),
                t0_hash=_artifact_hash(record.resources, "t0"),
                t0_buf_path=_artifact_output(record.resources, "t0", "buf"),
                start_vertex=start_vertex,
                vertex_count=vertex_count,
                u0_hash=_artifact_hash(record.resources, "u0"),
                u1_hash=u1_hash,
                u0_identity=_artifact_identity(record.resources, "u0"),
                u1_identity=u1_identity,
            )
        )

    return batches


def _resolve_input_slots_for_dispatch(record: _DispatchEventRecord, cs_hash: str) -> tuple[str, str, str]:
    del cs_hash
    labels = set(record.resources.keys())
    if {"t4", "t2", "t3"}.issubset(labels):
        return "t4", "t2", "t3"
    if {"t3", "t1", "t2"}.issubset(labels):
        return "t3", "t1", "t2"
    raise ValueError(
        f"Could not infer producer CS input slots from dumped resources: {', '.join(sorted(labels))}"
    )


def _most_common_path(paths: list[str | None]) -> str:
    valid_paths = [str(path) for path in paths if path]
    if not valid_paths:
        raise ValueError("Could not resolve a common resource path from the frame dump")
    counts = Counter(valid_paths)
    return counts.most_common(1)[0][0]


def _most_common_hash(values: list[str | None]) -> str | None:
    valid_values = [str(value).lower() for value in values if value]
    if not valid_values:
        return None
    return Counter(valid_values).most_common(1)[0][0]


def _infer_reused_vs_resource_hash(
    draw_group: list[_ResolvedDrawRecord],
    *,
    labels: tuple[str, ...],
    extension: str | None = None,
) -> str | None:
    grouped: dict[str, dict[str, object]] = {}
    for record in draw_group:
        for label in labels:
            if extension is not None and record.vs_resource_extensions.get(label) != extension:
                continue
            hash_value = _normalize_hash(record.vs_resource_hashes.get(label))
            if not hash_value:
                continue
            item = grouped.setdefault(hash_value, {"labels": set(), "count": 0})
            item["labels"].add(label)
            item["count"] = int(item["count"]) + 1
    if not grouped:
        return None
    return max(
        grouped.items(),
        key=lambda item: (
            len(item[1]["labels"]),
            int(item[1]["count"]),
            item[0],
        ),
    )[0]


def _infer_static_match_hashes(
    draw_group: list[_ResolvedDrawRecord],
    *,
    texcoord_hash_hint: str | None = None,
    position_hash_hint: str | None = None,
) -> dict[str, str | None]:
    # The old heuristic guessed "static position" from common VS slots. Transparent
    # passes can bind skinned position in vs-t4, which made us replace a dynamic
    # position stream with the bind-pose Position buffer. Prefer producer/draw
    # facts when we have them, then fall back to the broad heuristic.
    position_hash = _normalize_hash(position_hash_hint) or _infer_reused_vs_resource_hash(
        draw_group,
        labels=("vs-t4", "vs-t6"),
    )
    outline_hash = _infer_reused_vs_resource_hash(draw_group, labels=("vs-t6", "vs-t8"), extension="buf")
    if outline_hash == position_hash:
        outline_hash = None
    return {
        "texcoord": _normalize_hash(texcoord_hash_hint)
        or _infer_reused_vs_resource_hash(draw_group, labels=("vs-t3", "vs-t5")),
        "position": position_hash,
        "outline": outline_hash,
    }


def _infer_texture_slots(draw_group: list[_ResolvedDrawRecord]) -> dict[str, TextureBinding]:
    desired_slots = ("ps-t5", "ps-t7", "ps-t8", "ps-t18")
    candidate = max(
        draw_group,
        key=lambda record: (
            sum(1 for slot in desired_slots if slot in record.ps_resource_paths),
            int(record.index_count),
            int(record.event_index),
        ),
    )
    texture_slots: dict[str, TextureBinding] = {}
    for slot in desired_slots:
        source_path = candidate.ps_resource_paths.get(slot)
        hash_value = _normalize_hash(candidate.ps_resource_hashes.get(slot))
        if not source_path or not hash_value:
            continue
        extension = Path(source_path).suffix.lower().lstrip(".") or "dds"
        texture_slots[slot] = TextureBinding(
            slot=slot,
            hash_value=hash_value,
            source_path=str(source_path),
            extension=extension,
            draw_index=int(candidate.event_index),
            ps_hash=candidate.ps_hash,
            rt_count=int(candidate.rt_count),
        )
    return texture_slots


def _g_buffer_draw_score(record: _ResolvedDrawRecord) -> tuple[int, int, int, int]:
    desired_slots = ("ps-t5", "ps-t7", "ps-t8", "ps-t18")
    texture_score = sum(1 for slot in desired_slots if slot in record.ps_resource_hashes)
    return texture_score, int(record.rt_count), int(record.index_count), int(record.event_index)


def _build_vb0_origin_trace(
    *,
    post_cs_vb0_path: str,
    pre_cs_vb0_path: str,
    batches: list[DispatchBatch],
    dispatch_records: dict[int, _DispatchEventRecord],
) -> Vb0OriginTrace:
    stages: list[Vb0OriginStage] = []
    source_hashes: set[str] = set()

    for batch in batches:
        dispatch_record = dispatch_records[batch.dispatch_index]
        u1_path = _artifact_output(dispatch_record.resources, "u1", "buf") or post_cs_vb0_path
        stages.append(
            Vb0OriginStage(
                stage_name=f"{batch.dispatch_index:06d}_{batch.cs_hash[:8]}",
                buffer_path=str(u1_path),
                covered_vertex_ranges=[(int(batch.start_vertex), int(batch.end_vertex))],
            )
        )
        if batch.u1_hash:
            source_hashes.add(batch.u1_hash)
        if batch.u0_hash:
            source_hashes.add(batch.u0_hash)
        if batch.t0_hash:
            source_hashes.add(batch.t0_hash)

    return Vb0OriginTrace(
        final_vb0_path=str(post_cs_vb0_path),
        closest_rest_pose_path=str(pre_cs_vb0_path),
        note="Closest bind/rest-like source traced dynamically from the producer dispatch chain.",
        source_hashes=sorted(source_hashes),
        stages=stages,
    )


def _build_model_bundle(scan_result: _FrameScanResult, raw_ib_hash: str) -> DetectedModelBundle:
    raw_matching_draws = [record for record in scan_result.draw_records if record.raw_ib_hash == raw_ib_hash]
    if not raw_matching_draws:
        available = ", ".join(scan_result.raw_ib_hashes) or "<none>"
        raise ValueError(
            f"Could not find any draw slices for IB hash {raw_ib_hash} in {scan_result.frame_dump_dir}. "
            f"Available IB hashes: {available}"
        )

    main_draw = max(raw_matching_draws, key=_g_buffer_draw_score)
    post_cs_vb0_hash = _normalize_hash(main_draw.vb0_hash)
    post_cs_vb0_identity = _normalize_hash(main_draw.vb0_identity)
    if not post_cs_vb0_hash or not main_draw.vb0_buf_path:
        raise ValueError(f"Could not resolve the post-CS VB0 for IB hash {raw_ib_hash}")

    # The same source IB can appear in unrelated helper/material passes. For importing
    # the complete skinned model, keep the draw slices that consume the same final
    # post-CS VB0 pool as the main draw. This preserves tiny first-index=0 slices
    # such as the 366-index draw while avoiding unrelated uses of the big IB.
    if post_cs_vb0_identity and "@" in post_cs_vb0_identity:
        matching_draws = [
            record
            for record in raw_matching_draws
            if _normalize_hash(record.vb0_identity) == post_cs_vb0_identity
        ]
    else:
        matching_draws = [
            record
            for record in raw_matching_draws
            if _normalize_hash(record.vb0_hash) == post_cs_vb0_hash
        ]
    if not matching_draws:
        matching_draws = raw_matching_draws

    slice_groups: dict[tuple[int, int], list[_ResolvedDrawRecord]] = defaultdict(list)
    for draw_record in matching_draws:
        slice_groups[(draw_record.first_index, draw_record.index_count)].append(draw_record)

    dispatch_batches = _build_dispatch_batches(
        scan_result.dispatch_records,
        target_u1_hash=post_cs_vb0_hash,
        target_u1_identity=post_cs_vb0_identity,
    )
    if not dispatch_batches:
        raise ValueError(f"Could not find producer dispatches that write {post_cs_vb0_hash}")

    pre_cs_position_paths: list[str | None] = []
    pre_cs_weight_paths: list[str | None] = []
    pre_cs_frame_paths: list[str | None] = []
    pre_cs_position_hashes_by_dispatch: dict[int, str | None] = {}
    for batch in dispatch_batches:
        dispatch_record = scan_result.dispatch_records[batch.dispatch_index]
        position_slot, weight_slot, frame_slot = _resolve_input_slots_for_dispatch(dispatch_record, batch.cs_hash)
        pre_cs_position_paths.append(_artifact_output(dispatch_record.resources, position_slot, "buf"))
        pre_cs_weight_paths.append(_artifact_output(dispatch_record.resources, weight_slot, "buf"))
        pre_cs_frame_paths.append(_artifact_output(dispatch_record.resources, frame_slot, "buf"))
        pre_cs_position_hashes_by_dispatch[batch.dispatch_index] = _artifact_hash(dispatch_record.resources, position_slot)

    pre_cs_vb0_path = _most_common_path(pre_cs_position_paths)
    pre_cs_weight_path = _most_common_path(pre_cs_weight_paths)
    pre_cs_frame_path = _most_common_path(pre_cs_frame_paths)

    reference_draw = max(
        matching_draws,
        key=lambda record: (
            int(record.index_count),
            int(record.event_index),
        ),
    )

    slices: list[DetectedSlice] = []
    for first_index, index_count in sorted(slice_groups):
        draw_group = sorted(slice_groups[(first_index, index_count)], key=lambda record: int(record.event_index))
        slice_info = _cached_index_slice(draw_group[-1].ib_txt_path)
        used_vertex_start = min(slice_info.used_vertex_ids)
        used_vertex_end = max(slice_info.used_vertex_ids)
        producer_batch = None
        for batch in dispatch_batches:
            if used_vertex_start >= batch.start_vertex and used_vertex_end <= batch.end_vertex:
                producer_batch = batch

        selected_draw = draw_group[-1]
        position_hash_hint = (
            None
            if producer_batch is None
            else pre_cs_position_hashes_by_dispatch.get(producer_batch.dispatch_index)
        )
        texcoord_hash_hint = selected_draw.vs_resource_hashes.get("vs-t5") or selected_draw.vs_resource_hashes.get("vs-t3")
        static_match_hashes = _infer_static_match_hashes(
            draw_group,
            texcoord_hash_hint=texcoord_hash_hint,
            position_hash_hint=position_hash_hint,
        )
        texture_slots = _infer_texture_slots(draw_group)
        slices.append(
            DetectedSlice(
                ib_txt_path=selected_draw.ib_txt_path,
                raw_ib_hash=raw_ib_hash,
                display_ib_hash=selected_draw.display_ib_hash,
                draw_indices=tuple(int(record.event_index) for record in draw_group),
                first_index=int(first_index),
                index_count=int(index_count),
                used_vertex_start=int(used_vertex_start),
                used_vertex_end=int(used_vertex_end),
                producer_start_vertex=None if producer_batch is None else int(producer_batch.start_vertex),
                producer_vertex_count=None if producer_batch is None else int(producer_batch.vertex_count),
                vb1_layout_path=selected_draw.vb1_layout_path,
                producer_dispatch_index=None if producer_batch is None else int(producer_batch.dispatch_index),
                producer_cs_hash=None if producer_batch is None else producer_batch.cs_hash,
                producer_t0_hash=None if producer_batch is None else producer_batch.t0_hash,
                last_cs_hash=None if producer_batch is None else producer_batch.cs_hash,
                last_cs_cb0_hash=None if producer_batch is None else producer_batch.cb0_hash,
                last_consumer_draw_index=int(draw_group[-1].event_index),
                match_vs_texcoord_hash=static_match_hashes["texcoord"],
                match_vs_position_hash=static_match_hashes["position"],
                match_vs_outline_hash=static_match_hashes["outline"],
                texture_slots=texture_slots,
            )
        )

    vb0_origin_trace = _build_vb0_origin_trace(
        post_cs_vb0_path=main_draw.vb0_buf_path,
        pre_cs_vb0_path=pre_cs_vb0_path,
        batches=dispatch_batches,
        dispatch_records=scan_result.dispatch_records,
    )

    main_slice = max(slices, key=lambda item: (int(item.index_count), int(item.last_consumer_draw_index or 0)))
    return DetectedModelBundle(
        profile_id=YIHUAN_PROFILE.profile_id,
        frame_dump_dir=scan_result.frame_dump_dir,
        ib_hash=raw_ib_hash,
        model_name=raw_ib_hash,
        vb0_buf_path=str(main_draw.vb0_buf_path),
        pre_cs_vb0_buf_path=str(pre_cs_vb0_path),
        post_cs_vb0_buf_path=str(main_draw.vb0_buf_path),
        t5_buf_path=str(reference_draw.t5_buf_path or ""),
        vb1_buf_path=str(reference_draw.vb1_buf_path or ""),
        t0_buf_path=str(reference_draw.t0_buf_path or ""),
        t1_buf_path=str(reference_draw.t1_buf_path or ""),
        t2_buf_path=str(reference_draw.t2_buf_path or ""),
        t3_buf_path=str(reference_draw.t3_buf_path or ""),
        t7_buf_path=str(reference_draw.t7_buf_path or ""),
        pre_cs_weight_buf_path=str(pre_cs_weight_path),
        pre_cs_frame_buf_path=str(pre_cs_frame_path),
        main_ib_txt_path=main_slice.ib_txt_path,
        slices=slices,
        vb0_origin_trace=vb0_origin_trace,
    )


def discover_yihuan_model(frame_dump_dir: str | None = None, ib_hash: str | None = None) -> DetectedModelBundle:
    """Discover one 异环 model bundle from the current frame-dump directory."""
    scan_result = _scan_yihuan_frame_dump(frame_dump_dir)
    if ib_hash:
        requested_hash = _normalize_hash(ib_hash)
        if requested_hash is None:
            raise ValueError("IB hash is empty.")
        raw_ib_hash = scan_result.display_to_raw_ib.get(requested_hash, requested_hash)
        if raw_ib_hash not in scan_result.raw_ib_hashes:
            available = ", ".join(scan_result.raw_ib_hashes) or "<none>"
            raise ValueError(
                f"Could not find IB hash {requested_hash} in {scan_result.frame_dump_dir}. "
                f"Available IB hashes: {available}"
            )
        return _build_model_bundle(scan_result, raw_ib_hash)

    if not scan_result.raw_ib_hashes:
        raise ValueError(f"No indexed draw slices were discovered in {scan_result.frame_dump_dir}")
    if len(scan_result.raw_ib_hashes) > 1:
        raise ValueError(
            "Multiple IB hashes were discovered in this frame dump. Fill IB Hash explicitly to choose one."
        )
    return _build_model_bundle(scan_result, scan_result.raw_ib_hashes[0])


def resolve_yihuan_bundle_from_ib_hash(
    ib_hash: str,
    *,
    frame_dump_dir: str | None = None,
    use_pre_cs_source: bool = True,
) -> ResolvedImportBundle:
    """Resolve the current 异环 import bundle from one raw or display IB hash."""
    scan_result = _scan_yihuan_frame_dump(frame_dump_dir)
    requested_hash = _normalize_hash(ib_hash)
    if requested_hash is None:
        raise ValueError("IB hash is empty.")

    raw_ib_hash = scan_result.display_to_raw_ib.get(requested_hash, requested_hash)
    detected_model = _build_model_bundle(scan_result, raw_ib_hash)

    selected_slice = None
    if requested_hash != raw_ib_hash:
        for detected_slice in detected_model.slices:
            if _normalize_hash(detected_slice.display_ib_hash) == requested_hash:
                selected_slice = detected_slice
                break
    if selected_slice is None:
        selected_slice = max(
            detected_model.slices,
            key=lambda item: (int(item.index_count), int(item.last_consumer_draw_index or 0)),
        )

    return ResolvedImportBundle(
        profile_id=detected_model.profile_id,
        frame_dump_dir=detected_model.frame_dump_dir,
        ib_hash=detected_model.ib_hash,
        model_name=detected_model.model_name,
        model_slice_count=len(detected_model.slices),
        selected_slice=selected_slice,
        import_variant="pre_cs" if use_pre_cs_source else "post_cs",
        vb0_buf_path=detected_model.pre_cs_vb0_buf_path if use_pre_cs_source else detected_model.post_cs_vb0_buf_path,
        pre_cs_vb0_buf_path=detected_model.pre_cs_vb0_buf_path,
        post_cs_vb0_buf_path=detected_model.post_cs_vb0_buf_path,
        t5_buf_path=detected_model.t5_buf_path,
        vb1_buf_path=detected_model.vb1_buf_path,
        t0_buf_path=detected_model.t0_buf_path,
        t1_buf_path=detected_model.t1_buf_path,
        t2_buf_path=detected_model.t2_buf_path,
        t3_buf_path=detected_model.t3_buf_path,
        t7_buf_path=None if use_pre_cs_source else detected_model.t7_buf_path,
        pre_cs_weight_buf_path=detected_model.pre_cs_weight_buf_path,
        pre_cs_frame_buf_path=detected_model.pre_cs_frame_buf_path,
        vb0_origin_trace=detected_model.vb0_origin_trace,
        last_cs_hash=selected_slice.last_cs_hash,
        last_cs_cb0_hash=selected_slice.last_cs_cb0_hash,
    )
