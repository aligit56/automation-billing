import logging
from fastapi import FastAPI, Form
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
from pydantic import BaseModel
from typing import List
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import config
from models import Base, Employee, ExceptionRecord, ValidationRule, BillingSnapshot, SystemSettings
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

# Seed initial data and defaults
with SessionLocal() as db:
    orchestrator = AttendanceVerificationOrchestrator(db, config, mock_api=True)
    orchestrator.seed_initial_master_data("2026-08")
    
    if not db.query(SystemSettings).first():
        db.add(SystemSettings(sender_email="admin@axian.com", trigger_time="17:00"))
        db.commit()

# --- Scheduler Setup ---
scheduler = BackgroundScheduler()

def scheduled_job():
    logger.info("Running automated scheduled job (e.g., pipeline or reminders)...")
    with SessionLocal() as db:
        orch = AttendanceVerificationOrchestrator(db, config, mock_api=True)
        orch.run_month_end_pipeline("2026-08", auto_resolve_demo=True)

def update_scheduler_trigger(time_str: str):
    """Updates the apscheduler job trigger."""
    hour, minute = time_str.split(":")
    scheduler.reschedule_job('daily_job', trigger=CronTrigger(hour=hour, minute=minute))
    logger.info(f"Scheduler updated to run daily at {time_str}")

# Add the job with a default time (will be updated shortly after start)
scheduler.add_job(scheduled_job, CronTrigger(hour=17, minute=0), id='daily_job')
scheduler.start()

# Sync scheduler with DB on startup
with SessionLocal() as db:
    settings = db.query(SystemSettings).first()
    if settings:
        update_scheduler_trigger(settings.trigger_time)
# -----------------------

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
        
        return {
            "total_employees": employees,
            "exceptions": [e.to_dict() for e in exceptions],
            "rules": [r.to_dict() for r in rules],
            "snapshots": [s.to_dict() for s in snapshots]
        }

@app.get("/api/employees")
def get_employees():
    """Reads directly from the dummy Excel sheet."""
    filepath = "dummy_attendance_records.xlsx"
    if not os.path.exists(filepath):
        from generate_dummy_excel import generate_excel
        generate_excel(filepath)
    return parse_excel(filepath)

class EmailPayload(BaseModel):
    employee_codes: List[str]

