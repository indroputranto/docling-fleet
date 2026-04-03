#!/usr/bin/env python3
"""
Flask Web Server for Document Processing with Pinecone Integration
Receives documents from SharePoint via Power Automate, processes them with the new vessel processing pipeline,
and uploads embeddings to Pinecone instead of Langdock.
"""

import os
import json
import logging
import tempfile
import time
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from functools import wraps
from production_config import get_config
import sys
from contextlib import contextmanager
import base64
import re

# Import our new processing modules
from process_vessel_new import DynamicTextExtractor
from process_agency import AgencyDataExtractor
from embedding_uploader_new import ChapterBasedSemanticEnhancer, generate_embeddings_with_retry, upsert_to_pinecone_with_retry, make_ascii_id, create_smart_batches, extract_chunks_from_chapter_based_json, deduplicate_chunks, count_tokens_in_batch

# Set up root logger as early as possible
log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flask_new.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(sys.stdout)
    ]
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
    
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("flask_new.log"),
            logging.StreamHandler()
        ]
    )

setup_logging()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = config.MAX_CONTENT_LENGTH

# Configuration 
ALLOWED_EXTENSIONS = config.ALLOWED_EXTENSIONS
API_KEY = config.API_KEY

# Set up Pinecone upload logger
pinecone_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pinecone_upload.log")
pinecone_logger = logging.getLogger('pinecone_upload')
pinecone_logger.setLevel(logging.INFO)
pinecone_logger.propagate = False  # Prevent double logging
# Remove all handlers if reloading
if pinecone_logger.hasHandlers():
    pinecone_logger.handlers.clear()
file_handler = logging.FileHandler(pinecone_log_path)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
pinecone_logger.addHandler(file_handler)
pinecone_logger.info("Pinecone upload logger initialized.")

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

