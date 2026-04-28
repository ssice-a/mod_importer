# 异环导入导出解耦与集合桥梁重构计划

## 当前方向

导入、分配、导出必须解耦。导入只负责把可读 mesh 数据带进 Blender；导出只读取集合和对象上的导出绑定；导出产物默认只写游戏需要的 buf，不把 manifest 当作 mod 产物输出。

集合是中间桥梁：角色根集合下面是 region/part 集合，part 集合下面可以再创建 IB 子集合。把外部插件导入的模型拖入对应集合，再运行面板操作，就等于赋予它导出语义。

第一版自动拆分只按物体拆分，不把一个物体按三角面切开。单个物体自身超过 65535 顶点或 256 骨骼时，导出器报警并要求用户手动拆物体。

## 已落地的第一版能力

- 增加 `Analyze Frame Stages`：扫描 FrameAnalysis 的 draw/dispatch/hash/slot，生成 UI 摘要并写入 Blender text block `modimp_frame_analysis_report.json`。
- 增加 `Export Mode`：`Buffers Only` 默认只输出 buf；`Buffers + INI` 才生成 INI/HLSL。
- 导出器现在按每个导出 IB 的当前顶点组自动生成 per-IB palette，不再要求预先存在 BoneMerge palette 文件。
- 顶点组可以先按旧 palette 重命名为全局骨骼编号；导出时再按当前 IB 重新本地化为 `uint8` local palette。
- `Split Export Parts` 会在 part 集合下创建 `partXX_ibYY` 子集合，按物体边界重新分配，避免一个 IB 超过 65535 顶点或 256 骨骼。
- 导出器允许读取 `part -> ib 子集合 -> mesh` 结构；每个 IB 子集合会导出为独立 buffer part。
- 增加可逆顶点组改名：`Rename Groups From Palette` 记录原名并用 palette 把局部编号改成全局编号；`Restore Group Names` 可恢复。

## 导出规则

- 导出只写游戏运行需要的资源：IB、position、blend、normal/frame、texcoord、palette、可选 INI/HLSL。
- 导出时使用临时 evaluated mesh，应用对象变换、形态键当前混合、修改器结果和三角化；不真实修改用户原 mesh 的四边面、形态键或修改器。
- 坐标按 profile 做逆变换，保证 Blender 所见即所得，写回游戏坐标。
- 法线和 tangent 默认从临时 mesh 重新计算并按 profile 打包，不默认复用旧导入 normal/tangent 属性。
- UV0 使用活动 UV；UV1 使用名为 `UV1` 的层，缺失时复制 UV0；UV3/UV4 使用物体自身 `UV3/UV4`，缺失时使用导入属性 `packed_uv2/packed_uv3`，再缺失则写默认值并报告。
- 一个导出 IB 一个 palette。palette 的内容是当前 IB 的 local bone index 到 BoneStore global bone id 的映射。

## 集合和拆分规则

推荐集合结构：

```text
<source_ib_hash>/
  <region_hash>-<index_count>-<first_index>/
    part00/
      part00_ib00/
        mesh objects...
      part00_ib01/
        mesh objects...
```

如果 part 下没有 IB 子集合，导出器仍支持旧结构：`part00` 下面直接放 mesh。运行 `Split Export Parts` 后会自动迁移到子集合结构。

自动拆分规则：

- 单物体顶点数超过 65535：报警并停止该物体导出。
- 单物体使用骨骼数超过 256：报警并停止该物体导出。
- 多物体合计顶点数超过 65535：按物体拆成多个 IB 子集合。
- 多物体合计骨骼数超过 256：按物体拆成多个 IB 子集合。
- 每个 IB 子集合独立从 0 重编号顶点和索引，独立生成 palette。
- 自动拆分允许修改 Blender 场景：创建子集合、移动对象、添加 `__ibXX` 后缀、写入导出属性，并在状态栏报告。

## FrameAnalysis 和骨骼映射

FrameAnalysis 解析器按 profile 运行，不把异环规则写死进通用层。异环 profile 负责定义阶段判定、槽位语义、坐标变换、normal/UV/outline 打包格式。

解析器需要从帧分析目录读取：

- draw 的 `first_index/index_count/base_vertex`
- IB/VB/VS/PS/CS hash
- `7816b819` 这类最终稳定资源哈希
- g-buffer、depth、velocity、outline、shadow/skip 候选阶段
- producer CS dispatch 顺序和 `cs-cb0/t0/u*` 资源

BoneMergeMap 按 CS dispatch 顺序生成。它记录原生子部位的 local bone range、global bone base、bone count、source hash 和用户可读改名。外部模型可以用按钮按这张映射表重命名顶点组，再由导出器重新生成 per-IB palette。

## 后续还需要补齐

- 面板里增加可编辑的阶段确认 UI，而不是只输出 JSON text block。
- `UV3/UV4` 的集合级默认值目前主要依赖导入属性；后续要把集合级默认 UV buffer/属性正式写入集合桥梁。
- 顶点色/outline 参数需要 profile 化，缺失时写 profile 默认值，而不是散落在 exporter 里。
- 当前 FrameAnalysis stage report 只是候选报告，还没有完整反推 velocity/outline 的专用规则。
- 如果要让 `ee0ea907` 参与绘制，它必须作为独立 part/IB 走同一套集合、palette、runtime buffer 和 pass layout 规则。

## 验证清单

- FrameAnalysis 扫描当前目录时能列出 body、`ee0ea907`、skin CS、depth、g-buffer、velocity/outline 候选。
- 外部插件导入模型拖进 part 集合后，能创建导出集合、拆分 IB 子集合、重命名顶点组。
- buf-only 导出不会写 INI/HLSL，也不会写 manifest。
- 导出的每个 IB 顶点数小于等于 65535，palette 骨骼数小于等于 256。
- 导出的 normal/frame 来自当前 Blender 可见结果重新计算，UV/tangent/权重 buffer 字节数符合 INI resource 格式。
