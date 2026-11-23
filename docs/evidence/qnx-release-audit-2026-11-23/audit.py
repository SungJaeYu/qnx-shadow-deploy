#!/usr/bin/env python3
"""Compare installed metadata and exported guest IDs with the cached release set.

Read only package descriptors, the selected profile, and whitelisted guest fields.
Do not read license stores or export vendor files.
"""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
import plistlib
import re
import xml.etree.ElementTree as ET


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(args):
    sdk = args.sdk
    package_root = sdk / ".packages/metadata"
    units = ET.parse(args.catalog).getroot().findall("./units/unit")
    release = [u for u in units if u.get("id") == "com.qnx.qnx800.patchset806"]
    assert len(release) == 1, "Expected exactly one 8.0.6 release definition"
    release = release[0]
    expected = {}
    for req in release.findall("./requires/required"):
        bounds = req.get("range", "")[1:-1].split(",")
        assert len(bounds) == 2 and bounds[0] == bounds[1], "Release is not exactly pinned"
        assert req.get("range") == f"[{bounds[0]},{bounds[0]}]"
        expected[req.get("name")] = bounds[0]

    installed, assets = {}, []
    for descriptor in sorted(package_root.glob("*/*/ResourceDescriptor.afd")):
        resource = json.loads(descriptor.read_text())
        package, version = resource["id"], resource["version"]
        assert package not in installed, "Duplicate installed package"
        installed[package] = version
        asset_file = descriptor.with_name("AssetDescriptor.json")
        for asset in json.loads(asset_file.read_text())["assets"]:
            assets.append((package, version, asset))

    profile = ET.fromstring(gzip.decompress(args.profile.read_bytes()))
    profile_units = {u.get("id"): u.get("version") for u in profile.findall("./units/unit")}
    package_diff = {
        "missing": sorted(expected.keys() - installed.keys()),
        "extra": sorted(installed.keys() - expected.keys()),
        "version_mismatch": [p for p in sorted(expected.keys() & installed.keys())
                             if expected[p] != installed[p]],
    }
    profile_mismatch = [p for p, v in installed.items() if profile_units.get(p) != v]
    by_id = {}
    for package, version, asset in assets:
        if asset.get("buildID") and asset.get("arch") == "x86_64":
            by_id.setdefault(asset["buildID"], []).append((package, version, asset))

    records, current = [], None
    for line in args.guest.read_text().splitlines():
        if line.startswith("FILE "):
            current = {"path": line[5:]}
            records.append(current)
        elif "=" in line and current is not None:
            key, value = line.split("=", 1)
            assert key in {"QNX_BUILDID", "NAME", "VERSION", "DATE", "PACKAGE"}
            current[key] = value

    vendor, inferred = [], []
    for record in records:
        build_id = record.get("QNX_BUILDID", "").removeprefix("(GNU)")
        if "PACKAGE" not in record:
            if not build_id:
                continue
            owners = {(p, v) for p, v, a in by_id.get(build_id, [])}
            assert len(owners) == 1, f"Unknown or ambiguous untagged build: {record['path']}"
            package, version = next(iter(owners))
            inferred.append(record["path"])
        else:
            package, version = record["PACKAGE"].rsplit("/", 1)
        matches = [a for p, v, a in by_id.get(build_id, []) if (p, v) == (package, version)]
        entry = {"path": record["path"], "package": package, "version": version,
                 "package_source": "sdk_build_id_lookup" if record["path"] in inferred else "guest_use_info",
                 "release_version_matches": expected.get(package) == version,
                 "build_id": build_id or None,
                 "matching_sdk_assets": sorted(a["name"] for a in matches)}
        vendor.append(entry)

    kernel = next(r for r in vendor if r["path"] == "/proc/boot/procnto-smp-instr")
    kernel_asset = next((p, v, a) for p, v, a in assets
                        if a["name"] == "target/qnx/x86_64/boot/sys/procnto-smp-instr")
    kp, kv, ka = kernel_asset
    kernel_bytes = (sdk / ka["name"]).read_bytes()
    kernel_check = {
        "guest_package_matches": (kernel["package"], kernel["version"]) == (kp, kv),
        "sdk_build_id": ka["buildID"],
        "sdk_sha512_matches_descriptor": base64.b64encode(hashlib.sha512(kernel_bytes).digest()).decode() == ka["digestSHA512"],
        "guest_build_id_available": bool(kernel["build_id"]),
        "scope": "The boot kernel exposes package/version but no GNU build ID through use -i. SDK integrity is a separate check, not a byte-for-byte guest kernel comparison.",
    }
    assert not any(package_diff.values()), package_diff
    assert not profile_mismatch, profile_mismatch
    assert all(v["release_version_matches"] for v in vendor), "Guest package mismatch"
    assert all(v["matching_sdk_assets"] for v in vendor if v["build_id"]), "Guest build ID mismatch"
    assert kernel_check["guest_package_matches"] and kernel_check["sdk_sha512_matches_descriptor"]

    qsc = plistlib.loads(args.qsc_plist.read_bytes())
    report = {
        "observed_date": "2026-11-23",
        "status": "passed_with_documented_scope",
        "release": {"id": release.get("id"), "version": release.get("version"),
                    "source": "QNX Software Center local server-cache/www_qnx_com_80_swcenter/content.xml",
                    "catalog_sha256": sha256(args.catalog), "pinned_package_count": len(expected)},
        "installed_package_count": len(installed), "package_diff": package_diff,
        "profile": {"sha256": sha256(args.profile), "unit_count": len(profile_units),
                    "installed_version_mismatch": profile_mismatch,
                    "additional_units": {p: profile_units[p] for p in sorted(profile_units.keys() - installed.keys())}},
        "packages": [{"id": p, "release_version": expected[p], "installed_version": installed[p]}
                     for p in sorted(expected)],
        "qsc": {"version": qsc["CFBundleShortVersionString"], "build": qsc["CFBundleVersion"],
                "support_gap": "Installed QSC 1.7 is below the 8.0.6 release-note minimum 2.0.4. macOS/ARM is not a supported SDP development host."},
        "guest": {"uname": args.guest.with_name("uname.log").read_text().strip(),
                  "inventory_sha256": sha256(args.guest),
                  "scanned_paths": len(records), "vendor_package_records": len(vendor),
                  "vendor_build_id_records": sum(bool(v["build_id"]) for v in vendor),
                  "unique_vendor_build_ids": len({v["build_id"] for v in vendor if v["build_id"]}),
                  "vendor_records": vendor, "package_inferred_from_build_id": inferred,
                  "kernel": kernel_check},
        "limitations": [
            "Release membership is checked against a locally cached vendor catalog; no catalog refresh or independent signature validation was performed.",
            "Package inventory agreement does not hash every SDK file or prove vendor-supported installation.",
            "Guest scan covers regular files in /proc/boot, /system/bin, /system/lib, /system/lib/dll and /system/lib/dll/pam; aliases are counted as separate paths.",
            "Build IDs identify vendor builds; they are not cryptographic integrity proofs of every guest byte.",
            "No execution-time robustness or real-time hardware guarantee follows from release alignment.",
        ],
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "installed": len(installed),
                      "diff": package_diff, "vendor_records": len(vendor),
                      "build_id_records": report["guest"]["vendor_build_id_records"],
                      "unique_build_ids": report["guest"]["unique_vendor_build_ids"],
                      "no_build_id": [v["path"] for v in vendor if not v["build_id"]],
                      "package_inferred_from_build_id": inferred}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["sdk", "catalog", "profile", "qsc-plist", "guest", "output"]:
        parser.add_argument("--" + name, type=Path, required=True)
    audit(parser.parse_args())
