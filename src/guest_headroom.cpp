#include "shadow/guest_headroom.hpp"
#include <charconv>
#include <optional>

namespace shadow {
namespace {
bool valid_size_annotation(std::string_view suffix) {
    if (suffix.empty())
        return true;
    if (suffix.size() < 5 || suffix.substr(0, 2) != " (" || suffix.back() != ')')
        return false;

    const auto size = suffix.substr(2, suffix.size() - 3);
    constexpr std::string_view digits = "0123456789";
    auto unit_start = size.find_first_not_of(digits);
    if (unit_start == 0 || unit_start == std::string_view::npos)
        return false;
    if (size[unit_start] == '.') {
        const auto fraction_start = unit_start + 1;
        unit_start = size.find_first_not_of(digits, fraction_start);
        if (unit_start == fraction_start || unit_start == std::string_view::npos)
            return false;
    }
    const auto unit = size.substr(unit_start);
    return unit == "B" || unit == "KB" || unit == "MB" || unit == "GB" || unit == "TB" || unit == "PB";
}
} // namespace

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
        if (parsed.ec != std::errc{})
            return GuestHeadroom::invalid;
        // QNX may append a display size such as " (32.000MB)" to the page count.
        const auto consumed = static_cast<std::size_t>(parsed.ptr - value.data());
        if (!valid_size_annotation(value.substr(consumed)))
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
