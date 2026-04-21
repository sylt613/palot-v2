#!/usr/bin/env python3

import json
import os
import urllib.request

FAL_KEY = "1e07659e-01b6-4bc7-921d-dd31007c3943:6f83a8d477010bc95bc5f3a0391279a1"
OUT_PATH = "/root/Desktop/palot/version2/ornaments/nano_banana2_sheet.png"

PROMPT = (
    "Create a single ornament specimen sheet for a Hebrew sefer, on a pure white background, "
    "in monochrome black and gray with an embossed engraved relief look, classic Hasidic / old sefer title-page style. "
    "The sheet should contain seven separate ornamental designs arranged cleanly in rows with lots of white space between them. "
    "No text labels anywhere in the image. No Hebrew letters. No symbols. Only ornaments. "
    "\n\n"
    "Top row: one wide chapter divider, centered, horizontal, symmetrical, compact and weighty, suitable for end of ענף. "
    "Second row: one medium table-of-contents divider, centered, horizontal, symmetrical, calmer than the chapter divider. "
    "Third row left: one compact narrow vertical side flanker for a chapter heading, like a spear-crown / flame-leaf motif, self-contained, not a frame. "
    "Third row right: one tiny TOC printer's mark accent, diamond-based with four restrained points, very small and classic, not logo-like. "
    "Fourth row: one very long horizontal header leader that is mostly a continuous straight rule, and ONLY at the far right end the line curls upward and around once, hooking gently back. The left end stays plain. No center ornament. No second curl. "
    "Fifth row: one minimal footnote separator, very thin horizontal rule with a tiny centered accent. "
    "Bottom row: one rectangular title frame / cartouche for a mamar heading, with straight sides, empty white center, and restrained embossed corner ornamentation. "
    "\n\n"
    "Art direction: not airy Art Nouveau, not Rococo, not Victorian clip-art excess. Weighty, noble, precise, engraved, print-ready."
)


def main():
    url = "https://fal.run/fal-ai/nano-banana-2"
    payload = {
        "prompt": PROMPT,
        "image_size": {"width": 1536, "height": 1536},
        "num_images": 1,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Key {FAL_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        result = json.loads(resp.read())
    img_url = result["images"][0]["url"]
    with urllib.request.urlopen(img_url, timeout=120) as resp:
        data = resp.read()
    with open(OUT_PATH, "wb") as f:
        f.write(data)
    print(OUT_PATH)


if __name__ == "__main__":
    main()
