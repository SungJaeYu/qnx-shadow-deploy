#include "shadow/continuity.hpp"
#include "shadow/control.hpp"
#include "shadow/io.hpp"
#include "shadow/live_range.hpp"
#include "shadow/policy.hpp"
#include "shadow/process.hpp"
#include "shadow/schedule.hpp"
#include "shadow/staging.hpp"
#include <algorithm>
#include <cerrno>
#include <charconv>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <fcntl.h>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <optional>
#include <poll.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>
#include <vector>
#ifdef __APPLE__
#include <libproc.h>
#endif

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
    const auto close_file = [](std::FILE* file) { std::fclose(file); };
    std::unique_ptr<std::FILE, decltype(close_file)> stream(::fdopen(fd, "rb"), close_file);
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
bool same_pipe(int first, int second) {
    struct stat a{}, b{};
    return ::fstat(first, &a) == 0 && S_ISFIFO(a.st_mode) &&
           ::fstat(second, &b) == 0 && S_ISFIFO(b.st_mode) &&
           a.st_dev == b.st_dev && a.st_ino == b.st_ino;
}

bool aliases_range_pipe(int reader, int other) {
    if (reader == other || same_pipe(reader, other)) return true;
#ifdef __APPLE__
    // Darwin assigns different stat identities to the ends of an anonymous pipe.
    pipe_fdinfo input{}, endpoint{};
    return ::proc_pidfdinfo(::getpid(), reader, PROC_PIDFDPIPEINFO, &input, sizeof(input)) == sizeof(input) &&
           ::proc_pidfdinfo(::getpid(), other, PROC_PIDFDPIPEINFO, &endpoint, sizeof(endpoint)) == sizeof(endpoint) &&
           input.pipeinfo.pipe_peerhandle != 0 &&
           input.pipeinfo.pipe_peerhandle == endpoint.pipeinfo.pipe_handle;
#else
    return false;
#endif
}

struct PipeAliases {
    bool stdout_pipe = false;
    bool stderr_pipe = false;
};

PipeAliases pipe_aliases(int argc, char** argv, bool range_input) {
    PipeAliases aliases;
    const std::string_view option = range_input ? "--live-range-fd" : "--output-fd";
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::string_view(argv[i]) != option) continue;
        // Check every occurrence before parsing can print help or an error. The
        // decimal prefix protects the pipe even when the actual value is invalid.
        char* end = nullptr;
        const long value = std::strtol(argv[i + 1], &end, 10);
        if (end == argv[i + 1] || value < 0 || value > std::numeric_limits<int>::max()) continue;
        const int fd = static_cast<int>(value);
        if (range_input) {
            aliases.stdout_pipe |= aliases_range_pipe(fd, STDOUT_FILENO);
            aliases.stderr_pipe |= aliases_range_pipe(fd, STDERR_FILENO);
        } else {
            aliases.stdout_pipe |= fd == STDOUT_FILENO || same_pipe(fd, STDOUT_FILENO);
            aliases.stderr_pipe |= fd == STDERR_FILENO || same_pipe(fd, STDERR_FILENO);
        }
    }
    return aliases;
}

constexpr std::uint64_t telemetry_shutdown_grace_ns = 20'000'000;

struct AuthorityWriteTiming {
    std::uint64_t start_ns = 0;
    std::uint64_t end_ns = 0;
    const char* outcome = nullptr;
};

struct AtomicPipe {
    int fd;
    std::size_t record_limit;

    static AtomicPipe prepare(int fd) {
        struct stat info{};
        const int flags = ::fcntl(fd, F_GETFL);
        if (flags == -1 || (flags & O_ACCMODE) != O_WRONLY ||
            ::fstat(fd, &info) == -1 || !S_ISFIFO(info.st_mode))
            return {fd, 0};
        const auto limit = ::fpathconf(fd, _PC_PIPE_BUF);
        if (limit <= 0 || ::fcntl(fd, F_SETFL, flags | O_NONBLOCK) == -1) return {fd, 0};
        return {fd, static_cast<std::size_t>(limit)};
    }

    const char* write_record(const std::string& record, AuthorityWriteTiming* timing) const {
        if (record.size() > record_limit) return "output_record_too_large";
        if (timing) timing->start_ns = shadow::monotonic_ns();
        const auto count = ::write(fd, record.data(), record.size());
        const int error = count < 0 ? errno : 0;
        if (timing) {
            timing->end_ns = shadow::monotonic_ns();
            timing->outcome = count == static_cast<ssize_t>(record.size()) ? "accepted" : "rejected";
        }
        if (count == static_cast<ssize_t>(record.size())) return nullptr;
        if (error == EAGAIN || error == EWOULDBLOCK) return "output_pipe_full";
        if (error == EPIPE) return "output_reader_disconnected";
        if (error == EINTR) return "output_write_interrupted";
        return "output_write_failed";
    }
};

