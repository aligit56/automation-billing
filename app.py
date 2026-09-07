import logging
import uuid
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv, set_key, dotenv_values
load_dotenv(dotenv_path=".env", override=False)  # Load .env on every startup
from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
from pydantic import BaseModel
from typing import List, Optional
import smtplib
from email.mime.text import MIMEText
import socket

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Connect to a public IP to figure out our local IP routing
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"
from email.mime.multipart import MIMEMultipart
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import config
from models import Base, Employee, ExceptionRecord, ValidationRule, BillingSnapshot, SystemSettings, EmailToken
from orchestrator import AttendanceVerificationOrchestrator
from snapshot_engine import ImmutableSnapshotEngine
from generate_dummy_excel import parse_excel, update_excel_record

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("App")

app = FastAPI(title="Attendance Admin Panel API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = create_engine(config.db_uri, echo=False)
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)

with SessionLocal() as db:
    orchestrator = AttendanceVerificationOrchestrator(db, config, mock_api=True)
    orchestrator.seed_initial_master_data("2026-08")
    existing = db.query(SystemSettings).first()
    if not existing:
        # Try to seed from .env file first, then fall back to defaults
        env_vals = dotenv_values(".env")
        db.add(SystemSettings(
            sender_email=env_vals.get("SMTP_SENDER_EMAIL") or os.getenv("SMTP_SENDER_EMAIL", "admin@axian.com"),
            smtp_password=env_vals.get("SMTP_PASSWORD") or os.getenv("SMTP_PASSWORD") or None,
            smtp_host=env_vals.get("SMTP_HOST") or os.getenv("SMTP_HOST", "smtp.gmail.com"),
            smtp_port=int(env_vals.get("SMTP_PORT") or os.getenv("SMTP_PORT", "587")),
            trigger_time=env_vals.get("SMTP_TRIGGER_TIME") or os.getenv("SMTP_TRIGGER_TIME", "17:00"),
        ))
        db.commit()
        logger.info("[Startup] SystemSettings seeded from .env / defaults")

scheduler = BackgroundScheduler()

def scheduled_job():
    logger.info("Running automated scheduled job...")
    with SessionLocal() as db:
        settings = db.query(SystemSettings).first()
        if not settings or not settings.active_schedule_id or not settings.schedules_json:
            logger.info("No active schedule configured. Skipping.")
            return

        import json
        import calendar
        from datetime import date
        
        try:
            schedules = json.loads(settings.schedules_json)
        except Exception:
            return
            
        active_profile = next((p for p in schedules if p.get("id") == settings.active_schedule_id), None)
        if not active_profile:
            logger.info("Active schedule profile not found. Skipping.")
            return
            
        today = date.today()
        
        is_recurring = active_profile.get("is_recurring", True)
        if not is_recurring:
            target_month = int(active_profile.get("target_month", today.month))
            if today.month != target_month:
                logger.info(f"Skipping: Schedule is for month {target_month}, but current month is {today.month}.")
                return
        
        # Find the last day of the current month
        last_day = calendar.monthrange(today.year, today.month)[1]
        
        days_before = int(active_profile.get("schedule_days_before_end", 5))
        target_initial_day = last_day - days_before
        
        logger.info(f"Today is day {today.day}. Initial trigger day is {target_initial_day}.")
        
        import pandas as pd
        if today.day == target_initial_day:
            logger.info("Today is the initial verification day. Sending emails to all employees.")
            try:
                df = pd.read_excel("dummy_attendance_records.xlsx")
                all_codes = df["employee_code"].dropna().astype(str).tolist()
                logger.info(f"Found {len(all_codes)} employees. Triggering emails...")
                _send_emails(all_codes, db, custom_subject="Attendance Verification Request", custom_body=active_profile.get("custom_email_body") or None)
            except Exception as e:
                logger.error(f"Failed to trigger emails: {e}")
                
        else:
            # Check reminders
            reminders = []
            try:
                reminders = json.loads(active_profile.get("reminders_json", "[]"))
            except Exception:
                pass
                
            for r in reminders:
                days_after = int(r.get("days", 2))
                rem_date = target_initial_day + days_after
                if rem_date > last_day:
                    rem_date = last_day
                    
                if today.day == rem_date:
                    logger.info(f"Today is reminder day ({days_after} days after initial). Sending reminders...")
                    try:
                        df = pd.read_excel("dummy_attendance_records.xlsx")
                        # Only send reminders to those who haven't approved/disputed
                        pending_df = df[~df["status"].isin(["Approved", "Disputed", "Resolved"])]
                        pending_codes = pending_df["employee_code"].dropna().astype(str).tolist()
                        
                        logger.info(f"Found {len(pending_codes)} employees who haven't responded. Triggering reminders...")
                        if pending_codes:
                            _send_emails(pending_codes, db, custom_subject="Reminder: Attendance Verification Request", custom_body=r.get("body") or None)
                    except Exception as e:
                        logger.error(f"Failed to trigger reminders: {e}")
        
        # Original placeholder logic for month-end:
        if today.day == last_day:
            logger.info("Running month-end billing pipeline...")
            orch = AttendanceVerificationOrchestrator(db, config, mock_api=True)
            orch.run_month_end_pipeline(f"{today.year}-{today.month:02d}", auto_resolve_demo=True)

