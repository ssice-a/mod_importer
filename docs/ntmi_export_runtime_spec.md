# NTMI 异环导入导出与运行时规范

本文档面向 Blender 导入导出工具，记录当前异环 fast path 运行时已经修改了什么、INI 应该怎么生成、工具需要导出哪些数据，以及容易踩到的边界。

目标不是复刻原版 3Dmigoto 的通用写法，而是服务当前异环角色替换链路：

```text
原游戏 CS 蒙皮段
-> Collector 收集每段 cs-t0
-> 结束段 build 全局骨骼池
-> NTMI Core 用全局骨骼池 + per-part palette 给自定义模型蒙皮
-> DrawIndexed fast TextureOverride 替换 IB/VB/VS/PS 资源并绘制
```

## 运行时新增能力

### DrawIndexed Fast TextureOverride

新增的 draw 入口不再依赖：

```ini
[ShaderOverride_XXX]
checktextureoverride = ib
```

而是在 `DrawIndexed` 入口直接用：

```text
match_index_count -> match_first_index -> 当前 IB resource hash
```

筛选 `[TextureOverride]`。

推荐写法：

```ini
[TextureOverride_IB_4c512c5c_62346_52407]
hash = 4c512c5c
match_first_index = 52407
match_index_count = 62346
handling = skip
...
drawindexed = 63726,0,0
```

这里的 `hash` 是当前绑定 IB 的完整 resource hash。`match_index_count` 和 `match_first_index` 必须精确，工具不应生成宽松匹配。

### 智能槽位匹配

全局 `d3dx.ini` 声明运行时允许扫描哪些槽位：

```ini
[FastTextureOverride]
match_vb_slots = 0
match_vs_slots = 3-8
match_ps_slots = 0-8,18-24
auto_restore_draw_slots = 1
; auto_restore_dispatch_slots = 0
```

`match_*_slots` 是性能门槛。工具生成的 `match = ...` 只会在这些槽位中找资源。

静态资源匹配：

```ini
match = vs, 9f1ca2bd, ResourcePart_4c512c5c_62346_52407_part00_Texcoord
match = vs, b9df1c62, ResourcePart_4c512c5c_62346_52407_part00_Position
match = ps, 480539e8, ResourceT5
```

动态资源匹配：

```ini
match = vb, dynamic, ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPositionVB
match = vs, dynamic, ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedNormal
match = vs, dynamic_prev, ResourcePart_4c512c5c_62346_52407_part00_RuntimePrevSkinnedPosition
```

规则：

- `dynamic` 不写 hash。它按 Collector 建立的原游戏 resource pointer -> 动态输出 slot 映射来绑定。
- `dynamic_prev` 只用于 TAA/velocity previous position，依赖同一个 draw 中前面的 `vb dynamic` 锁定当前实例。
- 同一个 TextureOverride 内，建议顺序固定为：`vb dynamic`、`vs dynamic_prev`、其他 `dynamic`、静态 `match`、显式槽位绑定、`drawindexed`。
- `auto_restore_draw_slots = 1` 会在 fast override draw 结束后恢复被本次命中实际改写的 IB/VB/VS/PS 槽位。调试 frame analysis 时可以关闭，生产建议开启。
- `auto_restore_dispatch_slots` 默认关闭。Collector post run 必须使用 NTMI 私有高位 CS 槽位，并在命令末尾显式清空。

### Collector

Collector 用于 CS 阶段轻量收集骨骼，不再靠 shader hash/checktextureoverride 触发。

当前角色示例：

```ini
[CollectorSkinPart_4c512c5c]
group = cs-u1
match_cs_t0_hash = bed2036c
match_cs_u0_hash = 4c1f57af
match_cs_u1_hash = 689792de
collect = write, cs-t0, cs-cb0[1]
if cs-cb0[1] == 12675 && cs-cb0[2] == 14431
  post collect = build, ResourceRuntimeGlobalT0
  map = cs-u1:ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedPosition, cs-u1:ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedPositionVB, cs-u0:ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedNormal
  map = cs-u1:ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPosition, cs-u1:ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPositionVB, cs-u0:ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedNormal
  post run = CommandList_SkinParts_4c512c5c
endif
```

