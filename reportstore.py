"""Off-chain storage for full lab reports.

The ledger (and the smart contract) keep only the SHA-256 hash of a report. The report itself is saved
here as a file named after that hash (content addressing, the same idea IPFS uses). Anyone can then
re-hash the stored file and compare it with the hash on the ledger, so a changed report is caught.

If an IPFS node is running, set IPFS_API (for example http://127.0.0.1:5001) and each report is also
pinned there and its CID is returned. Without it everything still works from the local folder.
"""
import hashlib
import json
import os
import re
import urllib.request
import uuid
from pathlib import Path

REPORT_DIR = Path(__file__).parent / "reports"
HASH_RE = re.compile(r"^(?:0x)?([0-9a-f]{64})$")


def canonical(report: dict) -> bytes:
    """The exact bytes that are hashed and stored. Must match ledger.report_hash."""
    return json.dumps(report, sort_keys=True).encode()


def normalize(h):
    m = HASH_RE.match((h or "").lower())
    return m.group(1) if m else None


def save(report: dict) -> str:
    """Store the report and return its hash as 0x...; saving the same report twice is harmless."""
    raw = canonical(report)
    digest = hashlib.sha256(raw).hexdigest()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{digest}.json"
    if not path.exists():
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(raw)
        tmp.replace(path)
    return "0x" + digest


def load(h):
    """Return (report or None, status). Status: 'verified', 'tampered', 'missing' or 'invalid'."""
    digest = normalize(h)
    if not digest:
        return None, "invalid"
    path = REPORT_DIR / f"{digest}.json"
    if not path.exists():
        return None, "missing"
    raw = path.read_bytes()
    try:
        report = json.loads(raw)
    except ValueError:
        return None, "tampered"
    if hashlib.sha256(raw).hexdigest() != digest:
        return report, "tampered"
    return report, "verified"


def pin_to_ipfs(report: dict):
    """Pin the report to a local IPFS node if IPFS_API is set. Returns (cid or None, message)."""
    api = os.environ.get("IPFS_API", "").rstrip("/")
    if not api:
        return None, "IPFS not configured (set IPFS_API to pin reports)."
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"report.json\"\r\n"
            f"Content-Type: application/json\r\n\r\n").encode() + canonical(report) + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{api}/api/v0/add?pin=true", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            cid = json.loads(resp.read().decode())["Hash"]
        return cid, "Pinned to IPFS."
    except Exception as e:  # the website must keep working when the node is down
        return None, f"IPFS pin failed: {type(e).__name__}."
