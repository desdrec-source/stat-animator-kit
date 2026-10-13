#!/usr/bin/env python3
"""statanim - data-driven animated statistics infographic renderer.

Usage:
  python statanim.py SPEC.json [-o out.mp4] [--theme NAME|path] [--data data.json]
                    [--final-png f.png] [--png-at SECONDS:path.png ...] [--no-video] [--workers N]

The JSON spec contains canvas, an optional theme (themes/<name>.json), colors, fonts, background
and a list of elements (text / line / donut / bar / panel / rule / gridlines / highlight / marker).
See README.md for the full format.
"""
import argparse, copy, json, math, os, re, subprocess, sys, time
from multiprocessing import Pool
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
DIGITS = "0123456789"

# ================================================================ easing registry
def _clamp(p):
    return min(max(p, 0.0), 1.0)

def _out_bounce(p):
    n1, d1 = 7.5625, 2.75
    if p < 1 / d1:
        return n1 * p * p
    if p < 2 / d1:
        p -= 1.5 / d1
        return n1 * p * p + 0.75
    if p < 2.5 / d1:
        p -= 2.25 / d1
        return n1 * p * p + 0.9375
    p -= 2.625 / d1
    return n1 * p * p + 0.984375

def _cubic_bezier(x1, y1, x2, y2):
    def bez(t, a, b):
        return 3 * a * (1 - t) ** 2 * t + 3 * b * (1 - t) * t * t + t ** 3
    def f(p):
        t = p
        for _ in range(8):  # Newton on x(t) = p
            x = bez(t, x1, x2) - p
            dx = 3 * x1 * (1 - t) ** 2 + 6 * (x2 - x1) * (1 - t) * t + 3 * (1 - x2) * t * t
            if abs(dx) < 1e-6:
                break
            t = _clamp(t - x / dx)
        return bez(t, y1, y2)
    return f

def _spring(stiffness=180.0, damping=12.0):
    """damped spring normalised to the element duration (lands exactly on 1 at p = 1)."""
    w0 = math.sqrt(stiffness)
    z = min(damping / (2 * w0), 0.999)
    wd = w0 * math.sqrt(1 - z * z)
    T = 6.0 / (z * w0)  # time for the envelope to decay to ~e^-6
    def f(p):
        tt = p * T
        return 1 - math.exp(-z * w0 * tt) * (math.cos(wd * tt) + z * w0 / wd * math.sin(wd * tt))
    return f

def _make_back(s):
    out = lambda p: 1 + (s + 1) * (p - 1) ** 3 + s * (p - 1) ** 2
    s2 = s * 1.525
    def inout(p):
        if p < 0.5:
            return ((2 * p) ** 2 * ((s2 + 1) * 2 * p - s2)) / 2
        return ((2 * p - 2) ** 2 * ((s2 + 1) * (p * 2 - 2) + s2) + 2) / 2
    inn = lambda p: (s + 1) * p ** 3 - s * p * p
    return inn, out, inout

EASINGS = {
    "linear": lambda p: p,
    "in_quad": lambda p: p * p,
    "out_quad": lambda p: 1 - (1 - p) ** 2,
    "in_out_quad": lambda p: 2 * p * p if p < 0.5 else 1 - (-2 * p + 2) ** 2 / 2,
    "in_cubic": lambda p: p ** 3,
    "out_cubic": lambda p: 1 - (1 - p) ** 3,
    "in_out_cubic": lambda p: 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2,
    "out_quart": lambda p: 1 - (1 - p) ** 4,
    "in_out_quart": lambda p: 8 * p ** 4 if p < 0.5 else 1 - (-2 * p + 2) ** 4 / 2,
    "out_quint": lambda p: 1 - (1 - p) ** 5,
    "in_out_quint": lambda p: 16 * p ** 5 if p < 0.5 else 1 - (-2 * p + 2) ** 5 / 2,
    "in_expo": lambda p: 0.0 if p <= 0 else 2 ** (10 * p - 10),
    "out_expo": lambda p: 1.0 if p >= 1 else 1 - 2 ** (-10 * p),
    "in_out_expo": lambda p: (0.0 if p <= 0 else 1.0 if p >= 1 else
                              2 ** (20 * p - 10) / 2 if p < 0.5 else (2 - 2 ** (-20 * p + 10)) / 2),
    "in_sine": lambda p: 1 - math.cos(p * math.pi / 2),
    "out_sine": lambda p: math.sin(p * math.pi / 2),
    "in_out_sine": lambda p: -(math.cos(math.pi * p) - 1) / 2,
    "out_elastic": lambda p: (p if p in (0, 1) else
                              2 ** (-10 * p) * math.sin((p * 10 - 0.75) * (2 * math.pi) / 3) + 1),
    "out_bounce": _out_bounce,
}
_EASE_CACHE = {}
_EASE_RE = re.compile(r"^([a-z_]+)(?:\(([^)]*)\))?$")

def get_ease(name, overshoot=1.70158):
    """'out_cubic', 'out_back', 'out_back(2.2)', 'in_out_back', 'steps(5)', 'cubic_bezier(.2,.8,.2,1)',
    'spring(180,12)'.  Returns f(p) with p clamped to [0,1] and f(1) == 1 exactly."""
    if callable(name):
        return name
    key = (name, overshoot)
    if key in _EASE_CACHE:
        return _EASE_CACHE[key]
    m = _EASE_RE.match(str(name).strip())
    if not m:
        raise ValueError(f"bad easing {name!r}")
    base, args = m.group(1), [float(a) for a in (m.group(2) or "").split(",") if a.strip()]
    if base in EASINGS:
        f0 = EASINGS[base]
    elif base in ("in_back", "out_back", "in_out_back"):
        inn, out, inout = _make_back(args[0] if args else overshoot)
        f0 = {"in_back": inn, "out_back": out, "in_out_back": inout}[base]
    elif base in ("steps", "steps_end"):
        n = int(args[0]) if args else 5
        f0 = lambda p, n=n: math.floor(p * n + 1e-9) / n
    elif base == "steps_start":
        n = int(args[0]) if args else 5
        f0 = lambda p, n=n: math.ceil(p * n - 1e-9) / n
    elif base == "cubic_bezier":
        f0 = _cubic_bezier(*args)
    elif base == "spring":
        f0 = _spring(*args)
    else:
        raise ValueError(f"unknown easing {name!r}")
    def f(p, f0=f0):
        p = _clamp(p)
        if p >= 1:
            return 1.0
        if p <= 0:
            return 0.0
        return f0(p)
    _EASE_CACHE[key] = f
    return f

def ease_out_cubic(p):  # kept for backwards compatibility
    return get_ease("out_cubic")(p)

def progress(t, start, dur):
    if dur <= 0:
        return 1.0 if t >= start else 0.0
    return min(max((t - start) / dur, 0.0), 1.0)

def quantize(t, step):
    """step-time: hold each pose for 1/step s ('on twos' at 12 fps)."""
    if not step:
        return t
    return math.floor(t * step + 1e-6) / step

