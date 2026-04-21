# Ornament Techniques — Palaot v2

## 1. Tile-and-Fade (Horizontal Rules / Flankers)

Used for: anaf flankers, header leader band.

### Concept
An ornament image has a decorative **curl/flourish** at one end and a plain **straight rule** at the other end.  The straight end is tiled (1-pixel column repeated) to fill any target width, then the tiled region fades to transparent so the join is invisible.

### Source Image Conventions
- `ornaments/subhead_rule_right.png` (also used for anaf flankers): RGB, white background.  **Curl is on the RIGHT** of the image.
- `ornaments/header_leader_right.png`: RGBA, transparent background.  Flourish on the RIGHT (detected automatically by `_extract_flourish_terminal`).

### Steps — Anaf Flanker (`_draw_anaf_flanker`)

```
1. Load source (RGB) → vectorized white-to-alpha:
       alpha = clip(255 - mean(R,G,B), 0, 255)   # using numpy

2. getbbox() → crop to content.

3. Scale to render_h (16pt) preserving aspect ratio.

4. Determine flip:
   Source curl is on the RIGHT.
   side='right' (right flanker, curl near label = LEFT of canvas):
       → FLIP_LEFT_RIGHT  (curl goes to left)
       → tile from right end (1px col at x = curl_w_px-1)
       → fade RIGHT end
   side='left' (left flanker, curl near label = RIGHT of canvas):
       → no flip  (curl stays on right)
       → tile from left end (1px col at x = 0)
       → place curl at x = needed_w_px - curl_w_px
       → fade LEFT end

5. Gray tint: alpha channel × 0.80

6. Flatten to RGB on white → ImageReader → cache by (w_px, h_px, side)
```

### Steps — Header Leader (`_draw_header_band_leader`)

```
1. Load source (RGBA) → crop.
2. _extract_flourish_terminal(img):
       - Scan columns left→right; find where opaque count > 1.5× rule baseline.
       - Returns (tile_col_1px, flourish_crop) where flourish is at RIGHT end.
3. _build_tiled_leader(tile, flourish, needed_w_px, canvas_h):
       - Tile left portion by repeating tile_col.
       - Paste flourish at right end.
       - Result has flourish on RIGHT.
4. curl_side='left' → FLIP_LEFT_RIGHT the result.
5. Gray tint via _apply_gray_halo (see §2).
```

### `_fade_rgba_alpha(img, fade_side, fade_zone_px)`
Multiplies the alpha channel by a linear ramp from 0→1 (or 1→0) over `fade_zone_px` columns on the specified side.

---

## 2. Gray Halo (`_apply_gray_halo`)

Used for: page frame, anaf end ornament, header ornament.

### What it does
Adds a soft gray glow around all dark ink pixels, producing the effect:
**dark gray core → lighter gray halo → white background**

### Parameters (module-level constants)
| Constant | Value | Meaning |
|---|---|---|
| `_HALO_RADIUS` | 6 | Gaussian blur radius (px at output resolution) |
| `_HALO_DARKEST` | 120 | Darkest gray value in halo zone (0=black) |
| `_INK_THRESHOLD` | 180 | Pixels darker than this are "ink" |

### Steps
```
1. Convert canvas to grayscale → build binary ink mask (px < _INK_THRESHOLD → 255).
2. GaussianBlur(radius=6) on ink mask → smooth falloff.
3. For every pixel where blur > 10 AND pixel is NOT ink AND pixel is near-white:
       t = blur_value / 255
       new_value = 255 - t × (255 - 120)
   i.e. blend white → gray-120 proportionally.
4. Pixels originally >= 254 (outer margin) are untouched.
```

### Ink Gray Level
Before calling `_apply_gray_halo`, darken the source to the desired gray:
```python
# 130 = target gray for ink (0=black, 255=white)
ink_gray = 255 - alpha * (255 - 130)   # alpha from source RGBA
```
- Frame / header ornament: ink gray ≈ 110–130, intensity 0.72–0.80
- Anaf end ornament: ink gray 130, intensity 0.72

---

## 3. Anaf Flanker Geometry

```
                  label_left      label_right
left_x_left   left_x_right  gap  gap  right_x_left  right_x_right
     |---------------|         [LABEL]         |---------------|
     ←   flanker_w   →                         ←   flanker_w   →
curl near RIGHT                               curl near LEFT
(side='left')                                 (side='right')
```

`flanker_w = (C_W × 0.325 - label_width) / 2 - gap`

`H1_ORNAMENT_GAP` (settings.py) controls the gap between label and curl.

---

## 4. Source Files

| File | Used for | Notes |
|---|---|---|
| `ornaments/subhead_rule_right.png` | Anaf flankers | RGB, curl on RIGHT |
| `ornaments/header_leader_right.png` | Header leader band | RGBA, flourish on RIGHT |
| `ornaments/anaf_end_orn_raw.png` | Post-anaf divider | RGBA, cropped from `output/drawing0endorn.svg` |
| `output/drawing0endorn.svg` | Source SVG for end ornament | Rendered via inkscape at 300dpi |
| `curly lines/even better single.png` | Source for subhead_rule_right.png | RGB, 925×1152, curl on RIGHT |
| `curly lines/drawing.svg` | Source for header_leader_right.png | SVG, rendered+cropped+gray |
