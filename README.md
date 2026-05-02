# Mod Importer

这是一个按“单集合桥梁”工作的 Blender 插件。当前主目标是异环 Profile，并以当前手写的 `83527398.ini` 与 `83527398-BoneStore.ini` 架构为准。

## 当前流程

1. 在 `FrameAnalysis/Profile` 中分析帧分析目录，生成 stage map、CS collect map、draw pass map 和 BoneMergeMap。
2. 插件只维护一个工作集合：`modimp_collection_name`。导入、外部模型分配、拆分和导出都围绕这一个集合进行。
3. 把模型放进集合树：`source_ib_hash / region_hash-index_count-first_index / partXX / optional partXX_ibYY / mesh objects`。
4. 对外部插件导入的模型，先执行 `Apply BoneMergeMap To Groups`，把局部顶点组编号转换成 BoneStore 全局骨骼编号。
5. 导出时每个 IB 子集合独立生成 IB、position、blend、normal/frame、texcoord 和 per-IB palette。
6. 默认可以只导出 buf；可选生成与当前手写 INI 对齐的 INI/BoneStore 资源。

## 重要约定

- 导入和导出不耦合：导入只把可读 mesh 带进 Blender；导出只读取集合树与集合属性。
- 不再支持旧的导入集合/导出集合双轨，也不再支持导入已导出的旧包。
- 顶点组工具只读当前集合绑定的 BoneMergeMap，不再从外部 palette 文件推断。
- 缺少 stage map、BoneMergeMap 或关键集合属性时直接报错，不猜测。
- PS 材质槽位不由插件强行管理；用户可以基于生成模板或手写 INI 继续配置。

## 导出限制

- 单个导出 IB 使用 `R16_UINT`，顶点数不能超过 65535。
- 单个导出 IB 的 local palette 不能超过 256 根骨骼。
- 单个物体自身超过 65535 顶点或 256 根骨骼时，第一版只报错，不自动按三角面切碎。
- 法线、tangent、UV 和权重会从导出临时 mesh 重新打包，不会破坏用户真实网格数据。
