# Texture Marking

English | [中文](../texture_marking.md)

Texture marking tells the add-on which dumped PS textures should become Blender materials and which textures should be exported back to the game.

## Candidate Source

After Analyze, the add-on collects texture candidates from visible draws, especially g-buffer-like draws with many render targets.

Each candidate records:

- region;
- draw index;
- PS slot, such as `ps-t2` or `ps-t7`;
- resource hash;
- source dump path;
- PS hash;
- render target count.

If a good g-buffer-like draw exists, the UI selects it by default. If not, the add-on does not fail immediately; the user can still choose a candidate draw manually.

## Semantics

Supported marks:

- `Base Color`: connected to Principled BSDF `Base Color`.
- `Normal`: connected through a Normal Map node to Principled BSDF `Normal`.
- `Material`: saved as a material texture candidate, without channel interpretation yet.
- `Effect`: saved as an effect texture candidate, without channel interpretation yet.

Base Color and Normal are unique per region. A new mark replaces the old one. Material and Effect can have multiple entries.

## Blender Material Nodes

When texture marks are applied, the add-on creates or updates a material on matching imported meshes.

Base Color:

```text
Image Texture -> Principled BSDF Base Color
```

Normal:

```text
Image Texture -> Normal Map -> Principled BSDF Normal
```

The add-on rebuilds a clean Principled material graph when applying marks so old temporary nodes do not block the links.

## DDS Preview

Blender cannot preview every DDS compression format directly.

The add-on looks for `texconv.exe` in this order:

1. `MODIMP_TEXCONV` environment variable.
2. `assets/tools/texconv/texconv.exe`.
3. `texconv.exe` from `PATH`.

DDS files are converted into PNG thumbnails under `.modimp_cache/` for Blender preview. The original DDS remains the export source unless the user replaces it in Blender.

The bundled `texconv.exe` comes from Microsoft DirectXTex. Keep `assets/tools/texconv/LICENSE-DirectXTex.txt` when publishing the add-on.

## Export Rules

On export:

- DDS source images are copied when possible.
- PNG/JPG/TGA and other non-DDS sources are converted to DDS with `texconv.exe`.
- Missing texture sources are reported as warnings.
- If a region has no valid texture mark, the INI omits that PS binding.
- Base Color is usually sRGB.
- Normal, Material, and Effect are usually Non-Color.

## Advice

- Mark textures after Analyze and before Import when possible.
- If the model is already imported, mark textures and click `Apply Texture Marks To Models`.
- If materials do not update, reload the add-on or restart Blender, then apply again.
- If DDS preview fails, check `assets/tools/texconv/texconv.exe` and the source texture path.