@app.post("/api/send_email")
def send_email(payload: EmailPayload):
    with SessionLocal() as db:
        settings = db.query(SystemSettings).first()
        if not settings or not settings.smtp_password:
            return {"status": "error", "message": "SMTP credentials not configured in settings."}
            
        sender = settings.sender_email
        host = settings.smtp_host
        port = settings.smtp_port
        pwd = settings.smtp_password
        
        employees_data = get_employees()
        code_to_emp = {e["employee_code"]: e for e in employees_data}
        
        sent_count = 0
        try:
            server = smtplib.SMTP(host, port)
            server.starttls()
            server.login(sender, pwd)
            
            for code in payload.employee_codes:
                emp = code_to_emp.get(code)
                if not emp or not emp["email"]:
                    continue
                
                emp_name = emp["full_name"]
                emp_email = emp["email"]
                days_worked = emp["days_worked"]
                approved_leaves = emp["approved_leaves"]
                unapproved_absences = emp["unapproved_absences"]
                
                msg = MIMEMultipart("alternative")
                msg['Subject'] = 'Action Required: Attendance Correction'
                msg['From'] = sender
                msg['To'] = emp_email
                
                local_ip = get_local_ip()
                html = f"""
                <html>
                  <head>
                    <style>
                      body {{ font-family: Arial, sans-serif; color: #333; line-height: 1.6; background-color: #f4f7f6; padding: 20px; }}
                      .container {{ max-width: 600px; margin: 0 auto; background: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
                      h2 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
                      .footer {{ margin-top: 30px; font-size: 12px; color: #7f8c8d; text-align: center; }}
                    </style>
                  </head>
                  <body>
                    <div class="container">
                      <h2>Attendance Summary</h2>
                      <p>Hello <strong>{emp_name}</strong>,</p>
                      <p>Please review your attendance records for the recent period. We have noted the following statistics:</p>
                      
                      <table width="100%" cellpadding="0" cellspacing="0" style="margin-top: 20px; margin-bottom: 20px;">
                        <tr>
                          <td align="center" style="padding: 15px; background: #ecf0f1; border-radius: 6px; width: 30%;">
                            <strong style="display: block; font-size: 24px; color: #2980b9;">{days_worked}</strong> Days Worked
                          </td>
                          <td width="5%"></td>
                          <td align="center" style="padding: 15px; background: #ecf0f1; border-radius: 6px; width: 30%;">
                            <strong style="display: block; font-size: 24px; color: #2980b9;">{approved_leaves}</strong> Approved Leaves
                          </td>
                          <td width="5%"></td>
                          <td align="center" style="padding: 15px; background: #ffebee; border-radius: 6px; width: 30%; color: #c0392b;">
                            <strong style="display: block; font-size: 24px; color: #c0392b;">{unapproved_absences}</strong> Absences
                          </td>
                        </tr>
                      </table>
                      
                      <div style="margin-top: 30px; text-align: center;">
                        <a href="{os.environ.get('PUBLIC_URL', 'http://localhost:8000')}/correction?code={code}" style="display: inline-block; background-color: #2980b9; color: #ffffff; text-decoration: none; padding: 12px 25px; border-radius: 4px; font-weight: bold; font-size: 16px;">Correct My Attendance</a>
                        <p style="font-size: 13px; color: #7f8c8d; margin-top: 10px;">Click the button above to securely submit corrections via our web portal.</p>
                      </div>
                      
                      <div class="footer">
                        <p>This is an automated message from the Axian Admin Panel.</p>
                      </div>
                    </div>
                  </body>
                </html>
                """
                part = MIMEText(html, 'html')
                msg.attach(part)
                
                server.send_message(msg)
                logger.info(f"[ACTUAL HTML EMAIL SENT] To: {emp_email}")
                sent_count += 1
                
            server.quit()
            return {"status": "success", "message": f"Sent {sent_count} HTML emails from {sender}"}
        except Exception as e:
            logger.error(f"SMTP Error: {e}")
            return {"status": "error", "message": f"SMTP Error: {e}"}

@app.get("/correction", response_class=HTMLResponse)
def get_correction_form(code: str):
    employees_data = get_employees()
    emp = next((e for e in employees_data if e["employee_code"] == code), None)
    
    if not emp:
        return "<html><body><h2>Error: Employee not found.</h2></body></html>"
        
    return f"""
    <html>
      <head>
        <title>Attendance Correction</title>
        <style>
          body {{ font-family: Arial, sans-serif; background: #f4f7f6; padding: 40px; color: #333; }}
          .container {{ background: #fff; max-width: 500px; margin: 0 auto; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
          h2 {{ color: #2c3e50; margin-top: 0; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
          label {{ display: block; margin-bottom: 5px; font-weight: bold; margin-top: 15px; color: #34495e; }}
          input {{ width: 100%; padding: 12px; margin-bottom: 5px; border: 1px solid #bdc3c7; border-radius: 4px; box-sizing: border-box; font-size: 16px; }}
          button {{ width: 100%; padding: 14px; background: #2980b9; color: white; border: none; border-radius: 4px; font-size: 16px; font-weight: bold; cursor: pointer; margin-top: 20px; }}
          button:hover {{ background: #2471a3; }}
        </style>
      </head>
      <body>
        <div class="container">
          <h2>Attendance Correction</h2>
          <p>Submitting corrections for <strong>{emp['full_name']}</strong> ({code}).</p>
          <form action="/api/submit_correction" method="POST">
            <input type="hidden" name="employee_code" value="{code}">
            
            <label>Days Worked</label>
            <input type="number" step="0.5" name="days_worked" value="{emp['days_worked']}" required>
            
            <label>Approved Leaves</label>
            <input type="number" step="0.5" name="approved_leaves" value="{emp['approved_leaves']}" required>
            
            <label>Unapproved Absences</label>
            <input type="number" step="0.5" name="unapproved_absences" value="{emp['unapproved_absences']}" required>
            
            <button type="submit">Submit Correction</button>
          </form>
        </div>
      </body>
    </html>
    """

