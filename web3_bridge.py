"""Optional bridge: mirror ledger records to the deployed HoneyTrace contract.

The local ledger stays the source the website reads from. When RPC_URL and
PRIVATE_KEY are set and a deployment file exists, every test result and stage
change is also sent to the smart contract, and the transaction hash is returned.
If anything is missing or fails, the website keeps working and reports why.

RPC_URL and PRIVATE_KEY can come from the shell or from a `.env` file next to this
file (copy `.env.example`). Needs `pip install web3` only when the bridge is used.
"""
import json
import os
import threading
from pathlib import Path

_STAGES = ["Harvested", "Tested", "Processed", "Distributed", "Sold"]
_VERDICT = {"Pure": 1, "Adulterated": 2}
# One transaction sequence at a time. Without this, two checks at the same moment can
# reuse a nonce or read each other's batch id from the contract.
_chain_lock = threading.Lock()


def _here():
    return Path(__file__).resolve().parent


def _env_file():
    return _here() / ".env"


def _load_env():
    """Read KEY=VALUE lines from .env. Variables already set in the shell win."""
    f = _env_file()
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def _deploy_file():
    return Path(os.environ.get("HONEY_DEPLOYMENT", str(_here() / "deployments" / "localhost.json")))


def _map_file():
    return _here() / "chain_map.json"


def chain_status():
    """Return (enabled, message) for the website."""
    _load_env()
    if not os.environ.get("RPC_URL") or not os.environ.get("PRIVATE_KEY"):
        return False, "Blockchain mirror is off. Set RPC_URL and PRIVATE_KEY (in .env) to turn it on."
    if not _deploy_file().exists():
        return False, "No deployment file found. Deploy the contract first (npm run deploy:local)."
    try:
        address = json.loads(_deploy_file().read_text()).get("address")
    except ValueError:
        return False, "The deployment file is damaged. Deploy the contract again (npm run deploy:local)."
    return True, "Mirroring to the smart contract" + (f" at {address}." if address else ".")


def _connect():
    from web3 import Web3  # imported here so the app runs without web3 installed
    dep = json.loads(_deploy_file().read_text())
    w3 = Web3(Web3.HTTPProvider(os.environ["RPC_URL"]))
    account = w3.eth.account.from_key(os.environ["PRIVATE_KEY"])
    contract = w3.eth.contract(address=dep["address"], abi=dep["abi"])
    return w3, account, contract


def _send(w3, account, call):
    tx = call.build_transaction({"from": account.address,
                                 "nonce": w3.eth.get_transaction_count(account.address)})
    signed = account.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction  # web3 v7 / v6
    receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw), timeout=120)
    if receipt.status != 1:
        raise RuntimeError("Transaction reverted.")
    return w3.to_hex(receipt.transactionHash)


def _load_map():
    f = _map_file()
    return json.loads(f.read_text()) if f.exists() else {}


def _hash_bytes(report_hash):
    return bytes.fromhex(report_hash.replace("0x", ""))


def _matches_chain(contract, chain_id, report_hash):
    """True if the contract holds the same report hash for this batch as the local ledger."""
    try:
        return bool(contract.functions.verifyReport(chain_id, _hash_bytes(report_hash)).call())
    except Exception as e:
        if "Batch not found" in str(e):
            return False
        raise  # a network problem is not a mismatch


def mirror_test(code, beekeeper, origin, verdict, confidence, report_hash):
    enabled, msg = chain_status()
    if not enabled:
        return {"enabled": False, "message": msg}
    with _chain_lock:
        try:
            w3, account, contract = _connect()
            _send(w3, account, contract.functions.registerBatch(beekeeper, origin))
            chain_id = contract.functions.batchCount().call()
            tx = _send(w3, account, contract.functions.recordQuality(
                chain_id, _VERDICT[verdict], int(round(confidence * 100)), _hash_bytes(report_hash)))
            mapping = _load_map()
            mapping[code] = chain_id
            _map_file().write_text(json.dumps(mapping, indent=2))
            return {"enabled": True, "ok": True, "tx": tx, "chain_id": chain_id,
                    "message": "Recorded on the smart contract."}
        except Exception as e:  # keep the website working whatever happens on the chain side
            return {"enabled": True, "ok": False, "message": f"Not mirrored: {e}"}


def mirror_stage(code, stage, note="", report_hash=None):
    """Send a stage change. Pass the batch's report hash so a stale id (for example after the
    local blockchain was restarted) is caught instead of moving some other batch."""
    enabled, msg = chain_status()
    if not enabled:
        return {"enabled": False, "message": msg}
    chain_id = _load_map().get(code)
    if chain_id is None:
        return {"enabled": True, "ok": False, "message": "Not mirrored: this batch was never sent to the contract."}
    with _chain_lock:
        try:
            w3, account, contract = _connect()
            if report_hash is not None and not _matches_chain(contract, chain_id, report_hash):
                return {"enabled": True, "ok": False,
                        "message": "Not mirrored: the contract's record for this batch does not match the "
                                   "local ledger. If you restarted the local blockchain, run a new check "
                                   "to register batches again."}
            tx = _send(w3, account, contract.functions.advanceStage(chain_id, _STAGES.index(stage), note))
            return {"enabled": True, "ok": True, "tx": tx, "chain_id": chain_id,
                    "message": "Recorded on the smart contract."}
        except Exception as e:
            return {"enabled": True, "ok": False, "message": f"Not mirrored: {e}"}
