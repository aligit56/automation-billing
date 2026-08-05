import subprocess
import time
import re
import os
import sys

def main():
    print("==================================================")
    print("      Axian Attendance System - Startup           ")
    print("==================================================")
    print("[1/2] Starting Secure Cloudflare Tunnel...")
    
    # Ensure any old log is removed
    if os.path.exists("cf.log"):
        os.remove("cf.log")
        
    # Start cloudflared in the background
    cf_process = subprocess.Popen(
        ["cloudflared.exe", "tunnel", "--url", "http://localhost:8000"],
        stdout=open("cf.log", "w"),
        stderr=subprocess.STDOUT
    )
    
    # Wait for the URL to appear in the log
    print("      Waiting for public URL (usually takes 5 seconds)...")
    public_url = None
    for _ in range(30):
        if os.path.exists("cf.log"):
            with open("cf.log", "r") as f:
                content = f.read()
                match = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', content)
                if match:
                    public_url = match.group(0)
                    break
        time.sleep(1)
        
    if not public_url:
        print("[-] Failed to get Cloudflare URL. Email links will default to localhost.")
    else:
        print(f"\n[+] SUCCESS! Your Public URL is: {public_url}")
        print("      (This URL will be automatically used in employee emails)\n")
        os.environ["PUBLIC_URL"] = public_url
        
    print("[2/2] Starting FastAPI Server...")
    print("      Admin Panel will be available at: http://localhost:8000")
    print("      Press Ctrl+C to stop the server and the tunnel.")
    print("==================================================\n")
    
    # Start FastAPI server
    try:
        subprocess.run(["uv", "run", "--python", ".venv_uv", "python", "app.py"])
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
    finally:
        cf_process.terminate()
        
if __name__ == "__main__":
    main()
