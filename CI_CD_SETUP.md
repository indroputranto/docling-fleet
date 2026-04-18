# CI/CD Setup Guide - GitHub Actions to Digital Ocean

This guide will help you set up automated deployment from your GitHub repository to your Digital Ocean Droplet.

## Prerequisites

1. **GitHub Repository**: Your code is in `https://github.com/indroputranto/docling-fleet`
2. **Digital Ocean Droplet**: A provisioned Droplet with SSH access
3. **GitHub Account**: With access to repository settings

## Step 1: Set Up GitHub Secrets

### 1.1 Go to GitHub Repository Settings

1. Navigate to your repository: `https://github.com/indroputranto/docling-fleet`
2. Click on **Settings** tab
3. In the left sidebar, click **Secrets and variables** → **Actions**

### 1.2 Add Repository Secrets

Add the following secrets by clicking **New repository secret**:

| Secret Name | Value | Description |
|-------------|-------|-------------|
| `DROPLET_IP` | Your Droplet's IP address | Your Digital Ocean Droplet IP |
| `DROPLET_USERNAME` | `root` (or a dedicated deploy user) | SSH username |
| `DROPLET_SSH_KEY` | Private key contents | Use SSH key auth — never store passwords in secrets |

**Important**: Always use SSH key authentication. Never store plaintext passwords in GitHub Secrets or documentation.

## Step 2: Initial Server Setup

### 2.1 Connect to Your Droplet

```bash
ssh root@<YOUR_DROPLET_IP>
```

### 2.2 Run Initial Deployment

```bash
# Clone the repository
git clone https://github.com/indroputranto/docling-fleet.git /opt/docling
cd /opt/docling

# Make deployment script executable
chmod +x deploy.sh

# Run deployment
./deploy.sh
```

### 2.3 Configure Environment Variables

```bash
# Edit the environment file
nano /opt/docling/.env
```

Add your actual values:

```bash
# Environment
FLASK_ENV=production
PORT=5000

# Security Configuration
API_KEY=your_secure_api_key_here
ENCRYPTION_KEY=your_encryption_key_here

# Langdock Configuration
LANGDOCK_API_KEY=your_langdock_api_key_here
LANGDOCK_FOLDER_ID=your_langdock_folder_id_here
```

### 2.4 Restart the Service

```bash
systemctl restart docling
```

## Step 3: Test the Deployment

### 3.1 Check Service Status

```bash
# Check if services are running
systemctl status docling
systemctl status nginx

# Check logs
journalctl -u docling -f
```

### 3.2 Test the API

```bash
# Test health endpoint
curl http://<YOUR_DROPLET_IP>/health

# Expected response:
{
  "status": "healthy",
  "service": "Docling Document Processing API",
  "version": "1.0.0",
  "environment": "production",
  "encryption": "HTTPS/TLS enabled"
}
```

## Step 4: Set Up SSL (Optional but Recommended)

### 4.1 If You Have a Domain

If you have a domain pointing to your server (e.g., `api.ocean7.com`):

```bash
# Run SSL setup script
./setup_ssl.sh api.ocean7.com
```

### 4.2 Without a Domain

The application will work with HTTP, but for production use, consider:
- Using a domain name
- Setting up SSL certificates
- Using a CDN for additional security

## Step 5: Verify CI/CD Pipeline

### 5.1 Make a Test Change

1. Make a small change to your code
2. Commit and push to the `main` branch
3. Go to **Actions** tab in GitHub to monitor the deployment

### 5.2 Monitor Deployment

The GitHub Actions workflow will:
1. Checkout code
2. Set up Python environment
3. Install dependencies
4. Run tests
5. Deploy to Digital Ocean Droplet
6. Restart services

## Step 6: Power Automate Integration

### 6.1 Update Power Automate Flow

Update your Power Automate HTTP request to use the new production URL:

```
URL: http://<YOUR_DROPLET_IP>/process-document
Headers:
  Authorization: Bearer YOUR_API_KEY
  Content-Type: multipart/form-data
```

