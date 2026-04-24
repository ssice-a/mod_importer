# Mod Importer

这个 Blender 插件现在按“**通用核心 + Profile**”工作，当前首个实现的 Profile 是 **异环**。

## 当前异环 Profile 支持

- 直接输入 `IB Hash` 自动从 `FrameAnalysis/log.txt + deduped` 解析模型
- 导入整模所有 slice，而不是只导单个 draw
- 导入并保留：
  - pre-CS 位置 `7fec12c0`
  - 权重索引/权重 `9337f625`
  - pre-CS 法线/方向源 `d0b09bfb`
  - packed UV `ad3c9baf`
  - 原始 `firstindex/indexcount`
  - 生产该 slice 的 CS dispatch / hash / `cs-cb0` hash
- Blender 活动 UV 作为导出时的 `UV0`
- 其余 3 组 packed `half2` 原样保存并导出
- 顶点组命名使用纯数字字符串，便于和外部骨骼合并插件兼容
- 按集合重建共享大缓冲并导出：
  - `IB`
  - pre-CS 位置
  - 权重索引/权重
  - pre-CS 法线/方向
  - packed UV
- 导出 runtime HLSL：
  - `yihuan_collect_t0_cs.hlsl`
  - `yihuan_gather_t0_cs.hlsl`

## 不由本插件负责的内容

- 不导出 palette / bone merge 数据
- 不生成 INI
- 外部 palette 继续由 `E:\vscode\3dmigoto_bone_merge` 导出

## 导入流程

1. 在侧边栏选择 `Profile = 异环`
2. 填 `Frame Dump Dir`
3. 填 `IB Hash`
4. 点 `Resolve From IB Hash`
5. 点 `Import Resolved Model`

导入后，UI 会展示：

- `Last CS Hash`
- `Last CS CB0 Hash`
- 对应 slice 的 `firstindex/indexcount`

## 导出流程

1. 编辑导入后的对象
2. 确认集合中对象仍保留导入时写入的元数据和 attributes
3. 填 `Export Collection`
4. 填 `Export Dir`
5. 点 `Export Collection Package`

导出结果包括：

- `buffers/`
  - `{ib_hash}-ib.buf`
  - `{ib_hash}-7fec12c0.buf`
  - `{ib_hash}-9337f625.buf`
  - `{ib_hash}-d0b09bfb.buf`
  - `{ib_hash}-ad3c9baf.buf`
- `hlsl/`
  - `yihuan_collect_t0_cs.hlsl`
  - `yihuan_gather_t0_cs.hlsl`
- `draw_manifest.json`
- `cs_batches.json`
- `runtime_manifest.json`

## 重要约定

- 当前运行时判断键采用 **最后一次相关 CS 的 `cs-cb0` 哈希**
- 同时保留 `last_cs_hash` 作为辅助信息
- 当前异环链路的骨骼采集 / 回填 HLSL 默认围绕 `cs-t0` 工作
- 当前索引格式仍按 `R16_UINT` 处理，导出顶点窗口不能超过 `65536` 个可索引顶点
