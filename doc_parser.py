#!/usr/bin/env python3
"""
doc_parser.py — Parse a .docx into a structured JSON-like doc_parts list.

Each element is a dict with 'type' and associated data.
Types:
  - sefer_title:  { type, text }
  - heading1:     { type, label, name, new_section: True }
  - heading2:     { type, text }
  - body:         { type, text, segments: [{text, bold}], footnotes: [str] }

The parser handles:
  - Multiple heading levels (Heading 1, 2, 3 in the docx)
  - Footnote extraction with markers embedded in text
  - RTL quote fixing
  - Rich text: bold runs in body paragraphs are flagged so the renderer
    can use the Margoliot font for them
  - Heading 1 = "ענף" label; if immediately followed by Heading 2,
    that H2 is the anaf name (merged into heading1 element)
  - All other Heading 2 = standalone heading2 elements (sub-sections)
  - Heading 3 = heading2 elements (in-column sub-headings)

Usage:
    python3 doc_parser.py /root/Desktop/palot/1-22\ fixed.docx
    # writes /root/Desktop/palot/doc_parts.json
"""

import json, re, sys, os
import zipfile
import xml.etree.ElementTree as ET


def fix_rtl_quotes(text):
    """Replace smart/curly quotes with Hebrew geresh/gershayim,
    and en/em dashes with hyphens."""
    result = []
    for ch in text:
        if ch in '\u201c\u201d\u201e':
            result.append('\u05F4')
        elif ch in '\u2018\u2019\u201a':
            result.append('\u05F3')
        elif ch in '\u2013\u2014':
            result.append('-')
        else:
            result.append(ch)
    return ''.join(result)


