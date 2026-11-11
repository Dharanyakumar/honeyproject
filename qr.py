"""Small QR code generator with no extra packages (byte mode, error correction M, versions 1-10).

Enough for a batch link such as http://host:5000/trace/HNY-2026-0001 (up to 213 characters).
The algorithm follows the QR standard (ISO/IEC 18004): Reed-Solomon codes over GF(256),
function patterns, 8 mask patterns scored by the standard penalty rules.
"""

# Version -> (error-correction codewords per block, number of blocks) for level M.
_ECC = {1: (10, 1), 2: (16, 1), 3: (26, 1), 4: (18, 2), 5: (24, 2),
        6: (16, 4), 7: (18, 4), 8: (22, 4), 9: (22, 5), 10: (26, 5)}
MAX_VERSION = 10
_M_FORMAT_BITS = 0  # level M = 00


def _alignment_positions(ver):
    if ver == 1:
        return []
    n = ver // 7 + 2
    step = (ver * 8 + n * 3 + 5) // (n * 4 - 4) * 2
    return [6] + [ver * 4 + 10 - (n - 2 - i) * step for i in range(n - 1)]


def _num_data_codewords(ver):
    ecc_len, blocks = _ECC[ver]
    return _num_raw_codewords(ver) - ecc_len * blocks


def _num_raw_codewords(ver):
    size = ver * 4 + 17
    result = size * size
    result -= 64 * 3                      # three finder patterns with separators
    result -= 15 * 2 + 1                  # format information and the dark module
    result -= (size - 16) * 2             # timing patterns
    if ver >= 2:
        n = ver // 7 + 2
        result -= (n - 1) ** 2 * 25       # alignment patterns
        result -= (n - 2) * 2 * 20        # alignment patterns overlapping the timing patterns
    if ver >= 7:
        result -= 36                      # version information
    return result // 8


# ---- Reed-Solomon over GF(256), polynomial 0x11D ----
def _gf_mul(x, y):
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _bch(value, poly, ecc_bits):
    """Remainder of value * x^ecc_bits divided by the generator polynomial."""
    rem = value
    for _ in range(ecc_bits):
        rem = (rem << 1) ^ ((rem >> (ecc_bits - 1)) * poly)
    return rem


