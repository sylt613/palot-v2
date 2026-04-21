# Palaot v2 — Practical Agent Guide

This guide is for AI agents continuing work on this sefer project. It covers the
three core workflows: generating ornament images, splitting/tiling them for layout,
and placing them at correct size in the PDF.

## Quick Start

```bash
# 1. Restore packages (run at every session start)
bash /data/.persist/boot-shim.sh

# 2. Generate ornaments (or regenerate specific ones)
cd /data/v2-import/palot/version2
SKIP_EXISTING=1 python3 generate_ornaments.py     # only missing ones
ORNAMENT_SEED=41731 python3 generate_ornaments.py  # full regeneration

# 3. Build the PDF
python3 build_v2.py
# Output: output/palaot.pdf + output/palaot-NN.png page previews
```

## Project Layout

```
/data/v2-import/palot/version2/
├── build_v2.py              # PDF builder (reportlab)
├── generate_ornaments.py    # AI ornament generation (fal API)
├── settings.py              # Page geometry, font paths, constants
├── doc_parts.json           # Parsed Hebrew content
├── ornaments/               # Generated PNG assets (8 types)
├── fonts/                   # Hebrew fonts
├── output/
│   ├── palaot.pdf           # Latest PDF
│   ├── palaot-NN.png        # Page previews (200 DPI)
│   └── jobs/                # Build archives with QA reports
└── PROJECT_GUIDE.md         # This file
```

---

## Workflow 1: Generating Good Ornament Images

### The API

We use `fal-ai/nano-banana-2/edit`. Key facts:
- Endpoint: `https://fal.run/fal-ai/nano-banana-2/edit`
- Auth key: hardcoded in `generate_ornaments.py` line 18
- **fal ignores the `image_size` parameter** — it returns whatever dimensions it wants
  (often 2144×496 or 1024×1024). The `normalize_canvas()` post-processor handles this.
- Typical latency: 15-50 seconds per image
- Accepts `image_urls` array of data-URI references for style guidance

### Prompt Engineering — What Works

The prompt has two parts: a shared prefix (style family rules) and a per-ornament body.

**The prefix establishes:**
- Monochrome black/white only, pure white background
- Modern engraved sefer style — clean, crisp, dignified
- All ornaments must look like one coordinated suite
- Designed for small print size — fewer/larger forms, no micro-detail
- One isolated ornament centered on canvas

**Per-ornament prompts must specify:**
1. **Exact canvas dimensions** in pixels — even though fal ignores this, it influences composition
2. **Actual physical print size** — e.g., "prints 0.28 inches wide" — this is critical for the AI
   to understand it should use bold simple forms, not intricate detail
3. **Whitespace padding** in pixels — prevents the ornament from touching canvas edges
4. **Shape vocabulary** — explicitly name the curves: "S-curves, C-curves, volutes, scrolls"
5. **What NOT to do** — "No diamonds, no angular geometric shapes, no filled gray areas"

**Key lesson learned:** If you don't explicitly forbid geometric shapes (diamonds, stars),
the AI defaults to them. Always include negative constraints. The same applies to gray
fill/shading — you must say "thin black linework only, no gray fill, no shading, no 3D effect."

### Style Consistency via Chained References

Each generated ornament is added as a style reference for the next one:
```
reference chain: [base_style, ornament_1, ornament_2, ...] (capped at 4 chained)
```
This makes later ornaments match earlier ones. The base reference is always:
```
/data/projects/seforim-ornaments/output/modern-flourish/sheet.png
```

To regenerate a subset while keeping style continuity, delete the target PNGs and
run with `SKIP_EXISTING=1`. Existing ornaments load as references but aren't regenerated:
```bash
rm ornaments/anaf_flanker.png ornaments/divider_toc.png
SKIP_EXISTING=1 python3 generate_ornaments.py
```

### Post-Processing Pipeline (normalize_canvas)

