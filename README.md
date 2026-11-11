# HoneyChain

Honey quality check using machine learning, with batch records on a tamper-evident ledger
and an optional mirror to a Solidity smart contract.
Final build: **90% of the plan** (69% built and tested here, 21% written and waiting for one run on your machine). The last 10% is the real lab dataset, which needs measured honey samples. See `docs/REPORT.md`.

## Run the website

```bash
pip install -r requirements.txt
python seed.py        # optional: 8 demo batches
python app.py         # open http://127.0.0.1:5000
```

The first start trains the model (about 5 seconds) and saves it in `ml/`.
The old one-file version is frozen at the 50% stage in `legacy/` and does not have the new 75% features.

## Run the tests

```bash
python -m unittest discover -s tests      # 110 Python tests, no blockchain needed
python scripts/benchmark.py               # performance study, writes docs/performance.json
npm install && npm test                   # 15 smart contract tests (Hardhat)
```

## Log in (new)

Demo accounts, password `honey123` for all: `admin`, `lab`, `beekeeper`, `distributor`.
Reading the ledger, tracing a batch and scanning a QR code need no login. Writing does:
beekeeper = quality check; lab = check, spectrum, move batches, retrain; distributor = move batches; admin = everything.
Every new ledger block records who made it. With `HONEY_DEMO=0`, random passwords are generated and printed once in the terminal.

## New in the final build

| Part | Where |
| --- | --- |
| Photo screening model (colour and clarity of a jar photo; a screen, never a verdict) | `photo.py`, Photo page |
| End-to-end tests, helper-tool tests, performance study | `tests/test_e2e.py`, `tests/test_tools.py`, `scripts/benchmark.py` |
| Real-data checker and trainer | `ml/validate_real_data.py`, `docs/DATA_COLLECTION.md` |
| One-command live check of the contract mirror and IPFS | `scripts/live_check.py` |
| Project report | `docs/REPORT.md` |

## New in the 75%

| Part | Where |
| --- | --- |
| Login and roles, who-did-what on each block | `auth.py`, Login page |
| QR code per batch (pure Python, no package), print label on the batch page | `qr.py`, `/qr/<batch>.svg` |
| Full lab reports stored by hash and re-verified; optional IPFS pin (`IPFS_API=http://127.0.0.1:5001`) | `reportstore.py`, `/report/<hash>` |
| Spectral (NIR) second-opinion model, synthetic spectra | `spectral.py`, Spectrum page |

For QR codes to open on a phone, set `PUBLIC_URL` (for example `http://192.168.1.5:5000`) before `python app.py`.

## What was in the 50%

| Part | Where | Status |
| --- | --- | --- |
| Synthetic dataset, Random Forest model, grades, standard-limit checks | `ml/`, `predictor.py` | Done and tested |
| Website: checker, model page, ledger, batch trace, progress | `app.py`, `templates/`, `static/` | Done and tested |
| Hash-chained ledger and tamper demo | `ledger.py`, Ledger page | Done and tested |
| Dataset import and retrain (upload a CSV on the Model page, or `python ml/train.py --csv my.csv`) | `predictor.py`, Model page | Done and tested |
| Smart contract `HoneyTrace.sol` and 15 Hardhat tests | `contracts/`, `test/` | Written, **not yet run** |
| Deploy script and Web3 mirror to the contract | `scripts/deploy.js`, `web3_bridge.py` | Written, bridge logic tested with a fake contract, **not yet run against a real chain** |

## Connect the website to the smart contract

Use three terminals in the project folder.

```bash
# terminal 1: install, test, start a local blockchain (leave it running)
npm install
npm test                      # expect 15 passing
npx hardhat node              # prints 20 test accounts with private keys

# terminal 2: deploy and configure
npm run deploy:local          # expect: HoneyTrace deployed to 0x... on localhost
pip install web3
cp .env.example .env          # Windows: copy .env.example .env
# open .env and paste the "Private Key" of Account #0 from terminal 1 as PRIVATE_KEY
python app.py
```

The app reads `.env` by itself, so there is nothing to export in the shell.
You know it works when the header shows **Contract mirror on**, the Check page shows a
transaction hash under "Smart contract", and terminal 1 logs the `registerBatch` and `recordQuality` calls.

If it shows **Contract mirror off**, hover the chip or open the Ledger page: it says what is missing
(no `.env`, no deployment file, or the node is not running). The website keeps working either way.
Restarted the local node? Run `npm run deploy:local` again and run new checks. Old batches are not
on the new chain, so the app skips sending their stage changes (the local ledger still updates) and says
the contract's record does not match, instead of moving some other batch.
For Sepolia, set `SEPOLIA_RPC_URL` and use `npm run deploy:sepolia` with a test wallet only.

## Contract rules (what the tests check)

- Only authorized accounts can register batches or record results. Only the admin can authorize accounts.
- A verdict is recorded once and is final. A rejected batch cannot be re-tested as Pure.
- A batch cannot be marked Tested or moved on until a test is recorded.
- Stages only move forward, and an adulterated batch cannot move at all.
- Only the report hash is stored on-chain. `verifyReport` shows if a lab report was changed later.

## Still to do

Real lab dataset (needs measured samples: follow `docs/DATA_COLLECTION.md`). Run `npm test` and `python scripts/live_check.py` once on your machine; the IPFS pin is tested against a fake node only.

## Honest notes for the review

- The dataset is synthetic, built from published purity limits (Codex Alimentarius, BIS IS 4941).
  Accuracy figures are not real-world results until measured samples are used.
- The Solidity contract and Hardhat tests have not been compiled or run by the person who built this
  (the build environment could not reach the npm registry). Run `npm test` first and fix anything it reports.
- The tamper demo buttons are for presentations. Set `HONEY_DEMO=0` to hide and disable them.
- `.env` holds a private key. Use a test wallet only, and never share the file.

- Spectra are synthetic like the main dataset. The login is demo-grade access control, not a production identity system.
