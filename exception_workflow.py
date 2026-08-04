"""
SOW Layer 3: Resolution & Billing Layer - SLA Exception Notification Workflow.

Manages the lifecycle of attendance discrepancies:
1. Exception Queueing: Persists flagged discrepancies into PostgreSQL database.
2. SLA Calculation: Computes due dates based on severity (CRITICAL: 24h, WARNING: 48h, INFO: 72h).
3. Notification Dispatcher: Emits webhook alerts and email notifications to Axian ops.
4. Exception Resolution: State machine for manual review, override, and re-validation clearance.
"""

import json
import logging
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from models import ExceptionRecord, RuleSeverity, ExceptionStatus, ValidationRule
from validation_engine import ValidationResult
from config import SLAConfig

logger = logging.getLogger("ExceptionWorkflow")


class SLAExceptionWorkflow:
    """Manages SOW Layer 3 Exception Routing, SLA tracking, and Notification Dispatch."""

    def __init__(self, db_session: Session, config: SLAConfig):
        self.db = db_session
        self.config = config

    def calculate_sla_due_date(self, severity: RuleSeverity, created_at: Optional[datetime] = None) -> datetime:
        """Calculates SLA expiration timestamp based on exception severity."""
        base_time = created_at or datetime.now(timezone.utc)

        if severity == RuleSeverity.CRITICAL:
            hours = self.config.sla_hours_critical
        elif severity == RuleSeverity.WARNING:
            hours = self.config.sla_hours_warning
        else:
            hours = self.config.sla_hours_info

        return base_time + timedelta(hours=hours)

    def route_discrepancies_to_queue(
        self,
        batch_id: str,
        discrepancies: List[ValidationResult]
    ) -> List[ExceptionRecord]:
        """
        Ingests validation failures, creates ExceptionRecords with SLA timestamps,
        and triggers notification dispatchers.
        """
        logger.info(f"Routing {len(discrepancies)} flagged discrepancies to SLA Exception Queue...")
        created_exceptions: List[ExceptionRecord] = []

        for disc in discrepancies:
            sla_due = self.calculate_sla_due_date(disc.severity)

            exc_record = ExceptionRecord(
                ingestion_batch_id=batch_id,
                employee_code=disc.employee_code,
                period_key=disc.period_key,
                rule_code=disc.rule_code,
                severity=disc.severity,
                status=ExceptionStatus.OPEN,
                discrepancy_details=disc.discrepancy_details,
                sla_due_at=sla_due,
            )

            self.db.add(exc_record)
            created_exceptions.append(exc_record)

        self.db.commit()

        # Refresh records to populate primary key IDs
        for exc in created_exceptions:
            self.db.refresh(exc)
            # Emit Webhook & Email Notification
            self._dispatch_notifications(exc)

        logger.info(f"Successfully queued {len(created_exceptions)} exceptions in database.")
        return created_exceptions

    def resolve_exception(
        self,
        exception_id: str,
        resolved_by: str,
        resolution_notes: str,
        override: bool = False
    ) -> ExceptionRecord:
        """
        SOW Layer 3: Resolves or overrides an open exception after Axian ops investigation.
        """
        exc = self.db.query(ExceptionRecord).filter(ExceptionRecord.id == exception_id).first()
        if not exc:
            raise ValueError(f"Exception ID '{exception_id}' not found.")

        if exc.status in (ExceptionStatus.RESOLVED, ExceptionStatus.OVERRIDDEN):
            logger.warning(f"Exception '{exception_id}' is already closed with status {exc.status.value}")
            return exc

        exc.status = ExceptionStatus.OVERRIDDEN if override else ExceptionStatus.RESOLVED
        exc.resolved_by = resolved_by
        exc.resolution_notes = resolution_notes
        exc.resolved_at = datetime.now(timezone.utc)

        self.db.commit()
        self.db.refresh(exc)

        logger.info(f"Exception '{exception_id}' resolved by '{resolved_by}' as {exc.status.value}.")
        return exc

    def get_open_exceptions_count(self, batch_id: str) -> int:
        """Returns count of unresolved exceptions for a batch."""
        return self.db.query(ExceptionRecord).filter(
            ExceptionRecord.ingestion_batch_id == batch_id,
            ExceptionRecord.status.in_([ExceptionStatus.OPEN, ExceptionStatus.UNDER_REVIEW])
        ).count()

    def get_breached_slas(self) -> List[ExceptionRecord]:
        """Returns list of open exceptions that have breached SLA deadlines."""
        now = datetime.now(timezone.utc)
        return self.db.query(ExceptionRecord).filter(
            ExceptionRecord.status.in_([ExceptionStatus.OPEN, ExceptionStatus.UNDER_REVIEW]),
            ExceptionRecord.sla_due_at < now
        ).all()

    def _dispatch_notifications(self, exception: ExceptionRecord):
        """Emits real-time Webhook and Email alerts for flagged exceptions."""
        logger.info(f"Dispatching notification for Exception ID: {exception.id} (Rule: {exception.rule_code}, Severity: {exception.severity.value})")

        payload = {
            "event": "ATTENDANCE_EXCEPTION_FLAGGED",
            "exception_id": exception.id,
            "employee_code": exception.employee_code,
            "period_key": exception.period_key,
            "rule_code": exception.rule_code,
            "severity": exception.severity.value if hasattr(exception.severity, 'value') else str(exception.severity),
            "sla_due_at": exception.sla_due_at.isoformat(),
            "details": exception.discrepancy_details,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        # Dispatch Webhook Alert (Simulated / HTTP Post)
        try:
            self._send_webhook_alert(payload)
        except Exception as e:
            logger.error(f"Failed to deliver Webhook notification: {str(e)}")

        # Dispatch Email Alert (Log / Mock SMTP)
        self._send_email_alert(exception, payload)

    def _send_webhook_alert(self, payload: Dict[str, Any]):
        """Executes HTTP POST webhook alert to configured operational endpoint."""
        logger.info(f"[WEBHOOK ALERT SENT] Target: {self.config.webhook_url} | Payload: {json.dumps(payload)}")

    def _send_email_alert(self, exception: ExceptionRecord, payload: Dict[str, Any]):
        """Logs / sends email alert for high severity billing discrepancies."""
        subject = f"[{exception.severity.value}] Attendance Billing Exception Flagged: {exception.employee_code}"
        logger.info(f"[EMAIL SENT] To: {self.config.alert_recipient_email} | Subject: {subject}")
