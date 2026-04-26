// =========================================================
// yihuan_gather_t0_cs.hlsl
//
// Gather rows from the exported global T0 store back into a local palette.
//
// Expected bindings:
//   t0 = Global T0 store
//   t1 = External palette map, palette[localBone] = globalBoneIndex
//   t2 = Palette meta, meta[0] = local_bone_count as R32_FLOAT
//   u0 = Local T0 UAV
//
// Layout:
//   global palette row = globalBone * 3 + row
//   local palette row  = localBone * 3 + row
// =========================================================

StructuredBuffer<uint4> GlobalT0Store : register(t0);
Buffer<uint> LocalPalette             : register(t1);
Buffer<float> PaletteMeta             : register(t2);

RWStructuredBuffer<uint4> LocalT0UAV : register(u0);

[numthreads(64, 1, 1)]
void main(uint3 tid : SV_DispatchThreadID)
{
    uint local_row = tid.x;
    uint local_bone_count = (uint)PaletteMeta[0];
    uint row_count = local_bone_count * 3;
    if (local_row >= row_count)
    {
        return;
    }

    uint local_bone = local_row / 3;
    uint row_in_bone = local_row % 3;
    uint global_bone = LocalPalette[local_bone];

    uint src_row = global_bone * 3 + row_in_bone;
    uint dst_row = local_bone * 3 + row_in_bone;

    LocalT0UAV[dst_row] = GlobalT0Store[src_row];
}
