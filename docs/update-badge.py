#!/usr/bin/env python3
"""Update docs/badges/version.svg from the version in pyproject.toml."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def get_version() -> str:
    text = (ROOT / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        sys.exit("version not found in pyproject.toml")
    return m.group(1)


def make_badge(version: str) -> str:
    right_width = len(version) * 6 + 6
    total_width = 63 + right_width
    right_x = 63 + (right_width - 2) // 2
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="20">
    <linearGradient id="b" x2="0" y2="100%">
        <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
        <stop offset="1" stop-opacity=".1"/>
    </linearGradient>
    <mask id="a">
        <rect width="{total_width}" height="20" rx="3" fill="#fff"/>
    </mask>
    <g mask="url(#a)">
        <path fill="#555" d="M0 0h63v20H0z"/>
        <path fill="#4c1" d="M63 0h{right_width}v20H63z"/>
        <path fill="url(#b)" d="M0 0h{total_width}v20H0z"/>
    </g>
    <g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
        <text x="31.5" y="15" fill="#010101" fill-opacity=".3">version</text>
        <text x="31.5" y="14">version</text>
        <text x="{right_x}" y="15" fill="#010101" fill-opacity=".3">{version}</text>
        <text x="{right_x}" y="14">{version}</text>
    </g>
</svg>
"""


version = get_version()
badge_path = ROOT / "docs" / "badges" / "version.svg"
badge_path.write_text(make_badge(version))
print(f"version.svg → {version}")
