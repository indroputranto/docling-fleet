#!/usr/bin/env python3
"""
Flask Web Server for Document Processing
Receives documents from SharePoint via Power Automate, processes them,
and sends results to Langdock.
"""

import os
import json
import logging
import tempfile
from flask import Flask, request, jsonify, render_template, redirect, url_for, make_response, flash
from werkzeug.utils import secure_filename
import requests
from dotenv import load_dotenv
# Legacy pipeline modules — only available in local/server environments.
# Imported lazily so the app still boots on Vercel where these files are excluded.
try:
    from process_vessel_new import DynamicTextExtractor
except ImportError:
    DynamicTextExtractor = None

try:
    from process_agency import AgencyDataExtractor
except ImportError:
    AgencyDataExtractor = None
from functools import wraps
from production_config import get_config
import sys
import time
from contextlib import contextmanager

# Set up root logger as early as possible
# On Vercel the filesystem is read-only, so fall back to stdout-only logging
log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flask.log")
_log_handlers = [logging.StreamHandler(sys.stdout)]
try:
    _log_handlers.insert(0, logging.FileHandler(log_path))
except OSError:
    pass  # read-only filesystem (e.g. Vercel) — stdout only
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=_log_handlers,
)

def log_uncaught_exceptions(exctype, value, tb):
    logging.critical("Uncaught exception", exc_info=(exctype, value, tb))

sys.excepthook = log_uncaught_exceptions

# Ensure Flask and Werkzeug use the root logger
logging.getLogger('werkzeug').setLevel(logging.INFO)

# Load environment variables
load_dotenv()

# Get configuration
config = get_config()

# Configure logging
def setup_logging():
    """Setup logging configuration."""
    log_level = getattr(logging, config.LOG_LEVEL.upper())
    
    # Create log directory if it doesn't exist
    log_dir = os.path.dirname(config.LOG_FILE)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    
    _handlers = [logging.StreamHandler()]
    try:
        _handlers.insert(0, logging.FileHandler("flask.log"))
    except OSError:
        pass  # read-only filesystem
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=_handlers,
    )

setup_logging()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = config.MAX_CONTENT_LENGTH
app.config['SECRET_KEY'] = os.getenv('JWT_SECRET', 'change-me-in-production')

# Database
import os as _os
_db_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'platform.db')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', f'sqlite:///{_db_path}')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

from models import db
db.init_app(app)

# Create tables and seed first admin if needed
with app.app_context():
    db.create_all()
    from models import User
    if not User.query.filter_by(role='admin').first():
        admin_email    = os.getenv('ADMIN_EMAIL', 'admin@platform.com')
        admin_password = os.getenv('ADMIN_PASSWORD', 'changeme123')
        admin = User(email=admin_email, role='admin')
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()
        logging.info(f"[boot] Seeded admin user: {admin_email}")

# Register blueprints
from chat_routes import chat_bp
from auth import auth_bp
from cms.routes import cms_bp
from documents.routes import documents_bp

app.register_blueprint(chat_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(cms_bp)
app.register_blueprint(documents_bp)

# Configuration 
ALLOWED_EXTENSIONS = config.ALLOWED_EXTENSIONS
LANGDOCK_API_KEY = config.LANGDOCK_API_KEY
LANGDOCK_FOLDER_ID = config.LANGDOCK_FOLDER_ID
API_KEY = config.API_KEY

# Set up Langdock upload logger
langdock_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "langdock_upload.log")
langdock_logger = logging.getLogger('langdock_upload')
langdock_logger.setLevel(logging.INFO)
langdock_logger.propagate = False  # Prevent double logging
# Remove all handlers if reloading
if langdock_logger.hasHandlers():
    langdock_logger.handlers.clear()
try:
    _ld_file_handler = logging.FileHandler(langdock_log_path)
    _ld_file_handler.setLevel(logging.INFO)
    _ld_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    langdock_logger.addHandler(_ld_file_handler)
except OSError:
    # Read-only filesystem (e.g. Vercel) — fall back to root logger
    langdock_logger.propagate = True
langdock_logger.info("Langdock upload logger initialized.")

