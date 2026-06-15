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
OUT_PDF         = os.path.join(BASE_DIR, "output", "palaot_tehila_full.pdf")
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

# Body font — Mazal Regular (owner request; was Carizma Regular)
BODY_FONT_PATH  = "/data/palot-v2code/fonts/mazalregular.ttf"
BODY_FONT_NAME  = "mazal"

# Heading font — EFT Frankfurt Bold / Regular (all headings + title + header)
HEADING_FONT_PATH      = "/data/palot-v2code/fonts/EFT_FRANKFURT BOLD.TTF"
HEADING_FONT_NAME      = "pninamedium"
HEADING_FONT_REG_PATH  = "/data/palot-v2code/fonts/EFT_FRANKFURT.TTF"
HEADING_FONT_REG_NAME  = "pninaregular"

# Dropcap / first-word font — Carizma Bold
DROPCAP_FONT_PATH = "/data/palot-v2code/fonts/1CarizmaBold.ttf"
DROPCAP_FONT_NAME = "carizmabold"

# Ornament font — BergamotOrnaments (symbol font, keep)
ORNAMENT_FONT_PATH = "/root/fonts_extracted/Fonts (2)/BergamotOrnaments-3oK6.ttf"
ORNAMENT_FONT_NAME = "bergamot"

# Bold body font — Mazal Bold (owner request; was Carizma Bold)
BOLD_BODY_FONT_PATH = "/data/palot-v2code/fonts/mazalbold.ttf"
BOLD_BODY_FONT_NAME = "mazalbold"

# Even-page header title font — EFT Frankfurt Bold
EFT_TALPIYOT_FONT_PATH = "/data/palot-v2code/fonts/EFT_FRANKFURT BOLD.TTF"
EFT_TALPIYOT_FONT_NAME = "pninamedium"

# Footnote font — reuse body font
FN_FONT_NAME    = "mazal"

# ═══════════════════════════════════════════════════════════════════════════
# PAGE GEOMETRY (in inches, converted to points at 72 dpi)
# ═══════════════════════════════════════════════════════════════════════════
PAGE_W_IN       = 5.5  # page width in inches
PAGE_H_IN       = 8.5  # page height in inches
MARGIN_TOP      = 0.5  # top margin in inches
MARGIN_BOTTOM   = 0.4  # bottom margin in inches
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
BODY_FONT_SIZE      = 12.2  # main body font size
BODY_LINE_HEIGHT    = None                   # auto = size * LINE_HEIGHT_MULT
LINE_HEIGHT_MULT    = 1.33  # line height multiplier

# ── Justification: loose-line control (ported from kidushin) ──────────────────
# Mazal's natural interword space is unusually narrow (~0.175em), so full
# justification blows lines out into spacey gaps ("rivers"). At DRAW time we cap
# the rendered interword gap at JUST_WORD_GAP_MAX_MULT x the (target) natural
# space and absorb the residual as a hair of uniform LETTER-SPACING across the
# line (Hebrew square script is non-connecting, so this is invisible — it is NOT
# kashida). This kills spacey gaps WITHOUT changing line breaks or page count.
JUST_WORD_GAP_MAX_MULT  = 1.5    # rendered gap target = 1.5x the natural space
JUST_LETTER_SPACE_MAX_EM = 0.013  # residual letter-spacing cap, <= +1.3% of FS / slot (barely perceptible, kidushin-like). Lowered from 0.04 (owner: letters read too loose). Residual the gap-cap can't absorb stays as a slightly wider word gap on a few lines — acceptable.
# Draw-time target word-space: cap + letter-spacing are computed against
# max(font-natural-space, size x WORD_SPACE_TARGET_EM). Widening the reference a
# touch (toward Bringhurst's ~0.24em optimum) makes the cap pack lines tighter at
# the source. This affects ONLY the rendered fill, never the wrap/line-count, so
# it cannot desync the measurer or shift pagination. 0 = use font space as-is.
WORD_SPACE_TARGET_EM    = 0.25   # owner standard (kidushin parity 2026-06-14; was 0.235)

