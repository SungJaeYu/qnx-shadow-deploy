#include "shadow/control.hpp"
#include "shadow/io.hpp"
#include <cerrno>
#include <charconv>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <poll.h>
#include <stdexcept>
#include <string_view>
#include <sys/socket.h>
#include <unistd.h>

namespace {
constexpr unsigned default_timeout_ms = 30000;
constexpr unsigned max_timeout_ms = 300000;

int unknown(unsigned generation, const char* reason) {
    std::cout << "{\"outcome\":\"unknown\",\"generation\":";
    if (generation) std::cout << generation;
    else std::cout << "null";
    std::cout << ",\"reason\":\"" << reason << "\",\"active_owner\":null}\n";
    return 5;
}

bool parse_number(std::string_view text, unsigned& value) {
    const auto* end = text.data() + text.size();
    const auto parsed = std::from_chars(text.data(), end, value);
    return parsed.ec == std::errc{} && parsed.ptr == end && value > 0;
}

// A single monotonic deadline covers connection, request transmission, and both replies.
class Connection {
public:
    explicit Connection(int fd, unsigned timeout_ms)
        : fd_(fd), deadline_ns_(shadow::monotonic_ns() + timeout_ms * 1000000ULL) {}
    ~Connection() { ::close(fd_); }

    bool wait(short events) const {
        while (!expired()) {
            const auto now = shadow::monotonic_ns();
            if (now >= deadline_ns_) break;
            const auto ms = static_cast<int>((deadline_ns_ - now + 999999) / 1000000);
            pollfd fd{fd_, events, 0};
            const auto ready = ::poll(&fd, 1, ms);
            if (ready > 0) return !expired();
            if (!ready || errno != EINTR) break;
        }
        return false;
    }
    bool expired() const { return shadow::monotonic_ns() >= deadline_ns_; }

private:
    int fd_;
    std::uint64_t deadline_ns_;
};

int deploy(const sockaddr_un& address, std::string request, unsigned timeout_ms) {
    const auto fd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd == -1) return unknown(0, "broker_unreachable");
    const Connection connection(fd, timeout_ms);
    if (!shadow::configure_control_fd(fd)) return unknown(0, "broker_unreachable");
    if (::connect(fd, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) == -1) {
        if (errno != EINPROGRESS && errno != EINTR) return unknown(0, "broker_unreachable");
        if (!connection.wait(POLLOUT))
            return unknown(0, connection.expired() ? "timeout" : "broker_unreachable");
        int error = 0;
        socklen_t length = sizeof(error);
        if (::getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &length) == -1 || error)
            return unknown(0, "broker_unreachable");
    }

    request.push_back('\n');
    std::string_view unsent(request);
    while (!unsent.empty() && connection.wait(POLLOUT)) {
        const auto sent = ::send(fd, unsent.data(), unsent.size(), 0);
        if (sent > 0) unsent.remove_prefix(static_cast<std::size_t>(sent));
        else if (!sent || (errno != EINTR && errno != EWOULDBLOCK && errno != EAGAIN)) break;
    }
    if (!unsent.empty()) return unknown(0, connection.expired() ? "timeout" : "contact_lost");

    unsigned generation = 0;
    std::string pending;
    while (connection.wait(POLLIN)) {
        char bytes[512];
        const auto received = ::recv(fd, bytes, sizeof(bytes), 0);
        if (received < 0 && (errno == EINTR || errno == EWOULDBLOCK || errno == EAGAIN)) continue;
        if (received <= 0) break;
        pending.append(bytes, static_cast<std::size_t>(received));
        if (pending.size() > 4096) return unknown(generation, "invalid_broker_reply");
        for (auto end = pending.find('\n'); end != std::string::npos; end = pending.find('\n')) {
            const std::string_view line(pending.data(), end);
            if (!generation && line.substr(0, 2) == "a " && parse_number(line.substr(2), generation)) {
                pending.erase(0, end + 1);
                continue;
            }
            if (line.size() > 3 && line[1] == ' ' && line[2] == '{' && line.back() == '}') {
                const int code = line.front() - '0';
                if ((generation && (code == 0 || code == 2 || code == 3 || code == 6)) ||
                    (!generation && code == 4)) {
                    std::cout << line.substr(2) << '\n';
                    return code;
                }
            }
            return unknown(generation, "invalid_broker_reply");
        }
    }
    return unknown(generation, connection.expired() ? "timeout" : "contact_lost");
}

void usage(std::ostream& out) {
    out << "Usage: shadowctl [--socket PATH] deploy /absolute/candidate.manifest --wait [--timeout-ms N]\n"
           "The socket path may also be set via SHADOW_CONTROL_SOCKET. Default timeout: 30000 ms.\n";
}
} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string_view(argv[1]) == "--help") {
        usage(std::cout);
        return 0;
    }
    const char* socket_path = std::getenv("SHADOW_CONTROL_SOCKET");
    if (argc >= 3 && std::string_view(argv[1]) == "--socket") {
        socket_path = argv[2];
        argc -= 2;
        argv += 2;
    }
    unsigned timeout_ms = default_timeout_ms;
    if ((argc != 4 && argc != 6) || !socket_path || std::string_view(argv[1]) != "deploy" ||
        std::string_view(argv[3]) != "--wait" ||
        (argc == 6 && (std::string_view(argv[4]) != "--timeout-ms" ||
                      !parse_number(argv[5], timeout_ms) || timeout_ms > max_timeout_ms))) {
        usage(std::cerr);
        return 1;
    }
    const std::string manifest(argv[2]);
    if (manifest.empty() || manifest.front() != '/' || manifest.size() > shadow::control_path_limit ||
        manifest.find('\n') != std::string::npos) {
        std::cerr << "shadowctl: manifest must be an absolute path of at most 4096 bytes without a newline\n";
        return 1;
    }

    std::signal(SIGPIPE, SIG_IGN);
    try {
        return deploy(shadow::control_address(socket_path), manifest, timeout_ms);
    } catch (const std::invalid_argument& error) {
        std::cerr << "shadowctl: " << error.what() << '\n';
        return 1;
    }
}
