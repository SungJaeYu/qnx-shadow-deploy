#include "shadow/process.hpp"
#include "shadow/guest_headroom.hpp"
#include "shadow/io.hpp"
#include <algorithm>
#include <cerrno>
#include <condition_variable>
#include <cstring>
#include <fcntl.h>
#include <mutex>
#include <optional>
#include <signal.h>
#include <spawn.h>
#include <stdexcept>
#include <sys/wait.h>
#include <thread>
#include <time.h>
#include <unistd.h>
#ifdef __QNXNTO__
#include <process.h>
#include <sys/neutrino.h>
#include <sys/netmgr.h>
#include <sys/procmgr.h>
#include <sys/resource.h>
#endif

extern char** environ;
namespace shadow {
namespace {
#if defined(__QNXNTO__) && defined(__x86_64__)
constexpr rlim_t guest_address_space_bytes = 16 * 1024 * 1024;

const char* guest_launcher_path() {
    const char* path = _cmdname(nullptr);
    if (!path || path[0] != '/' || access(path, X_OK) != 0)
        throw std::runtime_error("--qnx-guest-profile cannot resolve the broker executable");
    return path;
}

void require_guest_headroom() {
    const long page_size = sysconf(_SC_PAGESIZE);
    const int fd = open("/proc/vm/stats", O_RDONLY | O_CLOEXEC);
    auto headroom = GuestHeadroom::invalid;
    if (fd >= 0) {
        // QNX emits the snapshot in one read and truncates it to the requested size.
        char snapshot[4096];
        const auto count = read(fd, snapshot, sizeof(snapshot));
        close(fd);
        if (page_size > 0 && count > 0 && count < static_cast<ssize_t>(sizeof(snapshot))) {
            headroom = classify_guest_headroom(
                std::string_view(snapshot, static_cast<std::size_t>(count)),
                static_cast<std::uint64_t>(page_size));
        }
    }
    if (headroom == GuestHeadroom::invalid)
        throw std::runtime_error("QNX guest candidate admission: invalid VM counters");
    if (headroom == GuestHeadroom::insufficient)
        throw std::runtime_error("QNX guest candidate admission: insufficient memory headroom");
}
#endif

struct Pipe {
    int fd[2]{-1, -1};
    Pipe() {
        if (pipe(fd) < 0) throw std::runtime_error("pipe failed");
        if (fcntl(fd[0], F_SETFD, FD_CLOEXEC) < 0 || fcntl(fd[1], F_SETFD, FD_CLOEXEC) < 0) {
            close(fd[0]); close(fd[1]); throw std::runtime_error("fcntl failed");
        }
    }
    ~Pipe() { for (int f : fd) if (f >= 0) close(f); }
    int release(int index) { int f = fd[index]; fd[index] = -1; return f; }
};
struct SpawnActions {
    posix_spawn_file_actions_t value{};
    SpawnActions() { if (posix_spawn_file_actions_init(&value)) throw std::runtime_error("spawn actions init failed"); }
    ~SpawnActions() { posix_spawn_file_actions_destroy(&value); }
    void dup(int from, int to) {
        if (posix_spawn_file_actions_adddup2(&value, from, to)) throw std::runtime_error("spawn dup action failed");
    }
    void close_fd(int fd) {
        if (posix_spawn_file_actions_addclose(&value, fd)) throw std::runtime_error("spawn close action failed");
    }
};
#ifndef __QNXNTO__
TransportStatus status(IoResult r) {
    if (r == IoResult::ok) return TransportStatus::ok;
    return r == IoResult::timeout ? TransportStatus::timeout : TransportStatus::disconnected;
}
#endif
} // namespace
struct WorkerProcess::Impl {
    pid_t child = -1;
    int input = -1, output = -1;
#ifdef __QNXNTO__
    int connection = -1;
#endif
    struct Task {
        Sample sample;
        std::uint64_t deadline;
        std::promise<Observation> promise;
        WorkerExchangeTiming* timing;
    };
    std::mutex mutex;
    std::condition_variable cv;
    std::optional<Task> task;
    bool stopping = false, busy = false, usable = true;
    std::thread dispatch;

