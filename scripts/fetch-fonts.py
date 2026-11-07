#!/usr/bin/env python3
"""Download a brand's fonts from Google Fonts into brands/<name>/fonts/.

Renders never touch the network: each chapter's graphics folder gets a copy
of these files. Run this once when a brand is created or its fonts change.
Reads the two families from brands/<name>/brand.json → "fonts".

    fetch-fonts.py <brand>
"""

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"
WEIGHTS = {"sans": "400;600;700", "mono": "500;600"}


def main():
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    bdir = ROOT / "brands" / sys.argv[1]
    fonts = json.loads((bdir / "brand.json").read_text())["fonts"]
    families = "&".join(f"family={urllib.parse.quote_plus(fonts[k])}:wght@{w}"
                        for k, w in WEIGHTS.items())
    url = f"https://fonts.googleapis.com/css2?{families}&display=block"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    css = urllib.request.urlopen(req).read().decode()

    out = bdir / "fonts"
    out.mkdir(exist_ok=True)
    for old in out.glob("*.woff2"):
        old.unlink()
    faces = []
    for subset, block in re.findall(r"/\* (\S+) \*/\s*(@font-face \{.*?\})", css, re.S):
        if subset != "latin":
            continue
        fam = re.search(r"font-family: '([^']+)'", block).group(1)
        weight = re.search(r"font-weight: (\d+)", block).group(1)
        src = re.search(r"url\((\S+?)\)", block).group(1)
        name = f"{fam.replace(' ', '')}-{weight}.woff2"
        urllib.request.urlretrieve(src, out / name)
        faces.append(re.sub(r"url\(\S+?\)", f"url({name})", block))
    if not faces:
        print(f"FAIL  Google Fonts returned nothing for {fonts}.", file=sys.stderr)
        return 1
    (out / "fonts.css").write_text("\n".join(faces) + "\n")
    print(f"{len(faces)} font files → {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
