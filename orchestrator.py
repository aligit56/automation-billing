"""
Pipeline Orchestrator Module.

Coordinates the end-to-end Automated Attendance Verification & Billing Workflow:
1. SOW Layer 1: Data Ingestion (nGAGE API Connector)
2. SOW Layer 2: Validation Engine Execution
3. SOW Layer 3: SLA Exception Routing & Dispute Resolution
4. SOW Layer 4: Immutable Hash-Backed Billing Snapshot Generation & Invoicing Trigger
"""

import uuid
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from models import (
    Employee, WorkingCalendar, AttendanceIngestion, ValidationRule,
    ExceptionRecord, BillingSnapshot, EmployeeStatus, RuleSeverity
)
from ngage_client import NGAGEClient
from validation_engine import ValidationEngine, ValidationResult
from exception_workflow import SLAExceptionWorkflow
from snapshot_engine import ImmutableSnapshotEngine
from config import AppConfig

logger = logging.getLogger("PipelineOrchestrator")


class AttendanceVerificationOrchestrator:
    """End-to-End Orchestrator for Axian x nGAGE Attendance Billing Pipeline."""

    def __init__(self, db_session: Session, app_config: AppConfig, mock_api: bool = True):
        self.db = db_session
        self.config = app_config
        self.ngage_client = NGAGEClient(app_config.ngage_api, mock_mode=mock_api)
        self.validator = ValidationEngine(db_session, app_config.validation)
        self.exception_handler = SLAExceptionWorkflow(db_session, app_config.sla)
        self.snapshot_engine = ImmutableSnapshotEngine(db_session, app_config.snapshot)

    def seed_initial_master_data(self, period_key: str):
        """Seeds baseline Employees and WorkingCalendars into database for testing."""
        logger.info(f"Seeding master headcount data and working calendars for period '{period_key}'...")

        # Working Calendar (US-MAIN: 22 working days in month)
        cal = self.db.query(WorkingCalendar).filter(
            WorkingCalendar.period_key == period_key,
            WorkingCalendar.location_code == "US-MAIN"
        ).first()
        if not cal:
            cal = WorkingCalendar(
                period_key=period_key,
                location_code="US-MAIN",
                total_calendar_days=31,
                expected_working_days=22,
                statutory_holidays=1,
                weekend_days=8
            )
            self.db.add(cal)

        # Baseline Master Employees
        employees_data = [
            ("EMP-1001", "Alice Smith", "alice@axian.com", EmployeeStatus.ACTIVE),
            ("EMP-1002", "Bob Jones", "bob@axian.com", EmployeeStatus.ACTIVE),
            ("EMP-1003", "Charlie Brown", "charlie@axian.com", EmployeeStatus.ACTIVE),
            ("EMP-1004", "Diana Prince", "diana@axian.com", EmployeeStatus.ACTIVE),
        ]

        for code, name, email, status in employees_data:
            emp = self.db.query(Employee).filter(Employee.employee_code == code).first()
            if not emp:
                emp = Employee(
                    employee_code=code,
                    full_name=name,
                    email=email,
                    status=status,
                    location_code="US-MAIN"
                )
                self.db.add(emp)

        # Validation Rules
        rules = [
            ("HEADCOUNT_MATCH", "Headcount & Active Status Match", RuleSeverity.CRITICAL, 24),
            ("WORKING_DAYS_MISMATCH", "Expected vs. Actual Working Days", RuleSeverity.WARNING, 48),
            ("UNAPPROVED_ABSENCE", "Unapproved Absence Exceeded", RuleSeverity.WARNING, 48),
        ]
        for code, rname, sev, sla in rules:
            r = self.db.query(ValidationRule).filter(ValidationRule.rule_code == code).first()
            if not r:
                r = ValidationRule(rule_code=code, rule_name=rname, severity=sev, sla_hours=sla)
                self.db.add(r)

        self.db.commit()
        logger.info("Master data seeded successfully.")

    def run_month_end_pipeline(self, period_key: str, auto_resolve_demo: bool = False) -> Dict[str, Any]:
        """
        Executes complete SOW month-end verification pipeline:
        1. Ingest from nGAGE API
        2. Execute Validation Engine
        3. Route Exceptions to SLA Queue
        4. Option to resolve/override exceptions
        5. Generate Immutable Billing Snapshot
        """
        batch_id = str(uuid.uuid4())
        logger.info(f"=== STARTING MONTH-END ATTENDANCE PIPELINE (Batch ID: {batch_id}) ===")

        # -------------------------------------------------------------
        # SOW Layer 1: Data Ingestion Layer
        # -------------------------------------------------------------
        raw_records = self.ngage_client.fetch_monthly_attendance(period_key)

        ingestion_entities: List[AttendanceIngestion] = []
        for raw in raw_records:
            entity = AttendanceIngestion(
                ingestion_batch_id=batch_id,
                employee_code=raw["employee_code"],
                period_key=period_key,
                days_worked=float(raw.get("days_worked", 0)),
                approved_leaves=float(raw.get("approved_leaves", 0)),
                unapproved_absences=float(raw.get("unapproved_absences", 0)),
                raw_payload=raw
            )
            self.db.add(entity)
            ingestion_entities.append(entity)

        self.db.commit()
        for e in ingestion_entities:
            self.db.refresh(e)

        logger.info(f"Layer 1 Complete: Ingested {len(ingestion_entities)} records into AttendanceIngestion table.")

        # -------------------------------------------------------------
        # SOW Layer 2: Validation Rules Engine
        # -------------------------------------------------------------
        discrepancies = self.validator.validate_ingestion_batch(ingestion_entities, period_key)

        # Separate records into clean vs flagged
        failed_codes = {d.employee_code for d in discrepancies}
        approved_records = [rec for rec in ingestion_entities if rec.employee_code not in failed_codes]

        logger.info(f"Layer 2 Complete: {len(approved_records)} passed clean validation. {len(discrepancies)} flagged.")

        # -------------------------------------------------------------
        # SOW Layer 3: Resolution & Billing Layer (Exception Queue & SLA)
        # -------------------------------------------------------------
        queued_exceptions = []
        if discrepancies:
            queued_exceptions = self.exception_handler.route_discrepancies_to_queue(batch_id, discrepancies)
            logger.info(f"Layer 3 In-Progress: {len(queued_exceptions)} open exceptions in resolution queue.")

            if auto_resolve_demo:
                logger.info("Demo Mode: Simulating Axian ops resolving/overriding exceptions...")
                for exc in queued_exceptions:
                    if exc.rule_code == "WORKING_DAYS_MISMATCH":
                        # Resolve with approved shift correction
                        self.exception_handler.resolve_exception(
                            exc.id,
                            resolved_by="OpsManager_Axian",
                            resolution_notes="Shift schedule variance confirmed & approved by site lead."
                        )
                        # Add resolved employee to approved records for snapshot inclusion
                        target_rec = next((r for r in ingestion_entities if r.employee_code == exc.employee_code), None)
                        if target_rec and target_rec not in approved_records:
                            approved_records.append(target_rec)
                    elif exc.rule_code == "UNAPPROVED_ABSENCE":
                        # Override absence after PTO documentation produced
                        self.exception_handler.resolve_exception(
                            exc.id,
                            resolved_by="HR_Admin",
                            resolution_notes="Retroactive sick leave documentation attached.",
                            override=True
                        )
                        target_rec = next((r for r in ingestion_entities if r.employee_code == exc.employee_code), None)
                        if target_rec and target_rec not in approved_records:
                            approved_records.append(target_rec)
                    elif exc.rule_code == "HEADCOUNT_MATCH":
                        self.exception_handler.resolve_exception(
                            exc.id,
                            resolved_by="OpsManager_Axian",
                            resolution_notes="Employee verified as recently onboarded.",
                            override=True
                        )
                        target_rec = next((r for r in ingestion_entities if r.employee_code == exc.employee_code), None)
                        if target_rec and target_rec not in approved_records:
                            approved_records.append(target_rec)

        # -------------------------------------------------------------
        # SOW Layer 4: Immutable Snapshot Engine
        # -------------------------------------------------------------
        snapshot = None
        open_exceptions_remaining = self.exception_handler.get_open_exceptions_count(batch_id)

        if open_exceptions_remaining > 0:
            logger.warning(f"Snapshot generation BLOCKED! {open_exceptions_remaining} open exceptions remain in queue.")
        else:
            logger.info("All validation checks cleared / exceptions resolved! Triggering Snapshot Engine...")
            snapshot = self.snapshot_engine.create_billing_snapshot(
                batch_id=batch_id,
                period_key=period_key,
                approved_records=approved_records
            )

        return {
            "batch_id": batch_id,
            "period_key": period_key,
            "total_ingested": len(ingestion_entities),
            "discrepancies_flagged": len(discrepancies),
            "exceptions_queued": len(queued_exceptions),
            "open_exceptions_remaining": open_exceptions_remaining,
            "approved_headcount_snapshotted": len(approved_records),
            "snapshot": snapshot.to_dict() if snapshot else None
        }
