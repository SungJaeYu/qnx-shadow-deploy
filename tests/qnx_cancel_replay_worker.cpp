#include "shadow/io.hpp"
#include "shadow/protocol.hpp"

#include <cerrno>
#include <charconv>
#include <chrono>
#include <climits>
#include <csignal>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <fcntl.h>
#include <iostream>
#include <sstream>
#include <string>
#include <sys/neutrino.h>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>

namespace {

int report_error(const char* operation) {
    std::perror(operation);
    return 1;
}

int write_cancel(const std::string& stem, bool near_deadline) {
    constexpr char command[] = "cancel\n";
    static_assert(sizeof(command) - 1 <= PIPE_BUF, "cancel must fit in one atomic FIFO write");
    std::signal(SIGPIPE, SIG_IGN);
    // The driver connects stdout to its already-open private control FIFO.
    constexpr int fd = STDOUT_FILENO;
    struct stat sink{};
    if (fstat(fd, &sink) == -1) return report_error("cancel writer stat stdout");
    const int flags = fcntl(fd, F_GETFL);
    if (flags == -1) return report_error("cancel writer get stdout flags");
    if (!S_ISFIFO(sink.st_mode) || (flags & O_ACCMODE) != O_WRONLY) {
        std::cerr << "cancel writer: stdout must be a write-only FIFO; mode="
                  << std::oct << sink.st_mode << " access=" << (flags & O_ACCMODE) << '\n';
        return 1;
    }
    if (fcntl(fd, F_SETFL, flags | O_NONBLOCK) == -1) {
        return report_error("cancel writer set stdout nonblocking");
    }

    std::string receipt;
    const auto witness_deadline = shadow::monotonic_ns() + 5000000000ULL;
    while (shadow::monotonic_ns() < witness_deadline) {
        std::ifstream witness(stem + ".witness");
        // A partial line can be visible while the worker is writing the receipt.
        if (std::getline(witness, receipt) && !witness.eof()) break;
        receipt.clear();
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    if (receipt.empty()) {
        std::cerr << "cancel writer: receipt window expired\n";
        return 77;
    }
    const std::string acquired_key = " acquired_ns=";
    const auto marker = receipt.find(acquired_key);
    if (receipt.rfind("received pid=", 0) != 0 || marker == std::string::npos) {
        std::cerr << "cancel writer: malformed receipt\n";
        return 1;
    }
    std::uint64_t acquired_ns = 0;
    const auto* end = receipt.data() + receipt.size();
    const auto parsed = std::from_chars(receipt.data() + marker + acquired_key.size(), end, acquired_ns);
    if (parsed.ec != std::errc{} || parsed.ptr == end || *parsed.ptr != ' ' ||
        acquired_ns == 0 || acquired_ns > UINT64_MAX - 300000000ULL) {
        std::cerr << "cancel writer: malformed acquisition timestamp\n";
        return 1;
    }
    if (near_deadline) {
        const auto target_ns = acquired_ns + 300000000ULL;
        const auto now = shadow::monotonic_ns();
        if (now < target_ns) {
            std::this_thread::sleep_for(std::chrono::nanoseconds(target_ns - now));
        }
    }

    const auto before = shadow::monotonic_ns();
    const auto written = write(fd, command, sizeof(command) - 1);
    const int write_error = errno;
    const auto after = shadow::monotonic_ns();
    errno = write_error;
    if (written == -1) return report_error("cancel writer write FIFO");
    if (written != static_cast<ssize_t>(sizeof(command) - 1)) {
        std::cerr << "cancel writer: incomplete FIFO write\n";
        return 1;
    }
    std::ofstream timestamps(stem + ".write");
    timestamps << "before_ns=" << before << " after_ns=" << after << std::endl;
    if (!timestamps) return report_error("cancel writer save write timestamps");
    std::ofstream release(stem + ".release");
    release << receipt << std::endl;
    if (!release) return report_error("cancel writer save release token");
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc == 4 && std::string(argv[1]) == "--write-cancel") {
        const std::string scenario(argv[3]);
        if (scenario != "live-near" && scenario != "handoff-gated") return 2;
        return write_cancel(argv[2], scenario == "live-near");
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
