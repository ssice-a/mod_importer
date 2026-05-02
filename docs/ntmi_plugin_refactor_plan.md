# NTMI 插件重构计划

本文档是下一轮插件重构的执行蓝图。基准只认两件事：改版 3DMigoto fast path 与当前 `E:\yh\Mods\bohe` 示例包；旧 `BoneStore / PoseSlot / ShaderOverride + checktextureoverride` 链路全部视为历史包袱，不再兼容。

## 目标

- 性能第一：导出 INI 必须使用 DrawIndexed fast TextureOverride、精确 `hash + match_first_index + match_index_count`，不生成全局 ShaderRegex、宽松 fallback 或每帧大范围 check。
- 可复用第二：蒙皮只调用 `E:\yh\Core\NTMI` 的公共 ABI，插件不再复制或维护角色专属 HLSL。
- 整洁第三：删除旧生成器、旧 HLSL、旧 BoneStore 表、旧 manifest/capture fallback。不能为了旧包多保留一条分支。

## Canonical Runtime

- 主规范：`docs/ntmi_export_runtime_spec.md`。
- 示例包：`E:\yh\Mods\bohe\4c512c5c.ini`。
- Core：`E:\yh\Core\NTMI`，调用 `CommandList\NTMIv1\SkinFromBoundSlots`。
- 主 INI 负责资源、Collector、skin CommandList、fast TextureOverride。
- palette 资源直接写进主 INI。
- 不再生成 `*-BoneStore.ini`、`CharacterMetaTable.buf`、`PaletteTable.buf`、LocalT0 中间资源或 pose slot HLSL。

## 已清理的示例包

- `E:\yh\Mods\bohe\4c512c5c.ini` 已移除无用 `analyse_options`、旧 `CB0` 资源段和未引用贴图资源段。
- 已删除 `E:\yh\Mods\bohe` 下未被当前 INI 引用的旧文件：旧备份、`CharacterMetaTable.buf`、旧 `*-cb0.buf`、未引用贴图。
- 当前 `bohe` 包应保持最小结构：主 INI、`Buffer/`、`Texture/`。

## 源码必须删除的旧逻辑

- 删除 `assets/hlsl/*.hlsl` 中旧的 Yihuan pose-slot、local T0、scratch skin、global T0 HLSL。
- 删除或改空 `core/hlsl_assets.py`；导出器不再向 mod 包复制 HLSL。
- 删除 `core/exporter.py` 中所有旧资源生成：`ResourceYihuanRuntime*`、`PoseState`、`PoseFeature`、`PoseGlobalT0`、`PoseSelectedLocalT0`、`RestoreResourceSlots`、`ShaderOverride_Yihuan*`、`checktextureoverride`。
- 删除旧 BoneStore 写出：`_write_yihuan_bonestore_tables`、`_write_yihuan_bonestore_ini`、`CharacterMetaTable.buf`、`PaletteTable.buf`、`BoneStore/Buffer`。
- 删除 manifest/capture fallback 与 roundtrip 旧包导入路径；缺少新集合属性或 FrameAnalysis 映射时直接报错。
- 删除旧 `R16_UINT` 顶点数硬限制；保留 palette 256 骨骼限制。

## 新导出器结构

- `FrameAnalysis/Profile` 产出运行配置：
  - `source_ib_hash`
  - 每个 region 的 `match_first_index / match_index_count`
  - 原 draw 列表与 mesh 名称
  - `CollectorSkinPart` 配置：`group`、`match_cs_t0_hash`、`match_cs_u0_hash`、`match_cs_u1_hash`、`collect` key、结束条件
  - VS/PS 静态资源 hash 到语义的映射：texcoord、position、outline、材质贴图
  - 贴图槽位映射：默认从高 RT 数量的可见 g-buffer draw 中提取 `ps-t5 / ps-t7 / ps-t8 / ps-t18`
- `Exporter` 产出数据：
  - per-part static buffers：IB、position、blend、normal/frame、texcoord、outline
  - per-part dynamic Resource 段：position、positionVB、normal、prev position
  - per-part palette：local bone index -> global bone index
  - Texture 目录下的材质贴图副本与主 INI 贴图 Resource 段
  - 主 INI
- `INI generator` 只生成新格式：
  - `[ResourcePart_<...>]`
  - `[CollectorSkinPart_<ib_hash>]`
  - `[CommandList_SkinParts_<ib_hash>]`
  - `[TextureOverride_IB_<ib_hash>_<index_count>_<first_index>]`
  - `match = vb, dynamic`
  - `match = vs, dynamic_prev`
  - `match = vs/ps, <resource_hash>, Resource...`

## 必须记录的数据模型

导出器不能只看 Blender mesh。当前 INI 依赖 FrameAnalysis、profile 和导出 mesh 共同生成，至少要保存下面五张表。

