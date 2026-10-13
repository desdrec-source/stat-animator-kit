# Stat Animator: starter kit

Built by Des Dreckett for the AI Video Motion community

Stat Animator turns a JSON data file into an animated stat infographic: an H.264 MP4 plus a final-frame PNG. It runs on the CPU with Python (Pillow + numpy) and ffmpeg. The layout, theme and data are separate files, so you can change any one of them without touching the others.

> The numbers in `specs/example_data*.json` are **EXAMPLE DATA**. They are invented, illustrative figures for a fictional "Example Co.", not real statistics.

## Install
1. Python 3.11+ (tested on 3.13).
2. ffmpeg on your PATH:
   - macOS: `brew install ffmpeg`
   - Debian/Ubuntu: `sudo apt install ffmpeg`
   - Windows: `winget install ffmpeg` (or download from https://ffmpeg.org/download.html), then check that `ffmpeg -version` works.
3. Create a virtual environment:
```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip install -r requirements.txt
```
4. Fetch the fonts (once). The repo contains only the font licences; this downloads the OFL `.ttf` files from github.com/google/fonts into `fonts/`:
```
bash fetch_fonts.sh                                 # or: .venv/bin/python fetch_fonts.py   (Windows: .venv\Scripts\python fetch_fonts.py)
```

## Render
```
bash render_styles.sh            # all vertical (1080x1920) styles -> out/styles/<name>.mp4 + <name>_final.png
bash render_styles.sh landscape  # 1920x1080 layouts
bash render_styles.sh all
.venv/bin/python statanim.py specs/styles/neo_brutalist_vertical.json -o out/x.mp4 --final-png out/x_final.png --png-at 3.0:out/x_mid.png
```
Options:
- `--data FILE`: use different numbers or text with the same layout.
- `--theme NAME|path`: re-skin the video. This overrides the spec's theme.
- `--no-video`: render stills and run the layout check only.
- `--crf 16 --preset slow --workers N`: encoder and speed settings.

Set `PYTHON=/path/to/python` to make `render_styles.sh` use a different interpreter.

Every render starts with a layout check that reports elements clipping the canvas or overlapping each other.

## The 6 styles (themes/)
| Theme | Layouts |
|---|---|
| dark_stat_card | vertical, landscape |
| editorial_paper | vertical |
| highlighter_newsprint | vertical |
| kinetic_big_number | vertical, landscape |
| neo_brutalist | vertical |
| terminal_market_desk | vertical |

## Swap data or theme
- **Data:** copy `specs/example_data.json` (used by every vertical layout and by `dark_stat_card_landscape`) or `specs/example_data_landscape.json` (used by `kinetic_big_number_landscape`). Edit the values and text but keep the keys. Then either point the layout's `"data_file"` at your copy or pass `--data my.json`. Percentages that are meant to add up must still add up (for example `bar_*` = 100).
- **Theme:** pass `--theme editorial_paper`, or set `"theme"` in the layout. You can also copy a file in `themes/` to make your own palette and fonts. Fonts are loaded from `fonts/` and can use variable-font axes, for example `"LibreFranklin[wght].ttf:wght=700"`.
- **Layout:** copy a file in `specs/styles/` and move, resize or add elements. Text values like `"$key"` / `"${key}"` are filled in from the data file.
- Long text: use `max_width`, `wrap_width` and `max_lines` on text elements, and re-check the layout report.

## QA checklist (before you publish a video)
- [ ] The layout check prints `OK (no clipping / overlaps)`.
- [ ] Open `*_final.png`: no text is clipped or overlapping, and long titles wrap cleanly.
- [ ] Watch the MP4 once: counters land on the right final values and bars match the percentages.
- [ ] Shares add up (100%) and totals equal the sum of their parts.
- [ ] The source line names your real source. Remove the "EXAMPLE DATA" wording only once you are using real data.
- [ ] You have the rights to any logos, images or data you add (see LICENCES.md).
- [ ] Output plays on the target platform (H.264, yuv420p, faststart).

## Style notes
See STYLES.md for a short summary of each style.

## Licences
See LICENCES.md.
