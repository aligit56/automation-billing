import unittest
import sys
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Local module imports
from config import config
from models import Base, Employee, WorkingCalendar, AttendanceIngestion, ExceptionRecord, BillingSnapshot, EmployeeStatus, RuleSeverity, ExceptionStatus
from orchestrator import AttendanceVerificationOrchestrator
from validation_engine import ValidationEngine
from exception_workflow import SLAExceptionWorkflow
from snapshot_engine import ImmutableSnapshotEngine

# Disable verbose application logs to keep the test runner output clean
logging.getLogger("ValidationEngine").setLevel(logging.CRITICAL)
logging.getLogger("SnapshotEngine").setLevel(logging.CRITICAL)
logging.getLogger("ExceptionWorkflow").setLevel(logging.CRITICAL)
logging.getLogger("PipelineOrchestrator").setLevel(logging.CRITICAL)
logging.getLogger("nGAGEClient").setLevel(logging.CRITICAL)


class AutomationTestRunner(unittest.TestCase):
    """Comprehensive test suite for Attendance Verification & Billing System."""

    def setUp(self):
        """Set up in-memory database and seed master data before each test."""
        self.engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(self.engine)
        SessionLocal = sessionmaker(bind=self.engine)
        self.db = SessionLocal()

        self.orchestrator = AttendanceVerificationOrchestrator(self.db, config, mock_api=True)
        # Seed master data (Employees, Calendars) for period "2026-08"
        self.orchestrator.seed_initial_master_data("2026-08")

    def tearDown(self):
        """Clean up the database session after each test."""
        self.db.close()

    def test_01_schema_and_types(self):
        """Data Types & Schema Validation against models.py"""
        emp = self.db.query(Employee).first()
        self.assertIsInstance(emp.employee_code, str, "Employee code should be a string")
        self.assertTrue(hasattr(emp, "status"), "Employee must have a status field")

        cal = self.db.query(WorkingCalendar).first()
        self.assertIsInstance(cal.expected_working_days, int, "Expected working days must be integer")
        self.assertEqual(cal.location_code, "US-MAIN")

    def test_02_scenario_a_perfect_data(self):
        """Scenario A: Perfect data match (0 exceptions generated, verified snapshot successfully created)"""
        batch_id = "batch-A"
        # US-MAIN expected working days is 22 for this mocked period
        records = [
            AttendanceIngestion(ingestion_batch_id=batch_id, employee_code="EMP-1001", period_key="2026-08", days_worked=22.0, approved_leaves=0.0, unapproved_absences=0.0),
            AttendanceIngestion(ingestion_batch_id=batch_id, employee_code="EMP-1002", period_key="2026-08", days_worked=22.0, approved_leaves=0.0, unapproved_absences=0.0)
        ]
        self.db.add_all(records)
        self.db.commit()

        # Run Validation Layer
        validator = ValidationEngine(self.db, config.validation)
        discrepancies = validator.validate_ingestion_batch(records, "2026-08")
        self.assertEqual(len(discrepancies), 0, "Perfect data should yield 0 discrepancies")

        # Run Snapshot Engine (Layer 4) directly since records are clean
        snapshot_engine = ImmutableSnapshotEngine(self.db, config.snapshot)
        snapshot = snapshot_engine.create_billing_snapshot(batch_id, "2026-08", records)

        self.assertIsNotNone(snapshot, "Snapshot should be generated for perfect data")
        self.assertTrue(snapshot.snapshot_id.startswith("SNP-2026-08-"), "Snapshot ID format is invalid")
        self.assertEqual(snapshot.total_headcount, 2)

    def test_03_scenario_b_data_mismatch(self):
        """Scenario B: Data mismatch triggering the exception workflow and SLA assignment"""
        batch_id = "batch-B"
        # EMP-1001 worked 18 days, 0 approved leaves, 4 unapproved absences (Mismatch + Unapproved rule violation)
        records = [
            AttendanceIngestion(ingestion_batch_id=batch_id, employee_code="EMP-1001", period_key="2026-08", days_worked=18.0, approved_leaves=0.0, unapproved_absences=4.0)
        ]
        self.db.add_all(records)
        self.db.commit()

        # Run Validation Layer
        validator = ValidationEngine(self.db, config.validation)
        discrepancies = validator.validate_ingestion_batch(records, "2026-08")
        self.assertGreater(len(discrepancies), 0, "Mismatched data should flag discrepancies")

        # Route to Exception Workflow (Layer 3)
        workflow = SLAExceptionWorkflow(self.db, config.sla)
        queued = workflow.route_discrepancies_to_queue(batch_id, discrepancies)

        self.assertEqual(len(queued), 2, "Expected 2 exceptions (WORKING_DAYS_MISMATCH, UNAPPROVED_ABSENCE)")
        self.assertEqual(queued[0].status, ExceptionStatus.OPEN, "Exceptions should initially be OPEN")
        self.assertIsNotNone(queued[0].sla_due_at, "SLA due date must be assigned to the exception")

    def test_04_scenario_c_exception_resolution(self):
        """Scenario C: Exception resolution leading to snapshot creation"""
        batch_id = "batch-C"
        # EMP-1002 worked 20 days, 2 approved leaves, 0 absences. Wait, 20 + 2 = 22, which matches expected 22 perfectly!
        # Let's make an actual exception first: 20 days worked, 0 leaves, 2 absences.
        records = [
            AttendanceIngestion(ingestion_batch_id=batch_id, employee_code="EMP-1002", period_key="2026-08", days_worked=20.0, approved_leaves=0.0, unapproved_absences=2.0)
        ]
        self.db.add_all(records)
        self.db.commit()

        validator = ValidationEngine(self.db, config.validation)
        discrepancies = validator.validate_ingestion_batch(records, "2026-08")
        
        workflow = SLAExceptionWorkflow(self.db, config.sla)
        queued = workflow.route_discrepancies_to_queue(batch_id, discrepancies)
        
        # Verify it's blocked before resolution
        open_count_before = workflow.get_open_exceptions_count(batch_id)
        self.assertGreater(open_count_before, 0, "Should have open exceptions blocking the snapshot")

        # Resolve all exceptions manually (Simulating Ops Action)
        for exc in queued:
            workflow.resolve_exception(exc.id, resolved_by="QA_Automation", resolution_notes="Overrides applied for testing.")

        # Verify resolution
        open_count_after = workflow.get_open_exceptions_count(batch_id)
        self.assertEqual(open_count_after, 0, "All exceptions should be resolved")

        # Generate Snapshot now that exceptions are resolved
        snapshot_engine = ImmutableSnapshotEngine(self.db, config.snapshot)
        snapshot = snapshot_engine.create_billing_snapshot(batch_id, "2026-08", records)
        self.assertIsNotNone(snapshot, "Snapshot must be generated successfully after exception resolution")


if __name__ == "__main__":
    class CustomTestRunner(unittest.TextTestRunner):
        """Custom test runner to format output specifically for QA/Automation Engineer requirements."""
        def run(self, test):
            print("\n" + "="*70)
            print("AXIAN ATTENDANCE & BILLING VERIFICATION SUITE")
            print("="*70)
            result = super().run(test)
            print("="*70)
            print("TEST SUMMARY REPORT")
            print(f"Total Tests Executed: {result.testsRun}")
            print(f"[PASS] Passed: {result.testsRun - len(result.failures) - len(result.errors)}")
            print(f"[FAIL] Failed: {len(result.failures)}")
            print(f"[WARN] Errors: {len(result.errors)}")
            
            if result.wasSuccessful():
                print("\nFINAL STATUS: [PASS] All layers validated. System ready for deployment.")
            else:
                print("\nFINAL STATUS: [FAIL] Issues detected during validation.")
            print("="*70 + "\n")
            return result

    unittest.main(testRunner=CustomTestRunner(verbosity=2))
