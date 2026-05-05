# NTMI Mod Importer

English | [中文](README.md)

NTMI Mod Importer is a Blender add-on for the modified 3DMigoto / NTMI fast path workflow. It imports character meshes from FrameAnalysis, lets you edit them in Blender, then exports game-ready buffers and optional INI files.

The add-on targets the new NTMI runtime pipeline. It no longer generates the old BoneStore, PoseSlot, ShaderRegex, or `ShaderOverride + checktextureoverride = ib` workflow.

## Requirements

- Windows.
- Blender 4.0 or newer. The current development environment uses Blender 5.0.
- A modified 3DMigoto build that supports NTMI fast TextureOverride and Collector syntax.
- [NTMI-PACKAGE v0.3.0](https://github.com/ssice-a/NTMI-PACKAGE/releases/tag/NTMI-PACKAGE-v0.3.0).
- [XXMI-Libs-Package v0.3.0](https://github.com/ssice-a/XXMI-Libs-Package/releases/tag/v0.3.0).
- To install the runtime packages, download both releases, extract them, then copy their contents into your 3DMigoto folder and overwrite the matching files.
- FrameAnalysis dumps for the target character and target IB.
- `texconv.exe` for DDS preview and conversion. The add-on looks for `assets/tools/texconv/texconv.exe` by default.

## Quick Start

1. Enable the add-on in Blender.
2. Open `View3D > Sidebar > Mod Importer`.
3. Choose the FrameAnalysis folder.
4. Enter the target IB hash.
5. Click `Analyze`.
6. Mark base color, normal, material, or effect textures if needed.
7. Click `Import`.
8. Edit the model in Blender.
9. Choose an export directory.
10. Click `Export`.

Export modes:

- `Buffers Only`: export game buffers and textures only.
- `Buffers + INI`: export buffers, textures, and the NTMI fast-path INI.

## Documentation

- [INI Syntax](docs/en/ini_syntax.md): Collector, dynamic resource, match, draw, and palette rules.
- [Plugin Workflow](docs/en/plugin_workflow.md): Analyze, Import, edit, and export workflow.
- [Texture Marking](docs/en/texture_marking.md): texture candidates, DDS preview, material nodes, and export rules.
- [Troubleshooting](docs/en/troubleshooting.md): common import, texture, bone, INI, and in-game issues.

## Design Principles

- Performance first: do not generate broad VS checks, legacy ShaderRegex fallback, or runtime work that can be resolved during analysis/export.
- Reuse second: skinning logic calls NTMI Core; the add-on does not copy per-character HLSL.
- Cleanliness third: legacy logic is removed instead of kept as compatibility branches.