# ================================================================ colours / fonts / theme
def hex2rgba(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    a = int(h[6:8], 16) if len(h) >= 8 else 255
    return r + (a,)

def hex2rgb(h):
    return hex2rgba(h)[:3]

def deep_merge(a, b):
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out

def load_theme(ref, spec_dir="."):
    """theme name (themes/<name>.json), path, or inline dict; follows "extends"."""
    if not ref:
        return {}
    if isinstance(ref, dict):
        th = ref.get("theme", ref)
    else:
        cands = [ref, os.path.join(spec_dir, ref), os.path.join(HERE, "themes", ref),
                 os.path.join(HERE, "themes", ref + ".json")]
        p = next((c for c in cands if os.path.isfile(c)), None)
        if p is None:
            raise FileNotFoundError(f"theme {ref!r} not found (looked in themes/)")
        with open(p) as f:
            js = json.load(f)
        th = js.get("theme", js)
        spec_dir = os.path.dirname(p)
    if th.get("extends"):
        th = deep_merge(load_theme(th["extends"], spec_dir), {k: v for k, v in th.items() if k != "extends"})
    return th

_FEAT_CACHE = {}
def font_has_feature(path, tag):
    key = (path, tag)
    if key not in _FEAT_CACHE:
        try:
            from fontTools.ttLib import TTFont
            t = TTFont(path, lazy=True)
            feats = set()
            if "GSUB" in t and t["GSUB"].table.FeatureList:
                feats = {r.FeatureTag for r in t["GSUB"].table.FeatureList.FeatureRecord}
            _FEAT_CACHE[key] = tag in feats
        except Exception:
            _FEAT_CACHE[key] = False
    return _FEAT_CACHE[key]

_AXIS_TAGS = {b"Weight": "wght", b"Optical size": "opsz", b"Optical Size": "opsz", b"Width": "wdth",
              b"Italic": "ital", b"Slant": "slnt"}


class Ctx:
    def __init__(self, spec, spec_dir, theme):
        self.spec = spec
        self.theme = theme or {}
        self.colors = dict(self.theme.get("colors", {}))
        self.colors.update(spec.get("colors", {}))
        self.fonts = dict(self.theme.get("fonts", {}))
        self.fonts.update(spec.get("fonts", {}))
        self.spec_dir = spec_dir
        self._fcache = {}
        self._faces = {}
        self.els = {}

    def th(self, path, default=None):
        cur = self.theme
        for k in path.split("."):
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def rgba(self, c, opacity=1.0):
        """role name | 'series:1' | '#RRGGBB' | '#RRGGBBAA' | [r,g,b(,a)] -> (r,g,b,a)"""
        for _ in range(8):
            if isinstance(c, str) and not c.startswith("#"):
                name, _, idx = c.partition(":")
                if idx and isinstance(self.colors.get(name), list):
                    c = self.colors[name][int(idx)]
                elif c in self.colors:
                    c = self.colors[c]
                else:
                    raise KeyError(f"unknown colour {c!r}")
            else:
                break
        r = hex2rgba(c) if isinstance(c, str) else tuple(int(v) for v in c) + ((255,) if len(c) == 3 else ())
        return r[:3] + (int(round(r[3] * opacity)),)

    def color(self, c):
        return self.rgba(c)[:3]

    def font(self, name, size):
        spec = self.fonts.get(name, name)
        path, _, var = spec.partition(":")  # "File[wght].ttf:wght=700,opsz=48"
        key = (spec, int(size))
        if key not in self._fcache:
            cands = [path, os.path.join(self.spec_dir, path), os.path.join(HERE, "fonts", path)]
            p = next((c for c in cands if os.path.exists(c)), None)
            if p is None:
                raise FileNotFoundError(f"font {name!r} -> {path!r} not found")
            f = ImageFont.truetype(p, int(size), layout_engine=ImageFont.Layout.RAQM)
            if var:
                want = dict(kv.split("=") for kv in var.split(","))
                vals = []
                for ax in f.get_variation_axes():
                    nm = ax["name"]
                    tag = _AXIS_TAGS.get(nm, nm.decode() if isinstance(nm, bytes) else nm)
                    w = want.get(tag)
                    v = float(size) if w == "auto" else float(w) if w is not None else ax["default"]
                    vals.append(min(max(v, ax["minimum"]), ax["maximum"]))
                f.set_variation_by_axes(vals)
            f._sa_path = p
            self._fcache[key] = f
        return self._fcache[key]

    def face(self, font, tabular=None, features=None):
        tabular = tabular or self.th("numbers.tabular", "auto")
        features = tuple(features if features is not None else self.th("numbers.features", []) or [])
        key = (id(font), tabular, features)
        if key not in self._faces:
            self._faces[key] = NumFace(font, tabular, list(features))
        return self._faces[key]


# ================================================================ numbers
def fmt_number(v, decimals=0, thousands=True, prefix="", suffix="", tsep=",", dsep=".", compact=False,
               compact_decimals=1, sign="auto"):
    neg = v < 0
    a = abs(v)
    unit = ""
    if compact:
        for k, u in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
            if a >= k:
                a, unit, decimals = a / k, u, compact_decimals
                break
    if decimals == 0:
        s = f"{int(round(a)):,}" if thousands else f"{int(round(a))}"
    else:
        s = f"{a:,.{decimals}f}" if thousands else f"{a:.{decimals}f}"
    if tsep != "," or dsep != ".":
        s = s.replace(",", "\0").replace(".", dsep).replace("\0", tsep)
    sg = "-" if neg else ("+" if sign == "always" and v > 0 else "")
    return f"{prefix}{sg}{s}{unit}{suffix}"


class NumFace:
    """Digit layout for one font.
    mode plain: the font's digits are already equal width (Inter Tight? JetBrains Mono, Archivo Black...)
    mode tnum : OpenType tabular figures (Inter, Source Serif 4, Barlow Condensed, Space Grotesk)
    mode slots: each digit is centred in a fixed cell = widest digit (Anton, Oswald, Libre Franklin...)
    numbers.tabular: auto (pick the above) | tnum | slots | off (natural spacing, for numbers that do not count).
    extra: OpenType features always applied to numbers, e.g. ["lnum"] for lining figures in Playfair."""
    def __init__(self, font, tabular="auto", extra=None):
        self.f = font
        extra = list(extra or [])
        base = extra or None
        adv = [font.getlength(d, features=base) for d in DIGITS]
        equal = max(adv) - min(adv) < 0.01
        has_tnum = font_has_feature(getattr(font, "_sa_path", font.path), "tnum")
        tadv = [font.getlength(d, features=["tnum"] + extra) for d in DIGITS] if has_tnum else adv
        tequal = max(tadv) - min(tadv) < 0.01
        if tabular == "off" or (equal and tabular != "slots"):
            self.mode, self.feat, self.cell = "plain", base, max(adv)
        elif tabular in ("auto", "tnum") and has_tnum and tequal:
            self.mode, self.feat, self.cell = "tnum", ["tnum"] + extra, tadv[0]
        else:
            self.mode, self.feat, self.cell = "slots", base, max(adv)
        bb = font.getbbox("0123456789", anchor="ls", features=self.feat)
        self.cap = -bb[1]
        self.desc = max(bb[3], 0)

    def width(self, s):
        if self.mode != "slots":
            return self.f.getlength(s, features=self.feat)
        return sum(w for _, _, w in self.cells(s))

    def cells(self, s):
        """(char, x_offset, width) per character - fixed geometry for every digit."""
        x = 0.0
        for ch in s:
            w = self.cell if ch in DIGITS else self.f.getlength(ch, features=self.feat)
            yield ch, x, w
            x += w

    def draw(self, dr, x, y, s, fill):
        if self.mode != "slots":
            dr.text((x, y), s, font=self.f, fill=fill, anchor="ls", features=self.feat)
            return
        for ch, ox, w in self.cells(s):
            if ch in DIGITS:
                dr.text((x + ox + w / 2, y), ch, font=self.f, fill=fill, anchor="ms", features=self.feat)
            else:
                dr.text((x + ox, y), ch, font=self.f, fill=fill, anchor="ls", features=self.feat)


_STRIPS = {}
def odometer_strip(face, fill):
    """vertical strip of digits 0..9,0 for rolling numbers.  Digits are spaced further apart than the
    clipping window (pitch = 1.5x glyph height) so mid-roll you see motion, not two stacked digits.
    Returns dict(img, pitch, win_h, pad, cw, y_first) where y_first = window top for digit 0."""
    key = (id(face), tuple(fill))
    if key not in _STRIPS:
        pad = int(math.ceil(0.08 * face.f.size))
        gh = int(math.ceil(face.cap + face.desc))
        pitch = int(math.ceil(gh * 1.5))
        win_h = gh + 2 * pad
        cw = int(math.ceil(face.cell)) + 4
        top = pad
        im = Image.new("RGBA", (cw, top + pitch * 11 + win_h), (0, 0, 0, 0))
        dr = ImageDraw.Draw(im)
        for i, d in enumerate(DIGITS + "0"):
            dr.text((cw / 2, top + i * pitch + pad + face.cap), d, font=face.f, fill=tuple(fill), anchor="ms",
                    features=face.feat)
        _STRIPS[key] = dict(img=im, pitch=pitch, win_h=win_h, pad=pad, cw=cw, y_first=top)
    return _STRIPS[key]


# ================================================================ elements
class Element:
    def __init__(self, d, ctx, defaults):
        self.d = d
        self.ctx = ctx
        self.id = d.get("id", d["type"])
        self.start = float(d.get("start", 0))
        self.dur = float(d.get("dur", 0))
        self.enter = d.get("enter", defaults.get("enter", "fade"))
        self.enter_dur = float(d.get("enter_dur", defaults.get("enter_dur", 0.6)))
        self.enter_offset = float(d.get("enter_offset", defaults.get("enter_offset", 30)))
        self.enter_scale = float(d.get("enter_scale", defaults.get("enter_scale", 1.15)))
        ov = float(d.get("back_overshoot", defaults.get("back_overshoot", 1.70158)))
        base_ease = d.get("ease", defaults.get("ease", "out_cubic"))
        self.ease = get_ease(base_ease, ov)
        self.ease_enter = get_ease(d.get("ease_enter", defaults.get("ease_enter", base_ease)), ov)
        self.ease_emph = get_ease(d.get("ease_emphasis", defaults.get("ease_emphasis", "out_back")), ov)
        self.ease_count = get_ease(d.get("ease_count", defaults.get("ease_count", "out_cubic")), ov)
        self.time_step = float(d.get("time_step", defaults.get("time_step", 0)) or 0)
        self.allow_overlap = d.get("allow_overlap", False)
        self.blend = d.get("blend", "normal")
        self.check_layout = d.get("check", True)

    def end_time(self):
        return self.start + max(self.dur, self.enter_dur if self.enter not in ("none", "cut") else 0)

    def enter_state(self, t):
        """dict(op, dx, dy, scale, wipe).  enter: fade | slide_up/down/left/right | pop | wipe |
        slam_up/down/left/right | none | cut"""
        st = {"op": 1.0, "dx": 0.0, "dy": 0.0, "scale": 1.0, "wipe": 1.0}
        if self.enter in ("none", "cut"):
            st["op"] = 1.0 if t >= self.start else 0.0
            return st
        p = progress(t, self.start, self.enter_dur)
        e = self.ease_enter(p)
        if self.enter == "pop":  # scale enter_scale -> 1 with overshoot, fast fade-in
            st["scale"] = self.enter_scale + (1 - self.enter_scale) * self.ease_emph(p)
            st["op"] = _clamp(p * 4)
            return st
        if self.enter in ("wipe", "wipe_left"):
            st["wipe"] = e
            return st
        if self.enter.startswith("slam_"):  # large offset + overshoot landing
            off = (1 - self.ease_emph(p)) * self.enter_offset
            st["op"] = _clamp(p * 5)
            st["dx"], st["dy"] = {"slam_up": (0, off), "slam_down": (0, -off), "slam_left": (off, 0),
                                  "slam_right": (-off, 0)}[self.enter]
            return st
        off = (1 - e) * self.enter_offset
        st["op"] = e
        if self.enter == "slide_up":
            st["dy"] = off
        elif self.enter == "slide_down":
            st["dy"] = -off
        elif self.enter == "slide_left":
            st["dx"] = off
        elif self.enter == "slide_right":
            st["dx"] = -off
        return st

    def draw(self, layer, t):
        raise NotImplementedError

    def bbox(self):
        raise NotImplementedError


def _apply_role(d, ctx):
    """merge theme.type[role] defaults (font, size, color, case, line_height) under explicit keys."""
    role = d.get("role")
    out = dict(ctx.th(f"type.{role}", {}) or {}) if role else {}
    out.update(d)
    return out


def _case(s, c):
    return s.upper() if c == "upper" else s.lower() if c == "lower" else s


class TextEl(Element):
    """Static text. x,y + Pillow anchor; wrap_width, max_width (auto-shrink), max_lines, balance,
    word_stagger (kinetic word-by-word pop-in)."""
    def __init__(self, d, ctx, defaults):
        d = _apply_role(d, ctx)
        super().__init__(d, ctx, defaults)
        size = d.get("size", 32)
        fname = d.get("font", "body")
        text = _case(str(d["text"]), d.get("case"))
        if d.get("break_at"):
            text = text.replace(d["break_at"], "\n")
        wrap = d.get("wrap_width")
        maxw = d.get("max_width")
        while True:
            f = ctx.font(fname, size)
            lines = self._wrap(text, f, wrap) if wrap else text.split("\n")
            if wrap and d.get("balance") and len(lines) > 1:
                w = wrap
                while w > 50 and len(self._wrap(text, f, w - 10)) == len(lines):
                    w -= 10
                lines = self._wrap(text, f, w)
            widest = max(f.getlength(l) for l in lines)
            too_many = d.get("max_lines") and len(lines) > d["max_lines"]
            if ((maxw and widest > maxw) or too_many) and size > 8:
                size -= 1
                continue
            break
        self.fname = fname
        self.font, self.size, self.lines = f, size, lines
        self.lh = d.get("line_height", 1.2) * size
        self.color = ctx.rgba(d.get("color", "text"))
        self.anchor = d.get("anchor", "ls")
        self.word_stagger = float(d.get("word_stagger", 0))
        self.word_dur = float(d.get("word_dur", 0.25))
        if self.word_stagger:
            self.enter = "none"
            self.n_words = sum(len(l.split()) for l in lines)

    def end_time(self):
        if self.word_stagger:
            return self.start + (self.n_words - 1) * self.word_stagger + self.word_dur
        return super().end_time()

    @staticmethod
    def _wrap(text, f, width):
        out = []
        for para in text.split("\n"):
            cur = ""
            for w in para.split(" "):
                cand = (cur + " " + w).strip()
                if cur and f.getlength(cand) > width:
                    out.append(cur)
                    cur = w
                else:
                    cur = cand
            out.append(cur)
        return out

    def _positions(self):
        x, y = self.d["x"], self.d["y"]
        for i, l in enumerate(self.lines):
            yield l, (x, y + i * self.lh)

    def draw(self, layer, t):
        dr = ImageDraw.Draw(layer)
        if not self.word_stagger:
            for l, p in self._positions():
                dr.text(p, l, font=self.font, fill=self.color, anchor=self.anchor)
            return
        wi = 0
        ha, va = self.anchor[0], self.anchor[1]
        for l, (x, y) in self._positions():
            lw = self.font.getlength(l)
            x0 = x - (0 if ha == "l" else lw / 2 if ha == "m" else lw)
            words = l.split(" ")
            for k, w in enumerate(words):
                ws = self.start + wi * self.word_stagger
                wi += 1
                if t < ws:
                    continue
                p = progress(t, ws, self.word_dur)
                sc = 1.0 + 0.3 * (1 - self.ease_emph(p))
                wx = x0 + (self.font.getlength(" ".join(words[:k]) + " ") if k else 0)
                ww = self.font.getlength(w)
                if p >= 1:
                    dr.text((wx, y), w, font=self.font, fill=self.color, anchor="l" + va)
                else:
                    f2 = self.ctx.font(self.fname, max(1, round(self.size * sc)))
                    ww2 = f2.getlength(w)
                    a = int(self.color[3] * _clamp(p * 3))
                    dr.text((wx + ww / 2 - ww2 / 2, y), w, font=f2, fill=self.color[:3] + (a,), anchor="l" + va)

    def bbox(self):
        dr = ImageDraw.Draw(Image.new("L", (1, 1)))
        bs = [dr.textbbox(p, l, font=self.font, anchor=self.anchor) for l, p in self._positions()]
        return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))


