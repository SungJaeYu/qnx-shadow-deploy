#include "shadow/control.hpp"
#include "shadow/io.hpp"
#include <cassert>
#include <cerrno>
#include <cstddef>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

namespace shadow {
namespace {
void close_fd(int& fd) {
    if (fd != -1) ::close(fd);
    fd = -1;
}

bool send_record(int fd, const std::string& record) {
    return ::send(fd, record.data(), record.size(), 0) == static_cast<ssize_t>(record.size());
}
} // namespace

sockaddr_un control_address(const std::string& path) {
    sockaddr_un address{};
    if (path.empty() || path.front() != '/' || path.size() >= sizeof(address.sun_path))
        throw std::invalid_argument("control socket requires a short absolute path");
    const auto slash = path.rfind('/');
    const auto parent = slash ? path.substr(0, slash) : "/";
    struct stat info{};
    if (::stat(parent.c_str(), &info) != 0 || !S_ISDIR(info.st_mode) ||
        info.st_uid != ::geteuid() || (info.st_mode & 0777) != 0700)
        throw std::invalid_argument("control socket directory must be owned by the broker user with mode 0700");
#if defined(__APPLE__) || defined(__QNXNTO__)
    address.sun_len = static_cast<unsigned char>(offsetof(sockaddr_un, sun_path) + path.size() + 1);
#endif
    address.sun_family = AF_UNIX;
    std::memcpy(address.sun_path, path.c_str(), path.size() + 1);
    return address;
}

bool configure_control_fd(int fd) {
    const int flags = ::fcntl(fd, F_GETFL);
    return flags != -1 && ::fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0 &&
           ::fcntl(fd, F_SETFD, FD_CLOEXEC) == 0;
}

DeploymentControl::DeploymentControl(const std::string& path) : path_(path) {
    const auto address = control_address(path);

    listener_ = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (listener_ == -1 || !configure_control_fd(listener_) ||
        ::bind(listener_, reinterpret_cast<const sockaddr*>(&address), sizeof(address)) != 0) {
        const auto error = errno;
        close_fd(listener_);
        throw std::runtime_error(std::string("cannot bind control socket: ") + std::strerror(error));
    }
    if (::listen(listener_, 8) != 0) {
        const auto error = errno;
        close_fd(listener_);
        ::unlink(path_.c_str());
        throw std::runtime_error(std::string("cannot listen on control socket: ") + std::strerror(error));
    }
}

DeploymentControl::~DeploymentControl() {
    close_fd(reader_);
    close_fd(waiter_);
    close_fd(listener_);
    ::unlink(path_.c_str());
}

std::optional<std::string> DeploymentControl::request() {
    if (reader_ == -1) {
        reader_ = ::accept(listener_, nullptr, nullptr);
        if (reader_ == -1) return {};
        if (!configure_control_fd(reader_)) {
            close_fd(reader_);
            return {};
        }
        pending_.clear();
        reader_deadline_ns_ = monotonic_ns() + 2000000000ULL;
    }

    char bytes[control_path_limit + 1];
    const auto count = ::recv(reader_, bytes, sizeof(bytes) - pending_.size(), 0);
    if (count > 0) {
        pending_.append(bytes, static_cast<std::size_t>(count));
        const auto end = pending_.find('\n');
        if (end == pending_.size() - 1 && pending_.front() == '/' &&
            pending_.find('\0') == std::string::npos) {
            pending_.pop_back();
            return pending_;
        }
        if (end != std::string::npos || pending_.size() == sizeof(bytes)) {
            refuse("{\"outcome\":\"refused\",\"generation\":null,\"reason\":\"invalid_request\",\"active_owner\":null}");
            return {};
        }
    } else if (!count || (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) {
        close_fd(reader_);
    }
    if (monotonic_ns() >= reader_deadline_ns_) close_fd(reader_);
    return {};
}

void DeploymentControl::accept(unsigned generation) {
    assert(reader_ != -1 && !awaiting_result());
    waiter_ = reader_;
    reader_ = -1;
    if (!send_record(waiter_, "a " + std::to_string(generation) + "\n")) close_fd(waiter_);
}

void DeploymentControl::refuse(const std::string& result) {
    send_record(reader_, "4 " + result + "\n");
    close_fd(reader_);
}

void DeploymentControl::finish(unsigned exit_code, const std::string& result) {
    if (waiter_ == -1) return;
    send_record(waiter_, std::to_string(exit_code) + " " + result + "\n");
    close_fd(waiter_);
}
} // namespace shadow
