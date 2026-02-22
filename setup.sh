#!/usr/bin/env bash
set -e

echo "============================================"
echo " SEC EDGAR Filing Downloader - Setup"
echo "============================================"
echo

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is not installed."
    echo "Install it from https://www.python.org/downloads/ or via your package manager."
    exit 1
fi

# Install dependencies
echo "Installing dependencies..."
pip3 install -r requirements.txt

echo
echo "Setup complete! Starting the application..."
echo "Open http://localhost:5000 in your browser."
echo "Press Ctrl+C to stop the server."
echo
python3 app.py