### CollectorConfig

从原生 skin CS 链分析得到，用于决定何时收集骨骼、何时 build 全局骨骼池。

- `source_ib_hash`：角色/包主 IB hash，例如 `4c512c5c`。
- `group_slot`：当前实例分组槽位，例如 `cs-u1`。
- `match_cs_t0_hash`：确认骨骼矩阵输入资源。
- `match_cs_u0_hash`：确认原生 normal/frame 输出资源。
- `match_cs_u1_hash`：确认原生 position 输出资源。
- `collect_source`：当前为 `cs-t0`。
- `collect_key`：当前为 `cs-cb0[1]`。
- `finish_condition`：最后一次原生蒙皮 dispatch 的 cb0 条件，例如 `cs-cb0[1] == 12675 && cs-cb0[2] == 14431`。
- `core_global_t0_resource`：Core 提供的全局骨骼池引用。容量与 group 隔离由 Core/Collector 负责，角色 INI 不再声明或计算全局池容量。

`finish_condition` 必须从 FrameAnalysis 里找到最后一次目标蒙皮 dispatch 后自动生成。它决定什么时候写：

```ini
post collect = build, <core_global_t0_resource>
post run = CommandList_SkinParts_<source_ib_hash>
```

CollectorConfig 不能靠 shader hash 猜。分析器应从目标可见 g-buffer draw 的 `vb0` 出发，按实际 resource 追溯 producer。通常只需要向上一层追到写出该 `vb0` 的原生 skin CS，因为游戏可能对同一模型执行多次蒙皮，但不同输出 resource 的内容并不等价。

### BoneMergeMap

用于把 Blender 顶点组和每个 part 的 local palette 映射到运行时全局骨骼池。

- `collect_key`：对应原生段的 `cs-cb0[1]`。
- `cs_t0_hash` 或 dump/resource 标识。
- `bone_count`：通常等于该段 `cs-t0` 矩阵行数 / 3。
- `global_bone_base`：按 Collector build 顺序累加得到。
- `local_bone_index`。
- `global_bone_index = global_bone_base + local_bone_index`。
- `display_name`：用于 UI、顶点组批量改名和报告。

全局骨骼矩阵的起点不来自 draw 的 `first_index`，而来自 CS 收集链的段顺序与骨骼数量累加。

### PartExportMap

每个导出 part 都必须有完整导出元数据。

- `part_token`：例如 `4c512c5c_62346_52407_part00`。
- `native_ib_hash`。
- `match_first_index`。
- `match_index_count`。
- `vertex_count`：生成 `$\NTMIv1\vertex_count = ...`，决定 NTMI Core dispatch 线程数。
- `position_float_count = vertex_count * 3`。
- `normal_row_count = vertex_count * 2`。
- `ib_format`：按最大 index 自动选择 `DXGI_FORMAT_R16_UINT` 或 `DXGI_FORMAT_R32_UINT`。
- `buffer_files`：IB、position、blend、normal/frame、texcoord、outline、palette。
- `local_palette_count`：必须小于等于 256。
- `dynamic_slots`：默认 profile 值，例如 16，可由项目设置覆盖。

`vertex_count` 必须由导出的 mesh 顶点数自动生成，不能用 index count 代替。

### DrawPassMap

每个 fast TextureOverride region 都要由 FrameAnalysis 和导出 part 共同生成。

- `hash`：当前绑定原生 IB resource hash。
- `match_first_index`。
- `match_index_count`。
- `replacement_ib_resource`。
- `drawindexed` 列表：每条包含 `index_count`、`start_index`、`base_vertex`、mesh/display name、可选 draw 开关。
- VS match 列表：
  - `vb dynamic` -> `RuntimeSkinnedPositionVB`
  - `vs dynamic_prev` -> `RuntimePrevSkinnedPosition`
  - texcoord resource hash -> `Texcoord`
  - position resource hash -> `Position`
  - `vs dynamic` -> `RuntimeSkinnedNormal`
  - outline/vertex color resource hash -> `OutlineParam`
- 是否需要显式 PS 贴图绑定，以及绑定发生在哪一条 draw 前。

所有静态 VS/PS resource hash 都必须来自 FrameAnalysis 当前 pass 的原始槽位资源，不应手写。

DrawPassMap 的 producer 关系同样以实际 resource 为准：

- 先确定目标 g-buffer/depth/velocity/outline draw 的原生 `vb0`。
- 用 `vb0` resource 反推它来自哪一次原生 skin CS 输出。
- 再从该 CS 链获取 CollectorConfig、finish condition 与 BoneMergeMap。
- 不靠 VS/PS shader hash 推断骨骼链，只把 shader/hash 用作阶段和槽位布局的辅助信息。

### TextureMap

