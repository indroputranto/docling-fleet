# Power Automate Integration Setup

This document provides step-by-step instructions for setting up a Power Automate flow that monitors SharePoint for new document uploads and sends them to your document processing API.

## Prerequisites

1. **Microsoft 365 Account** with Power Automate access
2. **SharePoint Site** where documents will be uploaded
3. **Ngrok Account** for exposing your local API
4. **Langdock Account** for receiving processed documents

## Security Features

This integration includes multiple layers of encryption and security:

- **HTTPS/TLS Encryption** - All data in transit is encrypted
- **API Key Authentication** - Secure access to your API endpoints
- **File Encryption** - Optional encryption of processed files
- **Input Validation** - Protection against malicious inputs
- **Audit Logging** - Complete request/response logging

## Step 1: Set Up Your Local API Server

### 1.1 Install Dependencies
```bash
pip install -r requirements.txt
```

### 1.2 Configure Environment Variables
Create a `.env` file based on `env_example.txt`:
```bash
cp env_example.txt .env
# Edit .env with your actual values
```

**Required Security Variables:**
```bash
# Security Configuration
API_KEY=your_secure_api_key_here
ENCRYPTION_KEY=your_encryption_key_here

# Langdock Configuration
LANGDOCK_API_KEY=your_langdock_api_key_here
LANGDOCK_FOLDER_ID=your_langdock_folder_id_here
```

**Generate Secure Keys:**
```bash
# Generate API key (use a strong, random string)
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Generate encryption key
python -c "from encryption_utils import generate_encryption_key; print(generate_encryption_key())"
```

### 1.3 Test Encryption Features
```bash
python test_encryption.py
```

### 1.4 Start the Flask Server
```bash
./start_server.sh
```

### 1.5 Expose with Ngrok
```bash
# Install ngrok (download from https://ngrok.com/download)
# Configure your auth token
ngrok config add-authtoken YOUR_NGROK_AUTH_TOKEN

# Start tunnel
ngrok http 5000
```

Note the HTTPS URL provided by ngrok (e.g., `https://abc123.ngrok.io`)

## Step 2: Create Power Automate Flow

