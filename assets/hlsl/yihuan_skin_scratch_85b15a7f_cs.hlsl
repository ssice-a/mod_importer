// =========================================================
// yihuan_skin_scratch_85b15a7f_cs.hlsl
//
// Lightweight local-palette skinning for the 85b15a7f replacement mesh.
// This is intentionally independent of the currently intercepted native CS:
// f33 supplies residual/history poses, while the visible main GBuffer body in
// the 190452 dump is produced later by 1e2a. We gather the right LocalT0 first,
// then compute our own scratch pose from the mod buffers.
//
// Expected bindings:
//   t0 = gathered LocalT0 rows, 3 float4 rows per local bone
//   t1 = packed blend buffer exposed as R32_UINT, 2 uints per vertex
//   t2 = source frame buffer, 2 snorm float4 rows per vertex
//   t3 = source position buffer, 3 floats per vertex
//   u0 = scratch frame UAV, 2 snorm float4 rows per vertex
//   u1 = scratch position UAV, 3 floats per vertex
//   u2 = pose state UAV, used only to skip non-matching native dispatches
// =========================================================

StructuredBuffer<uint4> LocalT0 : register(t0);
Buffer<uint> BlendTyped : register(t1);
Buffer<float4> SourceFrame : register(t2);
Buffer<float> SourcePosition : register(t3);

RWBuffer<float4> ScratchFrameUAV : register(u0);
RWBuffer<float> ScratchPositionUAV : register(u1);
RWBuffer<uint> PoseState : register(u2);

static const uint PoseStateSelectionValid = 2u;

uint local_vertex_count()
{
    uint position_float_count = 0u;
    SourcePosition.GetDimensions(position_float_count);
    return position_float_count / 3u;
}

uint local_bone_count()
{
    uint local_t0_rows = 0u;
    uint local_t0_stride = 0u;
    LocalT0.GetDimensions(local_t0_rows, local_t0_stride);
    return local_t0_rows / 3u;
}

float3 transform_point(uint bone_index, float3 position)
{
    uint row_base = bone_index * 3u;
    float4 row0 = asfloat(LocalT0[row_base + 0u]);
    float4 row1 = asfloat(LocalT0[row_base + 1u]);
    float4 row2 = asfloat(LocalT0[row_base + 2u]);
    return float3(
        dot(row0.xyz, position) + row0.w,
        dot(row1.xyz, position) + row1.w,
        dot(row2.xyz, position) + row2.w
    );
}

float3 transform_vector(uint bone_index, float3 vector_value)
{
    uint row_base = bone_index * 3u;
    float4 row0 = asfloat(LocalT0[row_base + 0u]);
    float4 row1 = asfloat(LocalT0[row_base + 1u]);
    float4 row2 = asfloat(LocalT0[row_base + 2u]);
    return float3(
        dot(row0.xyz, vector_value),
        dot(row1.xyz, vector_value),
        dot(row2.xyz, vector_value)
    );
}

float3 normalize_or(float3 value, float3 fallback)
{
    float length_sq = dot(value, value);
    if (length_sq <= 1.0e-12)
    {
        return fallback;
    }
    return value * rsqrt(length_sq);
}

[numthreads(64, 1, 1)]
void main(uint3 tid : SV_DispatchThreadID)
{
    if (PoseState[PoseStateSelectionValid] == 0u)
    {
        return;
    }

    uint vertex_id = tid.x;
    uint vertex_count = local_vertex_count();
    if (vertex_id >= vertex_count)
    {
        return;
    }

    uint bone_count = local_bone_count();

    uint blend_base = vertex_id * 2u;
    uint packed_indices = BlendTyped[blend_base + 0u];
    uint packed_weights = BlendTyped[blend_base + 1u];

    uint4 indices = uint4(
        packed_indices & 0xffu,
        (packed_indices >> 8u) & 0xffu,
        (packed_indices >> 16u) & 0xffu,
        (packed_indices >> 24u) & 0xffu
    );
    float4 weights = float4(
        (float)(packed_weights & 0xffu),
        (float)((packed_weights >> 8u) & 0xffu),
        (float)((packed_weights >> 16u) & 0xffu),
        (float)((packed_weights >> 24u) & 0xffu)
    ) * (1.0 / 255.0);

    float weight_sum = weights.x + weights.y + weights.z + weights.w;

    uint position_base = vertex_id * 3u;
    float3 source_position = float3(
        SourcePosition[position_base + 0u],
        SourcePosition[position_base + 1u],
        SourcePosition[position_base + 2u]
    );

    uint frame_base = vertex_id * 2u;
    float4 source_frame0 = SourceFrame[frame_base + 0u];
    float4 source_frame1 = SourceFrame[frame_base + 1u];

    float3 skinned_position = source_position;
    float3 skinned_frame0 = source_frame0.xyz;
    float3 skinned_frame1 = source_frame1.xyz;

    if (weight_sum > 1.0e-6)
    {
        weights /= weight_sum;

        skinned_position = float3(0.0, 0.0, 0.0);
        skinned_frame0 = float3(0.0, 0.0, 0.0);
        skinned_frame1 = float3(0.0, 0.0, 0.0);

        if (weights.x > 0.0)
        {
            if (indices.x < bone_count)
            {
                skinned_position += transform_point(indices.x, source_position) * weights.x;
                skinned_frame0 += transform_vector(indices.x, source_frame0.xyz) * weights.x;
                skinned_frame1 += transform_vector(indices.x, source_frame1.xyz) * weights.x;
            }
        }
        if (weights.y > 0.0)
        {
            if (indices.y < bone_count)
            {
                skinned_position += transform_point(indices.y, source_position) * weights.y;
                skinned_frame0 += transform_vector(indices.y, source_frame0.xyz) * weights.y;
                skinned_frame1 += transform_vector(indices.y, source_frame1.xyz) * weights.y;
            }
        }
        if (weights.z > 0.0)
        {
            if (indices.z < bone_count)
            {
                skinned_position += transform_point(indices.z, source_position) * weights.z;
                skinned_frame0 += transform_vector(indices.z, source_frame0.xyz) * weights.z;
                skinned_frame1 += transform_vector(indices.z, source_frame1.xyz) * weights.z;
            }
        }
        if (weights.w > 0.0)
        {
            if (indices.w < bone_count)
            {
                skinned_position += transform_point(indices.w, source_position) * weights.w;
                skinned_frame0 += transform_vector(indices.w, source_frame0.xyz) * weights.w;
                skinned_frame1 += transform_vector(indices.w, source_frame1.xyz) * weights.w;
            }
        }

        skinned_frame0 = normalize_or(skinned_frame0, source_frame0.xyz);
        skinned_frame1 = normalize_or(skinned_frame1, source_frame1.xyz);
    }

    ScratchPositionUAV[position_base + 0u] = skinned_position.x;
    ScratchPositionUAV[position_base + 1u] = skinned_position.y;
    ScratchPositionUAV[position_base + 2u] = skinned_position.z;

    ScratchFrameUAV[frame_base + 0u] = float4(skinned_frame0, source_frame0.w);
    ScratchFrameUAV[frame_base + 1u] = float4(skinned_frame1, source_frame1.w);
}