class LineEl(Element):
    """One baseline of runs: text, counting numbers and colour swatches.
    Number runs get a fixed slot (width of the final value) and are right-aligned inside it, so nothing
    after them moves while they count.  Digits use NumFace (tnum or fixed per-digit cells), so condensed
    fonts without tabular figures do not jitter.  count_mode: ease | linear | steps | odometer | none.
    A run with "sync": "<bar/donut id>" (+ optional "seg": i) shows value x that element's fill progress,
    so it lands on the final value on exactly the frame the fill completes."""
    def __init__(self, d, ctx, defaults):
        d = _apply_role(d, ctx)
        super().__init__(d, ctx, defaults)
        th = ctx.th
        self.count_mode = d.get("count_mode", defaults.get("count_mode", "ease"))
        self.count_steps = int(d.get("count_steps", th("numbers.count_steps", 12)))
        self.runs = []
        x = 0.0
        for r in d["runs"]:
            r = dict(r)
            if "swatch" in r:
                sz = r.get("size", 24)
                bw = r.get("border", 0)
                r.update(_kind="sw", _x=x, _w=sz, _color=ctx.rgba(r["swatch"]), _border=bw,
                         _bcolor=ctx.rgba(r.get("border_color", "border")) if bw else None)
                x += sz + r.get("gap", 0.45 * sz)
            else:
                size = r.get("size", d.get("size", 32))
                fname = r.get("font", d.get("font", "body"))
                f = ctx.font(fname, size)
                r["_font"] = f
                r["_color"] = ctx.rgba(r.get("color", d.get("color", "text")))
                if "value" in r:
                    r["value"] = float(r["value"])
                    face = ctx.face(f, r.get("tabular", d.get("tabular")), r.get("features", d.get("features")))
                    nk = dict(decimals=r.get("decimals", 0), thousands=r.get("thousands", True),
                              tsep=th("numbers.thousands_sep", ","), dsep=th("numbers.decimal_sep", "."),
                              compact=r.get("compact", th("numbers.compact", False)),
                              compact_decimals=r.get("compact_decimals", th("numbers.compact_decimals", 1)),
                              sign=r.get("sign", th("numbers.sign", "auto")))
                    body = fmt_number(r["value"], **nk)
                    pre, suf = r.get("prefix", ""), r.get("suffix", "")
                    ps = r.get("prefix_scale", d.get("prefix_scale", 1.0))
                    ss = r.get("suffix_scale", d.get("suffix_scale", 1.0))
                    fp = ctx.font(fname, max(1, round(size * ps))) if pre else None
                    fs = ctx.font(fname, max(1, round(size * ss))) if suf else None
                    r.update(_kind="num", _face=face, _nk=nk, _body=body, _final=pre + body + suf,
                             _fp=fp, _fs=fs, _pw=fp.getlength(pre) if pre else 0,
                             _sw=fs.getlength(suf) if suf else 0,
                             _salign=r.get("suffix_align", d.get("suffix_align", "baseline")),
                             _palign=r.get("prefix_align", d.get("prefix_align", "baseline")),
                             _mode=r.get("count_mode", self.count_mode),
                             _flash=self._flash_cfg(r.get("flash", d.get("flash"))))
                    w = r["_pw"] + face.width(body) + r["_sw"]
                else:
                    r.update(_kind="txt")
                    r["text"] = _case(str(r["text"]), r.get("case", d.get("case")))
                    w = f.getlength(r["text"])
                r.update(_x=x, _w=w)
                x += w
            self.runs.append(r)
        self.total_w = x
        align = d.get("align", "left")
        self.x0 = d["x"] - (0 if align == "left" else self.total_w / (2 if align == "center" else 1))
        self.y = d["y"]
        self.count_start = self.start + float(d.get("count_delay", 0.0))
        self.count_dur = float(d.get("count_dur", self.dur))
        self.sync_default = d.get("sync")
        for r in self.runs:
            r["_sync"] = None

    def _flash_cfg(self, fl):
        """"flash": true -> theme numbers.final_flash; or an explicit {color, text_color, dur, pad}."""
        if fl is True:
            fl = self.ctx.th("numbers.final_flash", {"color": "accent", "text_color": "bg", "dur": 0.15})
        return fl or None

    def link(self, els):
        for r in self.runs:
            if r["_kind"] == "num":
                s = r.get("sync", self.sync_default)
                if s:
                    if s not in els:
                        raise KeyError(f"{self.id}: sync target {s!r} not found")
                    r["_sync"] = els[s]

    def _run_done(self, r):
        if r["_sync"] is not None:
            return r["_sync"].fill_end()
        return self.count_start + r.get("delay", 0) + r.get("dur", self.count_dur)

    def end_time(self):
        e = super().end_time()
        for r in self.runs:
            if r["_kind"] == "num":
                fl = r["_flash"]
                e = max(e, self._run_done(r) + (float(fl.get("dur", 0.15)) if fl else 0))
        return e

    def _prog(self, r, t):
        """(linear progress p, displayed fraction e) for a number run."""
        if r["_sync"] is not None:
            e = r["_sync"].fill_progress(t, r.get("seg"))
            return e, e
        s0 = self.count_start + r.get("delay", 0)
        p = progress(t, s0, r.get("dur", self.count_dur))
        m = r["_mode"]
        if m == "linear":
            e = p
        elif m == "steps":
            e = math.floor(p * self.count_steps + 1e-9) / self.count_steps
        elif m in ("none", "appear"):
            e = 1.0 if t >= s0 else 0.0
        else:
            e = self.ease_count(p)
        return p, e

    def _draw_num(self, layer, dr, r, x, t):
        face = r["_face"]
        p, e = self._prog(r, t)
        col = r["_color"]
        tdone = self._run_done(r)
        fl = r["_flash"]
        if fl and tdone <= t < tdone + float(fl.get("dur", 0.15)):
            px, py = fl.get("pad", [10, 6])
            dr.rectangle((x - px, self.y - face.cap - py, x + r["_w"] + px, self.y + face.desc + py),
                         fill=self.ctx.rgba(fl.get("color", "accent")))
            col = self.ctx.rgba(fl.get("text_color", "bg"))
        right = x + r["_w"]
        pre, suf = r.get("prefix", ""), r.get("suffix", "")
        if suf:
            sy = self.y if r["_salign"] == "baseline" else self.y - face.cap + self.ctx.face(r["_fs"]).cap
            dr.text((right - r["_sw"], sy), suf, font=r["_fs"], fill=col, anchor="ls")
        bright = right - r["_sw"]
        if r["_mode"] == "odometer" and e < 1 and r["_sync"] is None:
            self._draw_odometer(layer, dr, r, bright, p, col)
            body_w = face.width(r["_body"])
        else:
            if e >= 1:
                body = r["_body"]
            else:
                v0 = float(r.get("from", 0))
                body = fmt_number(v0 + (r["value"] - v0) * e, **r["_nk"])
            body_w = face.width(body)
            face.draw(dr, bright - body_w, self.y, body, col)
        if pre:
            psy = self.y if r["_palign"] == "baseline" else self.y - face.cap + self.ctx.face(r["_fp"]).cap
            dr.text((bright - body_w - r["_pw"], psy), pre, font=r["_fp"], fill=col, anchor="ls")

    def _draw_odometer(self, layer, dr, r, bright, p, col):
        """Rolling digits: each digit column scrolls from 0 to its target digit inside a clipped window
        (lower places make one extra full turn); higher place values settle later."""
        face, body = r["_face"], r["_body"]
        x = bright - face.width(body)
        cols = list(face.cells(body))
        dpos = [i for i, (ch, _, _) in enumerate(cols) if ch in DIGITS]
        n = len(dpos)
        st = odometer_strip(face, col)
        strip, pitch, win_h, pad, cw = st["img"], st["pitch"], st["win_h"], st["pad"], st["cw"]
        top = int(round(self.y - face.cap - pad))
        fade = np.ones(win_h, np.float32)
        if pad:
            ramp = np.linspace(0, 1, pad + 1, dtype=np.float32)[1:]
            fade[:pad] = ramp
            fade[-pad:] = ramp[::-1]
        for k, i in enumerate(dpos):
            ch, ox, w = cols[i]
            j = n - 1 - k                                  # place index (0 = units)
            span = 0.6 + 0.4 * (j / max(n - 1, 1))         # higher places use more of the count time
            ee = self.ease_count(_clamp(p / span))
            target = int(ch)
            turns = 1 if j < n - 1 else 0
            pos = float(target) if ee >= 1 else ((target + 10 * turns) * ee) % 10
            y0 = int(round(st["y_first"] + pos * pitch))
            a = np.asarray(strip.crop((0, y0, cw, y0 + win_h))).copy()
            a[..., 3] = (a[..., 3].astype(np.float32) * fade[:, None]).astype(np.uint8)
            layer.alpha_composite(Image.fromarray(a, "RGBA"), (int(round(x + ox + w / 2 - cw / 2)), top))
        for ch, ox, w in cols:
            if ch not in DIGITS:
                dr.text((x + ox, self.y), ch, font=face.f, fill=col, anchor="ls", features=face.feat)

    def draw(self, layer, t):
        dr = ImageDraw.Draw(layer)
        for r in self.runs:
            x = self.x0 + r["_x"]
            if r["_kind"] == "sw":
                sz = r["_w"]
                yc = self.y - r.get("center_above_baseline", 0.36 * self.d.get("size", 32))
                box = [round(x), round(yc - sz / 2), round(x + sz), round(yc + sz / 2)]
                dr.rounded_rectangle(box, radius=r.get("radius", 3), fill=r["_color"],
                                     outline=r["_bcolor"], width=int(r["_border"]))
            elif r["_kind"] == "txt":
                dr.text((x, self.y), r["text"], font=r["_font"], fill=r["_color"], anchor="ls")
            else:
                self._draw_num(layer, dr, r, x, t)

    def run_bbox(self, k):
        r = self.runs[k]
        x = self.x0 + r["_x"]
        dr = ImageDraw.Draw(Image.new("L", (1, 1)))
        s = r.get("text", r.get("_final", ""))
        b = dr.textbbox((x, self.y), s, font=r["_font"], anchor="ls") if "_font" in r else \
            (x, self.y - r["_w"], x + r["_w"], self.y)
        return (x, b[1], x + r["_w"], b[3])

    def bbox(self):
        dr = ImageDraw.Draw(Image.new("L", (1, 1)))
        boxes = []
        for r in self.runs:
            x = self.x0 + r["_x"]
            if r["_kind"] == "sw":
                yc = self.y - r.get("center_above_baseline", 0.36 * self.d.get("size", 32))
                boxes.append((x, yc - r["_w"] / 2, x + r["_w"], yc + r["_w"] / 2))
            elif r["_kind"] == "txt":
                if r["text"].strip():
                    boxes.append(dr.textbbox((x, self.y), r["text"], font=r["_font"], anchor="ls"))
            else:
                b = dr.textbbox((x, self.y), r["_final"], font=r["_font"], anchor="ls")
                boxes.append((x, b[1], x + r["_w"], b[3]))
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


