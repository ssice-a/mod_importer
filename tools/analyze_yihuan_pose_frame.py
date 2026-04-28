from __future__ import annotations

import argparse
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path


DEFAULT_FRAME_DIR = Path(r"E:\yh\FrameAnalysis-2026-04-28-030811")
DEFAULT_MAIN_INI = Path(r"E:\yh\Mods\83527398-BoneStore\83527398.ini")
DEFAULT_BONESTORE_INI = Path(r"E:\yh\Mods\83527398-BoneStore\83527398-BoneStore.ini")

BODY_IB_HASH = "83527398"
BODY_FIRST_INDEX = 29448
BODY_INDEX_COUNT = 115740
LOCAL_VERTEX_COUNT = 28338
NATIVE_BODY_VERTEX_START = 7880
NATIVE_BODY_VERTEX_COUNT = 24820
REQUIRED_NATIVE_VERTEX_COUNT = NATIVE_BODY_VERTEX_START + NATIVE_BODY_VERTEX_COUNT
SAMPLE_COUNT = 128
FEATURE_RMS_THRESHOLD = 0.05
MAX_POSE_SLOTS = 32

# Runtime pose slots are keyed from the 85b15a7f native body slice. With that
# narrower feature, f33 and 1e2a can safely update the same pose slot instead of
# aliasing unrelated whole-buffer afterimage passes.
CS_HASHES = {
    "f33fea3cca2704e4": "f33",
    "1e2a9061eadfeb6c": "1e2a",
}

VS_BRANCHES = {
    "9d62ac15f0b2cf93": ("4100", "if vs == 4100"),
    "83b4d27352a7b440": ("4200", "if vs == 4200 || vs == 4201"),
    "8dfd5e4f9395f1c0": ("4201", "if vs == 4200 || vs == 4201"),
    "90e5f30bc8bfe0ae": ("4202", "if  vs == 4202 || vs == 4203"),
    "95c1180ad8070a67": ("4203", "if  vs == 4202 || vs == 4203"),
    "4a3cae54b763970f": ("4204", "if vs == 4204"),
    "3fbb880f44604182": ("4300", "if vs == 4300"),
}


@dataclass(frozen=True)
class TargetDraw:
    event_id: int
    vs_hash: str
    ps_hash: str
    ib_txt_path: Path
    vb0_path: Path | None


@dataclass(frozen=True)
class NativeOutput:
    event_id: int
    cs_hash: str
    stage_name: str
    path: Path
    vertex_count: int


@dataclass
class PoseSlot:
    slot: int
    feature: list[tuple[float, float, float]]
    last_event_id: int
    last_stage_name: str
    updates: int = 1


@dataclass(frozen=True)
class CollectMeta:
    label: str
    expected_start: int
    expected_count: int
    global_bone_base: int
    bone_count: int

    @property
    def global_end_inclusive(self) -> int:
        return self.global_bone_base + self.bone_count - 1


class Analyzer:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def check(self, condition: bool, message: str) -> None:
        if condition:
            print(f"[OK] {message}")
        else:
            print(f"[FAIL] {message}")
            self.failures.append(message)

    def warn_if(self, condition: bool, message: str) -> None:
        if condition:
            print(f"[WARN] {message}")
            self.warnings.append(message)


def parse_event_id(path: Path) -> int:
    return int(path.name.split("-", 1)[0])


def read_index_slice_header(path: Path) -> tuple[int | None, int | None]:
    first_index: int | None = None
    index_count: int | None = None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip().lower()
            if line.startswith("first index:"):
                first_index = int(line.split(":", 1)[1].strip())
            elif line.startswith("index count:"):
                index_count = int(line.split(":", 1)[1].strip())
            if first_index is not None and index_count is not None:
                break
    return first_index, index_count


def find_single_vb0(frame_dir: Path, event_id: int, vs_hash: str, ps_hash: str) -> Path | None:
    matches = sorted(frame_dir.glob(f"{event_id:06d}-vb0=*-vs={vs_hash}-ps={ps_hash}.buf"))
    if len(matches) != 1:
        return None
    return matches[0]


def find_target_draws(frame_dir: Path) -> list[TargetDraw]:
    draws: list[TargetDraw] = []
    pattern = re.compile(r"^(\d{6})-ib=([0-9a-f]+)-vs=([0-9a-f]+)-ps=([0-9a-f]+)\.txt$", re.I)
    for path in sorted(frame_dir.glob(f"*-ib={BODY_IB_HASH}-*.txt")):
        match = pattern.match(path.name)
        if match is None:
            continue
        event_id = int(match.group(1))
        first_index, index_count = read_index_slice_header(path)
        if first_index != BODY_FIRST_INDEX or index_count != BODY_INDEX_COUNT:
            continue
        vs_hash = match.group(3).lower()
        ps_hash = match.group(4).lower()
        draws.append(
            TargetDraw(
                event_id=event_id,
                vs_hash=vs_hash,
                ps_hash=ps_hash,
                ib_txt_path=path,
                vb0_path=find_single_vb0(frame_dir, event_id, vs_hash, ps_hash),
            )
        )
    return draws


