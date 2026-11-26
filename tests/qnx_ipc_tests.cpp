#include "qnx_ipc_fixture.hpp"
#include "shadow/process.hpp"

#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <spawn.h>
#include <signal.h>
#include <sys/neutrino.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <thread>
#include <vector>

extern char** environ;

namespace {
using namespace fixture;
using shadow::TransportStatus;
using namespace std::chrono_literals;
constexpr int attempts = 2;
constexpr int attempt_watchdog_seconds = 12;

enum class Action { release, exit_receiver, timeout };
struct Case {
    const char* name;
    const char* state;
    Action action;
    bool receive_first;
};
constexpr Case cases[]{
    {"send-release", "SEND", Action::release, false},
    {"send-exit", "SEND", Action::exit_receiver, false},
    {"send-timeout", "SEND", Action::timeout, false},
    {"reply-release", "REPLY", Action::release, true},
    {"reply-exit", "REPLY", Action::exit_receiver, true},
    {"reply-timeout-late", "REPLY", Action::timeout, true},
};

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

struct FifoPaths {
    const std::string command;
    const std::string event;

    explicit FifoPaths(const std::string& dir)
        : command(dir + "/commands"), event(dir + "/events") {
        require(mkfifo(command.c_str(), 0600) == 0, "create command FIFO failed");
        if (mkfifo(event.c_str(), 0600) != 0) {
            unlink(command.c_str());
            throw std::runtime_error("create event FIFO failed");
        }
    }
    ~FifoPaths() {
        unlink(event.c_str());
        unlink(command.c_str());
    }
};

class Control {
public:
    explicit Control(const std::string& dir) : paths_(dir),
        command_anchor_(paths_.command, O_RDONLY | O_NONBLOCK),
        commands_(paths_.command, O_WRONLY | O_NONBLOCK),
        events_(paths_.event, O_RDONLY | O_NONBLOCK),
        event_anchor_(paths_.event, O_WRONLY | O_NONBLOCK) {}

    void gate(char command) {
        std::cout << "gate command=" << command << " requested_ns=" << shadow::monotonic_ns() << std::endl;
        send(commands_.get(), command);
        std::cout << "gate_written command=" << command << " written_ns=" << shadow::monotonic_ns() << std::endl;
    }

    Event next_event() {
        const auto event = receive<Event>(events_.get());
        log_event(std::cout, event);
        return event;
    }

