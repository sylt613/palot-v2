# Palaot v2 — Agent State Document

> **Purpose**: Live reference for any agent resuming work on this project.
> Read this FIRST — it has everything you need.

---

## FRAME APPROACH — SVG Vector Assembly (CURRENT)

The old AI-generated PNG frames have been **replaced** with a vector-based frame assembler that renders pieces from a source SVG (`output/image.svg`) using cairosvg.

### How it works:
1. **`frame_assembler.py`** — the main frame builder (~220 lines)
2. Source SVG: `output/image.svg` (viewBox `0 0 2048 1365`) — a baroque scroll-and-curl ornamental frame
3. SVG rendered to `output/image_svg_render.png` (3200×2133, scale factor 1.5625×)
4. **9-slice approach**: renders TL corner + top border + left border from SVG via viewBox manipulation
5. Other 3 corners derived by flipping TL corner (TR = flip-LR, BL = flip-TB, BR = rotate-180)
6. **Borders are STRETCHED, not tiled** — the SVG border is one symmetric design (spirals → S-waves → diamond center), not a repeating tile. We render the half-border from SVG scaled to exactly fill half the target width, then mirror it. This eliminates seams entirely.
7. Corners pasted ON TOP of borders to hide any corner-to-border transition
8. White cleaning: all pixels with R,G,B >= 235 → pure 255 (for reportlab transparent masking)

### SVG coordinate system:
- Content bounding box: x=335..1710, y=74..1290
- Center of symmetry: x=1023, y=682
- Corner size in SVG coords: 504×368
- Top border region: x=839..1023 (left half, 184 SVG units wide)
- Left border region: y=442..682 (top half, 240 SVG units tall)

### Corner scaling:
- Corners scaled to max 35% of target dimensions, capped at 1.5× native
- `sc = min(target_w * 0.35 / 504, target_h * 0.35 / 368, 1.5)`

### Integration in build_v2.py:
- `draw_frame_box()` at ~line 841 calls `assemble_frame()` dynamically
- `_ASSEMBLED_FRAME_CACHE` dict near line ~549 caches by `(w_px, h_px, intensity)`
- Intensity < 1.0 lightens toward white (0.55 = grayish)
- Frame drawn FIRST, text ON TOP (prevents frame from making text grainy)

### Current intensity/color settings (as of session 11):
- **TOC frame**: intensity=0.55 (grayish)
- **Mamar frame**: intensity=0.55 (grayish)
- **TOC title text**: RGB(0.3, 0.3, 0.3) — medium gray
- **Mamar title text**: RGB(0.25, 0.25, 0.25) — dark gray
- **TOC subhead rules (ornaments)**: intensity=0.55
- **TOC mamar label/name text**: RGB(0.3, 0.3, 0.3)

### Backup:
- `frame_assembler_GOOD_BACKUP.py` — last known-good version (tiling approach, before stretch)
- `output/palaot_good_backup.pdf` — last known-good PDF

### Abandoned frame approaches (history):
1. Strip+corner tiling with `divider_post_anaf.png` + `frame_corner_halo.png` — user rejected
2. `fal-ai/nano-banana-pro/edit` AI-generated PNG frames — replaced by SVG vector assembly
3. `_build_strip_frame()` at ~line 742 — DEAD CODE
4. Raster-based tiling of SVG render — replaced by cairosvg vector rendering
5. ABBA mirror-tile approach for borders — replaced by stretch (single symmetric piece, not repeating tile)

---

## 1. Project Overview

**Goal**: Print-ready 5.5"×8.5" Hebrew sefer PDF (Palaot v2).

**Tech stack**: Python 3, Pillow, reportlab, python-bidi, uharfbuzz, fonttools, pypdf, cairosvg, numpy

**Boot**: Run `bash /data/.persist/boot-shim.sh` at every session start.

**PYTHONPATH**: Always run python as `PYTHONPATH=/data/.persist/python-packages python3`

**Build command**:
```bash
cd /data/v2-import/palot/version2 && PYTHONPATH=/data/.persist/python-packages python3 build_v2.py && cp output/palaot.pdf output/palaot_latest.pdf
```

**PDF viewer**: `http://localhost:7778/palaot_latest.pdf`
```bash
nohup python3 -m http.server 7778 --directory /data/v2-import/palot/version2/output > /dev/null 2>&1 &
```

**Chrome fix** (for Playwright):
```bash
mkdir -p /opt/google/chrome && ln -sf /root/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome /opt/google/chrome/chrome
```

---

## 2. Directory Layout

