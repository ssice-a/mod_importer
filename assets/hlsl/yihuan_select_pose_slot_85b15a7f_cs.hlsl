// Select an existing pose slot for the current draw's original vb0.
//
// t0 = current native vb0 / position buffer (R32_FLOAT x/y/z)
// t1 = PoseFeature buffer
// t2 = CharacterMetaTable
// u0 = PoseState UAV

Buffer<float> NativePosition : register(t0);
Buffer<float> PoseFeature    : register(t1);
Buffer<uint4> CharacterMeta  : register(t2);

RWBuffer<uint> PoseState : register(u0);

static const uint PoseStateSlotCount = 0u;
static const uint PoseStateSelectedSlot = 1u;
static const uint PoseStateSelectionValid = 2u;
static const uint PoseStateMatchedDrawCount = 6u;
static const uint PoseStateFallbackCount = 7u;
static const uint PoseStateSlotValidBase = 8u;
static const uint MetaRuntimeRow = 1u;
static const uint MetaIndexRow = 3u;

uint MaxPoseSlots()
{
    return max(CharacterMeta[MetaRuntimeRow].x, 1u);
}

uint FeatureRow()
{
    return CharacterMeta[MetaIndexRow].x;
}

uint NativeFeatureVertexStart()
{
    return CharacterMeta[FeatureRow()].x;
}

uint NativeFeatureVertexCount()
{
    return CharacterMeta[FeatureRow()].y;
}

uint SampleCount()
{
    return max(CharacterMeta[FeatureRow()].z, 1u);
}

uint FeatureFloatCount()
{
    return SampleCount() * 3u;
}

uint RequiredNativeVertexCount()
{
    return NativeFeatureVertexStart() + NativeFeatureVertexCount();
}

float FeatureRmsThreshold()
{
    return asfloat(CharacterMeta[FeatureRow()].w);
}

float FeatureMaxSumSq()
{
    float threshold = FeatureRmsThreshold();
    return threshold * threshold * (float)SampleCount();
}

uint ActiveSlotCount()
{
    return min(PoseState[PoseStateSlotCount], MaxPoseSlots());
}

bool SlotIsValid(uint slot)
{
    return PoseState[PoseStateSlotValidBase + slot] != 0u;
}

void SetSelectedSlot(uint slot, bool is_valid)
{
    PoseState[PoseStateSelectedSlot] = slot;
    PoseState[PoseStateSelectionValid] = is_valid ? 1u : 0u;
}

float3 LoadFeatureSample(uint sample_index, uint native_vertex_count)
{
    uint feature_sample_count = SampleCount();
    uint feature_vertex_count = NativeFeatureVertexCount();
    uint vertex_index = NativeFeatureVertexStart();
    if (feature_sample_count > 1u && feature_vertex_count > 1u)
    {
        vertex_index += (sample_index * (feature_vertex_count - 1u)) / (feature_sample_count - 1u);
    }

    uint base_index = vertex_index * 3u;
    return float3(
        NativePosition[base_index + 0u],
        NativePosition[base_index + 1u],
        NativePosition[base_index + 2u]);
}

[numthreads(1, 1, 1)]
void main(uint3 tid : SV_DispatchThreadID)
{
    uint native_float_count = 0u;
    NativePosition.GetDimensions(native_float_count);
    uint native_vertex_count = native_float_count / 3u;

    uint slot_count = ActiveSlotCount();
    if (slot_count == 0u)
    {
        SetSelectedSlot(0u, false);
        PoseState[PoseStateFallbackCount] = PoseState[PoseStateFallbackCount] + 1u;
        return;
    }

    if (native_vertex_count < RequiredNativeVertexCount())
    {
        // Some draw-time VB references are valid as vertex buffers but not
        // readable as CS SRVs. Do not clear the current pose in that case:
        // keep publishing the most recent valid skin output instead of
        // drawing an empty replacement mesh.
        uint previous_slot = min(PoseState[PoseStateSelectedSlot], slot_count - 1u);
        if (PoseState[PoseStateSelectionValid] == 0u)
        {
            previous_slot = slot_count - 1u;
        }
        SetSelectedSlot(previous_slot, true);
        PoseState[PoseStateFallbackCount] = PoseState[PoseStateFallbackCount] + 1u;
        return;
    }

    uint best_slot = 0u;
    float best_error = 3.402823e38f;

    for (uint slot = 0u; slot < slot_count; ++slot)
    {
        if (!SlotIsValid(slot))
        {
            continue;
        }

        float sum_sq = 0.0f;
        uint slot_base = slot * FeatureFloatCount();
        uint sample_count = SampleCount();
        for (uint sample_index = 0u; sample_index < sample_count; ++sample_index)
        {
            float3 current_sample = LoadFeatureSample(sample_index, native_vertex_count);
            uint feature_base = slot_base + sample_index * 3u;
            float3 stored_sample = float3(
                PoseFeature[feature_base + 0u],
                PoseFeature[feature_base + 1u],
                PoseFeature[feature_base + 2u]);
            float3 diff = current_sample - stored_sample;
            sum_sq += dot(diff, diff);
        }

        if (sum_sq < best_error)
        {
            best_error = sum_sq;
            best_slot = slot;
        }
    }

    bool matched = (best_error <= FeatureMaxSumSq());
    SetSelectedSlot(best_slot, true);
    PoseState[PoseStateMatchedDrawCount] = PoseState[PoseStateMatchedDrawCount] + (matched ? 1u : 0u);
    PoseState[PoseStateFallbackCount] = PoseState[PoseStateFallbackCount] + (matched ? 0u : 1u);
}
