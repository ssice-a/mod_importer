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
auto_restore_touched_slots = 1
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
- `auto_restore_touched_slots = 1` 会在 fast override/post run 结束后恢复被本次命中实际改写的槽位。调试 frame analysis 时可以关闭，生产建议开启。

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

调用方必须绑定：

```ini
cs-t64 = 当前 Collector build 出来的全局骨骼池
cs-t65 = 当前部件 palette
cs-t1  = 当前部件 blend，R32_UINT，2 uint/vertex
cs-t2  = 当前部件 frame/normal 输入，R8G8B8A8_SNORM，2 row/vertex
cs-t3  = 当前部件 position 输入，R32_FLOAT，3 float/vertex
cs-u0  = 当前部件 normal/frame 输出 UAV
cs-u1  = 当前部件 position 输出 UAV

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
5. Collector。
6. Collector post skin CommandList。
7. Draw replacement TextureOverride。

当前模板采用扁平化写法：每个角色只保留一个 `CommandList_SkinParts_<ib_hash>`，按 part 顺序绑定 palette、输入、输出并调用 `CommandList\NTMIv1\SkinFromBoundSlots`。不要再为每个 part 生成额外 skin wrapper：

```ini
[CommandList_SkinParts_4c512c5c]
cs-t64 = ResourceRuntimeGlobalT0

; part 52407_0
$\NTMIv1\vertex_count = 11370
cs-t65 = Resource\Data_4c512c5c\Palette_4c512c5c_52407_0_part00
cs-t1 = ResourcePart_4c512c5c_52407_0_part00_BlendTyped
cs-t2 = ResourcePart_4c512c5c_52407_0_part00_F33Frame
cs-t3 = ResourcePart_4c512c5c_52407_0_part00_F33Position
cs-u0 = ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedNormal_UAV
cs-u1 = ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedPosition_UAV
run = CommandList\NTMIv1\SkinFromBoundSlots
ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedPosition = copy ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedPosition_UAV
ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedPositionVB = copy ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedPosition_UAV
ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedNormal = copy ResourcePart_4c512c5c_52407_0_part00_RuntimeSkinnedNormal_UAV

; part 62346_52407
$\NTMIv1\vertex_count = 18606
cs-t65 = Resource\Data_4c512c5c\Palette_4c512c5c_62346_52407_part00
cs-t1 = ResourcePart_4c512c5c_62346_52407_part00_BlendTyped
cs-t2 = ResourcePart_4c512c5c_62346_52407_part00_F33Frame
cs-t3 = ResourcePart_4c512c5c_62346_52407_part00_F33Position
cs-u0 = ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedNormal_UAV
cs-u1 = ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPosition_UAV
run = CommandList\NTMIv1\SkinFromBoundSlots
ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPosition = copy ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPosition_UAV
ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPositionVB = copy ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPosition_UAV
ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedNormal = copy ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedNormal_UAV
```

后续如果源码支持 UAV 输出直接作为 Buffer/VB/SRV 视图使用，可以再减少 publish copy；当前工具先按上面写法生成。

### Data INI

Data INI 只放静态表，建议命名：

```text
Mods/<mod_name>/<source_ib_hash>-Data.ini
```

当前只需要 palette：

```ini
namespace = Data_4c512c5c

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
| `*-ib.buf` | IB | `DXGI_FORMAT_R16_UINT` | index count 个 uint16 |
| `*-position.buf` | Position / F33Position / PositionVB | `R32_FLOAT` 或 `stride = 12` | `vertex_count * 3` 个 float |
| `*-blend.buf` | BlendTyped | `R32_UINT` | `vertex_count * 2` 个 uint |
| `*-normal.buf` | F33Frame / Normal | `R8G8B8A8_SNORM` | `vertex_count * 2` 个 row |
| `*-texcoord.buf` | Texcoord | `R16G16_FLOAT` | 按当前 shader 读取布局导出 |
| `*-outline.buf` | OutlineParam | `R8G8B8A8_UNORM` | `vertex_count` 个 row |
| `*-Palette.buf` | Palette | `R32_UINT` | local bone count 个 uint |

当前导出限制：

- 单个导出 IB 使用 `R16_UINT`，顶点数必须小于等于 65535。
- 单个 part 的 local bone index 仍是 8-bit，local palette 不能超过 256。
- 超出限制时工具应拆 part 或报错，不要静默生成错误数据。

### 动态输出资源大小

工具生成 `array` 时应遵循：

```text
RuntimeSkinnedPosition_UAV array = vertex_count * 3
RuntimeSkinnedNormal_UAV array   = vertex_count * 2
RuntimePrevSkinnedPosition array = vertex_count * 3
```

`RuntimeSkinnedPosition` / `RuntimeSkinnedPositionVB` / `RuntimeSkinnedNormal` 可以省略 `array`，复用 copy 目标 desc；但如果生成器要显式写，也必须和上面的数量一致。

### Texture

贴图资源仍按普通 3Dmigoto 写法：

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
- R16 IB 模式下 `vertex_count <= 65535`。
- 每个顶点 blend 正好 4 个 index + 4 个 weight，权重可归一化到 0..255。
- local bone index 范围小于 palette 长度，且 palette 长度小于等于 256。
- palette 中的 global bone index 小于 Collector build 后的全局骨骼数。
- normal/frame 数据必须按游戏 shader 读取的双 row 布局导出，不要只导出 Blender 法线。
- `vertex_count` 不要用 `index_count` 代替。
- `dynamic_slots` 大于等于目标同屏实例上限。
- `dynamic_prev_of` 指向的 Resource 必须存在，且两者 `dynamic_slots` 一致。
- `map = ...` 中声明的动态输出 Resource 必须和 draw 侧 `match = dynamic` 使用的 Resource 对应。
- `auto_restore_touched_slots = 1` 开启时，post run 不应依赖自己污染 CS 槽位给后续原生 dispatch 使用。

## 命名建议

推荐命名：

```text
ResourcePart_<ib_hash>_<index_count>_<first_index>_partXX_<Role>
```

例如：

```ini
ResourcePart_4c512c5c_62346_52407_part00_IB
ResourcePart_4c512c5c_62346_52407_part00_F33Position
ResourcePart_4c512c5c_62346_52407_part00_BlendTyped
ResourcePart_4c512c5c_62346_52407_part00_RuntimeSkinnedPositionVB
```

Data namespace：

```ini
namespace = Data_<ib_hash>
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
5. 生成 Data INI palette 资源段。
6. 生成 Collector。
7. 生成 Collector post skin CommandList。
8. 生成 TextureOverride draw replacement。
9. 输出校验报告：顶点数、索引数、local bone 数、palette 长度、动态槽数、需要用户确认的 PS 槽位。

这份规范以当前 `E:\yh\Mods\bohe\4c512c5c.ini` 和 `E:\yh\Core\NTMI` 为基准。后续如果源码增加 fast action stream 或减少 publish copy，工具只需要替换 CommandList 模板，静态 buffer 和 palette 数据格式保持不变。
