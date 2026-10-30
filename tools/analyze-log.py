#!/usr/bin/env python3
"""Read an Autorun run log and say where the frame time goes.

DXVK-Sarek's own log says what it compiled and whether the state cache was
warm; the runtime log says how many frames were presented, how much was read
from the card and which threads burned the cores. This reads both, lines the
frame rate up against the card reads, and prints what the numbers mean.

    tools/analyze-log.py DarkSoulsII_20261030-195124.log
    tools/analyze-log.py DarkSoulsII_*.log autorun_runtime.log

Nothing is inferred that the log does not print; a section whose input is
missing says so instead of guessing.
"""

import os
import re
import statistics
import sys

# --------------------------------------------------------------------------
# parsing

PROGRESS = re.compile(
    r"\[PROGRESS\] (?P<t>\d+)s "
    r".*?sd_reads=(?P<sd_reads>\d+) sd_ms=(?P<sd_ms>\d+) sd_mb=(?P<sd_mb>\d+) "
    r".*?cache_hits=(?P<cache_hits>\d+) cache_mb=(?P<cache_mb>\d+)/(?P<cache_max>\d+) "
    r".*?frames=(?P<frames>\d+) heap_used_mb=(?P<heap_used>\d+) heap_free_mb=(?P<heap_free>\d+)")

SDCACHE = re.compile(r"\[SDCACHE\] (?P<text>.*)")
THREADS = re.compile(r"\[THREADS\] (?P<count>\d+) threads use (?P<cores>[\d.]+) cores: (?P<list>.*)")
BALANCE = re.compile(r"\[BALANCE\] busiest core ([\d.]+)% -> ([\d.]+)%")
PLAY = re.compile(r"\[PLAY\] .* ran (\d+m|\d+h \d+m)")

DXVK_VERSION = re.compile(r"info:\s+DXVK(?:-Sarek)?: (v[\d.]+)")
DXVK_CONFIG = re.compile(r"^\s*info:\s{2,}(?P<key>(?:dxvk|d3d9|d3d11|dxgi)\.[A-Za-z0-9_]+) = (?P<value>.*?)\s*$")
DXVK_CACHE = re.compile(r"DXVK: Read (?P<count>\d+) valid state cache entries")
DXVK_METHOD = re.compile(r"DXVK: Shader compilation method: (?P<method>\w+)")
DXVK_WORKERS = re.compile(r"DXVK: Using (?P<count>\d+) (?P<kind>[a-z ]*)compiler threads")
DXVK_NEW_CACHE = re.compile(r"Creating new state cache file")
DXVK_DEGRADED = re.compile(r"warn:\s+Degraded features: (?P<count>\d+)")
DXVK_ERR = re.compile(r"^err:\s+(?P<text>.*)")
INIT_CORES = re.compile(r"\[INIT\] processors=(?P<count>\d+)")
VOODOO = re.compile(r"\[VOODOO\]")
GL_RENDERER = re.compile(r"load name=(?:.*\\)?(RndrGL|opengl32)\.(?i:dll)")
GL_SWAP = re.compile(r"\[NXGL\] WGL swap #(\d+)")
NO_FRAME = re.compile(r"\[STALL\] no new frame for (\d+) s.*?presented (\d+) times")
BUFFER_OUT = re.compile(r"a buffer is out and has not come back")