含义：

- `group = cs-u1`：按当前原游戏输出 position resource pointer 分组。多人同屏、残影和主体会分到不同 group。
- `match_cs_*_hash`：确认这是目标蒙皮 CS 状态，比较的是槽位底层 resource hash，不是 CS shader hash。
- `collect = write, cs-t0, cs-cb0[1]`：以 `cs-cb0[1]` 为 key，记录本段局部骨骼 `cs-t0` 指针。同 key 后写覆盖前写。
- `if cs-cb0[...]`：判断当前 dispatch 是否为这一轮结束段。
- `finish_condition` 由最后一个目标 producer dispatch 的稳定 flat uint 字段生成。通常用 collect key 的 start lane 加实际 vertex-count lane，例如 `f33` 常见为 `cs-cb0[1] == start && cs-cb0[2] == count`，`1e2a` 常见为 `cs-cb0[1] == start && cs-cb0[3] == count`。
- Analyzer 必须先从目标 g-buffer draw 的实际 `vb0` resource identity（例如 `hash@GPUAddress`）反推 producer CS 输出池，再在同一输出池内确定 collect 区间；稳定 hash 只用于 INI guard，不能单独用来决定区间。
- `post collect = build`：原游戏当前 dispatch 执行完后，把当前 group 收集到的 `cs-t0` 按 key 从小到大拼成全局骨骼池。
- `map = ...`：每个子部件一条，显式声明原游戏输出槽位 resource pointer 到动态输出 Resource 的映射。
- `post run`：运行角色的后处理 CommandList，调用 NTMI Core 蒙皮。

### 动态资源

动态资源仍然用普通 `[ResourceXXX]`，只额外声明 `dynamic_slots`：

```ini
[ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPosition]
dynamic_slots = 16
type = Buffer
format = R32_FLOAT

[ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPositionVB]
dynamic_slots = 16
type = Buffer
stride = 12

[ResourcePart_4c512c5c_62346_52407_part00_RuntimePrevSkinnedPosition]
dynamic_slots = 16
dynamic_prev_of = ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPosition
type = Buffer
format = R32_FLOAT
array = 55818
```

规则：

- `dynamic_slots` 至少要覆盖同屏同角色实例数、主体/残影等并发 group。当前建议默认 16。
- `dynamic_prev_of` 表示这个 Resource 读取目标 current Resource 的上一帧/历史 dynamic slot。
- `RuntimeSkinnedPosition` 和 `RuntimeSkinnedPositionVB` 都映射到同一个原游戏 `cs-u1`，分别服务 VS SRV 与 VB 绑定。
- `RuntimeSkinnedNormal` 映射到原游戏 `cs-u0`。

## NTMI Core ABI

当前公共 Core 位于：

```text
E:\yh\Core\NTMI
```

Core INI：

```ini
namespace = NTMIv1

[Constants]
global $vertex_count = 0

[CustomShaderSkinFromBoundSlots]
cs = Shaders/SkinFromAtlasBoundSlots.hlsl
dispatch = $vertex_count/64+1, 1, 1

[CommandListSkinFromBoundSlots]
run = CustomShaderSkinFromBoundSlots
```

当前 Core 只需要 `Shaders/SkinFromAtlasBoundSlots.hlsl`。旧的 local T0 两段式 shader 不再属于有效运行链路，插件也不应生成或引用它们。

调用方必须绑定：