def event_lines(log_lines: list[str], event_id: int) -> list[str]:
    prefix = f"{event_id:06d} "
    return [line for line in log_lines if line.startswith(prefix)]


def summarize_log_health(analyzer: Analyzer, log_lines: list[str]) -> None:
    hard_patterns = ("failed", "error", "invalid", "substantiate")
    hard_hits = [
        line.strip()
        for line in log_lines
        if any(pattern in line.lower() for pattern in hard_patterns)
    ]
    analyzer.check(not hard_hits, "log has no failed/error/invalid/substantiate lines")
    if hard_hits:
        for line in hard_hits[:8]:
            print(f"  {line}")

    null_copy_count = sum("copy source was null" in line.lower() for line in log_lines)
    if null_copy_count:
        print(f"[INFO] Copy source was NULL count: {null_copy_count} (usually harmless for restoring empty cs-u slots)")


def analyze_target_draws(analyzer: Analyzer, log_lines: list[str], draws: list[TargetDraw]) -> None:
    analyzer.check(bool(draws), f"found target IB draw first={BODY_FIRST_INDEX} count={BODY_INDEX_COUNT}")
    print(f"[INFO] target draw events: {', '.join(f'{draw.event_id:06d}' for draw in draws) or 'none'}")

    for draw in draws:
        lines = event_lines(log_lines, draw.event_id)
        lower_blob = "\n".join(lines).lower()
        branch_info = VS_BRANCHES.get(draw.vs_hash)
        readable_vs = branch_info[0] if branch_info else "unknown"
        print(f"[DRAW {draw.event_id:06d}] vs={draw.vs_hash} filter={readable_vs} ps={draw.ps_hash}")

        analyzer.check(draw.vb0_path is not None, f"{draw.event_id:06d} has a dumped native vb0 for feature matching")
        if branch_info is None:
            analyzer.warn_if(True, f"{draw.event_id:06d} uses an unrecognized VS hash: {draw.vs_hash}")
            continue

        branch_pattern = branch_info[1].lower()
        analyzer.check(
            f"{branch_pattern}: true" in lower_blob,
            f"{draw.event_id:06d} entered expected branch {branch_pattern}",
        )
        analyzer.check(
            "run = customshader_yihuanselectposeslot_85b15a7f" in lower_blob,
            f"{draw.event_id:06d} ran SelectPoseSlot",
        )
        analyzer.check(
            "run = customshader_yihuanpublishposeslot_85b15a7f" in lower_blob,
            f"{draw.event_id:06d} ran PublishPoseSlot",
        )
        drawindexed_count = lower_blob.count("drawindexed")
        analyzer.check(drawindexed_count >= 2, f"{draw.event_id:06d} issued replacement DrawIndexed calls ({drawindexed_count})")


def file_vertex_count(path: Path) -> int:
    size = path.stat().st_size
    if size % 12 != 0:
        raise ValueError(f"{path} size is not divisible by float3 stride: {size}")
    return size // 12


def find_native_outputs(frame_dir: Path) -> list[NativeOutput]:
    outputs: list[NativeOutput] = []
    pattern = re.compile(r"^(\d{6})-u1=([0-9a-f]+)-cs=([0-9a-f]+)\.buf$", re.I)
    for path in sorted(frame_dir.glob("*-u1=*-cs=*.buf")):
        match = pattern.match(path.name)
        if match is None:
            continue
        cs_hash = match.group(3).lower()
        stage_name = CS_HASHES.get(cs_hash)
        if stage_name is None:
            continue
        vertex_count = file_vertex_count(path)
        if vertex_count < REQUIRED_NATIVE_VERTEX_COUNT:
            continue
        outputs.append(
            NativeOutput(
                event_id=int(match.group(1)),
                cs_hash=cs_hash,
                stage_name=stage_name,
                path=path,
                vertex_count=vertex_count,
            )
        )
    return outputs


def feature_from_float3_buffer(path: Path, *, sample_count: int = SAMPLE_COUNT) -> tuple[list[tuple[float, float, float]], int]:
    data = path.read_bytes()
    if len(data) % 12 != 0:
        raise ValueError(f"{path} size is not divisible by 12")
    vertex_count = len(data) // 12
    if vertex_count < REQUIRED_NATIVE_VERTEX_COUNT:
        return [], vertex_count
    feature: list[tuple[float, float, float]] = []
    for sample_index in range(sample_count):
        vertex_index = NATIVE_BODY_VERTEX_START
        if sample_count > 1 and NATIVE_BODY_VERTEX_COUNT > 1:
            vertex_index += (sample_index * (NATIVE_BODY_VERTEX_COUNT - 1)) // (sample_count - 1)
        feature.append(struct.unpack_from("<3f", data, vertex_index * 12))
    return feature, vertex_count


