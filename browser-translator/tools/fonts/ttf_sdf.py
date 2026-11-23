"""Glyph -> TMP-style SDF tile renderer (pure Python, fontTools only).

Why hand-rolled: the only thing this build needs is "put this character's shape
into an existing atlas rectangle in the encoding TMP already uses".  TMP stores
an Alpha8 distance field where

    128 = the glyph outline,  +127 at 9 px inside,  0 at 9 px outside

i.e. value = clamp(128 + d_px * 128 / 9).  The 9 is the asset's own
m_AtlasPadding (9) and it was verified against the shipped atlas: across a stem
edge of 'A' the stored values step 128 -> 114 -> 104 -> 93 -> 82 ..., i.e. about
14.2 per pixel.

Pipeline per glyph: decompose the TrueType outline (composites included) with
fontTools, flatten the curves, scanline-fill it at 4x supersampling with
non-zero winding, run an exact squared Euclidean distance transform
(Felzenszwalb) over that mask, then read the distance back at the centre of
every output pixel.
"""

import math

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.basePen import decomposeQuadraticSegment
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont

SUP = 4                      # supersampling factor
EDGE_VALUE = 128.0           # value at the outline
SPREAD_PX = 9.0              # +-range of the field, = TMP m_AtlasPadding
TOL_PX = 0.3                 # curve flattening tolerance, pixels


def _sq_edt_1d(f):
    """Felzenszwalb & Huttenlocher exact 1-D squared distance transform."""
    n = len(f)
    d = [0.0] * n
    v = [0] * n
    z = [0.0] * (n + 1)
    k = 0
    v[0] = 0
    z[0] = -1e30
    z[1] = 1e30
    for q in range(1, n):
        s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0 * q - 2.0 * v[k])
        while s <= z[k]:
            k -= 1
            s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0 * q - 2.0 * v[k])
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = 1e30
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        d[q] = (q - v[k]) ** 2 + f[v[k]]
    return d


def _sq_edt(mask, w, h, target):
    """Squared distance from every cell to the nearest cell with value `target`."""
    inf = 1e18
    grid = [[0.0 if mask[y * w + x] == target else inf for x in range(w)]
            for y in range(h)]
    for y in range(h):
        grid[y] = _sq_edt_1d(grid[y])
    for x in range(w):
        col = _sq_edt_1d([grid[y][x] for y in range(h)])
        for y in range(h):
            grid[y][x] = col[y]
    return grid


