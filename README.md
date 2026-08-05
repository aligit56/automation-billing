# Automation Billing

A demo repository for attendance verification and billing automation integrating with nGAGE APIs.

## Overview

This repository contains a prototype pipeline that:

- Seeds master HR and attendance data
- Runs validation and exception workflows
- Produces immutable billing snapshots
- Supports a demo dashboard and billed snapshot generation

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

## Files

- `app.py` – entry point for the application
- `demo_pipeline.py` – demo workflow execution script
- `exception_workflow.py` – exception handling and validation logic
- `snapshot_engine.py` – billing snapshot generation
- `validation_engine.py` – attendance validation logic
- `ngage_client.py` – nGAGE API integration
- `static/` – demo dashboard front-end assets

## Tests

Run unit tests using:

```powershell
python -m unittest discover -v
```

## Notes

- Repository is published to GitHub at `https://github.com/aligit56/automation-billing`
- Keep generated files, logs, and local environments out of source control using `.gitignore`

## License

Provided as-is for demo purposes.