# ── Line breaking (Knuth–Plass total-fit) — ported from kidushin ──────────────
# When True, body paragraphs are broken with a global optimal (minimise total
# demerits) algorithm instead of greedy first-fit + fill-variance heuristic. This
# evens out inter-word spacing across the whole paragraph (the main driver of
# spacey rivers). Hebrew has no hyphenation → only inter-word breakpoints. It
# changes ONLY which words land on which line — line/page counts may shift, but the
# measurer is unaffected (KP is used for the SAME measured widths). 0 ragged kept.
USE_KP_LINEBREAK     = True
# Inter-word glue (TeX-style), as a fraction of the natural space width. Mazal's
# natural space is narrow, so the stretch unit must be on that order or normal full
# lines score "too loose" and the optimiser degenerates.
KP_SPACE_STRETCH     = 0.50   # +50% of natural space
KP_SPACE_SHRINK      = 0.45   # how far a space may shrink (generous)
# Bias toward tighter lines: stretching (loose, river-prone) lines penalised harder
# than shrinking, so the optimiser packs a word in and shrinks rather than leaving a
# gappy line. 1.0 = symmetric; >1 prefers shrinking over growing.
KP_STRETCH_BIAS      = 2.00
KP_MAX_BADNESS       = 100000.0  # badness cap (TeX inf_bad). High so the cubic
                                 # gradient survives below ~0.9 fill (else the DP
                                 # tie-breaks "one catastrophic + one perfect" line
                                 # over "two decent" — the stubborn loose line bug).
KP_LINE_PENALTY      = 10.0   # added to each line's badness before squaring
KP_EMERGENCY_STRETCH = 1.0    # extra stretch (× space) granted in the fallback pass
KP_OVERCAP_WEIGHT    = 1.0    # re-add clipped cubic past the cap (monotone gradient)
KP_RIVER_WEIGHT      = 2500.0 # penalise vertically-stacked inter-word gaps
KP_ADJ_DEMERITS      = 30000.0  # fitness-class adjacency demerit (even page color)

# ── Font expansion (draw-time pdfTeX "hz" lever) — ported from kidushin ────────
# When a justified body line's residual inter-word gap would still exceed
# FX_TARGET_EXCESS (× natural space) after the gap-cap + letter-spacing cascade,
# widen the glyphs horizontally (up to +FX_MAX_EXPAND) so the gap drops toward the
# target. Kills spacey lines / rivers without touching wrap/line-count/height
# (invisible to paginator/balancer/measurer/footnotes). ±4% is below the visible
# threshold for this Hebrew face. Set FX_ENABLE=False to disable.
FX_ENABLE            = True
FX_MAX_EXPAND        = 0.04    # max horizontal glyph stretch (fraction; 0.04 = +4%)
FX_TARGET_EXCESS     = 0.50    # gap excess (× natural space) we expand down toward
FX_STUBBORN_TRIGGER  = 1.0     # a line still this loose sits at the packing limit —
FX_MAX_EXPAND_STUBBORN = 0.07  # allow stronger glyph expansion for THAT line only

# ── Interword FLOOR (draw-time anti-cram) ──
# The mirror of the FX expand lever, applied to OVER-FULL lines.  When a line is
# packed so tight that its natural interword gap falls below a readable floor
# (words touching / overlapping — e.g. a balance reflow shrank a paragraph to
# fewer lines), CONDENSE the glyphs horizontally (FX < 1.0, bounded) just enough
# to lift the gap back to the floor.  Conservation: the line still fills col_width
# exactly, so wrap / line-count / height are untouched (invisible to the
# paginator / balancer).  Readable interword is sacred — a sane positive floor is
# enforced; words can never touch.  JUST_WORD_GAP_MIN_MULT is the floor as a
# fraction of the natural font space; FX_MAX_CONDENSE bounds how far glyphs may be
# squeezed (±3.5% is below the visible threshold for this Hebrew face).
JUST_WORD_GAP_MIN_MULT = 0.90  # gap floor = 0.90x the natural font space (readable)
FX_MAX_CONDENSE        = 0.06  # max horizontal glyph CONDENSE (fraction; 0.06 = -6%)
DROPCAP_SIZE        = 12.8  # first word stays visibly larger than body
DROPCAP_BODY_GAP    = 3.0  # gap between dropcap word and body text