Every raw fal output goes through:
1. **white-to-alpha** — pixels with R,G,B all ≥ 246 become transparent
2. **trim** — `getbbox()` crops to content bounding box
3. **scale** — fit into target canvas preserving aspect ratio
4. **center** — paste onto exact target-size transparent canvas

This is why fal's unpredictable output dimensions don't matter.

### Environment Variables

| Var | Default | Purpose |
|-----|---------|---------|
| `ONLY` | (all) | Space-separated ornament names to generate |
| `SKIP_EXISTING` | 0 | Set to 1 to skip existing PNGs (still loaded as refs) |
| `ORNAMENT_SEED` | 41731 | Shared seed for suite consistency |
| `ORNAMENT_REF_CHAIN` | 4 | Max chained style references |
| `ORNAMENT_RETRIES` | 3 | Retry count for fal timeouts |

---

## Workflow 2: Splitting Images into Tileable Parts

### The Problem

The header leader ornament is a horizontal rule + terminal flourish. The AI generates
it as one piece, but:
- The content often doesn't fill the requested width (e.g., 489-860px out of 1350px)
- Different pages need different header widths
- We need the flourish on specific sides (left or right)

### The Solution: Extract + Tile

The builder (`build_v2.py` lines ~1514-1665) implements a three-step system:

#### Step 1: `_extract_flourish_terminal(img)`

Scans every column of the RGBA image using the alpha channel:
- Counts opaque pixels per column (alpha > 0)
- The plain rule body has ~14% of canvas height as opaque pixels
- The flourish terminal has 20-50% opaque pixels
- Threshold: `1.5 × median_rule_count` cleanly splits them

Returns two images:
- **tile**: A narrow 20px-wide strip from the plain rule section (tileable)
- **flourish**: Everything from the flourish start to the image edge

#### Step 2: `_build_tiled_leader(tile, flourish, width, height)`

1. Creates a canvas at the exact needed pixel dimensions
2. Tiles the rule strip repeatedly to fill the width
3. Pastes the flourish at the terminal end (right edge)

#### Step 3: `_draw_header_leader_with_terminal()`

Orchestrates the full pipeline:
1. Computes pixel dimensions from PDF point span at 300 DPI
2. Checks `_LEADER_CACHE` (keyed by `(width, height, terminal_side)`)
3. Calls extract + tile if not cached
4. **Flattens RGBA onto white RGB background** (required for reportlab — see below)
5. Uses color-range mask `[254,255,254,255,254,255]` for near-white transparency
6. Flips horizontally for left-side terminals

### Why Caching Matters

`_extract_flourish_terminal()` scans pixel data with `getdata()` — expensive per call.
Without caching, building 20 pages with headers would scan the image 20+ times.
The `_LEADER_CACHE` dict eliminates this.

---

## Workflow 3: Placing Ornaments at Correct Size

### Page Geometry

| Dimension | Value |
|-----------|-------|
| Page | 5.5" × 8.5" (396 × 612 pt) |
| Inner margin | 0.5" (36 pt) |
| Outer margin | 0.5" (36 pt) |
| Content width | 4.5" (324 pt) |
| Ornament DPI | 300 |

### Size Conversion

All ornaments are generated at 300 DPI. To convert:
- **px to inches**: `px / 300`
- **px to points**: `px / 300 × 72`
- **inches to px**: `inches × 300`

Example: `anaf_flanker.png` is 150×210 px → 0.50"×0.70" at 300 DPI → 36×50.4 pt.
But in the layout it renders smaller (12.5 pt wide) via the `flanker_render_w` setting.

### reportlab Image Rendering — The Alpha Trap

**Critical lesson:** reportlab's `mask='auto'` on RGBA images eats semi-transparent
pixels, making ornaments nearly invisible.

**Solution:**
1. Flatten the RGBA image onto a white RGB background:
   ```python
   bg = Image.new("RGB", img.size, (255, 255, 255))
   bg.paste(img, mask=img.split()[3])
   ```
