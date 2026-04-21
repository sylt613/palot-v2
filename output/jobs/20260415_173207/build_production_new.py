#!/usr/bin/env python3
"""
build_production_new.py — Palaot HaOtiyot typesetting engine.

Reads doc_parts.json (from doc_parser.py) + settings.py → produces a professional
two-column RTL Hebrew PDF.

Key improvements over build_palaot.py:
  - All constants from settings.py (no hardcoded values)
  - Uses doc_parts.json types: sefer_title, heading1, heading2, body
  - Fixed font paths (FrankRuhl Pro Regular via settings.py)
  - Fixed column balancing algorithm
  - Fixed H2 keep-with-next: subheads never orphaned at end of column
  - Ornamental footnote separator (~2/3 page width)
  - Bold footnote reference numbers
  - Proper margins (0.75in top/bottom, 0.5in left/right)
"""

import os, re, sys, math, tempfile, subprocess, unicodedata, io, json, statistics, zipfile, copy
from collections import defaultdict, deque
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.pdfgen.textobject import PDFTextObject
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont, TTFontFile
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.lib.rl_accel import fp_str
from reportlab.graphics import renderPDF
from bidi.algorithm import get_display
from svglib.svglib import svg2rlg
from PIL import Image, ImageOps, ImageChops, ImageFilter
import uharfbuzz as hb
from fontTools.ttLib import TTFont as FTFont

import settings as S

# Override output paths so the fixing-build never clobbers the main output/
_FIXING_BUILD_DIR = os.path.dirname(os.path.abspath(__file__))
S.OUT_DIR = os.path.join(_FIXING_BUILD_DIR, "output")
S.OUT_PDF = os.path.join(S.OUT_DIR, "palaot.pdf")

# ═══════════════════════════════════════════════════════════════════════════
# HARFBUZZ TJ INJECTION — correct combining mark positioning
# ═══════════════════════════════════════════════════════════════════════════

_hb_font_cache = {}
_font_ttf_paths = {}
_reverse_cmap_cache = {}

def _get_hb_font(ttf_path):
    if ttf_path not in _hb_font_cache:
        blob = hb.Blob.from_file_path(ttf_path)
        face = hb.Face(blob)
        font = hb.Font(face)
        font.scale = (face.upem, face.upem)
        _hb_font_cache[ttf_path] = (font, face)
    return _hb_font_cache[ttf_path]

def _get_reverse_cmap(ttf_path):
    if ttf_path not in _reverse_cmap_cache:
        ft = FTFont(ttf_path)
        cmap_table = ft.getBestCmap()
        glyph_order = ft.getGlyphOrder()
        name_to_gid = {name: i for i, name in enumerate(glyph_order)}
        rev = {}
        for ucp, gname in cmap_table.items():
            gid = name_to_gid.get(gname)
            if gid is not None:
                rev[gid] = ucp
        ft.close()
        _reverse_cmap_cache[ttf_path] = rev
    return _reverse_cmap_cache[ttf_path]

# ── Auto-extracted GPOS fallback offset cache ──
_auto_fallback_cache = {}

_HEBREW_FINAL_TO_NORMAL = {
    0x05DA: 0x05DB, 0x05DD: 0x05DE, 0x05DF: 0x05E0,
    0x05E3: 0x05E4, 0x05E5: 0x05E6,
}

def _extract_gpos_fallbacks(ttf_path):
    """Auto-extract GPOS anchor data for fallback mark positioning."""
    if ttf_path in _auto_fallback_cache:
        return _auto_fallback_cache[ttf_path]
    fallbacks = {}
    try:
        ft = FTFont(ttf_path)
    except Exception:
        _auto_fallback_cache[ttf_path] = fallbacks
        return fallbacks
    if "GPOS" not in ft:
        ft.close()
        _auto_fallback_cache[ttf_path] = fallbacks
        return fallbacks

    glyph_order = ft.getGlyphOrder()
    name_to_gid = {name: i for i, name in enumerate(glyph_order)}
    cmap = ft.getBestCmap() or {}
    gname_to_cp = {}
    for ucp, gname in cmap.items():
        gname_to_cp[gname] = ucp

    gpos = ft["GPOS"].table
    all_mark_class_members = {}
    all_base_anchors = {}
    all_lig_anchors = {}

    for li, lookup in enumerate(gpos.LookupList.Lookup):
        ltype = lookup.LookupType
        subtables = lookup.SubTable
        if ltype == 9:
            if not subtables: continue
            actual_type = subtables[0].ExtensionLookupType
            subtables = [s.ExtSubTable for s in subtables]
        else:
            actual_type = ltype

        if actual_type == 4:  # MarkBasePos
            mark_class_members = {}
            base_anchors = {}
            for sub in subtables:
                if not hasattr(sub, 'BaseCoverage') or sub.Format != 1: continue
                base_glyphs = sub.BaseCoverage.glyphs
                mark_glyphs = sub.MarkCoverage.glyphs
                mark_array = sub.MarkArray
                base_array = sub.BaseArray
                for mi, mark_rec in enumerate(mark_array.MarkRecord):
                    cls = mark_rec.Class
                    if cls not in mark_class_members: mark_class_members[cls] = []
                    mark_gname = mark_glyphs[mi]
                    mark_x = getattr(mark_rec.MarkAnchor, 'XCoordinate', 0) if mark_rec.MarkAnchor else 0
                    mark_class_members[cls].append((mark_gname, mark_x))
                for bi, base_rec in enumerate(base_array.BaseRecord):
                    base_gname = base_glyphs[bi]
                    if base_gname not in base_anchors: base_anchors[base_gname] = {}
                    for cls_idx, base_anchor in enumerate(base_rec.BaseAnchor):
                        if base_anchor is not None:
                            base_anchors[base_gname][cls_idx] = getattr(base_anchor, 'XCoordinate', 0)
            all_mark_class_members[li] = mark_class_members
            all_base_anchors[li] = base_anchors

        elif actual_type == 5:  # MarkLigPos
            for sub in subtables:
                if not hasattr(sub, 'LigatureCoverage') or sub.Format != 1: continue
                lig_glyphs = sub.LigatureCoverage.glyphs
                lig_array = sub.LigatureArray
                for lgi, lig_attach in enumerate(lig_array.LigatureAttach):
                    lig_gname = lig_glyphs[lgi]
                    for comp_idx, comp_rec in enumerate(lig_attach.ComponentRecord):
                        for cls_idx, lig_anchor in enumerate(comp_rec.LigatureAnchor):
                            if lig_anchor is not None:
                                if lig_gname not in all_lig_anchors: all_lig_anchors[lig_gname] = {}
                                all_lig_anchors[lig_gname][cls_idx] = getattr(lig_anchor, 'XCoordinate', 0)

    # Step 2: Find missing mark classes on final-form letters
    base_coverage = defaultdict(set)
    for li, ba in all_base_anchors.items():
        for base_gname, cls_dict in ba.items():
            for cls_idx in cls_dict:
                base_coverage[base_gname].add((li, cls_idx))

    for final_cp, normal_cp in _HEBREW_FINAL_TO_NORMAL.items():
        final_gname = cmap.get(final_cp)
        normal_gname = cmap.get(normal_cp)
        if not final_gname or not normal_gname: continue
        normal_entries = base_coverage.get(normal_gname, set())
        final_entries = base_coverage.get(final_gname, set())
        normal_by_lookup = defaultdict(set)
        for li, cls in normal_entries: normal_by_lookup[li].add(cls)
        final_by_lookup = defaultdict(set)
        for li, cls in final_entries: final_by_lookup[li].add(cls)
        for li, normal_classes in normal_by_lookup.items():
            missing = normal_classes - final_by_lookup.get(li, set())
            if not missing: continue
            mcm = all_mark_class_members.get(li, {})
            na = all_base_anchors.get(li, {}).get(normal_gname, {})
            for cls_idx in missing:
                base_x = na.get(cls_idx, 0)
                for mark_gname, mark_x in mcm.get(cls_idx, []):
                    mark_cp = gname_to_cp.get(mark_gname)
                    if mark_cp is None: continue
                    key = (final_cp, mark_cp)
                    if key not in fallbacks:
                        fallbacks[key] = base_x - mark_x

    # Step 3: Handle ccmp composite glyphs
    if "GSUB" in ft:
        gsub = ft["GSUB"].table
        ccmp_lookups = set()
        for feat_rec in gsub.FeatureList.FeatureRecord:
            if feat_rec.FeatureTag == "ccmp":
                ccmp_lookups.update(feat_rec.Feature.LookupListIndex)
        composites = {}
        for li in ccmp_lookups:
            if li >= len(gsub.LookupList.Lookup): continue
            lookup = gsub.LookupList.Lookup[li]
            ltype = lookup.LookupType
            subtables = lookup.SubTable
            if ltype == 7:
                if not subtables: continue
                actual_type = subtables[0].ExtensionLookupType
                subtables = [s.ExtSubTable for s in subtables]
            else:
                actual_type = ltype
            if actual_type == 4:
                for sub in subtables:
                    if not hasattr(sub, 'ligatures'): continue
                    for first_glyph, lig_set in sub.ligatures.items():
                        for lig in lig_set:
                            components = [first_glyph] + list(lig.Component)
                            composite_gname = lig.LigGlyph
                            base_g, mark_gs = None, []
                            for comp_g in components:
                                comp_cp = gname_to_cp.get(comp_g)
                                if comp_cp and unicodedata.category(chr(comp_cp)) == 'Mn':
                                    mark_gs.append(comp_g)
                                elif base_g is None:
                                    base_g = comp_g
                            if base_g and mark_gs:
                                composites[composite_gname] = (base_g, mark_gs)

        for comp_gname, (base_gname, consumed_marks) in composites.items():
            base_cp = gname_to_cp.get(base_gname)
            if base_cp is None: continue
            consumed_mark_cps = set()
            for mg in consumed_marks:
                mcp = gname_to_cp.get(mg)
                if mcp: consumed_mark_cps.add(mcp)
            for li, ba in all_base_anchors.items():
                comp_anchors = ba.get(comp_gname, {})
                mcm = all_mark_class_members.get(li, {})
                for cls_idx, comp_base_x in comp_anchors.items():
                    for mark_gname, mark_x in mcm.get(cls_idx, []):
                        mark_cp = gname_to_cp.get(mark_gname)
                        if mark_cp and mark_cp in consumed_mark_cps:
                            key = (base_cp, mark_cp)
                            if key not in fallbacks:
                                fallbacks[key] = comp_base_x - mark_x
            comp_lig_anchors = all_lig_anchors.get(comp_gname, {})
            for li, mcm in all_mark_class_members.items():
                for cls_idx, comp_base_x in comp_lig_anchors.items():
                    for mark_gname, mark_x in mcm.get(cls_idx, []):
                        mark_cp = gname_to_cp.get(mark_gname)
                        if mark_cp and mark_cp in consumed_mark_cps:
                            key = (base_cp, mark_cp)
                            if key not in fallbacks:
                                fallbacks[key] = comp_base_x - mark_x

    ft.close()
    _auto_fallback_cache[ttf_path] = fallbacks
    return fallbacks


def _has_combining_marks(text):
    for ch in text:
        if unicodedata.category(ch) == 'Mn':
            return True
    return False

def _escape_pdf_bytes(raw_bytes):
    result = []
    for b in raw_bytes:
        if b == 0x28:    result.append('\\(')
        elif b == 0x29:  result.append('\\)')
        elif b == 0x5C:  result.append('\\\\')
        else:            result.append('\\%03o' % b)
    return ''.join(result)

# ── The monkey-patch ──
_orig_formatText = PDFTextObject._formatText

def _hb_formatText(self, text):
    """Emit TJ arrays with HarfBuzz-computed mark positioning for Hebrew."""
    canv = self._canvas
    font = pdfmetrics.getFont(self._fontname)
    if not font._dynamicFont or not _has_combining_marks(text):
        return _orig_formatText(self, text)
    ttf_path = _font_ttf_paths.get(self._fontname)
    if not ttf_path:
        return _orig_formatText(self, text)

    vis_text = text
    original_text = vis_text[::-1]

    hb_font, face = _get_hb_font(ttf_path)
    upem = face.upem

    # Pass 1: ccmp OFF
    buf1 = hb.Buffer()
    buf1.add_str(original_text)
    buf1.direction = "rtl"
    buf1.script = "Hebr"
    buf1.language = "he"
    hb.shape(hb_font, buf1, {"liga": False, "ccmp": False, "calt": False,
                              "mark": True, "mkmk": True, "kern": True})
    hb_info1 = buf1.glyph_infos
    hb_pos1 = buf1.glyph_positions
    if len(hb_pos1) != len(vis_text):
        return _orig_formatText(self, text)

    # Pass 2: ccmp ON
    buf2 = hb.Buffer()
    buf2.add_str(original_text)
    buf2.direction = "rtl"
    buf2.script = "Hebr"
    buf2.language = "he"
    hb.shape(hb_font, buf2, {"liga": False, "ccmp": True, "calt": False,
                              "mark": True, "mkmk": True, "kern": True})
    _pass2_offsets = {}
    for inf, pos in zip(buf2.glyph_infos, buf2.glyph_positions):
        _pass2_offsets[(inf.cluster, inf.codepoint)] = pos.x_offset

    _FALLBACK_OFFSETS = _extract_gpos_fallbacks(ttf_path)
    _cluster_base = {}
    for ci, ch in enumerate(original_text):
        if unicodedata.category(ch) != 'Mn':
            _cluster_base[ci] = ord(ch)
    _rev_cmap = _get_reverse_cmap(ttf_path)

    _hb_entries = []
    for i in range(len(hb_pos1)):
        key = (hb_info1[i].cluster, hb_info1[i].codepoint)
        x_off = _pass2_offsets[key] if key in _pass2_offsets else hb_pos1[i].x_offset
        gid = hb_info1[i].codepoint
        ucp = _rev_cmap.get(gid, 0)
        if x_off == 0 and ucp and unicodedata.category(chr(ucp)) == 'Mn':
            base_cp = _cluster_base.get(hb_info1[i].cluster)
            if base_cp is not None:
                fallback = _FALLBACK_OFFSETS.get((base_cp, ucp))
                if fallback is not None:
                    x_off = fallback
        _hb_entries.append((hb_info1[i].cluster, ucp, x_off, hb_pos1[i].x_advance))

    _hb_by_key = {}
    for cluster, ucp, x_off, x_adv in _hb_entries:
        _hb_by_key.setdefault((cluster, ucp), []).append((x_off, x_adv))

    hb_positions = []
    _consumed = {}
    for i in range(len(vis_text)):
        ch_cp = ord(vis_text[i])
        orig_idx = len(vis_text) - 1 - i
        target_cluster = 0
        for ci in sorted(_cluster_base.keys()):
            if ci <= orig_idx:
                target_cluster = ci
        lookup_key = (target_cluster, ch_cp)
        entries = _hb_by_key.get(lookup_key, [])
        idx = _consumed.get(lookup_key, 0)
        if idx < len(entries):
            hb_positions.append(entries[idx])
            _consumed[lookup_key] = idx + 1
        else:
            hb_positions.append((_hb_entries[i][2], _hb_entries[i][3]))

    chunks = font.splitString(vis_text, canv._doc)
    R = []
    char_idx = 0
    for subset_idx, raw_bytes in chunks:
        if subset_idx != self._curSubset:
            pdffontname = font.getSubsetInternalName(subset_idx, canv._doc)
            R.append("%s %s Tf %s TL" % (pdffontname, fp_str(self._fontsize), fp_str(self._leading)))
            self._curSubset = subset_idx
        tj_parts = []
        for byte_val in raw_bytes:
            x_off, x_adv = hb_positions[char_idx]
            ch = vis_text[char_idx]
            rl_w_1000 = font.face.getCharWidth(ord(ch))
            x_off_1000 = x_off * 1000.0 / upem
            x_adv_1000 = x_adv * 1000.0 / upem
            if abs(x_off_1000) > 0.5:
                tj_parts.append('%.1f' % (-x_off_1000))
            escaped = _escape_pdf_bytes(bytes([byte_val]))
            tj_parts.append('(%s)' % escaped)
            post_disp = x_off_1000 + (rl_w_1000 - x_adv_1000)
            if abs(post_disp) > 0.5:
                tj_parts.append('%.1f' % post_disp)
            char_idx += 1
        R.append("[%s] TJ" % ' '.join(tj_parts))
    return ' '.join(R)

PDFTextObject._formatText = _hb_formatText

# ═══════════════════════════════════════════════════════════════════════════
# FONT LOADING
# ═══════════════════════════════════════════════════════════════════════════
_OTF_CACHE_DIR = os.path.join(tempfile.gettempdir(), "rl_font_cache")

def _convert_otf_to_ttf(otf_path):
    from fontTools.pens.cu2quPen import Cu2QuPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib.tables._g_l_y_f import table__g_l_y_f
    from fontTools.ttLib.tables._l_o_c_a import table__l_o_c_a
    base = os.path.splitext(os.path.basename(otf_path))[0]
    os.makedirs(_OTF_CACHE_DIR, exist_ok=True)
    otf_mtime = int(os.path.getmtime(otf_path))
    cache_name = f"{base}_{otf_mtime}.ttf"
    cached_path = os.path.join(_OTF_CACHE_DIR, cache_name)
    if os.path.exists(cached_path):
        return cached_path
    font = FTFont(otf_path)
    if "CFF " not in font:
        font.close()
        return otf_path
    glyph_order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    glyf_table = table__g_l_y_f()
    glyf_table.glyphs = {}
    glyf_table.glyphOrder = glyph_order
    for gn in glyph_order:
        tt_pen = TTGlyphPen(glyphSet=glyph_set)
        cu2qu_pen = Cu2QuPen(tt_pen, max_err=1.0, reverse_direction=True)
        try:
            glyph_set[gn].draw(cu2qu_pen)
            glyf_table.glyphs[gn] = tt_pen.glyph()
        except Exception:
            empty = TTGlyphPen(glyphSet=None)
            glyf_table.glyphs[gn] = empty.glyph()
    font["glyf"] = glyf_table
    font["loca"] = table__l_o_c_a()
    del font["CFF "]
    font.sfntVersion = "\x00\x01\x00\x00"
    maxp = font["maxp"]
    maxp.tableVersion = 0x00010000
    for attr in ('maxZones','maxTwilightPoints','maxStorage',
                 'maxFunctionDefs','maxInstructionDefs',
                 'maxStackElements','maxSizeOfInstructions'):
        if not hasattr(maxp, attr):
            setattr(maxp, attr, 2 if attr == 'maxZones' else 0)
    maxp.maxComponentElements = max(
        (1 for g in glyf_table.glyphs.values()
         if hasattr(g, "isComposite") and g.isComposite()), default=0)
    maxp.maxComponentDepth = 1
    font.save(cached_path)
    font.close()
    return cached_path

def _fix_otto_header(font_path):
    """Fix fonts that have glyf outlines but OTTO sfnt header (confuses ReportLab)."""
    os.makedirs(_OTF_CACHE_DIR, exist_ok=True)
    base = os.path.splitext(os.path.basename(font_path))[0]
    mtime = int(os.path.getmtime(font_path))
    cached = os.path.join(_OTF_CACHE_DIR, f"{base}_fixed_{mtime}.ttf")
    if os.path.exists(cached):
        return cached
    font = FTFont(font_path)
    if font.sfntVersion == "OTTO" and "glyf" in font and "CFF " not in font:
        font.sfntVersion = "\x00\x01\x00\x00"
        # Fix maxp table — need version 1.0 with all required fields
        maxp = font["maxp"]
        if maxp.tableVersion != 0x00010000:
            maxp.tableVersion = 0x00010000
            # Compute actual max values from glyf
            glyf = font["glyf"]
            max_pts = max_ctrs = max_comp_pts = max_comp_ctrs = 0
            for gn in font.getGlyphOrder():
                g = glyf[gn]
                if g.numberOfContours > 0:
                    npts = (max(g.endPtsOfContours) + 1) if g.endPtsOfContours else 0
                    max_pts = max(max_pts, npts)
                    max_ctrs = max(max_ctrs, g.numberOfContours)
                elif g.numberOfContours == -1:
                    max_comp_pts = max(max_comp_pts, 100)
                    max_comp_ctrs = max(max_comp_ctrs, 10)
            defaults = {
                'maxZones': 2, 'maxTwilightPoints': 0, 'maxStorage': 0,
                'maxFunctionDefs': 0, 'maxInstructionDefs': 0,
                'maxStackElements': 0, 'maxSizeOfInstructions': 0,
                'maxComponentElements': 0, 'maxComponentDepth': 0,
                'maxPoints': max_pts, 'maxContours': max_ctrs,
                'maxCompositePoints': max_comp_pts,
                'maxCompositeContours': max_comp_ctrs,
            }
            for attr, val in defaults.items():
                if not hasattr(maxp, attr):
                    setattr(maxp, attr, val)
        font.save(cached)
        font.close()
        return cached
    font.close()
    return font_path

def load_font(rl_name, font_path):
    ext = os.path.splitext(font_path)[1].lower()
    needs_convert = (ext == '.otf')
    if not needs_convert:
        # Check if it's a CFF font or has wrong OTTO header
        try:
            probe = FTFont(font_path)
            if "CFF " in probe:
                needs_convert = True
            elif probe.sfntVersion == "OTTO" and "glyf" in probe:
                # Has TrueType outlines but wrong header — just fix header
                probe.close()
                font_path = _fix_otto_header(font_path)
                pdfmetrics.registerFont(TTFont(rl_name, font_path))
                _font_ttf_paths[rl_name] = font_path
                return font_path
            probe.close()
        except Exception:
            pass
    if needs_convert:
        ttf_path = _convert_otf_to_ttf(font_path)
        font_path = ttf_path
    pdfmetrics.registerFont(TTFont(rl_name, font_path))
    _font_ttf_paths[rl_name] = font_path
    return font_path

# ── Load fonts from settings ──
print("Loading fonts...")
load_font(S.BODY_FONT_NAME,        S.BODY_FONT_PATH)
load_font(S.DROPCAP_FONT_NAME,     S.DROPCAP_FONT_PATH)
load_font(S.HEADING_FONT_REG_NAME, S.HEADING_FONT_REG_PATH)
load_font(S.HEADING_FONT_NAME,     S.HEADING_FONT_PATH)
if os.path.exists(S.ORNAMENT_FONT_PATH):
    load_font(S.ORNAMENT_FONT_NAME,    S.ORNAMENT_FONT_PATH)
else:
    print(f"Skipping missing ornament font: {S.ORNAMENT_FONT_PATH}")
load_font(S.BOLD_BODY_FONT_NAME,   S.BOLD_BODY_FONT_PATH)
load_font(S.EFT_TALPIYOT_FONT_NAME, S.EFT_TALPIYOT_FONT_PATH)
UI_SYMBOL_FONT = S.BODY_FONT_NAME
for _symbol_font_path in (
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf'):
    if os.path.exists(_symbol_font_path):
        load_font('uisymbols', _symbol_font_path)
        UI_SYMBOL_FONT = 'uisymbols'
        break

_ORN_DRAWING_CACHE = {}
_SVG_FILE_CACHE = {}
_PNG_READER_CACHE = {}

_ORNAMENT_DIR = os.path.join(os.path.dirname(__file__), 'ornaments')
_PNG_DIVIDER_POST_ANAF = os.path.join(_ORNAMENT_DIR, 'divider_post_anaf.png')
_PNG_DIVIDER_TOC = os.path.join(_ORNAMENT_DIR, 'divider_toc.png')
_PNG_FOOTNOTE_SEPARATOR = os.path.join(_ORNAMENT_DIR, 'footnote_separator.png')
_PNG_MAMAR_FRAME = os.path.join(_ORNAMENT_DIR, 'mamar_title_frame.png')
_PNG_TOC_MAMAR_FRAME = os.path.join(_ORNAMENT_DIR, 'toc_mamar_frame.png')
_PNG_ANAF_FLANKER = os.path.join(_ORNAMENT_DIR, 'anaf_flanker.png')
_PNG_TOC_ENTRY_ACCENT = os.path.join(_ORNAMENT_DIR, 'toc_entry_accent.png')
_PNG_SUBHEAD_RULE_RIGHT = os.path.join(_ORNAMENT_DIR, 'subhead_rule_right.png')
_PNG_TITLE_RULE_RIGHT   = os.path.join(_ORNAMENT_DIR, 'title_rule_right.png')
_PNG_HEADER_LEADER_RIGHT = os.path.join(_ORNAMENT_DIR, 'header_leader_right.png')
_PNG_NANO_GUIDED_SHEET = os.path.join(_ORNAMENT_DIR, 'nano_banana2_guided_output.png')
_PNG_FRAME_MASTER     = os.path.join(_ORNAMENT_DIR, 'frame_master.png')

_NANO_CROP_CACHE = {}
_FRAME_CACHE = {}  # keyed by (target_w_px, target_h_px, intensity)

# ── Gray halo constants (applied at output resolution) ──────────────────
_HALO_RADIUS   = 6     # Gaussian blur radius in output pixels
_HALO_DARKEST  = 120   # darkest gray value in halo (0=black, 255=white)
_INK_THRESHOLD = 180   # pixels darker than this are "ink"

def _apply_gray_halo(canvas):
    """Add a soft gray outline around all dark (ink) pixels in canvas.

    Works at whatever resolution canvas is.  Steps:
      1. Save original grayscale (to know which pixels were white).
      2. Build binary ink mask (pixels < _INK_THRESHOLD).
      3. Gaussian-blur the mask → smooth falloff gradient.
      4. Where blurred mask > 0 AND pixel is NOT original ink AND pixel
         is near-white → blend toward _HALO_DARKEST proportionally.
      5. Restore any pixel that was originally at or above the
         white-mask threshold (254) — these are outer-margin pixels
         that must stay transparent in the PDF.
    """
    gray = canvas.convert("L")
    w, h = gray.size
    gpix = gray.load()

    # 1. ink mask
    ink = Image.new("L", (w, h), 0)
    ipix = ink.load()
    for y in range(h):
        for x in range(w):
            if gpix[x, y] < _INK_THRESHOLD:
                ipix[x, y] = 255

    # 2. blur
    blurred = ink.filter(ImageFilter.GaussianBlur(radius=_HALO_RADIUS))
    bpix = blurred.load()

    # 3. composite halo — only where original was NOT at mask threshold
    _MASK_THRESH = 254  # pixels >= this get masked transparent by draw_frame_box
    cpix = canvas.load()
    for y in range(h):
        for x in range(w):
            bv = bpix[x, y]
            if bv > 10 and ipix[x, y] == 0:
                r, g, b = cpix[x, y]
                if r > 200 and g > 200 and b > 200 and gpix[x, y] < _MASK_THRESH:
                    t = bv / 255.0
                    nv = int(255 - t * (255 - _HALO_DARKEST))
                    cpix[x, y] = (nv, nv, nv)
    return canvas

def _build_frame_image(target_w_px, target_h_px, intensity=1.0):
    """Build a frame of exactly (target_w_px x target_h_px).

    Strategy: crop generous corner rectangles that include the FULL scroll
    ornament plus the overlap zone where rules meet the curl.  Edge bands
    are TILED using a 1-pixel-wide/tall slice that covers the SAME source
    rows/cols as the corner crop, scaled to the SAME output size.  This
    guarantees pixel-perfect seamless joins — the rule lines in the tiled
    band are at exactly the same pixel positions as in the corner.

    Source frame (986×645 after crop from frame_master.png).
    """
    cache_key = (target_w_px, target_h_px, round(intensity, 3))
    cached = _FRAME_CACHE.get(cache_key)
    if cached is not None:
        return cached

    if not os.path.exists(_PNG_FRAME_MASTER):
        return None
    try:
        src = Image.open(_PNG_FRAME_MASTER).convert("RGB")

        F_TOP, F_BOT, F_LEFT, F_RIGHT = 184, 828, 273, 1258
        frame = src.crop((F_LEFT, F_TOP, F_RIGHT + 1, F_BOT + 1))
        FW, FH = frame.size  # 986×645

        # ── Corner crop sizes in source pixels ──
        # Large enough to include full scroll ornament + rule overlap.
        CX      = 80     # horizontal extent of corner crop from each side
        CY_TOP  = 90     # vertical extent of top corners
        CY_BOT  = 100    # vertical extent of bottom corners

        # Side band widths in source
        L_W = 25
        R_W = 25

        # Tile slice source positions (well past corner ornaments)
        TILE_COL = 200
        TILE_ROW = 322

        # ── UNIFORM scale factor ──
        # One scale for corners AND bands — preserves aspect ratio (no
        # curl distortion) and ensures seamless joins.
        s = min(target_w_px / FW, target_h_px / FH)

        # Enforce minimum so scroll curls are large enough to see.
        s_min = max(75 / CX, 80 / max(CY_TOP, CY_BOT))
        s_max = min(target_w_px * 0.4 / CX, target_h_px * 0.4 / max(CY_TOP, CY_BOT))
        s = max(s, s_min)
        s = min(s, s_max)

        # Corner output sizes (uniform scale, preserves aspect ratio)
        c_w   = max(1, round(CX     * s))
        c_h_t = max(1, round(CY_TOP * s))
        c_h_b = max(1, round(CY_BOT * s))

        # Side band output widths — same scale as corner
        l_w = max(3, round(L_W * s))
        r_w = max(3, round(R_W * s))

        def _resize(im, w, h):
            return im.resize((max(1, w), max(1, h)), Image.LANCZOS)

        def _tile_h(slice_1px, span_w, band_h):
            """Tile a 1px-wide column slice horizontally to fill span_w × band_h."""
            col = slice_1px.resize((1, band_h), Image.LANCZOS)
            strip = Image.new("RGB", (span_w, band_h), (255, 255, 255))
            for x in range(span_w):
                strip.paste(col, (x, 0))
            return strip

        def _tile_v(slice_1px, band_w, span_h):
            """Tile a 1px-tall row slice vertically to fill band_w × span_h."""
            row = slice_1px.resize((band_w, 1), Image.LANCZOS)
            strip = Image.new("RGB", (band_w, span_h), (255, 255, 255))
            for y in range(span_h):
                strip.paste(row, (0, y))
            return strip

        # ── Crop corners from source ──
        tl = _resize(frame.crop((0,       0,          CX, CY_TOP)),          c_w, c_h_t)
        tr = _resize(frame.crop((FW-CX,   0,          FW, CY_TOP)),          c_w, c_h_t)
        bl = _resize(frame.crop((0,       FH-CY_BOT,  CX, FH)),             c_w, c_h_b)
        br = _resize(frame.crop((FW-CX,   FH-CY_BOT,  FW, FH)),            c_w, c_h_b)

        # ── 1px tile slices ──
        # CRITICAL: slices cover the SAME source row/col range as the
        # corners, and are scaled to the SAME output height/width.
        # This guarantees rule lines land at identical pixel positions.
        #
        # Top band: 1px column at TILE_COL, rows 0..CY_TOP → scaled to 1 × c_h_t
        top_slice = frame.crop((TILE_COL, 0,        TILE_COL + 1, CY_TOP))
        # Bottom band: 1px column at TILE_COL, rows FH-CY_BOT..FH → scaled to 1 × c_h_b
        bot_slice = frame.crop((TILE_COL, FH-CY_BOT, TILE_COL + 1, FH))
        # Left band: 1px row at TILE_ROW, cols 0..L_W → scaled to l_w × 1
        lft_slice = frame.crop((0,      TILE_ROW, L_W,  TILE_ROW + 1))
        # Right band: 1px row at TILE_ROW, cols FW-R_W..FW → scaled to r_w × 1
        rgt_slice = frame.crop((FW-R_W, TILE_ROW, FW,   TILE_ROW + 1))

        # ── Assemble on white canvas ──
        canvas = Image.new("RGB", (target_w_px, target_h_px), (255, 255, 255))

        # Horizontal span between left and right corners
        h_span = target_w_px - 2 * c_w

        # Top edge band: tiled, same height as top corners (c_h_t)
        if h_span > 0:
            canvas.paste(_tile_h(top_slice, h_span, c_h_t), (c_w, 0))

        # Bottom edge band: tiled, same height as bottom corners (c_h_b)
        if h_span > 0:
            canvas.paste(_tile_h(bot_slice, h_span, c_h_b), (c_w, target_h_px - c_h_b))

        # Side bands (tiled between top and bottom corners)
        side_top    = c_h_t
        side_bot    = target_h_px - c_h_b
        side_span_h = side_bot - side_top
        if side_span_h > 0:
            canvas.paste(_tile_v(lft_slice, l_w, side_span_h), (0, side_top))
            canvas.paste(_tile_v(rgt_slice, r_w, side_span_h), (target_w_px - r_w, side_top))

        # Corners drawn last (on top of edge bands for clean overlap)
        canvas.paste(tl, (0,                 0))
        canvas.paste(tr, (target_w_px - c_w, 0))
        canvas.paste(bl, (0,                 target_h_px - c_h_b))
        canvas.paste(br, (target_w_px - c_w, target_h_px - c_h_b))

        # Gray halo outline around all black lines (applied at output resolution
        # so halo width is consistent regardless of frame scale)
        canvas = _apply_gray_halo(canvas)

        if intensity < 1.0:
            canvas = _lighten_pil_image(canvas.convert("RGBA"), intensity).convert("RGB")

        _FRAME_CACHE[cache_key] = canvas
        return canvas
    except Exception as e:
        return None


def draw_frame_box(c, x_left, y_bot, width_pt, height_pt, intensity=1.0):
    """Draw the baroque scroll frame at any size using the tiled frame builder.
    x_left, y_bot are PDF coordinates (bottom-left of frame). Returns True on success."""
    ppi = 300
    w_px = max(8, round(width_pt  * ppi / 72))
    h_px = max(8, round(height_pt * ppi / 72))
    img = _build_frame_image(w_px, h_px, intensity=intensity)
    if img is None:
        return False
    reader = ImageReader(img)
    c.saveState()
    c.drawImage(reader, x_left, y_bot, width=width_pt, height=height_pt,
                mask=[254, 255, 254, 255, 254, 255])
    c.restoreState()
    return True

def _light_to_alpha(img, cutoff=245):
    rgba = img.convert('RGBA')
    data = []
    for r, g, b, a in rgba.getdata():
        if r >= cutoff and g >= cutoff and b >= cutoff:
            data.append((255, 255, 255, 0))
        else:
            data.append((r, g, b, a))
    rgba.putdata(data)
    return rgba

def _lighten_pil_image(img, frac):
    """Blend RGB channels of an RGBA image toward white by (1-frac).
    frac=0.8 → 80% intensity (slightly lighter). Alpha channel unchanged."""
    r, g, b, a = img.split()
    lut = [int(v * frac + 255 * (1 - frac)) for v in range(256)]
    r = r.point(lut); g = g.point(lut); b = b.point(lut)
    return Image.merge('RGBA', (r, g, b, a))

def _get_png_reader(png_path, darken=None):
    """Return a cached ImageReader for *png_path*.
    
    darken: if a float < 1.0, multiply every non-transparent pixel's RGB
            channels by that factor (e.g. 0.55 = 55 % of original brightness).
            Darkened variants are cached separately.
    """
    cache_key = (png_path, darken)
    reader = _PNG_READER_CACHE.get(cache_key)
    if reader is None:
        if not os.path.exists(png_path):
            return None
        try:
            if darken is not None and darken < 1.0:
                img = Image.open(png_path).convert('RGBA')
                r, g, b, a = img.split()
                import numpy as np
                arr = np.array(img, dtype=np.float32)
                arr[:, :, 0] *= darken
                arr[:, :, 1] *= darken
                arr[:, :, 2] *= darken
                arr = np.clip(arr, 0, 255).astype(np.uint8)
                img = Image.fromarray(arr, 'RGBA')
                import io
                buf = io.BytesIO()
                img.save(buf, format='PNG')
                buf.seek(0)
                reader = ImageReader(buf)
            else:
                reader = ImageReader(png_path)
        except Exception:
            return None
        _PNG_READER_CACHE[cache_key] = reader
    return reader

def draw_png_ornament(c, png_path, x_center, y_center, width=None, height=None,
                      preserve_aspect=True, mirror=False, flip_vertical=False, darken=None):
    reader = _get_png_reader(png_path, darken=darken)
    if reader is None:
        return False
    iw, ih = reader.getSize()
    if not width and not height:
        return False
    if preserve_aspect:
        if width is None:
            scale = height / ih
            width = iw * scale
        elif height is None:
            scale = width / iw
            height = ih * scale
        else:
            scale = min(width / iw, height / ih)
            width = iw * scale
            height = ih * scale
    else:
        if width is None:
            width = iw
        if height is None:
            height = ih

    c.saveState()
    if mirror:
        c.translate(x_center, 0)
        c.scale(-1, 1)
        x = -width / 2
    else:
        x = x_center - width / 2
    if flip_vertical:
        c.translate(0, y_center * 2)
        c.scale(1, -1)
    y = y_center - height / 2
    c.drawImage(reader, x, y, width=width, height=height, mask='auto')
    c.restoreState()
    return True

def draw_generated_divider(c, x_center, y_center, width, kind='chapter'):
    png_path = _PNG_DIVIDER_TOC if kind == 'toc' else _PNG_DIVIDER_POST_ANAF
    return draw_png_ornament(c, png_path, x_center, y_center, width=width, preserve_aspect=True)

_POST_ANAF_DIV_CACHE = {}  # keyed by (needed_w_px, rule_h_px, band_h_px)

def _build_plain_tiled_rule(needed_w_px, canvas_h_px, fade_zone_frac=0.18):
    """Build a plain horizontal rule (tile body only, no curl), faded at both ends.
    Returns an RGBA PIL Image, or None if source asset missing."""
    if not os.path.exists(_PNG_SUBHEAD_RULE_RIGHT):
        return None
    try:
        img = Image.open(_PNG_SUBHEAD_RULE_RIGHT).convert("RGBA")
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)
        tile, _flourish = _extract_flourish_terminal(img)
        if tile is None:
            return None
        tiled = _build_tiled_leader(tile, None, needed_w_px, canvas_h_px)
        fade_zone_px = min(max(16, int(needed_w_px * fade_zone_frac)), needed_w_px // 3)
        tiled = _fade_rgba_alpha(tiled, fade_side='left',  fade_zone_px=fade_zone_px)
        tiled = _fade_rgba_alpha(tiled, fade_side='right', fade_zone_px=fade_zone_px)
        return tiled
    except Exception:
        return None

def _build_symmetric_tiled_rule(needed_w_px, canvas_h_px):
    """Build a symmetric curl+line+curl rule from subhead_rule_right.png.
    Left half: image flipped horizontally (curl on left, line going right).
    Right half: original image (line going left, curl on right).
    Both halves share the tiled body; the curls face outward.
    Returns an RGBA PIL Image."""
    if not os.path.exists(_PNG_SUBHEAD_RULE_RIGHT):
        return None
    try:
        img = Image.open(_PNG_SUBHEAD_RULE_RIGHT).convert("RGBA")
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)
        tile, flourish = _extract_flourish_terminal(img)
        if tile is None:
            return None
        half_w = needed_w_px // 2
        # Right half: curl on right end
        right_half = _build_tiled_leader(tile, flourish, half_w, canvas_h_px)
        # Left half: flip the right half horizontally (curl now on left)
        left_half = right_half.transpose(Image.FLIP_LEFT_RIGHT)
        # Assemble: left_half | right_half
        canvas = Image.new("RGBA", (needed_w_px, canvas_h_px), (255, 255, 255, 0))
        canvas.alpha_composite(left_half, (0, 0))
        canvas.alpha_composite(right_half, (needed_w_px - half_w, 0))
        return canvas
    except Exception:
        return None

