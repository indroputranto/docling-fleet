#!/usr/bin/env python3
"""
Test script for app_new.py integration
Tests the document processing flow without actually uploading to Pinecone
"""

import json
import requests
import sys
import os

def test_health_check():
    """Test the health check endpoint"""
    print("🩺 Testing health check endpoint...")
    try:
        response = requests.get("http://localhost:5001/health")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Health check passed: {data.get('status', 'unknown')}")
            print(f"   Service: {data.get('service', 'N/A')}")
            print(f"   Version: {data.get('version', 'N/A')}")
            print(f"   Pipeline: {data.get('processing_pipeline', 'N/A')}")
            return True
        else:
            print(f"❌ Health check failed: Status {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Health check error: {e}")
        return False

def test_document_processing():
    """Test document processing endpoint with a sample file"""
    print("\n📄 Testing document processing endpoint...")
    
    # Create a sample DOCX file for testing
    sample_file_path = "/Users/indroputranto/Desktop/docling/source/vessels/ARA_ROTTERDAMDONE.docx"
    
    if not os.path.exists(sample_file_path):
        print(f"❌ Sample file not found: {sample_file_path}")
        return False
    
    try:
        with open(sample_file_path, 'rb') as f:
            files = {'file': ('test_vessel.docx', f, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')}
            data = {
                'document_type': 'vessel',
                'source_url': 'test://localhost',
                'sharepoint_id': 'test-123'
            }
            
            print("   Sending test vessel document...")
            response = requests.post(
                "http://localhost:5001/process-document",
                files=files,
                data=data,
                timeout=120  # 2 minute timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    print("✅ Document processing succeeded!")
                    print(f"   Filename: {result['results']['filename']}")
                    print(f"   Document type: {result['results']['document_type']}")
                    
                    # Check processing results
                    processing_results = result['results'].get('processing_results', [])
                    print(f"   Processing results: {len(processing_results)} documents processed")
                    
                    # Check Pinecone results
                    pinecone_results = result['results'].get('pinecone_results', [])
                    print(f"   Pinecone results: {len(pinecone_results)} upload operations")
                    
                    for pr in pinecone_results:
                        if pr.get('result', {}).get('success'):
                            embeddings_count = pr.get('result', {}).get('embeddings_uploaded', 0)
                            print(f"   ✅ Pinecone upload: {embeddings_count} embeddings uploaded")
                        else:
                            print(f"   ❌ Pinecone upload failed: {pr.get('result', {}).get('error', 'Unknown error')}")
                    
                    return True
                else:
                    print(f"❌ Document processing failed: {result.get('error', 'Unknown error')}")
                    return False
            else:
                print(f"❌ Request failed: Status {response.status_code}")
                try:
                    error_data = response.json()
                    print(f"   Error: {error_data.get('error', 'Unknown error')}")
                except:
                    print(f"   Response: {response.text}")
                return False
                
    except Exception as e:
        print(f"❌ Document processing error: {e}")
        return False

def main():
    """Main test function"""
    print("🧪 Testing app_new.py Integration")
    print("=" * 50)
    
    # Test 1: Health check
    health_ok = test_health_check()
    
    if not health_ok:
        print("\n❌ Health check failed. Make sure the server is running on port 5001.")
        print("   Start the server with: python app_new.py")
        return False
    
    # Test 2: Document processing
    processing_ok = test_document_processing()
    
    print("\n" + "=" * 50)
    if health_ok and processing_ok:
        print("🎉 All tests passed! app_new.py integration is working correctly.")
    else:
        print("❌ Some tests failed. Check the logs for more details.")
    
    return health_ok and processing_ok

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
