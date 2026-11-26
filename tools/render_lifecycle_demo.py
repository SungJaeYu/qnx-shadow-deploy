#!/usr/bin/env python3
"""Render the recorded QNX lifecycle demonstration from a Markdown summary."""

import argparse
import re
import subprocess
import tempfile
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "docs/evidence/live-lifecycle-2026-11-26.md"
WIDTH, HEIGHT = 1280, 720
INK = "#eaf3fb"
MUTED = "#adc1d4"
TEAL = "#59e5ca"
AMBER = "#ffd28a"
PANEL = "#172a3e"
PHASES = ("Replay", "Qualify", "Promote", "Rollback", "Retry", "Commit")
CARD_SECONDS = (5, 7, 6, 7, 7, 8)


def evidence():
    _, guest = SUMMARY.read_text(encoding="utf-8").split(
        "## QNX x86-64 guest observation\n", 1
    )
    guest, _ = guest.split("\n## ", 1)
    values = dict(re.findall(r"^\| ([^|]+?) \| ([^|]+?) \|$", guest, re.M))

    def match(pattern):
        result = re.search(pattern, guest)
        if result is None:
            raise ValueError(f"Expected QNX guest observation is missing: {pattern}")
        return result.groups()

    baseline, first, second = match(
        r"connected to baseline (\d+)\s+and first candidate (\d+)"
        r"[\s\S]*?second candidate (\d+)"
    )
    period, budget, cutoff = match(
        r"(\d+) ms period, (\d+) ms decision budget and\s+([\d.]+) ms IPC cutoff"
    )
    (revision,) = match(r"built from clean commit\s+`([0-9a-f]+)`")
    (strict_exit,) = match(r"Strict timing exited (\d+)")
    return values, baseline, first, second, period, budget, cutoff, revision, strict_exit


def label(x, y, content, size, color=INK, bold=False):
    weight = "bold" if bold else "normal"
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
        f'fill="{color}">{escape(content)}</text>'
    )


def rect(x, y, width, height, fill, radius=0):
    return (
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
        f'fill="{fill}" rx="{radius}"/>'
    )


def card(active, chapter, headline, lines, tiles, footer, highlight=TEAL):
    elements = [
        rect(0, 0, WIDTH, HEIGHT, "#101d2e"),
        rect(0, 0, WIDTH, 6, TEAL),
        label(70, 63, "SHADOWDEPLOY   /   QNX GUEST RECORD", 25, TEAL, True),
        label(1022, 63, "26 NOV 2026", 24, MUTED),
        label(70, 140, chapter, 24, highlight, True),
        label(70, 212, headline, 59, INK, True),
    ]
    elements.extend(label(72, 273 + 37 * index, line, 27, MUTED)
                    for index, line in enumerate(lines))

    for index, (caption, value) in enumerate(tiles):
        x = 70 + 385 * index
        elements.extend([
            rect(x, 355, 370, 125, PANEL, 14),
            label(x + 22, 393, caption, 22, MUTED),
            label(x + 22, 452, value, 41, highlight, True),
        ])

    for index, phase in enumerate(PHASES):
        x = 70 + 191 * index
        accent = highlight if index in active else MUTED
        elements.extend([
            rect(x, 545, 178, 5, accent if index in active else "#395168", 2),
            label(x + 1, 587, phase, 24, accent, index in active),
        ])

    elements.extend([
        rect(70, 623, 1140, 1, "#395168"),
        label(70, 672, footer, 23, MUTED),
    ])
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'font-family="DejaVu Sans, sans-serif">{"".join(elements)}</svg>'
    )


def story():
    values, baseline, first, second, period, budget, cutoff, revision, strict_exit = evidence()
    source = f"QNX 8.0.0 x86-64  /  QEMU TCG  /  Run revision {revision[:8]}"
    rate_hz = float(values["Effective selected rate"].split()[0])
    lateness_ms = float(values["Max nominal-grid lateness"].split()[0])
    return [
        card((), "RECORDED RUN SUMMARY", "Crash, rollback, and retry", (
            "Two candidate generations run under the same QNX broker.",
            "This recap is rendered from the recorded observations.",
        ), (
            ("Sample period", f"{period} ms"), ("Decision budget", f"{budget} ms"),
            ("IPC cutoff", f"{cutoff} ms"),
        ), source),
        card((0, 1), "BASELINE OWNS OUTPUT", "Replay, then qualify", (
            "The driver waits for 20 inputs before staging generation 1.",
            "Replay warms the candidate. Qualification uses fresh inputs.",
        ), (
            ("Selected output", "Baseline"), ("Baseline PID", baseline),
            ("Candidate PID", first),
        ), source),
        card((2,), "GENERATION 1 ENTERS PROBATION", "First promotion", (
            "The candidate takes output authority at a sample boundary.",
            "The same baseline stays warm and available for fallback.",
        ), (
            ("Promotion sequence", values["Generation 1 promoted"]), ("Selected output", "Candidate 1"),
            ("Standby PID", baseline),
        ), source),
        card((3,), "CANDIDATE EXIT DURING PROBATION", "Rollback to the baseline", (
            "The driver signals the owned candidate PID after promotion.",
            "The broker selects the baseline result for the rollback input.",
        ), (
            ("Signaled PID", first), ("Rollback sequence", values["Rollback to the current baseline"]),
            ("Selected PID", baseline),
        ), source, AMBER),
        card((4, 5), "GENERATION 2 COMPLETES PROBATION", "Retry and commit", (
            "After reaping generation 1, the broker stages the same artifact.",
            "The new process replays history, qualifies fresh and commits.",
        ), (
            ("Second promotion sequence", values["Generation 2 promoted"]),
            ("Commit sequence", values["Generation 2 committed"]), ("Committed PID", second),
        ), f"Baseline PID {baseline} stayed in place through commit and then retired."),
        card((), "FUNCTIONAL CHECKS PASSED", "Strict timing failed", (
            f"Strict verifier exit {strict_exit}. The skipped nominal slot failed the cadence check.",
            f"Guest rate {rate_hz:.3f} Hz. Max nominal-grid lateness {lateness_ms:.3f} ms.",
        ), (
            ("Selected / requested", values["Selected / requested outputs"]),
            ("Decision deadline misses", values["Decision deadline misses"]),
            ("Skipped nominal slots", values["Skipped nominal slots"]),
        ), "Physical hardware timing remains unmeasured.", AMBER),
    ]


def render(output):
    with tempfile.TemporaryDirectory(prefix="shadow-lifecycle-") as directory:
        work = Path(directory)
        playlist = []
        for index, (scene, seconds) in enumerate(zip(story(), CARD_SECONDS), 1):
            svg = work / f"scene-{index}.svg"
            png = svg.with_suffix(".png")
            svg.write_text(scene, encoding="utf-8")
            subprocess.run(["magick", "-background", "#101d2e", str(svg), str(png)], check=True)
            playlist.extend((f"file '{png.name}'", f"duration {seconds}"))

        # The concat demuxer needs a final marker to honor the last still's duration.
        playlist.append(f"file '{png.name}'")
        concat = work / "scenes.ffconcat"
        concat.write_text("\n".join(playlist) + "\n", encoding="utf-8")
        subprocess.run([
            "ffmpeg", "-v", "error", "-y", "-f", "concat", "-i", str(concat),
            "-t", str(sum(CARD_SECONDS)), "-vf", "fps=24", "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-crf", "20", "-movflags", "+faststart", str(output),
        ], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="destination MP4 path")
    render(parser.parse_args().output.resolve())


if __name__ == "__main__":
    main()
