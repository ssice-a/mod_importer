# Yihuan Package Runtime Flow

## WWMI 的 package 思路

WWMI 把运行时拆成两层：

- `Core/WWMI`：统一入口、快捷键、通知、回调、通用 compute shader。
- `Mods/*`：只提供某个 mod 的资源、metadata 和少量 TextureOverride。

核心流程在 `Core/WWMI/WuWa-Model-Importer.ini`：

- `[ShaderRegexEnableTextureOverrides]` 挂到所有 VS，运行 `CommandListFireCallbacks`。
- `CommandListFireCallbacks` 默认 `CheckTextureOverride = vb0`，需要时打开 `ib` 或兼容模式的 `vs-cb*`。
- Mod 通过 TextureOverride 响应这些回调，并把自己的资源传给 Core command/custom shader。
- 骨骼相关 shader 是通用工具：`SkeletonMerger.hlsl` 从原生 skeleton/cb 读取骨骼，写入 merged skeleton；`SkeletonRemapper.hlsl` 再按映射表重排。

它的重点不是把每个角色的 INI 写得很聪明，而是把“触发、检测、骨骼合并、资源传递、调试 UI”集中到 Core，让角色包只提供表和资源。

## 我们要拆的两层

### Core Package

建议命名为 `Core/YHMI` 或 `Core/YihuanMI`。它只放通用运行时：

- 全局开关、调试显示、快捷键框架。
- 通用 VS/CS callback 入口。
- 通用资源保存/恢复 commandlist 模板。
- 通用 BoneStore HLSL：骨骼采集、pose slot 分配、global T0 slot 存储、local palette 构建、custom mesh 蒙皮、pose 发布。
- 通用 draw-stage helper：按当前 `vb0` 特征选择 pose，再发布到当前 part runtime buffer。

Core 不应该硬编码角色 hash、part 名、drawindexed、贴图槽位。

### Character Package

每个角色一个包，例如：

```text
Mods/bohe/
  4c512c5c.ini
  4c512c5c-BoneStore.ini   # 后续可合并进主 INI，避免命名空间冲突
  Buffer/*.buf
  BoneStore/Buffer/*.buf
  Texture/*.dds
  hlsl/*.hlsl              # 若 Core 尚未统一，可先复制
```

角色包提供：

- `CharacterMetaTable.buf`：角色级 runtime 配置、feature 采样范围、collect meta 范围。
- `PaletteTable.buf`：全局骨骼池映射。
- 每个 part 的 `Palette_*.buf`：该 IB 的 local bone index 到 BoneStore global bone index。
- 每个 part 的静态资源：IB、Position、Blend、Normal/Frame、Texcoord、OutlineParam。
- 每个 part 的 runtime buffer 声明：SkinnedPosition、SkinnedNormal、Scratch、PoseSkinned、PrevSkinnedPosition。
- VS/PS/CS hash 到阶段的显式映射：depth、gbuffer、velocity、outline、shadow/skip。
- 每个阶段的显式 draw layout：每个 VS 槽位绑定什么资源、每个 draw 是否受开关控制、哪些 draw 需要 PS 贴图槽。

## 我们的运行时流程

### 1. 启动与回调

Core 注册通用 callback：

- 已知 VS 用 `ShaderOverride + filter_index + checktextureoverride = ib`。
- 可选全局 fallback 用 `ShaderRegex -> CheckTextureOverride = ib`，但生产版建议按 profile 决定是否启用，避免误命中和性能问题。
- 已知 skin CS 用 `ShaderOverride + filter_index + checktextureoverride = cs-cb0`。

角色包声明它需要的 VS/CS hash 和对应 filter index。

### 2. 原生 CS 后收集骨骼/pose

每次原生 skin CS 命中后：

1. 保存原始 CS 绑定。
2. 用当前原生输出 `cs-u1` 或当前 draw `vb0` 做 feature，分配/选择 pose slot。
3. 从原生 `cs-t0` 读取当前 local T0，并按 `CharacterMetaTable` 写进该 pose slot 的 global T0 cache。
4. 对每个需要绘制的 custom part：
   - 绑定该 part 的 per-IB palette。
   - 从 global T0 cache 重建该 part 的 local palette。
   - 绑定该 part 的 static Position/Blend/Frame。
   - 运行 custom skin shader，写入该 part 自己的 Scratch。
   - 将 Scratch 存入该 part 自己的 PoseSkinnedPosition/Normal slot。
5. 恢复原始 CS 绑定。

关键规则：