def parse_docx(docx_path):
    """
    Parse a .docx file into structured doc_parts.

    Returns list of dicts, each with 'type' and associated fields.
    """
    from docx import Document
    from docx.oxml.ns import qn

    # ── Load footnote id→text map ────────────────────────────────────
    fn_map = {}
    try:
        with zipfile.ZipFile(docx_path) as z:
            if 'word/footnotes.xml' in z.namelist():
                fn_xml = z.read('word/footnotes.xml').decode('utf-8')
                fn_root = ET.fromstring(fn_xml)
                fn_ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                for fn_el in fn_root.findall('w:footnote', fn_ns):
                    fn_id = fn_el.get(qn('w:id'))
                    fn_type = fn_el.get(qn('w:type'))
                    if fn_type is not None:
                        continue
                    texts = []
                    for t in fn_el.iter(qn('w:t')):
                        if t.text:
                            texts.append(t.text)
                    fn_map[fn_id] = ''.join(texts).strip()
    except Exception:
        pass

    doc = Document(docx_path)
    items = []
    global_fn_counter = [0]

    def para_text_and_fns(para):
        """Walk runs, building PLAIN text with embedded fn markers.
        Returns (text_str, footnote_texts).
        Used for headings and sefer_title."""
        parts = []
        footnote_texts = []
        for run in para.runs:
            refs = run._r.findall('.//' + qn('w:footnoteReference'))
            run_text = run.text or ''
            parts.append(run_text)
            for ref in refs:
                fn_id = ref.get(qn('w:id'))
                fn_text = fn_map.get(fn_id, '')
                if fn_text:
                    global_fn_counter[0] += 1
                    local_num = len(footnote_texts) + 1
                    marker = f'\u27E8fn{local_num}\u27E9'
                    if parts and parts[-1].endswith(' '):
                        parts[-1] = parts[-1].rstrip(' ') + marker + ' '
                    else:
                        parts.append(marker)
                    footnote_texts.append(fn_text)
        text = fix_rtl_quotes(''.join(parts).strip())
        return text, footnote_texts

    def para_rich_and_fns(para):
        """Walk runs, building RICH text segments [{text, bold}].
        Also returns (flat_text, segments, footnote_texts).
        Used for body paragraphs to capture bold/regular distinction."""
        segments = []
        footnote_texts = []
        for run in para.runs:
            refs = run._r.findall('.//' + qn('w:footnoteReference'))
            run_text = run.text or ''
            is_bold = bool(run.bold)

            # Handle fn markers: embed in segment text
            seg_text = run_text
            for ref in refs:
                fn_id = ref.get(qn('w:id'))
                fn_text = fn_map.get(fn_id, '')
                if fn_text:
                    global_fn_counter[0] += 1
                    local_num = len(footnote_texts) + 1
                    marker = f'\u27E8fn{local_num}\u27E9'
                    seg_text = seg_text.rstrip(' ') + marker + ' '
                    footnote_texts.append(fn_text)

            if seg_text:
                seg_text_fixed = fix_rtl_quotes(seg_text)
                # Merge with previous segment if same boldness
                if segments and segments[-1]['bold'] == is_bold:
                    segments[-1]['text'] += seg_text_fixed
                else:
                    segments.append({'text': seg_text_fixed, 'bold': is_bold})

        # Build flat text
        flat = fix_rtl_quotes(''.join(s['text'] for s in segments).strip())

        # Trim leading/trailing whitespace in each segment, but preserve
        # internal spacing
        if segments:
            segments[0]['text'] = segments[0]['text'].lstrip()
            segments[-1]['text'] = segments[-1]['text'].rstrip()
            # Remove empty segments
            segments = [s for s in segments if s['text']]

        return flat, segments, footnote_texts

    first_item = True
    for para in doc.paragraphs:
        style = para.style.name
        text, footnote_texts = para_text_and_fns(para)
        if not text:
            continue

        if first_item:
            items.append({'type': 'sefer_title', 'text': text})
            first_item = False
            continue

        if style == 'Heading 1':
            # ענף label — name will be filled by next Heading 2 if present
            items.append({
                'type': 'heading1',
                'label': text,
                'name': '',
                'new_section': True,
            })
        elif style == 'Heading 2':
            # If immediately follows a heading1 with no name yet, merge as anaf name
            if items and items[-1]['type'] == 'heading1' and items[-1]['name'] == '':
                items[-1]['name'] = text
            else:
                # Standalone heading2 (sub-section within column flow)
                items.append({'type': 'heading2', 'text': text})
        elif style == 'Heading 3':
            # In-column sub-heading → treated as heading2 in layout
            items.append({'type': 'heading2', 'text': text})
        else:
            # Body paragraph — extract rich text (bold flag per run)
            flat, segments, fns = para_rich_and_fns(para)
            if not flat:
                continue
            # If the entire paragraph is bold, treat it as a heading2
            all_bold = segments and all(s['bold'] for s in segments)
            if all_bold:
                # Fully bold body paragraph → treat as in-column subheading
                if items and items[-1]['type'] == 'heading1' and items[-1]['name'] == '':
                    items[-1]['name'] = flat
                else:
                    items.append({'type': 'heading2', 'text': flat})
                continue
            # Check if any run is bold (mixed paragraph)
            has_bold = any(s['bold'] for s in segments)
            item = {
                'type': 'body',
                'text': flat,
                'footnotes': fns,
            }
            if has_bold:
                item['segments'] = segments
            items.append(item)

    return items


def save_doc_parts(items, output_path):
    """Save parsed doc parts as JSON."""
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(items)} doc parts to {output_path}")

    # Summary
    from collections import Counter
    counts = Counter(it['type'] for it in items)
    for t, c in sorted(counts.items()):
        print(f"  {t}: {c}")
    bold_paras = sum(1 for it in items if it.get('type') == 'body' and it.get('segments'))
    print(f"  body paragraphs with bold segments: {bold_paras}")


def load_doc_parts(path):
    """Load doc parts from JSON."""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


if __name__ == '__main__':
    docx_path = sys.argv[1] if len(sys.argv) > 1 else '/root/Desktop/palot/1-22 fixed.docx'
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'doc_parts.json')
    items = parse_docx(docx_path)
    save_doc_parts(items, out_path)
