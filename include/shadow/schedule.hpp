#pragma once
#include <algorithm>
#include <cstdint>

namespace shadow {
// Keep sensor reads tied to the producer timestamp. A record queued ahead of
// the read must still pass the original acquisition-relative age check.
inline std::uint64_t next_live_read_ns(std::uint64_t acquired_ns,
                                      std::uint64_t work_completed_ns,
                                      std::uint64_t period_ns) {
    return std::max(acquired_ns + period_ns, work_completed_ns);
}

// Use the next slot boundary unless spacing or work completion requires a
// later target. A target inside that slot can still use the rest of the slot.
inline std::uint64_t next_grid_release_ns(std::uint64_t origin_ns,
                                         std::uint64_t acquired_ns,
                                         std::uint64_t work_completed_ns,
                                         std::uint64_t period_ns) {
    const auto spacing_ns = period_ns / 2 + period_ns % 2;
    const auto offset_ns = acquired_ns - origin_ns;
    const auto next_slot_ns = acquired_ns + (period_ns - offset_ns % period_ns);
    return std::max({next_slot_ns, acquired_ns + spacing_ns, work_completed_ns});
}
} // namespace shadow
