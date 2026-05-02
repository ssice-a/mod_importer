"""Internal dataclasses used by the importer/exporter."""

from __future__ import annotations

from dataclasses import dataclass


PackedHalf2x4 = tuple[
    tuple[float, float],
    tuple[float, float],
    tuple[float, float],
    tuple[float, float],
]
Snorm4 = tuple[float, float, float, float]


@dataclass(frozen=True)
class IndexSlice:
    """One draw-call slice parsed from a 3DMigoto IB text dump."""

    source_path: str
    format_name: str
    first_index: int
    index_count: int
    triangles: list[tuple[int, int, int]]
    used_vertex_ids: tuple[int, ...]


@dataclass(frozen=True)
class CompactedGeometry:
    """Mesh data after remapping the used vertex ids into a dense range."""

    positions: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int]]
    packed_uv_entries: list[PackedHalf2x4]
    original_vertex_ids: list[int]


@dataclass(frozen=True)
class DispatchBatch:
    """One compute dispatch that writes into the shared post-CS outputs."""

    dispatch_index: int
    cs_hash: str
    cb0_hash: str
    cb0_buf_path: str
    t0_hash: str | None
    t0_buf_path: str | None
    start_vertex: int
    vertex_count: int
    u0_hash: str | None
    u1_hash: str | None

    @property
    def end_vertex(self) -> int:
        return self.start_vertex + self.vertex_count - 1


@dataclass(frozen=True)
class Vb0OriginStage:
    """One observed stage in the b1c65387 compute chain."""

    stage_name: str
    buffer_path: str
    covered_vertex_ranges: list[tuple[int, int]]


@dataclass(frozen=True)
class Vb0OriginTrace:
    """The current best-effort trace back to the bind/rest-like VB0."""

    final_vb0_path: str
    closest_rest_pose_path: str
    note: str
    source_hashes: list[str]
    stages: list[Vb0OriginStage]


@dataclass(frozen=True)
class DetectedSlice:
    """One detected draw slice that belongs to a larger imported model."""

    ib_txt_path: str
    raw_ib_hash: str
    display_ib_hash: str | None
    draw_indices: tuple[int, ...]
    first_index: int
    index_count: int
    used_vertex_start: int
    used_vertex_end: int
    producer_start_vertex: int | None
    producer_vertex_count: int | None
    vb1_layout_path: str | None
    producer_dispatch_index: int | None
    producer_cs_hash: str | None
    producer_t0_hash: str | None
    last_cs_hash: str | None
    last_cs_cb0_hash: str | None
    last_consumer_draw_index: int | None
    depth_vs_hashes: tuple[str, ...]
    gbuffer_vs_hashes: tuple[str, ...]


@dataclass(frozen=True)
class DetectedModelBundle:
    """Resolved resources for the currently understood 异环 frame-dump model."""

    profile_id: str
    frame_dump_dir: str
    ib_hash: str
    model_name: str
    vb0_buf_path: str
    pre_cs_vb0_buf_path: str
    post_cs_vb0_buf_path: str
    t5_buf_path: str
    vb1_buf_path: str
    t0_buf_path: str
    t1_buf_path: str
    t2_buf_path: str
    t3_buf_path: str
    t7_buf_path: str
    pre_cs_weight_buf_path: str
    pre_cs_frame_buf_path: str
    main_ib_txt_path: str
    slices: list[DetectedSlice]
    vb0_origin_trace: Vb0OriginTrace


@dataclass(frozen=True)
class ResolvedImportBundle:
    """All resources resolved from one IB hash and one selected slice."""

    profile_id: str
    frame_dump_dir: str
    ib_hash: str
    model_name: str
    model_slice_count: int
    selected_slice: DetectedSlice
    import_variant: str
    vb0_buf_path: str
    pre_cs_vb0_buf_path: str
    post_cs_vb0_buf_path: str
    t5_buf_path: str
    vb1_buf_path: str
    t0_buf_path: str
    t1_buf_path: str
    t2_buf_path: str
    t3_buf_path: str
    t7_buf_path: str | None
    pre_cs_weight_buf_path: str
    pre_cs_frame_buf_path: str
    vb0_origin_trace: Vb0OriginTrace
    last_cs_hash: str | None
    last_cs_cb0_hash: str | None