- 不同 IB/part 不共用 PoseSkinnedPosition/Normal runtime buffer，否则后写入的 part 会覆盖前一个 part。
- `StoreGlobalT0PoseSlot` 应读取当前原生 `cs-t0`，不是读取一个空的全局 BoneStore buffer。
- per-IB palette 必须保留，因为每个 IB 的 local bone index 语义不同。

### 3. Draw 阶段判断 VB 并发布 pose

每个 `TextureOverride_IB_*` 命中后：

1. 保存原始图形/CS/PS 绑定。
2. 按 `vs == filter_index` 进入明确阶段。
3. 先绑定该 part 的静态 IB。
4. 运行 `SelectAndPublishPose_<part>`：
   - 用原生 `vb0` feature 选择 pose slot。
   - 从该 part 的 PoseSkinnedPosition/Normal slot 发布到该 part 的 RuntimeSkinnedPosition/Normal。
   - 设置 `vb0 = RuntimeSkinnedPositionVB`。
5. 再按该 VS 阶段绑定特有槽位：
   - depth compact：`vs-t3 = Texcoord`, `vs-t4 = Position`, `vs-t5 = RuntimeSkinnedNormal`
   - gbuffer main：按 VS layout 绑定 `Texcoord/Position/OutlineParam/RuntimeSkinnedNormal`
   - velocity：`vs-t4 = RuntimePrevSkinnedPosition`
   - outline：绑定 `OutlineParam` 到对应槽位
6. 按 draw 列表执行 `drawindexed`，可选用变量开关包住指定 draw。
7. 分支结束后恢复资源绑定。

### 4. 帧末历史位置

`[Present]`：

1. 对每个 part 执行：
   `RuntimePrevSkinnedPosition = copy RuntimeSkinnedPosition_UAV`
2. 清空本帧 pose slots。

这保证 velocity/TAA 阶段拿到上一帧位置，而不是当前帧位置。

## 导出器应生成的 package 数据

导出插件不要生成“聪明逻辑”，只生成明确表和明确资源：

- `FrameAnalysis/Profile` 生成 stage map：
  - skin CS hash
  - last stable cb0 hash
  - depth/g-buffer/velocity/outline/shadow VS/PS hash
  - 每个 region 的 `first_index/index_count`
  - 每个 draw 的顺序、mesh 名、index range
- `BoneMergeMap` 生成：
  - 原生子部位 local bone range
  - global bone base/count
  - per-IB palette 所需映射
- `CharacterMetaTable.buf` 合并：
  - pose slot/feature 参数
  - collect meta 行
  - part runtime meta 行
- `PaletteTable.buf` 和 per-IB `Palette_*.buf`
- 静态 mesh buf：
  - IB uint16
  - Position float3
  - Blend packed uint32x2
  - Normal/Frame snorm8x4 pair
  - Texcoord half2x4
  - OutlineParam u8x4
- INI：
  - 只显式写阶段和槽位差异
  - 不把静态资源绑定藏进 HLSL
  - 不把不同 part 挤进同一个 runtime buffer

## 插件 UI 流程

建议做成四个按钮阶段：

1. `Analyze FrameAnalysis`
   - 读取 frame dump/log，生成 stage map、BoneMergeMap、候选 draw pass。
2. `Prepare Collection`
   - 建立角色集合和每个 region/part 集合。
   - 外部导入的模型拖进去后，用集合赋予导出语义。
3. `Validate And Split`
   - 检查单 IB 顶点数 <= 65535。
   - 检查单 IB local palette <= 256。
   - 超限则按物体拆子集合。
4. `Export Package`
   - 只导出 buf。
   - 或导出 buf + INI + package runtime。

## 当前要吸取的教训

- VB 判断必须发生在 draw 阶段，用原生 `vb0` feature 选择 pose；不能只靠 CS 阶段最后一次结果。
- 多 part 必须 per-part runtime buffer，不然 pose publish 会互相覆盖。
- 多角色必须 per-package runtime namespace。`PoseState/PoseFeature/PoseGlobalT0/RestoreSlots/CommandList/CustomShader/默认贴图资源` 都要带 source IB hash 后缀；只隔离 BoneStore namespace 不够，否则多个角色同屏时会共用同一份 pose cache，表现为窜骨骼。
- `vs-t4` 在 velocity pass 需要上一帧 position。
- `normal.buf` 与 Blender 面朝向不是同一回事；导出器必须保证 frame normal 的符号和游戏 shader 读取方式一致。
- 贴图槽位不能全局乱绑，只能在对应材质 draw 前绑定，必要时 draw 后恢复。
- BoneStore namespace 最好按 source IB 或 package id 隔离，或者直接并入主 INI，避免多个角色包冲突。
