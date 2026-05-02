// Rebuild 85b15a7f_part00 LocalT0 from a selected full-global pose slot.
//
// t0 = PoseGlobalT0
// t1 = 85b15a7f_part00 palette, palette[localBone] = globalBone
// t2 = CharacterMetaTable
// u0 = PoseState UAV
// u1 = SelectedLocalT0 UAV

StructuredBuffer<uint4> PoseGlobalT0 : register(t0);
Buffer<uint> LocalPalette            : register(t1);
Buffer<uint4> CharacterMeta          : register(t2);

RWBuffer<uint> PoseState : register(u0);
RWStructuredBuffer<uint4> SelectedLocalT0 : register(u1);

static const uint PoseStateSelectedSlot = 1u;
static const uint PoseStateSelectionValid = 2u;
static const uint MetaRuntimeRow = 1u;
static const uint MetaBoneRow = 2u;

bool HasSelectedPose()
{
    return PoseState[PoseStateSelectionValid] != 0u;
}

uint SelectedPoseSlot()
{
    uint max_pose_slots = max(CharacterMeta[MetaRuntimeRow].x, 1u);
    return min(PoseState[PoseStateSelectedSlot], max_pose_slots - 1u);
}

uint GlobalBoneCount()
{
    return CharacterMeta[MetaBoneRow].x;
}

uint GlobalRowCount()
{
    return CharacterMeta[MetaBoneRow].y;
}

uint LocalBoneCount()
{
    uint count = 0u;
    LocalPalette.GetDimensions(count);
    return count;
}

uint LocalRowCount()
{
    uint rows = 0u;
    uint stride = 0u;
    SelectedLocalT0.GetDimensions(rows, stride);
    return min(rows, LocalBoneCount() * 3u);
}

uint PoseGlobalT0Capacity()
{
    uint rows = 0u;
    uint stride = 0u;
    PoseGlobalT0.GetDimensions(rows, stride);
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
    if (local_row >= LocalRowCount())
    {
        return;
    }

    uint slot = SelectedPoseSlot();
    uint local_bone = local_row / 3u;
    uint row_in_bone = local_row % 3u;
    uint global_bone = min(LocalPalette[local_bone], GlobalBoneCount() - 1u);

    uint src_row = slot * GlobalRowCount() + global_bone * 3u + row_in_bone;
    if (src_row >= PoseGlobalT0Capacity())
    {
        return;
    }
    SelectedLocalT0[local_row] = PoseGlobalT0[src_row];
}