def update_scheduler_trigger(time_str: str):
    hour, minute = time_str.split(":")
    scheduler.reschedule_job('daily_job', trigger=CronTrigger(hour=hour, minute=minute))
    logger.info(f"Scheduler updated to run daily at {time_str}")

scheduler.add_job(scheduled_job, CronTrigger(hour=17, minute=0), id='daily_job')
scheduler.start()

with SessionLocal() as db:
    settings = db.query(SystemSettings).first()
    if settings:
        update_scheduler_trigger(settings.trigger_time)

def get_local_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"

def get_employee_token_status(db, employee_code: str) -> dict:
    token = db.query(EmailToken).filter(
        EmailToken.employee_code == employee_code
    ).order_by(EmailToken.sent_at.desc()).first()
    if not token:
        return {"token_status": "no_token", "token": None, "sent_at": None}
    now = datetime.now(timezone.utc)
    if token.is_expired or (token.expires_at and now > token.expires_at.replace(tzinfo=timezone.utc)):
        status = "expired"
    elif token.is_used:
        status = "used"
    else:
        status = "active"
    return {"token_status": status, "token": token.token, "sent_at": token.sent_at.isoformat() if token.sent_at else None}

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def read_index():
    return FileResponse("static/index.html")

@app.get("/api/dashboard_data")
def get_dashboard_data():
    with SessionLocal() as db:
        employees = db.query(Employee).count()
        exceptions = db.query(ExceptionRecord).all()
        rules = db.query(ValidationRule).all()
        snapshots = db.query(BillingSnapshot).all()
        return {"total_employees": employees, "exceptions": [e.to_dict() for e in exceptions], "rules": [r.to_dict() for r in rules], "snapshots": [s.to_dict() for s in snapshots]}

@app.get("/api/employees")
def get_employees_api():
    filepath = "dummy_attendance_records.xlsx"
    if not os.path.exists(filepath):
        from generate_dummy_excel import generate_excel
        generate_excel(filepath)
    raw_data = parse_excel(filepath)
    formatted_data = []
    with SessionLocal() as db:
        for i, row in enumerate(raw_data):
            code = row["employee_code"]
            token_info = get_employee_token_status(db, code)
            excel_status = str(row.get("verification_status", "")).strip()
            if excel_status in ("Verified", "Approved"):
                ui_status = "Approved"
            elif excel_status == "Rejected":
                ui_status = "Rejected"
            elif excel_status == "Correction Submitted":
                ui_status = "Response Received"
            elif token_info["token_status"] == "expired":
                ui_status = "Expired"
            elif token_info["token_status"] == "active":
                ui_status = "Email Sent"
            else:
                ui_status = "No Action Taken"
            formatted_data.append({
                "id": i + 1, "code": code, "name": row["full_name"], "email": row["email"],
                "currentDays": row["days_worked"], "approvedLeaves": row.get("approved_leaves", 0),
                "unapprovedAbsences": row.get("unapproved_absences", 0),
                "correctedDays": row.get("corrected_days", ""),
                "status": ui_status, "token": token_info["token"], "sentAt": token_info["sent_at"],
            })
    return formatted_data

class SchedulePayload(BaseModel):
    time: str
    date: str

class ApprovalPayload(BaseModel):
    employee_code: str

