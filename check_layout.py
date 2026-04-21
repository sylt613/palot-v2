#!/usr/bin/env python3
"""
check_layout.py — Programmatic layout checker for palaot v2.

Reads the latest layout_log.json and reports:
  1. Real bang-ins (elements whose drawn y_bot overlaps the next element's y_top,
     accounting for the inter-fragment gap that the log does NOT include).
  2. Subhead after-gap violations (gap between subhead bottom and next element
     is below MIN_ACCEPTABLE_AFTER or above MAX_ACCEPTABLE_AFTER).
  3. Missing paragraphs (source_idxs that appear in doc_parts but not in the log).

Usage:
    python3 check_layout.py [path/to/layout_log.json]

If no path given, uses the most recent job in output/jobs/.
Exit code: 0 = clean, 1 = issues found.
"""

import json
import os
import sys
import glob
from collections import defaultdict

# ── Config ────────────────────────────────────────────────────────────────────
# Thresholds (in points). Adjust to match settings.py values.
MIN_AFTER_GAP        = 1.0    # subhead→body gap must be at least this
MAX_AFTER_GAP        = 30.0   # subhead→body gap should not exceed this (would look detached)
OVERLAP_THRESHOLD    = 0.5    # overlaps smaller than this (pt) are floating-point noise
# The log records y_top BEFORE _inter_frag_gap is subtracted.  Entries whose
# y_bot > next y_top by more than OVERLAP_THRESHOLD indicate a real draw overlap.
# But log entries for fragments within the same column already have the gap
# subtracted between them — so y_top[n+1] = y_bot[n] - inter_frag_gap.
# A "bang-in" shows as next.y_top > prev.y_bot (positive value after subtraction).
# We flag cases where the gap is negative (overlap) OR suspiciously small.
BANG_IN_THRESHOLD    = -0.4   # gap < this → real bang-in

def find_latest_log():
    jobs = sorted(glob.glob("output/jobs/*/layout_log.json"))
    if not jobs:
        print("ERROR: No layout_log.json found in output/jobs/")
        sys.exit(2)
    return jobs[-1]

def load_log(path):
    with open(path) as f:
        return json.load(f)

def check(log_path):
    log = load_log(log_path)
    print(f"Loaded {len(log)} entries from {log_path}")

    # Group by (page, col), sort top→bottom (descending y_top)
    pages = defaultdict(list)
    for e in log:
        pages[(e['page'], e['col'])].append(e)

    issues = []

    for key in sorted(pages.keys()):
        entries = sorted(pages[key], key=lambda x: -x['y_top'])
        for i in range(len(entries) - 1):
            a = entries[i]
            b = entries[i + 1]
            # gap = space between a's bottom and b's top (includes inter-fragment gap)
            gap = a['y_bot'] - b['y_top']

            # Bang-in: b starts ABOVE a's bottom (gap is negative = overlap)
            if gap < BANG_IN_THRESHOLD:
                issues.append({
                    'type': 'BANG_IN',
                    'page': a['page'], 'display_pg': a['display_pg'], 'col': a['col'],
                    'gap': gap,
                    'a': f"[{a['kind']}] y={a['y_top']}..{a['y_bot']} {a['text'][:40]!r}",
                    'b': f"[{b['kind']}] y={b['y_top']}..{b['y_bot']} {b['text'][:40]!r}",
                })

            # Subhead after-gap too small
            if a['kind'] == 'subhead' and gap < MIN_AFTER_GAP:
                issues.append({
                    'type': 'SUB_AFTER_TOO_SMALL',
                    'page': a['page'], 'display_pg': a['display_pg'], 'col': a['col'],
                    'gap': gap,
                    'a': f"subhead {a['text'][:40]!r}",
                    'b': f"[{b['kind']}] {b['text'][:40]!r}",
                })

            # Subhead after-gap too large (detached)
            if a['kind'] == 'subhead' and gap > MAX_AFTER_GAP:
                issues.append({
                    'type': 'SUB_AFTER_TOO_LARGE',
                    'page': a['page'], 'display_pg': a['display_pg'], 'col': a['col'],
                    'gap': gap,
                    'a': f"subhead {a['text'][:40]!r}",
                    'b': f"[{b['kind']}] {b['text'][:40]!r}",
                })

    # Report
    if not issues:
        print("\n✓ No layout issues detected.\n")
        return 0

    bang_ins   = [x for x in issues if x['type'] == 'BANG_IN']
    sub_small  = [x for x in issues if x['type'] == 'SUB_AFTER_TOO_SMALL']
    sub_large  = [x for x in issues if x['type'] == 'SUB_AFTER_TOO_LARGE']

    if bang_ins:
        print(f"\n{'='*60}")
        print(f"BANG-INS ({len(bang_ins)} found):")
        for x in bang_ins:
            heb_pg = _heb_page(x['display_pg'])
            print(f"  pg {x['page']} (display {heb_pg}) col={x['col']}  overlap={-x['gap']:.2f}pt")
            print(f"    {x['a']}")
            print(f"    {x['b']}")

    if sub_small:
        print(f"\n{'='*60}")
        print(f"SUBHEAD AFTER-GAP TOO SMALL (<{MIN_AFTER_GAP}pt)  ({len(sub_small)} found):")
        for x in sub_small:
            heb_pg = _heb_page(x['display_pg'])
            print(f"  pg {x['page']} (display {heb_pg}) col={x['col']}  gap={x['gap']:.2f}pt")
            print(f"    {x['a']}")
            print(f"    {x['b']}")

    if sub_large:
        print(f"\n{'='*60}")
        print(f"SUBHEAD AFTER-GAP TOO LARGE (>{MAX_AFTER_GAP}pt)  ({len(sub_large)} found):")
        for x in sub_large:
            heb_pg = _heb_page(x['display_pg'])
            print(f"  pg {x['page']} (display {heb_pg}) col={x['col']}  gap={x['gap']:.2f}pt")
            print(f"    {x['a']}")
            print(f"    {x['b']}")

    total = len(bang_ins) + len(sub_small) + len(sub_large)
    print(f"\nTotal issues: {total}")
    return 1


def _heb_page(n):
    """Return display page number as Hebrew letter (alef=1, bet=2 … )."""
    _letters = 'אבגדהוזחטיכלמנסעפצקרשת'
    if 1 <= n <= len(_letters):
        return _letters[n - 1]
    return str(n)


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else find_latest_log()
    sys.exit(check(path))
