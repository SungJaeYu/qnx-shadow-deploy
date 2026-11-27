#pragma once
#include <cstdint>
#include <optional>
#include <string>
#include <sys/un.h>

namespace shadow {
// One request and at most two replies per connection. Paths are absolute
// and newline-delimited. All descriptor operations at sample boundaries are nonblocking.
constexpr unsigned control_path_limit = 4096;
sockaddr_un control_address(const std::string& path);
bool configure_control_fd(int fd);

class DeploymentControl {
public:
    explicit DeploymentControl(const std::string& path);
    ~DeploymentControl();
    DeploymentControl(const DeploymentControl&) = delete;
    DeploymentControl& operator=(const DeploymentControl&) = delete;

    std::optional<std::string> request();
    void accept(unsigned generation);
    void refuse(const std::string& result);
    bool awaiting_result() const { return waiter_ != -1; }
    void finish(unsigned exit_code, const std::string& result);

private:
    std::string path_;
    int listener_ = -1;
    int reader_ = -1;
    int waiter_ = -1;
    std::string pending_;
    std::uint64_t reader_deadline_ns_ = 0;
};
} // namespace shadow
