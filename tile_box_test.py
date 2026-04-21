#!/usr/bin/env python3
"""
tile_box_test.py — Build seamless box frames at multiple sizes.

Approach A: Decompose tilebox.png into corners + edge strips, reassemble.
            Corners feathered at inner edges for smooth transition.
Approach B: Use ornamental border strip, tile edges, darken-blend at corners.
Approach C: Hybrid — ornament strip edges + tilebox scroll corners on top.

Outputs to output/tile_tests/
"""

from PIL import Image, ImageDraw, ImageFilter
import os, math, time

TILEBOX_SRC = "tilebox.png"
ORNAMENT_SRC = "ornaments/fal_ref2.png"
OUT = "output/tile_tests"


# ════════════════════════════════════════════════════════════════
#  UTILITIES
# ════════════════════════════════════════════════════════════════

def tile_h(img, target_w):
    """Tile image horizontally."""
    tw, th = img.size
    out = Image.new("RGBA", (target_w, th), (255,255,255,255))
    x = 0
    while x < target_w:
        pw = min(tw, target_w - x)
        out.paste(img.crop((0, 0, pw, th)), (x, 0))
        x += tw
    return out

def tile_v(img, target_h):
    """Tile image vertically."""
    tw, th = img.size
    out = Image.new("RGBA", (tw, target_h), (255,255,255,255))
    y = 0
    while y < target_h:
        ph = min(th, target_h - y)
        out.paste(img.crop((0, 0, tw, ph)), (0, y))
        y += th
    return out

def make_palindrome_h(strip):
    """[original][mirrored] horizontally — seamless tile, 2× width."""
    w, h = strip.size
    t = Image.new("RGBA", (w*2, h), (255,255,255,255))
    t.paste(strip, (0, 0))
    t.paste(strip.transpose(Image.FLIP_LEFT_RIGHT), (w, 0))
    return t

def make_palindrome_v(strip):
    """[original][mirrored] vertically — seamless tile, 2× height."""
    w, h = strip.size
    t = Image.new("RGBA", (w, h*2), (255,255,255,255))
    t.paste(strip, (0, 0))
    t.paste(strip.transpose(Image.FLIP_TOP_BOTTOM), (0, h))
    return t

def darken_paste(base, overlay, pos):
    """Paste overlay using darken blend (min per channel)."""
    bx, by = pos
    ow, oh = overlay.size
    for y in range(oh):
        dy = by + y
        if dy < 0 or dy >= base.size[1]: continue
        for x in range(ow):
            dx = bx + x
            if dx < 0 or dx >= base.size[0]: continue
            pb = base.getpixel((dx, dy))
            po = overlay.getpixel((x, y))
            base.putpixel((dx, dy), (
                min(pb[0], po[0]),
                min(pb[1], po[1]),
                min(pb[2], po[2]),
                max(pb[3], po[3]),
            ))

def make_feather_mask(size, fade, corner="tl"):
    """
    Create alpha mask for a corner: solid 255 except fading to 0 on the
    inner edges (the edges that meet the tiled bands).

    corner: 'tl','tr','bl','br' — determines which edges fade.
    fade: number of pixels for the gradient.
    """
    mask = Image.new("L", (size, size), 255)
    w = h = size
    for y in range(h):
        for x in range(w):
            # Compute fade factor for each inner edge
            if corner == "tl":
                fx = min(1.0, (w - 1 - x) / fade) if (w - 1 - x) < fade else 1.0
                fy = min(1.0, (h - 1 - y) / fade) if (h - 1 - y) < fade else 1.0
            elif corner == "tr":
                fx = min(1.0, x / fade) if x < fade else 1.0
                fy = min(1.0, (h - 1 - y) / fade) if (h - 1 - y) < fade else 1.0
            elif corner == "bl":
                fx = min(1.0, (w - 1 - x) / fade) if (w - 1 - x) < fade else 1.0
                fy = min(1.0, y / fade) if y < fade else 1.0
            else:  # br
                fx = min(1.0, x / fade) if x < fade else 1.0
                fy = min(1.0, y / fade) if y < fade else 1.0
            mask.putpixel((x, y), int(255 * fx * fy))
    return mask


# ════════════════════════════════════════════════════════════════
#  APPROACH A — tilebox.png decomposition
# ════════════════════════════════════════════════════════════════

