#!/usr/bin/env python3
"""
make_shar_blatt.py — Palaot HaOtiyot Shar Blatt Generator

Generates a professional title page (shar blatt) for the Palaot HaOtiyot sefer.
Composites text on a clean background with proper typography and spacing.

Run normally:
    python3 make_shar_blatt.py

Usage with external background:
    SHAR_BG_SRC=/path/to/bg.png python3 make_shar_blatt.py
"""

import io
import os
import sys
import json
from pathlib import Path

import numpy as np
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageFont
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

# ═══════════════════════════════════════════════════════════════════════════
# PATHS & CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

DIR = Path(__file__).resolve().parent

# Import settings
sys.path.insert(0, str(DIR))
import settings as S

# Font paths
FONTS_DIR = Path(S.FONTS_DIR) if hasattr(S, 'FONTS_DIR') else Path("/data/fonts/Fonts (2) (5)/Fonts (2)")
FONT_HEADING_B = str(FONTS_DIR / "EFT_FRANKFURT BOLD.TTF")
FONT_BODY_R = str(FONTS_DIR / "1CarizmaRegular.ttf")
FONT_BODY_B = str(FONTS_DIR / "1CarizmaBold.ttf")

# Output versioning
VERSION = os.getenv("SHAR_VERSION", "v1")
OUT_BG = DIR / "output" / f"shar_blatt_{VERSION}_bg.png"
OUT_PDF = DIR / "output" / f"shar_blatt_{VERSION}_printready.pdf"

# Page geometry
BLEED = 0.125
TRIM_W = 5.5
TRIM_H = 8.5
MARK_MARGIN = 0.25

PANEL_W_IN = TRIM_W + 2 * BLEED
PANEL_H_IN = TRIM_H + 2 * BLEED
BW = PANEL_W_IN * inch
BH = PANEL_H_IN * inch
PAGE_W = (PANEL_W_IN + 2 * MARK_MARGIN) * inch
PAGE_H = (PANEL_H_IN + 2 * MARK_MARGIN) * inch
OX = MARK_MARGIN * inch
OY = MARK_MARGIN * inch

# Typography base
BASE_SZ = 42
TITLE_FONT = "heading_bold"
MAMAR_FONT = "body_regular"
AUTHOR_FONT = "body_regular"
AUTHOR_NAME_FONT = "heading_bold"

# Colors (0-1 grayscale)
TEXT_COLOR_DARK = 0.0
TEXT_COLOR_GRAY = 0.35

_FONTS_REG = False

# ═══════════════════════════════════════════════════════════════════════════
# TEXT UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def vis(text):
    """Apply bidirectional text algorithm for Hebrew."""
    return get_display(text, base_dir="R")

def fix_quotes(text):
    """Convert ASCII quotes to Hebrew quotes."""
    out = []
    for ch in text:
        if ch == '"':
            out.append("\u05F4")
        elif ch == "'":
            out.append("\u05F3")
        else:
            out.append(ch)
    return "".join(out)

def register_fonts():
    """Register all required fonts with ReportLab."""
    global _FONTS_REG
    if _FONTS_REG:
        return
    pdfmetrics.registerFont(TTFont("heading_bold", FONT_HEADING_B))
    pdfmetrics.registerFont(TTFont("body_regular", FONT_BODY_R))
    pdfmetrics.registerFont(TTFont("body_bold", FONT_BODY_B))
    _FONTS_REG = True

def text_width(text, font_name, size):
    """Measure text width in points."""
    return pdfmetrics.stringWidth(vis(fix_quotes(text)), font_name, size)

def fit_font_size(font_name, lines, max_width, start_size=60, min_size=8):
    """Find largest font size where all lines fit within max_width."""
    for size in range(start_size, min_size - 1, -1):
        widths = [text_width(line, font_name, size) for line in lines]
        if max(widths) <= max_width:
            return size
    return min_size

# ═══════════════════════════════════════════════════════════════════════════
# BACKGROUND HANDLING
# ═══════════════════════════════════════════════════════════════════════════

def make_fallback_bg():
    """Create a minimal grayscale background if none provided."""
    w, h = int(BW), int(BH)
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    
    # Simple border
    margin = 40
    d.rectangle(
        [margin, margin, w - margin, h - margin],
        outline=(200, 200, 200),
        width=2
    )
    d.rectangle(
        [margin + 8, margin + 8, w - margin - 8, h - margin - 8],
        outline=(230, 230, 230),
        width=1
    )
    
    img.save(OUT_BG)
    print(f"Created fallback background: {OUT_BG}")
    return img

