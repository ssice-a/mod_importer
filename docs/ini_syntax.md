# INI 语法

本文记录当前插件生成的 NTMI fast-path INI 规则。

旧的 `ShaderOverride + checktextureoverride = ib`、ShaderRegex、BoneStore、PoseSlot、角色专属 HLSL 复制方案已经废弃，不属于当前支持范围。

## 运行链路

当前 INI 的核心流程是：

```text
原游戏蒙皮 CS
-> Collector 收集原游戏骨骼 atlas
-> TextureOverride 命中时，按当前 vb0 实例触发 Collector build
-> CommandList_SkinParts_* 调用 NTMI Core 蒙皮
-> Fast TextureOverride 替换 IB/VB/VS/PS 资源并 draw 自定义模型
```

## TextureOverride

一个 draw region 由原游戏 IB hash、`match_first_index`、`match_index_count` 精确命中。

```ini
[TextureOverride_IB_0456d530_105840_70866]
hash = 0456d530
match_first_index = 70866
match_index_count = 105840
handling = skip
collector = CollectorSkinPart_0456d530, vb0
ib = ResourcePart_0456d530_105840_70866_part00_IB
match = vb, dynamic, ResourcePart_0456d530_105840_70866_part00_RuntimeSkinnedPositionVB
match = vs, dynamic_prev, ResourcePart_0456d530_105840_70866_part00_RuntimePrevSkinnedPosition
match = vs, 9393e3bb, ResourcePart_0456d530_105840_70866_part00_Texcoord
match = vs, 9130d548, ResourcePart_0456d530_105840_70866_part00_Position
match = vs, dynamic, ResourcePart_0456d530_105840_70866_part00_RuntimeSkinnedNormal
match = vs, d77b480e, ResourcePart_0456d530_105840_70866_part00_OutlineParam
ps-t7 = ResourceTexture_0456d530_105840_70866_T7
drawindexed = 77442,0,0
```

规则：

- `hash` 是当前绑定的原游戏 IB resource hash。
- `match_first_index` 与 `match_index_count` 必须精确。
- `collector = CollectorName, vb0` 表示用当前原游戏 `vb0` 实例触发对应 Collector。
- `handling = skip` 表示跳过原游戏 draw，改由自定义 draw 替代。
- `ib = ...` 绑定导出的自定义 IB。
- `drawindexed = index_count,start_index,base_vertex` 从自定义 IB 中绘制指定切片。

## match 语法

`match = ...` 使用修改版 3DMigoto 的 fast matching 语法。

```ini
match = vb, dynamic, ResourcePart_*_RuntimeSkinnedPositionVB
match = vs, dynamic_prev, ResourcePart_*_RuntimePrevSkinnedPosition
match = vs, <resource_hash>, ResourcePart_*_Texcoord
match = vs, <resource_hash>, ResourcePart_*_Position
match = vs, dynamic, ResourcePart_*_RuntimeSkinnedNormal
match = ps, <resource_hash>, ResourceTexture_*
```

含义：

- `dynamic` 使用当前 Collector group 记录的原游戏 runtime resource pointer 来绑定。
- `dynamic_prev` 读取当前 position dynamic slot 的上一帧资源，主要用于 TAA/velocity。
- hash match 用于静态 VS/PS 资源替换，例如 texcoord、position、outline 参数或贴图。
- PS 贴图既可以用 `ps-tN = Resource...` 显式绑定，也可以在适合的 pass 中用 `match = ps, hash, Resource...`。

## Collector

Collector 负责收集原游戏骨骼 atlas，并把原游戏 runtime 输出资源映射到导出的动态资源。

```ini
[CollectorSkinPart_0456d530]
group = cs-u1
match_cs_t0_hash = f5def9cf
match_cs_u0_hash = c0db46f4
match_cs_u1_hash = fa3995da
collect = write, cs-t0, cs-cb0[1]
build = Resource\NTMIv1\RuntimeGlobalT0
map = cs-u1:ResourcePart_0456d530_105840_70866_part00_RuntimeSkinnedPosition, cs-u1:ResourcePart_0456d530_105840_70866_part00_RuntimeSkinnedPositionVB, cs-u0:ResourcePart_0456d530_105840_70866_part00_RuntimeSkinnedNormal
map = cs-u1:ResourcePart_0456d530_35697_176706_part00_RuntimeSkinnedPosition, cs-u1:ResourcePart_0456d530_35697_176706_part00_RuntimeSkinnedPositionVB, cs-u0:ResourcePart_0456d530_35697_176706_part00_RuntimeSkinnedNormal
run = CommandList_SkinParts_0456d530
```

规则：

