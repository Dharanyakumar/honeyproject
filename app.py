import json
import math
import os
from datetime import datetime, timedelta, timezone

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, session, url_for

import auth
import ledger
import photo
import predictor
import qr
import reportstore
import spectral
import web3_bridge

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # CSV uploads up to 2 MB
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SECRET_KEY"] = auth.secret_key()
DEMO_MODE = os.environ.get("HONEY_DEMO", "1") == "1"  # shows the tamper demo and demo logins
auth.init_users(DEMO_MODE)

ROADMAP = [
    # (name, status, weight %, description)
    # done = built and checked in the build environment
    # ready = written, but needs a run on a machine with npm, web3 or an IPFS node
    ("Problem study and standards", "done", 3, "Honey purity limits (Codex, BIS) mapped to input features."),
    ("Dataset and ML model", "done", 6, "Synthetic dataset, Random Forest, cross-validated."),
    ("Website UI and quality checker", "done", 8, "Checker, ledger explorer and batch trace pages."),
    ("Smart contract design", "done", 4, "HoneyTrace.sol: roles, batches, stages, events."),
    ("Local hash-chained ledger", "done", 4, "Tamper-evident ledger that mirrors the contract."),
    ("Tamper demo and automated tests", "done", 5, "Edit a block, watch verification fail, then restore."),
    ("Dataset import and retrain tool", "done", 5, "Upload a lab CSV, retrain the model, see new metrics."),
    ("Hardhat tests and deploy script", "ready", 7, "15 contract tests and a deploy script. Run: npm install, then npm test."),
    ("Web3 mirror to the deployed contract", "ready", 8, "Every result and stage change is also sent to the contract. Stale batch ids are caught first."),
    ("Login and role control", "done", 5, "Beekeeper, lab, distributor and admin accounts. Every block records who made it."),
    ("QR code per batch", "done", 6, "Each batch page has a printable QR label. Customers scan it to see the full history."),
    ("Spectral (NIR) model", "done", 10, "Second opinion from a 61-point near-infrared spectrum. Synthetic data."),
    ("Photo screening model", "done", 5, "Colour and clarity screen from a jar photo. A screen only, never a verdict. Synthetic training images."),
    ("Report storage (IPFS-ready)", "ready", 6, "Full lab reports saved by hash and re-verified. Pinning to a real IPFS node is written, not yet run."),
    ("End-to-end tests and performance study", "done", 5, "Full user journeys, ledger scaling, tamper-detection and noise-robustness study."),
    ("Project report", "done", 3, "Method, results, limits and the run-on-your-machine checklist (docs/REPORT.md)."),
    ("Real lab dataset", "next", 10, "Needs measured honey samples. Use docs/DATA_COLLECTION.md, then python ml/validate_real_data.py my.csv --train."),
]


def progress_numbers():
    verified = sum(r[2] for r in ROADMAP if r[1] == "done")
    written = sum(r[2] for r in ROADMAP if r[1] == "ready")
    return dict(total=verified + written, verified=verified, written=written)


IST = timezone(timedelta(hours=5, minutes=30))


@app.template_filter("dt")
def fmt_dt(ts):
    return datetime.fromtimestamp(ts, IST).strftime("%d %b %Y, %H:%M")


@app.template_filter("short")
def short_hash(h, n=8):
    h = h.replace("0x", "")
    return h[:n] + "…" + h[-4:]


def honeycomb(blocks, cols=4, rows=3, radius=44):
    """Pointy-top hex cells; each cell is one ledger block (empty cells stay outlined)."""
    w = math.sqrt(3) * radius
    cells = []
    for i in range(cols * rows):
        r, c = divmod(i, cols)
        cx = radius + c * w + (w / 2 if r % 2 else 0) + 4
        cy = radius + r * radius * 1.5 + 4
        pts = " ".join(
            f"{cx + (radius - 3) * math.cos(math.radians(30 + 60 * k)):.1f},"
            f"{cy + (radius - 3) * math.sin(math.radians(30 + 60 * k)):.1f}" for k in range(6))
        block = blocks[i] if i < len(blocks) else None
        cells.append(dict(points=pts, cx=cx, cy=cy, block=block, delay=i * 70))
    width = cols * w + w / 2 + 8
    height = (rows - 1) * radius * 1.5 + 2 * radius + 8
    return cells, round(width), round(height)


def stats():
    blocks = ledger.all_blocks()
    batches = {}
    for b in blocks:
        batches[b["data"]["batch"]] = b["data"]
    return dict(
        blocks=len(blocks),
        batches=len(batches),
        pure=sum(1 for d in batches.values() if d["verdict"] == "Pure"),
        rejected=sum(1 for d in batches.values() if d["verdict"] == "Adulterated"),
    )


