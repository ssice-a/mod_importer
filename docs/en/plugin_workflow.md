# Plugin Workflow

English | [中文](../plugin_workflow.md)

This page follows the normal user workflow.

## 1. Analyze

Choose the FrameAnalysis folder, enter the target IB hash, then click `Analyze`.

Analyze scans and caches:

- draw regions for the target IB;
- the visible g-buffer-like draw used for texture candidates;
- original CS producer information for the target `vb0`;
- Collector configuration;
- BoneMergeMap;
- draw pass and texture candidate reports.

Analyze is separate from Import so large FrameAnalysis folders can be cached and texture marks can be prepared before importing.

## 2. Mark Textures

Open `Texture Marking` after Analyze.

Choose a region and draw. The add-on tries to select the most g-buffer-like draw by default. Mark candidates as:

- `Base Color`;
- `Normal`;
- `Material`;
- `Effect`.

Base Color and Normal are unique per region. Material and Effect can have multiple entries.

If the model is already imported, click `Apply Texture Marks To Models` to update existing Blender materials.

## 3. Import

Click `Import`.

Import creates or updates the working collection and imports all detected draw regions for the target IB. Imported meshes include:

- position;
- normal/frame;
- UV;
- outline or vertex color data;
- vertex groups;
- region metadata;
- draw metadata;
- marked texture slot metadata.

If Base Color and Normal were marked before import, the add-on creates Blender materials and links those textures automatically.

## 4. Edit

Edit the model in Blender.

Notes:

- Meshes should stay under the correct region collection.
- Vertex groups should represent global bone ids before export.
- External meshes should be placed in the target region collection before running `Apply BoneMergeMap To Groups`.
- Empty numeric vertex groups are removed during export.
- Vertex groups are sorted by name before export to keep blend packing stable.
- UV0 uses the mesh active render UV layer. If no active render UV exists, export falls back to `UV0` or the current active UV.
- If there is no `UV1` layer, UV0 is exported as UV1 as well.
- Export evaluates transforms, modifiers, current shapekey values, and triangulation on a temporary mesh. The original Blender mesh is not destructively modified.

## 5. Export

Choose an export directory and mode:

- `Buffers Only`: write game buffers and textures only.
- `Buffers + INI`: write buffers, textures, and the NTMI fast-path INI.

Export writes:

- IB;
- position;
- blend;
- normal/frame;
- texcoord;
- outline parameters;
- per-part palette;
- optional shapekey buffers;
- textures;
- optional INI.

## Collection Structure

The add-on uses one working collection.

First-level child collections represent export regions, commonly named like:

```text
0456d530-105840-70866
```

Meaning:

- source IB hash: `0456d530`;
- index count: `105840`;
- first index: `70866`.

Meshes can be placed directly under a region collection. Extra child collections are only needed when one region must be split into multiple palettes because the local bone count exceeds 256.

## BoneMergeMap

BoneMergeMap records how original local bones map into the Collector-built global bone pool.

Use it when:

- imported meshes still use local bone groups;
- an external importer created vertex groups starting from 0;
- selected objects need conversion to global bone ids.

Run BoneMergeMap conversion only on objects inside the correct region collection. The region identity decides which mapping is used.

## Shapekey Export

Shapekey export is optional.

When enabled, selected shapekeys become runtime-adjustable data. The static mesh still matches the current Blender view. Runtime shapekey initial weights equal the Blender values, so the initial game result does not apply the same shapekey twice.

