"""
Database Schema Module using SQLAlchemy 2.0.

Defines PostgreSQL relational models for:
1. Employees - Active workforce master database
2. WorkingCalendars - Target monthly working day expectations per site
3. AttendanceIngestion - Raw nGAGE API audit records per employee/period
4. ValidationRules - Rule definitions, severities, and SLA configurations
5. Exceptions - Flagged discrepancies requiring Axian resolution
6. BillingSnapshots - Immutable, hash-backed billing records for invoice generation

SOW References:
- SOW Layer 1: AttendanceIngestion table (stores raw JSON payload + parsed metrics)
- SOW Layer 2: ValidationRules & Exceptions tables
- SOW Layer 3: Exceptions SLA resolution metadata
- SOW Layer 4: BillingSnapshots (Cryptographic SHA-256 hash & canonical payload lock)
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional, Dict, Any

from sqlalchemy import (
    Column, String, Float, Integer, Boolean, DateTime, Text, Enum,
    ForeignKey, UniqueConstraint, Index, JSON
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class EmployeeStatus(str, PyEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    TERMINATED = "TERMINATED"
    ON_LEAVE = "ON_LEAVE"


class RuleSeverity(str, PyEnum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


class ExceptionStatus(str, PyEnum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    OVERRIDDEN = "OVERRIDDEN"


def generate_uuid() -> str:
    return str(uuid.uuid4())


class Employee(Base):
    """Active Headcount Master Directory."""
    __tablename__ = "employees"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    employee_code = Column(String(50), unique=True, nullable=False, index=True)  # nGAGE ID
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    status = Column(Enum(EmployeeStatus), default=EmployeeStatus.ACTIVE, nullable=False)
    location_code = Column(String(50), nullable=False, default="US-MAIN")
    joined_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    ingestions = relationship("AttendanceIngestion", back_populates="employee")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "employee_code": self.employee_code,
            "full_name": self.full_name,
            "email": self.email,
            "status": self.status.value if isinstance(self.status, EmployeeStatus) else self.status,
            "location_code": self.location_code,
        }


class WorkingCalendar(Base):
    """Expected Monthly Working Calendar Target per Location."""
    __tablename__ = "working_calendars"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    period_key = Column(String(7), nullable=False, index=True)  # Format: YYYY-MM
    location_code = Column(String(50), nullable=False, index=True)
    total_calendar_days = Column(Integer, nullable=False)
    expected_working_days = Column(Integer, nullable=False)
    statutory_holidays = Column(Integer, default=0, nullable=False)
    weekend_days = Column(Integer, default=0, nullable=False)
    is_locked = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("period_key", "location_code", name="uix_period_location"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "period_key": self.period_key,
            "location_code": self.location_code,
            "expected_working_days": self.expected_working_days,
            "total_calendar_days": self.total_calendar_days,
        }


class AttendanceIngestion(Base):
    """SOW Layer 1: Ingested nGAGE Raw Attendance Data & Metrics."""
    __tablename__ = "attendance_ingestion"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    ingestion_batch_id = Column(String(36), nullable=False, index=True)
    employee_code = Column(String(50), ForeignKey("employees.employee_code"), nullable=False, index=True)
    period_key = Column(String(7), nullable=False, index=True)
    days_worked = Column(Float, nullable=False, default=0.0)
    approved_leaves = Column(Float, nullable=False, default=0.0)
    unapproved_absences = Column(Float, nullable=False, default=0.0)
    raw_payload = Column(JSON, nullable=True)  # Auditable nGAGE REST payload
    ingested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    employee = relationship("Employee", back_populates="ingestions")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ingestion_batch_id": self.ingestion_batch_id,
            "employee_code": self.employee_code,
            "period_key": self.period_key,
            "days_worked": self.days_worked,
            "approved_leaves": self.approved_leaves,
            "unapproved_absences": self.unapproved_absences,
            "ingested_at": self.ingested_at.isoformat() if self.ingested_at else None,
        }


class ValidationRule(Base):
    """SOW Layer 2: Rule Definitions & SLA Configurations."""
    __tablename__ = "validation_rules"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    rule_code = Column(String(50), unique=True, nullable=False, index=True)
    rule_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    severity = Column(Enum(RuleSeverity), default=RuleSeverity.WARNING, nullable=False)
    sla_hours = Column(Integer, default=24, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    parameters = Column(JSON, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_code": self.rule_code,
            "rule_name": self.rule_name,
            "severity": self.severity.value if isinstance(self.severity, RuleSeverity) else self.severity,
            "sla_hours": self.sla_hours,
            "is_active": self.is_active,
        }


class ExceptionRecord(Base):
    """SOW Layer 3: Flagged Discrepancies & SLA Resolution Records."""
    __tablename__ = "exceptions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    ingestion_batch_id = Column(String(36), nullable=False, index=True)
    employee_code = Column(String(50), nullable=False, index=True)
    period_key = Column(String(7), nullable=False, index=True)
    rule_code = Column(String(50), ForeignKey("validation_rules.rule_code"), nullable=False)
    severity = Column(Enum(RuleSeverity), nullable=False)
    status = Column(Enum(ExceptionStatus), default=ExceptionStatus.OPEN, nullable=False)
    discrepancy_details = Column(JSON, nullable=False)
    sla_due_at = Column(DateTime, nullable=False)
    resolved_by = Column(String(255), nullable=True)
    resolution_notes = Column(Text, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ingestion_batch_id": self.ingestion_batch_id,
            "employee_code": self.employee_code,
            "period_key": self.period_key,
            "rule_code": self.rule_code,
            "severity": self.severity.value if isinstance(self.severity, RuleSeverity) else self.severity,
            "status": self.status.value if isinstance(self.status, ExceptionStatus) else self.status,
            "discrepancy_details": self.discrepancy_details,
            "sla_due_at": self.sla_due_at.isoformat() if self.sla_due_at else None,
            "resolved_by": self.resolved_by,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


class BillingSnapshot(Base):
    """SOW Layer 4: Immutable, SHA-256 Hash-Backed Billing Snapshot."""
    __tablename__ = "billing_snapshots"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    snapshot_id = Column(String(100), unique=True, nullable=False, index=True)
    period_key = Column(String(7), nullable=False, index=True)
    ingestion_batch_id = Column(String(36), nullable=False)
    total_headcount = Column(Integer, nullable=False)
    total_billable_days = Column(Float, nullable=False)
    total_approved_leaves = Column(Float, nullable=False, default=0.0)
    snapshot_payload = Column(JSON, nullable=False)  # Canonical JSON structure
    snapshot_hash = Column(String(64), nullable=False)  # SHA-256 Digest
    hmac_signature = Column(String(128), nullable=False)  # HMAC-SHA256 Signature
    previous_snapshot_hash = Column(String(64), nullable=True)  # Audit hash chain
    is_immutable = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "period_key": self.period_key,
            "ingestion_batch_id": self.ingestion_batch_id,
            "total_headcount": self.total_headcount,
            "total_billable_days": self.total_billable_days,
            "snapshot_hash": self.snapshot_hash,
            "hmac_signature": self.hmac_signature,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

class SystemSettings(Base):
    """Admin configuration for email and automated triggers."""
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True)
    sender_email = Column(String(255), nullable=False, default="admin@axian.com")
    trigger_time = Column(String(10), nullable=False, default="17:00")
    smtp_password = Column(String(255), nullable=True)
    smtp_host = Column(String(255), nullable=False, default="smtp.gmail.com")
    smtp_port = Column(Integer, nullable=False, default=587)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "sender_email": self.sender_email,
            "trigger_time": self.trigger_time,
            "smtp_password": "********" if self.smtp_password else "",
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port
        }
