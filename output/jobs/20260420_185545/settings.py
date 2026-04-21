#!/usr/bin/env python3
"""
settings.py — All editable settings for the Palaot HaOtiyot build.

Edit values here and re-run build_production_new.py to see changes.
Every setting has a comment explaining what it controls.
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ═══════════════════════════════════════════════════════════════════════════
# INPUT / OUTPUT
# ═══════════════════════════════════════════════════════════════════════════
DOCX_PATH       = os.path.join(BASE_DIR, "1-22 fixed.docx")
DOC_PARTS_PATH  = os.path.join(BASE_DIR, "doc_parts.json")
OUT_PDF         = os.path.join(BASE_DIR, "output", "palaot_v2.pdf")
OUT_DIR         = os.path.join(BASE_DIR, "output")
SHAAR_BLATT_PDF = "/root/covers/v10/shar_blatt_v36_printready.pdf"

# ═══════════════════════════════════════════════════════════════════════════
# BOOK METADATA
# ═══════════════════════════════════════════════════════════════════════════
BOOK_TITLE      = 'פלאות האותיות'  # title for even-page header
TITLE_PAGE      = False  # no title page

# ═══════════════════════════════════════════════════════════════════════════
# FONTS — paths and ReportLab names
# ═══════════════════════════════════════════════════════════════════════════
FONTS_DIR       = os.path.join(BASE_DIR, "fonts")
FIXED_FONTS_DIR = os.path.join(BASE_DIR, "fonts")

# Body font — 1Carizma Regular
BODY_FONT_PATH  = os.path.join(BASE_DIR, "fonts", "EFT_NETSACH NERIA LIGHT.TTF")
BODY_FONT_NAME  = "eft_netsachnerialight"

# Heading font — EFT Pnina Medium / Regular (all headings + title)
HEADING_FONT_PATH      = os.path.join(BASE_DIR, "fonts", "EFT_NETSACH NERIA BOLD.TTF")
HEADING_FONT_NAME      = "eft_netsachneriabold"
HEADING_FONT_REG_PATH  = os.path.join(BASE_DIR, "fonts", "EFT_NETSACH NERIA.TTF")
HEADING_FONT_REG_NAME  = "eft_netsachneria"

# Dropcap font — EFT Pnina Regular (first word, slightly bigger than body)
DROPCAP_FONT_PATH = f"{FONTS_DIR}/EFT_PNINA.TTF"
DROPCAP_FONT_NAME = "pninaregular"

# Ornament font — BergamotOrnaments (keep existing ornament font)
ORNAMENT_FONT_PATH = "/root/fonts_extracted/Fonts (2)/BergamotOrnaments-3oK6.ttf"
ORNAMENT_FONT_NAME = "bergamot"

# Bold body font — 1Carizma Bold (used for bold runs in body text)
BOLD_BODY_FONT_PATH = os.path.join(BASE_DIR, "fonts", "EFT_NETSACH NERIA BOLD.TTF")
BOLD_BODY_FONT_NAME = "eft_netsachneriabold"

# Even-page header title font — EFT Pnina Medium (not bold/heavy)
EFT_TALPIYOT_FONT_PATH = f"{FONTS_DIR}/EFT_PNINA MEDIUM.TTF"
EFT_TALPIYOT_FONT_NAME = "pninamedium"

# Footnote font — reuse body font
FN_FONT_NAME    = "1carizma"

# ═══════════════════════════════════════════════════════════════════════════
# PAGE GEOMETRY (in inches, converted to points at 72 dpi)
# ═══════════════════════════════════════════════════════════════════════════
PAGE_W_IN       = 5.5  # page width in inches
PAGE_H_IN       = 8.5  # page height in inches
MARGIN_TOP      = 0.75  # top margin in inches
MARGIN_BOTTOM   = 0.5  # bottom margin in inches
MARGIN_LEFT     = 0.5  # left/right (outer) margin in inches
MARGIN_RIGHT    = 0.5  # left/right (outer) margin in inches
# inner margin (spine) = same as outer for this book
MARGIN_INNER    = 0.5
MARGIN_OUTER    = 0.5

# Derived (computed at import time)
PAGE_W          = PAGE_W_IN * 72
PAGE_H          = PAGE_H_IN * 72

# ═══════════════════════════════════════════════════════════════════════════
# COLUMN LAYOUT
# ═══════════════════════════════════════════════════════════════════════════
COL_GAP         = 14.0  # gutter between columns (pt)
NUM_COLS        = 2  # number of columns for body text

# ═══════════════════════════════════════════════════════════════════════════
# TYPOGRAPHY — sizes in points
# ═══════════════════════════════════════════════════════════════════════════
BODY_FONT_SIZE      = 13.5  # main body font size
BODY_LINE_HEIGHT    = None                   # auto = size * LINE_HEIGHT_MULT
LINE_HEIGHT_MULT    = 1.26  # line height multiplier (tighter)
DROPCAP_SIZE        = 14.2  # dropcap first-word size
DROPCAP_BODY_GAP    = 3.0  # gap between dropcap word and body text

# ── Heading 1 (ענף) ──
H1_FONT_SIZE        = 15.0  # ענף label size (surrounded by ornaments)
H1_NAME_FONT_SIZE   = 17.0  # ענף name (anaf title) — bigger than label
H1_TOP_GAP          = 28.0  # extra space above anaf heading (pt)
H1_NEW_PAGE         = True  # heading 1 forces new page
H1_ONE_COLUMN       = True  # heading 1 renders in 1-column mode
H1_ORNAMENTS        = True  # ornaments around ענף label
H1_ORNAMENT_CHAR    = "I"                    # Bergamot glyph for label ornaments
H1_ORNAMENT_SIZE    = 13.0  # size of label ornaments
H1_ORNAMENT_GAP     = 7.0  # gap between ornament and label text
H1_ORNAMENT_COLOR   = (0.486, 0.486, 0.486)  # gray ornaments

# ── Heading 2 (in-column sub-heading) ──
H2_FONT_SIZE        = 12.5  # same as body
H2_FONT_NAME        = "pninamedium"          # heading font (centered, medium weight)
H2_CENTERED         = True  # centered within column
H2_SPACE_BEFORE     = None                   # auto: PSEP * 1.0 (normal gap, no rules)
H2_SPACE_AFTER      = None                   # auto: same as PSEP (regular gap)
H2_KEEP_WITH_NEXT   = True  # must stay on same column as following par
H2_LINE_HEIGHT_MULT = 1.3  # tighter line height for multi-line h2

# ── Sefer title (first line of document) ──
TITLE_FONT_SIZE     = 21.0  # title at top of first page
TITLE_FONT_NAME     = "pninamedium"

# ── Inter-paragraph spacing ──
PARA_SEP            = None                   # auto: LH * 0.4
MIN_PARA_GAP        = 3.0  # minimum gap between paragraphs (pt)

# ── Orphan / widow control ──
MIN_PART_LINES      = 2  # minimum lines in any column fragment

# ═══════════════════════════════════════════════════════════════════════════
# HEADERS / FOOTERS
# ═══════════════════════════════════════════════════════════════════════════
HEADER_ENABLED      = True
FOOTER_ENABLED      = False                  # no footer

# ── Page number ──
PGNUM_POSITION      = 'outside'  # "outside" = outer edge of page
PGNUM_FONT_SIZE     = 13.0
PGNUM_FONT_NAME     = "pninaregular"
PGNUM_ORNAMENT      = True  # gray ornament next to page number
PGNUM_ORN_CHAR      = "I"                    # Bergamot glyph
PGNUM_ORN_SIZE      = 13.0
PGNUM_ORN_COLOR     = (0.486, 0.486, 0.486)  # gray

# ── Even page (verso) header: book title ──
EVEN_HEADER_TEXT    = "auto"                 # "auto" = use BOOK_TITLE
EVEN_HEADER_FONT    = "pninaregular"         # Pnina Regular
EVEN_HEADER_SIZE    = 11.5
EVEN_HEADER_POSITION = 'inside'  # inside = spine side

# ── Odd page (recto) header: anaf label + name ──
ODD_HEADER_FONT_LABEL  = "pninamedium"      # ענף label font (medium)
ODD_HEADER_FONT_NAME   = "pninaregular"     # anaf name font (regular)
ODD_HEADER_SIZE         = 13.0  # anaf number/label size
ODD_HEADER_MAAR_SIZE    = 14.5  # maar title size (bigger than anaf)
ODD_HEADER_DIAMOND      = True  # diamond separator
ODD_HEADER_DIAMOND_COLOR = (0.486, 0.486, 0.486)  # gray diamond
ODD_HEADER_POSITION     = 'inside'  # inside = spine side

# ═══════════════════════════════════════════════════════════════════════════
# ORNAMENTS
# ═══════════════════════════════════════════════════════════════════════════
# End-of-section ornament (before heading 1 or end of last ענף)
SECTION_END_ORNAMENT        = True
SECTION_END_ORN_CHAR        = "N"            # Bergamot character
SECTION_END_ORN_SIZE        = 14.0
SECTION_END_ORN_COLOR       = (0.0, 0.0, 0.0)

# Anaf label ornaments (2 flanking ornaments around ענף label)
ANAF_LABEL_ORNAMENTS        = False
ANAF_LABEL_ORN_CHAR         = "I"            # Bergamot wave
ANAF_LABEL_ORN_COLOR        = (0.722, 0.129, 0.129)  # gray

# ═══════════════════════════════════════════════════════════════════════════
# FOOTNOTES
# ═══════════════════════════════════════════════════════════════════════════
FN_FONT_SIZE        = 8.5  # footnote body font size
FN_LINE_HEIGHT      = None                   # auto: FN_FONT_SIZE * 1.45
FN_GAP              = 3.0  # gap between footnotes
FN_RULE_WIDTH       = 0.667  # footnote rule width as fraction of page
FN_RULE_ORNAMENTAL  = True  # use ornamental line instead of plain
FN_RULE_ORN_CHAR    = None                   # None = use vector line ornament
FN_REF_BOLD         = True  # footnote reference number is bold
FN_SUPER_SIZE       = 7.5  # superscript marker size in body text
FN_TEXT_GAP         = 20.0  # gap between body text bottom and fn rule
FN_RULE_GAP         = 8.0  # gap below fn rule before fn text

# ═══════════════════════════════════════════════════════════════════════════
# COLUMN BALANCING
# ═══════════════════════════════════════════════════════════════════════════
# Strategy: first move lines, then stretch gaps, then as last resort stretch lines
MAX_GAP_STRETCH     = None                   # auto: LH * 0.4 max extra per gap
MAX_LINE_STRETCH    = 0.1  # max 10% of LH extra per line (last resort)
ALLOW_ORPHAN        = False  # avoid orphans unless stuck
ALLOW_WIDOW         = True  # allow widows if needed (no indent on col2 first line)

# ═══════════════════════════════════════════════════════════════════════════
# LAST-LINE CENTERING
# ═══════════════════════════════════════════════════════════════════════════
LAST_LINE_CENTER    = True  # center the last line of each paragraph

# ═══════════════════════════════════════════════════════════════════════════
# DROP CAPS
# ═══════════════════════════════════════════════════════════════════════════
DROPCAP_ENABLED     = False  # bold first word as dropcap
DROPCAP_LINES       = 2  # how many lines the dropcap spans


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTED VALUES — derived from above, computed at import time
# ═══════════════════════════════════════════════════════════════════════════

def compute_derived():
    """Compute all derived values from settings. Call after modifying settings."""
    import types
    m = sys.modules[__name__]

    m.LH = m.BODY_LINE_HEIGHT or (m.BODY_FONT_SIZE * m.LINE_HEIGHT_MULT)
    m.PSEP = m.PARA_SEP if m.PARA_SEP is not None else (m.LH * 0.4)
    m.H2_SPACE_BEFORE_PT = m.H2_SPACE_BEFORE if m.H2_SPACE_BEFORE is not None else (m.PSEP * 1.8)  # a bit extra before subheading
    m.H2_SPACE_AFTER_PT = m.H2_SPACE_AFTER if m.H2_SPACE_AFTER is not None else m.PSEP             # regular gap after (tight coupling to next para)
    m.FN_LH = m.FN_LINE_HEIGHT or (m.FN_FONT_SIZE * 1.45)
    m.MAX_GAP_STRETCH_PT = m.MAX_GAP_STRETCH if m.MAX_GAP_STRETCH is not None else (m.LH * 0.4)
    m.MAX_LINE_STRETCH_PT = m.MAX_LINE_STRETCH * m.LH

import sys
compute_derived()
