# Licences

Built by Des Dreckett for the AI Video Motion community

## Code: statanim.py, render_styles.sh, fetch_fonts.sh, fetch_fonts.py, themes/*.json, specs/**/*.json, README.md, STYLES.md
MIT License, Copyright (c) 2026 Des Dreckett. The full text is in `LICENSE`.

## Example data: specs/example_data.json, specs/example_data_landscape.json
The data is invented and illustrative ("Example Co." is fictional). It comes from no third party and falls under the same licence as the code (MIT).

## Fonts (fonts/): all SIL Open Font License 1.1, which allows commercial use, embedding and redistribution with the licence attached. The fonts must not be sold on their own, and modified versions must not use any Reserved Font Name.
The repository ships only the licence texts (`fonts/OFL-*.txt`). The `.ttf` files are downloaded by `fetch_fonts.sh` / `fetch_fonts.py` from the official Google Fonts repository (https://github.com/google/fonts, `ofl/<family>/`). Inter and Oswald are published there only as variable fonts, so the static weights the themes use (Inter-Regular/Medium/SemiBold/Bold/ExtraBold, Oswald-SemiBold/Bold) are generated locally from `Inter[opsz,wght].ttf` (opsz 14) and `Oswald[wght].ttf` with fontTools. These are unmodified instances of the OFL originals, used under the same licence.
| Font files | Licence file | Source |
|---|---|---|
| Anton-Regular.ttf | OFL-Anton.txt | https://fonts.google.com/specimen/Anton · https://github.com/googlefonts/AntonFont |
| ArchivoBlack-Regular.ttf | OFL-ArchivoBlack.txt | https://fonts.google.com/specimen/Archivo+Black · https://github.com/Omnibus-Type/ArchivoBlack |
| BarlowCondensed-ExtraBold.ttf | OFL-BarlowCondensed.txt | https://fonts.google.com/specimen/Barlow+Condensed · https://github.com/jpt/barlow |
| IBMPlexSansCondensed-Medium.ttf, -SemiBold.ttf | OFL-IBMPlexSansCondensed.txt (Reserved Font Name "Plex") | https://fonts.google.com/specimen/IBM+Plex+Sans+Condensed · https://github.com/IBM/plex |
| Inter-Regular/Medium/SemiBold/Bold/ExtraBold.ttf | OFL-Inter.txt | https://fonts.google.com/specimen/Inter · https://github.com/rsms/inter |
| JetBrainsMono[wght].ttf | OFL-JetBrainsMono.txt | https://fonts.google.com/specimen/JetBrains+Mono · https://github.com/JetBrains/JetBrainsMono |
| LibreFranklin[wght].ttf | OFL-LibreFranklin.txt | https://fonts.google.com/specimen/Libre+Franklin · https://github.com/googlefonts/Libre-Franklin |
| Oswald-SemiBold.ttf, Oswald-Bold.ttf | OFL-Oswald.txt | https://fonts.google.com/specimen/Oswald · https://github.com/googlefonts/OswaldFont |
| PlayfairDisplay[wght].ttf, PlayfairDisplay-Italic[wght].ttf | OFL-PlayfairDisplay.txt (Reserved Font Name "Playfair Display") | https://fonts.google.com/specimen/Playfair+Display · https://github.com/clauseggers/Playfair-Display |
| SourceSerif4[opsz,wght].ttf | OFL-SourceSerif4.txt | https://fonts.google.com/specimen/Source+Serif+4 · https://github.com/adobe-fonts/source-serif |
| SpaceGrotesk[wght].ttf | OFL-SpaceGrotesk.txt | https://fonts.google.com/specimen/Space+Grotesk · https://github.com/floriankarsten/space-grotesk |

## Python dependencies (installed by the user from PyPI, not bundled)
| Package | Licence | Source |
|---|---|---|
| pillow 12.3.0 | MIT-CMU (HPND) | https://github.com/python-pillow/Pillow |
| numpy 2.5.4 | BSD-3-Clause | https://github.com/numpy/numpy |
| fonttools 4.66.1 | MIT | https://github.com/fonttools/fonttools |

## External tool (not bundled)
ffmpeg is LGPL-2.1+ or GPL depending on the build (https://ffmpeg.org/legal.html). The kit calls the ffmpeg binary that is already on your system. Output videos are yours.

## Your content
Any data, logos or images you add are your responsibility. Make sure you have the right to publish them.