def _build_mamar_inner_band(needed_w_px, canvas_h_px):
    """Tile the inner diamond/scroll band from mamar_title_frame.png.
    Crops rows 18–94 (the scrollwork interior), skips corner pieces,
    tiles horizontally to fill needed_w_px, scales to canvas_h_px.
    Returns an RGBA PIL Image."""
    if not os.path.exists(_PNG_MAMAR_FRAME):
        return None
    try:
        img = Image.open(_PNG_MAMAR_FRAME).convert("RGBA")
        fw, fh = img.size
        # Inner scroll band: rows 18–94, skip corner curls (~120px each side)
        corner_skip = max(10, int(fw * 0.10))
        band = img.crop((corner_skip, 18, fw - corner_skip, 94))
        bw, bh = band.size
        # Scale to canvas_h_px
        scale = canvas_h_px / bh
        scaled_w = max(1, round(bw * scale))
        band = band.resize((scaled_w, canvas_h_px), Image.LANCZOS)
        # Tile horizontally
        canvas = Image.new("RGBA", (needed_w_px, canvas_h_px), (255, 255, 255, 0))
        for x in range(0, needed_w_px, scaled_w):
            paste_w = min(scaled_w, needed_w_px - x)
            canvas.alpha_composite(band.crop((0, 0, paste_w, canvas_h_px)), (x, 0))
        return canvas
    except Exception:
        return None

_INNER_BAND_FRAG_CACHE = {}  # keyed by (w_px, h_px)