@app.post("/api/submit_correction", response_class=HTMLResponse)
def submit_correction(
    employee_code: str = Form(...),
    days_worked: float = Form(...),
    approved_leaves: float = Form(...),
    unapproved_absences: float = Form(...)
):
    filepath = "dummy_attendance_records.xlsx"
    success = update_excel_record(filepath, employee_code, days_worked, approved_leaves, unapproved_absences)
    
    if success:
        return f"""
        <html>
            <body style="font-family: Arial; padding: 40px; text-align: center; background: #f4f7f6;">
                <div style="background: white; padding: 40px; border-radius: 8px; max-width: 500px; margin: 0 auto; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                    <h2 style="color: #27ae60;">Success!</h2>
                    <p>Your attendance correction for <strong>{employee_code}</strong> has been successfully submitted and saved.</p>
                    <p>You may now close this window.</p>
                </div>
            </body>
        </html>
        """
    else:
        return f"""
        <html>
            <body style="font-family: Arial; padding: 40px; text-align: center; background: #f4f7f6;">
                <div style="background: white; padding: 40px; border-radius: 8px; max-width: 500px; margin: 0 auto; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                    <h2 style="color: #c0392b;">Error</h2>
                    <p>Failed to find employee record for <strong>{employee_code}</strong>.</p>
                    <p>Please contact HR.</p>
                </div>
            </body>
        </html>
        """

class SettingsPayload(BaseModel):
    sender_email: str
    trigger_time: str
    smtp_password: str
    smtp_host: str
    smtp_port: int

@app.get("/api/settings")
def get_settings():
    with SessionLocal() as db:
        settings = db.query(SystemSettings).first()
        if settings:
            return settings.to_dict()
        return {"sender_email": "admin@axian.com", "trigger_time": "17:00", "smtp_password": "", "smtp_host": "smtp.gmail.com", "smtp_port": 587}

@app.post("/api/settings")
def update_settings(payload: SettingsPayload):
    with SessionLocal() as db:
        settings = db.query(SystemSettings).first()
        if settings:
            settings.sender_email = payload.sender_email
            settings.trigger_time = payload.trigger_time
            if payload.smtp_password and payload.smtp_password != "********":
                settings.smtp_password = payload.smtp_password
            settings.smtp_host = payload.smtp_host
            settings.smtp_port = payload.smtp_port
        else:
            settings = SystemSettings(
                sender_email=payload.sender_email, 
                trigger_time=payload.trigger_time,
                smtp_password=payload.smtp_password if payload.smtp_password != "********" else None,
                smtp_host=payload.smtp_host,
                smtp_port=payload.smtp_port
            )
            db.add(settings)
        db.commit()
        update_scheduler_trigger(payload.trigger_time)
        return {"status": "success", "settings": settings.to_dict()}

@app.post("/api/run_pipeline")
def run_pipeline():
    with SessionLocal() as db:
        orch = AttendanceVerificationOrchestrator(db, config, mock_api=True)
        result = orch.run_month_end_pipeline("2026-08", auto_resolve_demo=True)
        safe_result = {
            "batch_id": result["batch_id"],
            "period_key": result["period_key"],
            "total_ingested": result["total_ingested"],
            "discrepancies_flagged": result["discrepancies_flagged"],
            "exceptions_queued": result["exceptions_queued"],
            "open_exceptions_remaining": result["open_exceptions_remaining"],
            "approved_headcount_snapshotted": result["approved_headcount_snapshotted"],
            "snapshot": result["snapshot"]
        }
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
