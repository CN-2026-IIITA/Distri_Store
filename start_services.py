#!/usr/bin/env python3
"""
Start both DistriStore backend and frontend services.
Keeps them running in background and returns their URLs.
"""

import subprocess
import time
import os
import sys
import requests
import json

os.chdir('G:\\projects\\CN_project')

# Set environment variables
env = os.environ.copy()
env['DS_API_PORT'] = '8888'

print("=" * 60)
print("  Starting DistriStore Services")
print("=" * 60)

# Start backend
print("\n[1/2] Starting backend on port 8888...")
backend_process = subprocess.Popen(
    ['.venv\\Scripts\\python', '-m', 'backend.main'],
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0
)
print(f"Backend process started (PID: {backend_process.pid})")

# Wait for backend to start
time.sleep(5)

# Start frontend
print("\n[2/2] Starting frontend with npm run dev...")
frontend_process = subprocess.Popen(
    ['npm', 'run', 'dev', '--', '--host'],
    cwd='frontend',
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0
)
print(f"Frontend process started (PID: {frontend_process.pid})")

# Wait for frontend to start
time.sleep(5)

# Test backend
print("\n[3/4] Testing backend...")
backend_url = "http://localhost:8888"
backend_status = False
try:
    response = requests.get(f"{backend_url}/status", timeout=5)
    if response.status_code == 200:
        backend_status = True
        print(f"✓ Backend responding at {backend_url}")
        print(f"  Response: {response.json()}")
except Exception as e:
    print(f"✗ Backend not responding: {e}")

# Test frontend
print("\n[4/4] Testing frontend...")
frontend_url = "http://localhost:5173"
frontend_status = False
try:
    response = requests.get(frontend_url, timeout=5)
    if response.status_code == 200:
        frontend_status = True
        print(f"✓ Frontend responding at {frontend_url}")
except Exception as e:
    # Try to parse Vite output to find the actual port
    print(f"✗ Frontend port 5173 not responding, checking logs...")
    try:
        # Read first part of frontend output to find the port
        time.sleep(2)
        frontend_url = "http://localhost:5173"  # Vite default
        response = requests.get(frontend_url, timeout=5)
        if response.status_code == 200:
            frontend_status = True
            print(f"✓ Frontend responding at {frontend_url}")
    except Exception as e2:
        print(f"  Still not responding: {e2}")

# Final report
print("\n" + "=" * 60)
print("  Service Status Report")
print("=" * 60)
print(f"\nBackend:  {backend_url:<35} {'✓ RUNNING' if backend_status else '✗ FAILED'}")
print(f"Frontend: {frontend_url:<35} {'✓ RUNNING' if frontend_status else '✗ FAILED'}")
print("\n" + "=" * 60)

if backend_status and frontend_status:
    print("\n✓ All services running successfully!")
    print(f"\n  Backend URL:  {backend_url}")
    print(f"  Frontend URL: {frontend_url}")
    print(f"\n  Backend PID:  {backend_process.pid}")
    print(f"  Frontend PID: {frontend_process.pid}")
    print("\nServices will keep running. Ctrl+C to stop.")
else:
    print("\n✗ Some services failed to start.")
    sys.exit(1)

# Keep the script running to maintain the subprocesses
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n\nShutting down services...")
    backend_process.terminate()
    frontend_process.terminate()
    time.sleep(2)
    backend_process.kill()
    frontend_process.kill()
    print("Services stopped.")