    Event event(EventKind expected) {
        const auto event = next_event();
        require(event.kind == expected, "unexpected receiver event");
        return event;
    }

private:
    FifoPaths paths_;
    Fd command_anchor_;
    Fd commands_;
    Fd events_;
    Fd event_anchor_;
};

struct Connections {
    int descriptors = 0;
    std::vector<int> side_channels;
    std::vector<int> receiver_channels;
};

Connections connections(const char* phase, int receiver, int channel) {
    Connections result;
    for (int next = 0;;) {
        struct _server_info server{};
        const int coid = ConnectServerInfo(0, next, &server);
        if (coid < 0) {
            require(errno == EINVAL, "connection enumeration failed");
            break;
        }
        if (coid < static_cast<int>(_NTO_SIDE_CHANNEL)) {
            ++result.descriptors;
        } else {
            result.side_channels.push_back(coid);
            if (server.pid == receiver && server.chid == channel)
                result.receiver_channels.push_back(coid);
        }
        std::cout << "connection phase=" << phase << " coid=" << coid << " pid=" << server.pid
                  << " channel=" << server.chid << " flags=" << server.flags << std::endl;
        next = coid + 1;
    }
    std::cout << "descriptor_count phase=" << phase << " count=" << result.descriptors << std::endl;
    return result;
}

void pidin(const std::string& dir, const std::string& file, char* format) {
    const auto start = shadow::monotonic_ns();
    auto process = std::to_string(getpid());
    const auto path = dir + "/" + file;
    Fd output(path, O_WRONLY | O_CREAT | O_EXCL);
    Fd errors(path + ".stderr", O_WRONLY | O_CREAT | O_EXCL);
    char utility[] = "pidin";
    char pid_flag[] = "-p";
    char format_flag[] = "-f";
    char* args[]{utility, pid_flag, process.data(), format_flag, format, nullptr};
    posix_spawn_file_actions_t actions;
    require(posix_spawn_file_actions_init(&actions) == 0, "pidin actions init failed");
    pid_t child = -1;
    int error = posix_spawn_file_actions_adddup2(&actions, output.get(), STDOUT_FILENO);
    if (!error) error = posix_spawn_file_actions_adddup2(&actions, errors.get(), STDERR_FILENO);
    if (!error) error = posix_spawnp(&child, utility, &actions, nullptr, args, environ);
    posix_spawn_file_actions_destroy(&actions);
    require(error == 0, "cannot spawn pidin");
    int status = 0;
    pid_t waited;
    do {
        waited = waitpid(child, &status, 0);
    } while (waited < 0 && errno == EINTR);
    require(waited == child, "wait for pidin failed");
    std::cout << "snapshot file=" << file << " start_ns=" << start
              << " end_ns=" << shadow::monotonic_ns() << " wait_status=" << status << std::endl;
    require(WIFEXITED(status) && WEXITSTATUS(status) == 0, "pidin failed");
}

bool observe(const std::string& dir, const Case& test, int receiver, std::uint64_t deadline) {
    char format[] = "abJB";
    for (unsigned i = 0; i < 32 && shadow::monotonic_ns() < deadline; ++i) {
        const auto file = "state-" + std::to_string(i) + ".txt";
        pidin(dir, file, format);
        std::ifstream snapshot(dir + "/" + file);
        require(snapshot.good(), "cannot read pidin snapshot");
        std::string line;
        while (std::getline(snapshot, line)) {
            int pid, tid, blocked;
            std::string state;
            std::istringstream row(line);
            if (!(row >> pid >> tid >> state >> blocked)) continue;
            row >> std::ws;
            const auto accepted = shadow::monotonic_ns();
            if (row.eof() && pid == getpid() && tid > 1 && blocked == receiver &&
                state == test.state && accepted < deadline) {
                std::cout << "state_observed sender_pid=" << pid << " sender_tid=" << tid
                          << " receiver_pid=" << blocked << " state=" << state
                          << " accepted_ns=" << accepted << std::endl;
                return true;
            }
        }
    }
    std::cout << "state_unobserved expected=" << test.state << " receiver_pid=" << receiver << std::endl;
    return false;
}

const char* status_name(TransportStatus status) {
    switch (status) {
        case TransportStatus::ok: return "ok";
        case TransportStatus::timeout: return "timeout";
        case TransportStatus::disconnected: return "disconnected";
        case TransportStatus::protocol_error: return "protocol_error";
        case TransportStatus::unavailable: return "unavailable";
    }
    return "unknown";
}

struct Exchange {
    shadow::Sample sample{};
    std::uint64_t deadline;
    std::uint64_t submit_started_ns;
    std::uint64_t submit_ended_ns;
    std::future<shadow::Observation> pending;

    Exchange(shadow::WorkerProcess& worker, std::uint64_t sequence, std::uint64_t budget_ns) {
        sample.sequence = sequence;
        sample.acquired_ns = shadow::monotonic_ns();
        sample.position_m = static_cast<double>(sequence);
        deadline = sample.acquired_ns + budget_ns;
        submit_started_ns = shadow::monotonic_ns();
        pending = worker.submit(sample, deadline);
        submit_ended_ns = shadow::monotonic_ns();
        std::cout << "submit sequence=" << sequence << " acquired_ns=" << sample.acquired_ns
                  << " deadline_ns=" << deadline << " started_ns=" << submit_started_ns
                  << " ended_ns=" << submit_ended_ns << std::endl;
    }

    shadow::Observation result() {
        require(pending.wait_for(2s) == std::future_status::ready, "exchange future exceeded fixture bound");
        const auto out = pending.get();
        std::cout << "result sequence=" << sample.sequence << " status=" << status_name(out.status)
                  << " completed_ns=" << out.completed_ns << " estimate_sequence=" << out.estimate.sequence
                  << " estimate_acquired_ns=" << out.estimate.acquired_ns << std::endl;
        return out;
    }

    void check_sample(const Event& event) const {
        require(event.kind == EventKind::sample && event.result > 0 &&
                event.sequence == sample.sequence && event.acquired_ns == sample.acquired_ns,
                "receiver got unexpected sample");
    }

