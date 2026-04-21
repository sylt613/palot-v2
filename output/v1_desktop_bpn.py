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
from PIL import Image, ImageOps
import uharfbuzz as hb
from fontTools.ttLib import TTFont as FTFont

import settings as S

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
load_font(S.ORNAMENT_FONT_NAME,    S.ORNAMENT_FONT_PATH)
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
SUB_AFTER_GAP = LH * 0.35
SUB_LH = SUB_FS * S.H2_LINE_HEIGHT_MULT
SUB_BEFORE_FRAG_GAP = S.H2_SPACE_BEFORE_PT
SUB_AFTER_FRAG_GAP = S.SUB_AFTER_FRAG_GAP
MIN_SUB_AFTER_GAP = S.MIN_SUB_AFTER_GAP
MAX_SUB_AFTER_GAP = S.MAX_SUB_AFTER_GAP_PT   # ~26pt — used only by last-resort stretch pass

# Whether heading→paragraph gaps are allowed to stretch for balancing.
# False = locked (preferred). Last-resort path can unlock them temporarily.
ALLOW_SUB_AFTER_STRETCH = S.ALLOW_SUB_AFTER_STRETCH

# Mutable flag managed by _col_layout's last-resort path. Do NOT set directly.
_force_sub_after_stretchable = False

MIN_PART_LINES = S.MIN_PART_LINES

# Maximum top-padding that can be applied to the shorter column to absorb
# residual imbalance that gap-stretching alone cannot fix.
MAX_TOP_PAD = S.MAX_TOP_PAD_PT   # ~6.5pt — small residual correction only

# Footnotes
FN_FS = S.FN_FONT_SIZE
FN_LH = S.FN_LH
FN_GAP = S.FN_GAP
FN_RULE_GAP = S.FN_RULE_GAP
FN_TEXT_GAP = S.FN_TEXT_GAP
FN_SUPER_FS = S.FN_SUPER_SIZE

# Header
HEADER_FS = S.PGNUM_FONT_SIZE
PGNUM_FS = S.PGNUM_FONT_SIZE
HEADER_TITLE_FS = S.EVEN_HEADER_SIZE
HEADER_ANAF_FS = S.ODD_HEADER_SIZE
HEADER_MAAR_FS = getattr(S, 'ODD_HEADER_MAAR_SIZE', S.ODD_HEADER_SIZE)
HEADER_ORN_FS = S.PGNUM_ORN_SIZE

HSEP = S.HSEP  # extra spacing around section headings

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
PAREN_FS_RATIO = S.PAREN_FS_RATIO
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
    """Replace ASCII quotes/apostrophes with proper Hebrew geresh/gershayim.
    Used for any text rendered in Livorna or Livorna Bold."""
    result = []
    for ch in text:
        if ch == '"':
            result.append('\u05F4')   # ״ gershayim
        elif ch == "'":
            result.append('\u05F3')   # ׳ geresh
        else:
            result.append(ch)
    return ''.join(result)

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

def _draw_header_leader(c, y, x_left, x_right, fade_side='right'):
    """Header leader line with a subtle fade near one end."""
    if x_right <= x_left:
        return

    def _segmented_line(y0, lw, g0, g1, y_off=0.0):
        n = 18
        dx = (x_right - x_left) / n
        for i in range(n):
            xa = x_left + i * dx
            xb = x_left + (i + 1) * dx
            t = (i + 1) / n
            if fade_side == 'left':
                t = 1.0 - t
            # Quadratic easing for a softer fade
            tt = t * t
            g = g0 + (g1 - g0) * tt
            c.setStrokeColorRGB(g, g, g)
            c.setLineWidth(lw)
            c.line(xa, y0 + y_off, xb, y0 + y_off)

    _segmented_line(y, 0.9, 0.75, 0.96, y_off=0.0)
    _segmented_line(y, 0.35, 0.90, 0.985, y_off=-0.5)
    _segmented_line(y, 0.18, 0.82, 0.97, y_off=-0.9)

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
                           text_color=(0.0, 0.0, 0.0),
                           white_outline=(1.0, 1.0, 1.0),
                           gray_outline=(0.62, 0.62, 0.62)):
    """Draw title with smooth gray outer halo, white inner halo, and solid black text.

    Uses multiple closely-spaced gray rings stepping outward with progressive
    lightening — same technique as shaar blatt draw_centered_rtl_with_halo,
    which produces a smooth falloff at any font size.
    """
    ring_dirs = (
        (1.00, 0.00), (0.93, 0.36), (0.71, 0.71), (0.36, 0.93),
        (0.00, 1.00), (-0.36, 0.93), (-0.71, 0.71), (-0.93, 0.36),
        (-1.00, 0.00), (-0.93, -0.36), (-0.71, -0.71), (-0.36, -0.93),
        (0.00, -1.00), (0.36, -0.93), (0.71, -0.71), (0.93, -0.36),
    )

    def _draw_ring(radius, gray):
        c.setFillGray(gray)
        for ux, uy in ring_dirs:
            c.drawString(x + ux * radius, y + uy * radius, text)

    white_r  = max(0.90, size * 0.018)
    gray_r0  = max(1.35, size * 0.031)
    gray_r1  = gray_r0 + max(0.22, size * 0.005)
    gray_r2  = gray_r1 + max(0.22, size * 0.005)
    gray_r3  = gray_r2 + max(0.22, size * 0.005)

    gray_core  = gray_outline[0]   # e.g. 0.62
    gray_mid   = gray_core + (1.0 - gray_core) * 0.35   # ~0.75
    gray_outer = gray_core + (1.0 - gray_core) * 0.65   # ~0.86

    c.saveState()
    c.setFont(font, size)
    _draw_ring(gray_r3, gray_outer)   # outermost — lightest
    _draw_ring(gray_r2, gray_mid)
    _draw_ring(gray_r1, gray_core)    # inner gray — full density
    _draw_ring(gray_r0, gray_core)
    _draw_ring(white_r, white_outline[0])  # white knockout ring
    c.setFillGray(text_color[0])
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
    """Odd (recto) page header: outside=page number, inside=mamar + orn + ענף + orn."""
    header_y = C_TOP - 2

    # Page number at outer LEFT edge - Hebrew letters in Livorna Regular
    pg_str = heb_page(page_num)
    c.setFillColorRGB(0.15, 0.15, 0.15)
    draw_rtl_token(c, pg_str, "livornaregular", PGNUM_FS, C_LEFT, header_y, color=(0.15, 0.15, 0.15))
    pg_w = sum(Wid(ch, "livornaregular", PGNUM_FS) for ch in pg_str)

    # Inner-right: [mamar name BOLD] [orn] [ענף X] [orn after]
    label_vis = vis(livorna_fix_quotes(strip_nikud(anaf_label))) if anaf_label else ""
    mamar_vis = vis(livorna_fix_quotes(strip_nikud(MAMAR_NAME))) if MAMAR_NAME else ""
    orn_w_after_mamar = 26
    orn_w_after_anaf = 18
    orn_gap = Wid(" ", HEADING_FONT, HEADER_ANAF_FS) * 0.5
    small_orn_gap = Wid(" ", HEADING_FONT, HEADER_ANAF_FS) * 0.66
    mamar_anaf_svg = os.path.join(os.path.dirname(__file__), 'BergamotOrnaments-3oK6_O.svg')
    mamar_anaf_orn_w = 22
    mamar_anaf_gap = 4

    x = C_RIGHT
    leader_stop_x = x

    # Mamar name in bold — rightmost
    if mamar_vis:
        mw = Wid(mamar_vis, HEADING_BOLD_FONT, HEADER_MAAR_FS)
        if x - mw < C_LEFT:
            words = mamar_vis.split()
            while words and Wid(' '.join(words) + '...', HEADING_BOLD_FONT, HEADER_MAAR_FS) > x - C_LEFT - 2:
                words.pop()
            mamar_vis = ' '.join(words) + ('...' if words else '')
            mw = Wid(mamar_vis, HEADING_BOLD_FONT, HEADER_MAAR_FS)
        x -= mw
        c.setFont(HEADING_BOLD_FONT, HEADER_MAAR_FS)
        c.setFillColorRGB(0.15, 0.15, 0.15)
        c.drawString(x, header_y, mamar_vis)
        leader_stop_x = x - 6
        
        # Ornament between mamar and ענף (Bergamot)
        if label_vis:
            x -= (orn_gap + mamar_anaf_gap) + mamar_anaf_orn_w * 0.5
            draw_svg_file_ornament(c, mamar_anaf_svg, x, header_y + 3.5, mamar_anaf_orn_w, gray=0.48)
            x -= mamar_anaf_orn_w * 0.5 + (small_orn_gap + mamar_anaf_gap)
            c.setFillColorRGB(0.15, 0.15, 0.15)

    # Anaf label
    if label_vis:
        lw = Wid(label_vis, HEADING_FONT, HEADER_ANAF_FS)
        x -= lw
        c.setFont(HEADING_FONT, HEADER_ANAF_FS)
        c.setFillColorRGB(0.15, 0.15, 0.15)
        c.drawString(x, header_y, label_vis)
        
        leader_stop_x = x - 4

    # Leader: fades out near the ornament
    rule_y = header_y - 1.35
    _draw_header_leader(c, rule_y,
                        x_left=C_LEFT + pg_w + 4,
                        x_right=leader_stop_x,
                        fade_side='left')