def require_api_key(f):
    """Decorator to require API key authentication. """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not API_KEY:
            # If no API key is configured, skip authentication
            return f(*args, **kwargs)
        
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({"error": "Missing Authorization header"}), 401
        
        # Check for Bearer token or API key
        if auth_header.startswith('Bearer '):
            provided_key = auth_header[7:]  # Remove 'Bearer ' prefix
        else:
            provided_key = auth_header
        
        if provided_key != API_KEY:
            return jsonify({"error": "Invalid API key"}), 401
        
        return f(*args, **kwargs)
    return decorated_function

def allowed_file(filename):
    """Check if file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def classify_document_type(filename, folder_path=""):
    """Classify document as agency or vessel based on filename and folder path."""
    filename_lower = filename.lower()
    
    # Check file extension first
    if filename_lower.endswith(('.docx', '.pdf')):
        return 'vessel'
    elif filename_lower.endswith(('.xlsx', '.xls')):
        return 'agency'
    
    # Check folder path for additional context
    folder_lower = folder_path.lower()
    if 'vessel' in folder_lower or 'vessels' in folder_lower:
        return 'vessel'
    elif 'agency' in folder_lower or 'agencies' in folder_lower:
        return 'agency'
    
    # Default classification based on common patterns
    if any(keyword in filename_lower for keyword in ['vessel', 'ship', 'mv_', 'mv-']):
        return 'vessel'
    elif any(keyword in filename_lower for keyword in ['agency', 'agent', 'supplier']):
        return 'agency'
    
    # Default to vessel for unknown types
    return 'vessel'

def send_to_langdock(file_path: str, filename: str, session_id: str = None, document_type: str = None) -> dict:
    from document_logger import document_logger
    import json
    import os
    
    # Normalize filename for consistent mapping
    def _normalize_filename(filename: str) -> str:
        return filename.strip().replace(' ', '_').lower()
    
    norm_filename = _normalize_filename(filename)
    
    # Use different folder IDs and mapping files for vessel/agency/copy
    if document_type == 'vessel':
        folder_id = '5cd21121-92ef-4ca5-86ac-7f3fe15ae406'
        mapping_file = 'attachment_mappings.json'
    elif document_type == 'agency':
        folder_id = 'ec908356-4afd-4674-87cb-b20d2cd6ad55'
        mapping_file = 'agency_attachment_mappings.json'
    elif document_type == 'copy':
        folder_id = '9d119938-6f81-4e3b-8c6b-54455f0133c4'  # Copy folder ID
        mapping_file = 'copy_attachment_mappings.json'  # Separate mapping file for copied files
    else:
        # fallback to old config for safety
        folder_id = LANGDOCK_FOLDER_ID
        mapping_file = 'attachment_mappings.json'
    
    # Load or create mapping
    if os.path.exists(mapping_file):
        with open(mapping_file, 'r') as f:
            try:
                attachment_mappings = json.load(f)
                logging.info(f"[DEBUG] Loaded attachment mappings from {os.path.abspath(mapping_file)}")
            except Exception as e:
                attachment_mappings = {}
                logging.error(f"[ERROR] Failed to load attachment mappings: {e}")
    else:
        attachment_mappings = {}
        logging.info(f"[DEBUG] No existing mapping file found at {os.path.abspath(mapping_file)}")
    
    if session_id:
        document_logger.log_langdock_upload_start(session_id, filename, file_path)
    
    if not LANGDOCK_API_KEY or not folder_id:
        error_msg = "Langdock credentials not configured"
        logging.error(f"[ERROR] {error_msg}")
        if session_id:
            document_logger.log_langdock_upload_failure(session_id, filename, error_msg)
        return {"success": False, "error": error_msg}
    
    attachment_id = attachment_mappings.get(norm_filename)
    logging.info(f"[DEBUG] Looking up mapping for {norm_filename}, found: {attachment_id}")
    
    try:
        headers = {'Authorization': f'Bearer {LANGDOCK_API_KEY}'}
        with open(file_path, 'rb') as f:
            files = {'file': (filename, f, 'application/octet-stream')}
            url = f"https://api.langdock.com/knowledge/{folder_id}"
            
            if attachment_id:
                # Overwrite existing file (PATCH)
                data = {'attachmentId': attachment_id}
                response = requests.patch(url, headers=headers, files=files, data=data)
                action = 'updated'
                logging.info(f"[DEBUG] Attempted PATCH for {norm_filename} with attachmentId {attachment_id}")
                # If PATCH fails with 'Attachment not found', remove mapping and retry as POST
                if response.status_code == 500 and 'Attachment not found' in response.text:
                    # Remove stale mapping and retry as POST
                    del attachment_mappings[norm_filename]
                    with open(mapping_file, 'w') as f_map:
                        json.dump(attachment_mappings, f_map, indent=2)
                    logging.warning(f"[WARNING] Attachment not found for {norm_filename}, retrying as new upload (POST)")
                    if session_id:
                        document_logger.langdock_logger.warning(f"[{session_id}] Attachment not found for {filename}, retrying as new upload (POST)")
                    # Retry as POST
                    f.seek(0)
                    response = requests.post(url, headers=headers, files=files)
                    action = 'uploaded'
                    logging.info(f"[DEBUG] Retried as POST for {norm_filename}")
            else:
                # Upload new file (POST)
                response = requests.post(url, headers=headers, files=files)
                action = 'uploaded'
                logging.info(f"[DEBUG] Attempted POST for {norm_filename} (no existing mapping)")
        
        if response.status_code == 200:
            logging.info(f"[DEBUG] Successfully {action} {norm_filename} to Langdock")
            mapping_updated = False
            if (not attachment_id or action == 'uploaded'):
                try:
                    response_data = response.json()
                    new_attachment_id = None
                    
                    # Check different possible response formats
                    if 'attachmentId' in response_data:
                        new_attachment_id = response_data['attachmentId']
                    elif 'result' in response_data and 'id' in response_data['result']:
                        new_attachment_id = response_data['result']['id']
                    elif 'id' in response_data:
                        new_attachment_id = response_data['id']
                    
                    if new_attachment_id:
                        attachment_mappings[norm_filename] = new_attachment_id
                        mapping_updated = True
                        logging.info(f"[DEBUG] Stored new attachmentId for {norm_filename}: {new_attachment_id}")
                        with open(mapping_file, 'w') as f:
                            json.dump(attachment_mappings, f, indent=2)
                        logging.info(f"[DEBUG] Saved updated mapping to {os.path.abspath(mapping_file)}")
                    else:
                        logging.warning(f"[WARNING] No attachmentId found in response for {norm_filename}")
                        logging.info(f"[DEBUG] Full API response for {norm_filename}: {response_data}")
                except Exception as e:
                    logging.error(f"[ERROR] Failed to extract/save attachmentId for {norm_filename}: {e}")
            
            if session_id:
                document_logger.log_langdock_upload_success(session_id, filename, response.status_code)
            return {"success": True, "action": action}
        else:
            error_msg = f"Failed to {action} {filename}. Status code: {response.status_code}, Response: {response.text}"
            logging.error(f"[ERROR] {error_msg}")
            if session_id:
                document_logger.log_langdock_upload_failure(session_id, filename, error_msg, response.status_code)
            return {"success": False, "error": error_msg}
            
    except Exception as e:
        error_msg = f"Error uploading/updating {filename} to Langdock: {str(e)}"
        logging.error(f"[ERROR] {error_msg}")
        if session_id:
            document_logger.log_langdock_upload_failure(session_id, filename, error_msg)
        return {"success": False, "error": error_msg}

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for Power Automate."""
    return jsonify({
        "status": "healthy",
        "service": config.APP_NAME,
        "version": config.APP_VERSION,
        "environment": os.getenv('FLASK_ENV', 'development'),
        "encryption": "HTTPS/TLS enabled"
    })