@app.context_processor
def inject():
    return dict(nav_stats=stats(), user=auth.current_user(), can=auth.can, roles=auth.ROLES,
                progress_total=progress_numbers()["total"])


@app.before_request
def csrf_guard():
    return auth.same_origin_only()


def actor():
    u = auth.current_user()
    return {"user": u["username"], "role": u["role"]} if u else None


@app.get("/login")
def login():
    if auth.current_user():
        return redirect(auth.safe_next(request.args.get("next")))
    return render_template("login.html", next=request.args.get("next", ""), error=None,
                           demo=DEMO_MODE, demo_users=auth.DEMO_USERS, demo_password=auth.DEMO_PASSWORD)


@app.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip().lower()
    key = (request.remote_addr, username)
    error = None
    if auth.locked(key):
        error = "Too many wrong attempts. Wait a minute and try again."
    else:
        found = auth.authenticate(username, request.form.get("password", ""))
        if found:
            auth.clear_failures(key)
            session.clear()
            session["user"] = found
            return redirect(auth.safe_next(request.form.get("next")))
        auth.note_failure(key)
        error = "Wrong username or password."
    return render_template("login.html", next=request.form.get("next", ""), error=error, demo=DEMO_MODE,
                           demo_users=auth.DEMO_USERS, demo_password=auth.DEMO_PASSWORD), 401


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.errorhandler(403)
def forbidden(_):
    return render_template("403.html"), 403


@app.get("/")
def index():
    blocks = ledger.all_blocks()[-12:]
    cells, cw, ch = honeycomb(blocks)
    return render_template("index.html", metrics=predictor.METRICS, cells=cells, cw=cw, ch=ch,
                           progress=progress_numbers()["total"])


@app.get("/check")
@auth.require("check", api=False)
def check():
    return render_template("check.html", fields=predictor.FIELDS, samples=predictor.SAMPLES)


@app.post("/api/predict")
@auth.require("check")
def api_predict():
    payload = request.get_json(silent=True) or {}
    try:
        values = predictor.parse(payload.get("values", {}))
    except predictor.ValidationError as e:
        return jsonify(error=str(e)), 400
    beekeeper = (payload.get("beekeeper") or "").strip()[:60] or "Unnamed beekeeper"
    origin = (payload.get("origin") or "").strip()[:60] or "Unknown origin"

    result = predictor.predict(values)
    code = ledger.next_batch_code()
    report = {"batch": code, "values": values, "result": {k: result[k] for k in ("verdict", "grade", "confidence")}}
    stored_hash = reportstore.save(report)
    cid, ipfs_message = reportstore.pin_to_ipfs(report)
    block = ledger.add_test_record(code, beekeeper, origin, report, result["verdict"], result["confidence"],
                                   actor=actor(), report_cid=cid)
    assert block["data"]["report_hash"] == stored_hash
    chain = web3_bridge.mirror_test(code, beekeeper, origin, result["verdict"], result["confidence"],
                                    block["data"]["report_hash"])
    return jsonify(result=result, batch=code, block=block, chain=chain, ipfs=dict(cid=cid, message=ipfs_message))


@app.get("/ledger")
def ledger_page():
    blocks = ledger.all_blocks()
    ok, bad_index = ledger.verify_chain()
    return render_template("ledger.html", blocks=list(reversed(blocks)), ok=ok, bad_index=bad_index,
                           demo=DEMO_MODE, has_backup=ledger.has_backup(), count=len(blocks))


@app.get("/api/verify")
def api_verify():
    ok, bad = ledger.verify_chain()
    return jsonify(valid=ok, broken_at=bad, blocks=len(ledger.all_blocks()))


def trace_url(code):
    base = os.environ.get("PUBLIC_URL", "").rstrip("/") or request.url_root.rstrip("/")
    return f"{base}/trace/{code}"


