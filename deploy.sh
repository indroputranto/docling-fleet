#!/bin/bash

# Docling Deployment Script for Digital Ocean Droplet
# This script sets up the application on a fresh Digital Ocean Droplet

set -e  # Exit on any error

echo "Starting Docling deployment..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    print_error "Please run as root (use sudo)"
    exit 1
fi

# Update system packages
print_status "Updating system packages..."
apt-get update -y
apt-get upgrade -y

# Install required system packages
print_status "Installing required system packages..."
apt-get install -y python3 python3-pip python3-venv nginx supervisor git curl ufw

# Create application directory
print_status "Setting up application directory..."
mkdir -p /opt/docling
cd /opt/docling

# Always get the latest code from the repository
echo "Ensuring we have the latest code from repository..."
echo "Current directory: $(pwd)"
echo "Current user: $(whoami)"

if [ -d ".git" ]; then
  echo "Repository exists, cleaning and pulling latest..."
  GIT_SSH_COMMAND="ssh -i /root/.ssh/deploy_key -o StrictHostKeyChecking=no" git fetch origin main
  GIT_SSH_COMMAND="ssh -i /root/.ssh/deploy_key -o StrictHostKeyChecking=no" git reset --hard origin/main
  GIT_SSH_COMMAND="ssh -i /root/.ssh/deploy_key -o StrictHostKeyChecking=no" git clean -fdx
  echo "Git operations completed"
else
  echo "ERROR: No git repository found in $(pwd)."
  echo "Please ensure this directory is a git repository, or manually clean it before running this script."
  exit 1
fi

chmod +x deploy.sh

# Create virtual environment in persistent location
print_status "Setting up Python virtual environment..."
VENV_PATH="/opt/docling_venv"

# Always ensure we have a working virtual environment
print_status "Ensuring virtual environment exists and is working..."

# Remove any existing corrupted venv
if [ -d "$VENV_PATH" ] && [ ! -f "$VENV_PATH/bin/python" ]; then
  print_status "Removing corrupted virtual environment..."
  rm -rf "$VENV_PATH"
fi

# Create venv if it doesn't exist
if [ ! -d "$VENV_PATH" ]; then
  print_status "Creating new virtual environment in persistent location..."
  python3 -m venv "$VENV_PATH"
  if [ $? -ne 0 ]; then
    print_error "Failed to create virtual environment"
    exit 1
  fi
  print_status "Virtual environment created successfully at $VENV_PATH"
else
  print_status "Using existing virtual environment at $VENV_PATH"
fi

# Verify virtual environment is working
if [ ! -f "$VENV_PATH/bin/python" ]; then
  print_error "Virtual environment verification failed"
  print_status "Removing and recreating..."
  rm -rf "$VENV_PATH"
  python3 -m venv "$VENV_PATH"
  if [ $? -ne 0 ]; then
    print_error "Failed to recreate virtual environment"
    exit 1
  fi
fi

# Create symlink to venv in current directory for compatibility
print_status "Creating symlink to virtual environment..."
if [ -L "venv" ]; then
  rm venv
elif [ -d "venv" ]; then
  rm -rf venv
fi
ln -sf "$VENV_PATH" venv

# Verify symlink
if [ ! -L "venv" ] || [ "$(readlink venv)" != "$VENV_PATH" ]; then
  print_error "Failed to create symlink"
  exit 1
fi

print_status "Activating virtual environment..."
source venv/bin/activate

# Install Python dependencies
print_status "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

if [ $? -eq 0 ]; then
  print_status "Dependencies installed successfully"
else
  print_error "Failed to install dependencies"
  exit 1
fi

# Only deactivate if we're in a virtual environment
if [ -n "$VIRTUAL_ENV" ]; then
  deactivate
fi
print_status "Virtual environment setup complete"

# Create environment file if it doesn't exist
if [ ! -f ".env" ]; then
    print_status "Creating environment file..."
    cp env_production.txt .env
    print_warning "Please configure your .env file with actual values before starting the service"
fi

# Create necessary directories
print_status "Creating necessary directories..."
mkdir -p /var/log/docling
mkdir -p /opt/docling/output
mkdir -p /tmp/docling

# Set up Flask systemd service
print_status "Setting up Flask systemd service..."
cp docling-flask.service /etc/systemd/system/docling-flask.service
chmod 644 /etc/systemd/system/docling-flask.service

# Clean up old Nginx configurations
print_status "Cleaning up old Nginx configurations..."
rm -f /etc/nginx/sites-enabled/docling
rm -f /etc/nginx/sites-available/docling
rm -f /etc/nginx/conf.d/rate-limit.conf

# Set up Nginx configuration with rate limiting in http block
print_status "Setting up Nginx configuration..."
cat > /etc/nginx/conf.d/rate-limit.conf << 'EOF'
# Rate limiting configuration
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
EOF

