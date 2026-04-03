# Docling Project - Document Processing with SharePoint Integration

This is a local installation of Docling, a powerful document processing library that helps prepare documents for generative AI applications. This version includes enhanced capabilities for processing vessel and agency documents with SharePoint and Power Automate integration.

## Features

- Parsing of multiple document formats (PDF, DOCX, XLSX, HTML, images)
- Advanced PDF understanding with strikethrough text detection
- Unified document representation
- Various export formats (Markdown, HTML, JSON, CSV)
- Local execution capabilities
- AI framework integrations (Langdock)
- OCR support for scanned documents
- **NEW**: SharePoint integration via Power Automate
- **NEW**: Automated document processing workflow
- **NEW**: Vessel and agency data extraction
- **NEW**: Cloud deployment ready with Ngrok

## Setup

1. Create and activate the virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On macOS/Linux
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment variables:
```bash
cp env_example.txt .env
# Edit .env with your actual values:
# - LANGDOCK_API_KEY: Your Langdock API key
# - LANGDOCK_FOLDER_ID: Your Langdock folder ID
# - Optional: SharePoint credentials for direct integration
```

## Usage

### Local Document Processing

You can use Docling to process various document formats including PDF, DOCX, XLSX, HTML, and images:

```python
from process_vessel import VesselDataExtractor
from process_agency import AgencyDataExtractor

# Process vessel documents
vessel_extractor = VesselDataExtractor()
vessels_data = vessel_extractor.process_files()
vessel_extractor.save_outputs(vessels_data)

# Process agency documents
agency_extractor = AgencyDataExtractor()
agencies_data = agency_extractor.process_files(['path/to/agency.xlsx'])
agency_extractor.save_outputs(agencies_data)
```

### Web API Server

Start the Flask web server for SharePoint integration:

```bash
# Start the server
./start_server.sh

# Or manually:
python app.py
```

The server will run on `http://localhost:5000` with the following endpoints:

- `GET /health` - Health check
- `POST /process-document` - Process single document
- `POST /process-batch` - Process multiple documents

### Testing the API

Run the test suite to verify everything works:

```bash
python test_api.py
```

## SharePoint & Power Automate Integration

### Overview

This application can be integrated with SharePoint and Power Automate to create an automated document processing workflow:

1. **Document Upload**: Users upload documents to SharePoint
2. **Power Automate Trigger**: Detects new document uploads
3. **API Processing**: Sends documents to this application via Ngrok
4. **Data Extraction**: Processes vessel/agency data
5. **Langdock Upload**: Sends processed results to Langdock

### Setup Instructions

#### 1. Expose Your API with Ngrok

```bash
# Install ngrok (download from https://ngrok.com/download)
# Configure your auth token
ngrok config add-authtoken YOUR_NGROK_AUTH_TOKEN

# Start tunnel
ngrok http 5000
```

Note the HTTPS URL provided by ngrok (e.g., `https://abc123.ngrok.io`)

#### 2. Create Power Automate Flow

Follow the detailed instructions in [POWER_AUTOMATE_SETUP.md](POWER_AUTOMATE_SETUP.md) to:

1. Create a new automated flow
2. Add SharePoint trigger for file creation
3. Configure HTTP request to your API
4. Add error handling and notifications

#### 3. Test the Integration

1. Upload a test document to SharePoint
2. Monitor the Power Automate flow execution
3. Check your API logs for processing
4. Verify documents appear in Langdock

### API Endpoints

#### Process Document
```http
POST /process-document
Content-Type: multipart/form-data

Parameters:
- file: Document file
- document_type: 'vessel', 'agency', or 'auto'
- source_url: SharePoint file URL (optional)
- sharepoint_id: SharePoint file ID (optional)
```

#### Health Check
```http
GET /health
```

