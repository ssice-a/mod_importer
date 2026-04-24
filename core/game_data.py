"""Game-specific data conversion helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .models import SectionTransform, Snorm4


Vec3 = tuple[float, float, float]


@dataclass(frozen=True)
class DecodedTangentFrame:
    """One decoded tangent-space frame in Blender space."""

    tangent: Vec3
    normal: Vec3
    bitangent_sign: float


def _normalize3(vector: Vec3) -> Vec3:
    length = (vector[0] * vector[0] + vector[1] * vector[1] + vector[2] * vector[2]) ** 0.5
    if length <= 1e-8:
        return (0.0, 0.0, 0.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


class GameDataConverter(ABC):
    """Abstract game-data conversion layer used by importer/exporter code."""

    profile_id: str

    @abstractmethod
    def to_blender_position(self, position: Vec3) -> Vec3:
        """Convert one game-space position into Blender space."""

    @abstractmethod
    def from_blender_position(self, position: Vec3) -> Vec3:
        """Convert one Blender-space position back into game space."""

    @abstractmethod
    def to_blender_direction(self, direction: Vec3) -> Vec3:
        """Convert one game-space direction into Blender space."""

    @abstractmethod
    def from_blender_direction(self, direction: Vec3) -> Vec3:
        """Convert one Blender-space direction back into game space."""

    @abstractmethod
    def decode_pre_cs_frames(
        self,
        frame_a: list[Snorm4],
        frame_b: list[Snorm4],
    ) -> list[DecodedTangentFrame]:
        """Decode one pre-CS packed tangent frame per vertex into Blender space."""

    @abstractmethod
    def encode_pre_cs_frames(
        self,
        tangents: list[Vec3],
        normals: list[Vec3],
        bitangent_signs: list[float],
    ) -> tuple[list[Snorm4], list[Snorm4]]:
        """Encode Blender-space tangent frames back into the game's pre-CS packed layout."""

    @abstractmethod
    def decode_post_cs_frames(
        self,
        frame_a: list[Snorm4],
        frame_b: list[Snorm4],
    ) -> list[DecodedTangentFrame]:
        """Decode one post-CS packed tangent frame per vertex into Blender space."""

    @abstractmethod
    def to_blender_section_transform(self, section_transform: SectionTransform) -> SectionTransform:
        """Convert one rigid section transform from game space into Blender space."""