class TileboxFrame:
    """
    Decompose tilebox.png (1536×1024) into 4 corners + tileable edge strips.

    Frame geometry (in source pixels):
      Top band lines:    y = 168..215  (outer line y=168, inner line y=214)
      Bottom band lines: y = 807..855
      Left band lines:   x = 123..175
      Right band lines:  x = 1361..1413

    Corner ornaments extend further:
      Top-left scroll goes to ~x=250, y=250 from frame origin.
      We use generous CORNER_SZ = 200 to capture the full scroll.
    """

    # Source frame bounding box
    F_TOP, F_BOT = 160, 860
    F_LEFT, F_RIGHT = 115, 1420

    # Band thickness (the thin multi-line border)
    BAND_TOP = 55     # y thickness of top/bottom bands
    BAND_LEFT = 58    # x thickness of left/right bands

    # Corner region size (must be big enough for the full scroll ornament)
    CORNER_SZ = 200

    def __init__(self):
        src = Image.open(TILEBOX_SRC).convert("RGBA")
        L, R, T, B = self.F_LEFT, self.F_RIGHT, self.F_TOP, self.F_BOT
        CS = self.CORNER_SZ
        BT, BL = self.BAND_TOP, self.BAND_LEFT

        # ── Corners ──
        self.c_tl = src.crop((L, T, L+CS, T+CS))
        self.c_tr = src.crop((R-CS, T, R, T+CS))
        self.c_bl = src.crop((L, B-CS, L+CS, B))
        self.c_br = src.crop((R-CS, B-CS, R, B))

        # ── Edge strips (between corners) ──
        # These will be palindrome-tiled to fill any length.
        # Top/bottom: full band height, from after left corner to before right corner
        self.e_top = src.crop((L+CS, T, R-CS, T+BT))
        self.e_bot = src.crop((L+CS, B-BT, R-CS, B))
        # Left/right: full band width, from after top corner to before bottom corner
        self.e_left  = src.crop((L, T+CS, L+BL, B-CS))
        self.e_right = src.crop((R-BL, T+CS, R, B-CS))

        # Pre-build palindrome tiles
        self.tile_top = make_palindrome_h(self.e_top)
        self.tile_bot = make_palindrome_h(self.e_bot)
        self.tile_left  = make_palindrome_v(self.e_left)
        self.tile_right = make_palindrome_v(self.e_right)

        print(f"  TileboxFrame: corners={CS}px, top band={self.e_top.size}, left band={self.e_left.size}")

    def build(self, box_w, box_h, scale=1.0):
        """Build frame at box_w × box_h. Scale <1 = thinner border."""
        CS = self.CORNER_SZ
        BT, BL = self.BAND_TOP, self.BAND_LEFT
        FADE = 50  # feather distance at inner corner edges

        if scale != 1.0:
            sc = lambda img: img.resize(
                (max(1, int(img.width*scale)), max(1, int(img.height*scale))),
                Image.LANCZOS)
            c_tl, c_tr = sc(self.c_tl), sc(self.c_tr)
            c_bl, c_br = sc(self.c_bl), sc(self.c_br)
            tt = make_palindrome_h(sc(self.e_top))
            tb = make_palindrome_h(sc(self.e_bot))
            tl = make_palindrome_v(sc(self.e_left))
            tr = make_palindrome_v(sc(self.e_right))
            cs = c_tl.width   # scaled corner size
            bt = tt.height    # scaled band thickness (top)
            bl = tl.width     # scaled band thickness (left)
            fade = max(10, int(FADE * scale))
        else:
            c_tl, c_tr, c_bl, c_br = self.c_tl, self.c_tr, self.c_bl, self.c_br
            tt, tb = self.tile_top, self.tile_bot
            tl, tr = self.tile_left, self.tile_right
            cs, bt, bl = CS, BT, BL
            fade = FADE

        frame = Image.new("RGBA", (box_w, box_h), (255,255,255,255))
        ew = box_w - 2*cs  # edge width between corners
        eh = box_h - 2*cs  # edge height between corners

        if ew <= 0 or eh <= 0:
            print(f"    Box {box_w}×{box_h} too small for corner size {cs}")
            return frame

        # ── Tiled edges (FULL width/height, under corners) ──
        # Extend edges under the corner region so feathered corners blend over them
        frame.paste(tile_h(tt, box_w), (0, 0))                    # top (full width)
        frame.paste(tile_h(tb, box_w), (0, box_h - bt))           # bottom (full width)
        frame.paste(tile_v(tl, box_h), (0, 0))                    # left (full height)
        frame.paste(tile_v(tr, box_h), (box_w - bl, 0))           # right (full height)

        # ── Feathered corners (alpha-composited on top) ──
        for corner_name, c_img, pos in [
            ("tl", c_tl, (0, 0)),
            ("tr", c_tr, (box_w - cs, 0)),
            ("bl", c_bl, (0, box_h - cs)),
            ("br", c_br, (box_w - cs, box_h - cs)),
        ]:
            mask = make_feather_mask(cs, fade, corner=corner_name)
            c_with_alpha = c_img.copy()
            c_with_alpha.putalpha(mask)
            frame.paste(c_with_alpha, pos, c_with_alpha)

        return frame


