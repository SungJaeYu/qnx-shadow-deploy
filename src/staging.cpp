#include "shadow/staging.hpp"
#include <algorithm>
#include "shadow/io.hpp"
#include <cerrno>
#include <condition_variable>
#include <deque>
#include <fcntl.h>
#include <map>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>

namespace shadow {
const char* native_architecture() {
#if defined(__aarch64__) || defined(__arm64__)
    return "aarch64";
#elif defined(__x86_64__)
    return "x86_64";
#else
    return "unsupported";
#endif
}
const char* native_os() {
#ifdef __QNXNTO__
    return "qnx";
#elif defined(__APPLE__)
    return "darwin";
#elif defined(__linux__)
    return "linux";
#else
    return "unsupported";
#endif
}
CandidateManifest read_manifest(const std::string& path) {
    // Do not open a FIFO or wait on a device masquerading as a local manifest.
    const int fd = open(path.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) throw std::runtime_error("manifest_open_failed");
    struct Close { int fd; ~Close() { close(fd); } } guard{fd};
    struct stat info{};
    if (fstat(fd, &info) || !S_ISREG(info.st_mode) || info.st_size <= 0 || info.st_size > 4096)
        throw std::runtime_error("manifest_not_bounded_regular_file");
    std::string content;
    char buffer[512];
    for (;;) {
        const auto count = read(fd, buffer, sizeof(buffer));
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) throw std::runtime_error("manifest_read_failed");
        if (!count) break;
        content.append(buffer, static_cast<std::size_t>(count));
        if (content.size() > 4096) throw std::runtime_error("manifest_too_large");
    }
    std::map<std::string, std::string> fields;
    std::istringstream stream(content);
    std::string line;
    while (std::getline(stream, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty() || line[0] == '#') continue;
        const auto equal = line.find('=');
        if (equal == std::string::npos || !equal || equal + 1 == line.size() ||
            !fields.emplace(line.substr(0, equal), line.substr(equal + 1)).second)
            throw std::runtime_error("manifest_invalid_or_duplicate_field");
    }
    const std::vector<std::string> keys{"format", "artifact", "executable", "protocol", "architecture",
        "os", "contract", "replay_samples", "mode", "fault_after"};
    if (fields.size() != keys.size()) throw std::runtime_error("manifest_field_set_mismatch");
    for (const auto& key : keys) if (!fields.count(key)) throw std::runtime_error("manifest_field_set_mismatch");
    if (fields.at("format") != "1" || fields.at("protocol") != std::to_string(protocol_version))
        throw std::runtime_error("manifest_protocol_mismatch");
    if (fields.at("architecture") != native_architecture() || fields.at("os") != native_os())
        throw std::runtime_error("manifest_target_mismatch");
    if (fields.at("contract") != "position-m-velocity-mps-v1")
        throw std::runtime_error("manifest_contract_mismatch");
    auto number = [&](const std::string& key, unsigned maximum) {
        const auto& text = fields.at(key);
        if (text.find_first_not_of("0123456789") != std::string::npos)
            throw std::runtime_error("manifest_invalid_number");
        unsigned long result = 0;
        try { result = std::stoul(text); } catch (...) { throw std::runtime_error("manifest_invalid_number"); }
        if (result > maximum) throw std::runtime_error("manifest_invalid_number");
        return static_cast<unsigned>(result);
    };
    CandidateManifest result{fields.at("artifact"), fields.at("executable"), fields.at("mode"),
        number("replay_samples", history_capacity), number("fault_after", 1000000)};
    if (!result.replay_samples) throw std::runtime_error("manifest_invalid_replay_window");
    if (result.artifact.size() > 64 || result.artifact.find_first_not_of(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.") != std::string::npos)
        throw std::runtime_error("manifest_invalid_artifact_id");
    if (result.executable.find('\0') != std::string::npos)
        throw std::runtime_error("manifest_invalid_executable");
    if (result.executable[0] != '/') {
        const auto slash = path.find_last_of('/');
        result.executable = (slash == std::string::npos ? "./" : path.substr(0, slash + 1)) + result.executable;
    }
    if (stat(result.executable.c_str(), &info) || !S_ISREG(info.st_mode) || access(result.executable.c_str(), X_OK))
        throw std::runtime_error("manifest_executable_unavailable");
    const std::vector<std::string> modes{"good", "baseline", "bad-units", "delay", "crash", "nan", "stale", "bad-header", "offset"};
    bool known = false;
    for (const auto& mode : modes) if (result.mode == mode) known = true;
    if (!known) throw std::runtime_error("manifest_unknown_worker_mode");
    return result;
}

struct CandidateStager::Impl {
    mutable std::mutex mutex;
    std::condition_variable cv;
    std::deque<Sample> queue;
    std::unique_ptr<WorkerProcess> worker;
    std::optional<Estimate> previous;
    StagingStatus state;
    bool stopping = false, busy = true;
    std::uint64_t enqueued = 0;
    std::thread thread;

    void run(const std::string& path, const std::vector<Sample>& history, const Contract& contract,
             WorkerLaunch launch) {
        try {
            const auto start = monotonic_ns();
            const auto manifest = read_manifest(path);
            if (history.size() < manifest.replay_samples) throw std::runtime_error("insufficient_replay_history");
            auto started = std::make_unique<WorkerProcess>(manifest.executable, manifest.mode,
                                                           manifest.fault_after, launch);
            {
                std::lock_guard<std::mutex> lock(mutex);
                state.pid = started->pid(); state.artifact = manifest.artifact;
                worker = std::move(started);
                // Live samples may have arrived while startup was in progress.
                queue.insert(queue.begin(), history.end() - manifest.replay_samples, history.end());
                if (queue.size() > history_capacity) { state.error = "replay_backlog_overflow"; stopping = true; }
                busy = false;
            }
            PositionResidualMonitor residual;
            for (;;) {
                Sample sample;
                {
                    std::unique_lock<std::mutex> lock(mutex);
                    cv.wait(lock, [&] { return stopping || !queue.empty(); });
                    if (stopping) break;
                    sample = queue.front(); queue.pop_front(); busy = true;
                }
                if (monotonic_ns() - start > 5000000000ULL) throw std::runtime_error("state_preparation_deadline_exceeded");
                const auto receipt_origin = monotonic_ns();
                const auto observation = worker->submit(sample, receipt_origin + contract.deadline_ns * 3 / 4).get();
                // Keep the original sequence/time for state evolution. Only transport timing
                // starts now; historical samples never count as fresh qualification output.
                const auto reason = check_observation(sample, observation, previous, contract, receipt_origin);
                if (reason != "ok") throw std::runtime_error("replay_" + reason);
                const auto interval_ns = previous ?
                    std::max(contract.period_ns, sample.acquired_ns - previous->acquired_ns) : contract.period_ns;
                if (!residual.accepts(sample, observation.estimate, interval_ns))
                    throw std::runtime_error("replay_measurement_residual_exceeded");
                {
                    std::lock_guard<std::mutex> lock(mutex);
                    previous = observation.estimate; ++state.replayed;
                    state.through_sequence = sample.sequence; busy = false;
                }
            }
        } catch (const std::exception& error) {
            std::lock_guard<std::mutex> lock(mutex);
            if (state.error.empty()) state.error = error.what();
            stopping = true;
        }
        // Cleanup can wait for an exchange or startup timeout, never on the broker's loop.
        worker.reset();
        std::lock_guard<std::mutex> lock(mutex);
        state.done = true;
    }
};
CandidateStager::CandidateStager(std::string manifest, std::vector<Sample> history, Contract contract,
                                 WorkerLaunch launch)
    : impl_(std::make_unique<Impl>()) {
    if (!history.empty()) impl_->enqueued = history.back().sequence;
    impl_->thread = std::thread([this, manifest = std::move(manifest), history = std::move(history), contract, launch] {
        impl_->run(manifest, history, contract, launch);
    });
}
CandidateStager::~CandidateStager() {
    cancel();
    if (impl_->thread.joinable()) impl_->thread.join();
}
void CandidateStager::enqueue(const Sample& sample) {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if (impl_->stopping) return;
    if (sample.sequence != impl_->enqueued + 1 || impl_->queue.size() >= history_capacity) {
        impl_->state.error = sample.sequence != impl_->enqueued + 1 ? "replay_input_gap" : "replay_backlog_overflow";
        impl_->stopping = true;
    } else { impl_->queue.push_back(sample); impl_->enqueued = sample.sequence; }
    impl_->cv.notify_one();
}
void CandidateStager::cancel() {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    impl_->stopping = true; impl_->cv.notify_one();
}
StagingStatus CandidateStager::status() const {
    std::lock_guard<std::mutex> lock(impl_->mutex); return impl_->state;
}
PreparedCandidate CandidateStager::take_if_ready(std::uint64_t through_sequence) {
    std::lock_guard<std::mutex> lock(impl_->mutex);
    if (impl_->stopping || impl_->busy || !impl_->queue.empty() || !impl_->worker || !impl_->previous ||
        impl_->state.through_sequence != through_sequence) return {};
    impl_->stopping = true; impl_->cv.notify_one();
    return {std::move(impl_->worker), *impl_->previous};
}
} // namespace shadow
