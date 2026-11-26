#include "qnx_ipc_fixture.hpp"
#include "shadow/protocol.hpp"

#include <cerrno>
#include <iostream>
#include <sys/neutrino.h>
#include <time.h>

int main(int argc, char** argv) {
    using namespace fixture;
    try {
        if (argc != 3) throw std::runtime_error("expected RUN_DIRECTORY FAULT_AFTER");
        const std::string dir = argv[1];
        Fd commands(dir + "/commands", O_RDONLY | O_NONBLOCK);
        Fd events(dir + "/events", O_WRONLY | O_NONBLOCK);
        auto report = [&](EventKind kind, std::int64_t result, int error, const shadow::Sample& sample) {
            send(events.get(), Event{kind, result, error, shadow::monotonic_ns(),
                                     sample.sequence, sample.acquired_ns});
        };
        shadow::Hello hello;
        hello.channel = ChannelCreate(0);
        if (hello.channel < 0 || !shadow::write_exact(STDOUT_FILENO, &hello, sizeof(hello)))
            throw std::runtime_error("native handshake failed");

        report(EventKind::ready, hello.channel, 0, {});
        for (;;) {
            const char gate = receive<char>(commands.get());
            if (gate != 'R' && gate != 'P') throw std::runtime_error("expected receive or probe");
            shadow::Sample sample{};
            const auto receive_ns = gate == 'P' ? probe_ns : 2 * second_ns;
            if (TimerTimeout(CLOCK_MONOTONIC, _NTO_TIMEOUT_RECEIVE, nullptr, &receive_ns, nullptr) < 0)
                throw std::runtime_error("receive timer failed");
            const auto rcvid = MsgReceive(hello.channel, &sample, sizeof(sample), nullptr);
            const int receive_error = rcvid < 0 ? errno : 0;
            if (rcvid < 0 && gate == 'P' && receive_error == ETIMEDOUT) {
                report(EventKind::receive_timeout, rcvid, receive_error, {});
                continue;
            }
            if (rcvid <= 0 || !shadow::valid_header(sample.header, shadow::Kind::sample)) {
                report(EventKind::receive_error, rcvid, receive_error, sample);
                throw std::runtime_error("unexpected native receive");
            }
            report(EventKind::sample, rcvid, 0, sample);
            if (receive<char>(commands.get()) != 'Y') throw std::runtime_error("expected reply");
            shadow::Estimate estimate;
            estimate.sequence = sample.sequence;
            estimate.acquired_ns = sample.acquired_ns;
            estimate.position_m = sample.position_m;
            const int result = MsgReply(rcvid, sizeof(estimate), &estimate, sizeof(estimate));
            const int reply_error = result < 0 ? errno : 0;
            report(EventKind::reply, result, reply_error, sample);
        }
    } catch (const std::exception& error) {
        std::cerr << "receiver: " << error.what() << '\n';
        return 1;
    }
}
