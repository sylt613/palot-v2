# Frame Tiling Reference — Corners + Sides

## Overview

When building a decorative frame from a single source image (e.g. `frame_master.png`),
the frame is decomposed into **4 corners** and **4 edge bands** (top, bottom, left, right).
Corners are cropped and resized. Edge bands are **tiled** (repeated) from a 1px
representative slice. This document captures the lessons learned.

---

## Source Image Anatomy

```
frame_master.png (1536×1024 full image)
  → Crop to frame bounding box: F_TOP=184, F_BOT=828, F_LEFT=273, F_RIGHT=1258
  → Cropped frame: 986×645 pixels

Cropped frame layout:

  TL corner ──── top band (tiled) ──── TR corner
  │                                           │
  left band                           right band
  (tiled)                              (tiled)
  │                                           │
  BL corner ──── bottom band (tiled) ── BR corner
```

## Critical Lesson 1: Corner Crops Must Include the FULL Transition Zone

The scroll ornament in each corner doesn't end abruptly — it **overlaps** with
the rule lines. You CANNOT crop the corner at the ornament's visual edge
because the rule lines start inside the ornament zone.

### Pixel analysis of top-left corner:
- Scroll curl occupies rows 0–38, cols 0–74
- BUT the horizontal rule lines (rows 34–51) start as early as col 22
- The rule lines and scroll curl **overlap** in the zone cols 22–74, rows 34–38
- Clean tiling region (all rules present, no scroll) starts at **col 78**

### Correct corner crop boundaries (source pixels):
```
CX      = 80    # horizontal extent (past col 78 clean boundary)
CY_TOP  = 90    # vertical extent for top corners (past row 87 clean boundary)
CY_BOT  = 100   # vertical extent for bottom corners (past row 557 = FH-88)
L_W     = 25    # left side band width
R_W     = 25    # right side band width
```

### How to find these boundaries:
1. For each row in the top band, scan left-to-right for the rightmost dark pixel
   that's part of the scroll (not a rule). The scroll ends around col 74.
2. For each column past the scroll, check if ALL rule lines are present (dark
   pixels at all expected rule rows). Clean region starts at col 78.
3. For vertical extent: scan the left side band columns at each row; find where
   both vertical rule clusters are present with no scroll detail between them.
   Clean from row 87 downward (top), up to row 556 (bottom).
4. Add a few pixels of margin → CX=80, CY_TOP=90, CY_BOT=100.

## Critical Lesson 2: The Tile Slice Must Cover the SAME Source Rows as the Corner

### The bug
If the corner covers source rows 0..90 and the tile slice covers rows 0..52,
they are resized independently:
- Corner: 90 source rows → c_h_t output rows (ratio = c_h_t / 90)
- Band: 52 source rows → top_h output rows (ratio = top_h / 52)

Even with the same scale factor, **integer rounding** makes these ratios differ
slightly. A rule line at source row 35 ends up at different output pixel
positions in the corner vs the band → visible seam.

### The fix
Crop the tile slice from the **exact same source row range** as the corner:
```python
# Corner crop:
tl = resize(frame.crop((0, 0, CX, CY_TOP)), c_w, c_h_t)

# Tile slice — SAME rows 0..CY_TOP, just 1px wide from a clean column:
top_slice = frame.crop((TILE_COL, 0, TILE_COL + 1, CY_TOP))
# Then scale to 1 × c_h_t (SAME output height as corner)
```

Now both are scaling 90 source rows → c_h_t output rows. The LANCZOS
resampling places rule lines at identical pixel positions. Perfect join.

### Same principle for all 4 bands:
```
Top band:    source rows 0..CY_TOP        → output height c_h_t
Bottom band: source rows FH-CY_BOT..FH    → output height c_h_b
Left band:   source cols 0..L_W           → output width l_w
Right band:  source cols FW-R_W..FW       → output width r_w
```

## Critical Lesson 3: Use ONE Uniform Scale Factor

### The bug
Computing corner width and height from different axis ratios:
```python
c_w = round(CX * target_w / FW)    # horizontal ratio
c_h = round(CY * target_h / FH)    # vertical ratio — DIFFERENT!
```
This distorts the corner's aspect ratio — scroll curls look horizontally
stretched or squished.

### The fix
Use a single scale factor `s` for everything:
```python
s = min(target_w / FW, target_h / FH)
# clamp to min/max for visual quality
c_w   = round(CX     * s)
c_h_t = round(CY_TOP * s)
c_h_b = round(CY_BOT * s)
l_w   = round(L_W    * s)
r_w   = round(R_W    * s)
```

## Lesson 4: Tile Slice Location

The 1px slice must come from a column/row that is:
1. **Past the corner ornament** — no scroll curl pixels
2. **In the uniform rule region** — all rule lines present
3. **Not at the midpoint** — the midpoint (col 493) may have different
   pixel content than near the corners; use a column closer to corners
   but past the clean boundary (col 200 works well)

### Source positions used:
```
TILE_COL = 200   # for top/bottom horizontal bands
TILE_ROW = 322   # for left/right vertical bands
```

## Lesson 5: Assembly Order

1. Paint tiled bands FIRST (they go behind corners)
2. Paint corners LAST (they overlap the band edges)

The corner image includes some of the rule lines within its crop area.
The tiled band also has those same rules at the boundary. By painting
corners on top, you get a clean overlap where the corner's actual pixel
data (with the scroll-to-rule transition) covers the tiled band's edge.

## Lesson 6: Tiling Implementation

For a 1px-wide column slice tiled horizontally:
```python
def _tile_h(slice_1px, span_w, band_h):
    col = slice_1px.resize((1, band_h), Image.LANCZOS)
    strip = Image.new("RGB", (span_w, band_h), (255,255,255))
    for x in range(span_w):
        strip.paste(col, (x, 0))
    return strip
```
- First resize the 1px slice vertically to match output band height
- Then paste it at every x position across the span
- This produces perfectly uniform horizontal rule lines

Same principle vertically for side bands (1px-tall row slice tiled down).

## Quick Reference: Source Frame Pixel Data

### Horizontal rules in top band (at col 200):
| Rows    | Description          | Pixel value |
|---------|---------------------|-------------|
| 34–36   | Outer rule (3px)    | ~1 (black)  |
| 42–44   | Middle rule (3px)   | ~0 (black)  |
| 49–51   | Inner rule (3px)    | ~2 (black)  |

### Vertical rules in left band (at row 322):
| Cols    | Description          | Pixel value |
|---------|---------------------|-------------|
| 8–11    | Inner pair           | 4–146       |
| 17–19   | Outer pair           | 7–154       |

### Bottom band (at col 200):
| Row | Description   | Pixel value  |
|-----|--------------|--------------|
| 609 | Single rule  | ~10 (black)  |

### Corner ornament extents (top-left):
- Scroll curl: cols 0–74, rows 0–38
- Scroll + inner detail: extends to col 66 at rows 55–65
- Rule overlap zone: cols 22–77, rows 34–51
