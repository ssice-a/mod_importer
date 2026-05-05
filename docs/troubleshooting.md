# 常见问题

[English](en/troubleshooting.md) | 中文

## Analyze 找不到 draw slices

检查：

- FrameAnalysis 文件夹是否来自同一个场景和同一个角色。
- 输入的是原游戏 IB hash，而不是导出的替换 IB。
- 目标 draw 是否真的被 dump。
- 目标模型是否是 LOD 或透明部件，可能使用另一个 IB hash。

如果某个透明 IB 的顶点数与主 region 完全一致，它可能复用主 region 的动态蒙皮结果，而不是拥有独立 Collector。

## 导入模型看起来很暗

常见原因：

- 没有标记或应用基础色贴图。
- Blender 材质预览没有使用正确材质。
- 游戏坐标到 Blender 坐标转换后，面朝向与法线方向不一致。
- 自定义法线丢失或被错误重算。

当前目标规则：

- 保留游戏原始法线方向。
- 必要时修正 Blender 中的面朝向，方便编辑。
- 导出时做对应逆处理，让游戏收到期望格式。

## 贴图标记没有应用到材质

正常日志应包含类似内容：

```text
[ModImporter][Texture] ... exact region ... slots=[...]
[ModImporter][Texture] ... applying slots=...
[ModImporter][Texture] loaded image node ...
[ModImporter][Texture] linked base color ...
```

如果日志停在 `has_base=True` 或 `has_normal=True`，通常是 Blender 仍在使用旧的 Python 模块缓存。重载插件或重启 Blender 后再应用。

如果日志出现 `failed to load image`，检查源贴图路径和 `texconv.exe`。

## DDS 缩略图不显示

检查：

- `assets/tools/texconv/texconv.exe` 是否存在。
- dump 出来的 DDS 源文件是否还存在。
- `.modimp_cache/` 是否可写。
- DDS 格式是否被 DirectXTex 支持。

## 游戏内骨骼扭曲

常见原因：

- 选错了 CS producer 链。
- Collector group 与目标 g-buffer draw 实际使用的 `vb0` 不一致。
- BoneMergeMap 在错误的 region 上执行。
- 导出前顶点组不是全局骨骼编号。
- 空 numeric 顶点组或重复顶点组改变了 blend 打包顺序。
- 约束或修改器改变了导出时的 evaluated mesh。

建议检查：

- 目标 g-buffer draw 的 `vb0` 是否能追溯到同一个 Collector 输出池。
- 每个 region 是否有自己的 palette。
- blend 中写的是当前 palette 的 local bone index。
- palette 是否正确映射到期望的 global bone id。

## 顶点组出现 `.001`

这是 Blender 名称冲突导致的自动后缀。反复导入或同场景存在旧对象时容易出现。

建议：

- 干净测试时重启 Blender。
- 删除旧的同名对象和集合。
- 只对正确 region 集合下的对象执行 BoneMergeMap 转换。
- 导出前执行顶点组排序与空组清理。

## 热键影响其他 mod

3DMigoto INI 中的变量和 key section 是全局环境。热键变量必须带唯一前缀，建议包含 IB hash 或 mod id。

推荐：

```ini
global persist $swapkey_0456d530_up = 0
```

不要使用 `$swapkey_up` 这种泛用名字。

## 游戏内模型消失

检查：

- INI 是否无解析错误。
- 原游戏 IB hash、first index、index count 是否匹配当前 dump。
- runtime buffer 的 `array` 是否正确。
- `dynamic_slots` 是否足够。
- Collector 是否在替换 draw 前触发。
- `drawindexed` 范围是否在导出的 IB 内。

## 导出很慢

导出会评估 Blender mesh、修改器、shapekey、法线、UV、贴图和 palette 数据。

优化建议：

- 从导出集合中移除不需要的对象。
- 迭代时尽量减少重型实时修改器。
- 调试 Buffer 时优先用 `Buffers Only`。
- 只有 Buffer 结果稳定后再导出 INI。
