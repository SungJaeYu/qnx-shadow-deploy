#include "shadow/protocol.hpp"

#include <algorithm>
#include <array>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <string>
#include <sys/netmgr.h>
#include <sys/neutrino.h>
#include <thread>
#include <time.h>
#include <unistd.h>
#include <vector>

namespace {
using namespace shadow;

struct Case {
    std::string name;
    long status = estimate_reply_status;
    std::size_t length = sizeof(EstimateReply);
    std::array<unsigned char, sizeof(NativeEstimateReply) + 8> bytes{};
    bool valid = false;
};

void require(bool condition, const std::string& message) {
    if (!condition) {
        std::cerr << "FAIL " << message << '\n';
        std::exit(1);
    }
}
} // namespace

int main() {
    Estimate estimate;
    estimate.sequence = 17;
    estimate.acquired_ns = 20000000;
    estimate.position_m = 1.5;
    const EstimateReply canonical{estimate, estimate_reply_footer};
    Case normal{"canonical"};
    std::memcpy(normal.bytes.data(), &canonical, sizeof(canonical));
    normal.valid = true;
    std::vector<Case> cases{normal};
    for (std::size_t length = 0; length < sizeof(canonical); ++length) {
        Case short_reply = normal;
        short_reply.name = "short/" + std::to_string(length);
        short_reply.length = length;
        short_reply.valid = false;
        cases.push_back(short_reply);
    }
    cases.push_back(normal);
    for (std::size_t length = sizeof(canonical) + 1; length <= normal.bytes.size(); ++length) {
        Case oversized = normal;
        oversized.name = "oversized/" + std::to_string(length);
        oversized.length = length;
        std::fill(oversized.bytes.begin() + sizeof(canonical), oversized.bytes.end(), 0x5a);
        oversized.valid = false;
        cases.push_back(oversized);
    }
    for (const long status : {0L, estimate_reply_status - 1, estimate_reply_status + 1}) {
        Case wrong_status = normal;
        wrong_status.name = "status/" + std::to_string(status);
        wrong_status.status = status;
        wrong_status.valid = false;
        cases.push_back(wrong_status);
    }
    if (std::numeric_limits<long>::digits > 32) {
        Case wide_status = normal;
        wide_status.name = "wide-status";
        wide_status.status = static_cast<long>((1ULL << 32) + estimate_reply_status);
        wide_status.valid = false;
        cases.push_back(wide_status);
    }
    for (const auto offset : {offsetof(Header, signature), offsetof(Header, version),
                              offsetof(Header, kind), offsetof(EstimateReply, footer)}) {
        Case malformed = normal;
        malformed.name = "invalid-field/" + std::to_string(offset);
        malformed.bytes[offset] ^= 0xff;
        malformed.valid = false;
        cases.push_back(malformed);
    }
    cases.push_back(normal);
    cases.push_back(cases[1]);  // Empty directly after a successful full reply.
    cases.push_back(normal);

    const int channel = ChannelCreate(0);
    require(channel >= 0, "ChannelCreate");
    const int connection = ConnectAttach(ND_LOCAL_NODE, getpid(), channel, _NTO_SIDE_CHANNEL, 0);
    require(connection >= 0, "ConnectAttach");
    std::thread receiver([&] {
        for (std::size_t index = 0; index < cases.size(); ++index) {
            std::size_t request = cases.size();
            const auto rcvid = MsgReceive(channel, &request, sizeof(request), nullptr);
            require(rcvid > 0 && request == index, "loopback request");
            const auto& reply = cases[index];
            require(MsgReply(rcvid, reply.status, reply.bytes.data(), reply.length) == 0,
                    reply.name + " MsgReply");
        }
    });

    unsigned accepted = 0;
    for (std::size_t index = 0; index < cases.size(); ++index) {
        const auto& reply = cases[index];
        NativeEstimateReply receive{};
        const NativeEstimateReply initial{};
        const std::uint64_t timeout_ns = 2000000000ULL;
        if (TimerTimeout(CLOCK_MONOTONIC, _NTO_TIMEOUT_SEND | _NTO_TIMEOUT_REPLY,
                         nullptr, &timeout_ns, nullptr) == -1)
            require(false, "TimerTimeout");
        const long status = MsgSend(connection, &index, sizeof(index), &receive, sizeof(receive));
        require(status == reply.status, reply.name + " MsgSend status");
        const auto copied = std::min(reply.length, sizeof(receive));
        const auto* actual = reinterpret_cast<const unsigned char*>(&receive);
        const auto* untouched = reinterpret_cast<const unsigned char*>(&initial);
        require(std::equal(actual, actual + copied, reply.bytes.begin()), reply.name + " copied prefix");
        require(std::equal(actual + copied, actual + sizeof(receive), untouched + copied),
                reply.name + " untouched suffix");
        const bool valid = valid_native_estimate_reply(receive, status);
        require(valid == reply.valid, reply.name + " v2 decoder verdict");
        if (valid) ++accepted;
    }
    receiver.join();
    require(ConnectDetach(connection) == 0, "ConnectDetach");
    require(ChannelDestroy(channel) == 0, "ChannelDestroy");
    std::cout << "PASS native reply conformance exchanges=" << cases.size()
              << " canonical=" << accepted << " rejected=" << cases.size() - accepted << '\n';
}
