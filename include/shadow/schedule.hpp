#pragma once
#include <algorithm>
#include <cstdint>

namespace shadow {
// Never chase the old release grid after an oversleep: doing so can place the
// next acquisition almost immediately after the last and corrupt dt-based work.
inline std::uint64_t next_release_ns(std::uint64_t acquired_ns,
                                     std::uint64_t work_completed_ns,
                                     std::uint64_t period_ns) {
    return std::max(acquired_ns + period_ns, work_completed_ns);
}
} // namespace shadow
