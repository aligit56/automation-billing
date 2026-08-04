import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config import config
from models import Base
from orchestrator import AttendanceVerificationOrchestrator
from snapshot_engine import ImmutableSnapshotEngine

# Set up clean logging for the demo
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
    datefmt='%H:%M:%S'
)

def run_demo():
    print("\n" + "="*85)
    print(" AXIAN x nGAGE ATTENDANCE VERIFICATION & BILLING PIPELINE ")
    print("="*85 + "\n")

    # 1. Setup in-memory DB for demo
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    orchestrator = AttendanceVerificationOrchestrator(db, config, mock_api=True)
    
    print("\n--- STEP 1: SEEDING MASTER HR DATA ---")
    orchestrator.seed_initial_master_data("2026-08")

    print("\n--- STEP 2: RUNNING END-TO-END PIPELINE ---")
    result = orchestrator.run_month_end_pipeline("2026-08", auto_resolve_demo=True)

    print("\n--- PIPELINE SUMMARY ---")
    print(f"Total Records Ingested : {result['total_ingested']}")
    print(f"Discrepancies Flagged  : {result['discrepancies_flagged']}")
    print(f"Exceptions Queued      : {result['exceptions_queued']}")
    print(f"Approved for Billing   : {result['approved_headcount_snapshotted']}")
    
    if result["snapshot"]:
        print(f"\n--- STEP 3: IMMUTABLE SNAPSHOT GENERATED ---")
        print(f"Snapshot ID : {result['snapshot']['snapshot_id']}")
        print(f"SHA-256 Hash: {result['snapshot']['snapshot_hash']}")
        print(f"HMAC Sig    : {result['snapshot']['hmac_signature']}")

    print("\n--- STEP 4: TAMPER DETECTION SECURITY CHECK ---")
    print("Simulating malicious database tampering (Changing billable days to 999.0)...")
    
    # Intentionally corrupt the database to show tamper detection
    snapshot_engine = ImmutableSnapshotEngine(db, config.snapshot)
    
    # We need to load the actual snapshot object from DB to modify it
    from models import BillingSnapshot
    db_snapshot = db.query(BillingSnapshot).filter(BillingSnapshot.snapshot_id == result['snapshot']['snapshot_id']).first()
    
    import copy
    tampered_payload = copy.deepcopy(db_snapshot.snapshot_payload)
    tampered_payload["summary"]["total_billable_days"] = 999.0
    db_snapshot.snapshot_payload = tampered_payload
    db.commit()

    print("Re-verifying snapshot integrity...")
    is_valid, msg = snapshot_engine.verify_snapshot_integrity(db_snapshot.snapshot_id)
    
    print("\n" + "="*85)
    print(" DEMO COMPLETE")
    print("="*85 + "\n")

if __name__ == "__main__":
    run_demo()
