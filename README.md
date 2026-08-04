# Attendance Automation (Axian x nGAGE)

A small demo project for Attendance Verification & Billing automation integrating with nGAGE APIs.

## Overview

This repository contains a prototype pipeline that:

- Seeds master HR data
- Runs validation and exception workflows
- Produces immutable billing snapshots

## Quickstart

1. Create and activate a Python virtual environment (Windows PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Run the demo pipeline:

```powershell
python demo_pipeline.py
```

## Tests

Run unit tests (if present):

```powershell
python -m unittest discover -v
```

## Notes

- This repository was pushed to GitHub at `aligit56/attendance-automation`.
- If you run into missing dependency issues on MSYS environments, prefer using a system Python with working `pip` or use WSL/Windows-native Python.

## License

Provided as-is for demo purposes.