class Telemetry {
public:
    Telemetry(bool split, bool disabled)
        : stream(split ? static_cast<std::ostream&>(record_) : std::cout),
          split_(split), disabled_(disabled) {}

    void emit() {
        if (!split_) {
            stream.flush();
            return;
        }
        if (!disabled_) {
            resume_pending();
            if (pipe_failed_ || !pending_.empty()) {
                ++dropped;
            } else {
                pending_ = record_.str();
                resume_pending();
                // If no bytes were sent, dropping the entire row leaves nothing to finish.
                if (!pending_.empty() && pending_offset_ == 0) {
                    ++dropped;
                    pending_.clear();
                }
            }
        }
        record_.str({});
    }

    void drain(std::uint64_t deadline_ns) {
        while (!pending_.empty()) {
            const auto now = shadow::monotonic_ns();
            if (now >= deadline_ns) break;
            const auto wait_ms = static_cast<int>((deadline_ns - now + 999'999) / 1'000'000);
            pollfd fd{STDOUT_FILENO, POLLOUT, 0};
            if (::poll(&fd, 1, wait_ms) <= 0 || !(fd.revents & POLLOUT)) break;
            resume_pending();
        }
        if (!pending_.empty()) abandon_pending();
    }

private:
    void resume_pending() {
        if (pending_.empty()) return;
        const auto count = ::write(STDOUT_FILENO, pending_.data() + pending_offset_,
                                   pending_.size() - pending_offset_);
        if (count > 0) {
            pending_offset_ += static_cast<std::size_t>(count);
            if (pending_offset_ == pending_.size()) {
                pending_.clear();
                pending_offset_ = 0;
            }
        } else if (count == 0 || (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) {
            abandon_pending();
        }
    }

    void abandon_pending() {
        ++dropped;
        pending_.clear();
        pending_offset_ = 0;
        // A partial row may be visible. Never append another row to its prefix.
        pipe_failed_ = true;
    }

    std::ostringstream record_;
public:
    std::ostream& stream;
    unsigned dropped = 0;
private:
    bool split_;
    bool disabled_;
    bool pipe_failed_ = false;
    std::string pending_;
    std::size_t pending_offset_ = 0;
};

void value(std::ostream& out, double x) {
    if (std::isfinite(x)) out << x;
    else out << "null";
}
void milliseconds(std::ostream& out, std::optional<std::uint64_t> ns) {
    if (ns) out << static_cast<double>(*ns) / 1e6;
    else out << "null";
}
constexpr unsigned phase_timing_capacity = 4096;
struct WorkerPhaseTiming {
    shadow::WorkerExchangeTiming exchange;
    std::uint64_t future_wait_ns = 0;
    std::uint64_t future_return_ns = 0;
    std::uint64_t retirement_start_ns = 0;
    std::uint64_t retirement_end_ns = 0;
};
struct PhaseTiming {
    std::uint64_t acquired_ns = 0;
    std::uint64_t ipc_deadline_ns = 0;
    std::uint64_t selected_ns = 0;
    AuthorityWriteTiming authority_write;
    std::uint64_t flush_start_ns = 0;
    std::uint64_t flush_end_ns = 0;
    WorkerPhaseTiming baseline;
    WorkerPhaseTiming candidate;
};
void timestamp(const char* key, std::uint64_t ns) {
    std::clog << ",\"" << key << "\":";
    if (ns) std::clog << ns;
    else std::clog << "null";
}
void worker_phase_timing(const WorkerPhaseTiming& timing) {
    const auto& exchange = timing.exchange;
    if (!exchange.submit_ns) { std::clog << "null"; return; }
    std::clog << "{\"submit_ns\":" << exchange.submit_ns;
    timestamp("dispatch_ns", exchange.dispatch_ns);
    timestamp("ipc_start_ns", exchange.ipc_start_ns);
    timestamp("ipc_end_ns", exchange.ipc_end_ns);
    timestamp("pre_expiry_ns", exchange.pre_expiry_ns);
    timestamp("completed_ns", exchange.completed_ns);
    timestamp("future_wait_ns", timing.future_wait_ns);
    timestamp("future_return_ns", timing.future_return_ns);
    timestamp("retirement_start_ns", timing.retirement_start_ns);
    timestamp("retirement_end_ns", timing.retirement_end_ns);
    std::clog << "}";
}
void emit_phase_timing(const PhaseTiming& timing, unsigned sequence) {
    std::clog << "{\"type\":\"phase_timing\",\"sequence\":" << sequence;
    timestamp("acquired_ns", timing.acquired_ns);
    timestamp("ipc_deadline_ns", timing.ipc_deadline_ns);
    timestamp("selected_ns", timing.selected_ns);
    timestamp("authority_write_start_ns", timing.authority_write.start_ns);
    timestamp("authority_write_end_ns", timing.authority_write.end_ns);
    std::clog << ",\"authority_write_outcome\":";
    if (timing.authority_write.outcome) std::clog << '"' << timing.authority_write.outcome << '"';
    else std::clog << "null";
    timestamp("flush_start_ns", timing.flush_start_ns);
    timestamp("flush_end_ns", timing.flush_end_ns);
    std::clog << ",\"baseline\":";
    worker_phase_timing(timing.baseline);
    std::clog << ",\"candidate\":";
    worker_phase_timing(timing.candidate);
    std::clog << "}\n";
}
shadow::Observation collect(std::future<shadow::Observation>& future, WorkerPhaseTiming* timing) {
    if (timing) timing->future_wait_ns = shadow::monotonic_ns();
    const auto observation = future.get();
    if (timing) timing->future_return_ns = shadow::monotonic_ns();
    return observation;
}
void retire(std::unique_ptr<shadow::WorkerProcess>& worker, WorkerPhaseTiming* timing) {
    if (timing) timing->retirement_start_ns = shadow::monotonic_ns();
    worker.reset();
    if (timing) timing->retirement_end_ns = shadow::monotonic_ns();
}
void observation(std::ostream& out, const shadow::Observation& o, const shadow::Sample& sample) {
    if (o.status == shadow::TransportStatus::unavailable) { out << "null"; return; }
    out << "{\"position\":";
    if (o.status == shadow::TransportStatus::ok) value(out, o.estimate.position_m);
    else out << "null";
    out << ",\"latency_ms\":";
    value(out, o.completed_ns >= sample.acquired_ns ? static_cast<double>(o.completed_ns - sample.acquired_ns) / 1e6 : 0);
    out << "}";
}
void json_string(std::ostream& out, const std::string& text) {
    out << '"';
    for (unsigned char ch : text) {
        if (ch == '"' || ch == '\\') out << '\\' << ch;
        else if (ch < 32) out << "\\u00" << std::hex << std::setw(2) << std::setfill('0')
                                 << static_cast<unsigned>(ch) << std::dec << std::setfill(' ');
        else out << ch;
    }
    out << '"';
}
std::string control_result(const char* outcome, unsigned generation, const std::string& reason,
                           shadow::Source owner) {
    std::ostringstream out;
    out << "{\"outcome\":\"" << outcome << "\",\"generation\":";
    if (generation) out << generation;
    else out << "null";
    out << ",\"reason\":";
    json_string(out, reason);
    out << ",\"active_owner\":";
    if (owner == shadow::Source::none) out << "null";
    else out << '"' << shadow::name(owner) << '"';
    out << '}';
    return out.str();
}
void control_event(Telemetry& telemetry, unsigned generation, std::uint64_t after, const std::string& event,
                   const std::string& reason, const shadow::StagingStatus& status = {}) {
    auto& out = telemetry.stream;
    out << "{\"type\":\"deployment\",\"generation\":" << generation << ",\"after_sequence\":" << after
              << ",\"event\":"; json_string(out, event);
    out << ",\"reason\":"; json_string(out, reason);
    out << ",\"artifact\":"; json_string(out, status.artifact);
    out << ",\"candidate_pid\":" << status.pid << ",\"replayed\":" << status.replayed
              << ",\"through_sequence\":" << status.through_sequence << "}\n";
    telemetry.emit();
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
    const auto output_aliases = pipe_aliases(argc, argv, false);
    const auto range_aliases = pipe_aliases(argc, argv, true);
    if (output_aliases.stderr_pipe || range_aliases.stderr_pipe) return 2;
    if (output_aliases.stdout_pipe || range_aliases.stdout_pipe) {
        std::cerr << (range_aliases.stdout_pipe ? "shadow-demo: --live-range-fd must be separate from stdout\n" :
                                                "shadow-demo: --output-fd must be separate from stdout\n");
        return 2;
    }
    try {
        std::string worker = "./build/shadow-worker", scenario = "rollback";
        std::string manifest, trace_path, control_socket;
        bool control_stdin = false, scenario_set = false, phase_timing = false;
        bool no_telemetry = false;
        int output_fd = -1, live_range_fd = -1;
        unsigned stage_at = 20;
        unsigned samples = 180, period_ms = 20, deadline_ms = 10, qualify = 20, probation = 40;
        for (int i = 1; i < argc; ++i) {
            const std::string option = argv[i];
            if (option == "--help") {
                std::cout << "Usage: shadow-demo [--worker PATH] [--scenario NAME] [--samples N]\n"
                             "  [--period-ms N] [--deadline-ms N] [--qualify N] [--probation N]\n"
                             "  [--candidate-manifest PATH --stage-at N] [--control-stdin] [--control-socket PATH]\n"
                             "  [--output-fd FD [--no-telemetry]] (dedicated pipe writer)\n"
                             "  [--phase-timing] (up to 4096 samples on stderr after the run)\n"
                             "  [--sensor-trace PATH] (one integer millimetre value per sample)\n"
                             "  [--live-range-fd FD] (dedicated pipe reader for live millimetre records)\n"
                             "Live deployment mode starts baseline only. Stage after N completed inputs or send\n"
                             "  stage PATH / cancel commands on stdin, or submit using shadowctl.\n"
                             "Scenarios: promote, reject-units, reject-delay, reject-nan, reject-stale,\n"
                             "  reject-protocol, reject-switch, rollback, baseline-failure, no-fallback,\n"
                             "  post-commit-failure, semantic-bias, semantic-freeze\n"
                             "JSON Lines are written to stdout; diagnostics go to stderr.\n";
                return 0;
            }
            if (option == "--no-telemetry") { no_telemetry = true; continue; }
            if (option == "--phase-timing") { phase_timing = true; continue; }
            if (option == "--control-stdin") { control_stdin = true; continue; }
            if (i + 1 >= argc) throw std::invalid_argument("Missing option value");
            const std::string argument = argv[++i];
            if (option == "--worker") worker = argument;
            else if (option == "--scenario") { scenario = argument; scenario_set = true; }
            else if (option == "--candidate-manifest") manifest = argument;
            else if (option == "--control-socket") {
                if (argument.empty()) throw std::invalid_argument("control socket path is empty");
                control_socket = argument;
            } else if (option == "--sensor-trace") {
                if (argument.empty()) throw std::invalid_argument("sensor trace path is empty");
                trace_path = argument;
            } else if (option == "--stage-at") stage_at = number(argument);
            else if (option == "--output-fd") {
                if (output_fd != -1) throw std::invalid_argument("--output-fd must appear only once");
                const auto* end = argument.data() + argument.size();
                const auto parsed = std::from_chars(argument.data(), end, output_fd);
                if (parsed.ec != std::errc{} || parsed.ptr != end || output_fd < 0)
                    throw std::invalid_argument("--output-fd requires an integer descriptor above 2");
            } else if (option == "--live-range-fd") {
                if (live_range_fd != -1) throw std::invalid_argument("--live-range-fd must appear only once");
                live_range_fd = static_cast<int>(number(argument));
                if (live_range_fd <= STDERR_FILENO)
                    throw std::invalid_argument("--live-range-fd requires an integer descriptor above 2");
            } else if (option == "--samples") samples = number(argument);
            else if (option == "--period-ms") period_ms = number(argument);
            else if (option == "--deadline-ms") deadline_ms = number(argument);
            else if (option == "--qualify") qualify = number(argument);
            else if (option == "--probation") probation = number(argument);
            else throw std::invalid_argument("Unknown option: " + option);
        }
        const bool split_output = output_fd != -1;
        if (split_output) {
            std::signal(SIGPIPE, SIG_IGN);
            AtomicPipe::prepare(STDERR_FILENO);
        }
        if (!samples || !period_ms || !deadline_ms || deadline_ms >= period_ms || period_ms > 1000)
            throw std::invalid_argument("Require samples > 0 and 0 < deadline-ms < period-ms <= 1000");
        Contract contract;
        contract.deadline_ns = static_cast<std::uint64_t>(deadline_ms) * 1000000;
        contract.period_ns = static_cast<std::uint64_t>(period_ms) * 1000000;
        contract.qualification_samples = qualify; contract.probation_samples = probation;
        const bool live = control_stdin || !manifest.empty() || !control_socket.empty();
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
        if (no_telemetry && output_fd == -1)
            throw std::invalid_argument("--no-telemetry requires --output-fd");
        AtomicPipe authority{output_fd, 0};
        if (split_output) {
            if (output_fd <= STDERR_FILENO || same_pipe(output_fd, STDIN_FILENO) ||
                same_pipe(output_fd, STDOUT_FILENO))
                throw std::invalid_argument("--output-fd requires a dedicated descriptor above 2");
            authority = AtomicPipe::prepare(output_fd);
            const int fd_flags = ::fcntl(output_fd, F_GETFD);
            if (!authority.record_limit || fd_flags == -1 ||
                ::fcntl(output_fd, F_SETFD, fd_flags | FD_CLOEXEC) == -1)
                throw std::invalid_argument("--output-fd must be a writable pipe or FIFO");
            if (!no_telemetry) {
                if (same_pipe(STDOUT_FILENO, STDERR_FILENO))
                    throw std::invalid_argument("split output requires separate stdout and stderr, or --no-telemetry");
                if (!AtomicPipe::prepare(STDOUT_FILENO).record_limit)
                    throw std::invalid_argument("split output requires a writable stdout pipe or --no-telemetry");
            }
        }
        std::optional<LiveRangeInput> range_input;
        if (live_range_fd != -1) {
            if (!trace_path.empty())
                throw std::invalid_argument("--live-range-fd and --sensor-trace are mutually exclusive");
            if (aliases_range_pipe(live_range_fd, output_fd) || aliases_range_pipe(live_range_fd, STDIN_FILENO))
                throw std::invalid_argument("--live-range-fd requires a separate input pipe");
            range_input.emplace(live_range_fd);
        }
        const bool bounded_telemetry = split_output ||
            (!control_socket.empty() && AtomicPipe::prepare(STDOUT_FILENO).record_limit);
        Telemetry telemetry(bounded_telemetry, no_telemetry);
        auto& out = telemetry.stream;
        const auto trace = trace_path.empty() ? std::vector<double>{} : load_sensor_trace(trace_path, samples);
        std::signal(SIGPIPE, SIG_IGN);
        std::signal(SIGINT, stop); std::signal(SIGTERM, stop);
        // Keep records alive until both dispatch threads have joined, including on exceptions.
        std::vector<PhaseTiming> phase_records(phase_timing ? std::min(samples, phase_timing_capacity) : 0);
        auto baseline = std::make_unique<WorkerProcess>(worker, baseline_mode, fault_after);
        std::unique_ptr<WorkerProcess> candidate;
        if (!live) candidate = std::make_unique<WorkerProcess>(worker, candidate_mode, fault_after);
        std::unique_ptr<CandidateStager> stager;
        std::deque<Sample> history;
        Commands commands;
        unsigned generation = 0;
        StagingStatus staging_status;
        std::optional<DeploymentControl> control;
        if (!control_socket.empty()) control.emplace(control_socket);
        Source active_owner = Source::none;
        unsigned hook_exit_code = 0;
        std::string hook_result;
        const auto begin_stage = [&](const std::string& path, unsigned after_sequence) {
            // An asynchronous preparation failure can reach rejected before step()
            // reports the terminal decision. Reserve the generation until that reply.
            if ((control && control->awaiting_result()) || stager || candidate || !supervisor.begin_staging())
                return false;
            ++generation;
            staging_status = {};
            stager = std::make_unique<CandidateStager>(path,
                std::vector<Sample>(history.begin(), history.end()), contract);
            control_event(telemetry, generation, after_sequence, "stage_requested", "manifest_validation_pending");
            return true;
        };
        const auto finish_hook = [&]() {
            if (!hook_result.empty() && !stager) {
                control->finish(hook_exit_code, hook_result);
                hook_result.clear();
            }
        };
        const auto producer_run_id = std::to_string(baseline->pid()) + "-" + std::to_string(monotonic_ns());
        const char* delivery_error = nullptr;
        unsigned delivered_outputs = 0;
        unsigned shadow_qualification_omissions = 0;
        unsigned last_selected_sequence = 0;
        unsigned last_delivered_sequence = 0;
        out << std::setprecision(10);
        out << "{\"type\":\"meta\",\"schema\":" << (live ? 2 : 1) << ",\"backend\":\"" << WorkerProcess::backend()
                  << "\",\"scenario\":\"" << scenario << "\",\"baseline_pid\":" << baseline->pid()
                  << ",\"candidate_pid\":" << (candidate ? candidate->pid() : -1) << ",\"period_ms\":" << period_ms
                  << ",\"deadline_ms\":" << deadline_ms << ",\"ipc_budget_ms\":" << deadline_ms * 0.75
                  << ",\"qualification_samples\":" << qualify
                  << ",\"probation_samples\":" << probation << ",\"position_unit\":\"m\"";
        out << ",\"producer_run_id\":\"" << producer_run_id << "\"";
        out << ",\"requested_inputs\":" << samples;
        if (!trace.empty()) out << ",\"input_source\":\"sensor-trace-mm\"";
        if (range_input) out << ",\"input_source\":\"live-range-mm\"";
        out << "}\n";
        telemetry.emit();
        const char* input_error = nullptr;
        auto start = monotonic_ns();
        auto next_release = start;
        ServiceContinuity continuity(start, contract.period_ns);
        unsigned worker_retirement_samples = 0;
        std::uint64_t max_worker_retirement_ns = 0;
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
                        control_event(telemetry, generation, i - 1, "preparation_failed", staging_status.error, staging_status);
                        stager->cancel();
                    } else {
                        auto prepared = stager->take_if_ready(i - 1);
                        if (prepared.worker) {
                            staging_status = stager->status();
                            candidate = std::move(prepared.worker);
                            supervisor.begin_shadowing(prepared.last_replayed);
                            control_event(telemetry, generation, i - 1, "replay_complete", "live_qualification_required", staging_status);
                        }
                    }
                }
                if (staging_status.done) stager.reset();
            }
            finish_hook();
            std::optional<std::string> command;
            if (!manifest.empty() && i == stage_at + 1) command = "stage " + manifest;
            else if (control_stdin) command = commands.poll_line();
            if (command) {
                if (command->rfind("stage ", 0) == 0 && command->size() > 6) {
                    if (!begin_stage(command->substr(6), i - 1))
                        control_event(telemetry, generation, i - 1, "command_rejected", "deployment_busy_or_committed");
                } else if (*command == "cancel" && supervisor.request_cancel()) {
                    if (stager) stager->cancel();
                    control_event(telemetry, generation, i - 1, "cancel_requested", "operator_cancelled");
                } else control_event(telemetry, generation, i - 1, "command_rejected", "invalid_command_or_phase");
            }
            if (control) {
                const auto request = control->request();
                if (request) {
                    if (supervisor.phase() == Phase::committed) {
                        control->refuse(control_result("refused", 0, "deployment_already_committed", active_owner));
                    } else if (begin_stage(*request, i - 1)) {
                        control->accept(generation);
                    } else {
                        control->refuse(control_result("refused", 0, "deployment_busy", active_owner));
                    }
                }
            }
            Sample sample{};
            const auto ipc_budget_ns = contract.deadline_ns * 3 / 4;
            if (range_input) {
                input_error = range_input->receive(i, monotonic_ns() + contract.period_ns, ipc_budget_ns, sample);
                if (interrupted) { input_error = nullptr; break; }
                if (input_error) {
                    supervisor.stop_output(input_error);
                    active_owner = Source::none;
                    if (control && control->awaiting_result() && hook_result.empty()) {
                        hook_exit_code = 6;
                        hook_result = control_result("failed", generation, input_error, active_owner);
                    }
                    break;
                }
                if (i == 1 && sample.acquired_ns < start) {
                    start = sample.acquired_ns;
                    continuity = ServiceContinuity(start, contract.period_ns);
                }
            } else {
                sample.sequence = i;
                sample.acquired_ns = monotonic_ns();
            }
            const double seconds = static_cast<double>(sample.acquired_ns - start) / 1e9;
            double truth = 0;
            if (!trace.empty()) {
                sample.position_m = trace[i - 1];
            } else if (!range_input) {
                truth = 10 + 0.5 * seconds + std::sin(seconds * 0.6);
                sample.position_m = truth + 0.08 * std::sin(static_cast<double>(i) * 1.7);
            }
            history.push_back(sample);
            if (history.size() > history_capacity) history.pop_front();
            if (stager && supervisor.phase() == Phase::warming) stager->enqueue(sample);
            const auto deadline = sample.acquired_ns + contract.deadline_ns;
            // Reserve a quarter of the decision budget for validation and selection.
            const auto ipc_deadline = sample.acquired_ns + ipc_budget_ns;
            auto* const phase = i <= phase_records.size() ? &phase_records[i - 1] : nullptr;
            auto* const baseline_timing = phase ? &phase->baseline : nullptr;
            auto* const candidate_timing = phase ? &phase->candidate : nullptr;
            if (phase) {
                phase->acquired_ns = sample.acquired_ns;
                phase->ipc_deadline_ns = ipc_deadline;
            }
            std::future<Observation> bf, cf;
            if (baseline) bf = baseline->submit(sample, ipc_deadline,
                                                baseline_timing ? &baseline_timing->exchange : nullptr);
            if (candidate) cf = candidate->submit(sample, ipc_deadline,
                                                  candidate_timing ? &candidate_timing->exchange : nullptr);
            Observation b, c;
            if (bf.valid()) b = collect(bf, baseline_timing);
            const bool candidate_pending = split_output && supervisor.phase() == Phase::shadowing && cf.valid()
                && cf.wait_for(std::chrono::nanoseconds::zero()) == std::future_status::timeout;
            if (cf.valid() && !candidate_pending) c = collect(cf, candidate_timing);
            auto decision = supervisor.step(sample, b, c, candidate_pending);
            // A result received before the deadline may still be published too late.
            // Do not describe receipt latency as end-to-end output latency.
            const auto selected_ns = monotonic_ns();
            if (phase) phase->selected_ns = selected_ns;
            const bool publication_miss = selected_ns > deadline;
            const auto timing = continuity.acquire(sample.acquired_ns, next_release);
            if (publication_miss) {
                auto stopped = supervisor.stop_output("decision_deadline_exceeded");
                stopped.baseline_check = decision.baseline_check;
                stopped.candidate_check = decision.candidate_check;
                decision = std::move(stopped);
            }
            continuity.decide(selected_ns, decision.output.has_value(), publication_miss);
            bool output_delivered = false;
            if (split_output && decision.output) {
                last_selected_sequence = i;
                std::ostringstream record;
                record << std::setprecision(10)
                       << "{\"type\":\"authoritative_output\",\"schema\":1,\"producer_run_id\":\"" << producer_run_id
                       << "\",\"sequence\":" << i << ",\"source\":\"" << name(decision.source)
                       << "\",\"position_m\":" << decision.output->position_m
                       << ",\"deadline_ns\":" << deadline << "}\n";
                const auto bytes = record.str();
                if (monotonic_ns() > deadline) {
                    delivery_error = "output_write_deadline_exceeded";
                } else {
                    delivery_error = authority.write_record(bytes, phase ? &phase->authority_write : nullptr);
                    if (!delivery_error) {
                        output_delivered = true;
                        last_delivered_sequence = i;
                        ++delivered_outputs;
                        const auto write_end_ns = phase ? phase->authority_write.end_ns : monotonic_ns();
                        if (write_end_ns > deadline) {
                            delivery_error = "output_write_returned_after_deadline";
                            if (phase) phase->authority_write.outcome = "accepted_late";
                        }
                    }
                }
                if (delivery_error) supervisor.stop_output(delivery_error);
            }
            bool shadow_qualification_omitted = false;
            if (candidate_pending) {
                c = collect(cf, candidate_timing);
                shadow_qualification_omitted = supervisor.finish_pending_candidate(sample, c, decision);
                if (shadow_qualification_omitted) ++shadow_qualification_omissions;
            }
            if (control) {
                active_owner = supervisor.phase() == Phase::failed ? Source::none : decision.source;
                if (control->awaiting_result() && hook_result.empty()) {
                    const auto state = supervisor.phase();
                    const auto* reason = delivery_error ? delivery_error : decision.reason.c_str();
                    if (state == Phase::committed || state == Phase::rejected ||
                        state == Phase::rolled_back || state == Phase::failed) {
                        hook_exit_code = state == Phase::committed ? 0 :
                            state == Phase::rejected ? 2 : state == Phase::rolled_back ? 3 : 6;
                        hook_result = control_result(name(state), generation, reason, active_owner);
                    }
                }
            }
            out << "{\"type\":\"sample\",\"sequence\":" << i << ",\"time_s\":" << seconds
                      << ",\"truth\":";
            if (trace.empty() && !range_input) value(out, truth); else out << "null";
            if (range_input) out << ",\"acquired_ns\":" << sample.acquired_ns;
            out << ",\"input\":" << sample.position_m << ",\"baseline\":";
            observation(out, b, sample); out << ",\"candidate\":"; observation(out, c, sample);
            out << ",\"phase\":\"" << name(supervisor.phase()) << "\",\"source\":\"" << name(decision.source)
                      << "\",\"output\":";
            if (decision.output) value(out, decision.output->position_m); else out << "null";
            out << ",\"generation\":" << generation << ",\"baseline_pid\":" << (baseline ? baseline->pid() : -1)
                      << ",\"candidate_pid\":" << (candidate ? candidate->pid() : -1)
                      << ",\"event\":"; json_string(out, decision.event);
            out << ",\"reason\":"; json_string(out, decision.reason);
            out << ",\"baseline_check\":\"" << decision.baseline_check
                      << "\",\"candidate_check\":\"" << decision.candidate_check
                      << "\",\"decision_latency_ms\":" << static_cast<double>(selected_ns - sample.acquired_ns) / 1e6
                      << ",\"publication_miss\":" << (publication_miss ? "true" : "false")
                      << ",\"nominal_slot\":" << timing.nominal_slot
                      << ",\"skipped_nominal_slots\":" << timing.skipped_slots
                      << ",\"acquired_offset_ns\":" << sample.acquired_ns - start
                      << ",\"scheduled_release_offset_ns\":" << next_release - start
                      << ",\"decision_offset_ns\":" << selected_ns - start
                      << ",\"release_lateness_ms\":";
            milliseconds(out, timing.scheduling_lateness_ns);
            out << ",\"nominal_slot_lateness_ms\":";
            milliseconds(out, timing.nominal_lateness_ns);
            out << ",\"nominal_grid_lateness_ms\":";
            milliseconds(out, timing.nominal_grid_lateness_ns);
            out << ",\"acquisition_interval_ms\":";
            milliseconds(out, timing.acquisition_interval_ns);
            if (split_output) {
                out << ",\"shadow_qualification_omitted\":" << (shadow_qualification_omitted ? "true" : "false");
                out << ",\"output_delivered\":" << (output_delivered ? "true" : "false")
                    << ",\"output_delivery_error\":";
                if (delivery_error) json_string(out, delivery_error);
                else out << "null";
            }
            if (phase) phase->flush_start_ns = monotonic_ns();
            out << "}\n";
            telemetry.emit();
            if (phase) phase->flush_end_ns = monotonic_ns();
            // Retire children after the decision. They cannot publish directly.
            const bool retire_candidate = candidate && !supervisor.needs_candidate();
            const bool retire_baseline = baseline && !supervisor.needs_baseline();
            if (retire_candidate || retire_baseline) {
                const auto retirement_start_ns = monotonic_ns();
                if (retire_candidate) retire(candidate, candidate_timing);
                if (retire_baseline) retire(baseline, baseline_timing);
                const auto retirement_ns = monotonic_ns() - retirement_start_ns;
                ++worker_retirement_samples;
                max_worker_retirement_ns = std::max(max_worker_retirement_ns, retirement_ns);
            }
            finish_hook();
            if (supervisor.phase() == Phase::failed) break;
            // Rebase on actual acquisition, not the old nominal release grid.
            next_release = next_release_ns(sample.acquired_ns, monotonic_ns(), contract.period_ns);
        }
        if (control) {
            stager.reset();
            finish_hook();
            control.reset();
        }
        std::uint64_t telemetry_deadline_ns = 0;
        if (split_output) ::close(output_fd);
        if (bounded_telemetry) {
            telemetry_deadline_ns = monotonic_ns() + telemetry_shutdown_grace_ns;
            telemetry.drain(telemetry_deadline_ns);
        }
        const auto missing = continuity.acquired - continuity.selected;
        out << "{\"type\":\"summary\",\"phase\":\"" << name(supervisor.phase())
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
        if (continuity.last_nominal_slot) out << *continuity.last_nominal_slot;
        else out << "null";
        out << ",\"max_scheduling_lateness_ms\":";
        milliseconds(out, continuity.max_scheduling_lateness_ns);
        out << ",\"max_nominal_slot_lateness_ms\":";
        milliseconds(out, continuity.max_nominal_slot_lateness_ns);
        out << ",\"max_nominal_grid_lateness_ms\":";
        milliseconds(out, continuity.max_nominal_grid_lateness_ns);
        out << ",\"max_acquisition_interval_ms\":";
        milliseconds(out, continuity.max_acquisition_interval_ns);
        out << ",\"max_selected_interval_ms\":";
        milliseconds(out, continuity.max_selected_interval_ns);
        out << ",\"effective_selected_rate_hz\":";
        if (continuity.selected_span_ns)
            out << (continuity.selected - 1) * 1e9 / static_cast<double>(continuity.selected_span_ns);
        else out << "null";
        out << ",\"worker_retirement_samples\":" << worker_retirement_samples
                  << ",\"max_worker_retirement_ns\":";
        if (worker_retirement_samples) out << max_worker_retirement_ns;
        else out << "null";
        if (split_output) {
            out << ",\"shadow_qualification_omissions\":" << shadow_qualification_omissions;
            out << ",\"delivered_outputs\":" << delivered_outputs
                << ",\"last_selected_sequence\":" << last_selected_sequence
                << ",\"last_delivered_sequence\":" << last_delivered_sequence
                << ",\"output_delivery_error\":";
            if (delivery_error) json_string(out, delivery_error);
            else out << "null";
        }
        if (range_input) {
            out << ",\"input_error\":";
            if (input_error) json_string(out, input_error); else out << "null";
        }
        if (bounded_telemetry) out << ",\"telemetry_records_dropped\":" << telemetry.dropped;
        out << ",\"external_delivery\":\"unmeasured\"}\n";
        telemetry.emit();
        if (bounded_telemetry) telemetry.drain(telemetry_deadline_ns);
        if (delivery_error) {
            std::ostringstream error;
            error << "shadow-demo: " << delivery_error
                  << " last_selected_sequence=" << last_selected_sequence
                  << " last_delivered_sequence=" << last_delivered_sequence << '\n';
            std::cerr << error.str();
        }
        if (telemetry.dropped) std::cerr << "shadow-demo: telemetry_records_dropped=" +
                                            std::to_string(telemetry.dropped) + "\n";
        for (unsigned i = 0; i < phase_records.size() && i < continuity.acquired; ++i)
            emit_phase_timing(phase_records[i], i + 1);
        if (phase_timing) std::clog.flush();
        if (delivery_error) return 4;
        if (input_error) {
            std::cerr << "shadow-demo: " << input_error << " expected_sequence=" << continuity.acquired + 1 << '\n';
            return 5;
        }
        return interrupted ? 130 : (supervisor.phase() == Phase::failed ? 3 : 0);
    } catch (const std::exception& error) {
        std::cerr << "shadow-demo: " << error.what() << '\n'; return 2;
    }
}
