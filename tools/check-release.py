#!/usr/bin/env python3
"""Check a DXVK-Sarek release tarball against what Autorun accepts.

Autorun (the Nintendo Switch runtime) does not run a generic installer: it
reads the release asset list over the GitHub API, downloads one named archive,
and unpacks it with its own small tar reader in
``horizon-wine/source/dxvk_releases.c``. Anything that reader cannot make sense
of is dropped, and a payload that survives with the wrong DLLs is rejected as
``DXVK_INVALID_ARCHIVE`` -- the download is thrown away and the user is left
with an install that never appears.

The rules below are copied from that file, so this script fails a release in CI
instead of on a console:

* the asset is named ``dxvk-sarek-<ver>.tar.gz`` (rank 3), ``-v<ver>`` (2) or
  ``-dyasync-v<ver>`` (1), with ``<ver>`` exactly as ``RELEASE`` has it;
* the archive is gzip and every member is a plain ustar entry: no GNU long
  names, no pax headers, member name shorter than 100 bytes -- the reader only
  looks at the first 100 bytes of the header and gives up on the whole archive
  when a name is not NUL-terminated there;
* a DLL is only unpacked when the directory directly above it is ``x32`` or
  ``x64`` and its file name is one Autorun knows;
* both architectures end up with files, and every DLL is a PE for that machine
  (0x014c i386 / 0x10b PE32, 0x8664 x64 / 0x20b PE32+) marked as a DLL.

Usage:

    tools/check-release.py releases/dxvk-sarek-1.14.1.tar.gz
    tools/check-release.py --self-test
"""

import io
import os
import re
import sys
import tarfile
import tempfile

# horizon-wine/source/dxvk_releases.c -- sarek_dlls[]
X32_DLLS = ("ddraw.dll", "d3d8.dll", "d3d9.dll", "d3d10core.dll", "d3d11.dll", "dxgi.dll")
# ddraw is 32-bit only (src/meson.build builds it for cpu_family == 'x86'), so
# a 64-bit payload ships the other five and Autorun accepts that.
X64_DLLS = tuple(name for name in X32_DLLS if name != "ddraw.dll")

APPROVED = frozenset(X32_DLLS) | frozenset(X64_DLLS)

ASSET_PATTERNS = (
    "dxvk-sarek-{version}.tar.gz",
    "dxvk-sarek-v{version}.tar.gz",
    "dxvk-sarek-dyasync-v{version}.tar.gz",
)

# dxvk_options.c: dxvk_options_version() returns 0 above 1.14.1 for
# DXVK_SOURCE_SAREK, and the options screen refuses an unrecognised release.
# Installing still works, so this is a warning rather than a failure.
AUTORUN_LAST_KNOWN_VERSION = (1, 14, 1)

# dxvk_releases.c: valid_pe()
MACHINES = {"x32": (0x014C, 0x010B), "x64": (0x8664, 0x020B)}
IMAGE_FILE_DLL = 0x2000

NAME_FIELD = 100            # ustar name[100], the reader stops there
MEMBER_SIZE_LIMIT = 64 << 20  # dxvk_releases.c: size > 64 MiB aborts


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, message):
        self.errors.append(message)

    def warn(self, message):
        self.warnings.append(message)

    def ok(self):
        return not self.errors


def parse_version(text):
    """Mirror dxvk_options_version(): 1 to 3 dot-separated parts, each <= 99."""
    if not text:
        return None
    parts = text.split(".")
    if not 1 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        return None
    numbers = tuple(int(part) for part in parts)
    if any(number > 99 for number in numbers):
        return None
    return numbers + (0,) * (3 - len(numbers))


def asset_version(filename, expected):
    """Return the version an Autorun asset name carries, or None."""
    for pattern in ASSET_PATTERNS:
        candidate = pattern.format(version=expected) if expected else None
        if candidate and filename == candidate:
            return expected

    # Fall back to the generic shape so a wrong version is named, not ignored.
    match = re.fullmatch(r"dxvk-sarek-(?:dyasync-)?v?(\d+(?:\.\d+){0,2})\.tar\.gz", filename)
    return match.group(1) if match else None


def pe_info(data):
    """Return (machine, magic, characteristics) the way valid_pe() reads them."""
    if len(data) < 64 or data[:2] != b"MZ":
        return None

    offset = int.from_bytes(data[0x3C:0x40], "little")
    if offset < 64 or offset > 0x7FFFFFFF or offset + 26 > len(data):
        return None

    header = data[offset:offset + 26]
    if header[:4] != b"PE\0\0":
        return None

    machine = int.from_bytes(header[4:6], "little")
    characteristics = int.from_bytes(header[22:24], "little")
    magic = int.from_bytes(header[24:26], "little")
    return machine, magic, characteristics


