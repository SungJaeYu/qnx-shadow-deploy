#pragma once

#include "shadow/io.hpp"
#include <cstdint>
#include <fcntl.h>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace fixture {

constexpr std::uint64_t second_ns = 1000000000ULL;
constexpr std::uint64_t probe_ns = 50000000ULL;

enum class EventKind { ready, sample, reply, receive_timeout, receive_error };
struct Event {
    EventKind kind;
    std::int64_t result;
    int error;
    std::uint64_t timestamp_ns;
    std::uint64_t sequence;
    std::uint64_t acquired_ns;
};

class Fd {
public:
    explicit Fd(const std::string& path, int flags)
        : fd_(open(path.c_str(), flags | O_CLOEXEC, 0600)) {
        if (fd_ < 0) throw std::runtime_error("open failed: " + path);
    }
    ~Fd() { close(fd_); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
    int get() const { return fd_; }
private:
    int fd_;
};

template <typename T>
void send(int fd, const T& value) {
    if (shadow::write_until(fd, &value, sizeof(value), shadow::monotonic_ns() + second_ns)
        != shadow::IoResult::ok) throw std::runtime_error("fixture control write failed");
}

template <typename T>
T receive(int fd) {
    T value{};
    if (shadow::read_until(fd, &value, sizeof(value), shadow::monotonic_ns() + 4 * second_ns)
        != shadow::IoResult::ok) throw std::runtime_error("fixture control read failed");
    return value;
}

} // namespace fixture