def draw_running_header_even(c, page_num, anaf_label, anaf_name):
    """Even (verso) page header: inside=title + ornament, outside=page number."""
    header_y = C_TOP - 2
    pg_str = heb_page(page_num)

    title_vis = vis(livorna_fix_quotes(BOOK_TITLE))

    # Title with glow effect
    title_x = C_LEFT + 2
    _draw_title_with_glow(c, title_x, header_y, title_vis,
                           EFT_TALPIYOT_FONT, HEADER_TITLE_FS)
    tw = Wid(title_vis, EFT_TALPIYOT_FONT, HEADER_TITLE_FS)
    content_end_x = title_x + tw

    # Page number at outer RIGHT edge - Hebrew letters in Livorna Regular
    pw = sum(Wid(ch, "livornaregular", PGNUM_FS) for ch in pg_str)
    draw_rtl_token(c, pg_str, "livornaregular", PGNUM_FS, C_RIGHT - pw, header_y, color=(0.15, 0.15, 0.15))

    # Shortened rule: between title and page number, just under baseline
    rule_y = header_y - 1.2
    _draw_header_leader(c, rule_y,
                        x_left=content_end_x + 2,
                        x_right=C_RIGHT - pw - 4,
                        fade_side='right')

def draw_divider_line(c, y_top, y_bot):
    div_x = C_LEFT + COL_W + COL_GAP / 2
    c.setStrokeColorRGB(0.6, 0.6, 0.6)
    c.setLineWidth(0.4)
    c.line(div_x, y_bot, div_x, y_top)

# ═══════════════════════════════════════════════════════════════════════════
# TABLE OF CONTENTS PAGE
# ═══════════════════════════════════════════════════════════════════════════

TOC_TITLE_FS   = S.TOC_TITLE_FS
TOC_MAMAR_FS   = S.TOC_MAMAR_FS
TOC_ENTRY_FS   = S.TOC_ENTRY_FS
TOC_ENTRY_LH   = TOC_ENTRY_FS * 1.6
TOC_DOT_CHAR   = S.TOC_DOT_CHAR

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
    strip = strip.point(lambda p: int(max(0, min(255, p * 0.60))))
    rgb = Image.merge("RGB", (strip, strip, strip))
    tmp = tempfile.NamedTemporaryFile(prefix="palot_contact_strip_", suffix=".jpg", delete=False)
    rgb.save(tmp.name, "JPEG", quality=95)
    _CONTACT_STRIP_PATH = tmp.name
    return _CONTACT_STRIP_PATH

def draw_contact_page(c):
    """Contact page for front matter (final page 2 / even)."""
    set_page_geometry(2)
    x_center = (C_LEFT + C_RIGHT) / 2
    front_font = S.TITLE_FONT_NAME

    # Bottom marble strip, matching title-page treatment
    strip_path = _make_contact_strip_image()
    strip_h = PAGE_H * 0.12
    strip_y = PAGE_H * 0.055 + (0.2 * 72)
    strip_top = strip_y + strip_h

    bw = C_W * S.CONTACT_BOX_WIDTH_FRAC
    bh = PAGE_H * S.CONTACT_BOX_HEIGHT_FRAC
    bx = x_center - bw / 2
    by = -PAGE_H * 0.04
    radius = S.CONTACT_BOX_RADIUS

    c.setFillColorRGB(S.CONTACT_BOX_FILL, S.CONTACT_BOX_FILL, S.CONTACT_BOX_FILL)
    c.roundRect(bx, by, bw, bh, radius, fill=1, stroke=0)
    c.setStrokeColorRGB(S.CONTACT_BOX_STROKE_OUTER, S.CONTACT_BOX_STROKE_OUTER, S.CONTACT_BOX_STROKE_OUTER)
    c.setLineWidth(0.95)
    c.roundRect(bx, by, bw, bh, radius, fill=0, stroke=1)
    c.setStrokeColorRGB(S.CONTACT_BOX_STROKE_MID, S.CONTACT_BOX_STROKE_MID, S.CONTACT_BOX_STROKE_MID)
    c.setLineWidth(0.4)
    c.roundRect(bx + 2.4, by + 2.4, bw - 4.8, bh - 4.8, radius - 1.2, fill=0, stroke=1)
    c.setStrokeColorRGB(S.CONTACT_BOX_STROKE_INNER, S.CONTACT_BOX_STROKE_INNER, S.CONTACT_BOX_STROKE_INNER)
    c.setLineWidth(0.18)
    c.roundRect(bx + 4.8, by + 4.8, bw - 9.6, bh - 9.6, radius - 2.2, fill=0, stroke=1)

    lines = [
        (text, front_font, size, gray)
        for (text, size, gray) in S.CONTACT_LINES
    ]

    face = pdfmetrics.getFont(front_font).face
    asc = (face.ascent or 800) / 1000.0
    start_y = by + bh * 0.89
    y = start_y
    for text, font, size, gray in lines:
        if text is None:
            y -= size
            continue
        vis_text = vis(livorna_fix_quotes(strip_nikud(text))) if any('\u0590' <= ch <= '\u05FF' for ch in text) else text
        tw = Wid(vis_text, font, size)
        c.setFont(font, size)
        c.setFillColorRGB(gray, gray, gray)
        baseline = y - asc * size * 0.08
        c.drawString(x_center - tw / 2, baseline, vis_text)
        y -= size * 1.42

    # Draw strip last so it stays visible in front of the bottom-emerging box
    if strip_path:
        c.drawImage(ImageReader(strip_path), 0, strip_y, width=PAGE_W, height=strip_h, mask="auto")
        c.saveState()
        c.setLineWidth(1.8)
        c.setStrokeColorRGB(0.28, 0.28, 0.28)
        c.line(0, strip_top, PAGE_W, strip_top)
        c.setLineWidth(0.8)
        c.setStrokeColorRGB(0.78, 0.78, 0.78)
        c.line(0, strip_top - 2.5, PAGE_W, strip_top - 2.5)
        c.restoreState()

