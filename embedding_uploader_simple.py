#!/usr/bin/env python3
"""
Simple Embedding Uploader - Works with Clean Chunk Structure
Processes clean chunks from two-tier detection system
"""

import os
import json
from pinecone import Pinecone
from openai import OpenAI
import tiktoken
import re
import time
from typing import List, Dict, Any
import hashlib

# Pinecone configuration
PINECONE_API_KEY = "pcsk_3Q5vmu_E7oUn86efDCN2LoKoWnmrAzogTfjNQMXfCe82CR6RQTjRX1JAa1DspBirEKEC5v"
PINECONE_HOST = "https://vessel-embeddings-u2t79ad.svc.aped-4627-b74a.pinecone.io"
INDEX_NAME = "vessel-embeddings"

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

def retry_with_exponential_backoff(func, max_retries=3, base_delay=1, max_delay=60):
    """Retry a function with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise e
            delay = min(base_delay * (2 ** attempt), max_delay)
            print(f"    Retry {attempt + 1}/{max_retries} after {delay}s delay: {e}")
            time.sleep(delay)

def generate_embeddings_with_retry(texts: List[str]) -> List[List[float]]:
    """Generate embeddings with retry logic for rate limits."""
    def _generate():
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts,
            encoding_format="float"
        )
        return [item.embedding for item in response.data]
    
    return retry_with_exponential_backoff(_generate)

def upsert_to_pinecone_with_retry(upsert_tuples):
    """Upsert data to Pinecone with retry logic."""
    def _upsert():
        return index.upsert(upsert_tuples)
    
    return retry_with_exponential_backoff(_upsert)

def count_tokens_in_batch(texts, model="text-embedding-ada-002"):
    """Count tokens in a batch of texts."""
    try:
        enc = tiktoken.encoding_for_model(model)
        return sum(len(enc.encode(str(text))) for text in texts)
    except Exception:
        # Fallback: rough estimation
        return sum(len(str(text).split()) * 1.3 for text in texts)

def split_oversized_chunk(text: str, max_tokens: int = 8000, model: str = "text-embedding-ada-002") -> List[str]:
    """Split oversized chunks into multiple smaller chunks while preserving ALL content."""
    enc = tiktoken.encoding_for_model(model)
    tokens = enc.encode(str(text))
    
    if len(tokens) <= max_tokens:
        return [text]
    
    # Split text into logical segments (paragraphs first, then sentences if needed)
    paragraphs = text.split('\n\n')
    if len(paragraphs) == 1:
        # If no paragraph breaks, split by lines
        paragraphs = text.split('\n')
    if len(paragraphs) == 1:
        # If still one big block, split by sentences
        import re
        paragraphs = re.split(r'(?<=[.!?])\s+', text)
    
    chunks = []
    current_chunk = ""
    current_tokens = 0
    
    for para in paragraphs:
        para_tokens = len(enc.encode(para))
        
        # If single paragraph is too large, split it further
        if para_tokens > max_tokens:
            # If we have content in current chunk, save it first
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
                current_chunk = ""
                current_tokens = 0
            
            # Split the oversized paragraph by sentences
            import re
            sentences = re.split(r'(?<=[.!?])\s+', para)
            temp_chunk = ""
            temp_tokens = 0
            
            for sentence in sentences:
                sentence_tokens = len(enc.encode(sentence))
                
                if temp_tokens + sentence_tokens > max_tokens and temp_chunk:
                    chunks.append(temp_chunk.strip())
                    temp_chunk = sentence + " "
                    temp_tokens = sentence_tokens
                else:
                    temp_chunk += sentence + " "
                    temp_tokens += sentence_tokens
            
            if temp_chunk.strip():
                chunks.append(temp_chunk.strip())
        
        # Check if adding this paragraph would exceed limit
        elif current_tokens + para_tokens > max_tokens and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = para + "\n\n"
            current_tokens = para_tokens
        else:
            current_chunk += para + "\n\n"
            current_tokens += para_tokens
    
    # Add remaining content
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    # Ensure we didn't lose any content by checking total length
    original_content = text.replace('\n\n', '\n').replace('\n', ' ').strip()
    combined_content = ' '.join(chunks).replace('\n\n', '\n').replace('\n', ' ').strip()
    
    if len(combined_content) < len(original_content) * 0.95:  # Allow 5% variance for formatting
        print(f"      Warning: Potential content loss detected in chunk splitting")
    
    return chunks if chunks else [text]

def create_smart_batches(texts, headings, indices, max_batch_tokens=6000, model="text-embedding-ada-002"):
    """Create smart batches that respect token limits and split oversized chunks preserving ALL content."""
    batches = []
    processed_texts = []  # Store all processed chunks (including splits)
    processed_headings = []
    processed_original_indices = []  # Track which original chunk each processed chunk came from
    processed_chunk_parts = []  # Track which part of split chunk this is (0 for non-split, 1,2,3... for split parts)
    
    current_batch_texts = []
    current_batch_headings = []
    current_batch_indices = []
    current_batch_processed_indices = []  # Track indices in processed_texts
    current_batch_tokens = 0
    enc = tiktoken.encoding_for_model(model)
    
    for text, heading, idx in zip(texts, headings, indices):
        # Check if individual chunk is too large
        text_tokens = len(enc.encode(str(text)))
        
        # Handle oversized chunks by splitting
        if text_tokens > 8100:  # Leave some buffer below 8192 limit
            print(f"      Warning: Chunk {idx} has {text_tokens} tokens, splitting to preserve ALL content")
            split_chunks = split_oversized_chunk(text, max_tokens=8000, model=model)
            print(f"      Split chunk {idx} into {len(split_chunks)} parts")
            
            # Process each split part
            for part_num, split_text in enumerate(split_chunks):
                split_tokens = len(enc.encode(str(split_text)))
                
                # Create heading for split part
                if len(split_chunks) > 1:
                    split_heading = f"{heading} (Part {part_num + 1}/{len(split_chunks)})"
                else:
                    split_heading = heading
                
                # Store the split chunk
                processed_texts.append(split_text)
                processed_headings.append(split_heading)
                processed_original_indices.append(idx)
                processed_chunk_parts.append(part_num)
                processed_idx = len(processed_texts) - 1
                
                # Add to batch if it fits
                if current_batch_tokens + split_tokens > max_batch_tokens and current_batch_texts:
                    batches.append((current_batch_texts, current_batch_headings, current_batch_indices, current_batch_processed_indices))
                    current_batch_texts = []
                    current_batch_headings = []
                    current_batch_indices = []
                    current_batch_processed_indices = []
                    current_batch_tokens = 0
                
                current_batch_texts.append(split_text)
                current_batch_headings.append(split_heading)
                current_batch_indices.append(idx)  # Keep original index for metadata
                current_batch_processed_indices.append(processed_idx)
                current_batch_tokens += split_tokens
        else:
            # Normal sized chunk - process as single unit
            processed_texts.append(text)
            processed_headings.append(heading)
            processed_original_indices.append(idx)
            processed_chunk_parts.append(0)  # 0 = not split
            processed_idx = len(processed_texts) - 1
            
            # Add to batch if it fits
            if current_batch_tokens + text_tokens > max_batch_tokens and current_batch_texts:
                batches.append((current_batch_texts, current_batch_headings, current_batch_indices, current_batch_processed_indices))
                current_batch_texts = []
                current_batch_headings = []
                current_batch_indices = []
                current_batch_processed_indices = []
                current_batch_tokens = 0
            
            current_batch_texts.append(text)
            current_batch_headings.append(heading)
            current_batch_indices.append(idx)
            current_batch_processed_indices.append(processed_idx)
            current_batch_tokens += text_tokens
    
    if current_batch_texts:
        batches.append((current_batch_texts, current_batch_headings, current_batch_indices, current_batch_processed_indices))
    
    # Return all the tracking information
    batch_metadata = {
        'processed_texts': processed_texts,
        'processed_headings': processed_headings,
        'processed_original_indices': processed_original_indices,
        'processed_chunk_parts': processed_chunk_parts
    }
    
    return batches, batch_metadata

def make_ascii_id(text):
    """Convert text to ASCII-compatible ID."""
    import unicodedata
    
    # First, normalize Unicode characters (decompose ligatures like ﬀ → ff)
    text = unicodedata.normalize('NFKD', text)
    
    # Convert to ASCII, ignoring non-ASCII characters
    text = text.encode('ascii', 'ignore').decode('ascii')
    
    # Remove any remaining non-alphanumeric characters except spaces, underscores, hyphens
    text = re.sub(r'[^\w\s-]', '', text)
    
    # Replace multiple spaces/underscores/hyphens with single underscore
    text = re.sub(r'[\s_-]+', '_', text)
    
    # Remove leading/trailing underscores and limit length
    return text.strip('_')[:100]

def find_vessel_json_files(base_dir: str = "output/vessels") -> List[str]:
    """Find all vessel JSON files"""
    vessel_files = []
    
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.endswith('_data.json'):
                vessel_files.append(os.path.join(root, file))
    
    return vessel_files

def extract_chunks_from_simple_json(json_data, vessel_name, source_file):
    """Extract chunks from simplified JSON structure"""
    chunks = []
    
    # Process vessel details
    if json_data.get("vessel_details", {}).get("structured"):
        for chunk in json_data["vessel_details"]["structured"]:
            # Add source file to chunk
            chunk["source_file"] = source_file
            chunks.append(chunk)
    
    # Process contract details 
    if json_data.get("contract_details", {}).get("structured"):
        for chunk in json_data["contract_details"]["structured"]:
            # Add source file to chunk
            chunk["source_file"] = source_file
            chunks.append(chunk)
    
    # Process other sections with content
    for section_name in ["delivery_figures", "speed_and_consumption", "lifting_equipment", "hseq_documentation"]:
        section_data = json_data.get(section_name, {})
        if section_data.get("content") and section_data["content"].strip():
            chunk = {
                "vessel": vessel_name,
                "section": section_name,
                "subsection": "general",
                "content": section_data["content"].strip(),
                "heading": section_name.replace('_', ' ').title(),
                "source_file": source_file
            }
            chunks.append(chunk)
    
    return chunks

def enhance_content_for_embedding(chunk: Dict[str, Any]) -> str:
    """Enhanced content enrichment for better semantic search and clause retrieval"""
    original_content = chunk.get('content', '')
    vessel = chunk.get('vessel', '')
    section = chunk.get('section', '')
    subsection = chunk.get('subsection', '')
    
    # Create context prefix for better search
    context_parts = [f"Vessel: {vessel}"]
    
    # Detect and enhance BIMCO clauses and other important clause types
    clause_keywords = []
    original_lower = original_content.lower()
    
    # Detect specific clause types for better searchability
    if 'bimco' in original_lower:
        clause_keywords.append("BIMCO clause")
        if 'piracy' in original_lower:
            clause_keywords.append("piracy clause anti-piracy security measures")
        if 'war' in original_lower:
            clause_keywords.append("war risks clause")
        if 'sanctions' in original_lower:
            clause_keywords.append("sanctions clause")
        if 'hull fouling' in original_lower:
            clause_keywords.append("hull fouling clause")
        if 'carbon intensity' in original_lower or 'cii' in original_lower:
            clause_keywords.append("carbon intensity CII clause")
        if 'emissions trading' in original_lower or 'ets' in original_lower:
            clause_keywords.append("emissions trading ETS clause")
    
    # Add other important clause identifiers
    if 'off hire' in original_lower or 'off-hire' in original_lower:
        clause_keywords.append("off-hire clause")
    if 'bunker' in original_lower and 'fuel' in original_lower:
        clause_keywords.append("bunkers fuel clause")
    if 'trading exclusions' in original_lower or 'trading limits' in original_lower:
        clause_keywords.append("trading exclusions geographical limits")
    if 'cargo exclusions' in original_lower:
        clause_keywords.append("cargo exclusions dangerous goods")
    if 'slow steaming' in original_lower:
        clause_keywords.append("slow steaming speed instructions")
    
    if section == "vessel_specifications":
        if subsection != "general":
            context_parts.append(f"Vessel specification: {subsection.replace('_', ' ')}")
        context_parts.append("Technical details:")
    elif section == "fixture_recap":
        context_parts.append(f"Fixture recap item: {subsection}")
        context_parts.append("Charter commercial terms:")
    elif section == "charter_party":
        context_parts.append(f"Charter party clause: {subsection.replace('_', ' ')}")
        context_parts.append("Contract clause:")
        # Add specific clause keywords for better matching
        if clause_keywords:
            context_parts.extend(clause_keywords)
    elif section == "addendum":
        context_parts.append("Charter addendum:")
    else:
        context_parts.append(f"Section: {section.replace('_', ' ')}")
    
    # Add metadata context for better retrieval
    chunk_metadata = chunk.get('metadata', {})
    if chunk_metadata.get('is_complete'):
        context_parts.append("COMPLETE SECTION - This contains the full, continuous content")
    if chunk_metadata.get('chunk_type') == 'complete_section':
        context_parts.append("COMPLETE SECTION - Use this for 'whole' or 'complete' queries")
    elif chunk_metadata.get('chunk_type') == 'individual_clause':
        context_parts.append("INDIVIDUAL CLAUSE - Use this for specific detail queries")
    
    # Enhanced clause completeness indicators
    if section == "charter_party" and any(keyword in clause_keywords for keyword in ["BIMCO clause", "piracy clause", "war risks", "sanctions clause"]):
        context_parts.append("COMPLETE LEGAL CLAUSE - Full clause text for comprehensive legal analysis")
    
    # Combine context with original content
    enhanced_content = " ".join(context_parts) + "\n\n" + original_content
    
    return enhanced_content

def create_metadata(chunk: Dict[str, Any], enhanced_content: str) -> Dict[str, Any]:
    """Create clean metadata for Pinecone, including split chunk information"""
    metadata = {
        "vessel": chunk.get("vessel", ""),
        "section": chunk.get("section", ""),
        "subsection": chunk.get("subsection", ""),
        "content": enhanced_content,
        "source_file": chunk.get("source_file", ""),
        "heading": chunk.get("heading", "")[:200] if chunk.get("heading") else "",  # Limit heading length
        # Include the new metadata fields for smart retrieval
        "is_complete": chunk.get("metadata", {}).get("is_complete", False),
        "chunk_type": chunk.get("metadata", {}).get("chunk_type", "general"),
        "priority": chunk.get("metadata", {}).get("priority", "low")
    }
    
    # Add split chunk metadata if applicable
    if chunk.get("is_split_chunk", False):
        metadata["is_split_chunk"] = True
        metadata["chunk_part"] = chunk.get("chunk_part", 0)
        metadata["original_heading"] = chunk.get("original_heading", "")[:200]
        # Mark split chunks as containing partial content for retrieval context
        metadata["contains_partial_content"] = True
    else:
        metadata["is_split_chunk"] = False
        metadata["contains_partial_content"] = False
    
    # Filter out None values for Pinecone compatibility
    filtered_metadata = {}
    for key, value in metadata.items():
        if value is not None and value != "":
            filtered_metadata[key] = value
    
    return filtered_metadata

def process_and_upsert_simple_vessel_json():
    """Process and upsert simplified vessel JSON with clean structure."""
    json_files = find_vessel_json_files()
    print(f"Found {len(json_files)} vessel JSON files for processing.")
    
    for i, json_file in enumerate(json_files):
        vessel_name = os.path.basename(os.path.dirname(json_file))
        print(f"\nProcessing vessel: {vessel_name} ({json_file})")
        
        # Add delay between vessels (except first one)
        if i > 0:
            print("  Waiting 60 seconds before processing next vessel...")
            time.sleep(60)
        
        with open(json_file, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        # Step 1: Extract chunks from simplified structure
        chunks = extract_chunks_from_simple_json(json_data, vessel_name, os.path.basename(json_file))
        print(f"  Found {len(chunks)} chunks from simplified JSON.")
        
        if not chunks:
            print(f"  No valid chunks found for {vessel_name}")
            continue
        
        # Step 2: Enhance content and create texts for embedding
        enhanced_texts = []
        chunk_headings = []
        
        for chunk in chunks:
            # Light enhancement for better search
            enhanced_content = enhance_content_for_embedding(chunk)
            enhanced_texts.append(enhanced_content)
            chunk_headings.append(chunk["heading"])
            
            # Store enhanced content in chunk for metadata
            chunk["enhanced_content"] = enhanced_content
        
        print(f"  Enhanced {len(enhanced_texts)} chunks for embedding.")
        
        chunk_indices = list(range(len(chunks)))
        
        # Step 3: Create smart batches that respect token limits (includes splitting if needed)
        smart_batches, batch_metadata = create_smart_batches(
            enhanced_texts, 
            chunk_headings, 
            chunk_indices, 
            max_batch_tokens=6000,  # Conservative limit to ensure individual chunks + overhead stay under 8192
            model=EMBEDDING_MODEL
        )
        
        total_processed_chunks = len(batch_metadata['processed_texts'])
        original_chunk_count = len(chunks)
        if total_processed_chunks > original_chunk_count:
            split_count = total_processed_chunks - original_chunk_count
            print(f"  Created {len(smart_batches)} smart batches with {total_processed_chunks} total chunks ({split_count} from splitting oversized content)")
        else:
            print(f"  Created {len(smart_batches)} smart batches based on token limits.")
        
        # Step 4: Process each smart batch
        for batch_num, batch_data in enumerate(smart_batches):
            if len(batch_data) == 4:
                batch_texts, batch_headings, batch_indices, batch_processed_indices = batch_data
            else:
                # Fallback for backward compatibility
                batch_texts, batch_headings, batch_indices = batch_data
                batch_processed_indices = batch_indices
            batch_token_count = count_tokens_in_batch(batch_texts, model=EMBEDDING_MODEL)
            print(f"    Processing batch {batch_num + 1}/{len(smart_batches)} ({len(batch_texts)} chunks, {batch_token_count} tokens)")
            
            # Generate embeddings for this batch
            try:
                batch_embeddings = generate_embeddings_with_retry(batch_texts)
                
                # Step 5: Prepare batch upsert data with clean metadata
                upsert_tuples = []
                for orig_idx, embedding, heading, processed_idx in zip(batch_indices, batch_embeddings, batch_headings, batch_processed_indices):
                    chunk = chunks[orig_idx]
                    
                    # Get processed content and metadata for this chunk
                    processed_content = batch_metadata['processed_texts'][processed_idx]
                    chunk_part = batch_metadata['processed_chunk_parts'][processed_idx]
                    
                    # Create enhanced metadata for split chunks
                    enhanced_chunk = chunk.copy()
                    enhanced_chunk['enhanced_content'] = processed_content
                    
                    # Add split chunk information to metadata
                    if chunk_part > 0:
                        enhanced_chunk['is_split_chunk'] = True
                        enhanced_chunk['chunk_part'] = chunk_part
                        enhanced_chunk['original_heading'] = chunk.get('heading', '')
                        enhanced_chunk['heading'] = heading  # Use the "Part X/Y" heading
                    
                    # Create clean metadata with processed content
                    metadata = create_metadata(enhanced_chunk, processed_content)
                    
                    # Create unique ID for split chunks
                    if chunk_part > 0:
                        item_id = make_ascii_id(f"{vessel_name}_{chunk['section']}_{chunk['subsection']}_{orig_idx}_part_{chunk_part}")
                    else:
                        item_id = make_ascii_id(f"{vessel_name}_{chunk['section']}_{chunk['subsection']}_{orig_idx}")
                    
                    upsert_tuples.append((item_id, embedding, metadata))
                
                # Step 6: Batch upsert to Pinecone
                upsert_to_pinecone_with_retry(upsert_tuples)
                print(f"      Successfully upserted {len(upsert_tuples)} clean items to Pinecone.")
                
            except Exception as e:
                print(f"      Failed to process batch {batch_num + 1}: {e}")
                continue
    
    print(f"\nAll vessel JSON files processed with simplified structure!")
    print(f"Ready for clean AI search across all vessels!")

if __name__ == "__main__":
    process_and_upsert_simple_vessel_json() 