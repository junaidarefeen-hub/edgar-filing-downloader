@echo off
echo ============================================
echo  SEC EDGAR Filing Downloader - Setup
echo ============================================
echo.

:: Check for Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Install dependencies
echo Installing dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo Setup complete! Starting the application...
echo Open http://localhost:5000 in your browser.
echo Press Ctrl+C to stop the server.
echo.
python app.py
