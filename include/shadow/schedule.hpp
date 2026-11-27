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

// Round up to the first grid boundary at or after both end of work and the
// half-period guard. The guard limits short estimation intervals after an
// oversleep while letting ordinary wake-up lateness decay on the next slot.
inline std::uint64_t next_grid_release_ns(std::uint64_t origin_ns,
                                          std::uint64_t acquired_ns,
                                          std::uint64_t work_completed_ns,
                                          std::uint64_t period_ns) {
    const auto spacing_ns = period_ns / 2 + period_ns % 2;
    const auto earliest_ns = std::max(acquired_ns + spacing_ns, work_completed_ns);
    const auto offset_ns = earliest_ns - origin_ns;
    const auto remainder_ns = offset_ns % period_ns;
    return remainder_ns ? earliest_ns + period_ns - remainder_ns : earliest_ns;
}
} // namespace shadow
