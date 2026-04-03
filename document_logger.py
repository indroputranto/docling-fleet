#!/usr/bin/env python3
"""
Document Processing Logger
Provides comprehensive logging for document processing pipeline including
SharePoint webhook reception, document classification, processing, and Langdock uploads.
"""

import os
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

class DocumentLogger:
    """Comprehensive logger for document processing pipeline."""
    
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        
        # Create separate loggers for different components
        self.setup_loggers()
        
    def setup_loggers(self):
        """Setup different loggers for different components."""
        
        # Main document processing logger
        self.doc_logger = logging.getLogger('document_processing')
        self.doc_logger.setLevel(logging.INFO)
        self.doc_logger.propagate = False
        
        # Clear existing handlers
        if self.doc_logger.hasHandlers():
            self.doc_logger.handlers.clear()
        
        # File handler for document processing
        doc_handler = logging.FileHandler(self.log_dir / "document_processing.log")
        doc_handler.setLevel(logging.INFO)
        doc_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - [%(name)s] - %(message)s'
        )
        doc_handler.setFormatter(doc_formatter)
        self.doc_logger.addHandler(doc_handler)
        
        # Langdock upload logger (keeping for backward compatibility)
        self.langdock_logger = logging.getLogger('langdock_upload')
        self.langdock_logger.setLevel(logging.INFO)
        self.langdock_logger.propagate = False
        
        if self.langdock_logger.hasHandlers():
            self.langdock_logger.handlers.clear()
        
        langdock_handler = logging.FileHandler(self.log_dir / "langdock_uploads.log")
        langdock_handler.setLevel(logging.INFO)
        langdock_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - [%(name)s] - %(message)s'
        )
        langdock_handler.setFormatter(langdock_formatter)
        self.langdock_logger.addHandler(langdock_handler)
        
        # Pinecone upload logger
        self.pinecone_logger = logging.getLogger('pinecone_upload')
        self.pinecone_logger.setLevel(logging.INFO)
        self.pinecone_logger.propagate = False
        
        if self.pinecone_logger.hasHandlers():
            self.pinecone_logger.handlers.clear()
        
        pinecone_handler = logging.FileHandler(self.log_dir / "pinecone_uploads.log")
        pinecone_handler.setLevel(logging.INFO)
        pinecone_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - [%(name)s] - %(message)s'
        )
        pinecone_handler.setFormatter(pinecone_formatter)
        self.pinecone_logger.addHandler(pinecone_handler)
        
        # Error logger
        self.error_logger = logging.getLogger('processing_errors')
        self.error_logger.setLevel(logging.ERROR)
        self.error_logger.propagate = False
        
        if self.error_logger.hasHandlers():
            self.error_logger.handlers.clear()
        
        error_handler = logging.FileHandler(self.log_dir / "processing_errors.log")
        error_handler.setLevel(logging.ERROR)
        error_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - [%(name)s] - %(message)s'
        )
        error_handler.setFormatter(error_formatter)
        self.error_logger.addHandler(error_handler)
        
        # Console handler for all loggers
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - [%(name)s] - %(message)s'
        )
        console_handler.setFormatter(console_formatter)
        
        self.doc_logger.addHandler(console_handler)
        self.langdock_logger.addHandler(console_handler)
        self.pinecone_logger.addHandler(console_handler)
        self.error_logger.addHandler(console_handler)
    
    def start_processing_session(self, filename: str, webhook_data: Dict[str, Any]) -> str:
        """Start a new document processing session and return session ID."""
        session_id = str(uuid.uuid4())
        
        self.doc_logger.info(f"=== STARTING PROCESSING SESSION {session_id} ===")
        self.doc_logger.info(f"Filename: {filename}")
        self.doc_logger.info(f"Folder Path: {webhook_data.get('folder_path', 'N/A')}")
        self.doc_logger.info(f"File Size: {webhook_data.get('file_size', 'N/A')}")
        self.doc_logger.info(f"Created By: {webhook_data.get('created_by', 'N/A')}")
        self.doc_logger.info(f"Created Date: {webhook_data.get('created_date', 'N/A')}")
        self.doc_logger.info(f"Processing started at: {datetime.now().isoformat()}")
        
        return session_id
    
    def log_document_classification(self, session_id: str, filename: str, document_type: str, folder_path: str = ""):
        """Log document classification results."""
        self.doc_logger.info(f"[{session_id}] Document classification completed")
        self.doc_logger.info(f"[{session_id}] Filename: {filename}")
        self.doc_logger.info(f"[{session_id}] Folder Path: {folder_path}")
        self.doc_logger.info(f"[{session_id}] Classified as: {document_type}")
    
    def log_processing_start(self, session_id: str, document_type: str, extractor_class: str):
        """Log the start of document processing."""
        self.doc_logger.info(f"[{session_id}] Starting {document_type} processing with {extractor_class}")
    
    def log_processing_complete(self, session_id: str, document_type: str, results_count: int):
        """Log the completion of document processing."""
        self.doc_logger.info(f"[{session_id}] {document_type} processing completed successfully")
        self.doc_logger.info(f"[{session_id}] Generated {results_count} result files")
    
    def log_langdock_upload_start(self, session_id: str, filename: str, file_path: str):
        """Log the start of a Langdock upload."""
        self.langdock_logger.info(f"[{session_id}] Starting Langdock upload for: {filename}")
        self.langdock_logger.info(f"[{session_id}] File path: {file_path}")
    
    def log_langdock_upload_success(self, session_id: str, filename: str, response_status: int):
        """Log successful Langdock upload."""
        self.langdock_logger.info(f"[{session_id}] Successfully uploaded {filename} to Langdock (Status: {response_status})")
    
    def log_langdock_upload_failure(self, session_id: str, filename: str, error: str, response_status: Optional[int] = None):
        """Log failed Langdock upload."""
        status_info = f" (Status: {response_status})" if response_status else ""
        self.langdock_logger.error(f"[{session_id}] Failed to upload {filename} to Langdock{status_info}: {error}")
        self.error_logger.error(f"[{session_id}] Langdock upload failed for {filename}: {error}")
    
    def log_pinecone_upload_start(self, session_id: str, document_type: str, chunks_count: int):
        """Log the start of a Pinecone upload."""
        self.pinecone_logger.info(f"[{session_id}] Starting Pinecone upload for: {document_type}")
        self.pinecone_logger.info(f"[{session_id}] Total chunks to process: {chunks_count}")
    
    def log_pinecone_upload_success(self, session_id: str, document_type: str, embeddings_uploaded: int):
        """Log successful Pinecone upload."""
        self.pinecone_logger.info(f"[{session_id}] Successfully uploaded {embeddings_uploaded} embeddings for {document_type} to Pinecone")
    
    def log_pinecone_upload_failure(self, session_id: str, document_type: str, error: str):
        """Log failed Pinecone upload."""
        self.pinecone_logger.error(f"[{session_id}] Failed to upload {document_type} to Pinecone: {error}")
        self.error_logger.error(f"[{session_id}] Pinecone upload failed for {document_type}: {error}")
    
    def log_processing_error(self, session_id: str, error: str, error_type: str = "Processing"):
        """Log processing errors."""
        self.doc_logger.error(f"[{session_id}] {error_type} error: {error}")
        self.error_logger.error(f"[{session_id}] {error_type} error: {error}")
    
    def log_session_complete(self, session_id: str, success: bool, total_files_processed: int = 0, total_langdock_uploads: int = 0, total_pinecone_uploads: int = 0):
        """Log the completion of a processing session."""
        status = "SUCCESSFULLY COMPLETED" if success else "FAILED"
        self.doc_logger.info(f"[{session_id}] === SESSION {status} ===")
        self.doc_logger.info(f"[{session_id}] Total files processed: {total_files_processed}")
        self.doc_logger.info(f"[{session_id}] Total Langdock uploads: {total_langdock_uploads}")
        self.doc_logger.info(f"[{session_id}] Total Pinecone embeddings: {total_pinecone_uploads}")
        self.doc_logger.info(f"[{session_id}] Session ended at: {datetime.now().isoformat()}")
        self.doc_logger.info(f"[{session_id}] {'='*50}")
    
    def log_webhook_reception(self, webhook_data: Dict[str, Any]):
        """Log webhook data reception."""
        self.doc_logger.info("=== WEBHOOK RECEIVED ===")
        self.doc_logger.info(f"File Name: {webhook_data.get('file_name', 'N/A')}")
        self.doc_logger.info(f"Folder Path: {webhook_data.get('folder_path', 'N/A')}")
        self.doc_logger.info(f"File Size: {webhook_data.get('file_size', 'N/A')}")
        self.doc_logger.info(f"Created By: {webhook_data.get('created_by', 'N/A')}")
        self.doc_logger.info(f"Created Date: {webhook_data.get('created_date', 'N/A')}")
        self.doc_logger.info(f"Has File Content: {'Yes' if webhook_data.get('file_content') else 'No'}")
    
    def log_base64_decoding(self, session_id: str, filename: str, success: bool, error: str = None):
        """Log base64 decoding results."""
        if success:
            self.doc_logger.info(f"[{session_id}] Successfully decoded base64 content for {filename}")
        else:
            self.doc_logger.error(f"[{session_id}] Failed to decode base64 content for {filename}: {error}")
            self.error_logger.error(f"[{session_id}] Base64 decoding failed for {filename}: {error}")
    
    def log_file_save(self, session_id: str, filename: str, file_path: str, success: bool, error: str = None):
        """Log file save operations."""
        if success:
            self.doc_logger.info(f"[{session_id}] Successfully saved {filename} to {file_path}")
        else:
            self.doc_logger.error(f"[{session_id}] Failed to save {filename} to {file_path}: {error}")
            self.error_logger.error(f"[{session_id}] File save failed for {filename}: {error}")

# Global logger instance
document_logger = DocumentLogger() 