# ── Heading 1 (ענף) ──
H1_FONT_SIZE        = 15.0  # ענף label size (surrounded by ornaments)
H1_NAME_FONT_SIZE   = 17.0  # ענף name (anaf title) — bigger than label
H1_TOP_GAP          = 28.0  # extra space above anaf heading (pt)
H1_NEW_PAGE         = True  # heading 1 forces new page
H1_ONE_COLUMN       = True  # heading 1 renders in 1-column mode
H1_ORNAMENTS        = True  # ornaments around ענף label
ANAF_NUMBER_GERESH  = False # if False, strip trailing geresh/apostrophe from ענף number (ענף א not ענף א׳)
H1_ORNAMENT_CHAR    = "I"                    # Bergamot glyph for label ornaments
H1_ORNAMENT_SIZE    = 13.0  # size of label ornaments
H1_ORNAMENT_GAP     = 7.0  # gap between ornament and label text
H1_ORNAMENT_COLOR   = (0.486, 0.486, 0.486)  # gray ornaments

# ── Heading 2 (in-column sub-heading) ──
H2_FONT_SIZE        = 12.5  # same as body
H2_FONT_NAME        = "pninamedium"         # match subheadings to Frankfurt heading style
H2_CENTERED         = True  # centered within column
H2_SPACE_BEFORE     = None                   # auto: PSEP * 1.0 (normal gap, no rules)
H2_SPACE_AFTER      = None                   # auto: same as PSEP (regular gap)
H2_KEEP_WITH_NEXT   = True  # must stay on same column as following par
H2_LINE_HEIGHT_MULT = 1.3  # tighter line height for multi-line h2

# ── Sefer title (first line of document) ──
TITLE_FONT_SIZE     = 21.0  # title at top of first page
TITLE_FONT_NAME     = "pninamedium"          # EFT Frankfurt Bold (shared alias)

# ── Inter-paragraph spacing ──
PARA_SEP            = None                   # auto: LH * 0.4
MIN_PARA_GAP        = 3.0  # minimum gap between paragraphs (pt)

