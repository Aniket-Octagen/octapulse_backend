#!/usr/bin/env python3
"""
Theta PMO - Application Launcher
=================================
Runs both backend (Flask) and frontend (Vite) servers simultaneously.

Usage:
    python run_app.py

Author: Theta PMO Team
Date: 2026-02-17
"""

import subprocess
import sys
import time
import os
from pathlib import Path

# ANSI color codes for terminal output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'

def print_banner():
    """Print application startup banner."""
    banner = f"""
{Colors.CYAN}{Colors.BOLD}
╔══════════════════════════════════════════════════════════════╗
║                     THETA PMO SYSTEM                         ║
║              Professional Project Management Suite            ║
╚══════════════════════════════════════════════════════════════╝
{Colors.END}
{Colors.GREEN}Starting both Backend and Frontend servers...{Colors.END}
"""
    print(banner)

def check_python_version():
    """Check if Python version is compatible."""
    if sys.version_info < (3, 8):
        print(f"{Colors.RED}Error: Python 3.8 or higher is required{Colors.END}")
        print(f"Current version: {sys.version}")
        sys.exit(1)
    print(f"{Colors.GREEN}✓ Python version: {sys.version.split()[0]}{Colors.END}")

def check_node_installed():
    """Check if Node.js is installed."""
    try:
        result = subprocess.run(['node', '--version'], 
                              capture_output=True, 
                              text=True,
                              shell=True)
        if result.returncode == 0:
            print(f"{Colors.GREEN}✓ Node.js version: {result.stdout.strip()}{Colors.END}")
            return True
    except FileNotFoundError:
        pass
    
    print(f"{Colors.RED}✗ Node.js not found. Please install Node.js from https://nodejs.org/{Colors.END}")
    return False

def check_backend_dependencies():
    """Check if backend dependencies are installed."""
    try:
        import flask
        import openpyxl
        import werkzeug
        print(f"{Colors.GREEN}✓ Backend dependencies installed{Colors.END}")
        return True
    except ImportError as e:
        print(f"{Colors.YELLOW}⚠ Missing backend dependencies{Colors.END}")
        print(f"{Colors.YELLOW}Run: pip install -r requirements.txt{Colors.END}")
        return False

def check_frontend_dependencies():
    """Check if frontend node_modules exist."""
    frontend_path = Path(__file__).parent / "frontend"
    node_modules = frontend_path / "node_modules"
    
    if node_modules.exists():
        print(f"{Colors.GREEN}✓ Frontend dependencies installed{Colors.END}")
        return True
    else:
        print(f"{Colors.YELLOW}⚠ Frontend dependencies not installed{Colors.END}")
        print(f"{Colors.YELLOW}Run: cd frontend && npm install{Colors.END}")
        return False

def run_backend():
    """Start the Flask backend server."""
    print(f"\n{Colors.BLUE}{Colors.BOLD}[BACKEND] Starting Flask server on port 5000...{Colors.END}")
    
    # Use subprocess with shell=True for Windows compatibility
    if sys.platform == 'win32':
        backend_process = subprocess.Popen(
            ['python', 'backend_server.py'],
            shell=True,
            cwd=Path(__file__).parent
        )
    else:
        backend_process = subprocess.Popen(
            ['python3', 'backend_server.py'],
            cwd=Path(__file__).parent
        )
    
    return backend_process

def run_frontend():
    """Start the Vite frontend development server."""
    print(f"\n{Colors.CYAN}{Colors.BOLD}[FRONTEND] Starting Vite dev server...{Colors.END}")
    
    frontend_path = Path(__file__).parent / "frontend"
    
    # Use npm run dev with shell=True for Windows compatibility
    if sys.platform == 'win32':
        frontend_process = subprocess.Popen(
            ['npm', 'run', 'dev'],
            shell=True,
            cwd=frontend_path
        )
    else:
        frontend_process = subprocess.Popen(
            ['npm', 'run', 'dev'],
            cwd=frontend_path
        )
    
    return frontend_process

def main():
    """Main application launcher."""
    try:
        # Print banner
        print_banner()
        
        # Check system requirements
        print(f"\n{Colors.BOLD}Checking system requirements...{Colors.END}")
        check_python_version()
        
        if not check_node_installed():
            sys.exit(1)
        
        backend_ok = check_backend_dependencies()
        frontend_ok = check_frontend_dependencies()
        
        if not backend_ok or not frontend_ok:
            print(f"\n{Colors.YELLOW}Please install missing dependencies and try again.{Colors.END}")
            sys.exit(1)
        
        # Start servers
        print(f"\n{Colors.BOLD}Starting servers...{Colors.END}")
        backend_process = run_backend()
        time.sleep(2)  # Give backend time to start
        frontend_process = run_frontend()
        
        # Print success message
        print(f"""
{Colors.GREEN}{Colors.BOLD}
╔══════════════════════════════════════════════════════════════╗
║                    SERVERS RUNNING                           ║
╚══════════════════════════════════════════════════════════════╝
{Colors.END}
{Colors.CYAN}Backend API:{Colors.END}       http://localhost:5000
{Colors.CYAN}Frontend App:{Colors.END}      http://localhost:3000 (or next available port)

{Colors.YELLOW}Press Ctrl+C to stop both servers{Colors.END}
""")
        
        # Wait for processes
        try:
            backend_process.wait()
            frontend_process.wait()
        except KeyboardInterrupt:
            print(f"\n\n{Colors.YELLOW}Shutting down servers...{Colors.END}")
            backend_process.terminate()
            frontend_process.terminate()
            
            # Wait for graceful shutdown
            time.sleep(1)
            backend_process.kill()
            frontend_process.kill()
            
            print(f"{Colors.GREEN}✓ Servers stopped successfully{Colors.END}")
            sys.exit(0)
    
    except Exception as e:
        print(f"\n{Colors.RED}Error: {str(e)}{Colors.END}")
        sys.exit(1)

if __name__ == '__main__':
    main()
