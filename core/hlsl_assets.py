"""Export bundled runtime HLSL assets for supported profiles."""

from __future__ import annotations

import shutil
from pathlib import Path

from .profiles import YIHUAN_PROFILE


_PROFILE_HLSL_FILES = {
    YIHUAN_PROFILE.profile_id: (
        "yihuan_gather_t0_cs.hlsl",
    ),
}

_PROFILE_STALE_HLSL_FILES = {
    YIHUAN_PROFILE.profile_id: (
        "yihuan_collect_t0_cs.hlsl",
        "yihuan_gather_t0_cs.hlsl",
        "yihuan_skin_mesh_cs.hlsl",
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
    output_dir = output_root / "BoneStore" / "hlsl"
    output_dir.mkdir(parents=True, exist_ok=True)

    for file_name in required_files:
        source_path = assets_dir / file_name
        if not source_path.is_file():
            raise ValueError(f"Missing bundled HLSL asset: {source_path}")
        target_path = output_dir / file_name
        if target_path.is_file() and target_path.read_bytes() == source_path.read_bytes():
            continue
        shutil.copy2(source_path, target_path)
        for compiled_path in output_dir.glob(f"{target_path.stem}*.bin"):
            compiled_path.unlink()

    for file_name in _PROFILE_STALE_HLSL_FILES.get(profile_id, ()):
        if file_name not in required_files:
            stale_path = output_dir / file_name
            if stale_path.is_file():
                stale_path.unlink()

    legacy_root_hlsl_dir = output_root / "hlsl"
    if legacy_root_hlsl_dir.is_dir():
        for file_name in _PROFILE_STALE_HLSL_FILES.get(profile_id, ()):
            legacy_path = legacy_root_hlsl_dir / file_name
            if legacy_path.is_file():
                legacy_path.unlink()
            for compiled_path in legacy_root_hlsl_dir.glob(f"{Path(file_name).stem}*.bin"):
                compiled_path.unlink()

    return output_dir
