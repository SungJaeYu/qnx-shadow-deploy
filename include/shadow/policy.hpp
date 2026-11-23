#pragma once
#include "shadow/protocol.hpp"
#include <optional>
#include <string>

namespace shadow {
struct Contract {
    std::uint64_t deadline_ns = 10000000;
    double max_position_m = 1000;
    double max_velocity_mps = 100;
    double max_step_m = 5;
    unsigned qualification_samples = 20;
    unsigned probation_samples = 40;
};
enum class Phase { shadowing, probation, committed, rejected, rolled_back, failed };
enum class Source { none, baseline, candidate };
const char* name(Phase phase);
const char* name(Source source);
struct Decision {
    Phase phase = Phase::shadowing;
    Source source = Source::none;
    std::optional<Estimate> output;
    std::string event;
    std::string reason;
    std::string baseline_check;
    std::string candidate_check;
};
class Supervisor {
public:
    explicit Supervisor(Contract contract);
    Decision step(const Sample& sample, const Observation& baseline, const Observation& candidate);
    Decision stop_output(const std::string& reason);
    Phase phase() const { return phase_; }
    bool needs_baseline() const { return phase_ != Phase::committed && phase_ != Phase::failed; }
    bool needs_candidate() const {
        return phase_ == Phase::shadowing || phase_ == Phase::probation || phase_ == Phase::committed;
    }
private:
    std::string check(const Sample&, const Observation&, const std::optional<Estimate>&) const;
    Contract contract_;
    Phase phase_ = Phase::shadowing;
    unsigned qualified_ = 0;
    unsigned probation_ = 0;
    std::uint64_t last_sequence_ = 0;
    std::uint64_t last_acquired_ns_ = 0;
    std::optional<Estimate> last_baseline_, last_candidate_, last_output_;
};
} // namespace shadow