def ensure_background():
    """Prepare background image (from SHAR_BG_SRC or fallback)."""
    src_override = os.getenv("SHAR_BG_SRC")
    
    if src_override:
        print(f"Using provided background: {src_override}")
        src = Image.open(src_override).convert("RGB")
        
        # Trim near-white surround if present
        arr = np.array(src)
        near_white = (arr[:, :, 0] > 245) & (arr[:, :, 1] > 245) & (arr[:, :, 2] > 245)
        ys, xs = np.where(~near_white)
        if len(xs) > 0 and len(ys) > 0:
            l, t, r, b = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
            src = src.crop((l, t, r, b))
        
        # Fit within panel
        tw, th = int(BW), int(BH)
        sw, sh = src.size
        scale = min((tw * 0.985) / sw, (th * 0.975) / sh)
        nw, nh = int(sw * scale), int(sh * scale)
        src = src.resize((nw, nh), Image.LANCZOS)
        
        canvas = Image.new("RGB", (tw, th), (255, 255, 255))
        left = (tw - nw) // 2
        top = max(6, (th - nh) // 2 - 6)
        canvas.paste(src, (left, top))
        src = canvas
        
        # Convert to grayscale, add center lift for text readability
        g = ImageOps.autocontrast(src.convert("L"), cutoff=1)
        a = np.array(g, dtype=np.float32)
        h, w = a.shape
        yy, xx = np.mgrid[0:h, 0:w]
        cx, cy = w * 0.50, h * 0.54
        rx, ry = w * 0.29, h * 0.56
        d = ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2
        fade = np.clip(1.0 - d, 0.0, 1.0)
        
        # Lift center but keep texture
        lifted = np.clip(a + (fade ** 1.55) * 36.0, 0, 255).astype(np.uint8)
        out = Image.fromarray(lifted, "L").convert("RGB")
        out.save(OUT_BG)
        print(f"Prepared background: {OUT_BG}")
        return out
    
    if OUT_BG.exists():
        print(f"Reusing background: {OUT_BG}")
        return Image.open(OUT_BG).convert("RGB")
    
    # Fallback
    return make_fallback_bg()

def detect_safe_box(img_path):
    """Detect the inner white text area of the shar blatt background."""
    img = Image.open(img_path).convert("L")
    w, h = img.size
    
    # Work at reduced resolution for speed
    sw, sh = 400, 600
    sm = img.resize((sw, sh), Image.LANCZOS)
    px = sm.load()
    
    cx, cy = sw // 2, sh // 2
    
    def col_min(x, y0, y1):
        return min(px[x, y] for y in range(y0, y1))
    
    def row_min(y, x0, x1):
        return min(px[x, y] for x in range(x0, x1))
    
    band_y0, band_y1 = int(sh * 0.35), int(sh * 0.65)
    band_x0, band_x1 = int(sw * 0.35), int(sw * 0.65)
    dark_thresh = 180
    
    # Scan from center outward
    left = cx
    while left > int(sw * 0.05) and col_min(left, band_y0, band_y1) > dark_thresh:
        left -= 1
    right = cx
    while right < int(sw * 0.95) and col_min(right, band_y0, band_y1) > dark_thresh:
        right += 1
    top = cy
    while top > int(sh * 0.05) and row_min(top, band_x0, band_x1) > dark_thresh:
        top -= 1
    bottom = cy
    while bottom < int(sh * 0.95) and row_min(bottom, band_x0, band_x1) > dark_thresh:
        bottom += 1
    
    # Fallback
    if right - left < int(sw * 0.25):
        left, right = int(sw * 0.20), int(sw * 0.80)
    if bottom - top < int(sh * 0.30):
        top, bottom = int(sh * 0.15), int(sh * 0.85)
    
    # Inset for text breathing room
    pad_x = int((right - left) * 0.06)
    pad_y_top = int((bottom - top) * 0.06)
    pad_y_bot = int((bottom - top) * 0.16)
    
    left = (left + pad_x) * (w / sw)
    right = (right - pad_x) * (w / sw)
    top = (top + pad_y_top) * (h / sh)
    bottom = (bottom - pad_y_bot) * (h / sh)
    
    return {
        "left": left,
        "right": right,
        "top": top,
        "bottom": bottom,
        "w": right - left,
        "h": bottom - top,
    }

# ═══════════════════════════════════════════════════════════════════════════
# PDF DRAWING HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def draw_crop_marks(c):
    """Draw printing crop/registration marks."""
    tl = (MARK_MARGIN + BLEED) * inch
    tr = (MARK_MARGIN + PANEL_W_IN - BLEED) * inch
    tb = (MARK_MARGIN + BLEED) * inch
    tt = (MARK_MARGIN + PANEL_H_IN - BLEED) * inch
    ml = 0.1875 * inch
    mg = 0.0625 * inch
    bl = MARK_MARGIN * inch
    
    c.saveState()
    c.setStrokeGray(0.0)
    c.setLineWidth(0.25)
    
    for x in (tl, tr):
        c.line(x, tb - mg - ml, x, tb - mg)
        c.line(x, tt + mg, x, tt + mg + ml)
    for y in (tb, tt):
        c.line(tl - mg - ml, y, tl - mg, y)
        c.line(tr + mg, y, tr + mg + ml, y)
    
    c.setDash(3, 3)
    c.setStrokeGray(0.7)
    c.rect(bl, bl, BW, BH, stroke=1, fill=0)
    c.restoreState()

def draw_centered_text(c, text, font, size, cx, y, gray=TEXT_COLOR_DARK):
    """Draw horizontally centered text."""
    d = vis(fix_quotes(text))
    w = pdfmetrics.stringWidth(d, font, size)
    c.setFont(font, size)
    c.setFillGray(gray)
    c.drawString(cx - w / 2, y, d)

def draw_centered_outline(c, text, font, size, cx, y, text_gray=TEXT_COLOR_DARK, outline_gray=0.68):
    """Draw text with multi-ring outline (glow effect)."""
    d = vis(fix_quotes(text))
    w = pdfmetrics.stringWidth(d, font, size)
    x = cx - w / 2
    
    white_r = max(1.8, size * 0.032)
    gray_r0 = white_r + max(1.15, size * 0.018)
    step = max(0.22, size * 0.0042)
    gray_r1 = gray_r0 + step
    gray_r2 = gray_r1 + step
    gray_r3 = gray_r2 + step
    gray_mid = outline_gray + (1.0 - outline_gray) * 0.35
    gray_outer = outline_gray + (1.0 - outline_gray) * 0.70
    
    ring_dirs = [
        (1.00, 0.00), (0.93, 0.36), (0.71, 0.71), (0.36, 0.93),
        (0.00, 1.00), (-0.36, 0.93), (-0.71, 0.71), (-0.93, 0.36),
        (-1.00, 0.00), (-0.93, -0.36), (-0.71, -0.71), (-0.36, -0.93),
        (0.00, -1.00), (0.36, -0.93), (0.71, -0.71), (0.93, -0.36),
    ]
    
    c.saveState()
    c.setFont(font, size)
    
    # Draw rings
    for radius, g in [(gray_r3, gray_outer), (gray_r2, gray_mid), (gray_r1, outline_gray), (gray_r0, outline_gray)]:
        c.setFillGray(g)
        for ux, uy in ring_dirs:
            c.drawString(x + ux * radius, y + uy * radius, d)
    
    # White knockout
    c.setFillGray(1.0)
    for ux, uy in ring_dirs:
        c.drawString(x + ux * white_r, y + uy * white_r, d)
    
    # Text
    c.setFillGray(text_gray)
    c.drawString(x, y, d)
    c.restoreState()

# ═══════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════

def load_doc_parts():
    """Load doc_parts.json."""
    doc_parts_path = DIR / "doc_parts.json"
    if not doc_parts_path.exists():
        print(f"Warning: doc_parts.json not found")
        return []
    try:
        with open(doc_parts_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading doc_parts.json: {e}")
        return []

def extract_metadata(doc_parts):
    """Extract title and first heading from doc_parts."""
    sefer_title = None
    heading1 = None
    
    for item in doc_parts:
        if item.get("type") == "sefer_title" and not sefer_title:
            sefer_title = item.get("text", "").strip()
        elif item.get("type") == "heading1" and not heading1:
            heading1 = {
                "label": item.get("label", "").strip(),
                "name": item.get("name", "").strip(),
            }
    
    return sefer_title, heading1

# ═══════════════════════════════════════════════════════════════════════════
# MAIN BUILD
# ═══════════════════════════════════════════════════════════════════════════

def compose_pdf():
    """Generate the shar blatt PDF."""
    print(f"\n{'='*70}")
    print(f"Palaot HaOtiyot Shar Blatt Builder")
    print(f"{'='*70}\n")
    
    # Load metadata
    doc_parts = load_doc_parts()
    sefer_title, heading1 = extract_metadata(doc_parts)
    
    print(f"Title: {sefer_title}")
    print(f"Mamar: {heading1['name'] if heading1 else 'N/A'}\n")
    
    # Setup
    register_fonts()
    ensure_background()
    safe = detect_safe_box(str(OUT_BG))
    print(f"Safe box: left={safe['left']:.0f}, right={safe['right']:.0f}, "
          f"top={safe['top']:.0f}, bottom={safe['bottom']:.0f}\n")
    
    # Create canvas
    os.makedirs(OUT_BG.parent, exist_ok=True)
    c = rl_canvas.Canvas(str(OUT_PDF), pagesize=(PAGE_W, PAGE_H))
    c.setTitle("Palaot HaOtiyot - Shar Blatt")
    
    draw_crop_marks(c)
    c.saveState()
    c.translate(OX, OY)
    
    # Draw background
    c.drawImage(ImageReader(str(OUT_BG)), 0, 0, width=BW, height=BH, mask="auto")
    
    # Extract safe area coordinates
    safe_l = safe["left"]
    safe_r = safe["right"]
    safe_t = BH - safe["top"]
    safe_b = BH - safe["bottom"]
    safe_b = max(safe_b, BH * 0.20)
    
    cx = (safe_l + safe_r) / 2
    pw = safe_r - safe_l
    ph = safe_t - safe_b
    
    # Helper functions
    def draw_center(text, font, size, y, gray=TEXT_COLOR_DARK):
        draw_centered_text(c, text, font, size, cx, y, gray)
    
    def draw_outline(text, font, size, y, text_gray=TEXT_COLOR_DARK):
        draw_centered_outline(c, text, font, size, cx, y, text_gray)
    
    # ── Fit typography ─────────────────────────────────────────────────────
    title_lines = ["מחיית", "עמלק", "תיקון"]
    base_sz = BASE_SZ
    
    while base_sz >= 15:
        szs = [base_sz, int(base_sz * 1.05), int(base_sz * 0.75)]
        gaps = [int(base_sz * 0.20), int(base_sz * 0.16)]
        ws = [text_width(ln, TITLE_FONT, s) for ln, s in zip(title_lines, szs)]
        title_h = szs[0] + gaps[0] + szs[1] + gaps[1] + szs[2]
        
        if max(ws) <= pw * 0.90:
            break
        base_sz -= 2
    
    title_sizes = [base_sz, int(base_sz * 1.05), int(base_sz * 0.75)]
    title_gaps = [int(base_sz * 0.20), int(base_sz * 0.16)]
    
    # Spacing
    sec_gap = int(base_sz * 0.50)   # increased from 0.32
    sub_gap = int(base_sz * 0.12)   # increased from 0.08
    sec_gap2 = int(base_sz * 0.45)  # increased from 0.28 (space before author)
    sec_gap3 = int(base_sz * 0.40)  # increased from 0.26
    
    # Mamar
    mamar_lbl = "מאמר"
    mamar_name = heading1["name"] if heading1 else "קליפת עמלק"
    ml_sz = fit_font_size(MAMAR_FONT, [mamar_lbl], pw * 0.26, 12, 6)
    mt_sz = fit_font_size(MAMAR_FONT, [mamar_name], pw * 0.88, 14, 6)
    
    # Author
    mat_sz = fit_font_size(AUTHOR_FONT, ["מאת"], pw * 0.20, 10, 5)
    auth_name_sz = fit_font_size(AUTHOR_NAME_FONT, ["יואל באלזאם"], pw * 0.50, 14, 8)
    auth_info = ["(908) 783-8307", "2151 58th St", "Brooklyn, NY 11204"]
    auth_info_sz = fit_font_size(AUTHOR_FONT, auth_info, pw * 0.60, 10, 6)
    
    # ── Layout from top ────────────────────────────────────────────────────
    Y = safe_t - int(ph * 0.15)  # moved title down from 0.08 to 0.15
    
    # Title
    for i, (text, size) in enumerate(zip(title_lines, title_sizes)):
        if i == 0:
            draw_center(text, TITLE_FONT, size, Y - size, TEXT_COLOR_DARK)
        else:
            draw_outline(text, TITLE_FONT, size, Y - size, TEXT_COLOR_DARK)
        Y -= size
        if i < len(title_gaps):
            Y -= title_gaps[i]
    
    # Mamar section
    Y -= sec_gap
    draw_center(mamar_lbl, MAMAR_FONT, ml_sz, Y - ml_sz, TEXT_COLOR_GRAY)
    Y -= ml_sz + sub_gap
    draw_center(mamar_name, MAMAR_FONT, mt_sz, Y - mt_sz, TEXT_COLOR_DARK)
    Y -= mt_sz
    
    # Author section
    Y -= sec_gap2
    draw_center("מאת", AUTHOR_FONT, mat_sz, Y - mat_sz, TEXT_COLOR_DARK)
    Y -= mat_sz + sub_gap
    draw_center("יואל באלזאם", AUTHOR_NAME_FONT, auth_name_sz, Y - auth_name_sz, TEXT_COLOR_DARK)
    Y -= auth_name_sz + sub_gap
    
    # Contact info
    for line in auth_info:
        draw_center(line, AUTHOR_FONT, auth_info_sz, Y - auth_info_sz, TEXT_COLOR_DARK)
        Y -= auth_info_sz + sub_gap
    
    c.restoreState()
    c.save()
    print(f"Saved: {OUT_PDF}\n")

if __name__ == "__main__":
    compose_pdf()
