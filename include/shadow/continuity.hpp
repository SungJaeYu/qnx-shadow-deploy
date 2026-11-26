#pragma once
#include <algorithm>
#include <cstdint>
#include <optional>

namespace shadow {
struct AcquisitionTiming {
    std::uint64_t nominal_slot;
    std::uint64_t skipped_slots;
    std::uint64_t nominal_lateness_ns;
    std::uint64_t nominal_grid_lateness_ns;
    std::uint64_t scheduling_lateness_ns;
    std::optional<std::uint64_t> acquisition_interval_ns;
};

// Observes the existing acquisition and selection boundaries without scheduling work.
class ServiceContinuity {
public:
    ServiceContinuity(std::uint64_t origin_ns, std::uint64_t period_ns)
        : origin_ns_(origin_ns), period_ns_(period_ns) {}

    AcquisitionTiming acquire(std::uint64_t acquired_ns, std::uint64_t scheduled_ns) {
        const auto offset = acquired_ns - origin_ns_;
        const auto slot = offset / period_ns_;
        AcquisitionTiming timing{slot, 0, offset % period_ns_, offset - acquired * period_ns_,
                                 acquired_ns - scheduled_ns, {}};
        if (last_nominal_slot) {
            timing.acquisition_interval_ns = acquired_ns - last_acquired_ns_;
            timing.skipped_slots = slot - *last_nominal_slot - 1;
            update_max(max_acquisition_interval_ns, *timing.acquisition_interval_ns);
        } else {
            initial_unoccupied_slots = slot;
        }
        update_max(max_nominal_slot_lateness_ns, timing.nominal_lateness_ns);
        update_max(max_nominal_grid_lateness_ns, timing.nominal_grid_lateness_ns);
        update_max(max_scheduling_lateness_ns, timing.scheduling_lateness_ns);
        skipped_slots += timing.skipped_slots;
        last_nominal_slot = slot;
        last_acquired_ns_ = acquired_ns;
        ++acquired;
        return timing;
    }

    void decide(std::uint64_t decision_ns, bool has_output, bool deadline_miss) {
        if (deadline_miss) ++decision_deadline_misses;
        if (!has_output) return;
        if (selected) {
            update_max(max_selected_interval_ns, decision_ns - last_selected_ns_);
            selected_span_ns = decision_ns - first_selected_ns_;
        } else {
            first_selected_ns_ = decision_ns;
        }
        last_selected_ns_ = decision_ns;
        ++selected;
    }

    unsigned acquired = 0;
    unsigned selected = 0;
    unsigned decision_deadline_misses = 0;
    std::uint64_t initial_unoccupied_slots = 0;
    std::uint64_t skipped_slots = 0;
    std::optional<std::uint64_t> last_nominal_slot;
    std::optional<std::uint64_t> max_acquisition_interval_ns;
    std::optional<std::uint64_t> max_nominal_slot_lateness_ns;
    std::optional<std::uint64_t> max_nominal_grid_lateness_ns;
    std::optional<std::uint64_t> max_scheduling_lateness_ns;
    std::optional<std::uint64_t> max_selected_interval_ns;
    std::uint64_t selected_span_ns = 0;

private:
    static void update_max(std::optional<std::uint64_t>& maximum, std::uint64_t value) {
        maximum = std::max(maximum.value_or(0), value);
    }

    std::uint64_t origin_ns_;
    std::uint64_t period_ns_;
    std::uint64_t last_acquired_ns_ = 0;
    std::uint64_t first_selected_ns_ = 0;
    std::uint64_t last_selected_ns_ = 0;
};
} // namespace shadow
