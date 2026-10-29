"""Run local acceptance checks and record only results that actually completed."""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
suite=unittest.defaultTestLoader.discover(str(ROOT/"tests"))
result=unittest.TextTestRunner(verbosity=1).run(suite)
report={"at":datetime.now(timezone.utc).isoformat(timespec="seconds"),"passed":result.testsRun-len(result.failures)-len(result.errors),
        "total":result.testsRun,"live_ai":False,"n8n_e2e":False,
        "summary":"Local acceptance checks for approvals, recovery, sanitized photos, exact quote totals, customer decisions, staff ownership, overdue alerts, approved answers and Gmail/HubSpot adapter contracts. Provider checks use simulated responses; external accounts remain unverified."}
path=ROOT/"docs"/"verification.json"
if path.exists():
    previous=json.loads(path.read_text(encoding="utf-8"))
    if previous.get("n8n_e2e"):
        report.update({k:v for k,v in previous.items() if k.startswith('n8n_')})
if result.wasSuccessful():
    path.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
sys.exit(0 if result.wasSuccessful() else 1)
