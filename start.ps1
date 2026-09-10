# PMO EDDR Data Processing Application - Quick Start Script
# This script will help you get both backend and frontend running

Write-Host "==================================" -ForegroundColor Cyan
Write-Host "PMO Application Quick Start" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan
Write-Host ""

# Change to the script directory
Set-Location -Path $PSScriptRoot

# Check Python
Write-Host "[1/6] Checking Python installation..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "✓ $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "✗ Python not found. Please install Python 3.8 or higher." -ForegroundColor Red
    exit 1
}

# Check Node.js
Write-Host "[2/6] Checking Node.js installation..." -ForegroundColor Yellow
try {
    $nodeVersion = node --version 2>&1
    Write-Host "✓ Node.js $nodeVersion" -ForegroundColor Green
} catch {
    Write-Host "✗ Node.js not found. Please install Node.js 16 or higher." -ForegroundColor Red
    exit 1
}

# Install Python dependencies
Write-Host "[3/6] Installing Python dependencies..." -ForegroundColor Yellow
if (Test-Path "requirements.txt") {
    try {
        pip install -r requirements.txt -q
        Write-Host "✓ Python dependencies installed" -ForegroundColor Green
    } catch {
        Write-Host "⚠ Error installing Python dependencies" -ForegroundColor Red
    }
} else {
    Write-Host "⚠ requirements.txt not found" -ForegroundColor Yellow
}

# Install Node dependencies
Write-Host "[4/6] Installing Node dependencies..." -ForegroundColor Yellow
if (Test-Path "frontend/package.json") {
    Push-Location frontend
    try {
        npm install --silent 2>&1 | Out-Null
        Write-Host "✓ Node dependencies installed" -ForegroundColor Green
    } catch {
        Write-Host "⚠ Error installing Node dependencies" -ForegroundColor Red
    }
    Pop-Location
} else {
    Write-Host "⚠ frontend/package.json not found" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==================================" -ForegroundColor Cyan
Write-Host "Starting Application..." -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan
Write-Host ""

# Start Backend Server
Write-Host "[5/6] Starting Flask Backend (Port 5000)..." -ForegroundColor Yellow
$backend = Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; python backend_server.py" -PassThru
Start-Sleep -Seconds 3
Write-Host "✓ Backend server started (PID: $($backend.Id))" -ForegroundColor Green

# Start Frontend Dev Server
Write-Host "[6/6] Starting React Frontend (Port 3000)..." -ForegroundColor Yellow
$frontend = Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot\frontend'; npm run dev" -PassThru
Start-Sleep -Seconds 3
Write-Host "✓ Frontend dev server started (PID: $($frontend.Id))" -ForegroundColor Green

Write-Host ""
Write-Host "==================================" -ForegroundColor Green
Write-Host "✓ Application Started Successfully!" -ForegroundColor Green
Write-Host "==================================" -ForegroundColor Green
Write-Host ""
Write-Host "📱 Frontend: " -NoNewline; Write-Host "http://localhost:3000" -ForegroundColor Cyan
Write-Host "🔧 Backend API: " -NoNewline; Write-Host "http://localhost:5000/api" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press Ctrl+C in each window to stop the servers" -ForegroundColor Yellow
Write-Host ""