def feature_rms(
    left: list[tuple[float, float, float]],
    right: list[tuple[float, float, float]],
) -> float:
    if len(left) != len(right):
        raise ValueError("feature length mismatch")
    sum_sq = 0.0
    for left_item, right_item in zip(left, right):
        dx = left_item[0] - right_item[0]
        dy = left_item[1] - right_item[1]
        dz = left_item[2] - right_item[2]
        sum_sq += dx * dx + dy * dy + dz * dz
    return math.sqrt(sum_sq / max(1, len(left)))


def best_pose_slot(slots: list[PoseSlot], feature: list[tuple[float, float, float]]) -> tuple[int | None, float]:
    best_slot: int | None = None
    best_error = float("inf")
    for slot in slots:
        error = feature_rms(feature, slot.feature)
        if error < best_error:
            best_error = error
            best_slot = slot.slot
    return best_slot, best_error


def simulate_pose_slots(
    native_outputs: list[NativeOutput],
    draws: list[TargetDraw],
) -> tuple[list[PoseSlot], dict[int, tuple[int | None, float, int]]]:
    slots: list[PoseSlot] = []
    draw_by_event: dict[int, list[TargetDraw]] = {}
    for draw in draws:
        draw_by_event.setdefault(draw.event_id, []).append(draw)

    outputs_by_event: dict[int, list[NativeOutput]] = {}
    for output in native_outputs:
        outputs_by_event.setdefault(output.event_id, []).append(output)

    draw_matches: dict[int, tuple[int | None, float, int]] = {}
    event_ids = sorted(set(outputs_by_event) | set(draw_by_event))
    for event_id in event_ids:
        for output in outputs_by_event.get(event_id, []):
            feature, _vertex_count = feature_from_float3_buffer(output.path)
            if not feature:
                continue
            if not feature:
                draw_matches[draw.event_id] = (None, float("inf"), vertex_count)
                continue
            best_slot, best_error = best_pose_slot(slots, feature)
            if best_slot is not None and best_error <= FEATURE_RMS_THRESHOLD:
                slot = slots[best_slot]
                slot.feature = feature
                slot.last_event_id = output.event_id
                slot.last_stage_name = output.stage_name
                slot.updates += 1
            elif len(slots) < MAX_POSE_SLOTS:
                slots.append(
                    PoseSlot(
                        slot=len(slots),
                        feature=feature,
                        last_event_id=output.event_id,
                        last_stage_name=output.stage_name,
                    )
                )
            elif best_slot is not None:
                slot = slots[best_slot]
                slot.feature = feature
                slot.last_event_id = output.event_id
                slot.last_stage_name = output.stage_name
                slot.updates += 1

        for draw in draw_by_event.get(event_id, []):
            if draw.vb0_path is None:
                draw_matches[draw.event_id] = (None, float("inf"), len(slots))
                continue
            feature, vertex_count = feature_from_float3_buffer(draw.vb0_path)
            best_slot, best_error = best_pose_slot(slots, feature)
            draw_matches[draw.event_id] = (best_slot, best_error, vertex_count)

    return slots, draw_matches


def analyze_feature_matching(
    analyzer: Analyzer,
    frame_dir: Path,
    draws: list[TargetDraw],
) -> None:
    native_outputs = find_native_outputs(frame_dir)
    analyzer.check(bool(native_outputs), "found dumped f33/1e2a native u1 outputs for offline feature simulation")
    print(
        "[INFO] native pose outputs: "
        + ", ".join(f"{item.stage_name}@{item.event_id:06d}:{item.vertex_count}" for item in native_outputs)
    )

    slots, draw_matches = simulate_pose_slots(native_outputs, draws)
    print(f"[INFO] simulated pose slots: {len(slots)} / {MAX_POSE_SLOTS}")
    for slot in slots:
        print(
            f"  slot {slot.slot}: last={slot.last_stage_name}@{slot.last_event_id:06d}, updates={slot.updates}"
        )
    analyzer.warn_if(len(slots) >= MAX_POSE_SLOTS, f"simulated slot count reached MaxPoseSlots={MAX_POSE_SLOTS}")

    for draw in draws:
        slot, rms, vertex_count = draw_matches.get(draw.event_id, (None, float("inf"), 0))
        analyzer.check(
            slot is not None and rms <= FEATURE_RMS_THRESHOLD,
            f"{draw.event_id:06d} native vb0 feature matches a pose slot (slot={slot}, rms={rms:.6f}, vertices={vertex_count})",
        )