class DonutEl(Element):
    """Anti-aliased ring; `value` percent sweeps clockwise from 12 o'clock."""
    def __init__(self, d, ctx, defaults):
        super().__init__(d, ctx, defaults)
        self.cx, self.cy, self.r = d["cx"], d["cy"], d["r"]
        self.th = d["thickness"]
        self.value = float(d["value"])
        self.color = np.array(ctx.color(d.get("color", "accent")), np.float32)
        self.rest = np.array(ctx.color(d.get("rest_color", "track")), np.float32)
        self.track_dur = float(d.get("track_dur", 0.5))
        x0, y0 = int(math.floor(self.cx - self.r - 2)), int(math.floor(self.cy - self.r - 2))
        x1, y1 = int(math.ceil(self.cx + self.r + 2)), int(math.ceil(self.cy + self.r + 2))
        self.box = (x0, y0, x1, y1)
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        dx, dy = xx + 0.5 - self.cx, yy + 0.5 - self.cy
        dist = np.sqrt(dx * dx + dy * dy)
        rin = self.r - self.th
        self.ring = np.clip(self.r - dist + 0.5, 0, 1) * np.clip(dist - rin + 0.5, 0, 1)
        self.ang = np.mod(np.arctan2(dx, -dy), 2 * np.pi)
        self.dist = dist
        self.enter = "none"

    def end_time(self):
        return self.start + self.dur

    def fill_end(self):
        return self.start + self.dur

    def fill_progress(self, t, seg=None):
        return self.ease(progress(t, self.start, self.dur))

    def draw(self, layer, t):
        if t < self.start:
            return
        ta = self.ease(progress(t, self.start, self.track_dur))
        sweep = 2 * np.pi * self.value / 100.0 * self.fill_progress(t)
        g = np.clip((sweep - self.ang) * self.dist + 0.5, 0, 1)
        if sweep <= 0:
            g[:] = 0
        rgb = self.rest[None, None, :] * (1 - g[..., None]) + self.color[None, None, :] * g[..., None]
        a = self.ring * np.maximum(ta, g)
        arr = np.dstack([rgb, a[..., None] * 255]).astype(np.uint8)
        layer.alpha_composite(Image.fromarray(arr, "RGBA"), (self.box[0], self.box[1]))

    def bbox(self):
        return (self.cx - self.r, self.cy - self.r, self.cx + self.r, self.cy + self.r)


def _shape_image(w, h, radius, fill, border, bcolor, shadow, ss=3):
    """supersampled rounded rect + optional border ring + hard/soft offset shadow.
    Returns (RGBA image, ox, oy): (ox, oy) = position of the shape's top-left inside the image."""
    sdx = sdy = blur = 0
    if shadow:
        sdx, sdy = shadow.get("dx", 8), shadow.get("dy", 8)
        blur = shadow.get("blur", 0) if shadow.get("type", "hard") == "soft" else 0
    m = int(math.ceil(border + max(abs(sdx), abs(sdy)) + 3 * blur + 2))
    W, H = int(math.ceil(w)) + 2 * m, int(math.ceil(h)) + 2 * m

    def rect_mask(x0, y0, x1, y1, rad):
        mk = Image.new("L", (W * ss, H * ss), 0)
        ImageDraw.Draw(mk).rounded_rectangle([x0 * ss, y0 * ss, x1 * ss - 1, y1 * ss - 1],
                                             radius=max(rad, 0) * ss, fill=255)
        return np.asarray(mk.resize((W, H), Image.LANCZOS), np.float32) / 255

    def layer_of(mask, col):
        arr = np.zeros((H, W, 4), np.uint8)
        arr[..., :3] = col[:3]
        arr[..., 3] = (mask * col[3]).astype(np.uint8)
        return Image.fromarray(arr, "RGBA")

    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ox = oy = m
    outer = (ox - border, oy - border, ox + w + border, oy + h + border)
    inner = rect_mask(ox, oy, ox + w, oy + h, radius)
    if shadow:
        sm = rect_mask(outer[0] + sdx, outer[1] + sdy, outer[2] + sdx, outer[3] + sdy, radius + border)
        if blur:
            sm = np.asarray(Image.fromarray((sm * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur)),
                            np.float32) / 255
        out.alpha_composite(layer_of(sm, shadow["_rgba"]))
    if border:
        ring = np.clip(rect_mask(*outer, radius + border) - inner, 0, 1)
        if fill is not None and fill[3] == 255:
            ring = rect_mask(*outer, radius + border)  # opaque fill covers the inside anyway
        out.alpha_composite(layer_of(ring, bcolor))
    if fill is not None:
        out.alpha_composite(layer_of(inner, fill))
    return out, ox, oy


