// =========================================================
// yihuan_collect_t0_cs.hlsl
//
// Collect the currently bound local T0 rows into a global store.
//
// Expected bindings:
//   t0 = Original local T0 palette (current draw/dispatch binding)
//   t1 = External palette map, palette[localBone] = globalBoneIndex
//   t2 = Palette meta, meta[0] = local_bone_count
//   u0 = Global T0 store UAV
//
// Layout:
//   local palette row  = localBone * 3 + row
//   global palette row = globalBone * 3 + row
// =========================================================

StructuredBuffer<uint4> OriginalT0 : register(t0);
Buffer<uint> LocalPalette          : register(t1);
Buffer<uint> PaletteMeta           : register(t2);

RWStructuredBuffer<uint4> GlobalT0Store : register(u0);

[numthreads(64, 1, 1)]
void main(uint3 tid : SV_DispatchThreadID)
{
    uint local_row = tid.x;
    uint local_bone_count = PaletteMeta[0];
    uint row_count = local_bone_count * 3;
    if (local_row >= row_count)
    {
        return;
    }

    uint local_bone = local_row / 3;
    uint row_in_bone = local_row % 3;
    uint global_bone = LocalPalette[local_bone];

    uint src_row = local_bone * 3 + row_in_bone;
    uint dst_row = global_bone * 3 + row_in_bone;

    GlobalT0Store[dst_row] = OriginalT0[src_row];
}