def _get_inner_band_fragment_reader(width_pt, height_pt, ppi=300):
    """Return a cached ImageReader for a small centered fragment of the mamar inner band.
    Suitable for use as a compact horizontal ornament in headers/TOC (~30-50pt wide)."""
    w_px = max(1, round(width_pt * ppi / 72))
    h_px = max(1, round(height_pt * ppi / 72))
    cache_key = (w_px, h_px)
    reader = _INNER_BAND_FRAG_CACHE.get(cache_key)
    if reader is not None:
        return reader
    if not os.path.exists(_PNG_MAMAR_FRAME):
        return None
    try:
        img = Image.open(_PNG_MAMAR_FRAME).convert("RGBA")
        fw, fh = img.size
        corner_skip = max(10, int(fw * 0.10))
        # Crop the inner scroll band
        band = img.crop((corner_skip, 18, fw - corner_skip, 94))
        bw, bh = band.size
        # Take a centered slice of the band scaled to h_px tall
        scale = h_px / bh
        scaled_bw = max(1, round(bw * scale))
        band = band.resize((scaled_bw, h_px), Image.LANCZOS)
        # Take center w_px slice (or full width if narrower)
        if scaled_bw >= w_px:
            cx = scaled_bw // 2
            half = w_px // 2
            frag = band.crop((cx - half, 0, cx - half + w_px, h_px))
        else:
            frag = band
        # Fade both ends
        fade_zone_px = max(6, w_px // 4)
        frag = _fade_rgba_alpha(frag, fade_side='left',  fade_zone_px=fade_zone_px)
        frag = _fade_rgba_alpha(frag, fade_side='right', fade_zone_px=fade_zone_px)
        flat = Image.new("RGB", (w_px, h_px), (255, 255, 255))
        flat.paste(frag, mask=frag.split()[3])
        reader = ImageReader(flat)
        _INNER_BAND_FRAG_CACHE[cache_key] = reader
        return reader
    except Exception:
        return None

def draw_post_anaf_divider_band(c, x_center, y_top, width):
    """Draw the three-part post-anaf divider band:
      top:    symmetric curl+line+curl rule
      middle: tiled inner diamond/scroll band from mamar frame
      bottom: top rule flipped vertically
    y_top is the PDF y-coordinate of the top edge of the band.
    Returns total height drawn."""
    ppi = 300
    rule_h_pt = 11.0   # rendered height of each curl+line+curl rule
    band_h_pt = 14.0   # rendered height of the middle ornament strip
    gap_pt    = 1.5    # gap between rule and band
    total_h   = rule_h_pt * 2 + band_h_pt + gap_pt * 2

    cache_key = (round(width), round(rule_h_pt * 10), round(band_h_pt * 10))
    cached = _POST_ANAF_DIV_CACHE.get(cache_key)

    if cached is None:
        needed_w_px  = max(1, round(width * ppi / 72))
        rule_h_px    = max(4, round(rule_h_pt * ppi / 72))
        band_h_px    = max(4, round(band_h_pt * ppi / 72))

        rule_img  = _build_plain_tiled_rule(needed_w_px, rule_h_px)
        band_img  = _build_mamar_inner_band(needed_w_px, band_h_px)

        if rule_img is None:
            return False  # no assets, fall back to caller

        # Fade the band at both ends so it matches the rule fade
        if band_img is not None:
            fade_px = max(8, min(int(needed_w_px * 0.15), needed_w_px // 3))
            band_img = _fade_rgba_alpha(band_img, fade_side='left',  fade_zone_px=fade_px)
            band_img = _fade_rgba_alpha(band_img, fade_side='right', fade_zone_px=fade_px)

        # Flatten rule and band to RGB on white
        def _flat(img):
            out = Image.new("RGB", img.size, (255, 255, 255))
            out.paste(img, mask=img.split()[3])
            return out

        rule_flat     = _flat(rule_img)
        rule_flip_flat = _flat(rule_img.transpose(Image.FLIP_TOP_BOTTOM))
        band_flat     = _flat(band_img) if band_img else None

        cached = (ImageReader(rule_flat), ImageReader(rule_flip_flat),
                  ImageReader(band_flat) if band_flat else None)
        _POST_ANAF_DIV_CACHE[cache_key] = cached

    rule_reader, rule_flip_reader, band_reader = cached
    x_left = x_center - width / 2

    c.saveState()
    # Top rule
    c.drawImage(rule_reader, x_left, y_top - rule_h_pt,
                width=width, height=rule_h_pt, mask=[254, 255, 254, 255, 254, 255])
    # Middle band
    if band_reader:
        band_y = y_top - rule_h_pt - gap_pt - band_h_pt
        c.drawImage(band_reader, x_left, band_y,
                    width=width, height=band_h_pt, mask=[254, 255, 254, 255, 254, 255])
    # Bottom rule (flipped)
    bot_rule_y = y_top - rule_h_pt - gap_pt - band_h_pt - gap_pt - rule_h_pt
    c.drawImage(rule_flip_reader, x_left, bot_rule_y,
                width=width, height=rule_h_pt, mask=[254, 255, 254, 255, 254, 255])
    c.restoreState()
    return total_h

def draw_generated_footnote_separator(c, x_center, y_center, width):
    return draw_png_ornament(c, _PNG_FOOTNOTE_SEPARATOR, x_center, y_center, width=width, preserve_aspect=True)

def draw_generated_mamar_frame(c, x_center, y_center, width, height, intensity=1.0):
    """Draw the baroque scroll frame centred at (x_center, y_center)."""
    x_left = x_center - width / 2
    y_bot  = y_center - height / 2
    return draw_frame_box(c, x_left, y_bot, width, height, intensity=intensity)

def draw_generated_toc_mamar_frame(c, x_center, y_center, width, height, intensity=1.0):
    """Draw the baroque scroll frame for TOC mamar block, centred at (x_center, y_center)."""
    x_left = x_center - width / 2
    y_bot  = y_center - height / 2
    return draw_frame_box(c, x_left, y_bot, width, height, intensity=intensity)

_TITLE_RULE_CACHE = {}  # keyed by (needed_w_px, rule_h_px, flip_vertical)

def _get_tiled_title_rule_reader(needed_w_px, canvas_h_px, flip_vertical=False, intensity=1.0):
    """Build a tiled title rule: curl terminal on RIGHT end, faded plain rule on LEFT end.
    Source: title_rule_right.png (curl on right only — one direction).
    The left end is a continuation of the plain tiled rule body, faded to white.
    intensity: 1.0 = full black, 0.8 = 80% (lighter).
    Returns an ImageReader (RGB, white background) ready for drawImage."""
    cache_key = (needed_w_px, canvas_h_px, flip_vertical, round(intensity, 3))
    reader = _TITLE_RULE_CACHE.get(cache_key)
    if reader is not None:
        return reader
    src_path = _PNG_TITLE_RULE_RIGHT if os.path.exists(_PNG_TITLE_RULE_RIGHT) else _PNG_SUBHEAD_RULE_RIGHT
    if not os.path.exists(src_path):
        return None
    try:
        img = Image.open(src_path).convert("RGBA")
        if intensity < 1.0:
            img = _lighten_pil_image(img, intensity)
        tile, flourish = _extract_flourish_terminal(img)
        if tile is None:
            return None
        # Scale to canvas height
        if flourish.height != canvas_h_px:
            scale = canvas_h_px / flourish.height
            flourish = flourish.resize((max(1, round(flourish.width * scale)), canvas_h_px), Image.LANCZOS)
        fl_w = flourish.width
        if tile.height != canvas_h_px:
            scale = canvas_h_px / tile.height
            tile = tile.resize((max(1, round(tile.width * scale)), canvas_h_px), Image.LANCZOS)
        # Build canvas: tiled body for full width, then place curl on RIGHT only
        body_w = max(0, needed_w_px - fl_w)
        canvas = Image.new("RGBA", (needed_w_px, canvas_h_px), (255, 255, 255, 0))
        tile_w = tile.width
        for bx in range(0, body_w, tile_w):
            paste_w = min(tile_w, body_w - bx)
            canvas.alpha_composite(tile.crop((0, 0, paste_w, canvas_h_px)), (bx, 0))
        # Fade the left end of the body
        fade_zone_px = max(10, needed_w_px // 6)
        canvas = _fade_rgba_alpha(canvas, fade_side='left', fade_zone_px=fade_zone_px)
        # Place the curl terminal on the right end
        canvas.alpha_composite(flourish, (needed_w_px - fl_w, 0))
        if flip_vertical:
            canvas = canvas.transpose(Image.FLIP_TOP_BOTTOM)
        flat = Image.new("RGB", canvas.size, (255, 255, 255))
        flat.paste(canvas, mask=canvas.split()[3])
        reader = ImageReader(flat)
        _TITLE_RULE_CACHE[cache_key] = reader
        return reader
    except Exception:
        return None

def draw_title_rule_pair(c, x_center, y_top_rule, y_bot_rule, width, intensity=1.0):
    """Draw matching rules above and below a title block.
    Tiles the body of title_rule_right.png and places the curl terminal on both ends.
    Top rule: as-is (curl on right).  Bottom rule: flipped vertically.
    intensity: 1.0 = full black, 0.8 = 80% lighter."""
    render_h = 20.0
    ppi = 300
    needed_w_px = max(1, round(width * ppi / 72))
    canvas_h_px = max(8, round(render_h * ppi / 72))
    for y_rule, flip_v in [(y_top_rule, False), (y_bot_rule, True)]:
        reader = _get_tiled_title_rule_reader(needed_w_px, canvas_h_px, flip_vertical=flip_v,
                                              intensity=intensity)
        if reader is None:
            # Fallback: stretch the PNG
            png_path = _PNG_TITLE_RULE_RIGHT if os.path.exists(_PNG_TITLE_RULE_RIGHT) else _PNG_SUBHEAD_RULE_RIGHT
            draw_png_ornament(c, png_path, x_center, y_rule, width=width, height=render_h,
                              preserve_aspect=False, mirror=False, flip_vertical=flip_v)
        else:
            c.saveState()
            c.drawImage(reader, x_center - width / 2, y_rule - render_h / 2,
                        width=width, height=render_h, mask=[254, 255, 254, 255, 254, 255])
            c.restoreState()
    return True

def draw_generated_subhead_rule(c, x_center, y_center, width, mirror=False, flip_vertical=False, intensity=1.0):
    render_h = 14.0
    ppi = 300
    needed_w_px = max(1, round(width * ppi / 72))
    canvas_h_px = max(8, round(render_h * ppi / 72))
    terminal_side = 'left' if mirror else 'right'
    fade_side = 'right' if mirror else 'left'
    reader = _get_tiled_subhead_rule_reader(needed_w_px, canvas_h_px,
                                            terminal_side=terminal_side,
                                            fade_side=fade_side,
                                            flip_vertical=flip_vertical,
                                            intensity=intensity)
    if reader is None:
        png_path = _PNG_SUBHEAD_RULE_RIGHT if os.path.exists(_PNG_SUBHEAD_RULE_RIGHT) else _PNG_HEADER_LEADER_RIGHT
        return draw_png_ornament(c, png_path, x_center, y_center, width=width, height=render_h,
                                 preserve_aspect=False, mirror=mirror, flip_vertical=flip_vertical)
    c.saveState()
    c.drawImage(reader, x_center - width / 2, y_center - render_h / 2,
                width=width, height=render_h, mask=[254, 255, 254, 255, 254, 255])
    c.restoreState()
    return True

def _get_nano_crop_reader(kind):
    reader = _NANO_CROP_CACHE.get(kind)
    if reader is not None:
        return reader
    if not os.path.exists(_PNG_NANO_GUIDED_SHEET):
        return None
    try:
        img = Image.open(_PNG_NANO_GUIDED_SHEET).convert("RGBA")
        if kind == 'header_leader':
            # Narrow middle strip from the top ornament. This gives us the same AI
            # engraved family for the running part without dragging the giant end
            # curls across the full header width.
            crop = img.crop((360, 146, 980, 220))
            crop = _light_to_alpha(crop, cutoff=245)
        elif kind == 'header_terminal':
            # Take the actual end flourish from the top horizontal ornament band.
            # The previous crop accidentally clipped into the wrong region and only
            # captured a tiny fragment, which made the running header look broken.
            crop = img.crop((1028, 58, 1338, 255))
            crop = _light_to_alpha(crop, cutoff=245)
        else:
            return None
        bbox = crop.getbbox()
        if bbox:
            crop = crop.crop(bbox)
        reader = ImageReader(crop)
    except Exception:
        return None
    _NANO_CROP_CACHE[kind] = reader
    return reader

def draw_nano_crop(c, kind, x_center, y_center, width=None, height=None, preserve_aspect=True, mirror=False):
    reader = _get_nano_crop_reader(kind)
    if reader is None:
        return False
    iw, ih = reader.getSize()
    if not width and not height:
        return False
    if preserve_aspect:
        if width is None:
            scale = height / ih
            width = iw * scale
            height = ih * scale
        elif height is None:
            scale = width / iw
            width = iw * scale
            height = ih * scale
        else:
            scale = min(width / iw, height / ih)
            width = iw * scale
            height = ih * scale
    else:
        if width is None:
            width = iw
        if height is None:
            height = ih

    c.saveState()
    if mirror:
        c.translate(x_center, 0)
        c.scale(-1, 1)
        x = -width / 2
    else:
        x = x_center - width / 2
    y = y_center - height / 2
    c.drawImage(reader, x, y, width=width, height=height, mask='auto')
    c.restoreState()
    return True

def _draw_simple_printers_mark(c, x_center, y_center, size, gray=0.42):
    s = size / 2.0
    c.saveState()
    c.setStrokeColorRGB(gray, gray, gray)
    c.setFillColorRGB(gray, gray, gray)
    c.setLineWidth(0.45)
    p = c.beginPath()
    p.moveTo(x_center, y_center + s * 0.95)
    p.lineTo(x_center + s * 0.95, y_center)
    p.lineTo(x_center, y_center - s * 0.95)
    p.lineTo(x_center - s * 0.95, y_center)
    p.close()
    c.drawPath(p, fill=0, stroke=1)
    c.line(x_center - s * 0.52, y_center, x_center + s * 0.52, y_center)
    c.line(x_center, y_center - s * 0.52, x_center, y_center + s * 0.52)
    c.circle(x_center, y_center, s * 0.12, fill=1, stroke=0)
    c.restoreState()

_ANAF_FLANKER_CACHE = {}  # keyed by (needed_w_px, canvas_h_px, side)

def _draw_anaf_flanker(c, x_left, x_right, y_center, side='right'):
    """Draw a horizontal tiled curl+line flanker.
    side='right': curl on right end, line fades toward left (placed to the RIGHT of label).
    side='left' : curl on left end, line fades toward right (placed to the LEFT of label).
    x_left/x_right: the horizontal span to fill.
    y_center: vertical center of the rule."""
    render_h = 12.0
    ppi = 300
    width = x_right - x_left
    if width < 4:
        return
    needed_w_px = max(1, round(width * ppi / 72))
    canvas_h_px = max(4, round(render_h * ppi / 72))
    cache_key = (needed_w_px, canvas_h_px, side)
    reader = _ANAF_FLANKER_CACHE.get(cache_key)
    if reader is None:
        if not os.path.exists(_PNG_SUBHEAD_RULE_RIGHT):
            return
        try:
            img = Image.open(_PNG_SUBHEAD_RULE_RIGHT).convert("RGBA")
            bbox = img.getbbox()
            if bbox:
                img = img.crop(bbox)
            tile, flourish = _extract_flourish_terminal(img)
            if tile is None:
                return
            # Build with curl on right end
            tiled = _build_tiled_leader(tile, flourish, needed_w_px, canvas_h_px)
            if side == 'left':
                # Mirror so curl is on left, line fades toward right
                tiled = tiled.transpose(Image.FLIP_LEFT_RIGHT)
            # Fade the straight (inner) end toward the label
            fade_side = 'left' if side == 'right' else 'right'
            fade_zone_px = min(max(20, int(needed_w_px * 0.28)), needed_w_px // 2)
            tiled = _fade_rgba_alpha(tiled, fade_side=fade_side, fade_zone_px=fade_zone_px)
            flat = Image.new("RGB", tiled.size, (255, 255, 255))
            flat.paste(tiled, mask=tiled.split()[3])
            reader = ImageReader(flat)
            _ANAF_FLANKER_CACHE[cache_key] = reader
        except Exception:
            return
    c.saveState()
    c.drawImage(reader, x_left, y_center - render_h / 2,
                width=width, height=render_h,
                mask=[254, 255, 254, 255, 254, 255])
    c.restoreState()

def _load_odg_ornaments():
    if _ORN_DRAWING_CACHE:
        return _ORN_DRAWING_CACHE
    odg_path = os.path.join(os.path.dirname(__file__), 'orns.odg')
    if not os.path.exists(odg_path):
        return _ORN_DRAWING_CACHE
    try:
        with zipfile.ZipFile(odg_path) as zf:
            for idx, name in enumerate(sorted(n for n in zf.namelist() if n.startswith('Pictures/') and n.endswith('.svg'))):
                data = zf.read(name)
                drawing = svg2rlg(io.BytesIO(data))
                if drawing is not None:
                    key = 'top' if idx == 0 else 'bottom'
                    _ORN_DRAWING_CACHE[key] = drawing
    except Exception:
        return _ORN_DRAWING_CACHE
    return _ORN_DRAWING_CACHE

def draw_svg_ornament(c, kind, x_center, y_center, width, gray=0.7):
    drawings = _load_odg_ornaments()
    drawing = drawings.get(kind)
    if drawing is None:
        return False
    d = copy.deepcopy(drawing)
    sx = width / d.width
    sy = sx
    d.scale(sx, sy)
    d.width *= sx
    d.height *= sy
    for node in d.getContents():
        pass
    def recolor(shape):
        if hasattr(shape, 'fillColor') and shape.fillColor is not None:
            shape.fillColor = colors.Color(gray, gray, gray)
        if hasattr(shape, 'strokeColor') and shape.strokeColor is not None:
            shape.strokeColor = colors.Color(gray, gray, gray)
        if hasattr(shape, 'contents'):
            for child in shape.contents:
                recolor(child)
    recolor(d)
    c.saveState()
    renderPDF.draw(d, c, x_center - d.width / 2, y_center - d.height / 2)
    c.restoreState()
    return True

def draw_svg_file_ornament(c, svg_path, x_center, y_center, width, gray=0.7):
    drawing = _SVG_FILE_CACHE.get(svg_path)
    if drawing is None:
        if not os.path.exists(svg_path):
            return False
        try:
            drawing = svg2rlg(svg_path)
        except Exception:
            return False
        if drawing is None:
            return False
        _SVG_FILE_CACHE[svg_path] = drawing
    d = copy.deepcopy(drawing)
    sx = width / d.width
    sy = sx
    d.scale(sx, sy)
    d.width *= sx
    d.height *= sy
    def recolor(shape):
        if hasattr(shape, 'fillColor') and shape.fillColor is not None:
            shape.fillColor = colors.Color(gray, gray, gray)
        if hasattr(shape, 'strokeColor') and shape.strokeColor is not None:
            shape.strokeColor = colors.Color(gray, gray, gray)
        if hasattr(shape, 'contents'):
            for child in shape.contents:
                recolor(child)
    recolor(d)
    c.saveState()
    renderPDF.draw(d, c, x_center - d.width / 2, y_center - d.height / 2)
    c.restoreState()
    return True

# ═══════════════════════════════════════════════════════════════════════════
# PAGE GEOMETRY (from settings)
# ═══════════════════════════════════════════════════════════════════════════

PAGE_W = S.PAGE_W
PAGE_H = S.PAGE_H
MAR_T = S.MARGIN_TOP * 72
MAR_B = S.MARGIN_BOTTOM * 72
MAR_INNER = S.MARGIN_INNER * 72
MAR_OUTER = S.MARGIN_OUTER * 72

MAR_L = MAR_OUTER
MAR_R = MAR_INNER
C_BOT = MAR_B
C_TOP = PAGE_H - MAR_T
C_LEFT = MAR_L
C_RIGHT = PAGE_W - MAR_R
C_W = C_RIGHT - C_LEFT
C_H = C_TOP - C_BOT
COL_GAP = S.COL_GAP
COL_W = (C_W - COL_GAP) / 2
COL1_RIGHT = C_RIGHT
COL2_RIGHT = C_LEFT + COL_W

def set_page_geometry(page_num):
    global MAR_L, MAR_R, C_LEFT, C_RIGHT, C_W, COL_W, COL1_RIGHT, COL2_RIGHT
    if page_num % 2 == 1:  # odd / recto — spine right (RTL)
        MAR_L = MAR_OUTER
        MAR_R = MAR_INNER
    else:  # even / verso — spine left
        MAR_L = MAR_INNER
        MAR_R = MAR_OUTER
    C_LEFT = MAR_L
    C_RIGHT = PAGE_W - MAR_R
    C_W = C_RIGHT - C_LEFT
    COL_W = (C_W - COL_GAP) / 2
    COL1_RIGHT = C_RIGHT
    COL2_RIGHT = C_LEFT + COL_W

# ═══════════════════════════════════════════════════════════════════════════
# TYPOGRAPHY (from settings)
# ═══════════════════════════════════════════════════════════════════════════

FS = S.BODY_FONT_SIZE
LH = S.LH
DCS = S.DROPCAP_SIZE
DC_BODY_GAP = S.DROPCAP_BODY_GAP
PSEP = S.PSEP
MIN_PARA_GAP = S.MIN_PARA_GAP

# Heading sizes
ANAF_FS = S.H1_FONT_SIZE
ANAF_NAME_FS = S.H1_NAME_FONT_SIZE
SEC_FS = S.H2_FONT_SIZE
SUB_FS = S.H2_FONT_SIZE  # in-column subheadings (H2 in doc_parts = H3 in old docx)
SEFER_TITLE_FS = S.TITLE_FONT_SIZE

# Heading spacing
HEADING_AFTER_GAP = LH * 0.45
HEADING_BEFORE_GAP = LH * 0.5
H1_TOP_GAP = S.H1_TOP_GAP
SUB_AFTER_GAP = PSEP              # after subhead = regular inter-paragraph space (tight coupling)
SUB_LH = SUB_FS * S.H2_LINE_HEIGHT_MULT
SUB_RULE_INSET = 2.0
SUB_RULE_TOP_PAD = 7.0
SUB_TEXT_TOP_PAD = 22.0           # more room between top rule and subheading text
SUB_TEXT_BOTTOM_PAD = 11.0
SUB_BEFORE_FRAG_GAP = S.H2_SPACE_BEFORE_PT   # a bit extra before subheading (≈10.9pt)
SUB_AFTER_FRAG_GAP = SUB_AFTER_GAP
MIN_SUB_AFTER_GAP = PSEP * 0.4   # floor for balancer squeezing
MAX_SUB_AFTER_GAP = LH * 2.5     # ceiling for balancer stretching after sub

# Whether heading→paragraph gaps are allowed to stretch for column balancing.
# False = non-stretchable (tight heading→paragraph coupling, preferred typographically).
# True  = stretchable (old behavior, always achieves 0.0 DIFF).
# Even when False, _col_layout uses stretch as a last resort if reflow can't fix the imbalance.
ALLOW_SUB_AFTER_STRETCH = False

# Mutable flag used by _col_layout's last-resort path to temporarily override
# the non-stretchable behavior.  Do NOT set directly; managed by _col_layout.
_force_sub_after_stretchable = False

MIN_PART_LINES = S.MIN_PART_LINES

# Maximum top-padding that can be applied to the shorter column to absorb
# small residual imbalance without visibly changing paragraph spacing.
MAX_TOP_PAD = LH * 0.37

# Footnotes
FN_FS = S.FN_FONT_SIZE
FN_LH = S.FN_LH
FN_GAP = S.FN_GAP
FN_RULE_GAP = S.FN_RULE_GAP
FN_TEXT_GAP = S.FN_TEXT_GAP
FN_SUPER_FS = S.FN_SUPER_SIZE

# ═══════════════════════════════════════════════════════════════════════════
# TRACE LOG — comprehensive decision logging for debugging and AI analysis
# ═══════════════════════════════════════════════════════════════════════════
class TraceLog:
    """Full-trace logger that captures every decision the engine makes.

    Every event is a dict with at least:
      seq      — monotonic sequence number
      phase    — high-level phase (paginate, col_layout, render, helper)
      event    — specific event name
      page     — layout page number (0 = pre-pagination)
      detail   — human-readable description
    Plus arbitrary extra key/value pairs for structured data.

    The log is written to ``trace_log.jsonl`` (one JSON object per line)
    inside the job archive directory.  It is also accessible as
    ``_trace.entries`` for in-process inspection.
    """

    def __init__(self):
        self.entries = []
        self._seq = 0
        self._page = 0          # current layout page number
        self._phase = 'init'    # current phase label
        self._col_layout_depth = 0  # nesting depth for col_layout calls

    def set_page(self, page_num):
        self._page = page_num

    def set_phase(self, phase):
        self._phase = phase

    def log(self, event, detail='', **kw):
        self._seq += 1
        entry = {
            'seq': self._seq,
            'phase': self._phase,
            'event': event,
            'page': self._page,
            'detail': detail,
        }
        entry.update(kw)
        self.entries.append(entry)

    def save(self, jobs_dir):
        """Write trace_log.jsonl to the job archive."""
        path = os.path.join(jobs_dir, 'trace_log.jsonl')
        with open(path, 'w', encoding='utf-8') as f:
            for entry in self.entries:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')
        # Also write a summary of events by phase
        from collections import Counter
        phase_counts = Counter(e['phase'] for e in self.entries)
        event_counts = Counter(e['event'] for e in self.entries)
        summary_path = os.path.join(jobs_dir, 'trace_summary.txt')
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write(f"Total trace events: {len(self.entries)}\n\n")
            f.write("Events by phase:\n")
            for phase, cnt in sorted(phase_counts.items()):
                f.write(f"  {phase}: {cnt}\n")
            f.write(f"\nEvents by type (top 50):\n")
            for ev, cnt in event_counts.most_common(50):
                f.write(f"  {ev}: {cnt}\n")
        return path

    def clear(self):
        self.entries.clear()
        self._seq = 0

# Module-level singleton — used by all functions
_trace = TraceLog()

# Header
HEADER_FS = S.PGNUM_FONT_SIZE
PGNUM_FS = S.PGNUM_FONT_SIZE
HEADER_TITLE_FS = S.EVEN_HEADER_SIZE + 1.2
HEADER_ANAF_FS = max(11.8, S.ODD_HEADER_SIZE - 0.8)
HEADER_MAMAR_FS = max(13.2, S.ODD_HEADER_MAAR_SIZE - 1.0)
HEADER_ORN_FS = S.PGNUM_ORN_SIZE

HSEP = 9  # extra spacing around section headings

# Font name shortcuts
REG_FONT = S.BODY_FONT_NAME
BOLD_BODY_FONT = S.BOLD_BODY_FONT_NAME   # Margoliot Bold — for bold runs in body text
EFT_TALPIYOT_FONT = S.EFT_TALPIYOT_FONT_NAME  # Eft Talpiyot Class Bold — even-header title
DC_FONT = S.DROPCAP_FONT_NAME
HEADING_FONT = S.HEADING_FONT_REG_NAME
HEADING_BOLD_FONT = S.HEADING_FONT_NAME
ORN_FONT = S.ORNAMENT_FONT_NAME
FN_FONT = S.FN_FONT_NAME

# Smaller font size for text inside parentheses
PAREN_FS_RATIO = 0.85   # parenthesised body text rendered at 85% of body size
PAREN_FS = FS * PAREN_FS_RATIO

# Hebrew numbering
HEB_NUMS = ['', 'א', 'ב', 'ג', 'ד', 'ה', 'ו', 'ז', 'ח', 'ט', 'י',
            'יא', 'יב', 'יג', 'יד', 'טו', 'טז', 'יז', 'יח', 'יט', 'כ',
            'כא', 'כב', 'כג', 'כד', 'כה', 'כו', 'כז', 'כח', 'כט', 'ל']

def heb_num(n):
    if 0 < n < len(HEB_NUMS): return HEB_NUMS[n]
    return str(n)

HEB_PAGE_NUMS = {
    1:'א',2:'ב',3:'ג',4:'ד',5:'ה',6:'ו',7:'ז',8:'ח',9:'ט',10:'י',
    11:'יא',12:'יב',13:'יג',14:'יד',15:'טו',16:'טז',17:'יז',18:'יח',
    19:'יט',20:'כ',21:'כא',22:'כב',23:'כג',24:'כד',25:'כה',26:'כו',
    27:'כז',28:'כח',29:'כט',30:'ל',
}

def heb_page(n):
    return HEB_PAGE_NUMS.get(n, str(n))

# ═══════════════════════════════════════════════════════════════════════════
# TEXT HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def Wid(text, font, size):
    return pdfmetrics.stringWidth(text, font, size)

def strip_nikud(text):
    return ''.join(ch for ch in text
                   if not (0x05B0 <= ord(ch) <= 0x05C7
                           and unicodedata.category(ch) == 'Mn'))

def vis(text):
    return get_display(text, base_dir='R')

def sanitize_for_vilna(text):
    _map = {
        '\u05F3': "'", '\u05F4': '"',
        '\u201C': '"', '\u201D': '"', '\u201E': '"',
        '\u2018': "'", '\u2019': "'", '\u201A': "'",
        '\u2013': '-', '\u2014': '-', '\u05BE': '-',
    }
    return ''.join(_map.get(ch, ch) for ch in text)

def livorna_fix_quotes(text):
    """Normalise Hebrew quote characters for rendering IN FRANKREAL FONT.

    FONT-SPECIFIC: These rules apply to FrankReal (frankrealmed / frankrealbold).
    FrankReal has clean ASCII ' and " glyphs that render well at baseline.
    Do NOT assume these rules apply to other fonts (Livorna, EFT, etc.).
    If you add a new font, add a separate fix function and document its rules in AGENTS.md.

    - U+05F3 geresh → ' (ASCII single quote) — at baseline, no raising needed
    - U+05F4 gershayim → " (ASCII double quote) — at baseline, no raising needed
    - ASCII quotes passed through unchanged."""
    result = []
    for ch in text:
        if ch == '\u05F3':            # ׳ geresh → ASCII single quote
            result.append("'")
        elif ch == '\u05F4':          # ״ gershayim → ASCII double quote
            result.append('"')
        else:
            result.append(ch)
    return ''.join(result)

_GERESH_CHARS = ''         # no chars need raising — quotes now at baseline
GERESH_RISE   = 0.0        # no raise

def draw_string_raised(c, x, y, text, font, size):
    """drawString that applies livorna_fix_quotes before drawing.
    Quote chars (geresh/gershayim) are mapped to ASCII ' and " and rendered at baseline.
    No raising is applied."""
    text = livorna_fix_quotes(text)
    c.drawString(x, y, text)

def wrap_words(words, col_width, font, size):
    sp = Wid(' ', font, size)
    lines, cur, cur_w = [], [], 0
    for word in words:
        ww = Wid(vis(word), font, size)
        need = cur_w + (sp if cur else 0) + ww
        if cur and need > col_width + 0.01:
            lines.append(cur)
            cur, cur_w = [word], ww
        else:
            cur.append(word)
            cur_w = need
    if cur: lines.append(cur)
    return lines


def wrap_words_balanced(words, max_width, font, size):
    """Wrap words into lines of approximately equal width, capped at max_width.

    Uses binary search to find the narrowest column width that still fits the text
    in the same number of lines as the greedy wrap at max_width, producing balanced
    line lengths.
    """
    sp = Wid(' ', font, size)

    def _wrap_at(w):
        lines, cur, cur_w = [], [], 0
        for word in words:
            ww = Wid(vis(word), font, size)
            need = cur_w + (sp if cur else 0) + ww
            if cur and need > w + 0.01:
                lines.append(cur)
                cur, cur_w = [word], ww
            else:
                cur.append(word)
                cur_w = need
        if cur:
            lines.append(cur)
        return lines

    # Get line count at max_width
    greedy = _wrap_at(max_width)
    n_lines = len(greedy)
    if n_lines <= 1:
        return greedy

    # Binary search for the narrowest width that still gives n_lines lines
    total_text_w = sum(Wid(vis(w), font, size) for w in words) + sp * (len(words) - 1)
    lo = total_text_w / n_lines * 0.7   # rough lower bound
    hi = max_width
    for _ in range(30):
        mid = (lo + hi) / 2
        if len(_wrap_at(mid)) <= n_lines:
            hi = mid
        else:
            lo = mid
    return _wrap_at(hi)

def wrap_words_first_width(words, first_width, full_width, font, size):
    sp = Wid(' ', font, size)
    lines, cur, cur_w = [], [], 0
    max_w = first_width
    for word in words:
        ww = Wid(vis(word), font, size)
        need = cur_w + (sp if cur else 0) + ww
        if cur and need > max_w + 0.01:
            lines.append(cur)
            cur, cur_w = [word], ww
            max_w = full_width
        else:
            cur.append(word)
            cur_w = need
    if cur: lines.append(cur)
    return lines

def draw_justified_line(c, words, font, size, x_right, y, col_width, last=False):
    if not words: return
    sp = Wid(' ', font, size)
    wws = [Wid(vis(w), font, size) for w in words]
    tot = sum(wws)
    gaps = len(words) - 1
    c.setFont(font, size)
    if last:
        col_left = x_right - col_width
        cx = col_left + (col_width + tot + gaps * sp) / 2
        for w, ww in zip(words, wws):
            c.drawString(cx - ww, y, vis(w))
            cx -= ww + sp
    elif gaps == 0:
        c.drawString(x_right - wws[0], y, vis(words[0]))
    else:
        gap_w = (col_width - tot) / gaps
        cx = x_right
        for i, (w, ww) in enumerate(zip(words, wws)):
            c.drawString(cx - ww, y, vis(w))
            cx -= ww
            if i < gaps: cx -= gap_w

def draw_flush_right_line(c, words, font, size, x_right, y):
    if not words: return
    sp = Wid(' ', font, size)
    c.setFont(font, size)
    c.setFillColorRGB(0, 0, 0)
    cx = x_right
    for w in words:
        ww = Wid(vis(w), font, size)
        c.drawString(cx - ww, y, vis(w))
        cx -= ww + sp

def draw_rtl_token(c, text, font, size, x_left, y, color=(0, 0, 0)):
    """Draw a short RTL token (e.g. Hebrew page letters) without bidi surprises."""
    c.saveState()
    c.setFont(font, size)
    c.setFillColorRGB(*color)
    widths = [Wid(ch, font, size) for ch in text]
    x = x_left + sum(widths)
    for ch, cw in zip(text, widths):
        x -= cw
        c.drawString(x, y, ch)
    c.restoreState()

# ═══════════════════════════════════════════════════════════════════════════
# ORNAMENTAL DRAWING
# ═══════════════════════════════════════════════════════════════════════════

# ── h48 pgfhan ornament ──
_H48_PATH1 = [
    (7.5,0),(7.5,25),(0.25,25),(0.25,25.5),(8,25.5),(8,0.5),(57.5,0.5),
    (57.5,35),(33,35),(33,25.5),(48,25.5),(48,10),(17.5,10),(17.5,50.5),
    (73,50.5),(73,0.5),(147.5,0.5),(147.5,25.5),(155.25,25.5),(155.25,25),
    (148,25),(148,0),(72.5,0),(72.5,50),(18,50),(18,10.5),(47.5,10.5),
    (47.5,25),(32.5,25),(32.5,35.5),(58,35.5),(58,0),(7.5,0),
]
_H48_PATH2 = [
    (82.5,10),(82.5,60),(8,60),(8,35),(0.25,35),(0.25,35.5),(7.5,35.5),
    (7.5,60.5),(83,60.5),(83,10.5),(137.5,10.5),(137.5,50),(108,50),
    (108,35.5),(123,35.5),(123,25),(97.5,25),(97.5,60.5),(148,60.5),
    (148,35.5),(155.25,35.5),(155.25,35),(147.5,35),(147.5,60),(98,60),
    (98,25.5),(122.5,25.5),(122.5,35),(107.5,35),(107.5,50.5),(138,50.5),
    (138,10),(82.5,10),
]
_H48_NATIVE_W = 155.5
_H48_NATIVE_H = 61.0

def draw_h48_header(c, x_left, y_center, width):
    TILE_H = 5.0
    scale = TILE_H / _H48_NATIVE_H
    tile_w = _H48_NATIVE_W * scale
    n_tiles = int(math.ceil(width / tile_w)) + 1
    c.saveState()
    clip = c.beginPath()
    clip.rect(x_left, y_center - TILE_H/2 - 1, width, TILE_H + 2)
    c.clipPath(clip, stroke=0, fill=0)
    c.setStrokeColorRGB(0.55, 0.55, 0.55)
    c.setLineWidth(0.4)
    y_bot = y_center - TILE_H / 2
    for i in range(n_tiles):
        ox = x_left + i * tile_w
        for pts in (_H48_PATH1, _H48_PATH2):
            p = c.beginPath()
            p.moveTo(ox + pts[0][0]*scale, y_bot + pts[0][1]*scale)
            for (px, py) in pts[1:]:
                p.lineTo(ox + px*scale, y_bot + py*scale)
            c.drawPath(p, stroke=1, fill=0)
    c.restoreState()

def draw_orn88(c, x_center, y_center, width=80.0):
    """Draw vectorian ornament #88 natively."""
    _ORN88_NATIVE_W = 454.0
    _ORN88_NATIVE_H = 15.0
    scale = width / _ORN88_NATIVE_W
    height = _ORN88_NATIVE_H * scale
    ox = x_center - width / 2
    oy = y_center - height / 2
    def T(x, y): return ox + x*scale, oy + y*scale
    c.saveState()
    c.setStrokeColorRGB(0.25, 0.25, 0.25)
    c.setLineWidth(0.5)
    # Left half
    p = c.beginPath()
    p.moveTo(*T(181.6199,11.9278))
    p.curveTo(*T(178.5663,11.9278),*T(176.0909,9.842),*T(176.0274,7.2479))
    p.lineTo(*T(173.8822,7.2479))
    p.curveTo(*T(170.421,7.2479),*T(163.7019,11.9278),*T(163.7019,11.9278))
    p.curveTo(*T(156.8526,8.3067),*T(127.4752,7.0679),*T(126.8633,7.0468))
    p.lineTo(*T(0.9153,7.0468))
    p.curveTo(*T(0.4114,7.0468),*T(-0.0005,6.6338),*T(-0.0005,6.1256))
    p.curveTo(*T(-0.0005,5.6174),*T(0.4114,5.215),*T(0.9153,5.215))
    p.lineTo(*T(126.6462,5.215))
    p.curveTo(*T(151.4855,4.8021),*T(164.9238,1.9539),*T(164.9238,1.9539))
    p.curveTo(*T(167.5888,5.3315),*T(173.3507,6.0727),*T(176.1301,6.1997))
    p.curveTo(*T(176.6425,4.008),*T(178.9052,2.3563),*T(181.6199,2.3563))
    p.curveTo(*T(184.7126,2.3563),*T(187.2188,4.5056),*T(187.2188,7.142))
    p.curveTo(*T(187.2188,9.789),*T(184.7126,11.9278),*T(181.6199,11.9278))
    p.close()
    p.moveTo(*T(163.4986,3.1716))
    p.curveTo(*T(163.4986,3.1716),*T(150.8746,6.0197),*T(140.2867,6.2315))
    p.curveTo(*T(140.2867,6.2315),*T(156.5752,7.0468),*T(162.8866,10.0961))
    p.curveTo(*T(162.8866,10.0961),*T(167.3675,7.2479),*T(169.2002,6.6338))
    p.curveTo(*T(169.2002,6.6338),*T(164.7194,4.6009),*T(163.4986,3.1716))
    p.close()
    p.moveTo(*T(181.3076,3.7857))
    p.curveTo(*T(179.6251,3.7857),*T(178.2604,5.1939),*T(178.2604,6.9409))
    p.curveTo(*T(178.2604,8.6879),*T(179.6251,10.0961),*T(181.3076,10.0961))
    p.curveTo(*T(182.9921,10.0961),*T(184.3569,8.6879),*T(184.3569,6.9409))
    p.curveTo(*T(184.3569,5.1939),*T(182.9921,3.7857),*T(181.3076,3.7857))
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    # Right half
    p = c.beginPath()
    p.moveTo(*T(452.6291,7.0468))
    p.lineTo(*T(326.6759,7.0468))
    p.curveTo(*T(326.066,7.0679),*T(296.6887,8.3067),*T(289.8372,11.9278))
    p.curveTo(*T(289.8372,11.9278),*T(283.1192,7.2479),*T(279.6569,7.2479))
    p.lineTo(*T(277.515,7.2479))
    p.curveTo(*T(277.4483,9.842),*T(274.9707,11.9278),*T(271.9203,11.9278))
    p.curveTo(*T(268.8276,11.9278),*T(266.3203,9.789),*T(266.3203,7.142))
    p.curveTo(*T(266.3203,4.5056),*T(268.8276,2.3563),*T(271.9203,2.3563))
    p.curveTo(*T(274.6361,2.3563),*T(276.8988,4.008),*T(277.4112,6.1997))
    p.curveTo(*T(280.1884,6.0727),*T(285.9515,5.3315),*T(288.6154,1.9539))
    p.curveTo(*T(288.6154,1.9539),*T(302.0526,4.8021),*T(326.8951,5.2044))
    p.lineTo(*T(452.6291,5.215))
    p.curveTo(*T(453.1342,5.215),*T(453.5428,5.6174),*T(453.5428,6.1256))
    p.curveTo(*T(453.5428,6.6338),*T(453.1342,7.0468),*T(452.6291,7.0468))
    p.close()
    p.moveTo(*T(272.2305,3.7857))
    p.curveTo(*T(270.5481,3.7857),*T(269.1823,5.1939),*T(269.1823,6.9409))
    p.curveTo(*T(269.1823,8.6879),*T(270.5481,10.0961),*T(272.2305,10.0961))
    p.curveTo(*T(273.914,10.0961),*T(275.2788,8.6879),*T(275.2788,6.9409))
    p.curveTo(*T(275.2788,5.1939),*T(273.914,3.7857),*T(272.2305,3.7857))
    p.close()
    p.moveTo(*T(290.0426,3.1716))
    p.curveTo(*T(288.8197,4.6009),*T(284.3389,6.6338),*T(284.3389,6.6338))
    p.curveTo(*T(286.1706,7.2479),*T(290.6525,10.0961),*T(290.6525,10.0961))
    p.curveTo(*T(296.964,7.0468),*T(313.2525,6.2315),*T(313.2525,6.2315))
    p.curveTo(*T(302.6667,6.0197),*T(290.0426,3.1716),*T(290.0426,3.1716))
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    # Center flourishes
    p = c.beginPath()
    p.moveTo(*T(205.0712,8.042))
    p.curveTo(*T(209.1539,5.0245),*T(217.2706,7.0468),*T(223.7674,9.8949))
    p.curveTo(*T(230.2821,12.7431),*T(236.3903,14.776),*T(236.3903,14.776))
    p.curveTo(*T(232.7237,16.0042),*T(224.2883,11.229),*T(215.6221,8.6667))
    p.curveTo(*T(208.0654,6.4327),*T(205.392,7.8408),*T(205.0712,8.042))
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    p = c.beginPath()
    p.moveTo(*T(249.2166,6.5809))
    p.curveTo(*T(248.1461,7.6503),*T(244.6765,8.7832),*T(237.6111,6.4327))
    p.curveTo(*T(232.1138,4.6009),*T(224.1729,1.3928),*T(224.1729,1.3928))
    p.curveTo(*T(230.1286,0.7787),*T(231.6554,3.3727),*T(239.4449,5.8186))
    p.curveTo(*T(246.4764,8.0314),*T(249.2166,6.5809),*T(249.2166,6.5809))
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    # Center medallion
    p = c.beginPath()
    p.moveTo(*T(253.1013,14.8289))
    p.curveTo(*T(245.0534,15.4112),*T(238.2326,12.5101),*T(231.6384,9.7043))
    p.lineTo(*T(228.785,8.4973))
    p.curveTo(*T(221.9208,5.575),*T(212.5177,1.5728),*T(203.8927,1.8481))
    p.curveTo(*T(196.0132,2.1022),*T(191.4953,4.1668),*T(191.4953,7.502))
    p.curveTo(*T(191.4953,10.4243),*T(194.2926,12.1184),*T(199.81,12.5419))
    p.curveTo(*T(205.3539,12.976),*T(210.165,9.6832),*T(210.2127,9.6514))
    p.curveTo(*T(210.6277,9.3655),*T(211.1984,9.4608),*T(211.4853,9.8843))
    p.curveTo(*T(211.7755,10.2973),*T(211.6738,10.869),*T(211.2588,11.1549))
    p.curveTo(*T(211.0428,11.3031),*T(205.8801,14.8501),*T(199.6702,14.3736))
    p.curveTo(*T(191.3979,13.7383),*T(189.6625,10.2867),*T(189.6625,7.502))
    p.curveTo(*T(189.6625,4.8762),*T(191.5038,0.4187),*T(203.8345,0.0164))
    p.curveTo(*T(212.8628,-0.2695),*T(222.0394,3.2351),*T(229.0592,6.2315))
    p.lineTo(*T(231.9106,7.4385))
    p.curveTo(*T(238.3067,10.1596),*T(245.3615,13.5478),*T(252.9668,12.9972))
    p.curveTo(*T(261.9592,12.3513),*T(263.1133,9.4714),*T(263.1133,7.0468))
    p.curveTo(*T(263.1133,6.3374),*T(262.8443,5.7338),*T(262.2906,5.2044))
    p.curveTo(*T(260.8135,3.7645),*T(257.4529,2.9492),*T(253.5216,3.0763))
    p.curveTo(*T(248.7465,3.2139),*T(245.1815,4.5903),*T(245.136,4.6115))
    p.curveTo(*T(244.6807,4.8339),*T(244.1322,4.6433),*T(243.9109,4.188))
    p.curveTo(*T(243.6918,3.7327),*T(243.8824,3.1822),*T(244.3376,2.9598))
    p.curveTo(*T(244.5399,2.8645),*T(248.2901,1.4034),*T(253.4655,1.2446))
    p.curveTo(*T(257.9432,1.1069),*T(261.7199,2.0916),*T(263.5664,3.8915))
    p.curveTo(*T(264.4823,4.7703),*T(264.945,5.8397),*T(264.945,7.0468))
    p.curveTo(*T(264.945,11.769),*T(261.1831,14.2466),*T(253.1013,14.8289))
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    c.restoreState()

def draw_ornamental_rule(c, x_center, y, width, style='header'):
    hw = width / 2
    x_left = x_center - hw
    x_right_pos = x_center + hw
    if style == 'header':
        c.setStrokeColorRGB(0.25, 0.25, 0.25)
        c.setLineWidth(0.6)
        c.line(x_left, y + 1.5, x_right_pos, y + 1.5)
        c.line(x_left, y - 1.5, x_right_pos, y - 1.5)
        c.setFillColorRGB(0.25, 0.25, 0.25)
        d = 3.5
        p = c.beginPath()
        p.moveTo(x_center, y + d); p.lineTo(x_center + d, y)
        p.lineTo(x_center, y - d); p.lineTo(x_center - d, y); p.close()
        c.drawPath(p, fill=1, stroke=0)
        for offset in [-12, -8, 8, 12]:
            c.circle(x_center + offset, y, 1, fill=1, stroke=0)
    elif style == 'section':
        c.setStrokeColorRGB(0.3, 0.3, 0.3)
        c.setLineWidth(0.5)
        short_w = width * 0.65
        sl, sr = x_center - short_w/2, x_center + short_w/2
        c.line(sl, y, sr, y)
        c.setFillColorRGB(0.3, 0.3, 0.3)
        for xp in [sl, sr]:
            c.circle(xp, y, 1.2, fill=1, stroke=0)
        d = 2.2
        p = c.beginPath()
        p.moveTo(x_center, y + d); p.lineTo(x_center + d, y)
        p.lineTo(x_center, y - d); p.lineTo(x_center - d, y); p.close()
        c.drawPath(p, fill=1, stroke=0)

def draw_page_number_ornament(c, x_center, y, page_num):
    """Draw page number with ornamental frame."""
    num_str = heb_page(page_num)
    num_vis = vis(num_str)
    nw = Wid(num_vis, REG_FONT, PGNUM_FS)
    c.setFont(REG_FONT, PGNUM_FS)
    c.setFillColorRGB(0.15, 0.15, 0.15)
    c.drawString(x_center - nw/2, y, num_vis)
    bw, bh = nw + 16, 10
    bx, by = x_center - bw/2, y - 3
    c.setStrokeColorRGB(0.35, 0.35, 0.35)
    c.setLineWidth(0.4)
    c.line(bx, by, bx, by + bh)
    c.line(bx, by + bh, bx + 4, by + bh)
    c.line(bx, by, bx + 4, by)
    rx = bx + bw
    c.line(rx, by, rx, by + bh)
    c.line(rx, by + bh, rx - 4, by + bh)
    c.line(rx, by, rx - 4, by)
    c.setFillColorRGB(0.35, 0.35, 0.35)
    for px, py in [(bx - 3, by + bh/2), (rx + 3, by + bh/2)]:
        c.circle(px, py, 0.8, fill=1, stroke=0)

# ═══════════════════════════════════════════════════════════════════════════
# HEADER / FOOTER RENDERING
# ═══════════════════════════════════════════════════════════════════════════

BOOK_TITLE = S.BOOK_TITLE
MAMAR_NAME = None  # set from sefer_title item during build

def _draw_header_rule(c, y, x_start=None, x_end=None):
    if x_start is None:
        x_start = C_LEFT
    if x_end is None:
        x_end = C_RIGHT
    c.setStrokeColorRGB(0.75, 0.75, 0.75)
    c.setLineWidth(0.9)
    c.line(x_start, y, x_end, y)
    c.setStrokeColorRGB(0.92, 0.92, 0.92)
    c.setLineWidth(0.35)
    c.line(x_start, y - 0.5, x_end, y - 0.5)
    c.setStrokeColorRGB(0.82, 0.82, 0.82)
    c.setLineWidth(0.18)
    c.line(x_start, y - 0.9, x_end, y - 0.9)

def _draw_header_leader(c, y, x_left, x_right, fade_side='right', fade_zone=38):
    """Header leader line: full strength everywhere except the last fade_zone pts
    at the outer (page-number) end, where it fades to invisible.

    fade_side='left'  — strong at right end, fades over last fade_zone pts toward left
    fade_side='right' — strong at left end,  fades over last fade_zone pts toward right
    """
    if x_right <= x_left:
        return
    total = x_right - x_left

    def _segmented_line(y0, lw, g0, g1, y_off=0.0):
        n = 72
        dx = total / n
        for i in range(n):
            xa = x_left + i * dx
            xb = x_left + (i + 1) * dx
            # distance from the outer (fading) edge in pts
            if fade_side == 'left':
                dist = (i + 0.5) * dx          # distance from left edge
            else:
                dist = total - (i + 0.5) * dx  # distance from right edge
            # tt=1 full strength, tt=0 fully faded
            tt = min(dist / fade_zone, 1.0)
            tt = tt * tt * (3.0 - 2.0 * tt)   # smoothstep fade
            g = g1 + (g0 - g1) * tt
            c.setStrokeColorRGB(g, g, g)
            c.setLineWidth(lw)
            c.line(xa, y0 + y_off, xb, y0 + y_off)

    _segmented_line(y, 1.0,  0.64, 0.93,  y_off=0.0)
    _segmented_line(y, 0.38, 0.79, 0.97,  y_off=-0.5)
    _segmented_line(y, 0.20, 0.71, 0.95,  y_off=-0.9)

def _draw_header_leader_curl(c, y, x_left, x_right):
    """Odd-page leader: long continuous rule with a single curl at the far right end."""
    if x_right <= x_left + 10:
        return
    if draw_nano_crop(c, 'header_leader', (x_left + x_right) / 2, y + 0.8,
                      width=(x_right - x_left), preserve_aspect=False):
        return
    curl_w = min(15.5, max(11.0, (x_right - x_left) * 0.18))
    line_end = x_right - curl_w
    if line_end <= x_left:
        line_end = x_left + (x_right - x_left) * 0.75

    c.saveState()
    c.setStrokeColorRGB(0.38, 0.38, 0.38)
    c.setLineWidth(0.75)
    c.line(x_left, y, line_end, y)
    c.setStrokeColorRGB(0.63, 0.63, 0.63)
    c.setLineWidth(0.22)
    c.line(x_left, y - 0.45, line_end, y - 0.45)

    c.setStrokeColorRGB(0.35, 0.35, 0.35)
    c.setLineWidth(0.78)
    p = c.beginPath()
    p.moveTo(line_end, y)
    p.curveTo(line_end + curl_w * 0.42, y,
              line_end + curl_w * 0.78, y + 2.2,
              line_end + curl_w * 0.76, y + 5.5)
    p.curveTo(line_end + curl_w * 0.74, y + 8.6,
              line_end + curl_w * 0.44, y + 9.2,
              line_end + curl_w * 0.24, y + 7.1)
    p.curveTo(line_end + curl_w * 0.06, y + 5.3,
              line_end + curl_w * 0.18, y + 3.4,
              line_end + curl_w * 0.40, y + 3.2)
    c.drawPath(p, fill=0, stroke=1)
    c.restoreState()

def _draw_header_terminal_flourish(c, y, x_edge, side='right'):
    reader = None
    if os.path.exists(_PNG_HEADER_LEADER_RIGHT):
        try:
            img = Image.open(_PNG_HEADER_LEADER_RIGHT).convert("RGBA")
            bbox = img.getbbox()
            if bbox:
                img = img.crop(bbox)
            crop_w = max(1, int(img.width * 0.18))
            crop = img.crop((img.width - crop_w, 0, img.width, img.height))
            bbox = crop.getbbox()
            if bbox:
                crop = crop.crop(bbox)
            reader = ImageReader(crop)
        except Exception:
            reader = None
    if reader is None:
        reader = _get_nano_crop_reader('header_terminal')
    if reader is None:
        return False
    iw, ih = reader.getSize()
    height = 6.8
    width = iw * (height / ih)
    c.saveState()
    if side == 'left':
        c.translate(x_edge, 0)
        c.scale(-1, 1)
        x = 0
    else:
        x = x_edge - width
    # Sit the flourish on the rule rather than floating above it.
    c.drawImage(reader, x, y - height * 0.68, width=width, height=height, mask='auto')
    c.restoreState()
    return True

def _extract_flourish_terminal(img):
    """Extract just the flourish portion from the AI-generated leader (right end).
    
    img: RGBA PIL image of the leader (already auto-cropped), flourish on the RIGHT end.
    Returns: (tile, flourish) — both RGBA PIL images.
      tile: a narrow strip from the plain rule body, suitable for seamless horizontal tiling.
      flourish: the terminal flourish with some rule overlap for seamless join.
    """
    h = img.height
    w = img.width
    alpha_band = list(img.split()[3].getdata())  # just the alpha channel
    
    # Measure the "rule baseline" — median opaque pixel count in the left half
    rule_counts = []
    for x in range(0, min(w // 2, 200), 5):
        opaque = sum(1 for row in range(h) if alpha_band[row * w + x] > 30)
        if opaque > 0:
            rule_counts.append(opaque)
    if not rule_counts:
        return None, None
    rule_baseline = sorted(rule_counts)[len(rule_counts) // 2]
    
    # Flourish starts where opaque count first exceeds 1.5x the rule baseline
    # (scanning left to right from the middle of the image)
    flourish_threshold = rule_baseline * 1.5
    flourish_start = w  # default: no flourish
    for x in range(w // 3, w):
        opaque = sum(1 for row in range(h) if alpha_band[row * w + x] > 30)
        if opaque > flourish_threshold:
            # Walk back a few pixels for overlap to ensure seamless join
            flourish_start = max(0, x - 8)
            break
    
    # Extract tile from the safe plain-rule zone — use a single column so
    # tiling is perfectly seamless regardless of anti-aliasing or scaling.
    tile_x = min(w // 4, flourish_start // 2)
    tile = img.crop((tile_x, 0, tile_x + 1, h))

    # Extract flourish — include a generous overlap of clean body (30px)
    # so the join blends smoothly after downscaling.
    flourish_start_with_overlap = max(0, flourish_start - 30)
    flourish = img.crop((flourish_start_with_overlap, 0, w, h))
    
    return tile, flourish


def _build_tiled_leader(tile, flourish, needed_w_px, canvas_h):
    """Build a full-width leader by tiling the rule body and appending the flourish.
    
    tile: RGBA PIL image of a narrow plain-rule strip for horizontal tiling.
    flourish: RGBA PIL image of the terminal flourish (with rule overlap).
    needed_w_px: total width in pixels.
    canvas_h: height of the canvas in pixels.
    Returns: RGBA PIL image.
    """
    # Scale tile and flourish to match canvas_h if needed
    if tile.height != canvas_h:
        scale = canvas_h / tile.height
        tile = tile.resize((max(1, round(tile.width * scale)), canvas_h), Image.LANCZOS)
    if flourish is not None and flourish.height != canvas_h:
        scale = canvas_h / flourish.height
        flourish = flourish.resize((max(1, round(flourish.width * scale)), canvas_h), Image.LANCZOS)
    
    fl_w = flourish.width if flourish else 0
    tile_w = tile.width
    body_w = max(0, needed_w_px - fl_w)
    
    canvas = Image.new("RGBA", (needed_w_px, canvas_h), (255, 255, 255, 0))
    
    # Tile the plain rule body
    for bx in range(0, body_w, tile_w):
        paste_w = min(tile_w, body_w - bx)
        if paste_w < tile_w:
            canvas.alpha_composite(tile.crop((0, 0, paste_w, canvas_h)), (bx, 0))
        else:
            canvas.alpha_composite(tile, (bx, 0))
    
    # Paste the flourish at the right end
    if flourish and fl_w > 0:
        canvas.alpha_composite(flourish, (body_w, 0))
    
    return canvas


_LEADER_CACHE = {}  # keyed by (needed_w_px, canvas_h_px, terminal_side)
_SUBHEAD_RULE_CACHE = {}  # keyed by (needed_w_px, canvas_h_px, terminal_side, fade_side, flip_vertical)

def _fade_rgba_alpha(img, fade_side='left', fade_zone_px=40):
    if fade_zone_px <= 0:
        return img
    w, h = img.size
    alpha = img.split()[3]
    faded_alpha = Image.new('L', img.size, 0)
    for px in range(w):
        if fade_side == 'left':
            tt = min(max(px / float(fade_zone_px), 0.0), 1.0)
        else:
            dist = w - 1 - px
            tt = min(max(dist / float(fade_zone_px), 0.0), 1.0)
        tt = tt * tt * (3.0 - 2.0 * tt)
        faded_alpha.paste(int(round(255 * tt)), (px, 0, px + 1, h))
    out = img.copy()
    out.putalpha(ImageChops.multiply(alpha, faded_alpha))
    return out

def _get_tiled_subhead_rule_reader(needed_w_px, canvas_h_px, terminal_side='right', fade_side='left', flip_vertical=False, intensity=1.0):
    cache_key = (needed_w_px, canvas_h_px, terminal_side, fade_side, flip_vertical, round(intensity, 3))
    reader = _SUBHEAD_RULE_CACHE.get(cache_key)
    if reader is not None:
        return reader
    if not os.path.exists(_PNG_SUBHEAD_RULE_RIGHT):
        return None
    try:
        img = Image.open(_PNG_SUBHEAD_RULE_RIGHT).convert("RGBA")
        if intensity < 1.0:
            img = _lighten_pil_image(img, intensity)
        bbox = img.getbbox()
        if bbox:
            img = img.crop(bbox)
        tile, _flourish = _extract_flourish_terminal(img)
        if tile is None:
            return None
        # Line-only: tile the body across the full width, no curl terminal
        tiled = _build_tiled_leader(tile, None, needed_w_px, canvas_h_px)
        if flip_vertical:
            tiled = tiled.transpose(Image.FLIP_TOP_BOTTOM)
        # Fade BOTH ends symmetrically
        fade_zone_px = min(max(22, int(needed_w_px * 0.20)), needed_w_px // 3)
        tiled = _fade_rgba_alpha(tiled, fade_side='left',  fade_zone_px=fade_zone_px)
        tiled = _fade_rgba_alpha(tiled, fade_side='right', fade_zone_px=fade_zone_px)
        flat = Image.new("RGB", tiled.size, (255, 255, 255))
        flat.paste(tiled, mask=tiled.split()[3])
        reader = ImageReader(flat)
        _SUBHEAD_RULE_CACHE[cache_key] = reader
        return reader
    except Exception:
        return None

_HEADER_BAND_LEADER_CACHE = {}  # keyed by (needed_w_px, band_h_px)

def _draw_header_band_leader(c, y, x_left, x_right):
    """Header leader using the post-anaf inner-band, faded at both ends.
    y is the text baseline; the band is vertically centered on it."""
    if x_right <= x_left + 6:
        return
    render_h = 9.0
    ppi = 300
    span = x_right - x_left
    needed_w_px = max(1, round(span * ppi / 72))
    band_h_px   = max(4, round(render_h * ppi / 72))
    cache_key   = (needed_w_px, band_h_px)
    reader = _HEADER_BAND_LEADER_CACHE.get(cache_key)
    if reader is None:
        band_img = _build_mamar_inner_band(needed_w_px, band_h_px)
        if band_img is None:
            return
        # Fade both ends so it dissolves into the margin
        fade_px = max(8, min(int(needed_w_px * 0.12), needed_w_px // 3))
        band_img = _fade_rgba_alpha(band_img, fade_side='left',  fade_zone_px=fade_px)
        band_img = _fade_rgba_alpha(band_img, fade_side='right', fade_zone_px=fade_px)
        flat = Image.new("RGB", band_img.size, (255, 255, 255))
        flat.paste(band_img, mask=band_img.split()[3])
        reader = ImageReader(flat)
        _HEADER_BAND_LEADER_CACHE[cache_key] = reader
    c.saveState()
    c.drawImage(reader, x_left, y - render_h * 0.5,
                width=span, height=render_h,
                mask=[254, 255, 254, 255, 254, 255])
    c.restoreState()

def _draw_header_leader_with_terminal(c, y, x_left, x_right, terminal_side='right'):
    if x_right <= x_left + 10:
        return
    term_w = 9.0
    if os.path.exists(_PNG_HEADER_LEADER_RIGHT):
        try:
            # Render height: use 12pt — visible triple-rule, not overpowering
            render_h = 12.0
            ppi = 300
            canvas_h_px = max(8, round(render_h * ppi / 72))
            span = x_right - x_left
            needed_w_px = max(1, round(span * ppi / 72))
            
            cache_key = (needed_w_px, canvas_h_px, terminal_side)
            if cache_key not in _LEADER_CACHE:
                img = Image.open(_PNG_HEADER_LEADER_RIGHT).convert("RGBA")
                bbox = img.getbbox()
                if bbox:
                    img = img.crop(bbox)
                tile, flourish = _extract_flourish_terminal(img)
                if tile is None:
                    raise ValueError("Could not extract rule tile from leader")
                tiled = _build_tiled_leader(tile, flourish, needed_w_px, canvas_h_px)
                if terminal_side == 'left':
                    tiled = tiled.transpose(Image.FLIP_LEFT_RIGHT)
                fade_zone_px = min(max(16, int(needed_w_px * 0.16)), max(16, needed_w_px // 3))
                fade_side = 'right' if terminal_side == 'left' else 'left'
                alpha = tiled.split()[3]
                faded_alpha = Image.new('L', tiled.size, 0)
                for px in range(needed_w_px):
                    if fade_side == 'left':
                        tt = min(max(px / float(fade_zone_px), 0.0), 1.0)
                    else:
                        dist = needed_w_px - 1 - px
                        tt = min(max(dist / float(fade_zone_px), 0.0), 1.0)
                    tt = tt * tt * (3.0 - 2.0 * tt)
                    faded_alpha.paste(int(round(255 * tt)), (px, 0, px + 1, canvas_h_px))
                alpha = ImageChops.multiply(alpha, faded_alpha)
                tiled.putalpha(alpha)
                # Flatten RGBA onto white so reportlab doesn't mask out semi-transparent pixels
                flat = Image.new("RGB", tiled.size, (255, 255, 255))
                flat.paste(tiled, mask=tiled.split()[3])
                _LEADER_CACHE[cache_key] = flat

            reader = ImageReader(_LEADER_CACHE[cache_key])
            c.saveState()
            # Color-range mask: near-white pixels (254-255 per channel) become transparent
            c.drawImage(reader, x_left, y - render_h * 0.45, width=span, height=render_h,
                        mask=[254, 255, 254, 255, 254, 255])
            c.restoreState()
            return
        except Exception as e:
            import traceback; traceback.print_exc()

    # Fallback: vector drawing
    if terminal_side == 'right':
        body_left = x_left
        body_right = max(x_left, x_right - term_w + 1.6)
    else:
        body_left = min(x_right, x_left + term_w - 1.6)
        body_right = x_right
    if body_right > body_left + 8:
        # Fade away toward the text side, not the ornament side.
        fade_side = 'right' if terminal_side == 'left' else 'left'
        fade_zone = min(26, max(14, (body_right - body_left) * 0.18))
        fade_trim = min(6.0, max(2.5, fade_zone * 0.22))
        draw_nano_crop(c, 'header_leader', (body_left + body_right) / 2, y + 0.15,
                       width=(body_right - body_left), height=5.2,
                       preserve_aspect=False, mirror=(terminal_side == 'left'))
        _draw_header_leader(c, y + 0.02,
                           body_left + (fade_trim if fade_side == 'left' else 0),
                           body_right - (fade_trim if fade_side == 'right' else 0),
                           fade_side=fade_side, fade_zone=fade_zone)
    if terminal_side == 'right':
        if _draw_header_terminal_flourish(c, y, x_right, side='right'):
            return
    else:
        if _draw_header_terminal_flourish(c, y, x_left, side='left'):
            return
    if terminal_side == 'right':
        _draw_header_leader(c, y, x_left, max(x_left, x_right - term_w + 1.6), fade_side='right', fade_zone=18)
        if not _draw_header_terminal_flourish(c, y, x_right, side='right'):
            _draw_header_leader_curl(c, y, x_left, x_right)
    else:
        _draw_header_leader(c, y, min(x_right, x_left + term_w - 1.6), x_right, fade_side='left', fade_zone=18)
        if not _draw_header_terminal_flourish(c, y, x_left, side='left'):
            _draw_header_leader_curl(c, y, x_left, x_right)

def draw_gray_bottom_orn(c, x_center, y_center, width, gray=0.78, glow=False):
    if glow:
        c.saveState()
        c.setFillColorRGB(gray + 0.12, gray + 0.12, gray + 0.12)
        draw_svg_ornament(c, 'bottom', x_center, y_center, width * 1.08, gray=gray + 0.12)
        c.restoreState()
    if draw_svg_ornament(c, 'bottom', x_center, y_center, width, gray=gray):
        return True
    return False

def draw_gray_top_orn(c, x_center, y_center, width, gray=0.78, glow=False):
    if glow:
        c.saveState()
        c.setFillColorRGB(gray + 0.12, gray + 0.12, gray + 0.12)
        draw_svg_ornament(c, 'top', x_center, y_center, width * 1.08, gray=gray + 0.12)
        c.restoreState()
    if draw_svg_ornament(c, 'top', x_center, y_center, width, gray=gray):
        return True
    return False

def _draw_title_with_glow(c, x, y, text, font, size,
                           text_color=(0.25, 0.25, 0.25),
                           glow_color=(0.82, 0.82, 0.82),
                           glow_gap=1.4, glow_width=0.55):
    """Draw text with a thin equidistant glow (outline) around it.

    The glow is achieved by drawing the text repeatedly at small offsets in
    all 8 directions (glow_gap pts away from the baseline/origin), then the
    real text on top.  glow_width controls stroke width of the outline pass.
    """
    offsets = [(-glow_gap, 0), (glow_gap, 0), (0, -glow_gap), (0, glow_gap),
               (-glow_gap, -glow_gap), (-glow_gap, glow_gap),
               (glow_gap, -glow_gap), (glow_gap,  glow_gap)]
    c.saveState()
    c.setFont(font, size)
    r, g, b = glow_color
    c.setFillColorRGB(r, g, b)
    for dx, dy in offsets:
        c.drawString(x + dx, y + dy, text)
    r, g, b = text_color
    c.setFillColorRGB(r, g, b)
    c.drawString(x, y, text)
    c.restoreState()

def _draw_title_with_offset_outline(c, x, y, text, font, size,
                                    text_color=(0.25, 0.25, 0.25),
                                    inner_color=(1.0, 1.0, 1.0),
                                    outer_color=(0.78, 0.78, 0.78),
                                    inner_gap=0.45,
                                    outer_gap=1.0,
                                    outer_dx=0.55,
                                    outer_dy=-0.35):
    inner_offsets = [(-inner_gap, 0), (inner_gap, 0), (0, -inner_gap), (0, inner_gap),
                     (-inner_gap, -inner_gap), (-inner_gap, inner_gap),
                     (inner_gap, -inner_gap), (inner_gap, inner_gap)]
    outer_offsets = [(-outer_gap + outer_dx, outer_dy), (outer_gap + outer_dx, outer_dy),
                     (outer_dx, -outer_gap + outer_dy), (outer_dx, outer_gap + outer_dy),
                     (-outer_gap + outer_dx, -outer_gap + outer_dy),
                     (-outer_gap + outer_dx, outer_gap + outer_dy),
                     (outer_gap + outer_dx, -outer_gap + outer_dy),
                     (outer_gap + outer_dx, outer_gap + outer_dy)]
    c.saveState()
    c.setFont(font, size)
    c.setFillColorRGB(*outer_color)
    for dx, dy in outer_offsets:
        c.drawString(x + dx, y + dy, text)
    c.setFillColorRGB(*inner_color)
    for dx, dy in inner_offsets:
        c.drawString(x + dx, y + dy, text)
    c.setFillColorRGB(*text_color)
    c.drawString(x, y, text)
    c.restoreState()

def _draw_diamond_with_offset_glow(c, x, y, size, text_color=(0.5, 0.5, 0.5),
                                   glow_color=(0.88, 0.88, 0.88), dx=0.9, dy=-0.7):
    diamond = "◆"
    c.saveState()
    c.setFont(UI_SYMBOL_FONT, size)
    c.setFillColorRGB(*glow_color)
    c.drawString(x + dx, y + dy, diamond)
    c.setFillColorRGB(*text_color)
    c.drawString(x, y, diamond)
    c.restoreState()

def draw_running_header_odd_full(c, page_num, anaf_label, anaf_name):
    """Odd (recto) page header: outside=page number, inside=mamar | ענף + band.
    Layout (RTL): [mamar name] [|] [ענף label] [band] [page num]
    Band fills only the gap between ענף label and page number.
    A small | bar separates mamar name from ענף label.
    """
    header_y = C_TOP - 2
    rule_y = header_y + 2.25  # band center midway between baseline and cap-height

    # Page number at outer LEFT edge
    pg_str = heb_page(page_num)
    c.setFillColorRGB(0.15, 0.15, 0.15)
    draw_rtl_token(c, pg_str, REG_FONT, PGNUM_FS, C_LEFT, header_y, color=(0.15, 0.15, 0.15))
    pg_w = sum(Wid(ch, REG_FONT, PGNUM_FS) for ch in pg_str)

    label_vis = vis(livorna_fix_quotes(strip_nikud(anaf_label))) if anaf_label else ""
    mamar_vis = vis(livorna_fix_quotes(strip_nikud(MAMAR_NAME))) if MAMAR_NAME else ""

    # Truncate mamar name if too long
    if mamar_vis:
        mw = Wid(mamar_vis, HEADING_BOLD_FONT, HEADER_MAMAR_FS)
        if mw > C_RIGHT - C_LEFT - pg_w - 20:
            words = mamar_vis.split()
            while words and Wid(' '.join(words) + '...', HEADING_BOLD_FONT, HEADER_MAMAR_FS) > C_RIGHT - C_LEFT - pg_w - 20:
                words.pop()
            mamar_vis = ' '.join(words) + ('...' if words else '')
            mw = Wid(mamar_vis, HEADING_BOLD_FONT, HEADER_MAMAR_FS)
    else:
        mw = 0.0

    lw = Wid(label_vis, HEADING_FONT, HEADER_ANAF_FS) if label_vis else 0.0
    bar_w = Wid("|", HEADING_FONT, HEADER_ANAF_FS) if (label_vis and mamar_vis) else 0.0

    # Layout (RTL, right→left): mamar | ענף [band] pg#
    GAP = 6.0      # gap between text elements and band edge
    BAR_PAD = 4.0  # space either side of the | bar

    # Right anchor = right edge of content area
    # mamar name is rightmost
    mamar_x = C_RIGHT - mw
    # then | bar (BAR_PAD gap on both sides)
    bar_x    = mamar_x - BAR_PAD - bar_w  if (label_vis and mamar_vis) else mamar_x
    # then ענף label (another BAR_PAD gap after the bar)
    label_x  = bar_x - BAR_PAD - lw      if (label_vis and mamar_vis) else (mamar_x - GAP - lw)
    label_left = label_x               # leftmost edge of ענף text

    band_start = C_LEFT + pg_w + GAP
    band_end   = label_left - GAP if label_vis else (mamar_x - GAP)

    # Single band segment: page_num → ענף label
    if band_end > band_start + 8:
        _draw_header_band_leader(c, rule_y, x_left=band_start, x_right=band_end)

    # Draw ענף label
    if label_vis:
        c.setFont(HEADING_FONT, HEADER_ANAF_FS)
        c.setFillColorRGB(0.15, 0.15, 0.15)
        c.drawString(label_x, header_y, label_vis)

    # Draw | bar between ענף and mamar
    if label_vis and mamar_vis:
        c.setFont(HEADING_FONT, HEADER_ANAF_FS)
        c.setFillColorRGB(0.55, 0.55, 0.55)
        c.drawString(bar_x, header_y, "|")

    # Draw mamar name (rightmost)
    if mamar_vis:
        c.setFont(HEADING_BOLD_FONT, HEADER_MAMAR_FS)
        c.setFillColorRGB(0.15, 0.15, 0.15)
        c.drawString(mamar_x, header_y, mamar_vis)

def draw_running_header_even(c, page_num, anaf_label, anaf_name):
    """Even (verso) page header: inside=title + ornament band, outside=page number."""
    header_y = C_TOP - 2
    pg_str = heb_page(page_num)

    title_vis = vis(livorna_fix_quotes(BOOK_TITLE))
    # Title with offset white/gray outline
    title_x = C_LEFT + 2
    _draw_title_with_offset_outline(c, title_x, header_y, title_vis,
                                    EFT_TALPIYOT_FONT, HEADER_TITLE_FS,
                                    text_color=(0.24, 0.24, 0.24),
                                    inner_color=(1.0, 1.0, 1.0),
                                    outer_color=(0.79, 0.79, 0.79),
                                    inner_gap=0.42,
                                    outer_gap=0.92,
                                    outer_dx=0.50,
                                    outer_dy=-0.28)
    tw = Wid(title_vis, EFT_TALPIYOT_FONT, HEADER_TITLE_FS)
    content_end_x = title_x + tw

    # Page number at outer RIGHT edge
    pw = sum(Wid(ch, REG_FONT, PGNUM_FS) for ch in pg_str)
    draw_rtl_token(c, pg_str, REG_FONT, PGNUM_FS, C_RIGHT - pw, header_y, color=(0.15, 0.15, 0.15))

    # Leader: inner-band fragment, faded both ends.
    rule_y = header_y + 2.25  # band center midway between baseline and cap-height, matching odd header
    _draw_header_band_leader(c, rule_y,
                             x_left=content_end_x + 4,
                             x_right=C_RIGHT - pw - 4)

def draw_divider_line(c, y_top, y_bot):
    div_x = C_LEFT + COL_W + COL_GAP / 2
    c.setStrokeColorRGB(0.6, 0.6, 0.6)
    c.setLineWidth(0.4)
    c.line(div_x, y_bot, div_x, y_top)

# ═══════════════════════════════════════════════════════════════════════════
# TABLE OF CONTENTS PAGE
# ═══════════════════════════════════════════════════════════════════════════

TOC_TITLE_FS   = 22.0   # "תוכן הענינים" big title
TOC_MAMAR_FS   = 17.0   # "מאמר" label + mamar name — boxed, centered
TOC_ENTRY_FS   = 13.0   # each anaf entry line
TOC_ENTRY_LH   = TOC_ENTRY_FS * 1.6
TOC_DOT_CHAR   = "."

_CONTACT_STRIP_PATH = None

def _make_contact_strip_image():
    global _CONTACT_STRIP_PATH
    if _CONTACT_STRIP_PATH and os.path.exists(_CONTACT_STRIP_PATH):
        return _CONTACT_STRIP_PATH
    texture_path = "/root/covers/v10/texture_t1_full.png"
    if not os.path.exists(texture_path):
        return None
    img = Image.open(texture_path).convert("RGB")
    strip_h_px = 360
    sy0 = max(0, img.height // 2 - strip_h_px // 2)
    strip = img.crop((0, sy0, img.width, sy0 + strip_h_px))
    strip = strip.resize((2200, strip_h_px), Image.LANCZOS)
    strip = ImageOps.grayscale(strip)
    strip = ImageOps.autocontrast(strip, cutoff=3)
    strip = strip.point(lambda p: int(max(0, min(255, p * 0.82))))
    rgb = Image.merge("RGB", (strip, strip, strip))
    tmp = tempfile.NamedTemporaryFile(prefix="palot_contact_strip_", suffix=".jpg", delete=False)
    rgb.save(tmp.name, "JPEG", quality=95)
    _CONTACT_STRIP_PATH = tmp.name
    return _CONTACT_STRIP_PATH

def draw_contact_page(c):
    """Contact page for front matter (final page 2 / even).
    Design: two vertical ornament strips (post-anaf rule rotated 90°) close to
    the right side of the page, creating a narrow inset column between them.
    Author info sits toward the top of the inset; imud info toward the bottom.
    No boxes — style matches the book's engraved ornament theme."""
    set_page_geometry(2)

    # ── Text line definitions (needed before geometry so we can measure widths) ──
    lines_author = [
        ("לכל עניני הספר:", HEADING_BOLD_FONT, 13.0, 0.15),
        ("יואל באלזאם",      HEADING_BOLD_FONT, 13.5, 0.10),
        ("(908) 783-8307",  HEADING_FONT,      12.0, 0.15),
        ("2151 58th St",    HEADING_FONT,      11.5, 0.18),
        ("Brooklyn, NY 11204", HEADING_FONT,   11.5, 0.18),
    ]
    lines_imud = [
        ("עימוד ועיצוב:",    HEADING_BOLD_FONT, 12.0, 0.15),
        ("(845) 826 - 1850", HEADING_FONT,      11.5, 0.18),
    ]

    def _vis_text(text):
        return vis(livorna_fix_quotes(strip_nikud(text))) if any('\u0590' <= ch <= '\u05FF' for ch in text) else text

    max_text_w = max(
        Wid(_vis_text(text), font, size)
        for lines in (lines_author, lines_imud)
        for text, font, size, _gray in lines
    )

    # ── Vertical strip geometry ──────────────────────────────────────────
    # The inset column is centred within the right half of the content area
    # (even page → spine is on the left, so "right" is the outer/page edge).
    # Two strips flank the text column; the column is sized to fit the widest
    # text line plus a comfortable side padding.
    strip_w_pt   = 11.0   # rendered width of each vertical strip
    band_w_pt    = 14.0
    gap_pt       = 1.5
    vs_w_pt      = strip_w_pt + gap_pt + band_w_pt + gap_pt + strip_w_pt  # total strip assembly width

    text_pad_pt  = 10.0   # padding between strip inner edge and text on each side
    # Clear text column width = widest line + padding on both sides
    col_clear_w  = max_text_w + text_pad_pt * 2

    # Distance between the two strip centres = col_clear_w + one full strip assembly on each side
    inset_gap    = col_clear_w + vs_w_pt

    # Anchor the strip pair toward the right side of the content area
    pair_cx      = C_LEFT + C_W * 0.72
    strip1_x     = pair_cx - inset_gap / 2
    strip2_x     = pair_cx + inset_gap / 2

    # Vertical extent: bleed to page edge (top and bottom)
    strip_top_y  = PAGE_H   # bleed off top
    strip_bot_y  = 0        # bleed off bottom
    strip_h_pt   = strip_top_y - strip_bot_y

    # ── Build vertical strip images ──────────────────────────────────────
    # We reuse the post-anaf three-part ornament, but built vertically:
    # rotate the horizontal images 90° CCW so the rule runs top-to-bottom.
    ppi = 300
    needed_h_px  = max(1, round(strip_h_pt * ppi / 72))   # length dimension (was width)
    rule_w_px    = max(4, round(strip_w_pt * ppi / 72))   # thickness dimension (was height)

    # Inner band thickness for the middle part — thinner than the rule pair
    band_w_pt    = 14.0
    gap_pt       = 1.5
    band_w_px    = max(4, round(band_w_pt * ppi / 72))

    def _make_vert_strip():
        """Build a three-part vertical ornament strip (post-anaf rotated 90°).
        Returns an RGB PIL Image (white background), width≈(rule_w+gap+band_w+gap+rule_w),
        height=needed_h_px."""
        # Horizontal rule image (symmetric curl+line+curl)
        rule_h = _build_plain_tiled_rule(needed_h_px, rule_w_px, fade_zone_frac=0.14)
        if rule_h is None:
            return None
        band_h = _build_mamar_inner_band(needed_h_px, band_w_px)

        # Rotate each piece 90° CCW: what was "horizontal length" becomes height
        rule_v      = rule_h.rotate(90, expand=True)          # now: w=rule_w_px, h=needed_h_px
        rule_v_flip = rule_h.rotate(-90, expand=True)         # same but direction flipped
        if band_h is not None:
            band_v  = band_h.rotate(90, expand=True)          # w=band_w_px, h=needed_h_px
        else:
            band_v  = None

        gap_px = max(1, round(gap_pt * ppi / 72))
        total_w = rule_w_px + gap_px + (band_w_px + gap_px if band_v else 0) + rule_w_px

        def _flat(img):
            out = Image.new("RGB", img.size, (255, 255, 255))
            out.paste(img, mask=img.split()[3])
            return out

        canvas = Image.new("RGB", (total_w, needed_h_px), (255, 255, 255))
        # Left-to-right: rule_v | gap | band_v | gap | rule_v_flip
        canvas.paste(_flat(rule_v), (0, 0))
        x = rule_w_px + gap_px
        if band_v is not None:
            canvas.paste(_flat(band_v), (x, 0))
            x += band_w_px + gap_px
        canvas.paste(_flat(rule_v_flip), (x, 0))
        return canvas

    vert_strip = _make_vert_strip()

    # Draw both vertical strips
    if vert_strip is not None:
        vsr = ImageReader(vert_strip)
        vs_w_pt = strip_w_pt + gap_pt + band_w_pt + gap_pt + strip_w_pt  # total rendered width
        for sx in (strip1_x, strip2_x):
            x_draw = sx - vs_w_pt / 2
            c.saveState()
            c.drawImage(vsr, x_draw, strip_bot_y,
                        width=vs_w_pt, height=strip_h_pt,
                        mask=[254, 255, 254, 255, 254, 255])
            c.restoreState()

    # ── Text layout ──────────────────────────────────────────────────────
    # Inset column: between the two strip centres.
    # Text is centred within the inset, right-aligned (RTL).
    col_cx    = (strip1_x + strip2_x) / 2   # horizontal centre of inset column
    col_right = strip1_x - vs_w_pt / 2 - 4  # right edge of inset (just inside left strip)
    # NB: for even page, right side is the OUTER margin (away from spine), so "right" here
    # means toward the page's right physical edge.

    lines_author = [
        ("לכל עניני הספר:", HEADING_BOLD_FONT, 16.0, 0.15),
        ("יואל באלזאם",      HEADING_BOLD_FONT, 16.5, 0.10),
        ("(908) 783-8307",  HEADING_FONT,      14.5, 0.15),
        ("2151 58th St",    HEADING_FONT,      13.5, 0.18),
        ("Brooklyn, NY 11204", HEADING_FONT,   13.5, 0.18),
    ]
    lines_imud = [
        ("עימוד ועיצוב:",    HEADING_BOLD_FONT, 14.5, 0.15),
        ("(845) 826 - 1850", HEADING_FONT,      13.5, 0.18),
    ]

    face = pdfmetrics.getFont(HEADING_BOLD_FONT).face
    asc_frac = (face.ascent or 800) / 1000.0

    def _draw_line_group(lines, start_y):
        y = start_y
        for text, font, size, gray in lines:
            vis_text = vis(livorna_fix_quotes(strip_nikud(text))) if any('\u0590' <= ch <= '\u05FF' for ch in text) else text
            tw = Wid(vis_text, font, size)
            c.setFont(font, size)
            c.setFillColorRGB(gray, gray, gray)
            baseline = y - asc_frac * size * 0.08
            # Centre within the inset column
            c.drawString(col_cx - tw / 2, baseline, vis_text)
            y -= size * 1.45
        return y

    # Author block: start ~28% down from C_TOP
    author_start_y = C_TOP - strip_h_pt * 0.28
    _draw_line_group(lines_author, author_start_y)

    # Imud block: start ~65% down from C_TOP (toward bottom)
    imud_start_y = C_TOP - strip_h_pt * 0.65
    _draw_line_group(lines_imud, imud_start_y)


def draw_toc_page(c, anaf_entries, page_offset):
    """Draw a single תוכן הענינים (table of contents) page.

    anaf_entries: list of (label_str, name_str, page_num_int)
    page_offset:  the page number of the *first content page* (so TOC page
                  is page_offset - 1; the content pages start at page_offset).
    """
    # TOC is page 3 in the final book layout (odd page geometry)
    set_page_geometry(3)

    # ── Title: תוכן הענינים inside baroque scroll frame ──────────────────
    title_text   = vis("תוכן הענינים")
    title_cx     = C_LEFT + C_W / 2

    # Frame sizing
    tw           = Wid(title_text, HEADING_BOLD_FONT, TOC_TITLE_FS)
    frame_pad_x  = 40.0
    frame_pad_y  = 16.0
    title_frame_w = tw + frame_pad_x * 2
    title_frame_h = TOC_TITLE_FS + frame_pad_y * 2

    # Place frame top 6pt below C_TOP
    title_frame_top = C_TOP - 6
    title_frame_bot = title_frame_top - title_frame_h
    title_frame_left = title_cx - title_frame_w / 2

    # Draw text FIRST, frame on top (frame white interior masked transparent)
    # ── Vertical centering using measured Hebrew glyph body ──
    # FrankReal Bold body (excl. lamed/kuf/ayin): visual centre = 0.2475*fs above baseline
    # Single line: baseline = frame_centre - 0.2475 * fs
    BODY_CENTRE_ABOVE_BL = 0.2475
    frame_cy = title_frame_bot + title_frame_h / 2
    title_y  = frame_cy - BODY_CENTRE_ABOVE_BL * TOC_TITLE_FS
    c.setFont(HEADING_BOLD_FONT, TOC_TITLE_FS)
    c.setFillColorRGB(0.2, 0.2, 0.2)
    c.drawString(title_cx - tw / 2, title_y, title_text)

    draw_frame_box(c, title_frame_left, title_frame_bot, title_frame_w, title_frame_h, intensity=0.8)

    # ── Y-anchor below title frame ────────────────────────────────────────
    title_orn_y = title_frame_bot - TOC_TITLE_FS * 0.8

    # ── Mamar label + name decorated with subhead rules ──────────────────
    # Sub-rule render_h=14pt → half=7pt. Clear gap between rule edge and text edge.
    sub_rule_half    = 14.0 / 2            # 7pt
    sub_text_gap     = 6.0                 # clear gap between rule edge and text edge
    sub_rule_pad     = sub_rule_half + sub_text_gap   # 13pt: rule centre to text edge
    mamar_rule_w     = C_W * 0.72
    mamar_cx         = C_LEFT + C_W / 2

    if MAMAR_NAME:
        mamar_label    = vis(livorna_fix_quotes(strip_nikud("מאמר")))
        mamar_name     = vis(livorna_fix_quotes(strip_nikud(MAMAR_NAME)))
        mamar_label_fs = TOC_MAMAR_FS * 0.82
        mamar_gap      = TOC_MAMAR_FS * 0.55   # gap between label baseline and name cap-top

        label_cap_h    = mamar_label_fs * 0.72
        name_cap_h     = TOC_MAMAR_FS   * 0.72

        # Top rule: anchor from title so spacing is stable
        top_rule_y  = title_orn_y - TOC_TITLE_FS * 1.4
        label_y     = top_rule_y - sub_rule_pad - label_cap_h
        name_y      = label_y - mamar_gap - name_cap_h
        bot_rule_y  = name_y - sub_rule_pad

        c.setFillColorRGB(0.2, 0.2, 0.2)
        draw_generated_subhead_rule(c, mamar_cx, top_rule_y, mamar_rule_w,
                                    mirror=False, flip_vertical=False, intensity=0.8)
        c.setFont(HEADING_BOLD_FONT, mamar_label_fs)
        c.drawString(C_LEFT + (C_W - Wid(mamar_label, HEADING_BOLD_FONT, mamar_label_fs)) / 2,
                     label_y, mamar_label)
        c.setFont(HEADING_BOLD_FONT, TOC_MAMAR_FS)
        c.drawString(C_LEFT + (C_W - Wid(mamar_name, HEADING_BOLD_FONT, TOC_MAMAR_FS)) / 2,
                     name_y, mamar_name)
        draw_generated_subhead_rule(c, mamar_cx, bot_rule_y, mamar_rule_w,
                                    mirror=True, flip_vertical=True, intensity=0.8)

        mamar_bottom_y = bot_rule_y
    else:
        mamar_bottom_y = title_orn_y - TOC_TITLE_FS * 1.8

    # ── Gap to entry list ────────────────────────────────────────────────
    rule_y = mamar_bottom_y - TOC_MAMAR_FS * 1.6

    # ── Entry list ──────────────────────────────────────────────────────
    # Layout: entries start from the right edge.
    # Label (ענף א׳) is right-aligned to a fixed right column.
    # Anaf names are right-aligned to a fixed name column to the left of label.
    # Dot leaders fill between page number on the left and the name.
    # All in Livorna; label not bold, name bold.

    entry_lh = TOC_ENTRY_FS * 1.65   # tighter spacing between TOC entries
    entry_top = rule_y - entry_lh * 0.9
    label_col_w = max(Wid(vis(livorna_fix_quotes(strip_nikud(lbl))), HEADING_FONT, TOC_ENTRY_FS)
                      for lbl, _, _ in anaf_entries) + 8
    toc_inset = C_W * 0.06
    label_right_x = C_RIGHT - toc_inset
    name_right_x = label_right_x - label_col_w - 22
    pgnum_x = C_LEFT + toc_inset
    dot_w = Wid(TOC_DOT_CHAR, HEADING_FONT, TOC_ENTRY_FS)
    # Available width for the name column (between pgnum_x and name_right_x)
    name_avail_w = name_right_x - pgnum_x - dot_w * 6

    # No TOC fragment ornament between columns — kept simple

    y = entry_top
    for label_str, name_str, pg in anaf_entries:
        label_v = vis(livorna_fix_quotes(strip_nikud(label_str)))
        name_v  = vis(livorna_fix_quotes(strip_nikud(name_str)))
        pg_v    = heb_page(pg)

        # Label — right-aligned at indented right, not bold
        lw = Wid(label_v, HEADING_FONT, TOC_ENTRY_FS)
        c.setFont(HEADING_FONT, TOC_ENTRY_FS)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.drawString(label_right_x - lw, y, label_v)

        # Name — bold; wrap onto continuation lines if too wide
        nw = Wid(name_v, HEADING_BOLD_FONT, TOC_ENTRY_FS)
        c.setFont(HEADING_BOLD_FONT, TOC_ENTRY_FS)
        if nw <= name_avail_w:
            # Single line — draw normally
            c.drawString(name_right_x - nw, y, name_v)
            name_bottom_y = y   # baseline of name for dot-leader row
        else:
            # Multi-line wrap: split words and greedily fit each line RTL
            words = name_v.split(' ')
            lines = []
            cur_line = []
            cur_w = 0.0
            space_w = Wid(' ', HEADING_BOLD_FONT, TOC_ENTRY_FS)
            for word in words:
                ww = Wid(word, HEADING_BOLD_FONT, TOC_ENTRY_FS)
                add_w = ww if not cur_line else ww + space_w + cur_w
                if add_w <= name_avail_w or not cur_line:
                    cur_line.append(word)
                    cur_w = add_w
                else:
                    lines.append(' '.join(cur_line))
                    cur_line = [word]
                    cur_w = ww
            if cur_line:
                lines.append(' '.join(cur_line))
            # Draw first line at y, continuation lines below (descending = decreasing y)
            name_draw_y = y
            for li, line_text in enumerate(lines):
                lw2 = Wid(line_text, HEADING_BOLD_FONT, TOC_ENTRY_FS)
                c.drawString(name_right_x - lw2, name_draw_y, line_text)
                name_draw_y -= TOC_ENTRY_FS * 1.3
            name_bottom_y = name_draw_y + TOC_ENTRY_FS * 1.3  # y of last drawn line
            # Extra vertical advance for the wrapped line
            extra_wrap_lines = len(lines) - 1
            y -= TOC_ENTRY_FS * 1.3 * extra_wrap_lines

        # Page number — left-aligned at indented left
        pw = sum(Wid(ch, HEADING_FONT, TOC_ENTRY_FS) for ch in pg_v)
        draw_rtl_token(c, pg_v, HEADING_FONT, TOC_ENTRY_FS, pgnum_x, y, color=(0.1, 0.1, 0.1))

        # Dot leaders between page number and the start of the name (on the main baseline)
        nw_draw = Wid(name_v.split(' ')[0] if nw > name_avail_w else name_v,
                      HEADING_BOLD_FONT, TOC_ENTRY_FS)
        dot_start = pgnum_x + pw + dot_w * 2
        dot_end   = name_right_x - nw_draw - dot_w * 2
        dot_x = dot_start
        c.setFillColorRGB(0.45, 0.45, 0.45)
        c.setFont(HEADING_FONT, TOC_ENTRY_FS)
        while dot_x + dot_w <= dot_end:
            c.drawString(dot_x, y, TOC_DOT_CHAR)
            dot_x += dot_w * 2   # space between dots

        c.setFillColorRGB(0.1, 0.1, 0.1)
        y -= entry_lh

    # Post-ornament — TOC page has generous whitespace so use ~3× the regular gap
    post_orn_top_y = y + entry_lh - HSEP * 1.4 * 3
    draw_post_anaf_divider_band(c, C_LEFT + C_W / 2, post_orn_top_y, C_W * 0.72)

# ═══════════════════════════════════════════════════════════════════════════
# FOOTNOTE MARKER HANDLING
# ═══════════════════════════════════════════════════════════════════════════

FN_MARKER_RE = re.compile(r'\u27E8fn(\d+)\u27E9')

def split_word_fn_markers(word):
    parts = []
    pos = 0
    for m in FN_MARKER_RE.finditer(word):
        if m.start() > pos:
            parts.append(('text', word[pos:m.start()]))
        parts.append(('fn', int(m.group(1))))
        pos = m.end()
    if pos < len(word):
        parts.append(('text', word[pos:]))
    if not parts:
        parts.append(('text', word))
    return parts

def word_has_fn(word):
    return bool(FN_MARKER_RE.search(word))

def word_without_fn(word):
    return FN_MARKER_RE.sub('', word)

def word_display_width(word, font, size):
    clean = livorna_fix_quotes(word_without_fn(word))
    w = Wid(vis(clean), font, size) if clean else 0
    for m in FN_MARKER_RE.finditer(word):
        fn_num = int(m.group(1))
        w += Wid(vis(heb_num(fn_num)), REG_FONT, FN_SUPER_FS)
    return w

def word_w(word, font, size):
    if word_has_fn(word): return word_display_width(word, font, size)
    return Wid(vis(livorna_fix_quotes(word)), font, size)

# ── Tagged-word helpers ─────────────────────────────────────────────────────
# A tagged word is either:
#   plain string  →  rendered with the line's default font/size
#   (str, font, size) tuple  →  rendered with its own font/size
#
def tw_str(w):
    """Return the raw string of a (possibly tagged) word."""
    return w[0] if isinstance(w, tuple) else w

def tw_font(w, default_font):
    return w[1] if isinstance(w, tuple) else default_font

def tw_size(w, default_size):
    return w[2] if isinstance(w, tuple) else default_size

def tw_w(w, default_font=None, default_size=None):
    """Width of a tagged word."""
    s = tw_str(w)
    f = tw_font(w, default_font or REG_FONT)
    sz = tw_size(w, default_size or FS)
    return word_w(s, f, sz)

def tw_has_fn(w):
    return word_has_fn(tw_str(w))

def _segments_to_tagged_words(segments):
    """Convert a list of {text, bold} segments into a list of tagged words.

    Each segment is split on whitespace to produce words.  Bold words are
    tagged (word, BOLD_BODY_FONT, FS); regular words are plain strings.
    Text inside parentheses (including the parens themselves) is tagged with
    (word, font, PAREN_FS) for a smaller rendering size.

    Mid-word segment boundaries (e.g. bold run covering only part of a word)
    are handled by merging adjacent fragments into a single plain-string word
    so that e.g. bold-צ + plain-דיק → plain "צדיק" rather than two tokens.

    Returns list of words (plain strings or (str, font, size) tuples).
    """
    # ── Pass 1: produce a flat list of (str, font, size, had_leading_space)
    # had_leading_space=True means the fragment was preceded by whitespace
    # (i.e. it is the start of a new word, not glued to the previous one).
    fragments = []   # list of (text_str, font, size, leading_space)
    in_paren = False
    prev_ended_with_space = True   # treat start-of-text as word-boundary

    for seg in segments:
        raw_text = seg['text']
        is_bold  = seg['bold']
        base_font = BOLD_BODY_FONT if is_bold else REG_FONT

        buf = ''
        seg_frags = []   # (str, font, size)

        def flush_buf_seg():
            nonlocal buf
            # Split on regular whitespace only (not NBSP), so NBSP-joined
            # tokens stay as one word (e.g. math expressions like 196=200-4).
            if buf.strip():
                for w in re.split(r"[ \t\n\r]+", buf):
                    w = w.strip()
                    if not w: continue
                    sz = PAREN_FS if in_paren else FS
                    seg_frags.append((w, base_font, sz))
            buf = ''

        _paren_just_closed = False
        for ci, ch in enumerate(raw_text):
            # After closing paren, absorb trailing punctuation (.,;:)
            # so it stays in the paren-sized word and doesn't become a
            # separate regular-sized token that bidi can detach.
            if _paren_just_closed:
                _paren_just_closed = False
                if ch in '.,;:':
                    # Append punctuation to the last paren-sized frag
                    if seg_frags:
                        lw, lf, lsz = seg_frags[-1]
                        seg_frags[-1] = (lw + ch, lf, lsz)
                    else:
                        buf += ch
                    continue
            if ch == '(':
                flush_buf_seg()
                in_paren = True
                buf += ch
            elif ch == ')':
                buf += ch
                flush_buf_seg()
                in_paren = False
                _paren_just_closed = True
            else:
                buf += ch
        flush_buf_seg()

        if not seg_frags:
            # segment was all whitespace — mark word boundary
            prev_ended_with_space = True
            continue

        # Determine whether this segment started with whitespace
        seg_starts_with_space = raw_text[:1] in (' ', '\t', '\n') or not raw_text

        for i, (w, f, sz) in enumerate(seg_frags):
            if i == 0:
                leading = prev_ended_with_space or seg_starts_with_space
            else:
                leading = True   # internal split is always a new word
            fragments.append((w, f, sz, leading))

        # Did this segment end with whitespace?
        prev_ended_with_space = raw_text[-1:] in (' ', '\t', '\n') if raw_text else True

    # ── Pass 2: merge fragments that share a word boundary (leading=False)
    # into plain strings (drop font tags on merged tokens — use REG_FONT).
    result = []
    i = 0
    while i < len(fragments):
        w, f, sz, leading = fragments[i]
        if not leading and result:
            # Glue onto the previous token — both become a plain string
            prev = result[-1]
            prev_str = tw_str(prev)
            result[-1] = prev_str + w   # plain string
        else:
            # Normal new word
            if f == REG_FONT and sz == FS:
                result.append(w)
            else:
                result.append((w, f, sz))
        i += 1

    return result

def _all_tagged_plain(tagged_words):
    """True if all tagged words are plain strings (no special font/size)."""
    return all(isinstance(w, str) for w in tagged_words)



def _optimize_line_fills(lines_of_words, col_width, font, size):
    """Redistribute words between adjacent lines to minimise fill-ratio variance.
    Words may be plain strings or (str, font, size) tagged tuples."""
    T = len(lines_of_words)
    if T < 2: return lines_of_words
    sp = Wid(' ', font, size)
    all_words = []
    for ln in lines_of_words: all_words.extend(ln)
    W = len(all_words)
    if W < T: return lines_of_words
    wws = [tw_w(w, font, size) for w in all_words]

    def line_natural_w(start, end):
        k = end - start
        if k <= 0: return 0.0
        return sum(wws[start:end]) + sp * (k - 1)

    def _try_line_count(num_lines):
        nT = num_lines
        if nT < 1 or W < nT: return None
        if W < nT + (nT - 1): return None
        total_natural = line_natural_w(0, W)
        target_fill = min(total_natural / (nT * col_width), 0.97) if col_width > 0 else 0.9
        target_w = target_fill * col_width
        cuts = [0]
        pos = 0
        for line_i in range(nT - 1):
            remaining_lines = nT - line_i
            must_leave = (remaining_lines - 1) * 2 if line_i < nT - 2 else 1
            max_end = W - must_leave
            end = pos + 1
            while end < max_end and line_natural_w(pos, end + 1) <= col_width + 0.01:
                end += 1
            best_end = end
            best_dist = abs(line_natural_w(pos, end) - target_w)
            for e in range(max(pos + 2, end - 3), end + 1):
                if e > max_end: break
                if line_natural_w(pos, e) > col_width + 0.01: break
                d = abs(line_natural_w(pos, e) - target_w)
                if d < best_dist:
                    best_dist = d
                    best_end = e
            cuts.append(best_end)
            pos = best_end
        cuts.append(W)

        def _valid(cs):
            for i in range(nT):
                k = cs[i+1] - cs[i]
                if k < 1: return False
                if i < nT - 1 and k < 2: return False
                if line_natural_w(cs[i], cs[i+1]) > col_width + 0.5: return False
            return True
        if not _valid(cuts): return None

        def _fills(cs): return [line_natural_w(cs[i], cs[i+1]) / col_width for i in range(nT)]
        def _score(cs):
            fs = _fills(cs)
            # Penalise non-last lines that are too sparse (< 86% fill)
            pen = sum(max(0.0, 0.86 - f) * 12 for i, f in enumerate(fs) if i < nT - 1)
            # Prefer tighter packing: penalise low mean fill of non-last lines
            non_last = [f for i, f in enumerate(fs) if i < nT - 1] or fs
            mean_fill = sum(non_last) / len(non_last)
            # Score = variance + underfill penalty + tightness penalty
            return statistics.pstdev(fs) + pen + max(0.0, 0.94 - mean_fill) * 2.5

        best_cuts = list(cuts)
        best_score = _score(best_cuts)
        improved = True
        while improved:
            improved = False
            for b in range(1, nT):
                for delta in (-1, +1):
                    trial = list(best_cuts)
                    trial[b] += delta
                    if trial[b] <= trial[b-1]: continue
                    if trial[b] >= trial[b+1]: continue
                    if not _valid(trial): continue
                    s = _score(trial)
                    if s < best_score - 1e-9:
                        best_score = s
                        best_cuts = trial
                        improved = True
        return (best_score, best_cuts, nT)

    same_T_res = _try_line_count(T)
    if same_T_res is None: return lines_of_words
    same_score, same_cuts, _ = same_T_res

    def _has_severe_underfill(cuts, nT):
        for i in range(nT - 1):
            if line_natural_w(cuts[i], cuts[i+1]) / col_width < 0.70: return True
        return False

    if _has_severe_underfill(same_cuts, T):
        candidates = [(same_score, same_cuts, T)]
        for alt_T in (T - 1, T + 1):
            res = _try_line_count(alt_T)
            if res is not None: candidates.append(res)
        candidates.sort(key=lambda x: x[0])
        best_score, best_cuts, best_T = candidates[0]
        if best_T != T:
            if same_score > 0 and best_score >= same_score * 0.7:
                best_score, best_cuts, best_T = same_score, same_cuts, T
    else:
        best_score, best_cuts, best_T = same_score, same_cuts, T

    result = []
    for i in range(best_T):
        result.append(all_words[best_cuts[i]:best_cuts[i+1]])
    return result


def wrap_words_fn(words, col_width, font, size):
    """Wrap a list of words (plain or tagged) into lines fitting col_width."""
    sp = Wid(' ', font, size)
    lines, cur, cur_w = [], [], 0
    for word in words:
        ww = tw_w(word, font, size)
        need = cur_w + (sp if cur else 0) + ww
        if cur and need > col_width + 0.01:
            lines.append(cur)
            cur, cur_w = [word], ww
        else:
            cur.append(word)
            cur_w = need
    if cur: lines.append(cur)
    if len(lines) >= 2:
        lines = _optimize_line_fills(lines, col_width, font, size)
    return lines


def precompute_para(bold_word, rest_text, col_width, segments=None):
    """Compute lines for a paragraph with dropcap.

    If `segments` is provided (list of {text, bold} dicts), it is used to
    build tagged words (Margoliot for bold, FrankRuehl for regular, smaller
    for parenthesised text).  Otherwise, `rest_text` is used as a flat
    plain-font string (legacy path).
    """
    lines = []

    # Build the word list — tagged if segments provided, plain otherwise
    if segments:
        # First word is the dropcap word (bold_word from flat text).
        # Convert all segments to tagged words.
        all_tagged = _segments_to_tagged_words(segments)
        # The first word is always the dropcap; extract it from tagged list
        # so we preserve its plain string form for the DC renderer
        first_tagged = all_tagged[0] if all_tagged else None
        rest_tagged = all_tagged[1:] if len(all_tagged) > 1 else []
        # Dropcap word string (strip tags)
        actual_bold_word = tw_str(first_tagged) if first_tagged else bold_word
        rest_wds = rest_tagged
    else:
        actual_bold_word = bold_word
        # Still apply paren-shrinking even for plain (no-bold) paragraphs
        if rest_text:
            rest_wds = _segments_to_tagged_words([{'text': rest_text, 'bold': False}])
        else:
            rest_wds = []

    if not actual_bold_word:
        for lw in wrap_words_fn(rest_wds, col_width, REG_FONT, FS):
            lines.append({'type': 'body', 'words': lw, 'xr_off': 0,
                          'width': col_width, 'bold_word': None})
        return lines

    bw = Wid(vis(sanitize_for_vilna(word_without_fn(actual_bold_word))), DC_FONT, DCS)
    sp_reg = Wid(' ', REG_FONT, FS)
    indent = bw + sp_reg + DC_BODY_GAP

    # Line 1: bold word + rest
    avail1 = col_width - indent
    l1_rest, l1_w, idx = [], 0, 0
    while idx < len(rest_wds):
        ww = tw_w(rest_wds[idx], REG_FONT, FS)
        gap = sp_reg if l1_rest else 0
        if l1_w + gap + ww <= avail1 + 0.01:
            l1_rest.append(rest_wds[idx])
            l1_w += gap + ww
            idx += 1
        else: break
    lines.append({'type': 'dc1', 'words': l1_rest, 'xr_off': indent,
                  'width': avail1, 'bold_word': actual_bold_word})

    # Line 2: indented
    avail2 = col_width - indent
    l2_wds, l2_w = [], 0
    while idx < len(rest_wds):
        ww = tw_w(rest_wds[idx], REG_FONT, FS)
        gap = sp_reg if l2_wds else 0
        if l2_w + gap + ww <= avail2 + 0.01:
            l2_wds.append(rest_wds[idx])
            l2_w += gap + ww
            idx += 1
        else: break
    if l2_wds:
        lines.append({'type': 'dc2', 'words': l2_wds, 'xr_off': indent,
                      'width': avail2, 'bold_word': None})

    # Lines 3+: full width
    for lw in wrap_words_fn(rest_wds[idx:], col_width, REG_FONT, FS):
        lines.append({'type': 'body', 'words': lw, 'xr_off': 0,
                      'width': col_width, 'bold_word': None})
    return lines

# ═══════════════════════════════════════════════════════════════════════════
# DRAWING LINES WITH FOOTNOTE MARKERS
# ═══════════════════════════════════════════════════════════════════════════

def draw_word_with_fn(c, word, font, size, x, y, fn_counter_base):
    parts = split_word_fn_markers(word)
    total_w = word_display_width(word, font, size)
    cx = x
    for ptype, pval in parts:
        if ptype == 'text':
            if pval:
                tw = Wid(vis(pval), font, size)
                c.setFont(font, size)
                c.setFillColorRGB(0, 0, 0)
                c.drawString(cx - tw, y, vis(pval))
                cx -= tw
        elif ptype == 'fn':
            fn_global = fn_counter_base + pval
            marker_str = heb_num(fn_global)
            marker_vis = vis(marker_str)
            mw = Wid(marker_vis, REG_FONT, FN_SUPER_FS)
            c.setFont(REG_FONT, FN_SUPER_FS)
            c.setFillColorRGB(0, 0, 0)
            c.drawString(cx - mw, y + size * 0.45, marker_vis)
            cx -= mw
    return total_w

def draw_line_with_fn(c, words, font, size, x_right, y, col_width, last, fn_counter_base):
    """Draw a justified line of words.  Each word may be a plain string or a
    (str, font, size) tagged tuple for mixed-font (Margoliot / paren-size) lines."""
    if not words: return
    sp = Wid(' ', font, size)
    wws = [tw_w(w, font, size) for w in words]
    tot = sum(wws)
    gaps = len(words) - 1
    has_any_fn = any(tw_has_fn(w) for w in words)
    mixed = not _all_tagged_plain(words)

    def _draw_one(w, ww, cx):
        """Draw single word at position cx (right edge), using its tag font if any."""
        wf = tw_font(w, font)
        wsz = tw_size(w, size)
        wstr = tw_str(w)
        if tw_has_fn(w):
            draw_word_with_fn(c, wstr, wf, wsz, cx, y, fn_counter_base)
        else:
            c.setFont(wf, wsz)
            c.setFillColorRGB(0, 0, 0)
            draw_string_raised(c, cx - ww, y, vis(wstr), wf, wsz)

    # Don't stretch very sparse lines — if fill is under 55%, treat as a last line
    # (right-aligned / centered) to avoid ugly word-spread justification.
    natural_w = tot + gaps * sp
    if not last and col_width > 0 and natural_w / col_width < 0.55:
        last = True

    if not has_any_fn and not mixed:
        # Fast path: plain homogeneous line
        c.setFont(font, size)
        c.setFillColorRGB(0, 0, 0)
        if last:
            col_left = x_right - col_width
            cx = col_left + (col_width + tot + gaps * sp) / 2
            for w, ww in zip(words, wws):
                draw_string_raised(c, cx - ww, y, vis(w), font, size)
                cx -= ww + sp
        elif gaps == 0:
            draw_string_raised(c, x_right - wws[0], y, vis(words[0]), font, size)
        else:
            gap_w = (col_width - tot) / gaps
            cx = x_right
            for i, (w, ww) in enumerate(zip(words, wws)):
                draw_string_raised(c, cx - ww, y, vis(w), font, size)
                cx -= ww
                if i < gaps: cx -= gap_w
        return

    # General path: mixed fonts or fn markers
    if last:
        col_left = x_right - col_width
        cx = col_left + (col_width + tot + gaps * sp) / 2
        for w, ww in zip(words, wws):
            _draw_one(w, ww, cx)
            cx -= ww + sp
    elif gaps == 0:
        _draw_one(words[0], wws[0], x_right)
    else:
        gap_w = (col_width - tot) / gaps
        cx = x_right
        for i, (w, ww) in enumerate(zip(words, wws)):
            _draw_one(w, ww, cx)
            cx -= ww
            if i < gaps: cx -= gap_w


# ═══════════════════════════════════════════════════════════════════════════
# DRAWING PRECOMPUTED LINES
# ═══════════════════════════════════════════════════════════════════════════

def draw_precomputed_lines(c, line_descs, x_right, y_top, is_last_centered=True,
                           fn_counter_base=0, extra_lh=0.0):
    n = len(line_descs)
    if n == 0: return 0
    step = LH + extra_lh
    y = y_top
    for i, ld in enumerate(line_descs):
        is_last_line = (i == n - 1)
        center = is_last_line and is_last_centered
        lx = x_right - ld['xr_off']
        lw = ld['width']

        if ld['type'] == 'subhead':
            text_clean = livorna_fix_quotes(strip_nikud(ld['text']))
            text_vis = vis(text_clean)
            tw = Wid(text_vis, S.H2_FONT_NAME, SUB_FS)
            col_w = ld['width']
            n_sub_lines = ld.get('nlines', 1)
            col_left = x_right - col_w
            # Subheadings are centered within their column.
            # x_right is the right edge of the current column; col_w is the column width.
            cx_center = x_right - col_w / 2
            top_rule_y = y - SUB_RULE_TOP_PAD
            text_y = y - SUB_TEXT_TOP_PAD
            draw_generated_subhead_rule(c,
                                        col_left + col_w / 2,
                                        top_rule_y,
                                        col_w - SUB_RULE_INSET * 2,
                                        mirror=False,
                                        flip_vertical=False)
            if tw <= col_w:
                c.setFont(S.H2_FONT_NAME, SUB_FS)
                c.setFillColorRGB(0, 0, 0)
                c.drawString(cx_center - tw / 2, text_y, text_vis)
                last_baseline_y = text_y
            else:
                sub_lines = wrap_words(text_clean.split(), col_w, S.H2_FONT_NAME, SUB_FS)
                c.setFont(S.H2_FONT_NAME, SUB_FS)
                c.setFillColorRGB(0, 0, 0)
                line_y = text_y
                for li2, sub_line_words in enumerate(sub_lines):
                    lt = vis(' '.join(sub_line_words))
                    lw2 = Wid(lt, S.H2_FONT_NAME, SUB_FS)
                    c.drawString(cx_center - lw2 / 2, line_y, lt)
                    line_y -= SUB_LH
                last_baseline_y = line_y + SUB_LH
            bottom_rule_y = last_baseline_y - SUB_TEXT_BOTTOM_PAD
            draw_generated_subhead_rule(c,
                                        col_left + col_w / 2,
                                        bottom_rule_y,
                                        col_w - SUB_RULE_INSET * 2,
                                        mirror=True,
                                        flip_vertical=True)
            y -= (n_sub_lines - 1) * SUB_LH + step + SUB_AFTER_GAP + SUB_TEXT_TOP_PAD + SUB_TEXT_BOTTOM_PAD
            continue
        elif ld['type'] == 'dc1' and ld.get('bold_word'):
            bw_str = ld['bold_word']
            clean_bw = sanitize_for_vilna(word_without_fn(bw_str))
            bw_w = Wid(vis(clean_bw), DC_FONT, DCS)
            c.setFont(DC_FONT, DCS)
            c.setFillColorRGB(0, 0, 0)
            c.drawString(x_right - bw_w, y, vis(clean_bw))
            if ld['words']:
                draw_line_with_fn(c, ld['words'], REG_FONT, FS, lx, y, lw,
                                  last=center, fn_counter_base=fn_counter_base)
        else:
            c.setFillColorRGB(0, 0, 0)
            draw_line_with_fn(c, ld['words'], REG_FONT, FS, lx, y, lw,
                              last=center, fn_counter_base=fn_counter_base)
        y -= step
    return para_draw_height(line_descs, extra_lh)


# ═══════════════════════════════════════════════════════════════════════════
# FOOTNOTE RENDERING
# ═══════════════════════════════════════════════════════════════════════════

def clean_fn_text(text):
    t = text
    t = re.sub(r'\u27E8fn\d+\u27E9', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def compute_footnote_height(fn_texts, fn_start_idx):
    if not fn_texts: return 0
    total = FN_TEXT_GAP + FN_RULE_GAP + 10
    for i, fn_text in enumerate(fn_texts):
        clean = clean_fn_text(fn_text)
        fn_paras = clean.split('\n')
        for pi, fp in enumerate(fn_paras):
            fp = fp.strip()
            if not fp: continue
            if pi == 0:
                fn_num = fn_start_idx + i
                full_text = f"{heb_num(fn_num)}. " + fp
            else:
                full_text = fp
            words = full_text.split()
            fn_lines = wrap_words_first_width(words, C_W * 0.9, C_W, FN_FONT, FN_FS)
            total += len(fn_lines) * FN_LH
        if i < len(fn_texts) - 1:
            total += FN_GAP
    return total

def fn_text_to_line_dicts(fn_text, fn_num):
    clean = clean_fn_text(fn_text)
    result = []
    paras = [p.strip() for p in clean.split('\n') if p.strip()]
    all_groups = []
    for pi, fp in enumerate(paras):
        prefix = f"{heb_num(fn_num)}. " if pi == 0 else ""
        words = (prefix + fp).split()
        lines = wrap_words_first_width(words, C_W * 0.9, C_W, FN_FONT, FN_FS)
        all_groups.append(lines)
    total_groups = len(all_groups)
    for gi, lines in enumerate(all_groups):
        for li, lw in enumerate(lines):
            is_first = (gi == 0 and li == 0)
            is_last = (gi == total_groups - 1 and li == len(lines) - 1)
            result.append({'words': lw, 'fn_num': fn_num,
                           'is_first': is_first, 'is_last': is_last})
    return result

def draw_fn_line_dicts(c, fn_line_dicts, y_start):
    y = y_start
    sp = Wid(' ', FN_FONT, FN_FS)
    for ld in fn_line_dicts:
        lw = ld['words']
        is_last = ld['is_last']
        if ld['is_first']:
            num_str = heb_num(ld['fn_num'])
            num_vis = vis(num_str)
            # Bold footnote reference number
            num_w = Wid(num_vis, HEADING_BOLD_FONT, FN_FS)
            c.setFont(HEADING_BOLD_FONT, FN_FS)
            c.setFillColorRGB(0, 0, 0)
            c.drawString(C_RIGHT - num_w, y, num_vis)
            dot_w = Wid('.', HEADING_BOLD_FONT, FN_FS)
            c.drawString(C_RIGHT - num_w - dot_w, y, '.')
            margin = num_w + dot_w + sp
            rest = lw[1:] if len(lw) > 1 else []
            if rest:
                avail_w = C_W - margin
                if is_last:
                    draw_flush_right_line(c, rest, FN_FONT, FN_FS,
                                          C_RIGHT - margin, y)
                else:
                    draw_justified_line(c, rest, FN_FONT, FN_FS,
                                        C_RIGHT - margin, y, avail_w, last=False)
        else:
            if is_last:
                draw_flush_right_line(c, lw, FN_FONT, FN_FS, C_RIGHT, y)
            else:
                draw_justified_line(c, lw, FN_FONT, FN_FS, C_RIGHT, y, C_W, last=False)
        y -= FN_LH
    return y


# ═══════════════════════════════════════════════════════════════════════════
# HEADING DRAWING
# ═══════════════════════════════════════════════════════════════════════════

def draw_anaf_heading(c, label, name, y):
    """Draw ענף heading: Line 1 = flanker + label + flanker, Line 2 = name.
    Flankers are tiled horizontal curl+line rules, each ~32.5% of C_W,
    so together they span ~65% of the content width."""
    x_center = (C_LEFT + C_RIGHT) / 2
    INTER = ANAF_FS * 0.8

    label_clean = livorna_fix_quotes(strip_nikud(label))
    label_vis = vis(label_clean)
    gap = S.H1_ORNAMENT_GAP
    lw1 = Wid(label_vis, HEADING_BOLD_FONT, ANAF_FS)

    # Each flanker spans from content edge to gap before label
    flanker_w = (C_W * 0.65 - lw1) / 2 - gap
    flanker_w = max(flanker_w, 18.0)  # minimum visible width

    label_right = x_center + lw1 / 2
    label_left  = x_center - lw1 / 2

    # Right flanker: curl on RIGHT, line fades toward label
    right_x_left  = label_right + gap
    right_x_right = right_x_left + flanker_w
    # Left flanker: curl on LEFT, line fades toward label
    left_x_right  = label_left - gap
    left_x_left   = left_x_right - flanker_w

    # Clamp to content bounds
    right_x_right = min(right_x_right, C_RIGHT)
    left_x_left   = max(left_x_left,  C_LEFT)

    flanker_y = y + ANAF_FS * 0.28  # vertically center rule on label cap-height

    # Curl faces the label (inner side), line fades outward
    _draw_anaf_flanker(c, right_x_left, right_x_right, flanker_y, side='left')
    _draw_anaf_flanker(c, left_x_left,  left_x_right,  flanker_y, side='right')

    # Label in black
    c.setFont(HEADING_BOLD_FONT, ANAF_FS)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(label_left, y, label_vis)

    if name:
        name_y = y - ANAF_FS - INTER
        name_clean = livorna_fix_quotes(strip_nikud(name))
        name_vis = vis(name_clean)
        tw = Wid(name_vis, HEADING_BOLD_FONT, ANAF_NAME_FS)
        c.setFont(HEADING_BOLD_FONT, ANAF_NAME_FS)
        c.setFillColorRGB(0, 0, 0)
        c.drawString(x_center - tw / 2, name_y, name_vis)
        return ANAF_FS + INTER + ANAF_NAME_FS
    return ANAF_FS

def anaf_heading_height(has_name=True):
    INTER = ANAF_FS * 0.8
    return (ANAF_FS + INTER + ANAF_NAME_FS) if has_name else ANAF_FS

def draw_section_header(c, text, y, kind='section'):
    text_clean = livorna_fix_quotes(strip_nikud(text))
    text_vis = vis(text_clean)
    x_center = (C_LEFT + C_RIGHT) / 2
    if kind == 'sefer_title':
        fs = SEFER_TITLE_FS
        font = S.TITLE_FONT_NAME
        c.setFillColorRGB(0, 0, 0)
        max_title_w = C_W * 0.80
        words = text_clean.split()
        lines = wrap_words_balanced(words, max_title_w, font, fs)
        n_lines = len(lines)
        if n_lines == 1:
            total_h = fs
            max_lw = Wid(vis(' '.join(lines[0])), font, fs)
        else:
            total_h = n_lines * fs * 1.18
            max_lw = max(Wid(vis(' '.join(lw)), font, fs) for lw in lines)

        frame_pad_x = 54
        frame_pad_y = 22.0   # inner vertical padding top & bottom inside frame

        frame_bw = max_lw + frame_pad_x * 2
        frame_bh = total_h + frame_pad_y * 2

        # y is the TOP of the allocated block (PDF y coords, so top = highest y value)
        frame_top  = y
        frame_bot  = y - frame_bh
        frame_left = x_center - frame_bw / 2

        # Draw text FIRST, then frame on top.
        # Frame white interior is masked transparent so text shows through the frame lines.
        #
        # ── Vertical centering using measured Hebrew glyph body ──
        # FrankReal Bold body glyphs (excl. lamed/kuf/ayin outliers):
        #   body_top  = -0.500 * fs  (above baseline)
        #   body_bot  =  0.005 * fs  (at baseline, anti-alias noise)
        #   visual centre of body = 0.2475 * fs above baseline
        # Frame inner whitespace centre ≈ geometric centre + 0.6pt
        # (CY_TOP=90 vs CY_BOT=100 asymmetry is ≈1pt at typical scale)
        BODY_CENTRE_ABOVE_BL = 0.2475  # fraction of fs
        frame_cy = frame_bot + frame_bh / 2          # geometric centre of frame box
        # For multi-line: text block visual centre is midway between
        # first line body top and last line body bottom.
        # first_baseline + BODY_CENTRE*fs - (n_lines-1)*fs*1.18/2 = frame_cy
        first_baseline = frame_cy - BODY_CENTRE_ABOVE_BL * fs + (n_lines - 1) * fs * 1.18 / 2

        c.setFillColorRGB(0, 0, 0)
        text_cy = first_baseline
        for line_words in lines:
            line_vis = vis(' '.join(line_words))
            lw = Wid(line_vis, font, fs)
            c.setFont(font, fs)
            c.drawString(x_center - lw / 2, text_cy, line_vis)
            text_cy -= fs * 1.18

        # Frame drawn AFTER text; white interior masked out so text is not blocked
        draw_frame_box(c, frame_left, frame_bot, frame_bw, frame_bh, intensity=1.0)

        return frame_bh
    else:
        fs = SEC_FS
        font = HEADING_BOLD_FONT
        tw = Wid(text_vis, font, fs)
        if tw <= C_W:
            c.setFont(font, fs)
            c.setFillColorRGB(0, 0, 0)
            c.drawString(x_center - tw / 2, y, text_vis)
            return fs
        else:
            words = text_clean.split()
            lines = wrap_words(words, C_W, font, fs)
            cy = y
            for line_words in lines:
                line_vis = vis(' '.join(line_words))
                lw = Wid(line_vis, font, fs)
                c.setFont(font, fs)
                c.setFillColorRGB(0, 0, 0)
                c.drawString(x_center - lw / 2, cy, line_vis)
                cy -= fs * 1.3
            return len(lines) * fs * 1.3

def heading_height(text, kind='section', has_name=True):
    if kind == 'anaf':
        return H1_TOP_GAP + anaf_heading_height(has_name=has_name) + HEADING_AFTER_GAP
    elif kind == 'sefer_title':
        fs = SEFER_TITLE_FS
        font = S.TITLE_FONT_NAME
        text_clean = strip_nikud(text)
        max_title_w = C_W * 0.80
        lines = wrap_words_balanced(text_clean.split(), max_title_w, font, fs)
        n_lines = len(lines)
        text_h = fs if n_lines == 1 else n_lines * fs * 1.18
        return text_h + HEADING_AFTER_GAP + LH * 0.5
    elif kind == 'anaf_sub':
        fs = SEC_FS
        font = HEADING_BOLD_FONT
        text_clean = strip_nikud(text)
        tw = Wid(vis(text_clean), font, fs)
        text_h = fs if tw <= C_W else len(wrap_words(text_clean.split(), C_W, font, fs)) * fs * 1.3
        return text_h + HEADING_AFTER_GAP
    else:
        fs = SEC_FS
        font = HEADING_BOLD_FONT
        text_clean = strip_nikud(text)
        tw = Wid(vis(text_clean), font, fs)
        text_h = fs if tw <= C_W else len(wrap_words(text_clean.split(), C_W, font, fs)) * fs * 1.3
        return text_h + HEADING_AFTER_GAP


def para_draw_height(lines, extra_lh=0.0):
    if not lines: return 0.0
    step = LH + extra_lh
    height = 0.0
    for i, ld in enumerate(lines):
        if ld.get('type') == 'subhead':
            n = ld.get('nlines', 1)
            height += step + (n - 1) * SUB_LH + SUB_TEXT_TOP_PAD + SUB_TEXT_BOTTOM_PAD
            if i == len(lines) - 1:
                height += SUB_AFTER_GAP
        else:
            height += ld.get('nlines', 1) * step
    return height


def _count_fns_in_lines(lines):
    count = 0
    for ld in lines:
        for word in ld.get('words', []):
            count += len(FN_MARKER_RE.findall(tw_str(word)))
    return count


def _lines_plain_text(lines):
    parts = []
    for ld in lines:
        if ld.get('type') == 'subhead':
            txt = ld.get('text', '').strip()
            if txt:
                parts.append(txt)
            continue
        words = []
        if ld.get('type') == 'dc1' and ld.get('bold_word'):
            words.append(ld.get('bold_word'))
        words.extend(tw_str(w) for w in ld.get('words', []))
        txt = ' '.join(words).strip()
        if txt:
            parts.append(txt)
    return ' '.join(parts).strip()


def _normalize_audit_text(text):
    text = (text or '').replace('\u00a0', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _source_item_text(item):
    typ = item.get('type')
    if typ == 'heading1':
        return ' '.join(p for p in (item.get('label', ''), item.get('name', '')) if p).strip()
    if typ == 'body' and item.get('segments'):
        return ''.join(seg.get('text', '') for seg in item.get('segments', [])).strip()
    return (item.get('text') or '').strip()


# ═══════════════════════════════════════════════════════════════════════════
# COLUMN BALANCING — _col_layout and helpers
# ═══════════════════════════════════════════════════════════════════════════

def _is_subhead_frag(para_run, kind, pi, k):
    para = para_run[pi]
    if para.get('is_subhead'): return True
    if kind == 'tail':
        lines = para['lines'][k:]
        if lines and lines[0].get('type') == 'subhead': return True
    return False

def _stretchable_gaps(cd, para_run):
    """Count only gaps that can actually be stretched/shrunk for scoring.
    Sub-after gaps are excluded when locked (non-stretchable mode)."""
    count = 0
    for ci in range(len(cd) - 1):
        cur_kind, cur_pi, cur_k = cd[ci]
        nxt_kind, nxt_pi, nxt_k = cd[ci + 1]
        if _sub_after_is_locked(para_run, cur_kind, cur_pi, cur_k):
            continue
        count += 1
    return count

def _is_subhead_ending_frag(para_run, kind, pi, k):
    """Returns True if this fragment ends with a subhead line.
    Pure detection — does NOT check ALLOW_SUB_AFTER_STRETCH."""
    para = para_run[pi]
    if para.get('is_subhead'): return True
    if kind == 'head': lines = para['lines'][:k]
    elif kind == 'tail': lines = para['lines'][k:]
    else: lines = para['lines']
    return bool(lines and lines[-1].get('type') == 'subhead')

def _sub_after_is_locked(para_run, kind, pi, k):
    """Returns True if this sub-after gap should be treated as non-stretchable
    for capacity/scoring purposes.  Returns False when stretch is globally
    allowed, when the last-resort flag is set, or when this isn't a sub-after
    fragment at all."""
    if ALLOW_SUB_AFTER_STRETCH or _force_sub_after_stretchable:
        return False
    return _is_subhead_ending_frag(para_run, kind, pi, k)

def _inter_frag_gap(para_run, cd, ci, es):
    if ci + 1 >= len(cd):
        return max(MIN_PARA_GAP, PSEP + es)
    cur_kind, cur_pi, cur_k = cd[ci]
    nxt_kind, nxt_pi, nxt_k = cd[ci + 1]
    if _is_subhead_ending_frag(para_run, cur_kind, cur_pi, cur_k):
        # Gap between subheading and following paragraph.
        # When _force_sub_after_stretchable is set (last-resort fallback),
        # allow the intrinsic sub-after gap to shrink/stretch for balancing.
        # Otherwise, keep the visual sub-after space fixed.
        if _force_sub_after_stretchable:
            return max(MIN_SUB_AFTER_GAP, min(MAX_SUB_AFTER_GAP, SUB_AFTER_GAP + es))
        return SUB_AFTER_FRAG_GAP
    if _is_subhead_frag(para_run, nxt_kind, nxt_pi, nxt_k):
        # Space before a subheading stretches/shrinks with es, but must always
        # remain proportionally larger than a regular gap: floor at PSEP + half
        # of the original premium so the subheading space is always visibly wider.
        premium = SUB_BEFORE_FRAG_GAP - PSEP
        floor = max(MIN_PARA_GAP, PSEP + premium * 0.5)
        return max(floor, SUB_BEFORE_FRAG_GAP + es)
    return max(MIN_PARA_GAP, PSEP + es)

def _reflow_lines(lines, target_count):
    """Try to reflow a paragraph to produce target_count lines.
    Supports ±1 and ±2 lines (±2 done by chaining two ±1 operations)."""
    n = len(lines)
    delta = target_count - n
    if delta == 0: return list(lines)
    if abs(delta) > 2: return None
    if abs(delta) == 2:
        # Chain two ±1 reflows
        step1 = _reflow_lines(lines, n + (1 if delta > 0 else -1))
        if step1 is None: return None
        return _reflow_lines(step1, target_count)
    if n < 2: return None
    body_idxs = [i for i, ld in enumerate(lines) if ld['type'] == 'body']
    if not body_idxs: return None

    def _valid(new_lines):
        return all(len(l['words']) >= 2 for l in new_lines if l['type'] == 'body')

    if target_count == n - 1:
        # For fully-justified Hebrew text, lines are ~100% full so merging two
        # adjacent lines is impossible.  Instead re-break ALL body words into
        # (num_body - 1) lines using the same greedy algorithm as the grow branch.
        sp = Wid(' ', REG_FONT, FS)
        ref = lines[body_idxs[0]]
        col_w = ref['width']
        xr_off = ref['xr_off']
        all_words = []
        for i in body_idxs: all_words.extend(lines[i]['words'])
        num_body_target = len(body_idxs) - 1
        if num_body_target < 1: return None
        if len(all_words) < num_body_target * 2: return None
        wws = [tw_w(w) for w in all_words]

        def line_natural_w(start, end):
            k = end - start
            if k <= 0: return 0.0
            return sum(wws[start:end]) + sp * (k - 1)

        W, T = len(all_words), num_body_target
        total_natural = line_natural_w(0, W)
        # For shrink, content is denser per line — clamp fill target to 0.97
        target_fill = min(total_natural / (T * col_w), 0.97) if col_w > 0 else 0.9
        target_w = target_fill * col_w

        def cuts_valid(cs):
            for i in range(T):
                k = cs[i+1] - cs[i]
                if k < 2: return False
                if line_natural_w(cs[i], cs[i+1]) > col_w + 0.5: return False
            return True

        # Build initial cuts with a bidirectional approach:
        # Forward greedy from the front, check if last line fits.
        # If not, use reverse greedy from the back and pick the better result.
        def greedy_forward():
            cs = [0]; pos = 0
            for li in range(T - 1):
                rem = T - li
                max_end = W - (rem - 1) * 2
                end = pos + 1
                while end < max_end and line_natural_w(pos, end + 1) <= col_w + 0.01:
                    end += 1
                best_end, best_dist = end, abs(line_natural_w(pos, end) - target_w)
                for e in range(max(pos + 2, end - 3), end + 1):
                    if e > max_end or line_natural_w(pos, e) > col_w + 0.01: break
                    d = abs(line_natural_w(pos, e) - target_w)
                    if d < best_dist: best_dist = d; best_end = e
                cs.append(best_end); pos = best_end
            cs.append(W)
            return cs

        def greedy_backward():
            # Pack from the right, then reverse
            cs_r = [W]; pos = W
            for li in range(T - 1):
                rem = T - li
                min_start = rem * 2 - 2  # must leave room for remaining lines
                start = pos - 1
                while start > min_start and line_natural_w(start - 1, pos) <= col_w + 0.01:
                    start -= 1
                best_start, best_dist = start, abs(line_natural_w(start, pos) - target_w)
                for s in range(start, min(pos - 1, start + 4)):
                    if s < min_start: continue
                    if line_natural_w(s, pos) > col_w + 0.01: break
                    d = abs(line_natural_w(s, pos) - target_w)
                    if d < best_dist: best_dist = d; best_start = s
                cs_r.append(best_start); pos = best_start
            cs_r.append(0)
            return list(reversed(cs_r))

        fwd = greedy_forward()
        bwd = greedy_backward()
        # Pick the one where both ends fit
        cuts = fwd if cuts_valid(fwd) else (bwd if cuts_valid(bwd) else None)
        if cuts is None: return None

        def fills(cs): return [line_natural_w(cs[i], cs[i+1]) / col_w for i in range(T)]
        def score(cs):
            fs = fills(cs)
            pen = sum(max(0.0, 0.82 - f) * 10 for f in fs)
            mean_fill = sum(fs) / len(fs) if fs else 0
            return statistics.pstdev(fs) + pen + max(0.0, 0.92 - mean_fill) * 2.0

        best_cuts = list(cuts)
        best_score = score(best_cuts)
        improved = True
        while improved:
            improved = False
            for b in range(1, T):
                for delta in (-1, +1):
                    trial = list(best_cuts)
                    trial[b] += delta
                    if trial[b] <= trial[b-1] + 1: continue
                    if trial[b] >= trial[b+1] - 1: continue
                    if not cuts_valid(trial): continue
                    s = score(trial)
                    if s < best_score - 1e-9:
                        best_score = s; best_cuts = trial; improved = True
        cuts = best_cuts

        # Reject if any non-last body line is too sparse (< 65% fill)
        for i in range(T - 1):
            if col_w > 0 and line_natural_w(cuts[i], cuts[i+1]) / col_w < 0.65:
                return None

        new_body = []
        for i in range(T):
            new_body.append({'type': 'body', 'words': all_words[cuts[i]:cuts[i+1]],
                             'xr_off': xr_off, 'width': col_w, 'bold_word': None})

        # Rebuild lines list: replace body slots with new_body, drop the last one
        new_lines = []
        body_iter = iter(new_body)
        for i, ld in enumerate(lines):
            if i in set(body_idxs):
                nb = next(body_iter, None)
                if nb is not None:
                    new_lines.append(nb)
                # else: this was the dropped line — skip it
            else:
                new_lines.append(ld)
        if _valid(new_lines) and len(new_lines) == target_count: return new_lines
        return None

    else:  # target_count == n + 1
        sp = Wid(' ', REG_FONT, FS)
        ref = lines[body_idxs[0]]
        col_w = ref['width']
        xr_off = ref['xr_off']
        all_words = []
        for i in body_idxs: all_words.extend(lines[i]['words'])
        num_body_target = len(body_idxs) + 1
        if len(all_words) < num_body_target * 2: return None
        wws = [tw_w(w) for w in all_words]

        def line_natural_w(start, end):
            k = end - start
            if k <= 0: return 0.0
            return sum(wws[start:end]) + sp * (k - 1)

        W, T = len(all_words), num_body_target
        total_natural = line_natural_w(0, W)
        target_fill = min(total_natural / (T * col_w), 0.97) if col_w > 0 else 0.9
        target_w = target_fill * col_w
        cuts = [0]
        pos = 0
        for line_i in range(T - 1):
            remaining_lines = T - line_i
            must_leave = (remaining_lines - 1) * 2
            max_end = W - must_leave
            end = pos + 1
            while end < max_end and line_natural_w(pos, end + 1) <= col_w + 0.01:
                end += 1
            best_end = end
            best_dist = abs(line_natural_w(pos, end) - target_w)
            for e in range(max(pos + 2, end - 3), end + 1):
                if e > max_end: break
                if line_natural_w(pos, e) > col_w + 0.01: break
                d = abs(line_natural_w(pos, e) - target_w)
                if d < best_dist: best_dist = d; best_end = e
            cuts.append(best_end)
            pos = best_end
        cuts.append(W)

        def cuts_valid(cs):
            for i in range(T):
                k = cs[i+1] - cs[i]
                if k < 2: return False
                if line_natural_w(cs[i], cs[i+1]) > col_w + 0.5: return False
            return True
        if not cuts_valid(cuts): return None

        def fills(cs): return [line_natural_w(cs[i], cs[i+1]) / col_w for i in range(T)]
        def score(cs):
            fs = fills(cs)
            # Penalise lines that are too sparse (< 82% fill)
            pen = sum(max(0.0, 0.82 - f) * 10 for f in fs)
            # Prefer tighter packing: penalise low mean fill
            mean_fill = sum(fs) / len(fs) if fs else 0
            return statistics.pstdev(fs) + pen + max(0.0, 0.92 - mean_fill) * 2.0

        best_cuts = list(cuts)
        best_score = score(best_cuts)
        improved = True
        while improved:
            improved = False
            for b in range(1, T):
                for delta in (-1, +1):
                    trial = list(best_cuts)
                    trial[b] += delta
                    if trial[b] <= trial[b-1] + 1: continue
                    if trial[b] >= trial[b+1] - 1: continue
                    if not cuts_valid(trial): continue
                    s = score(trial)
                    if s < best_score - 1e-9:
                        best_score = s
                        best_cuts = trial
                        improved = True
        cuts = best_cuts

        new_body = []
        for i in range(T):
            new_body.append({'type': 'body', 'words': all_words[cuts[i]:cuts[i+1]],
                             'xr_off': xr_off, 'width': col_w, 'bold_word': None})

        # Reject reflow if any non-last body line is too sparse (< 65% fill)
        for i, nb in enumerate(new_body[:-1]):
            nw = sum(tw_w(w) for w in nb['words']) + sp * max(0, len(nb['words']) - 1)
            if col_w > 0 and nw / col_w < 0.65:
                return None

        new_lines = []
        body_iter = iter(new_body)
        body_set = set(body_idxs)
        for i, ld in enumerate(lines):
            if i in body_set:
                new_lines.append(next(body_iter))
            else:
                new_lines.append(ld)
        new_lines.insert(body_idxs[-1] + 1, next(body_iter))
        if _valid(new_lines): return new_lines
        return None


def _compute_top_pads(h1_final, h2_final, c1d=None, c2d=None, para_run=None):
    """Apply only small top-padding to absorb residual imbalance.

    This stays below a quarter line so it does not create visible column-top
    gaps, but it cleans up the remaining 1-4pt mismatches that gap balancing
    alone may leave behind.

    Exception: never pad a column whose first fragment is a subhead — the
    subhead already has SUB_RULE_TOP_PAD built in and an extra gap at the
    column top looks like a mistake."""
    def _first_is_subhead(cd):
        if not cd or para_run is None:
            return False
        kind, pi, k = cd[0]
        return _is_subhead_frag(para_run, kind, pi, k)

    diff = h1_final - h2_final
    if abs(diff) <= 0.05:
        return 0.0, 0.0
    pad = min(abs(diff), MAX_TOP_PAD)
    if pad <= 0.05:
        return 0.0, 0.0
    if diff > 0:
        # col2 is shorter — would pad col2 down; suppress if col2 starts with subhead
        if _first_is_subhead(c2d):
            return 0.0, 0.0
        return 0.0, pad
    # col1 is shorter — would pad col1 down; suppress if col1 starts with subhead
    if _first_is_subhead(c1d):
        return 0.0, 0.0
    return pad, 0.0


def _col_layout_residual(para_run, c1d, c2d, e1, e2):
    """Return the hard residual: height diff that CANNOT be absorbed by
    gap-stretching/shrinking, for an already-computed _col_layout result.
    Re-computes _effective_residual internally."""
    h1, h2 = _col_raw_heights(para_run, c1d, c2d, 0.0, 0.0, 0.0, 0.0)
    diff = abs(h1 - h2)
    if diff <= 0.05:
        return 0.0
    MAX_E_PER_GAP = LH * 1.5
    MAX_SUB_AFTER = MAX_SUB_AFTER_GAP
    MIN_SUB_AFTER = MIN_SUB_AFTER_GAP

    def stretch_cap(cd):
        total = 0.0
        for ci in range(len(cd) - 1):
            ck, cp, ck2 = cd[ci]
            if _is_subhead_ending_frag(para_run, ck, cp, ck2):
                pass  # sub-after gaps are non-stretchable — excluded
            else:
                total += MAX_E_PER_GAP
        return total

    def shrink_cap(cd):
        total = 0.0
        for ci in range(len(cd) - 1):
            ck, cp, ck2 = cd[ci]
            if _is_subhead_ending_frag(para_run, ck, cp, ck2):
                pass  # sub-after gap base is 0; shrink cap is 0
            else:
                base = _inter_frag_gap(para_run, cd, ci, 0.0)
                total += max(0.0, base - MIN_PARA_GAP)
        return total

    if h1 > h2:
        grow_cap   = stretch_cap(c2d)
        shrnk_cap  = shrink_cap(c1d)
    else:
        grow_cap   = stretch_cap(c1d)
        shrnk_cap  = shrink_cap(c2d)
    return max(0.0, diff - grow_cap - shrnk_cap)


def _col_layout(para_run, allow_trailing_subhead=False, max_height=None):
    """
    Compute balanced two-column layout for a list of para-dicts.

    **BUG FIX**: Enhanced subhead keep-with-next logic.
    A subhead is never the last fragment in a column — it must be followed
    by at least MIN_PART_LINES of the next paragraph in the same column.

    If allow_trailing_subhead is True, trailing subheads in col2 (the last
    column) are allowed.  This is used for the last group on the last page
    where no further body text follows.

    If max_height is set, prefer layouts where max(h1,h2) <= max_height
    even if they are less balanced. This is used by the paginator to fit
    content within a page budget.
    """
    global _force_sub_after_stretchable
    _force_sub_after_stretchable = False  # reset at start of each call
    _trace._col_layout_depth += 1
    _depth = _trace._col_layout_depth
    _saved_phase = _trace._phase
    _trace.set_phase(f'col_layout.d{_depth}')
    n = len(para_run)
    lc = [p['nlines'] for p in para_run]
    _para_desc = ','.join(
        ('sub' if p.get('is_subhead') else f'body({p["nlines"]})') for p in para_run)
    _trace.log('col_layout_entry',
               f'd={_depth} n={n} paras=[{_para_desc}] '
               f'allow_trailing_sub={allow_trailing_subhead} max_h={max_height}',
               depth=_depth, n_paras=n, max_height=max_height)

    def _starts_with_subhead_frag(cd):
        """True if the first fragment in cd is a subhead para or starts with a subhead line."""
        if not cd:
            return False
        kind, pi, k = cd[0]
        para = para_run[pi]
        if para.get('is_subhead'):
            return True
        if kind == 'tail':
            lines = para['lines'][k:]
        elif kind == 'head':
            lines = para['lines'][:k]
        else:
            lines = para['lines']
        return bool(lines and lines[0].get('type') == 'subhead')

    def _ph_frag(kind, pi, k):
        frag = (para_run[pi]['lines'][:k] if kind == 'head'
                else para_run[pi]['lines'][k:] if kind == 'tail'
                else para_run[pi]['lines'])
        return para_draw_height(frag, 0.0)

    def _ph_cd(cd):
        if not cd: return 0.0
        h = 0.0
        for ci, (kind, pi, k) in enumerate(cd):
            h += _ph_frag(kind, pi, k)
            if ci < len(cd) - 1:
                h += _inter_frag_gap(para_run, cd, ci, 0.0)
        return h

    def _compute_padding(c1d, c2d, h1, h2):
        """3-stage column balancing — ONLY stretch inter-paragraph gaps.
        Line-height stretch (elh) is absolute last resort and should rarely fire.

        Stage 1: Stretch gaps on the shorter column
        Stage 2: Shrink gaps on the taller column
        Stage 3: Last resort only — line-height stretch on shorter column (up to 10% of LH)

        KEY: We use per-gap max stretch that is generous enough to absorb most diffs
        through gap stretching alone, so line spacing within paragraphs stays normal.
        """
        MAX_E_PER_GAP = LH * 1.5   # reasonable max per gap (~26pt) — must match _effective_residual
        MAX_ELH = 0.0              # DISABLED: never stretch line spacing within paragraphs

        def _base_h(cd):
            h = 0.0
            for ci, (kind, pi, k) in enumerate(cd):
                frag_lines = (para_run[pi]['lines'][:k] if kind == 'head'
                              else para_run[pi]['lines'][k:] if kind == 'tail'
                              else para_run[pi]['lines'])
                h += para_draw_height(frag_lines, 0.0)
                if ci < len(cd) - 1:
                    h += _inter_frag_gap(para_run, cd, ci, 0.0)
            return h

        def _max_stretch(cd):
            """Total stretch capacity for gaps in this column descriptor.
            Sub-after gaps excluded when locked (non-stretchable mode)."""
            total = 0.0
            for ci in range(len(cd) - 1):
                cur_kind, cur_pi, cur_k = cd[ci]
                if _sub_after_is_locked(para_run, cur_kind, cur_pi, cur_k):
                    pass  # sub-after gap: no stretch counted when locked
                else:
                    total += MAX_E_PER_GAP
            return total

        def _max_shrink(cd):
            """Total shrink capacity for gaps in this column descriptor."""
            total = 0.0
            for ci in range(len(cd) - 1):
                cur_kind, cur_pi, cur_k = cd[ci]
                if _is_subhead_ending_frag(para_run, cur_kind, cur_pi, cur_k):
                    base = _inter_frag_gap(para_run, cd, ci, 0.0)
                    total += max(0.0, base - MIN_SUB_AFTER_GAP)
                else:
                    base = _inter_frag_gap(para_run, cd, ci, 0.0)
                    floor = MIN_PARA_GAP
                    total += max(0.0, base - floor)
            return total

        def _line_count(cd):
            total = 0
            for kind, pi, k in cd:
                frag_lines = (para_run[pi]['lines'][:k] if kind == 'head'
                              else para_run[pi]['lines'][k:] if kind == 'tail'
                              else para_run[pi]['lines'])
                total += sum(ld.get('nlines', 1) for ld in frag_lines)
            return total

        ah1 = _base_h(c1d) if c1d else 0.0
        ah2 = _base_h(c2d) if c2d else 0.0
        diff = ah1 - ah2  # positive = col1 taller
        if abs(diff) <= 0.05: return 0.0, 0.0, 0.0, 0.0

        def _count_sub_gaps(cd):
            """Count sub-after gaps that are currently locked (non-stretchable)."""
            return sum(1 for ci in range(len(cd) - 1)
                       if _sub_after_is_locked(para_run, cd[ci][0], cd[ci][1], cd[ci][2]))

        def _count_reg_gaps(cd):
            """Count gaps that are stretchable (regular + unlocked sub-after)."""
            return sum(1 for ci in range(len(cd) - 1)
                       if not _sub_after_is_locked(para_run, cd[ci][0], cd[ci][1], cd[ci][2]))

        def _apply_stretch(remaining, cd):
            """Compute e for this column to stretch it by up to `remaining` pt.
            Returns (e, actual_stretch_applied).
            Locked sub-after gaps are excluded from stretch capacity.
            When _force_sub_after_stretchable is set, all gaps are stretchable."""
            n_sub = _count_sub_gaps(cd)
            n_reg = _count_reg_gaps(cd)
            # Only stretchable gaps count
            reg_cap = n_reg * MAX_E_PER_GAP
            # Unlocked sub-after gaps also contribute stretch capacity
            n_unlocked_sub = sum(1 for ci in range(len(cd) - 1)
                                 if _is_subhead_ending_frag(para_run, cd[ci][0], cd[ci][1], cd[ci][2])
                                 and not _sub_after_is_locked(para_run, cd[ci][0], cd[ci][1], cd[ci][2]))
            sub_cap = n_unlocked_sub * MAX_SUB_AFTER_GAP
            total_cap = reg_cap + sub_cap
            if total_cap <= 0: return 0.0, 0.0
            used = min(remaining, total_cap)
            # Distribute e across all stretchable gaps
            n_total = n_reg  # sub-after gaps use their own formula in _inter_frag_gap
            if n_total > 0:
                e = used / n_total
            elif n_unlocked_sub > 0:
                e = used / n_unlocked_sub
            else:
                e = 0.0
            return e, used

        def _apply_shrink(remaining, cd):
            """Compute e (negative) to shrink this column by up to `remaining` pt.
            Sub-after gaps can always shrink."""
            n_sub = _count_sub_gaps(cd)
            n_reg = _count_reg_gaps(cd)
            n_total = n_sub + n_reg
            # Sub-after: can shrink from base(0) to MIN(0) = 0 capacity  
            # Regular: can shrink from PSEP to MIN_PARA_GAP
            sub_shrink_cap = sum(
                max(0.0, _inter_frag_gap(para_run, cd, ci, 0.0) - MIN_SUB_AFTER_GAP)
                for ci in range(len(cd) - 1)
                if _is_subhead_ending_frag(para_run, cd[ci][0], cd[ci][1], cd[ci][2])
            )
            reg_shrink_cap = sum(
                max(0.0, _inter_frag_gap(para_run, cd, ci, 0.0) - MIN_PARA_GAP)
                for ci in range(len(cd) - 1)
                if not _is_subhead_ending_frag(para_run, cd[ci][0], cd[ci][1], cd[ci][2])
            )
            total_cap = sub_shrink_cap + reg_shrink_cap
            if total_cap <= 0 or n_total == 0: return 0.0, 0.0
            used = min(remaining, total_cap)
            # Use regular gaps first, then sub-after if any shrink capacity
            if n_reg > 0 and reg_shrink_cap > 0:
                reg_used = min(used, reg_shrink_cap)
                e = -reg_used / n_reg
            else:
                e = -used / n_total
            return e, used

        e1, e2, elh1, elh2 = 0.0, 0.0, 0.0, 0.0
        remaining = abs(diff)

        if diff > 0:
            # col1 is taller — stretch col2 gaps, then shrink col1 gaps
            e2, used2 = _apply_stretch(remaining, c2d)
            remaining -= used2
            if remaining > 0.05:
                e1_shrink, used1 = _apply_shrink(remaining, c1d)
                e1 = e1_shrink
                remaining -= used1
            # Stage 3: line-height stretch on col2 — DISABLED (MAX_ELH=0)
        else:
            # col2 is taller — stretch col1 gaps, then shrink col2 gaps
            e1, used1 = _apply_stretch(remaining, c1d)
            remaining -= used1
            if remaining > 0.05:
                e2_shrink, used2 = _apply_shrink(remaining, c2d)
                e2 = e2_shrink
                remaining -= used2
            # Stage 3: line-height stretch on col1 — DISABLED (MAX_ELH=0)

        return e1, e2, elh1, elh2

    def _ends_with_orphaned_subhead(cd):
        """BUG FIX: Check if a column descriptor ends with a subhead
        that has no following body text in the same column.
        This is the core of the keep-with-next fix."""
        if not cd: return False
        last_kind, last_pi, last_k = cd[-1]
        para = para_run[last_pi]
        # Case 1: The entire last fragment is a subhead paragraph
        if para.get('is_subhead'):
            return True
        # Case 2: The last fragment is a 'head' or 'whole' that ends with a subhead line
        if last_kind == 'head':
            frag_lines = para['lines'][:last_k]
        elif last_kind == 'tail':
            frag_lines = para['lines'][last_k:]
        else:
            frag_lines = para['lines']
        if frag_lines and frag_lines[-1].get('type') == 'subhead':
            return True
        return False

    def _ret(c1d, c2d, e1, e2, elh1, elh2, reason=''):
        """Trace-logging wrapper for all return paths in _col_layout."""
        _h1 = _ph_cd(c1d) if c1d else 0.0
        _h2 = _ph_cd(c2d) if c2d else 0.0
        _res = abs(_h1 + e1 - _h2 - e2) if c1d and c2d else 0.0
        _trace.log('col_layout_return',
                   f'd={_depth} reason={reason} '
                   f'c1_frags={len(c1d)} c2_frags={len(c2d) if c2d else 0} '
                   f'h1={_h1:.1f} h2={_h2:.1f} e1={e1:.2f} e2={e2:.2f} '
                   f'elh1={elh1:.2f} elh2={elh2:.2f} residual={_res:.2f} '
                   f'force_sub_stretch={_force_sub_after_stretchable}',
                   depth=_depth, e1=round(e1, 3), e2=round(e2, 3),
                   residual=round(_res, 3), reason=reason)
        _trace._col_layout_depth -= 1
        _trace.set_phase(_saved_phase)
        return c1d, c2d, e1, e2, elh1, elh2

    # Single para
    if n == 1:
        nL = lc[0]
        if nL < 2:
            return _ret([('whole', 0, None)], [], 0.0, 0.0, 0.0, 0.0, 'single_para_too_short')
        if nL <= MIN_PART_LINES * 2 - 1:
            best_k = nL // 2
            c1d = [('head', 0, best_k)]
            c2d = [('tail', 0, best_k)]
            h1, h2 = best_k * LH, (nL - best_k) * LH
            e1, e2, elh1, elh2 = _compute_padding(c1d, c2d, h1, h2)
            return _ret(c1d, c2d, e1, e2, elh1, elh2, 'single_para_small')
        best_k, best_d = nL // 2, float('inf')
        for k in range(MIN_PART_LINES, nL - MIN_PART_LINES + 1):
            d = abs(k - (nL - k))
            if d < best_d or (abs(d - best_d) <= 0.01 and k > best_k):
                best_d, best_k = d, k
        c1d = [('head', 0, best_k)]
        c2d = [('tail', 0, best_k)]

        # BUG FIX: Ensure split doesn't orphan a subhead at end of col1
        if _ends_with_orphaned_subhead(c1d):
            # Move at least MIN_PART_LINES more lines into col1 to include body after subhead
            for alt_k in range(best_k + 1, nL - MIN_PART_LINES + 1):
                trial_c1d = [('head', 0, alt_k)]
                if not _ends_with_orphaned_subhead(trial_c1d):
                    best_k = alt_k
                    c1d = trial_c1d
                    c2d = [('tail', 0, best_k)]
                    break

        h1, h2 = _ph_cd(c1d), _ph_cd(c2d)
        if abs(h1 - h2) > 0.05:
            new_nL = nL + 1 if nL % 2 == 1 else nL - 1
            reflowed = _reflow_lines(para_run[0]['lines'], new_nL)
            if reflowed is None and new_nL == nL + 1:
                reflowed = _reflow_lines(para_run[0]['lines'], nL - 1)
            elif reflowed is None and new_nL == nL - 1:
                reflowed = _reflow_lines(para_run[0]['lines'], nL + 1)
            if reflowed is not None:
                rnL = len(reflowed)
                rk = rnL // 2
                if rk >= MIN_PART_LINES and (rnL - rk) >= MIN_PART_LINES:
                    para_run[0] = dict(para_run[0])
                    para_run[0]['lines'] = reflowed
                    para_run[0]['nlines'] = rnL
                    lc[0] = rnL
                    rc1d = [('head', 0, rk)]
                    rc2d = [('tail', 0, rk)]
                    # BUG FIX: Check subhead orphan after reflow
                    if not _ends_with_orphaned_subhead(rc1d):
                        rh1, rh2 = _ph_cd(rc1d), _ph_cd(rc2d)
                        re1, re2, relh1, relh2 = _compute_padding(rc1d, rc2d, rh1, rh2)
                        return _ret(rc1d, rc2d, re1, re2, relh1, relh2, 'single_para_reflow')
        e1, e2, elh1, elh2 = _compute_padding(c1d, c2d, h1, h2)
        return _ret(c1d, c2d, e1, e2, elh1, elh2, 'single_para_fallback')

    # Multiple paras: exhaustive search
    best_residual = float('inf')
    best_h1, best_h2 = 0.0, 0.0
    best_c1d, best_c2d = None, None
    best_score = (float('inf'),) * 4   # (residual, per_gap, Pk, diff)

    def _col_stretch_cap(cd):
        """Total available stretch (grow) capacity for a column descriptor.
        Locked sub-after gaps are excluded from capacity."""
        MAX_E_PER_GAP = LH * 1.5
        total = 0.0
        for ci in range(len(cd) - 1):
            cur_kind, cur_pi, cur_k = cd[ci]
            nxt_kind, nxt_pi, nxt_k = cd[ci + 1]
            if _sub_after_is_locked(para_run, cur_kind, cur_pi, cur_k):
                pass  # sub-after gap locked: no stretch counted
            elif _is_subhead_frag(para_run, nxt_kind, nxt_pi, nxt_k):
                total += MAX_E_PER_GAP
            else:
                total += MAX_E_PER_GAP
        return total

    def _col_shrink_cap(cd):
        """Total available shrink capacity for a column descriptor."""
        total = 0.0
        for ci in range(len(cd) - 1):
            cur_kind, cur_pi, cur_k = cd[ci]
            nxt_kind, nxt_pi, nxt_k = cd[ci + 1]
            if _is_subhead_ending_frag(para_run, cur_kind, cur_pi, cur_k):
                # sub-after gaps base at 0; shrink cap is 0
                continue
            base = _inter_frag_gap(para_run, cd, ci, 0.0)
            if _is_subhead_frag(para_run, nxt_kind, nxt_pi, nxt_k):
                # sub-before gap: floor preserves half premium
                premium = SUB_BEFORE_FRAG_GAP - PSEP
                floor = max(MIN_PARA_GAP, PSEP + premium * 0.5)
                total += max(0.0, base - floor)
            else:
                total += max(0.0, base - MIN_PARA_GAP)
        return total

    def _effective_residual(c1d, c2d, h1, h2):
        """Hard residual: how much height difference CANNOT be absorbed by any lever.
        Returns a scalar >= 0.  Zero means perfect balance is achievable.
        Top-padding is NOT counted as a lever — it creates ugly blank gaps.
        """
        diff = abs(h1 - h2)
        if diff <= 0.05: return 0.0
        if h1 > h2:
            # col1 taller — grow col2 (shorter) or shrink col1 (taller)
            grow_cap   = _col_stretch_cap(c2d)
            shrink_cap = _col_shrink_cap(c1d)
        else:
            # col2 taller — grow col1 (shorter) or shrink col2 (taller)
            grow_cap   = _col_stretch_cap(c1d)
            shrink_cap = _col_shrink_cap(c2d)
        return max(0.0, diff - grow_cap - shrink_cap)

    def _split_score(c1d, c2d, h1, h2):
        """Full scoring tuple — lexicographically smaller is better.

        Implements the GPT-5.4 recommended score:
          (residual, max_per_gap_stretch, P(k), height_diff)

        Where P(k) = -(S_shorter - S_taller) * sign(H_taller - H_shorter)
        = penalises splits where the SHORTER column has LESS stretch than the
          taller column.  Negative means the shorter col is gap-rich (preferred).

        Per the user requirement: the column WITH more stretchable gaps should
        be the SHORTER one so it stretches UP to match.
        """
        diff = abs(h1 - h2)
        res  = _effective_residual(c1d, c2d, h1, h2)

        # per-gap stretch on the shorter column (lower = less ugly gap distribution)
        g1 = _stretchable_gaps(c1d, para_run)
        g2 = _stretchable_gaps(c2d, para_run)
        if diff <= 0.05:
            per_gap = 0.0
        elif h1 > h2:
            per_gap = diff / g2 if g2 > 0 else 9999.0
        else:
            per_gap = diff / g1 if g1 > 0 else 9999.0

        # P(k): prefer shorter column to have MORE stretch (negative = better)
        # S_1, S_2 = total stretch capacity of each column
        S1 = _col_stretch_cap(c1d)
        S2 = _col_stretch_cap(c2d)
        if diff <= 0.05:
            Pk = 0.0
        elif h1 > h2:
            # col2 is shorter — want S2 > S1 → -(S2-S1) is negative (good)
            Pk = -(S2 - S1)
        else:
            # col1 is shorter — want S1 > S2 → -(S1-S2) is negative (good)
            Pk = -(S1 - S2)

        return (res, per_gap, Pk, diff)

    def _is_better(c1d, c2d, h1, h2):
        nonlocal best_residual, best_h1, best_h2, best_score
        new_score = _split_score(c1d, c2d, h1, h2)
        if new_score < best_score:
            best_score = new_score
            return True
        return False

    # 1. Whole-para splits
    for sp in range(1, n):
        # BUG FIX: Never leave a subhead as the last thing in col1
        if para_run[sp - 1].get('is_subhead'):
            continue
        c1d = [('whole', i, None) for i in range(sp)]
        c2d = [('whole', i, None) for i in range(sp, n)]
        # BUG FIX: Check NEITHER column ends with orphaned subhead
        if _ends_with_orphaned_subhead(c1d):
            continue
        if not allow_trailing_subhead and _ends_with_orphaned_subhead(c2d):
            continue
        h1, h2 = _ph_cd(c1d), _ph_cd(c2d)
        if _is_better(c1d, c2d, h1, h2):
            best_residual = best_score[0]
            best_h1, best_h2 = h1, h2
            best_c1d, best_c2d = c1d, c2d

    # 2. Within-para splits
    for pi in range(n):
        if para_run[pi].get('is_subhead'): continue
        nL = lc[pi]
        if nL < MIN_PART_LINES * 2: continue
        for k in range(MIN_PART_LINES, nL - MIN_PART_LINES + 1):
            c1d = ([('whole', i, None) for i in range(pi)] + [('head', pi, k)])
            c2d = ([('tail', pi, k)] + [('whole', i, None) for i in range(pi + 1, n)])
            # BUG FIX: Check NEITHER column ends with orphaned subhead
            if _ends_with_orphaned_subhead(c1d):
                continue
            if not allow_trailing_subhead and _ends_with_orphaned_subhead(c2d):
                continue
            h1 = _ph_cd(c1d)
            h2 = _ph_cd(c2d)
            if _is_better(c1d, c2d, h1, h2):
                best_residual = best_score[0]
                best_h1, best_h2 = h1, h2
                best_c1d, best_c2d = c1d, c2d

     # 3. Subhead-bridging splits: col1 = whole paras [0..sp-1],
    #    col2 = whole paras [sp..n-1] — same as section 1 but allows col2_prefix
    #    to contain a subhead (i.e. col2 starts or contains a subhead followed by
    #    body text).  Section 1 skips sp where para[sp-1] is a subhead; this
    #    section handles splits where the subhead sits *inside* col2's prefix,
    #    not at its end, so it is not orphaned.
    last_pi = n - 1
    if not para_run[last_pi].get('is_subhead'):
        for sp in range(1, n):
            if para_run[sp - 1].get('is_subhead'):
                continue
            col2_prefix = list(range(sp, n - 1))
            # col2 prefix is allowed to contain subheads as long as they are
            # followed by body text within col2 (not orphaned at the end).
            # _ends_with_orphaned_subhead handles the end-check below.
            c1d = [('whole', i, None) for i in range(sp)]
            c2d = ([('whole', i, None) for i in col2_prefix]
                   + [('whole', last_pi, None)])
            if _ends_with_orphaned_subhead(c1d):
                continue
            if _ends_with_orphaned_subhead(c2d):
                continue
            h1 = _ph_cd(c1d)
            h2 = _ph_cd(c2d)
            if _is_better(c1d, c2d, h1, h2):
                best_residual = best_score[0]
                best_h1, best_h2 = h1, h2
                best_c1d, best_c2d = c1d, c2d

    if best_c1d is None:
        # ── Special handling: trailing keep-with-next subhead ──
        # If the last paragraph is a subhead with keep_with_next, no valid split
        # exists because col2 always ends with an orphaned subhead.
        # Fix: compute the best layout for paras [0..n-2] (excluding the trailing
        # subhead), then append the subhead to whichever column is shorter.
        # This avoids the degenerate fallback where col1 gets everything and
        # col2 gets only the lone subhead.
        trailing_is_subhead = (n >= 2 and para_run[n - 1].get('is_subhead'))
        if trailing_is_subhead:

            # Recurse on the prefix (all paras except the trailing subhead)
            prefix = para_run[:n - 1]
            if len(prefix) == 1:
                # Single body paragraph — split it across columns, append subhead to col2
                nL_body = prefix[0]['nlines']
                if nL_body >= MIN_PART_LINES * 2:
                    best_k = nL_body // 2
                    for k in range(MIN_PART_LINES, nL_body - MIN_PART_LINES + 1):
                        d = abs(k - (nL_body - k))
                        if d < abs(best_k - (nL_body - best_k)):
                            best_k = k
                    best_c1d = [('head', 0, best_k)]
                    best_c2d = [('tail', 0, best_k), ('whole', n - 1, None)]
                else:
                    best_c1d = [('whole', 0, None)]
                    best_c2d = [('whole', n - 1, None)]
            else:
                pc1, pc2, pe1, pe2, pelh1, pelh2 = _col_layout(prefix)
                # Append trailing subhead to col2
                best_c1d = list(pc1)
                best_c2d = list(pc2) + [('whole', n - 1, None)]
            best_h1, best_h2 = _ph_cd(best_c1d), _ph_cd(best_c2d)
        else:
            fallback_sp = 1
            for sp in range(1, n):
                if not para_run[sp - 1].get('is_subhead'):
                    fallback_sp = sp
                    break
            best_c1d = [('whole', i, None) for i in range(fallback_sp)]
            best_c2d = [('whole', i, None) for i in range(fallback_sp, n)]
            best_h1, best_h2 = _ph_cd(best_c1d), _ph_cd(best_c2d)

    e1, e2, elh1, elh2 = _compute_padding(best_c1d, best_c2d, best_h1, best_h2)

    # Reflow pass
    def _final_h(cd, es, elh, line_counts=None, e_top=0.0):
        h = e_top
        for ci, (kind, pi, k) in enumerate(cd):
            frag_lines = (para_run[pi]['lines'][:k] if kind == 'head'
                          else para_run[pi]['lines'][k:] if kind == 'tail'
                          else para_run[pi]['lines'])
            h += para_draw_height(frag_lines, elh)
            if ci < len(cd) - 1:
                h += _inter_frag_gap(para_run, cd, ci, es)
        return h

    fh1 = _final_h(best_c1d, e1, elh1)
    fh2 = _final_h(best_c2d, e2, elh2)
    residual = abs(fh1 - fh2)

    # Only try reflow if gap stretching left a significant residual.
    # LH * 0.25 (~4.4pt) means gap stretching truly couldn't handle the imbalance.
    REFLOW_THRESHOLD = LH * 0.25
    # Reflow must improve residual by at least this much to be worth
    # the justification degradation from changing paragraph line counts.
    # Lowered from LH*0.3 to LH*0.05 so reflow is accepted for any meaningful gain.
    REFLOW_MIN_IMPROVEMENT = LH * 0.05

    if residual > REFLOW_THRESHOLD:
        def _try_all_splits_reflow():
            """Find best split after a tentative reflow, using full _split_score for comparison.
            Returns (score_tuple, c1d, c2d, e1, e2, elh1, elh2) or None."""
            loc_best = None
            # whole-para splits
            for sp in range(1, n):
                if para_run[sp - 1].get('is_subhead'): continue
                rc1d = [('whole', i, None) for i in range(sp)]
                rc2d = [('whole', i, None) for i in range(sp, n)]
                if _ends_with_orphaned_subhead(rc1d): continue
                if not allow_trailing_subhead and _ends_with_orphaned_subhead(rc2d): continue
                rh1, rh2 = _ph_cd(rc1d), _ph_cd(rc2d)
                r_score = _split_score(rc1d, rc2d, rh1, rh2)
                # Only accept if it improves on current best by meaningful margin
                if r_score[0] < residual - REFLOW_MIN_IMPROVEMENT:
                    if loc_best is None or r_score < loc_best[0]:
                        re1, re2, relh1, relh2 = _compute_padding(rc1d, rc2d, rh1, rh2)
                        loc_best = (r_score, list(rc1d), list(rc2d), re1, re2, relh1, relh2)
            # within-para splits
            for pi2 in range(n):
                if para_run[pi2].get('is_subhead'): continue
                rnL2 = para_run[pi2]['nlines']
                if rnL2 < MIN_PART_LINES * 2: continue
                for rk in range(MIN_PART_LINES, rnL2 - MIN_PART_LINES + 1):
                    rc1d = ([('whole', i, None) for i in range(pi2)] + [('head', pi2, rk)])
                    rc2d = ([('tail', pi2, rk)] + [('whole', i, None) for i in range(pi2 + 1, n)])
                    if _ends_with_orphaned_subhead(rc1d): continue
                    if not allow_trailing_subhead and _ends_with_orphaned_subhead(rc2d): continue
                    rh1, rh2 = _ph_cd(rc1d), _ph_cd(rc2d)
                    r_score = _split_score(rc1d, rc2d, rh1, rh2)
                    if r_score[0] < residual - REFLOW_MIN_IMPROVEMENT:
                        if loc_best is None or r_score < loc_best[0]:
                            re1, re2, relh1, relh2 = _compute_padding(rc1d, rc2d, rh1, rh2)
                            loc_best = (r_score, list(rc1d), list(rc2d), re1, re2, relh1, relh2)
            return loc_best

        best_reflow = None
        for reflow_pi in range(n):
            if para_run[reflow_pi].get('is_subhead'): continue
            orig_lines = para_run[reflow_pi]['lines']
            orig_nL = para_run[reflow_pi]['nlines']
            # PERF GUARD: skip reflow on very long paragraphs (expensive _try_all_splits)
            if orig_nL > 25: continue
            # FIX: try ±1 and ±2 lines (±2 handled by chaining in _reflow_lines)
            for delta in (-2, -1, +1, +2):
                target = orig_nL + delta
                if target < 1: continue
                reflowed = _reflow_lines(orig_lines, target)
                if reflowed is None: continue
                rnL = len(reflowed)
                saved_para = para_run[reflow_pi]
                para_run[reflow_pi] = dict(saved_para, lines=reflowed, nlines=rnL)
                new_lc = list(lc)
                new_lc[reflow_pi] = rnL
                hit = _try_all_splits_reflow()
                if hit is not None:
                    r_score, rc1d, rc2d, re1, re2, relh1, relh2 = hit
                    if best_reflow is None or r_score < best_reflow[0]:
                        best_reflow = (r_score, [(reflow_pi, reflowed, rnL)],
                                       rc1d, rc2d, re1, re2, relh1, relh2)
                para_run[reflow_pi] = saved_para

        # Dual-paragraph reflows — run when single-para didn't get below REFLOW_THRESHOLD
        # PERF GUARD: skip dual-para reflow when para_run is large or individual paras
        # are long — the O(n² × lines²) cost makes the build hang on 12pt body text.
        _dual_reflow_eligible = (
            n <= 6 and
            all(p['nlines'] <= 18 for p in para_run if not p.get('is_subhead'))
        )
        if _dual_reflow_eligible and (best_reflow is None or best_reflow[0][0] > REFLOW_THRESHOLD):
            for pi_a in range(n):
                if para_run[pi_a].get('is_subhead'): continue
                orig_a = para_run[pi_a]['lines']
                nL_a = para_run[pi_a]['nlines']
                for da in (-1, +1):
                    ta = nL_a + da
                    if ta < 1: continue
                    ra = _reflow_lines(orig_a, ta)
                    if ra is None: continue
                    rnL_a = len(ra)
                    saved_a = para_run[pi_a]
                    para_run[pi_a] = dict(saved_a, lines=ra, nlines=rnL_a)
                    for pi_b in range(pi_a + 1, n):
                        if para_run[pi_b].get('is_subhead'): continue
                        orig_b = para_run[pi_b]['lines']
                        nL_b = para_run[pi_b]['nlines']
                        for db in (-1, +1):
                            tb = nL_b + db
                            if tb < 1: continue
                            rb = _reflow_lines(orig_b, tb)
                            if rb is None: continue
                            rnL_b = len(rb)
                            saved_b = para_run[pi_b]
                            para_run[pi_b] = dict(saved_b, lines=rb, nlines=rnL_b)
                            new_lc = list(lc)
                            new_lc[pi_a] = rnL_a
                            new_lc[pi_b] = rnL_b
                            hit = _try_all_splits_reflow()
                            if hit is not None:
                                r_score, rc1d, rc2d, re1, re2, relh1, relh2 = hit
                                if best_reflow is None or r_score < best_reflow[0]:
                                    best_reflow = (r_score, [(pi_a, ra, rnL_a), (pi_b, rb, rnL_b)],
                                                   rc1d, rc2d, re1, re2, relh1, relh2)
                            para_run[pi_b] = saved_b
                    para_run[pi_a] = saved_a

        if best_reflow is not None and best_reflow[0] < best_score:
            r_val, reflows, rc1d, rc2d, re1, re2, relh1, relh2 = best_reflow
            for rpi, rlines, rnl in reflows:
                para_run[rpi] = dict(para_run[rpi])
                para_run[rpi]['lines'] = rlines
                para_run[rpi]['nlines'] = rnl
                lc[rpi] = rnl
            best_c1d, best_c2d = rc1d, rc2d
            e1, e2, elh1, elh2 = re1, re2, relh1, relh2

    # ── Height-constrained fallback ──
    # If max_height is set and the balance-optimal result exceeds it,
    # search for the best within-para split where max(h1,h2) <= max_height.
    # This allows the paginator to fit more content on a page even if the
    # columns are less balanced.
    if max_height is not None and best_c1d is not None:
        cur_max = max(_ph_cd(best_c1d), _ph_cd(best_c2d))
        if cur_max > max_height + 0.01:
            # Try all within-para splits with different k values
            hc_best = None  # (max_h, residual, c1d, c2d)
            for pi in range(n):
                if para_run[pi].get('is_subhead'):
                    continue
                nL_pi = lc[pi]
                if nL_pi < MIN_PART_LINES * 2:
                    continue
                for k in range(MIN_PART_LINES, nL_pi - MIN_PART_LINES + 1):
                    hc_c1d = ([('whole', i, None) for i in range(pi)]
                              + [('head', pi, k)])
                    hc_c2d = ([('tail', pi, k)]
                              + [('whole', i, None) for i in range(pi + 1, n)])
                    if _ends_with_orphaned_subhead(hc_c1d):
                        continue
                    if not allow_trailing_subhead and _ends_with_orphaned_subhead(hc_c2d):
                        continue
                    hc_h1 = _ph_cd(hc_c1d)
                    hc_h2 = _ph_cd(hc_c2d)
                    hc_max = max(hc_h1, hc_h2)
                    if hc_max > max_height + 0.01:
                        continue
                    hc_res = abs(hc_h1 - hc_h2)
                    # Pick the split that fits AND has the best balance
                    # (and among equal balance, pick the one with most content = highest max_h)
                    if hc_best is None or hc_res < hc_best[0] - 0.5 or (abs(hc_res - hc_best[0]) <= 0.5 and hc_max > hc_best[1]):
                        hc_best = (hc_res, hc_max, hc_c1d, hc_c2d, hc_h1, hc_h2)
            if hc_best is not None:
                _, _, best_c1d, best_c2d, best_h1, best_h2 = hc_best
                e1, e2, elh1, elh2 = _compute_padding(best_c1d, best_c2d, best_h1, best_h2)

    # ── Last-resort: allow sub-after stretch if balance is still poor ──
    # When ALLOW_SUB_AFTER_STRETCH is False, heading→paragraph gaps are locked.
    # If the best split still has significant imbalance after reflow + gap opt,
    # unlock sub-after gaps and re-run _compute_padding to absorb the residual.
    # The flag stays set so that _col_height / _inter_frag_gap / draw code
    # use the same stretchable semantics. It is reset at the start of the
    # next _col_layout call.
    if not ALLOW_SUB_AFTER_STRETCH and best_c1d is not None and best_c2d:
        lr_h1 = _final_h(best_c1d, e1, elh1) if best_c1d else 0.0
        lr_h2 = _final_h(best_c2d, e2, elh2) if best_c2d else 0.0
        lr_residual = abs(lr_h1 - lr_h2)
        if lr_residual > 5.0:  # only unlock sub-after spacing for visible imbalance
            _force_sub_after_stretchable = True
            lr_raw_h1 = _ph_cd(best_c1d)
            lr_raw_h2 = _ph_cd(best_c2d)
            e1, e2, elh1, elh2 = _compute_padding(best_c1d, best_c2d, lr_raw_h1, lr_raw_h2)

    return _ret(best_c1d, best_c2d, e1, e2, elh1, elh2, 'multi_para_final')


def _col_height(para_run, c1d, c2d, e1, e2, elh1=0.0, elh2=0.0, e_top1=0.0, e_top2=0.0):
    def mh(cd, es, elh, e_top=0.0):
        h = e_top
        for ci, (kind, pi, k) in enumerate(cd):
            frag_lines = (para_run[pi]['lines'][:k] if kind == 'head'
                          else para_run[pi]['lines'][k:] if kind == 'tail'
                          else para_run[pi]['lines'])
            h += para_draw_height(frag_lines, elh)
            if ci < len(cd) - 1:
                h += _inter_frag_gap(para_run, cd, ci, es)
        return h
    return max(mh(c1d, e1, elh1, e_top1), mh(c2d, e2, elh2, e_top2)) if (c1d or c2d) else 0.0


def _col_raw_heights(para_run, c1d, c2d, e1, e2, elh1=0.0, elh2=0.0):
    """Return (h1, h2) — the raw height of each column before top-padding."""
    def mh(cd, es, elh):
        h = 0.0
        for ci, (kind, pi, k) in enumerate(cd):
            frag_lines = (para_run[pi]['lines'][:k] if kind == 'head'
                          else para_run[pi]['lines'][k:] if kind == 'tail'
                          else para_run[pi]['lines'])
            h += para_draw_height(frag_lines, elh)
            if ci < len(cd) - 1:
                h += _inter_frag_gap(para_run, cd, ci, es)
        return h
    h1 = mh(c1d, e1, elh1) if c1d else 0.0
    h2 = mh(c2d, e2, elh2) if c2d else 0.0
    return h1, h2


def _draw_col_pair(c, para_run, c1d, c2d, e1, e2, y_top, fn_counter_before,
                   elh1=0.0, elh2=0.0, e_top1=0.0, e_top2=0.0, page_num=0,
                   log_sink=None):
    if not para_run: return 0.0

    def _count_fns_in_col(cd):
        total = 0
        for (kind, pi, k) in cd:
            para = para_run[pi]
            lines = (para['lines'] if kind == 'whole'
                     else para['lines'][:k] if kind == 'head'
                     else para['lines'][k:])
            total += _count_fns_in_lines(lines)
        return total

    def draw_col(cd, x_right, es, elh, fn_base, col_idx, e_top=0.0):
        y = y_top - e_top
        for ci, (kind, pi, k) in enumerate(cd):
            para = para_run[pi]
            para_ends = para.get('is_para_end', True)
            frag_lines = (para['lines'] if kind == 'whole'
                          else para['lines'][:k] if kind == 'head'
                          else para['lines'][k:])
            if kind == 'whole':
                ht = draw_precomputed_lines(c, para['lines'], x_right, y,
                                            is_last_centered=para_ends,
                                            fn_counter_base=fn_base, extra_lh=elh)
            elif kind == 'head':
                ht = draw_precomputed_lines(c, para['lines'][:k], x_right, y,
                                            is_last_centered=False,
                                            fn_counter_base=fn_base, extra_lh=elh)
            else:
                ht = draw_precomputed_lines(c, para['lines'][k:], x_right, y,
                                            is_last_centered=para_ends,
                                            fn_counter_base=fn_base, extra_lh=elh)
            # Layout log entry for this fragment
            if log_sink is not None:
                first_line = frag_lines[0] if frag_lines else {}
                txt_preview = ''
                rendered_words = []
                for ld in frag_lines:
                    if ld.get('type') == 'subhead':
                        sub_txt = ld.get('text', '').strip()
                        if sub_txt:
                            rendered_words.append(sub_txt)
                            if not txt_preview:
                                txt_preview = sub_txt
                        continue
                    if ld.get('type') == 'dc1' and ld.get('bold_word'):
                        rendered_words.append(ld.get('bold_word'))
                        if not txt_preview:
                            txt_preview = ld.get('bold_word')
                    words = ld.get('words', [])
                    if words:
                        rendered_words.extend(tw_str(w) for w in words)
                        if not txt_preview:
                            txt_preview = ' '.join(str(w) for w in words[:6])
                rendered_text = ' '.join(rendered_words).strip()
                log_sink.append({
                    'page':        page_num,
                    'display_pg':  max(1, page_num - 4),
                    'col':         col_idx,
                    'y_top':       round(y, 2),
                    'height':      round(ht, 2),
                    'y_bot':       round(y - ht, 2),
                    'el_idx':      para.get('orig_el_idx', ''),
                    'source_idxs': list(para.get('source_idxs', [])),
                    'kind':        first_line.get('type', kind),
                    'frag':        kind,
                    'n_lines':     len(frag_lines),
                    'rendered_text': rendered_text,
                    'text':        txt_preview[:80],
                })
            y -= ht
            if ci < len(cd) - 1:
                y -= _inter_frag_gap(para_run, cd, ci, es)
        return (y_top - e_top) - y

    fns_in_col1 = _count_fns_in_col(c1d)
    h1 = draw_col(c1d, COL1_RIGHT, e1, elh1, fn_counter_before, 1, e_top=e_top1)
    h2 = draw_col(c2d, COL2_RIGHT, e2, elh2, fn_counter_before + fns_in_col1, 2, e_top=e_top2)
    return max(h1 + e_top1, h2 + e_top2)


# ═══════════════════════════════════════════════════════════════════════════
# PAGE LAYOUT ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class PageLayout:

    def __init__(self, items, out_pdf):
        self.items = items
        self.out_pdf = out_pdf
        self.cv = rl_canvas.Canvas(out_pdf, pagesize=(PAGE_W, PAGE_H))
        self.page_num = 0
        self.fn_counter = 0
        self.current_anaf_label = ""
        self.current_anaf_name = ""
        self.anaf_page_map = {}   # label -> page_num (content page, before TOC offset)
        self._layout_log = []    # list of dicts, one per rendered element
        self._page_plans = {}
        self._qa_issues = []
        # Reset trace log for this build
        _trace.clear()
        _trace.log('build_start', f'items={len(items)} output={out_pdf}')

    def _record_issue(self, kind, detail, **kw):
        issue = {'kind': kind, 'detail': detail}
        issue.update(kw)
        self._qa_issues.append(issue)

    def _layout_log_text(self, entry):
        txt = entry.get('text', '')
        return txt if isinstance(txt, str) else str(txt)

    def _build_qa_report(self, elements, plan_by_page):
        global _force_sub_after_stretchable
        rendered_by_el = defaultdict(list)
        merged_expected_by_source = {}
        for entry in self._layout_log:
            source_idxs = entry.get('source_idxs') or []
            if len(source_idxs) > 1:
                merged_expected = _normalize_audit_text(' '.join(
                    _source_item_text(self.items[src_idx]) for src_idx in source_idxs
                ))
                for src_idx in source_idxs:
                    merged_expected_by_source[int(src_idx)] = merged_expected
            if source_idxs:
                for src_idx in source_idxs:
                    rendered_by_el[int(src_idx)].append(entry)
                continue
            el_idx = entry.get('el_idx', '')
            if el_idx == '':
                continue
            rendered_by_el[int(el_idx)].append(entry)

        coverage = []
        for idx, item in enumerate(self.items):
            entries = rendered_by_el.get(idx, [])
            if item.get('type') in ('body', 'heading2'):
                source_text = merged_expected_by_source.get(
                    idx, _normalize_audit_text(_source_item_text(item))
                )
                rendered_text = _normalize_audit_text(' '.join(e.get('rendered_text', '') for e in entries))
                rendered_count = len(entries)
                status = 'ok'
                if rendered_count == 0:
                    status = 'missing'
                    self._record_issue('missing_render',
                                       f'el[{idx}] {item.get("type")} was not rendered',
                                       el_idx=idx)
                elif source_text and rendered_text and source_text != rendered_text:
                    status = 'text_mismatch'
                    self._record_issue('text_mismatch',
                                       f'el[{idx}] {item.get("type")} text mismatch between source and rendered log',
                                       el_idx=idx)
                coverage.append({
                    'el_idx': idx,
                    'kind': item.get('type'),
                    'expected_lines': '',
                    'rendered_lines': sum(int(e.get('n_lines', 0) or 0) for e in entries),
                    'source_text': source_text[:400],
                    'rendered_text': rendered_text[:400],
                    'status': status,
                })
            else:
                rendered_count = len(entries)
                source_text = merged_expected_by_source.get(
                    idx, _normalize_audit_text(_source_item_text(item))
                )
                rendered_text = _normalize_audit_text(' '.join(e.get('rendered_text', '') for e in entries))
                status = 'ok'
                if rendered_count == 0:
                    status = 'missing'
                    self._record_issue('missing_render',
                                       f'el[{idx}] {item.get("type")} was not rendered',
                                       el_idx=idx)
                elif rendered_count > 1:
                    status = 'duplicate'
                    self._record_issue('duplicate_render',
                                       f'el[{idx}] {item.get("type")} rendered {rendered_count} times',
                                       el_idx=idx, rendered_count=rendered_count)
                elif source_text and rendered_text and source_text != rendered_text:
                    status = 'text_mismatch'
                    self._record_issue('text_mismatch',
                                       f'el[{idx}] {item.get("type")} text mismatch between source and rendered log',
                                       el_idx=idx)
                coverage.append({
                    'el_idx': idx,
                    'kind': item.get('type'),
                    'expected_lines': 1,
                    'rendered_lines': rendered_count,
                    'source_text': source_text[:400],
                    'rendered_text': rendered_text[:400],
                    'status': status,
                })

        page_balances = []
        last_content_page = max(plan_by_page.keys()) if plan_by_page else 0
        for page_num, blocks in sorted(plan_by_page.items()):
            col_pairs = [b for b in blocks if b.get('kind') == 'col_pair']
            if len(col_pairs) > 1:
                self._record_issue('multiple_col_pairs',
                                   f'page {page_num} has {len(col_pairs)} col_pair blocks',
                                   page=page_num, count=len(col_pairs))
            for bi, blk in enumerate(col_pairs):
                _saved_force_sub = _force_sub_after_stretchable
                _force_sub_after_stretchable = bool(blk.get('sub_after_last_resort'))
                try:
                    h1, h2 = _col_raw_heights(blk['paras'], blk['c1d'], blk['c2d'],
                                              blk.get('e1', 0.0), blk.get('e2', 0.0),
                                              blk.get('elh1', 0.0), blk.get('elh2', 0.0))
                finally:
                    _force_sub_after_stretchable = _saved_force_sub
                total1 = h1 + blk.get('e_top1', 0.0)
                total2 = h2 + blk.get('e_top2', 0.0)
                diff = abs(total1 - total2)
                rec = {
                    'page': page_num,
                    'display_pg': max(1, page_num - 4),
                    'block_index': bi,
                    'h1': round(total1, 3),
                    'h2': round(total2, 3),
                    'diff': round(diff, 3),
                    'e1': round(blk.get('e1', 0.0), 3),
                    'e2': round(blk.get('e2', 0.0), 3),
                    'sub_after_last_resort': bool(
                        blk.get('sub_after_last_resort')
                    ),
                }
                page_balances.append(rec)
                # Last page of the book naturally ends short — don't flag it
                is_last_page = (page_num == last_content_page)
                if diff > 5.0 and not is_last_page:
                    self._record_issue('page_imbalance',
                                       f'page {page_num} diff={diff:.3f}pt',
                                       page=page_num, display_pg=max(1, page_num - 4), diff=round(diff, 3))

        summary = {
            'total_issues': len(self._qa_issues),
            'issues': list(self._qa_issues),
            'coverage': coverage,
            'page_balances': page_balances,
        }
        return summary


    def build(self):
        global MAMAR_NAME
        # Preprocess: a heading2 immediately after a heading1 is the anaf name,
        # not an in-column subhead. Keep the source index so QA can still mark
        # both source items as rendered by the single anaf heading block.
        items_to_render = []
        for i, item in enumerate(self.items):
            cur = dict(item)
            cur['_source_idxs'] = [i]
            if (item['type'] == 'heading2'
                    and i > 0
                    and self.items[i - 1]['type'] == 'heading1'):
                prev = items_to_render[-1] if items_to_render else None
                if prev is not None and prev.get('type') == 'heading1' and not prev.get('name'):
                    prev['name'] = item['text']
                    prev.setdefault('_source_idxs', []).append(i)
                    continue
            items_to_render.append(cur)

        # Extract mamar name from sefer_title item
        for item in items_to_render:
            if item['type'] == 'sefer_title':
                MAMAR_NAME = item['text']
                break

        elements = []
        last_heading_kind = None

        for item in items_to_render:
            if item['type'] == 'sefer_title':
                elements.append({'kind': 'sefer_title', 'text': item['text'],
                                 'source_idxs': list(item.get('_source_idxs', []))})
                last_heading_kind = 'sefer_title'

            elif item['type'] == 'heading1':
                elements.append({
                    'kind': 'anaf',
                    'label': item['label'],
                    'name': item.get('name', ''),
                    'source_idxs': list(item.get('_source_idxs', [])),
                })
                last_heading_kind = 'anaf'

            elif item['type'] == 'heading2':
                # heading2 = in-column sub-heading (flows inside column)
                text = item['text']
                # Apply livorna_fix_quotes before measuring so nlines matches the draw path exactly
                text_clean = livorna_fix_quotes(strip_nikud(text))
                text_vis = vis(text_clean)
                sub_tw = Wid(text_vis, S.H2_FONT_NAME, SUB_FS)
                if sub_tw <= COL_W:
                    n_sub_lines = 1
                else:
                    sub_wrapped = wrap_words(text_clean.split(), COL_W, S.H2_FONT_NAME, SUB_FS)
                    n_sub_lines = max(1, len(sub_wrapped))
                ld = {
                    'type': 'subhead', 'text': text, 'words': [],
                    'xr_off': 0, 'width': COL_W, 'nlines': n_sub_lines,
                }
                elements.append({
                    'kind': 'para', 'lines': [ld], 'footnotes': [],
                    'is_subhead': True, 'nlines': n_sub_lines,
                    'source_idxs': list(item.get('_source_idxs', [])),
                })
                last_heading_kind = None

            elif item['type'] == 'body':
                segments = item.get('segments')
                if segments:
                    # Rich text path: use tagged words
                    all_tagged = _segments_to_tagged_words(segments)
                    if not all_tagged: continue
                    bold_word_str = tw_str(all_tagged[0])
                    lds = precompute_para(bold_word_str, '', COL_W, segments=segments)
                else:
                    # Split on regular whitespace only (not NBSP), so NBSP-joined
                    # tokens (e.g. math expressions) are preserved as single words.
                    raw = item['text'].strip()
                    words = re.split(r'[ \t\n\r]+', raw)
                    words = [w for w in words if w]
                    if not words: continue
                    # Reconstruct rest_text from original, preserving NBSP
                    first_end = raw.find(words[0]) + len(words[0])
                    rest_text = raw[first_end:].lstrip(' \t\n\r')
                    lds = precompute_para(words[0], rest_text, COL_W)
                elements.append({
                    'kind': 'para',
                    'lines': lds,
                    'footnotes': item.get('footnotes', []),
                    'source_idxs': list(item.get('_source_idxs', [])),
                })
                last_heading_kind = None

        # Content pages are physically after 4 front-matter pages:
        # 1 title placeholder, 2 contact, 3 TOC, 4 blank verso.
        # Printed body numbering starts at 1 on the first content page.
        self.page_num = 4   # _paginate will increment to 5 on first physical content page
        self._paginate(elements)
        self._qa_summary = self._build_qa_report(elements, self._page_plans)
        self.cv.save()

        # ── Build front matter and prepend it ──────────────────────────────
        # Collect anaf entries in order with their (content) page numbers.
        # Printed content page numbering starts at 1.
        anaf_entries = []
        for item in items_to_render:
            if item['type'] == 'heading1':
                lbl = item['label']
                name = item.get('name', '')
                pg = self.anaf_page_map.get(lbl, 1)
                anaf_entries.append((lbl, name, pg))

        front_pdf_path = self.out_pdf.replace('.pdf', '_front_tmp.pdf')
        front_cv = rl_canvas.Canvas(front_pdf_path, pagesize=(PAGE_W, PAGE_H))
        # Page 2: contact
        draw_contact_page(front_cv)
        front_cv.showPage()
        # Page 3: TOC
        draw_toc_page(front_cv, anaf_entries, page_offset=1)
        front_cv.showPage()
        # Page 4: blank verso before body starts
        set_page_geometry(4)
        front_cv.showPage()
        front_cv.save()

        # Merge: shaar blatt (page 1) + front matter + content pages
        from pypdf import PdfWriter, PdfReader
        writer = PdfWriter()
        shaar_blatt_cropped = None
        SHAAR_BLATT_PDF = S.SHAAR_BLATT_PDF
        if SHAAR_BLATT_PDF and os.path.exists(SHAAR_BLATT_PDF):
            # Crop shaar blatt from 450x666 (6.25"x9.25" with 0.375" bleed) to
            # 396x612 (5.5"x8.5") — remove 27pt bleed evenly from all four sides.
            shaar_blatt_cropped = self.out_pdf.replace('.pdf', '_shaar_tmp.pdf')
            import subprocess as _sp
            _sp.run([
                'gs', '-sDEVICE=pdfwrite', '-dNOPAUSE', '-dBATCH', '-dQUIET',
                '-dDEVICEWIDTHPOINTS=396', '-dDEVICEHEIGHTPOINTS=612',
                '-dFIXEDMEDIA',
                f'-sOutputFile={shaar_blatt_cropped}',
                '-c', '<</PageOffset [-27 -27]>> setpagedevice',
                '-f', SHAAR_BLATT_PDF,
            ], check=True)
            shaar_reader = PdfReader(shaar_blatt_cropped)
            writer.add_page(shaar_reader.pages[0])
        # Pages 2-4: contact, TOC, blank
        front_reader = PdfReader(front_pdf_path)
        for page in front_reader.pages:
            writer.add_page(page)
        # Content pages
        content_reader = PdfReader(self.out_pdf)
        for page in content_reader.pages:
            writer.add_page(page)
        with open(self.out_pdf, 'wb') as f:
            writer.write(f)
        os.remove(front_pdf_path)
        if shaar_blatt_cropped and os.path.exists(shaar_blatt_cropped):
            os.remove(shaar_blatt_cropped)

        print(f"Saved: {self.out_pdf}")
        self._save_job_artifacts()
        return self.page_num

    def _save_job_artifacts(self):
        """Save job archive: doc_parts snapshot, settings snapshot, script copy, layout logs."""
        import datetime, shutil as _shutil, inspect as _inspect

        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        out_dir = os.path.dirname(os.path.abspath(self.out_pdf))
        jobs_dir = os.path.join(out_dir, 'jobs', ts)
        os.makedirs(jobs_dir, exist_ok=True)

        # 1. Copy doc_parts.json
        src_json = S.DOC_PARTS_PATH
        if os.path.isfile(src_json):
            _shutil.copy2(src_json, os.path.join(jobs_dir, 'doc_parts.json'))

        # 2. Copy settings.py (resolve symlink if any)
        settings_path = os.path.realpath(_inspect.getfile(S))
        if os.path.isfile(settings_path):
            _shutil.copy2(settings_path, os.path.join(jobs_dir, 'settings.py'))

        # 3. Copy this build script (resolve symlink if any)
        script_path = os.path.realpath(os.path.abspath(__file__))
        if os.path.isfile(script_path):
            _shutil.copy2(script_path, os.path.join(jobs_dir, 'build_production_new.py'))

        # 4. Write job manifest (JSON)
        manifest = {
            'timestamp':    ts,
            'source':       src_json,
            'output':       self.out_pdf,
            'shaar_blatt':  S.SHAAR_BLATT_PDF,
            'settings':     settings_path,
            'script':       script_path,
            'n_pages':      self.page_num,
            'n_log_entries': len(self._layout_log),
        }
        with open(os.path.join(jobs_dir, 'manifest.json'), 'w', encoding='utf-8') as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        # 5. Write layout log — JSON
        log_json_path = os.path.join(jobs_dir, 'layout_log.json')
        with open(log_json_path, 'w', encoding='utf-8') as f:
            json.dump(self._layout_log, f, ensure_ascii=False, indent=2)

        # 6. Write layout log — TSV
        log_tsv_path = os.path.join(jobs_dir, 'layout_log.tsv')
        tsv_cols = ['page', 'display_pg', 'col', 'y_top', 'height', 'y_bot',
                    'el_idx', 'kind', 'frag', 'n_lines', 'text', 'rendered_text']
        with open(log_tsv_path, 'w', encoding='utf-8') as f:
            f.write('\t'.join(tsv_cols) + '\n')
            for entry in self._layout_log:
                row = [str(entry.get(c, '')) for c in tsv_cols]
                f.write('\t'.join(row) + '\n')

        # 7. Write trace log (full decision trace)
        trace_path = _trace.save(jobs_dir)

        # 8. Write QA report
        qa_summary = getattr(self, '_qa_summary', None) or {
            'total_issues': len(self._qa_issues),
            'issues': list(self._qa_issues),
            'coverage': [],
            'page_balances': [],
        }
        qa_json_path = os.path.join(jobs_dir, 'qa_report.json')
        with open(qa_json_path, 'w', encoding='utf-8') as f:
            json.dump(qa_summary, f, ensure_ascii=False, indent=2)

        qa_txt_path = os.path.join(jobs_dir, 'qa_report.txt')
        with open(qa_txt_path, 'w', encoding='utf-8') as f:
            f.write(f"Total issues: {qa_summary.get('total_issues', 0)}\n\n")
            f.write("Page balances:\n")
            for rec in qa_summary.get('page_balances', []):
                flag = '  ***' if rec.get('diff', 0.0) > 5.0 else ''
                fallback = ' last_resort_sub_gap' if rec.get('sub_after_last_resort') else ''
                f.write(
                    f"  page={rec['page']} display={rec['display_pg']} block={rec['block_index']} "
                    f"h1={rec['h1']:.3f} h2={rec['h2']:.3f} diff={rec['diff']:.3f}{fallback}{flag}\n"
                )
            f.write("\nCoverage issues:\n")
            issues = qa_summary.get('issues', [])
            if issues:
                for issue in issues:
                    f.write(f"  [{issue.get('kind','issue')}] {issue.get('detail','')}\n")
            else:
                f.write("  none\n")

        print(f"  Job archive saved: {jobs_dir}/")
        print(f"    manifest.json  — job parameters")
        print(f"    layout_log.tsv — {len(self._layout_log)} rendered elements (human-readable)")
        print(f"    layout_log.json — same data as JSON")
        print(f"    trace_log.jsonl — {len(_trace.entries)} trace events (full decision log)")
        print(f"    trace_summary.txt — event counts by phase/type")
        print(f"    qa_report.json — coverage + balance verification")
        print(f"    qa_report.txt — human-readable QA summary")

    def _paginate(self, elements):
        _trace.set_phase('paginate')
        _trace.log('paginate_start', f'n_elements={len(elements)}')
        el_idx, line_off, fn_off = 0, 0, 0
        carry_fn_lds = []
        while el_idx < len(elements) or carry_fn_lds:
            self.page_num += 1
            set_page_geometry(self.page_num)
            _trace.set_page(self.page_num)
            _trace.log('page_start',
                       f'layout_pg={self.page_num} display_pg={max(1,self.page_num-4)} '
                       f'el_idx={el_idx} line_off={line_off} fn_off={fn_off} '
                       f'carry_fns={len(carry_fn_lds)}',
                       el_idx=el_idx, line_off=line_off, fn_off=fn_off)
            el_idx, line_off, fn_off, carry_fn_lds = self._render_page(
                elements, el_idx, line_off, fn_off, carry_fn_lds)
            _trace.log('page_end',
                       f'layout_pg={self.page_num} -> el_idx={el_idx} line_off={line_off} '
                       f'fn_off={fn_off} carry_fns={len(carry_fn_lds)}',
                       el_idx=el_idx, line_off=line_off, fn_off=fn_off)
        _trace.log('paginate_end', f'total_pages={self.page_num}')

    def _render_page(self, elements, el_idx, line_off, fn_off, carry_fn_lds):
        _trace.set_phase('render_page')
        c = self.cv
        header_y = C_TOP - 2
        display_page_num = max(1, self.page_num - 4)
        _trace.log('render_page_entry',
                   f'el_idx={el_idx} line_off={line_off} fn_off={fn_off} '
                   f'carry_fns={len(carry_fn_lds)} total_elements={len(elements)}',
                   el_idx=el_idx, line_off=line_off, fn_off=fn_off,
                   carry_fns=len(carry_fn_lds))
        header_anaf_label = self.current_anaf_label
        header_anaf_name = self.current_anaf_name
        # If the first element on this page is a new anaf heading, use that
        # label (not the previous anaf's label) for the header.
        for probe in elements[el_idx: min(el_idx + 8, len(elements))]:
            if probe.get('kind') == 'anaf':
                header_anaf_label = probe.get('label', '')
                header_anaf_name = probe.get('name', '')
                break
            elif probe.get('kind') == 'para':
                break  # real content before any anaf — keep current
        if S.HEADER_ENABLED:
            if self.page_num % 2 == 1:
                draw_running_header_odd_full(c, display_page_num,
                                             header_anaf_label,
                                             header_anaf_name)
            else:
                draw_running_header_even(c, display_page_num,
                                         header_anaf_label,
                                         header_anaf_name)
        text_top = header_y - 26
        total_avail_h = text_top - C_BOT
        # On display page 1 two ornaments consume extra vertical space that the
        # paginator does not see. Track them in a variable so we can add to used_h
        # at the right moments below.
        _is_pg1 = (max(1, self.page_num - 4) == 1) and S.SECTION_END_ORNAMENT
        _sefer_title_orn_h = 0.0  # ornament after sefer title removed
        _SECTION_ORN_H = (HSEP * 2.2  + LH * 2.2) if S.SECTION_END_ORNAMENT else 0.0
        # The ornament's bottom gap (LH * 2.2) is decorative whitespace that can
        # safely intrude into the bottom margin.  By reducing the reservation
        # during pagination we pack more content on section-end pages, avoiding
        # nearly-empty pages that only hold a line or two plus the ornament.
        _ORN_MARGIN_INTRUDE = (LH * 2.2) if S.SECTION_END_ORNAMENT else 0.0
        _SECTION_ORN_RESERVE = max(0.0, _SECTION_ORN_H - _ORN_MARGIN_INTRUDE)

        def _post_anaf_orn_h_for(next_el_idx):
            """Return the *reservation* height for the section-end ornament if it
            would actually be rendered after breaking before element next_el_idx.
            The ornament is drawn when the next element is an anaf heading OR
            this is the final block of the book.
            Returns the reduced reservation (_SECTION_ORN_RESERVE) so the
            ornament can intrude into the bottom margin."""
            if not S.SECTION_END_ORNAMENT:
                return 0.0
            # Check if the break point is at or before a new anaf
            if next_el_idx >= len(elements):
                # End of book — ornament is drawn
                return _SECTION_ORN_RESERVE
            if elements[next_el_idx]['kind'] == 'anaf':
                return _SECTION_ORN_RESERVE
            return 0.0

        # Ornament space reservation is computed dynamically via _orn_h_after().
        # The ornament is only drawn when the page ends at a section boundary
        # (next element is an anaf heading or end of book). On pages where the
        # break is mid-element (e.g. page 1), no ornament is drawn and no space
        # is reserved.
        def _orn_h_after(ei):
            """Ornament height if page were to break with next element at index ei."""
            return _post_anaf_orn_h_for(ei)
        FN_BASE_OVERHEAD = 10 + FN_TEXT_GAP

        def body_avail(fn_ld_count=0):
            # Reserve footnote rule+gap overhead only when there will actually be footnotes
            if fn_ld_count > 0:
                fn_space = FN_BASE_OVERHEAD + fn_ld_count * FN_LH
            else:
                fn_space = 0
            return max(total_avail_h - fn_space, 0)

        fn_lds_on_page = list(carry_fn_lds)
        plan = []
        used_h = 0.0
        acc_paras = []
        acc_fn_lds = []
        page_fn_start = self.fn_counter + 1
        fn_cursor = self.fn_counter

        def flush_acc(force_height=None, allow_trailing_subhead=False, max_height=None):
            nonlocal used_h, fn_lds_on_page
            if not acc_paras: return 0.0
            c1d, c2d, e1, e2, elh1, elh2 = _col_layout(acc_paras,
                                                           allow_trailing_subhead=allow_trailing_subhead,
                                                           max_height=max_height)
            sub_after_last_resort = bool(_force_sub_after_stretchable)
            # Compute raw (no-top-pad) column heights, then derive top-pads
            if force_height is not None:
                h = force_height
                e_top1, e_top2 = 0.0, 0.0
            else:
                raw_h1, raw_h2 = _col_raw_heights(acc_paras, c1d, c2d, e1, e2, elh1, elh2)
                e_top1, e_top2 = _compute_top_pads(raw_h1, raw_h2, c1d, c2d, acc_paras)
                h = _col_height(acc_paras, c1d, c2d, e1, e2, elh1, elh2,
                                e_top1=e_top1, e_top2=e_top2)
            fn_lds_on_page.extend(acc_fn_lds)
            plan.append({'kind': 'col_pair', 'paras': list(acc_paras),
                         'c1d': c1d, 'c2d': c2d, 'e1': e1, 'e2': e2,
                         'elh1': elh1, 'elh2': elh2,
                          'e_top1': e_top1, 'e_top2': e_top2,
                         'fn_lds': list(acc_fn_lds), 'height': h,
                         'sub_after_last_resort': sub_after_last_resort})
            acc_paras.clear()
            acc_fn_lds.clear()
            acc_origins.clear()
            used_h += h
            return h

        # ── Track element indices for subhead rollback ──
        # Each time we append to acc_paras from a new element, record:
        #   (element_index, line_offset_before, fn_offset_before, fn_cursor_before, fn_lds_count)
        acc_origins = []

        def _pullback_trailing_subheads():
            """Remove trailing subhead(s) from acc_paras and roll back cursors.
            This prevents subheads from being orphaned at the bottom of a page
            without their following body paragraph.
            Returns True if any subheads were pulled back."""
            nonlocal cur_el_idx, cur_ln_off, cur_fn_off, fn_cursor
            # Only pull back if there are more elements to come
            # (otherwise the subhead IS the last thing and should stay)
            if cur_el_idx >= len(elements):
                return False
            pulled = False
            while acc_paras and acc_paras[-1].get('is_subhead'):
                acc_paras.pop()
                if acc_origins:
                    orig = acc_origins.pop()
                    cur_el_idx = orig['el_idx']
                    cur_ln_off = orig['ln_off']
                    cur_fn_off = orig['fn_off']
                    fn_cursor = orig['fn_cursor']
                    # Also remove corresponding footnote line dicts
                    n_fn_lds_to_remove = orig['fn_lds_added']
                    if n_fn_lds_to_remove > 0:
                        del acc_fn_lds[-n_fn_lds_to_remove:]
                pulled = True
            return pulled

        page_empty = True
        cur_el_idx, cur_ln_off, cur_fn_off = el_idx, line_off, fn_off

        while cur_el_idx < len(elements):
            el = elements[cur_el_idx]
            _trace.log('element_visit',
                       f'el[{cur_el_idx}] kind={el["kind"]} is_sub={el.get("is_subhead",False)} '
                       f'nlines={len(el.get("lines",[]))} line_off={cur_ln_off} '
                       f'used_h={used_h:.1f} avail={body_avail(len(fn_lds_on_page+acc_fn_lds)):.1f} '
                       f'acc_paras={len(acc_paras)}',
                       el_idx=cur_el_idx, kind=el['kind'],
                       is_sub=el.get('is_subhead', False),
                       nlines=len(el.get('lines', [])),
                       line_off=cur_ln_off, used_h=round(used_h, 2),
                       acc_n=len(acc_paras))

            if el['kind'] == 'sefer_title':
                flush_acc()
                page_empty = False
                sec_h = heading_height(el['text'], 'sefer_title')
                _title_gap_above = LH * 3.5
                plan.append({'kind': 'sefer_title', 'text': el['text'], 'height': sec_h,
                             'gap_above': _title_gap_above, 'orig_el_idx': cur_el_idx,
                             'source_idxs': list(el.get('source_idxs', []))})
                used_h += sec_h + _title_gap_above + _sefer_title_orn_h
                _trace.log('sefer_title_placed', f'height={sec_h:.1f} used_h={used_h:.1f}',
                           height=round(sec_h, 2))

                cur_el_idx += 1; cur_ln_off = 0; cur_fn_off = 0
                continue

            if el['kind'] == 'anaf':
                page_has_real_content = (
                    any(b['kind'] != 'sefer_title' for b in plan) or bool(acc_paras))
                if page_has_real_content:
                    _trace.log('anaf_break', f'el[{cur_el_idx}] page already has content -> break')
                    break
                self.current_anaf_label = el['label']
                self.current_anaf_name = el.get('name', '')
                flush_acc()
                page_empty = False
                sec_h = heading_height(el['label'], 'anaf', has_name=bool(el.get('name', '')))
                if used_h + sec_h <= body_avail(len(fn_lds_on_page)) + 0.01:
                    next_el = elements[cur_el_idx + 1] if cur_el_idx + 1 < len(elements) else None
                    heading_ok = True
                    if next_el and next_el['kind'] == 'para':
                        if used_h + sec_h + MIN_PART_LINES * LH > body_avail(len(fn_lds_on_page)) + 0.01:
                            heading_ok = False
                    if heading_ok:
                        plan.append({'kind': 'anaf', 'label': el['label'], 'name': el.get('name', ''),
                                     'height': sec_h, 'gap_above': 0.0, 'orig_el_idx': cur_el_idx,
                                     'source_idxs': list(el.get('source_idxs', []))})
                        used_h += sec_h
                        _trace.log('anaf_placed', f'el[{cur_el_idx}] label={el["label"]} '
                                   f'height={sec_h:.1f} used_h={used_h:.1f}',
                                   height=round(sec_h, 2))

                        cur_el_idx += 1; cur_ln_off = 0; cur_fn_off = 0
                    else:
                        _trace.log('anaf_no_room_body',
                                   f'el[{cur_el_idx}] heading fits but no room for MIN_PART_LINES body -> break')
                        break
                else:
                    _trace.log('anaf_no_room',
                               f'el[{cur_el_idx}] heading {sec_h:.1f}pt doesnt fit (used={used_h:.1f} avail={body_avail(len(fn_lds_on_page)):.1f}) -> break')
                    break
                continue

            # ── paragraphs ──
            if el['kind'] != 'para':
                _trace.log('skip_non_para', f'el[{cur_el_idx}] kind={el["kind"]} -> skip')
                cur_el_idx += 1; cur_ln_off = 0; cur_fn_off = 0
                continue

            all_lines = el['lines']
            all_fns = el.get('footnotes', [])
            remaining_lines = all_lines[cur_ln_off:]
            remaining_fns = all_fns[cur_fn_off:]

            if not remaining_lines:
                _trace.log('para_empty', f'el[{cur_el_idx}] no remaining lines -> advance')
                cur_el_idx += 1; cur_ln_off = 0; cur_fn_off = 0
                continue

            fns_on_page_so_far = (sum(1 for ld in fn_lds_on_page if ld.get('is_first'))
                                  + sum(1 for ld in acc_fn_lds if ld.get('is_first')))
            fn_num_base = fn_cursor + fns_on_page_so_far + 1
            para_fn_lds = []
            for fi, ft in enumerate(remaining_fns):
                para_fn_lds.extend(fn_text_to_line_dicts(clean_fn_text(ft), fn_num_base + fi))

            _visual_nlines = sum(ld.get('nlines', 1) for ld in remaining_lines)
            trial_para = {'lines': remaining_lines, 'nlines': _visual_nlines, 'is_para_end': True,
                          'orig_el_idx': cur_el_idx}
            if el.get('is_subhead'): trial_para['is_subhead'] = True
            trial_para['source_idxs'] = list(el.get('source_idxs', [cur_el_idx]))

            trial_acc = acc_paras + [trial_para]
            trial_fns = fn_lds_on_page + acc_fn_lds + para_fn_lds
            _trace.set_phase('render_page.trial_layout')
            c1d, c2d, e1, e2, elh1, elh2 = _col_layout(trial_acc)
            _trace.set_phase('render_page')
            _tr_h1, _tr_h2 = _col_raw_heights(trial_acc, c1d, c2d, e1, e2, elh1, elh2)
            _tr_top1, _tr_top2 = _compute_top_pads(_tr_h1, _tr_h2, c1d, c2d, trial_acc)
            trial_h = _col_height(trial_acc, c1d, c2d, e1, e2, elh1, elh2,
                                  e_top1=_tr_top1, e_top2=_tr_top2)

            _last_plan_kind = plan[-1]['kind'] if plan else None
            _last_is_heading = _last_plan_kind in ('anaf', 'anaf_sub', 'section', 'sefer_title')
            sep = PSEP if (plan and not acc_paras and not _last_is_heading) else 0.0

            _avail_now = body_avail(len(trial_fns))
            _total_need = used_h + trial_h + sep + _orn_h_after(cur_el_idx + 1)
            _trace.log('para_trial',
                       f'el[{cur_el_idx}] is_sub={el.get("is_subhead",False)} '
                       f'nlines={_visual_nlines} trial_h={trial_h:.1f} sep={sep:.1f} '
                       f'orn_h={_orn_h_after(cur_el_idx+1):.1f} '
                       f'total_need={_total_need:.1f} avail={_avail_now:.1f} '
                       f'fits={_total_need <= _avail_now + 0.01}',
                       el_idx=cur_el_idx, nlines=_visual_nlines,
                       trial_h=round(trial_h, 2), avail=round(_avail_now, 2),
                       fits=_total_need <= _avail_now + 0.01)

            if _total_need <= _avail_now + 0.01:
                # Whole para fits — add to accumulator
                acc_origins.append({
                    'el_idx': cur_el_idx, 'ln_off': cur_ln_off, 'fn_off': cur_fn_off,
                    'fn_cursor': fn_cursor, 'fn_lds_added': len(para_fn_lds),
                })
                acc_paras.append(trial_para)
                acc_fn_lds.extend(para_fn_lds)
                fn_cursor += len(remaining_fns)
                _trace.log('para_whole_fit',
                           f'el[{cur_el_idx}] whole para fits, acc_paras now {len(acc_paras)}',
                           acc_n=len(acc_paras))
                cur_el_idx += 1; cur_ln_off = 0; cur_fn_off = 0
                page_empty = False
                continue

            # Split
            nL = len(remaining_lines)
            best_n = 0
            _trace.log('split_start',
                       f'el[{cur_el_idx}] doesnt fit whole, nL={nL} MIN_PART={MIN_PART_LINES} '
                       f'trying split nn={nL - MIN_PART_LINES}..{MIN_PART_LINES}',
                       el_idx=cur_el_idx, nL=nL)

            _trace.set_phase('render_page.split_loop')
            for nn in range(nL - MIN_PART_LINES, MIN_PART_LINES - 1, -1):
                if nn < MIN_PART_LINES: break
                fn_in_head = _count_fns_in_lines(remaining_lines[:nn])
                head_fn_lds = []
                for fi in range(fn_in_head):
                    head_fn_lds.extend(fn_text_to_line_dicts(clean_fn_text(remaining_fns[fi]), fn_num_base + fi))
                _t_vnl = sum(ld.get('nlines', 1) for ld in remaining_lines[:nn])
                t_para = {'lines': remaining_lines[:nn], 'nlines': _t_vnl}
                if el.get('is_subhead'): t_para['is_subhead'] = True
                t_para['source_idxs'] = list(el.get('source_idxs', [cur_el_idx]))
                t_acc = acc_paras + [t_para]
                t_fns = fn_lds_on_page + acc_fn_lds + head_fn_lds
                sep2 = PSEP if (plan and not acc_paras) else 0.0
                # When splitting mid-element, the break doesn't create a section
                # boundary, so _orn_h_after(cur_el_idx) = 0 (no ornament).
                _split_orn_h = _orn_h_after(cur_el_idx)
                avail_for_cols = body_avail(len(t_fns)) - used_h - sep2 - _split_orn_h
                # First try balance-optimal, then height-constrained if it doesn't fit
                tc1, tc2, te1, te2, telh1, telh2 = _col_layout(t_acc)
                _tt_h1, _tt_h2 = _col_raw_heights(t_acc, tc1, tc2, te1, te2, telh1, telh2)
                _tt_top1, _tt_top2 = _compute_top_pads(_tt_h1, _tt_h2, tc1, tc2, t_acc)
                t_h = _col_height(t_acc, tc1, tc2, te1, te2, telh1, telh2,
                                  e_top1=_tt_top1, e_top2=_tt_top2)

                # If balance-optimal doesn't fit, retry with height constraint
                _used_height_constraint = False
                if t_h > avail_for_cols + 0.01 and avail_for_cols > LH * 2:
                    _used_height_constraint = True
                    tc1, tc2, te1, te2, telh1, telh2 = _col_layout(t_acc, max_height=avail_for_cols)
                    _tt_h1, _tt_h2 = _col_raw_heights(t_acc, tc1, tc2, te1, te2, telh1, telh2)
                    _tt_top1, _tt_top2 = _compute_top_pads(_tt_h1, _tt_h2, tc1, tc2, t_acc)
                    t_h = _col_height(t_acc, tc1, tc2, te1, te2, telh1, telh2,
                                      e_top1=_tt_top1, e_top2=_tt_top2)

                _split_fits = used_h + t_h + sep2 + _split_orn_h <= body_avail(len(t_fns)) + 0.01
                _trace.log('split_try',
                           f'nn={nn} t_h={t_h:.1f} avail_cols={avail_for_cols:.1f} '
                           f'height_constrained={_used_height_constraint} fits={_split_fits}',
                           nn=nn, t_h=round(t_h, 2), avail=round(avail_for_cols, 2),
                           fits=_split_fits)
                if _split_fits:
                    best_n = nn
                    _trace.log('split_found', f'best_n={best_n}', best_n=best_n)
                    break
            _trace.set_phase('render_page')

            if best_n == 0:
                _trace.log('split_none_found', f'el[{cur_el_idx}] no split fits')

            # ── Balance-trim retry ──
            # After finding best_n, try best_n-1 and best_n-2 to see if
            # sending one or two fewer lines to this page gives a better
            # column balance (lower _col_layout residual). Only attempt this
            # when acc_paras already contains a subhead (the structural case
            # where col2 starts with a subhead and has no stretchable gap to
            # absorb the imbalance), and only when the trim leaves >= MIN_PART_LINES.
            _has_subhead_in_acc = any(p.get('is_subhead') for p in acc_paras)
            if best_n > 0 and _has_subhead_in_acc:
                _trace.log('balance_trim_start',
                           f'best_n={best_n} trying trim (subhead in acc)')
                # Score current best_n
                _bt_vnl = sum(ld.get('nlines', 1) for ld in remaining_lines[:best_n])
                _bt_para = {'lines': remaining_lines[:best_n], 'nlines': _bt_vnl}
                if el.get('is_subhead'): _bt_para['is_subhead'] = True
                _bt_para['source_idxs'] = list(el.get('source_idxs', [cur_el_idx]))
                _bt_acc = acc_paras + [_bt_para]
                _trace.set_phase('render_page.balance_trim')
                _bt_c1, _bt_c2, _bt_e1, _bt_e2, _bt_el1, _bt_el2 = _col_layout(_bt_acc)
                _bt_score = _col_layout_residual(_bt_acc, _bt_c1, _bt_c2, _bt_e1, _bt_e2)
                _trace.log('balance_trim_base', f'base score={_bt_score:.2f}',
                           score=round(_bt_score, 3))
                _best_balance_n = best_n
                _best_balance_score = _bt_score
                for _trim in (1, 2):
                    _try_n = best_n - _trim
                    if _try_n < MIN_PART_LINES:
                        break
                    _tr_vnl = sum(ld.get('nlines', 1) for ld in remaining_lines[:_try_n])
                    _tr_para = {'lines': remaining_lines[:_try_n], 'nlines': _tr_vnl}
                    if el.get('is_subhead'): _tr_para['is_subhead'] = True
                    _tr_para['source_idxs'] = list(el.get('source_idxs', [cur_el_idx]))
                    _tr_acc = acc_paras + [_tr_para]
                    _tr_fns = fn_lds_on_page + acc_fn_lds + (
                        [ld for fi in range(_count_fns_in_lines(remaining_lines[:_try_n]))
                         for ld in fn_text_to_line_dicts(clean_fn_text(remaining_fns[fi]), fn_num_base + fi)])
                    sep_tr = PSEP if (plan and not acc_paras and not _last_is_heading) else 0.0
                    _tr_c1, _tr_c2, _tr_e1, _tr_e2, _tr_el1, _tr_el2 = _col_layout(_tr_acc)
                    _tr_h1, _tr_h2 = _col_raw_heights(_tr_acc, _tr_c1, _tr_c2, _tr_e1, _tr_e2, _tr_el1, _tr_el2)
                    _tr_top1, _tr_top2 = _compute_top_pads(_tr_h1, _tr_h2, _tr_c1, _tr_c2, _tr_acc)
                    _tr_h = _col_height(_tr_acc, _tr_c1, _tr_c2, _tr_e1, _tr_e2, _tr_el1, _tr_el2,
                                        e_top1=_tr_top1, e_top2=_tr_top2)
                    _trim_fits = used_h + _tr_h + sep_tr + _split_orn_h <= body_avail(len(_tr_fns)) + 0.01
                    if not _trim_fits:
                        _trace.log('balance_trim_nofit', f'trim={_trim} try_n={_try_n} doesnt fit')
                        break  # doesn't fit — no point trying further
                    _tr_score = _col_layout_residual(_tr_acc, _tr_c1, _tr_c2, _tr_e1, _tr_e2)
                    _trace.log('balance_trim_try',
                               f'trim={_trim} try_n={_try_n} score={_tr_score:.2f} '
                               f'vs best={_best_balance_score:.2f} '
                               f'improved={_tr_score < _best_balance_score - 0.5}',
                               trim=_trim, try_n=_try_n, score=round(_tr_score, 3))
                    if _tr_score < _best_balance_score - 0.5:
                        _best_balance_score = _tr_score
                        _best_balance_n = _try_n
                _trace.set_phase('render_page')
                if _best_balance_n != best_n:
                    _trace.log('balance_trim_applied',
                               f'best_n {best_n}->{_best_balance_n}',
                               old_n=best_n, new_n=_best_balance_n)
                best_n = _best_balance_n

            # ── Trailing-subhead pullback retry ──
            _sub_retry_used = False
            if acc_paras and acc_paras[-1].get('is_subhead') and best_n == 0:
                _trace.log('subhead_pullback_start',
                           f'trailing subhead in acc, best_n=0, attempting pullback')
                _saved_acc = list(acc_paras)
                _saved_origins = list(acc_origins)
                _saved_fn_lds = list(acc_fn_lds)
                _saved_el_idx = cur_el_idx
                _saved_ln_off = cur_ln_off
                _saved_fn_off = cur_fn_off
                _saved_fn_cursor = fn_cursor
                _orig_best_n = best_n

                _pullback_trailing_subheads()
                _trace.log('subhead_pullback_done',
                           f'acc_paras after pullback: {len(acc_paras)}',
                           acc_n=len(acc_paras))

                if acc_paras:
                    retry_best_n = 0
                    _trace.set_phase('render_page.pullback_retry')
                    for nn2 in range(nL - MIN_PART_LINES, MIN_PART_LINES - 1, -1):
                        if nn2 < MIN_PART_LINES: break
                        fn_in_head2 = _count_fns_in_lines(remaining_lines[:nn2])
                        head_fn_lds2 = []
                        for fi2 in range(fn_in_head2):
                            head_fn_lds2.extend(fn_text_to_line_dicts(clean_fn_text(remaining_fns[fi2]), fn_num_base + fi2))
                        _t_vnl2 = sum(ld.get('nlines', 1) for ld in remaining_lines[:nn2])
                        t_para2 = {'lines': remaining_lines[:nn2], 'nlines': _t_vnl2}
                        if el.get('is_subhead'): t_para2['is_subhead'] = True
                        t_para2['source_idxs'] = list(el.get('source_idxs', [cur_el_idx]))
                        t_acc2 = acc_paras + [t_para2]
                        t_fns2 = fn_lds_on_page + acc_fn_lds + head_fn_lds2
                        sep3 = PSEP if (plan and not acc_paras) else 0.0
                        tc1_2, tc2_2, te1_2, te2_2, telh1_2, telh2_2 = _col_layout(t_acc2)
                        _th1_2, _th2_2 = _col_raw_heights(t_acc2, tc1_2, tc2_2, te1_2, te2_2, telh1_2, telh2_2)
                        _ttop1_2, _ttop2_2 = _compute_top_pads(_th1_2, _th2_2, tc1_2, tc2_2, t_acc2)
                        t_h2 = _col_height(t_acc2, tc1_2, tc2_2, te1_2, te2_2, telh1_2, telh2_2,
                                           e_top1=_ttop1_2, e_top2=_ttop2_2)

                        if used_h + t_h2 + sep3 + _split_orn_h <= body_avail(len(t_fns2)) + 0.01:
                            retry_best_n = nn2
                            _trace.log('pullback_retry_fit', f'nn2={nn2} fits')
                            break

                    _trace.set_phase('render_page')
                    # Never accept a retry split after pulling back a trailing
                    # subhead. Doing so would place body lines on the current
                    # page before their subhead, then restart the next page at
                    # that subhead and duplicate/reorder text.


                if not _sub_retry_used:
                    _trace.log('subhead_pullback_rejected',
                               'pullback retry would reorder subhead/body; restoring state')
                    acc_paras.clear(); acc_paras.extend(_saved_acc)
                    acc_origins.clear(); acc_origins.extend(_saved_origins)
                    acc_fn_lds.clear(); acc_fn_lds.extend(_saved_fn_lds)
                    cur_el_idx = _saved_el_idx
                    cur_ln_off = _saved_ln_off
                    cur_fn_off = _saved_fn_off
                    fn_cursor = _saved_fn_cursor

            # ── Next-page look-ahead ──
            # When we split a paragraph, the remaining lines go to the next
            # page.  If the next page's starting content (tail of this split
            # + the following elements) would produce a structurally
            # unbalanceable layout, try reducing best_n so the next page
            # gets more content (and therefore more inter-paragraph gaps to
            # absorb the imbalance).
            #
            # We build a lightweight *preview* of what the next page's
            # para_run might look like (tail of current split + up to
            # _LOOKAHEAD_ELEMENTS following whole elements) and score its
            # _col_layout_residual.  If poor, we try best_n - 1 .. down to
            # MIN_PART_LINES and pick the first reduction that makes the
            # next-page residual acceptable (or at least strictly better).
            _LOOKAHEAD_ELEMENTS = 4        # how many following elements to peek
            _LOOKAHEAD_RES_THRESH = LH * 0.25  # next-page residual must be under this
            if best_n > 0 and not _sub_retry_used:
                _trace.set_phase('render_page.lookahead')
                # Gather next-page preview paras
                def _build_next_page_preview(keep_n):
                    """Return a trial para_run for the next page assuming we
                    keep *keep_n* lines of the current paragraph on THIS page."""
                    preview = []
                    # 1) tail of the current split paragraph
                    tail_lines = remaining_lines[keep_n:]
                    if tail_lines:
                        _tvnl = sum(ld.get('nlines', 1) for ld in tail_lines)
                        preview.append({'lines': tail_lines, 'nlines': _tvnl,
                                        'is_para_end': True, 'orig_el_idx': cur_el_idx,
                                        'source_idxs': list(el.get('source_idxs', [cur_el_idx]))})
                    # 2) following whole elements (subheads + body paras)
                    ei = cur_el_idx + 1
                    added = 0
                    while ei < len(elements) and added < _LOOKAHEAD_ELEMENTS:
                        nxt = elements[ei]
                        if nxt['kind'] == 'anaf':
                            _trace.log('lookahead_anaf_stop',
                                       f'hit anaf at el[{ei}], stopping preview',
                                       stopped_at=ei)
                            break  # new section — stop
                        if nxt['kind'] == 'para':
                            nxt_lines = nxt['lines']
                            _nvnl = sum(ld.get('nlines', 1) for ld in nxt_lines)
                            p = {'lines': nxt_lines, 'nlines': _nvnl,
                                 'is_para_end': True, 'orig_el_idx': ei,
                                 'source_idxs': list(nxt.get('source_idxs', [ei]))}
                            if nxt.get('is_subhead'):
                                p['is_subhead'] = True
                            preview.append(p)
                            added += 1
                        ei += 1
                    return preview

                _np_preview = _build_next_page_preview(best_n)
                _trace.log('lookahead_preview',
                           f'best_n={best_n} preview_paras={len(_np_preview)} '
                           f'preview_types=[{",".join("sub" if p.get("is_subhead") else "body(" + str(p.get("nlines","?")) + ")" for p in _np_preview)}]',
                           preview_n=len(_np_preview),
                           best_n=best_n)
                if len(_np_preview) >= 2:
                    _np_c1, _np_c2, _np_e1, _np_e2, _, _ = _col_layout(_np_preview)
                    _np_res = _col_layout_residual(_np_preview, _np_c1, _np_c2, _np_e1, _np_e2)
                    _trace.log('lookahead_score',
                               f'next_page residual={_np_res:.2f} thresh={_LOOKAHEAD_RES_THRESH:.2f} '
                               f'trouble={_np_res > _LOOKAHEAD_RES_THRESH}',
                               residual=round(_np_res, 3),
                               threshold=round(_LOOKAHEAD_RES_THRESH, 3))
                    if _np_res > _LOOKAHEAD_RES_THRESH:
                        # Next page has balance trouble — try reducing best_n
                        _la_best_n = best_n
                        _la_best_res = _np_res
                        for _la_try in range(best_n - 1, MIN_PART_LINES - 1, -1):
                            # Check this page still fits with fewer lines
                            if _la_try < MIN_PART_LINES:
                                # _la_try == 0 is also valid: don't split at all
                                if _la_try > 0:
                                    break
                            _la_np = _build_next_page_preview(_la_try)
                            if len(_la_np) < 2:
                                _trace.log('lookahead_try_skip',
                                           f'try_n={_la_try} preview too short ({len(_la_np)})',
                                           try_n=_la_try)
                                continue
                            _la_c1, _la_c2, _la_e1, _la_e2, _, _ = _col_layout(_la_np)
                            _la_res = _col_layout_residual(
                                _la_np, _la_c1, _la_c2, _la_e1, _la_e2)
                            _trace.log('lookahead_try',
                                       f'try_n={_la_try} next_res={_la_res:.2f} '
                                       f'best_res={_la_best_res:.2f}',
                                       try_n=_la_try, next_res=round(_la_res, 3))
                            if _la_res < _la_best_res:
                                _la_best_res = _la_res
                                _la_best_n = _la_try
                            if _la_res <= _LOOKAHEAD_RES_THRESH:
                                break  # good enough
                        if _la_best_n != best_n:
                            # Also verify the CURRENT page still balances OK
                            # with the reduced best_n
                            if _la_best_n >= MIN_PART_LINES:
                                _la_vnl = sum(ld.get('nlines', 1)
                                              for ld in remaining_lines[:_la_best_n])
                                _la_para = {'lines': remaining_lines[:_la_best_n],
                                            'nlines': _la_vnl}
                                if el.get('is_subhead'):
                                    _la_para['is_subhead'] = True
                                _la_acc = acc_paras + [_la_para]
                                _la_cc1, _la_cc2, _la_ce1, _la_ce2, _, _ = \
                                    _col_layout(_la_acc)
                                _la_cur_res = _col_layout_residual(
                                    _la_acc, _la_cc1, _la_cc2, _la_ce1, _la_ce2)
                                # Accept if current page stays balanced and
                                # next page improves
                                if _la_cur_res <= _np_res + LH * 0.5:
                                    _trace.log('lookahead_accepted',
                                               f'best_n {best_n}->{_la_best_n} '
                                               f'cur_res={_la_cur_res:.2f} next_res={_la_best_res:.2f}',
                                               old_n=best_n, new_n=_la_best_n)
                                    best_n = _la_best_n
                                else:
                                    _trace.log('lookahead_rejected',
                                               f'cur_page would degrade: cur_res={_la_cur_res:.2f} > threshold={_np_res + LH*0.5:.2f}')
                            elif _la_best_n == 0:
                                _trace.log('lookahead_no_split',
                                           f'lookahead says dont split at all')
                                best_n = 0
                        else:
                            _trace.log('lookahead_no_improvement',
                                       f'no better split found for next page')
                else:
                    _trace.log('lookahead_skip',
                               f'preview has {len(_np_preview)} paras (< 2), skipping lookahead')
                _trace.set_phase('render_page')

            # ── Commit or flush ──
            if best_n == 0:
                if page_empty and not acc_paras:
                    best_n = min(nL, MIN_PART_LINES)
                    _trace.log('force_min_split',
                               f'empty page, forcing best_n={best_n}')
                else:
                    _trace.log('page_full',
                               f'best_n=0, flushing acc ({len(acc_paras)} paras) and breaking',
                               acc_n=len(acc_paras))
                    _pullback_trailing_subheads()
                    flush_acc()
                    page_empty = False
                    break

            _trace.log('split_commit',
                       f'el[{cur_el_idx}] committing best_n={best_n} of {nL} lines '
                       f'(sub_retry={_sub_retry_used})',
                       best_n=best_n, nL=nL, sub_retry=_sub_retry_used)

            fn_in_head = _count_fns_in_lines(remaining_lines[:best_n])
            head_para_fn_lds = []
            for fi in range(fn_in_head):
                head_para_fn_lds.extend(fn_text_to_line_dicts(clean_fn_text(remaining_fns[fi]), fn_num_base + fi))

            _head_vnl = sum(ld.get('nlines', 1) for ld in remaining_lines[:best_n])
            head_para = {'lines': remaining_lines[:best_n], 'nlines': _head_vnl, 'is_para_end': False,
                         'orig_el_idx': cur_el_idx}
            if el.get('is_subhead'): head_para['is_subhead'] = True
            head_para['source_idxs'] = list(el.get('source_idxs', [cur_el_idx]))
            acc_paras.append(head_para)
            acc_fn_lds.extend(head_para_fn_lds)
            fn_cursor += fn_in_head
            page_empty = False
            # Compute max_height for flush so _col_layout uses the height constraint
            _flush_fns = list(fn_lds_on_page) + list(acc_fn_lds)
            _flush_last_kind = plan[-1]['kind'] if plan else None
            _flush_last_heading = _flush_last_kind in ('anaf', 'anaf_sub', 'section', 'sefer_title')
            _flush_sep = PSEP if (plan and _flush_last_heading is False) else 0.0
            _flush_avail = body_avail(len(_flush_fns)) - used_h - _flush_sep - _orn_h_after(cur_el_idx)
            flush_acc(max_height=_flush_avail if _flush_avail > 0 else None)

            # When sub_retry pulled back the trailing subhead, _pullback_trailing_subheads
            # already set cur_el_idx to the subhead element and cur_ln_off=0.
            # We must NOT override that — the next page should start at the subhead.
            # Only update cur_ln_off/cur_el_idx relative to the body paragraph split
            # when sub_retry was NOT used (normal split path).
            if _sub_retry_used:
                # cur_el_idx already points to the pulled-back subhead (correct).
                # The split was on the body paragraph (_saved_el_idx), so advance
                # from there by best_n lines, then the subhead follows on next page.
                # But since _pullback already rolled back cur_el_idx to the subhead,
                # we simply leave cur_el_idx/cur_ln_off as-is (pointing to subhead).
                pass
            else:
                cur_ln_off = cur_ln_off + best_n
                cur_fn_off = cur_fn_off + fn_in_head
                if cur_ln_off >= len(all_lines):
                    cur_el_idx += 1; cur_ln_off = 0; cur_fn_off = 0
            break

        # ── Last-resort balance bleed ──
        is_final_flush = (cur_el_idx >= len(elements))
        BLEED_THRESHOLD     = LH * 0.5
        BLEED_MIN_IMPROVEMENT = LH * 0.5
        if (not is_final_flush
                and acc_paras
                and not acc_paras[-1].get('is_subhead')
                and acc_origins):
            _trace.set_phase('render_page.bleed')
            _bl_c1, _bl_c2, _bl_e1, _bl_e2, _bl_el1, _bl_el2 = _col_layout(acc_paras)
            _bl_res = _col_layout_residual(acc_paras, _bl_c1, _bl_c2, _bl_e1, _bl_e2)
            _trace.log('bleed_check',
                       f'residual={_bl_res:.2f} threshold={BLEED_THRESHOLD:.2f} '
                       f'try_bleed={_bl_res > BLEED_THRESHOLD}',
                       residual=round(_bl_res, 3))
            if _bl_res > BLEED_THRESHOLD:
                _last_para   = acc_paras[-1]
                _last_origin = acc_origins[-1]
                _last_nL     = _last_para['nlines']
                _best_bleed = 0
                _best_res   = _bl_res
                for _bleed in (1, 2):
                    _keep = _last_nL - _bleed
                    if _keep < MIN_PART_LINES:
                        break
                    # Build trimmed acc_paras
                    _trim_para = dict(_last_para,
                                      lines=_last_para['lines'][:_keep],
                                      nlines=_keep)
                    _trim_acc  = acc_paras[:-1] + [_trim_para]
                    _tr_c1, _tr_c2, _tr_e1, _tr_e2, _tr_el1, _tr_el2 = _col_layout(_trim_acc)
                    _tr_res = _col_layout_residual(_trim_acc, _tr_c1, _tr_c2, _tr_e1, _tr_e2)
                    if _tr_res < _best_res - BLEED_MIN_IMPROVEMENT:
                        _best_res   = _tr_res
                        _best_bleed = _bleed
                if _best_bleed > 0:
                    _trace.log('bleed_applied',
                               f'bleeding {_best_bleed} lines, new residual={_best_res:.2f}',
                               bleed=_best_bleed, new_res=round(_best_res, 3))
                    _keep = _last_nL - _best_bleed
                    # Trim the last para in acc_paras
                    acc_paras[-1] = dict(_last_para,
                                         lines=_last_para['lines'][:_keep],
                                         nlines=_keep,
                                         is_para_end=False)
                    # Roll back cur_el_idx / cur_ln_off so the bled lines start next page
                    _orig_el_idx  = _last_origin['el_idx']
                    _orig_ln_off  = _last_origin['ln_off']
                    _orig_fn_off  = _last_origin['fn_off']
                    _orig_fn_curs = _last_origin['fn_cursor']
                    cur_el_idx    = _orig_el_idx
                    cur_ln_off    = _orig_ln_off + _keep
                    cur_fn_off    = _orig_fn_off  # footnotes for bled lines stay on next page
                    fn_cursor     = _orig_fn_curs + _count_fns_in_lines(_last_para['lines'][:_keep])
                    # Trim excess fn_lds added by the last para from acc_fn_lds
                    _excess_fns   = _last_origin['fn_lds_added']
                    _kept_fns     = _count_fns_in_lines(_last_para['lines'][:_keep])
                    # Rebuild acc_fn_lds: keep only the fns corresponding to kept lines
                    if _excess_fns > 0:
                        del acc_fn_lds[-_excess_fns:]
                        # Re-add footnotes for just the kept lines
                        _fn_num_base  = fn_counter + (
                            sum(1 for ld in fn_lds_on_page if ld.get('is_first'))
                            + sum(1 for ld in acc_fn_lds if ld.get('is_first')) + 1)
                        _kfns = elements[_orig_el_idx].get('footnotes', [])
                        for _fi in range(_kept_fns):
                            acc_fn_lds.extend(fn_text_to_line_dicts(
                                clean_fn_text(_kfns[_orig_fn_off + _fi]),
                                _fn_num_base + _fi))

        # BUG FIX: Before final flush, pull back trailing subheads
        if not is_final_flush:
            _pullback_trailing_subheads()
        _trace.set_phase('render_page.final_flush')
        _trace.log('final_flush',
                   f'acc_paras={len(acc_paras)} is_final={is_final_flush} '
                   f'plan_blocks={len(plan)}',
                   acc_n=len(acc_paras), is_final=is_final_flush)
        flush_acc(allow_trailing_subhead=is_final_flush)
        body_used = used_h

        max_fn_lines = max(0, int((text_top - C_BOT - body_used - 10 - FN_TEXT_GAP) / FN_LH))
        if len(fn_lds_on_page) <= max_fn_lines:
            fn_draw = fn_lds_on_page
            fn_carry = []
        else:
            fn_draw = fn_lds_on_page[:max_fn_lines]
            fn_carry = fn_lds_on_page[max_fn_lines:]

        # Page-level stretch
        fn_rule_y = C_BOT + len(fn_draw) * FN_LH + 10
        body_bottom_min = fn_rule_y + FN_TEXT_GAP
        is_last_page = (cur_el_idx >= len(elements) and not fn_carry)
        is_section_end = (cur_el_idx < len(elements)
                          and elements[cur_el_idx]['kind'] == 'anaf'
                          and cur_ln_off == 0)
        skip_stretch = is_last_page or is_section_end
        display_pg = max(1, self.page_num - 4)
        _trace.set_phase('render_page.page_stretch')
        _trace.log('page_stretch_check',
                   f'is_last={is_last_page} is_section_end={is_section_end} skip={skip_stretch}',
                   is_last=is_last_page, is_section_end=is_section_end)

        simulated_y = text_top
        for blk in plan:
            if blk['kind'] in ('sefer_title', 'anaf', 'anaf_sub', 'section'):
                simulated_y -= blk['height']
            else:
                if simulated_y < text_top: simulated_y -= PSEP
                simulated_y -= blk['height']
        slack = simulated_y - body_bottom_min
        PAGE_MAX_E_PER_GAP = LH * 0.10
        MAX_ELH = 0.0
        display_pg = max(1, self.page_num - 4)

        if slack > 0.5 and not skip_stretch:
            for blk in plan:
                if blk['kind'] != 'col_pair' or slack < 0.05: continue
                para_run_blk = blk['paras']
                gaps1 = _stretchable_gaps(blk['c1d'], para_run_blk)
                gaps2 = _stretchable_gaps(blk['c2d'], para_run_blk)
                cur_e1, cur_e2 = blk.get('e1', 0.0), blk.get('e2', 0.0)


                # Capacity = how many more pts of height we can add before any
                # stretchable gap hits PAGE_MAX_E_PER_GAP.
                # We need BOTH columns to grow by the same amount (so they stay balanced).
                # Each column's height grows by delta_e * gaps_in_that_col.
                # The limiting factor is the column with fewer gaps:
                #   col_growth = delta_e * gaps  →  delta_e_max = (PAGE_MAX_E_PER_GAP - cur_e) 
                # We want BOTH columns to grow by the same `absorb` amount.
                # If col1 has g1 gaps and col2 has g2 gaps, adding delta_e to both:
                #   col1 grows delta_e * g1,  col2 grows delta_e * g2
                # That breaks balance unless g1 == g2.
                # Correct approach: ask _col_layout to re-solve with a forced height.
                cur_h = blk['height']
                # Max we can add: find how much both columns CAN grow via gap stretch,
                # respecting per-column PAGE_MAX_E_PER_GAP limit.
                # Since _col_layout will rebalance, we just need to know how much
                # total height the col_pair can absorb (= min of each column's capacity).
                def _col_absorb_cap(cd, cur_e):
                    """Max additional height absorbable by gap-stretching this column."""
                    g = _stretchable_gaps(cd, para_run_blk)
                    if g == 0:
                        return 0.0
                    room_per_gap = max(0.0, PAGE_MAX_E_PER_GAP - cur_e)
                    return room_per_gap * g

                cap1 = _col_absorb_cap(blk['c1d'], cur_e1)
                cap2 = _col_absorb_cap(blk['c2d'], cur_e2)
                # Both columns must absorb the same amount (to stay on the same page)
                absorb = min(slack, cap1, cap2)
                if absorb < 0.05: continue

                # Re-run _col_layout with the new target height so it properly
                # re-distributes e1/e2 to keep columns balanced.
                target_h = cur_h + absorb
                new_c1d, new_c2d, new_e1, new_e2, new_elh1, new_elh2 = \
                    _col_layout(para_run_blk)
                # Compute new height with the balanced layout
                _ss_h1, _ss_h2 = _col_raw_heights(para_run_blk, new_c1d, new_c2d,
                                                   new_e1, new_e2, new_elh1, new_elh2)
                _ss_top1, _ss_top2 = _compute_top_pads(_ss_h1, _ss_h2,
                                                        new_c1d, new_c2d, para_run_blk)
                new_h = _col_height(para_run_blk, new_c1d, new_c2d, new_e1, new_e2,
                                    new_elh1, new_elh2, e_top1=_ss_top1, e_top2=_ss_top2)
                actual_absorb = new_h - cur_h
                blk['c1d'], blk['c2d'] = new_c1d, new_c2d
                blk['e1'], blk['e2'] = new_e1, new_e2
                blk['elh1'], blk['elh2'] = new_elh1, new_elh2
                blk['e_top1'], blk['e_top2'] = _ss_top1, _ss_top2
                blk['height'] = new_h
                slack -= actual_absorb if actual_absorb > 0 else absorb

            # Reflow for remaining slack
            simulated_y2 = text_top
            for blk in plan:
                if blk['kind'] in ('sefer_title', 'anaf', 'anaf_sub', 'section'):
                    simulated_y2 -= blk['height']
                else:
                    if simulated_y2 < text_top: simulated_y2 -= PSEP
                    simulated_y2 -= blk['height']
            remaining_slack = simulated_y2 - body_bottom_min

            if remaining_slack > 0.5:
                for blk in plan:
                    if blk['kind'] != 'col_pair' or remaining_slack < 0.5: continue
                    para_run = blk['paras']
                    # Measure current imbalance before any reflow
                    _cur_h1, _cur_h2 = _col_raw_heights(
                        para_run, blk['c1d'], blk['c2d'],
                        blk.get('e1', 0.0), blk.get('e2', 0.0),
                        blk.get('elh1', 0.0), blk.get('elh2', 0.0))
                    _cur_imbal = abs(_cur_h1 - _cur_h2)
                    for _round in range(3):
                        if remaining_slack < 0.5: break
                        best_pi, best_lines, best_nL = None, None, None
                        # Try candidates in order; pick the first that keeps balance acceptable
                        candidates = sorted(
                            [(pi, p) for pi, p in enumerate(para_run)
                             if not p.get('is_subhead') and p['nlines'] >= 3],
                            key=lambda x: -x[1]['nlines'])
                        for pi, p in candidates:
                            reflowed = _reflow_lines(p['lines'], p['nlines'] + 1)
                            if reflowed is None:
                                continue
                            # Speculatively apply and check balance
                            saved_lines = p['lines']
                            saved_nlines = p['nlines']
                            para_run[pi] = dict(para_run[pi])
                            para_run[pi]['lines'] = reflowed
                            para_run[pi]['nlines'] = len(reflowed)
                            _tc1d, _tc2d, _te1, _te2, _telh1, _telh2 = _col_layout(para_run)
                            _th1, _th2 = _col_raw_heights(para_run, _tc1d, _tc2d, _te1, _te2, _telh1, _telh2)
                            _new_imbal = abs(_th1 - _th2)
                            # Accept only if imbalance doesn't grow significantly
                            if _new_imbal <= _cur_imbal + LH * 0.5:
                                best_pi, best_lines, best_nL = pi, reflowed, len(reflowed)
                                # Restore para (will be set again below)
                                para_run[pi]['lines'] = saved_lines
                                para_run[pi]['nlines'] = saved_nlines
                                break
                            else:
                                # Restore and try next candidate
                                para_run[pi]['lines'] = saved_lines
                                para_run[pi]['nlines'] = saved_nlines
                        if best_pi is None: break
                        para_run[best_pi] = dict(para_run[best_pi])
                        para_run[best_pi]['lines'] = best_lines
                        para_run[best_pi]['nlines'] = best_nL
                        c1d, c2d, e1, e2, elh1, elh2 = _col_layout(para_run)
                        blk['c1d'], blk['c2d'] = c1d, c2d
                        blk['e1'], blk['e2'] = e1, e2
                        blk['elh1'], blk['elh2'] = elh1, elh2
                        _sr_h1, _sr_h2 = _col_raw_heights(para_run, c1d, c2d, e1, e2, elh1, elh2)
                        _sr_top1, _sr_top2 = _compute_top_pads(_sr_h1, _sr_h2, c1d, c2d, para_run)
                        blk['e_top1'], blk['e_top2'] = _sr_top1, _sr_top2
                        new_h = _col_height(para_run, c1d, c2d, e1, e2, elh1, elh2,
                                           e_top1=_sr_top1, e_top2=_sr_top2)
                        delta_h = new_h - blk['height']
                        blk['height'] = new_h
                        remaining_slack -= delta_h
                        _cur_imbal = abs(_sr_h1 - _sr_h2)


        # ── Render pass ──
        self._page_plans[self.page_num] = [dict(block) for block in plan]
        render_y = text_top
        local_fnc = self.fn_counter
        first_block = True
        last_was_heading = False
        x_center = (C_LEFT + C_RIGHT) / 2

        for bi, block in enumerate(plan):
            next_block = plan[bi + 1] if bi + 1 < len(plan) else None

            if block['kind'] in ('sefer_title', 'section', 'anaf', 'anaf_sub'):
                if block['kind'] == 'anaf':
                    self.current_anaf_label = block['label']
                    self.current_anaf_name = block.get('name', '')
                    # Record the page this anaf starts on (for TOC)
                    lbl = block['label']
                    if lbl not in self.anaf_page_map:
                        self.anaf_page_map[lbl] = max(1, self.page_num - 4)
                gap_above = block.get('gap_above', 0.0)
                render_y -= gap_above
                if block['kind'] == 'anaf':
                    render_y -= H1_TOP_GAP
                    h = draw_anaf_heading(c, block['label'], block.get('name', ''), render_y)
                else:
                    h = draw_section_header(c, block['text'], render_y, kind=block['kind'])
                # Log full-width heading
                txt_log = block.get('label', '') or block.get('text', '')
                if block['kind'] == 'anaf' and block.get('name'):
                    txt_log = (block.get('label', '') + ' ' + block.get('name', '')).strip()
                self._layout_log.append({
                    'page':       self.page_num,
                    'display_pg': max(1, self.page_num - 4),
                    'col':        0,   # 0 = full-width
                    'y_top':      round(render_y, 2),
                    'height':     round(h, 2),
                    'y_bot':      round(render_y - h, 2),
                    'el_idx':     block.get('orig_el_idx', ''),
                    'source_idxs': list(block.get('source_idxs', [])),
                    'kind':       block['kind'],
                    'frag':       'full',
                    'n_lines':    1,
                    'rendered_text': txt_log,
                    'text':       txt_log[:80],
                })
                render_y -= h
                render_y -= HEADING_AFTER_GAP
                if block['kind'] == 'sefer_title':
                    render_y -= LH * 0.5
                # Ornament after sefer title removed — frame provides enough visual distinction

                first_block = False
                last_was_heading = True
                continue

            if not first_block and not last_was_heading:
                render_y -= PSEP
            first_block = False
            last_was_heading = False

            col_top = render_y
            h_used = _draw_col_pair(c, block['paras'],
                                    block['c1d'], block['c2d'],
                                    block['e1'], block['e2'],
                                    col_top, local_fnc,
                                    elh1=block.get('elh1', 0.0),
                                    elh2=block.get('elh2', 0.0),
                                    e_top1=block.get('e_top1', 0.0),
                                    e_top2=block.get('e_top2', 0.0),
                                    page_num=self.page_num,
                                    log_sink=self._layout_log)
            render_y -= h_used

            local_fnc += sum(1 for ld in block['fn_lds'] if ld.get('is_first', False))

            # End-of-section ornament before next anaf, and after final anaf
            next_el_is_anaf = (
                (next_block is not None and next_block['kind'] == 'anaf')
                or (next_block is None and cur_el_idx < len(elements)
                    and elements[cur_el_idx]['kind'] == 'anaf'))
            final_block_of_book = (bi == len(plan) - 1 and cur_el_idx >= len(elements))
            if (next_el_is_anaf or final_block_of_book) and S.SECTION_END_ORNAMENT:
                div_w = C_W * 0.72
                if False:
                    pass
                else:
                    render_y -= HSEP * 1.4
                    drawn_h = draw_post_anaf_divider_band(c, x_center, render_y, div_w)
                    if not drawn_h:
                        # fallback: old PNG divider
                        if not draw_generated_divider(c, x_center, render_y - 18, div_w, kind='chapter'):
                            draw_gray_bottom_orn(c, x_center, render_y - 18, div_w, gray=0.55, glow=False)
                    render_y -= LH * 2.2


        local_fnc += sum(1 for ld in fn_carry if ld.get('is_first', False))
        self.fn_counter = local_fnc

        # Draw footnotes
        if fn_draw:
            fn_rule_y = C_BOT + len(fn_draw) * FN_LH + 10
            # Ornamental footnote separator (~2/3 page width)
            if S.FN_RULE_ORNAMENTAL:
                rule_w = C_W * S.FN_RULE_WIDTH
                if not draw_generated_footnote_separator(c, (C_LEFT + C_RIGHT) / 2, fn_rule_y, width=rule_w):
                    draw_orn88(c, (C_LEFT + C_RIGHT) / 2, fn_rule_y, width=rule_w)
            else:
                rule_w = C_W * 0.3
                c.setStrokeColorRGB(0.3, 0.3, 0.3)
                c.setLineWidth(0.4)
                c.line(C_RIGHT - rule_w, fn_rule_y, C_RIGHT, fn_rule_y)
            draw_fn_line_dicts(c, fn_draw, fn_rule_y - 10)

        c.showPage()
        return cur_el_idx, cur_ln_off, cur_fn_off, fn_carry


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

import argparse as _argparse

def main():
    parser = _argparse.ArgumentParser(
        description="Palaot HaOtiyot typesetting engine",
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage examples:
  # Defaults from settings.py (backward compatible):
  python3 build_production_new.py

  # Positional args — source output [shaar_blatt]:
  python3 build_production_new.py doc_parts.json output/book.pdf
  python3 build_production_new.py doc_parts.json output/book.pdf /root/covers/v10/shar_blatt_v36_printready.pdf

  # Config file:
  python3 build_production_new.py --config job.json

  # job.json schema:
  {
    "source":      "path/to/doc_parts.json",       # required
    "output":      "path/to/output.pdf",            # required
    "shaar_blatt": "path/to/shar_blatt.pdf",        # optional
    "settings":    "path/to/settings.py"            # optional (not yet implemented)
  }
""")
    parser.add_argument('source', nargs='?', default=None,
                        help="Path to doc_parts.json (positional)")
    parser.add_argument('output', nargs='?', default=None,
                        help="Path to output PDF (positional)")
    parser.add_argument('shaar_blatt', nargs='?', default=None,
                        help="Path to shaar blatt PDF (positional, optional)")
    parser.add_argument('--config', metavar='JOB_JSON',
                        help="Path to job config JSON file")
    args = parser.parse_args()

    # Resolve paths from CLI / config / settings defaults
    source_path   = S.DOC_PARTS_PATH
    out_pdf       = S.OUT_PDF
    out_dir       = S.OUT_DIR
    shaar_blatt   = S.SHAAR_BLATT_PDF

    if args.config:
        with open(args.config, 'r', encoding='utf-8') as f:
            job = json.load(f)
        source_path = job.get('source', source_path)
        out_pdf     = job.get('output', out_pdf)
        shaar_blatt = job.get('shaar_blatt', shaar_blatt)
        out_dir     = os.path.dirname(os.path.abspath(out_pdf))
        # settings override not yet implemented — would require re-importing S
    else:
        if args.source:
            source_path = args.source
        if args.output:
            out_pdf  = args.output
            out_dir  = os.path.dirname(os.path.abspath(out_pdf))
        if args.shaar_blatt:
            shaar_blatt = args.shaar_blatt

    # Apply resolved paths back to S so all internal code uses them
    S.DOC_PARTS_PATH  = source_path
    S.OUT_PDF         = out_pdf
    S.OUT_DIR         = out_dir
    S.SHAAR_BLATT_PDF = shaar_blatt

    print("Loading doc_parts.json...")
    with open(source_path, 'r', encoding='utf-8') as f:
        items = json.load(f)
    print(f"  Source:  {source_path}")
    print(f"  Output:  {out_pdf}")
    print(f"  Cover:   {shaar_blatt}")
    print(f"  Found {len(items)} content items")

    from collections import Counter
    counts = Counter(it['type'] for it in items)
    for t, cnt in sorted(counts.items()):
        print(f"    {t}: {cnt}")

    os.makedirs(out_dir, exist_ok=True)

    print(f"\nRendering to {out_pdf}...")
    layout = PageLayout(items, out_pdf)
    n_pages = layout.build()
    print(f"  {n_pages} pages rendered")

     # Generate PNG previews
    print("\nGenerating PNG previews (200 DPI)...")
    stem = os.path.join(out_dir, os.path.splitext(os.path.basename(out_pdf))[0])
    import glob as _glob
    for old_png in _glob.glob(stem + '-*.png'):
        os.remove(old_png)
    # Try system pdftoppm first, then the persisted apt-bin version
    _pdftoppm_bin = 'pdftoppm'
    if not subprocess.run(['which', 'pdftoppm'], capture_output=True).returncode == 0:
        _persist_bin = '/data/.persist/apt-bin/pdftoppm'
        if os.path.exists(_persist_bin):
            _pdftoppm_bin = _persist_bin
    # Build LD_LIBRARY_PATH with all known lib dirs
    _lib_dirs = ['/data/.persist/apt-lib', '/tmp', '/usr/lib/x86_64-linux-gnu']
    _env = dict(os.environ)
    _env['LD_LIBRARY_PATH'] = ':'.join(_lib_dirs) + ':' + _env.get('LD_LIBRARY_PATH', '')
    try:
        subprocess.run(
            [_pdftoppm_bin, '-r', '200', '-png', out_pdf, stem],
            check=True, env=_env)
        for f in sorted(os.listdir(out_dir)):
            if f.startswith(os.path.basename(stem) + '-') and f.endswith('.png'):
                print(f"  {os.path.join(out_dir, f)}")
    except (FileNotFoundError, subprocess.CalledProcessError) as _e:
        print(f"  pdftoppm not available ({_e}) — skipping PNG previews")
        print(f"  PDF available at: {out_pdf}")

    print("\nDone!")

if __name__ == "__main__":
    main()
