# QNX 8.0.6 release membership audit

## Observed result

All 260 installed package IDs and exact versions match the locally cached QNX
release definition `com.qnx.qnx800.patchset806/8.0.6.00398T202611040724L`.
There are no missing, extra or mismatched installed packages. The installation
profile contains the same 260 versions plus one synthetic Windows heartbeat
autopatch unit, recorded separately in [results.json](results.json).

The existing guest was booted again with native macOS QEMU 11.1.3, TCG, 2 CPUs,
1 GiB RAM, no networking and snapshot disk writes. The kernel reports package
`com.qnx.qnx800.target.microkernel.core/2.8.0.00047T202610300907L`, which is pinned
by that release definition. Its `uname` version is still `8.0.0`.

The scan found 286 vendor paths: 285 paths with build IDs, representing 164
distinct builds, plus the kernel. Every observed build ID matches an x86-64
asset in its release-pinned SDK package. Aliases count as separate paths.
`awk`, `gawk` and `liblzma.so.5` provide build IDs without package tags; their
package ownership was inferred from unique SDK metadata matches and is marked
as such. The boot kernel does not expose a GNU build ID through `use -i`; its
package/version matched, and the separate SDK kernel file passed its package
SHA-512 check. This is not a byte-for-byte guest kernel comparison.

These observations support release membership for the complete installed
package inventory and inspected guest components. Old component dates and
`8.0.0` version strings do not independently indicate a mixed release. No
package mismatch was found that would require regenerating the existing image.

## Separate support gap

The installed QNX Software Center is `1.7.0.v202103252036`. The
[8.0.6 release notes](https://developers.qnx.com/developers/docs/relnotes8.0/com.qnx.doc.release_notes/topic/8.0.6_prod_release_notes/8.0.6_rn.html)
require QSC 2.0.4 or later and a new baseline installation using the Conservative
policy, and exclude macOS/ARM development hosts. The observed package agreement
does not certify this older-client Mac installation as vendor-supported.
Reproduction on a supported x86-64 host remains separate follow-up work.

## Evidence and limits

- [guest-build-info.log](guest-build-info.log) and [uname.log](uname.log) are the
  original target files, extracted without rewriting. The command selected only
  build ID, name, version, date and package metadata before recording the file.
- [serial-original.log](serial-original.log) contains boot output, commands,
  the exported archive and normal VM termination. The archive's POSIX checksum
  and byte count were `2627079667 9295` on both target and host before shutdown.
- [environment.json](environment.json) records the exact emulator command,
  existing compiler metadata, hashes and export custody. All five executable
  hashes and all three image artifact hashes still match their earlier records.
- [audit.py](audit.py) compares the package definition, installation profile,
  SDK asset metadata and exported guest records. SDK metadata and images remain
  local; only derived identifiers, hashes and project-owned evidence are stored.
- The catalog is the local QSC server cache, not a freshly downloaded or
  independently signature-validated release manifest. The audit does not hash
  every SDK/guest file or establish the integrity of every build ID-bearing byte.
- The guest scan covers regular files under `/proc/boot`, `/system/bin`,
  `/system/lib`, `/system/lib/dll` and `/system/lib/dll/pam` at the stated depths.
  It is not a recursive filesystem or BSP completeness audit. Project executable
  identity was already checked in the native execution evidence; their host
  hashes and the containing image are unchanged here.
- No runtime behavior, deadlines or image contents were changed. Timing
  failures remain preserved and deferred; this audit makes no real-time claim.

## Reproduce with locally available SDK metadata

```sh
python3 docs/evidence/qnx-release-audit-2026-11-23/audit.py \
  --sdk /path/to/qnx800 \
  --catalog /path/to/server-cache/www_qnx_com_80_swcenter/content.xml \
  --profile /path/to/installation.profile.gz \
  --qsc-plist '/Applications/QNX Software Center.app/Contents/Info.plist' \
  --guest docs/evidence/qnx-release-audit-2026-11-23/guest-build-info.log \
  --output /tmp/qnx-release-audit.json
```

The guest metadata was collected with:

```sh
uname -a > /data/release-audit/uname.log
for f in /proc/boot/* /system/bin/* /system/lib/* /system/lib/dll/* /system/lib/dll/pam/*; do
    if test -f "$f"; then
        printf 'FILE %s\n' "$f"
        use -i "$f" 2>/dev/null | grep -E '^(QNX_BUILDID|NAME|VERSION|DATE|PACKAGE)='
    fi
done > /data/release-audit/guest-build-info.log
```
