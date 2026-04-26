// =========================================================
// yihuan_skin_mesh_cs.hlsl
//
// Skin one exported part into the part-local runtime buffers.
//
// Expected bindings:
//   t0 = gathered part-local T0, uint4 rows, 3 rows per local bone
//   t1 = part pre-CS position buffer, R32_FLOAT typed buffer,
//        three scalar entries per vertex
//   t2 = part packed BlendIndices/BlendWeights buffer, uint2 per vertex
//   t3 = part pre-CS frame buffer, uint2 per vertex
//   cb0 = skin dispatch params:
//        x = input vertex_start
//        y = vertex_count
//        z = output vertex_start
//        w = local_bone_count
//   u0 = part skinned frame output, R16G16B16A16_SNORM typed buffer,
//        two float4 entries per vertex: tangent row then normal row
//   u1 = part skinned position output, R32_FLOAT typed buffer,
//        three scalar entries per vertex
//
// Notes:
//   T0 rows are interpreted as float4 rows. This matches the current reverse
//   engineering assumption that f33/1e2a consume 3-row affine skin records.
// =========================================================

StructuredBuffer<uint4> LocalT0 : register(t0);
Buffer<float> PrePosition : register(t1);
StructuredBuffer<uint2> PackedWeights : register(t2);
StructuredBuffer<uint2> PreFrame : register(t3);

cbuffer SkinDispatchCB : register(b0)
{
    uint4 SkinDispatch;
};

RWBuffer<float4> SkinnedFrame : register(u0);
RWBuffer<float> SkinnedPosition : register(u1);

uint4 UnpackU8x4(uint packed_value)
{
    return uint4(
        packed_value & 0xffu,
        (packed_value >> 8u) & 0xffu,
        (packed_value >> 16u) & 0xffu,
        (packed_value >> 24u) & 0xffu
    );
}

float UnpackSnorm8(uint raw_value)
{
    int signed_value = (raw_value < 128u) ? (int)raw_value : ((int)raw_value - 256);
    return max((float)signed_value / 127.0, -1.0);
}

float4 UnpackSnorm8x4(uint packed_value)
{
    uint4 raw_value = UnpackU8x4(packed_value);
    return float4(
        UnpackSnorm8(raw_value.x),
        UnpackSnorm8(raw_value.y),
        UnpackSnorm8(raw_value.z),
        UnpackSnorm8(raw_value.w)
    );
}

float3 LoadPosition(uint vertex_index)
{
    uint position_index = vertex_index * 3u;
    return float3(
        PrePosition[position_index],
        PrePosition[position_index + 1u],
        PrePosition[position_index + 2u]
    );
}

void StorePosition(uint vertex_index, float3 position)
{
    uint position_index = vertex_index * 3u;
    SkinnedPosition[position_index] = position.x;
    SkinnedPosition[position_index + 1u] = position.y;
    SkinnedPosition[position_index + 2u] = position.z;
}

float3 TransformPoint(uint local_bone, float3 position)
{
    float4 row0 = asfloat(LocalT0[local_bone * 3u + 0u]);
    float4 row1 = asfloat(LocalT0[local_bone * 3u + 1u]);
    float4 row2 = asfloat(LocalT0[local_bone * 3u + 2u]);
    return float3(
        dot(row0.xyz, position) + row0.w,
        dot(row1.xyz, position) + row1.w,
        dot(row2.xyz, position) + row2.w
    );
}

float3 TransformVector(uint local_bone, float3 vector_value)
{
    float4 row0 = asfloat(LocalT0[local_bone * 3u + 0u]);
    float4 row1 = asfloat(LocalT0[local_bone * 3u + 1u]);
    float4 row2 = asfloat(LocalT0[local_bone * 3u + 2u]);
    return float3(
        dot(row0.xyz, vector_value),
        dot(row1.xyz, vector_value),
        dot(row2.xyz, vector_value)
    );
}

[numthreads(64, 1, 1)]
void main(uint3 tid : SV_DispatchThreadID)
{
    uint local_vertex = tid.x;
    uint vertex_start = SkinDispatch.x;
    uint vertex_count = SkinDispatch.y;
    uint output_vertex_start = SkinDispatch.z;
    uint local_bone_count = SkinDispatch.w;
    if (local_vertex >= vertex_count)
    {
        return;
    }

    uint vertex_index = vertex_start + local_vertex;
    uint output_vertex_index = output_vertex_start + local_vertex;
    float3 source_position = LoadPosition(vertex_index);
    uint2 packed_weight_pair = PackedWeights[vertex_index];
    uint4 blend_indices = UnpackU8x4(packed_weight_pair.x);
    uint4 blend_weights_u8 = UnpackU8x4(packed_weight_pair.y);
    float4 blend_weights = float4(blend_weights_u8) / 255.0;

    float weight_sum = blend_weights.x + blend_weights.y + blend_weights.z + blend_weights.w;
    if (weight_sum <= 1e-6)
    {
        StorePosition(output_vertex_index, source_position);
        uint zero_frame_index = output_vertex_index * 2u;
        SkinnedFrame[zero_frame_index] = float4(1.0, 0.0, 0.0, 1.0);
        SkinnedFrame[zero_frame_index + 1u] = float4(0.0, 0.0, -1.0, 1.0);
        return;
    }
    blend_weights /= weight_sum;

    float3 skinned_position = 0.0;
    [unroll]
    for (uint influence = 0u; influence < 4u; influence += 1u)
    {
        if (blend_weights[influence] > 0.0 && blend_indices[influence] < local_bone_count)
        {
            skinned_position += TransformPoint(blend_indices[influence], source_position) * blend_weights[influence];
        }
    }
    StorePosition(output_vertex_index, skinned_position);

    float4 frame0 = UnpackSnorm8x4(PreFrame[vertex_index].x);
    float4 frame1 = UnpackSnorm8x4(PreFrame[vertex_index].y);
    float3 source_tangent = normalize(frame0.xyz);
    float3 source_normal = normalize(-frame1.xyz);
    float bitangent_sign = (frame1.w >= 0.0) ? 1.0 : -1.0;

    float3 skinned_tangent = 0.0;
    float3 skinned_normal = 0.0;
    [unroll]
    for (uint influence_frame = 0u; influence_frame < 4u; influence_frame += 1u)
    {
        if (blend_weights[influence_frame] > 0.0 && blend_indices[influence_frame] < local_bone_count)
        {
            uint bone_index = blend_indices[influence_frame];
            skinned_tangent += TransformVector(bone_index, source_tangent) * blend_weights[influence_frame];
            skinned_normal += TransformVector(bone_index, source_normal) * blend_weights[influence_frame];
        }
    }

    skinned_tangent = normalize(skinned_tangent);
    skinned_normal = normalize(skinned_normal);
    float4 out_frame0 = float4(skinned_tangent, 1.0);
    float4 out_frame1 = float4(-skinned_normal, bitangent_sign);

    uint frame_index = output_vertex_index * 2u;
    SkinnedFrame[frame_index] = out_frame0;
    SkinnedFrame[frame_index + 1u] = out_frame1;
}
