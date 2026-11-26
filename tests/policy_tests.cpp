#include "shadow/continuity.hpp"
#include "shadow/policy.hpp"
#include "shadow/schedule.hpp"
#include <functional>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <utility>
#include <vector>

using namespace shadow;
#define CHECK(expr) do { if (!(expr)) throw std::runtime_error(#expr); } while (false)
Sample sample(std::uint64_t seq) { Sample s; s.sequence = seq; s.acquired_ns = seq * 20000000; s.position_m = 10; return s; }
Observation ok(const Sample& s, double position = 10) {
    Observation o; o.status = TransportStatus::ok; o.estimate.sequence = s.sequence;
    o.estimate.acquired_ns = s.acquired_ns; o.estimate.position_m = position;
    o.completed_ns = s.acquired_ns + 1000000; return o;
}
Contract quick() { Contract c; c.qualification_samples = 2; c.probation_samples = 3; return c; }
Decision tick(Supervisor& g, unsigned i) { auto s = sample(i); return g.step(s, ok(s), ok(s)); }
int main() {
    const std::vector<std::pair<std::string, std::function<void()>>> tests{
        {"pending candidate neither qualifies nor promotes", [] {
            Supervisor g(quick());
            CHECK(tick(g, 1).phase == Phase::shadowing);
            for (const auto sequence : {2u, 4u}) {
                const auto s = sample(sequence);
                auto d = g.step(s, ok(s), {}, true);
                CHECK(d.phase == Phase::shadowing && d.source == Source::baseline);
                CHECK(d.candidate_check == "pending");
                CHECK(g.finish_pending_candidate(s, ok(s), d));
                CHECK(d.phase == Phase::shadowing && d.candidate_check == "ok");
                if (sequence == 2) CHECK(tick(g, 3).phase == Phase::shadowing);
            }
            CHECK(tick(g, 5).event == "candidate_promoted");
        }},
        {"unsafe drained candidate rejects without withdrawing baseline", [] {
            Supervisor g(quick());
            const auto s = sample(1);
            auto d = g.step(s, ok(s), {}, true);
            CHECK(g.finish_pending_candidate(s, ok(s, std::numeric_limits<double>::quiet_NaN()), d));
            CHECK(d.phase == Phase::rejected && d.event == "candidate_rejected");
            CHECK(d.reason == "non_finite_output" && d.source == Source::baseline && d.output);
            CHECK(tick(g, 2).source == Source::baseline);
        }},
        {"cancelled shadow drains without changing the cancellation decision", [] {
            Supervisor g(quick());
            CHECK(g.request_cancel());
            const auto s = sample(1);
            auto d = g.step(s, ok(s), {}, true);
            CHECK(!g.finish_pending_candidate(s, ok(s, std::numeric_limits<double>::quiet_NaN()), d));
            CHECK(d.phase == Phase::rejected && d.event == "candidate_cancelled");
            CHECK(d.reason == "operator_cancelled" && d.source == Source::baseline && d.output);
            CHECK(d.candidate_check == "drained_without_validation");
            CHECK(!g.needs_candidate() && tick(g, 2).source == Source::baseline);
        }},
        {"preparation cannot qualify or restart the baseline", [] {
            Supervisor g(quick(), false);
            CHECK(tick(g, 1).phase == Phase::idle); CHECK(g.begin_staging());
            for (unsigned i = 2; i <= 10; ++i) {
                auto d = tick(g, i); CHECK(d.phase == Phase::warming); CHECK(d.source == Source::baseline);
            }
            g.begin_shadowing(ok(sample(10)).estimate);
            CHECK(tick(g, 11).phase == Phase::shadowing);
            CHECK(tick(g, 12).phase == Phase::shadowing);
            CHECK(tick(g, 13).event == "candidate_promoted");
        }},
        {"replay to live continuity is checked", [] {
            Supervisor g(quick(), false); tick(g, 1); CHECK(g.begin_staging());
            g.begin_shadowing(ok(sample(1), 20).estimate);
            auto d = tick(g, 2); CHECK(d.phase == Phase::rejected);
            CHECK(d.reason == "position_step_exceeded"); CHECK(d.source == Source::baseline);
        }},
        {"retry clears candidate qualification but preserves output continuity", [] {
            Supervisor g(quick(), false); tick(g, 1); CHECK(g.begin_staging());
            g.reject_staging("invalid_manifest"); CHECK(tick(g, 2).source == Source::baseline);
            CHECK(g.begin_staging()); g.begin_shadowing(ok(sample(2), 60).estimate);
            for (unsigned i = 3; i <= 4; ++i) {
                auto s = sample(i); CHECK(g.step(s, ok(s), ok(s, 60)).source == Source::baseline);
            }
            auto s = sample(5); auto d = g.step(s, ok(s), ok(s, 60));
            CHECK(d.phase == Phase::rejected); CHECK(d.reason == "switch_discontinuity");
        }},
        {"operator cancellation still validates the current fallback", [] {
            Supervisor g(quick()); tick(g, 1); tick(g, 2); tick(g, 3);
            CHECK(g.request_cancel()); auto d = tick(g, 4);
            CHECK(d.phase == Phase::rolled_back); CHECK(d.output->sequence == 4);
            CHECK(d.event == "candidate_cancelled"); CHECK(g.begin_staging());
            CHECK(g.request_cancel()); CHECK(tick(g, 5).phase == Phase::rejected);
            Supervisor h(quick()); tick(h, 1); tick(h, 2); tick(h, 3);
            CHECK(h.request_cancel()); auto s = sample(4);
            CHECK(!h.step(s, {}, ok(s)).output); CHECK(h.phase() == Phase::failed);
        }},
        {"commit prevents staging and cancellation without a fallback", [] {
            Supervisor g(quick()); for (unsigned i = 1; i <= 5; ++i) tick(g, i);
            CHECK(!g.begin_staging()); CHECK(!g.request_cancel());
        }},
        {"late acquisition cannot cause a catch-up burst", [] {
            const std::uint64_t acquired = 329732000, completed = 329900000, period = 30000000;
            CHECK(next_release_ns(acquired, completed, period) == acquired + period);
            CHECK(next_release_ns(acquired, completed, period) - acquired >= period);
        }},
        {"processing overrun does not create an input backlog", [] {
            CHECK(next_release_ns(100000000, 180000000, 30000000) == 180000000);
            CHECK(next_release_ns(180000000, 181000000, 30000000) == 210000000);
        }},
        {"continuity uses fixed slots and separates the early-stop tail", [] {
            ServiceContinuity continuity(100, 10);
            CHECK(!continuity.max_acquisition_interval_ns);
            const auto first = continuity.acquire(113, 100);
            CHECK(first.nominal_slot == 1);
            CHECK(first.skipped_slots == 0);
            CHECK(first.nominal_lateness_ns == 3);
            CHECK(first.scheduling_lateness_ns == 13);
            CHECK(continuity.initial_unoccupied_slots == 1);
            continuity.decide(114, true, false);
            const auto second = continuity.acquire(140, 123);
            CHECK(second.nominal_slot == 4);
            CHECK(second.skipped_slots == 2);
            CHECK(second.acquisition_interval_ns == 27);
            CHECK(second.nominal_grid_lateness_ns == 30);
            continuity.decide(149, false, true);
            continuity.acquire(151, 150);
            continuity.decide(152, true, false);
            CHECK(continuity.skipped_slots == 2);
            CHECK(continuity.max_acquisition_interval_ns == 27);
            CHECK(continuity.max_selected_interval_ns == 38);
            CHECK(continuity.selected_span_ns == 38);
            CHECK(continuity.selected == 2);
            CHECK(continuity.acquired == 3);
            CHECK(continuity.decision_deadline_misses == 1);
        }},
        {"promotion starts at the next sequence boundary", [] {
            Supervisor g(quick());
            CHECK(tick(g, 1).source == Source::baseline);
            CHECK(tick(g, 2).phase == Phase::shadowing);
            auto d = tick(g, 3); CHECK(d.source == Source::candidate); CHECK(d.event == "candidate_promoted");
            CHECK(d.output->sequence == 3);
            CHECK(tick(g, 4).phase == Phase::probation);
            CHECK(tick(g, 5).phase == Phase::committed); CHECK(!g.needs_baseline());
        }},
        {"bad candidate never gains authority", [] {
            Supervisor g(quick()); auto s = sample(1); auto c = ok(s, 10001);
            auto d = g.step(s, ok(s), c); CHECK(d.phase == Phase::rejected);
            CHECK(d.source == Source::baseline); CHECK(d.reason == "position_out_of_range");
            CHECK(tick(g, 2).phase == Phase::rejected);
        }},
        {"rollback uses current baseline sample", [] {
            Supervisor g(quick()); for (unsigned i = 1; i <= 3; ++i) tick(g, i);
            auto s = sample(4); Observation dead; dead.status = TransportStatus::disconnected;
            auto d = g.step(s, ok(s, 10.2), dead); CHECK(d.phase == Phase::rolled_back);
            CHECK(d.output->sequence == 4); CHECK(d.source == Source::baseline);
        }},
        {"both workers failing stops output", [] {
            Supervisor g(quick()); for (unsigned i = 1; i <= 3; ++i) tick(g, i);
            auto d = g.step(sample(4), {}, {}); CHECK(d.phase == Phase::failed); CHECK(!d.output);
        }},
        {"loss of standby during probation stops output", [] {
            Supervisor g(quick()); for (unsigned i = 1; i <= 3; ++i) tick(g, i);
            auto s = sample(4); auto d = g.step(s, {}, ok(s)); CHECK(d.phase == Phase::failed); CHECK(!d.output);
        }},
        {"unqualified candidate cannot replace failed baseline", [] {
            Supervisor g(quick()); auto s = sample(1); auto d = g.step(s, {}, ok(s));
            CHECK(d.phase == Phase::failed); CHECK(d.source == Source::none);
        }},
        {"no fallback is promised after commit", [] {
            Supervisor g(quick()); for (unsigned i = 1; i <= 5; ++i) tick(g, i);
            auto s = sample(6); auto d = g.step(s, ok(s), {}); CHECK(d.phase == Phase::failed); CHECK(!d.output);
        }},
        {"late and mismatched responses cannot qualify", [] {
            for (int fault = 0; fault < 6; ++fault) {
                Supervisor g(quick()); auto s = sample(1); auto c = ok(s);
                if (fault == 0) c.completed_ns = s.acquired_ns + quick().deadline_ns + 1;
                if (fault == 1) c.estimate.sequence = 2;
                if (fault == 2) --c.estimate.acquired_ns;
                if (fault == 3) ++c.estimate.header.version;
                if (fault == 4) c.estimate.position_m = std::numeric_limits<double>::quiet_NaN();
                if (fault == 5) c.estimate.velocity_mps = std::numeric_limits<double>::infinity();
                CHECK(g.step(s, ok(s), c).phase == Phase::rejected);
            }
        }},
        {"deadline equality passes, future sequence does not", [] {
            Supervisor g(quick()); auto s = sample(1); auto c = ok(s);
            c.completed_ns = s.acquired_ns + quick().deadline_ns;
            CHECK(g.step(s, ok(s), c).phase == Phase::shadowing);
        }},
        {"output equality is not required", [] {
            Supervisor g(quick());
            for (unsigned i = 1; i <= 3; ++i) { auto s = sample(i); g.step(s, ok(s, 10), ok(s, 10.5)); }
            CHECK(g.phase() == Phase::probation);
        }},
        {"individually valid outputs may still be unsafe to switch", [] {
            Supervisor g(quick()); Decision d;
            for (unsigned i = 1; i <= 3; ++i) { auto s = sample(i); d = g.step(s, ok(s, 10), ok(s, 60)); }
            CHECK(d.phase == Phase::rejected); CHECK(d.reason == "switch_discontinuity");
        }},
        {"probation rolls back before candidate drift strands fallback", [] {
            Supervisor g(quick()); tick(g, 1); tick(g, 2);
            auto third = sample(3);
            CHECK(g.step(third, ok(third), ok(third, 14)).phase == Phase::probation);
            auto fourth = sample(4); auto d = g.step(fourth, ok(fourth), ok(fourth, 18));
            CHECK(d.event == "candidate_rolled_back");
            CHECK(d.reason == "rollback_margin_exceeded");
            CHECK(d.phase == Phase::rolled_back); CHECK(d.source == Source::baseline);
            CHECK(d.output && d.output->sequence == 4 && d.output->position_m == 10);
        }},
        {"promotion reserves room for baseline motion", [] {
            Supervisor g(quick()); tick(g, 1);
            auto second = sample(2); g.step(second, ok(second, 9.99), ok(second, 14));
            auto third = sample(3);
            auto d = g.step(third, ok(third, 9.98), ok(third, 14.97));
            CHECK(d.event == "candidate_rejected"); CHECK(d.reason == "rollback_margin_exceeded");
            CHECK(d.phase == Phase::rejected); CHECK(d.source == Source::baseline);
        }},
        {"last probation sample still preserves fallback", [] {
            Supervisor g(quick()); tick(g, 1); tick(g, 2); tick(g, 3); tick(g, 4);
            auto fifth = sample(5); auto d = g.step(fifth, ok(fifth), ok(fifth, 5));
            CHECK(d.phase == Phase::rolled_back); CHECK(d.source == Source::baseline);
            CHECK(d.reason == "rollback_margin_exceeded");
        }},
        {"rollback continuity is checked", [] {
            Supervisor g(quick()); tick(g, 1); tick(g, 2);
            auto s3 = sample(3); g.step(s3, ok(s3, 10), ok(s3, 14));
            auto s4 = sample(4); auto d = g.step(s4, ok(s4, 6), {});
            CHECK(d.phase == Phase::failed); CHECK(d.reason == "output_discontinuity"); CHECK(!d.output);
        }},
        {"sustained live residual rejects a biased shadow candidate", [] {
            auto contract = quick(); contract.qualification_samples = 10;
            Supervisor g(contract);
            for (unsigned i = 1; i <= 4; ++i) {
                auto s = sample(i);
                CHECK(g.step(s, ok(s), ok(s, 13)).phase == Phase::shadowing);
            }
            auto s = sample(5); const auto d = g.step(s, ok(s), ok(s, 13));
            CHECK(d.phase == Phase::rejected); CHECK(d.source == Source::baseline);
            CHECK(d.candidate_check == "measurement_residual_exceeded");
        }},
        {"semantic candidate failure rolls back after promotion", [] {
            auto contract = quick(); contract.probation_samples = 10;
            Supervisor g(contract); tick(g, 1); tick(g, 2); tick(g, 3);
            for (unsigned i = 4; i <= 7; ++i) {
                auto s = sample(i);
                CHECK(g.step(s, ok(s), ok(s, 13)).phase == Phase::probation);
            }
            auto s = sample(8); const auto d = g.step(s, ok(s), ok(s, 13));
            CHECK(d.phase == Phase::rolled_back); CHECK(d.source == Source::baseline);
            CHECK(d.reason == "measurement_residual_exceeded");
        }},
        {"semantic failure after commit stops publication", [] {
            Supervisor g(quick());
            for (unsigned i = 1; i <= 5; ++i) tick(g, i);
            for (unsigned i = 6; i <= 9; ++i) {
                auto s = sample(i);
                CHECK(g.step(s, {}, ok(s, 13)).phase == Phase::committed);
            }
            auto s = sample(10); const auto d = g.step(s, {}, ok(s, 13));
            CHECK(d.phase == Phase::failed); CHECK(!d.output);
            CHECK(d.reason == "candidate_measurement_residual_exceeded");
        }},
        {"biased standby is unavailable for fallback", [] {
            auto contract = quick(); contract.probation_samples = 10;
            Supervisor g(contract); tick(g, 1); tick(g, 2); tick(g, 3);
            for (unsigned i = 4; i <= 7; ++i) {
                auto s = sample(i);
                CHECK(g.step(s, ok(s, 13), ok(s)).phase == Phase::probation);
            }
            auto s = sample(8); const auto d = g.step(s, ok(s, 13), {});
            CHECK(d.phase == Phase::failed); CHECK(!d.output);
            CHECK(d.reason == "no_valid_fallback");
            CHECK(d.baseline_check == "measurement_residual_exceeded");
        }},
        {"baseline residual alone can invalidate authority", [] {
            Supervisor g(quick(), false);
            for (unsigned i = 1; i <= 4; ++i) {
                auto s = sample(i);
                CHECK(g.step(s, ok(s, 13), {}).source == Source::baseline);
            }
            auto s = sample(5); const auto d = g.step(s, ok(s, 13), {});
            CHECK(d.phase == Phase::failed); CHECK(!d.output);
            CHECK(d.reason == "baseline_measurement_residual_exceeded");
        }},
        {"transient residuals clear and candidate generation starts fresh", [] {
            auto contract = quick(); contract.qualification_samples = 10;
            Supervisor g(contract);
            for (unsigned i = 1; i <= 4; ++i) {
                auto s = sample(i); CHECK(g.step(s, ok(s), ok(s, 13)).phase == Phase::shadowing);
            }
            CHECK(tick(g, 5).phase == Phase::shadowing);
            auto sixth = sample(6);
            CHECK(g.step(sixth, ok(sixth), ok(sixth, 13)).phase == Phase::shadowing);
            CHECK(g.request_cancel());
            auto seventh = sample(7);
            CHECK(g.step(seventh, ok(seventh), ok(seventh, 13)).phase == Phase::rejected);
            CHECK(g.begin_staging());
            g.begin_shadowing(ok(seventh, 13).estimate);
            for (unsigned i = 8; i <= 11; ++i) {
                auto s = sample(i); CHECK(g.step(s, ok(s), ok(s, 13)).phase == Phase::shadowing);
            }
            CHECK(tick(g, 12).phase == Phase::shadowing);
        }},
        {"duplicate and invalid inputs stop the broker", [] {
            Supervisor g(quick()); tick(g, 1);
            auto d = tick(g, 1); CHECK(d.phase == Phase::failed); CHECK(!d.output);
            CHECK(!tick(g, 2).output);
            Supervisor h(quick()); auto s = sample(1); s.position_m = std::numeric_limits<double>::infinity();
            CHECK(h.step(s, ok(s), ok(s)).phase == Phase::failed);
        }},
        {"invalid configuration is rejected", [] {
            auto c = quick(); c.qualification_samples = 0; bool thrown = false;
            try { Supervisor g(c); } catch (const std::invalid_argument&) { thrown = true; }
            CHECK(thrown);
        }},
        {"a missed publication decision deadline is terminal", [] {
            Supervisor g(quick()); tick(g, 1);
            CHECK(!g.stop_output("decision_deadline_exceeded").output);
            CHECK(g.phase() == Phase::failed); CHECK(!tick(g, 2).output);
            CHECK(!g.needs_baseline()); CHECK(!g.needs_candidate());
        }},
        {"rate and velocity constraints apply before authority", [] {
            Supervisor g(quick()); tick(g, 1); auto s = sample(2);
            CHECK(g.step(s, ok(s), ok(s, 16)).reason == "position_step_exceeded");
            Supervisor h(quick()); auto c = ok(sample(1)); c.estimate.velocity_mps = 101;
            CHECK(h.step(sample(1), ok(sample(1)), c).reason == "velocity_out_of_range");
        }}
    };
    unsigned failures = 0;
    for (const auto& test : tests) {
        try { test.second(); std::cout << "PASS " << test.first << '\n'; }
        catch (const std::exception& e) { ++failures; std::cerr << "FAIL " << test.first << ": " << e.what() << '\n'; }
    }
    return failures ? 1 : 0;
}
