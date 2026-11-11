"""Fill the ledger with demo batches so the website has data for the review."""
import random

import pandas as pd

import ledger
import predictor
import reportstore

random.seed(11)
ledger.LEDGER_FILE.unlink(missing_ok=True)
df = pd.read_csv("ml/honey_dataset.csv")

DEMO = [
    ("Muthu Apiaries", "Nilgiris, Tamil Nadu", "Pure", "Sold"),
    ("Kovai Hive Co-op", "Coimbatore, Tamil Nadu", "Pure", "Distributed"),
    ("Valparai Bee Farm", "Valparai, Tamil Nadu", "Pure", "Processed"),
    ("Anamalai Honey Works", "Anamalai, Tamil Nadu", "Adulterated", None),
    ("Sathy Forest Collective", "Sathyamangalam, Tamil Nadu", "Pure", "Tested"),
    ("Yercaud Bee Keepers", "Yercaud, Tamil Nadu", "Pure", "Distributed"),
    ("Kodai Hill Honey", "Kodaikanal, Tamil Nadu", "Adulterated", None),
    ("Palani Apiary", "Palani, Tamil Nadu", "Pure", "Tested"),
]
NOTES = {"Processed": "Filtered and bottled", "Distributed": "Dispatched to retail partner",
         "Sold": "Sold at retail counter"}

for name, origin, label, final in DEMO:
    row = df[df.label == label].sample(1, random_state=random.randint(0, 9999)).iloc[0]
    values = {k: float(row[k]) for k in predictor.FEATURES}
    result = predictor.predict(values)
    code = ledger.next_batch_code()
    report = {"batch": code, "values": values,
              "result": {k: result[k] for k in ("verdict", "grade", "confidence")}}
    reportstore.save(report)
    ledger.add_test_record(code, name, origin, report, result["verdict"], result["confidence"],
                           actor={"user": "lab", "role": "lab"})
    if final and result["verdict"] == "Pure":
        for stage in ledger.STAGES[2:ledger.STAGES.index(final) + 1]:
            ledger.advance(code, stage, NOTES[stage], actor={"user": "distributor", "role": "distributor"})
print("Seeded", len(ledger.all_blocks()), "blocks")
