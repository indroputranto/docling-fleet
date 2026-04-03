#!/usr/bin/env python3
"""
Latest Embedding Uploader - Enhanced for Heading-Based Structure
Optimized for heading-based chunks with rich metadata
"""

import os
import json
from pinecone import Pinecone
from openai import OpenAI
import tiktoken
import re
import time
from typing import List, Dict, Any, Tuple
import hashlib
from collections import defaultdict

# Pinecone configuration
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "")
PINECONE_HOST = "https://vessel-embeddings-u2t79ad.svc.aped-4627-b74a.pinecone.io"
INDEX_NAME = "vessel-embeddings"
DIMENSION = 1536  # Updated for text-embedding-ada-002
METRIC = "cosine"

pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(INDEX_NAME)

# Langdock Embedding API configuration
LANGDOCK_API_KEY = "sk-do3FADTbdhFAzdMJufXGR0qyddP0uh3JbliKS-L9svwBUQyMvQhTiPVgl_k7_D7dS1fklbIJvzWi0JlGMjTsBA"
LANGDOCK_REGION = "eu"
LANGDOCK_BASE_URL = f"https://api.langdock.com/openai/{LANGDOCK_REGION}/v1"
EMBEDDING_MODEL = "text-embedding-ada-002"

# Initialize OpenAI client for Langdock
client = OpenAI(
    base_url=LANGDOCK_BASE_URL,
    api_key=LANGDOCK_API_KEY
)

class HeadingBasedEmbeddingEnhancer:
    """Enhances embeddings with heading-based structure"""
    
    def enhance_metadata(self, chunk: Dict[str, Any]) -> Dict[str, Any]:
        """Create enhanced metadata with semantic structure"""
        metadata = {
            # Basic identification
            "vessel_name": chunk["vessel"],
            "document_type": "vessel_charter_party",
            "source_file": chunk["source_file"],
            
            # Content classification
            "content_type": chunk["content_type"],
            "content_category": chunk["content_type"].split('_')[0],  # e.g., 'charter', 'cargo'
            "content_subcategory": '_'.join(chunk["content_type"].split('_')[1:]),  # e.g., 'exclusions', 'requirements'
            
            # Structural information
            "heading": chunk["heading"],
            "heading_level": chunk["heading_level"],
            "section_path": " > ".join(chunk.get("parent_headings", []) + [chunk["heading"]]),
            
            # Semantic markers
            "is_primary_source": chunk["content_type"].endswith("_primary"),
            "content_role": "authoritative" if chunk["content_type"].endswith("_primary") else "supplementary",
            
            # Content indicators
            "contains_exclusions": "exclusions" in chunk["content_type"] or any(k in chunk["keywords"] for k in ["exclusions", "prohibited", "restricted"]),
            "contains_requirements": "requirements" in chunk["content_type"] or any(k in chunk["keywords"] for k in ["requirements", "mandatory", "obligatory"]),
            
            # Search optimization
            "keywords": chunk["keywords"],
            "semantic_type": self._get_semantic_type(chunk["content_type"]),
            "content_summary": self._generate_content_summary(chunk["content"])
        }
        
        return metadata

    def create_enhanced_text(self, chunk: Dict[str, Any]) -> str:
        """Create enhanced searchable text with semantic markers"""
        text_parts = []
        
        # Add clear section markers
        text_parts.append(f"### DOCUMENT TYPE: Vessel Charter Party")
        text_parts.append(f"### VESSEL NAME: {chunk['vessel']}")
        
        # Add hierarchical context
        if chunk.get('parent_headings'):
            text_parts.append(f"### DOCUMENT STRUCTURE: {' > '.join(chunk['parent_headings'])}")
        
        # Add content classification
        text_parts.append(f"### SECTION TYPE: {chunk['content_type'].replace('_', ' ').title()}")
        
        # Add heading with emphasis
        text_parts.append(f"### HEADING: {chunk['heading']}")
        
        # Add primary source marker if applicable
        if chunk['content_type'].endswith('_primary'):
            text_parts.append("### AUTHORITY: Primary Source Document")
            text_parts.append("### ROLE: Authoritative Reference for this Subject")
        
        # Add content with clear marker
        text_parts.append("### CONTENT START ###")
        text_parts.append(chunk['content'])
        text_parts.append("### CONTENT END ###")
        
        # Add semantic context
        text_parts.append("### SEMANTIC CONTEXT ###")
        text_parts.append(f"This section contains {chunk['content_type'].replace('_', ' ')} information")
        if 'exclusions' in chunk['content_type']:
            text_parts.append("This section defines prohibited and restricted items")
        elif 'trading' in chunk['content_type']:
            text_parts.append("This section defines trading limits and restrictions")
        
        # Add relevant keywords
        text_parts.append("### KEYWORDS ###")
        text_parts.append(", ".join(chunk['keywords']))
        
        return "\n\n".join(text_parts)

    def _get_semantic_type(self, content_type: str) -> str:
        """Determine semantic type for better AI understanding"""
        if "exclusions" in content_type:
            return "prohibition_definition"
        elif "requirements" in content_type:
            return "mandatory_requirements"
        elif "trading" in content_type:
            return "operational_limits"
        elif "specifications" in content_type:
            return "technical_details"
        elif "recap" in content_type:
            return "summary_information"
        return "general_information"

    def _generate_content_summary(self, content: str) -> str:
        """Generate a brief summary of content for metadata"""
        # Take first sentence or up to 100 characters
        summary = content.split('.')[0].strip()
        if len(summary) > 100:
            summary = summary[:97] + "..."
        return summary