```
/data/v2-import/palot/version2/
├── build_v2.py                  # Main PDF builder (~6800 lines)
├── frame_assembler.py           # SVG-based frame assembly (~220 lines)
├── frame_assembler_GOOD_BACKUP.py  # Backup of last working version
├── generate_ornaments.py        # Ornament generation via fal AI (legacy)
├── settings.py                  # Project settings
├── agents.md                    # THIS FILE
├── ornaments/
│   ├── user_frame_output.png    ← style reference (1536×1024, legacy)
│   ├── frame_toc_nb2.png        ← TOC frame PNG (4000×2000, FALLBACK only)
│   ├── frame_mamar_nb2.png      ← mamar frame PNG (6580×2000, FALLBACK only)
│   ├── anaf_flanker.png         300×210 RGBA
│   ├── divider_post_anaf.png    900×210 RGBA (symmetrical)
│   ├── divider_toc.png          720×168 RGBA
│   ├── footnote_separator.png   1050×60 RGBA
│   ├── header_leader_right.png  1350×120 RGBA
│   ├── subhead_rule_right.png   (used for subhead ornament rules)
│   ├── mamar_title_frame.png    1000×420 RGBA (legacy inner band)
│   └── toc_entry_accent.png     180×180 RGBA
├── output/
│   ├── palaot.pdf               # Latest built PDF (21 pages)
│   ├── palaot_latest.pdf        # Copy served by HTTP server
│   ├── palaot_good_backup.pdf   # Backup PDF
│   ├── image.svg                # Source SVG frame (viewBox 2048×1365)
│   ├── image_svg_render.png     # SVG rendered at 3200×2133
│   ├── test_frame_toc.png       # Test frame 1200×600 (for debugging)
│   └── test_frame_mamar.png     # Test frame 1980×600 (for debugging)
└── fonts/
    ├── FrankReal_Med.ttf        # Body font
    ├── FrankReal_Bold.ttf       # Bold body font
    ├── EFT_PNINA MEDIUM.TTF    # Heading bold (pninamedium)
    ├── EFT_PNINA.TTF           # Heading regular (pninaregular)
    └── ...other fonts
```

---

## 3. Font Configuration

| Role | Font Name | File |
|---|---|---|
| Body text | frankrealmed | FrankReal_Med.ttf |
| Bold body | frankrealbold | FrankReal_Bold.ttf |
| Headings bold / titles / dropcap | pninamedium | EFT_PNINA MEDIUM.TTF |
| Headings regular / headers / page numbers | pninaregular | EFT_PNINA.TTF |
| Footnotes | frankrealmed | FrankReal_Med.ttf |

---

## PERMANENT RULES (DO NOT CHANGE)

### H2 Subheading Spacing
- **Space BEFORE** = `PSEP * 1.8` (extra, separates from preceding paragraph)
- **Space AFTER** = `PSEP` (tight, heading attaches to following paragraph)
- **Invariant**: SUB_AFTER gap must ALWAYS stay visibly smaller than SUB_BEFORE gap,
  regardless of subhead line count or balancer/stretch mode. Enforced in
  `_inter_frag_gap` (~line 4880) with ceiling `min(MAX_SUB_AFTER_GAP, SUB_BEFORE_FRAG_GAP * 0.8)`.
  This keeps the subhead visually coupled with the paragraph below even when
  the subhead itself spans multiple lines.

### Anaf Heading (H1) Spacing
- **Space ABOVE** (H1_TOP_GAP): `LH * 2.5` (~42.5pt)
- **Space BELOW** (HEADING_AFTER_GAP): `LH * 1.5` (~25.5pt)

### Heading Margin Conformance (ALL HEADINGS)
**All headings (anaf/H1, H2, sefer_title) MUST stay within content width `C_W`.**
They must either shrink-to-fit or wrap to multiple lines — NEVER overflow the
content margins, regardless of font. Any future heading renderer MUST:
1. Measure text width at nominal size.
2. If it exceeds `C_W` (or role-specific budget like `C_W * 0.80` for titles),
   shrink the font down to a floor (70% for anaf label, 80% for anaf name).
3. If still too wide at the floor, wrap via `wrap_words_balanced`.
4. Return the ACTUAL rendered height so reflow accounts for wrapped lines.

Reference impl: `draw_anaf_heading` + `_anaf_label_fit_size` + `_anaf_name_fit_size`
+ `anaf_heading_height` (~lines 4588–4654). `heading_height()` must pass
`label`/`name` through so height-queries match render.

### Section-End Ornament: Skip If No Room
When a paragraph is the last before a section break, reserve ~19.8pt for post-anaf ornament. If paragraph fits without ornament but not with it, skip ornament — don't split paragraph.

---

## 4. Key Code Locations in build_v2.py