```ini
cs-t64 = 当前 Collector build 出来的全局骨骼池
cs-t65 = 当前部件 palette
cs-t66 = 当前部件 blend，R32_UINT，2 uint/vertex
cs-t67 = 当前部件 frame/normal 输入，R8G8B8A8_SNORM，2 row/vertex
cs-t68 = 当前部件 position 输入，R32_FLOAT，3 float/vertex
cs-u6  = 当前部件 normal/frame 输出 UAV
cs-u7  = 当前部件 position 输出 UAV

$\NTMIv1\vertex_count = 当前部件顶点数
run = CommandList\NTMIv1\SkinFromBoundSlots
```

`vertex_count` 是自定义模型该部件的顶点数，不是 draw 的 index count。它必须和导出的 position/blend/frame buffer 顶点数一致。

Core HLSL 当前直接从全局骨骼池读取：

```text
global bone = LocalPalette[local bone]
matrix rows = PoseGlobalT0[global bone * 3 + 0..2]
```

因此工具不再需要生成：

- `CharacterMetaTable`
- `local_t0_rows`
- LocalT0 中间 buffer
- BoneStore 旧命名资源

## Shapekey 运行时方案

统一命名使用 `shapekey`，不要再混用 morph / shape key / blendshape。第一版目标是保持 Blender 所见即所得，同时为后续按键、动画或额外 HLSL 写权重留下稳定接口。

### 基础语义

静态 `Position / Normal` 永远导出 Blender 当前可见结果。无论是否启用 runtime shapekey，导出后的初始模型都必须和 Blender 视图一致。

运行时 shapekey 只负责从导出时的当前形态继续增减，不重新套一遍初始权重：

```text
delta_weight = runtime_weight - initial_weight
position_runtime = exported_visible_position + sum(delta_weight * delta_position)
frame_runtime    = exported_visible_frame    + sum(delta_weight * delta_frame)
```

示例：

```text
Blender 导出前 Key_A.value = 0.7
静态 Position/Normal = Key_A 0.7 后的可见网格
ShapekeyRuntime[Key_A] 初始 = 0.7
HLSL 中 delta_weight = 0.7 - 0.7 = 0
=> 游戏初始画面等于 Blender 当前画面
```

如果运行时把 `Key_A` 改成 `1.0`，只额外应用 `+0.3`；改成 `0.0`，则应用 `-0.7`。

### 执行顺序

shapekey 必须发生在蒙皮前：

```text
Blender 当前可见 position/frame
-> Shapekey delta adjustment
-> CommandList\NTMIv1\SkinWithShapekeyFromBoundSlots
-> DrawIndexed fast TextureOverride
```

不要在蒙皮后修改 position。shapekey 是模型局部空间变形，必须先改未蒙皮的 position 和 frame，再交给骨骼蒙皮。

### 选择性导出

每个 shapekey 都可以独立选择是否导出为 runtime shapekey：

- 选中的 shapekey 会写入 `ShapekeyStatic`，并在 `ShapekeyRuntime` 中拥有稳定 `key_index`。
- 未选中的 shapekey 只参与静态网格烘焙；它们的当前效果会留在 `Position / Normal` 中，但后续不能运行时调整。
- 如果某个导出 part 没有任何 runtime shapekey，不生成 shapekey 资源，也不调用 shapekey 版 Core HLSL。

### 资源分组

导出资源只分两类逻辑数据区，避免资源段膨胀：

```ini
[ResourcePart_<part>_ShapekeyStatic]
type = Buffer
format = R32G32B32A32_FLOAT
filename = Buffer/<part>-shapekey-static.buf

[ResourceShapekeyRuntime_<character>_UAV]
type = RWBuffer
format = R32_FLOAT
array = <runtime_shapekey_count>

[ResourceShapekeyRuntime_<character>]
type = Buffer
format = R32_FLOAT
array = <runtime_shapekey_count>
filename = Buffer/<character>-shapekey-runtime.buf
```