def make_ascii_id(text: str) -> str:
    """Create ASCII-safe ID from text"""
    # Create MD5 hash of normalized text
    normalized = text.lower().strip()
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()

def count_tokens(text: str, model: str = EMBEDDING_MODEL) -> int:
    """Count tokens for a text string"""
    encoding = tiktoken.encoding_for_model(model)
    return len(encoding.encode(text))

class TokenRateLimiter:
    """Manages token rate limits for API calls"""
    
    def __init__(self, tokens_per_minute: int = 150000):
        self.tokens_per_minute = tokens_per_minute
        self.token_usage = []
        self.last_reset = time.time()
    
    def _cleanup_old_usage(self):
        """Remove usage data older than 1 minute"""
        now = time.time()
        cutoff = now - 60
        self.token_usage = [usage for usage in self.token_usage if usage["timestamp"] > cutoff]
        
    def _get_current_usage(self) -> int:
        """Get total token usage in the last minute"""
        self._cleanup_old_usage()
        return sum(usage["tokens"] for usage in self.token_usage)
    
    def wait_if_needed(self, tokens: int):
        """Wait if adding these tokens would exceed the rate limit"""
        while True:
            current_usage = self._get_current_usage()
            if current_usage + tokens <= self.tokens_per_minute:
                break
            time.sleep(1)
    
    def record_usage(self, tokens: int):
        """Record token usage"""
        self.token_usage.append({
            "timestamp": time.time(),
            "tokens": tokens
        })

rate_limiter = TokenRateLimiter()

def create_smart_batches(chunks: List[Dict[str, Any]], max_tokens: int = 8000) -> List[Tuple[List[str], List[Dict]]]:
    """Create smart batches of chunks respecting token limits"""
    batches = []
    current_batch_texts = []
    current_batch_data = []
    current_tokens = 0
    
    # Sort chunks to keep related content together
    sorted_chunks = sorted(chunks, key=lambda x: (
        x.get('content_type', ''),
        x.get('heading_level', 0),
        x.get('heading', '')
    ))
    
    for chunk in sorted_chunks:
        # Create enhanced text for embedding
        enhanced_text = HeadingBasedEmbeddingEnhancer().create_enhanced_text(chunk)
        chunk_tokens = count_tokens(enhanced_text)
        
        # Start new batch if this chunk would exceed token limit
        if current_tokens + chunk_tokens > max_tokens and current_batch_texts:
            batches.append((current_batch_texts, current_batch_data))
            current_batch_texts = []
            current_batch_data = []
            current_tokens = 0
        
        # Add chunk to current batch
        current_batch_texts.append(enhanced_text)
        current_batch_data.append(chunk)
        current_tokens += chunk_tokens
    
    # Add final batch if not empty
    if current_batch_texts:
        batches.append((current_batch_texts, current_batch_data))
    
    return batches