class GlyphSdf:
    def __init__(self, ttf_path):
        self.font = TTFont(ttf_path, lazy=False)
        self.cmap = self.font.getBestCmap()
        self.glyph_set = self.font.getGlyphSet()
        self.upem = self.font['head'].unitsPerEm

    def has(self, ch):
        return ord(ch) in self.cmap

    # ------------------------------------------------------------------
    def outline(self, ch):
        """Flattened contours in font units, composites decomposed."""
        gname = self.cmap[ord(ch)]
        rec = DecomposingRecordingPen(self.glyph_set)
        self.glyph_set[gname].draw(rec)

        contours, pts, cur = [], [], None
        for op, args in rec.value:
            if op == 'moveTo':
                if len(pts) >= 3:
                    contours.append(pts)
                pts = [args[0]]
                cur = args[0]
            elif op == 'lineTo':
                pts.append(args[0])
                cur = args[0]
            elif op == 'qCurveTo':
                for ctrl, end in decomposeQuadraticSegment(list(args)):
                    pts.extend(_flatten_quad(cur, ctrl, end, flat=8))
                    cur = end
            elif op == 'curveTo':
                c1, c2, end = args
                pts.extend(_flatten_cubic(cur, c1, c2, end, flat=10))
                cur = end
            elif op == 'closePath':
                if len(pts) >= 3:
                    contours.append(pts)
                pts, cur = [], None
            elif op == 'endPath':
                if len(pts) >= 3:
                    contours.append(pts)
                pts, cur = [], None
        if len(pts) >= 3:
            contours.append(pts)
        return contours

    def bbox(self, contours):
        xs = [p[0] for c in contours for p in c]
        ys = [p[1] for c in contours for p in c]
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    # ------------------------------------------------------------------
    def render(self, ch, w, h, scale, ox, oy):
        """Draw `ch` into a w*h Alpha8 tile with an explicit transform.

        pixel_x = (glyph_x - ox) * scale, pixel_y = (glyph_y - oy) * scale,
        y increasing upwards.  No fitting: the caller decides the size, so a
        glyph is never rescaled to fill its atlas rectangle.
        """
        empty = bytes(w * h)
        if not self.has(ch):
            return empty
        contours = self.outline(ch)
        if not contours:
            return empty

        W, H = w * SUP, h * SUP
        mask = bytearray(W * H)
        edges = []
        for c in contours:
            n = len(c)
            for i in range(n):
                x1, y1 = c[i]
                x2, y2 = c[(i + 1) % n]
                if y1 == y2:
                    continue
                edges.append(((x1 - ox) * scale * SUP, (y1 - oy) * scale * SUP,
                              (x2 - ox) * scale * SUP, (y2 - oy) * scale * SUP))
        if not edges:
            return empty

        for row in range(H):
            yc = row + 0.5
            cross = []
            for x1, y1, x2, y2 in edges:
                if (y1 <= yc < y2) or (y2 <= yc < y1):
                    t = (yc - y1) / (y2 - y1)
                    cross.append((x1 + t * (x2 - x1), 1 if y2 > y1 else -1))
            if not cross:
                continue
            cross.sort()
            wind = 0
            base = row * W
            for i in range(len(cross) - 1):
                wind += cross[i][1]
                if wind == 0:
                    continue
                xa = max(0, int(math.ceil(cross[i][0] - 0.5)))
                xb = min(W - 1, int(math.floor(cross[i + 1][0] - 0.5)))
                for x in range(xa, xb + 1):
                    mask[base + x] = 1

        inside = _sq_edt(mask, W, H, 0)
        outside = _sq_edt(mask, W, H, 1)

        out = bytearray(w * h)
        half = SUP // 2
        k = 127.0 / SPREAD_PX
        for y in range(h):
            sy = y * SUP + half
            for x in range(w):
                sx = x * SUP + half
                if mask[sy * W + sx]:
                    d = math.sqrt(inside[sy][sx]) / SUP
                else:
                    d = -math.sqrt(outside[sy][sx]) / SUP
                v = EDGE_VALUE + d * k
                out[y * w + x] = 0 if v < 0 else (255 if v > 255 else int(v + 0.5))
        return bytes(out)

    def tile(self, ch, w, h, margin=0.6):
        """Render `ch` fitted into a w*h tile (legacy behaviour, keeps the
        stage-9b experiment reproducible)."""
        contours = self.outline(ch)
        bb = self.bbox(contours)
        if not bb:
            return bytes(w * h)
        bw, bh = bb[2] - bb[0], bb[3] - bb[1]
        if bw <= 0 or bh <= 0:
            return bytes(w * h)
        scale = min((w - 2 * margin) / bw, (h - 2 * margin) / bh)
        ox = bb[0] - (w / scale - bw) / 2.0
        oy = bb[1] - (h / scale - bh) / 2.0
        return self.render(ch, w, h, scale, ox, oy)

    def natural_tile(self, ch, point_size, pad_px):
        """Glyph at its natural size for `point_size`, with `pad_px` of empty
        margin on every side (the shipped atlas keeps 1-3 px; TMP packs with
        m_AtlasPadding between glyphs, so the tile itself only needs a little).

        Historical output for stages 10-13 only. Do not use the whole padded
        tile as a TMP GlyphRect: its UV extent does not match the ink metrics.
        New font builds must use natural_cell() and store its outside ring.
        Returns (tile, w, h, metrics) with metrics in pixels at point_size.
        """
        scale = point_size / self.upem
        contours = self.outline(ch)
        bb = self.bbox(contours)
        if not bb:
            return None
        ink_w = (bb[2] - bb[0]) * scale
        ink_h = (bb[3] - bb[1]) * scale
        w = int(math.ceil(ink_w)) + 2 * pad_px
        h = int(math.ceil(ink_h)) + 2 * pad_px
        ox = bb[0] - pad_px / scale
        oy = bb[1] - pad_px / scale
        tile = self.render(ch, w, h, scale, ox, oy)
        adv = self.font['hmtx'][self.cmap[ord(ch)]][0] * scale
        metrics = [ink_w, ink_h, bb[0] * scale, bb[3] * scale, adv]
        return tile, w, h, metrics

    def natural_cell(self, ch, point_size, ring_px=9):
        """Pixel-aligned ink rect with an OUTSIDE SDF ring, for TMP UV quads.

        Unlike legacy natural_tile(), ring pixels are not part of GlyphRect.
        Metrics and rect describe the same integer pixel box; bearings place
        that box on the baseline, while advance stays the font's native value.
        Return (cell, cell_w, cell_h, metrics, rect_w, rect_h).
        """
        scale = point_size / self.upem
        bounds = BoundsPen(self.glyph_set)
        self.glyph_set[self.cmap[ord(ch)]].draw(bounds)
        bb = bounds.bounds
        advance = self.font['hmtx'][self.cmap[ord(ch)]][0] * scale
        if not bb:
            return b'', 0, 0, [0.0, 0.0, 0.0, 0.0, advance], 0, 0
        left, bottom = math.floor(bb[0] * scale), math.floor(bb[1] * scale)
        right, top = math.ceil(bb[2] * scale), math.ceil(bb[3] * scale)
        rw, rh = right - left, top - bottom
        cw, ch_ = rw + ring_px * 2, rh + ring_px * 2
        cell = self.render(ch, cw, ch_, scale,
                           (left - ring_px) / scale, (bottom - ring_px) / scale)
        return cell, cw, ch_, [float(rw), float(rh), float(left), float(top), advance], rw, rh


def _flatten_quad(p0, p1, p2, flat=8):
    pts = []
    for s in range(1, flat + 1):
        t = s / float(flat)
        mt = 1.0 - t
        pts.append((mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0],
                    mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]))
    return pts


def _flatten_cubic(p0, p1, p2, p3, flat=10):
    pts = []
    for s in range(1, flat + 1):
        t = s / float(flat)
        mt = 1.0 - t
        pts.append((mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0]
                    + 3 * mt * t * t * p2[0] + t ** 3 * p3[0],
                    mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1]
                    + 3 * mt * t * t * p2[1] + t ** 3 * p3[1]))
    return pts


def write_png(path, width, height, alpha):
    """Tiny greyscale PNG writer so results can be eyeballed without Pillow."""
    import struct
    import zlib
    rows = []
    for y in range(height - 1, -1, -1):
        row = bytearray([0])
        for x in range(width):
            v = alpha[y * width + x]
            row += bytes((v, v, v))
        rows.append(bytes(row))
    raw = b''.join(rows)

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))

    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(bytes(raw), 6))
    png += chunk(b'IEND', b'')
    open(path, 'wb').write(png)