class BulkApprovalPayload(BaseModel):
    employee_codes: List[str]

class EditPayload(BaseModel):
    employee_code: str
    new_status: str

class EmailPayload(BaseModel):
    employee_codes: List[str]
    custom_subject: Optional[str] = None
    custom_body: Optional[str] = None

class ExpirePayload(BaseModel):
    employee_code: str

class SettingsPayload(BaseModel):
    sender_email: str
    smtp_password: str
    smtp_host: str
    smtp_port: int
    trigger_time: Optional[str] = None

class SchedulerPayload(BaseModel):
    trigger_time: str
    schedule_days_before_end: int
    reminders_json: str
    active_schedule_id: str
    schedules_json: str

@app.post("/api/set_schedule")
def set_schedule(payload: SchedulePayload):
    try:
        hour, minute = map(int, payload.time.split(":"))
        scheduler.reschedule_job("daily_job", trigger=CronTrigger(hour=hour, minute=minute))
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/approve_correction")
def approve_correction(payload: ApprovalPayload):
    from generate_dummy_excel import approve_excel_record
    success = approve_excel_record("dummy_attendance_records.xlsx", payload.employee_code)
    if success: return {"status": "success"}
    return {"status": "error", "message": "Employee not found"}

@app.post("/api/reject_correction")
def reject_correction(payload: ApprovalPayload):
    from generate_dummy_excel import reject_excel_record
    success = reject_excel_record("dummy_attendance_records.xlsx", payload.employee_code)
    if success: return {"status": "success"}
    return {"status": "error", "message": "Employee not found"}

@app.post("/api/bulk_approve")
def bulk_approve(payload: BulkApprovalPayload):
    from generate_dummy_excel import approve_excel_record
    for code in payload.employee_codes: approve_excel_record("dummy_attendance_records.xlsx", code)
    return {"status": "success"}

@app.post("/api/edit_employee")
def edit_employee(payload: EditPayload):
    from generate_dummy_excel import update_excel_status
    success = update_excel_status("dummy_attendance_records.xlsx", payload.employee_code, payload.new_status)
    if success: return {"status": "success"}
    return {"status": "error", "message": "Employee not found"}

@app.post("/api/expire_token")
def expire_token(payload: ExpirePayload):
    with SessionLocal() as db:
        token = db.query(EmailToken).filter(
            EmailToken.employee_code == payload.employee_code,
            EmailToken.is_used == False,
            EmailToken.is_expired == False,
        ).order_by(EmailToken.sent_at.desc()).first()
        if not token:
            return {"status": "error", "message": "No active token found"}
        token.is_expired = True
        db.commit()
        from generate_dummy_excel import update_excel_status
        update_excel_status("dummy_attendance_records.xlsx", payload.employee_code, "Expired")
        return {"status": "success"}

