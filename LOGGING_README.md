# Document Processing Logging System

This document explains the comprehensive logging system implemented for the document processing pipeline.

## Overview

The logging system provides detailed tracking of the entire document processing workflow, from SharePoint webhook reception to Langdock upload completion. Each processing session is assigned a unique session ID for easy tracking.

## Log Files

The system creates three main log files in the `logs/` directory:

1. **`document_processing.log`** - Main processing pipeline logs
2. **`langdock_uploads.log`** - Langdock upload specific logs  
3. **`processing_errors.log`** - Error logs for debugging

## Log Format

All logs follow this format:
```
2024-01-02 14:29:12,345 - INFO - [document_processing] - === STARTING PROCESSING SESSION abc123-def456 ===
2024-01-02 14:29:12,346 - INFO - [document_processing] - Filename: test_vessel_document.docx
2024-01-02 14:29:12,347 - INFO - [langdock_upload] - [abc123-def456] Successfully uploaded test_vessel.json to Langdock (Status: 200)
```

## Session Tracking

Each document processing session gets a unique UUID that appears in all related log entries:

```
[abc123-def456] Document classification completed
[abc123-def456] Starting vessel processing with VesselDataExtractor
[abc123-def456] Successfully uploaded test_vessel.json to Langdock
```

## What Gets Logged

### 1. Webhook Reception
- File name, folder path, size, creator, creation date
- Whether file content is provided

### 2. Document Processing
- Session start with unique ID
- Base64 decoding status
- File save operations
- Document classification results
- Processing start/completion
- Number of files generated

### 3. Langdock Uploads
- Upload start for each file
- Success/failure status with HTTP response codes
- Error details if uploads fail

### 4. Session Completion
- Overall success/failure status
- Total files processed
- Total Langdock uploads
- Session duration

## Using the Log Viewer

The `view_logs.py` script provides several ways to monitor logs:

### Show Log Summary
```bash
python view_logs.py --summary
```

### View Recent Activity (last 24 hours)
```bash
python view_logs.py --recent 24
```

### View Specific Log File
```bash
# View document processing logs
python view_logs.py --file processing --lines 100

# View Langdock upload logs
python view_logs.py --file langdock --lines 50

# View error logs
python view_logs.py --file errors --lines 20
```

### Follow Logs in Real-Time
```bash
python view_logs.py --file processing --follow
```

### View All Logs
```bash
python view_logs.py --lines 30
```

## Testing the Logging System

Run the test script to verify the logging system works:

```bash
python test_logging.py
```

This will generate sample log entries in all three log files.

## Example Log Output

When a document is processed, you'll see logs like this:

```
=== WEBHOOK RECEIVED ===
File Name: test vessel document 2.docx
Folder Path: Delte dokumenter/Vessels Fleet Documents - Docling/
File Size: 1024
Created By: it@ocean7projects.com
Created Date: 2025-07-02T14:29:12Z
Has File Content: Yes

=== STARTING PROCESSING SESSION abc123-def456 ===
Filename: test vessel document 2.docx
Folder Path: Delte dokumenter/Vessels Fleet Documents - Docling/
File Size: 1024
Created By: it@ocean7projects.com
Created Date: 2025-07-02T14:29:12Z
Processing started at: 2025-01-02T14:29:12.345678

[abc123-def456] Successfully decoded base64 content for test vessel document 2.docx
[abc123-def456] Successfully saved test vessel document 2.docx to /tmp/tmpxxx/test vessel document 2.docx
[abc123-def456] Document classification completed
[abc123-def456] Filename: test vessel document 2.docx
[abc123-def456] Folder Path: Delte dokumenter/Vessels Fleet Documents - Docling/
[abc123-def456] Classified as: vessel
[abc123-def456] Starting vessel processing with VesselDataExtractor
[abc123-def456] vessel processing completed successfully
[abc123-def456] Generated 1 result files
[abc123-def456] Starting Langdock upload for: MV_TEST_VESSEL_data.json
[abc123-def456] File path: /tmp/tmpxxx/output/vessels/MV_TEST_VESSEL/MV_TEST_VESSEL_data.json
[abc123-def456] Successfully uploaded MV_TEST_VESSEL_data.json to Langdock (Status: 200)
[abc123-def456] === SESSION SUCCESSFULLY COMPLETED ===
[abc123-def456] Total files processed: 1
[abc123-def456] Total Langdock uploads: 1
[abc123-def456] Session ended at: 2025-01-02T14:29:15.123456
[abc123-def456] ==================================================
```

## Monitoring in Production

For production monitoring, you can:

1. **Follow logs in real-time:**
   ```bash
   python view_logs.py --file processing --follow
   ```

2. **Check for errors:**
   ```bash
   python view_logs.py --file errors
   ```

3. **Monitor Langdock uploads:**
   ```bash
   python view_logs.py --file langdock --follow
   ```

4. **Get recent activity summary:**
   ```bash
   python view_logs.py --recent 1  # Last hour
   ```

## Log Rotation

Consider implementing log rotation for production use to prevent log files from growing too large. You can use tools like `logrotate` on Linux systems.

## Troubleshooting

If you don't see logs:

1. Check that the `logs/` directory exists and is writable
2. Verify the Flask application has write permissions
3. Check that the logging system is properly imported in `app.py`
4. Run the test script to verify logging works: `python test_logging.py` 