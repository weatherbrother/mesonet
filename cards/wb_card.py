#!/usr/bin/env python3
"""
Weather Brother - Kentucky Mesonet station card renderer.
LAYOUT VERSION 1.2  -  FROZEN

Changelog
    1.2  Footer and attribution are module constants (ATTRIBUTION, FOOTER_URL)
         instead of string literals inside render(), so callers can read what
         the card actually stamps rather than keeping their own copy.
         Footer moved to weatherbrother.com, retired getweatherbrother.app.
         Attribution now credits the camera image, which the card displays.
    1.1  Provenance line is clamped to the panel's inner margins and shortens
         itself when a stale pill is present. In 1.0 any stale observation
         carrying a camera time overran both margins by ~70px, with the left
         overflow printing on top of the accent bar.

This file defines the card. Do not edit it to change how a card looks on a
given day. Do not regenerate it. Callers supply data only.

Usage:
    python wb_card.py payload.json out.png

Payload keys:
    county          "Ballard County"
    site            "Bandana"
    station_id      "BAND"
    air_f           98.5
    dewpoint_f      72.6
    humidity_pct    44.3
    heat_index_f    111.0     omit or null to suppress the field
    obs_time        "1:55 PM CDT"
    obs_date        "Sat Sep 5"
    obs_age_min     4
    camera_path     "/path/to/frame.jpg"   omit or null if unavailable
    camera_time     "1:50 PM CDT"          omit or null if not published
"""
import json
import os
import sys
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- constants
W, H = 1080, 1920
NAVY, ICE, WHITE = (0, 31, 142), (230, 241, 244), (255, 255, 255)
GREEN, AMBER, RED = (96, 183, 21), (255, 194, 0), (224, 0, 0)
INK, SLATE, LAVENDER = (23, 37, 51), (94, 129, 144), (184, 198, 255)
RULE = (219, 230, 234)

HDR_H = 190                      # navy brand strip
CAM_Y0, CAM_Y1 = 190, 1010       # camera zone
PANEL = (70, 1030, 1010, 1640)   # white data panel
TAGLINE_Y = 1666
FTR_Y = 1730                     # navy footer band
MARGIN, ACCENT_W, RADIUS = 70, 15, 28
STALE_MIN = 20

# Read by callers so nobody keeps a second copy of these strings.
VERSION = "1.2"
ATTRIBUTION = "Data and camera image: Kentucky Mesonet at WKU"
FOOTER_URL = "weatherbrother.com"

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "fonts")
ASSETS = os.path.join(HERE, "assets")


def f(name, size):
    return ImageFont.truetype(os.path.join(FONTS, name), size)


BOLD, SEMI, MED, REG, ITAL = ("Poppins-Bold.ttf", "Poppins-SemiBold.ttf",
                              "Poppins-Medium.ttf", "Poppins-Regular.ttf",
                              "Poppins-Italic.ttf")


def tw(d, s, font):
    b = d.textbbox((0, 0), s, font=font)
    return b[2] - b[0], b[3] - b[1], b


def ctext(d, cx, y, s, font, fill):
    w, _, b = tw(d, s, font)
    d.text((cx - w / 2 - b[0], y), s, font=font, fill=fill)


