#!/usr/bin/env python3
"""Generate CircleClash's app icon: two clashing osu!-style hit circles (pink + ice)
on a dark rounded tile, with neon glow. Renders at high resolution with supersampling,
then exports icon.ico (multi-size) and icon.png (256px) for the window icon."""
from PIL import Image, ImageDraw, ImageFilter

SS = 8            # supersampling factor
S = 256           # final base size
N = S * SS

PINK = (255, 102, 171)
ICE = (102, 217, 255)
INK = (10, 10, 13)
INK2 = (20, 20, 28)


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return m


def ring(center, r, width, color, glow=False):
    """Draw a glowing ring onto its own RGBA layer and return it."""
    layer = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = center
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color + (255,), width=width)
    # inner soft fill for a hit-circle look
    d.ellipse((cx - r + width, cy - r + width, cx + r - width, cy + r - width),
              fill=color + (38,))
    if glow:
        g = layer.filter(ImageFilter.GaussianBlur(SS * 6))
        out = Image.new("RGBA", (N, N), (0, 0, 0, 0))
        out = Image.alpha_composite(out, g)
        out = Image.alpha_composite(out, layer)
        return out
    return layer


def build():
    # background tile
    bg = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(bg)
    d.rounded_rectangle((0, 0, N - 1, N - 1), radius=int(N * 0.235),
                        fill=INK + (255,))
    # subtle top sheen
    sheen = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    ds = ImageDraw.Draw(sheen)
    ds.rounded_rectangle((0, 0, N - 1, int(N * 0.5)), radius=int(N * 0.235),
                         fill=INK2 + (255,))
    sheen = sheen.filter(ImageFilter.GaussianBlur(SS * 10))
    bg = Image.alpha_composite(bg, sheen)

    # two overlapping hit circles
    r = int(N * 0.255)
    w = int(N * 0.052)
    off = int(N * 0.135)
    cy = N // 2
    left = ring((N // 2 - off, cy), r, w, PINK, glow=True)
    right = ring((N // 2 + off, cy), r, w, ICE, glow=True)

    img = Image.alpha_composite(bg, right)
    img = Image.alpha_composite(img, left)

    # clip everything to the rounded tile
    mask = rounded_mask(N, int(N * 0.235))
    img.putalpha(Image.composite(img.getchannel("A"), Image.new("L", (N, N), 0), mask))

    # downsample
    base = img.resize((S, S), Image.LANCZOS)
    base.save("icon.png")

    sizes = [16, 24, 32, 48, 64, 128, 256]
    base.save("icon.ico", sizes=[(s, s) for s in sizes])
    print("wrote icon.png (256) and icon.ico", sizes)


if __name__ == "__main__":
    build()
