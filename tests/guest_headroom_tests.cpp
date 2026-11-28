#include "shadow/guest_headroom.hpp"
#include <cstdlib>
#include <iostream>

namespace {
using shadow::GuestHeadroom;

void check(std::string_view counters, GuestHeadroom expected, const char* scenario,
           unsigned long long page_size = 4096) {
    if (shadow::classify_guest_headroom(counters, page_size) != expected) {
        std::cerr << scenario << '\n';
        std::exit(EXIT_FAILURE);
    }
}
} // namespace

int main() {
    check("vm_aspace=0x10\nvmem_avail=0x40000 (1.000GB)\npages_free=0x28000 (640.000MB)\n",
          GuestHeadroom::sufficient, "native VM counter annotations");
    check("vmem_avail=0x2000 (32.000MB)\npages_free=0x1fff (31.996MB)\n",
          GuestHeadroom::insufficient, "native VM counters below physical threshold");
    check("pages_free=0x2000 (32.000MB)\nvmem_avail=0x8 (32KB)\n",
          GuestHeadroom::insufficient, "native counters below unreserved threshold");
    check("pages_free=0x0 (0B)\nvmem_avail=0x2000\n", GuestHeadroom::insufficient, "zero counter");
    check("pages_free=0x2000 (32.000MB\nvmem_avail=0x2000\n",
          GuestHeadroom::invalid, "truncated annotation");
    check("pages_free=0x2000 (32.000MB)junk\nvmem_avail=0x2000\n",
          GuestHeadroom::invalid, "junk after annotation");
    check("pages_free=0x2000 (-32.000MB)\nvmem_avail=0x2000\n",
          GuestHeadroom::invalid, "negative annotation");
    check("pages_free=0x-2000 (32.000MB)\nvmem_avail=0x2000\n",
          GuestHeadroom::invalid, "negative counter");
    check("pages_free=0x2000 (32.000MB)\nvmem_avail=0x2000\npages_free=0x2000 (32MB)\n",
          GuestHeadroom::invalid, "duplicate annotated counter");
    check("pages_free=0x1fff (999.999MB)\nvmem_avail=0x2000 (32MB)\n",
          GuestHeadroom::insufficient, "the hex count determines admission");
    check("vmem_avail=0x2000\npages_free=0x2000\n", GuestHeadroom::sufficient, "exact threshold");
    check("pages_free=0x1fff\nvmem_avail=0x2000\n", GuestHeadroom::insufficient, "physical threshold");
    check("pages_free=0x2000\nvmem_avail=0x1fff\n", GuestHeadroom::insufficient, "unreserved threshold");
    check("pages_free=0x1fff\nvmem_avail=0x1fff\n", GuestHeadroom::sufficient, "larger page size", 8192);
    check("pages_free=0x1\nvmem_avail=0x1\n", GuestHeadroom::insufficient, "round required pages up", 33554431);
    check("pages_free=0x2000\nvmem_avail=0x2000\n", GuestHeadroom::invalid, "invalid page size", 0);
    check("vm_aspace=0x10\npages_free=0x2000\nvmem_avail=0x2000\n",
          GuestHeadroom::sufficient, "unrelated counter");
    check("pages_free=0x2000\n", GuestHeadroom::invalid, "missing unreserved counter");
    check("vmem_avail=0x2000\n", GuestHeadroom::invalid, "missing physical counter");
    check("pages_free=0x2000\nvmem_avail=0x2000\npages_free=0x2000\n",
          GuestHeadroom::invalid, "duplicate physical counter");
    check("pages_free=0x2000\nvmem_avail=0x2000\nvmem_avail=0x2000\n",
          GuestHeadroom::invalid, "duplicate unreserved counter");
    check("pages_free=0x2000\nvmem_avail=0x2000", GuestHeadroom::invalid, "unterminated record");
    check("pages_free=0x2000\nvmem_avail=0x2000\nother=0x", GuestHeadroom::invalid, "truncated tail");
    check("pages_free=0x2000\nvmem_avail=0x2000bytes\n", GuestHeadroom::invalid, "trailing garbage");
    check("pages_free=0x10000000000000000\nvmem_avail=0x2000\n",
          GuestHeadroom::invalid, "counter overflow");
}
