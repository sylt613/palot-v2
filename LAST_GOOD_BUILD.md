# Last Good Build Reference

## Build Info
- **Date**: 2026-04-15 17:53
- **Job directory**: `output/jobs/20260415_174530/`
- **PDF**: `output/palaot.pdf` / `output/palaot_latest.pdf`
- **Pages**: 20

## What's in this build
- **Font switch**: All headings now use EFT Pnina
  - `pninamedium` (`EFT_PNINA MEDIUM.TTF`) → sefer title, H1 bold, H2, dropcap, even-header title, odd-page ענף label
  - `pninaregular` (`EFT_PNINA.TTF`) → H1 regular, even-page header, odd-page anaf name, page number
  - Body text remains FrankReal Med; bold body runs remain FrankReal Bold
- **Bottom fill fix**: `skip_stretch` no longer suppresses stretching on section-end pages — columns now fill to bottom margin on all pages
- **Gray halo** outline around all frame black lines (applied at output resolution)
  - `_HALO_RADIUS=6`, `_HALO_DARKEST=120`, `_INK_THRESHOLD=180`
  - Halo only on interior side (pixels originally < 254 preserved for white mask)
- **Vertical centering** of Hebrew text inside frames using measured glyph body
  - `BODY_CENTRE_ABOVE_BL = 0.2475` (visual centre of body glyphs above baseline)
  - Applied in both `draw_section_header` (sefer_title) and `draw_toc_page` (TOC title)
- Title rules: `title_rule_right.png` (one-direction curl, right end only)
- TOC mamar block uses subhead rules (not frame)
- Contact page: larger text sizes, strips bleed to edge
- Frame white interior masked transparent (`mask=[254,255,254,255,254,255]`)
- Text drawn BEHIND frame (text first, frame on top)

## Key files in this job
- `output/jobs/20260415_173207/` — build script + settings snapshots
- `output/jobs/20260415_173207/layout_log.json` — element positions
- `output/jobs/20260415_173207/qa_report.txt` — QA summary

## Reference docs
- `TILING_REFERENCE.md` — frame tiling knowledge (corner crops, tile slices, seams)
- `VERTICAL_CENTERING_GUIDE.md` — Hebrew glyph body measurements, centering formulas
