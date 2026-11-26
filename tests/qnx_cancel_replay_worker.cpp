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
#include <devctl.h>
#include <fstream>
#include <fcntl.h>
#include <iostream>
#include <sstream>
#include <string>
#include <sys/neutrino.h>
#include <sys/procfs.h>
#include <sys/states.h>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>

namespace {

int report_error(const char* operation) {
    std::perror(operation);
    return 1;
}

int prepare_control_fifo() {
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

    return 0;
}

int write_control(const std::string& command, std::uint64_t& before, std::uint64_t& after) {
    if (command.size() > PIPE_BUF) {
        std::cerr << "cancel writer: command exceeds atomic FIFO write limit\n";
        return 1;
    }
    before = shadow::monotonic_ns();
    const auto written = write(STDOUT_FILENO, command.data(), command.size());
    const int write_error = errno;
    after = shadow::monotonic_ns();
    errno = write_error;
    if (written == -1) return report_error("cancel writer write FIFO");
    if (written != static_cast<ssize_t>(command.size())) {
        std::cerr << "cancel writer: incomplete FIFO write\n";
        return 1;
    }
    return 0;
}

int write_cancel(const std::string& stem, bool near_deadline) {
    if (prepare_control_fifo()) return 1;
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

    std::uint64_t before{}, after{};
    if (write_control("cancel\n", before, after)) return 1;
    std::ofstream timestamps(stem + ".write");
    timestamps << "before_ns=" << before << " after_ns=" << after << std::endl;
    if (!timestamps) return report_error("cancel writer save write timestamps");
    std::ofstream release(stem + ".release");
    release << receipt << std::endl;
    if (!release) return report_error("cancel writer save release token");
    return 0;
}

struct ReplySnapshot {
    using Tid = decltype(procfs_status{}.tid);
    procfs_status status{};
    std::uint64_t start_ns{};
    std::uint64_t end_ns{};

    int read(int fd, Tid tid) {
        status = {};
        status.tid = tid;
        start_ns = shadow::monotonic_ns();
        const auto result = devctl(fd, DCMD_PROC_TIDSTATUS, &status, sizeof(status), nullptr);
        end_ns = shadow::monotonic_ns();
        return result;
    }

    bool matches(pid_t broker, pid_t peer, Tid tid) const {
        return status.pid == broker && status.tid == tid && tid > 1 && status.state == STATE_REPLY &&
               status.blocked.connect.pid == peer && status.blocked.connect.chid >= 0 &&
               status.blocked.connect.coid >= 0;
    }

    void record(std::ostream& output, const char* label) const {
        const auto& endpoint = status.blocked.connect;
        output << label << " pid=" << status.pid << " tid=" << status.tid
               << " thread_start_ns=" << status.start_time << " peer=" << endpoint.pid
               << " chid=" << endpoint.chid << " coid=" << endpoint.coid
               << " start_ns=" << start_ns << " end_ns=" << end_ns
               << " blocked_ns=" << status.nsec_since_block << '\n';
    }
};

int stock_replay_cancel(const std::string& stem, pid_t broker, pid_t baseline) {
    if (broker <= 1 || baseline <= 1 || broker == baseline || prepare_control_fifo()) return 1;
    const std::string broker_path = "/proc/" + std::to_string(broker) + "/ctl";
    const int fd = open(broker_path.c_str(), O_RDONLY);
    if (fd == -1) return report_error("replay observer open broker procfs");

    // Sample 16 provides enough history. Target its next 500 ms input boundary,
    // allowing 30 ms for the command to reach the FIFO before the next poll.
    // The precise proof uses the actual stage and snapshot timestamps.
    const auto sample_deadline = shadow::monotonic_ns() + 2000000000ULL;
    std::uint64_t target_ns{};
    while (shadow::monotonic_ns() < sample_deadline && !target_ns) {
        std::ifstream trace(stem + ".jsonl");
        for (std::string line; std::getline(trace, line);) {
            if (trace.eof() || line.rfind("{\"type\":\"sample\",\"sequence\":16,", 0) != 0) continue;
            const std::string key = "\"decision_latency_ms\":";
            const auto marker = line.find(key);
            if (marker == std::string::npos) break;
            const double latency_ms = std::strtod(line.c_str() + marker + key.size(), nullptr);
            if (latency_ms >= 0 && latency_ms < 450) {
                target_ns = shadow::monotonic_ns() + 470000000ULL -
                            static_cast<std::uint64_t>(latency_ms * 1000000);
            }
            break;
        }
        if (!target_ns) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    if (!target_ns) {
        std::cerr << "replay observer: missing complete acquisition sample 16\n";
        return 1;
    }
    const auto now = shadow::monotonic_ns();
    if (now < target_ns) {
        std::this_thread::sleep_for(std::chrono::nanoseconds(target_ns - now));
    }
    std::uint64_t stage_before{}, stage_after{};
    if (write_control("stage " + stem + ".manifest\n", stage_before, stage_after)) return 1;

    // This demo has only a baseline dispatch thread, stager and candidate dispatch thread
    // in addition to its main thread. Allow for non-contiguous thread IDs.
    const auto deadline = stage_before + 750000000ULL;
    ReplySnapshot pre;
    while (shadow::monotonic_ns() < deadline) {
        for (ReplySnapshot::Tid tid = 2; tid <= 16; ++tid) {
            const int result = pre.read(fd, tid);
            if (result == ESRCH) continue;
            if (result != EOK) {
                std::cerr << "replay observer: before-status error=" << result << '\n';
                return 1;
            }
            if (pre.status.state != STATE_REPLY) continue;
            const auto peer = pre.status.blocked.connect.pid;
            if (peer <= 1 || peer == baseline || peer == broker || !pre.matches(broker, peer, tid)) continue;

            std::uint64_t before{}, after{};
            if (write_control("cancel\n", before, after)) return 1;
            ReplySnapshot post;
            const int post_result = post.read(fd, tid);
            if (post_result != EOK && post_result != ESRCH) {
                std::cerr << "replay observer: after-status error=" << post_result << '\n';
                return 1;
            }
            const std::string peer_path = "/proc/" + std::to_string(peer) + "/ctl";
            const int peer_fd = open(peer_path.c_str(), O_RDONLY);
            if (peer_fd == -1) return report_error("replay observer open candidate procfs");
            procfs_info info{};
            const int owner = devctl(peer_fd, DCMD_PROC_INFO, &info, sizeof(info), nullptr);
            close(peer_fd);
            if (owner != EOK || info.pid != peer || info.parent != broker) {
                std::cerr << "replay observer: candidate ownership mismatch or procfs error=" << owner << '\n';
                return 1;
            }
            std::ofstream evidence(stem + ".exact");
            evidence << "candidate parent=" << info.parent << " pid=" << info.pid
                     << " start_ns=" << info.start_time << '\n';
            evidence << "stage before_ns=" << stage_before << " after_ns=" << stage_after << '\n';
            pre.record(evidence, "pre_reply");
            evidence << "write before_ns=" << before << " after_ns=" << after << '\n';
            if (post_result == EOK && post.matches(broker, peer, tid)) {
                post.record(evidence, "post_reply");
            } else {
                evidence << "post_unobserved\n";
            }
            evidence.flush();
            return evidence ? 0 : report_error("replay observer save evidence");
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    std::cerr << "replay observer: no matching pre-write REPLY snapshot\n";
    return 77;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc == 5 && std::string(argv[1]) == "--stock-replay-cancel") {
        return stock_replay_cancel(argv[2], std::stoi(argv[3]), std::stoi(argv[4]));
    }
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
