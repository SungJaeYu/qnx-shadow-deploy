#pragma once
#include "shadow/protocol.hpp"
#include <future>
#include <memory>
#include <string>
#include <cstdint>

namespace shadow {
// One persistent dispatch thread and at most one outstanding sample per child.
class WorkerProcess {
public:
    WorkerProcess(const std::string& executable, const std::string& mode, unsigned fault_after);
    ~WorkerProcess();
    WorkerProcess(const WorkerProcess&) = delete;
    WorkerProcess& operator=(const WorkerProcess&) = delete;
    std::future<Observation> submit(Sample sample, std::uint64_t absolute_deadline_ns);
    int pid() const;
    static const char* backend();
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace shadow
