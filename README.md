# Mod Importer

这是一个面向改版 3DMigoto NTMI fast path 的 Blender 导入导出插件。当前实现目标是生成与 `E:\yh\Mods\bohe` 示例包一致的资源结构，而不是兼容旧 `BoneStore / PoseSlot / ShaderOverride` 架构。

## 当前基准

- 运行时规范：`docs/ntmi_export_runtime_spec.md`
- 插件重构计划：`docs/ntmi_plugin_refactor_plan.md`
- 示例包：`E:\yh\Mods\bohe`
- 公共 Core：`E:\yh\Core\NTMI`

## 目标流程

1. 用 FrameAnalysis/Profile 分析目标角色，生成 draw region、Collector、VS/PS 资源槽位、BoneMergeMap。
2. 在 Blender 中使用单一工作集合承载导出语义；外部插件导入的模型也先放进这个集合。
3. 顶点组可直接使用全局骨骼编号；需要时用 BoneMergeMap 工具批量转换。
4. 导出每个 part 的 IB、position、blend、normal/frame、texcoord、outline、palette。
5. 可选生成 NTMI fast path INI：Collector + `CommandList_SkinParts_*` + DrawIndexed fast TextureOverride。

## 关键约束

- 性能第一：不生成全局 ShaderRegex，不生成 `ShaderOverride + checktextureoverride = ib`。
- 可复用第二：蒙皮只调用 `E:\yh\Core\NTMI`，插件不再复制角色专属 HLSL。
- 整洁第三：不保留旧包兼容路径；旧逻辑该删就删。
- IB 根据最大索引自动选择 `R16_UINT` 或 `R32_UINT`。
- 单个导出 part 的 local palette 仍不能超过 256。