### 6.2 Test the Integration

1. Upload a test document to SharePoint
2. Monitor the Power Automate flow execution
3. Check the API logs: `journalctl -u docling -f`
4. Verify documents appear in Langdock

## Troubleshooting

### Common Issues

#### 1. GitHub Actions Fails

**Problem**: SSH connection fails
**Solution**: 
- Verify secrets are correct
- Check if server is accessible
- Ensure SSH service is running

#### 2. Service Won't Start

**Problem**: Docling service fails to start
**Solution**:
```bash
# Check service status
systemctl status docling

# Check logs
journalctl -u docling -f

# Common issues:
# - Missing .env file
# - Invalid environment variables
# - Port already in use
```

#### 3. Nginx Issues

**Problem**: Nginx configuration errors
**Solution**:
```bash
# Test Nginx configuration
nginx -t

# Check Nginx logs
tail -f /var/log/nginx/error.log

# Restart Nginx
systemctl restart nginx
```

#### 4. Permission Issues

**Problem**: Permission denied errors
**Solution**:
```bash
# Fix permissions
chown -R root:root /opt/docling
chmod -R 755 /opt/docling
chmod +x /opt/docling/deploy.sh
```

### Debug Commands

```bash
# View all service logs
journalctl -u docling -f
journalctl -u nginx -f

# Check disk space
df -h

# Check memory usage
free -h

# Check running processes
ps aux | grep python

# Check open ports
netstat -tlnp

# Test API endpoints
curl -X GET http://<YOUR_DROPLET_IP>/health
curl -X POST http://<YOUR_DROPLET_IP>/process-document \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -F "file=@test.docx" \
  -F "document_type=vessel"
```

## Security Considerations

### 1. Firewall Configuration

The deployment script automatically configures UFW firewall:
- Port 22 (SSH)
- Port 80 (HTTP)
- Port 443 (HTTPS)

### 2. API Security

- API key authentication
- Rate limiting (10 requests/second)
- Input validation
- Security headers

### 3. Server Security

- Automatic security updates
- Firewall enabled
- Non-root user (optional)
- SSL/TLS encryption (with domain)

## Monitoring and Maintenance

### 1. Log Monitoring

```bash
# Real-time log monitoring
journalctl -u docling -f

# View recent logs
journalctl -u docling --since "1 hour ago"

# Export logs
journalctl -u docling > docling_logs.txt
```

### 2. Service Management

```bash
# Restart service
systemctl restart docling

# Stop service
systemctl stop docling

# Start service
systemctl start docling

# Check status
systemctl status docling
```

### 3. Backup Strategy

```bash
# Backup application
tar -czf docling_backup_$(date +%Y%m%d).tar.gz /opt/docling

# Backup logs
tar -czf logs_backup_$(date +%Y%m%d).tar.gz /var/log/docling
```

### 4. Updates

The CI/CD pipeline automatically updates the application when you push to the main branch. For manual updates:

```bash
cd /opt/docling
git pull origin main
systemctl restart docling
```

## Performance Optimization

### 1. Nginx Configuration

The deployment includes:
- Rate limiting
- Security headers
- Proxy buffering
- Connection timeouts

### 2. Application Optimization

- Gunicorn (optional for production)
- Process management
- Memory optimization
- Log rotation

### 3. Monitoring

Consider setting up:
- Application monitoring (New Relic, DataDog)
- Server monitoring (Digital Ocean monitoring)
- Log aggregation (ELK stack)

## Support

For issues with:
- **GitHub Actions**: Check Actions tab in repository
- **Server Deployment**: Use debug commands above
- **Application**: Check service logs
- **Power Automate**: Verify API endpoints and authentication

## Next Steps

1. Set up CI/CD pipeline
2. Configure production environment
3. Test API endpoints
4. Integrate with Power Automate
5. Set up monitoring and alerting
6. Implement backup strategy
7. Set up SSL certificates (if domain available) 