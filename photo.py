"""Photo screening model (a quick colour-and-texture check from a jar photo).

A photo cannot prove honey is pure. Colour, clarity and foam only hint at syrup feeding, caramel colouring or
dilution. So this model is a SCREENING step: it never writes to the ledger and never decides a verdict. The
lab-value check and the spectral check stay the main tests.

Pipeline: centre crop -> 10 colour and texture numbers -> Random Forest.

The training images are SYNTHETIC (rendered by `render()` below from colour and texture ranges taken from honey
colour studies, with deliberate overlap between the classes: pale pure honey exists, and so does dark caramel-
coloured syrup honey). The feature extractor works on real photos. Replace `generate()` with features from your own
labelled photos before trusting the accuracy.
"""
import io
import json
import threading
from pathlib import Path

import joblib
import numpy as np
from PIL import Image, UnidentifiedImageError
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.model_selection import cross_val_score, train_test_split

BASE = Path(__file__).parent
MODEL_FILE = BASE / "ml" / "photo_model.joblib"
METRICS_FILE = BASE / "ml" / "photo_metrics.json"

FEATURES = ["red", "green", "blue", "hue", "saturation", "brightness", "brightness_spread",
            "foam", "cloudy", "texture"]
SIDE = 64                      # photos are shrunk to SIDE x SIDE before measuring
MIN_SIDE = 32                  # smaller than this carries too little information
ALLOWED = {"JPEG", "PNG", "WEBP"}
Image.MAX_IMAGE_PIXELS = 25_000_000   # refuse decompression bombs

MODEL = METRICS = None
_lock = threading.Lock()


class PhotoError(ValueError):
    pass


