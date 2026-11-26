#include "shadow/io.hpp"
#include "shadow/protocol.hpp"

#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <sys/neutrino.h>
#include <thread>
#include <unistd.h>

namespace {

int write_cancel(const std::string& stem, std::uint64_t acquired_ns) {
    std::ifstream witness(stem + ".witness");
    std::string receipt;
    if (!std::getline(witness, receipt) || receipt.empty()) return 2;
    const auto target_ns = acquired_ns ? acquired_ns + 300000000ULL : 0;
    const auto now = shadow::monotonic_ns();
    if (now < target_ns) {
        std::this_thread::sleep_for(std::chrono::nanoseconds(target_ns - now));
    }
    const auto before = shadow::monotonic_ns();
    constexpr char command[] = "cancel\n";
    if (!shadow::write_exact(3, command, sizeof(command) - 1)) return 1;
    const auto after = shadow::monotonic_ns();
    std::cout << "before_ns=" << before << " after_ns=" << after << std::endl;
    std::ofstream release(stem + ".release");
    release << receipt << std::endl;
    return release ? 0 : 1;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc == 4 && std::string(argv[1]) == "--write-cancel") {
        return write_cancel(argv[2], std::stoull(argv[3]));
    }
    const auto* directory = std::getenv("SHADOW_QNX_REPLAY_GATE_DIR");
    if (!directory) return 2;
    const auto* mode = std::getenv("SHADOW_QNX_CANCEL_CASE");
    const std::string scenario = mode ? mode : "replay-gated";
    const std::string stem = std::string(directory) + "/" + scenario;
    const auto release_path = stem + ".release";
    std::ofstream witness(stem + ".witness");
    if (!witness) return 2;

    shadow::Hello hello{};
    hello.channel = ChannelCreate(0);
    if (hello.channel == -1 || !shadow::write_exact(STDOUT_FILENO, &hello, sizeof(hello))) return 1;
    unsigned received_samples = 0;
    for (;;) {
        shadow::Sample sample{};
        struct _msg_info info{};
        const int rcvid = MsgReceive(hello.channel, &sample, sizeof(sample), &info);
        if (rcvid == -1 && errno == EINTR) continue;
        if (rcvid < 0) return 1;
        if (rcvid == 0) continue;
        if (info.msglen != static_cast<decltype(info.msglen)>(sizeof(sample)) ||
            info.srcmsglen != static_cast<decltype(info.srcmsglen)>(sizeof(sample)) ||
            info.dstmsglen != static_cast<decltype(info.dstmsglen)>(sizeof(shadow::NativeEstimateReply)) ||
            !shadow::valid_header(sample.header, shadow::Kind::sample)) {
            MsgError(rcvid, EINVAL);
            return 1;
        }

        const auto received = shadow::monotonic_ns();
        ++received_samples;
        const bool gated = (scenario == "replay-gated" && received_samples == 1) ||
                           (scenario == "handoff-gated" && received_samples == 17) ||
                           (scenario == "live-near" && sample.sequence == 22);
        if (gated) {
            std::ostringstream receipt;
            receipt << "received pid=" << getpid() << " sender_pid=" << info.pid
                    << " sequence=" << sample.sequence
                    << " acquired_ns=" << sample.acquired_ns << " rcvid=" << rcvid
                    << " time_ns=" << received;
            const auto receipt_line = receipt.str();
            witness << receipt_line << std::endl;
            const auto gate_deadline = scenario == "live-near"
                ? sample.acquired_ns + 337500000ULL : received + 300000000ULL;
            for (;;) {
                if (shadow::monotonic_ns() >= gate_deadline) {
                    std::cerr << "cancel fixture reply gate expired\n";
                    return 1;
                }
                std::ifstream gate(release_path);
                std::string token;
                if (std::getline(gate, token) && token == receipt_line) break;
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            }
            witness << "released sequence=" << sample.sequence << " rcvid=" << rcvid
                    << " time_ns=" << shadow::monotonic_ns() << std::endl;
        } else if (scenario == "replay-gated") {
            // Keep preparation active across the next broker input boundary.
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
        shadow::Estimate estimate{};
        estimate.sequence = sample.sequence;
        estimate.acquired_ns = sample.acquired_ns;
        estimate.position_m = sample.position_m;
        const shadow::EstimateReply reply{estimate, shadow::estimate_reply_footer};
        const int result = MsgReply(rcvid, shadow::estimate_reply_status, &reply, sizeof(reply));
        if (gated) {
            witness << "replied sequence=" << sample.sequence << " rcvid=" << rcvid
                    << " time_ns=" << shadow::monotonic_ns() << " result=" << result << std::endl;
        }
        if (result == -1) return 1;
    }
}
