#!/usr/bin/env python3
"""
Script to clear Pinecone index and upload fresh vectors with the new chunking strategy
"""

import os
from pinecone import Pinecone
from openai import OpenAI
import tiktoken
import time

# Pinecone configuration
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "")
PINECONE_HOST = "https://vessel-embeddings-u2t79ad.svc.aped-4627-b74a.pinecone.io"
INDEX_NAME = "vessel-embeddings"

# Langdock configuration
LANGDOCK_API_KEY = os.getenv("LANGDOCK_API_KEY") or "<YOUR_LANGDOCK_API_KEY>"
LANGDOCK_REGION = "eu"
LANGDOCK_BASE_URL = f"https://api.langdock.com/openai/{LANGDOCK_REGION}/v1"
EMBEDDING_MODEL = "text-embedding-ada-002"

pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(INDEX_NAME)

# Initialize OpenAI client for Langdock
client = OpenAI(
    base_url=LANGDOCK_BASE_URL,
    api_key=LANGDOCK_API_KEY
)

def clear_pinecone_index():
    """Clear all vectors from the Pinecone index"""
    print("Clearing Pinecone index...")
    try:
        # Get current stats
        stats = index.describe_index_stats()
        current_count = stats.get('total_vector_count', 0)
        print(f"   Current vectors in index: {current_count}")
        # Delete all vectors in the default namespace
        index.delete(delete_all=True, namespace="")  # Explicitly use default namespace
        print("Index cleared successfully")
        # Verify it's empty
        stats_after = index.describe_index_stats()
        new_count = stats_after.get('total_vector_count', 0)
        print(f"   Vectors after clearing: {new_count}")
    except Exception as e:
        if "Namespace not found" in str(e):
            print("   Namespace not found. Index is already empty.")
            return True
        print(f"Error clearing index: {e}")
        return False
    return True

def main():
    """Main function to clear and upload fresh vectors"""
    print("🔄 Pinecone Index Refresh")
    print("=" * 50)
    
    # Step 1: Clear the index
    if not clear_pinecone_index():
        print("❌ Failed to clear index. Aborting.")
        return
    
    print("\n✅ Process complete!")

if __name__ == "__main__":
    main() 