- `group = cs-u1` 通常按原游戏 skinned position 输出资源分组，用于区分同屏实例、不同动作和残影。
- `match_cs_t0_hash`、`match_cs_u0_hash`、`match_cs_u1_hash` 是 Collector 的资源级 guard，不是 shader hash。
- `collect = write, cs-t0, cs-cb0[1]` 表示用稳定的 CB key 收集当前段骨骼输入。
- `build = Resource\NTMIv1\RuntimeGlobalT0` 表示为当前 group 构建全局骨骼矩阵池。
- 每一条 `map = ...` 都声明一组原游戏输出资源到自定义动态资源的映射。
- `run = CommandList_SkinParts_*` 在 build 后调用自定义蒙皮。

Analyzer 应当从目标 g-buffer draw 实际使用的 `vb0` 反推 producer CS 输出池，再确定 Collector 链。不要只靠 VS/PS shader hash 猜骨骼链。

## Skin CommandList

导出的 skin CommandList 使用高位 CS 槽位调用 NTMI Core。

```ini
[CommandList_SkinParts_0456d530]
cs-t64 = Resource\NTMIv1\RuntimeGlobalT0

cs-t65 = ResourcePalette_0456d530_105840_70866_part00
$\NTMIv1\vertex_count = 24632
cs-t66 = ResourcePart_0456d530_105840_70866_part00_BlendTyped
cs-t67 = ResourcePart_0456d530_105840_70866_part00_Normal
cs-t68 = ResourcePart_0456d530_105840_70866_part00_Position
cs-u6 = ResourcePart_0456d530_105840_70866_part00_RuntimeSkinnedNormal_UAV
cs-u7 = ResourcePart_0456d530_105840_70866_part00_RuntimeSkinnedPosition_UAV
run = CommandList\NTMIv1\SkinFromBoundSlots
ResourcePart_0456d530_105840_70866_part00_RuntimeSkinnedPosition = copy ResourcePart_0456d530_105840_70866_part00_RuntimeSkinnedPosition_UAV
ResourcePart_0456d530_105840_70866_part00_RuntimeSkinnedPositionVB = copy ResourcePart_0456d530_105840_70866_part00_RuntimeSkinnedPosition_UAV
ResourcePart_0456d530_105840_70866_part00_RuntimeSkinnedNormal = copy ResourcePart_0456d530_105840_70866_part00_RuntimeSkinnedNormal_UAV
```

当前 ABI：

- `cs-t64`：Collector build 出来的全局骨骼矩阵池。
- `cs-t65`：当前 part 的 palette。
- `cs-t66`：当前 part 的 blend index/weight。
- `cs-t67`：当前 part 的静态 normal/frame。
- `cs-t68`：当前 part 的静态 position。
- `cs-t69`：可选 shapekey static 数据。
- `cs-t70`：可选 shapekey runtime 权重。
- `cs-u6`：runtime skinned normal 输出。
- `cs-u7`：runtime skinned position 输出。

`$\NTMIv1\vertex_count` 是导出 part 的顶点数，不是 index count。

## 资源命名

静态 per-part 资源：

- `ResourcePart_*_IB`
- `ResourcePart_*_Position`
- `ResourcePart_*_BlendTyped`
- `ResourcePart_*_Normal`
- `ResourcePart_*_Texcoord`
- `ResourcePart_*_OutlineParam`
- `ResourcePalette_*`

动态 per-part 资源：

- `ResourcePart_*_RuntimeSkinnedPosition_UAV`
- `ResourcePart_*_RuntimeSkinnedPosition`
- `ResourcePart_*_RuntimeSkinnedPositionVB`
- `ResourcePart_*_RuntimeSkinnedNormal_UAV`
- `ResourcePart_*_RuntimeSkinnedNormal`
- `ResourcePart_*_RuntimePrevSkinnedPosition`

`dynamic_slots` 默认 16，用于覆盖同屏同角色实例、残影和其他并发 group。

## Palette 规则

每个导出 part 都有独立 local palette。

```text
Blend local bone index -> Palette local slot -> RuntimeGlobalT0 global bone index
```

规则：

- Blender 顶点组在导出前应当是全局骨骼编号语义。
- 导出时每个 part 会重新分配从 0 开始的 local bone index。
- palette 保存 local bone index 到 global bone index 的映射。
- 单个导出 part 的 local bone 数不能超过 256，因为 blend index 按 8-bit 打包。
- IB 可以按最大索引自动导出为 `R16_UINT` 或 `R32_UINT`，顶点数不再受 65535 限制。

## Shapekey

Runtime shapekey 是可选功能。

静态 position 和 normal 总是导出 Blender 当前可见结果。如果某个 shapekey 在 Blender 中是 `0.7`，导出的静态网格已经包含 `0.7` 的结果。Runtime shapekey 数据只保存相对当前导出状态的增量，避免游戏初始画面重复叠加同一个 shapekey。

