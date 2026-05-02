# 异环导入导出重构计划

## 当前原则

- 只有一个工作集合。导入、分配、拆分、BoneMergeMap 转换和导出都围绕 `modimp_collection_name`。
- 导入和导出解耦。导入只提供可编辑 mesh；导出只读取集合树和集合属性。
- 集合作为桥梁。外部插件导入的模型拖进目标 part 集合后，通过面板写入导出属性并执行骨骼编号转换。
- 导出默认只写游戏需要的 buf；可选生成 INI。不会输出额外的中间描述文件作为 mod 产物。

## 集合结构

推荐结构：

```text
<source_ib_hash>/
  <region_hash>-<index_count>-<first_index>/
    part00/
      part00_ib00/
        mesh objects...
      part00_ib01/
        mesh objects...
```

如果 part 下没有 IB 子集合，导出器仍允许 `part00` 直接包含 mesh。执行拆分工具后，会按物体边界创建 `partXX_ibYY` 子集合。

## BoneMergeMap

FrameAnalysis 分析器按 Profile 解析 draw、dispatch、IB/VB、VS/PS/CS hash、CS 资源槽位和阶段候选。结果写入当前集合，并保留 JSON text block 作为可读报告。

BoneMergeMap 的 entry 至少包含：

- `source_ib_hash`
- `region_hash`
- `first_index`
- `index_count`
- `local_bone_index`
- `global_bone_index`
- `display_name`

`Apply BoneMergeMap To Groups` 使用当前对象所属 region/part 查表，把数字局部顶点组改成全局骨骼编号。`Restore Pre-BoneMerge Group Names` 只恢复执行该转换前记录的名称，语义限定为“导出模型转换前名称”。

## 导出规则

- 每个 IB 子集合独立生成一个 palette。palette 表示该 IB 的 local bone index 如何映射到 BoneStore global bone id。
- 导出使用 evaluated mesh：应用对象变换、形态键当前混合、修改器结果和三角化，但不真实修改用户模型。
- 坐标按 Profile 逆变换写回游戏坐标。
- UV0 使用活动 UV；UV1 存在则使用 `UV1`，否则复制 UV0；UV3/UV4 优先使用对象层，缺失时写 Profile 默认值并报告。
- 法线和 tangent 默认从当前临时 mesh 重新计算并按 Profile 打包。
- 边缘光/轮廓参数属于导出子模块，按 Profile 默认值或对象/集合属性写入。

## 拆分规则

- 单个物体顶点数超过 65535：报错，要求用户先拆物体。
- 单个物体骨骼数超过 256：报错，要求用户先拆物体或减骨骼。
- 一个 part 下多个物体合计超过 65535 顶点：按物体拆成多个 IB 子集合。
- 一个 part 下多个物体合计超过 256 根不重叠骨骼：按物体拆成多个 IB 子集合。
- 每个拆分后的 IB 子集合都从 0 重新编号顶点和索引，并独立生成 palette。

## INI 对齐

- 主 INI 以当前手写 `83527398.ini` 为 canonical。
- 共享动态资源使用 `ResourceYihuanRuntime*`。
- 静态资源保留 per-part 命名：`ResourceYihuan_<part>_IB / Position / Blend / Normal / Texcoord / OutlineParam`。
- draw override 中先绑定静态 IB，再运行 pose 选择与发布，再绑定当前 VS 阶段专用槽位。
- velocity 阶段使用上一帧 `RuntimePrevSkinnedPosition`。
- BoneStore INI 只负责原生 CS 链的 GlobalT0 收集，并使用 table/buf 驱动。

## 验证清单

- 源码中不再存在旧双集合属性、旧包导入按钮、旧 palette 文件重命名入口。
- `python -B -m py_compile properties.py operators.py panel.py __init__.py core\discovery.py core\exporter.py core\importer.py` 通过。
- FrameAnalysis 能把 stage map、CS collect map、draw pass map 和 BoneMergeMap 写入当前集合。
- buf-only 导出只写 Buffer/BoneStore Buffer 所需文件。
- 可选 INI 输出的命名和槽位应向当前手写 INI 收敛。
