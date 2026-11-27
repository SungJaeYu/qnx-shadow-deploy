#include "shadow/live_range.hpp"
#include "shadow/io.hpp"
#include <charconv>
#include <fcntl.h>
#include <stdexcept>
#include <string_view>
#include <sys/stat.h>
#include <unistd.h>

namespace shadow {
namespace {
template<class Integer>
bool parse_integer(std::string_view text, Integer& value) {
    if (text.empty()) return false;
    const auto* end = text.data() + text.size();
    const auto parsed = std::from_chars(text.data(), end, value);
    return parsed.ec == std::errc{} && parsed.ptr == end;
}
} // namespace

LiveRangeInput::LiveRangeInput(int fd) : fd_(fd) {
    struct stat info{};
    const auto flags = ::fcntl(fd_, F_GETFL);
    const auto descriptor_flags = ::fcntl(fd_, F_GETFD);
    if (fd_ <= STDERR_FILENO || ::fstat(fd_, &info) == -1 || !S_ISFIFO(info.st_mode) ||
        flags == -1 || (flags & O_ACCMODE) != O_RDONLY || descriptor_flags == -1 ||
        ::fcntl(fd_, F_SETFD, descriptor_flags | FD_CLOEXEC) == -1)
        throw std::invalid_argument("--live-range-fd requires a dedicated read-only pipe or FIFO above 2");
    nonblocking(fd_);
}

LiveRangeInput::~LiveRangeInput() { ::close(fd_); }

const char* LiveRangeInput::receive(std::uint64_t sequence, std::uint64_t wait_deadline_ns,
                                    std::uint64_t ipc_budget_ns, Sample& sample) {
    char line[64];
    std::size_t length = 0;
    for (; length < sizeof(line); ++length) {
        const auto result = read_until(fd_, line + length, 1, wait_deadline_ns);
        if (result == IoResult::timeout) return length ? "live_range_partial_timeout" : "live_range_missing";
        if (result != IoResult::ok) return length ? "live_range_truncated" : "live_range_disconnected";
        if (line[length] == '\n') break;
    }
    if (length == sizeof(line)) return "live_range_oversize";
    std::string_view fields(line, length);
    if (fields.substr(0, 3) != "mm ") return "live_range_invalid_units";
    fields.remove_prefix(3);
    const auto first = fields.find(' ');
    if (first == std::string_view::npos || !parse_integer(fields.substr(0, first), sample.sequence))
        return "live_range_invalid_record";
    fields.remove_prefix(first + 1);
    const auto second = fields.find(' ');
    unsigned range_mm = 0;
    if (second == std::string_view::npos || !parse_integer(fields.substr(0, second), sample.acquired_ns) ||
        !parse_integer(fields.substr(second + 1), range_mm))
        return "live_range_invalid_record";
    if (range_mm > 1'000'000) return "live_range_out_of_range";
    if (sample.sequence != sequence) return "live_range_sequence";
    if (sample.acquired_ns <= last_acquired_ns_) return "live_range_timestamp_order";
    const auto now = monotonic_ns();
    if (sample.acquired_ns > now) return "live_range_future";
    if (now - sample.acquired_ns >= ipc_budget_ns) return "live_range_stale";
    sample.position_m = range_mm / 1000.0;
    last_acquired_ns_ = sample.acquired_ns;
    return nullptr;
}
} // namespace shadow