def read_collect_metas(path: Path) -> list[CollectMeta]:
    metas: list[CollectMeta] = []
    section_label: str | None = None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
                if section.lower().startswith("resourcecollectmeta_"):
                    section_label = section[len("ResourceCollectMeta_") :]
                else:
                    section_label = None
                continue
            if section_label is None or not line.lower().startswith("data ="):
                continue
            values = [int(item) for item in line.split("=", 1)[1].split()]
            if len(values) != 4:
                raise ValueError(f"{path}: bad collect meta {section_label}: {values}")
            metas.append(CollectMeta(section_label, values[0], values[1], values[2], values[3]))
            section_label = None
    return metas


def analyze_collect_coverage(analyzer: Analyzer, log_lines: list[str], bonestore_ini: Path) -> None:
    metas = read_collect_metas(bonestore_ini)
    analyzer.check(bool(metas), f"read collect metas from {bonestore_ini}")
    if not metas:
        return

    max_end = max(meta.global_end_inclusive for meta in metas)
    print(f"[INFO] collect meta count={len(metas)}, max global bone id={max_end}")
    e8_meta = next((meta for meta in metas if meta.label.lower() == "e8bfb30d_15786_366"), None)
    analyzer.check(e8_meta is not None, "366/e8bfb30d collect meta exists")
    if e8_meta is not None:
        analyzer.check(
            e8_meta.global_bone_base == 237 and e8_meta.bone_count == 46,
            f"366/e8bfb30d maps to global bones 237..282 (actual {e8_meta.global_bone_base}..{e8_meta.global_end_inclusive})",
        )

    lower_log = "\n".join(log_lines).lower()
    for meta in metas:
        run_token = f"_collectt0_{meta.label.lower()}] run = customshader_collectt0"
        analyzer.check(run_token in lower_log, f"collect hook ran for {meta.label}")


def analyze_main_ini_static(analyzer: Analyzer, main_ini: Path) -> None:
    text = main_ini.read_text(encoding="utf-8", errors="replace")
    lower_text = text.lower()
    analyzer.check(
        "[resourceyihuan_85b15a7f_part00_nativefeatureposition]" in lower_text,
        "main INI declares NativeFeaturePosition staging buffer",
    )
    analyzer.check(
        "nativefeatureposition = copy resourceyihuanrestorecsu1" in lower_text,
        "skin-stage feature source is copied from native cs-u1 before CS read",
    )
    analyzer.check(
        "nativefeatureposition = copy resourceyihuanrestorevb0" in lower_text,
        "draw-stage feature source is copied from native vb0 before CS read",
    )
    analyzer.check(
        "cs-t0 = resourceyihuanrestorecsu1" not in lower_text,
        "main INI does not bind borrowed cs-u1 directly as feature CS input",
    )
    analyzer.check(
        "cs-t0 = resourceyihuanrestorevb0" not in lower_text,
        "main INI does not bind borrowed vb0 directly as feature CS input",
    )
    analyzer.check(
        "if cs == 3300 || cs == 3301" in lower_text,
        "main INI lets f33/1e2a update body-slice pose slots",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze Yihuan 85b15a7f pose-cache FrameAnalysis logs.")
    parser.add_argument("--frame-dir", type=Path, default=DEFAULT_FRAME_DIR)
    parser.add_argument("--main-ini", type=Path, default=DEFAULT_MAIN_INI)
    parser.add_argument("--bonestore-ini", type=Path, default=DEFAULT_BONESTORE_INI)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frame_dir: Path = args.frame_dir
    log_path = frame_dir / "log.txt"
    analyzer = Analyzer()

    print(f"[INFO] frame dir: {frame_dir}")
    print(f"[INFO] main ini: {args.main_ini}")
    print(f"[INFO] bonestore ini: {args.bonestore_ini}")
    analyzer.check(frame_dir.is_dir(), "frame directory exists")
    analyzer.check(log_path.is_file(), "log.txt exists")
    analyzer.check(args.main_ini.is_file(), "main INI exists")
    analyzer.check(args.bonestore_ini.is_file(), "BoneStore INI exists")
    if not frame_dir.is_dir() or not log_path.is_file():
        return 2

    log_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    summarize_log_health(analyzer, log_lines)

    draws = find_target_draws(frame_dir)
    analyze_target_draws(analyzer, log_lines, draws)
    analyze_main_ini_static(analyzer, args.main_ini)
    analyze_collect_coverage(analyzer, log_lines, args.bonestore_ini)
    analyze_feature_matching(analyzer, frame_dir, draws)

    print(f"[SUMMARY] failures={len(analyzer.failures)} warnings={len(analyzer.warnings)}")
    if analyzer.failures:
        print("[SUMMARY] failing checks:")
        for failure in analyzer.failures:
            print(f"  - {failure}")
    return 1 if analyzer.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
