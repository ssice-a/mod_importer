// Store rerun native scratch outputs into the currently selected pose slot.
//
// t0 = ScratchPosition
// t1 = ScratchFrame
// t2 = CharacterMetaTable
// u0 = PoseState UAV
// u1 = PoseSkinnedPosition UAV
// u2 = PoseSkinnedNormal UAV

Buffer<float> ScratchPosition : register(t0);
Buffer<float4> ScratchFrame   : register(t1);
Buffer<uint4> CharacterMeta   : register(t2);

RWBuffer<uint> PoseState          : register(u0);
RWBuffer<float> PoseSkinnedPosition : register(u1);
RWBuffer<float4> PoseSkinnedNormal  : register(u2);

static const uint PoseStateSelectedSlot = 1u;
static const uint PoseStateSelectionValid = 2u;
static const uint MetaRuntimeRow = 1u;

uint max_pose_slots()
{
    return max(CharacterMeta[MetaRuntimeRow].x, 1u);
}

uint position_float_count()
{
    uint count = 0u;
    ScratchPosition.GetDimensions(count);
    return count;
}

uint frame_element_count()
{
    uint count = 0u;
    ScratchFrame.GetDimensions(count);
    return count;
}

uint local_vertex_count()
{
    return position_float_count() / 3u;
}

uint pose_slot_capacity(uint position_count, uint frame_count)
{
    uint pose_position_count = 0u;
    uint pose_frame_count = 0u;
    PoseSkinnedPosition.GetDimensions(pose_position_count);
    PoseSkinnedNormal.GetDimensions(pose_frame_count);

    uint safe_position_count = max(position_count, 1u);
    uint safe_frame_count = max(frame_count, 1u);
    uint slots_by_position = pose_position_count / safe_position_count;
    uint slots_by_frame = pose_frame_count / safe_frame_count;
    return min(max_pose_slots(), min(slots_by_position, slots_by_frame));
}

bool HasSelectedPose()
{
    return PoseState[PoseStateSelectionValid] != 0u;
}

uint SelectedPoseSlot()
{
    return PoseState[PoseStateSelectedSlot];
}

[numthreads(64, 1, 1)]
void main(uint3 tid : SV_DispatchThreadID)
{
    if (!HasSelectedPose())
    {
        return;
    }

    uint vertex = tid.x;
    uint vertex_count = local_vertex_count();
    if (vertex >= vertex_count)
    {
        return;
    }

    uint position_count = position_float_count();
    uint frame_count = frame_element_count();
    uint slot = SelectedPoseSlot();
    if (slot >= pose_slot_capacity(position_count, frame_count))
    {
        return;
    }

    uint src_position = vertex * 3u;
    uint dst_position = slot * position_count + src_position;
    PoseSkinnedPosition[dst_position + 0u] = ScratchPosition[src_position + 0u];
    PoseSkinnedPosition[dst_position + 1u] = ScratchPosition[src_position + 1u];
    PoseSkinnedPosition[dst_position + 2u] = ScratchPosition[src_position + 2u];

    uint src_frame = vertex * 2u;
    uint dst_frame = slot * frame_count + src_frame;
    PoseSkinnedNormal[dst_frame + 0u] = ScratchFrame[src_frame + 0u];
    PoseSkinnedNormal[dst_frame + 1u] = ScratchFrame[src_frame + 1u];
}