def classify(member_name):
    """Where Autorun would put a member: 'x32', 'x64' or None (ignored)."""
    slash = member_name.rfind("/")
    if slash < 0:
        return None

    if member_name[slash + 1:].lower() not in APPROVED:
        return None

    parent = member_name[:slash]
    cut = parent.rfind("/")
    directory = parent[cut + 1:] if cut >= 0 else parent
    return directory if directory in ("x32", "x64") else None


def check_archive(path, expected_version=None):
    report = Report()

    filename = os.path.basename(path)
    version = asset_version(filename, expected_version)

    if expected_version and version is None:
        report.error(
            "asset name %r is not one Autorun matches for %s; it looks for %s"
            % (filename, expected_version,
               ", ".join(p.format(version=expected_version) for p in ASSET_PATTERNS)))
    elif version is None:
        report.error(
            "asset name %r matches none of Autorun's Sarek patterns: %s"
            % (filename, ", ".join(p.format(version="<ver>") for p in ASSET_PATTERNS)))

    if version:
        parsed = parse_version(version)
        if parsed is None:
            report.error("version %r is not one Autorun can parse (1-3 parts, each <= 99)" % version)
        elif parsed > AUTORUN_LAST_KNOWN_VERSION:
            report.warn(
                "version %s is newer than %s, the last release Autorun's options table knows; "
                "it installs, but the DXVK options screen answers 'not recognized'"
                % (version, ".".join(str(n) for n in AUTORUN_LAST_KNOWN_VERSION)))

    try:
        with open(path, "rb") as handle:
            magic = handle.read(2)
    except OSError as error:
        report.error("cannot read %s: %s" % (path, error))
        return report

    if magic != b"\x1f\x8b":
        report.error("%s is not gzip; Autorun checks that first" % filename)
        return report

    try:
        archive = tarfile.open(path, "r:gz")
    except (tarfile.TarError, OSError) as error:
        report.error("%s is not a readable tar.gz: %s" % (path, error))
        return report

    payload = {"x32": {}, "x64": {}}

    with archive:
        for member in archive.getmembers():
            if member.isdir():
                continue

            if member.type in (tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK) \
                    or member.name.endswith(("/./@LongLink", "/@LongLink")):
                report.error(
                    "GNU long name entry %r: Autorun's reader has no long-name "
                    "support and unpacks a truncated name instead" % member.name)
                continue

            if member.type in (tarfile.XHDTYPE, tarfile.XGLTYPE):
                report.error(
                    "pax extended header for %r: Autorun's reader skips these, so "
                    "the next entry's name is whatever fits in 100 bytes" % member.name)
                continue

            if not member.isfile():
                continue

            encoded = member.name.encode("utf-8", "surrogateescape")
            if len(encoded) >= NAME_FIELD:
                report.error(
                    "%d-byte member path does not fit ustar's name field; Autorun "
                    "rejects the whole archive: %s" % (len(encoded), member.name))
                continue

            arch = classify(member.name)
            if arch is None:
                continue

            if member.size > MEMBER_SIZE_LIMIT:
                report.error(
                    "%s is %d bytes, over the 64 MiB Autorun reads" % (member.name, member.size))
                continue

            extracted = archive.extractfile(member)
            data = extracted.read() if extracted else b""
            payload[arch][os.path.basename(member.name).lower()] = (member.name, data)

    for arch in ("x32", "x64"):
        found = payload[arch]
        want_machine, want_magic = MACHINES[arch]
        required = X32_DLLS if arch == "x32" else X64_DLLS

        if not found:
            report.error(
                "no DLL under a %s/ directory; Autorun needs both architectures "
                "(it checks the directory above each DLL is named x32 or x64)" % arch)
            continue

        for name in required:
            if name not in found:
                report.error("missing %s for %s" % (name, arch))

        for name, (member_name, data) in sorted(found.items()):
            info = pe_info(data)
            if info is None:
                report.error("%s is not a PE image" % member_name)
                continue

            machine, magic, characteristics = info
            if machine != want_machine:
                report.error("%s is machine 0x%04x, expected 0x%04x for %s"
                             % (member_name, machine, want_machine, arch))
                continue
            if magic != want_magic:
                report.error("%s has optional-header magic 0x%03x, expected 0x%03x for %s"
                             % (member_name, magic, want_magic, arch))
                continue
            if not characteristics & IMAGE_FILE_DLL:
                report.error("%s is not marked as a DLL (characteristics 0x%04x)"
                             % (member_name, characteristics))

    return report