- `ShapekeyStatic` 按 part 生成，保存该 part 的 shapekey 静态数据，包括 header、导出初始权重、稀疏 delta records、delta position、delta normal/tangent/frame 所需数据。具体打包格式由 Core HLSL 固定，导出器只按该 ABI 写。
- `ShapekeyRuntime` 按角色/工作集合共享，保存当前 runtime shapekey 权重。这样同一个 shapekey 影响多个 part 时，动画或按键逻辑只需要写一份权重。
- 当前实现导出 `<character>-shapekey-runtime.buf` 作为初始权重，并由 skin HLSL 直接读取 `ResourceShapekeyRuntime_<character>`。后续动画 HLSL 如果写 `ResourceShapekeyRuntime_<character>_UAV`，必须在动画链路内显式 publish/copy 到可读 Buffer。
- `ShapekeyRuntime_UAV` 和 `ShapekeyRuntime` 只是同一个动态数据区的写视图/读视图；不允许再生成单独的 default weights、per-key weight 或临时 morph buffer。

### 稳定 key 索引

`ShapekeyStatic` 中每个 runtime shapekey 必须记录稳定 key 信息：

```text
key_index
name_hash / name_id
initial_weight
min_weight
max_weight
delta_record_offset
delta_record_count
```

Blender 中的真实名称保存在集合/对象属性和 JSON 报告中，HLSL 只认 `key_index`。后续动画系统只需要写 `ShapekeyRuntime[key_index]`，不需要理解 Blender 名称。

### 稀疏 delta

不能让每个顶点遍历所有 shapekey。`ShapekeyStatic` 应采用按顶点可寻址的稀疏结构：

```text
vertex -> affected shapekey records
record -> key_index + delta_position + delta_frame
```

运行时每个顶点只处理真正影响自己的 shapekey records，避免 runtime shapekey 数量增加后线性拖慢所有顶点。

### 顶点顺序约束

shapekey delta 必须按最终导出顶点顺序写入，而不是 Blender 原始顶点 index。导出器的统一顺序是：

1. 生成最终 part 顶点重编号。
2. 用同一套重编号写 position、blend、frame、texcoord、outline。
3. 用同一套重编号写 shapekey delta。
4. 用同一套 part/palette 划分生成 shapekey 静态数据。

如果一个 region 因 local palette 超过 256 被拆成多个导出 part，每个 part 都有自己的 `ShapekeyStatic`。`ShapekeyRuntime` 仍然可以按角色共享，但 part 内的 records 必须引用共享 runtime buffer 中的稳定 `key_index`。

### 法线与 frame

shapekey 必须影响 position 和 frame。只改 position 会导致 g-buffer、轮廓线、边缘光和法线贴图表现不一致。

导出器必须用同一套 profile 转换和 frame 打包逻辑生成 delta。建议导出：

```text
delta_position = shapekey_position - exported_visible_position
delta_normal   = shapekey_normal   - exported_visible_normal
delta_tangent  = shapekey_tangent  - exported_visible_tangent
```

运行时：

```text
dw       = runtime_weight - initial_weight
position = exported_visible_position + sum(dw * delta_position)
normal   = normalize(exported_visible_normal + sum(dw * delta_normal))
tangent  = normalize(exported_visible_tangent + sum(dw * delta_tangent))
```

导出器必须使用 Blender evaluated mesh / custom normal 的当前结果作为可见基准，并按游戏原始 frame 格式重新打包。坐标和符号仍由 profile converter 统一处理。

当前实现会尝试在导出时对每个 runtime shapekey 额外评估一次 mesh，用相同 loop/frame 规则生成 frame delta。如果 Blender 无法重建 tangent frame 或评估后 loop 数不一致，该 shapekey 的 frame delta 会退化为 0 并在导出报告中警告；position delta 仍会导出。

### 第一版限制

- 第一版只支持相对 shapekey 且导出前后拓扑一致的情况。
- 非 Basis relative key、驱动器、复杂联动可以先烘焙进静态网格，不进入 runtime shapekey。
- 修改器如果改变拓扑，允许烘焙静态结果，但 runtime shapekey 必须报警跳过，避免 delta 顶点顺序失配。