def _send_emails(employee_codes, db_session, custom_subject=None, custom_body=None):
    settings = db_session.query(SystemSettings).first()
    if not settings or not settings.smtp_password:
        return {"status": "error", "message": "SMTP credentials not configured in settings."}
    sender = settings.sender_email
    host = settings.smtp_host
    port = settings.smtp_port
    pwd = settings.smtp_password
    employees_data = parse_excel("dummy_attendance_records.xlsx")
    code_to_emp = {e["employee_code"]: e for e in employees_data}
    sent_count = 0
    try:
        server = smtplib.SMTP(host, port)
        server.starttls()
        server.login(sender, pwd)
        local_ip = get_local_ip()
        public_url = os.environ.get("PUBLIC_URL") or f"http://{local_ip}:8000"
        for code in employee_codes:
            emp = code_to_emp.get(code)
            if not emp or not emp.get("email"): continue
            new_token = str(uuid.uuid4())
            expires_at = datetime.now(timezone.utc) + timedelta(days=7)
            db_token = EmailToken(token=new_token, employee_code=code, is_used=False, is_expired=False, expires_at=expires_at)
            db_session.add(db_token)
            db_session.flush()
            emp_name = emp["full_name"]
            emp_email = emp["email"]
            days_worked = emp["days_worked"]
            approved_leaves = emp.get("approved_leaves", 0)
            unapproved_absences = emp.get("unapproved_absences", 0)
            correction_link = f"{public_url}/correction?token={new_token}"

            subject = custom_subject or "Action Required: Attendance Verification"
            msg = MIMEMultipart("alternative")
            msg['Subject'] = subject
            msg['From'] = sender
            msg['To'] = emp_email

            # Build email body — use custom body if provided, else default template
            if custom_body:
                # Wrap custom body in branded template, inject the correction link button
                custom_body_html = custom_body.replace("\n", "<br>")
                html = f"""<html><head><style>body{{font-family:'Segoe UI',Arial,sans-serif;color:#333;line-height:1.6;background:#f0f4f8;padding:20px;margin:0}}.wrapper{{max-width:620px;margin:0 auto}}.header{{background:#1a237e;color:#fff;padding:24px 30px;border-radius:8px 8px 0 0}}.header h1{{margin:0;font-size:20px;font-weight:600}}.body{{background:#fff;padding:30px;border:1px solid #e0e0e0;border-top:none}}.cta{{text-align:center;margin:28px 0 10px}}.cta a{{display:inline-block;background:#1a237e;color:#fff!important;text-decoration:none;padding:14px 36px;border-radius:6px;font-weight:600;font-size:15px}}.expiry{{text-align:center;font-size:12px;color:#999;margin-top:8px}}.footer{{background:#f5f5f5;padding:16px 30px;border:1px solid #e0e0e0;border-top:none;border-radius:0 0 8px 8px;font-size:12px;color:#999;text-align:center}}</style></head><body><div class="wrapper"><div class="header"><h1>Axian Attendance Verification</h1></div><div class="body"><p>Hello <strong>{emp_name}</strong>,</p><p>{custom_body_html}</p><div class="cta"><a href="{correction_link}">Review &amp; Submit Correction</a></div><div class="expiry">This link is unique to you and expires in 7 days. Do not share it.</div></div><div class="footer">Automated message from Axian Admin Panel. Do not reply.</div></div></body></html>"""
            else:
                html = f"""<html><head><style>body{{font-family:'Segoe UI',Arial,sans-serif;color:#333;line-height:1.6;background:#f0f4f8;padding:20px;margin:0}}.wrapper{{max-width:620px;margin:0 auto}}.header{{background:#1a237e;color:#fff;padding:24px 30px;border-radius:8px 8px 0 0}}.header h1{{margin:0;font-size:20px;font-weight:600}}.header p{{margin:4px 0 0;font-size:13px;opacity:.8}}.body{{background:#fff;padding:30px;border:1px solid #e0e0e0;border-top:none}}.stat-grid{{display:flex;gap:12px;margin:20px 0}}.stat-box{{flex:1;text-align:center;padding:16px 10px;background:#f5f7ff;border-radius:8px;border:1px solid #e8eaf6}}.stat-num{{font-size:28px;font-weight:700;color:#1a237e;display:block}}.stat-label{{font-size:12px;color:#666;margin-top:4px}}.stat-box.warn .stat-num{{color:#c62828}}.stat-box.warn{{background:#fff5f5;border-color:#ffcdd2}}.cta{{text-align:center;margin:28px 0 10px}}.cta a{{display:inline-block;background:#1a237e;color:#fff!important;text-decoration:none;padding:14px 36px;border-radius:6px;font-weight:600;font-size:15px}}.expiry{{text-align:center;font-size:12px;color:#999;margin-top:8px}}.footer{{background:#f5f5f5;padding:16px 30px;border:1px solid #e0e0e0;border-top:none;border-radius:0 0 8px 8px;font-size:12px;color:#999;text-align:center}}</style></head><body><div class="wrapper"><div class="header"><h1>Axian Attendance Verification</h1><p>Please review and confirm your attendance records for August 2026</p></div><div class="body"><p>Hello <strong>{emp_name}</strong>,</p><p>Your attendance data has been recorded. Please review and submit corrections if anything is incorrect.</p><div class="stat-grid"><div class="stat-box"><span class="stat-num">{days_worked}</span><div class="stat-label">Days Worked</div></div><div class="stat-box"><span class="stat-num">{approved_leaves}</span><div class="stat-label">Approved Leaves</div></div><div class="stat-box warn"><span class="stat-num">{unapproved_absences}</span><div class="stat-label">Unresolved Absences</div></div></div><div class="cta"><a href="{correction_link}">Review &amp; Submit Correction</a></div><div class="expiry">This link is unique to you and expires in 7 days. Do not share it.</div></div><div class="footer">Automated message from Axian Admin Panel. Do not reply.</div></div></body></html>"""

            part = MIMEText(html, 'html')
            msg.attach(part)
            server.send_message(msg)
            logger.info(f"[EMAIL SENT] To: {emp_email} | Token: {new_token}")
            sent_count += 1
            from generate_dummy_excel import update_excel_status
            update_excel_status("dummy_attendance_records.xlsx", code, "Email Sent")
        db_session.commit()
        server.quit()
        return {"status": "success", "message": f"Sent {sent_count} email(s)", "sent": sent_count}
    except Exception as e:
        db_session.rollback()
        logger.error(f"SMTP Error: {e}")
        return {"status": "error", "message": f"SMTP Error: {str(e)}"}

