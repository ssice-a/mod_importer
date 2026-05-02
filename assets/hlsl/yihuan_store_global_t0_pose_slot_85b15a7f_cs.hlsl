// Copy the current dispatch's local T0 rows into the selected pose slot.
//
// cb0 = original skin dispatch params
// t0  = original local T0 palette for this dispatch
// t1  = CharacterMetaTable
// u0 = PoseState UAV
// u1 = PoseGlobalT0 UAV

cbuffer OriginalSkinCB0 : register(b0)
{
    uint4 SkinCB0_0;
};

StructuredBuffer<uint4> OriginalT0 : register(t0);
Buffer<uint4> CharacterMeta : register(t1);

RWBuffer<uint> PoseState : register(u0);
RWStructuredBuffer<uint4> PoseGlobalT0 : register(u1);

static const uint PoseStateSelectedSlot = 1u;
static const uint PoseStateSelectionValid = 2u;
static const uint MetaRuntimeRow = 1u;
static const uint MetaBoneRow = 2u;
static const uint MetaIndexRow = 3u;

bool CurrentCB0Matches(uint expected_start, uint expected_count)
{
    bool primary_form = (SkinCB0_0.y == expected_start && SkinCB0_0.z == expected_count);
    bool final_form = (SkinCB0_0.z == expected_start && SkinCB0_0.w == expected_count);
    return primary_form || final_form;
}

bool HasSelectedPose()
{
    return PoseState[PoseStateSelectionValid] != 0u;
}

uint SelectedPoseSlot()
{
    uint max_pose_slots = max(CharacterMeta[MetaRuntimeRow].x, 1u);
    return min(PoseState[PoseStateSelectedSlot], max_pose_slots - 1u);
}

uint CollectMetaCount()
{
    return CharacterMeta[MetaBoneRow].w;
}

uint4 LoadCollectMeta(uint meta_index)
{
    return CharacterMeta[CharacterMeta[MetaIndexRow].z + meta_index];
}

uint GlobalBoneCount()
{
    return CharacterMeta[MetaBoneRow].x;
}

uint GlobalRowCount()
{
    return CharacterMeta[MetaBoneRow].y;
}

uint PoseGlobalT0Capacity()
{
    uint rows = 0u;
    uint stride = 0u;
    PoseGlobalT0.GetDimensions(rows, stride);
    return rows;
}

uint OriginalT0Capacity()
{
    uint rows = 0u;
    uint stride = 0u;
    OriginalT0.GetDimensions(rows, stride);
    return rows;
}

[numthreads(64, 1, 1)]
void main(uint3 tid : SV_DispatchThreadID)
{
    if (!HasSelectedPose())
    {
        return;
    }

    uint local_row = tid.x;
    uint meta_count = CollectMetaCount();
    for (uint meta_index = 0u; meta_index < meta_count; meta_index++)
    {
        uint4 meta = LoadCollectMeta(meta_index);
        uint expected_start = meta.x;
        uint expected_count = meta.y;
        uint global_bone_base = meta.z;
        uint bone_count = meta.w;
        if (!CurrentCB0Matches(expected_start, expected_count))
        {
            continue;
        }

        uint row_count = bone_count * 3u;
        if (local_row >= row_count)
        {
            return;
        }

        uint local_bone = local_row / 3u;
        uint row_in_bone = local_row % 3u;
        uint global_bone = global_bone_base + local_bone;
        if (global_bone >= GlobalBoneCount())
        {
            return;
        }

        uint src_row = local_bone * 3u + row_in_bone;
        if (src_row >= OriginalT0Capacity())
        {
            return;
        }

        uint slot = SelectedPoseSlot();
        uint global_row = global_bone * 3u + row_in_bone;
        uint dst_row = slot * GlobalRowCount() + global_row;
        if (dst_row >= PoseGlobalT0Capacity())
        {
            return;
        }
        PoseGlobalT0[dst_row] = OriginalT0[src_row];
        return;
    }
}