class _Matrix:
    def __init__(self, ver):
        self.ver = ver
        self.size = ver * 4 + 17
        self.mod = [[False] * self.size for _ in range(self.size)]
        self.func = [[False] * self.size for _ in range(self.size)]

    def set_func(self, x, y, dark):
        self.mod[y][x] = dark
        self.func[y][x] = True

    def draw_function_patterns(self):
        size = self.size
        for i in range(size):
            self.set_func(6, i, i % 2 == 0)
            self.set_func(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        dist = max(abs(dx), abs(dy))
                        self.set_func(x, y, dist not in (2, 4))
        pos = _alignment_positions(self.ver)
        for i, py in enumerate(pos):
            for j, px in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == len(pos) - 1) or (i == len(pos) - 1 and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_func(px + dx, py + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)  # reserve the format areas
        self.draw_version()

    def draw_format(self, mask):
        data = (_M_FORMAT_BITS << 3) | mask
        bits = ((data << 10) | _bch(data, 0x537, 10)) ^ 0x5412
        get = lambda i: (bits >> i) & 1 == 1
        size = self.size
        for i in range(0, 6):
            self.set_func(8, i, get(i))
        self.set_func(8, 7, get(6))
        self.set_func(8, 8, get(7))
        self.set_func(7, 8, get(8))
        for i in range(9, 15):
            self.set_func(14 - i, 8, get(i))
        for i in range(0, 8):
            self.set_func(size - 1 - i, 8, get(i))
        for i in range(8, 15):
            self.set_func(8, size - 15 + i, get(i))
        self.set_func(8, size - 8, True)  # the always-dark module

    def draw_version(self):
        if self.ver < 7:
            return
        bits = (self.ver << 12) | _bch(self.ver, 0x1F25, 12)
        for i in range(18):
            dark = (bits >> i) & 1 == 1
            a, b = self.size - 11 + i % 3, i // 3
            self.set_func(a, b, dark)
            self.set_func(b, a, dark)

    def draw_codewords(self, data):
        size, i = self.size, 0
        for right in range(size - 1, 0, -2):
            if right <= 6:
                right -= 1
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = size - 1 - vert if upward else vert
                    if not self.func[y][x] and i < len(data) * 8:
                        self.mod[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1

    def apply_mask(self, mask):
        for y in range(self.size):
            for x in range(self.size):
                inv = [(x + y) % 2 == 0, y % 2 == 0, x % 3 == 0, (x + y) % 3 == 0,
                       (x // 3 + y // 2) % 2 == 0, x * y % 2 + x * y % 3 == 0,
                       (x * y % 2 + x * y % 3) % 2 == 0, ((x + y) % 2 + x * y % 3) % 2 == 0][mask]
                if inv and not self.func[y][x]:
                    self.mod[y][x] = not self.mod[y][x]

    def penalty(self):
        size, m = self.size, self.mod
        score = 0
        for grid in (m, [list(col) for col in zip(*m)]):  # rows, then columns
            for row in grid:
                run, prev = 1, row[0]
                for v in row[1:]:
                    if v == prev:
                        run += 1
                    else:
                        if run >= 5:
                            score += run - 2
                        run, prev = 1, v
                if run >= 5:
                    score += run - 2
                s = "".join("1" if v else "0" for v in row)
                for pat in ("10111010000", "00001011101"):
                    start = s.find(pat)
                    while start != -1:
                        score += 40
                        start = s.find(pat, start + 1)
        for y in range(size - 1):
            for x in range(size - 1):
                if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(v for row in m for v in row)
        total = size * size
        score += ((abs(dark * 20 - total * 10) + total - 1) // total - 1) * 10
        return score


def encode(text):
    """Return the QR code as a list of rows of booleans (True = dark module)."""
    raw = text.encode("utf-8")
    for ver in range(1, MAX_VERSION + 1):
        capacity = _num_data_codewords(ver)
        need_bits = 4 + (8 if ver < 10 else 16) + len(raw) * 8
        if need_bits <= capacity * 8:
            break
    else:
        raise ValueError("Text is too long for this QR generator (max 213 bytes).")

    bits = [0, 1, 0, 0]  # byte mode
    count_bits = 8 if ver < 10 else 16
    bits += [(len(raw) >> i) & 1 for i in range(count_bits - 1, -1, -1)]
    for b in raw:
        bits += [(b >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, capacity * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(data) < capacity:
        data.append(pad)
        pad ^= 0xEC ^ 0x11

    ecc_len, nblocks = _ECC[ver]
    raw_cw = _num_raw_codewords(ver)
    short_blocks = nblocks - raw_cw % nblocks
    short_len = raw_cw // nblocks
    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(nblocks):
        size = short_len - ecc_len + (0 if i < short_blocks else 1)
        chunk = data[k:k + size]
        k += size
        ecc = _rs_remainder(chunk, divisor)
        if i < short_blocks:
            chunk = chunk + [0]
        blocks.append(chunk + ecc)
    interleaved = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != short_len - ecc_len or j >= short_blocks:
                interleaved.append(blk[i])

    best = None
    for mask in range(8):
        m = _Matrix(ver)
        m.draw_function_patterns()
        m.draw_codewords(interleaved)
        m.apply_mask(mask)
        m.draw_format(mask)
        score = m.penalty()
        if best is None or score < best[0]:
            best = (score, m)
    return best[1].mod


def to_svg(text, scale=8, border=4, dark="#1B2A21", light="#FFFFFF"):
    """The QR code as a standalone SVG string."""
    rows = encode(text)
    n = len(rows)
    size = (n + border * 2) * scale
    path = "".join(f"M{(x + border) * scale},{(y + border) * scale}h{scale}v{scale}h-{scale}z"
                   for y, row in enumerate(rows) for x, v in enumerate(row) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
            f'shape-rendering="crispEdges" role="img" aria-label="QR code"><rect width="100%" height="100%" fill="{light}"/>'
            f'<path d="{path}" fill="{dark}"/></svg>')