@app.post("/api/send_email")
def send_email(payload: EmailPayload):
    with SessionLocal() as db:
        return _send_emails(payload.employee_codes, db, payload.custom_subject, payload.custom_body)


def _error_page(title, message, icon="!"):
    return f"""<!DOCTYPE html><html lang="en"><head><title>{title} - Axian</title><meta name="viewport" content="width=device-width,initial-scale=1"><style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:'Segoe UI',Arial,sans-serif;background:#f0f4f8;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}}.card{{background:#fff;border-radius:12px;padding:48px 40px;max-width:480px;width:100%;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.08)}}.icon{{font-size:56px;margin-bottom:20px}}h1{{font-size:22px;color:#1a237e;margin-bottom:12px}}p{{color:#555;line-height:1.6;font-size:15px}}</style></head><body><div class="card"><div class="icon">{icon}</div><h1>{title}</h1><p>{message}</p></div></body></html>"""

@app.get("/correction", response_class=HTMLResponse)
def get_correction_form(token: str = None, code: str = None):
    if code and not token:
        return HTMLResponse(_error_page("Link Invalid", "This link is no longer valid. Please contact your administrator to resend a new verification email.", "🔗"), status_code=410)
    if not token:
        return HTMLResponse(_error_page("Invalid Link", "This link is missing a security token. Please use the link from your email.", "⚠️"), status_code=400)
    with SessionLocal() as db:
        db_token = db.query(EmailToken).filter(EmailToken.token == token).first()
        if not db_token:
            return HTMLResponse(_error_page("Link Not Found", "This link does not exist or has been removed.", "🔍"), status_code=404)
        if db_token.is_used:
            return HTMLResponse(_error_page("Response Already Submitted", "Your attendance correction has already been submitted using this link. If you need to make further changes, please ask your administrator to send a new email.", "✅"), status_code=410)
        if db_token.is_expired:
            return HTMLResponse(_error_page("Link Expired", "This link has been expired by your administrator. Please contact HR to request a new verification email.", "⏰"), status_code=410)
        if db_token.expires_at:
            now = datetime.now(timezone.utc)
            exp = db_token.expires_at
            if exp.tzinfo is None: exp = exp.replace(tzinfo=timezone.utc)
            if now > exp:
                return HTMLResponse(_error_page("Link Expired", "This verification link has expired (valid for 7 days). Please contact HR to request a new email.", "⏰"), status_code=410)
        employee_code = db_token.employee_code
        employees_data = parse_excel("dummy_attendance_records.xlsx")
        emp = next((e for e in employees_data if e["employee_code"] == employee_code), None)
        if not emp:
            return HTMLResponse(_error_page("Employee Not Found", "We could not locate your attendance record. Please contact HR.", "👤"), status_code=404)
    first_letter = emp['full_name'][0].upper()
    days = int(emp['days_worked']) if emp['days_worked'] != '' else 0
    leaves = int(emp.get('approved_leaves', 0)) if emp.get('approved_leaves', 0) != '' else 0
    absences = int(emp.get('unapproved_absences', 0)) if emp.get('unapproved_absences', 0) != '' else 0
    return HTMLResponse(f"""<!DOCTYPE html><html lang="en"><head><title>Attendance Correction - Axian</title><meta name="viewport" content="width=device-width,initial-scale=1"><style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:'Segoe UI',Arial,sans-serif;background:#f0f4f8;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;color:#333}}.card{{background:#fff;border-radius:12px;max-width:520px;width:100%;box-shadow:0 4px 24px rgba(0,0,0,.08);overflow:hidden}}.card-header{{background:#1a237e;color:#fff;padding:24px 28px}}.card-header h1{{font-size:18px;font-weight:600}}.card-header p{{font-size:13px;opacity:.8;margin-top:4px}}.card-body{{padding:28px}}.emp-info{{background:#f5f7ff;border-radius:8px;padding:14px 18px;margin-bottom:24px;display:flex;align-items:center;gap:14px}}.emp-avatar{{width:40px;height:40px;border-radius:50%;background:#1a237e;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:16px;flex-shrink:0}}.emp-name{{font-weight:600;font-size:15px;color:#1a237e}}.emp-code{{font-size:12px;color:#666}}.current-stats{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:24px}}.stat{{text-align:center;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:12px 8px}}.stat-n{{font-size:22px;font-weight:700;color:#1a237e}}.stat-l{{font-size:11px;color:#6b7280;margin-top:2px}}.form-group{{margin-bottom:18px}}label{{display:block;font-size:13px;font-weight:600;color:#374151;margin-bottom:6px}}input[type=number]{{width:100%;padding:10px 14px;border:1px solid #d1d5db;border-radius:8px;font-size:15px;transition:border .2s;outline:none;-moz-appearance:textfield}}input[type=number]::-webkit-outer-spin-button,input[type=number]::-webkit-inner-spin-button{{-webkit-appearance:none;margin:0}}input[type=number]:focus{{border-color:#1a237e;box-shadow:0 0 0 3px rgba(26,35,126,.1)}}.note{{font-size:12px;color:#9ca3af;margin-top:4px}}.error-box{{background:#ffebee;border:1px solid #ef9a9a;border-radius:8px;padding:10px 14px;font-size:13px;color:#c62828;margin-bottom:14px;display:none}}.submit-btn{{width:100%;padding:14px;background:#1a237e;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;transition:background .2s;margin-top:8px}}.submit-btn:hover{{background:#283593}}.submit-btn:disabled{{opacity:.5;cursor:not-allowed}}.card-footer{{background:#f9fafb;border-top:1px solid #e5e7eb;padding:14px 28px;font-size:12px;color:#9ca3af;text-align:center}}</style></head><body><div class="card"><div class="card-header"><h1>Attendance Correction Form</h1><p>August 2026 &mdash; Please review and correct if anything is wrong</p></div><div class="card-body"><div class="emp-info"><div class="emp-avatar">{first_letter}</div><div><div class="emp-name">{emp['full_name']}</div><div class="emp-code">{employee_code}</div></div></div><div class="current-stats"><div class="stat"><div class="stat-n">{days}</div><div class="stat-l">Days Worked</div></div><div class="stat"><div class="stat-n">{leaves}</div><div class="stat-l">Approved Leaves</div></div><div class="stat"><div class="stat-n">{absences}</div><div class="stat-l">Absences</div></div></div><div id="errBox" class="error-box"></div><form id="corrForm" action="/api/submit_correction" method="POST" onsubmit="return validateForm()"><input type="hidden" name="token" value="{token}"><div class="form-group"><label>Corrected Days Worked <span style="color:#9ca3af;font-weight:400;font-size:11px">(0&ndash;31, whole numbers only)</span></label><input type="number" step="1" min="0" max="31" name="days_worked" id="dw" value="{days}" required></div><div class="form-group"><label>Approved Leaves <span style="color:#9ca3af;font-weight:400;font-size:11px">(0&ndash;31)</span></label><input type="number" step="1" min="0" max="31" name="approved_leaves" id="al" value="{leaves}" required></div><div class="form-group"><label>Unapproved Absences <span style="color:#9ca3af;font-weight:400;font-size:11px">(0&ndash;31)</span></label><input type="number" step="1" min="0" max="31" name="unapproved_absences" id="ua" value="{absences}" required></div><button type="submit" class="submit-btn" id="submitBtn">Submit Correction</button></form></div><div class="card-footer">This link is one-time use only. Once submitted it cannot be reopened.</div></div><script>function validateForm(){{var dw=parseInt(document.getElementById('dw').value)||0;var al=parseInt(document.getElementById('al').value)||0;var ua=parseInt(document.getElementById('ua').value)||0;var box=document.getElementById('errBox');var errors=[];if(dw<0||dw>31)errors.push('Days Worked must be 0–31.');if(al<0||al>31)errors.push('Approved Leaves must be 0–31.');if(ua<0||ua>31)errors.push('Absences must be 0–31.');if(dw+al+ua>31)errors.push('Total Days + Leaves + Absences cannot exceed 31.');if(errors.length){{box.textContent=errors.join(' | ');box.style.display='block';return false;}}box.style.display='none';document.getElementById('submitBtn').disabled=true;document.getElementById('submitBtn').textContent='Submitting...';return true;}}</script></body></html>""")

