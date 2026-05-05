# 贴图标记

贴图标记用于告诉插件：FrameAnalysis dump 中哪些 PS 贴图应该用于 Blender 材质，哪些贴图应该导出回游戏。

## 候选来源

Analyze 后，插件会从可见 draw 中收集贴图候选，尤其优先选择 RT 数量较多的 g-buffer-like draw。

每个候选会记录：

- region；
- draw index；
- PS 槽位，例如 `ps-t2` 或 `ps-t7`；
- resource hash；
- dump 源文件路径；
- PS hash；
- RT 数量。

如果能找到合适的 g-buffer-like draw，UI 会默认选中它。如果没找到，插件不会直接报错，用户仍然可以手动选择候选 draw。

## 贴图语义

当前支持四种标记：

- `基础色`：连接到 Principled BSDF 的 `Base Color`。
- `法线`：通过 Normal Map 节点连接到 Principled BSDF 的 `Normal`。
- `材质`：作为材质贴图候选保存，当前不强行解释通道含义。
- `特效`：作为特效贴图候选保存，当前不强行解释通道含义。

基础色和法线每个 region 只允许一个。新标记会替换旧标记。材质和特效允许多个。

## Blender 材质连接

点击应用贴图标记后，插件会为匹配的已导入 mesh 创建或更新材质。

基础色连接：

```text
Image Texture -> Principled BSDF Base Color
```

法线连接：

```text
Image Texture -> Normal Map -> Principled BSDF Normal
```

应用贴图标记时，插件会重建一套干净的 Principled 材质节点，避免旧临时节点阻碍连接。

## DDS 预览

Blender 不能直接预览所有 DDS 压缩格式。

插件按下面顺序查找 `texconv.exe`：

1. `MODIMP_TEXCONV` 环境变量。
2. `assets/tools/texconv/texconv.exe`。
3. 系统 `PATH` 中的 `texconv.exe`。

DDS 会被转换成 `.modimp_cache/` 下的 PNG 缩略图供 Blender 预览。原始 DDS 仍然作为导出源，除非用户在 Blender 材质中手动换成其他图片。

内置 `texconv.exe` 来自 Microsoft DirectXTex。发布插件时请保留 `assets/tools/texconv/LICENSE-DirectXTex.txt`。

## 导出规则

导出时：

- DDS 源图优先直接复制。
- PNG/JPG/TGA 等非 DDS 源图会通过 `texconv.exe` 转成 DDS。
- 贴图源文件缺失会报告警告。
- 如果某个 region 没有有效贴图标记，INI 不写对应 PS 绑定。
- 基础色通常使用 sRGB。
- 法线、材质、特效通常使用 Non-Color。

## 使用建议

- 尽量在 Analyze 后、Import 前完成贴图标记。
- 如果模型已经导入，标记后点击 `Apply Texture Marks To Models`。
- 如果材质没有更新，先重载插件或重启 Blender，再重新应用贴图标记。
- 如果 DDS 预览失败，检查 `assets/tools/texconv/texconv.exe` 和源贴图路径是否存在。

