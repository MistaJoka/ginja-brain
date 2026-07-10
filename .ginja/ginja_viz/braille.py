"""BrailleCanvas — a terminal pixel buffer.

Each terminal cell is one braille character (U+2800 block) holding a 2-wide ×
4-tall dot grid, so a canvas of C×R cells gives 2C × 4R addressable pixels.
Pure stdlib; a few thousand plots per frame cost ~1 ms — terminal I/O, not
this module, is the frame budget.
"""

# Braille dot bit for pixel (x % 2, y % 4)
_DOT = (
    (0x01, 0x08),
    (0x02, 0x10),
    (0x04, 0x20),
    (0x40, 0x80),
)


class BrailleCanvas:
    def __init__(self, cols: int, rows: int):
        self.cols = max(1, cols)
        self.rows = max(1, rows)
        self.width = self.cols * 2    # pixels
        self.height = self.rows * 4   # pixels
        self.buf = bytearray(self.cols * self.rows)

    def clear(self):
        for i in range(len(self.buf)):
            self.buf[i] = 0

    def plot(self, x: int, y: int):
        if 0 <= x < self.width and 0 <= y < self.height:
            self.buf[(y >> 2) * self.cols + (x >> 1)] |= _DOT[y & 3][x & 1]

    def scatter(self, pts):
        plot = self.plot
        for x, y in pts:
            plot(int(x), int(y))

    def line(self, x0: int, y0: int, x1: int, y1: int):
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.plot(x0, y0)
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def circle(self, cx: int, cy: int, r: int, fill: bool = False):
        cx, cy, r = int(cx), int(cy), int(r)
        if r <= 0:
            self.plot(cx, cy)
            return
        x, y, err = -r, 0, 2 - 2 * r
        while x < 0:
            if fill:
                self.line(cx + x, cy + y, cx - x, cy + y)
                self.line(cx + x, cy - y, cx - x, cy - y)
            else:
                self.plot(cx - x, cy + y)
                self.plot(cx - y, cy - x)
                self.plot(cx + x, cy - y)
                self.plot(cx + y, cy + x)
            r = err
            if r <= y:
                y += 1
                err += y * 2 + 1
            if r > x or err > y:
                x += 1
                err += x * 2 + 1

    def ellipse(self, cx: int, cy: int, rx: int, ry: int, steps: int = 0):
        import math
        steps = steps or max(16, int(2 * math.pi * max(rx, ry)))
        for i in range(steps):
            a = 2 * math.pi * i / steps
            self.plot(int(cx + rx * math.cos(a)), int(cy + ry * math.sin(a)))

    def rect(self, x0: int, y0: int, x1: int, y1: int, fill: bool = False):
        if fill:
            for y in range(int(y0), int(y1) + 1):
                self.line(x0, y, x1, y)
        else:
            self.line(x0, y0, x1, y0)
            self.line(x1, y0, x1, y1)
            self.line(x1, y1, x0, y1)
            self.line(x0, y1, x0, y0)

    def render(self) -> list:
        """Rows of braille text (no color)."""
        out = []
        for r in range(self.rows):
            row = self.buf[r * self.cols:(r + 1) * self.cols]
            out.append("".join(chr(0x2800 + b) for b in row))
        return out


def render_bands(canvases, ansi_colors, overlay=None) -> list:
    """Merge layered canvases into ANSI-colored rows.

    canvases: list of BrailleCanvas, ordered dim → bright (same size).
    ansi_colors: matching list of 256-color codes.
    overlay: optional {(row, col): (char, color_index)} of literal characters
             (engine labels, status glyphs) drawn on top.
    Color changes are emitted only when the band switches, keeping the escape
    volume — the real ssh FPS ceiling — low.
    """
    base = canvases[0]
    cols, rows = base.cols, base.rows
    overlay = overlay or {}
    out = []
    for r in range(rows):
        parts = []
        current = -1
        for c in range(cols):
            ov = overlay.get((r, c))
            if ov is not None:
                ch, band = ov[0], min(ov[1], len(ansi_colors) - 1)
            else:
                bits = 0
                band = -1
                for i, cv in enumerate(canvases):
                    b = cv.buf[r * cols + c]
                    if b:
                        bits |= b
                        band = i
                ch = chr(0x2800 + bits) if bits else " "
            if band != current and ch != " ":
                parts.append(f"\x1b[38;5;{ansi_colors[max(band, 0)]}m")
                current = band
            parts.append(ch)
        parts.append("\x1b[0m")
        out.append("".join(parts))
    return out
