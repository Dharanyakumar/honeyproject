"""Local hash-chained ledger used for the 25 % demo.

It mirrors the HoneyTrace smart contract (batches, stages, verdict, report hash)
so the website works without a running Ethereum node. In the next phase this
module is replaced by Web3.py calls to the deployed contract.
"""
import hashlib
import json
import threading
import time
from pathlib import Path

STAGES = ["Harvested", "Tested", "Processed", "Distributed", "Sold"]
LEDGER_FILE = Path(__file__).parent / "ledger.json"
BACKUP_FILE = Path(__file__).parent / "ledger.backup.json"
_lock = threading.Lock()


def _hash(block: dict) -> str:
    body = {k: v for k, v in block.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _load():
    if LEDGER_FILE.exists():
        return json.loads(LEDGER_FILE.read_text())
    return []


def _save(chain):
    LEDGER_FILE.write_text(json.dumps(chain, indent=2))


def _append(chain, data):
    prev = chain[-1]["hash"] if chain else "0" * 64
    block = {"index": len(chain), "timestamp": int(time.time()), "prev_hash": prev, "data": data}
    block["hash"] = _hash(block)
    chain.append(block)
    return block


def report_hash(report: dict) -> str:
    return "0x" + hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()


def add_test_record(batch_code, beekeeper, origin, report, verdict, confidence, actor=None, report_cid=None):
    """Register a batch and its ML test result as one block. actor = who logged it (username and role)."""
    with _lock:
        chain = _load()
        data = {
            "batch": batch_code, "beekeeper": beekeeper, "origin": origin,
            "stage": "Tested", "verdict": verdict, "confidence": confidence,
            "report_hash": report_hash(report),
        }
        if actor:
            data["by"] = actor
        if report_cid:
            data["report_cid"] = report_cid
        block = _append(chain, data)
        _save(chain)
        return block


def advance(batch_code, stage, note="", actor=None):
    with _lock:
        chain = _load()
        latest = batch_state(chain, batch_code)
        if not latest:
            raise ValueError("Batch not found")
        if latest["verdict"] == "Adulterated":
            raise ValueError("Adulterated batch cannot move through the supply chain")
        if STAGES.index(stage) <= STAGES.index(latest["stage"]):
            raise ValueError("Stage must move forward")
        data = {**latest, "stage": stage, "note": note}
        data.pop("by", None)
        if actor:
            data["by"] = actor
        block = _append(chain, data)
        _save(chain)
        return block


def batch_state(chain, batch_code):
    state = None
    for b in chain:
        if b["data"]["batch"] == batch_code:
            state = b["data"]
    return state


def batch_history(batch_code):
    return [b for b in _load() if b["data"]["batch"] == batch_code]


def all_blocks():
    return _load()


def verify_chain():
    chain = _load()
    for i, b in enumerate(chain):
        if b["hash"] != _hash(b):
            return False, i
        if i and b["prev_hash"] != chain[i - 1]["hash"]:
            return False, i
    return True, None


def tamper(index):
    """DEMO ONLY: secretly edit a stored block without fixing its hash, to show detection."""
    with _lock:
        chain = _load()
        if not 0 <= index < len(chain):
            raise ValueError("That block does not exist.")
        if not BACKUP_FILE.exists():
            BACKUP_FILE.write_text(LEDGER_FILE.read_text())
        data = chain[index]["data"]
        data["verdict"] = "Pure" if data["verdict"] == "Adulterated" else "Adulterated"
        data["confidence"] = 99.9
        _save(chain)  # hashes are not recomputed, so verification must fail


def has_backup():
    return BACKUP_FILE.exists()


def restore():
    """DEMO ONLY: undo tamper()."""
    with _lock:
        if not BACKUP_FILE.exists():
            raise ValueError("Nothing to restore.")
        LEDGER_FILE.write_text(BACKUP_FILE.read_text())
        BACKUP_FILE.unlink()


def next_batch_code():
    n = len({b["data"]["batch"] for b in _load()}) + 1
    return f"HNY-{time.strftime('%Y')}-{n:04d}"
