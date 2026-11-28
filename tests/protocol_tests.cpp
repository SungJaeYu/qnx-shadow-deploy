#include "shadow/protocol.hpp"
#include "shadow/io.hpp"
#include "shadow/process.hpp"

#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <unistd.h>

namespace {
void require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(1);
    }
}

bool accepts_native_bytes(const void* bytes, std::size_t size, long status) {
    shadow::NativeEstimateReply receive{};
    std::memcpy(&receive, bytes, std::min(size, sizeof(receive)));
    return shadow::valid_native_estimate_reply(receive, status);
}
void verify_spawn_with_closed_standard_fds(const char* executable) {
    for (const unsigned mask : {1U, 2U, 4U, 7U}) {
        int saved[3]{};
        for (int fd = STDIN_FILENO; fd <= STDERR_FILENO; ++fd) {
            saved[fd] = dup(fd);
            require(saved[fd] >= 0, "save standard descriptor");
        }
        for (int fd = STDIN_FILENO; fd <= STDERR_FILENO; ++fd) {
            if (mask & (1U << fd)) close(fd);
        }
        std::string error;
        try {
            shadow::WorkerProcess worker(executable, "baseline", 0);
            shadow::Sample sample{};
            sample.sequence = 1;
            sample.acquired_ns = shadow::monotonic_ns();
            sample.position_m = 10;
            const auto result = worker.submit(sample, sample.acquired_ns + 500000000ULL).get();
            if (result.status != shadow::TransportStatus::ok || result.estimate.sequence != sample.sequence) {
                error = "worker exchange failed";
            }
        } catch (const std::exception& failure) {
            error = failure.what();
        }
        for (int fd = STDIN_FILENO; fd <= STDERR_FILENO; ++fd) {
            require(dup2(saved[fd], fd) == fd, "restore standard descriptor");
            close(saved[fd]);
        }
        require(error.empty(), error.c_str());
    }
}
} // namespace

int main(int argc, char** argv) {
    if (argc == 2) verify_spawn_with_closed_standard_fds(argv[1]);
    using namespace shadow;
    Estimate estimate{};
    estimate.sequence = 3;
    estimate.acquired_ns = 20000000;
    const EstimateReply reply{estimate, estimate_reply_footer};
    require(valid_estimate_reply(reply), "canonical envelope");
    require(accepts_native_bytes(&reply, sizeof(reply), estimate_reply_status), "canonical native reply");

    for (std::size_t size = 0; size < sizeof(reply); ++size) {
        require(!accepts_native_bytes(&reply, size, estimate_reply_status), "truncated native reply");
    }
    require(accepts_native_bytes(&reply, sizeof(reply), estimate_reply_status), "full reply after truncation");
    require(!accepts_native_bytes(&reply, 0, estimate_reply_status), "empty reply after success");

    for (long status : {-1L, 0L, estimate_reply_status - 1, estimate_reply_status + 1}) {
        require(!accepts_native_bytes(&reply, sizeof(reply), status), "wrong status with full envelope");
    }
    if (std::numeric_limits<long>::digits > 32) {
        constexpr auto wider_status = static_cast<long>((1ULL << 32) + estimate_reply_status);
        require(!accepts_native_bytes(&reply, sizeof(reply), wider_status), "status with matching low 32 bits");
    }

    EstimateReply invalid = reply;
    invalid.estimate.header.signature = 0;
    require(!accepts_native_bytes(&invalid, sizeof(invalid), estimate_reply_status), "invalid signature");
    invalid = reply;
    --invalid.estimate.header.version;
    require(!accepts_native_bytes(&invalid, sizeof(invalid), estimate_reply_status), "old protocol version");
    invalid = reply;
    invalid.estimate.header.kind = Kind::sample;
    require(!accepts_native_bytes(&invalid, sizeof(invalid), estimate_reply_status), "incorrect kind");
    invalid = reply;
    invalid.footer.front() = 0;
    require(!valid_estimate_reply(invalid), "invalid completion footer");

    const NativeEstimateReply extended{reply, {0x01}};
    require(!accepts_native_bytes(&extended, sizeof(reply) + 1, estimate_reply_status), "changed guard byte");
    std::array<unsigned char, sizeof(NativeEstimateReply) + 1> capped{};
    std::memcpy(capped.data(), &reply, sizeof(reply));
    capped[sizeof(NativeEstimateReply) - 1] = 0x01;
    require(!accepts_native_bytes(capped.data(), capped.size(), estimate_reply_status), "changed final guard byte");
    const NativeEstimateReply unchanged_guard{reply, {}};
    require(accepts_native_bytes(&unchanged_guard, sizeof(unchanged_guard), estimate_reply_status),
            "equal guard bytes are indistinguishable from untouched storage");
}