@app.get("/qr/<code>.svg")
def qr_svg(code):
    if not ledger.batch_history(code):
        abort(404)
    return Response(qr.to_svg(trace_url(code)), mimetype="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@app.get("/report/<h>")
def report_page(h):
    report, status = reportstore.load(h)
    if status == "invalid":
        abort(404)
    digest = reportstore.normalize(h)
    batch_code = report.get("batch") if isinstance(report, dict) else None
    on_ledger = any(b["data"]["report_hash"].replace("0x", "") == digest for b in ledger.all_blocks())
    return render_template("report.html", report=report, status=status, digest=digest, on_ledger=on_ledger,
                           batch_code=batch_code,
                           pretty=json.dumps(report, indent=2, sort_keys=True) if report is not None else "")


@app.get("/api/report/<h>")
def api_report(h):
    report, status = reportstore.load(h)
    if status == "invalid":
        return jsonify(error="That is not a report hash."), 400
    return jsonify(status=status, report=report), (404 if status == "missing" else 200)


@app.get("/spectrum")
@auth.require("spectrum", api=False)
def spectrum_page():
    return render_template("spectrum.html", metrics=spectral.METRICS, samples=spectral.SAMPLES,
                           wavelengths=[int(w) for w in spectral.WAVELENGTHS])


@app.post("/api/spectrum")
@auth.require("spectrum")
def api_spectrum():
    payload = request.get_json(silent=True) or {}
    try:
        arr = spectral.parse(payload.get("spectrum"))
    except spectral.SpectrumError as e:
        return jsonify(error=str(e)), 400
    return jsonify(result=spectral.predict(arr))


@app.get("/photo")
@auth.require("photo", api=False)
def photo_page():
    return render_template("photo.html", metrics=photo.METRICS, max_mb=app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024))


@app.post("/api/photo")
@auth.require("photo")
def api_photo():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify(error="Choose a photo first."), 400
    try:
        return jsonify(result=photo.predict(upload.read()))
    except photo.PhotoError as e:
        return jsonify(error=str(e)), 400


@app.get("/photo/sample/<kind>.png")
def photo_sample(kind):
    if kind not in ("pure", "adulterated"):
        abort(404)
    return Response(photo.sample_png(kind), mimetype="image/png", headers={"Cache-Control": "no-store"})


@app.get("/trace/<code>")
def trace(code):
    history = ledger.batch_history(code)
    if not history:
        abort(404)
    state = history[-1]["data"]
    _, report_status = reportstore.load(state["report_hash"])
    return render_template("trace.html", code=code, history=history, state=state, stages=ledger.STAGES,
                           trace_url=trace_url(code), report_status=report_status)


@app.post("/api/advance/<code>")
@auth.require("advance")
def api_advance(code):
    payload = request.get_json(silent=True) or {}
    stage = payload.get("stage")
    if stage not in ledger.STAGES:
        return jsonify(error="Choose a valid stage."), 400
    note = (payload.get("note") or "")[:120]
    try:
        block = ledger.advance(code, stage, note, actor=actor())
    except ValueError as e:
        return jsonify(error=str(e)), 400
    chain = web3_bridge.mirror_stage(code, stage, note, block["data"]["report_hash"])
    return jsonify(block=block, chain=chain)


@app.get("/api/chain")
def api_chain():
    enabled, message = web3_bridge.chain_status()
    return jsonify(enabled=enabled, message=message)


@app.post("/api/tamper")
@auth.require("tamper")
def api_tamper():
    if not DEMO_MODE:
        abort(404)
    try:
        index = int((request.get_json(silent=True) or {}).get("index"))
        ledger.tamper(index)
    except (TypeError, ValueError) as e:
        return jsonify(error=str(e) if str(e) else "Choose a block number."), 400
    ok, bad = ledger.verify_chain()
    return jsonify(valid=ok, broken_at=bad)


@app.post("/api/restore")
@auth.require("tamper")
def api_restore():
    if not DEMO_MODE:
        abort(404)
    try:
        ledger.restore()
    except ValueError as e:
        return jsonify(error=str(e)), 400
    ok, bad = ledger.verify_chain()
    return jsonify(valid=ok, broken_at=bad)


@app.get("/model")
def model_page():
    return render_template("model.html", metrics=predictor.METRICS, features=predictor.FEATURES,
                           min_rows=predictor.MIN_ROWS, min_class=predictor.MIN_PER_CLASS)


@app.post("/api/retrain")
@auth.require("retrain")
def api_retrain():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify(error="Choose a CSV file first."), 400
    try:
        df, dropped = predictor.parse_dataset(upload.read())
    except predictor.ValidationError as e:
        return jsonify(error=str(e)), 400
    metrics = predictor.retrain(df, f"Uploaded: {os.path.basename(upload.filename)[:60]}")
    return jsonify(metrics=metrics, rows=len(df), dropped=dropped)


@app.get("/model/template.csv")
def model_template():
    rows = [",".join(predictor.FEATURES + ["label"])]
    for label, sample in (("Pure", predictor.SAMPLES["pure"]), ("Adulterated", predictor.SAMPLES["adulterated"])):
        rows.append(",".join([str(sample[f]) for f in predictor.FEATURES] + [label]))
    return Response("\n".join(rows) + "\n", mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=honey_dataset_template.csv"})


@app.get("/roadmap")
def roadmap():
    return render_template("roadmap.html", items=ROADMAP, progress=progress_numbers())


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="That file is larger than 2 MB."), 413


@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(debug=True, port=5000)
