# NTMI Mod Importer

[English](README.en.md) | 中文

NTMI Mod Importer 是一个 Blender 插件，用于配合修改版 3DMigoto / NTMI fast path 工作流，从 FrameAnalysis 导入角色模型，在 Blender 中编辑后导出游戏可用的 Buffer 与可选 INI。

当前插件只面向新的 NTMI 运行链路，不再生成旧的 BoneStore、PoseSlot、ShaderRegex、`ShaderOverride + checktextureoverride = ib` 方案。

## 环境要求

- Windows。
- Blender 4.0 或更高版本。当前开发环境使用 Blender 5.0。
- 支持 NTMI fast TextureOverride 与 Collector 语法的修改版 3DMigoto。
- 游戏环境中已安装 NTMI Core，例如 `Core/NTMI`。
- 目标角色与目标 IB 的 FrameAnalysis dump。
- `texconv.exe` 用于 DDS 预览和转换。插件默认会查找 `assets/tools/texconv/texconv.exe`。

## 快速开始

1. 在 Blender 中启用插件。
2. 打开 `View3D > Sidebar > Mod Importer`。
3. 选择 FrameAnalysis 文件夹。
4. 输入目标 IB Hash。
5. 点击 `Analyze`。
6. 如有需要，在贴图标记面板中标记基础色、法线、材质或特效贴图。
7. 点击 `Import` 导入模型。
8. 在 Blender 中编辑模型。
9. 选择导出目录。
10. 点击 `Export`。

导出模式：

- `Buffers Only`：只导出游戏 Buffer 与贴图。
- `Buffers + INI`：导出 Buffer、贴图和 NTMI fast-path INI。

## 文档

- [INI 语法](docs/ini_syntax.md)：Collector、dynamic resource、match、draw 与 palette 规则。
- [插件流程](docs/plugin_workflow.md)：Analyze、Import、编辑、导出流程。
- [贴图标记](docs/texture_marking.md)：贴图候选、DDS 预览、材质节点与导出规则。
- [常见问题](docs/troubleshooting.md)：导入、贴图、骨骼、INI 与游戏内问题排查。

## 设计原则

- 性能第一：不生成大范围 VS check，不生成旧 ShaderRegex fallback，不把可离线分析的事情放到运行时。
- 复用第二：蒙皮逻辑调用 NTMI Core，插件不再为每个角色复制专属 HLSL。
- 整洁第三：旧逻辑直接删除，不为兼容旧包保留独立路径。
