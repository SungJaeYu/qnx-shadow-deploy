#include "shadow/policy.hpp"
#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace shadow {
namespace {
constexpr double demo_motion_bound_mps = 2.0;

double motion_allowance_m(std::uint64_t interval_ns) {
    return demo_motion_bound_mps * (interval_ns / 1e9);
}

bool has_rollback_margin(const Estimate& baseline, const Estimate& candidate,
                         std::uint64_t interval_ns, const Contract& contract) {
    return std::abs(candidate.position_m - baseline.position_m) + motion_allowance_m(interval_ns)
        <= contract.max_step_m;
}
} // namespace

bool PositionResidualMonitor::accepts(const Sample& sample, const Estimate& estimate,
                                      std::uint64_t interval_ns) {
    // Alpha 0.25 is the slowest supported demo filter. The allowance decays
    // like that filter after a long acquisition interval.
    constexpr double lag_retention = 0.75;
    constexpr double residual_slack_m = 1.0;
    constexpr unsigned excess_limit = 5;
    const double movement_m = motion_allowance_m(interval_ns);
    filter_lag_allowance_m_ = lag_retention *
        (filter_lag_allowance_m_.value_or(3.0 * movement_m) + movement_m);
    const double residual_m = std::abs(estimate.position_m - sample.position_m);
    if (residual_m <= residual_slack_m + *filter_lag_allowance_m_) {
        consecutive_excesses_ = 0;
    } else if (consecutive_excesses_ < excess_limit) {
        ++consecutive_excesses_;
    }
    return consecutive_excesses_ < excess_limit;
}
const char* name(Phase p) {
    switch (p) {
    case Phase::idle: return "idle";
    case Phase::warming: return "warming";
    case Phase::shadowing: return "shadowing";
    case Phase::probation: return "probation";
    case Phase::committed: return "committed";
    case Phase::rejected: return "rejected";
    case Phase::rolled_back: return "rolled_back";
    case Phase::failed: return "failed";
    }
    return "failed";
}
const char* name(Source s) {
    switch (s) {
    case Source::baseline: return "baseline";
    case Source::candidate: return "candidate";
    case Source::none: return "none";
    }
    return "none";
}
Supervisor::Supervisor(Contract c, bool candidate_started) : contract_(c),
    phase_(candidate_started ? Phase::shadowing : Phase::idle) {
    if (!c.deadline_ns || !c.period_ns || !c.qualification_samples || !c.probation_samples ||
        !std::isfinite(c.max_position_m) || c.max_position_m <= 0 ||
        !std::isfinite(c.max_velocity_mps) || c.max_velocity_mps <= 0 ||
        !std::isfinite(c.max_step_m) || c.max_step_m <= 0)
        throw std::invalid_argument("Contract limits and sample counts must be positive and finite");
}
Decision Supervisor::stop_output(const std::string& reason) {
    phase_ = Phase::failed;
    Decision decision; decision.phase = phase_; decision.event = "output_stopped"; decision.reason = reason;
    return decision;
}
bool Supervisor::begin_staging() {
    if (phase_ != Phase::idle && phase_ != Phase::rejected && phase_ != Phase::rolled_back) return false;
    phase_ = Phase::warming; qualified_ = 0; probation_ = 0;
    last_candidate_.reset();
    candidate_residual_ = {};
    cancel_requested_ = false;
    pending_event_ = "candidate_staged"; pending_reason_ = "state_preparation_started";
    return true;
}
void Supervisor::begin_shadowing(const Estimate& last_replayed) {
    if (phase_ != Phase::warming) throw std::logic_error("Candidate is not warming");
    phase_ = Phase::shadowing; last_candidate_ = last_replayed;
    pending_event_ = "candidate_ready"; pending_reason_ = "replay_caught_up";
}
void Supervisor::reject_staging(const std::string& reason) {
    if (phase_ != Phase::warming) throw std::logic_error("Candidate is not warming");
    phase_ = Phase::rejected;
    pending_event_ = "candidate_rejected"; pending_reason_ = reason;
}
bool Supervisor::request_cancel() {
    if (phase_ != Phase::warming && phase_ != Phase::shadowing && phase_ != Phase::probation) return false;
    cancel_requested_ = true; return true;
}
std::string check_observation(const Sample& s, const Observation& o,
                              const std::optional<Estimate>& previous, const Contract& contract,
                              std::uint64_t receipt_origin_ns) {
    switch (o.status) {
    case TransportStatus::timeout: return "deadline_exceeded";
    case TransportStatus::disconnected: return "worker_disconnected";
    case TransportStatus::protocol_error: return "protocol_mismatch";
    case TransportStatus::unavailable: return "unavailable";
    case TransportStatus::ok: break;
    }
    const auto& e = o.estimate;
    if (!valid_header(e.header, Kind::estimate)) return "protocol_mismatch";
    if (e.sequence != s.sequence || e.acquired_ns != s.acquired_ns) return "stale_or_mismatched_output";
    if (o.completed_ns < receipt_origin_ns || o.completed_ns - receipt_origin_ns > contract.deadline_ns)
        return "deadline_exceeded";
    if (!std::isfinite(e.position_m) || !std::isfinite(e.velocity_mps)) return "non_finite_output";
    if (std::abs(e.position_m) > contract.max_position_m) return "position_out_of_range";
    if (std::abs(e.velocity_mps) > contract.max_velocity_mps) return "velocity_out_of_range";
    if (previous && std::abs(e.position_m - previous->position_m) > contract.max_step_m)
        return "position_step_exceeded";
    return "ok";
}
Decision Supervisor::step(const Sample& s, const Observation& b, const Observation& c) {
    Decision d;
    d.event = std::move(pending_event_); pending_event_.clear();
    d.reason = std::move(pending_reason_); pending_reason_.clear();
    auto finish = [&]() { d.phase = phase_; return d; };
    auto fail = [&](const std::string& why) {
        phase_ = Phase::failed; d.source = Source::none; d.output.reset();
        d.event = "output_stopped"; d.reason = why;
    };
    if (phase_ == Phase::failed) return finish();
    if (!valid_header(s.header, Kind::sample) || !std::isfinite(s.position_m) ||
        s.sequence <= last_sequence_ || s.acquired_ns <= last_acquired_ns_) {
        fail("invalid_or_non_monotonic_input"); return finish();
    }
    const auto sample_interval_ns = last_acquired_ns_ ?
        std::max(contract_.period_ns, s.acquired_ns - last_acquired_ns_) : contract_.period_ns;
    last_sequence_ = s.sequence;
    last_acquired_ns_ = s.acquired_ns;
    d.baseline_check = needs_baseline() ? check_observation(s, b, last_baseline_, contract_, s.acquired_ns) : "not_running";
    d.candidate_check = needs_candidate() ? check_observation(s, c, last_candidate_, contract_, s.acquired_ns) : "not_running";
    if (d.baseline_check == "ok" && !baseline_residual_.accepts(s, b.estimate, sample_interval_ns))
        d.baseline_check = "measurement_residual_exceeded";
    if (d.candidate_check == "ok" && !candidate_residual_.accepts(s, c.estimate, sample_interval_ns))
        d.candidate_check = "measurement_residual_exceeded";
    const bool baseline_ok = d.baseline_check == "ok";
    const bool candidate_ok = d.candidate_check == "ok";
    if (baseline_ok) last_baseline_ = b.estimate;
    if (candidate_ok) last_candidate_ = c.estimate;

    if (cancel_requested_) {
        cancel_requested_ = false;
        phase_ = phase_ == Phase::probation ? Phase::rolled_back : Phase::rejected;
        d.event = "candidate_cancelled"; d.reason = "operator_cancelled";
    }

    if (phase_ == Phase::shadowing) {
        if (!baseline_ok) { fail("baseline_" + d.baseline_check); return finish(); }
        if (!candidate_ok) {
            phase_ = Phase::rejected; d.event = "candidate_rejected"; d.reason = d.candidate_check;
        } else if (qualified_ >= contract_.qualification_samples) {
            // Qualified on the previous sample. Switch only at this sample boundary.
            if (last_output_ && std::abs(c.estimate.position_m - last_output_->position_m) > contract_.max_step_m) {
                phase_ = Phase::rejected; d.event = "candidate_rejected"; d.reason = "switch_discontinuity";
            } else if (!has_rollback_margin(b.estimate, c.estimate, sample_interval_ns, contract_)) {
                phase_ = Phase::rejected; d.event = "candidate_rejected"; d.reason = "rollback_margin_exceeded";
            } else {
                phase_ = Phase::probation; d.event = "candidate_promoted"; d.reason = "qualification_passed";
            }
        } else {
            ++qualified_;
        }
    }
    if (phase_ == Phase::probation) {
        if (!candidate_ok) {
            if (!baseline_ok) { fail("no_valid_fallback"); return finish(); }
            phase_ = Phase::rolled_back; d.event = "candidate_rolled_back"; d.reason = d.candidate_check;
        } else if (!baseline_ok) {
            // Do not silently claim fallback protection after losing the standby.
            fail("standby_" + d.baseline_check); return finish();
        } else if (!has_rollback_margin(b.estimate, c.estimate, sample_interval_ns, contract_)) {
            phase_ = Phase::rolled_back; d.event = "candidate_rolled_back"; d.reason = "rollback_margin_exceeded";
        } else if (++probation_ >= contract_.probation_samples) {
            phase_ = Phase::committed; d.event = "candidate_committed"; d.reason = "probation_passed";
        }
    }
    if (phase_ == Phase::committed) {
        if (!candidate_ok) { fail("candidate_" + d.candidate_check); return finish(); }
        d.source = Source::candidate; d.output = c.estimate;
    } else if (phase_ == Phase::probation) {
        d.source = Source::candidate; d.output = c.estimate;
    } else {
        if (!baseline_ok) { fail("baseline_" + d.baseline_check); return finish(); }
        d.source = Source::baseline; d.output = b.estimate;
    }
    // Check continuity across promotion AND rollback, not just within each worker.
    if (last_output_ && std::abs(d.output->position_m - last_output_->position_m) > contract_.max_step_m) {
        fail("output_discontinuity"); return finish();
    }
    last_output_ = d.output;
    return finish();
}
} // namespace shadow
