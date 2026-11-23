#include "shadow/io.hpp"
#include <cerrno>
#include <climits>
#include <fcntl.h>
#include <poll.h>
#include <stdexcept>
#include <time.h>
#include <unistd.h>

namespace shadow {
std::uint64_t monotonic_ns() {
    timespec ts{};
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) throw std::runtime_error("clock_gettime failed");
    return static_cast<std::uint64_t>(ts.tv_sec) * 1000000000ULL + static_cast<std::uint64_t>(ts.tv_nsec);
}
void nonblocking(int fd) {
    const int flags = fcntl(fd, F_GETFL);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0)
        throw std::runtime_error("Unable to configure nonblocking pipe");
}
static IoResult transfer(int fd, void* data, std::size_t size, std::uint64_t deadline, bool writing) {
    auto* bytes = static_cast<unsigned char*>(data);
    std::size_t offset = 0;
    while (offset < size) {
        const auto now = monotonic_ns();
        if (now >= deadline) return IoResult::timeout;
        const auto remaining_ms = (deadline - now + 999999) / 1000000;
        const int wait_ms = remaining_ms > INT_MAX ? INT_MAX : static_cast<int>(remaining_ms);
        pollfd p{fd, static_cast<short>(writing ? POLLOUT : POLLIN), 0};
        const int ready = poll(&p, 1, wait_ms);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0) return IoResult::disconnected;
        if (ready == 0) continue;
        if (p.revents & POLLNVAL) return IoResult::disconnected;
        const auto n = writing ? write(fd, bytes + offset, size - offset) : read(fd, bytes + offset, size - offset);
        if (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (n <= 0) return IoResult::disconnected;
        offset += static_cast<std::size_t>(n);
    }
    return monotonic_ns() > deadline ? IoResult::timeout : IoResult::ok;
}
IoResult read_until(int fd, void* data, std::size_t size, std::uint64_t deadline) {
    return transfer(fd, data, size, deadline, false);
}
IoResult write_until(int fd, const void* data, std::size_t size, std::uint64_t deadline) {
    return transfer(fd, const_cast<void*>(data), size, deadline, true);
}
bool read_exact(int fd, void* data, std::size_t size) {
    auto* p = static_cast<unsigned char*>(data);
    while (size) {
        const auto n = read(fd, p, size);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        p += n; size -= static_cast<std::size_t>(n);
    }
    return true;
}
bool write_exact(int fd, const void* data, std::size_t size) {
    const auto* p = static_cast<const unsigned char*>(data);
    while (size) {
        const auto n = write(fd, p, size);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        p += n; size -= static_cast<std::size_t>(n);
    }
    return true;
}
} // namespace shadow
