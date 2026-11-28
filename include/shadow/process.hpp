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
    std::uint64_t direct_start_ns = 0;
    std::uint64_t ipc_start_ns = 0;
    std::uint64_t ipc_end_ns = 0;
    std::uint64_t pre_expiry_ns = 0;
    std::uint64_t completed_ns = 0;
};
// Opt-in policy for candidate processes on the measured x86-64 guest.
enum class WorkerLaunch { standard, qnx_guest_candidate };

void validate_qnx_guest_profile();
#if defined(__QNXNTO__) && defined(__x86_64__)
inline constexpr char qnx_candidate_exec_option[] = "--internal-qnx-candidate-exec";
[[noreturn]] void exec_qnx_guest_candidate(char** worker_args);
#endif

// One persistent dispatch thread and at most one outstanding sample per child.
class WorkerProcess {
public:
    WorkerProcess(const std::string& executable, const std::string& mode, unsigned fault_after,
                  WorkerLaunch launch = WorkerLaunch::standard);
    ~WorkerProcess();
    WorkerProcess(const WorkerProcess&) = delete;
    WorkerProcess& operator=(const WorkerProcess&) = delete;
    std::future<Observation> submit(Sample sample, std::uint64_t absolute_deadline_ns,
                                    WorkerExchangeTiming* timing = nullptr);
#ifdef __QNXNTO__
    // A side-channel connection belongs to the process and can be used by the broker thread.
    Observation exchange_direct(const Sample& sample, std::uint64_t absolute_deadline_ns,
                                WorkerExchangeTiming* timing = nullptr);
#endif
    int pid() const;
    static const char* backend();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace shadow