class Run:
    def __init__(self, path):
        self.path = path
        self.samples = []
        self.sd_cache = None
        self.threads = []
        self.balance = []
        self.dxvk_version = None
        self.dxvk_config = {}
        self.dxvk_cache_entries = None
        self.dxvk_method = None
        self.dxvk_workers = []
        self.dxvk_new_cache = False
        self.degraded = 0
        self.errors = []
        self.cores = None
        self.played = None
        self.voodoo = False
        self.gl_renderer = False
        self.gl_swap = 0
        self.no_frame = None
        self.buffer_out = False
        self._parse()

    def _parse(self):
        with open(self.path, "r", errors="replace") as handle:
            for line in handle:
                m = PROGRESS.search(line)
                if m:
                    self.samples.append({k: int(v) for k, v in m.groupdict().items()})
                    continue

                m = SDCACHE.search(line)
                if m:
                    self.sd_cache = m.group("text")
                    continue

                m = THREADS.search(line)
                if m:
                    self.threads.append((int(m.group("count")), float(m.group("cores")), m.group("list")))
                    continue

                m = BALANCE.search(line)
                if m:
                    self.balance.append((float(m.group(1)), float(m.group(2))))
                    continue

                m = INIT_CORES.search(line)
                if m:
                    self.cores = int(m.group("count"))

                m = PLAY.search(line)
                if m:
                    self.played = m.group(1)

                m = DXVK_VERSION.search(line)
                if m and not self.dxvk_version:
                    self.dxvk_version = m.group(1)
                    continue

                m = DXVK_CONFIG.search(line)
                if m:
                    self.dxvk_config[m.group("key")] = m.group("value")
                    continue

                m = DXVK_CACHE.search(line)
                if m:
                    self.dxvk_cache_entries = int(m.group("count"))
                    continue

                m = DXVK_METHOD.search(line)
                if m:
                    self.dxvk_method = m.group("method")
                    continue

                m = DXVK_WORKERS.search(line)
                if m:
                    self.dxvk_workers.append((int(m.group("count")), (m.group("kind") + "compiler").strip()))
                    continue

                if DXVK_NEW_CACHE.search(line):
                    self.dxvk_new_cache = True
                    continue

                m = DXVK_DEGRADED.search(line)
                if m:
                    self.degraded = int(m.group("count"))
                    continue

                if VOODOO.search(line):
                    self.voodoo = True
                    continue

                if GL_RENDERER.search(line):
                    self.gl_renderer = True
                    continue

                m = GL_SWAP.search(line)
                if m:
                    self.gl_swap = max(self.gl_swap, int(m.group(1)))
                    continue

                m = NO_FRAME.search(line)
                if m:
                    self.no_frame = (int(m.group(1)), int(m.group(2)))
                    continue

                if BUFFER_OUT.search(line):
                    self.buffer_out = True
                    continue

                m = DXVK_ERR.match(line)
                if m:
                    self.errors.append(m.group("text").strip())

    # percentiles over the intervals between samples -------------------------

    def intervals(self):
        """(seconds, fps, new card reads, megabytes read, heap used) per sample gap."""
        out = []
        for a, b in zip(self.samples, self.samples[1:]):
            seconds = b["t"] - a["t"]
            if seconds < 5:
                continue
            out.append({
                "t": b["t"],
                "seconds": seconds,
                "fps": (b["frames"] - a["frames"]) / seconds,
                "reads": b["sd_reads"] - a["sd_reads"],
                "mb": b["sd_mb"] - a["sd_mb"],
                "heap": b["heap_used"],
            })
        return out


def correlation(xs, ys):
    if len(xs) < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denominator = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return numerator / denominator if denominator else None


# --------------------------------------------------------------------------
# reporting

def heading(text):
    print()
    print(text)
    print("-" * len(text))