## 推荐生成的 INI 结构

### 主 INI

主 INI 负责角色运行逻辑，建议命名：

```text
Mods/<mod_name>/<source_ib_hash>.ini
```

应包含：

1. 贴图资源。
2. 全局骨骼池工作资源。
3. 每个 part 的静态资源。
4. 每个 part 的动态输出资源。
5. 每个 part 的 palette 资源段。
6. Collector。
7. Collector post skin CommandList。
8. Draw replacement TextureOverride。

当前模板采用扁平化写法：每个角色只保留一个 `CommandList_SkinParts_<ib_hash>`，按 part 顺序绑定 palette、输入、输出并调用 `CommandList\NTMIv1\SkinFromBoundSlots`。不要再为每个 part 生成额外 skin wrapper：

```ini
[CommandList_SkinParts_4c512c5c]
cs-t64 = ResourceRuntimeGlobalT0

; part 52407_0
$\NTMIv1\vertex_count = 11370
cs-t65 = ResourcePalette_4c512c5c_52407_0_part00
cs-t66 = ResourcePart_4c512c5c_52407_0_part00_BlendTyped
cs-t67 = ResourcePart_4c512c5c_52407_0_part00_Normal
cs-t68 = ResourcePart_4c512c5c_52407_0_part00_Position
cs-u6 = ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedNormal_UAV
cs-u7 = ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedPosition_UAV
run = CommandList\NTMIv1\SkinFromBoundSlots
ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedPosition = copy ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedPosition_UAV
ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedPositionVB = copy ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedPosition_UAV
ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedNormal = copy ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedNormal_UAV

; part 62346_52407
$\NTMIv1\vertex_count = 18606
cs-t65 = ResourcePalette_4c512c5c_62346_52407_part00
cs-t66 = ResourcePart_4c512c5c_62346_52407_part00_BlendTyped
cs-t67 = ResourcePart_4c512c5c_62346_52407_part00_Normal
cs-t68 = ResourcePart_4c512c5c_62346_52407_part00_Position
cs-u6 = ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedNormal_UAV
cs-u7 = ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPosition_UAV
run = CommandList\NTMIv1\SkinFromBoundSlots
ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPosition = copy ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPosition_UAV
ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPositionVB = copy ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPosition_UAV
ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedNormal = copy ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedNormal_UAV

cs-t64 = null
cs-t65 = null
cs-t66 = null
cs-t67 = null
cs-t68 = null
cs-u6 = null
cs-u7 = null
```

后续如果源码支持 UAV 输出直接作为 Buffer/VB/SRV 视图使用，可以再减少 publish copy；当前工具先按上面写法生成。

### Palette 资源段

palette 直接写在主 INI 中：

```ini
[ResourcePalette_4c512c5c_52407_0_part00]
type = Buffer
format = R32_UINT
filename = Buffer/4c512c5c-52407-0-Palette.buf

[ResourcePalette_4c512c5c_62346_52407_part00]
type = Buffer
format = R32_UINT
filename = Buffer/4c512c5c-62346-0-Palette.buf
```

工具不要再生成 `CharacterMetaTable`，也不要把 Collector 放进 Core。

## 工具必须导出的文件

### 每个 part 的 Buffer

以 `4c512c5c-62346-52407_part00` 为例：