# Set up Nginx site configuration
cat > /etc/nginx/sites-available/docling << 'EOF'
server {
    listen 80;
    server_name _;
    
    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer-when-downgrade" always;
    add_header Content-Security-Policy "default-src 'self' http: https: data: blob: 'unsafe-inline'" always;
    
    location / {
        limit_req zone=api burst=20 nodelay;
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    # Health check endpoint
    location /health {
        proxy_pass http://127.0.0.1:5000/health;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

# Enable Nginx site
ln -sf /etc/nginx/sites-available/docling /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Test Nginx configuration
print_status "Testing Nginx configuration..."
nginx -t

# Set up firewall
print_status "Setting up firewall..."
ufw allow 22
ufw allow 80
ufw allow 443
ufw --force enable

# Set proper permissions
print_status "Setting proper permissions..."
chown -R root:root /opt/docling
chmod -R 755 /opt/docling
chmod +x /opt/docling/deploy.sh

# Stop any existing Flask processes
print_status "Stopping any existing Flask processes..."
pkill -f "python.*app" || true
pkill -f "flask" || true
sleep 2

# Reload systemd and enable services
print_status "Enabling and starting services..."
systemctl daemon-reload
systemctl enable docling-flask
systemctl restart docling-flask
systemctl restart nginx
systemctl restart ngrok

# Wait a moment for services to start
sleep 5

# Check service status
print_status "Checking service status..."
if systemctl is-active --quiet docling-flask; then
    print_status "Docling Flask service is running"
else
    print_error "Docling Flask service failed to start"
    systemctl status docling-flask
    exit 1
fi

if systemctl is-active --quiet nginx; then
    print_status "Nginx service is running"
else
    print_error "Nginx service failed to start"
    systemctl status nginx
    exit 1
fi

# Set up ngrok if not already configured
print_status "Setting up ngrok tunnel..."
if [ ! -f "/usr/local/bin/ngrok" ]; then
    print_status "Installing ngrok..."
    cd /tmp
    wget https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz
    tar xvzf ngrok-v3-stable-linux-amd64.tgz
    mv ngrok /usr/local/bin/
    chmod +x /usr/local/bin/ngrok
    rm ngrok-v3-stable-linux-amd64.tgz
    print_status "Ngrok installed"
fi

# Create ngrok configuration if it doesn't exist
if [ ! -f "/etc/ngrok/ngrok.yml" ]; then
    print_status "Creating ngrok configuration..."
    mkdir -p /etc/ngrok
    
    cat > /etc/ngrok/ngrok.yml << 'EOF'
version: "2"
authtoken: 2z4d8Hr7fDH8xjYYGAik1CPUzFE_2c5Yqj9eBJwZwxwoBVbv2
tunnels:
  docling:
    proto: http
    addr: 5000
    hostname: docling.ngrok.io
    inspect: false
EOF
    
    chmod 600 /etc/ngrok/ngrok.yml
    print_warning "Please update /etc/ngrok/ngrok.yml with your actual ngrok auth token"
    print_warning "   Get your token from: https://dashboard.ngrok.com/get-started/your-authtoken"
fi

# Create ngrok systemd service if it doesn't exist
if [ ! -f "/etc/systemd/system/ngrok.service" ]; then
    print_status "Creating ngrok systemd service..."
    cat > /etc/systemd/system/ngrok.service << 'EOF'
[Unit]
Description=Ngrok Tunnel Service
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/ngrok start --config=/etc/ngrok/ngrok.yml docling
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
Environment=NGROK_LOG=stdout

[Install]
WantedBy=multi-user.target
EOF
    
    chmod 644 /etc/systemd/system/ngrok.service
    systemctl daemon-reload
    systemctl enable ngrok
    print_status "Ngrok service configured"
fi

# Check if ngrok auth token is configured
if grep -q "YOUR_NGROK_AUTH_TOKEN" /etc/ngrok/ngrok.yml; then
    print_warning "Ngrok auth token not configured. Please update /etc/ngrok/ngrok.yml"
    print_warning "   Service will not start until token is configured"
else
    systemctl start ngrok
    if systemctl is-active --quiet ngrok; then
        print_status "Ngrok service is running"
print_status "Your app is available at: https://docling.ngrok.io"
    else
        print_warning "Ngrok service failed to start. Check configuration and auth token"
    fi
fi

# Get server IP
SERVER_IP=$(curl -s ifconfig.me)

print_status "🎉 Deployment completed successfully!"
echo ""
echo "📋 Deployment Summary:"
echo "  • Application: Docling Document Processing API"
echo "  • Server IP: $SERVER_IP"
echo "  • API URL: http://$SERVER_IP"
echo "  • Health Check: http://$SERVER_IP/health"
echo ""
echo "🔧 Next Steps:"
echo "  1. Configure your .env file with actual values:"
echo "     nano /opt/docling/.env"
echo ""
echo "  2. Restart the service after configuration:"
echo "     systemctl restart docling"
echo ""
echo "  3. Check logs if needed:"
echo "     journalctl -u docling -f"
echo ""
echo "  4. Test the API:"
echo "     curl http://$SERVER_IP/health"
echo ""
echo "📚 Useful Commands:"
echo "  • View service status: systemctl status docling"
echo "  • View logs: journalctl -u docling -f"
echo "  • Restart service: systemctl restart docling"
echo "  • View Nginx logs: tail -f /var/log/nginx/access.log"

# === Restart Flask Service ===
echo "Restarting docling-flask service..."
sudo systemctl restart docling-flask
if systemctl is-active --quiet docling-flask; then
  echo "docling-flask service restarted successfully."
else
  echo "[ERROR] docling-flask service failed to restart!"
  exit 1
fi 