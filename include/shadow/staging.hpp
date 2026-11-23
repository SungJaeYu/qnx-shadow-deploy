#pragma once
#include "shadow/policy.hpp"
#include "shadow/process.hpp"
#include <memory>
#include <string>
#include <vector>

namespace shadow {
constexpr std::size_t history_capacity = 64;
struct CandidateManifest {
    std::string artifact, executable, mode;
    unsigned replay_samples = 0, fault_after = 0;
};
CandidateManifest read_manifest(const std::string& path);
const char* native_architecture();
const char* native_os();

struct StagingStatus {
    bool done = false;
    int pid = -1;
    unsigned replayed = 0;
    std::uint64_t through_sequence = 0;
    std::string artifact, error;
};
struct PreparedCandidate {
    std::unique_ptr<WorkerProcess> worker;
    Estimate last_replayed;
};
// All file IO, startup, replay exchanges, and cancellation cleanup run off the broker.
// Only one candidate can be staging; the bounded backlog never drops input silently.
class CandidateStager {
public:
    CandidateStager(std::string manifest, std::vector<Sample> history, Contract contract);
    ~CandidateStager();
    CandidateStager(const CandidateStager&) = delete;
    CandidateStager& operator=(const CandidateStager&) = delete;
    void enqueue(const Sample& sample);
    void cancel();
    StagingStatus status() const;
    PreparedCandidate take_if_ready(std::uint64_t through_sequence);
private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace shadow
