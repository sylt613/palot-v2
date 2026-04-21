"""
frame_assembler.py
------------------
Assembles a decorative frame PNG at any target width x height by manipulating
the source SVG's viewBox and rendering via cairosvg.

The SVG frame (output/image.svg, viewBox 0 0 2048 1365) has:
  - 4 ornate corners
  - Decorative border strips between corners
  - Content area in the center

Strategy: We use 9-slice rendering from the SVG. We render each of the 9 regions
separately from the SVG at appropriate scales, then composite them.

Actually simpler: render the SVG corners at fixed size, and tile the border
strips by rendering them from the SVG too (so everything stays vector-clean).

SIMPLEST approach that works: render the full SVG frame at the target size,
using a modified viewBox that crops to just the frame content area, preserving
the frame's native proportions and just centering it.

But the user wants the frame to FILL the target rectangle at any aspect ratio.
So we use 9-slice from the SVG: render corners from SVG viewBox crops, render
border strips from SVG viewBox crops, tile borders with mirror pattern.
"""

import os
import io
import numpy as np
from PIL import Image

try:
    import cairosvg
    _HAS_CAIROSVG = True
except ImportError:
    _HAS_CAIROSVG = False

_DIR = os.path.dirname(__file__)
_SVG_PATH = os.path.join(_DIR, 'output', 'image.svg')
_SRC_PATH = os.path.join(_DIR, 'output', 'image_svg_render.png')

# SVG viewBox: 0 0 2048 1365
# Content bounding box in SVG coords (viewBox units):
#   Scale factor from render (3200x2133) to SVG (2048x1365) = 0.64
#   Render content: x=524..2672, y=115..2016
#   SVG content: x=335..1710, y=74..1290
# Center of symmetry in SVG: x=1023, y=682

# SVG coordinate regions (approximate, from render coords / 1.5625):
_SVG_CONTENT_X0 = 335
_SVG_CONTENT_Y0 = 74
_SVG_CONTENT_X1 = 1710
_SVG_CONTENT_Y1 = 1290
_SVG_CENTER_X = 1023
_SVG_CENTER_Y = 682

# Corner size in SVG coords: 787/1.5625 = 504 wide, 575/1.5625 = 368 tall
_SVG_CORNER_W = 504
_SVG_CORNER_H = 368


def _render_svg_region(vb_x, vb_y, vb_w, vb_h, out_w, out_h):
    """Render a region of the SVG by setting a custom viewBox."""
    with open(_SVG_PATH, 'r') as f:
        svg_text = f.read()
    
    # Replace the viewBox and dimensions
    import re
    svg_text = re.sub(r'viewBox="[^"]*"', f'viewBox="{vb_x} {vb_y} {vb_w} {vb_h}"', svg_text)
    svg_text = re.sub(r'width="[^"]*"', f'width="{out_w}"', svg_text)
    svg_text = re.sub(r'height="[^"]*"', f'height="{out_h}"', svg_text)
    
    png_data = cairosvg.svg2png(bytestring=svg_text.encode('utf-8'),
                                 output_width=out_w, output_height=out_h)
    return Image.open(io.BytesIO(png_data)).convert('RGB')


def _clean_white(arr):
    """Force near-white pixels to pure 255 (for reportlab transparent masking)."""
    mask = np.all(arr >= 235, axis=2)
    arr[mask] = 255
    return arr


def assemble_frame(target_w_px, target_h_px):
    """
    Compose an ornamental frame at target_w_px x target_h_px pixels.
    Uses cairosvg to render SVG regions (vector-clean, no raster seams).
    Falls back to raster 9-slice if cairosvg unavailable.
    """
    if not _HAS_CAIROSVG:
        return _assemble_frame_raster(target_w_px, target_h_px)
    
    return _assemble_frame_svg(target_w_px, target_h_px)


