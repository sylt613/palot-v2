#!/usr/bin/env python3
"""
make_shar_blatt.py — Palaot HaOtiyot Shar Blatt Generator.

Generates a clean, professional title page (shar blatt) with:
- Minimal grayscale design
- Book title (large, centered)
- Mamar info (section heading)
- Author/contact information
- Ornamental spacing

Usage:
    python3 make_shar_blatt.py
"""

import os
import sys
import json
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from bidi.algorithm import get_display

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

# Output
OUT_PDF = DIR / "output" / "shar_blatt_palaot.pdf"

# Page geometry (letter size, 5.5" x 8.5")
PAGE_W, PAGE_H = letter
MARGIN_H = 0.5 * inch  # horizontal margins
MARGIN_TOP = 1.2 * inch  # top margin (where title starts)
MARGIN_BOTTOM = 0.75 * inch  # bottom padding

# Usable area
CONTENT_W = PAGE_W - 2 * MARGIN_H
CONTENT_LEFT = MARGIN_H

# Typography
TITLE_SIZE = 32
MAMAR_LABEL_SIZE = 18
MAMAR_NAME_SIZE = 16
AUTHOR_HEADER_SIZE = 14
AUTHOR_TEXT_SIZE = 11

# Spacing (points)
SPACE_AFTER_TITLE = 48
SPACE_AFTER_MAMAR_LABEL = 8
SPACE_AFTER_MAMAR_NAME = 52
SPACE_BETWEEN_AUTHOR_LINES = 3
SPACE_AFTER_AUTHOR_HEADER = 8
SPACE_AFTER_AUTHOR_INFO = 12

# Colors (0-1 RGB)
TEXT_COLOR_DARK = (0.15, 0.15, 0.15)
TEXT_COLOR_GRAY = (0.4, 0.4, 0.4)
TEXT_COLOR_LIGHT_GRAY = (0.7, 0.7, 0.7)

# ═══════════════════════════════════════════════════════════════════════════
# FONT UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def load_font(font_name, font_path):
    """Register a TTF font with ReportLab."""
    if not os.path.exists(font_path):
        print(f"Warning: Font not found: {font_path}")
        return False
    try:
        pdfmetrics.registerFont(TTFont(font_name, font_path))
        print(f"  Loaded: {font_name}")
        return True
    except Exception as e:
        print(f"  Error loading {font_name}: {e}")
        return False

def setup_fonts():
    """Load all required fonts."""
    print("Loading fonts...")
    load_font("heading_bold", FONT_HEADING_B)
    load_font("body_regular", FONT_BODY_R)

def measure_text(text, font_name, size):
    """Measure text width in points."""
    try:
        font = pdfmetrics.getFont(font_name)
        return font.stringWidth(text, size)
    except:
        return len(text) * size * 0.5  # rough fallback

def draw_centered_text(canvas_obj, text, y, font_name, size, color=TEXT_COLOR_DARK):
    """Draw horizontally centered text at given y position."""
    canvas_obj.setFont(font_name, size)
    canvas_obj.setFillColorRGB(*color)
    text_w = measure_text(text, font_name, size)
    x = CONTENT_LEFT + (CONTENT_W - text_w) / 2
    canvas_obj.drawString(x, y, text)

def draw_right_aligned_text(canvas_obj, text, y, font_name, size, color=TEXT_COLOR_DARK):
    """Draw right-aligned text."""
    canvas_obj.setFont(font_name, size)
    canvas_obj.setFillColorRGB(*color)
    text_w = measure_text(text, font_name, size)
    x = CONTENT_LEFT + CONTENT_W - text_w
    canvas_obj.drawString(x, y, text)

# ═══════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════

