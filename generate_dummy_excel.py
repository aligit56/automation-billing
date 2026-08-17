import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter

def generate_excel(filepath="dummy_attendance_records.xlsx"):
    # Define the data
    data = [
        {
            "period_key": "2026-08", "employee_code": "EMP-1001", "full_name": "Alice Smith", 
            "email": "workwithzain09@gmail.com", "location_code": "US-MAIN", "days_worked": 22.0, 
            "approved_leaves": 0.0, "unapproved_absences": 0.0, "status": "ACTIVE", 
            "verification_status": "Verified", "employee_notes": "",
            "corrected_days": 22.0, "corrected_leaves": 0.0, "corrected_absences": 0.0
        },
        {
            "period_key": "2026-08", "employee_code": "EMP-1002", "full_name": "Bob Jones", 
            "email": "241896@students.au.edu.pk", "location_code": "US-MAIN", "days_worked": 20.0, 
            "approved_leaves": 2.0, "unapproved_absences": 0.0, "status": "ACTIVE", 
            "verification_status": "Correction Submitted", "employee_notes": "Sick leave approved",
            "corrected_days": 22.0, "corrected_leaves": 0.0, "corrected_absences": 0.0
        },
        {
            "period_key": "2026-08", "employee_code": "EMP-1004", "full_name": "Diana Prince", 
            "email": "zainkhantge2@gmail.com", "location_code": "US-MAIN", "days_worked": 18.0, 
            "approved_leaves": 0.0, "unapproved_absences": 0.0, "status": "ACTIVE", 
            "verification_status": "Awaiting Response", "employee_notes": "Missed some days",
            "corrected_days": "", "corrected_leaves": "", "corrected_absences": ""
        },
        {
            "period_key": "2026-08", "employee_code": "EMP-1003", "full_name": "Charlie Brown", 
            "email": "beattlerokkie17679@gmail.com", "location_code": "US-MAIN", "days_worked": 19.0, 
            "approved_leaves": 0.0, "unapproved_absences": 3.0, "status": "ACTIVE", 
            "verification_status": "Pending", "employee_notes": "No call no show",
            "corrected_days": "", "corrected_leaves": "", "corrected_absences": ""
        },
        {
            "period_key": "2026-08", "employee_code": "EMP-1005", "full_name": "Eve Adams", 
            "email": "workwithzain02@gmail.com", "location_code": "US-MAIN", "days_worked": 22.0, 
            "approved_leaves": 0.0, "unapproved_absences": 0.0, "status": "INACTIVE", 
            "verification_status": "Rejected", "employee_notes": "Was terminated mid-month",
            "corrected_days": 25.0, "corrected_leaves": 0.0, "corrected_absences": 0.0
        },
        {
            "period_key": "2026-08", "employee_code": "EMP-9999", "full_name": "Ghost Employee", 
            "email": "ghost@axian.com", "location_code": "US-MAIN", "days_worked": 22.0, 
            "approved_leaves": 0.0, "unapproved_absences": 0.0, "status": "ACTIVE", 
            "verification_status": "", "employee_notes": "Not in master DB",
            "corrected_days": "", "corrected_leaves": "", "corrected_absences": ""
        },
        {
            "period_key": "2026-08", "employee_code": "EMP-1006", "full_name": "Muhammad Yaseen", 
            "email": "muhammadyaseen.ysn@gmail.com", "location_code": "US-MAIN", "days_worked": 21.0, 
            "approved_leaves": 1.0, "unapproved_absences": 0.0, "status": "ACTIVE", 
            "verification_status": "", "employee_notes": "",
            "corrected_days": "", "corrected_leaves": "", "corrected_absences": ""
        },
    ]

    df = pd.DataFrame(data)

    # Write to Excel
    writer = pd.ExcelWriter(filepath, engine='openpyxl')
    df.to_excel(writer, sheet_name="Attendance_Summary", index=False)
    writer.close()

    # Load workbook for formatting
    wb = load_workbook(filepath)
    ws = wb["Attendance_Summary"]

    # Formatting headers
    header_fill = PatternFill(start_color="00008B", end_color="00008B", fill_type="solid") # Dark Blue
    header_font = Font(color="FFFFFF", bold=True)

    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font

    # Number formatting
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=6, max_col=8):
        for cell in row:
            cell.number_format = '0.0'

    # Auto-fit columns
    for column_cells in ws.columns:
        length = max(len(str(cell.value)) for cell in column_cells) if column_cells else 10
        ws.column_dimensions[get_column_letter(column_cells[0].column)].width = length + 2

    # Data Validation
    dv = DataValidation(type="list", formula1='"Pending,Verified,Awaiting Response,Correction Submitted,Rejected"', allow_blank=True)
    ws.add_data_validation(dv)
    status_col_idx = df.columns.get_loc("verification_status") + 1
    col_letter = get_column_letter(status_col_idx)
    dv.add(f'{col_letter}2:{col_letter}{ws.max_row}')

    wb.save(filepath)
    print(f"Excel file successfully generated at {filepath}")

