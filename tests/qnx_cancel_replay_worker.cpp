#include "shadow/io.hpp"
#include "shadow/protocol.hpp"

#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <string>
#include <sys/neutrino.h>
#include <thread>
#include <unistd.h>

int main() {
    const auto* directory = std::getenv("SHADOW_QNX_REPLAY_GATE_DIR");
    if (!directory) return 2;
    const std::string stem(directory);
    const auto release_path = stem + "/replay-gated.release";
    std::ofstream witness(stem + "/replay-gated.witness");
    if (!witness) return 2;

    shadow::Hello hello{};
    hello.channel = ChannelCreate(0);
    if (hello.channel == -1 || !shadow::write_exact(STDOUT_FILENO, &hello, sizeof(hello))) return 1;
    bool first = true;
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
        if (first) {
            witness << "received pid=" << getpid() << " sequence=" << sample.sequence
                    << " acquired_ns=" << sample.acquired_ns << " rcvid=" << rcvid
                    << " time_ns=" << received << std::endl;
            const auto gate_deadline = received + 300000000ULL;
            for (;;) {
                if (shadow::monotonic_ns() >= gate_deadline) {
                    std::cerr << "replay reply gate expired\n";
                    return 1;
                }
                if (access(release_path.c_str(), F_OK) == 0) break;
                if (errno != ENOENT) return 1;
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            }
            witness << "released sequence=" << sample.sequence << " rcvid=" << rcvid
                    << " time_ns=" << shadow::monotonic_ns() << std::endl;
        } else {
            // Keep preparation active across the next broker input boundary.
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
        shadow::Estimate estimate{};
        estimate.sequence = sample.sequence;
        estimate.acquired_ns = sample.acquired_ns;
        estimate.position_m = sample.position_m;
        const shadow::EstimateReply reply{estimate, shadow::estimate_reply_footer};
        const int result = MsgReply(rcvid, shadow::estimate_reply_status, &reply, sizeof(reply));
        if (first) {
            witness << "replied sequence=" << sample.sequence << " rcvid=" << rcvid
                    << " time_ns=" << shadow::monotonic_ns() << " result=" << result << std::endl;
            first = false;
        }
        if (result == -1) return 1;
    }
}