@app.post("/api/submit_correction", response_class=HTMLResponse)
def submit_correction(token: str = Form(...), days_worked: int = Form(...), approved_leaves: int = Form(...), unapproved_absences: int = Form(...)):
    # ── Server-side validation ──────────────────────────────────────────────
    errors = []
    if days_worked < 0 or days_worked > 31:
        errors.append("Days Worked must be between 0 and 31.")
    if approved_leaves < 0 or approved_leaves > 31:
        errors.append("Approved Leaves must be between 0 and 31.")
    if unapproved_absences < 0 or unapproved_absences > 31:
        errors.append("Unapproved Absences must be between 0 and 31.")
    if days_worked + approved_leaves + unapproved_absences > 31:
        errors.append("Total of Days Worked + Leaves + Absences cannot exceed 31.")
    if errors:
        return HTMLResponse(_error_page(
            "Invalid Submission",
            "Please correct the following: " + " | ".join(errors),
            "⚠️"
        ), status_code=422)

    # ── Token Validation ────────────────────────────────────────────────────
    employee_code = None
    try:
        with SessionLocal() as db:
            db_token = db.query(EmailToken).filter(EmailToken.token == token).first()
            if not db_token:
                return HTMLResponse(_error_page("Link Not Found", "This link does not exist. Please contact HR.", "🔍"), status_code=404)
            if db_token.is_used:
                return HTMLResponse(_error_page("Already Submitted", "You have already submitted a response using this link.", "✅"), status_code=410)
            if db_token.is_expired:
                return HTMLResponse(_error_page("Link Expired", "This link has been expired by your administrator.", "⏰"), status_code=410)
            if db_token.expires_at:
                now = datetime.now(timezone.utc)
                exp = db_token.expires_at
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if now > exp:
                    return HTMLResponse(_error_page("Link Expired", "This link expired after 7 days.", "⏰"), status_code=410)
            employee_code = db_token.employee_code
            db_token.is_used = True
            db_token.used_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as ex:
        logger.error(f"Token DB error: {ex}")
        return HTMLResponse(_error_page("Server Error", "A database error occurred. Please try again or contact HR.", "❌"), status_code=500)

    # ── Update Excel ────────────────────────────────────────────────────────
    try:
        filepath = "dummy_attendance_records.xlsx"
        success = update_excel_record(filepath, employee_code, days_worked, approved_leaves, unapproved_absences)
    except Exception as ex:
        logger.error(f"Excel update error: {ex}")
        return HTMLResponse(_error_page("Save Error", "Your response was recorded but we could not update the attendance file. Please contact HR.", "⚠️"), status_code=500)

    if success:
        return HTMLResponse("""<!DOCTYPE html><html lang="en"><head><title>Submitted - Axian</title><meta name="viewport" content="width=device-width,initial-scale=1"><style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Segoe UI',Arial,sans-serif;background:#f0f4f8;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}.card{background:#fff;border-radius:12px;padding:48px 40px;max-width:480px;width:100%;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.08)}.icon{font-size:56px;margin-bottom:20px}h1{font-size:22px;color:#1a237e;margin-bottom:12px}p{color:#555;line-height:1.6;font-size:15px}.badge{display:inline-block;background:#e8f5e9;color:#2e7d32;border-radius:20px;padding:6px 18px;font-size:13px;font-weight:600;margin-top:20px}</style></head><body><div class="card"><div class="icon">&#x2705;</div><h1>Correction Submitted!</h1><p>Your attendance correction has been recorded. Your administrator will review it shortly.</p><div class="badge">You may now close this window</div></div></body></html>""")
    return HTMLResponse(_error_page("Submission Error", "We could not find your attendance record. Please contact HR.", "❌"), status_code=500)