贴图由 FrameAnalysis 的目标可见 g-buffer draw 推断，并在导入 Blender 时生成材质。

- `draw_id`：用于定位候选贴图来源，优先选择目标 region 中 RT 输出数量最多的可见 g-buffer draw。
- `ps_hash`。
- 每个槽位的 `slot`、`resource_hash`、dump 路径、语义。
- 当前异环 profile 默认语义：
  - `ps-t5`：normal。
  - `ps-t7`：base_color。
  - `ps-t8`：material_0。
  - `ps-t18`：material_1。
- 导出文件名：如 `Texture/NM.dds`、`Texture/Body.dds`、`Texture/t8.dds`、`Texture/t18.dds`。
- Blender 材质节点连接：base color 接 Principled BSDF，normal 经 Normal Map 节点接入，材质贴图保留 Non-Color 节点与属性。

## 贴图与材质方案

贴图链路也走 profile，不走手工补丁。当前异环 profile 暂定规则：

- 从 FrameAnalysis 中找到目标 region 的可见 g-buffer draw 候选。
- 优先选择输出 RT 数量最多、且同时绑定目标 body IB/range 的 draw；这是最可能携带完整材质 PS 槽位的一次 draw。
- 默认槽位语义：
  - `ps-t5`：法线贴图，导入 Blender 时按 Non-Color，接 `Normal Map` 节点，再接 Principled BSDF 的 `Normal`。
  - `ps-t7`：基础色贴图，导入 Blender 时按 sRGB，接 Principled BSDF 的 `Base Color`。
  - `ps-t8`：材质贴图，按 Non-Color 保存到材质属性与节点树，第一版不强行猜通道语义。
  - `ps-t18`：材质贴图，按 Non-Color 保存到材质属性与节点树，第一版不强行猜通道语义。
- FrameAnalysis 解析器记录每个槽位的 resource hash、dump 文件路径、原始槽位号、候选 draw id、PS hash。
- 导入时自动在 Blender 创建材质球：
  - 材质名使用 region/mesh/display name。
  - 自动创建 Image Texture 节点。
  - 基础色和法线按上面规则连接到 Principled BSDF。
  - `ps-t8 / ps-t18` 先保留为未连接或接入自定义 frame，方便用户后续确认通道。
- 导出时自动复制或写出贴图到 `Texture/`：
  - 默认命名为 `Texture/<semantic>.dds`，如 `Body.dds`、`NM.dds`、`t8.dds`、`t18.dds`。
  - 如果用户在材质面板改了贴图，则以 Blender 材质当前引用为准。
  - 主 INI 生成 `[ResourceT5/T7/T8/T18]` 或 profile 配置的等价资源名。
- PS 槽位生成策略：
  - 能确认当前 draw 必须显式覆盖材质时，生成 `ps-tN = Resource...`。
  - 能稳定用 fast path hash 匹配时，可以生成 `match = ps, <hash>, Resource...`。
  - 不知道语义时只记录候选，不强行接线或覆盖，避免贴图污染其他 draw。

这部分遵守性能第一：贴图识别发生在导入/分析阶段；运行时只使用已经确认的槽位绑定或 fast match，不增加额外 check。

## IB 与顶点限制

- 导出器根据最大索引选择 IB 格式：
  - `max_index <= 65535`：写 `DXGI_FORMAT_R16_UINT` 与 uint16 buf。
  - `max_index > 65535`：写 `DXGI_FORMAT_R32_UINT` 与 uint32 buf。
- 不再因为一个 part 顶点数超过 65535 自动拆分。
- local bone index 仍是 8-bit；单个导出 part 的 local palette 超过 256 时必须拆分或报错。
- 自动拆分只为骨骼数服务，不为 R16 顶点窗口服务。

## 全局骨骼索引

- FrameAnalysis 按原生 CS dispatch 顺序构建全局骨骼池索引。
- Blender 顶点组在导出前应表示全局骨骼编号，或通过 BoneMergeMap 工具批量转换。
- 每个 part 导出自己的 local palette；blend 中写 local index，palette 中写 global index。
- 这与当前 BoneMergeMap 思路不冲突，只是 runtime 不再需要 `CharacterMetaTable` 去二次解释。

## 集合与导入导出规则

