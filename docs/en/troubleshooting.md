# Troubleshooting

English | [中文](../troubleshooting.md)

## Analyze Cannot Find Draw Slices

Check:

- the FrameAnalysis folder comes from the same scene and character;
- the entered hash is the original game IB hash, not the exported replacement IB;
- the target draw was actually dumped;
- the target model may be an LOD or transparent companion part with another IB hash.

If a transparent IB has the same vertex count as a main region, it may reuse the main region dynamic skin output instead of having an independent Collector.

## Imported Model Looks Dark

Common causes:

- Base Color was not marked or applied.
- Blender material preview is not using the intended material.
- Face orientation and normal direction disagree after game-to-Blender transform.
- Custom normals were lost or recalculated incorrectly.

Intended rules:

- preserve the original game normal direction;
- fix Blender face orientation when needed for editing;
- reverse the transform on export so the game receives the expected format.

## Texture Marks Do Not Apply

Expected logs include:

```text
[ModImporter][Texture] ... exact region ... slots=[...]
[ModImporter][Texture] ... applying slots=...
[ModImporter][Texture] loaded image node ...
[ModImporter][Texture] linked base color ...
```

If logs stop at `has_base=True` or `has_normal=True`, Blender is likely still using a cached old Python module. Reload the add-on or restart Blender, then apply again.

If logs show `failed to load image`, check the source texture path and `texconv.exe`.

## DDS Thumbnail Is Missing

Check:

- `assets/tools/texconv/texconv.exe` exists;
- the dumped DDS source still exists;
- `.modimp_cache/` is writable;
- the DDS format is supported by DirectXTex.

## Bones Are Twisted

Common causes:

- the wrong CS producer chain was selected;
- the Collector group does not match the actual `vb0` used by the target g-buffer draw;
- BoneMergeMap was applied on the wrong region;
- vertex groups were not global bone ids before export;
- empty or duplicate numeric vertex groups changed blend packing;
- constraints or modifiers changed the evaluated export mesh unexpectedly.

Recommended checks:

- confirm the target g-buffer draw `vb0` traces back to the same Collector output pool;
- confirm each region has its own palette;
- confirm blend indices are local to the current palette;
- confirm the palette maps to the expected global bone ids.

## Vertex Groups Get `.001`

Blender adds suffixes when names collide. This can happen after repeated imports or when old objects remain in the same scene.

Recommended fixes:

- restart Blender for a clean import test;
- delete old duplicate objects and collections;
- run BoneMergeMap conversion only on objects under the correct region collection;
- sort and clean vertex groups before export.

## Hotkeys Affect Another Mod

3DMigoto INI variables and key sections are global. Use unique prefixes that include the IB hash or mod id.

Recommended:

```ini
global persist $swapkey_0456d530_up = 0
```

Avoid generic names such as `$swapkey_up`.

## Model Disappears In Game

Check:

- the INI has no parse errors;
- original IB hash, first index, and index count match the dump;
- runtime buffer `array` values are valid;
- `dynamic_slots` is large enough;
- the Collector triggers before replacement draw;
- `drawindexed` ranges are inside the exported IB.

## Export Is Slow

Export evaluates Blender mesh data, modifiers, shapekeys, normals, UVs, textures, and palette data.

Suggestions:

- remove unused objects from the export collection;
- avoid heavy live modifiers during repeated export tests;
- use `Buffers Only` while iterating;
- export INI only after buffers are stable.