    void check_release(Control& control) {
        const auto out = result();
        const auto reply = control.event(EventKind::reply);
        require(reply.sequence == sample.sequence && reply.acquired_ns == sample.acquired_ns &&
                reply.result == 0 && out.status == TransportStatus::ok && out.completed_ns <= deadline &&
                out.estimate.sequence == sample.sequence && out.estimate.acquired_ns == sample.acquired_ns &&
                out.estimate.position_m == sample.position_m, "released exchange did not return the matching estimate");
    }
};

bool run_exchange(const std::string& dir, const Case& test, Control& control,
                  shadow::WorkerProcess& worker, std::uint64_t budget_ns) {
    const bool wait_for_timeout = test.action == Action::timeout;
    if (test.receive_first) control.gate('R');
    Exchange exchange(worker, 1, budget_ns);
    const bool observed = observe(dir, test, worker.pid(), exchange.deadline);
    bool sample_received = observed && test.receive_first;
    if (sample_received) exchange.check_sample(control.event(EventKind::sample));

    if (observed && test.action == Action::release && shadow::monotonic_ns() < exchange.deadline) {
        if (!test.receive_first) {
            control.gate('R');
            exchange.check_sample(control.event(EventKind::sample));
        }
        control.gate('Y');
        exchange.check_release(control);
        return true;
    }

    bool receiver_killed = false;
    if (observed && test.action == Action::exit_receiver && shadow::monotonic_ns() < exchange.deadline) {
        const auto signal_ns = shadow::monotonic_ns();
        const int result = kill(worker.pid(), SIGKILL);
        const int error = result < 0 ? errno : 0;
        std::cout << "terminate pid=" << worker.pid() << " signal_ns=" << signal_ns
                  << " result=" << result << " errno=" << error << std::endl;
        require(result == 0, "could not terminate owned receiver");
        receiver_killed = true;
    }

    const auto out = exchange.result();
    bool conclusive = observed && wait_for_timeout;
    if (receiver_killed && out.status == TransportStatus::disconnected && out.completed_ns <= exchange.deadline) {
        conclusive = true;
    } else {
        require(out.status == TransportStatus::timeout && out.completed_ns >= exchange.deadline,
                "exchange did not time out at the requested gate");
    }

    if (!receiver_killed && test.receive_first && !sample_received) {
        // A missed state snapshot does not imply the receiver saw no sample.
        // Read the outcome of the already-open gate before destroying it.
        const auto event = control.next_event();
        if (event.kind == EventKind::sample) {
            exchange.check_sample(event);
            sample_received = true;
        } else {
            require(event.kind == EventKind::receive_timeout, "first receive did not end cleanly");
        }
    }
    if (!receiver_killed && sample_received) {
        control.gate('Y');
        const auto reply = control.event(EventKind::reply);
        require(reply.sequence == exchange.sample.sequence &&
                reply.acquired_ns == exchange.sample.acquired_ns &&
                reply.timestamp_ns >= out.completed_ns, "late reply event did not match the timed-out sample");
    }
    if (!receiver_killed) {
        control.gate('P');
        control.event(EventKind::probe_ready);
        std::this_thread::sleep_for(1ms);
    }
    Exchange probe(worker, 2, 2 * probe_ns);
    const auto after = probe.result();
    require(after.status == TransportStatus::unavailable && after.completed_ns < probe.deadline,
            "failed worker did not return unavailable within a fresh deadline");
    if (!receiver_killed) {
        const auto idle = control.event(EventKind::receive_timeout);
        require(idle.receive_started_ns <= probe.submit_started_ns &&
                probe.submit_ended_ns < idle.timestamp_ns &&
                after.completed_ns <= idle.timestamp_ns,
                "receive probe did not cover the second submission and completion");
    }
    return conclusive;
}

void run_recovery(Control& control, shadow::WorkerProcess& worker) {
    control.gate('R');
    Exchange exchange(worker, 3, second_ns);
    exchange.check_sample(control.event(EventKind::sample));
    control.gate('Y');
    exchange.check_release(control);
}

template <typename Operation>
bool use_worker(const std::string& receiver, const std::string& dir, Operation operation) {
    char fds[] = "ao";
    pidin(dir, "fds-before.txt", fds);
    const auto before = connections("before", -1, -1);
    int child = -1;
    int channel = -1;
    int receiver_coid = -1;
    bool verdict = false;
    std::exception_ptr failure;
    {
        Control control(dir);
        {
            shadow::WorkerProcess worker(receiver, dir, 0);
            child = worker.pid();
            try {
                const auto ready = control.event(EventKind::ready);
                channel = static_cast<int>(ready.result);
                std::cout << "worker_ready sender_pid=" << getpid() << " receiver_pid=" << child
                          << " channel=" << channel << std::endl;
                const auto attached = connections("attached", child, channel);
                require(attached.receiver_channels.size() == 1, "receiver connection was not uniquely identified");
                receiver_coid = attached.receiver_channels.front();
                for (int coid : before.side_channels)
                    require(coid != receiver_coid, "receiver coid already existed before setup");
                pidin(dir, "fds-attached.txt", fds);
                verdict = operation(control, worker);
            } catch (...) {
                failure = std::current_exception();
            }
        }
    }
    const auto waited = waitpid(child, nullptr, WNOHANG);
    const int wait_error = waited < 0 ? errno : 0;
    const int present = kill(child, 0);
    const int process_error = present < 0 ? errno : 0;
    const auto after = connections("after", child, channel);
    pidin(dir, "fds-after.txt", fds);
    std::cout << "cleanup receiver_pid=" << child << " waitpid=" << waited
              << " wait_errno=" << wait_error << " kill_zero=" << present
              << " kill_errno=" << process_error << std::endl;
    require(waited == -1 && wait_error == ECHILD && present == -1 && process_error == ESRCH,
            "receiver survived destruction or was not reaped");
    require(after.descriptors == before.descriptors, "fixture descriptor count did not return to baseline");
    for (int coid : after.side_channels)
        require(coid != receiver_coid, "owned receiver coid remained after destruction");
    require(after.receiver_channels.empty(), "receiver connection remained after destruction");
    if (failure) std::rethrow_exception(failure);
    return verdict;
}

int attempt(const std::string& receiver, const std::string& dir, const Case& test, std::uint64_t budget_ns) {
    try {
        const bool verdict = use_worker(receiver, dir, [&](Control& control, shadow::WorkerProcess& worker) {
            return run_exchange(dir, test, control, worker, budget_ns);
        });
        const auto recovery = dir + "/recovery";
        require(mkdir(recovery.c_str(), 0700) == 0, "create recovery directory failed");
        use_worker(receiver, recovery, [](Control& control, shadow::WorkerProcess& worker) {
            run_recovery(control, worker);
            return true;
        });
        return verdict ? 0 : 77;
    } catch (const std::exception& error) {
        std::cerr << "attempt: " << error.what() << std::endl;
        return 1;
    }
}

void clean_attempt_group(pid_t child, const std::string& dir, const char* reason) {
    const int result = kill(-child, SIGKILL);
    const int error = result < 0 ? errno : 0;
    std::cout << reason << " directory=" << dir << " kill_result=" << result
              << " kill_errno=" << error << std::endl;
    if (result < 0) kill(child, SIGKILL);
    for (const auto& prefix : {dir, dir + "/recovery"}) {
        for (const auto* name : {"/commands", "/events"}) {
            const auto path = prefix + name;
            if (unlink(path.c_str()) < 0 && errno != ENOENT)
                std::cerr << "fifo_cleanup_failure path=" << path << " errno=" << errno << std::endl;
        }
    }
}

int reap_attempt(pid_t child) {
    const auto deadline = shadow::monotonic_ns() + second_ns;
    int status = 0;
    while (shadow::monotonic_ns() < deadline) {
        const auto waited = waitpid(child, &status, WNOHANG);
        if (waited == child) return status;
        require(waited == 0 || errno == EINTR, "watchdog reap failed");
        std::this_thread::sleep_for(2ms);
    }
    throw std::runtime_error("watchdog could not reap attempt within cleanup bound");
}

int supervise(const std::string& receiver, const std::string& dir, const Case& test, std::uint64_t budget_ns) {
    require(mkdir(dir.c_str(), 0700) == 0, "create attempt directory failed");
    Fd output(dir + "/stdout.txt", O_WRONLY | O_CREAT | O_EXCL);
    Fd errors(dir + "/stderr.txt", O_WRONLY | O_CREAT | O_EXCL);
    const pid_t child = fork();
    require(child >= 0, "watchdog fork failed");
    if (child == 0) {
        if (setpgid(0, 0) < 0 || dup2(output.get(), STDOUT_FILENO) < 0 ||
            dup2(errors.get(), STDERR_FILENO) < 0) _exit(1);
        close(output.get());
        close(errors.get());
        _exit(attempt(receiver, dir, test, budget_ns));
    }
    // Establish the group on both sides of fork to cover child startup races.
    if (setpgid(child, child) < 0 && errno != ESRCH && getpgid(child) != child) {
        clean_attempt_group(child, dir, "watchdog_group_setup_failure");
        reap_attempt(child);
        throw std::runtime_error("watchdog could not establish attempt process group");
    }
    const auto limit = shadow::monotonic_ns() + attempt_watchdog_seconds * second_ns;
    while (shadow::monotonic_ns() < limit) {
        siginfo_t info{};
        // Retain the leader's PID until group cleanup has addressed its descendants.
        if (waitid(P_PID, child, &info, WEXITED | WNOHANG | WNOWAIT) < 0) {
            if (errno == EINTR) continue;
            if (errno != ECHILD) {
                clean_attempt_group(child, dir, "watchdog_wait_failure");
                reap_attempt(child);
            }
            throw std::runtime_error("watchdog waitid failed");
        }
        if (info.si_pid == child) {
            if (info.si_code != CLD_EXITED)
                clean_attempt_group(child, dir, "watchdog_abnormal_exit");
            const int status = reap_attempt(child);
            std::cout << "attempt_wait_status=" << status << " directory=" << dir << std::endl;
            return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
        }
        std::this_thread::sleep_for(2ms);
    }
    clean_attempt_group(child, dir, "watchdog_timeout");
    const int status = reap_attempt(child);
    std::cout << "attempt_wait_status=" << status << " directory=" << dir << std::endl;
    return 1;
}

} // namespace