# ════════════════════════════════════════════════════════════════
#  APPROACH B — ornamental border strip
# ════════════════════════════════════════════════════════════════

class OrnamentFrame:
    """
    Build box frames from the thick ornamental strip (fal_ref2.png).

    The strip is 248×42 (after cropping to ink band).
    Palindrome-tiled for horizontal and rotated for vertical edges.
    Corners: darken-blend where H and V strips cross.
    """

    STRIP_TOP, STRIP_BOT = 28, 70   # ink band in source

    def __init__(self):
        img = Image.open(ORNAMENT_SRC).convert("RGBA")
        self.strip = img.crop((0, self.STRIP_TOP, img.width, self.STRIP_BOT))
        self.sh = self.strip.height   # 42
        self.tile_h = make_palindrome_h(self.strip)   # 496×42
        # Vertical tile: rotate strip 90° CW, then palindrome
        self.strip_v = self.strip.transpose(Image.ROTATE_90)   # 42×248
        self.tile_v = make_palindrome_v(self.strip_v)           # 42×496
        print(f"  OrnamentFrame: strip={self.strip.size}, h_tile={self.tile_h.size}, v_tile={self.tile_v.size}")

    def build(self, box_w, box_h, scale=1.0):
        """Build ornament box frame."""
        if scale != 1.0:
            nh = max(6, int(self.sh * scale))
            nw = max(10, int(self.strip.width * scale))
            strip = self.strip.resize((nw, nh), Image.LANCZOS)
            strip_v = strip.transpose(Image.ROTATE_90)
            th = make_palindrome_h(strip)
            tv = make_palindrome_v(strip_v)
            sh = nh
        else:
            th = self.tile_h
            tv = self.tile_v
            sh = self.sh

        frame = Image.new("RGBA", (box_w, box_h), (255,255,255,255))

        # ── Horizontal edges (full width, top and bottom) ──
        top_edge = tile_h(th, box_w)
        bot_edge = top_edge.transpose(Image.FLIP_TOP_BOTTOM)
        frame.paste(top_edge, (0, 0))
        frame.paste(bot_edge, (0, box_h - sh))

        # ── Vertical edges using darken so corners look like crossing ──
        left_edge = tile_v(tv, box_h)
        right_edge = left_edge.transpose(Image.FLIP_LEFT_RIGHT)
        darken_paste(frame, left_edge, (0, 0))
        darken_paste(frame, right_edge, (box_w - sh, 0))

        return frame


# ════════════════════════════════════════════════════════════════
#  APPROACH C — Hybrid: ornament strip edges + tilebox corners
# ════════════════════════════════════════════════════════════════

