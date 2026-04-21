#!/usr/bin/env python3
"""Generate exact-size modern ornaments for Palaot v2 via nano-banana-2 edit.

One ornament per image, with exact target dimensions and explicit whitespace.
The goal is to avoid downstream cropping whenever possible.
"""

import base64
import json
import os
import socket
import time
import urllib.request

from PIL import Image


FAL_KEY = "150589bb-6e4b-4062-a0a3-398b5b9b3799:0fc8fe71094bafa697059c26987cde25"
BASE_DIR = os.path.dirname(__file__)
OUT_DIR = os.path.join(BASE_DIR, "ornaments")
os.makedirs(OUT_DIR, exist_ok=True)

REFERENCE_IMAGE = "/data/projects/seforim-ornaments/output/modern-flourish/sheet.png"

PAGE_WIDTH_IN = 5.5
PAGE_HEIGHT_IN = 8.5
MARGIN_INNER_IN = 0.5
MARGIN_OUTER_IN = 0.5
CONTENT_WIDTH_IN = PAGE_WIDTH_IN - MARGIN_INNER_IN - MARGIN_OUTER_IN
COL_GAP_IN = 14 / 72.0
COL_WIDTH_IN = (CONTENT_WIDTH_IN - COL_GAP_IN) / 2.0
SHARED_SEED = int(os.environ.get("ORNAMENT_SEED", "41731"))
MAX_CHAINED_REFERENCES = int(os.environ.get("ORNAMENT_REF_CHAIN", "4"))
REQUEST_RETRIES = int(os.environ.get("ORNAMENT_RETRIES", "3"))

PROMPT_PREFIX = (
    "Use the supplied reference image only as style inspiration. "
    "Keep the same approved modern engraved sefer ornament family: monochrome black and gray only, pure white background, "
    "clean contemporary sefer dignity, crisp print-ready contours, restrained relief, no muddy shading, no text, no letters, no labels. "
    "All ornaments in this project must look like they belong to one single coordinated suite made by the same designer at the same time. "
    "Match stroke logic, darkness, engraving density, corner behavior, and silhouette discipline across the full set. "
    "These ornaments will print very small on a 5.5 by 8.5 inch sefer page, so avoid micro-detail. "
    "Use fewer, larger forms with open breathing space and visibly legible structure at small print size. "
    "If a detail would disappear when printed small, omit it. "
    "Output exactly one isolated ornament, centered, with no extra decorations outside the requested shape. "
    "Respect the requested image dimensions and the requested blank whitespace exactly. "
    "Do not place a small ornament loosely inside a large blank image. Make the ornament intentionally sized for the requested canvas. "
)


