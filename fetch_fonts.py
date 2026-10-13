#!/usr/bin/env python3
"""Download the kit's fonts (all SIL OFL 1.1) from the official google/fonts repo into fonts/.

Usage: .venv/bin/python fetch_fonts.py [--force]
Inter and Oswald are published there only as variable fonts, so the static weights the
themes use (Inter-Bold.ttf, Oswald-SemiBold.ttf, ...) are instanced locally with fontTools
(installed from requirements.txt). Licence texts are already in fonts/OFL-*.txt.
"""
import os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "fonts")
BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/"

# local file name -> path in github.com/google/fonts
DIRECT = {
    "Anton-Regular.ttf": "anton/Anton-Regular.ttf",
    "ArchivoBlack-Regular.ttf": "archivoblack/ArchivoBlack-Regular.ttf",
    "BarlowCondensed-ExtraBold.ttf": "barlowcondensed/BarlowCondensed-ExtraBold.ttf",
    "IBMPlexSansCondensed-Medium.ttf": "ibmplexsanscondensed/IBMPlexSansCondensed-Medium.ttf",
    "IBMPlexSansCondensed-SemiBold.ttf": "ibmplexsanscondensed/IBMPlexSansCondensed-SemiBold.ttf",
    "JetBrainsMono[wght].ttf": "jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
    "LibreFranklin[wght].ttf": "librefranklin/LibreFranklin%5Bwght%5D.ttf",
    "PlayfairDisplay[wght].ttf": "playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf",
    "PlayfairDisplay-Italic[wght].ttf": "playfairdisplay/PlayfairDisplay-Italic%5Bwght%5D.ttf",
    "SourceSerif4[opsz,wght].ttf": "sourceserif4/SourceSerif4%5Bopsz,wght%5D.ttf",
    "SpaceGrotesk[wght].ttf": "spacegrotesk/SpaceGrotesk%5Bwght%5D.ttf",
}
# variable source -> {static output name: axis location}
INSTANCES = {
    "inter/Inter%5Bopsz,wght%5D.ttf": {
        "Inter-Regular.ttf": {"wght": 400, "opsz": 14},
        "Inter-Medium.ttf": {"wght": 500, "opsz": 14},
        "Inter-SemiBold.ttf": {"wght": 600, "opsz": 14},
        "Inter-Bold.ttf": {"wght": 700, "opsz": 14},
        "Inter-ExtraBold.ttf": {"wght": 800, "opsz": 14},
    },
    "oswald/Oswald%5Bwght%5D.ttf": {
        "Oswald-SemiBold.ttf": {"wght": 600},
        "Oswald-Bold.ttf": {"wght": 700},
    },
}

def fetch(rel, dest):
    url = BASE + rel
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read()
    if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        raise SystemExit(f"ERROR: {url} did not return a font file")
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)

def main():
    force = "--force" in sys.argv
    os.makedirs(FONTS, exist_ok=True)
    for name, rel in DIRECT.items():
        dest = os.path.join(FONTS, name)
        if os.path.exists(dest) and not force:
            print("ok  ", name); continue
        print("get ", name); fetch(rel, dest)
    for rel, outs in INSTANCES.items():
        if all(os.path.exists(os.path.join(FONTS, n)) for n in outs) and not force:
            for n in outs: print("ok  ", n)
            continue
        from fontTools.ttLib import TTFont
        from fontTools.varLib import instancer
        src = os.path.join(FONTS, "_src_" + os.path.basename(rel).replace("%5B", "[").replace("%5D", "]"))
        print("get ", os.path.basename(src)[5:]); fetch(rel, src)
        for name, loc in outs.items():
            print("make", name, loc)
            inst = instancer.instantiateVariableFont(TTFont(src), loc, updateFontNames=False)
            inst.save(os.path.join(FONTS, name))
        os.remove(src)
    print("fonts ready in", FONTS)

if __name__ == "__main__":
    main()