def draw_toc_page(c, anaf_entries, page_offset):
    """Draw a single תוכן הענינים (table of contents) page.

    anaf_entries: list of (label_str, name_str, page_num_int)
    page_offset:  the page number of the *first content page* (so TOC page
                  is page_offset - 1; the content pages start at page_offset).
    """
    # TOC is page 3 in the final book layout (odd page geometry)
    set_page_geometry(3)

    # ── Title: תוכן הענינים ──────────────────────────────────────────────
    title_text = vis("תוכן הענינים")
    title_y = C_TOP - 16
    tw = Wid(title_text, HEADING_BOLD_FONT, TOC_TITLE_FS)
    c.setFont(HEADING_BOLD_FONT, TOC_TITLE_FS)
    c.setFillColorRGB(0.1, 0.1, 0.1)
    c.drawString(C_LEFT + (C_W - tw) / 2, title_y, title_text)

    # ── Mamar label + name ──────────────────────────────────────────────
    title_orn_y = title_y - TOC_TITLE_FS * 1.35
    draw_gray_top_orn(c, C_LEFT + C_W / 2, title_orn_y, 66, gray=0.65)

    mamar_y = title_orn_y - TOC_TITLE_FS * 4.2
    if MAMAR_NAME:
        mamar_label = vis(livorna_fix_quotes(strip_nikud("מאמר")))
        mamar_name_clean = livorna_fix_quotes(strip_nikud(MAMAR_NAME))
        mamar_label_fs = TOC_MAMAR_FS * 0.82
        mamar_gap = TOC_MAMAR_FS * 0.55
        mamar_name_max_w = C_W * 0.72
        mamar_name_lines_words = wrap_words(mamar_name_clean.split(), mamar_name_max_w, HEADING_BOLD_FONT, TOC_MAMAR_FS)
        mamar_name_lines = [vis(' '.join(line_words)) for line_words in mamar_name_lines_words]
        mamar_name_lh = TOC_MAMAR_FS * 1.25
        mw = max(
            Wid(mamar_label, HEADING_BOLD_FONT, mamar_label_fs),
            max((Wid(line, HEADING_BOLD_FONT, TOC_MAMAR_FS) for line in mamar_name_lines), default=0),
        )
        mx = C_LEFT + (C_W - mw) / 2
        pad_x = 18
        pad_y = 11
        bx = mx - pad_x
        by = mamar_y - pad_y + 0.5
        bw = mw + pad_x * 2
        name_block_h = (len(mamar_name_lines) - 1) * mamar_name_lh + TOC_MAMAR_FS if mamar_name_lines else TOC_MAMAR_FS
        bh = mamar_label_fs + name_block_h + mamar_gap + pad_y * 2
        # Box: same simple light shading language as the contact page
        c.setFillColorRGB(0.975, 0.975, 0.975)
        c.roundRect(bx, by, bw, bh, 9, fill=1, stroke=0)
        c.setStrokeColorRGB(0.66, 0.66, 0.66)
        c.setLineWidth(0.95)
        c.roundRect(bx, by, bw, bh, 9, fill=0, stroke=1)
        c.setStrokeColorRGB(0.92, 0.92, 0.92)
        c.setLineWidth(0.4)
        c.roundRect(bx + 2.4, by + 2.4, bw - 4.8, bh - 4.8, 7.8, fill=0, stroke=1)
        c.setStrokeColorRGB(0.80, 0.80, 0.80)
        c.setLineWidth(0.18)
        c.roundRect(bx + 4.8, by + 4.8, bw - 9.6, bh - 9.6, 6.6, fill=0, stroke=1)
        c.setFillColorRGB(0.24, 0.24, 0.24)
        face = pdfmetrics.getFont(HEADING_BOLD_FONT).face
        label_asc = (face.ascent or 800) / 1000.0 * mamar_label_fs
        label_desc = abs((face.descent or -200) / 1000.0) * mamar_label_fs
        name_asc = (face.ascent or 800) / 1000.0 * TOC_MAMAR_FS
        name_desc = abs((face.descent or -200) / 1000.0) * TOC_MAMAR_FS
        name_block_h_metrics = (label_asc * 0) + ((len(mamar_name_lines) - 1) * mamar_name_lh if mamar_name_lines else 0) + name_asc + name_desc
        block_h = (label_asc + label_desc) + mamar_gap + name_block_h_metrics
        cy = by + (bh - block_h) / 2
        name_top_y = cy + name_block_h_metrics - name_asc
        label_y = cy + (name_asc + name_desc) + mamar_gap + label_desc
        c.setFont(HEADING_BOLD_FONT, mamar_label_fs)
        c.drawString(C_LEFT + (C_W - Wid(mamar_label, HEADING_BOLD_FONT, mamar_label_fs)) / 2, label_y, mamar_label)
        c.setFont(HEADING_BOLD_FONT, TOC_MAMAR_FS)
        for i, line in enumerate(mamar_name_lines):
            line_y = name_top_y - i * mamar_name_lh
            c.drawString(C_LEFT + (C_W - Wid(line, HEADING_BOLD_FONT, TOC_MAMAR_FS)) / 2, line_y, line)

    # No rule under sub-title
    rule_y = mamar_y - TOC_MAMAR_FS * 2.2

    # ── Entry list ──────────────────────────────────────────────────────
    # Layout: entries start from the right edge.
    # Label (ענף א׳) is right-aligned to a fixed right column.
    # Anaf names are right-aligned to a fixed name column to the left of label.
    # Dot leaders fill between page number on the left and the name.
    # All in Livorna; label not bold, name bold.

    entry_lh = TOC_ENTRY_FS * 2.32
    entry_top = rule_y - entry_lh * 1.7
    label_col_w = max(Wid(vis(livorna_fix_quotes(strip_nikud(lbl))), HEADING_FONT, TOC_ENTRY_FS)
                      for lbl, _, _ in anaf_entries) + 8
    toc_inset = C_W * 0.06
    label_right_x = C_RIGHT - toc_inset
    name_right_x = label_right_x - label_col_w - 22
    pgnum_x = C_LEFT + toc_inset
    dot_w = Wid(TOC_DOT_CHAR, HEADING_FONT, TOC_ENTRY_FS)

    y = entry_top
    for label_str, name_str, pg in anaf_entries:
        label_v = vis(livorna_fix_quotes(strip_nikud(label_str)))
        name_v  = vis(livorna_fix_quotes(strip_nikud(name_str)))
        pg_v    = heb_page(pg)

        # Label — right-aligned at outer right, not bold
        lw = Wid(label_v, HEADING_FONT, TOC_ENTRY_FS)
        c.setFont(HEADING_FONT, TOC_ENTRY_FS)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.drawString(label_right_x - lw, y, label_v)

        draw_gray_top_orn(c, label_right_x - lw - 14, y + 3.0, 20)

        # Name — bold, right-aligned so the entry begins from the right side
        nw = Wid(name_v, HEADING_BOLD_FONT, TOC_ENTRY_FS)
        c.setFont(HEADING_BOLD_FONT, TOC_ENTRY_FS)
        c.drawString(name_right_x - nw, y, name_v)

        # Page number — left-aligned at outer left
        pw = sum(Wid(ch, HEADING_FONT, TOC_ENTRY_FS) for ch in pg_v)
        draw_rtl_token(c, pg_v, HEADING_FONT, TOC_ENTRY_FS, pgnum_x, y, color=(0.1, 0.1, 0.1))

        # Dot leaders between page number and the start of the name
        dot_start = pgnum_x + pw + dot_w * 2
        dot_end   = name_right_x - nw - dot_w * 2
        dot_x = dot_start
        c.setFillColorRGB(0.45, 0.45, 0.45)
        c.setFont(HEADING_FONT, TOC_ENTRY_FS)
        while dot_x + dot_w <= dot_end:
            c.drawString(dot_x, y, TOC_DOT_CHAR)
            dot_x += dot_w * 2   # space between dots

        c.setFillColorRGB(0.1, 0.1, 0.1)
        y -= entry_lh

    # Ornament after the last entry — post-anaf style with generous breathing space
    orn_y = y + entry_lh - LH * 2.5
    draw_gray_bottom_orn(c, C_LEFT + C_W / 2, orn_y, 66, gray=0.65, glow=False)

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
    clean = word_without_fn(word)
    w = Wid(vis(clean), font, size) if clean else 0
    for m in FN_MARKER_RE.finditer(word):
        fn_num = int(m.group(1))
        w += Wid(vis(heb_num(fn_num)), REG_FONT, FN_SUPER_FS)
    return w

def word_w(word, font, size):
    if word_has_fn(word): return word_display_width(word, font, size)
    return Wid(vis(word), font, size)

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
    _paren_just_closed = False     # persists across segments

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

        for ci, ch in enumerate(raw_text):
            # After closing paren, absorb trailing punctuation (.,;:)
            # so it stays in the paren-sized word and doesn't become a
            # separate regular-sized token that bidi can detach.
            if _paren_just_closed:
                _paren_just_closed = False
                if ch in '.,;:':
                    # Append punctuation to the last paren-sized frag
                    # (may be in seg_frags from this segment or fragments
                    #  from a prior segment)
                    if seg_frags:
                        lw, lf, lsz = seg_frags[-1]
                        seg_frags[-1] = (lw + ch, lf, lsz)
                    elif fragments:
                        lw, lf, lsz, lls = fragments[-1]
                        fragments[-1] = (lw + ch, lf, lsz, lls)
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
            # Penalise non-last lines that are too sparse (< 82% fill)
            pen = sum(max(0.0, 0.82 - f) * 10 for i, f in enumerate(fs) if i < nT - 1)
            # Prefer tighter packing: penalise low mean fill of non-last lines
            non_last = [f for i, f in enumerate(fs) if i < nT - 1] or fs
            mean_fill = sum(non_last) / len(non_last)
            # Score = variance + underfill penalty + tightness penalty
            return statistics.pstdev(fs) + pen + max(0.0, 0.92 - mean_fill) * 2.0

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
            c.drawString(cx - ww, y, vis(wstr))

    # Don't stretch sparse lines — if fill is under 55%, treat as a last line
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
            if tw <= col_w:
                draw_x = x_right - (col_w + tw) / 2
                c.setFont(S.H2_FONT_NAME, SUB_FS)
                c.setFillColorRGB(0, 0, 0)
                c.drawString(draw_x, y, text_vis)
            else:
                sub_lines = wrap_words(text_clean.split(), col_w * 0.95, S.H2_FONT_NAME, SUB_FS)
                c.setFont(S.H2_FONT_NAME, SUB_FS)
                c.setFillColorRGB(0, 0, 0)
                line_y = y
                for sub_line_words in sub_lines:
                    lt = vis(' '.join(sub_line_words))
                    lw2 = Wid(lt, S.H2_FONT_NAME, SUB_FS)
                    draw_x = x_right - (col_w + lw2) / 2
                    c.drawString(draw_x, line_y, lt)
                    line_y -= SUB_LH
            y -= (n_sub_lines - 1) * SUB_LH + step + SUB_AFTER_GAP
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
    """Draw ענף heading: Line 1 = ornament + label + ornament, Line 2 = name."""
    x_center = (C_LEFT + C_RIGHT) / 2
    INTER = ANAF_FS * 0.8

    label_clean = livorna_fix_quotes(strip_nikud(label))
    label_vis = vis(label_clean)
    gap = S.H1_ORNAMENT_GAP
    orn_w = 28
    ow = orn_w
    lw1 = Wid(label_vis, HEADING_BOLD_FONT, ANAF_FS)
    total_w = ow + gap + lw1 + gap + ow
    sx = x_center + total_w / 2
    c.setFillColorRGB(0, 0, 0)

    # Ornaments in light gray
    draw_gray_top_orn(c, sx - ow / 2, y + 4.4, orn_w)
    draw_gray_top_orn(c, sx - ow - gap - lw1 - gap - ow / 2, y + 4.4, orn_w)

    # Label in black
    c.setFont(HEADING_BOLD_FONT, ANAF_FS)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(sx - ow - gap - lw1, y, label_vis)

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
        font = HEADING_BOLD_FONT
        width = C_W * 0.95
        tw = Wid(text_vis, font, fs)
        c.setFont(font, fs)
        c.setFillColorRGB(0, 0, 0)
        if tw <= width:
            c.drawString(x_center - tw / 2, y, text_vis)
            return fs
        lines = wrap_words(text_clean.split(), width, font, fs)
        cy = y
        for line_words in lines:
            line_vis = vis(' '.join(line_words))
            lw = Wid(line_vis, font, fs)
            c.drawString(x_center - lw / 2, cy, line_vis)
            cy -= fs * 1.3
        return len(lines) * fs * 1.3
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
            lines = wrap_words(words, C_W * 0.95, font, fs)
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
        font = HEADING_BOLD_FONT
        text_clean = livorna_fix_quotes(strip_nikud(text))
        width = C_W * 0.95
        tw = Wid(vis(text_clean), font, fs)
        text_h = fs if tw <= width else len(wrap_words(text_clean.split(), width, font, fs)) * fs * 1.3
        return text_h + HEADING_AFTER_GAP + 18
    elif kind == 'anaf_sub':
        fs = SEC_FS
        font = HEADING_BOLD_FONT
        text_clean = strip_nikud(text)
        tw = Wid(vis(text_clean), font, fs)
        text_h = fs if tw <= C_W else len(wrap_words(text_clean.split(), C_W * 0.95, font, fs)) * fs * 1.3
        return text_h + HEADING_AFTER_GAP
    else:
        fs = SEC_FS
        font = HEADING_BOLD_FONT
        text_clean = strip_nikud(text)
        tw = Wid(vis(text_clean), font, fs)
        text_h = fs if tw <= C_W else len(wrap_words(text_clean.split(), C_W * 0.95, font, fs)) * fs * 1.3
        return text_h + HEADING_AFTER_GAP


