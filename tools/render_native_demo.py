#!/usr/bin/env python3
"""Make an English-captioned video from selected archived QNX events."""

import argparse
import json
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "docs/evidence/qnx-validation-2026-11-23/shadow-validation"
SIZE = 1280, 720
FPS = 24
# Each entry names an archived decision and its exact input sequence.
SCENES = (
    ("default-reject-delay", 23, "output_stopped", "Default budget failed",
     "The broker withheld a late decision. The original run ends here.", 8),
    ("functional-reject-units", 2, "candidate_rejected", "A live worker can be wrong",
     "A position outside the contract range leaves the baseline in charge.", 7),
    ("functional-reject-delay", 2, "candidate_rejected", "The candidate missed its IPC cutoff",
     "The current baseline sample was published within the 75 ms decision budget.", 7),
    ("functional-rollback", 4, "candidate_promoted", "Qualification transfers authority",
     "The qualified candidate starts supplying output at the input boundary.", 7),
    ("functional-rollback", 8, "candidate_rolled_back", "A probation fault returns authority",
     "The candidate disconnected. The broker used the current baseline sample.", 7),
    ("functional-live", 81, "candidate_committed", "Probation completes and the baseline retires",
     "This separate live-attachment run promoted at input 42 and committed at 81.", 8),
)


def read_scene(stem, sequence, event):
    with (ARCHIVE / (stem + ".jsonl")).open(encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    start, summary = rows[0], rows[-1]
    if start["backend"] != "qnx-native-ipc" or summary["type"] != "summary":
        raise ValueError(f"Unexpected transport or incomplete archive: {stem}")
    row, = [row for row in rows if row.get("sequence") == sequence and row.get("event") == event]
    owner = row["source"]
    output = row["output"]
    if owner == "none" and output is None:
        selected = "None"
    elif owner in ("baseline", "candidate") and output == row[owner]["position"]:
        selected = f"{owner.title()}  /  {output:.8f} m"
    else:
        raise ValueError(f"Selected output does not match its source: {stem}/{sequence}")

    return (
        f'{start["period_ms"]} ms period  /  {start["deadline_ms"]} ms decision budget',
        f'Input {sequence}   |   {row["decision_latency_ms"]:.3f} ms to decision',
        row["reason"],
        selected,
        f'{summary["outputs"]} selected outputs  /  {summary["missing_outputs"]} missing',
    )


def draw_card(font_file, index, stem, sequence, event, title, caption):
    budget, decision, reason, selected, totals = read_scene(stem, sequence, event)
    background, panel = "#0c1723", "#172939"
    white, muted, teal, orange = "#f2f7fa", "#aabfce", "#71e4d0", "#ffb27d"
    accent = orange if event in ("output_stopped", "candidate_rejected") else teal
    frame = Image.new("RGB", SIZE, background)
    draw = ImageDraw.Draw(frame)

    def label(x, y, value, size, color=white, limit=1152):
        font = ImageFont.truetype(font_file, size)
        if draw.textlength(value, font=font) > limit:
            raise ValueError(f"Caption exceeds frame: {value}")
        draw.text((x, y), value, font=font, fill=color, anchor="lt")

    label(64, 43, "SHADOWDEPLOY  /  NATIVE QNX TRACE", 24, teal)
    label(64, 86, "QEMU TCG x86-64 guest  /  archived 2026-11-23  /  hardware timing unmeasured", 22, muted)
    draw.line((64, 130, 1216, 130), fill="#334b5b", width=2)
    label(64, 162, title, 44)
    label(64, 231, budget, 27, muted)

    draw.rounded_rectangle((64, 286, 1216, 508), radius=18, fill=panel)
    draw.rectangle((64, 307, 69, 486), fill=accent)
    label(93, 316, decision, 30)
    label(93, 371, reason, 29, accent)
    label(93, 429, "Selected output", 23, muted)
    label(295, 425, selected, 32, limit=885)
    label(64, 536, caption, 28)
    label(64, 595, f"Source file: {stem}.jsonl", 22, muted)
    label(64, 633, f"Full run: {totals}", 22, muted)
    for step in range(len(SCENES)):
        x = 64 + step * 193
        draw.rectangle((x, 682, x + 187, 685), fill=accent if step <= index else "#334b5b")
    return cv2.cvtColor(np.asarray(frame), cv2.COLOR_RGB2BGR)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", required=True, help="Path to a TrueType font")
    parser.add_argument("output", type=Path, help="Destination MP4")
    args = parser.parse_args()
    frames = [
        (draw_card(args.font, index, *scene[:-1]), scene[-1])
        for index, scene in enumerate(SCENES)
    ]
    writer = cv2.VideoWriter(str(args.output), cv2.VideoWriter_fourcc(*"avc1"), FPS, SIZE)
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not open an H.264 MP4 writer")
    try:
        for frame, seconds in frames:
            for _ in range(FPS * seconds):
                writer.write(frame)
    finally:
        writer.release()


if __name__ == "__main__":
    main()
