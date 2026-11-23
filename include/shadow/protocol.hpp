#pragma once
#include <cstdint>
#include <type_traits>

namespace shadow {
constexpr std::uint32_t magic = 0x53484457;
constexpr std::uint16_t protocol_version = 1;
enum class Kind : std::uint16_t { hello = 1, sample = 2, estimate = 3 };
struct Header {
    std::uint32_t signature = magic;
    std::uint16_t version = protocol_version;
    Kind kind = Kind::sample;
};
struct Sample {
    Header header{};
    std::uint64_t sequence = 0;
    std::uint64_t acquired_ns = 0;
    double position_m = 0;
};
struct Estimate {
    Header header{magic, protocol_version, Kind::estimate};
    std::uint64_t sequence = 0;
    std::uint64_t acquired_ns = 0;
    double position_m = 0;
    double velocity_mps = 0;
};
struct Hello {
    Header header{magic, protocol_version, Kind::hello};
    std::int32_t channel = -1;
    std::uint32_t reserved = 0;
};
inline bool valid_header(const Header& h, Kind kind) {
    return h.signature == magic && h.version == protocol_version && h.kind == kind;
}
static_assert(sizeof(Header) == 8 && sizeof(Sample) == 32);
static_assert(sizeof(Estimate) == 40 && sizeof(Hello) == 16);
static_assert(std::is_trivially_copyable<Sample>::value);
static_assert(std::is_trivially_copyable<Estimate>::value);
// Local IPC only: both peers use the same architecture, ABI and protocol version.
enum class TransportStatus { ok, timeout, disconnected, protocol_error, unavailable };
struct Observation {
    TransportStatus status = TransportStatus::unavailable;
    Estimate estimate{};
    std::uint64_t completed_ns = 0;
};
} // namespace shadow