int main(int argc, char** argv) {
    try {
        require(argc == 3, "usage: qnx-ipc-tests RECEIVER 7500000|112500000");
        const std::string budget = argv[2];
        require(budget == "7500000" || budget == "112500000", "unsupported IPC budget");
        require(signal(SIGPIPE, SIG_IGN) != SIG_ERR, "could not ignore SIGPIPE");
        struct utsname identity{};
        require(uname(&identity) == 0, "uname failed");
        char root[] = "./qnx-ipc-run-XXXXXX";
        require(mkdtemp(root) != nullptr, "cannot create evidence directory in current working directory");
        std::cout << "run directory=" << root << " backend=" << shadow::WorkerProcess::backend()
                  << " budget_ns=" << budget << " attempts_per_case=" << attempts
                  << " watchdog_seconds=" << attempt_watchdog_seconds << " guest=" << identity.sysname
                  << ' ' << identity.release << ' ' << identity.version << ' ' << identity.machine << std::endl;
        bool failed = false;
        bool inconclusive = false;
        for (const auto& test : cases) {
            for (int i = 1; i <= attempts; ++i) {
                const auto dir = std::string(root) + "/" + test.name + "-" + std::to_string(i);
                int code = 1;
                try {
                    code = supervise(argv[1], dir, test, std::stoull(budget));
                } catch (const std::exception& error) {
                    std::cerr << "attempt_setup_failure directory=" << dir << " error=" << error.what() << std::endl;
                }
                std::cout << "verdict case=" << test.name << " attempt=" << i
                          << " result=" << (code == 0 ? "PASS" : code == 77 ? "INCONCLUSIVE" : "FAIL")
                          << " exit_code=" << code << " directory=" << dir << std::endl;
                failed |= code != 0 && code != 77;
                inconclusive |= code == 77;
            }
        }
        return failed ? 1 : inconclusive ? 77 : 0;
    } catch (const std::exception& error) {
        std::cerr << "watchdog: " << error.what() << std::endl;
        return 1;
    }
}
