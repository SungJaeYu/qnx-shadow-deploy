#pragma once
#include <cstddef>
#include <cstdint>

namespace shadow {
std::uint64_t monotonic_ns();
enum class IoResult { ok, timeout, disconnected };
IoResult read_until(int fd, void* data, std::size_t size, std::uint64_t deadline);
IoResult write_until(int fd, const void* data, std::size_t size, std::uint64_t deadline);
bool read_exact(int fd, void* data, std::size_t size);
bool write_exact(int fd, const void* data, std::size_t size);
void nonblocking(int fd);
} // namespace shadow
