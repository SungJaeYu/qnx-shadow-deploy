#include "shadow/policy.hpp"
#include <cmath>
#include <stdexcept>

namespace shadow {
const char* name(Phase p) {
    switch (p) {
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
Supervisor::Supervisor(Contract c) : contract_(c) {
    if (!c.deadline_ns || !c.qualification_samples || !c.probation_samples ||
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
std::string Supervisor::check(const Sample& s, const Observation& o,
                              const std::optional<Estimate>& previous) const {
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
    if (o.completed_ns < s.acquired_ns || o.completed_ns - s.acquired_ns > contract_.deadline_ns)
        return "deadline_exceeded";
    if (!std::isfinite(e.position_m) || !std::isfinite(e.velocity_mps)) return "non_finite_output";
    if (std::abs(e.position_m) > contract_.max_position_m) return "position_out_of_range";
    if (std::abs(e.velocity_mps) > contract_.max_velocity_mps) return "velocity_out_of_range";
    if (previous && std::abs(e.position_m - previous->position_m) > contract_.max_step_m)
        return "position_step_exceeded";
    return "ok";
}
Decision Supervisor::step(const Sample& s, const Observation& b, const Observation& c) {
    Decision d;
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
    last_sequence_ = s.sequence;
    last_acquired_ns_ = s.acquired_ns;
    d.baseline_check = needs_baseline() ? check(s, b, last_baseline_) : "not_running";
    d.candidate_check = needs_candidate() ? check(s, c, last_candidate_) : "not_running";
    const bool baseline_ok = d.baseline_check == "ok";
    const bool candidate_ok = d.candidate_check == "ok";
    if (baseline_ok) last_baseline_ = b.estimate;
    if (candidate_ok) last_candidate_ = c.estimate;

    if (phase_ == Phase::shadowing) {
        if (!baseline_ok) { fail("baseline_" + d.baseline_check); return finish(); }
        if (!candidate_ok) {
            phase_ = Phase::rejected; d.event = "candidate_rejected"; d.reason = d.candidate_check;
        } else if (qualified_ >= contract_.qualification_samples) {
            // Qualified on the previous sample. Switch only at this sample boundary.
            if (last_output_ && std::abs(c.estimate.position_m - last_output_->position_m) > contract_.max_step_m) {
                phase_ = Phase::rejected; d.event = "candidate_rejected"; d.reason = "switch_discontinuity";
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
