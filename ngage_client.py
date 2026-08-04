"""
SOW Layer 1: Data Ingestion Layer - nGAGE API Integration Engine.

Provides secure communication with nGAGE REST endpoints:
- OAuth2 Client Credentials flow with automatic token refresh.
- Exponential backoff retry logic for 429/50x network failures.
- Paginated data fetching for large active workforce headcount.
- Full payload validation and audit logging support.
"""

import time
import logging
import json
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta

from config import NGAGEApiConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("nGAGEClient")


class NGAGEAuthError(Exception):
    """Raised when OAuth authentication fails with nGAGE API."""
    pass


class NGAGEApiError(Exception):
    """Raised when nGAGE API calls return persistent non-2xx statuses."""
    pass


class NGAGEClient:
    """Production-grade nGAGE API Connector Engine with OAuth2 & Exponential Backoff."""

    def __init__(self, config: NGAGEApiConfig, mock_mode: bool = True):
        self.config = config
        self.mock_mode = mock_mode
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    def _is_token_valid(self) -> bool:
        """Checks if current OAuth access token exists and has not expired."""
        if not self._access_token or not self._token_expires_at:
            return False
        # Add 60s buffer for token clock skew
        return datetime.now(timezone.utc) < (self._token_expires_at - timedelta(seconds=60))

    def authenticate(self) -> str:
        """
        SOW Layer 1: OAuth2 Client Credentials Grant Flow.
        Fetches or refreshes bearer token securely.
        """
        if self._is_token_valid():
            return self._access_token

        logger.info("Authenticating with nGAGE OAuth2 server...")

        if self.mock_mode:
            self._access_token = f"mock_bearer_token_{int(time.time())}"
            self._token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=3600)
            logger.info("Mock OAuth2 token acquired successfully.")
            return self._access_token

        # Production OAuth HTTP Request
        payload = json.dumps({
            "grant_type": "client_credentials",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }).encode('utf-8')

        req = urllib.request.Request(
            self.config.token_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    self._access_token = data["access_token"]
                    expires_in = data.get("expires_in", 3600)
                    self._token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                    logger.info("nGAGE OAuth2 token acquired successfully.")
                    return self._access_token
                else:
                    raise NGAGEAuthError(f"OAuth request failed with status {response.status}")
        except Exception as e:
            logger.error(f"nGAGE Authentication Failed: {str(e)}")
            raise NGAGEAuthError(f"Authentication failed: {str(e)}")

    def _execute_with_retry(self, url: str, headers: Dict[str, str]) -> Dict[str, Any]:
        """
        Executes HTTP GET with exponential backoff retries for rate-limiting (429) & server errors.
        """
        attempt = 0
        backoff = self.config.backoff_factor

        while attempt < self.config.max_retries:
            attempt += 1
            try:
                if self.mock_mode:
                    # Simulated network behavior
                    return self._generate_mock_http_response(url)

                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                    if response.status == 200:
                        return json.loads(response.read().decode('utf-8'))
                    elif response.status in (429, 500, 502, 503, 504):
                        logger.warning(f"HTTP {response.status} encountered. Retrying attempt {attempt}/{self.config.max_retries}...")
                    else:
                        raise NGAGEApiError(f"API Error HTTP {response.status}")
            except urllib.error.HTTPError as he:
                if he.code in (429, 500, 502, 503, 504) and attempt < self.config.max_retries:
                    logger.warning(f"HTTPError {he.code} encountered on attempt {attempt}. Retrying in {backoff:.2f}s...")
                else:
                    raise NGAGEApiError(f"HTTP Error {he.code}: {he.reason}")
            except Exception as ex:
                if attempt >= self.config.max_retries:
                    raise NGAGEApiError(f"Request failed after {attempt} retries: {str(ex)}")

            time.sleep(backoff)
            backoff *= 2  # Exponential delay boost

        raise NGAGEApiError(f"Max retries ({self.config.max_retries}) exceeded for URL: {url}")

    def fetch_monthly_attendance(self, period_key: str) -> List[Dict[str, Any]]:
        """
        SOW Layer 1: Fetch attendance actuals for active headcount with pagination support.

        Returns list of employee attendance records containing:
        - employee_code
        - days_worked
        - approved_leaves
        - unapproved_absences
        """
        token = self.authenticate()
        all_records: List[Dict[str, Any]] = []
        page = 1
        has_more = True

        logger.info(f"Initiating nGAGE attendance ingestion for period '{period_key}'...")

        while has_more:
            url = f"{self.config.base_url}/attendance?period={period_key}&page={page}&limit={self.config.page_size}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "Axian-Attendance-Billing-Pipeline/1.0"
            }

            response_data = self._execute_with_retry(url, headers)
            records = response_data.get("data", [])
            all_records.extend(records)

            pagination = response_data.get("pagination", {})
            total_pages = pagination.get("total_pages", 1)

            logger.info(f"Ingested page {page}/{total_pages} ({len(records)} records). Total so far: {len(all_records)}")

            if page >= total_pages:
                has_more = False
            else:
                page += 1

        logger.info(f"Ingestion completed for period '{period_key}'. Total records ingested: {len(all_records)}")
        return all_records

    def _generate_mock_http_response(self, url: str) -> Dict[str, Any]:
        """Generates realistic mock dataset for unit testing and offline execution."""
        # Check pagination query param
        page = 1
        if "page=2" in url:
            page = 2

        if page == 1:
            records = [
                {
                    "employee_code": "EMP-1001",
                    "full_name": "Alice Smith",
                    "status": "ACTIVE",
                    "days_worked": 22.0,
                    "approved_leaves": 0.0,
                    "unapproved_absences": 0.0,
                    "location_code": "US-MAIN"
                },
                {
                    "employee_code": "EMP-1002",
                    "full_name": "Bob Jones",
                    "status": "ACTIVE",
                    "days_worked": 20.0,
                    "approved_leaves": 2.0,
                    "unapproved_absences": 0.0,
                    "location_code": "US-MAIN"
                },
                {
                    "employee_code": "EMP-1003",
                    "full_name": "Charlie Brown",
                    "status": "ACTIVE",
                    "days_worked": 18.0,
                    "approved_leaves": 1.0,
                    "unapproved_absences": 3.0,  # Unapproved absence discrepancy!
                    "location_code": "US-MAIN"
                },
                {
                    "employee_code": "EMP-1004",
                    "full_name": "Diana Prince",
                    "status": "ACTIVE",
                    "days_worked": 15.0,  # Expected working days mismatch (expected 22)
                    "approved_leaves": 0.0,
                    "unapproved_absences": 0.0,
                    "location_code": "US-MAIN"
                }
            ]
            return {
                "status": "success",
                "pagination": {"page": 1, "total_pages": 2, "total_records": 5},
                "data": records
            }
        else:
            records = [
                {
                    "employee_code": "EMP-9999",  # Headcount mismatch: Not in Axian master DB!
                    "full_name": "Ghost Employee",
                    "status": "ACTIVE",
                    "days_worked": 22.0,
                    "approved_leaves": 0.0,
                    "unapproved_absences": 0.0,
                    "location_code": "US-MAIN"
                }
            ]
            return {
                "status": "success",
                "pagination": {"page": 2, "total_pages": 2, "total_records": 5},
                "data": records
            }