    ~Impl() {
        { std::lock_guard<std::mutex> lock(mutex); stopping = true; }
        cv.notify_one();
        if (dispatch.joinable()) dispatch.join();
#ifdef __QNXNTO__
        if (connection >= 0) ConnectDetach(connection);
#endif
        if (input >= 0) close(input);
        if (output >= 0) close(output);
        if (child > 0) {
            // These are exclusively owned demo children. Never signal arbitrary PIDs.
            kill(child, SIGKILL);
            while (waitpid(child, nullptr, 0) < 0 && errno == EINTR) {}
        }
    }
    void require_idle() const {
        if (busy || stopping) throw std::logic_error("Worker already has an outstanding sample");
    }
    Observation exchange(const Sample& sample, std::uint64_t deadline, WorkerExchangeTiming* timing) {
        Observation o;
        if (!usable) { o.completed_ns = monotonic_ns(); return o; }
        const auto checked_ns = monotonic_ns();
        if (checked_ns >= deadline) {
            if (timing) timing->pre_expiry_ns = checked_ns;
            o.status = TransportStatus::timeout;
        } else {
#ifdef __QNXNTO__
            NativeEstimateReply receive{};
            // This boundary includes arming the timeout when MsgSend is entered.
            const auto ipc_start_ns = timing ? monotonic_ns() : 0;
            // No intervening kernel call may clear the timeout before MsgSend.
            // ChannelCreate(0) in the worker intentionally omits _NTO_CHF_UNBLOCK.
            if (TimerTimeout(CLOCK_MONOTONIC,
                             _NTO_TIMEOUT_SEND | _NTO_TIMEOUT_REPLY | TIMER_ABSTIME,
                             nullptr, &deadline, nullptr) == -1) {
                o.status = TransportStatus::disconnected;
            } else {
                const long result = MsgSend(connection, &sample, sizeof(sample), &receive, sizeof(receive));
                const int send_error = result == -1 ? errno : 0;
                if (timing) {
                    timing->ipc_start_ns = ipc_start_ns;
                    timing->ipc_end_ns = monotonic_ns();
                }
                if (result == -1) {
                    o.status = send_error == ETIMEDOUT ? TransportStatus::timeout : TransportStatus::disconnected;
                } else if (valid_native_estimate_reply(receive, result)) {
                    o.estimate = receive.reply.estimate;
                    o.status = TransportStatus::ok;
                } else {
                    o.status = TransportStatus::protocol_error;
                }
            }
#else
            if (timing) timing->ipc_start_ns = monotonic_ns();
            o.status = status(write_until(input, &sample, sizeof(sample), deadline));
            EstimateReply reply{};
            if (o.status == TransportStatus::ok) {
                o.status = status(read_until(output, &reply, sizeof(reply), deadline));
            }
            if (timing) timing->ipc_end_ns = monotonic_ns();
            if (o.status == TransportStatus::ok) {
                if (valid_estimate_reply(reply)) o.estimate = reply.estimate;
                else o.status = TransportStatus::protocol_error;
            }
#endif
        }
        o.completed_ns = monotonic_ns();
        if (o.completed_ns > deadline) o.status = TransportStatus::timeout;
        // A failed exchange may leave a partial or late response. Never reuse the connection.
        if (o.status != TransportStatus::ok) usable = false;
        return o;
    }
    void loop() {
        for (;;) {
            std::optional<Task> current;
            {
                std::unique_lock<std::mutex> lock(mutex);
                cv.wait(lock, [&] { return stopping || task.has_value(); });
                if (!task) return;
                current = std::move(task); task.reset();
            }
            try {
                auto* const timing = current->timing;
                if (timing) timing->dispatch_ns = monotonic_ns();
                const auto result = exchange(current->sample, current->deadline, timing);
                if (timing) timing->completed_ns = result.completed_ns;
                { std::lock_guard<std::mutex> lock(mutex); busy = false; }
                current->promise.set_value(result);
            } catch (...) {
                { std::lock_guard<std::mutex> lock(mutex); busy = false; }
                current->promise.set_exception(std::current_exception());
            }
        }
    }
};
void validate_qnx_guest_profile() {
#if defined(__QNXNTO__) && defined(__x86_64__)
    guest_launcher_path();
#else
    throw std::invalid_argument("--qnx-guest-profile requires the QNX x86-64 guest");
#endif
}

#if defined(__QNXNTO__) && defined(__x86_64__)
[[noreturn]] void exec_qnx_guest_candidate(char** worker_args) {
    rlimit limits{};
    if (getrlimit(RLIMIT_AS, &limits) == -1)
        throw std::runtime_error("Cannot read QNX candidate address-space limit: " + std::string(std::strerror(errno)));
    // Preserve any tighter limits inherited from the broker.
    limits.rlim_cur = std::min(limits.rlim_cur, guest_address_space_bytes);
    limits.rlim_max = std::min(limits.rlim_max, guest_address_space_bytes);
    if (setrlimit(RLIMIT_AS, &limits) == -1)
        throw std::runtime_error("Cannot set QNX candidate address-space limit: " + std::string(std::strerror(errno)));

    // Modify only this process and deny raising limits after exec in either credential domain.
    const int ability_error = procmgr_ability(0, PROCMGR_ADN_ROOT | PROCMGR_ADN_NONROOT |
        PROCMGR_AOP_DENY | PROCMGR_AOP_LOCK | PROCMGR_AOP_INHERIT_YES | PROCMGR_AID_RLIMIT,
        PROCMGR_AID_EOL);
    if (ability_error != 0)
        throw std::runtime_error("Cannot lock QNX candidate limit ability: " + std::string(std::strerror(ability_error)));

    // The candidate replaces this owned child. Its Hello and native channel belong to that PID.
    execv(worker_args[0], worker_args);
    throw std::runtime_error("Cannot execute QNX candidate: " + std::string(std::strerror(errno)));
}
#endif

WorkerProcess::WorkerProcess(const std::string& executable, const std::string& mode, unsigned fault_after,
                             WorkerLaunch launch)
    : impl_(std::make_unique<Impl>()) {
    Pipe to_child, from_child;
    SpawnActions actions;
    actions.dup(to_child.fd[0], STDIN_FILENO);
    actions.dup(from_child.fd[1], STDOUT_FILENO);
    for (int fd : {to_child.fd[0], to_child.fd[1], from_child.fd[0], from_child.fd[1]}) actions.close_fd(fd);
    std::string count = std::to_string(fault_after);
    char* args[]{const_cast<char*>(executable.c_str()), const_cast<char*>(mode.c_str()), count.data(), nullptr};
    int error;
    if (launch == WorkerLaunch::qnx_guest_candidate) {
#if defined(__QNXNTO__) && defined(__x86_64__)
        require_guest_headroom();
        const char* launcher = guest_launcher_path();
        char* guest_args[]{const_cast<char*>(launcher), const_cast<char*>(qnx_candidate_exec_option),
                          args[0], args[1], args[2], nullptr};
        error = posix_spawn(&impl_->child, launcher, &actions.value, nullptr, guest_args, environ);
#else
        throw std::invalid_argument("--qnx-guest-profile requires the QNX x86-64 guest");
#endif
    } else {
        error = posix_spawn(&impl_->child, executable.c_str(), &actions.value, nullptr, args, environ);
    }
    if (error) { impl_->child = -1; throw std::runtime_error("Cannot start worker: " + std::string(std::strerror(error))); }
    // Close child ends before reading the handshake so an early exit produces EOF.
    close(to_child.release(0)); close(from_child.release(1));
    impl_->input = to_child.release(1); impl_->output = from_child.release(0);
    nonblocking(impl_->input); nonblocking(impl_->output);
    Hello hello{};
    if (read_until(impl_->output, &hello, sizeof(hello), monotonic_ns() + 3000000000ULL) != IoResult::ok ||
        !valid_header(hello.header, Kind::hello) || hello.reserved != 0) throw std::runtime_error("Worker handshake failed");
#ifdef __QNXNTO__
    if (hello.channel < 0) throw std::runtime_error("Worker has no QNX channel");
    impl_->connection = ConnectAttach(ND_LOCAL_NODE, impl_->child, hello.channel, _NTO_SIDE_CHANNEL, 0);
    if (impl_->connection == -1) {
        const int attach_error = errno;
        throw std::runtime_error("ConnectAttach failed for worker " + std::to_string(impl_->child) +
                                 ", channel " + std::to_string(hello.channel) + ": " +
                                 std::strerror(attach_error) + " (errno " + std::to_string(attach_error) + ")");
    }
#else
    if (hello.channel != -1) throw std::runtime_error("Unexpected worker transport");
#endif
    // Impl joins dispatch before its members are destroyed.
    auto* const implementation = impl_.get();
    implementation->dispatch = std::thread([implementation] { implementation->loop(); });
}
WorkerProcess::~WorkerProcess() = default;
std::future<Observation> WorkerProcess::submit(Sample sample, std::uint64_t deadline,
                                               WorkerExchangeTiming* timing) {
    if (timing) timing->submit_ns = monotonic_ns();
    std::lock_guard<std::mutex> lock(impl_->mutex);
    impl_->require_idle();
    Impl::Task task{sample, deadline, {}, timing};
    auto future = task.promise.get_future();
    impl_->busy = true; impl_->task = std::move(task); impl_->cv.notify_one();
    return future;
}
#ifdef __QNXNTO__
Observation WorkerProcess::exchange_direct(const Sample& sample, std::uint64_t deadline,
                                           WorkerExchangeTiming* timing) {
    {
        std::lock_guard<std::mutex> lock(impl_->mutex);
        impl_->require_idle();
        impl_->busy = true;
    }
    struct Release {
        Impl& impl;
        ~Release() {
            std::lock_guard<std::mutex> lock(impl.mutex);
            impl.busy = false;
        }
    } release{*impl_};
    if (timing) timing->direct_start_ns = monotonic_ns();
    const auto result = impl_->exchange(sample, deadline, timing);
    if (timing) timing->completed_ns = result.completed_ns;
    return result;
}
#endif
int WorkerProcess::pid() const { return impl_->child; }
const char* WorkerProcess::backend() {
#ifdef __QNXNTO__
    return "qnx-native-ipc";
#else
    return "host-posix-pipes";
#endif
}
} // namespace shadow
