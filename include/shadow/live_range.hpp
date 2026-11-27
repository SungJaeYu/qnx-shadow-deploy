#pragma once
#include "shadow/protocol.hpp"
#include <cstdint>

namespace shadow {
class LiveRangeInput {
public:
    explicit LiveRangeInput(int fd);
    ~LiveRangeInput();
    LiveRangeInput(const LiveRangeInput&) = delete;
    LiveRangeInput& operator=(const LiveRangeInput&) = delete;

    // Returns a stable diagnostic on failure. The caller must stop the run.
    const char* receive(std::uint64_t sequence, std::uint64_t wait_deadline_ns,
                        std::uint64_t ipc_budget_ns, Sample& sample);

private:
    int fd_;
    std::uint64_t last_acquired_ns_ = 0;
};
} // namespace shadow