### 2.1 Create New Flow
1. Go to [Power Automate](https://flow.microsoft.com)
2. Click "Create" → "Automated cloud flow"
3. Name it "Document Processing Flow"

### 2.2 Add Trigger
1. Search for "SharePoint" trigger
2. Select "When a file is created in a folder"
3. Configure:
   - **Site Address**: Your SharePoint site URL
   - **Library Name**: Your document library (e.g., "Documents")
   - **Folder Path**: Leave empty for root folder, or specify subfolder

### 2.3 Add HTTP Request Action
1. Click "+" to add new step
2. Search for "HTTP" action
3. Select "HTTP with Azure AD"
4. Configure:
   - **Method**: POST
   - **URI**: `https://your-ngrok-url.ngrok.io/process-document`
   - **Headers**:
     ```
     Content-Type: multipart/form-data
     Authorization: Bearer YOUR_API_KEY
     ```
   - **Body**: Form data with:
     - **file**: `@{triggerBody()?['{Link}']}` (SharePoint file link)
     - **document_type**: `auto` (or specify 'vessel' or 'agency')
     - **source_url**: `@{triggerBody()?['{Link}']}`
     - **sharepoint_id**: `@{triggerBody()?['{ID}']}`

### 2.4 Add Error Handling (Optional)
1. Add "Condition" action after HTTP request
2. Check if HTTP response status is 200
3. If not, add notification or logging action

### 2.5 Add Success Notification (Optional)
1. Add "Send an email" action
2. Configure email notification for successful processing

## Step 3: Advanced Flow Configuration

### 3.1 File Type Filtering
Add a condition before the HTTP request to filter file types:
- **Condition**: `endsWith(triggerBody()?['{Name}'], '.docx') || endsWith(triggerBody()?['{Name}'], '.pdf') || endsWith(triggerBody()?['{Name}'], '.xlsx')`

### 3.2 Document Type Detection
Use a switch statement to determine document type:
```json
{
  "switch": {
    "on": "@{toLower(triggerBody()?['{Name}'])}",
    "cases": [
      {
        "case": "contains(@{toLower(triggerBody()?['{Name}'])}, 'vessel')",
        "actions": {
          "document_type": "vessel"
        }
      },
      {
        "case": "contains(@{toLower(triggerBody()?['{Name}'])}, 'agency')",
        "actions": {
          "document_type": "agency"
        }
      }
    ],
    "default": {
      "actions": {
        "document_type": "auto"
      }
    }
  }
}
```

### 3.3 Retry Logic
Add retry action for HTTP requests:
1. Add "Scope" action around HTTP request
2. Add "Retry" action within scope
3. Configure retry settings (3 attempts, 30-second intervals)

### 3.4 Security Headers
Ensure your HTTP request includes security headers:
```
Authorization: Bearer YOUR_API_KEY
User-Agent: PowerAutomate/1.0
X-Request-ID: @{guid()}
```

## Step 4: Testing the Flow

### 4.1 Test with Sample Documents
1. Upload a test vessel document (.docx or .pdf) to SharePoint
2. Monitor the flow execution in Power Automate
3. Check your API logs for processing
4. Verify documents appear in Langdock

### 4.2 Monitor Flow Performance
1. Go to "My flows" → Select your flow → "Run history"
2. Check for any failures or errors
3. Monitor processing times

### 4.3 Security Testing
1. Test with invalid API key (should return 401)
2. Test with missing authorization header (should return 401)
3. Test with invalid file types (should return 400)
4. Verify HTTPS is being used (check browser developer tools)

## Step 5: Production Deployment

### 5.1 Secure Your API
1. **API Key Authentication** - Already implemented
2. **HTTPS/TLS Encryption** - Provided by Ngrok
3. **Input Validation** - Already implemented
4. **Rate Limiting** - Consider implementing for high traffic
5. **Request Logging** - Already implemented

### 5.2 Additional Security Measures
1. **IP Whitelisting**: Restrict API access to Power Automate IP ranges
2. **Request Signing**: Implement request signature verification
3. **File Scanning**: Add antivirus scanning for uploaded files
4. **Audit Trail**: Log all file processing activities

### 5.3 Scale Your Solution
1. Deploy to cloud platform (Azure, AWS, etc.)
2. Use managed Ngrok or similar service
3. Implement proper logging and monitoring
4. Set up alerts for failures

### 5.4 Backup and Recovery
1. Implement backup strategy for processed data
2. Set up monitoring for API availability
3. Create disaster recovery plan

## Step 6: Encryption Options

### 6.1 File Encryption
To enable file encryption for processed documents:

```python
# In your processing logic, add:
from encryption_utils import EncryptionManager

encryption_manager = EncryptionManager()
# Encrypt processed files before sending to Langdock
encrypted_file_path = encryption_manager.encrypt_file(processed_file_path)
```

### 6.2 Data Encryption
To encrypt sensitive data in responses:

```python
# Encrypt sensitive fields in processing results
from encryption_utils import encrypt_sensitive_data

encrypted_results = encrypt_sensitive_data(processing_results)
```

### 6.3 End-to-End Encryption
For maximum security, implement end-to-end encryption:

1. **Client-side encryption** (Power Automate)
2. **Transport encryption** (HTTPS/TLS)
3. **Server-side encryption** (File and data encryption)
4. **Storage encryption** (Encrypted file storage)

## Troubleshooting

### Common Issues

1. **Ngrok URL Changes**: Ngrok URLs change on restart. Update Power Automate flow with new URL.

2. **Authentication Errors**: 
   - Verify API key is correct
   - Check Authorization header format: `Bearer YOUR_API_KEY`
   - Ensure API key is set in environment variables

3. **File Size Limits**: SharePoint has file size limits. Check your API's max file size setting.

4. **Processing Failures**: Check API logs for detailed error messages.

5. **Encryption Errors**: Verify encryption key is properly configured.

### Debug Steps

1. **Test API Endpoint**: Use Postman or curl to test your API directly
2. **Check Flow History**: Review Power Automate flow execution history
3. **Monitor Logs**: Check both Power Automate and API logs
4. **Validate File Format**: Ensure uploaded files match expected formats
5. **Test Encryption**: Run `python test_encryption.py` to verify encryption

### Security Debugging

1. **Check HTTPS**: Verify all requests use HTTPS
2. **Verify Authentication**: Test with and without valid API key
3. **Monitor Logs**: Check for unauthorized access attempts
4. **File Validation**: Ensure only allowed file types are processed

## API Endpoints Reference

### Health Check
- **URL**: `GET /health`
- **Purpose**: Verify API is running
- **Authentication**: None required

### Process Document
- **URL**: `POST /process-document`
- **Purpose**: Process single document
- **Authentication**: API key required
- **Parameters**:
  - `file`: Document file (multipart/form-data)
  - `document_type`: 'vessel', 'agency', or 'auto'
  - `source_url`: SharePoint file URL
  - `sharepoint_id`: SharePoint file ID

### Process Batch
- **URL**: `POST /process-batch`
- **Purpose**: Process multiple documents
- **Authentication**: API key required
- **Body**: JSON array of document objects

## Security Considerations

1. **API Authentication**: Implemented with API key
2. **HTTPS Only**: Always use HTTPS in production
3. **Input Validation**: Validate all inputs to prevent injection attacks
4. **Rate Limiting**: Consider implementing rate limiting to prevent abuse
5. **Logging**: Log all requests for security monitoring
6. **File Encryption**: Optional file encryption available
7. **Data Encryption**: Sensitive data encryption available
8. **Audit Trail**: Complete request/response logging

## Compliance and Standards

This implementation supports various security standards:

- **GDPR**: Data encryption and audit logging
- **HIPAA**: Secure handling of sensitive information
- **SOX**: Audit trail and access controls
- **ISO 27001**: Information security management 