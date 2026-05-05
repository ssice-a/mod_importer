# NTMI Texture Pipeline Plan

本文档记录当前贴图导入、标记、预览、导出和 INI 绑定规则。

## 当前规则

- FrameAnalysis 从可见 g-buffer draw 中收集 `ps-t*` 贴图候选，用户可在贴图标记面板中手动标记语义。
- 贴图语义目前分为：基础色、法线、材质、特效。
- 基础色和法线每个子部件只保留一个；材质和特效允许多个。
- 导入或应用贴图时：
  - 基础色连接到 Principled BSDF `Base Color`，颜色空间使用 sRGB。
  - 法线贴图使用 Non-Color，经 Normal Map 节点连接到 Principled BSDF `Normal`。
  - Blender 不支持的 DDS 会通过内置 `texconv.exe` 转为缓存 PNG 后再加载。
- 导出 `Buffers + INI` 时：
  - DDS 源图直接复制到 `Texture/`。
  - PNG/JPG/TGA 等非 DDS 源图通过内置 `texconv.exe` 转为 DDS。
  - INI `filename = ...` 统一指向导出的 `.dds`。

## texconv 集成

插件按以下顺序寻找转换器：

1. `MODIMP_TEXCONV` 环境变量。
2. `assets/tools/texconv/texconv.exe` 内置工具。
3. 系统 `PATH` 中的 `texconv.exe`。

内置工具来自 Microsoft DirectXTex，许可证见 `assets/tools/texconv/LICENSE-DirectXTex.txt`。

## 文件命名

导出命名使用稳定格式：

```text
Texture/<region-token>-<slot-token>-<content-hash>.dds
```

示例：

```text
Texture/0456d530_105840_70866-t7-a1b2c3d4.dds
```

## 后续计划

- 做材质 draw grouping：同材质 draw 合并绑定，换材质时切换 `ps-t*`。
- 细化 profile 中各贴图的 DXGI 格式偏好，减少未知来源贴图的默认猜测。
- 支持贴图标记预设模板，减少每个角色重复标记工作。