def _shadow_cfg(d, ctx):
    """element 'shadow': false | true (= theme shape.shadow) | {type hard|soft, dx, dy, blur, color, opacity}"""
    sh = d.get("shadow", True)
    if sh is True:
        sh = ctx.th("shape.shadow")
    if not sh or sh.get("type", "hard") == "none":
        return None
    sh = dict(sh)
    sh["_rgba"] = ctx.rgba(sh.get("color", "shadow"), sh.get("opacity", 1.0))
    return sh


class PanelEl(Element):
    """Card / block: x, y, w, h, fill, opacity, radius, border, border_color,
    shadow (false | true = theme | {type hard|soft, dx, dy, blur, color, opacity})."""
    def __init__(self, d, ctx, defaults):
        super().__init__(d, ctx, defaults)
        self.x, self.y, self.w, self.h = d["x"], d["y"], d["w"], d["h"]
        rad = d.get("radius", ctx.th("shape.corner_radius", 0))
        border = d.get("border", ctx.th("shape.stroke_width", 0))
        bcol = ctx.rgba(d.get("border_color", ctx.th("shape.stroke_color", "border"))) if border else None
        fill = ctx.rgba(d.get("fill", "surface"), d.get("opacity", 1.0)) if d.get("fill", "surface") else None
        self.img, self.ox, self.oy = _shape_image(self.w, self.h, rad, fill, border, bcol, _shadow_cfg(d, ctx))
        self.border = border

    def draw(self, layer, t):
        layer.alpha_composite(self.img, (int(round(self.x - self.ox)), int(round(self.y - self.oy))))

    def bbox(self):
        b = self.border
        return (self.x - b, self.y - b, self.x + self.w + b, self.y + self.h + b)


class RuleEl(Element):
    """Straight rule x0,y0 -> x1,y1 (width, color).  Draws on from the start point over draw_dur."""
    def __init__(self, d, ctx, defaults):
        super().__init__(d, ctx, defaults)
        self.p0 = (d["x0"], d["y0"])
        self.p1 = (d["x1"], d.get("y1", d["y0"]))
        self.width = d.get("width", 2)
        self.color = ctx.rgba(d.get("color", "text"))
        self.draw_on = d.get("draw_on", True)
        if self.draw_on:
            self.enter = "none"
            self.dur = max(self.dur, float(d.get("draw_dur", self.enter_dur)))

    def draw(self, layer, t):
        p = self.ease(progress(t, self.start, self.dur)) if self.draw_on else 1.0
        if p <= 0:
            return
        x0, y0 = self.p0
        x1 = x0 + (self.p1[0] - x0) * p
        y1 = y0 + (self.p1[1] - y0) * p
        dr = ImageDraw.Draw(layer)
        hw = self.width / 2
        if y0 == y1:
            if abs(x1 - x0) < 1:
                return
            dr.rectangle([min(x0, x1), y0 - hw, max(x0, x1) - 1, max(y0 - hw, y0 + hw - 1)], fill=self.color)
        elif x0 == x1:
            if abs(y1 - y0) < 1:
                return
            dr.rectangle([x0 - hw, min(y0, y1), max(x0 - hw, x0 + hw - 1), max(y0, y1) - 1], fill=self.color)
        else:
            dr.line([(x0, y0), (x1, y1)], fill=self.color, width=int(self.width))

    def bbox(self):
        hw = self.width / 2
        return (min(self.p0[0], self.p1[0]) - hw, min(self.p0[1], self.p1[1]) - hw,
                max(self.p0[0], self.p1[0]) + hw, max(self.p0[1], self.p1[1]) + hw)


