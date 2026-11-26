"""Build a maximally compatible Windows .ico.

Pillow writes PNG-compressed entries, which Windows Explorer can ignore for
desktop shortcut icons. This script writes classic BMP (DIB) entries for every
size so the shell renders the icon everywhere.
"""

from __future__ import annotations

import struct
from pathlib import Path

from PIL import Image

ROOT = Path(r"C:\Users\13155\Documents\deepseek-harness\default-workspace\examos")
SRC = ROOT / "assets" / "icon-source.jpg"
DST = ROOT / "assets" / "examos.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def bmp_entry(img: Image.Image) -> bytes:
    size = img.size[0]
    rgba = img.convert("RGBA")
    pixels = rgba.load()
    # XOR bitmap: bottom-up BGRA
    xor = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            r, g, b, a = pixels[x, y]
            xor += bytes((b, g, r, a))
    # AND mask: 1 bit per pixel, rows padded to 4 bytes, all opaque (0)
    row_bytes = ((size + 31) // 32) * 4
    and_mask = b"\x00" * (row_bytes * size)
    header = struct.pack(
        "<IiiHHIIiiII",
        40,          # biSize
        size,        # biWidth
        size * 2,    # biHeight (XOR + AND)
        1,           # biPlanes
        32,          # biBitCount
        0,           # biCompression
        len(xor) + len(and_mask),
        0, 0, 0, 0,
    )
    return header + bytes(xor) + and_mask


def main() -> None:
    base = Image.open(SRC).convert("RGBA")
    side = int(min(base.size) * 0.86)
    left = (base.width - side) // 2
    top = int((base.height - side) * 0.42)
    base = base.crop((left, top, left + side, top + side))

    frames = []
    for size in SIZES:
        frames.append((size, bmp_entry(base.resize((size, size), Image.LANCZOS))))

    data = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    directory = b""
    body = b""
    for size, payload in frames:
        width = 0 if size == 256 else size
        directory += struct.pack("<BBBBHHII", width, width, 0, 0, 1, 32, len(payload), offset)
        body += payload
        offset += len(payload)
    DST.write_bytes(data + directory + body)
    print(f"wrote {DST} ({DST.stat().st_size} bytes, {len(frames)} classic BMP entries)")


if __name__ == "__main__":
    main()
