#include "shadow/guest_headroom.hpp"
#include <charconv>
#include <optional>

namespace shadow {
GuestHeadroom classify_guest_headroom(std::string_view counters, std::uint64_t page_size) {
    if (counters.empty() || counters.back() != '\n' || page_size == 0)
        return GuestHeadroom::invalid;

    std::optional<std::uint64_t> physical_free;
    std::optional<std::uint64_t> unreserved;
    while (!counters.empty()) {
        const auto end = counters.find('\n');
        const auto line = counters.substr(0, end);
        counters.remove_prefix(end + 1);
        const auto equals = line.find('=');
        if (equals == std::string_view::npos || equals == 0)
            return GuestHeadroom::invalid;

        const auto key = line.substr(0, equals);
        if (key != "pages_free" && key != "vmem_avail")
            continue;

        auto& counter = key == "pages_free" ? physical_free : unreserved;
        const auto value = line.substr(equals + 1);
        if (counter || value.substr(0, 2) != "0x")
            return GuestHeadroom::invalid;
        std::uint64_t pages = 0;
        const auto parsed = std::from_chars(value.data() + 2, value.data() + value.size(), pages, 16);
        if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size())
            return GuestHeadroom::invalid;
        counter = pages;
    }
    if (!physical_free || !unreserved)
        return GuestHeadroom::invalid;

    // 16 MiB for the candidate and 16 MiB of spare capacity for the incumbent.
    constexpr std::uint64_t required_bytes = 32 * 1024 * 1024;
    const auto required_pages = required_bytes / page_size + (required_bytes % page_size != 0);
    return *physical_free >= required_pages && *unreserved >= required_pages
        ? GuestHeadroom::sufficient : GuestHeadroom::insufficient;
}
} // namespace shadow
