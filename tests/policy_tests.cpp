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
        {"rollback continuity is checked", [] {
            Supervisor g(quick()); tick(g, 1); tick(g, 2);
            auto s3 = sample(3); g.step(s3, ok(s3, 10), ok(s3, 14));
            auto s4 = sample(4); auto d = g.step(s4, ok(s4, 6), {});
            CHECK(d.phase == Phase::failed); CHECK(d.reason == "output_discontinuity"); CHECK(!d.output);
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