def report(run):
    name = os.path.basename(run.path)
    print("%s" % name)
    print("=" * len(name))

    frames = run.samples[-1]["frames"] if run.samples else 0
    seconds = run.samples[-1]["t"] if run.samples else 0
    print("%d frames over %d s%s" % (
        frames, seconds, (", ran %s" % run.played) if run.played else ""))

    # -- was DXVK in the path at all -----------------------------------------
    if run.dxvk_version is None:
        heading("DXVK never loaded")
        print("  No DXVK line appears in this log, so none of its settings apply")
        print("  to this run and no change in DXVK can alter it.")
        if run.voodoo:
            print("  * the runtime wrapped the game in dgVoodoo: the wrapper's")
            print("    d3d9.dll comes first on the DLL path, so the game talks to")
            print("    it and DXVK is never asked for.")
        if run.gl_renderer:
            print("  * the game loaded an OpenGL renderer (RndrGL.dll /")
            print("    opengl32.dll): it draws through the runtime's GL stack,")
            print("    not through Direct3D, so there is nothing for DXVK to")
            print("    intercept.")
        if not run.voodoo and not run.gl_renderer:
            print("  * neither a wrapper nor an OpenGL renderer was seen here, so")
            print("    the program likely stopped before it asked for Direct3D.")
            print("    A wrapper shows up in the runtime log as [VOODOO] lines, so")
            print("    that log is worth reading alongside this one.")
        if run.gl_swap or run.no_frame:
            heading("Where it stopped")
        if run.gl_swap:
            print("  the program reached swap #%d" % run.gl_swap)
        if run.no_frame:
            print("  no new frame for %d s, %d presented in all" % run.no_frame)
        if run.buffer_out:
            print("  the screen reports a buffer out that has not come back:")
            print("  the swap never completed, which is where the program stops")
        if run.gl_swap or run.no_frame:
            print()

    # -- what DXVK was doing -------------------------------------------------
    if run.dxvk_version is not None:
        heading("DXVK")
        print("  build            : %s" % run.dxvk_version)
    if run.dxvk_method:
        print("  compilation      : %s" % run.dxvk_method)
    for count, kind in run.dxvk_workers:
        print("  %-16s : %d threads" % (kind, count))
    if run.dxvk_cache_entries is not None:
        print("  state cache      : %d entries read" % run.dxvk_cache_entries)
    if run.dxvk_new_cache:
        print("  state cache      : REBUILT on this run -- nothing carried over")
    if run.degraded:
        print("  degraded features: %d" % run.degraded)
    for error in run.errors[:5]:
        print("  error            : %s" % error)

    interesting = ("dxvk.shaderCompilationMethod", "dxvk.numShaderCompilerThreads",
                   "dxvk.numCompilerThreads", "dxvk.framePace", "d3d9.maxFrameRate",
                   "d3d9.presentInterval", "dxgi.maxFrameRate", "dxgi.syncInterval",
                   "d3d9.samplerAnisotropy", "d3d9.maxFrameLatency")
    shown = [k for k in interesting if k in run.dxvk_config]
    if shown:
        print("  settings         :")
        for key in shown:
            print("    %s = %s" % (key, run.dxvk_config[key]))

    # -- the frame timeline --------------------------------------------------
    intervals = run.intervals()
    if intervals:
        heading("Frame rate against card reads")
        print("   time      fps   card reads   MB read   heap used")
        for row in intervals:
            bar = "#" * min(30, int(row["fps"] / 2))
            print("  %4d s %6.1f %+8d %10d %8d  %s" % (
                row["t"], row["fps"], row["reads"], row["mb"], row["heap"], bar))

        fps = [r["fps"] for r in intervals]
        reads = [r["reads"] for r in intervals]
        corr = correlation(fps, reads)
        quiet = [r["fps"] for r in intervals if r["reads"] < 20]
        busy = [r["fps"] for r in intervals if r["reads"] >= 100]
        playing = [r["fps"] for r in intervals if r["fps"] > 5]

        heading("Where it stalls")
        if not playing or len(intervals) < 3:
            print("  the program never got going: %.1f fps over %d intervals."
                % (max(fps), len(intervals)))
            print("  There is nothing to compare here; look at what it was")
            print("  waiting for at the end of the log instead of at frame times.")
            print()
            playing = []

        if playing:
            print("  median while playing     : %.1f fps" % statistics.median(playing))
        if quiet and playing:
            print("  intervals with <20 reads : %d, median %.1f fps" % (len(quiet), statistics.median(quiet)))
        if busy and playing:
            print("  intervals with >=100 reads: %d, median %.1f fps" % (len(busy), statistics.median(busy)))
        if corr is not None and playing:
            print("  correlation fps/reads    : %.2f" % corr)
            if corr < -0.4 and quiet and busy:
                print("  -> the dips follow the card, not the shaders: this is asset streaming.")
            elif busy and statistics.median(busy) < statistics.median(quiet) - 5:
                print("  -> dips are more likely with card reads, but the link is loose.")
            elif quiet and busy:
                print("  -> the dips do not line up with card reads; look at the threads below.")

    # -- threads -------------------------------------------------------------
    if run.threads:
        heading("Cores")
        count, cores_used, listing = run.threads[-1]
        print("  %d threads, %.2f cores busy in the last sample" % (count, cores_used))
        if run.cores:
            print("  the console gave the program %d cores" % run.cores)
            headroom = run.cores - cores_used
            print("  headroom             : %.2f cores" % headroom)
        # Every sample, not just the last: compilation is a burst, and a run
        # can end long after the burst did.
        compiler_peak = 0.0

        for _, _, sample_list in run.threads:
            for entry in sample_list.split():
                if "dxvk-shader" not in entry and "dyasync" not in entry:
                    continue
                share = re.search(r"(\d+(?:\.\d+)?)%$", entry)
                if share:
                    compiler_peak = max(compiler_peak, float(share.group(1)))

        if compiler_peak >= 5.0:
            print("  compiler threads       : peaked at %.1f% of a core on their own" % compiler_peak)
            print("  -> compilation is taking frame time here")
        elif run.threads:
            print("  compiler threads       : never above 5% of a core")
            print("  -> compilation is not the bottleneck in this run")

    # -- advice --------------------------------------------------------------
    advice = []

    if run.dxvk_method == "async":
        advice.append(
            "shaderCompilationMethod is async: objects are not drawn until their pipeline "
            "exists, and the queue is unbounded. On few cores dyasync keeps a placeholder "
            "on screen instead, which is what this fork defaults to.")

    limiters = []
    if run.dxvk_config.get("d3d9.presentInterval", "1") == "0":
        limiters.append("vsync off")
    if run.dxvk_config.get("d3d9.maxFrameRate", "0") in ("0", ""):
        limiters.append("no frame limit")
    if len(limiters) == 2:
        advice.append(
            "vsync off and no frame limit: every dip shows up as a torn, uneven frame "
            "instead of costing one vblank. Either cap at the panel rate or turn vsync on, "
            "then compare -- the frame rate may be identical while it feels steadier.")

    if run.dxvk_cache_entries is not None and run.dxvk_cache_entries < 2000:
        advice.append(
            "the state cache holds %d entries. If a run does not add to it, new pipelines are "
            "recompiled every session; compare the count across two runs." % run.dxvk_cache_entries)

    if run.sd_cache and "0 to 0 MB" in run.sd_cache:
        advice.append(
            "the runtime's card read cache reports 0 MB: %s" % run.sd_cache)

    if advice:
        heading("Worth trying")
        for line in advice:
            print("  * %s" % line)


def main(argv):
    paths = argv[1:]
    if not paths:
        print(__doc__.strip())
        return 2

    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        for p in missing:
            print("error: %s does not exist" % p)
        return 1

    for index, path in enumerate(paths):
        if index:
            print()
        report(Run(path))

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