def synthetic_pe(machine, magic, dll=True, size=1024):
    """A minimal image with the fields valid_pe() reads and nothing else."""
    image = bytearray(size)
    image[0:2] = b"MZ"
    offset = 0x80
    image[0x3C:0x40] = offset.to_bytes(4, "little")
    image[offset:offset + 4] = b"PE\0\0"
    image[offset + 4:offset + 6] = machine.to_bytes(2, "little")
    characteristics = 0x0002 | (IMAGE_FILE_DLL if dll else 0)
    image[offset + 22:offset + 24] = characteristics.to_bytes(2, "little")
    image[offset + 24:offset + 26] = magic.to_bytes(2, "little")
    return bytes(image)


def build_fixture(directory, asset="dxvk-sarek-1.14.1.tar.gz",
                  x32_dlls=X32_DLLS, x64_dlls=X64_DLLS,
                  x64_machine=0x8664, arch_dirs=("x32", "x64"), long_name=None):
    """Write a tarball shaped like `make release` output for the self-test."""
    prefix = "dxvk-sarek-1.14.1/build"
    files = {}

    for name in x32_dlls:
        files["%s/x32/%s" % (prefix, name)] = synthetic_pe(0x014C, 0x010B)
    for name in x64_dlls:
        files["%s/x64/%s" % (prefix, name)] = synthetic_pe(x64_machine, 0x020B)

    files["%s/meson.build" % "dxvk-sarek-1.14.1"] = b"project('dxvk')\n"

    if long_name:
        files["%s/%s" % (prefix, long_name)] = b"#\n"

    path = os.path.join(directory, asset)

    with tarfile.open(path, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
        if "x32" not in arch_dirs or "x64" not in arch_dirs:
            # Rename the whole arch directory, the way a bad `make dist` would.
            rename = {name: name.replace("/x32/", "/x86/").replace("/x64/", "/amd64/")
                      for name in files}
            files = {rename[name]: data for name, data in files.items()}

        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))

    return path


def self_test():
    cases = []

    with tempfile.TemporaryDirectory() as directory:
        path = build_fixture(directory, asset="dxvk-sarek-1.14.1.tar.gz")
        cases.append(("a complete payload", path, [], []))

        path = build_fixture(directory, asset="machine.tar.gz", x64_machine=0x014C)
        cases.append(("a 64-bit DLL built as i386", path, ["machine 0x014c"], []))

        path = build_fixture(directory, asset="partial.tar.gz", x32_dlls=X32_DLLS[:3])
        cases.append(("ddraw and friends missing", path, ["missing d3d10core.dll for x32"], []))

        path = build_fixture(directory, asset="arch.tar.gz", arch_dirs=("x86", "amd64"))
        cases.append(("arch directories Autorun does not know", path,
                      ["no DLL under a x32/ directory"], []))

        path = build_fixture(directory, asset="long.tar.gz",
                             long_name="a" * 60 + "/b" * 25 + "/d3d9.dll")
        cases.append(("a member path over 100 bytes", path, ["does not fit ustar"], []))

        path = build_fixture(directory, asset="dxvk-sarek-v1.14.1.tar.gz")
        cases.append(("an asset with a v prefix", path, [], []))

        path = build_fixture(directory, asset="dxvk-1.14.1.tar.gz")
        cases.append(("an asset name Autorun ignores", path,
                      ["is not one Autorun matches for"], []))

        path = build_fixture(directory, asset="dxvk-sarek-1.15.0.tar.gz")
        cases.append(("a release newer than the options table",
                      path, [], ["newer than 1.14.1"]))

        failures = 0
        for description, archive, want_errors, want_warnings in cases:
            report = check_archive(archive, "1.14.1")
            found = report.errors
            missing = [text for text in want_errors
                       if not any(text in error for error in found)]
            unexpected = [] if want_errors else found

            if missing or unexpected:
                failures += 1
                print("FAIL  %s" % description)
                for text in missing:
                    print("        expected an error containing %r, got %r" % (text, found))
                for text in unexpected:
                    print("        unexpected error: %s" % text)
            else:
                print("ok    %s" % description)

    print()
    print("%d/%d self-test cases passed" % (len(cases) - failures, len(cases)))
    return failures == 0


def main(argv):
    if "--self-test" in argv[1:]:
        return 0 if self_test() else 1

    if len(argv) != 2:
        print(__doc__.strip().splitlines()[0])
        print("usage: %s <tarball> | --self-test" % argv[0])
        return 2

    path = argv[1]
    expected = None

    release_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "RELEASE")
    if os.path.exists(release_file):
        with open(release_file, "r", encoding="utf-8") as handle:
            expected = handle.read().strip() or None

    report = check_archive(path, expected)

    for warning in report.warnings:
        print("warning: %s" % warning)
    for error in report.errors:
        print("error: %s" % error)

    if report.ok():
        print("%s: ok for Autorun" % os.path.basename(path))
        return 0

    print("%s: %d problem(s), Autorun would not install this"
          % (os.path.basename(path), len(report.errors)))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
