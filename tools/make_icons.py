"""Rasterize the app icon (same design as site/icons/icon.svg) to PNG without extra dependencies."""
import struct
import zlib
from pathlib import Path

BG, FG, DOT = (0x7A, 0x2E, 0x1D), (0xF6, 0xF3, 0xEE), (0xE0, 0xB2, 0x5A)
# (x, y, w, h, opacity) in a 512 grid, pill-shaped bars
BARS = [(128, 140, 256, 34, 1.0), (128, 216, 200, 26, .85), (128, 278, 232, 26, .85), (128, 340, 168, 26, .85)]


def in_pill(x, y, bx, by, bw, bh):
    r = bh / 2
    if not (by <= y <= by + bh):
        return False
    cx = min(max(x, bx + r), bx + bw - r)
    return (x - cx) ** 2 + (y - (by + r)) ** 2 <= r * r


def pixel(x, y, maskable):
    # full-bleed background for maskable icons; rounded corners otherwise
    if not maskable:
        r = 96
        cx, cy = min(max(x, r), 512 - r), min(max(y, r), 512 - r)
        if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
            return (0, 0, 0, 0)
    col = BG
    for bx, by, bw, bh, a in BARS:
        if in_pill(x, y, bx, by, bw, bh):
            col = tuple(round(f * a + b * (1 - a)) for f, b in zip(FG, BG))
    if (x - 368) ** 2 + (y - 352) ** 2 <= 36 ** 2:
        col = DOT
    return (*col, 255)


def png(size, path, maskable=False, ss=2):
    rows = []
    s = 512 / size
    for j in range(size):
        row = bytearray([0])
        for i in range(size):
            acc = [0, 0, 0, 0]
            for dj in range(ss):
                for di in range(ss):
                    p = pixel((i + (di + .5) / ss) * s, (j + (dj + .5) / ss) * s, maskable)
                    acc = [a + b for a, b in zip(acc, p)]
            row += bytes(round(a / ss / ss) for a in acc)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")
    Path(path).write_bytes(data)


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "site" / "icons"
    png(192, out / "icon-192.png", maskable=True)  # iOS applies its own rounding
    png(512, out / "icon-512.png", maskable=True)
    print("icons written to", out)
