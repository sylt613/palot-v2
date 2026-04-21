# Vertical Centering Guide — Hebrew Text in Baroque Scroll Frame

Reference document for vertically centering Hebrew text within the
`frame_master.png` baroque scroll frame in Palaot v2.

---

## 1. Problem

Hebrew text inside a decorative frame must *look* vertically centred.
Using font metrics (ascent/descent) or a rough "cap-height" fraction
(e.g., 0.72) gives wrong results because:

- Standard font metrics include lamed (ל, tall ascender) and kuf/final
  forms (ק ך ן ץ ף, long descenders) — outliers that don't represent
  the uniform body zone.
- The visual "body" of Hebrew text is what the eye centres on, **not**
  the full ascent/descent range.

---

## 2. Measurement Method

Render individual Hebrew glyphs in PIL at large size (fs=200px),
draw on a white canvas with fill=0, scan for ink pixels (threshold < 250),
and measure top/bottom relative to the baseline.

**Font**: `fonts/FrankReal_Bold.ttf` (ReportLab name: `frankrealbold`)
**PIL font metrics** at fs=200: ascent=147, descent=50.
Baseline = draw_y + ascent.

---

## 3. Glyph Measurements (FrankReal Bold, fs=200px)

### Body Glyphs (used for centering)

Glyphs included: `בגדהוזחטיכמנספצרשת`

Excluded:
- **Lamed (ל)** — tall ascender, sticks above body
- **Kuf (ק)** and final forms **(ך ן ץ ף)** — long descenders
- **Ayin (ע)** — has a small non-uniform dip below body bottom (+10px),
  not representative of the uniform visual bottom

| Glyph | Top (from baseline) | Bottom (from baseline) | Height |
|-------|--------------------:|----------------------:|-------:|
| ב     | -100               | -1                    | 99     |
| ג     | -100               | 0                     | 100    |
| ד     | -100               | 0                     | 100    |
| ה     | -100               | +1                    | 101    |
| ו     | -100               | 0                     | 100    |
| ז     | -100               | +1                    | 101    |
| ח     | -100               | +1                    | 101    |
| ט     | -100               | +1                    | 101    |
| י     | -100               | -32                   | 68     |
| כ     | -100               | -1                    | 99     |
| מ     | -100               | +1                    | 101    |
| נ     | -100               | -1                    | 99     |
| ס     | -100               | +1                    | 101    |
| פ     | -100               | -1                    | 99     |
| צ     | -100               | -1                    | 99     |
| ר     | -100               | +1                    | 101    |
| ש     | -100               | -1                    | 99     |
| ת     | -100               | +1                    | 101    |

### Outlier Glyphs (for reference only)

| Glyph | Top  | Bottom | Height | Notes                        |
|-------|-----:|-------:|-------:|------------------------------|
| ל     | -135 | +1     | 136    | Ascender extends 35px above body |
| ע     | -100 | +10    | 110    | Small dip 10px below body    |
| ק     | -100 | +40    | 140    | Descender extends 40px below |
| ן     | -100 | +39    | 139    | Descender extends 39px below |
| ץ     | -100 | +39    | 139    | Descender extends 39px below |
| ף     | -100 | +40    | 140    | Descender extends 40px below |
| ך     | -100 | +40    | 140    | Descender extends 40px below |

---

## 4. Derived Constants

All values as **fractions of font size (fs)**:

| Constant                    | Value    | Meaning                                            |
|-----------------------------|----------|-----------------------------------------------------|
| `BODY_ASCENT_FRAC`         | 0.5000   | Body top is 0.50×fs above baseline                  |
| `BODY_DESCENT_FRAC`        | 0.0050   | Body bottom is 0.005×fs below baseline (≈0, noise)  |
| `BODY_HEIGHT_FRAC`         | 0.5050   | Total body height ≈ 0.505×fs                        |
| **`BODY_CENTRE_ABOVE_BL`** | **0.2475** | Visual centre of body is 0.2475×fs above baseline |

Formula: `BODY_CENTRE_ABOVE_BL = (BODY_ASCENT_FRAC - BODY_DESCENT_FRAC) / 2`

---

