"""
SOW Layer 4: Immutable Billing Snapshot Engine.

Generates immutable, tamper-proof snapshots of attendance billing records once validation passes:
1. Canonical JSON Serialization: Normalizes floats, keys, and structure deterministically.
2. Cryptographic Hash Engine: Computes SHA-256 digest (`snapshot_hash`).
3. HMAC Digital Signature: HMAC-SHA256 signing using secret key (`hmac_signature`).
4. Audit Hash Linkage: Links `previous_snapshot_hash` for immutable blockchain-like ledger integrity.
5. Verification Engine: Re-computes and validates snapshot digests to detect post-invoice tampering.
"""

import hmac
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session

from models import BillingSnapshot, AttendanceIngestion
from config import SnapshotConfig

logger = logging.getLogger("SnapshotEngine")


class TamperDetectedError(Exception):
    """Raised when a billing snapshot hash fails verification checks."""
    pass


class ImmutableSnapshotEngine:
    """SOW Layer 4: Hash-Backed Tamper-Proof Billing Record Snapshot Service."""

    def __init__(self, db_session: Session, config: SnapshotConfig):
        self.db = db_session
        self.config = config

    def render_canonical_json(self, payload: Dict[str, Any]) -> str:
        """
        Renders deterministic canonical JSON string.
        Ensures keys are sorted alphabetically, separators are standard, and UTF-8 encoded.
        """
        return json.dumps(payload, sort_keys=True, indent=None, separators=(',', ':'))

    def compute_sha256_hash(self, canonical_json: str) -> str:
        """Computes standard SHA-256 hex digest of the canonical JSON string."""
        return hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()

    def compute_hmac_signature(self, canonical_json: str) -> str:
        """Computes HMAC-SHA256 signature using secret key."""
        return hmac.new(
            self.config.hmac_secret.encode('utf-8'),
            canonical_json.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    def create_billing_snapshot(
        self,
        batch_id: str,
        period_key: str,
        approved_records: List[AttendanceIngestion]
    ) -> BillingSnapshot:
        """
        SOW Layer 4: Generates a new version-locked, hash-backed billing snapshot.
        """
        logger.info(f"Generating SOW Layer 4 Immutable Billing Snapshot for period '{period_key}' ({len(approved_records)} records)...")

        # 1. Sort records deterministically by employee_code
        sorted_records = sorted(approved_records, key=lambda r: r.employee_code)

        # 2. Build structured payload
        total_headcount = len(sorted_records)
        total_billable_days = sum(r.days_worked for r in sorted_records)
        total_approved_leaves = sum(r.approved_leaves for r in sorted_records)

        employee_items = []
        for r in sorted_records:
            employee_items.append({
                "employee_code": r.employee_code,
                "days_worked": float(r.days_worked),
                "approved_leaves": float(r.approved_leaves),
                "unapproved_absences": float(r.unapproved_absences),
            })

        payload = {
            "version": self.config.system_version,
            "period_key": period_key,
            "ingestion_batch_id": batch_id,
            "summary": {
                "total_headcount": total_headcount,
                "total_billable_days": total_billable_days,
                "total_approved_leaves": total_approved_leaves,
            },
            "line_items": employee_items
        }

        # 3. Canonical Serialization & Cryptographic Digests
        canonical_str = self.render_canonical_json(payload)
        snapshot_hash = self.compute_sha256_hash(canonical_str)
        hmac_sig = self.compute_hmac_signature(canonical_str)

        # 4. Fetch Previous Snapshot Hash for Hash-Chain Linkage
        prev_snapshot = self.db.query(BillingSnapshot).order_by(BillingSnapshot.created_at.desc()).first()
        prev_hash = prev_snapshot.snapshot_hash if prev_snapshot else None

        # 5. Format Unique Snapshot ID
        snapshot_id = f"SNP-{period_key}-{snapshot_hash[:8].upper()}"

        # 6. Save Snapshot to Database
        snapshot = BillingSnapshot(
            snapshot_id=snapshot_id,
            period_key=period_key,
            ingestion_batch_id=batch_id,
            total_headcount=total_headcount,
            total_billable_days=total_billable_days,
            total_approved_leaves=total_approved_leaves,
            snapshot_payload=payload,
            snapshot_hash=snapshot_hash,
            hmac_signature=hmac_sig,
            previous_snapshot_hash=prev_hash,
            is_immutable=True
        )

        self.db.add(snapshot)
        self.db.commit()
        self.db.refresh(snapshot)

        logger.info(f"Billing Snapshot '{snapshot_id}' created successfully.")
        logger.info(f"SHA-256 Hash: {snapshot_hash}")
        logger.info(f"HMAC Signature: {hmac_sig}")
        if prev_hash:
            logger.info(f"Hash Chain Linked to Previous Snapshot: {prev_hash[:16]}...")

        return snapshot

    def verify_snapshot_integrity(self, snapshot_id: str) -> Tuple[bool, str]:
        """
        SOW Layer 4 Verification: Re-evaluates snapshot hash and HMAC signature
        against stored database record to detect unauthorized modification or DB tampering.
        """
        snapshot = self.db.query(BillingSnapshot).filter(BillingSnapshot.snapshot_id == snapshot_id).first()
        if not snapshot:
            return False, f"Snapshot ID '{snapshot_id}' not found."

        # Re-compute Digests
        canonical_str = self.render_canonical_json(snapshot.snapshot_payload)
        recalculated_hash = self.compute_sha256_hash(canonical_str)
        recalculated_hmac = self.compute_hmac_signature(canonical_str)

        # Check Hash Match
        if recalculated_hash != snapshot.snapshot_hash:
            logger.critical(f"TAMPER DETECTED! Snapshot '{snapshot_id}' hash mismatch! Stored: {snapshot.snapshot_hash}, Calculated: {recalculated_hash}")
            return False, f"TAMPER DETECTED: Payload SHA-256 hash mismatch!"

        # Check HMAC Match
        if recalculated_hmac != snapshot.hmac_signature:
            logger.critical(f"TAMPER DETECTED! Snapshot '{snapshot_id}' HMAC signature mismatch!")
            return False, f"TAMPER DETECTED: Invalid HMAC signature!"

        logger.info(f"Snapshot '{snapshot_id}' verified. Data integrity is intact.")
        return True, "Snapshot verified clean. Integrity intact."
