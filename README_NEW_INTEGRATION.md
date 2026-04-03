# New Integrated Document Processing Pipeline

This document describes the new integrated document processing pipeline that combines the SharePoint webhook listener with the enhanced vessel processing and Pinecone embedding upload functionality.

## Overview

The new `app_new.py` replaces the Langdock upload workflow with a Pinecone embedding workflow while maintaining all existing webhook and document processing capabilities.

## Key Components

### 1. Flask Webhook Server (`app_new.py`)
- **Port**: 5001 (different from original to avoid conflicts)
- **Endpoints**:
  - `/health` - Health check for Power Automate
  - `/webhook/sharepoint` - Main webhook endpoint for document processing
  - `/process-document` - Direct document upload endpoint

### 2. Enhanced Document Processing (`process_vessel_new.py`)
- **Class**: `DynamicTextExtractor`
- **Features**:
  - Chapter-based document organization
  - Strikethrough formatting preservation (`~~text~~`)
  - Individual clause extraction and numbering
  - Support for multiple file formats (DOCX, PDF, RTF, TXT, CSV)

### 3. Pinecone Integration (`embedding_uploader_new.py`)
- **Class**: `ChapterBasedSemanticEnhancer`
- **Features**:
  - Semantic enhancement of content for better embeddings
  - Chapter-aware metadata generation
  - Batch processing with token limits
  - Deduplication of similar content

### 4. Enhanced Logging (`document_logger.py`)
- **New Features**:
  - Pinecone upload logging
  - Session tracking
  - Error categorization
  - Separate log files for different operations

## Document Processing Flow

1. **Document Reception**
   - Power Automate sends document via webhook to `/webhook/sharepoint`
   - Document is decoded from base64 and saved to `Received/` folder
   - Document type is classified (vessel/agency) based on filename and folder path

2. **Document Processing**
   - **Vessels**: Processed using `DynamicTextExtractor`
     - Extracts text with chapter organization
     - Preserves numbering and formatting
     - Organizes into structured chapters
   - **Agencies**: Processed using existing `AgencyDataExtractor`
     - Currently saves to output but doesn't upload to Pinecone

3. **Embedding Generation and Upload**
   - Chunks are extracted from processed data
   - Content is semantically enhanced for better search
   - Embeddings are generated using Langdock's embedding API
   - Embeddings are uploaded to Pinecone with rich metadata

4. **Cleanup**
   - Original received files are deleted after processing
   - Temporary processing files are cleaned up

## Configuration

### Environment Variables
All configuration is handled through the existing `production_config.py` system:
- `API_KEY` - Webhook authentication
- `MAX_CONTENT_LENGTH` - File size limits
- `ALLOWED_EXTENSIONS` - Supported file types

### Pinecone Configuration
Configured in `embedding_uploader_new.py`:
- **API Key**: `pcsk_3Q5vmu_E7oUn86efDCN2LoKoWnmrAzogTfjNQMXfCe82CR6RQTjRX1JAa1DspBirEKEC5v`
- **Index**: `vessel-embeddings`
- **Dimension**: 1536 (text-embedding-ada-002)

### Langdock API Configuration
For embedding generation:
- **API Key**: `sk-do3FADTbdhFAzdMJufXGR0qyddP0uh3JbliKS-L9svwBUQyMvQhTiPVgl_k7_D7dS1fklbIJvzBA`
- **Region**: EU
- **Model**: text-embedding-ada-002

## Running the New Service

### Start the Server
```bash
python app_new.py
```

The server will start on port 5001 (different from the original app.py on port 5000).

### Test the Integration
```bash
python test_app_new.py
```

This will run health checks and test document processing with a sample file.

## Logging

### Log Files
- `flask_new.log` - Main Flask application logs
- `pinecone_upload.log` - Pinecone upload operations
- `logs/document_processing.log` - Document processing pipeline
- `logs/pinecone_uploads.log` - Detailed Pinecone upload logs
- `logs/processing_errors.log` - Error logs

### Session Tracking
Each webhook request gets a unique session ID for tracking:
- Webhook reception
- Document classification
- Processing start/completion
- Pinecone upload operations
- Session completion with statistics

## Metadata Structure

### Pinecone Metadata
Each embedding includes rich metadata:
```json
{
  "vessel": "MV ARA ROTTERDAM",
  "chapter": "2_contract_details",
  "sub_chapter": "charter_party",
  "clause_title": "1) Delivery",
  "clause_number": "1",
  "content_type": "chapter_based",
  "structure_version": "new_chapter_based",
  "is_enhanced": true,
  "enhanced_for_search": true,
  "contains_strikethrough": false,
  "priority": "high",
  "is_vessel_specifications": false,
  "is_contract_details": true,
  "is_fixture_recap": false,
  "is_charter_party": true,
  "is_clause": true,
  "is_individual_clause": true,
  "clause_type": "delivery"
}
```

## Differences from Original App

### Enhanced Features
1. **Chapter-based processing** instead of flat text extraction
2. **Pinecone embeddings** instead of Langdock file upload
3. **Semantic enhancement** for better search capabilities
4. **Individual clause tracking** with numbering
5. **Strikethrough preservation** for amended text
6. **Enhanced logging** with session tracking

### Backward Compatibility
- Maintains all existing webhook endpoints
- Uses same authentication system
- Preserves document classification logic
- Keeps same file handling approach

## Troubleshooting

### Common Issues
1. **Port conflicts**: Make sure port 5001 is available
2. **Missing dependencies**: Install required packages from `requirements.txt`
3. **Pinecone connection**: Verify API key and index configuration
4. **Large files**: Adjust `MAX_CONTENT_LENGTH` if needed

### Debug Mode
Enable debug logging by setting log level to DEBUG in the configuration.

### Health Check
Always verify the health endpoint returns 200 OK before processing documents:
```bash
curl http://localhost:5001/health
```

## Future Enhancements

1. **Agency Pinecone Integration**: Add agency document embedding support
2. **Incremental Updates**: Avoid reprocessing unchanged documents
3. **Batch Processing**: Handle multiple documents in a single webhook
4. **Search API**: Add search endpoints for querying embedded content
5. **Monitoring Dashboard**: Real-time processing statistics
