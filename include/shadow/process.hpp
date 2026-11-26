#pragma once
#include "shadow/protocol.hpp"
#include <future>
#include <memory>
#include <string>
#include <cstdint>

namespace shadow {
// The caller keeps these timestamps alive until the future is ready or the worker is joined.
struct WorkerExchangeTiming {
    std::uint64_t submit_ns = 0;
    std::uint64_t dispatch_ns = 0;
    std::uint64_t ipc_start_ns = 0;
    std::uint64_t ipc_end_ns = 0;
    std::uint64_t pre_expiry_ns = 0;
    std::uint64_t completed_ns = 0;
};
// One persistent dispatch thread and at most one outstanding sample per child.
class WorkerProcess {
public:
    WorkerProcess(const std::string& executable, const std::string& mode, unsigned fault_after);
    ~WorkerProcess();
    WorkerProcess(const WorkerProcess&) = delete;
    WorkerProcess& operator=(const WorkerProcess&) = delete;
    std::future<Observation> submit(Sample sample, std::uint64_t absolute_deadline_ns,
                                    WorkerExchangeTiming* timing = nullptr);
    int pid() const;
    static const char* backend();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace shadow
