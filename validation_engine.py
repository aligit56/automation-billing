"""
SOW Layer 2: Validation Rules Engine.

Executes automated verification checks comparing nGAGE ingested actuals
against Axian active headcount master data and monthly working calendar targets:
1. Headcount & Active Status Match (Rule: HEADCOUNT_MATCH)
2. Expected vs. Actual Working Days in Period (Rule: WORKING_DAYS_MISMATCH)
3. Unapproved Absences Threshold Check (Rule: UNAPPROVED_ABSENCE)
"""

import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from models import (
    Employee, WorkingCalendar, AttendanceIngestion, ValidationRule,
    RuleSeverity, EmployeeStatus
)
from config import ValidationConfig

logger = logging.getLogger("ValidationEngine")


@dataclass
class ValidationResult:
    """Represents outcome of a single record validation check."""
    passed: bool
    employee_code: str
    period_key: str
    rule_code: str
    severity: RuleSeverity
    discrepancy_details: Dict[str, Any]


class ValidationEngine:
    """Core Validation Logic Engine enforcing SOW billing integrity rules."""

    def __init__(self, db_session: Session, config: ValidationConfig):
        self.db = db_session
        self.config = config

    def validate_ingestion_batch(
        self,
        batch_records: List[AttendanceIngestion],
        period_key: str
    ) -> List[ValidationResult]:
        """
        Executes all active validation rules across the ingested batch.
        Returns a list of failed validation results for exception routing.
        """
        logger.info(f"Starting SOW Layer 2 validation for {len(batch_records)} records in period '{period_key}'...")
        discrepancies: List[ValidationResult] = []

        # Pre-fetch working calendar for period
        calendar_map = self._load_working_calendars(period_key)
        active_employees = self._load_active_employees()

        for record in batch_records:
            # Rule 1: Headcount & Active Status Match
            res_headcount = self._check_headcount_match(record, active_employees)
            if not res_headcount.passed:
                discrepancies.append(res_headcount)
                # If headcount match fails completely, skip secondary rules for unknown employee
                continue

            # Rule 2: Expected vs Actual Working Days
            res_days = self._check_working_days_match(record, calendar_map)
            if not res_days.passed:
                discrepancies.append(res_days)

            # Rule 3: Unapproved Absences Check
            res_absences = self._check_unapproved_absences(record)
            if not res_absences.passed:
                discrepancies.append(res_absences)

        logger.info(f"Validation finished. Total discrepancies detected: {len(discrepancies)}")
        return discrepancies

    def _load_working_calendars(self, period_key: str) -> Dict[str, WorkingCalendar]:
        """Loads target working calendars indexed by location code."""
        calendars = self.db.query(WorkingCalendar).filter(WorkingCalendar.period_key == period_key).all()
        return {c.location_code: c for c in calendars}

    def _load_active_employees(self) -> Dict[str, Employee]:
        """Loads master employees directory indexed by employee_code."""
        employees = self.db.query(Employee).all()
        return {e.employee_code: e for e in employees}

    def _check_headcount_match(
        self,
        record: AttendanceIngestion,
        employee_map: Dict[str, Employee]
    ) -> ValidationResult:
        """
        Rule 1: Headcount & Active Employee Status Match.
        Checks if nGAGE employee exists in Axian master database with ACTIVE status.
        """
        rule_code = "HEADCOUNT_MATCH"
        emp = employee_map.get(record.employee_code)

        if not emp:
            return ValidationResult(
                passed=False,
                employee_code=record.employee_code,
                period_key=record.period_key,
                rule_code=rule_code,
                severity=RuleSeverity.CRITICAL,
                discrepancy_details={
                    "issue": "Employee code not found in Axian active headcount database",
                    "ingested_code": record.employee_code,
                    "action_required": "Verify if employee was recently onboarded or code was mistyped"
                }
            )

        if emp.status != EmployeeStatus.ACTIVE:
            return ValidationResult(
                passed=False,
                employee_code=record.employee_code,
                period_key=record.period_key,
                rule_code=rule_code,
                severity=RuleSeverity.CRITICAL,
                discrepancy_details={
                    "issue": f"Attendance logged for non-active employee (Status: {emp.status.value})",
                    "employee_name": emp.full_name,
                    "employee_status": emp.status.value,
                    "action_required": "Review termination/leave status in HRIS"
                }
            )

        return ValidationResult(
            passed=True,
            employee_code=record.employee_code,
            period_key=record.period_key,
            rule_code=rule_code,
            severity=RuleSeverity.INFO,
            discrepancy_details={}
        )

    def _check_working_days_match(
        self,
        record: AttendanceIngestion,
        calendar_map: Dict[str, WorkingCalendar]
    ) -> ValidationResult:
        """
        Rule 2: Expected vs. Actual Working Days in Period.
        Checks if (days_worked + approved_leaves) == expected_working_days for the employee's site.
        """
        rule_code = "WORKING_DAYS_MISMATCH"
        
        # Resolve location code from employee record or default
        emp = self.db.query(Employee).filter(Employee.employee_code == record.employee_code).first()
        location_code = emp.location_code if emp else "US-MAIN"
        calendar = calendar_map.get(location_code)

        expected_days = calendar.expected_working_days if calendar else 22.0
        total_accounted_days = record.days_worked + record.approved_leaves

        if abs(total_accounted_days - expected_days) > 0.01:
            variance = total_accounted_days - expected_days
            severity = RuleSeverity.CRITICAL if abs(variance) > 3.0 else RuleSeverity.WARNING

            return ValidationResult(
                passed=False,
                employee_code=record.employee_code,
                period_key=record.period_key,
                rule_code=rule_code,
                severity=severity,
                discrepancy_details={
                    "issue": "Accounted working days do not match site working calendar target",
                    "expected_working_days": expected_days,
                    "actual_days_worked": record.days_worked,
                    "approved_leaves": record.approved_leaves,
                    "total_accounted_days": total_accounted_days,
                    "variance_days": variance,
                    "location_code": location_code
                }
            )

        return ValidationResult(
            passed=True,
            employee_code=record.employee_code,
            period_key=record.period_key,
            rule_code=rule_code,
            severity=RuleSeverity.INFO,
            discrepancy_details={}
        )

    def _check_unapproved_absences(self, record: AttendanceIngestion) -> ValidationResult:
        """
        Rule 3: Unapproved Absences Check.
        Flags any unapproved absences exceeding the configured limit (default 0).
        """
        rule_code = "UNAPPROVED_ABSENCE"
        limit = self.config.max_allowed_unapproved_absences

        if record.unapproved_absences > limit:
            return ValidationResult(
                passed=False,
                employee_code=record.employee_code,
                period_key=record.period_key,
                rule_code=rule_code,
                severity=RuleSeverity.WARNING,
                discrepancy_details={
                    "issue": "Unapproved absences detected in monthly attendance record",
                    "unapproved_absences": record.unapproved_absences,
                    "allowed_threshold": limit,
                    "action_required": "Request supervisor approval or convert to leave balance"
                }
            )

        return ValidationResult(
            passed=True,
            employee_code=record.employee_code,
            period_key=record.period_key,
            rule_code=rule_code,
            severity=RuleSeverity.INFO,
            discrepancy_details={}
        )
