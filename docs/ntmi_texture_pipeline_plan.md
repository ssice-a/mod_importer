# NTMI Texture Pipeline Plan

本文档记录当前贴图导入、导出和 INI 绑定规则。当前阶段优先跑通流程：不做 DDS 转换，不依赖外部工具。

## 当前规则

- FrameAnalysis 从可见 g-buffer draw 中记录 `ps-t5 / ps-t7 / ps-t8 / ps-t18`。
- 导入时为模型创建材质：
  - `ps-t7` 作为基础色连接到 Principled BSDF `Base Color`。
  - `ps-t5` 作为法线图，以 Non-Color 连接到 Normal Map，再连接到 Principled BSDF `Normal`。
  - `ps-t8 / ps-t18` 暂时只记录，不默认连线。
- 导出 `Buffers + INI` 时，直接把记录到的源贴图原样复制到导出目录 `Texture/`。
- INI `filename = ...` 使用实际复制后的扩展名，例如 `.dds / .jpg / .png`。
- 插件不自动转换 DDS，不下载外部工具，也不修改 Blender 工程里的源图片。

## 文件命名

导出命名使用稳定格式：

```text
Texture/<region-token>-<slot-token>-<content-hash>.<source-extension>
```

示例：

```text
Texture/0456d530_105840_70866-ps_t7-a1b2c3d4.jpg
```

## 后续计划

- 如果确认游戏运行时必须 DDS，再单独实现可选转换链。
- 材质多 draw group 仍需继续完善：同材质 draw 合并绑定，换材质时切换 `ps-t*`。
- `ps-t8 / ps-t18` 的通道语义后续按 profile 再细化。
