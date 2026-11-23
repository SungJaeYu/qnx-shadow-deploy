#include "shadow/protocol.hpp"
#include "shadow/io.hpp"
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <signal.h>
#include <string>
#include <thread>
#include <unistd.h>
#ifdef __QNXNTO__
#include <cerrno>
#include <sys/neutrino.h>
#endif

int main(int argc, char** argv) {
    using namespace shadow;
    if (argc != 3) { std::cerr << "Usage: shadow-worker MODE FAULT_AFTER\n"; return 2; }
    const std::string mode = argv[1];
    if (mode != "baseline" && mode != "good" && mode != "bad-units" && mode != "delay" &&
        mode != "crash" && mode != "nan" && mode != "stale" && mode != "bad-header" && mode != "offset") {
        std::cerr << "Unknown worker mode\n"; return 2;
    }
    unsigned fault_after = 0;
    try { fault_after = static_cast<unsigned>(std::stoul(argv[2])); }
    catch (...) { std::cerr << "Invalid fault sequence\n"; return 2; }
    signal(SIGPIPE, SIG_IGN);
    Hello hello{};
#ifdef __QNXNTO__
    hello.channel = ChannelCreate(0);
    if (hello.channel == -1) { std::cerr << "ChannelCreate failed\n"; return 1; }
#endif
    if (!write_exact(STDOUT_FILENO, &hello, sizeof(hello))) return 1;
    bool initialized = false;
    double position = 0;
    std::uint64_t previous_ns = 0;
    for (;;) {
        Sample sample{};
#ifdef __QNXNTO__
        struct _msg_info info{};
        const int receive_id = MsgReceive(hello.channel, &sample, sizeof(sample), &info);
        if (receive_id == -1) { if (errno == EINTR) continue; return 1; }
        if (receive_id == 0) continue;
        if (info.msglen != static_cast<decltype(info.msglen)>(sizeof(sample)) ||
            info.srcmsglen != static_cast<decltype(info.srcmsglen)>(sizeof(sample)) ||
            info.dstmsglen != static_cast<decltype(info.dstmsglen)>(sizeof(Estimate)) ||
            !valid_header(sample.header, Kind::sample)) {
            MsgError(receive_id, EINVAL); continue;
        }
#else
        if (!read_exact(STDIN_FILENO, &sample, sizeof(sample))) return 0;
        if (!valid_header(sample.header, Kind::sample)) return 1;
#endif
        if (!std::isfinite(sample.position_m) || (initialized && sample.acquired_ns <= previous_ns)) return 1;
        const bool inject = sample.sequence >= fault_after;
        if (inject && mode == "crash") std::_Exit(42);
        if (inject && mode == "delay") std::this_thread::sleep_for(std::chrono::milliseconds(250));
        const double previous_position = position;
        const double alpha = mode == "baseline" ? 0.25 : 0.5;
        position = initialized ? position + alpha * (sample.position_m - position) : sample.position_m;
        const double dt = initialized ? static_cast<double>(sample.acquired_ns - previous_ns) / 1e9 : 1;
        Estimate estimate{};
        estimate.sequence = sample.sequence; estimate.acquired_ns = sample.acquired_ns;
        estimate.position_m = position;
        estimate.velocity_mps = initialized ? (position - previous_position) / dt : 0;
        if (inject && mode == "bad-units") estimate.position_m *= 1000;
        if (inject && mode == "nan") estimate.position_m = std::numeric_limits<double>::quiet_NaN();
        if (inject && mode == "stale") --estimate.sequence;
        if (inject && mode == "bad-header") ++estimate.header.version;
        if (mode == "offset") estimate.position_m += 50;
        initialized = true; previous_ns = sample.acquired_ns;
#ifdef __QNXNTO__
        // A timed-out caller can make MsgReply fail; the supervisor retires that child.
        if (MsgReply(receive_id, static_cast<int>(sizeof(estimate)), &estimate, sizeof(estimate)) == -1) return 1;
#else
        if (!write_exact(STDOUT_FILENO, &estimate, sizeof(estimate))) return 0;
#endif
    }
}
