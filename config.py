"""
Configuration module for Axian x nGAGE Attendance Verification & Billing System.

References:
- SOW Layer 1: Ingestion Config (nGAGE API Credentials, Endpoints, Retry Policies)
- SOW Layer 2: Validation Config (Thresholds and Default Rule Severity)
- SOW Layer 3: SLA Exception Config (SLA Windows in Hours, Webhook/Email Settings)
- SOW Layer 4: Immutable Snapshot Config (Hash Algorithm, HMAC Secret)
"""

import os
from dataclasses import dataclass, field


@dataclass
class NGAGEApiConfig:
    """SOW Layer 1: Configuration for nGAGE API Integration."""
    base_url: str = os.getenv("NGAGE_BASE_URL", "https://api.ngage-workforce.com/v2")
    client_id: str = os.getenv("NGAGE_CLIENT_ID", "axian_client_prod_883")
    client_secret: str = os.getenv("NGAGE_CLIENT_SECRET", "")
    token_url: str = os.getenv("NGAGE_TOKEN_URL", "https://api.ngage-workforce.com/oauth/token")
    timeout_seconds: int = 30
    max_retries: int = 4
    backoff_factor: float = 1.5
    page_size: int = 100


@dataclass
class ValidationConfig:
    """SOW Layer 2: Configuration for Business Rules Engine."""
    max_allowed_unapproved_absences: float = 0.0
    allow_overtime_days: bool = True
    strict_headcount_check: bool = True


@dataclass
class SLAConfig:
    """SOW Layer 3: SLA Resolution Windows & Notification Settings."""
    # SLA windows in hours
    sla_hours_critical: int = 24
    sla_hours_warning: int = 48
    sla_hours_info: int = 72
    
    webhook_url: str = os.getenv("ALERT_WEBHOOK_URL", "https://hooks.axian-ops.internal/attendance-alerts")
    smtp_server: str = os.getenv("SMTP_SERVER", "smtp.axian-ops.internal")
    alert_recipient_email: str = os.getenv("ALERT_EMAIL", "billing-disputes@axian.com")


@dataclass
class SnapshotConfig:
    """SOW Layer 4: Immutable Billing Snapshot Integrity Settings."""
    hash_algorithm: str = "sha256"
    hmac_secret: str = os.getenv("HMAC_SECRET", "")
    system_version: str = "v1.2.0"


@dataclass
class AppConfig:
    """Main Application Configuration Aggregator."""
    db_uri: str = os.getenv("DATABASE_URL", "sqlite:///attendance.db")  # Supports PostgreSQL postgresql://...
    environment: str = os.getenv("APP_ENV", "production")
    ngage_api: NGAGEApiConfig = field(default_factory=NGAGEApiConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    sla: SLAConfig = field(default_factory=SLAConfig)
    snapshot: SnapshotConfig = field(default_factory=SnapshotConfig)


config = AppConfig()