| What | Line(s) approx | Notes |
|---|---|---|
| PNG path constants | ~536–544 | `_PNG_DIVIDER_POST_ANAF`, etc. |
| `_ASSEMBLED_FRAME_CACHE` | ~549 | Cache for assembled frames |
| `draw_frame_box()` | ~841 | Calls `assemble_frame()`, applies intensity, draws with mask |
| `_build_strip_frame()` | ~742 | DEAD CODE |
| `draw_generated_subhead_rule()` | ~1248 | Subhead ornament rules |
| TOC frame sizing | ~2858–2863 | `frame_pad_x=40, frame_pad_y=30` |
| TOC frame call | ~2890 | `draw_frame_box(..., intensity=0.55)` |
| TOC title text color | ~2900 | `setFillColorRGB(0.3, 0.3, 0.3)` |
| TOC subhead rules | ~2930, 2938 | `intensity=0.55` |
| TOC mamar label/name | ~2929 | `setFillColorRGB(0.3, 0.3, 0.3)` |
| Mamar frame sizing | ~3728–3733 | `frame_pad_x=54, frame_pad_y=22` |
| Mamar frame call | ~3776 | `draw_frame_box(..., intensity=0.55)` |
| Mamar title text color | ~3779 | `setFillColorRGB(0.25, 0.25, 0.25)` |
| TOC rendering | ~2748 | `draw_toc_page()` |
| Mamar frame in body | ~3600 | `kind='sefer_title'`, balanced wrapping at 80% width |
| Chapter divider | ~6676 | `draw_generated_divider()` |

---

## 5. Key Technical Discoveries

1. **SVG border is ONE symmetric design, not a repeating tile** — spirals → S-waves → diamond center. Must stretch to fill, not tile.
2. **cairosvg viewBox manipulation** — render arbitrary SVG regions by rewriting viewBox + width/height attributes, then calling `cairosvg.svg2png()`.
3. **reportlab `mask='auto'` eats semi-transparent pixels** — flatten onto white bg, use color-range mask `[254,255,254,255,254,255]`.
4. **Draw order matters** — frame FIRST, text ON TOP prevents graininess.
5. **White cleaning threshold** — pixels with R,G,B >= 235 → force to pure 255 for mask transparency.
6. **Intensity parameter** — values < 1.0 lighten toward white: `lut = [int(v * intensity + 255 * (1 - intensity)) for v in range(256)]`
7. **doc_parts.json `type` field** — values: `sefer_title`, `heading1`, `heading2`, `body`.
8. **Title text**: "מחיית עמלק - תיקון ל'וקוץ ודרדר תצמיח לך'"
9. **`heading_height()` must include frame padding for sefer_title** — `frame_pad_y * 2` added.
10. **Page-stretch simulation must include `gap_above`** — `simulated_y` loops subtract `blk.get('gap_above', 0.0)`.
11. **Reflow overflow guard** — rejects line addition if `delta_h > remaining_slack + 0.5`.
12. **cairosvg installed at** `/data/.persist/python-packages` — must set PYTHONPATH.

---

## 6. Session Log

### Sessions 1–5 (summary)
- Layout engine, ornaments, fonts, spacing all finalized
- 21 pages, 0 QA issues, all pages user-approved

### Session 6 (frame replacement — nano-banana-2)
- Direct AI-generated PNG frames, style reference `user_frame_output.png`

### Session 7 (nano-banana-pro + strip+corner attempt)
- Switched to nano-banana-pro, attempted strip+corner — user rejected
- Reverted to direct-PNG, fixed draw order (frame FIRST, text ON TOP)

### Session 8 (TOC frame height fix)
- Increased `frame_pad_y` 16→30, cropped TOC PNG to 2.00:1

### Session 9 (SVG vector frame assembler)
- Replaced AI-generated PNG frames with SVG-based vector assembly
- `frame_assembler.py` written using cairosvg 9-slice approach
- ABBA mirror-flip tiling for borders between corners
- TOC frame verified good by user

### Session 10 (seam debugging)
- Mamar frame had visible seam on right side
- Debug crops generated, analyzed junction pixels
- Identified that tiling approach creates seams on wide frames

### Session 11 (current — stretch + grayish)
- **Replaced tiling with stretch** — border is one symmetric design, render half-border at exact needed width, mirror for other half. No seams possible.
- **Made frames grayish** — intensity 0.55 for both TOC and mamar frames
- **Made header text grayish** — TOC title 0.3, mamar title 0.25, TOC labels 0.3
- **Made header ornaments lighter** — subhead rules intensity 0.55
- Backup saved: `frame_assembler_GOOD_BACKUP.py`, `palaot_good_backup.pdf`
- **Status**: Build pending verification with new stretch approach + grayish styling

---

*Last updated: session 11 — stretch-based borders, grayish frames/text/ornaments.*