def parse_excel(filepath="dummy_attendance_records.xlsx"):
    """
    Parses the generated Excel file back into a list of dictionaries matching
    the ngage_client.py payload schema.
    """
    df = pd.read_excel(filepath, sheet_name="Attendance_Summary")
    
    # Handle NaN/Null values if necessary
    df = df.fillna("")
    
    records = df.to_dict(orient="records")
    return records

def update_excel_record(filepath, employee_code, days_worked, leaves, absences):
    """
    Updates an employee's attendance record in the Excel file without losing formatting.
    """
    wb = load_workbook(filepath)
    if "Attendance_Summary" not in wb.sheetnames:
        return False
        
    ws = wb["Attendance_Summary"]
    
    # Find headers to get column indices
    headers = {cell.value: cell.column for cell in ws[1]}
    
    code_col = headers.get("employee_code")
    days_col = headers.get("days_worked")
    leaves_col = headers.get("approved_leaves")
    abs_col = headers.get("unapproved_absences")
    
    # Ensure columns exist, if not we add logic to find them
    corr_days_col = headers.get("corrected_days")
    corr_leaves_col = headers.get("corrected_leaves")
    corr_abs_col = headers.get("corrected_absences")
    status_col = headers.get("verification_status")
    
    if not all([code_col, corr_days_col, status_col]):
        return False
        
    for row in range(2, ws.max_row + 1):
        if ws.cell(row=row, column=code_col).value == employee_code:
            ws.cell(row=row, column=corr_days_col).value = float(days_worked)
            ws.cell(row=row, column=corr_leaves_col).value = float(leaves)
            ws.cell(row=row, column=corr_abs_col).value = float(absences)
            ws.cell(row=row, column=status_col).value = "Correction Submitted"
            wb.save(filepath)
            return True
            
    return False

def approve_excel_record(filepath, employee_code):
    wb = load_workbook(filepath)
    if "Attendance_Summary" not in wb.sheetnames:
        return False
    ws = wb["Attendance_Summary"]
    headers = {cell.value: cell.column for cell in ws[1]}
    
    code_col = headers.get("employee_code")
    days_col = headers.get("days_worked")
    corr_days_col = headers.get("corrected_days")
    status_col = headers.get("verification_status")
    
    for row in range(2, ws.max_row + 1):
        if ws.cell(row=row, column=code_col).value == employee_code:
            corr_days = ws.cell(row=row, column=corr_days_col).value
            if corr_days != "" and corr_days is not None:
                ws.cell(row=row, column=days_col).value = float(corr_days)
            ws.cell(row=row, column=status_col).value = "Verified"
            wb.save(filepath)
            return True
    return False

def reject_excel_record(filepath, employee_code):
    wb = load_workbook(filepath)
    if "Attendance_Summary" not in wb.sheetnames:
        return False
    ws = wb["Attendance_Summary"]
    headers = {cell.value: cell.column for cell in ws[1]}
    
    code_col = headers.get("employee_code")
    status_col = headers.get("verification_status")
    
    for row in range(2, ws.max_row + 1):
        if ws.cell(row=row, column=code_col).value == employee_code:
            ws.cell(row=row, column=status_col).value = "Rejected"
            wb.save(filepath)
            return True
def update_excel_status(filepath, employee_code, new_status):
    wb = load_workbook(filepath)
    if "Attendance_Summary" not in wb.sheetnames:
        return False
    ws = wb["Attendance_Summary"]
    headers = {cell.value: cell.column for cell in ws[1]}
    
    code_col = headers.get("employee_code")
    status_col = headers.get("verification_status")
    
    for row in range(2, ws.max_row + 1):
        if ws.cell(row=row, column=code_col).value == employee_code:
            ws.cell(row=row, column=status_col).value = new_status
            wb.save(filepath)
            return True
    return False

if __name__ == "__main__":
    import os
    
    # Generate the excel file
    filepath = "dummy_attendance_records.xlsx"
    generate_excel(filepath)
    
    # Test the parser
    print("-" * 50)
    print("Testing Parser Utility:")
    parsed_data = parse_excel(filepath)
    
    for row in parsed_data[:2]:
        print(row)