def para_draw_height(lines, extra_lh=0.0):
    if not lines: return 0.0
    step = LH + extra_lh
    height = 0.0
    for ld in lines:
        if ld.get('type') == 'subhead':
            n = ld.get('nlines', 1)
            height += step + (n - 1) * SUB_LH
        else:
            height += ld.get('nlines', 1) * step
    if lines[-1].get('type') == 'subhead':
        height += SUB_AFTER_GAP
    return height


def _count_fns_in_lines(lines):
    count = 0
    for ld in lines:
        for word in ld.get('words', []):
            count += len(FN_MARKER_RE.findall(tw_str(word)))
    return count


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
    """Count only gaps that can actually be stretched/shrunk.
    Locked sub-after gaps are excluded. Unlocked sub-after gaps count."""
    count = 0
    for ci in range(len(cd) - 1):
        cur_kind, cur_pi, cur_k = cd[ci]
        if _sub_after_is_locked(para_run, cur_kind, cur_pi, cur_k):
            continue  # locked sub-after gap — skip
        count += 1
    return count

def _is_subhead_ending_frag(para_run, kind, pi, k):
    para = para_run[pi]
    if para.get('is_subhead'): return True
    if kind == 'head': lines = para['lines'][:k]
    elif kind == 'tail': lines = para['lines'][k:]
    else: lines = para['lines']
    return bool(lines and lines[-1].get('type') == 'subhead')

def _subhead_visual_discount(para_run, cd):
    """Extra whitespace a subhead introduces vs normal paragraph gaps.
    A subhead has before-gap (SUB_BEFORE_FRAG_GAP) replacing a normal gap
    (PSEP), plus SUB_AFTER_GAP baked into para_draw_height.
    This whitespace is real vertical space but contains no text, making
    the column's last text line sit higher than the mathematical height
    would suggest.  Discounting this during scoring lets the balancer
    give the subhead column slightly more content to compensate."""
    discount = 0.0
    for ci, (kind, pi, k) in enumerate(cd):
        if _is_subhead_frag(para_run, kind, pi, k):
            discount += SUB_AFTER_GAP
            if ci > 0:
                discount += SUB_BEFORE_FRAG_GAP - PSEP
    return discount

def _sub_after_is_locked(para_run, kind, pi, k):
    """True if this sub-after gap is non-stretchable for capacity/scoring."""
    if ALLOW_SUB_AFTER_STRETCH or _force_sub_after_stretchable:
        return False
    return _is_subhead_ending_frag(para_run, kind, pi, k)