class GridEl(Element):
    """Value-axis gridlines for horizontal bars: x, y, w, h, ticks [0,25,..], max, labels under the axis."""
    def __init__(self, d, ctx, defaults):
        super().__init__(d, ctx, defaults)
        self.x, self.y, self.w, self.h = d["x"], d["y"], d["w"], d["h"]
        self.ticks = d.get("ticks", [0, 25, 50, 75, 100])
        self.max = float(d.get("max", self.ticks[-1]))
        self.color = ctx.rgba(d.get("color", "grid"))
        self.lw = d.get("width", 1)
        self.labels = d.get("labels", True)
        if self.labels:
            self.font = ctx.font(d.get("font", "body"), d.get("size", 22))
            self.lcolor = ctx.rgba(d.get("label_color", "muted"))
            self.suffix = d.get("suffix", "")
            self.label_dy = d.get("label_dy", 32)

    def draw(self, layer, t):
        dr = ImageDraw.Draw(layer)
        for v in self.ticks:
            x = round(self.x + self.w * v / self.max)
            dr.rectangle([x - self.lw // 2, self.y, x - self.lw // 2 + self.lw - 1, self.y + self.h], fill=self.color)
            if self.labels:
                anc = "ls" if v == self.ticks[0] else "rs" if v == self.ticks[-1] else "ms"
                dr.text((x, self.y + self.h + self.label_dy), f"{v:g}{self.suffix}", font=self.font,
                        fill=self.lcolor, anchor=anc)

    def bbox(self):
        b = (self.x - self.lw, self.y, self.x + self.w + self.lw, self.y + self.h)
        if self.labels:
            b = (b[0], b[1], b[2], self.y + self.h + self.label_dy + 6)
        return b


class BarEl(Element):
    """Stacked horizontal bar; segments grow left->right in one eased sweep.
    fill_progress(t, seg) drives synced number runs, so labels land exactly when the fill does.
    Extra keys: ease (e.g. steps(6)), border, border_color, shadow, gap_color, fill_delay, fill_dur."""
    def __init__(self, d, ctx, defaults):
        super().__init__(d, ctx, defaults)
        self.x, self.y, self.w, self.h = d["x"], d["y"], d["w"], d["h"]
        self.segs = [(float(s["value"]), np.array(ctx.color(s["color"]), np.float32)) for s in d["segments"]]
        self.total = float(d.get("total", sum(v for v, _ in self.segs)))
        self.track = np.array(ctx.color(d.get("track", "track")), np.float32)
        self.track_alpha = float(d.get("track_opacity", 1.0))
        self.gap = d.get("gap", 0)
        self.gap_color = np.array(ctx.color(d["gap_color"]), np.float32) if d.get("gap_color") else None
        rad = d.get("radius", ctx.th("shape.bar_radius", 4))
        ss = 4
        m = Image.new("L", (int(self.w * ss), int(self.h * ss)), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, self.w * ss - 1, self.h * ss - 1], radius=rad * ss, fill=255)
        self.mask = np.asarray(m.resize((int(self.w), int(self.h)), Image.LANCZOS), np.float32) / 255
        self.track_dur = float(d.get("track_dur", 0.4))
        self.fill_start = self.start + float(d.get("fill_delay", self.track_dur * 0.5))
        self.fill_dur = float(d.get("fill_dur", self.dur - (self.fill_start - self.start)))
        self.enter = "none"
        self.border = d.get("border", 0)
        sh = _shadow_cfg(d, ctx) if d.get("shadow", False) else None
        self.frame = None
        if self.border or sh:
            self.frame, self.fox, self.foy = _shape_image(self.w, self.h, rad, None, self.border,
                                                          ctx.rgba(d.get("border_color", "border")), sh)
        cum, self.bounds = 0.0, []
        for v, _ in self.segs:
            self.bounds.append((cum / self.total, (cum + v) / self.total))
            cum += v

    def end_time(self):
        return max(self.start + self.dur, self.fill_end())

    def fill_end(self):
        return self.fill_start + self.fill_dur

    def fill_progress(self, t, seg=None):
        p = self.ease(progress(t, self.fill_start, self.fill_dur))
        if seg is None or p >= 1:
            return p
        a, b = self.bounds[seg]
        return _clamp((p - a) / (b - a)) if b > a else 1.0

    def draw(self, layer, t):
        if t < self.start:
            return
        ta = self.ease(progress(t, self.start, self.track_dur)) if self.track_dur > 0 else 1.0
        p = self.fill_progress(t)
        W, H = int(self.w), int(self.h)
        cols = np.arange(W, dtype=np.float32)
        rgb = np.tile(self.track, (W, 1))
        alpha = np.full(W, ta * self.track_alpha, np.float32)
        cum = 0.0
        filled = p * self.w
        for v, c in self.segs:
            sx0 = cum / self.total * self.w
            sx1 = min((cum + v) / self.total * self.w, filled)
            cum += v
            if sx1 <= sx0:
                break
            last = cum >= self.total - 1e-9
            gx1 = sx1 - (self.gap if not last and sx1 >= (cum / self.total * self.w) - 1e-6 else 0)
            cov = np.clip(np.minimum(gx1, cols + 1) - np.maximum(sx0, cols), 0, 1)
            rgb = rgb * (1 - cov[:, None]) + c[None, :] * cov[:, None]
            alpha = np.maximum(alpha, cov)
            if self.gap_color is not None and gx1 < sx1:
                gc = np.clip(np.minimum(sx1, cols + 1) - np.maximum(gx1, cols), 0, 1)
                rgb = rgb * (1 - gc[:, None]) + self.gap_color[None, :] * gc[:, None]
                alpha = np.maximum(alpha, gc)
        if self.frame is not None:
            fa = self.frame.copy()
            if ta < 1:
                fa.putalpha(fa.getchannel("A").point(lambda v: int(v * ta)))
            layer.alpha_composite(fa, (int(round(self.x - self.fox)), int(round(self.y - self.foy))))
        arr = np.empty((H, W, 4), np.uint8)
        arr[..., :3] = rgb[None, :, :].astype(np.uint8)
        arr[..., 3] = (self.mask * alpha[None, :] * 255).astype(np.uint8)
        layer.alpha_composite(Image.fromarray(arr, "RGBA"), (int(self.x), int(self.y)))

    def bbox(self):
        b = self.border
        return (self.x - b, self.y - b, self.x + self.w + b, self.y + self.h + b)


def _target_bbox(els, target):
    """'id' -> element bbox; 'id#k' -> bbox of run k of a line element (e.g. just the number)."""
    tid, _, k = target.partition("#")
    if tid not in els:
        raise KeyError(f"annotation target {tid!r} not found")
    e = els[tid]
    if k:
        return e.run_bbox(int(k))
    return e.bbox()


def _smooth_noise(n, rng, k=6):
    v = rng.standard_normal(n + 4 * k)
    ker = np.hanning(2 * k + 1)
    ker /= ker.sum()
    v = np.convolve(v, ker, mode="same")[2 * k:2 * k + n]
    return v / (np.abs(v).max() + 1e-9)


class HighlightEl(Element):
    """Highlighter swipe behind a target element (or box [x0,y0,x1,y1]); grows left->right with a ragged
    edge.  blend multiply by default so the ink stays dark.  Keys: target, box, pad [px,py], color, opacity.
    Put it BEFORE its target in the element list."""
    def __init__(self, d, ctx, defaults):
        cfg = dict(ctx.th("annotations.highlighter", {}) or {})
        cfg.update(d)
        d = cfg
        super().__init__(d, ctx, defaults)
        self.enter = "none"
        self.target, self.box = d.get("target"), d.get("box")
        self.pad = d.get("pad", [10, 6])
        self.color = ctx.rgba(d.get("color", "accent"), d.get("opacity", 0.85))
        self.blend = d.get("blend", "multiply")
        self.rough = d.get("rough_edge", True)
        self.seed = d.get("seed", 3)
        self.dur = self.dur or 0.5
        self.allow_overlap = True

    def link(self, els):
        if self.target:
            self.box = list(_target_bbox(els, self.target))
        x0, y0, x1, y1 = self.box
        px, py = self.pad
        self.bx = (x0 - px, y0 - py, x1 + px, y1 + py)
        W, H = int(math.ceil(self.bx[2] - self.bx[0])), int(math.ceil(self.bx[3] - self.bx[1]))
        rng = np.random.default_rng(self.seed)
        yy = np.mgrid[0:H, 0:W][0].astype(np.float32)
        amp = max(2.0, H * 0.07) if self.rough else 0.0
        top = amp * (0.5 + 0.5 * _smooth_noise(W, rng, 40))
        bot = H - amp * (0.5 + 0.5 * _smooth_noise(W, rng, 40))
        m = np.clip(yy - top[None, :] + 0.5, 0, 1) * np.clip(bot[None, :] - yy + 0.5, 0, 1)
        if self.rough:  # marker density streaks along the stroke
            m = m * (1 - 0.10 * (0.5 + 0.5 * _smooth_noise(H, rng, 3)))[:, None]
        self.mask = m
        self.edge = amp * 1.5 * _smooth_noise(H, rng, 4) if self.rough else np.zeros(H)
        self.W, self.H = W, H

    def draw(self, layer, t):
        p = self.ease(progress(t, self.start, self.dur))
        if p <= 0:
            return
        xs = np.arange(self.W, dtype=np.float32)[None, :]
        lim = p * (self.W + 12) + (self.edge[:, None] if p < 1 else 0)
        m = self.mask * np.clip(lim - xs + 0.5, 0, 1)
        arr = np.empty((self.H, self.W, 4), np.uint8)
        arr[..., :3] = self.color[:3]
        arr[..., 3] = (m * self.color[3]).astype(np.uint8)
        layer.alpha_composite(Image.fromarray(arr, "RGBA"), (int(round(self.bx[0])), int(round(self.bx[1]))))

    def bbox(self):
        return self.bx


class MarkerEl(Element):
    """Hand-drawn marker loop around a target element / box; draws on over dur (wobbly, ~1.1 turns)."""
    def __init__(self, d, ctx, defaults):
        cfg = dict(ctx.th("annotations.marker_circle", {}) or {})
        cfg.update(d)
        d = cfg
        super().__init__(d, ctx, defaults)
        self.enter = "none"
        self.target, self.box = d.get("target"), d.get("box")
        self.pad = d.get("pad", [26, 18])
        self.color = ctx.rgba(d.get("color", "accent"))
        self.width = d.get("width", 6)
        self.seed = d.get("seed", 5)
        self.dur = self.dur or 0.5
        self.allow_overlap = True

    def link(self, els):
        if self.target:
            self.box = list(_target_bbox(els, self.target))
        x0, y0, x1, y1 = self.box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        # 1.25x the half-size + padding so the loop clears the box corners, not just its edges
        rx, ry = (x1 - x0) / 2 * 1.25 + self.pad[0], (y1 - y0) / 2 * 1.25 + self.pad[1]
        rng = np.random.default_rng(self.seed)
        n = 260
        a = -math.pi * 0.62 + np.linspace(0, 2 * math.pi * 1.1, n)
        wob = 1 + 0.035 * _smooth_noise(n, rng, 14)
        grow = np.linspace(1.0, 1.07, n)  # the loop overshoots instead of closing onto itself
        self.pts = np.stack([cx + rx * wob * grow * np.cos(a), cy + ry * wob * grow * np.sin(a)], 1)
        mg = self.width * 2
        self.bx = (self.pts[:, 0].min() - mg, self.pts[:, 1].min() - mg,
                   self.pts[:, 0].max() + mg, self.pts[:, 1].max() + mg)

    def draw(self, layer, t):
        p = self.ease(progress(t, self.start, self.dur))
        n = len(self.pts)
        k = int(round(p * (n - 1))) + 1
        if p <= 0 or k < 2:
            return
        ss = 3
        ox, oy = int(self.bx[0]), int(self.bx[1])
        W, H = int(self.bx[2] - ox) + 1, int(self.bx[3] - oy) + 1
        im = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
        dr = ImageDraw.Draw(im)
        pts = [((x - ox) * ss, (y - oy) * ss) for x, y in self.pts[:k]]
        for i in range(1, len(pts)):
            f = min(1.0, i / (n * 0.06), (n - i) / (n * 0.10))  # tapered ends
            wv = max(1.0, self.width * ss * (0.5 + 0.5 * f))
            dr.line([pts[i - 1], pts[i]], fill=self.color, width=int(wv))
            dr.ellipse([pts[i][0] - wv / 2, pts[i][1] - wv / 2, pts[i][0] + wv / 2, pts[i][1] + wv / 2], fill=self.color)
        layer.alpha_composite(im.resize((W, H), Image.LANCZOS), (ox, oy))

    def bbox(self):
        return self.bx


TYPES = {"text": TextEl, "line": LineEl, "donut": DonutEl, "bar": BarEl, "panel": PanelEl, "rule": RuleEl,
         "gridlines": GridEl, "highlight": HighlightEl, "marker": MarkerEl}

# ================================================================ background / textures
def draw_taegeuk(W, H, m, ctx):
    """Korean flag motif, dimmed. m: {cx, cy, height, field_opacity, emblem_opacity}"""
    ss = 2
    fh = m["height"] * ss
    fw = fh * 1.5
    cx, cy = m["cx"] * ss, m["cy"] * ss
    field = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    emb = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    fd = ImageDraw.Draw(field)
    fd.rectangle([cx - fw / 2, cy - fh / 2, cx + fw / 2, cy + fh / 2], fill=(255, 255, 255, 255))
    # trigrams (black cut-outs on the field)
    theta = math.atan2(fh, fw)
    L, bt, gp = fh / 4, fh / 24, fh / 48
    dist = fh / 4 + fh / 8 + fh / 12
    tri = {(-1, -1): "SSS", (1, -1): "BSB", (-1, 1): "SBS", (1, 1): "BBB"}
    for (sx, sy), pat in tri.items():
        ux, uy = sx * math.cos(theta), sy * math.sin(theta)       # outward direction
        px, py = -uy, ux                                           # along bar
        tx, ty = cx + ux * dist, cy + uy * dist
        for i, kind in enumerate(pat):
            off = (i - 1) * (bt + gp)
            bx, by = tx + ux * off, ty + uy * off
            pieces = [(-L / 2, L / 2)] if kind == "S" else [(-L / 2, -gp / 2), (gp / 2, L / 2)]
            for a, b in pieces:
                pts = []
                for s, n in ((a, -bt / 2), (b, -bt / 2), (b, bt / 2), (a, bt / 2)):
                    pts.append((bx + px * s + ux * n, by + py * s + uy * n))
                fd.polygon(pts, fill=(0, 0, 0, 255))
    # emblem
    R = fh / 4
    S = int(R * 2 + 4)
    e = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ed = ImageDraw.Draw(e)
    c = S / 2
    red, blue = (205, 46, 58, 255), (0, 71, 160, 255)
    ed.pieslice([c - R, c - R, c + R, c + R], 180, 360, fill=red)
    ed.pieslice([c - R, c - R, c + R, c + R], 0, 180, fill=blue)
    ed.ellipse([c - R, c - R / 2, c, c + R / 2], fill=red)
    ed.ellipse([c, c - R / 2, c + R, c + R / 2], fill=blue)
    e = e.rotate(-math.degrees(theta), resample=Image.BICUBIC)
    emb.alpha_composite(e, (int(cx - S / 2), int(cy - S / 2)))
    field = field.resize((W, H), Image.LANCZOS)
    emb = emb.resize((W, H), Image.LANCZOS)
    def scale_alpha(im, k):
        a = np.asarray(im).copy()
        a[..., 3] = (a[..., 3] * k).astype(np.uint8)
        return Image.fromarray(a, "RGBA")
    return scale_alpha(field, m.get("field_opacity", 0.06)), scale_alpha(emb, m.get("emblem_opacity", 0.22))


def _upsampled_noise(rng, W, H, cell):
    w, h = max(2, W // cell + 2), max(2, H // cell + 2)
    small = Image.fromarray((rng.random((h, w)) * 255).astype(np.uint8), "L")
    return np.asarray(small.resize((W, H), Image.BICUBIC), np.float32) / 255.0


def make_texture(W, H, cfg):
    """static texture field in [-1, 1] (0 = no change), generated once per render (seeded).
    type: paper (fibres + low-frequency blotches) | newsprint (denser fibres) | noise (fine grain)."""
    typ = cfg.get("type", "none")
    rng = np.random.default_rng(cfg.get("seed", 7))
    sc = cfg.get("scale", 1.0)
    if typ == "noise":
        return np.clip(rng.standard_normal((H, W)).astype(np.float32) * 0.5, -1, 1)
    if typ in ("paper", "newsprint"):
        blot = _upsampled_noise(rng, W, H, max(8, int(260 * sc))) - 0.5
        mid = _upsampled_noise(rng, W, H, max(4, int(40 * sc))) - 0.5
        fine = rng.standard_normal((H, W)).astype(np.float32) * 0.18
        fib = Image.new("L", (W, H), 128)
        dr = ImageDraw.Draw(fib)
        nf = int(W * H / 900 * (1.6 if typ == "newsprint" else 1.0))
        xs, ys = rng.random(nf) * W, rng.random(nf) * H
        ang = rng.random(nf) * math.pi
        ln = rng.random(nf) * 22 * sc + 4
        sh = rng.integers(-1, 2, nf) * 34
        for x, y, a, l, s in zip(xs, ys, ang, ln, sh):
            dr.line([(x, y), (x + math.cos(a) * l, y + math.sin(a) * l)], fill=int(128 + s), width=1)
        fib = np.asarray(fib.filter(ImageFilter.GaussianBlur(0.7)), np.float32) / 128.0 - 1.0
        tex = blot * 1.3 + mid * 0.5 + fine + fib * 0.8
        return np.clip(tex / (np.abs(tex).max() + 1e-6) * 1.6, -1, 1)
    return None


def draw_pattern(bg, W, H, pat, ctx):
    """background.pattern: {type: none|dots|grid|graph_paper|lines|scanlines, spacing, color, opacity, width, dot_r}"""
    typ = pat.get("type", "none")
    if typ == "none":
        return
    sp = int(pat.get("spacing", 32))
    col = ctx.rgba(pat.get("color", "grid"), pat.get("opacity", 1.0))
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(lay)
    ox, oy = (int(v) for v in pat.get("offset", [0, 0]))
    lw = int(pat.get("width", 1))
    if typ == "dots":
        r = pat.get("dot_r", 2)
        for y in range(oy + sp // 2, H, sp):
            for x in range(ox + sp // 2, W, sp):
                dr.ellipse([x - r, y - r, x + r, y + r], fill=col)
    elif typ in ("grid", "graph_paper"):
        for x in range(ox, W, sp):
            dr.rectangle([x, 0, x + lw - 1, H], fill=col)
        for y in range(oy, H, sp):
            dr.rectangle([0, y, W, y + lw - 1], fill=col)
        if typ == "graph_paper":
            major = ctx.rgba(pat.get("color", "grid"), min(1.0, pat.get("opacity", 1.0) * 2))
            for x in range(ox, W, sp * 5):
                dr.rectangle([x, 0, x + lw - 1, H], fill=major)
            for y in range(oy, H, sp * 5):
                dr.rectangle([0, y, W, y + lw - 1], fill=major)
    elif typ == "lines":
        for y in range(oy, H, sp):
            dr.rectangle([0, y, W, y + lw - 1], fill=col)
    elif typ == "scanlines":
        for y in range(0, H, sp):
            dr.rectangle([0, y, W, y + max(1, sp // 2) - 1], fill=col)
    bg.alpha_composite(lay)


def build_background(spec, ctx):
    W, H = spec["canvas"]["width"], spec["canvas"]["height"]
    bgc = spec.get("background", {})
    typ = bgc.get("type", "solid")
    if typ in ("linear", "radial") and bgc.get("gradient"):
        g = bgc["gradient"]
        stops = [(np.array(ctx.color(c), np.float32), float(pos)) for c, pos in g["stops"]]
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        if typ == "linear":
            ang = math.radians(g.get("angle", 90))  # 90 = top -> bottom
            u = (xx / W - 0.5) * math.cos(ang) + (yy / H - 0.5) * math.sin(ang) + 0.5
        else:
            cx, cy = g.get("center", [0.5, 0.5])
            u = np.sqrt(((xx / W - cx) * W / H) ** 2 + (yy / H - cy) ** 2) / g.get("radius", 0.8)
        u = np.clip(u, 0, 1)
        img = np.zeros((H, W, 3), np.float32) + stops[0][0]
        for (c0, p0), (c1, p1) in zip(stops, stops[1:]):
            k = np.clip((u - p0) / max(p1 - p0, 1e-6), 0, 1)[..., None]
            img = np.where((u >= p0)[..., None], c0 * (1 - k) + c1 * k, img)
    else:
        default = "bg" if "bg" in ctx.colors else "#111317"
        img = np.tile(np.array(ctx.color(bgc.get("color", default)), np.float32), (H, W, 1))
    tcfg = deep_merge(ctx.th("texture", {}) or {}, spec.get("texture", {}))
    tex = make_texture(W, H, tcfg) if tcfg.get("type", "none") != "none" else None
    if tex is not None:  # multiply-style modulation around the base tone
        img = img * (1 + tcfg.get("strength", 0.04) * tex[..., None])
    if typ in ("linear", "radial") or tex is not None:  # +-0.5 LSB dither against banding
        img = img + np.random.default_rng(1).random((H, W, 1)).astype(np.float32) - 0.5
    bg = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    draw_pattern(bg, W, H, bgc.get("pattern", {}), ctx)
    for m in bgc.get("motifs", []):
        if m["type"] == "taegeuk":
            for lay in draw_taegeuk(W, H, m, ctx):
                bg.alpha_composite(lay)
    v = bgc.get("vignette", 0.0)
    if v:
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / math.sqrt(2)
        k = 1 - v * np.clip(d, 0, 1) ** 2
        arr = np.asarray(bg).astype(np.float32)
        arr[..., :3] *= k[..., None]
        bg = Image.fromarray(arr.astype(np.uint8), "RGBA")
    return bg

# ================================================================ scene
_VAR = re.compile(r"\$\{([A-Za-z0-9_.]+)\}")

def substitute(obj, data):
    """"$key" -> data value (any type); "...${key}..." -> string interpolation."""
    if isinstance(obj, dict):
        return {k: substitute(v, data) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute(v, data) for v in obj]
    if isinstance(obj, str):
        if obj.startswith("$") and not obj.startswith("${") and obj[1:] in data:
            return data[obj[1:]]
        return _VAR.sub(lambda m: str(data[m.group(1)]), obj)
    return obj


def load_spec(spec_path, data_path=None):
    with open(spec_path) as f:
        spec = json.load(f)
    base = os.path.dirname(os.path.abspath(spec_path))
    data = dict(spec.get("data", {}))          # inline defaults
    for df in (spec.get("data_file"), data_path):  # then data_file, then --data (highest priority)
        if not df:
            continue
        if not os.path.isabs(df) and not os.path.exists(df):
            df = os.path.join(base, df)
        with open(df) as f:
            data.update(json.load(f))
    spec = substitute({k: v for k, v in spec.items() if k not in ("data", "data_file")}, data)
    return spec


def theme_defaults(theme):
    """theme.motion / theme.numbers -> element defaults (spec "defaults" still wins)."""
    m = theme.get("motion", {})
    keys = ("enter", "enter_dur", "enter_offset", "enter_scale", "ease", "ease_enter", "ease_count",
            "ease_emphasis", "back_overshoot", "time_step")
    out = {k: m[k] for k in keys if k in m}
    if "count_mode" in theme.get("numbers", {}):
        out["count_mode"] = theme["numbers"]["count_mode"]
    return out


class Scene:
    def __init__(self, spec_path, data_path=None, theme_ref=None):
        self.spec = load_spec(spec_path, data_path)
        sdir = os.path.dirname(os.path.abspath(spec_path))
        self.theme = load_theme(theme_ref or self.spec.get("theme"), sdir)
        self.spec["background"] = deep_merge(self.theme.get("background", {}), self.spec.get("background", {}))
        self.ctx = Ctx(self.spec, sdir, self.theme)
        c = self.spec["canvas"]
        self.W, self.H, self.fps = c["width"], c["height"], c.get("fps", 30)
        defaults = deep_merge(theme_defaults(self.theme), self.spec.get("defaults", {}))
        self.defaults = defaults
        self.els = [TYPES[d["type"]](d, self.ctx, defaults) for d in self.spec["elements"]]
        self.by_id = {}
        for e in self.els:
            if e.id in self.by_id:
                raise ValueError(f"duplicate element id {e.id!r}")
            self.by_id[e.id] = e
        self.ctx.els = self.by_id
        for e in self.els:
            if hasattr(e, "link"):
                e.link(self.by_id)
        self.bg = build_background(self.spec, self.ctx)
        self.anim_end = max(e.end_time() for e in self.els)
        self.duration = c.get("duration") or (self.anim_end + c.get("hold", 2.0))
        self.n_frames = int(round(self.duration * self.fps))
        post = deep_merge(self.theme.get("post", {}), self.spec.get("post", {}))
        self.post = post
        g, cam = post.get("grain", {}), post.get("camera", {})
        self.grain_amt = float(g.get("amount", 0))
        self.grain_anim = self.grain_amt > 0 and g.get("animated", True)
        self.grain_fps = float(g.get("fps", 12))
        self.cam_push = cam.get("push", [1.0, 1.0])
        self.cam_ease = get_ease(cam.get("ease", "in_out_sine"))
        self.cam_step = float(cam.get("time_step", defaults.get("time_step", 0)) or 0)
        animated_post = self.grain_anim or self.cam_push[0] != self.cam_push[1]
        self.n_anim = self.n_frames if animated_post else \
            min(self.n_frames, int(math.ceil(self.anim_end * self.fps)) + 1)
        self._grain = None
        if self.grain_amt:
            rng = np.random.default_rng(g.get("seed", 11))
            size = float(g.get("size", 1.0))
            gw, gh = max(1, int(self.W / size)), max(1, int(self.H / size))
            frames = []
            for _ in range(8 if self.grain_anim else 1):
                n = np.clip(rng.standard_normal((gh, gw)) * 42, -127, 127)
                if (gw, gh) != (self.W, self.H):
                    n = np.asarray(Image.fromarray((n + 128).astype(np.uint8)).resize((self.W, self.H), Image.BILINEAR),
                                   np.float32) - 128
                frames.append(n.astype(np.int8))
            self._grain = frames
        sl = post.get("scanlines", {})
        self._scan = None
        if sl.get("spacing"):
            rows = np.ones(self.H, np.float32)
            rows[::int(sl["spacing"])] = 1 - sl.get("opacity", 0.35)
            self._scan = rows
        self._done_cache = {}

    def apply_post(self, frame, t):
        """camera push (scale about the centre, step-timed if the theme uses time_step), film grain, scanlines."""
        s0, s1 = self.cam_push
        if s0 != 1.0 or s1 != 1.0:
            tq = quantize(t, self.cam_step)
            s = s0 + (s1 - s0) * self.cam_ease(tq / max(self.duration, 1e-6))
            if abs(s - 1.0) > 1e-5:
                cx, cy = self.W / 2, self.H / 2
                frame = frame.transform(frame.size, Image.AFFINE, (1 / s, 0, cx - cx / s, 0, 1 / s, cy - cy / s),
                                        resample=Image.BICUBIC)
        if self._grain is None and self._scan is None:
            return frame
        arr = np.asarray(frame, np.float32)
        if self._grain is not None:
            i = int(math.floor(t * self.grain_fps + 1e-6)) % len(self._grain) if self.grain_anim else 0
            arr = arr + (self._grain[i].astype(np.float32) * (self.grain_amt * 255 * 0.5 / 42))[..., None]
        if self._scan is not None:
            arr = arr * self._scan[:, None, None]
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    @staticmethod
    def _composite(frame, layer, blend):
        if blend == "multiply":
            fa = np.asarray(frame, np.float32)
            la = np.asarray(layer, np.float32)
            a = la[..., 3:4] / 255.0
            out = fa.copy()
            out[..., :3] = fa[..., :3] * (1 - a + a * la[..., :3] / 255.0)
            return Image.fromarray(out.astype(np.uint8), "RGBA")
        frame.alpha_composite(layer)
        return frame

    def render(self, t, post=True):
        frame = self.bg.copy()
        for i, e in enumerate(self.els):
            te = quantize(t, e.time_step)
            if te < e.start:
                continue
            done = te >= e.end_time()
            if done and i in self._done_cache:
                frame = self._composite(frame, self._done_cache[i], e.blend)
                continue
            st = e.enter_state(te)
            if st["op"] <= 0:
                continue
            layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
            e.draw(layer, te)
            dx, dy, s = st["dx"], st["dy"], st["scale"]
            if dx or dy or s != 1.0:
                b = e.bbox()
                cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                layer = layer.transform(layer.size, Image.AFFINE,
                                        (1 / s, 0, cx - (cx + dx) / s, 0, 1 / s, cy - (cy + dy) / s),
                                        resample=Image.BICUBIC if s != 1.0 else Image.BILINEAR)
            if st["wipe"] < 1:
                b = e.bbox()
                xl = int(round(b[0] + (b[2] - b[0]) * st["wipe"]))
                a = np.asarray(layer.getchannel("A")).copy()
                a[:, max(xl, 0):] = 0
                layer.putalpha(Image.fromarray(a))
            if st["op"] < 1:
                op = st["op"]
                layer.putalpha(layer.getchannel("A").point(lambda v: int(v * op + 0.5)))
            if done:
                self._done_cache[i] = layer
            frame = self._composite(frame, layer, e.blend)
        frame = frame.convert("RGB")
        return self.apply_post(frame, t) if post else frame

    def check(self):
        issues, boxes = [], []
        for e in self.els:
            if not e.check_layout:
                continue
            b = e.bbox()
            boxes.append((e.id, b, e.allow_overlap))
            if b[0] < 0 or b[1] < 0 or b[2] > self.W or b[3] > self.H:
                issues.append(f"[clip] {e.id} bbox {tuple(round(v) for v in b)} outside {self.W}x{self.H}")
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                (ni, a, oki), (nj, b, okj) = boxes[i], boxes[j]
                if oki or okj:
                    continue
                ix = min(a[2], b[2]) - max(a[0], b[0])
                iy = min(a[3], b[3]) - max(a[1], b[1])
                if ix > 0 and iy > 0:
                    contains = (a[0] <= b[0] and a[1] <= b[1] and a[2] >= b[2] and a[3] >= b[3]) or \
                               (b[0] <= a[0] and b[1] <= a[1] and b[2] >= a[2] and b[3] >= a[3])
                    if not contains:
                        issues.append(f"[overlap] {ni} x {nj} ({ix:.0f}x{iy:.0f}px)")
        smax = max(self.cam_push)
        if smax > 1.0:  # camera push scales about the centre: edge content can leave the frame
            cx, cy = self.W / 2, self.H / 2
            for n, b, _ in boxes:
                X0, Y0 = cx + (b[0] - cx) * smax, cy + (b[1] - cy) * smax
                X1, Y1 = cx + (b[2] - cx) * smax, cy + (b[3] - cy) * smax
                if X0 < 0 or Y0 < 0 or X1 > self.W or Y1 > self.H:
                    issues.append(f"[clip@push x{smax}] {n} leaves the frame at full camera push")
        return issues, boxes

# ================================================================ multiprocessing
_SCENE = None
def _init(path, data, theme):
    global _SCENE
    _SCENE = Scene(path, data, theme)

def _frame(i):
    return _SCENE.render(i / _SCENE.fps).tobytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("-o", "--output")
    ap.add_argument("--data", help="data JSON overriding the spec's data_file/data")
    ap.add_argument("--theme", help="theme name (themes/<name>.json) or path; overrides the spec's theme")
    ap.add_argument("--final-png")
    ap.add_argument("--png-at", action="append", default=[], help="SECONDS:path.png")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--crf", type=int, default=16)
    ap.add_argument("--preset", default="slow")
    args = ap.parse_args()

    t0 = time.time()
    sc = Scene(args.spec, args.data, args.theme)
    issues, _ = sc.check()
    print(f"spec: {args.spec}  theme: {sc.theme.get('name', '-')}  {sc.W}x{sc.H}@{sc.fps}  "
          f"anim {sc.anim_end:.2f}s  total {sc.duration:.2f}s ({sc.n_frames} frames, {sc.n_anim} rendered)")
    for s in issues:
        print("WARNING", s)
    if not issues:
        print("layout check: OK (no clipping / overlaps)")
    for pa in args.png_at:
        s, p = pa.split(":", 1)
        sc.render(float(s)).save(p)
        print("wrote", p)
    if args.final_png:
        sc.render((sc.n_frames - 1) / sc.fps).save(args.final_png)
        print("wrote", args.final_png)
    if args.no_video or not args.output:
        return
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{sc.W}x{sc.H}", "-r", str(sc.fps), "-i", "-",
           "-c:v", "libx264", "-preset", args.preset, "-crf", str(args.crf), "-pix_fmt", "yuv420p",
           "-profile:v", "high", "-movflags", "+faststart", args.output]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    last = None
    with Pool(args.workers, initializer=_init, initargs=(args.spec, args.data, args.theme)) as pool:
        for b in pool.imap(_frame, range(sc.n_anim), chunksize=4):
            ff.stdin.write(b)
            last = b
    for _ in range(sc.n_frames - sc.n_anim):  # static hold
        ff.stdin.write(last)
    ff.stdin.close()
    ff.wait()
    print(f"wrote {args.output} in {time.time() - t0:.1f}s")
    sys.exit(ff.returncode)


if __name__ == "__main__":
    main()