class YihuanDataConverter(GameDataConverter):
    """异环 profile conversion rules."""

    profile_id = "yihuan"
    _axis_signs = (-1.0, -1.0, 1.0)
    _position_scale = 0.01

    def to_blender_position(self, position: Vec3) -> Vec3:
        return (
            self._axis_signs[0] * position[0] * self._position_scale,
            self._axis_signs[1] * position[1] * self._position_scale,
            self._axis_signs[2] * position[2] * self._position_scale,
        )

    def from_blender_position(self, position: Vec3) -> Vec3:
        inverse_scale = 1.0 / self._position_scale
        return (
            self._axis_signs[0] * position[0] * inverse_scale,
            self._axis_signs[1] * position[1] * inverse_scale,
            self._axis_signs[2] * position[2] * inverse_scale,
        )

    def to_blender_direction(self, direction: Vec3) -> Vec3:
        return (
            self._axis_signs[0] * direction[0],
            self._axis_signs[1] * direction[1],
            self._axis_signs[2] * direction[2],
        )

    def from_blender_direction(self, direction: Vec3) -> Vec3:
        return (
            self._axis_signs[0] * direction[0],
            self._axis_signs[1] * direction[1],
            self._axis_signs[2] * direction[2],
        )

    def decode_pre_cs_frames(
        self,
        frame_a: list[Snorm4],
        frame_b: list[Snorm4],
    ) -> list[DecodedTangentFrame]:
        if len(frame_a) != len(frame_b):
            raise ValueError("Frame A and Frame B record counts do not match.")

        decoded_frames: list[DecodedTangentFrame] = []
        for record_a, record_b in zip(frame_a, frame_b):
            tangent = _normalize3(self.to_blender_direction(record_a[:3]))
            # This chain stores the normal axis with an inverted sign.
            normal = _normalize3(self.to_blender_direction(tuple(-value for value in record_b[:3])))
            bitangent_sign = 1.0 if record_b[3] >= 0.0 else -1.0
            decoded_frames.append(
                DecodedTangentFrame(
                    tangent=tangent,
                    normal=normal,
                    bitangent_sign=bitangent_sign,
                )
            )
        return decoded_frames

    def encode_pre_cs_frames(
        self,
        tangents: list[Vec3],
        normals: list[Vec3],
        bitangent_signs: list[float],
    ) -> tuple[list[Snorm4], list[Snorm4]]:
        if len(tangents) != len(normals) or len(tangents) != len(bitangent_signs):
            raise ValueError("Tangent, normal, and sign counts do not match.")

        frame_a: list[Snorm4] = []
        frame_b: list[Snorm4] = []
        for tangent, normal, bitangent_sign in zip(tangents, normals, bitangent_signs):
            tangent_game = _normalize3(self.from_blender_direction(tangent))
            normal_game = _normalize3(self.from_blender_direction(normal))
            frame_a.append((tangent_game[0], tangent_game[1], tangent_game[2], 1.0))
            frame_b.append(
                (
                    -normal_game[0],
                    -normal_game[1],
                    -normal_game[2],
                    1.0 if bitangent_sign >= 0.0 else -1.0,
                )
            )
        return frame_a, frame_b

    def decode_post_cs_frames(
        self,
        frame_a: list[Snorm4],
        frame_b: list[Snorm4],
    ) -> list[DecodedTangentFrame]:
        if len(frame_a) != len(frame_b):
            raise ValueError("Post-CS frame record counts do not match.")
        decoded_frames: list[DecodedTangentFrame] = []
        for record_a, record_b in zip(frame_a, frame_b):
            tangent = _normalize3(self.to_blender_direction(record_a[:3]))
            normal = _normalize3(self.to_blender_direction(tuple(-value for value in record_b[:3])))
            decoded_frames.append(
                DecodedTangentFrame(
                    tangent=tangent,
                    normal=normal,
                    bitangent_sign=1.0 if record_b[3] >= 0.0 else -1.0,
                )
            )
        return decoded_frames

    def to_blender_section_transform(self, section_transform: SectionTransform) -> SectionTransform:
        axis_signs = self._axis_signs
        linear_rows = (
            (section_transform.basis_x[0], section_transform.basis_y[0], section_transform.basis_z[0]),
            (section_transform.basis_x[1], section_transform.basis_y[1], section_transform.basis_z[1]),
            (section_transform.basis_x[2], section_transform.basis_y[2], section_transform.basis_z[2]),
        )
        converted_rows = []
        for row_index, row in enumerate(linear_rows):
            converted_rows.append(
                tuple(
                    axis_signs[row_index] * row[column_index] * axis_signs[column_index]
                    for column_index in range(3)
                )
            )

        return SectionTransform(
            basis_x=(converted_rows[0][0], converted_rows[1][0], converted_rows[2][0]),
            basis_y=(converted_rows[0][1], converted_rows[1][1], converted_rows[2][1]),
            basis_z=(converted_rows[0][2], converted_rows[1][2], converted_rows[2][2]),
            translation=(
                axis_signs[0] * section_transform.translation[0] * self._position_scale,
                axis_signs[1] * section_transform.translation[1] * self._position_scale,
                axis_signs[2] * section_transform.translation[2] * self._position_scale,
            ),
            section_selector=section_transform.section_selector,
            section_record=section_transform.section_record,
            source_label=section_transform.source_label,
        )


_CONVERTERS = {
    YihuanDataConverter.profile_id: YihuanDataConverter(),
}


def get_game_data_converter(profile_id: str) -> GameDataConverter:
    """Return the game-data converter for one supported profile."""

    try:
        return _CONVERTERS[profile_id]
    except KeyError as exc:  # pragma: no cover - defensive path
        raise ValueError(f"Unsupported game-data converter profile: {profile_id}") from exc