#### Batch Processing
```http
POST /process-batch
Content-Type: application/json

Body:
{
  "documents": [
    {
      "sharepoint_url": "https://sharepoint.com/file.docx",
      "document_type": "vessel"
    }
  ]
}
```

## Document Types

### Vessel Documents
- **Formats**: DOCX, PDF
- **Extraction**: Vessel details, contract information, delivery figures, speed/consumption data, lifting equipment, HSEQ documentation
- **Output**: JSON and Markdown files

### Agency Documents
- **Formats**: XLSX, XLS
- **Extraction**: Agents, port captains, suppliers with contact information
- **Output**: JSON, Markdown, and CSV files

## Configuration

### Environment Variables

Create a `.env` file with the following variables:

```bash
# Langdock Configuration
LANGDOCK_API_KEY=your_langdock_api_key_here
LANGDOCK_FOLDER_ID=your_langdock_folder_id_here

# Server Configuration
PORT=5000

# Optional: SharePoint Configuration
SHAREPOINT_CLIENT_ID=your_sharepoint_client_id
SHAREPOINT_CLIENT_SECRET=your_sharepoint_client_secret
SHAREPOINT_TENANT_ID=your_sharepoint_tenant_id
```

### Ngrok Configuration

Edit `ngrok.yml` with your settings:

```yaml
version: "2"
authtoken: your_ngrok_auth_token_here
tunnels:
  docling-api:
    proto: http
    addr: 5000
    subdomain: your-custom-subdomain  # Optional
    inspect: false
```

## Production Deployment

### Security Considerations

1. **API Authentication**: Implement proper authentication for your API
2. **HTTPS Only**: Always use HTTPS in production
3. **Input Validation**: Validate all inputs to prevent injection attacks
4. **Rate Limiting**: Implement rate limiting to prevent abuse
5. **Logging**: Log all requests for security monitoring

### Scaling Options

1. **Cloud Deployment**: Deploy to Azure, AWS, or Google Cloud
2. **Managed Ngrok**: Use Ngrok's paid plans for stable URLs
3. **Load Balancing**: Implement load balancing for high traffic
4. **Monitoring**: Set up proper monitoring and alerting

## Troubleshooting

### Common Issues

1. **Ngrok URL Changes**: Update Power Automate flow with new URL when Ngrok restarts
2. **File Size Limits**: Check API's max file size setting (default: 50MB)
3. **Authentication Errors**: Verify SharePoint and Langdock credentials
4. **Processing Failures**: Check API logs for detailed error messages

### Debug Steps

1. **Test API Locally**: Use `python test_api.py` to verify endpoints
2. **Check Flow History**: Review Power Automate flow execution history
3. **Monitor Logs**: Check both Power Automate and API logs
4. **Validate File Format**: Ensure uploaded files match expected formats

## File Structure

```
docling/
├── app.py                          # Flask web server
├── process_vessel.py               # Vessel document processor
├── process_agency.py               # Agency document processor
├── sharepoint_integration.py       # SharePoint integration utilities
├── test_api.py                     # API testing script
├── requirements.txt                # Python dependencies
├── start_server.sh                 # Server startup script
├── POWER_AUTOMATE_SETUP.md         # Power Automate setup guide
├── ngrok.yml                       # Ngrok configuration
├── env_example.txt                 # Environment variables template
├── source/                         # Source documents
│   ├── vessels/                    # Vessel documents
│   └── agencies/                   # Agency documents
└── output/                         # Processed outputs
    ├── vessels/                    # Vessel processing results
    └── agencies/                   # Agency processing results
```

## Support

For more information about the core Docling library, visit the [official documentation](https://docling-project.github.io/docling/).

For SharePoint integration support, refer to the [Power Automate Setup Guide](POWER_AUTOMATE_SETUP.md). # Last updated: Tue Jul  1 10:18:09 WIB 2025

# Test connection after successful deployment
# Test deployment with virtual environment persistence fix
# Test deployment with port conflict fix
# Test deployment with deactivate command fix
