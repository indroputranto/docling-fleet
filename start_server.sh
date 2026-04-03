#!/bin/bash

# Start the Document Processing API Server
echo "Starting Document Processing API Server..."

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
fi

# Install dependencies if needed
echo "Checking dependencies..."
pip install -r requirements.txt

# Start the Flask server
echo "Starting Flask server on port 5000..."
python app.py 