# ── Orphan / widow control ──
MIN_PART_LINES      = 1  # allow 1-line widow at column bottom if it helps balance
MIN_HEAD_LINES      = 1  # min lines kept in col1 head (widow as last resort)
MIN_TAIL_LINES      = 2  # min lines in col2 tail / next-page tail (no orphan → 2)

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
# Post-anaf divider (anaf_end_orn_raw.png) is a self-contained flourish, NOT a
# tileable band. It MUST be drawn CONTAIN (fit-inside its width x height box,
# preserving native w:h) — never fit-to-box (stretch). This factor shrinks the
# contained ornament a bit (owner asked for "a bit smaller"); 0.85 = 15% smaller
# than the full undistorted contained size.
POST_ANAF_DIV_SCALE         = 0.85

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
# PAGE FILL  (grow a short page to the bottom; bottom-align columns)
# ═══════════════════════════════════════════════════════════════════════════
# When a page ends mid-section with significant bottom whitespace, fill it by
# (1) bounded inter-paragraph glue, then (2) a bounded UNIFORM leading feather
# (ELH).  Palot keeps ELH enabled (unlike kidushin's no-feather pages).  All
# knobs are fractions of LH / column-height so they scale with body size.
PAGE_FILL_ENABLE        = True   # master switch for the page-fill pass
PAGE_FILL_MAX_GAP_FRAC  = 1.25   # max inter-paragraph glue per stretchable gap = LH * this
PAGE_FILL_MAX_ELH_FRAC  = 0.06   # max uniform leading feather per line = LH * this (palot: ELH on)
# Section-tail redistribution: when a donor page is about to break before a
# section heading and the resulting section-tail page would be catastrophically
# short, pull trailing paragraph group(s) back from the donor so both pages end
# reasonably full.  Triggered only for true section tails (next element = anaf).
SECTION_TAIL_PULL_ENABLE   = True
SECTION_TAIL_SHORT_FRAC    = 0.40  # tail page is "too short" if filled < this fraction of avail
# Donor floor raised: the donor is a mid-section page that must stay (nearly)
# full — pulling content off it to feed a *section-end* tail (which is allowed to
# end short before its closing ornament) leaves the donor with a large, un-fillable
# empty bottom (owner: folio vav "tons empty space on bottom").  Only pull when the
# donor still ends near-full afterwards.
SECTION_TAIL_DONOR_MIN_FRAC= 0.82  # never pull so much the donor drops below this fill fraction
SECTION_TAIL_PULL_MAX_GROUPS = 4   # cap how many paragraph groups may move back

# ── Final-page pull-back (end of book) ──────────────────────────────────────
# The very last page of the book ends a section but has NO following anaf, so the
# section-tail pull above (which keys on a trailing anaf) never fires for it.  When
# the book's final content forms a near-empty stub page (a few lines + the closing
# ornament — the owner's "tetvav unacceptable"), pull trailing paragraph group(s)
# back from the PREVIOUS page so the final page stops being a tiny stub.
# Owner directive (2026-06-14): MAIN pages must stay FULL; pull the MINIMUM off the
# previous page.  The last page of a section need NOT be full — it only has to stop
# being an unacceptable tiny stub.  So target a MODEST final-page fill and keep the
# donor floor HIGH (the previous page stays essentially full); the pull is the
# fewest paragraph groups that lifts the final page above the stub threshold.
LAST_PAGE_PULL_ENABLE      = True
LAST_PAGE_MIN_FILL_FRAC    = 0.18  # final page is a "stub" if filled < this fraction of avail
LAST_PAGE_DONOR_MIN_FRAC   = 0.55  # keep the previous (main) page at/above this fill fraction
LAST_PAGE_PULL_MAX_GROUPS  = 4     # cap how many paragraph groups may move back

# ═══════════════════════════════════════════════════════════════════════════
# LAST-PAGE BALANCE
# ═══════════════════════════════════════════════════════════════════════════
# The final page cannot be grown to fill the sheet (a few-line tail would become
# grossly spacey), and a short single paragraph often cannot reflow to an even
# split.  When its two columns differ by more than the tolerance, bottom-align the
# shorter column to the taller one's baseline → a sanctioned equal short tail with
# no extra leading.  Tolerance is LH-relative.
LAST_PAGE_BOTTOM_ALIGN       = True
LAST_PAGE_BALANCE_TOL_FRAC   = 0.25  # tolerate <= LH*this height diff before aligning

# ═══════════════════════════════════════════════════════════════════════════
# LAST-LINE CENTERING
# ═══════════════════════════════════════════════════════════════════════════
LAST_LINE_CENTER    = True  # center the last line of each paragraph

# ═══════════════════════════════════════════════════════════════════════════
# DROP CAPS
# ═══════════════════════════════════════════════════════════════════════════
DROPCAP_ENABLED     = True   # bold first word as dropcap
DROPCAP_LINES       = 2  # how many lines the dropcap spans


