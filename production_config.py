#!/usr/bin/env python3
"""
Production Configuration for Digital Ocean Deployment
"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class ProductionConfig:
    """Production configuration settings."""
    
    # Server settings
    HOST = '0.0.0.0'
    PORT = int(os.getenv('PORT', 5000))
    DEBUG = False
    
    # Security settings
    API_KEY = os.getenv('API_KEY')
    ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY')
    
    # Langdock settings
    LANGDOCK_API_KEY = os.getenv('LANGDOCK_API_KEY')
    LANGDOCK_FOLDER_ID = os.getenv('LANGDOCK_FOLDER_ID')
    
    # File settings
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    ALLOWED_EXTENSIONS = {'docx', 'pdf', 'xlsx', 'xls'}
    
    # Logging settings
    LOG_LEVEL = 'INFO'
    LOG_FILE = '/var/log/docling/app.log'
    
    # Application settings
    APP_NAME = 'Docling Document Processing API'
    APP_VERSION = '1.0.0'
    
    # Directory settings
    BASE_DIR = '/opt/docling'
    OUTPUT_DIR = '/opt/docling/output'
    TEMP_DIR = '/tmp/docling'
    
    @classmethod
    def validate_config(cls):
        """Validate that all required configuration is present."""
        required_vars = [
            'API_KEY',
            'ENCRYPTION_KEY',
            'LANGDOCK_API_KEY',
            'LANGDOCK_FOLDER_ID'
        ]
        
        missing_vars = []
        for var in required_vars:
            if not getattr(cls, var):
                missing_vars.append(var)
        
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
        
        return True

class DevelopmentConfig:
    """Development configuration settings."""
    
    HOST = '127.0.0.1'
    PORT = int(os.getenv('PORT', 5000))
    DEBUG = True
    
    # Security settings
    API_KEY = os.getenv('API_KEY')
    ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY')
    
    # Langdock settings
    LANGDOCK_API_KEY = os.getenv('LANGDOCK_API_KEY')
    LANGDOCK_FOLDER_ID = os.getenv('LANGDOCK_FOLDER_ID')
    
    # File settings
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    ALLOWED_EXTENSIONS = {'docx', 'pdf', 'xlsx', 'xls'}
    
    # Logging settings
    LOG_LEVEL = 'DEBUG'
    LOG_FILE = 'debug.log'
    
    # Application settings
    APP_NAME = 'Docling Document Processing API (Development)'
    APP_VERSION = '1.0.0'
    
    # Directory settings
    BASE_DIR = os.getcwd()
    OUTPUT_DIR = 'output'
    TEMP_DIR = '/tmp/docling'

# Configuration mapping
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}

def get_config():
    """Get configuration based on environment."""
    env = os.getenv('FLASK_ENV', 'development')
    return config.get(env, config['default']) 