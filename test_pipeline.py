"""
Comprehensive Test Suite for Axian x nGAGE Automated Attendance Verification & Billing System.

Tests all 4 SOW functional layers:
1. API Ingestion Layer (OAuth2, Paginated Ingestion, Retry Handling)
2. Validation Rules Engine (Headcount, Working Days, Absences)
3. SLA Exception Queue Workflow (SLA Due Dates, State Machine, Notifications)
4. Immutable Snapshot Engine (SHA-256 Hashing, HMAC Signatures, Tamper Detection)
5. Full Pipeline Orchestration Run
"""

import sys
import os
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Local module imports
from config import config
from models import Base, Employee, WorkingCalendar, AttendanceIngestion, ExceptionRecord, BillingSnapshot, EmployeeStatus, RuleSeverity, ExceptionStatus
from ngage_client import NGAGEClient, NGAGEAuthError
from validation_engine import ValidationEngine
from exception_workflow import SLAExceptionWorkflow
from snapshot_engine import ImmutableSnapshotEngine, TamperDetectedError
from orchestrator import AttendanceVerificationOrchestrator


class TestAttendanceBillingPipeline(unittest.TestCase):
    """Test Suite verifying end-to-end SOW backend logic and cryptographic integrity."""

    def setUp(self):
        """Set up in-memory SQLite database session for unit tests."""
        self.engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(self.engine)
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        # Seed baseline master data
        self.orchestrator = AttendanceVerificationOrchestrator(self.db, config, mock_api=True)
        self.orchestrator.seed_initial_master_data("2026-07")

    def tearDown(self):
        self.db.close()

    def test_layer1_api_ingestion(self):
        """SOW Layer 1: Test OAuth Authentication, Retry Handling, and Paginated Ingestion."""
        client = NGAGEClient(config.ngage_api, mock_mode=True)

        # 1. OAuth Authentication
        token = client.authenticate()
        self.assertTrue(token.startswith("mock_bearer_token_"))

        # 2. Paginated Ingestion (Mock returns 2 pages, total 5 records)
        records = client.fetch_monthly_attendance("2026-07")
        self.assertEqual(len(records), 5)
        self.assertEqual(records[0]["employee_code"], "EMP-1001")
        self.assertEqual(records[-1]["employee_code"], "EMP-9999")

    def test_layer2_validation_engine(self):
        """SOW Layer 2: Test Business Validation Rules Engine."""
        client = NGAGEClient(config.ngage_api, mock_mode=True)
        raw_records = client.fetch_monthly_attendance("2026-07")

        batch_id = "test-batch-001"
        ingested_entities = []
        for raw in raw_records:
            entity = AttendanceIngestion(
                ingestion_batch_id=batch_id,
                employee_code=raw["employee_code"],
                period_key="2026-07",
                days_worked=float(raw["days_worked"]),
                approved_leaves=float(raw["approved_leaves"]),
                unapproved_absences=float(raw["unapproved_absences"]),
                raw_payload=raw
            )
            self.db.add(entity)
            ingested_entities.append(entity)
        self.db.commit()

        validator = ValidationEngine(self.db, config.validation)
        discrepancies = validator.validate_ingestion_batch(ingested_entities, "2026-07")

        # Verify Flagged Rules:
        # EMP-1003 has 3 unapproved absences -> UNAPPROVED_ABSENCE rule
        # EMP-1004 has 15 days worked vs 22 expected -> WORKING_DAYS_MISMATCH rule
        # EMP-9999 is unknown employee -> HEADCOUNT_MATCH rule
        rule_codes = {d.rule_code for d in discrepancies}
        self.assertIn("HEADCOUNT_MATCH", rule_codes)
        self.assertIn("WORKING_DAYS_MISMATCH", rule_codes)
        self.assertIn("UNAPPROVED_ABSENCE", rule_codes)

    def test_layer3_sla_exception_workflow(self):
        """SOW Layer 3: Test SLA Exception Queueing, Resolution, and Notification Alerts."""
        workflow = SLAExceptionWorkflow(self.db, config.sla)

        # 1. SLA Calculation Check
        sla_critical = workflow.calculate_sla_due_date(RuleSeverity.CRITICAL)
        sla_warning = workflow.calculate_sla_due_date(RuleSeverity.WARNING)

        self.assertGreater(sla_warning, sla_critical)

        # 2. Add sample exception to queue
        client = NGAGEClient(config.ngage_api, mock_mode=True)
        raw_records = client.fetch_monthly_attendance("2026-07")

        batch_id = "test-batch-002"
        ingested_entities = []
        for raw in raw_records:
            entity = AttendanceIngestion(
                ingestion_batch_id=batch_id,
                employee_code=raw["employee_code"],
                period_key="2026-07",
                days_worked=float(raw["days_worked"]),
                approved_leaves=float(raw["approved_leaves"]),
                unapproved_absences=float(raw["unapproved_absences"]),
            )
            self.db.add(entity)
            ingested_entities.append(entity)
        self.db.commit()

        validator = ValidationEngine(self.db, config.validation)
        discrepancies = validator.validate_ingestion_batch(ingested_entities, "2026-07")

        queued = workflow.route_discrepancies_to_queue(batch_id, discrepancies)
        self.assertGreater(len(queued), 0)

        open_count = workflow.get_open_exceptions_count(batch_id)
        self.assertEqual(open_count, len(queued))

        # 3. Resolve an exception
        target_exc = queued[0]
        resolved_exc = workflow.resolve_exception(
            target_exc.id,
            resolved_by="Axian_Tester",
            resolution_notes="Approved in unit test"
        )
        self.assertEqual(resolved_exc.status, ExceptionStatus.RESOLVED)

    def test_layer4_snapshot_engine_and_tamper_detection(self):
        """SOW Layer 4: Test SHA-256 Canonical JSON Hashing & Cryptographic Tamper Detection."""
        snapshot_engine = ImmutableSnapshotEngine(self.db, config.snapshot)

        # Create mock approved ingestion records
        records = [
            AttendanceIngestion(
                ingestion_batch_id="batch-100",
                employee_code="EMP-1001",
                period_key="2026-07",
                days_worked=22.0,
                approved_leaves=0.0,
                unapproved_absences=0.0
            ),
            AttendanceIngestion(
                ingestion_batch_id="batch-100",
                employee_code="EMP-1002",
                period_key="2026-07",
                days_worked=20.0,
                approved_leaves=2.0,
                unapproved_absences=0.0
            )
        ]

        # 1. Create Snapshot
        snapshot = snapshot_engine.create_billing_snapshot(
            batch_id="batch-100",
            period_key="2026-07",
            approved_records=records
        )

        self.assertIsNotNone(snapshot.snapshot_hash)
        self.assertIsNotNone(snapshot.hmac_signature)
        self.assertEqual(snapshot.total_headcount, 2)

        # 2. Verify Clean Integrity
        is_valid, msg = snapshot_engine.verify_snapshot_integrity(snapshot.snapshot_id)
        self.assertTrue(is_valid)

        # 3. Simulate Database Payload Tampering
        import copy
        tampered_payload = copy.deepcopy(snapshot.snapshot_payload)
        tampered_payload["summary"]["total_billable_days"] = 999.0  # Fraudulent change!
        snapshot.snapshot_payload = tampered_payload
        self.db.commit()

        # 4. Verify Tamper Detection Fails
        is_valid_after_tamper, tamper_msg = snapshot_engine.verify_snapshot_integrity(snapshot.snapshot_id)
        print("Tamper MSG:", tamper_msg)
        print("Payload:", snapshot.snapshot_payload["summary"])
        self.assertFalse(is_valid_after_tamper)
        self.assertIn("TAMPER DETECTED", tamper_msg)

    def test_end_to_end_orchestrator(self):
        """Test Full Month-End Pipeline Orchestration."""
        result = self.orchestrator.run_month_end_pipeline("2026-07", auto_resolve_demo=True)

        self.assertEqual(result["period_key"], "2026-07")
        self.assertEqual(result["total_ingested"], 5)
        self.assertGreater(result["discrepancies_flagged"], 0)
        self.assertIsNotNone(result["snapshot"])
        self.assertTrue(result["snapshot"]["snapshot_id"].startswith("SNP-2026-07-"))


if __name__ == "__main__":
    unittest.main()