# ---------------------------------------------------------------- features
def _rgb_to_hsv(rgb):
    """rgb: float array (..., 3) in 0..1. Returns hue in degrees, saturation, value."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx, mn = rgb.max(axis=-1), rgb.min(axis=-1)
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > 1e-9
    rm = nz & (mx == r)
    gm = nz & (mx == g) & ~rm
    bm = nz & ~rm & ~gm
    h[rm] = (60 * ((g - b)[rm] / d[rm])) % 360
    h[gm] = 60 * ((b - r)[gm] / d[gm]) + 120
    h[bm] = 60 * ((r - g)[bm] / d[bm]) + 240
    s = np.where(mx > 1e-9, d / np.where(mx > 1e-9, mx, 1), 0)
    return h, s, mx


def features_from_array(rgb):
    """rgb: uint8 or float array (H, W, 3). Returns the 10 numbers in FEATURES order."""
    a = np.asarray(rgb, dtype=float)
    if a.max() > 1.5:
        a = a / 255.0
    h, w = a.shape[:2]
    # keep the middle 60 %: jar edges, lids and table are not honey
    y0, y1, x0, x1 = int(h * .2), int(h * .8), int(w * .2), int(w * .8)
    a = a[y0:y1, x0:x1]
    hue, sat, val = _rgb_to_hsv(a)
    gray = a.mean(axis=2)
    gy = np.abs(np.diff(gray, axis=0)).mean()
    gx = np.abs(np.diff(gray, axis=1)).mean()
    # hue is circular but honey sits far from the 0/360 seam, so a plain mean is fine
    return [float(a[..., 0].mean()), float(a[..., 1].mean()), float(a[..., 2].mean()),
            float(hue.mean()), float(sat.mean()), float(val.mean()), float(val.std()),
            float(((val > 0.9) & (sat < 0.4)).mean()),      # bright, washed-out patches: foam and bubbles
            float((sat < 0.35).mean()),                      # pale cloudy areas: crystals, dilution
            float((gx + gy) / 2)]                            # fine texture


def load_image(raw: bytes):
    """Validate an uploaded file and return an RGB uint8 array of SIDE x SIDE."""
    if not raw:
        raise PhotoError("Choose a photo first.")
    try:
        img = Image.open(io.BytesIO(raw))
        fmt = img.format
        img.verify()
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError, SyntaxError):
        raise PhotoError("That file is not a readable photo. Use a JPEG, PNG or WebP image.")
    if fmt not in ALLOWED:
        raise PhotoError("Use a JPEG, PNG or WebP photo.")
    if min(img.size) < MIN_SIDE:
        raise PhotoError(f"The photo is too small. Both sides must be at least {MIN_SIDE} pixels.")
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    img = img.convert("RGB").resize((SIDE, SIDE), Image.BILINEAR)
    return np.asarray(img, dtype=np.uint8)


def quality_warnings(f):
    """Plain-language reasons a photo may be unreliable."""
    d = dict(zip(FEATURES, f))
    out = []
    if d["brightness"] < 0.18:
        out.append("The photo is very dark. Retake it in daylight.")
    if d["brightness"] > 0.95:
        out.append("The photo is over-exposed. Retake it without a flash glare.")
    if d["saturation"] < 0.12:
        out.append("The photo has almost no colour. It may not show honey.")
    if d["brightness_spread"] < 0.004 and d["texture"] < 0.002:
        out.append("The photo is almost one flat colour. Make sure the honey fills the middle of the frame.")
    if not (0 <= d["hue"] <= 75) and d["saturation"] > 0.25:
        out.append("The main colour is not yellow-to-brown. This does not look like honey.")
    return out


# ---------------------------------------------------------------- synthetic images
def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def render(rng, kind):
    """One SIDE x SIDE synthetic jar-centre photo. kind: 'pure' or 'adulterated'."""
    if kind == "pure":
        if rng.random() < 0.22:      # pale honeys (acacia, clover) are legitimately light
            hue, sat, val = rng.uniform(38, 47), rng.uniform(.48, .70), rng.uniform(.68, .88)
        else:
            hue, sat, val = rng.uniform(25, 41), rng.uniform(.62, .92), rng.uniform(.42, .80)
        bubbles, haze, noise = rng.poisson(1.4), rng.uniform(0, .18), rng.uniform(.004, .014)
    else:
        if rng.random() < 0.28:      # caramel-coloured syrup honey: dark and saturated, looks "rich"
            hue, sat, val = rng.uniform(20, 31), rng.uniform(.74, .95), rng.uniform(.30, .52)
        else:                        # diluted or syrup-fed: lighter, flatter, more foam
            hue, sat, val = rng.uniform(33, 52), rng.uniform(.32, .72), rng.uniform(.55, .92)
        bubbles, haze, noise = rng.poisson(4.2), rng.uniform(.08, .42), rng.uniform(.008, .026)

    yy, xx = np.mgrid[0:SIDE, 0:SIDE] / SIDE
    light = 1 + rng.uniform(-.12, .12) * (xx - .5) + rng.uniform(-.12, .12) * (yy - .5)   # uneven lighting
    h = np.full((SIDE, SIDE), hue) + rng.normal(0, 1.2, (SIDE, SIDE))
    s = np.full((SIDE, SIDE), sat)
    v = np.full((SIDE, SIDE), val) * light
    cloud = haze * (0.5 + 0.5 * np.sin(xx * rng.uniform(3, 9) + yy * rng.uniform(3, 9) + rng.uniform(0, 6)))
    s = s * (1 - cloud)                      # haze washes colour out
    v = v + .25 * cloud
    for _ in range(bubbles):                 # round bright patches
        cx, cy, r = rng.uniform(.15, .85), rng.uniform(.15, .85), rng.uniform(.03, .08)
        m = ((xx - cx) ** 2 + (yy - cy) ** 2) < r * r
        s[m] *= .25
        v[m] = np.maximum(v[m], .95)
    v = v + rng.normal(0, noise, (SIDE, SIDE))
    s = np.clip(s, 0, 1)
    v = np.clip(v, 0, 1)
    # HSV -> RGB
    hp = (h % 360) / 60
    c = v * s
    x = c * (1 - np.abs(hp % 2 - 1))
    z = np.zeros_like(c)
    sector = hp.astype(int) % 6
    r = np.choose(sector, [c, x, z, z, x, c])
    g = np.choose(sector, [x, c, c, x, z, z])
    b = np.choose(sector, [z, z, x, c, c, x])
    m = v - c
    return np.clip(np.stack([r + m, g + m, b + m], axis=-1), 0, 1)


def generate(n_per_class=700, seed=21):
    rng = np.random.default_rng(seed)
    X, y = [], []
    for label, kind in enumerate(("pure", "adulterated")):
        for _ in range(n_per_class):
            X.append(features_from_array(render(rng, kind)))
            y.append(label)
    return np.array(X), np.array(y)


def sample_png(kind, seed=3):
    """A deterministic demo photo (PNG bytes) so the page and tests work without a camera."""
    rng = np.random.default_rng({"pure": 100, "adulterated": 200}[kind] + seed)
    arr = (render(rng, kind) * 255).round().astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).resize((256, 256), Image.BICUBIC).save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- model
def train():
    X, y = generate()
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, stratify=y, random_state=7)
    model = RandomForestClassifier(n_estimators=200, max_depth=8, min_samples_leaf=3, random_state=7, n_jobs=-1)
    model.fit(X_tr, y_tr)
    pred = model.predict(X_te)
    cv = cross_val_score(model, X, y, cv=5, scoring="f1")
    metrics = {
        "accuracy": round(float(accuracy_score(y_te, pred)), 4),
        "f1": round(float(f1_score(y_te, pred)), 4),
        "cv_f1_mean": round(float(cv.mean()), 4),
        "confusion_matrix": confusion_matrix(y_te, pred).tolist(),
        "feature_importance": {f: round(float(v), 4) for f, v in
                               sorted(zip(FEATURES, model.feature_importances_), key=lambda t: -t[1])},
        "n_samples": int(len(y)), "train_size": int(len(y_tr)), "test_size": int(len(y_te)),
        "source": "Synthetic jar photos rendered by the project", "synthetic": True,
    }
    return model, metrics


def _load():
    global MODEL, METRICS
    try:
        MODEL = joblib.load(MODEL_FILE)
        METRICS = json.loads(METRICS_FILE.read_text())
        return
    except Exception:
        pass
    MODEL, METRICS = train()
    try:
        MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(MODEL, MODEL_FILE)
        METRICS_FILE.write_text(json.dumps(METRICS, indent=2))
    except OSError:
        pass


def predict(raw: bytes):
    """Screen one uploaded photo. Returns a dict for the website."""
    arr = load_image(raw)
    f = features_from_array(arr)
    with _lock:
        p = float(MODEL.predict_proba(np.asarray(f).reshape(1, -1))[0][1])
    warnings = quality_warnings(f)
    if p >= 0.65:
        screen = "Looks suspicious"
    elif p <= 0.35:
        screen = "Looks normal"
    else:
        screen = "Unclear"
    mean = (np.array(f[:3]) * 255).round().astype(int)
    return dict(screen=screen, p_adulterated=round(p * 100, 1), warnings=warnings,
                reliable=not warnings, colour="#%02x%02x%02x" % tuple(mean),
                features={k: round(v, 3) for k, v in zip(FEATURES, f)})


_load()