@app.route('/webhook/sharepoint', methods=['POST'])
@require_api_key
def sharepoint_webhook():
    from document_logger import document_logger
    
    session_id = None
    total_files_processed = 0
    total_langdock_uploads = 0
    
    try:
        # Set request size limit to 100MB and increase timeout
        request.environ['CONTENT_LENGTH'] = request.environ.get('CONTENT_LENGTH', 0)
        if int(request.environ['CONTENT_LENGTH']) > 100 * 1024 * 1024:  # 100MB
            return jsonify({"success": False, "error": "Request too large"}), 413
        
        # Use stream to handle large requests
        try:
            webhook_data = request.get_json(force=True)
        except Exception as e:
            return jsonify({"success": False, "error": f"Invalid JSON data: {str(e)}"}), 400

        if not webhook_data:
            return jsonify({"success": False, "error": "No webhook data provided"}), 400

        # Log webhook reception
        document_logger.log_webhook_reception(webhook_data)

        # Validate required fields
        required_fields = ['file_name', 'file_content']
        missing_fields = [field for field in required_fields if not webhook_data.get(field)]
        if missing_fields:
            return jsonify({"success": False, "error": f"Missing required fields: {', '.join(missing_fields)}"}), 400

        file_name = webhook_data.get('file_name')
        file_content_base64 = webhook_data.get('file_content')
        folder_path = webhook_data.get('folder_path', '')
        file_size = webhook_data.get('file_size', 0)
        created_by = webhook_data.get('created_by', 'unknown')
        created_date = webhook_data.get('created_date', '')

        # Start processing session
        session_id = document_logger.start_processing_session(file_name, webhook_data)

        # If file_content is provided, decode and save it
        if file_content_base64:
            import base64
            import re
            import os
            
            # Clean and decode base64 in chunks
            try:
                if not isinstance(file_content_base64, str):
                    file_content_base64 = str(file_content_base64)
                
                # Remove non-base64 characters
                file_content_base64 = re.sub(r'[^A-Za-z0-9+/=]', '', file_content_base64)
                
                # Process base64 in chunks
                chunk_size = 1024 * 1024  # 1MB chunks
                file_bytes = b''
                for i in range(0, len(file_content_base64), chunk_size):
                    chunk = file_content_base64[i:i + chunk_size]
                    if chunk:
                        file_bytes += base64.b64decode(chunk)
                
                document_logger.log_base64_decoding(session_id, file_name, True)
            except Exception as e:
                document_logger.log_base64_decoding(session_id, file_name, False, str(e))
                document_logger.log_processing_error(session_id, f"Base64 decoding failed: {str(e)}", "Base64 Decoding")
                return jsonify({"success": False, "error": f"Failed to decode base64 content: {str(e)}"}), 400

            # Save to Received/ folder
            received_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Received')
            os.makedirs(received_dir, exist_ok=True)
            received_file_path = os.path.join(received_dir, file_name)
            
            # Write file in chunks
            try:
                chunk_size = 1024 * 1024  # 1MB chunks
                with open(received_file_path, "wb") as f:
                    for i in range(0, len(file_bytes), chunk_size):
                        chunk = file_bytes[i:i + chunk_size]
                        if chunk:
                            f.write(chunk)
                
                file_stat = os.stat(received_file_path)
                document_logger.log_file_save(session_id, file_name, received_file_path, True)
                logging.info(f"[DEBUG] Saved file to {received_file_path} ({file_stat.st_size} bytes)")
            except Exception as e:
                document_logger.log_file_save(session_id, file_name, received_file_path, False, str(e))
                document_logger.log_processing_error(session_id, f"File save failed: {str(e)}", "File Save")
                return jsonify({"success": False, "error": f"Failed to save file: {str(e)}"}), 500

            # Check if file is a valid DOCX (if applicable)
            is_valid_docx = False
            if file_name.lower().endswith('.docx'):
                try:
                    from docx import Document as DocxDocument
                    doc = DocxDocument(received_file_path)
                    is_valid_docx = True
                    logging.info(f"[DEBUG] {file_name} is a valid DOCX file.")
                except Exception as e:
                    logging.error(f"[DEBUG] {file_name} is NOT a valid DOCX file: {e}")
            
            # --- Document type detection ---
            document_type = classify_document_type(file_name, folder_path)
            document_logger.log_document_classification(session_id, file_name, document_type, folder_path)
            
            results = {
                "filename": file_name,
                "document_type": document_type,
                "processing_results": [],
                "langdock_results": []
            }

            if document_type == 'vessel':
                document_logger.log_processing_start(session_id, "vessel", "DynamicTextExtractor")

                # Use Received/ as the source directory
                output_dir = os.path.join(received_dir, 'output', 'vessels')
                vessel_extractor = DynamicTextExtractor(source_dir=received_dir, output_dir=output_dir)
                vessels_data = vessel_extractor.process_files()
                vessel_extractor.save_outputs(vessels_data)
                
                # Count processed files
                processed_files = len(vessels_data)
                total_files_processed += processed_files
                document_logger.log_processing_complete(session_id, "vessel", processed_files)
                
                # Upload to Langdock
                for root, dirs, files in os.walk(output_dir):
                    for file in files:
                        if file.endswith((".json", ".md")):
                            out_path = os.path.join(root, file)
                            langdock_result = send_to_langdock(out_path, file, session_id, document_type)
                            total_langdock_uploads += 1
                            results["langdock_results"].append({
                                "file": file,
                                "result": langdock_result
                            })
                # Delete the received file after processing
                try:
                    if os.path.exists(received_file_path):
                        os.remove(received_file_path)
                        logging.info(f"[DEBUG] Deleted received file: {received_file_path}")
                except Exception as e:
                    logging.error(f"[DEBUG] Failed to delete received file: {received_file_path}: {e}")
                
            elif document_type == 'agency':
                document_logger.log_processing_start(session_id, "agency", "AgencyDataExtractor")
                
                try:
                    # Log the file details
                    logging.info(f"[DEBUG] Processing agency file: {received_file_path}")
                    logging.info(f"[DEBUG] File exists: {os.path.exists(received_file_path)}")
                    logging.info(f"[DEBUG] File size: {os.path.getsize(received_file_path) if os.path.exists(received_file_path) else 'N/A'}")
                    
                    # Initialize the agency extractor with the directory containing the file
                    agency_extractor = AgencyDataExtractor(received_dir)
                    
                    # Process the specific file
                    logging.info(f"[DEBUG] Calling process_files with: [{received_file_path}]")
                    agencies_data = agency_extractor.process_files([received_file_path])
                    logging.info(f"[DEBUG] process_files returned {len(agencies_data)} agencies: {list(agencies_data.keys())}")
                    
                    # Save outputs
                    output_dir = os.path.join(received_dir, 'output', 'agencies')
                    logging.info(f"[DEBUG] Saving outputs to: {output_dir}")
                    agency_extractor.save_outputs(agencies_data, output_dir)
                    
                    # Count processed files
                    processed_files = len(agencies_data)
                    total_files_processed += processed_files
                    logging.info(f"[DEBUG] Agency processing completed. Generated {processed_files} agencies")
                    document_logger.log_processing_complete(session_id, "agency", processed_files)
                    
                    # Upload to Langdock
                    logging.info(f"[DEBUG] Starting Langdock uploads from: {output_dir}")
                    for root, dirs, files in os.walk(output_dir):
                        for file in files:
                            if file.endswith((".json", ".md", ".csv")):
                                out_path = os.path.join(root, file)
                                logging.info(f"[DEBUG] Uploading file to Langdock: {out_path}")
                                langdock_result = send_to_langdock(out_path, file, session_id, document_type)
                                total_langdock_uploads += 1
                                results["langdock_results"].append({
                                    "file": file,
                                    "result": langdock_result
                                })
                    logging.info(f"[DEBUG] Completed {total_langdock_uploads} Langdock uploads")
                    
                except Exception as e:
                    error_msg = f"Error processing agency file: {str(e)}"
                    logging.error(f"[ERROR] {error_msg}")
                    logging.error(f"[ERROR] Exception details:", exc_info=True)
                    document_logger.log_processing_error(session_id, error_msg, "Agency Processing")
                    # Don't return error here, continue with cleanup
                
                # Delete the received file after processing
                try:
                    if os.path.exists(received_file_path):
                        os.remove(received_file_path)
                        logging.info(f"[DEBUG] Deleted received file: {received_file_path}")
                except Exception as e:
                    logging.error(f"[DEBUG] Failed to delete received file: {received_file_path}: {e}")
            else:
                document_logger.log_processing_error(session_id, f"Unknown document type: {document_type}", "Classification")
                return jsonify({"success": False, "error": "Unknown document type"}), 400

            # Log session completion
            document_logger.log_session_complete(session_id, True, total_files_processed, total_langdock_uploads)
            return jsonify({"success": True, "results": results})

        else:
            document_logger.log_processing_error(session_id, "No file_content provided", "Webhook Data")
            return jsonify({"success": False, "error": "No file_content provided and SharePoint download logic is commented out."}), 400
            
    except Exception as e:
        if session_id:
            document_logger.log_processing_error(session_id, str(e), "General")
            document_logger.log_session_complete(session_id, False, total_files_processed, total_langdock_uploads)
        else:
            logging.error("Exception in /webhook/sharepoint", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/webhook/copy', methods=['POST'])
@require_api_key
def copy_webhook():
    from document_logger import document_logger
    import tempfile
    
    session_id = None
    total_langdock_uploads = 0
    COPY_FOLDER_ID = '9d119938-6f81-4e3b-8c6b-54455f0133c4'  # Specific folder ID for /webhook/copy
    CHUNK_SIZE = 1024 * 1024  # 1MB chunks for processing
    MAX_RETRIES = 3
    TIMEOUT = 30  # 30 seconds timeout for API calls
    
    try:
        webhook_data = request.get_json()
        if not webhook_data:
            return jsonify({"success": False, "error": "No webhook data provided"}), 400

        # Log webhook reception
        document_logger.log_webhook_reception(webhook_data)

        file_name = webhook_data.get('file_name')
        file_content_base64 = webhook_data.get('file_content')

        if not file_name:
            return jsonify({"success": False, "error": "Missing file_name in webhook data"}), 400

        # Start processing session
        session_id = document_logger.start_processing_session(file_name, webhook_data)

        # If file_content is provided, process it
        if file_content_base64:
            import base64
            import re
            import os
            from contextlib import contextmanager
            
            @contextmanager
            def temporary_file():
                """Context manager for temporary file handling with guaranteed cleanup"""
                temp_file = None
                try:
                    temp_file = tempfile.NamedTemporaryFile(delete=False)
                    yield temp_file
                finally:
                    if temp_file:
                        try:
                            temp_file.close()
                            if os.path.exists(temp_file.name):
                                os.unlink(temp_file.name)
                        except Exception as e:
                            logging.error(f"[ERROR] Failed to cleanup temp file: {e}")
            
            # Clean and decode base64 in chunks
            try:
                if not isinstance(file_content_base64, str):
                    file_content_base64 = str(file_content_base64)
                # Remove non-base64 characters
                file_content_base64 = re.sub(r'[^A-Za-z0-9+/=]', '', file_content_base64)
                
                with temporary_file() as temp_file:
                    # Process base64 in chunks to reduce memory usage
                    content_length = len(file_content_base64)
                    for i in range(0, content_length, CHUNK_SIZE):
                        chunk = file_content_base64[i:i + CHUNK_SIZE]
                        decoded_chunk = base64.b64decode(chunk + '=' * (-len(chunk) % 4))
                        temp_file.write(decoded_chunk)
                    
                    temp_file.flush()
                    document_logger.log_base64_decoding(session_id, file_name, True)
                    
                    # Prepare results structure
                    results = {
                        "filename": file_name,
                        "langdock_folder": COPY_FOLDER_ID,
                        "langdock_results": []
                    }

                    # Send to Langdock with retries
                    retry_count = 0
                    success = False
                    last_error = None
                    
                    while retry_count < MAX_RETRIES and not success:
                        try:
                            # Override folder_id in send_to_langdock by using 'copy' as document_type
                            langdock_result = send_to_langdock(temp_file.name, file_name, session_id, 'copy')
                            total_langdock_uploads += 1
                            results["langdock_results"].append({
                                "file": file_name,
                                "result": langdock_result
                            })
                            success = True
                        except Exception as e:
                            last_error = str(e)
                            retry_count += 1
                            if retry_count < MAX_RETRIES:
                                logging.warning(f"[WARNING] Retry {retry_count} for Langdock upload of {file_name}: {e}")
                                time.sleep(1)  # Brief delay between retries
                    
                    if not success:
                        error_msg = f"Failed to upload to Langdock after {MAX_RETRIES} attempts: {last_error}"
                        document_logger.log_processing_error(session_id, error_msg, "Langdock Upload")
                        return jsonify({"success": False, "error": error_msg}), 500

                    # Log session completion
                    document_logger.log_session_complete(session_id, True, 0, total_langdock_uploads)
                    return jsonify({"success": True, "results": results})
                    
            except Exception as e:
                document_logger.log_base64_decoding(session_id, file_name, False, str(e))
                document_logger.log_processing_error(session_id, f"Base64 decoding failed: {str(e)}", "Base64 Decoding")
                return jsonify({"success": False, "error": f"Failed to decode base64 content: {str(e)}"}), 400

        else:
            document_logger.log_processing_error(session_id, "No file_content provided", "Webhook Data")
            return jsonify({"success": False, "error": "No file_content provided"}), 400
            
    except Exception as e:
        if session_id:
            document_logger.log_processing_error(session_id, str(e), "General")
            document_logger.log_session_complete(session_id, False, 0, total_langdock_uploads)
        else:
            logging.error("Exception in /webhook/copy", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/process-document', methods=['POST'])
@require_api_key
def process_document():
    """Main endpoint to process uploaded documents."""
    try:
        # Check if file was uploaded
        if 'file' not in request.files:
            return jsonify({"success": False, "error": "No file provided"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"success": False, "error": "No file selected"}), 400
        
        if not allowed_file(file.filename):
            return jsonify({"success": False, "error": "File type not allowed"}), 400
        
        # Get additional metadata from request
        document_type = request.form.get('document_type', 'auto')  # vessel, agency, or auto
        source_url = request.form.get('source_url', '')
        sharepoint_id = request.form.get('sharepoint_id', '')
        
        # Create temporary directory for processing
        with tempfile.TemporaryDirectory() as temp_dir:
            # Save uploaded file
            filename = secure_filename(file.filename)
            file_path = os.path.join(temp_dir, filename)
            file.save(file_path)
            
            logging.info(f"Processing file: {filename} (Type: {document_type})")
            
            results = {
                "filename": filename,
                "document_type": document_type,
                "source_url": source_url,
                "sharepoint_id": sharepoint_id,
                "processing_results": [],
                "langdock_results": []
            }
            
            # Determine document type if auto
            if document_type == 'auto':
                if filename.lower().endswith(('.docx', '.pdf')):
                    document_type = 'vessel'
                elif filename.lower().endswith(('.xlsx', '.xls')):
                    document_type = 'agency'
            
            # Process based on document type
            if document_type == 'vessel':
                # Process vessel document
                # Create temporary source structure
                vessel_source_dir = os.path.join(temp_dir, 'source', 'vessels')
                os.makedirs(vessel_source_dir, exist_ok=True)

                # Move file to source directory
                vessel_file_path = os.path.join(vessel_source_dir, filename)
                os.rename(file_path, vessel_file_path)

                # Process the file
                output_dir = os.path.join(temp_dir, 'output', 'vessels')
                vessel_extractor = DynamicTextExtractor(source_dir=vessel_source_dir, output_dir=output_dir)
                vessels_data = vessel_extractor.process_files()

                # Save outputs
                vessel_extractor.save_outputs(vessels_data)
                
                # Collect results
                for vessel_name, vessel_data in vessels_data.items():
                    results["processing_results"].append({
                        "vessel_name": vessel_name,
                        "data": vessel_data
                    })
                
                # Send processed files to Langdock
                for root, dirs, files in os.walk(output_dir):
                    for file in files:
                        if file.endswith(('.json', '.md')):
                            file_path = os.path.join(root, file)
                            langdock_result = send_to_langdock(file_path, file, session_id, document_type)
                            results["langdock_results"].append({
                                "file": file,
                                "result": langdock_result
                            })
            
            elif document_type == 'agency':
                # Process agency document
                agency_extractor = AgencyDataExtractor()
                
                # Create temporary source structure
                agency_source_dir = os.path.join(temp_dir, 'source', 'agencies')
                os.makedirs(agency_source_dir, exist_ok=True)
                
                # Move file to source directory
                agency_file_path = os.path.join(agency_source_dir, filename)
                os.rename(file_path, agency_file_path)
                
                # Process the file
                agency_extractor.source_dir = agency_source_dir
                agencies_data = agency_extractor.process_files([agency_file_path])
                
                # Save outputs
                output_dir = os.path.join(temp_dir, 'output', 'agencies')
                agency_extractor.save_outputs(agencies_data, output_dir)
                
                # Collect results
                for agency_name, agency_data in agencies_data.items():
                    results["processing_results"].append({
                        "agency_name": agency_name,
                        "data": agency_data
                    })
                
                # Send processed files to Langdock
                for root, dirs, files in os.walk(output_dir):
                    for file in files:
                        if file.endswith(('.json', '.md', '.csv')):
                            file_path = os.path.join(root, file)
                            langdock_result = send_to_langdock(file_path, file, session_id, document_type)
                            results["langdock_results"].append({
                                "file": file,
                                "result": langdock_result
                            })
            
            else:
                return jsonify({"success": False, "error": "Unknown document type"}), 400
            
            logging.info(f"Successfully processed {filename}")
            return jsonify({"success": True, "results": results})
    
    except Exception as e:
        logging.error(f"Error processing document: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

def get_client_id_from_request() -> str:
    """
    Determine the client_id from the incoming request.

    Priority:
      1. Subdomain of the Host header  (production: acme.platform.com → "acme")
      2. ?client=xxx query param       (dev/testing: localhost:5000?client=acme)
      3. "default"

    In Phase 2 the CMS will manage the subdomain → client_id mapping in a DB,
    but the detection logic here stays the same.
    """
    # Query param override (dev/testing)
    client_param = request.args.get('client')
    if client_param:
        return client_param.strip().lower()

    # Subdomain detection
    host = request.host.split(':')[0]  # strip port if present
    parts = host.split('.')
    # Skip IPs (all-numeric parts) and plain localhost
    is_ip = all(p.isdigit() for p in parts)
    # e.g. acme.platform.com → ['acme', 'platform', 'com'] → subdomain = 'acme'
    # 127.0.0.1 or localhost → skip
    if not is_ip and len(parts) >= 3 and parts[0] not in ('www', ''):
        return parts[0].lower()

    return 'default'


def _check_chat_cookie():
    """
    Validate cms_token cookie for chat gate.
    Accepts any active user role (admin, client_admin, user).
    Returns decoded payload or None.
    """
    from auth import _decode_token
    import jwt as _jwt
    token = request.cookies.get("cms_token")
    if not token:
        return None
    try:
        payload = _decode_token(token)
        return payload
    except _jwt.PyJWTError:
        return None


@app.route("/")
def home():
    """Serve the chat UI — requires a valid session cookie."""
    if not _check_chat_cookie():
        return redirect(url_for("chat_login", next="/"))
    client_id = get_client_id_from_request()
    return render_template('chat.html', client_id=client_id)


@app.route("/login", methods=["GET", "POST"])
def chat_login():
    """Standalone login page for chat users (all roles accepted)."""
    from auth import _decode_token, _make_token, ACCESS_EXPIRES
    import jwt as _jwt

    # Already logged in → go straight to chat
    if _check_chat_cookie():
        return redirect(request.args.get("next") or "/")

    if request.method == "GET":
        return render_template("login.html")

    email    = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not email or not password:
        flash("Email and password are required.", "error")
        return render_template("login.html")

    from models import User
    user = User.query.filter_by(email=email, active=True).first()
    if not user or not user.check_password(password):
        flash("Invalid email or password.", "error")
        return render_template("login.html")

    from datetime import datetime, timezone
    user.last_login_at = datetime.now(timezone.utc)
    from models import db as _db
    _db.session.commit()

    token = _make_token(user, ACCESS_EXPIRES)
    next_url = request.form.get("next") or request.args.get("next") or "/"
    resp = make_response(redirect(next_url))
    resp.set_cookie(
        "cms_token", token,
        httponly=True, samesite="Lax",
        max_age=int(ACCESS_EXPIRES.total_seconds()),
    )
    logging.info(f"[chat-login] Login success: {email} role={user.role}")
    return resp


@app.route("/logout")
def chat_logout():
    """Clear the session cookie and redirect to login."""
    resp = make_response(redirect(url_for("chat_login")))
    resp.delete_cookie("cms_token")
    return resp

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    logging.error("Global exception handler triggered", exc_info=True)
    return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT)