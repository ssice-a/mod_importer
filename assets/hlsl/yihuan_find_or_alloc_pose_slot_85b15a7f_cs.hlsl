// Find or allocate a pose slot from the native skinned position output.
//
// cb0 = original skin dispatch params
// t0 = native position output (original u1 / vb0, R32_FLOAT x/y/z)
// t2 = CharacterMetaTable
// u0 = PoseState UAV
// u1 = PoseFeature UAV

cbuffer OriginalSkinCB0 : register(b0)
{
    uint4 SkinCB0_0;
};

Buffer<float> NativePosition : register(t0);
Buffer<uint4> CharacterMeta  : register(t2);

RWBuffer<uint> PoseState    : register(u0);
RWBuffer<float> PoseFeature : register(u1);

static const uint PoseStateSlotCount = 0u;
static const uint PoseStateSelectedSlot = 1u;
static const uint PoseStateSelectionValid = 2u;
static const uint PoseStateOverflowCount = 3u;
static const uint PoseStateAllocatedCount = 4u;
static const uint PoseStateMatchedCollectCount = 5u;
static const uint PoseStateFallbackCount = 7u;
static const uint PoseStateSlotValidBase = 8u;
static const uint MetaRuntimeRow = 1u;
static const uint MetaBoneRow = 2u;
static const uint MetaIndexRow = 3u;

uint MaxPoseSlots()
{
    return max(CharacterMeta[MetaRuntimeRow].x, 1u);
}

uint FeatureRow()
{
    return CharacterMeta[MetaIndexRow].x;
}

uint CollectMetaCount()
{
    return CharacterMeta[MetaBoneRow].w;
}

uint4 LoadCollectMeta(uint meta_index)
{
    return CharacterMeta[CharacterMeta[MetaIndexRow].z + meta_index];
}

bool CurrentCB0Matches(uint expected_start, uint expected_count)
{
    bool primary_form = (SkinCB0_0.y == expected_start && SkinCB0_0.z == expected_count);
    bool final_form = (SkinCB0_0.z == expected_start && SkinCB0_0.w == expected_count);
    return primary_form || final_form;
}

bool CurrentDispatchMatchesAnyCollect()
{
    uint meta_count = CollectMetaCount();
    for (uint meta_index = 0u; meta_index < meta_count; ++meta_index)
    {
        uint4 meta = LoadCollectMeta(meta_index);
        if (CurrentCB0Matches(meta.x, meta.y))
        {
            return true;
        }
    }
    return false;
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

void MarkSlotValid(uint slot)
{
    PoseState[PoseStateSlotValidBase + slot] = 1u;
}

void SetSelectedSlot(uint slot)
{
    PoseState[PoseStateSelectedSlot] = slot;
    PoseState[PoseStateSelectionValid] = 1u;
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

float LoadStoredFeature(uint slot, uint sample_index, uint component)
{
    return PoseFeature[slot * FeatureFloatCount() + sample_index * 3u + component];
}

void StoreFeature(uint slot, uint native_vertex_count)
{
    uint feature_float_count = FeatureFloatCount();
    uint sample_count = SampleCount();
    uint pose_feature_count = 0u;
    PoseFeature.GetDimensions(pose_feature_count);

    uint slot_base = slot * feature_float_count;
    for (uint sample_index = 0u; sample_index < sample_count; ++sample_index)
    {
        float3 sample = LoadFeatureSample(sample_index, native_vertex_count);
        uint dst = slot_base + sample_index * 3u;
        if (dst + 2u >= pose_feature_count)
        {
            return;
        }
        PoseFeature[dst + 0u] = sample.x;
        PoseFeature[dst + 1u] = sample.y;
        PoseFeature[dst + 2u] = sample.z;
    }
}

[numthreads(1, 1, 1)]
void main(uint3 tid : SV_DispatchThreadID)
{
    if (!CurrentDispatchMatchesAnyCollect())
    {
        PoseState[PoseStateSelectionValid] = 0u;
        return;
    }

    uint native_float_count = 0u;
    NativePosition.GetDimensions(native_float_count);
    uint native_vertex_count = native_float_count / 3u;

    if (native_vertex_count < RequiredNativeVertexCount())
    {
        // Diagnostic fallback: the native skin UAV may be valid as a UAV but
        // not readable through this borrowed CS SRV binding. Still create a
        // usable pose slot so the later global-bone snapshot and mod rerun
        // can execute. Without this, every downstream stage early-outs and the
        // replacement mesh draws from an empty VB.
        uint slot_count_for_fallback = ActiveSlotCount();
        uint fallback_slot = 0u;
        if (slot_count_for_fallback == 0u)
        {
            PoseState[PoseStateSlotCount] = 1u;
            PoseState[PoseStateAllocatedCount] = PoseState[PoseStateAllocatedCount] + 1u;
            MarkSlotValid(0u);
        }
        else
        {
            fallback_slot = min(PoseState[PoseStateSelectedSlot], slot_count_for_fallback - 1u);
            if (PoseState[PoseStateSelectionValid] == 0u)
            {
                fallback_slot = slot_count_for_fallback - 1u;
            }
        }
        SetSelectedSlot(fallback_slot);
        PoseState[PoseStateFallbackCount] = PoseState[PoseStateFallbackCount] + 1u;
        return;
    }

    uint slot_count = ActiveSlotCount();
    uint best_slot = 0u;
    float best_error = 3.402823e38f;

    for (uint slot = 0u; slot < slot_count; ++slot)
    {
        if (!SlotIsValid(slot))
        {
            continue;
        }

        float sum_sq = 0.0f;
        uint sample_count = SampleCount();
        for (uint sample_index = 0u; sample_index < sample_count; ++sample_index)
        {
            float3 current_sample = LoadFeatureSample(sample_index, native_vertex_count);
            float3 stored_sample = float3(
                LoadStoredFeature(slot, sample_index, 0u),
                LoadStoredFeature(slot, sample_index, 1u),
                LoadStoredFeature(slot, sample_index, 2u));
            float3 diff = current_sample - stored_sample;
            sum_sq += dot(diff, diff);
        }

        if (sum_sq < best_error)
        {
            best_error = sum_sq;
            best_slot = slot;
        }
    }

    bool matched_existing = (slot_count > 0u && best_error <= FeatureMaxSumSq());
    uint selected_slot = best_slot;

    if (!matched_existing)
    {
        if (slot_count < MaxPoseSlots())
        {
            selected_slot = slot_count;
            PoseState[PoseStateSlotCount] = slot_count + 1u;
            MarkSlotValid(selected_slot);
            PoseState[PoseStateAllocatedCount] = PoseState[PoseStateAllocatedCount] + 1u;
        }
        else
        {
            // Keep rendering with the nearest known slot if the debug limit is exceeded.
            PoseState[PoseStateOverflowCount] = PoseState[PoseStateOverflowCount] + 1u;
            selected_slot = best_slot;
        }
    }
    else
    {
        PoseState[PoseStateMatchedCollectCount] = PoseState[PoseStateMatchedCollectCount] + 1u;
    }

    StoreFeature(selected_slot, native_vertex_count);
    SetSelectedSlot(selected_slot);
}