| 文件 | Resource 类型 | 格式 | 数量规则 |
| --- | --- | --- | --- |
| `*-ib.buf` | IB | `DXGI_FORMAT_R16_UINT` 或 `DXGI_FORMAT_R32_UINT` | index count 个 uint16/uint32 |
| `*-position.buf` | Position / PositionVB | `R32_FLOAT` 或 `stride = 12` | `vertex_count * 3` 个 float |
| `*-blend.buf` | BlendTyped | `R32_UINT` | `vertex_count * 2` 个 uint |
| `*-normal.buf` | Normal | `R8G8B8A8_SNORM` | `vertex_count * 2` 个 row |
| `*-texcoord.buf` | Texcoord | `R16G16_FLOAT` | 按当前 shader 读取布局导出 |
| `*-outline.buf` | OutlineParam | `R8G8B8A8_UNORM` | `vertex_count` 个 row |
| `*-Palette.buf` | Palette | `R32_UINT` | local bone count 个 uint |

当前导出限制：

- 默认优先使用 `R16_UINT`；当最大索引超过 65535 时切换到 `R32_UINT`，不再因为顶点数超过 65535 强制拆分。
- 单个 part 的 local bone index 仍是 8-bit，local palette 不能超过 256。
- local palette 超过 256 时仍应拆 part 或报错，不要静默生成错误数据。

### 动态输出资源大小

工具生成 `array` 时应遵循：

```text
RuntimeSkinnedPosition_UAV array = vertex_count * 3
RuntimeSkinnedNormal_UAV array   = vertex_count * 2
RuntimePrevSkinnedPosition array = vertex_count * 3
```

`RuntimeSkinnedPosition` / `RuntimeSkinnedPositionVB` / `RuntimeSkinnedNormal` 可以省略 `array`，复用 copy 目标 desc；但如果生成器要显式写，也必须和上面的数量一致。

### Texture

贴图资源仍按普通 3Dmigoto 写法。异环 profile 当前默认从可见 g-buffer draw 中识别这些槽位：

- `ps-t5`：法线贴图。
- `ps-t7`：基础色贴图。
- `ps-t8`：材质贴图。
- `ps-t18`：材质贴图。

FrameAnalysis 解析器应优先从目标 region 中 RT 输出数量最多的可见 g-buffer draw 提取这些 PS 资源，并记录 resource hash、dump 文件路径、槽位号、draw id 与 PS hash。导出器再把确认后的贴图复制或写入 `Texture/`。

```ini
[ResourceT5]
filename = Texture/NM.dds
[ResourceT7]
filename = Texture/Body.dds
[ResourceT8]
filename = Texture/t8.dds
[ResourceT18]
filename = Texture/t18.dds
```

工具可以生成默认资源段，但 PS 槽位替换是否使用智能 `match` 或显式 `ps-tN` 应交给配置决定。当前 bohe 选择：

```ini
ps-t5 = ResourceT5
ps-t7 = ResourceT7
ps-t8 = ResourceT8
ps-t18 = ResourceT18
```

Blender 导入时应自动创建材质：

- `ps-t7` 对应基础色，Image Texture 使用 sRGB，连接 Principled BSDF 的 `Base Color`。
- `ps-t5` 对应法线，Image Texture 使用 Non-Color，经 `Normal Map` 节点连接 Principled BSDF 的 `Normal`。
- `ps-t8` 与 `ps-t18` 按 Non-Color 导入，保留节点与材质属性，第一版不猜测通道语义。
- 如果用户后续替换材质贴图，导出时以 Blender 材质当前引用为准。

## 工具需要保存的元数据

FrameAnalysis/导入阶段至少要保存：

1. `source_ib_hash`，例如 `4c512c5c`。
2. 每个原游戏 draw part：
   - `match_first_index`
   - `match_index_count`
   - 原 draw index ranges
   - pass 类型：depth / gbuffer / velocity / outline / shadow / extra
3. 每个导出 part：
   - `part_id`
   - `vertex_count`
   - 输出 `drawindexed = index_count,start_index,base_vertex`
   - 静态 buffer 文件名
   - local palette 文件名
4. Collector 配置：
   - `group` 槽位，例如 `cs-u1`
   - `match_cs_t0_hash`
   - `match_cs_u0_hash`
   - `match_cs_u1_hash`
   - `collect` key，例如 `cs-cb0[1]`
   - 结束条件，例如 `cs-cb0[1] == 12675 && cs-cb0[2] == 14431`
   - BoneAtlas 总行数/容量