def load_doc_parts():
    """Load doc_parts.json from the project directory."""
    doc_parts_path = DIR / "doc_parts.json"
    if not doc_parts_path.exists():
        print(f"Warning: doc_parts.json not found at {doc_parts_path}")
        return []
    try:
        with open(doc_parts_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading doc_parts.json: {e}")
        return []

def extract_metadata(doc_parts):
    """Extract sefer_title and first heading1 from doc_parts."""
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
# BIDIRECTIONAL TEXT HANDLING
# ═══════════════════════════════════════════════════════════════════════════

def is_hebrew(text):
    """Check if text contains Hebrew characters."""
    return any('\u0590' <= ch <= '\u05FF' for ch in text)

def vis(text):
    """Visualize RTL text for ReportLab (apply bidi algorithm)."""
    if is_hebrew(text):
        return get_display(text)
    return text

# ═══════════════════════════════════════════════════════════════════════════
# MAIN BUILD
# ═══════════════════════════════════════════════════════════════════════════

def build_shar_blatt(output_path):
    """Generate the shar blatt PDF."""
    print(f"\nBuilding shar blatt: {output_path}")
    
    # Load metadata
    doc_parts = load_doc_parts()
    sefer_title, heading1 = extract_metadata(doc_parts)
    
    # Setup fonts
    setup_fonts()
    
    # Create canvas
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    c = rl_canvas.Canvas(str(output_path), pagesize=letter)
    c.setTitle("Palaot HaOtiyot - Shar Blatt")
    
    # Current Y position (top down)
    y = PAGE_H - MARGIN_TOP
    
    # ── TITLE ──
    if sefer_title:
        title_vis = vis(sefer_title)
        draw_centered_text(c, title_vis, y, "heading_bold", TITLE_SIZE, TEXT_COLOR_DARK)
        y -= TITLE_SIZE + SPACE_AFTER_TITLE
    
    # ── MAMAR INFO ──
    if heading1:
        # Mamar label (e.g., "ענף א'")
        label_vis = vis(heading1["label"])
        if label_vis:
            draw_centered_text(c, label_vis, y, "heading_bold", MAMAR_LABEL_SIZE, TEXT_COLOR_GRAY)
            y -= MAMAR_LABEL_SIZE + SPACE_AFTER_MAMAR_LABEL
        
        # Mamar name (main heading)
        name_vis = vis(heading1["name"])
        if name_vis:
            draw_centered_text(c, name_vis, y, "heading_bold", MAMAR_NAME_SIZE, TEXT_COLOR_DARK)
            y -= MAMAR_NAME_SIZE + SPACE_AFTER_MAMAR_NAME
    
    # ── AUTHOR SECTION ──
    # Header: "לכל עניני הספר:"
    header_vis = vis("לכל עניני הספר:")
    draw_centered_text(c, header_vis, y, "heading_bold", AUTHOR_HEADER_SIZE, TEXT_COLOR_DARK)
    y -= AUTHOR_HEADER_SIZE + SPACE_AFTER_AUTHOR_HEADER
    
    # Author name
    author_name = "יואל באלזאם"
    author_name_vis = vis(author_name)
    draw_centered_text(c, author_name_vis, y, "heading_bold", AUTHOR_TEXT_SIZE, TEXT_COLOR_DARK)
    y -= AUTHOR_TEXT_SIZE + SPACE_BETWEEN_AUTHOR_LINES
    
    # Contact info
    contact_lines = [
        "(908) 783-8307",
        "2151 58th St",
        "Brooklyn, NY 11204"
    ]
    
    for line in contact_lines:
        draw_centered_text(c, line, y, "body_regular", AUTHOR_TEXT_SIZE, TEXT_COLOR_DARK)
        y -= AUTHOR_TEXT_SIZE + SPACE_BETWEEN_AUTHOR_LINES
    
    # Save PDF
    c.save()
    print(f"Saved: {output_path}")
    print(f"\nShar blatt complete!")
    print(f"  Title: {sefer_title}")
    print(f"  Mamar: {heading1['name'] if heading1 else 'N/A'}")

# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    build_shar_blatt(str(OUT_PDF))
