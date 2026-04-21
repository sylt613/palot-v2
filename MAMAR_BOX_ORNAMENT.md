# Mamar Box Ornament — Design Notes

## ⚠️ HARD RULE — Curl Ornament Sizing
**ALWAYS lock aspect ratio when sizing curl ornament lines from `good curls svg.svg`.**
Width is chosen first (e.g. half the box width).  Height is then derived from the
natural band aspect ratio — never set width and height independently.

```python
orn_w = frame_bw * 0.5          # chosen width
orn_h = orn_w * band_rl_h / 1024.0  # locked height — NEVER stretch
```

If the height looks wrong, change `orn_w`; do not override `orn_h` separately.

## What it is
The TOC page and body sefer_title block both have a decorative box around the
"מאמר / mamar name" text.  The goal is to replace the current baroque `frame_mamar_nb2.png`
frame with curl ornament lines from `good curls svg.svg` plus a partial line box.

## Correct target location
**NOT** `draw_toc_page()` (that uses `draw_generated_subhead_rule` — keep as is).
The correct place is `draw_section_header()` → `kind == 'sefer_title'` block (~line 3955)
which currently calls `draw_frame_box(..., png_path='frame_mamar_nb2.png')`.

## Curl SVG line bands (RL coordinate space, Drawing is 1024×1024)
From `/data/curly lines/good curls svg.svg` (viewBox 0 0 2048 2048, width/height 1024,
root group transform (0.5, 0, 0, -0.5, 0, 1024)):

| Line | SVG y range | RL y range        | RL height | Use        |
|------|-------------|-------------------|-----------|------------|
| 3    | 1029–1261   | 393.5 – 509.5     | ~116 RL   | top orn    |
| 2    | 564–821     | 613.5 – 742.0     | ~128 RL   | bottom orn |

Group membership:
- Line 3 (top): groups 1,2,3,4,5
- Line 2 (bottom): groups 9,10,11,12,13,28,29,32

Use `_draw_curl_orn_line(c, line_id, x_center, y_center, target_w, target_h)` —
already implemented in build_v2.py.

## Box shape design
**NOT a full rounded rectangle.**  The correct design is an open-top partial box:
- Two vertical side lines
- Two bottom corner arcs (rounded)
- A short horizontal stub at the bottom from each corner (not full-width bottom)
- NO top horizontal line (hidden behind top ornament)
- The bottom ornament overlaps the middle of the bottom stub, leaving only corner arcs visible

Draw as a PDF path (not roundRect):
```python
r = 8.0   # corner radius
# Open-top U-shape path:
p = c.beginPath()
p.moveTo(x_left, y_top)          # top-left (open, no arc)
p.lineTo(x_left, y_bot + r)      # down left side
p.arcTo(x_left, y_bot, x_left + r, y_bot + 2*r,   # bottom-left arc  ← wrong
        startAng=180, extent=90)                     # use curveTo instead
# ... then bottom line, bottom-right arc, up right side, stop at top-right
```

Better with curveTo for the bottom corners (more control):
```python
p = c.beginPath()
p.moveTo(x_left, y_top)           # start top-left (open)
p.lineTo(x_left, y_bot + r)       # left side down
p.curveTo(x_left, y_bot, x_left, y_bot, x_left + r, y_bot)   # BL corner
p.lineTo(x_right - r, y_bot)      # bottom line
p.curveTo(x_right, y_bot, x_right, y_bot, x_right, y_bot + r) # BR corner
p.lineTo(x_right, y_top)          # right side up (open top)
c.drawPath(p, fill=0, stroke=1)
```

## Rendering order
1. Draw the open-top U-path (stroke only, lineWidth ~0.7, gray ~0.55)
2. Draw top curl ornament (line 3) centered on top edge — covers open top
3. Draw bottom curl ornament (line 2) centered on bottom edge — covers middle of bottom line

## Sizing
- `target_w = frame_bw` (existing frame width calculation)
- `curl_orn_h ≈ 20–28pt` — tune so ornament aspect ratio looks natural
- The natural aspect of line 3 band: 1024 wide × ~116 tall (RL units) ≈ 8.8:1
  At target_w = 250pt → natural height = 250/8.8 ≈ 28pt ✓

## Old approach (baroque frame)
`draw_frame_box(c, frame_left, frame_bot, frame_bw, frame_bh, intensity=0.80,
               png_path=os.path.join(_ORNAMENT_DIR, 'frame_mamar_nb2.png'))`
This renders `frame_mamar_nb2.png` tiled/stretched as a full ornamental frame border.
Keep this code commented out in case we need to revert.