def data_url_for(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")


ORNAMENTS = [
    {
        "name": "mamar_title_frame",
        "width": 1200,
        "height": 420,
        "physical": "4.00in wide x 1.40in tall at 300 dpi",
        "prompt": (
            "Create one rectangular mamar title frame. Final canvas size exactly 1200 by 420 pixels. "
            "This will typically print around 3.8 inches wide and about 0.80 inches tall on the page, so make the motif bold enough for that small size. "
            "Leave a clean empty inner text area of exactly 1010 by 282 pixels centered inside the frame. "
            "Treat that inner rectangle as a strict protected text-safe zone: no border mass, no corner curls, no shading, no inner nubs, and no decorative points may enter it at all. "
            "The frame should feel rounded and refined, with softened rounded corners rather than stiff square corners, but still dignified and engraved rather than soft like a greeting card. "
            "Push the visual weight outward into the outer border band so the text area feels roomy and relaxed. "
            "Keep especially generous vertical breathing room above and below the text-safe zone so Hebrew title lines do not feel crowded. "
            "Use slightly heavier strokes than before, but keep the heavier weight in the outer perimeter and corners, not pressing inward toward the text. "
            "No extra gray side pieces, bottom tabs, side wings, or protruding appendages outside or below the main rectangle. "
            "No floating extra flourishes outside the frame boundary. No crop needed afterward."
        ),
    },
    {
        "name": "toc_mamar_frame",
        "width": 960,
        "height": 260,
        "physical": "3.20in wide x 0.87in tall at 300 dpi",
        "prompt": (
            "Create one smaller rectangular frame for the TOC mamar box. Final canvas size exactly 960 by 260 pixels. "
            "This will typically print around 3.5 inches wide and about 0.75 inches tall, so keep the design open and bold enough to read clearly at that size. "
            "Leave a centered inner blank text area of exactly 848 by 182 pixels. "
            "Treat that inner rectangle as a strict protected text-safe zone: no ornament mass, no corner shading, no inward hooks, no inner points, and no decorative protrusions may enter it. "
            "This frame should match the same modern sefer family as the main mamar frame, with rounded refined corners and slightly heavier strokes, but remain calmer and more compact. "
            "The border should hug the outer edge discipline of the rectangle while leaving the inside feel clearly roomier than before. "
            "Keep broad clear top and bottom inner breathing room for the TOC text. "
            "No extra gray pieces, no bottom side protrusions, no side tabs, and no decorations floating outside the frame boundary. No crop needed afterward."
        ),
    },
    {
        "name": "divider_post_anaf",
        "width": 900,
        "height": 210,
        "physical": "3.00in wide x 0.70in tall at 300 dpi",
        "prompt": (
            "Create one end-of-section divider ornament. Final canvas size exactly 900 by 210 pixels. "
            "This will print only about 1.28 inches wide and about 0.30 inches tall, so the design must be very simple, slightly thicker, and immediately legible. "
            "Keep at least 110 pixels of pure white breathing room on the left and right edges, and at least 52 pixels on the top and bottom. "
            "The ornament must use the same curving scroll and volute language as the header flourish in the reference images — "
            "flowing engraved curls, clean black pen-weight strokes, elegant S-curves and C-curves. "
            "Use a centered horizontal arrangement with a simpler central knot and shorter symmetrical scroll extensions curling outward on both sides. "
            "Avoid complexity, layered inner loops, and fussy small secondary tendrils. "
            "Use fewer strokes than before and make those strokes a bit heavier. "
            "No diamonds, no angular geometric shapes. Only smooth curving scroll forms that match the header flourish family. "
            "The silhouette should read clearly at print size. No crop needed afterward."
        ),
    },
    {
        "name": "divider_toc",
        "width": 720,
        "height": 168,
        "physical": "2.40in wide x 0.56in tall at 300 dpi",
        "prompt": (
            "Create one smaller TOC divider ornament. Final canvas size exactly 720 by 168 pixels. "
            "This will print only about 0.92 inches wide and about 0.21 inches tall, so it must be very simple with only a few large readable strokes. "
            "Keep at least 86 pixels of pure white space on the left and right edges and at least 40 pixels on the top and bottom. "
            "The ornament must use the same curving scroll and volute language as the header flourish in the reference images — "
            "flowing engraved curls, clean thin black pen-weight strokes on white background, elegant S-curves and C-curves. "
            "Use a small central scroll knot with short symmetrical curling extensions on each side. "
            "This should be calmer and simpler than the end-of-anaf divider, still centered and symmetrical. "
            "IMPORTANT: No diamonds, no angular geometric shapes, no filled gray areas, no stars. Only smooth curving thin black scroll forms. "
            "No extra ornaments beyond the single divider. No crop needed afterward."
        ),
    },
    {
        "name": "toc_entry_accent",
        "width": 180,
        "height": 180,
        "physical": "0.60in x 0.60in at 300 dpi",
        "prompt": (
            "Create one tiny scroll accent mark. Final canvas size exactly 180 by 180 pixels, which represents 0.60 by 0.60 inches at 300 dpi. "
            "Keep the ornament itself inside an imaginary centered square of about 96 by 96 pixels, leaving generous pure white around it. "
            "The ornament must use the same curving scroll and volute language as the header flourish in the reference images — "
            "a small compact scroll or double-curl motif using thin clean black pen-weight strokes on white background. "
            "Think of a tiny fleuron made of two mirrored C-curves or a small scroll knot. "
            "IMPORTANT: No stars, no diamonds, no angular geometric shapes, no filled gray areas. Only smooth curving thin black scroll forms. "
            "It should read cleanly at small size, with a strong centered silhouette. No crop needed afterward."
        ),
    },
    {
        "name": "anaf_flanker",
        "width": 150,
        "height": 210,
        "physical": "0.50in wide x 0.70in tall at 300 dpi",
        "prompt": (
            "Create one single horizontal side-flourish ornament for anaf headings. Final canvas size exactly 150 by 210 pixels. "
            "This will print only about 0.28 inches wide and about 0.39 inches tall, so it must stay bold and readable, but may carry a little more detail than before. "
            "Compose the ornament as a single sideways flourish centered on the canvas, meant to be mirrored for left and right sides. "
            "Keep at least 24 pixels of pure white padding on all sides. "
            "The ornament must use the same curving scroll and volute language as the header flourish in the reference images — "
            "flowing black pen-weight strokes on pure white background, elegant S-curves and C-curves. "
            "Make it a single horizontal flourish with one dominant outward curl, one smaller returning inner curl, and a compact connecting stem so it feels richer than the tiny header accent but still like one ornament, not a stacked pair. "
            "Avoid vertical double-scroll compositions, stacked top-and-bottom motifs, tiny leaflets, fuzz, or fragile micro-detail. "
            "IMPORTANT: Use clean black linework only, no gray fill, no shading, no 3D effect. "
            "The line weight should be slightly heavier than before so it stands up better next to FrankReal. "
            "No diamonds, no spear-leaves, no geometric shapes. Only smooth curving scroll forms. "
            "No frame, no horizontal spread, no crop needed afterward."
        ),
    },
    {
        "name": "subhead_rule_right",
        "width": 660,
        "height": 60,
        "physical": f"{COL_WIDTH_IN:.2f}in wide x 0.20in tall at 300 dpi",
        "prompt": (
            "Create one full-width subheading border rule for a single text column. Final canvas size exactly 660 by 60 pixels. "
            f"This will print at about {COL_WIDTH_IN:.2f} inches wide and about 0.20 inches tall, so it must be designed specifically for that narrow width and low height. "
            "Use the same modern engraved sefer family as the running header ornaments. "
            "Most of the width should be a calm horizontal rule, visually strong enough at small size, with a single elegant curl-back terminal flourish only at the far right end. "
            "The far left end must stay plain, straight, and clean with no ornament. "
            "The right terminal should curl back inward in one restrained engraved motion, not a large floral bouquet. "
            "Keep the rule very low and horizontal, with the flourish using most of the limited height but staying compact. "
            "No center ornament, no second flourish, no fade, no diamonds, no gray blobs, and no tiny decorative chatter. "
            "This asset will be used above and below centered subheadings inside the column, so it must feel dignified and quiet while still visibly ornamental. "
            "No crop needed afterward."
        ),
    },
    {
        "name": "footnote_separator",
        "width": 1050,
        "height": 60,
        "physical": "3.50in wide x 0.32in tall at 300 dpi",
        "prompt": (
            "Create one ornamental footnote separator. Final canvas size exactly 1050 by 60 pixels. "
            "This will print around 3 inches wide and only about 0.17 inches tall, so it must be understated and almost typographic. "
            "Most of the width should be a thin quiet horizontal rule using the same stroke weight as the header leader rule. "
            "Keep the ornament extremely low vertically. "
            "Place a very small centered scroll knot or double-curl accent in the middle — using the same curving scroll and volute language as the header flourish. "
            "IMPORTANT: No diamonds, no angular geometric shapes, no filled gray areas. Only smooth curving thin black scroll forms. "
            "Keep at least 110 pixels of blank white space at both left and right ends. "
            "Use no micro-detail. No crop needed afterward."
        ),
    },
    {
        "name": "title_rule_right",
        "width": 1320,
        "height": 200,
        "physical": f"{COL_WIDTH_IN*2:.2f}in wide x 0.33in tall at 300 dpi",
        "prompt": (
            "Create one full-width title rule ornament for a Hebrew sefer. Final canvas size exactly 1320 by 200 pixels. "
            "STYLE: Look at the anaf flanker reference — same family. Clean black linework on white. "
            "DESIGN: 2 or 3 thin parallel horizontal lines run straight across the full canvas width. "
            "At the RIGHT end only, the lines gracefully sweep together and curl into a terminal. "
            "As the lines begin to curve and coil, they get slightly thicker — like a calligraphy pen stroke that swells naturally at the turn. "
            "The lines are thin and consistent when straight, then swell to slightly bolder as they complete the curl. "
            "ONE curl terminal only — one or two loops going the SAME direction, not an S-shape. "
            "CRITICAL SYMMETRY: the ornament must be perfectly top-to-bottom symmetric around the horizontal centerline. "
            "The upper half of the curl is the exact mirror of the lower half. Equal white space above and below. "
            "No gray fills, no shading, no gray wash — pure black lines on white only. "
            "The thickening happens naturally from the calligraphic pen weight change, not from filled shapes. "
            "LEFT end: lines terminate cleanly straight, no ornament, no tapering. "
            "At least 20px white margin on top and bottom edges. Canvas exactly 1320x200px. Pure white background."
        ),
    },
    {
        "name": "header_leader_right",
        "width": 1350,
        "height": 120,
        "physical": f"{CONTENT_WIDTH_IN:.2f}in wide x 0.40in tall at 300 dpi",
        "prompt": (
            "Create one full-width ornamental title rule for a Hebrew sefer. Final canvas size exactly 1350 by 120 pixels. "
            f"This will print about {CONTENT_WIDTH_IN:.2f} inches wide and about 0.40 inches tall. "
            "The design must have TWO distinct parts: "
            "(1) A repeating tile body: an elegant engraved scroll-and-rule pattern that can tile seamlessly left-to-right. "
            "The tile should consist of a clean horizontal rule with a small refined scroll or volute accent repeating above and below it at regular intervals — "
            "think of a classic engraved chapter rule with small regularly-spaced curling accents, not a plain line. "
            "This tile body fills roughly the left 85 percent of the canvas. "
            "(2) A terminal flourish at the far RIGHT end only: one single refined engraved curl or volute that terminates the rule, "
            "extending vertically to use up to 80 percent of the canvas height. "
            "CRITICAL SYMMETRY REQUIREMENT: The tile body must be perfectly top-bottom symmetric — the pattern above the center rule must be an exact mirror of the pattern below it. "
            "This is essential because the image will be flipped vertically to create a matching bottom rule, and the tile must look identical when flipped upside-down. "
            "The terminal flourish at the right end must also be top-bottom symmetric for the same reason. "
            "Use clean black engraved linework on pure white background. No gray fills, no shading, no 3D. "
            "No center blob ornament, no diamond shapes, no angular geometry. Only smooth scroll and volute forms in the engraved sefer family. "
            "Keep at least 10 pixels of white safety margin on all four edges. "
            "The overall impression should be more stylish and richer than a plain rule but still quiet enough for a title frame."
        ),
    },
]


def call_nano_banana(prompt, width, height, reference_data_urls, seed):
    payload = {
        "prompt": prompt,
        "image_urls": reference_data_urls,
        "num_images": 1,
        "output_format": "png",
        "resolution": "1K",
        "aspect_ratio": "1:1" if width == height else None,
        "image_size": {"width": width, "height": height},
        "seed": seed,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    req = urllib.request.Request(
        "https://fal.run/fal-ai/nano-banana-2/edit",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Key {FAL_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())


def call_with_retries(prompt, width, height, reference_data_urls, seed):
    last_error = None
    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            return call_nano_banana(prompt, width, height, reference_data_urls, seed)
        except (TimeoutError, urllib.error.URLError, socket.timeout) as error:
            last_error = error
            print(f"  retry {attempt}/{REQUEST_RETRIES} after error: {error}", flush=True)
            time.sleep(min(8 * attempt, 20))
    raise last_error


def download_image(url, filepath):
    with urllib.request.urlopen(url, timeout=120) as resp:
        data = resp.read()
    with open(filepath, "wb") as f:
        f.write(data)


def whiten_to_alpha(img, cutoff=246):
    rgba = img.convert("RGBA")
    out = []
    for r, g, b, a in rgba.getdata():
        if r >= cutoff and g >= cutoff and b >= cutoff:
            out.append((255, 255, 255, 0))
        else:
            out.append((r, g, b, a))
    rgba.putdata(out)
    return rgba


def normalize_canvas(filepath, target_w, target_h):
    img = Image.open(filepath)
    img = whiten_to_alpha(img)
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)

    scale = min(target_w / img.width, target_h / img.height)
    new_w = max(1, round(img.width * scale))
    new_h = max(1, round(img.height * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 0))
    x = (target_w - new_w) // 2
    y = (target_h - new_h) // 2
    canvas.alpha_composite(img, (x, y))
    canvas.save(filepath)


def main():
    if not os.path.exists(REFERENCE_IMAGE):
        raise SystemExit(f"Missing reference image: {REFERENCE_IMAGE}")

    only = set(os.environ.get("ONLY", "").split()) if os.environ.get("ONLY") else None

    # For title_rule_right: put anaf_flanker FIRST so it is the dominant style reference
    FAMILY_SEEDS = ["anaf_flanker.png", "subhead_rule_right.png", "header_leader_right.png"]
    if only and "title_rule_right" in only:
        style_references = []
        for seed_name in FAMILY_SEEDS:
            seed_path = os.path.join(OUT_DIR, seed_name)
            if os.path.exists(seed_path):
                style_references.append(data_url_for(seed_path))
        style_references.append(data_url_for(REFERENCE_IMAGE))
    else:
        style_references = [data_url_for(REFERENCE_IMAGE)]

    manifest = []
    for orn in ORNAMENTS:
        if only and orn["name"] not in only:
            continue

        prompt = (
            PROMPT_PREFIX
            + "Use every supplied reference image as part of the same approved style family. "
            + "Keep this new ornament visibly consistent with those references, while still respecting the exact requested geometry and size. "
            + orn["prompt"]
        )
        filepath = os.path.join(OUT_DIR, f"{orn['name']}.png")
        if os.environ.get("SKIP_EXISTING") == "1" and os.path.exists(filepath):
            print(f"Skipping existing {orn['name']} -> {filepath}")
            style_references.append(data_url_for(filepath))
            style_references = [style_references[0]] + style_references[-MAX_CHAINED_REFERENCES:]
            continue
        print(f"Generating {orn['name']} {orn['width']}x{orn['height']} ({orn['physical']})...", flush=True)
        started = time.time()
        active_references = [style_references[0]] + style_references[-MAX_CHAINED_REFERENCES:]
        result = call_with_retries(prompt, orn["width"], orn["height"], active_references, SHARED_SEED)
        images = result.get("images", [])
        if not images or not images[0].get("url"):
            raise RuntimeError(f"No image returned for {orn['name']}")
        download_image(images[0]["url"], filepath)
        normalize_canvas(filepath, orn["width"], orn["height"])
        elapsed = time.time() - started
        print(f"  OK ({elapsed:.1f}s) -> {filepath}")
        style_references.append(data_url_for(filepath))
        style_references = [style_references[0]] + style_references[-MAX_CHAINED_REFERENCES:]

        manifest.append({
            "name": orn["name"],
            "file": filepath,
            "width": orn["width"],
            "height": orn["height"],
            "physical": orn["physical"],
            "seed": SHARED_SEED,
            "prompt": prompt,
            "result": images[0],
        })

    manifest_path = os.path.join(OUT_DIR, "generation_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(manifest_path)


if __name__ == "__main__":
    main()