def generate_embeddings_with_retry(texts: List[str], max_retries: int = 3) -> List[List[float]]:
    """Generate embeddings with retry logic"""
    for attempt in range(max_retries):
        try:
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=texts
            )
            return [embedding.embedding for embedding in response.data]
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)  # Exponential backoff

def upsert_to_pinecone_with_retry(upsert_data: List[Tuple[str, List[float], Dict]], max_retries: int = 3):
    """Upsert to Pinecone with retry logic"""
    vectors = [
        {
            "id": item_id,
            "values": embedding,
            "metadata": metadata
        }
        for item_id, embedding, metadata in upsert_data
    ]
    
    for attempt in range(max_retries):
        try:
            index.upsert(vectors=vectors)
            return
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)

def process_and_upsert_latest():
    """Process and upsert vessel JSON with heading-based structure"""
    enhancer = HeadingBasedEmbeddingEnhancer()
    output_dir = "output/vessels"
    
    # Find all vessel JSON files
    vessel_dirs = [d for d in os.listdir(output_dir) 
                  if os.path.isdir(os.path.join(output_dir, d)) and d.startswith("MV")]
    
    print(f"Found {len(vessel_dirs)} vessel directories to process.")
    
    for vessel_dir in vessel_dirs:
        vessel_path = os.path.join(output_dir, vessel_dir)
        json_file = os.path.join(vessel_path, f"{vessel_dir}_data.json")
        
        if not os.path.exists(json_file):
            print(f"⚠️ No JSON file found for {vessel_dir}")
            continue
        
        print(f"\n📄 Processing {vessel_dir}")
        
        try:
            # Load vessel data
            with open(json_file, 'r', encoding='utf-8') as f:
                vessel_data = json.load(f)
            
            chunks = vessel_data["content_chunks"]
            print(f"  📊 Found {len(chunks)} chunks to process")
            
            # Create smart batches
            smart_batches = create_smart_batches(chunks)
            print(f"  📦 Created {len(smart_batches)} smart batches")
            
            # Process each batch
            for batch_num, (batch_texts, batch_data) in enumerate(smart_batches):
                batch_token_count = sum(count_tokens(text) for text in batch_texts)
                print(f"    🔄 Processing batch {batch_num + 1}/{len(smart_batches)} ({len(batch_texts)} chunks, {batch_token_count} tokens)")
                
                try:
                    # Generate embeddings
                    batch_embeddings = generate_embeddings_with_retry(batch_texts)
                    
                    # Prepare upsert data with enhanced metadata
                    upsert_tuples = []
                    for chunk_data, embedding in zip(batch_data, batch_embeddings):
                        # Create enhanced metadata
                        metadata = enhancer.enhance_metadata(chunk_data)
                        
                        # Create unique ID incorporating heading structure
                        heading_path = metadata.get("heading_path", chunk_data["heading"])
                        item_id = make_ascii_id(f"{chunk_data['vessel']}_{heading_path}")
                        
                        upsert_tuples.append((item_id, embedding, metadata))
                    
                    # Upsert to Pinecone
                    upsert_to_pinecone_with_retry(upsert_tuples)
                    print(f"      ✅ Successfully upserted {len(upsert_tuples)} items")
                    
                except Exception as e:
                    print(f"      ❌ Failed to process batch {batch_num + 1}: {e}")
                    continue
        
        except Exception as e:
            print(f"❌ Error processing {vessel_dir}: {e}")
            continue
    
    print("\n🎉 All vessel data processed with heading-based structure!")
    print("🔍 Ready for enhanced semantic search!")

if __name__ == "__main__":
    process_and_upsert_latest() 