def process_and_upload_to_pinecone(extracted_data: dict, document_type: str, session_id: str = None) -> dict:
    """Process extracted data and upload embeddings to Pinecone"""
    from document_logger import document_logger
    
    try:
        pinecone_logger.info(f"Starting Pinecone processing for {document_type}")
        
        if session_id:
            document_logger.log_pinecone_upload_start(session_id, document_type, 0)  # Will update count later
        
        # Initialize enhancer
        enhancer = ChapterBasedSemanticEnhancer()
        
        # Process each document in the extracted data
        total_chunks_processed = 0
        total_embeddings_uploaded = 0
        
        for doc_name, doc_data in extracted_data.items():
            pinecone_logger.info(f"Processing document: {doc_name}")
            
            # Extract chunks from the processed data
            chunks = extract_chunks_from_chapter_based_json(doc_data, doc_name, f"{doc_name}_data.json")
            pinecone_logger.info(f"Found {len(chunks)} chunks from {doc_name}")
            
            if not chunks:
                pinecone_logger.warning(f"No valid chunks found for {doc_name}")
                continue
            
            # Deduplicate chunks
            chunks = deduplicate_chunks(chunks)
            pinecone_logger.info(f"Processing {len(chunks)} unique chunks after deduplication")
            
            # Apply semantic enhancement
            enhanced_chunks = []
            for chunk in chunks:
                enhanced_content = enhancer.enhance_content_for_embedding(chunk)
                chunk["enhanced_content"] = enhanced_content
                enhanced_chunks.append(chunk)
            
            pinecone_logger.info(f"Enhanced {len(enhanced_chunks)} chunks with semantic boost")
            
            # Create smart batches
            smart_batches = create_smart_batches(
                enhanced_chunks, 
                max_batch_tokens=7000,
                model="text-embedding-ada-002"
            )
            pinecone_logger.info(f"Created {len(smart_batches)} smart batches based on token limits")
            
            # Process each smart batch
            for batch_num, batch_chunks in enumerate(smart_batches):
                batch_texts = [chunk["enhanced_content"] for chunk in batch_chunks]
                batch_token_count = count_tokens_in_batch(batch_texts, model="text-embedding-ada-002")
                pinecone_logger.info(f"Processing batch {batch_num + 1}/{len(smart_batches)} ({len(batch_chunks)} chunks, {batch_token_count} tokens)")
                
                try:
                    # Generate embeddings for this batch
                    batch_embeddings = generate_embeddings_with_retry(batch_texts)
                    
                    # Prepare batch upsert data with enhanced metadata
                    upsert_tuples = []
                    for chunk, embedding in zip(batch_chunks, batch_embeddings):
                        enhanced_content = chunk["enhanced_content"]
                        
                        # Create enhanced metadata
                        enhanced_metadata = enhancer.create_enhanced_metadata(chunk, enhanced_content)
                        
                        # Create unique ID
                        chapter = chunk.get('chapter', '')
                        sub_chapter = chunk.get('sub_chapter', '')
                        clause_title = chunk.get('clause_title', '')
                        clause_number = chunk.get('clause_number')
                        
                        id_parts = [doc_name, chapter]
                        if sub_chapter:
                            id_parts.append(sub_chapter)
                        
                        # For individual clauses, use clause number for cleaner IDs
                        if clause_number:
                            id_parts.append(f"clause_{clause_number}")
                        elif clause_title:
                            id_parts.append(clause_title[:50])  # Limit clause title length
                        
                        item_id = make_ascii_id('_'.join(id_parts))
                        upsert_tuples.append((item_id, embedding, enhanced_metadata))
                    
                    # Batch upsert to Pinecone
                    upsert_to_pinecone_with_retry(upsert_tuples)
                    pinecone_logger.info(f"Successfully upserted {len(upsert_tuples)} enhanced items to Pinecone")
                    total_embeddings_uploaded += len(upsert_tuples)
                    
                except Exception as e:
                    pinecone_logger.error(f"Failed to process batch {batch_num + 1}: {e}")
                    if session_id:
                        document_logger.log_pinecone_upload_failure(session_id, f"{document_type}_batch_{batch_num}", str(e))
                    continue
            
            total_chunks_processed += len(chunks)
        
        result = {
            "success": True,
            "chunks_processed": total_chunks_processed,
            "embeddings_uploaded": total_embeddings_uploaded,
            "documents_processed": len(extracted_data)
        }
        
        if session_id:
            document_logger.log_pinecone_upload_success(session_id, document_type, total_embeddings_uploaded)
        
        pinecone_logger.info(f"Pinecone processing complete: {result}")
        return result
        
    except Exception as e:
        error_msg = f"Error processing {document_type} data for Pinecone: {str(e)}"
        pinecone_logger.error(error_msg)
        if session_id:
            document_logger.log_pinecone_upload_failure(session_id, document_type, error_msg)
        return {"success": False, "error": error_msg}

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for Power Automate."""
    return jsonify({
        "status": "healthy",
        "service": f"{config.APP_NAME} - Pinecone Integration",
        "version": f"{config.APP_VERSION} - New",
        "environment": os.getenv('FLASK_ENV', 'development'),
        "encryption": "HTTPS/TLS enabled",
        "processing_pipeline": "New Chapter-Based with Pinecone"
    })

@app.route('/webhook/sharepoint', methods=['POST'])
@require_api_key
def sharepoint_webhook():
    from document_logger import document_logger
    
    session_id = None
    total_files_processed = 0
    total_embeddings_uploaded = 0
    
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
                "pinecone_results": []
            }

            if document_type == 'vessel':
                document_logger.log_processing_start(session_id, "vessel", "DynamicTextExtractor")
                
                # Use new DynamicTextExtractor from process_vessel_new.py
                vessel_extractor = DynamicTextExtractor(received_dir)
                vessels_data = vessel_extractor.process_files()
                
                # Save outputs to temporary output directory
                output_dir = os.path.join(received_dir, 'output', 'vessels')
                vessel_extractor.save_outputs(vessels_data, output_dir)
                
                # Count processed files
                processed_files = len(vessels_data)
                total_files_processed += processed_files
                document_logger.log_processing_complete(session_id, "vessel", processed_files)
                
                # Upload to Pinecone instead of Langdock
                pinecone_result = process_and_upload_to_pinecone(vessels_data, document_type, session_id)
                total_embeddings_uploaded += pinecone_result.get('embeddings_uploaded', 0)
                results["pinecone_results"].append({
                    "type": "vessel_embeddings",
                    "result": pinecone_result
                })
                
                # Clean up the received file after processing
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
                    
                    # Note: Agencies don't go to Pinecone in this implementation
                    # They can be handled separately or added later
                    logging.info(f"[DEBUG] Agency data processed but not uploaded to Pinecone (not implemented yet)")
                    results["pinecone_results"].append({
                        "type": "agency_data",
                        "result": {"success": True, "note": "Agency data processed but not uploaded to Pinecone"}
                    })
                    
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
            document_logger.log_session_complete(session_id, True, total_files_processed, 0, total_embeddings_uploaded)
            return jsonify({"success": True, "results": results})

        else:
            document_logger.log_processing_error(session_id, "No file_content provided", "Webhook Data")
            return jsonify({"success": False, "error": "No file_content provided"}), 400
            
    except Exception as e:
        if session_id:
            document_logger.log_processing_error(session_id, str(e), "General")
            document_logger.log_session_complete(session_id, False, total_files_processed, 0, total_embeddings_uploaded)
        else:
            logging.error("Exception in /webhook/sharepoint", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/process-document', methods=['POST'])
@require_api_key
def process_document():
    """Main endpoint to process uploaded documents with new pipeline."""
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
                "pinecone_results": []
            }
            
            # Determine document type if auto
            if document_type == 'auto':
                if filename.lower().endswith(('.docx', '.pdf')):
                    document_type = 'vessel'
                elif filename.lower().endswith(('.xlsx', '.xls')):
                    document_type = 'agency'
            
            # Process based on document type
            if document_type == 'vessel':
                # Process vessel document with new pipeline
                vessel_extractor = DynamicTextExtractor(temp_dir)
                
                # Process the file
                vessels_data = vessel_extractor.process_files()
                
                # Save outputs
                output_dir = os.path.join(temp_dir, 'output', 'vessels')
                vessel_extractor.save_outputs(vessels_data, output_dir)
                
                # Collect results
                for vessel_name, vessel_data in vessels_data.items():
                    results["processing_results"].append({
                        "vessel_name": vessel_name,
                        "data": vessel_data
                    })
                
                # Upload to Pinecone
                pinecone_result = process_and_upload_to_pinecone(vessels_data, document_type)
                results["pinecone_results"].append({
                    "type": "vessel_embeddings",
                    "result": pinecone_result
                })
            
            elif document_type == 'agency':
                # Process agency document
                agency_extractor = AgencyDataExtractor(temp_dir)
                
                # Process the file
                agencies_data = agency_extractor.process_files([file_path])
                
                # Save outputs
                output_dir = os.path.join(temp_dir, 'output', 'agencies')
                agency_extractor.save_outputs(agencies_data, output_dir)
                
                # Collect results
                for agency_name, agency_data in agencies_data.items():
                    results["processing_results"].append({
                        "agency_name": agency_name,
                        "data": agency_data
                    })
                
                # Note: Agencies don't go to Pinecone in this implementation
                results["pinecone_results"].append({
                    "type": "agency_data",
                    "result": {"success": True, "note": "Agency data processed but not uploaded to Pinecone"}
                })
            
            else:
                return jsonify({"success": False, "error": "Unknown document type"}), 400
            
            logging.info(f"Successfully processed {filename}")
            return jsonify({"success": True, "results": results})
    
    except Exception as e:
        logging.error(f"Error processing document: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/")
def home():
    return "Welcome to Docling with Pinecone Integration!", 200

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
    app.run(host="0.0.0.0", port=5001)  # Using different port to avoid conflicts