2. Use a color-range mask to make near-white pixels transparent:
   ```python
   drawImage(path, x, y, w, h, mask=[254, 255, 254, 255, 254, 255])
   ```
   This tells reportlab: any pixel where R∈[254,255] AND G∈[254,255] AND B∈[254,255]
   is transparent. Pure white (255,255,255) disappears; ornament ink stays.

### Ornament Inventory

| Asset | Canvas (px) | Print Size | Notes |
|-------|-------------|------------|-------|
| `mamar_title_frame.png` | 1200×420 | 4.0"×1.4" | Title frame, engraved style |
| `toc_mamar_frame.png` | 960×260 | 3.2"×0.87" | TOC section frame |
| `divider_post_anaf.png` | 900×210 | 3.0"×0.70" | End-of-section scroll divider |
| `divider_toc.png` | 720×168 | 2.4"×0.56" | TOC scroll divider |
| `toc_entry_accent.png` | 180×180 | 0.6"×0.6" | Small scroll accent bullet |
| `anaf_flanker.png` | 150×210 | 0.5"×0.7" | Vertical scroll beside anaf title |
| `footnote_separator.png` | 1050×60 | 3.5"×0.32" | Thin rule with scroll center |
| `header_leader_right.png` | 1350×120 | 4.5"×0.4" | Tiled header rule + flourish |

---

## Common Tasks

### Regenerate a single ornament with a new prompt

1. Edit the prompt in `generate_ornaments.py` (the `ORNAMENTS` list starting at line 54)
2. Delete the old PNG: `rm ornaments/the_ornament.png`
3. Run: `SKIP_EXISTING=1 python3 generate_ornaments.py`
4. Rebuild PDF: `python3 build_v2.py`
5. Check the page preview PNGs in `output/`

### Change the visual style family

1. Replace the reference image at the path in `REFERENCE_IMAGE` (line 23)
2. Delete all ornament PNGs: `rm ornaments/*.png`
3. Run full generation: `python3 generate_ornaments.py`
4. Rebuild PDF

### Adjust ornament sizing in layout

Size constants are in `build_v2.py`. Search for the ornament name — you'll find
`orn_w`, `orn_h`, or similar variables controlling the rendered point size.
The anaf flanker for example uses `orn_w=16` and `flanker_render_w=16*0.78=12.5pt`.

### Debug a build issue

Check the latest job archive in `output/jobs/YYYYMMDD_HHMMSS/`:
- `qa_report.txt` — human-readable coverage & balance checks
- `layout_log.tsv` — every rendered element with position/size
- `trace_log.jsonl` — full decision log (1000+ events)

---

## Dependencies & Persistence

All packages persist across sessions in `/data/.persist/`:

```bash
# Run at session start
bash /data/.persist/boot-shim.sh
```

| What | Location |
|------|----------|
| Python packages | `/data/.persist/python-packages/` |
| Pip bin scripts | `/data/.persist/bin/` |
| Apt binaries (pdftoppm etc.) | `/data/.persist/apt-bin/` |
| Shared libraries | `/data/.persist/apt-lib/` |

**Required pip packages:**
`Pillow reportlab python-bidi uharfbuzz fonttools pypdf svglib pycairo lxml meson-python`

---

## Known Limitations

1. **fal output dimensions are unpredictable** — always rely on `normalize_canvas()`
2. **Missing external resources** (builder gracefully skips):
   - Cover PDF: `/root/covers/v10/shar_blatt_v36_printready.pdf`
   - Ornament font: `/root/fonts_extracted/Fonts (2)/BergamotOrnaments-3oK6.ttf`
3. **Pillow deprecation**: `Image.getdata()` warns — will need `get_flattened_data()` in Pillow 14
4. **fal style drift**: Even with chained references and shared seed, regenerating the
   full suite can produce slightly different results. Always review after regeneration.
