#pragma once

#include <cstdint>
#include <string_view>

namespace shadow {
enum class GuestHeadroom { sufficient, insufficient, invalid };

GuestHeadroom classify_guest_headroom(std::string_view counters, std::uint64_t page_size);
} // namespace shadow