@app.get("/api/settings")
def get_settings():
    with SessionLocal() as db:
        settings = db.query(SystemSettings).first()
        if settings: return settings.to_dict()
        return {
            "sender_email": "admin@axian.com", 
            "trigger_time": "17:00", 
            "smtp_password": "", 
            "smtp_host": "smtp.gmail.com", 
            "smtp_port": 587,
            "schedule_days_before_end": 5,
            "schedule_reminder_days": 2
        }

@app.post("/api/settings")
def update_settings(payload: SettingsPayload):
    env_path = ".env"
    if not os.path.exists(env_path):
        open(env_path, "w").close()

    with SessionLocal() as db:
        settings = db.query(SystemSettings).first()
        new_password = None
        if settings:
            settings.sender_email = payload.sender_email
            if payload.trigger_time:
                settings.trigger_time = payload.trigger_time
            if payload.smtp_password and payload.smtp_password != "********":
                settings.smtp_password = payload.smtp_password
                new_password = payload.smtp_password
            else:
                new_password = settings.smtp_password
            settings.smtp_host = payload.smtp_host
            settings.smtp_port = payload.smtp_port
        else:
            new_password = payload.smtp_password if payload.smtp_password != "********" else None
            settings = SystemSettings(
                sender_email=payload.sender_email,
                trigger_time=payload.trigger_time or "17:00",
                smtp_password=new_password,
                smtp_host=payload.smtp_host,
                smtp_port=payload.smtp_port,
            )
            db.add(settings)
        db.commit()

        set_key(env_path, "SMTP_SENDER_EMAIL", payload.sender_email)
        set_key(env_path, "SMTP_HOST", payload.smtp_host)
        set_key(env_path, "SMTP_PORT", str(payload.smtp_port))
        if new_password:
            set_key(env_path, "SMTP_PASSWORD", new_password)

        return {"status": "success", "settings": settings.to_dict()}

