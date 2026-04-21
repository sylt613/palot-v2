#!/usr/bin/env python3

"""Build a hand-drawn geometry guide and restyle it with nano-banana-2.

This is the preferred workflow for the geometry-sensitive ornaments:
- draw the exact structure by hand first
- include a few trusted style exemplars from the current ornament set
- ask nano-banana-2 to restyle the strict guide into the same engraved family
"""

import base64
import json
import os
import urllib.request

from PIL import Image, ImageDraw

FAL_KEY = "1e07659e-01b6-4bc7-921d-dd31007c3943:6f83a8d477010bc95bc5f3a0391279a1"
BASE_DIR = "/root/Desktop/palot/version2/ornaments"
GUIDE_OUT = os.path.join(BASE_DIR, "nano_banana2_guided_input.png")
RESULT_OUT = os.path.join(BASE_DIR, "nano_banana2_guided_output.png")

DIVIDER_IN = os.path.join(BASE_DIR, "divider_post_anaf.png")
TOC_DIVIDER_IN = os.path.join(BASE_DIR, "divider_toc.png")
FOOTNOTE_IN = os.path.join(BASE_DIR, "footnote_separator.png")
FRAME_IN = os.path.join(BASE_DIR, "mamar_title_frame.png")


def paste_contained(dst, src, box):
    x0, y0, x1, y1 = box
    tw = x1 - x0
    th = y1 - y0
    scale = min(tw / src.width, th / src.height)
    nw = max(1, int(src.width * scale))
    nh = max(1, int(src.height * scale))
    img = src.resize((nw, nh), Image.LANCZOS)
    x = x0 + (tw - nw) // 2
    y = y0 + (th - nh) // 2
    dst.paste(img, (x, y))


def load_rgb(path):
    return Image.open(path).convert("RGB")


def draw_header_leader(draw, box):
    x0, y0, x1, y1 = box
    cy = (y0 + y1) // 2 + 12
    left = x0 + 34
    curl_w = min(110, int((x1 - x0) * 0.16))
    line_end = x1 - curl_w
    draw.line((left, cy, line_end, cy), fill=(44, 44, 44), width=5)
    draw.line((left, cy + 7, line_end, cy + 7), fill=(135, 135, 135), width=2)

    p0 = (line_end, cy)
    p1 = (line_end + curl_w * 0.42, cy)
    p2 = (line_end + curl_w * 0.78, cy + 18)
    p3 = (line_end + curl_w * 0.76, cy + 48)
    p4 = (line_end + curl_w * 0.74, cy + 76)
    p5 = (line_end + curl_w * 0.44, cy + 82)
    p6 = (line_end + curl_w * 0.24, cy + 61)
    p7 = (line_end + curl_w * 0.06, cy + 44)
    p8 = (line_end + curl_w * 0.18, cy + 28)
    p9 = (line_end + curl_w * 0.40, cy + 26)
    draw.line((p0, p1, p2, p3, p4, p5, p6, p7, p8, p9), fill=(44, 44, 44), width=5, joint="curve")


def draw_flanker(draw, box):
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    top = y0 + 18
    bot = y1 - 16
    mid = (top + bot) // 2
    pts = [
        (cx, top),
        (cx + 26, top + 58),
        (cx + 18, mid + 40),
        (cx, bot),
        (cx - 18, mid + 40),
        (cx - 26, top + 58),
    ]
    draw.polygon(pts, outline=(52, 52, 52), width=4)
    draw.line((cx, top + 18, cx, bot - 18), fill=(52, 52, 52), width=4)
    draw.arc((cx - 50, mid - 22, cx - 6, mid + 24), 288, 78, fill=(76, 76, 76), width=3)
    draw.arc((cx + 6, mid - 22, cx + 50, mid + 24), 102, 252, fill=(76, 76, 76), width=3)
    draw.ellipse((cx - 5, mid - 6, cx + 5, mid + 4), fill=(52, 52, 52))


def draw_toc_mark(draw, box):
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    s = min(x1 - x0, y1 - y0) * 0.32
    outer = [(cx, cy - s), (cx + s, cy), (cx, cy + s), (cx - s, cy)]
    inner = [(cx, cy - s * 0.45), (cx + s * 0.45, cy), (cx, cy + s * 0.45), (cx - s * 0.45, cy)]
    draw.polygon(outer, outline=(60, 60, 60), width=4)
    draw.polygon(inner, outline=(95, 95, 95), width=2)
    draw.line((cx - s * 0.55, cy, cx + s * 0.55, cy), fill=(60, 60, 60), width=2)
    draw.line((cx, cy - s * 0.55, cx, cy + s * 0.55), fill=(60, 60, 60), width=2)


def build_guide_sheet():
    dst = Image.new("RGB", (1536, 1536), "white")

    # Approved theme exemplars to teach style without giving up geometry control.
    paste_contained(dst, load_rgb(DIVIDER_IN), (80, 48, 1456, 286))
    paste_contained(dst, load_rgb(TOC_DIVIDER_IN), (250, 308, 1286, 456))
    paste_contained(dst, load_rgb(FOOTNOTE_IN), (230, 1132, 1306, 1234))
    paste_contained(dst, load_rgb(FRAME_IN), (120, 1250, 1416, 1492))

    draw = ImageDraw.Draw(dst)

    # Strict hand-drawn geometry guides for the problem ornaments.
    draw_header_leader(draw, (96, 504, 1440, 748))
    draw_flanker(draw, (182, 782, 496, 1102))
    draw_toc_mark(draw, (980, 826, 1160, 1006))

    dst.save(GUIDE_OUT)


def call_nano_banana():
    with open(GUIDE_OUT, "rb") as f:
        data_url = "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")

    prompt = (
        "Use the supplied reference image as a strict composition guide. "
        "The existing placed ornaments in the sheet are style exemplars; the hand-drawn lower ornaments are geometry guides. "
        "Redraw the full sheet in a unified monochrome black-and-gray engraved embossed Hasidic sefer style on pure white background. "
        "Preserve the exact structure of the hand-drawn geometry: "
        "one long header leader that is mostly a straight horizontal rule and curls upward and around only at the far right end, "
        "one narrow vertical flanker, and one tiny diamond-based TOC printer's mark. "
        "Make those hand-drawn ornaments feel like refined, premium versions of the approved exemplar ornaments already on the sheet. "
        "No text. No letters. No new symbols. No extra ornaments. "
        "Do not turn the leader into a centered ornament. Do not add a second curl. Do not make the flanker into a frame. "
        "Keep everything crisp, weighty, restrained, and print-ready."
    )

    payload = {
        "prompt": prompt,
        "image_url": data_url,
        "image_size": {"width": 1536, "height": 1536},
        "num_images": 1,
    }
    req = urllib.request.Request(
        "https://fal.run/fal-ai/nano-banana-2",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Key {FAL_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        result = json.loads(resp.read())
    url = result["images"][0]["url"]
    with urllib.request.urlopen(url, timeout=120) as resp:
        data = resp.read()
    with open(RESULT_OUT, "wb") as f:
        f.write(data)


def main():
    build_guide_sheet()
    call_nano_banana()
    print(GUIDE_OUT)
    print(RESULT_OUT)


if __name__ == "__main__":
    main()