# ═══════════════════════════════════════════════════════════════════════════
# MAMAR / SEFER_TITLE BOX
# ═══════════════════════════════════════════════════════════════════════════
# The mamar-box renderer supports multiple source images and post-processing
# pipelines. Selection is driven by these settings (env var MAMAR_BOX_VARIANT
# still works as an override for quick A/B).
#
# VARIANTS:
#   'default' → output/mamar_box_gpt15.png     (original GPT-15 ornate frame)
#   'alt'     → output/mamar_box_alt_asset.png (user-supplied variant)
#   'newbox'  → output/mamar_box_newbox.png    (clean rounded-rect w/ flourishes)
#
# If MAMAR_BOX_IMAGE_PATH is set, it overrides the variant's path.
# If MAMAR_BOX_BBOX is set, it overrides the variant's crop bbox (l,t,r,b).
# If MAMAR_BOX_ORN_H_PT is set, it overrides the variant's default orn height.
MAMAR_BOX_VARIANT        = 'newbox'     # 'default' | 'alt' | 'newbox'
MAMAR_BOX_IMAGE_PATH     = None         # optional absolute path override
MAMAR_BOX_BBOX           = None         # optional (l,t,r,b) crop override
MAMAR_BOX_ORN_H_PT       = None         # optional float override (pt)

# ── Cap-split tuning (horizontal ornament preservation) ──
# If MAMAR_BOX_CAP_SPLIT_FRACS is a 4-tuple (x1,x2,x3,x4) in [0..1], it
# overrides auto-detection — useful when the central swirl has thin curls
# that fall below the ink threshold.  [x2..x3] is the preserved center
# ornament; [0..x1] and [x4..1] are preserved corners; [x1..x2] and
# [x3..x4] are the stretchable rail zones.
# MAMAR_BOX_CAP_INK_THRESH lowers/raises the column-ink detection floor.
MAMAR_BOX_CAP_SPLIT_FRACS = (0.11, 0.26, 0.74, 0.89)  # newbox: hand-tuned for swirl
MAMAR_BOX_CAP_INK_THRESH  = 8           # auto-detect floor (lower = thinner curls counted)

# ── Source ink darkening (pre-halo) ──
# Remaps source pixels darker than INK_DARKEN_THRESHOLD down to near-black,
# turning soft gray strokes into a crisp "line" before the white cushion +
# halo are painted around them.  Set to None to disable.
MAMAR_BOX_INK_DARKEN_THRESHOLD = 210    # any pixel grayer than this → darkened
MAMAR_BOX_INK_DARKEN_TARGET    = 60     # darkened pixels mapped toward this value

# ── Halo / post-processing pipeline (mirrors post-anaf ornament look) ──
# When enabled, the assembled box image goes through:
#   1. render core outline
#   2. dilate slightly + fill white for the visible frame line
#   3. dilate outward again to create a gray ring outside the white
#   4. blur only that gray ring so its outer edges fade slightly
MAMAR_BOX_HALO            = True        # apply halo pipeline (now renders gray frame lines)
MAMAR_BOX_HALO_NEAR_DIL   = 1           # dilation for frame line thickness
MAMAR_BOX_HALO_RING_DIL   = 2           # (unused in gray mode, kept for compat)
MAMAR_BOX_HALO_FAR_BLUR   = 2.2         # (unused in gray mode, kept for compat)
MAMAR_BOX_HALO_WHITE_VAL  = 252         # (unused in gray mode, kept for compat)
MAMAR_BOX_HALO_GRAY_VAL   = 50          # (unused in gray mode, kept for compat)
MAMAR_BOX_HALO_FADE_STRENGTH = 0.85     # (unused in gray mode, kept for compat)

# ── Interior fill ──
# 'white'       → flood interior to pure white (current behaviour)
# 'transparent' → leave interior as PNG transparent (page bg shows through)
MAMAR_BOX_FILL            = 'white'

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

# Anaf-label flanker height (pt) — small curl+faded-line ornament (owner 2026-06-14)
ANAF_FLANKER_H = 8.0
