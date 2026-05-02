"""Export bundled runtime HLSL assets for supported profiles."""

from __future__ import annotations

import shutil
from pathlib import Path

from .profiles import YIHUAN_PROFILE


_PROFILE_HLSL_FILES = {
    YIHUAN_PROFILE.profile_id: (
        ("yihuan_collect_t0_cs.hlsl", "BoneStore/hlsl"),
        ("yihuan_build_local_t0_pose_slot_85b15a7f_cs.hlsl", "BoneStore/hlsl"),
        ("yihuan_skin_scratch_85b15a7f_cs.hlsl", "BoneStore/hlsl"),
        ("yihuan_store_global_t0_pose_slot_85b15a7f_cs.hlsl", "BoneStore/hlsl"),
        ("yihuan_clear_pose_slots_85b15a7f_cs.hlsl", "hlsl"),
        ("yihuan_find_or_alloc_pose_slot_85b15a7f_cs.hlsl", "hlsl"),
        ("yihuan_select_pose_slot_85b15a7f_cs.hlsl", "hlsl"),
        ("yihuan_store_scratch_pose_slot_85b15a7f_cs.hlsl", "hlsl"),
        ("yihuan_publish_pose_slot_85b15a7f_cs.hlsl", "hlsl"),
    ),
}


def export_profile_hlsl_assets(profile_id: str, output_directory: str | Path) -> Path:
    """Copy bundled HLSL assets for the selected profile into the export directory."""
    try:
        required_files = _PROFILE_HLSL_FILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported HLSL asset profile: {profile_id}") from exc

    assets_dir = Path(__file__).resolve().parent.parent / "assets" / "hlsl"
    if not assets_dir.is_dir():
        raise ValueError(f"Bundled HLSL assets directory not found: {assets_dir}")

    output_root = Path(output_directory).resolve()
    first_output_dir: Path | None = None
    for file_name, relative_dir in required_files:
        source_path = assets_dir / file_name
        if not source_path.is_file():
            raise ValueError(f"Missing bundled HLSL asset: {source_path}")
        output_dir = output_root / relative_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        if first_output_dir is None:
            first_output_dir = output_dir
        target_path = output_dir / file_name
        if target_path.is_file() and target_path.read_bytes() == source_path.read_bytes():
            continue
        shutil.copy2(source_path, target_path)
        for compiled_path in output_dir.glob(f"{target_path.stem}*.bin"):
            compiled_path.unlink()

    return first_output_dir or (output_root / "hlsl")
