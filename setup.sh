#!/bin/bash
# Huginn Setup Script
set -e

echo "=== Huginn Setup ==="

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "Python 3 not found. Please install Python 3.10+."
    exit 1
fi

# Create virtual env if needed
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate
source venv/bin/activate

# Install
echo "Installing dependencies..."
pip install --upgrade pip
pip install -e .

# Install Playwright browsers
echo "Installing Playwright browsers..."
playwright install chromium --with-deps 2>/dev/null || playwright install chromium

# Copy env
if [ ! -f .env ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo "Please edit .env and set HUGINN_API_KEY"
fi

# Create data dir
mkdir -p data

echo ""
echo "=== Setup Complete ==="
echo "To start Huginn:"
echo "  source venv/bin/activate"
echo "  huginn serve"
echo ""
