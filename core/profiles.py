"""Profile registry for importer/exporter behavior."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileDefinition:
    """One supported game/import-export profile."""

    profile_id: str
    label: str
    description: str
    default_frame_dump_dir: str


YIHUAN_PROFILE = ProfileDefinition(
    profile_id="yihuan",
    label="异环",
    description="当前已逆出的异环模型导入导出合同。",
    default_frame_dump_dir="",
)


_PROFILES = {
    YIHUAN_PROFILE.profile_id: YIHUAN_PROFILE,
}


PROFILE_ITEMS = tuple(
    (profile.profile_id, profile.label, profile.description)
    for profile in _PROFILES.values()
)


def get_profile(profile_id: str) -> ProfileDefinition:
    """Return the requested profile or raise a clear error."""
    try:
        return _PROFILES[profile_id]
    except KeyError as exc:  # pragma: no cover - defensive path
        raise ValueError(f"Unsupported profile: {profile_id}") from exc
