#include "shadow/staging.hpp"
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <signal.h>
#include <unistd.h>

using namespace shadow;
#define CHECK(expr) do { if (!(expr)) throw std::runtime_error(#expr); } while (false)
#ifdef __QNXNTO__
void check_native_direct_exchange(const std::string& worker_path) {
    constexpr std::uint64_t budget_ns = 1'000'000'000;
    {
        WorkerProcess worker(worker_path, "baseline", 0);
        Sample sample;
        sample.sequence = 1;
        sample.acquired_ns = monotonic_ns();
        sample.position_m = 10;
        CHECK(worker.submit(sample, sample.acquired_ns + budget_ns).get().status == TransportStatus::ok);

        ++sample.sequence;
        sample.acquired_ns = monotonic_ns();
        WorkerExchangeTiming timing;
        const auto reply = worker.exchange_direct(sample, sample.acquired_ns + budget_ns, &timing);
        CHECK(reply.status == TransportStatus::ok && reply.estimate.sequence == sample.sequence);
        CHECK(reply.estimate.acquired_ns == sample.acquired_ns);
        CHECK(!timing.submit_ns && !timing.dispatch_ns && !timing.pre_expiry_ns);
        CHECK(sample.acquired_ns <= timing.direct_start_ns && timing.direct_start_ns <= timing.ipc_start_ns);
        CHECK(timing.ipc_start_ns <= timing.ipc_end_ns && timing.ipc_end_ns <= timing.completed_ns);
        CHECK(timing.completed_ns == reply.completed_ns);

        WorkerExchangeTiming expired;
        CHECK(worker.exchange_direct(sample, monotonic_ns(), &expired).status == TransportStatus::timeout);
        CHECK(expired.direct_start_ns <= expired.pre_expiry_ns && expired.pre_expiry_ns <= expired.completed_ns);
        CHECK(expired.pre_expiry_ns && !expired.ipc_start_ns && !expired.ipc_end_ns);
        CHECK(worker.submit(sample, monotonic_ns() + budget_ns).get().status == TransportStatus::unavailable);
    }
    {
        WorkerProcess worker(worker_path, "bad-header", 0);
        Sample sample;
        sample.sequence = 1;
        sample.acquired_ns = monotonic_ns();
        sample.position_m = 10;
        CHECK(worker.exchange_direct(sample, sample.acquired_ns + budget_ns).status == TransportStatus::protocol_error);
        CHECK(worker.exchange_direct(sample, monotonic_ns() + budget_ns).status == TransportStatus::unavailable);
    }
    {
        WorkerProcess worker(worker_path, "delay", 0);
        Sample sample;
        sample.sequence = 1;
        sample.acquired_ns = monotonic_ns();
        sample.position_m = 10;
        const auto deadline = sample.acquired_ns + 100'000'000;
        WorkerExchangeTiming timing;
        const auto reply = worker.exchange_direct(sample, deadline, &timing);
        CHECK(reply.status == TransportStatus::timeout && reply.completed_ns >= deadline);
        CHECK(timing.ipc_start_ns && timing.ipc_end_ns);
        CHECK(worker.submit(sample, monotonic_ns() + budget_ns).get().status == TransportStatus::unavailable);
    }
    std::cout << "PASS native direct exchange, pre-expiry, protocol rejection and IPC timeout\n";
}
#endif

int main(int argc, char** argv) {
    // When spawned as a worker, deliberately never complete the hello handshake.
    // This process creates no descendants and is reaped by WorkerProcess.
    if (argc == 3) { std::this_thread::sleep_for(std::chrono::seconds(6)); return 0; }
    if (argc > 2) return 1;
    char directory[] = "/tmp/shadow-staging-XXXXXX";
    if (!mkdtemp(directory)) return 1;
    const std::string path = std::string(directory) + "/candidate.manifest";
    struct Cleanup {
        std::string file, directory;
        ~Cleanup() { unlink(file.c_str()); rmdir(directory.c_str()); }
    } cleanup{path, directory};
    try {
        char* resolved = realpath(argv[0], nullptr);
        if (!resolved) throw std::runtime_error("Cannot resolve test worker path");
        const std::string executable = resolved;
        free(resolved);
        const std::string worker_path = argc == 2 ? argv[1] :
            executable.substr(0, executable.find_last_of('/') + 1) + "shadow-worker";
#ifdef __QNXNTO__
        check_native_direct_exchange(worker_path);
#endif
        for (unsigned iteration = 0; iteration < 64; ++iteration) {
            int pid;
            {
                WorkerProcess worker(worker_path, "baseline", 0);
                pid = worker.pid();
            }
            CHECK(kill(pid, 0) == -1 && errno == ESRCH);
        }
        const std::string valid = "format=1\nartifact=test-v2\nexecutable=" + executable +
            "\nprotocol=2\narchitecture=" + native_architecture() + "\nos=" + native_os() +
            "\ncontract=position-m-velocity-mps-v1\nreplay_samples=1\nmode=good\nfault_after=0\n";
        auto save = [&](const std::string& text) { std::ofstream file(path); file << text; };
        auto reject = [&](std::string text, const std::string& error) {
            save(text); bool rejected = false;
            try { read_manifest(path); }
            catch (const std::runtime_error& e) { rejected = true; CHECK(e.what() == error); }
            CHECK(rejected);
        };
        auto replace = [&](const std::string& from, const std::string& to) {
            auto text = valid; text.replace(text.find(from), from.size(), to); return text;
        };
        save(valid); CHECK(read_manifest(path).replay_samples == 1);
        reject(valid + "protocol=2\n", "manifest_invalid_or_duplicate_field");
        reject(valid + "unknown=1\n", "manifest_field_set_mismatch");
        reject(replace("protocol=2", "protocol=1"), "manifest_protocol_mismatch");
        reject(replace("architecture=" + std::string(native_architecture()), "architecture=other"), "manifest_target_mismatch");
        reject(replace("contract=position-m-velocity-mps-v1", "contract=millimeters"), "manifest_contract_mismatch");
        reject(replace("replay_samples=1", "replay_samples=65"), "manifest_invalid_number");
        reject(replace("replay_samples=1", "replay_samples=0"), "manifest_invalid_replay_window");
        reject(replace("fault_after=0", "fault_after=-1"), "manifest_invalid_number");
        reject(std::string(4097, 'x'), "manifest_not_bounded_regular_file");
        save(valid);
        Sample initial; initial.sequence = 1; initial.acquired_ns = 100000000; initial.position_m = 10;
        CandidateStager stager(path, {initial}, Contract{});
        // Producer outruns the blocked startup deterministically. No input may be
        // silently evicted to let an incompletely prepared candidate become ready.
        for (unsigned i = 2; i <= history_capacity + 2; ++i) {
            auto sample = initial; sample.sequence = i; sample.acquired_ns *= i; stager.enqueue(sample);
        }
        CHECK(stager.status().error == "replay_backlog_overflow");
        CHECK(!stager.take_if_ready(history_capacity + 2).worker);
        const auto end = std::chrono::steady_clock::now() + std::chrono::seconds(4);
        while (!stager.status().done && std::chrono::steady_clock::now() < end)
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
        CHECK(stager.status().done);
        CHECK(stager.status().error == "replay_backlog_overflow");
        std::cout << "PASS immediate worker retirement, manifest admission and bounded replay overflow\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL " << error.what() << '\n'; return 1;
    }
}
