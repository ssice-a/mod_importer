"""Texture conversion helpers used by Blender preview and NTMI export."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_TEXCONV = PLUGIN_ROOT / "assets" / "tools" / "texconv" / "texconv.exe"
CACHE_ROOT = PLUGIN_ROOT / ".modimp_cache" / "textures"


class TextureConversionError(RuntimeError):
    """Raised when a texture conversion failed."""


class TextureConversionUnavailable(TextureConversionError):
    """Raised when texconv cannot be found."""


_KNOWN_DXGI_FORMATS = (
    "R32G32B32A32_FLOAT",
    "R16G16B16A16_FLOAT",
    "R16G16B16A16_UNORM",
    "R16G16B16A16_SNORM",
    "R10G10B10A2_UNORM",
    "R8G8B8A8_UNORM_SRGB",
    "R8G8B8A8_UNORM",
    "R8G8B8A8_SNORM",
    "B8G8R8A8_UNORM_SRGB",
    "B8G8R8A8_UNORM",
    "BC7_UNORM_SRGB",
    "BC7_UNORM",
    "BC6H_UF16",
    "BC6H_SF16",
    "BC5_UNORM",
    "BC5_SNORM",
    "BC4_UNORM",
    "BC4_SNORM",
    "BC3_UNORM_SRGB",
    "BC3_UNORM",
    "BC2_UNORM_SRGB",
    "BC2_UNORM",
    "BC1_UNORM_SRGB",
    "BC1_UNORM",
    "R16G16_FLOAT",
    "R16G16_UNORM",
    "R16G16_SNORM",
    "R8G8_UNORM",
    "R8G8_SNORM",
    "R16_FLOAT",
    "R16_UNORM",
    "R8_UNORM",
)


def _cache_key(path: Path) -> str:
    try:
        stat = path.stat()
        payload = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8", errors="ignore")
    except OSError:
        payload = str(path).encode("utf-8", errors="ignore")
    return hashlib.sha1(payload).hexdigest()[:12]


def find_texconv() -> Path | None:
    """Return the preferred texconv executable path."""

    env_path = os.environ.get("MODIMP_TEXCONV")
    if env_path:
        candidate = Path(env_path)
        if candidate.is_file():
            return candidate

    if BUNDLED_TEXCONV.is_file():
        return BUNDLED_TEXCONV

    for name in ("texconv.exe", "texconv"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _decode_process_output(payload: bytes | None) -> str:
    if not payload:
        return ""
    return payload.decode("utf-8", errors="replace").strip()


def _run_texconv(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    texconv = find_texconv()
    if texconv is None:
        raise TextureConversionUnavailable(
            "texconv.exe was not found. Expected bundled tool at "
            f"{BUNDLED_TEXCONV}, or set MODIMP_TEXCONV."
        )
    try:
        return subprocess.run(
            [str(texconv), "-nologo", *args],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        details = _decode_process_output(exc.stderr) or _decode_process_output(exc.stdout)
        raise TextureConversionError(f"texconv failed: {details}") from exc


def _find_converted_file(output_dir: Path, source: Path, extension: str) -> Path:
    expected = output_dir / f"{source.stem}.{extension}"
    if expected.is_file():
        return expected
    expected_upper = output_dir / f"{source.stem}.{extension.upper()}"
    if expected_upper.is_file():
        return expected_upper
    matches = sorted(output_dir.glob(f"*.{extension}")) + sorted(output_dir.glob(f"*.{extension.upper()}"))
    if matches:
        return matches[0]
    raise TextureConversionError(f"texconv did not produce a .{extension} file for {source}.")


def convert_dds_to_png_preview(source_path: str | Path) -> Path:
    """Convert a DDS source to a cached PNG that Blender can preview."""

    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".dds":
        return source

    cache_key = _cache_key(source)
    preview_dir = CACHE_ROOT / "png_preview"
    target = preview_dir / f"{source.stem}-{cache_key}.png"
    if target.is_file():
        return target

    work_dir = preview_dir / f"_tmp_{cache_key}"
    work_dir.mkdir(parents=True, exist_ok=True)
    # WIC PNG cannot represent several game/RT DDS formats directly
    # (for example R11G11B10_FLOAT), so normalize preview images first.
    _run_texconv(["-y", "-ft", "png", "-f", "R8G8B8A8_UNORM", "-o", str(work_dir), str(source)])
    produced = _find_converted_file(work_dir, source, "png")
    preview_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(produced, target)
    return target


def load_image_for_blender(source_path: str | Path, *, color_space: str | None = None):
    """Load an image in Blender, converting DDS to a cached PNG when needed."""

    import bpy  # Imported lazily so this module remains importable outside Blender tests.

    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(source)

    converted_preview: Path | None = None
    load_path = source
    if source.suffix.lower() == ".dds":
        # Prefer PNG previews for DDS even if Blender can technically load a
        # subset of DDS files. It gives consistent thumbnails/material previews.
        try:
            converted_preview = convert_dds_to_png_preview(source)
            load_path = converted_preview
        except TextureConversionError:
            load_path = source

    try:
        image = bpy.data.images.load(str(load_path), check_existing=True)
    except RuntimeError:
        if source.suffix.lower() != ".dds" or converted_preview is not None:
            raise
        converted_preview = convert_dds_to_png_preview(source)
        load_path = converted_preview
        image = bpy.data.images.load(str(load_path), check_existing=True)

    if converted_preview is not None:
        image["modimp_original_texture_path"] = str(source)
        image["modimp_converted_preview_path"] = str(converted_preview)

    if color_space and hasattr(image, "colorspace_settings"):
        try:
            image.colorspace_settings.name = color_space
        except TypeError:
            pass
    return image


def blender_image_export_source(image) -> str:
    """Return the original DDS when an image is our cached preview, otherwise its current filepath."""

    if image is None:
        return ""
    filepath = str(getattr(image, "filepath", "") or getattr(image, "filepath_raw", "") or "").strip()
    original_path = str(image.get("modimp_original_texture_path", "") or "").strip()
    preview_path = str(image.get("modimp_converted_preview_path", "") or "").strip()
    if filepath and original_path and preview_path:
        try:
            if Path(filepath).resolve() == Path(preview_path).resolve():
                return original_path
        except OSError:
            pass
    return filepath


def parse_dxgi_format_from_path(path: str | Path) -> str:
    """Parse common FrameAnalysis format suffixes, e.g. d77b480e-BC1_UNORM.dds."""

    text = str(Path(path).name).upper()
    for dxgi_format in sorted(_KNOWN_DXGI_FORMATS, key=len, reverse=True):
        if re.search(rf"(^|[-_]){re.escape(dxgi_format)}($|[-_.])", text):
            return dxgi_format
    return ""


def default_dds_format(*, slot: str = "", semantic: str = "", source_path: str | Path = "") -> str:
    """Choose a conservative DDS format when no original DXGI format is available."""

    parsed = parse_dxgi_format_from_path(source_path)
    if parsed:
        return parsed
    semantic = str(semantic or "").lower()
    slot = str(slot or "").lower()
    if semantic == "base_color" or slot == "ps-t7":
        return "BC7_UNORM_SRGB"
    return "BC7_UNORM"


def write_game_texture(
    source_path: str | Path,
    destination: str | Path,
    *,
    slot: str = "",
    semantic: str = "",
    dxgi_format: str = "",
) -> Path:
    """Write a game-ready DDS texture, copying DDS sources and converting other image types."""

    source = Path(source_path)
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        raise FileNotFoundError(source)

    if source.suffix.lower() == ".dds":
        shutil.copy2(source, destination_path)
        return destination_path

    target_format = dxgi_format or default_dds_format(slot=slot, semantic=semantic, source_path=source)
    cache_key = _cache_key(source)
    work_dir = CACHE_ROOT / "dds_export" / f"_tmp_{cache_key}_{target_format}"
    work_dir.mkdir(parents=True, exist_ok=True)
    _run_texconv(
        [
            "-y",
            "-ft",
            "dds",
            "-f",
            target_format,
            "-m",
            "0",
            "-o",
            str(work_dir),
            str(source),
        ]
    )
    produced = _find_converted_file(work_dir, source, "dds")
    shutil.copy2(produced, destination_path)
    return destination_path
