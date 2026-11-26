#include "shadow/continuity.hpp"
#include "shadow/io.hpp"
#include "shadow/policy.hpp"
#include "shadow/process.hpp"
#include "shadow/schedule.hpp"
#include "shadow/staging.hpp"
#include <charconv>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <deque>
#include <fcntl.h>
#include <iomanip>
#include <iostream>
#include <memory>
#include <optional>
#include <poll.h>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>
#include <vector>

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
std::vector<double> load_sensor_trace(const std::string& path, unsigned samples) {
    constexpr unsigned max_samples = 100000;
    constexpr unsigned max_line_bytes = 16;
    constexpr int max_position_mm = 1000000;
    if (samples > max_samples) throw std::invalid_argument("sensor trace supports at most 100000 samples");

    const int fd = ::open(path.c_str(), O_RDONLY | O_NONBLOCK);
    if (fd == -1) throw std::invalid_argument("cannot open sensor trace");
    struct stat info{};
    if (::fstat(fd, &info) == -1 || !S_ISREG(info.st_mode)) {
        ::close(fd);
        throw std::invalid_argument("sensor trace must be a regular file");
    }
    std::unique_ptr<std::FILE, decltype(&std::fclose)> stream(::fdopen(fd, "rb"), std::fclose);
    if (!stream) {
        ::close(fd);
        throw std::invalid_argument("cannot read sensor trace");
    }

    std::vector<double> positions;
    positions.reserve(samples);
    for (unsigned i = 1; i <= samples; ++i) {
        char line[max_line_bytes];
        unsigned length = 0;
        int ch = std::fgetc(stream.get());
        while (ch != '\n' && ch != EOF && length < sizeof(line)) {
            line[length++] = static_cast<char>(ch);
            ch = std::fgetc(stream.get());
        }
        int position_mm = 0;
        const auto parsed = std::from_chars(line, line + length, position_mm);
        if (ch != '\n' || parsed.ec != std::errc{} || parsed.ptr != line + length ||
            position_mm < -max_position_mm || position_mm > max_position_mm)
            throw std::invalid_argument("invalid sensor trace sample at line " + std::to_string(i));
        positions.push_back(position_mm / 1000.0);
    }
    if (std::fgetc(stream.get()) != EOF || std::ferror(stream.get()))
        throw std::invalid_argument("sensor trace read failed or contains more than --samples rows");
    return positions;
}
void value(double x) {
    if (std::isfinite(x)) std::cout << x;
    else std::cout << "null";
}
void milliseconds(std::optional<std::uint64_t> ns) {
    if (ns) std::cout << static_cast<double>(*ns) / 1e6;
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
void json_string(const std::string& text) {
    std::cout << '"';
    for (unsigned char ch : text) {
        if (ch == '"' || ch == '\\') std::cout << '\\' << ch;
        else if (ch < 32) std::cout << "\\u00" << std::hex << std::setw(2) << std::setfill('0')
                                 << static_cast<unsigned>(ch) << std::dec << std::setfill(' ');
        else std::cout << ch;
    }
    std::cout << '"';
}
void control_event(unsigned generation, std::uint64_t after, const std::string& event,
                   const std::string& reason, const shadow::StagingStatus& status = {}) {
    std::cout << "{\"type\":\"deployment\",\"generation\":" << generation << ",\"after_sequence\":" << after
              << ",\"event\":"; json_string(event);
    std::cout << ",\"reason\":"; json_string(reason);
    std::cout << ",\"artifact\":"; json_string(status.artifact);
    std::cout << ",\"candidate_pid\":" << status.pid << ",\"replayed\":" << status.replayed
              << ",\"through_sequence\":" << status.through_sequence << "}" << std::endl;
}
class Commands {
public:
    std::optional<std::string> poll_line() {
        if (pending_.find('\n') == std::string::npos && !closed_) {
            pollfd fd{STDIN_FILENO, POLLIN, 0};
            if (::poll(&fd, 1, 0) > 0 && (fd.revents & (POLLIN | POLLHUP))) {
                char buffer[512]; const auto count = read(STDIN_FILENO, buffer, sizeof(buffer));
                if (count > 0) pending_.append(buffer, static_cast<std::size_t>(count));
                else if (!count) closed_ = true;
            }
        }
        if (pending_.size() > 4096) { pending_.clear(); closed_ = true; return "invalid_command_too_long"; }
        const auto end = pending_.find('\n');
        if (end == std::string::npos) return {};
        auto line = pending_.substr(0, end); pending_.erase(0, end + 1);
        if (!line.empty() && line.back() == '\r') line.pop_back();
        return line;
    }
private:
    std::string pending_;
    bool closed_ = false;
};
} // namespace
int main(int argc, char** argv) {
    using namespace shadow;
    try {
        std::string worker = "./build/shadow-worker", scenario = "rollback";
        std::string manifest, trace_path;
        bool control_stdin = false, scenario_set = false;
        unsigned stage_at = 20;
        unsigned samples = 180, period_ms = 20, deadline_ms = 10, qualify = 20, probation = 40;
        for (int i = 1; i < argc; ++i) {
            const std::string option = argv[i];
            if (option == "--help") {
                std::cout << "Usage: shadow-demo [--worker PATH] [--scenario NAME] [--samples N]\n"
                             "  [--period-ms N] [--deadline-ms N] [--qualify N] [--probation N]\n"
                             "  [--candidate-manifest PATH --stage-at N] [--control-stdin]\n"
                             "  [--sensor-trace PATH] (one integer millimetre value per sample)\n"
                             "Live mode starts baseline only. Stage after N completed inputs or send\n"
                             "  stage PATH / cancel commands on stdin. Promotion follows qualification.\n"
                             "Scenarios: promote, reject-units, reject-delay, reject-nan, reject-stale,\n"
                             "  reject-protocol, reject-switch, rollback, baseline-failure, no-fallback,\n"
                             "  post-commit-failure, semantic-bias, semantic-freeze\n"
                             "JSON Lines are written to stdout; diagnostics go to stderr.\n";
                return 0;
            }
            if (option == "--control-stdin") { control_stdin = true; continue; }
            if (i + 1 >= argc) throw std::invalid_argument("Missing option value");
            const std::string argument = argv[++i];
            if (option == "--worker") worker = argument;
            else if (option == "--scenario") { scenario = argument; scenario_set = true; }
            else if (option == "--candidate-manifest") manifest = argument;
            else if (option == "--sensor-trace") {
                if (argument.empty()) throw std::invalid_argument("sensor trace path is empty");
                trace_path = argument;
            } else if (option == "--stage-at") stage_at = number(argument);
            else if (option == "--samples") samples = number(argument);
            else if (option == "--period-ms") period_ms = number(argument);
            else if (option == "--deadline-ms") deadline_ms = number(argument);
            else if (option == "--qualify") qualify = number(argument);
            else if (option == "--probation") probation = number(argument);
            else throw std::invalid_argument("Unknown option: " + option);
        }
        if (!samples || !period_ms || !deadline_ms || deadline_ms >= period_ms || period_ms > 1000)
            throw std::invalid_argument("Require samples > 0 and 0 < deadline-ms < period-ms <= 1000");
        Contract contract;
        contract.deadline_ns = static_cast<std::uint64_t>(deadline_ms) * 1000000;
        contract.period_ns = static_cast<std::uint64_t>(period_ms) * 1000000;
        contract.qualification_samples = qualify; contract.probation_samples = probation;
        const bool live = control_stdin || !manifest.empty();
        if (live && scenario_set) throw std::invalid_argument("Live deployment and scenario modes are separate");
        if (!manifest.empty() && (!stage_at || stage_at >= samples))
            throw std::invalid_argument("Require 0 < stage-at < samples");
        Supervisor supervisor(contract, !live);
        std::string candidate_mode = "good", baseline_mode = "baseline";
        unsigned fault_after = 2;
        if (live) { scenario = "live-deployment"; }
        else if (scenario == "promote") {}
        else if (scenario == "reject-units") candidate_mode = "bad-units";
        else if (scenario == "reject-delay") candidate_mode = "delay";
        else if (scenario == "reject-nan") candidate_mode = "nan";
        else if (scenario == "reject-stale") candidate_mode = "stale";
        else if (scenario == "reject-protocol") candidate_mode = "bad-header";
        else if (scenario == "reject-switch") {
            candidate_mode = "switch-offset"; fault_after = qualify;
        } else if (scenario == "semantic-bias" || scenario == "semantic-freeze") {
            candidate_mode = scenario; fault_after = qualify + 2;
        } else if (scenario == "rollback" || scenario == "no-fallback") {
            if (probation < 2) throw std::invalid_argument("Rollback scenarios require probation >= 2");
            candidate_mode = "crash"; fault_after = qualify + 1 + probation / 2;
            if (scenario == "no-fallback") baseline_mode = "crash";
        } else if (scenario == "baseline-failure") baseline_mode = "crash";
        else if (scenario == "post-commit-failure") { candidate_mode = "crash"; fault_after = qualify + probation + 2; }
        else throw std::invalid_argument("Unknown scenario");
        const auto trace = trace_path.empty() ? std::vector<double>{} : load_sensor_trace(trace_path, samples);
        std::signal(SIGPIPE, SIG_IGN);
        std::signal(SIGINT, stop); std::signal(SIGTERM, stop);
        auto baseline = std::make_unique<WorkerProcess>(worker, baseline_mode, fault_after);
        std::unique_ptr<WorkerProcess> candidate;
        if (!live) candidate = std::make_unique<WorkerProcess>(worker, candidate_mode, fault_after);
        std::unique_ptr<CandidateStager> stager;
        std::deque<Sample> history;
        Commands commands;
        unsigned generation = 0;
        StagingStatus staging_status;
        const auto run_stamp_ns = monotonic_ns();
        std::cout << std::setprecision(10);
        std::cout << "{\"type\":\"meta\",\"schema\":" << (live ? 2 : 1) << ",\"backend\":\"" << WorkerProcess::backend()
                  << "\",\"scenario\":\"" << scenario << "\",\"baseline_pid\":" << baseline->pid()
                  << ",\"candidate_pid\":" << (candidate ? candidate->pid() : -1) << ",\"period_ms\":" << period_ms
                  << ",\"deadline_ms\":" << deadline_ms << ",\"ipc_budget_ms\":" << deadline_ms * 0.75
                  << ",\"qualification_samples\":" << qualify
                  << ",\"probation_samples\":" << probation << ",\"position_unit\":\"m\"";
        std::cout << ",\"producer_run_id\":\"" << baseline->pid() << "-" << run_stamp_ns << "\"";
        std::cout << ",\"requested_inputs\":" << samples;
        if (!trace.empty()) std::cout << ",\"input_source\":\"sensor-trace-mm\"";
        std::cout << "}" << std::endl;
        const auto start = monotonic_ns();
        auto next_release = start;
        ServiceContinuity continuity(start, contract.period_ns);
        for (unsigned i = 1; i <= samples && !interrupted; ++i) {
            const auto now = monotonic_ns();
            if (now < next_release) std::this_thread::sleep_for(std::chrono::nanoseconds(next_release - now));
            if (interrupted) break;
            // Observe completed preparation and transfer ownership only at an input boundary.
            if (stager) {
                staging_status = stager->status();
                if (supervisor.phase() == Phase::warming) {
                    if (!staging_status.error.empty()) {
                        supervisor.reject_staging(staging_status.error);
                        control_event(generation, i - 1, "preparation_failed", staging_status.error, staging_status);
                        stager->cancel();
                    } else {
                        auto prepared = stager->take_if_ready(i - 1);
                        if (prepared.worker) {
                            staging_status = stager->status();
                            candidate = std::move(prepared.worker);
                            supervisor.begin_shadowing(prepared.last_replayed);
                            control_event(generation, i - 1, "replay_complete", "live_qualification_required", staging_status);
                        }
                    }
                }
                if (staging_status.done) stager.reset();
            }
            std::optional<std::string> command;
            if (!manifest.empty() && i == stage_at + 1) command = "stage " + manifest;
            else if (control_stdin) command = commands.poll_line();
            if (command) {
                if (command->rfind("stage ", 0) == 0 && command->size() > 6) {
                    if (stager || candidate || !supervisor.begin_staging()) {
                        control_event(generation, i - 1, "command_rejected", "deployment_busy_or_committed");
                    } else {
                        ++generation; staging_status = {};
                        stager = std::make_unique<CandidateStager>(command->substr(6),
                            std::vector<Sample>(history.begin(), history.end()), contract);
                        control_event(generation, i - 1, "stage_requested", "manifest_validation_pending");
                    }
                } else if (*command == "cancel" && supervisor.request_cancel()) {
                    if (stager) stager->cancel();
                    control_event(generation, i - 1, "cancel_requested", "operator_cancelled");
                } else control_event(generation, i - 1, "command_rejected", "invalid_command_or_phase");
            }
            Sample sample{};
            sample.sequence = i; sample.acquired_ns = monotonic_ns();
            const double seconds = static_cast<double>(sample.acquired_ns - start) / 1e9;
            const double truth = 10 + 0.5 * seconds + std::sin(seconds * 0.6);
            sample.position_m = trace.empty() ? truth + 0.08 * std::sin(static_cast<double>(i) * 1.7) : trace[i - 1];
            history.push_back(sample);
            if (history.size() > history_capacity) history.pop_front();
            if (stager && supervisor.phase() == Phase::warming) stager->enqueue(sample);
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
            const auto timing = continuity.acquire(sample.acquired_ns, next_release);
            if (publication_miss) {
                auto stopped = supervisor.stop_output("decision_deadline_exceeded");
                stopped.baseline_check = decision.baseline_check;
                stopped.candidate_check = decision.candidate_check;
                decision = std::move(stopped);
            }
            continuity.decide(selected_ns, decision.output.has_value(), publication_miss);
            std::cout << "{\"type\":\"sample\",\"sequence\":" << i << ",\"time_s\":" << seconds
                      << ",\"truth\":";
            if (trace.empty()) value(truth); else std::cout << "null";
            std::cout << ",\"input\":" << sample.position_m << ",\"baseline\":";
            observation(b, sample); std::cout << ",\"candidate\":"; observation(c, sample);
            std::cout << ",\"phase\":\"" << name(decision.phase) << "\",\"source\":\"" << name(decision.source)
                      << "\",\"output\":";
            if (decision.output) value(decision.output->position_m); else std::cout << "null";
            std::cout << ",\"generation\":" << generation << ",\"baseline_pid\":" << (baseline ? baseline->pid() : -1)
                      << ",\"candidate_pid\":" << (candidate ? candidate->pid() : -1)
                      << ",\"event\":"; json_string(decision.event);
            std::cout << ",\"reason\":"; json_string(decision.reason);
            std::cout << ",\"baseline_check\":\"" << decision.baseline_check
                      << "\",\"candidate_check\":\"" << decision.candidate_check
                      << "\",\"decision_latency_ms\":" << static_cast<double>(selected_ns - sample.acquired_ns) / 1e6
                      << ",\"publication_miss\":" << (publication_miss ? "true" : "false")
                      << ",\"nominal_slot\":" << timing.nominal_slot
                      << ",\"skipped_nominal_slots\":" << timing.skipped_slots
                      << ",\"acquired_offset_ns\":" << sample.acquired_ns - start
                      << ",\"scheduled_release_offset_ns\":" << next_release - start
                      << ",\"decision_offset_ns\":" << selected_ns - start
                      << ",\"release_lateness_ms\":";
            milliseconds(timing.scheduling_lateness_ns);
            std::cout << ",\"nominal_slot_lateness_ms\":";
            milliseconds(timing.nominal_lateness_ns);
            std::cout << ",\"nominal_grid_lateness_ms\":";
            milliseconds(timing.nominal_grid_lateness_ns);
            std::cout << ",\"acquisition_interval_ms\":";
            milliseconds(timing.acquisition_interval_ns);
            std::cout << "}" << std::endl;
            // Retire children after the decision. They cannot publish directly.
            if (!supervisor.needs_candidate()) candidate.reset();
            if (!supervisor.needs_baseline()) baseline.reset();
            if (decision.phase == Phase::failed) break;
            // Rebase on actual acquisition, not the old nominal release grid.
            next_release = next_release_ns(sample.acquired_ns, monotonic_ns(), contract.period_ns);
        }
        const auto missing = continuity.acquired - continuity.selected;
        std::cout << "{\"type\":\"summary\",\"phase\":\"" << name(supervisor.phase())
                  << "\",\"samples\":" << continuity.acquired << ",\"outputs\":" << continuity.selected
                  << ",\"missing_outputs\":" << missing
                  << ",\"publication_misses\":" << continuity.decision_deadline_misses
                  << ",\"interrupted\":" << (interrupted ? "true" : "false")
                  << ",\"requested_inputs\":" << samples
                  << ",\"acquired_inputs\":" << continuity.acquired
                  << ",\"selected_outputs\":" << continuity.selected
                  << ",\"missing_selected_outputs\":" << missing
                  << ",\"unprocessed_inputs\":" << samples - continuity.acquired
                  << ",\"decision_deadline_misses\":" << continuity.decision_deadline_misses
                  << ",\"initial_unoccupied_slots\":" << continuity.initial_unoccupied_slots
                  << ",\"skipped_nominal_slots\":" << continuity.skipped_slots
                  << ",\"last_nominal_slot\":";
        if (continuity.last_nominal_slot) std::cout << *continuity.last_nominal_slot;
        else std::cout << "null";
        std::cout << ",\"max_scheduling_lateness_ms\":";
        milliseconds(continuity.max_scheduling_lateness_ns);
        std::cout << ",\"max_nominal_slot_lateness_ms\":";
        milliseconds(continuity.max_nominal_slot_lateness_ns);
        std::cout << ",\"max_nominal_grid_lateness_ms\":";
        milliseconds(continuity.max_nominal_grid_lateness_ns);
        std::cout << ",\"max_acquisition_interval_ms\":";
        milliseconds(continuity.max_acquisition_interval_ns);
        std::cout << ",\"max_selected_interval_ms\":";
        milliseconds(continuity.max_selected_interval_ns);
        std::cout << ",\"effective_selected_rate_hz\":";
        if (continuity.selected_span_ns)
            std::cout << (continuity.selected - 1) * 1e9 / static_cast<double>(continuity.selected_span_ns);
        else std::cout << "null";
        std::cout << ",\"external_delivery\":\"unmeasured\"}" << std::endl;
        return interrupted ? 130 : (supervisor.phase() == Phase::failed ? 3 : 0);
    } catch (const std::exception& error) {
        std::cerr << "shadow-demo: " << error.what() << '\n'; return 2;
    }
}
