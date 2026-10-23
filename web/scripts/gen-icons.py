"""Regenerates public/icon-192.png and icon-512.png (stdlib only) to match
public/icon.svg: an orange rounded square with a white wireframe cube.
Run: python3 web/scripts/gen-icons.py"""
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "public"
BG, FG = (0xE8, 0x62, 0x2C), (255, 255, 255)
# Designed on a 512 grid (same coordinates as icon.svg).
HEX = [(256, 96), (400, 176), (400, 336), (256, 416), (112, 336), (112, 176)]
SEGS = [(HEX[i], HEX[(i + 1) % 6]) for i in range(6)] + [
    ((112, 176), (256, 256)), ((256, 256), (400, 176)), ((256, 256), (256, 416))]
HALF = 14.0


def dist(px, py, a, b):
    ax, ay = a; bx, by = b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5


def in_round_rect(x, y, r=96):
    cx = min(max(x, r), 512 - r); cy = min(max(y, r), 512 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def render(size):
    scale = 512 / size
    rows = []
    for j in range(size):
        row = bytearray([0])
        for i in range(size):
            cov = bg = 0
            n = 3  # 3x3 supersampling
            for sy in range(n):
                for sx in range(n):
                    x = (i + (sx + .5) / n) * scale; y = (j + (sy + .5) / n) * scale
                    if in_round_rect(x, y):
                        bg += 1
                        if min(dist(x, y, a, b) for a, b in SEGS) <= HALF:
                            cov += 1
            tot = n * n
            r, g, b = (round((BG[k] * (bg - cov) + FG[k] * cov) / bg) if bg else 0 for k in range(3))
            row += bytes([r, g, b, round(255 * bg / tot)])
        rows.append(bytes(row))
    return b"".join(rows)


def png(size):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(render(size), 9)) + chunk(b"IEND", b""))


for s in (192, 512):
    (OUT / f"icon-{s}.png").write_bytes(png(s))