- 插件只保留一个工作集合，不再区分 import/export collection。
- 导入模型可以直接是已转换顶点组的模型；额外保留一个按钮按 BoneMergeMap 批量转换顶点组。
- 集合只作为导出语义桥梁：region/part/mesh 归属、材质策略、outline 默认值、profile transform。
- 工作集合第一层子集合表示一个导出 region/TextureOverride/IB。它决定下面物体使用哪个原生 IB range 渲染、使用哪套 draw pass 和默认 palette 规则。
- 第一层子集合下可以直接放 mesh，也可以再放子集合。额外子集合只表示该 region 内因 local bone 数超过 256 而拆出的独立 palette/export part，不改变第一层 region 的 TextureOverride 语义。
- 通常不需要额外子集合；只有同一 region 内物体合计 local palette 超过 256 时，导出器才按物体边界创建子集合并分别生成 palette。
- 无论是否存在额外子集合，第一层 region 子集合都必须可以独立导出，并生成对应 TextureOverride。
- 导出时使用临时 mesh 应用变换、形态键当前值、修改器和三角化；不真实破坏用户模型。
- 法线/frame 按游戏 shader 需要的双 row 格式重新打包；要保留 Blender 自定义法线的结果，但坐标/符号必须经过 profile transform 校正。

## 性能规则

- 禁止生成全局 `ShaderRegex` 或“所有 VS 都 check”的逻辑。
- 禁止生成旧 `ShaderOverride + checktextureoverride = ib`。
- TextureOverride 必须用精确 draw region 命中。
- `match_*_slots` 的范围来自全局 fast path 配置，插件不扩大扫描范围。
- Collector 只在匹配到目标 CS 资源 hash 时收集，不按 shader hash 广撒网。
- 同一个角色只生成一个扁平 `CommandList_SkinParts_<ib_hash>`，按 part 顺序调用 Core；不要为每个 part 套多层 wrapper。

## 实施顺序

1. 冻结旧导出器入口：标记当前 `core/exporter.py` 的旧 Yihuan INI 生成器为待删除，不再继续修补旧格式。
2. 抽出新 INI emitter：用 `E:\yh\Mods\bohe\4c512c5c.ini` 作为模板生成单主 INI。
3. 替换 IB 写出：加入 R16/R32 自动选择，删除 65535 顶点数硬错误。
4. 替换 palette 写出：只写 per-part palette，不写 `CharacterMetaTable` / `PaletteTable`。
5. 删除旧 HLSL asset pipeline：不复制 `assets/hlsl`，不生成 `BoneStore/hlsl`。
6. 清理 UI 文案和 README：改成 NTMI fast path、单集合、buf-only/INI、BoneMergeMap 转换。
7. 删除旧文档或改成历史说明；`docs/ntmi_export_runtime_spec.md` 和本文档作为唯一有效规范。

## Shapekey 导出补充

- 命名统一使用 `shapekey`，不再混用 morph / shape key / blendshape。
- shapekey 运行顺序固定为：`Basis 静态网格 -> ApplyShapekeyPreSkin -> NTMI SkinFromBoundSlots -> Draw`。
- 导出 buf 只分两类逻辑数据区：
  - `ShapekeyStatic`：每个 part 的全部 shapekey 静态数据，包括 header、delta position、delta normal/tangent/frame 所需数据。
  - `ShapekeyRuntime`：每个 part 的运行时 shapekey 权重 buffer，可由按键、动画或额外 HLSL 改写。
- `ShapekeyRuntime_UAV` 和 `ShapekeyRuntime` 只允许作为同一个动态数据区的写视图/读视图，不允许再生成 default weights、per-key weight 或临时 morph buffer。
- 导出必须保持 Blender 所见即所得：如果导出前 `Key_A.value = 0.7`，导出后的 `ShapekeyRuntime` 初始权重也必须是 `0.7`。
- shapekey delta 必须按最终导出顶点重编号顺序写出，不能直接使用 Blender 原始顶点 index。
- 如果 region 因 local palette 超过 256 被拆成多个 part，每个 part 单独生成自己的 `ShapekeyStatic` / `ShapekeyRuntime`，不能共享原始大模型 shapekey 索引。
- shapekey 必须同时影响 position 和 frame。只改 position 会破坏法线、轮廓线、边缘光和 g-buffer 稳定性。
- 导出器只保留一套统一管线：同一套 part 划分、顶点重编号、profile 坐标/法线转换和游戏 frame 打包逻辑，同时服务静态 buffer 与 shapekey buffer。

## 验证

- `rg "CharacterMetaTable|PoseState|PoseFeature|PoseGlobalT0|ShaderOverride|checktextureoverride|BoneStore|local_t0|65535" core assets README.md docs` 不应在有效逻辑中命中。
- 导出 `bohe` 后不生成 `hlsl/`、`BoneStore/`、`*-BoneStore.ini`、`CharacterMetaTable.buf`、`PaletteTable.buf`。
- 大顶点 part 能自动写 `DXGI_FORMAT_R32_UINT`，游戏中正确绘制。
- local palette 超过 256 时仍明确报错或按物体拆分。
- 游戏中验证：Collector 命中、动态骨骼不串角色、材质槽位正确、法线方向正确、TAA previous position 正确、draw 开关覆盖所有阶段。