def _assemble_frame_svg(target_w_px, target_h_px):
    """SVG-based frame assembly — render each piece from vector source."""
    
    # Determine corner size in output pixels
    # Corner aspect ratio from SVG: 504x368
    # Scale to fit max 35% of each target dimension
    max_cw = target_w_px * 0.35
    max_ch = target_h_px * 0.35
    sc = min(max_cw / _SVG_CORNER_W, max_ch / _SVG_CORNER_H, 1.5)
    
    cw = max(4, int(_SVG_CORNER_W * sc))  # corner width in output px
    ch = max(4, int(_SVG_CORNER_H * sc))  # corner height in output px
    
    mid_w = max(1, target_w_px - 2 * cw)  # horizontal border length
    mid_h = max(1, target_h_px - 2 * ch)  # vertical border length
    
    # SVG border strip regions (between corners)
    # Top border: from corner right edge to center (we'll mirror for full)
    svg_top_x0 = _SVG_CONTENT_X0 + _SVG_CORNER_W  # ~839
    svg_top_x1 = _SVG_CENTER_X                      # ~1023  
    svg_top_y0 = _SVG_CONTENT_Y0                     # ~74
    svg_top_y1 = _SVG_CONTENT_Y0 + _SVG_CORNER_H    # ~442
    svg_top_w = svg_top_x1 - svg_top_x0             # ~184
    
    # Left border: from corner bottom edge to center
    svg_lft_x0 = _SVG_CONTENT_X0                     # ~335
    svg_lft_x1 = _SVG_CONTENT_X0 + _SVG_CORNER_W    # ~839
    svg_lft_y0 = _SVG_CONTENT_Y0 + _SVG_CORNER_H    # ~442
    svg_lft_y1 = _SVG_CENTER_Y                       # ~682
    svg_lft_h = svg_lft_y1 - svg_lft_y0             # ~240
    
    # ── Render corners from SVG ──
    # TL corner: SVG region [335, 74, 504, 368]
    c_tl = _render_svg_region(_SVG_CONTENT_X0, _SVG_CONTENT_Y0, 
                               _SVG_CORNER_W, _SVG_CORNER_H, cw, ch)
    c_tr = c_tl.transpose(Image.FLIP_LEFT_RIGHT)
    c_bl = c_tl.transpose(Image.FLIP_TOP_BOTTOM)
    c_br = c_tl.rotate(180)
    
    # ── Render top border tile from SVG ──
    # Calculate tile output width proportional to SVG coords
    tile_out_w = max(1, int(svg_top_w * sc))
    # The top border has same height as corner's top portion
    # Use the actual border thickness from SVG (top border y extent ~74 to ~190 in SVG)
    # Let's use the full corner height for the viewBox but same output height
    top_tile = _render_svg_region(svg_top_x0, svg_top_y0, svg_top_w, _SVG_CORNER_H, 
                                   tile_out_w, ch)
    top_tile_flip = top_tile.transpose(Image.FLIP_LEFT_RIGHT)
    
    # Build top strip symmetrically: tile left half, then mirror for right half
    half_w = (mid_w + 1) // 2
    left_half = Image.new('RGB', (half_w, ch), (255, 255, 255))
    x = 0
    flip = False
    while x < half_w:
        tile = top_tile_flip if flip else top_tile
        pw = min(tile_out_w, half_w - x)
        if pw == tile_out_w:
            left_half.paste(tile, (x, 0))
        else:
            left_half.paste(tile.crop((0, 0, pw, ch)), (x, 0))
        x += tile_out_w
        flip = not flip
    right_half = left_half.transpose(Image.FLIP_LEFT_RIGHT)
    top_strip = Image.new('RGB', (mid_w, ch), (255, 255, 255))
    top_strip.paste(left_half, (0, 0))
    top_strip.paste(right_half, (mid_w - half_w, 0))
    bot_strip = top_strip.transpose(Image.FLIP_TOP_BOTTOM)
    
    # ── Render left border tile from SVG ──
    tile_out_h = max(1, int(svg_lft_h * sc))
    lft_tile = _render_svg_region(svg_lft_x0, svg_lft_y0, _SVG_CORNER_W, svg_lft_h,
                                   cw, tile_out_h)
    lft_tile_flip = lft_tile.transpose(Image.FLIP_TOP_BOTTOM)
    
    # Build left strip symmetrically: tile top half, then mirror for bottom half
    half_h = (mid_h + 1) // 2
    top_half = Image.new('RGB', (cw, half_h), (255, 255, 255))
    y = 0
    flip = False
    while y < half_h:
        tile = lft_tile_flip if flip else lft_tile
        ph = min(tile_out_h, half_h - y)
        if ph == tile_out_h:
            top_half.paste(tile, (0, y))
        else:
            top_half.paste(tile.crop((0, 0, cw, ph)), (0, y))
        y += tile_out_h
        flip = not flip
    bot_half = top_half.transpose(Image.FLIP_TOP_BOTTOM)
    lft_strip = Image.new('RGB', (cw, mid_h), (255, 255, 255))
    lft_strip.paste(top_half, (0, 0))
    lft_strip.paste(bot_half, (0, mid_h - half_h))
    rgt_strip = lft_strip.transpose(Image.FLIP_LEFT_RIGHT)
    
    # ── Composite ──
    frame = Image.new('RGB', (target_w_px, target_h_px), (255, 255, 255))
    
    # Corners (on top of everything)
    # First paste borders, then corners on top to hide seams
    
    # Top and bottom strips (full corner height for clean corner overlap)
    frame.paste(top_strip, (cw, 0))
    frame.paste(bot_strip, (cw, target_h_px - ch))
    
    # Left and right strips (full corner width for clean corner overlap)
    frame.paste(lft_strip, (0, ch))
    frame.paste(rgt_strip, (target_w_px - cw, ch))
    
    # Corners ON TOP (they overlap the border strip ends, hiding any seam)
    frame.paste(c_tl, (0, 0))
    frame.paste(c_tr, (target_w_px - cw, 0))
    frame.paste(c_bl, (0, target_h_px - ch))
    frame.paste(c_br, (target_w_px - cw, target_h_px - ch))
    
    # Clean whites
    arr = np.array(frame)
    arr = _clean_white(arr)
    return Image.fromarray(arr)


def _assemble_frame_raster(target_w_px, target_h_px):
    """Fallback raster-based assembly from pre-rendered PNG."""
    src = Image.open(_SRC_PATH).convert('RGB')
    
    # Just scale the full source to fit, centered
    src_w, src_h = src.size
    sc = min(target_w_px / src_w, target_h_px / src_h)
    new_w = int(src_w * sc)
    new_h = int(src_h * sc)
    resized = src.resize((new_w, new_h), Image.LANCZOS)
    
    frame = Image.new('RGB', (target_w_px, target_h_px), (255, 255, 255))
    x_off = (target_w_px - new_w) // 2
    y_off = (target_h_px - new_h) // 2
    frame.paste(resized, (x_off, y_off))
    
    arr = np.array(frame)
    arr = _clean_white(arr)
    return Image.fromarray(arr)


if __name__ == '__main__':
    # Test TOC frame (2:1 aspect ratio)
    frame_toc = assemble_frame(1200, 600)
    frame_toc.save(os.path.join(_DIR, 'output', 'test_frame_toc.png'))
    print(f'TOC test frame saved: {frame_toc.size}')

    # Test mamar frame (3.3:1 aspect ratio)
    frame_mamar = assemble_frame(1980, 600)
    frame_mamar.save(os.path.join(_DIR, 'output', 'test_frame_mamar.png'))
    print(f'Mamar test frame saved: {frame_mamar.size}')
