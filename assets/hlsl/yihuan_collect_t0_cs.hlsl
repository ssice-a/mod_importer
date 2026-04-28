// =========================================================
// yihuan_collect_t0_cs.hlsl
//
// Collect is keyed by current cs-cb0 contents plus INI-provided collect meta.
//
// Expected bindings inherited from the original skin CS:
//   cb0 = original skin dispatch params
//   t0  = original local T0 palette for that dispatch
// Expected bindings set by the INI:
//   t2  = collect meta buffer, four uints:
//         expected_start expected_count global_bone_base bone_count
//   u0  = global T0 store UAV
// =========================================================

cbuffer OriginalSkinCB0 : register(b0)
{
    uint4 SkinCB0_0;
};

StructuredBuffer<uint4> OriginalT0 : register(t0);
Buffer<uint> CollectMeta : register(t2);
RWStructuredBuffer<uint4> GlobalT0Store : register(u0);

bool CurrentCB0Matches(uint expected_start, uint expected_count)
{
    bool primary_form = (SkinCB0_0.y == expected_start && SkinCB0_0.z == expected_count);
    bool final_form = (SkinCB0_0.z == expected_start && SkinCB0_0.w == expected_count);
    return primary_form || final_form;
}

uint4 LoadCollectMeta()
{
    return uint4(CollectMeta[0], CollectMeta[1], CollectMeta[2], CollectMeta[3]);
}

[numthreads(64, 1, 1)]
void main(uint3 tid : SV_DispatchThreadID)
{
    uint4 meta = LoadCollectMeta();
    uint expected_start = meta.x;
    uint expected_count = meta.y;
    uint global_bone_base = meta.z;
    uint bone_count = meta.w;
    if (!CurrentCB0Matches(expected_start, expected_count))
    {
        return;
    }

    uint local_row = tid.x;
    uint row_count = bone_count * 3u;
    if (local_row >= row_count)
    {
        return;
    }

    uint local_bone = local_row / 3u;
    uint row_in_bone = local_row % 3u;
    uint global_bone = global_bone_base + local_bone;

    uint src_row = local_bone * 3u + row_in_bone;
    uint dst_row = global_bone * 3u + row_in_bone;
    GlobalT0Store[dst_row] = OriginalT0[src_row];
}
