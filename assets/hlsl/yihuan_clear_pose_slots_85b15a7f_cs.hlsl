// Clear per-frame pose-slot state for 85b15a7f.
//
// u0 = PoseState UAV

RWBuffer<uint> PoseState : register(u0);

[numthreads(64, 1, 1)]
void main(uint3 tid : SV_DispatchThreadID)
{
    uint count = 0u;
    PoseState.GetDimensions(count);
    if (tid.x >= count)
    {
        return;
    }

    PoseState[tid.x] = 0u;
}