def fit(img, box):
    """Cover-crop an image into box without distortion."""
    bw, bh = box[2] - box[0], box[3] - box[1]
    sc = max(bw / img.width, bh / img.height)
    im = img.resize((max(1, round(img.width * sc)), max(1, round(img.height * sc))),
                    Image.LANCZOS)
    return im.crop(((im.width - bw) // 2, (im.height - bh) // 2,
                    (im.width - bw) // 2 + bw, (im.height - bh) // 2 + bh))


def hi_color(hi):
    if hi is None:
        return GREEN
    return RED if hi >= 103 else (AMBER if hi >= 100 else GREEN)


def rounded(size, r):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                        radius=r, fill=255)
    return m


def render(p, out):
    img = Image.new("RGB", (W, H), ICE)
    d = ImageDraw.Draw(img)
    hi = p.get("heat_index_f")
    accent = hi_color(hi)

    # ---- header ---------------------------------------------------------
    d.rectangle([0, 0, W, HDR_H], fill=NAVY)
    logo = Image.open(os.path.join(ASSETS, "logo_white_clean.png")).convert("RGBA")
    lh = 104
    logo = logo.resize((round(logo.width * lh / logo.height), lh), Image.LANCZOS)
    img.paste(logo, (MARGIN, (HDR_H - lh) // 2), logo)
    s = "Current conditions"
    w, _, b = tw(d, s, f(SEMI, 34))
    d.text((W - MARGIN - w - b[0], 62), s, font=f(SEMI, 34), fill=WHITE)
    s2 = f"{p['obs_date']}"
    w2, _, b2 = tw(d, s2, f(REG, 30))
    d.text((W - MARGIN - w2 - b2[0], 108), s2, font=f(REG, 30), fill=LAVENDER)

    # ---- camera zone ----------------------------------------------------
    cam = p.get("camera_path")
    if cam and os.path.exists(cam):
        img.paste(fit(Image.open(cam).convert("RGB"), (0, CAM_Y0, W, CAM_Y1)),
                  (0, CAM_Y0))
    else:
        sh = Image.open(os.path.join(ASSETS, "shield_navy.png")).convert("RGBA")
        t = int(W * 0.30)
        sh = sh.resize((t, round(sh.height * t / sh.width)), Image.LANCZOS)
        zc = (CAM_Y0 + CAM_Y1) // 2
        img.paste(sh, ((W - sh.width) // 2, zc - sh.height // 2 - 34), sh)
        ctext(d, W // 2, zc + sh.height // 2 - 12, "Camera offline", f(MED, 38), SLATE)

    # ---- data panel -----------------------------------------------------
    x0, y0, x1, y1 = PANEL
    lay = Image.new("RGB", (x1 - x0, y1 - y0), WHITE)
    ImageDraw.Draw(lay).rectangle([0, 0, ACCENT_W, y1 - y0], fill=accent)
    img.paste(lay, (x0, y0), rounded((x1 - x0, y1 - y0), RADIUS))
    cx = (x0 + ACCENT_W + x1) // 2
    ix0, ix1 = x0 + ACCENT_W + 46, x1 - 46

    ctext(d, cx, y0 + 26, p["county"].upper(), f(BOLD, 58), NAVY)
    ctext(d, cx, y0 + 98, f"Station {p['station_id']}  \u00b7  {p['site']}, KY",
          f(MED, 30), SLATE)
    d.line([(ix0, y0 + 154), (ix1, y0 + 154)], fill=RULE, width=2)

    # hero: air temperature
    ctext(d, cx, y0 + 176, f"{p['air_f']:.1f}\u00b0F", f(BOLD, 132), INK)
    ctext(d, cx, y0 + 330, "AIR TEMPERATURE", f(MED, 30), SLATE)
    d.line([(ix0, y0 + 382), (ix1, y0 + 382)], fill=RULE, width=2)

    # metric row: 2 or 3 even columns so the panel fills either way
    cols = [("DEW POINT", f"{p['dewpoint_f']:.1f}\u00b0F", INK),
            ("HUMIDITY", f"{p['humidity_pct']:.1f}%", INK)]
    if hi is not None:
        cols.append(("HEAT INDEX", f"{hi:.1f}\u00b0F", accent))
    span = (ix1 - ix0) / len(cols)
    for i, (lbl, val, col) in enumerate(cols):
        ccx = ix0 + span * (i + 0.5)
        ctext(d, ccx, y0 + 406, val, f(SEMI, 62), col)
        ctext(d, ccx, y0 + 486, lbl, f(MED, 26), SLATE)
        if i:
            d.line([(ix0 + span * i, y0 + 414), (ix0 + span * i, y0 + 518)],
                   fill=RULE, width=2)
    d.line([(ix0, y0 + 536), (ix1, y0 + 536)], fill=RULE, width=2)

    # provenance line, with stale pill inline when the ob has aged out
    age = p.get("obs_age_min")
    stale = age is not None and age > STALE_MIN
    pill_w = 0
    if stale:
        pt = f"{age} min old"
        pw, _, _ = tw(d, pt, f(SEMI, 24))
        pill_w = pw + 40 + 16

    # The line plus the pill can exceed the panel, and centering without a
    # clamp pushes the left end over the accent bar. So shorten by one rung
    # at a time until it fits, then clamp. The date goes first because the
    # header already renders the same obs_date value.
    _obs = f"Observed {p['obs_time']}"
    _date = f"  \u00b7  {p['obs_date']}"
    _cam = f"   |   Camera {p['camera_time']}" if p.get("camera_time") else ""
    avail = ix1 - ix0
    for parts in (_obs + _date + _cam, _obs + _cam, _obs):
        tot, _, tb = tw(d, parts, f(REG, 26))
        if tot + pill_w <= avail:
            break
    sx = max(ix0, cx - (tot + pill_w) / 2)
    d.text((sx - tb[0], y0 + 556), parts, font=f(REG, 26), fill=SLATE)
    if stale:
        px0 = sx + tot + 16
        d.rounded_rectangle([px0, y0 + 550, px0 + pill_w - 16, y0 + 592],
                            radius=21, fill=AMBER)
        ctext(d, px0 + (pill_w - 16) / 2, y0 + 558, f"{age} min old",
              f(SEMI, 24), INK)

    # ---- tagline + footer ----------------------------------------------
    ctext(d, W // 2, TAGLINE_Y,
          "This card is your county. The app is your driveway.",
          f(ITAL, 33), SLATE)
    d.rectangle([0, FTR_Y, W, H], fill=NAVY)
    ctext(d, W // 2, FTR_Y + 38, ATTRIBUTION, f(REG, 28), LAVENDER)
    ctext(d, W // 2, FTR_Y + 86, FOOTER_URL, f(BOLD, 50), WHITE)

    img.save(out, "PNG")
    return out


if __name__ == "__main__":
    payload = json.load(open(sys.argv[1]))
    print(render(payload, sys.argv[2]))
