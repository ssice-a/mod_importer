// Publish the selected pose-slot skinned outputs to the draw-facing buffers.
//
// t0 = PoseSkinnedPosition
// t1 = PoseSkinnedNormal
// t2 = CharacterMetaTable
// u0 = PoseState UAV
// u1 = Final SkinnedPosition UAV
// u2 = Final SkinnedNormal UAV

Buffer<float> PoseSkinnedPosition : register(t0);
Buffer<float4> PoseSkinnedNormal  : register(t1);
Buffer<uint4> CharacterMeta       : register(t2);

RWBuffer<uint> PoseState      : register(u0);
RWBuffer<float> FinalPosition : register(u1);
RWBuffer<float4> FinalNormal  : register(u2);

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
    FinalPosition.GetDimensions(count);
    return count;
}

uint frame_element_count()
{
    uint count = 0u;
    FinalNormal.GetDimensions(count);
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

    uint src_position = slot * position_count + vertex * 3u;
    uint dst_position = vertex * 3u;
    FinalPosition[dst_position + 0u] = PoseSkinnedPosition[src_position + 0u];
    FinalPosition[dst_position + 1u] = PoseSkinnedPosition[src_position + 1u];
    FinalPosition[dst_position + 2u] = PoseSkinnedPosition[src_position + 2u];

    uint src_frame = slot * frame_count + vertex * 2u;
    uint dst_frame = vertex * 2u;
    FinalNormal[dst_frame + 0u] = PoseSkinnedNormal[src_frame + 0u];
    FinalNormal[dst_frame + 1u] = PoseSkinnedNormal[src_frame + 1u];
}
