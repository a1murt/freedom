import sys, time
from cqa.db import init_db, SessionLocal
init_db()
from cqa.checklist.service import active_version
from cqa.checklist import service as svc
from cqa.models import ChecklistVersion, Job
from cqa.jobs.runner import start_evaluation_job, conversation_ids_for
from sqlalchemy import select
db = SessionLocal()
v1 = db.execute(select(ChecklistVersion).where(ChecklistVersion.version_no == 1)).scalars().first()
svc.activate(db, v1)
ids = conversation_ids_for(db, None, True)
db.close()
provider = sys.argv[1] if len(sys.argv) > 1 else "anthropic"
job_id = start_evaluation_job(ids, v1.id, provider=provider, model=None, mode="full", base_version_id=None, only_codes=None)
print("job", job_id, "conversations", len(ids), "provider", provider, flush=True)
while True:
    time.sleep(20)
    db = SessionLocal(); j = db.get(Job, job_id); db.close()
    print(time.strftime("%H:%M:%S"), j.status, f"done={j.done} failed={j.failed}/{j.total} cost=${j.cost_usd:.2f}", flush=True)
    if j.status in ("completed", "failed", "cancelled"):
        for l in (j.log or [])[-8:]: print("  ", l["level"], l["message"])
        break