def _inter_frag_gap(para_run, cd, ci, es):
    if ci + 1 >= len(cd):
        return max(MIN_PARA_GAP, PSEP + es)
    cur_kind, cur_pi, cur_k = cd[ci]
    nxt_kind, nxt_pi, nxt_k = cd[ci + 1]
    if _is_subhead_ending_frag(para_run, cur_kind, cur_pi, cur_k):
        # When last-resort flag is set, allow sub-after gap to stretch.
        if _force_sub_after_stretchable:
            return max(MIN_SUB_AFTER_GAP, min(MAX_SUB_AFTER_GAP, SUB_AFTER_FRAG_GAP + es))
        return SUB_AFTER_FRAG_GAP
    if _is_subhead_frag(para_run, nxt_kind, nxt_pi, nxt_k):
        # Floor ensures before-gap stays visibly larger than SUB_AFTER_GAP
        min_sub_before = SUB_AFTER_GAP + LH * 0.2
        return max(min_sub_before, SUB_BEFORE_FRAG_GAP + es)
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
        mid = (body_idxs[0] + body_idxs[-1]) / 2
        pairs = []
        for i in range(len(body_idxs) - 1):
            a, b = body_idxs[i], body_idxs[i + 1]
            if b == a + 1:
                pairs.append((abs((a + b) / 2 - mid), a, b))
        pairs.sort()
        for _, a, b in pairs:
            ld_a, ld_b = lines[a], lines[b]
            combined = ld_a['words'] + ld_b['words']
            sp = Wid(' ', REG_FONT, FS)
            total_w = sum(tw_w(w) for w in combined) + sp * (len(combined) - 1)
            if total_w <= ld_a['width'] + 0.01:
                new_lines = list(lines)
                merged = {'type': 'body', 'words': combined, 'xr_off': ld_a['xr_off'],
                          'width': ld_a['width'], 'bold_word': None}
                new_lines[a] = merged
                del new_lines[b]
                if _valid(new_lines): return new_lines
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
    alone may leave behind."""
    diff = h1_final - h2_final
    if abs(diff) <= 0.05:
        return 0.0, 0.0
    pad = min(abs(diff), MAX_TOP_PAD)
    if pad <= 0.05:
        return 0.0, 0.0
    if diff > 0:
        return 0.0, pad
    return pad, 0.0


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
    n = len(para_run)
    lc = [p['nlines'] for p in para_run]

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

    def _svd(cd):
        """Local shorthand: subhead visual discount for this para_run."""
        return _subhead_visual_discount(para_run, cd)

    def _max_stretch(cd):
        """Total stretch capacity for gaps in this column descriptor.
        Locked sub-after gaps are excluded; unlocked ones add MAX_SUB_AFTER_GAP."""
        MAX_E_PER_GAP = S.MAX_GAP_STRETCH_PT
        total = 0.0
        for ci in range(len(cd) - 1):
            cur_kind, cur_pi, cur_k = cd[ci]
            if _sub_after_is_locked(para_run, cur_kind, cur_pi, cur_k):
                continue  # locked sub-after gap
            if _is_subhead_ending_frag(para_run, cur_kind, cur_pi, cur_k):
                total += MAX_SUB_AFTER_GAP  # unlocked sub-after
            else:
                total += MAX_E_PER_GAP
        return total

    def _max_shrink(cd):
        """Total shrink capacity for gaps in this column descriptor."""
        total = 0.0
        for ci in range(len(cd) - 1):
            cur_kind, cur_pi, cur_k = cd[ci]
            base = _inter_frag_gap(para_run, cd, ci, 0.0)
            if _is_subhead_ending_frag(para_run, cur_kind, cur_pi, cur_k):
                floor = MIN_SUB_AFTER_GAP
            else:
                floor = MIN_PARA_GAP
            total += max(0.0, base - floor)
        return total

    def _compute_padding(c1d, c2d, h1, h2):
        """3-stage column balancing — ONLY stretch inter-paragraph gaps.
        Line-height stretch (elh) is absolute last resort and should rarely fire.

        Stage 1: Stretch gaps on the shorter column
        Stage 2: Shrink gaps on the taller column
        Stage 3: Last resort only — line-height stretch on shorter column (up to 10% of LH)

        KEY: We use per-gap max stretch that is generous enough to absorb most diffs
        through gap stretching alone, so line spacing within paragraphs stays normal.
        """
        MAX_E_PER_GAP = S.MAX_GAP_STRETCH_PT   # reasonable max per gap (~26pt) — must match _effective_residual
        MAX_ELH = S.MAX_LINE_STRETCH_PT        # DISABLED by default (0.0): never stretch line spacing within paragraphs

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
        # Balance on raw heights — what matters is that both columns end at the
        # same Y coordinate.  The visual discount was causing _compute_padding
        # to return zero when raw heights differed by up to ~10pt (discount
        # exactly cancelled the diff), leaving columns bottom-misaligned.
        diff = ah1 - ah2  # positive = col1 raw-taller
        if abs(diff) <= 0.05: return 0.0, 0.0, 0.0, 0.0
        gaps1 = _stretchable_gaps(c1d, para_run)
        gaps2 = _stretchable_gaps(c2d, para_run)

        e1, e2, elh1, elh2 = 0.0, 0.0, 0.0, 0.0
        remaining = abs(diff)

        if diff > 0:
            # col1 is taller — stretch col2 gaps, shrink col1 gaps, then stretch col2 lines
            # Stage 1: stretch col2 gaps
            if gaps2 > 0:
                can_stretch = _max_stretch(c2d)
                used = min(remaining, can_stretch)
                e2 = used / gaps2
                remaining -= used
            # Stage 2: shrink col1 gaps
            if remaining > 0.05 and gaps1 > 0:
                can_shrink = _max_shrink(c1d)
                used = min(remaining, can_shrink)
                e1 = -used / gaps1
                remaining -= used
            # Stage 3: line-height stretch on col2 — LAST RESORT
            if remaining > 0.05:
                lines2 = _line_count(c2d)
                if lines2 > 0:
                    can_elh = lines2 * MAX_ELH
                    used = min(remaining, can_elh)
                    elh2 = used / lines2
                    remaining -= used
        else:
            # col2 is taller — stretch col1 gaps, shrink col2 gaps, then stretch col1 lines
            # Stage 1: stretch col1 gaps
            if gaps1 > 0:
                can_stretch = _max_stretch(c1d)
                used = min(remaining, can_stretch)
                e1 = used / gaps1
                remaining -= used
            # Stage 2: shrink col2 gaps
            if remaining > 0.05 and gaps2 > 0:
                can_shrink = _max_shrink(c2d)
                used = min(remaining, can_shrink)
                e2 = -used / gaps2
                remaining -= used
            # Stage 3: line-height stretch on col1 — LAST RESORT
            if remaining > 0.05:
                lines1 = _line_count(c1d)
                if lines1 > 0:
                    can_elh = lines1 * MAX_ELH
                    used = min(remaining, can_elh)
                    elh1 = used / lines1
                    remaining -= used

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

    # Single para
    if n == 1:
        nL = lc[0]
        if nL < 2:
            return [('whole', 0, None)], [], 0.0, 0.0, 0.0, 0.0
        if nL <= MIN_PART_LINES * 2 - 1:
            best_k = nL // 2
            c1d = [('head', 0, best_k)]
            c2d = [('tail', 0, best_k)]
            h1, h2 = best_k * LH, (nL - best_k) * LH
            e1, e2, elh1, elh2 = _compute_padding(c1d, c2d, h1, h2)
            return c1d, c2d, e1, e2, elh1, elh2
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
                        return rc1d, rc2d, re1, re2, relh1, relh2
        e1, e2, elh1, elh2 = _compute_padding(c1d, c2d, h1, h2)
        return c1d, c2d, e1, e2, elh1, elh2

    # Multiple paras: exhaustive search
    best_residual = float('inf')
    best_h1, best_h2 = 0.0, 0.0
    best_c1d, best_c2d = None, None

    def _effective_residual(c1d, c2d, h1, h2):
        """Score a split — lower is better.
        Returns hard_residual = height diff that can't be absorbed at all.
        Uses _max_stretch/_max_shrink which respect _sub_after_is_locked.
        """
        diff = abs(h1 - h2)
        if diff <= 0.05: return 0.0
        if h1 > h2:
            grow_cap = _max_stretch(c2d)
            shrink_cap = _max_shrink(c1d)
            top_pad_cap = MAX_TOP_PAD if _starts_with_subhead_frag(c2d) else 0.0
        else:
            grow_cap = _max_stretch(c1d)
            shrink_cap = _max_shrink(c2d)
            top_pad_cap = MAX_TOP_PAD if _starts_with_subhead_frag(c1d) else 0.0
        return max(0.0, diff - grow_cap - shrink_cap - top_pad_cap)

    def _split_quality(c1d, c2d, h1, h2):
        """Return (residual, max_per_gap_stretch) — lower is better for both."""
        res = _effective_residual(c1d, c2d, h1, h2)
        diff = abs(h1 - h2)
        if diff <= 0.05: return (0.0, 0.0)
        gaps1 = _stretchable_gaps(c1d, para_run)
        gaps2 = _stretchable_gaps(c2d, para_run)
        # Compute what the per-gap stretch would be on the shorter column
        if h1 > h2:
            per_gap = diff / gaps2 if gaps2 > 0 else 9999.0
        else:
            per_gap = diff / gaps1 if gaps1 > 0 else 9999.0
        return (res, per_gap)

    def _combined_score(c1d, c2d, h1, h2):
        """Single score combining residual + per-gap stretch penalty +
        subhead visual imbalance penalty.
        Lower is better.

        When per-gap stretch exceeds a comfortable threshold (PSEP ≈ 7pt),
        the excess is penalized quadratically.  This strongly discourages
        splits that require grotesque gap stretching even when they have
        zero hard residual.

        Subhead visual discount: a column with a subheading has extra
        whitespace that makes its last text line sit higher.  We treat
        the net discount imbalance as additional effective height difference.
        """
        # Apply subhead visual discount to get "visually effective" heights
        vh1 = h1 - _svd(c1d)
        vh2 = h2 - _svd(c2d)
        res, per_gap = _split_quality(c1d, c2d, vh1, vh2)
        # Comfortable threshold: PSEP.  Beyond this gaps start looking bad.
        COMFORT = PSEP
        # Quadratic penalty: excess^2 / LH.  At 20pt excess this is ~22pt penalty.
        # At 5pt excess this is only ~1.4pt — mild.
        excess = max(0.0, per_gap - COMFORT)
        penalty = (excess * excess) / LH
        return res + penalty

    def _is_better(c1d, c2d, h1, h2):
        nonlocal best_residual, best_h1, best_h2
        new_score = _combined_score(c1d, c2d, h1, h2)
        old_score = _combined_score(best_c1d, best_c2d, best_h1, best_h2) if best_c1d else 9999.0
        if new_score < old_score - 0.1:
            return True
        if abs(new_score - old_score) <= 0.1:
            # Tie-break: prefer col1 >= col2 (RTL reading order)
            if h1 >= h2 and best_h1 < best_h2:
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
            best_residual = _effective_residual(c1d, c2d, h1, h2)
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
                best_residual = _effective_residual(c1d, c2d, h1, h2)
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
    # LH * 0.5 (~9pt) means gap stretching truly couldn't handle the imbalance.
    REFLOW_THRESHOLD = S.REFLOW_THRESHOLD_PT
    # Reflow must improve residual by at least this much to be worth
    # the justification degradation from changing paragraph line counts.
    REFLOW_MIN_IMPROVEMENT = S.REFLOW_MIN_IMPROVEMENT_PT

    if S.REFLOW_ENABLED and residual > REFLOW_THRESHOLD:
        def _try_all_splits(new_lc_local):
            """Find best split after reflow, using _effective_residual for comparison."""
            loc_best = None
            for sp in range(1, n):
                if para_run[sp - 1].get('is_subhead'): continue
                rc1d = [('whole', i, None) for i in range(sp)]
                rc2d = [('whole', i, None) for i in range(sp, n)]
                if _ends_with_orphaned_subhead(rc1d): continue
                if not allow_trailing_subhead and _ends_with_orphaned_subhead(rc2d): continue
                rh1, rh2 = _ph_cd(rc1d), _ph_cd(rc2d)
                re1, re2, relh1, relh2 = _compute_padding(rc1d, rc2d, rh1, rh2)
                rfh1 = _final_h(rc1d, re1, relh1)
                rfh2 = _final_h(rc2d, re2, relh2)
                # FIX: use _effective_residual so a reflow that gives a gap-less
                # column a new gap is recognised as a big improvement even if raw
                # diff is still > residual.
                r_eff = _effective_residual(rc1d, rc2d, rfh1, rfh2)
                r_raw = abs(rfh1 - rfh2)
                if r_eff < residual - REFLOW_MIN_IMPROVEMENT:
                    if loc_best is None or r_eff < loc_best[0]:
                        loc_best = (r_eff, list(rc1d), list(rc2d), re1, re2, relh1, relh2)
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
                    re1, re2, relh1, relh2 = _compute_padding(rc1d, rc2d, rh1, rh2)
                    rfh1, rfh2 = _final_h(rc1d, re1, relh1), _final_h(rc2d, re2, relh2)
                    r_eff = _effective_residual(rc1d, rc2d, rfh1, rfh2)
                    if r_eff < residual - REFLOW_MIN_IMPROVEMENT:
                        if loc_best is None or r_eff < loc_best[0]:
                            loc_best = (r_eff, list(rc1d), list(rc2d), re1, re2, relh1, relh2)
            return loc_best

        best_reflow = None
        for reflow_pi in range(n):
            if para_run[reflow_pi].get('is_subhead'): continue
            orig_lines = para_run[reflow_pi]['lines']
            orig_nL = para_run[reflow_pi]['nlines']
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
                hit = _try_all_splits(new_lc)
                if hit is not None:
                    r_eff, rc1d, rc2d, re1, re2, relh1, relh2 = hit
                    if best_reflow is None or r_eff < best_reflow[0]:
                        best_reflow = (r_eff, [(reflow_pi, reflowed, rnL)],
                                       rc1d, rc2d, re1, re2, relh1, relh2)
                para_run[reflow_pi] = saved_para

        # Dual-paragraph reflows — FIX: run when single-para didn't get below REFLOW_THRESHOLD
        if best_reflow is None or best_reflow[0] > REFLOW_THRESHOLD:
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
                            hit = _try_all_splits(new_lc)
                            if hit is not None:
                                r_eff, rc1d, rc2d, re1, re2, relh1, relh2 = hit
                                if best_reflow is None or r_eff < best_reflow[0]:
                                    best_reflow = (r_eff, [(pi_a, ra, rnL_a), (pi_b, rb, rnL_b)],
                                                   rc1d, rc2d, re1, re2, relh1, relh2)
                            para_run[pi_b] = saved_b
                    para_run[pi_a] = saved_a

        if best_reflow is not None:
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

    # ── Last-resort: unlock sub-after gaps if balance still poor ──
    # When residual > 5pt after all reflow + gap optimization, temporarily
    # allow heading→paragraph gaps to stretch and absorb the remainder.
    if not ALLOW_SUB_AFTER_STRETCH and best_c1d is not None and best_c2d:
        lr_h1 = _final_h(best_c1d, e1, elh1) if best_c1d else 0.0
        lr_h2 = _final_h(best_c2d, e2, elh2) if best_c2d else 0.0
        lr_residual = abs(lr_h1 - lr_h2)
        if lr_residual > 5.0:
            _force_sub_after_stretchable = True
            lr_raw_h1 = _ph_cd(best_c1d)
            lr_raw_h2 = _ph_cd(best_c2d)
            e1, e2, elh1, elh2 = _compute_padding(best_c1d, best_c2d, lr_raw_h1, lr_raw_h2)
            lrf_h1 = _final_h(best_c1d, e1, elh1)
            lrf_h2 = _final_h(best_c2d, e2, elh2)
            print(f"  [LR] sub-after unlocked: raw={lr_raw_h1:.2f}/{lr_raw_h2:.2f} -> final={lrf_h1:.2f}/{lrf_h2:.2f} e1={e1:.2f} e2={e2:.2f}")

    return best_c1d, best_c2d, e1, e2, elh1, elh2


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
                   elh1=0.0, elh2=0.0, e_top1=0.0, e_top2=0.0, page_num=0):
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

    def draw_col(cd, x_right, es, elh, fn_base, e_top=0.0):
        y = y_top - e_top
        for ci, (kind, pi, k) in enumerate(cd):
            para = para_run[pi]
            para_ends = para.get('is_para_end', True)
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
            y -= ht
            if ci < len(cd) - 1:
                gap = _inter_frag_gap(para_run, cd, ci, es)
                y -= gap
        return (y_top - e_top) - y

    fns_in_col1 = _count_fns_in_col(c1d)
    h1 = draw_col(c1d, COL1_RIGHT, e1, elh1, fn_counter_before, e_top=e_top1)
    h2 = draw_col(c2d, COL2_RIGHT, e2, elh2, fn_counter_before + fns_in_col1, e_top=e_top2)
    # DEBUG: actual bottom Y positions
    _last_y1 = y_top - e_top1 - h1  # where col1 text actually ends
    _last_y2 = y_top - e_top2 - h2  # where col2 text actually ends
    if abs(_last_y1 - _last_y2) > 0.1:
        print(f"  [DRAW] pg={page_num:2d} col1_bot={_last_y1:.2f} col2_bot={_last_y2:.2f} "
              f"DIFF={abs(_last_y1 - _last_y2):.2f}  h1={h1:.2f} h2={h2:.2f} "
              f"e_top1={e_top1:.2f} e_top2={e_top2:.2f}")
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

    def build(self):
        global MAMAR_NAME
        # ── Preprocess: fix typer mistake — heading2 immediately after
        #    heading1 is actually part of the H1 (the anaf name).
        #    Merge it into heading1 and remove the heading2 entry.
        items_to_render = []
        for i, item in enumerate(self.items):
            if (item['type'] == 'heading2'
                    and i > 0
                    and self.items[i - 1]['type'] == 'heading1'):
                # This H2 is really the anaf name — absorb into previous H1
                prev = items_to_render[-1]
                if not prev.get('name'):
                    prev['name'] = item['text']
                    # Skip this item entirely (it's now part of H1)
                    continue
                # If the H1 already has a name, this is a real subheading — keep it
            items_to_render.append(dict(item))

        current_anaf = None
        for item in items_to_render:
            if item['type'] == 'heading1':
                current_anaf = item
            elif item['type'] == 'heading2' and current_anaf is not None and not current_anaf.get('name'):
                current_anaf['name'] = item['text']

        # Extract mamar name from sefer_title item
        for item in items_to_render:
            if item['type'] == 'sefer_title':
                MAMAR_NAME = item['text']
                break

        elements = []
        last_heading_kind = None

        for item in items_to_render:
            if item['type'] == 'sefer_title':
                elements.append({'kind': 'sefer_title', 'text': item['text']})
                last_heading_kind = 'sefer_title'

            elif item['type'] == 'heading1':
                elements.append({
                    'kind': 'anaf',
                    'label': item['label'],
                    'name': item.get('name', ''),
                })
                last_heading_kind = 'anaf'

            elif item['type'] == 'heading2':
                # heading2 = in-column sub-heading (flows inside column)
                text = item['text']
                text_clean = strip_nikud(text)
                text_vis = vis(text_clean)
                sub_tw = Wid(text_vis, S.H2_FONT_NAME, SUB_FS)
                if sub_tw <= COL_W:
                    n_sub_lines = 1
                else:
                    sub_wrapped = wrap_words(text_clean.split(), COL_W * 0.95, S.H2_FONT_NAME, SUB_FS)
                    n_sub_lines = max(1, len(sub_wrapped))
                ld = {
                    'type': 'subhead', 'text': text, 'words': [],
                    'xr_off': 0, 'width': COL_W, 'nlines': n_sub_lines,
                }
                elements.append({
                    'kind': 'para', 'lines': [ld], 'footnotes': [],
                    'is_subhead': True, 'nlines': n_sub_lines,
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
                })
                last_heading_kind = None

        front_matter_count = 2
        if getattr(S, 'INCLUDE_SHAAR_BLATT', False):
            front_matter_count += 1
        if getattr(S, 'INCLUDE_INFO_PAGE', False):
            front_matter_count += 1

        # Printed body numbering starts at 1 on the first content page.
        self.page_num = 0
        self._paginate(elements)
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
        draw_toc_page(front_cv, anaf_entries, page_offset=1)
        front_cv.showPage()
        front_cv.showPage()
        front_cv.save()

        # Merge optional shaar blatt + optional info page + TOC + blank verso + content pages.
        from pypdf import PdfWriter, PdfReader
        writer = PdfWriter()
        if getattr(S, 'INCLUDE_SHAAR_BLATT', False):
            shaar_reader = PdfReader(S.SHAAR_BLATT_PDF)
            writer.add_page(shaar_reader.pages[0])
        if getattr(S, 'INCLUDE_INFO_PAGE', False):
            info_reader = PdfReader(S.INFO_PAGE_PDF)
            writer.add_page(info_reader.pages[0])
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

        print(f"Saved: {self.out_pdf}")
        return self.page_num

    def _paginate(self, elements):
        el_idx, line_off, fn_off = 0, 0, 0
        carry_fn_lds = []
        while el_idx < len(elements) or carry_fn_lds:
            self.page_num += 1
            set_page_geometry(self.page_num)
            el_idx, line_off, fn_off, carry_fn_lds = self._render_page(
                elements, el_idx, line_off, fn_off, carry_fn_lds)

    def _render_page(self, elements, el_idx, line_off, fn_off, carry_fn_lds):
        c = self.cv
        header_y = C_TOP - 2
        display_page_num = max(1, self.page_num)
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
        _is_pg1 = (max(1, self.page_num) == 1) and S.SECTION_END_ORNAMENT
        _sefer_title_orn_h = (HSEP * 1.3 + LH * 1.2) if _is_pg1 else 0.0
        _SECTION_ORN_H = (HSEP * 2.2  + LH * 2.2) if S.SECTION_END_ORNAMENT else 0.0
        # The ornament's bottom gap (LH * 2.2) is decorative whitespace that can
        # safely intrude into the bottom margin.  By reducing the reservation
        # during pagination we pack more content on section-end pages, avoiding
        # nearly-empty pages that only hold a line or two plus the ornament.
        _ORN_MARGIN_INTRUDE = (LH * 2.2) if S.SECTION_END_ORNAMENT else 0.0
        _SECTION_ORN_RESERVE = max(0.0, _SECTION_ORN_H - _ORN_MARGIN_INTRUDE)

        # Minimum body content (in pts) required on a page before we bother
        # drawing the section-end ornament.  If the page holds less than this
        # the ornament would dominate a nearly-empty page and force each column
        # to hold only one or two lines — so we skip it entirely.
        _MIN_CONTENT_FOR_ORN = LH * 4

        def _post_anaf_orn_h_for(next_el_idx):
            """Return the *reservation* height for the section-end ornament if it
            would actually be rendered after breaking before element next_el_idx.
            The ornament is drawn when the next element is an anaf heading OR
            this is the final block of the book.
            Returns the reduced reservation (_SECTION_ORN_RESERVE) so the
            ornament can intrude into the bottom margin.
            Returns 0.0 (skip ornament) when the page has too little body
            content to justify the ornament — avoids one-line columns."""
            if not S.SECTION_END_ORNAMENT:
                return 0.0
            # Skip ornament if there is too little content on this page
            if used_h < _MIN_CONTENT_FOR_ORN:
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
            # DEBUG: measure actual column heights
            _dbg_rh1, _dbg_rh2 = _col_raw_heights(acc_paras, c1d, c2d, e1, e2, elh1, elh2)
            _dbg_th1 = _dbg_rh1 + e_top1
            _dbg_th2 = _dbg_rh2 + e_top2
            _dbg_diff = abs(_dbg_th1 - _dbg_th2)
            print(f"  [DBG] page={self.page_num:2d}  raw_h1={_dbg_rh1:7.2f}  raw_h2={_dbg_rh2:7.2f}  "
                  f"e_top1={e_top1:6.2f}  e_top2={e_top2:6.2f}  "
                  f"total_h1={_dbg_th1:7.2f}  total_h2={_dbg_th2:7.2f}  "
                  f"DIFF={_dbg_diff:6.2f}  e1={e1:6.2f}  e2={e2:6.2f}")
            plan.append({'kind': 'col_pair', 'paras': list(acc_paras),
                         'c1d': c1d, 'c2d': c2d, 'e1': e1, 'e2': e2,
                         'elh1': elh1, 'elh2': elh2,
                         'e_top1': e_top1, 'e_top2': e_top2,
                         'sub_after_last_resort': bool(_force_sub_after_stretchable),
                         'fn_lds': list(acc_fn_lds), 'height': h})
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
                _pb_sub = acc_paras[-1]
                _pb_txt = ''
                if _pb_sub.get('lines') and _pb_sub['lines'][0].get('text'):
                    _pb_txt = _pb_sub['lines'][0]['text'][:40]
                print(f'  [PULLBACK] removing subhead: {_pb_txt}  cur_el_idx={cur_el_idx}')
                acc_paras.pop()
                if acc_origins:
                    orig = acc_origins.pop()
                    cur_el_idx = orig['el_idx']
                    cur_ln_off = orig['ln_off']
                    cur_fn_off = orig['fn_off']
                    fn_cursor = orig['fn_cursor']
                    print(f'  [PULLBACK] restored cursor to el_idx={cur_el_idx} ln_off={cur_ln_off}')
                    # Also remove corresponding footnote line dicts
                    n_fn_lds_to_remove = orig['fn_lds_added']
                    if n_fn_lds_to_remove > 0:
                        del acc_fn_lds[-n_fn_lds_to_remove:]
                pulled = True
            return pulled

        page_empty = True
        cur_el_idx, cur_ln_off, cur_fn_off = el_idx, line_off, fn_off
        print(f'  [PAGE START] page_num={self.page_num}  start el_idx={el_idx} ln_off={line_off}  el_kind={elements[el_idx]["kind"] if el_idx < len(elements) else "END"}')

        while cur_el_idx < len(elements):
            el = elements[cur_el_idx]

            if el['kind'] == 'sefer_title':
                flush_acc()
                page_empty = False
                sec_h = heading_height(el['text'], 'sefer_title')
                _title_gap_above = LH * 2.1
                plan.append({'kind': 'sefer_title', 'text': el['text'], 'height': sec_h, 'gap_above': _title_gap_above})
                used_h += sec_h + _title_gap_above + _sefer_title_orn_h

                cur_el_idx += 1; cur_ln_off = 0; cur_fn_off = 0
                continue

            if el['kind'] == 'anaf':
                page_has_real_content = (
                    any(b['kind'] != 'sefer_title' for b in plan) or bool(acc_paras))
                if page_has_real_content:
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
                                     'height': sec_h, 'gap_above': 0.0})
                        used_h += sec_h

                        cur_el_idx += 1; cur_ln_off = 0; cur_fn_off = 0
                    else:
                        break
                else:
                    break
                continue

            # ── paragraphs ──
            if el['kind'] != 'para':
                cur_el_idx += 1; cur_ln_off = 0; cur_fn_off = 0
                continue

            all_lines = el['lines']
            all_fns = el.get('footnotes', [])
            remaining_lines = all_lines[cur_ln_off:]
            remaining_fns = all_fns[cur_fn_off:]

            if not remaining_lines:
                cur_el_idx += 1; cur_ln_off = 0; cur_fn_off = 0
                continue

            fns_on_page_so_far = (sum(1 for ld in fn_lds_on_page if ld.get('is_first'))
                                  + sum(1 for ld in acc_fn_lds if ld.get('is_first')))
            fn_num_base = fn_cursor + fns_on_page_so_far + 1
            para_fn_lds = []
            for fi, ft in enumerate(remaining_fns):
                para_fn_lds.extend(fn_text_to_line_dicts(clean_fn_text(ft), fn_num_base + fi))

            _visual_nlines = sum(ld.get('nlines', 1) for ld in remaining_lines)
            trial_para = {'lines': remaining_lines, 'nlines': _visual_nlines, 'is_para_end': True}
            if el.get('is_subhead'): trial_para['is_subhead'] = True

            trial_acc = acc_paras + [trial_para]
            trial_fns = fn_lds_on_page + acc_fn_lds + para_fn_lds
            c1d, c2d, e1, e2, elh1, elh2 = _col_layout(trial_acc)
            _tr_h1, _tr_h2 = _col_raw_heights(trial_acc, c1d, c2d, e1, e2, elh1, elh2)
            _tr_top1, _tr_top2 = _compute_top_pads(_tr_h1, _tr_h2, c1d, c2d, trial_acc)
            trial_h = _col_height(trial_acc, c1d, c2d, e1, e2, elh1, elh2,
                                  e_top1=_tr_top1, e_top2=_tr_top2)

            _last_plan_kind = plan[-1]['kind'] if plan else None
            _last_is_heading = _last_plan_kind in ('anaf', 'anaf_sub', 'section', 'sefer_title')
            sep = PSEP if (plan and not acc_paras and not _last_is_heading) else 0.0

            _orn_res = _orn_h_after(cur_el_idx + 1)
            # Also allow taking the element when it fits WITHOUT the ornament
            # reservation — the ornament will be suppressed at draw time anyway
            # (via the _enough_content_for_orn check) if the remaining content
            # is too small.  This prevents a widow line being pushed to the next
            # page just to make room for an ornament that won't be drawn.
            _fits_with_orn    = used_h + trial_h + sep + _orn_res   <= body_avail(len(trial_fns)) + 0.01
            _fits_without_orn = used_h + trial_h + sep               <= body_avail(len(trial_fns)) + 0.01
            if _fits_with_orn or _fits_without_orn:
                if el.get('is_subhead'):
                    _sub_txt = el['lines'][0].get('text','')[:40] if el.get('lines') else ''
                    print(f'  [ADD SUB] el_idx={cur_el_idx} text={_sub_txt}')
                acc_origins.append({
                    'el_idx': cur_el_idx, 'ln_off': cur_ln_off, 'fn_off': cur_fn_off,
                    'fn_cursor': fn_cursor, 'fn_lds_added': len(para_fn_lds),
                })
                acc_paras.append(trial_para)
                acc_fn_lds.extend(para_fn_lds)
                fn_cursor += len(remaining_fns)
                cur_el_idx += 1; cur_ln_off = 0; cur_fn_off = 0
                page_empty = False
                continue

            # Split
            nL = len(remaining_lines)
            best_n = 0

            for nn in range(nL - MIN_PART_LINES, MIN_PART_LINES - 1, -1):
                if nn < MIN_PART_LINES: break
                fn_in_head = _count_fns_in_lines(remaining_lines[:nn])
                head_fn_lds = []
                for fi in range(fn_in_head):
                    head_fn_lds.extend(fn_text_to_line_dicts(clean_fn_text(remaining_fns[fi]), fn_num_base + fi))
                _t_vnl = sum(ld.get('nlines', 1) for ld in remaining_lines[:nn])
                t_para = {'lines': remaining_lines[:nn], 'nlines': _t_vnl}
                if el.get('is_subhead'): t_para['is_subhead'] = True
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
                if t_h > avail_for_cols + 0.01 and avail_for_cols > LH * 2:
                    tc1, tc2, te1, te2, telh1, telh2 = _col_layout(t_acc, max_height=avail_for_cols)
                    _tt_h1, _tt_h2 = _col_raw_heights(t_acc, tc1, tc2, te1, te2, telh1, telh2)
                    _tt_top1, _tt_top2 = _compute_top_pads(_tt_h1, _tt_h2, tc1, tc2, t_acc)
                    t_h = _col_height(t_acc, tc1, tc2, te1, te2, telh1, telh2,
                                      e_top1=_tt_top1, e_top2=_tt_top2)

                if used_h + t_h + sep2 + _split_orn_h <= body_avail(len(t_fns)) + 0.01:
                    best_n = nn
                    break

            # Section-end orphan prevention: if the split would leave only a
            # very small tail (≤ MIN_PART_LINES visual lines) on the next page
            # AND the next element is an anaf (section boundary), it is better
            # to NOT split at all — defer the whole element to the next page so
            # it opens a fresh section there instead of leaving a lonely tail.
            _next_is_anaf = (cur_el_idx + 1 < len(elements)
                             and elements[cur_el_idx + 1]['kind'] == 'anaf')
            _orphan_flush = False
            if (best_n > 0
                    and _next_is_anaf
                    and (nL - best_n) <= MIN_PART_LINES
                    and not page_empty):
                best_n = 0   # force flush; element deferred to next page
                _orphan_flush = True  # don't pull back preceding subhead

            if best_n == 0:
                if page_empty and not acc_paras:
                    best_n = min(nL, MIN_PART_LINES)
                else:
                    # BUG FIX: Before flushing, pull back trailing subheads
                    # so they aren't orphaned at the bottom of the page/column.
                    # Exception: when we deliberately deferred an element due to
                    # section-end orphan prevention, the preceding subhead is
                    # intentionally on this page (its body follows on the next).
                    if not _orphan_flush:
                        _pullback_trailing_subheads()
                    flush_acc()
                    page_empty = False
                    break

            fn_in_head = _count_fns_in_lines(remaining_lines[:best_n])
            head_para_fn_lds = []
            for fi in range(fn_in_head):
                head_para_fn_lds.extend(fn_text_to_line_dicts(clean_fn_text(remaining_fns[fi]), fn_num_base + fi))

            _head_vnl = sum(ld.get('nlines', 1) for ld in remaining_lines[:best_n])
            head_para = {'lines': remaining_lines[:best_n], 'nlines': _head_vnl, 'is_para_end': False}
            if el.get('is_subhead'): head_para['is_subhead'] = True
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

            cur_ln_off = cur_ln_off + best_n
            cur_fn_off = cur_fn_off + fn_in_head
            if cur_ln_off >= len(all_lines):
                cur_el_idx += 1; cur_ln_off = 0; cur_fn_off = 0
            break

        # BUG FIX: Before final flush, pull back trailing subheads
        # But on the very last page (no more elements), allow trailing subheads
        # in col2 so column balancing can work properly.
        is_final_flush = (cur_el_idx >= len(elements))
        if not is_final_flush:
            _pullback_trailing_subheads()
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
        display_pg = max(1, self.page_num)

        simulated_y = text_top
        for blk in plan:
            if blk['kind'] in ('sefer_title', 'anaf', 'anaf_sub', 'section'):
                simulated_y -= blk['height']
            else:
                if simulated_y < text_top: simulated_y -= PSEP
                simulated_y -= blk['height']
        slack = simulated_y - body_bottom_min
        PAGE_MAX_E_PER_GAP = S.PAGE_GAP_STRETCH_PT
        MAX_ELH = S.MAX_LINE_STRETCH_PT
        display_pg = max(1, self.page_num)

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
                        self.anaf_page_map[lbl] = max(1, self.page_num)
                gap_above = block.get('gap_above', 0.0)
                render_y -= gap_above
                if block['kind'] == 'anaf':
                    render_y -= H1_TOP_GAP
                    h = draw_anaf_heading(c, block['label'], block.get('name', ''), render_y)
                else:
                    h = draw_section_header(c, block['text'], render_y, kind=block['kind'])
                render_y -= h
                render_y -= HEADING_AFTER_GAP
                if block['kind'] == 'sefer_title':
                    render_y -= 18
                # Add an opening ornament after the sefer title
                if block['kind'] == 'sefer_title' and S.SECTION_END_ORNAMENT:
                    render_y -= HSEP * 1.3
                    draw_gray_bottom_orn(c, x_center, render_y + 4, 92, gray=0.55, glow=False)
                    render_y -= LH * 1.2

                first_block = False
                last_was_heading = True
                continue

            if not first_block and not last_was_heading:
                render_y -= PSEP
            first_block = False
            last_was_heading = False

            col_top = render_y
            global _force_sub_after_stretchable
            _saved_force_sub = _force_sub_after_stretchable
            _force_sub_after_stretchable = bool(block.get('sub_after_last_resort'))
            h_used = _draw_col_pair(c, block['paras'],
                                    block['c1d'], block['c2d'],
                                    block['e1'], block['e2'],
                                    col_top, local_fnc,
                                    elh1=block.get('elh1', 0.0),
                                    elh2=block.get('elh2', 0.0),
                                    e_top1=block.get('e_top1', 0.0),
                                    e_top2=block.get('e_top2', 0.0),
                                    page_num=self.page_num)
            _force_sub_after_stretchable = _saved_force_sub
            render_y -= h_used

            local_fnc += sum(1 for ld in block['fn_lds'] if ld.get('is_first', False))

            # End-of-section ornament before next anaf, and after final anaf.
            # Skip the ornament if the page has too little content — avoids
            # the case where the ornament reservation forces each column to
            # hold only one line.
            next_el_is_anaf = (
                (next_block is not None and next_block['kind'] == 'anaf')
                or (next_block is None and cur_el_idx < len(elements)
                    and elements[cur_el_idx]['kind'] == 'anaf'))
            final_block_of_book = (bi == len(plan) - 1 and cur_el_idx >= len(elements))
            _enough_content_for_orn = body_used >= LH * 4
            if (next_el_is_anaf or final_block_of_book) and S.SECTION_END_ORNAMENT and _enough_content_for_orn:
                render_y -= HSEP * 2.2
                draw_gray_bottom_orn(c, x_center, render_y + 4, 92, gray=0.55, glow=False)
                render_y -= LH * 2.2


        local_fnc += sum(1 for ld in fn_carry if ld.get('is_first', False))
        self.fn_counter = local_fnc

        # Draw footnotes
        if fn_draw:
            fn_rule_y = C_BOT + len(fn_draw) * FN_LH + 10
            # Ornamental footnote separator (~2/3 page width)
            if S.FN_RULE_ORNAMENTAL:
                rule_w = C_W * S.FN_RULE_WIDTH
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

def main():
    print("Loading doc_parts.json...")
    with open(S.DOC_PARTS_PATH, 'r', encoding='utf-8') as f:
        items = json.load(f)
    print(f"  Found {len(items)} content items")

    from collections import Counter
    counts = Counter(it['type'] for it in items)
    for t, cnt in sorted(counts.items()):
        print(f"    {t}: {cnt}")

    os.makedirs(S.OUT_DIR, exist_ok=True)

    print(f"\nRendering to {S.OUT_PDF}...")
    layout = PageLayout(items, S.OUT_PDF)
    n_pages = layout.build()
    print(f"  {n_pages} pages rendered")

    # Generate PNG previews
    print("\nGenerating PNG previews (200 DPI)...")
    prefix = os.path.join(S.OUT_DIR, "palaot")
    import glob as _glob
    for old_png in _glob.glob(os.path.join(S.OUT_DIR, "palaot-*.png")):
        os.remove(old_png)
    try:
        subprocess.run(
            ['pdftoppm', '-r', '200', '-png', S.OUT_PDF, prefix],
            check=True)
        for f in sorted(os.listdir(S.OUT_DIR)):
            if f.startswith('palaot-') and f.endswith('.png'):
                print(f"  {os.path.join(S.OUT_DIR, f)}")
    except FileNotFoundError:
        print("  pdftoppm not found — skipping PNG previews")

    print("\nDone!")

if __name__ == "__main__":
    main()