@app.post("/api/scheduler")
def update_scheduler(payload: SchedulerPayload):
    env_path = ".env"
    if not os.path.exists(env_path):
        open(env_path, "w").close()

    with SessionLocal() as db:
        settings = db.query(SystemSettings).first()
        if settings:
            settings.trigger_time = payload.trigger_time
            settings.schedule_days_before_end = payload.schedule_days_before_end
            settings.reminders_json = payload.reminders_json
            settings.active_schedule_id = payload.active_schedule_id
            settings.schedules_json = payload.schedules_json
        else:
            settings = SystemSettings(
                trigger_time=payload.trigger_time,
                schedule_days_before_end=payload.schedule_days_before_end,
                reminders_json=payload.reminders_json,
                active_schedule_id=payload.active_schedule_id,
                schedules_json=payload.schedules_json
            )
            db.add(settings)
        db.commit()
        update_scheduler_trigger(payload.trigger_time)

        set_key(env_path, "SMTP_TRIGGER_TIME", payload.trigger_time)
        return {"status": "success", "settings": settings.to_dict()}

@app.post("/api/run_pipeline")
def run_pipeline():
    with SessionLocal() as db:
        orch = AttendanceVerificationOrchestrator(db, config, mock_api=True)
        result = orch.run_month_end_pipeline("2026-08", auto_resolve_demo=True)
        safe_result = {"batch_id": result["batch_id"], "period_key": result["period_key"], "total_ingested": result["total_ingested"], "discrepancies_flagged": result["discrepancies_flagged"], "exceptions_queued": result["exceptions_queued"], "open_exceptions_remaining": result["open_exceptions_remaining"], "approved_headcount_snapshotted": result["approved_headcount_snapshotted"], "snapshot": result["snapshot"]}
        return {"status": "success", "result": safe_result}

@app.post("/api/verify_snapshot/{snapshot_id}")
def verify_snapshot(snapshot_id: str):
    with SessionLocal() as db:
        snapshot_engine = ImmutableSnapshotEngine(db, config.snapshot)
        is_valid, msg = snapshot_engine.verify_snapshot_integrity(snapshot_id)
        return {"is_valid": is_valid, "message": msg}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
