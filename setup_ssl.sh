#!/bin/bash

# SSL Certificate Setup Script for Docling
# This script sets up Let's Encrypt SSL certificates for HTTPS

set -e  # Exit on any error

echo "Setting up SSL certificates..."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

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

# Check if domain is provided
if [ -z "$1" ]; then
    print_error "Usage: $0 <your-domain.com>"
    print_error "Example: $0 api.ocean7.com"
    exit 1
fi

DOMAIN=$1

print_status "Setting up SSL for domain: $DOMAIN"

# Install certbot
print_status "Installing Certbot..."
apt-get update -y
apt-get install -y certbot python3-certbot-nginx

# Update Nginx configuration with domain
print_status "Updating Nginx configuration..."
cat > /etc/nginx/sites-available/docling << EOF
server {
    listen 80;
    server_name $DOMAIN;
    
    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer-when-downgrade" always;
    add_header Content-Security-Policy "default-src 'self' http: https: data: blob: 'unsafe-inline'" always;
    
    # Rate limiting
    limit_req_zone \$binary_remote_addr zone=api:10m rate=10r/s;
    
    location / {
        limit_req zone=api burst=20 nodelay;
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    # Health check endpoint
    location /health {
        proxy_pass http://127.0.0.1:5000/health;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

# Test Nginx configuration
print_status "Testing Nginx configuration..."
nginx -t

# Restart Nginx
print_status "Restarting Nginx..."
systemctl restart nginx

# Obtain SSL certificate
print_status "Obtaining SSL certificate from Let's Encrypt..."
certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email admin@$DOMAIN

# Set up automatic renewal
print_status "Setting up automatic certificate renewal..."
(crontab -l 2>/dev/null; echo "0 12 * * * /usr/bin/certbot renew --quiet") | crontab -

# Update firewall to allow HTTPS
print_status "Updating firewall for HTTPS..."
ufw allow 443

print_status "🎉 SSL setup completed successfully!"
echo ""
echo "📋 SSL Summary:"
echo "  • Domain: $DOMAIN"
echo "  • HTTPS URL: https://$DOMAIN"
echo "  • Health Check: https://$DOMAIN/health"
echo ""
echo "🔧 Certificate Management:"
echo "  • View certificate: certbot certificates"
echo "  • Renew manually: certbot renew"
echo "  • Auto-renewal: Configured in crontab"
echo ""
echo "📚 Useful Commands:"
echo "  • Check certificate status: certbot certificates"
echo "  • Test renewal: certbot renew --dry-run"
echo "  • View Nginx config: cat /etc/nginx/sites-available/docling" 