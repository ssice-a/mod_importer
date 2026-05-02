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
- `Exporter` 产出数据：
  - per-part static buffers：IB、position、blend、normal/frame、texcoord、outline
  - per-part dynamic Resource 段：position、positionVB、normal、prev position
  - per-part palette：local bone index -> global bone index
  - 主 INI
- `INI generator` 只生成新格式：
  - `[ResourcePart_<...>]`
  - `[CollectorSkinPart_<ib_hash>]`
  - `[CommandList_SkinParts_<ib_hash>]`
  - `[TextureOverride_IB_<ib_hash>_<index_count>_<first_index>]`
  - `match = vb, dynamic`
  - `match = vs, dynamic_prev`
  - `match = vs/ps, <resource_hash>, Resource...`

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

## 验证

- `rg "CharacterMetaTable|PoseState|PoseFeature|PoseGlobalT0|ShaderOverride|checktextureoverride|BoneStore|local_t0|65535" core assets README.md docs` 不应在有效逻辑中命中。
- 导出 `bohe` 后不生成 `hlsl/`、`BoneStore/`、`*-BoneStore.ini`、`CharacterMetaTable.buf`、`PaletteTable.buf`。
- 大顶点 part 能自动写 `DXGI_FORMAT_R32_UINT`，游戏中正确绘制。
- local palette 超过 256 时仍明确报错或按物体拆分。
- 游戏中验证：Collector 命中、动态骨骼不串角色、材质槽位正确、法线方向正确、TAA previous position 正确、draw 开关覆盖所有阶段。
