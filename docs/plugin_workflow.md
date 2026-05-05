# 插件流程

本文按用户实际操作顺序说明插件用法。

## 1. Analyze

选择 FrameAnalysis 文件夹，输入目标 IB Hash，然后点击 `Analyze`。

Analyze 会扫描并缓存：

- 目标 IB 的 draw regions；
- 用于贴图候选的可见 g-buffer-like draw；
- 目标 `vb0` 对应的原游戏 CS producer 信息；
- Collector 配置；
- BoneMergeMap；
- draw pass 与贴图候选报告。

Analyze 与 Import 分开是有意设计。这样可以先缓存大目录分析结果，也可以先做贴图标记，而不用反复扫描 FrameAnalysis。

## 2. 标记贴图

Analyze 后打开 `贴图标记` 面板。

选择子部件和 draw。插件会尽量默认选择最像 g-buffer 的 draw。你可以把候选贴图标记为：

- `基础色`
- `法线`
- `材质`
- `特效`

基础色和法线每个子部件只保留一个。材质和特效允许多个。

如果模型已经导入，可以点击 `Apply Texture Marks To Models`，把当前贴图标记应用到现有模型材质上。

## 3. Import

点击 `Import`。

Import 会创建或更新工作集合，并导入目标 IB 的所有检测到的 draw regions。导入模型包含：

- position；
- normal/frame；
- UV；
- outline 或 vertex color 数据；
- vertex groups；
- region metadata；
- draw metadata；
- 已标记的 texture slot metadata。

如果导入前已经标记了基础色和法线，插件会自动创建 Blender 材质并连接贴图。

## 4. 编辑模型

在 Blender 中编辑导入模型。

注意事项：

- 模型应放在正确的 region 集合下。
- 顶点组在导出前应当表示全局骨骼编号。
- 外部插件导入的模型，需要先放进目标 region 集合，再按需要执行 `Apply BoneMergeMap To Groups`。
- 导出前会清理空 numeric 顶点组。
- 导出前会按顶点组名称排序，避免 blend 打包顺序不稳定。
- UV0 使用 mesh 上 active render 的 UV 层；如果没有 active render UV，则退回 `UV0` 或当前 active UV。
- 如果没有 `UV1` 层，导出器会把 UV0 复制为 UV1。
- 导出使用临时 mesh 评估对象变换、修改器、当前 shapekey 值和三角化，不会破坏原始 Blender mesh。

## 5. Export

选择导出目录和导出模式：

- `Buffers Only`：只写游戏 Buffer 和贴图。
- `Buffers + INI`：写 Buffer、贴图和 NTMI fast-path INI。

导出内容包括：

- IB；
- position；
- blend；
- normal/frame；
- texcoord；
- outline 参数；
- per-part palette；
- 可选 shapekey buffer；
- 贴图；
- 可选 INI。

## 集合结构

插件只使用一个工作集合。

第一层子集合表示导出 region，常见命名：

```text
0456d530-105840-70866
```

含义：

- source IB hash：`0456d530`
- index count：`105840`
- first index：`70866`

region 集合下可以直接放 mesh。只有当同一 region 内 local bone 数超过 256，需要拆成多个 palette 时，才需要额外子集合。

## BoneMergeMap

BoneMergeMap 记录原游戏 local bone 到 Collector 全局骨骼池的映射。

需要使用它的情况：

- 导入模型仍然是 local bone 顶点组；
- 外部插件导入的模型顶点组从 0 开始；
- 需要把选中对象批量转换为导出器需要的全局骨骼编号。

BoneMergeMap 转换应当在正确的 region 集合中执行。region 身份决定使用哪一份映射表。

## Shapekey 导出

Shapekey 导出是可选的。

启用后，选中的 shapekey 会导出为运行时可调数据。静态网格仍然保持 Blender 当前可见结果。Runtime shapekey 的初始权重等于 Blender 当前值，所以游戏初始结果不会重复应用同一组 shapekey。