class HybridFrame:
    """
    Combine the best of both:
    - Thick ornamental tiled edges from fal_ref2.png (Approach B)
    - Scroll corner ornaments from tilebox.png (Approach A)

    The corners are alpha-composited on top of the tiled edges.
    """

    def __init__(self):
        self.ornament = OrnamentFrame()
        # Extract corners from tilebox
        src = Image.open(TILEBOX_SRC).convert("RGBA")
        L, R, T, B = 115, 1420, 160, 860
        CS = 200
        self.cs = CS
        self.c_tl = src.crop((L, T, L+CS, T+CS))
        self.c_tr = src.crop((R-CS, T, R, T+CS))
        self.c_bl = src.crop((L, B-CS, L+CS, B))
        self.c_br = src.crop((R-CS, B-CS, R, B))
        print(f"  HybridFrame: ornament strip + tilebox corners ({CS}px)")

    def build(self, box_w, box_h, scale=1.0):
        """Build hybrid frame."""
        # First build the ornament-strip base
        frame = self.ornament.build(box_w, box_h, scale=scale)

        # Scale corners
        cs = self.cs
        fade = 50
        if scale != 1.0:
            cs = max(20, int(cs * scale))
            fade = max(10, int(fade * scale))
            sc = lambda img: img.resize((cs, cs), Image.LANCZOS)
            corners = [sc(self.c_tl), sc(self.c_tr), sc(self.c_bl), sc(self.c_br)]
        else:
            corners = [self.c_tl, self.c_tr, self.c_bl, self.c_br]

        # Alpha-composite feathered corners on top
        for corner_name, c_img, pos in [
            ("tl", corners[0], (0, 0)),
            ("tr", corners[1], (box_w - cs, 0)),
            ("bl", corners[2], (0, box_h - cs)),
            ("br", corners[3], (box_w - cs, box_h - cs)),
        ]:
            mask = make_feather_mask(cs, fade, corner=corner_name)
            c_with_alpha = c_img.copy()
            c_with_alpha.putalpha(mask)
            frame.paste(c_with_alpha, pos, c_with_alpha)

        return frame


# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════

def main():
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()

    sizes = [
        ("small",  400, 280),
        ("medium", 600, 420),
        ("large",  900, 650),
        ("wide",   800, 350),
        ("page",   600, 850),
    ]

    # ── Approach A ──
    print("=" * 60)
    print("APPROACH A: tilebox.png (feathered corners)")
    print("=" * 60)
    tb = TileboxFrame()

    for label, bw, bh in sizes:
        f = tb.build(bw, bh)
        f.save(f"{OUT}/A_{label}_{bw}x{bh}.png")
        print(f"  A_{label}_{bw}x{bh}.png")

    for pct in [50, 75, 100]:
        f = tb.build(600, 420, scale=pct/100.0)
        f.save(f"{OUT}/A_scale{pct}.png")
        print(f"  A_scale{pct}.png")

    # ── Approach B ──
    print()
    print("=" * 60)
    print("APPROACH B: ornament strip (darken corners)")
    print("=" * 60)
    ob = OrnamentFrame()

    for label, bw, bh in sizes:
        f = ob.build(bw, bh)
        f.save(f"{OUT}/B_{label}_{bw}x{bh}.png")
        print(f"  B_{label}_{bw}x{bh}.png")

    for pct in [50, 75, 100, 150]:
        f = ob.build(600, 420, scale=pct/100.0)
        f.save(f"{OUT}/B_scale{pct}.png")
        print(f"  B_scale{pct}.png")

    # ── Approach C ──
    print()
    print("=" * 60)
    print("APPROACH C: hybrid (ornament edges + tilebox corners)")
    print("=" * 60)
    hf = HybridFrame()

    for label, bw, bh in sizes:
        f = hf.build(bw, bh)
        f.save(f"{OUT}/C_{label}_{bw}x{bh}.png")
        print(f"  C_{label}_{bw}x{bh}.png")

    for pct in [50, 75, 100]:
        f = hf.build(600, 420, scale=pct/100.0)
        f.save(f"{OUT}/C_scale{pct}.png")
        print(f"  C_scale{pct}.png")

    # ── 3-way comparison ──
    print()
    print("Creating 3-way comparisons...")
    for label, bw, bh in [("medium", 600, 420), ("page", 600, 850)]:
        a = Image.open(f"{OUT}/A_{label}_{bw}x{bh}.png")
        b = Image.open(f"{OUT}/B_{label}_{bw}x{bh}.png")
        c = Image.open(f"{OUT}/C_{label}_{bw}x{bh}.png")
        gap = 20
        total_w = bw * 3 + gap * 2
        comp = Image.new("RGBA", (total_w, bh + 50), (245, 245, 245, 255))
        draw = ImageDraw.Draw(comp)
        labels = ["A: tilebox", "B: ornament strip", "C: hybrid"]
        for i, (img, lbl) in enumerate(zip([a, b, c], labels)):
            x = i * (bw + gap)
            draw.text((x + bw // 2 - 40, 8), lbl, fill="black")
            comp.paste(img, (x, 50))
        comp.save(f"{OUT}/compare3_{label}.png")
        print(f"  compare3_{label}.png")

    print(f"\nDone in {time.time()-t0:.1f}s. Results in {OUT}/")


if __name__ == "__main__":
    main()