## 5. Centering Formula

### Single-line text

```
baseline = frame_centre_y - BODY_CENTRE_ABOVE_BL × fs
```

Where `frame_centre_y` is the geometric centre of the frame bounding box
in PDF coordinates (y increases upward).

### Multi-line text (N lines, leading factor L = 1.18)

```
first_baseline = frame_centre_y - BODY_CENTRE_ABOVE_BL × fs + (N-1) × fs × L / 2
```

Each subsequent line: `baseline -= fs × L`

**Derivation**: The visual centre of the text block is the midpoint
between the first line's body top and the last line's body bottom:

```
block_top    = first_baseline + BODY_ASCENT_FRAC × fs
block_bottom = first_baseline - (N-1)×fs×L - BODY_DESCENT_FRAC × fs
block_centre = (block_top + block_bottom) / 2
             = first_baseline + (BODY_ASCENT_FRAC - BODY_DESCENT_FRAC)×fs/2 - (N-1)×fs×L/2
             = first_baseline + BODY_CENTRE_ABOVE_BL × fs - (N-1)×fs×L/2
```

Setting `block_centre = frame_centre_y` and solving for `first_baseline`:

```
first_baseline = frame_centre_y - BODY_CENTRE_ABOVE_BL × fs + (N-1)×fs×L/2
```

---

## 6. Frame Inner Whitespace

The baroque scroll frame (`frame_master.png`) has rule lines near the
edges. The inner whitespace centre is slightly offset from the geometric
frame centre due to asymmetric corner crop heights (CY_TOP=90 vs CY_BOT=100).

At typical sefer_title size (frame ≈ 66-96pt tall), this offset is
approximately **+0.6pt** from geometric centre. This is small enough
to ignore — using the geometric centre produces equal spacing (±0.01pt).

For reference, the source frame rule positions:
- Top rules end at source row 52 (57.8% into CY_TOP=90 corner)
- Bottom rule at source row 609 (35% from bottom of CY_BOT=100 corner)
- See TILING_REFERENCE.md for full source pixel data

---

## 7. Code Locations

### `draw_section_header` — sefer_title (build_v2.py ≈ line 3604)

```python
BODY_CENTRE_ABOVE_BL = 0.2475
frame_cy = frame_bot + frame_bh / 2
first_baseline = frame_cy - BODY_CENTRE_ABOVE_BL * fs + (n_lines - 1) * fs * 1.18 / 2
```

### `draw_toc_page` — TOC title "תוכן הענינים" (build_v2.py ≈ line 2726)

```python
BODY_CENTRE_ABOVE_BL = 0.2475
frame_cy = title_frame_bot + title_frame_h / 2
title_y  = frame_cy - BODY_CENTRE_ABOVE_BL * TOC_TITLE_FS
```

---

## 8. Verification

For a 2-line sefer_title in a 95.92pt frame:
- frame_cy = 422.50
- first_baseline = 430.04, second_baseline = 404.08
- body_top = 441.04 (first line), body_bot = 403.97 (last line)
- block_centre = 422.50 = frame_cy ✓
- Space above body: 29.42pt, Space below body: 29.43pt ✓

For TOC title (single line, 54pt frame):
- Baseline shifts +2.48pt compared to old 0.72 cap-height formula
- Body visual centre now matches frame centre exactly

---

## 9. Previous (Incorrect) Approaches

### Old sefer_title formula
```python
first_baseline = frame_cy + total_h / 2 - fs * (1 - 0.72)
```
Off by ~10pt (text too high). Used `total_h` which double-counts line
spacing and a rough 0.72 cap-height estimate.

### Old TOC title formula
```python
title_y = frame_cy - TOC_TITLE_FS * 0.72 / 2
```
Off by ~2.5pt. Used 0.72 cap-height instead of measured 0.2475 body-centre offset.

---

## 10. If the Font Changes

Re-run the PIL measurement script to get new body metrics. The key
measurement: render body glyphs (exclude lamed, kuf, descending finals,
ayin) at large size, find ink bounding box relative to baseline, compute
`BODY_CENTRE_ABOVE_BL = (body_ascent_frac - body_descent_frac) / 2`.
