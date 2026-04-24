"""Export bundled runtime HLSL assets for supported profiles."""

from __future__ import annotations

import shutil
from pathlib import Path

from .profiles import YIHUAN_PROFILE


_PROFILE_HLSL_FILES = {
    YIHUAN_PROFILE.profile_id: (
        "yihuan_collect_t0_cs.hlsl",
        "yihuan_gather_t0_cs.hlsl",
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

    output_dir = Path(output_directory).resolve() / "hlsl"
    output_dir.mkdir(parents=True, exist_ok=True)

    for file_name in required_files:
        source_path = assets_dir / file_name
        if not source_path.is_file():
            raise ValueError(f"Missing bundled HLSL asset: {source_path}")
        shutil.copy2(source_path, output_dir / file_name)

    return output_dir