5. Draw 智能绑定配置：
   - `match_vb_slots`
   - `match_vs_slots`
   - `match_ps_slots`
   - VS 静态资源 hash：texcoord、position、outline 等
   - TAA previous position 使用 `dynamic_prev`
6. PS 材质策略：
   - 显式 `ps-tN = Resource`
   - 或 `match = ps, hash, Resource`

## Blender 导出校验

导出前必须做这些校验：

- 每个 part 的 `vertex_count` 与 position、blend、frame、texcoord、outline 数据长度一致。
- IB 最大索引小于该 part 的 `vertex_count`。
- IB 格式必须覆盖最大索引：`max_index <= 65535` 时用 `R16_UINT`，否则用 `R32_UINT`。
- 每个顶点 blend 正好 4 个 index + 4 个 weight，权重可归一化到 0..255。
- local bone index 范围小于 palette 长度，且 palette 长度小于等于 256。
- palette 中的 global bone index 小于 Collector build 后的全局骨骼数。
- normal/frame 数据必须按游戏 shader 读取的双 row 布局导出，不要只导出 Blender 法线。
- `vertex_count` 不要用 `index_count` 代替。
- `dynamic_slots` 大于等于目标同屏实例上限。
- `dynamic_prev_of` 指向的 Resource 必须存在，且两者 `dynamic_slots` 一致。
- `map = ...` 中声明的动态输出 Resource 必须和 draw 侧 `match = dynamic` 使用的 Resource 对应。
- `auto_restore_draw_slots = 1` 只恢复 draw 侧 IB/VB/VS/PS 槽位；Collector post run 不应依赖自动 CS 恢复。
- NTMI post skin 使用 `cs-t64/t65/t66/t67/t68` 与 `cs-u6/u7`，并应在命令末尾显式清空这些私有槽位。

## 命名建议

推荐命名：

```text
ResourcePart_<ib_hash>_<index_count>_<first_index>_partXX_<Role>
```

例如：

```ini
ResourcePart_4c512c5c_62346_52407_part00_IB
ResourcePart_4c512c5c_62346_52407_part00_Position
ResourcePart_4c512c5c_62346_52407_part00_BlendTyped
ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPositionVB
```

Core namespace 固定：

```ini
namespace = NTMIv1
```

## 当前不做的事情

导出工具第一版不要做：

- 自动生成旧 `ShaderOverride + checktextureoverride = ib`。
- 自动生成旧 BoneStore / LocalT0 两阶段资源。
- 自动维护 `CharacterMetaTable`。
- 自动 patch 游戏 f33 shader。
- 自动突破 256 local bones；超过就拆 part。
- 自动把多个角色/mod 的 Collector 合并。
- 在不知道 pass 语义时猜测 PS 槽位。

## 推荐生成流程

1. 分析 FrameAnalysis，建立 draw part、CS collector、VS/PS 槽位布局和 BoneMergeMap。
2. 从 Blender 导出每个 part 的静态 mesh buffer。
3. 根据顶点组生成 per-part local palette，并把 blend local bone index 重排到该 palette。
4. 生成主 INI 资源段和动态资源段。
5. 在主 INI 内生成 palette 资源段。
6. 生成 Collector。
7. 生成 Collector post skin CommandList。
8. 生成 TextureOverride draw replacement。
9. 输出校验报告：顶点数、索引数、local bone 数、palette 长度、动态槽数、需要用户确认的 PS 槽位。

这份规范以当前 `E:\yh\Mods\bohe\4c512c5c.ini` 和 `E:\yh\Core\NTMI` 为基准。后续如果源码增加 fast action stream 或减少 publish copy，工具只需要替换 CommandList 模板，静态 buffer 和 palette 数据格式保持不变。
