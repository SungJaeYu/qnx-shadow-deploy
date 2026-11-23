#include "shadow/io.hpp"
#include "shadow/policy.hpp"
#include "shadow/process.hpp"
#include <chrono>
#include <cmath>
#include <csignal>
#include <iomanip>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>

namespace {
volatile std::sig_atomic_t interrupted = 0;
void stop(int) { interrupted = 1; }
unsigned number(const std::string& text) {
    std::size_t used = 0;
    const auto value = std::stoul(text, &used);
    if (used != text.size() || text.empty() || text[0] == '-' || value > 1000000)
        throw std::invalid_argument("Invalid numeric argument");
    return static_cast<unsigned>(value);
}
void value(double x) {
    if (std::isfinite(x)) std::cout << x;
    else std::cout << "null";
}
void observation(const shadow::Observation& o, const shadow::Sample& sample) {
    if (o.status == shadow::TransportStatus::unavailable) { std::cout << "null"; return; }
    std::cout << "{\"position\":";
    if (o.status == shadow::TransportStatus::ok) value(o.estimate.position_m);
    else std::cout << "null";
    std::cout << ",\"latency_ms\":";
    value(o.completed_ns >= sample.acquired_ns ? static_cast<double>(o.completed_ns - sample.acquired_ns) / 1e6 : 0);
    std::cout << "}";
}
} // namespace
int main(int argc, char** argv) {
    using namespace shadow;
    try {
        std::string worker = "./build/shadow-worker", scenario = "rollback";
        unsigned samples = 180, period_ms = 20, deadline_ms = 10, qualify = 20, probation = 40;
        for (int i = 1; i < argc; ++i) {
            const std::string option = argv[i];
            if (option == "--help") {
                std::cout << "Usage: shadow-demo [--worker PATH] [--scenario NAME] [--samples N]\n"
                             "  [--period-ms N] [--deadline-ms N] [--qualify N] [--probation N]\n"
                             "Scenarios: promote, reject-units, reject-delay, reject-nan, reject-stale,\n"
                             "  reject-protocol, reject-switch, rollback, baseline-failure, no-fallback,\n"
                             "  post-commit-failure\n"
                             "JSON Lines are written to stdout; diagnostics go to stderr.\n";
                return 0;
            }
            if (i + 1 >= argc) throw std::invalid_argument("Missing option value");
            const std::string argument = argv[++i];
            if (option == "--worker") worker = argument;
            else if (option == "--scenario") scenario = argument;
            else if (option == "--samples") samples = number(argument);
            else if (option == "--period-ms") period_ms = number(argument);
            else if (option == "--deadline-ms") deadline_ms = number(argument);
            else if (option == "--qualify") qualify = number(argument);
            else if (option == "--probation") probation = number(argument);
            else throw std::invalid_argument("Unknown option: " + option);
        }
        if (!samples || !period_ms || !deadline_ms || deadline_ms >= period_ms || deadline_ms > 100 || period_ms > 1000)
            throw std::invalid_argument("Require samples > 0 and 0 < deadline-ms < period-ms <= 1000; deadline-ms <= 100");
        Contract contract;
        contract.deadline_ns = static_cast<std::uint64_t>(deadline_ms) * 1000000;
        contract.qualification_samples = qualify; contract.probation_samples = probation;
        Supervisor supervisor(contract);
        std::string candidate_mode = "good", baseline_mode = "baseline";
        unsigned fault_after = 2;
        if (scenario == "promote") {}
        else if (scenario == "reject-units") candidate_mode = "bad-units";
        else if (scenario == "reject-delay") candidate_mode = "delay";
        else if (scenario == "reject-nan") candidate_mode = "nan";
        else if (scenario == "reject-stale") candidate_mode = "stale";
        else if (scenario == "reject-protocol") candidate_mode = "bad-header";
        else if (scenario == "reject-switch") candidate_mode = "offset";
        else if (scenario == "rollback" || scenario == "no-fallback") {
            if (probation < 2) throw std::invalid_argument("Rollback scenarios require probation >= 2");
            candidate_mode = "crash"; fault_after = qualify + 1 + probation / 2;
            if (scenario == "no-fallback") baseline_mode = "crash";
        } else if (scenario == "baseline-failure") baseline_mode = "crash";
        else if (scenario == "post-commit-failure") { candidate_mode = "crash"; fault_after = qualify + probation + 2; }
        else throw std::invalid_argument("Unknown scenario");
        std::signal(SIGPIPE, SIG_IGN);
        std::signal(SIGINT, stop); std::signal(SIGTERM, stop);
        auto baseline = std::make_unique<WorkerProcess>(worker, baseline_mode, fault_after);
        auto candidate = std::make_unique<WorkerProcess>(worker, candidate_mode, fault_after);
        std::cout << std::setprecision(10);
        std::cout << "{\"type\":\"meta\",\"schema\":1,\"backend\":\"" << WorkerProcess::backend()
                  << "\",\"scenario\":\"" << scenario << "\",\"baseline_pid\":" << baseline->pid()
                  << ",\"candidate_pid\":" << candidate->pid() << ",\"period_ms\":" << period_ms
                  << ",\"deadline_ms\":" << deadline_ms << ",\"ipc_budget_ms\":" << deadline_ms * 0.75
                  << ",\"qualification_samples\":" << qualify
                  << ",\"probation_samples\":" << probation << ",\"position_unit\":\"m\"}" << std::endl;
        const auto period_ns = static_cast<std::uint64_t>(period_ms) * 1000000;
        const auto start = monotonic_ns();
        auto next_release = start;
        unsigned outputs = 0, misses = 0, emitted = 0;
        for (unsigned i = 1; i <= samples && !interrupted; ++i) {
            const auto now = monotonic_ns();
            if (now < next_release) std::this_thread::sleep_for(std::chrono::nanoseconds(next_release - now));
            if (interrupted) break;
            Sample sample{};
            sample.sequence = i; sample.acquired_ns = monotonic_ns();
            const double seconds = static_cast<double>(sample.acquired_ns - start) / 1e9;
            const double truth = 10 + 0.5 * seconds + std::sin(seconds * 0.6);
            sample.position_m = truth + 0.08 * std::sin(static_cast<double>(i) * 1.7);
            const auto deadline = sample.acquired_ns + contract.deadline_ns;
            // Reserve a quarter of the decision budget for validation and selection.
            const auto ipc_deadline = sample.acquired_ns + contract.deadline_ns * 3 / 4;
            std::future<Observation> bf, cf;
            if (baseline) bf = baseline->submit(sample, ipc_deadline);
            if (candidate) cf = candidate->submit(sample, ipc_deadline);
            Observation b, c;
            if (bf.valid()) b = bf.get();
            if (cf.valid()) c = cf.get();
            auto decision = supervisor.step(sample, b, c);
            // A result received before the deadline may still be published too late.
            // Do not describe receipt latency as end-to-end output latency.
            const auto selected_ns = monotonic_ns();
            const bool publication_miss = selected_ns > deadline;
            if (publication_miss) {
                auto stopped = supervisor.stop_output("decision_deadline_exceeded");
                stopped.baseline_check = decision.baseline_check;
                stopped.candidate_check = decision.candidate_check;
                decision = std::move(stopped); ++misses;
            }
            if (decision.output) ++outputs;
            ++emitted;
            std::cout << "{\"type\":\"sample\",\"sequence\":" << i << ",\"time_s\":" << seconds
                      << ",\"truth\":" << truth << ",\"input\":" << sample.position_m << ",\"baseline\":";
            observation(b, sample); std::cout << ",\"candidate\":"; observation(c, sample);
            std::cout << ",\"phase\":\"" << name(decision.phase) << "\",\"source\":\"" << name(decision.source)
                      << "\",\"output\":";
            if (decision.output) value(decision.output->position_m); else std::cout << "null";
            std::cout << ",\"event\":\"" << decision.event << "\",\"reason\":\"" << decision.reason
                      << "\",\"baseline_check\":\"" << decision.baseline_check
                      << "\",\"candidate_check\":\"" << decision.candidate_check
                      << "\",\"decision_latency_ms\":" << static_cast<double>(selected_ns - sample.acquired_ns) / 1e6
                      << ",\"publication_miss\":" << (publication_miss ? "true" : "false")
                      << ",\"release_lateness_ms\":" << static_cast<double>(sample.acquired_ns - next_release) / 1e6
                      << "}" << std::endl;
            // Retire children after the decision. They cannot publish directly.
            if (!supervisor.needs_candidate()) candidate.reset();
            if (!supervisor.needs_baseline()) baseline.reset();
            if (decision.phase == Phase::failed) break;
            next_release += period_ns;
            const auto end = monotonic_ns();
            // Skip missed releases; never burst stale inputs to catch up.
            if (next_release < end) next_release += ((end - next_release) / period_ns + 1) * period_ns;
        }
        std::cout << "{\"type\":\"summary\",\"phase\":\"" << name(supervisor.phase())
                  << "\",\"samples\":" << emitted << ",\"outputs\":" << outputs
                  << ",\"missing_outputs\":" << emitted - outputs << ",\"publication_misses\":" << misses
                  << ",\"interrupted\":" << (interrupted ? "true" : "false") << "}" << std::endl;
        return interrupted ? 130 : (supervisor.phase() == Phase::failed ? 3 : 0);
    } catch (const std::exception& error) {
        std::cerr << "shadow-demo: " << error.what() << '\n'; return 2;
    }
}
