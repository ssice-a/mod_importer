# 旧运行流文档已废弃

这份文档原本记录的是旧 Yihuan BoneStore / PoseSlot / ShaderOverride 回调方案。当前项目已经切到改版 3DMigoto 的 NTMI fast path，因此旧内容不再作为实现依据。

请改读：

- `docs/ntmi_export_runtime_spec.md`：当前运行时、INI、Buffer、Collector、动态资源规范。
- `docs/ntmi_plugin_refactor_plan.md`：插件源码清理与新导出器重构计划。

当前原则：

- 不再生成 `ShaderOverride + checktextureoverride = ib`。
- 不再生成 `BoneStore` 旧 INI、`CharacterMetaTable`、`PaletteTable`、pose slot HLSL。
- 不再把 65535 顶点作为必须拆分的限制；IB 可按最大索引选择 `R16_UINT` 或 `R32_UINT`。
- 仍保留每个导出 part 的 local palette 不能超过 256 的限制。
