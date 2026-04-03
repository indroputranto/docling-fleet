#!/usr/bin/env python3
"""
New Embedding Uploader - Enhanced for Chapter-Based Structure
Works with the new chapter-based output from process_vessel_new.py
Handles strikethrough formatting with double tildes and organized clause structure
"""

import os
import json
from pinecone import Pinecone
from openai import OpenAI
import tiktoken
import re
import time
from typing import List, Dict, Any, Tuple, Set
import hashlib
from collections import defaultdict
from pathlib import Path

# Pinecone configuration
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "")
PINECONE_HOST = "https://vessel-embeddings-u2t79ad.svc.aped-4627-b74a.pinecone.io"
INDEX_NAME = "vessel-embeddings"
DIMENSION = 1536
METRIC = "cosine"

pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(INDEX_NAME)

# Embedding API configuration - OpenAI (direct)
# Uses OPENAI_API_KEY environment variable
EMBEDDING_MODEL = "text-embedding-3-small"
client = OpenAI()  # Uses OPENAI_API_KEY from env by default

# Langdock Embedding API configuration (commented out - revert if needed)
# LANGDOCK_API_KEY = "sk-do3FADTbdhFAzdMJufXGR0qyddP0uh3JbliKS-L9svwBUQyMvQhTiPVgl_k7_D7dS1fklbIJvzWi0JlGMjTsBA"
# LANGDOCK_REGION = "eu"
# LANGDOCK_BASE_URL = f"https://api.langdock.com/openai/{LANGDOCK_REGION}/v1"
# EMBEDDING_MODEL = "text-embedding-ada-002"
# client = OpenAI(base_url=LANGDOCK_BASE_URL, api_key=LANGDOCK_API_KEY)

class ChapterBasedSemanticEnhancer:
    """Enhanced semantic processing for chapter-based vessel data structure"""
    
    def __init__(self):
        self.chapter_enhancement_templates = {
            '1_vessel_details': {
                'boost_keywords': ['vessel specifications', 'vessel details', 'technical specifications', 'vessel particulars', 'ship specifications', 'vessel dimensions', 'vessel capacities', 'vessel equipment'],
                'context_prefix': 'Vessel Technical Specifications: Complete vessel details including',
                'description_suffix': 'This contains comprehensive vessel specifications including dimensions, capacities, equipment, and technical particulars.',
                'priority': 'high'
            },
            '2_contract_details': {
                'boost_keywords': ['charter party', 'contract terms', 'charter clauses', 'commercial terms', 'legal provisions', 'charter conditions'],
                'context_prefix': 'Charter Party Contract: Legal and commercial terms including',
                'description_suffix': 'This contains the complete charter party contract with all legal clauses and commercial conditions.',
                'priority': 'high'
            },
            'fixture_recap': {
                'boost_keywords': ['fixture recap', 'charter summary', 'commercial terms', 'key terms', 'charter arrangement', 'fixture details'],
                'context_prefix': 'Fixture Recap: Key commercial terms and charter arrangements including',
                'description_suffix': 'This fixture recap contains essential commercial terms, delivery arrangements, and key charter conditions.',
                'priority': 'high'
            },
            'charter_party': {
                'boost_keywords': ['charter party clauses', 'contract clauses', 'legal terms', 'charter provisions', 'contractual obligations'],
                'context_prefix': 'Charter Party Clauses: Individual contract provisions including',
                'description_suffix': 'This contains individual charter party clauses with specific legal and operational terms.',
                'priority': 'high'
            },
            'addendum': {
                'boost_keywords': ['charter addendum', 'contract amendments', 'additional terms', 'supplementary clauses', 'charter modifications'],
                'context_prefix': 'Charter Addendum: Additional terms and modifications including',
                'description_suffix': 'This addendum contains additional terms and modifications to the main charter party.',
                'priority': 'medium'
            },
            'rider_clause': {
                'boost_keywords': ['rider clause', 'rider clauses', 'supplementary clauses', 'additional charter terms', 'charter modifications', 'rider provisions'],
                'context_prefix': 'Rider Clause of Charter Party: Supplementary terms and provisions including',
                'description_suffix': 'This rider clause contains supplementary terms that modify or add to the main charter party clauses.',
                'priority': 'high'
            },
            '3_delivery_figures': {
                'boost_keywords': ['delivery figures', 'delivery terms', 'delivery conditions', 'vessel delivery'],
                'context_prefix': 'Delivery Figures: Vessel delivery terms and conditions including',
                'description_suffix': 'This contains vessel delivery figures and delivery-related terms.',
                'priority': 'medium'
            },
            '4_speed_and_consumption': {
                'boost_keywords': ['speed consumption', 'vessel performance', 'fuel consumption', 'vessel speed', 'performance data'],
                'context_prefix': 'Speed and Consumption: Vessel performance specifications including',
                'description_suffix': 'This contains vessel speed and fuel consumption performance data.',
                'priority': 'medium'
            },
            '5_lifting_equipment': {
                'boost_keywords': ['lifting equipment', 'cargo gear', 'cranes', 'deck equipment', 'cargo handling'],
                'context_prefix': 'Lifting Equipment: Cargo handling equipment specifications including',
                'description_suffix': 'This contains specifications for vessel lifting equipment and cargo handling gear.',
                'priority': 'medium'
            },
            '6_hseq_documentation': {
                'boost_keywords': ['HSEQ documentation', 'safety documentation', 'health safety', 'environmental quality', 'compliance documents'],
                'context_prefix': 'HSEQ Documentation: Health, safety, environmental and quality documentation including',
                'description_suffix': 'This contains health, safety, environmental and quality documentation requirements.',
                'priority': 'low'
            },
            '7_vessel_communication_details': {
                'boost_keywords': ['vessel communication', 'contact details', 'captain contact', 'master contact', 'vessel phone', 'vessel email', 'communication details'],
                'context_prefix': 'Vessel Communication Details: Contact information and communication details including',
                'description_suffix': 'This contains vessel communication details, contact information, and captain/crew contact details.',
                'priority': 'medium'
            },
            '7_vessel_and_owners_details': {
                'boost_keywords': ['vessel and owners', 'owners details', 'owner contact', 'registered office', 'master contact', 'vessel phone', 'vessel email', 'owner address'],
                'context_prefix': 'Vessel and Owners details: Owner information, contact details and registered office including',
                'description_suffix': 'This contains vessel and owners details, owner contact information, and registered office address.',
                'priority': 'medium'
            }
        }
        
        # Clause-specific enhancement templates
        self.clause_enhancement_templates = {
            'bunkers': {
                'boost_keywords': ['bunkers clause', 'fuel delivery', 'bunker quantities', 'redelivery bunkers', 'fuel specifications', 'bunker sampling'],
                'context_prefix': 'Bunkers Clause: Fuel delivery and specifications including',
                'description_suffix': 'This bunkers clause contains fuel delivery, quality specifications, and bunker-related terms.',
                'priority': 'high'
            },
            'delivery': {
                'boost_keywords': ['delivery clause', 'vessel delivery', 'delivery port', 'delivery conditions', 'on-hire procedures'],
                'context_prefix': 'Delivery Clause: Vessel delivery procedures including',
                'description_suffix': 'This delivery clause contains vessel delivery procedures, location, and conditions.',
                'priority': 'high'
            },
            'redelivery': {
                'boost_keywords': ['redelivery clause', 'vessel redelivery', 'redelivery port', 'off-hire procedures', 'redelivery conditions'],
                'context_prefix': 'Redelivery Clause: Vessel redelivery procedures including',
                'description_suffix': 'This redelivery clause contains vessel redelivery procedures, location, and conditions.',
                'priority': 'high'
            },
            'hire': {
                'boost_keywords': ['hire clause', 'rate of hire', 'daily hire', 'hire payment', 'hire calculation'],
                'context_prefix': 'Hire Clause: Daily hire rates and payment terms including',
                'description_suffix': 'This hire clause contains daily hire rates, payment terms, and calculation methods.',
                'priority': 'high'
            },
            'performance': {
                'boost_keywords': ['performance clause', 'vessel performance', 'master duties', 'navigation requirements', 'voyage execution'],
                'context_prefix': 'Performance Clause: Vessel performance requirements including',
                'description_suffix': 'This performance clause contains vessel performance requirements and operational duties.',
                'priority': 'high'
            },
            'off_hire': {
                'boost_keywords': ['off-hire clause', 'hire suspension', 'breakdown conditions', 'vessel deficiency', 'time lost'],
                'context_prefix': 'Off-Hire Clause: Hire suspension conditions including',
                'description_suffix': 'This off-hire clause contains conditions for hire suspension and time lost procedures.',
                'priority': 'high'
            },
            'drydocking': {
                'boost_keywords': ['drydocking clause', 'drydock requirements', 'vessel maintenance', 'docking period', 'special survey'],
                'context_prefix': 'Drydocking Clause: Vessel maintenance requirements including',
                'description_suffix': 'This drydocking clause contains vessel maintenance, docking procedures, and survey requirements.',
                'priority': 'medium'
            }
        }
    
    def enhance_content_for_embedding(self, chunk_data: Dict[str, Any]) -> str:
        """Enhanced content strategy for chapter-based structure with strikethrough support"""
        original_content = chunk_data.get('content', '')
        chapter = chunk_data.get('chapter', '')
        sub_chapter = chunk_data.get('sub_chapter', '')
        clause_title = chunk_data.get('clause_title', '')
        vessel_name = chunk_data.get('vessel_name', '')
        
        # Preserve strikethrough formatting (double tildes)
        content = original_content
        
        # Determine enhancement template
        template = None
        
        # Check for rider clause first (highest priority)
        is_rider_clause = chunk_data.get('is_rider_clause', False)
        if is_rider_clause:
            template = self.chapter_enhancement_templates.get('rider_clause')
        elif sub_chapter and sub_chapter in self.chapter_enhancement_templates:
            template = self.chapter_enhancement_templates[sub_chapter]
        elif chapter and chapter in self.chapter_enhancement_templates:
            template = self.chapter_enhancement_templates[chapter]
        
        # Check for clause-specific enhancement (only if not already a rider clause)
        if not is_rider_clause and clause_title:
            clause_type = self._detect_clause_type(clause_title, content)
            if clause_type in self.clause_enhancement_templates:
                template = self.clause_enhancement_templates[clause_type]
        
        if not template:
            return content
        
        # Create enhanced content
        enhanced_parts = []
        
        # Add vessel context with unique identifiers for semantic search disambiguation
        # (e.g., MV OCEANIC vs Ocean7 vessels, MV NORDIC vs Nordic Felicia)
        vessel_identifiers = chunk_data.get("vessel_identifiers") or {}
        imo = vessel_identifiers.get("imo_number", "")
        call_sign = vessel_identifiers.get("call_sign", "")
        identifier_parts = [f"Vessel: {vessel_name}"]
        if imo:
            identifier_parts.append(f"IMO: {imo}")
        if call_sign:
            identifier_parts.append(f"Call sign: {call_sign}")
        enhanced_parts.append(" ".join(identifier_parts))
        
        # Add chapter context
        if chapter:
            enhanced_parts.append(f"Chapter: {chapter.replace('_', ' ').title()}")
        if sub_chapter:
            enhanced_parts.append(f"Sub-chapter: {sub_chapter.replace('_', ' ').title()}")
        
        # Add rider clause context if applicable
        if is_rider_clause:
            rider_clause_number = chunk_data.get('rider_clause_number')
            if rider_clause_number:
                enhanced_parts.append(f"Rider Clause {rider_clause_number}: {clause_title}")
            else:
                enhanced_parts.append(f"Rider Clause: {clause_title}")
        elif clause_title:
            enhanced_parts.append(f"Clause: {clause_title}")
        
        # Add context prefix
        enhanced_parts.append(f"{template['context_prefix']} for {vessel_name}:")
        
        # Add boost keywords
        boost_keywords = ' '.join(template['boost_keywords'])
        enhanced_parts.append(f"Keywords: {boost_keywords}")
        
        # Add original content (preserving strikethrough)
        enhanced_parts.append("Content:")
        enhanced_parts.append(content)
        
        # Add description suffix
        enhanced_parts.append(template['description_suffix'])
        
        return '\n\n'.join(enhanced_parts)
    
    def _detect_clause_type(self, clause_title: str, content: str) -> str:
        """Detect clause type from title and content"""
        title_lower = clause_title.lower()
        content_lower = content.lower()
        
        # Direct title matches
        if 'bunker' in title_lower:
            return 'bunkers'
        elif 'delivery' in title_lower and 'redelivery' not in title_lower:
            return 'delivery'
        elif 'redelivery' in title_lower:
            return 'redelivery'
        elif 'hire' in title_lower and 'off' not in title_lower:
            return 'hire'
        elif 'off' in title_lower and 'hire' in title_lower:
            return 'off_hire'
        elif 'performance' in title_lower or 'voyage' in title_lower:
            return 'performance'
        elif 'drydock' in title_lower or 'dry dock' in title_lower:
            return 'drydocking'
        elif 'survey' in title_lower:
            return 'survey'
        elif 'speed' in title_lower and 'consumption' in title_lower:
            return 'speed_consumption'
        elif 'insurance' in title_lower:
            return 'insurance'
        elif 'pollution' in title_lower:
            return 'pollution'
        elif 'lien' in title_lower:
            return 'liens'
        elif 'salvage' in title_lower:
            return 'salvage'
        elif 'general average' in title_lower:
            return 'general_average'
        elif 'exception' in title_lower:
            return 'exceptions'
        elif 'liberty' in title_lower or 'liberties' in title_lower:
            return 'liberties'
        elif 'war' in title_lower and 'risk' in title_lower:
            return 'war_risks'
        elif 'piracy' in title_lower:
            return 'piracy'
        elif 'sanction' in title_lower:
            return 'sanctions'
        
        # Content-based detection (more comprehensive)
        elif 'bunker' in content_lower and 'fuel' in content_lower:
            return 'bunkers'
        elif 'off hire' in content_lower or 'off-hire' in content_lower:
            return 'off_hire'
        elif 'hire' in content_lower and ('payment' in content_lower or 'rate' in content_lower):
            return 'hire'
        elif 'survey' in content_lower and ('delivery' in content_lower or 'redelivery' in content_lower):
            return 'survey'
        elif 'speed' in content_lower and 'consumption' in content_lower:
            return 'speed_consumption'
        elif 'insurance' in content_lower and 'vessel' in content_lower:
            return 'insurance'
        elif 'bimco' in content_lower:
            if 'war' in content_lower:
                return 'war_risks'
            elif 'piracy' in content_lower:
                return 'piracy'
            elif 'sanction' in content_lower:
                return 'sanctions'
            else:
                return 'bimco_clause'
        
        return 'general'
    
    def create_enhanced_metadata(self, chunk_data: Dict[str, Any], enhanced_content: str) -> Dict[str, Any]:
        """Create enhanced metadata for chapter-based structure"""
        vessel_name = chunk_data.get('vessel_name', '')
        chapter = chunk_data.get('chapter', '')
        sub_chapter = chunk_data.get('sub_chapter', '')
        clause_title = chunk_data.get('clause_title', '')
        vessel_identifiers = chunk_data.get("vessel_identifiers") or {}
        
        # Base metadata
        metadata = {
            "vessel": vessel_name,
            "chapter": chapter,
            "content": enhanced_content,
            "source_file": chunk_data.get('source_file', ''),
            "content_type": "chapter_based",
            "structure_version": "new_chapter_based",
            
            # Content classification
            "is_enhanced": True,
            "enhanced_for_search": True,
        }
        
        # Add chapter-specific metadata
        if chapter:
            metadata["chapter_name"] = chapter.replace('_', ' ').title()
            metadata["chapter_number"] = chapter.split('_')[0] if '_' in chapter else chapter
        
        if sub_chapter:
            metadata["sub_chapter"] = sub_chapter
            metadata["sub_chapter_name"] = sub_chapter.replace('_', ' ').title()
        
        if clause_title:
            metadata["clause_title"] = clause_title
            metadata["is_clause"] = True
            clause_type = self._detect_clause_type(clause_title, chunk_data.get('content', ''))
            metadata["clause_type"] = clause_type
            
            # Add clause number if available
            clause_number = chunk_data.get('clause_number')
            if clause_number:
                metadata["clause_number"] = clause_number
                metadata["is_individual_clause"] = True
        else:
            metadata["is_clause"] = False
            metadata["is_individual_clause"] = False
        
        # Rider clause specific metadata
        is_rider_clause = chunk_data.get('is_rider_clause', False)
        if is_rider_clause:
            metadata["is_rider_clause"] = True
            metadata["has_rider_clause"] = True  # Vessel has rider clauses
            
            # Add rider clause number if available
            rider_clause_number = chunk_data.get('rider_clause_number')
            if rider_clause_number:
                metadata["rider_clause_number"] = rider_clause_number
                metadata["rider_clause_title"] = clause_title
            
            # Set high priority for rider clauses
            metadata["priority"] = "high"
        else:
            metadata["is_rider_clause"] = False
        
        # Priority based on chapter/content type
        if chapter in ['1_vessel_details', '2_contract_details'] or sub_chapter in ['fixture_recap', 'charter_party']:
            metadata["priority"] = "high"
        elif chapter in ['3_delivery_figures', '4_speed_and_consumption', '5_lifting_equipment']:
            metadata["priority"] = "medium"
        else:
            metadata["priority"] = "low"
        
        # Content type flags
        metadata["is_vessel_specifications"] = chapter == '1_vessel_details'
        metadata["is_contract_details"] = chapter == '2_contract_details'
        metadata["is_fixture_recap"] = sub_chapter == 'fixture_recap'
        metadata["is_charter_party"] = sub_chapter == 'charter_party'
        metadata["is_addendum"] = sub_chapter == 'addendum'
        
        # Strikethrough detection
        content = chunk_data.get('content', '')
        metadata["contains_strikethrough"] = '~~' in content
        
        # Add unique identifiers for vessel disambiguation (enables metadata filtering)
        if vessel_identifiers.get("imo_number"):
            metadata["imo_number"] = vessel_identifiers["imo_number"]
        if vessel_identifiers.get("call_sign"):
            metadata["call_sign"] = vessel_identifiers["call_sign"]
        
        # Filter out None values for Pinecone compatibility
        filtered_metadata = {}
        for key, value in metadata.items():
            if value is not None and value != "":
                filtered_metadata[key] = value
        
        return filtered_metadata

def clear_pinecone_index():
    """Clear all vectors from the Pinecone index"""
    print("🧹 Clearing Pinecone index...")
    try:
        # Get current stats
        stats = index.describe_index_stats()
        current_count = stats.get('total_vector_count', 0)
        print(f"   Current vectors in index: {current_count}")
        
        if current_count == 0:
            print("   Index is already empty.")
            return True
        
        # Delete all vectors in the default namespace
        index.delete(delete_all=True, namespace="")  # Explicitly use default namespace
        print("   Index cleared successfully")
        
        # Verify it's empty
        stats_after = index.describe_index_stats()
        new_count = stats_after.get('total_vector_count', 0)
        print(f"   Vectors after clearing: {new_count}")
        return True
        
    except Exception as e:
        if "Namespace not found" in str(e):
            print("   Namespace not found. Index is already empty.")
            return True
        print(f"   ❌ Error clearing index: {e}")
        return False

def retry_with_exponential_backoff(func, max_retries=3, base_delay=1, max_delay=60):
    """Retry a function with exponential backoff"""
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
    """Generate embeddings with retry logic"""
    def _generate():
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts,
            encoding_format="float"
        )
        return [item.embedding for item in response.data]
    
    return retry_with_exponential_backoff(_generate)

def upsert_to_pinecone_with_retry(upsert_tuples):
    """Upsert data to Pinecone with retry logic"""
    def _upsert():
        return index.upsert(upsert_tuples)
    
    return retry_with_exponential_backoff(_upsert)

def count_tokens_in_batch(texts, model="text-embedding-3-small"):
    """Count tokens in a batch of texts (uses cl100k_base for text-embedding-3 models)"""
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return sum(len(enc.encode(str(text))) for text in texts)
    except Exception:
        # Fallback: rough estimation
        return sum(len(str(text).split()) * 1.3 for text in texts)

def create_smart_batches(chunks: List[Dict[str, Any]], max_batch_tokens=7000, model="text-embedding-3-small"):
    """Create smart batches that respect token limits"""
    batches = []
    current_batch_chunks = []
    current_batch_tokens = 0
    enc = tiktoken.get_encoding("cl100k_base")
    
    # Sort chunks to keep related content together
    sorted_chunks = sorted(chunks, key=lambda x: (
        x.get('chapter', ''),
        x.get('sub_chapter', ''),
        x.get('clause_title', '')
    ))
    
    for chunk in sorted_chunks:
        enhanced_content = chunk.get('enhanced_content', chunk.get('content', ''))
        chunk_tokens = len(enc.encode(str(enhanced_content)))
        
        # Start new batch if this chunk would exceed token limit
        if current_batch_tokens + chunk_tokens > max_batch_tokens and current_batch_chunks:
            batches.append(current_batch_chunks)
            current_batch_chunks = []
            current_batch_tokens = 0
        
        # Add chunk to current batch
        current_batch_chunks.append(chunk)
        current_batch_tokens += chunk_tokens
    
    # Add final batch if not empty
    if current_batch_chunks:
        batches.append(current_batch_chunks)
    
    return batches

def make_ascii_id(text: str) -> str:
    """Create ASCII-safe ID from text"""
    import unicodedata
    
    # First, normalize Unicode characters
    text = unicodedata.normalize('NFKD', text)
    
    # Convert to ASCII, ignoring non-ASCII characters
    text = text.encode('ascii', 'ignore').decode('ascii')
    
    # Remove any remaining non-alphanumeric characters except spaces, underscores, hyphens
    text = re.sub(r'[^\w\s-]', '', text)
    
    # Replace multiple spaces/underscores/hyphens with single underscore
    text = re.sub(r'[\s_-]+', '_', text)
    
    # Remove leading/trailing underscores and limit length
    return text.strip('_')[:100]

# State tracking file for change detection
STATE_FILE = ".embedding_state.json"

def calculate_file_hash(file_path: str) -> str:
    """Calculate SHA256 hash of a file"""
    hash_obj = hashlib.sha256()
    try:
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_obj.update(chunk)
        return hash_obj.hexdigest()
    except Exception as e:
        print(f"    ⚠️  Error calculating hash for {file_path}: {e}")
        return ""

def load_state() -> Dict[str, Dict[str, Any]]:
    """Load the state tracking file"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"  ⚠️  Error loading state file: {e}")
            return {}
    return {}

def save_state(state: Dict[str, Dict[str, Any]]):
    """Save the state tracking file"""
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"  ⚠️  Error saving state file: {e}")

def get_vessel_name_from_file(json_file: str) -> str:
    """Extract vessel name from JSON file path"""
    return os.path.basename(os.path.dirname(json_file))

def detect_changes(json_files: List[str], force_refresh: bool = False) -> Tuple[List[str], List[str], Set[str], Dict[str, Dict[str, Any]]]:
    """
    Detect which files have changed and need to be processed
    
    Returns:
        Tuple of (changed_files, new_files, removed_vessels, current_state)
    """
    current_state = {}
    previous_state = load_state() if not force_refresh else {}
    
    # Track current files and their hashes
    for json_file in json_files:
        file_hash = calculate_file_hash(json_file)
        vessel_name = get_vessel_name_from_file(json_file)
        current_state[json_file] = {
            "hash": file_hash,
            "vessel_name": vessel_name,
            "file_path": json_file
        }
    
    # Find changed and new files
    changed_files = []
    new_files = []
    
    for json_file in json_files:
        vessel_name = get_vessel_name_from_file(json_file)
        current_hash = current_state[json_file]["hash"]
        
        if json_file not in previous_state:
            new_files.append(json_file)
            print(f"  🆕 New file detected: {vessel_name}")
        elif previous_state[json_file].get("hash") != current_hash:
            changed_files.append(json_file)
            print(f"  🔄 Changed file detected: {vessel_name}")
    
    # Find removed vessels (files that were processed before but no longer exist)
    previous_vessels = {state.get("vessel_name") for state in previous_state.values()}
    current_vessels = {get_vessel_name_from_file(f) for f in json_files}
    removed_vessels = previous_vessels - current_vessels
    
    if removed_vessels:
        print(f"  🗑️  Removed vessels detected: {', '.join(removed_vessels)}")
    
    # Merge unchanged files into current_state to preserve them
    for json_file, file_state in previous_state.items():
        if json_file not in current_state and json_file in json_files:
            # File path might have changed, check by vessel name
            continue
        elif json_file in json_files and json_file not in changed_files and json_file not in new_files:
            # File unchanged, keep it in state
            current_state[json_file] = file_state
    
    return changed_files, new_files, removed_vessels, current_state

def delete_vessel_embeddings(vessel_name: str):
    """Delete all embeddings for a specific vessel from Pinecone"""
    try:
        print(f"    🗑️  Deleting embeddings for vessel: {vessel_name}")
        
        # Try to delete using metadata filter (Pinecone supports this in newer versions)
        try:
            index.delete(
                filter={"vessel": {"$eq": vessel_name}},
                namespace=""
            )
            print(f"      ✅ Deleted embeddings for {vessel_name}")
            return
        except Exception as e:
            # If direct delete with filter doesn't work, try alternative methods
            error_str = str(e).lower()
            if "filter" in error_str or "metadata" in error_str or "not supported" in error_str:
                # Fallback: Query-based deletion (requires fetching IDs first)
                print(f"      ⚠️  Filter-based delete not supported, using query-based deletion...")
                
                # We need to query to get IDs, but query requires a vector
                # Use a sparse vector approach or fetch all metadata
                # For now, we'll skip and let the upsert overwrite handle it
                # (since we delete before uploading new embeddings for the vessel)
                print(f"      ℹ️  Skipping explicit deletion - will overwrite with new embeddings")
                return
            else:
                # Re-raise if it's a different error
                raise e
                
    except Exception as e:
        print(f"      ⚠️  Error deleting embeddings for {vessel_name}: {e}")
        print(f"      ℹ️  Will proceed - new embeddings will overwrite old ones")

def find_vessel_json_files(base_dir: str = "output/vessels") -> List[str]:
    """Find all vessel JSON files"""
    vessel_files = []
    
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            if file.endswith('_data.json'):
                vessel_files.append(os.path.join(root, file))
    
    return vessel_files

def extract_vessel_identifiers(json_data: Dict[str, Any]) -> Dict[str, str]:
    """
    Extract unique vessel identifiers (IMO number, call sign) from vessel details.
    These identifiers make each vessel's embedding more distinct for semantic search,
    helping differentiate similar vessel names (e.g., MV OCEANIC vs Ocean7 vessels).
    """
    identifiers = {"imo_number": "", "call_sign": ""}
    
    if "chapters" not in json_data:
        return identifiers
    
    chapters = json_data["chapters"]
    vessel_details = chapters.get("1_vessel_details")
    if not vessel_details or not isinstance(vessel_details, dict):
        return identifiers
    
    content = vessel_details.get("content", [])
    if isinstance(content, list):
        content_text = "\n".join(str(item) for item in content if item)
    elif isinstance(content, str):
        content_text = content
    else:
        return identifiers
    
    # IMO number: handles "IMO number: 9624550", "IMO No.: 9267742", "IMO: 9549566"
    imo_patterns = [
        r"IMO\s*(?:number|no\.?)?\s*:\s*(\d{7})",
        r"IMO\s*:\s*(\d{7})",
        r"IMO\s+number\s+(\d{7})",
    ]
    for pattern in imo_patterns:
        match = re.search(pattern, content_text, re.IGNORECASE)
        if match:
            identifiers["imo_number"] = match.group(1)
            break
    
    # Call sign: handles "Call sign: PBYH", "Call Sign: D5AH2"
    call_sign_patterns = [
        r"Call\s*sign\s*:\s*([A-Z0-9]{4,8})",
        r"Call\s*Sign\s*:\s*([A-Z0-9]{4,8})",
    ]
    for pattern in call_sign_patterns:
        match = re.search(pattern, content_text, re.IGNORECASE)
        if match:
            identifiers["call_sign"] = match.group(1).upper()
            break
    
    return identifiers


def extract_chunks_from_chapter_based_json(json_data: Dict[str, Any], vessel_name: str, source_file: str, vessel_identifiers: Dict[str, str] = None) -> List[Dict[str, Any]]:
    """Extract chunks from chapter-based JSON structure"""
    chunks = []
    vessel_identifiers = vessel_identifiers or {}
    
    # Handle the new structure from process_vessel_new.py
    if "chapters" in json_data:
        chapters_data = json_data["chapters"]
        
        for chapter_key, chapter_data in chapters_data.items():
            if not isinstance(chapter_data, dict):
                continue
            
            # Handle direct chapter content (list of strings) OR tables-only chapters
            content_text = ""
            
            # First try to get content from content array
            if 'content' in chapter_data and chapter_data['content']:
                content = chapter_data['content']
                
                # Handle content as list of strings
                if isinstance(content, list):
                    content_text = '\n'.join(str(item) for item in content if item)
                elif isinstance(content, str):
                    content_text = content
            
            # If no content but tables exist, convert tables to text format
            if not content_text.strip() and 'tables' in chapter_data and chapter_data['tables']:
                tables = chapter_data['tables']
                table_parts = []
                
                for table_idx, table in enumerate(tables):
                    if table and isinstance(table, list):
                        table_text_rows = []
                        for row in table:
                            if isinstance(row, list):
                                # Join row cells with | separator, preserve empty cells as "N/A"
                                processed_cells = []
                                for cell in row:
                                    cell_str = str(cell).strip()
                                    if cell_str:
                                        processed_cells.append(cell_str)
                                    else:
                                        processed_cells.append("N/A")
                                
                                row_text = ' | '.join(processed_cells)
                                if row_text.strip():
                                    table_text_rows.append(row_text)
                        
                        if table_text_rows:
                            table_parts.append('\n'.join(table_text_rows))
                
                if table_parts:
                    content_text = '\n\n'.join(table_parts)
            
            # Create chunk if we have either content or table data
            if content_text and content_text.strip():
                chunk = {
                    "vessel_name": vessel_name,
                    "source_file": source_file,
                    "chapter": chapter_key,
                    "sub_chapter": "",
                    "clause_title": "",
                    "content": content_text.strip(),
                    "heading": chapter_data.get('title', chapter_key.replace('_', ' ').title()),
                    "clause_number": None,
                    "line_count": len(content_text.split('\n')),
                    "is_table_based": 'tables' in chapter_data and chapter_data['tables'] and not chapter_data.get('content'),
                    "vessel_identifiers": vessel_identifiers,
                }
                chunks.append(chunk)
            
            # Handle sub-chapters structure (for contract_details)
            if 'sub_chapters' in chapter_data:
                sub_chapters = chapter_data['sub_chapters']
                for sub_key, sub_data in sub_chapters.items():
                    if isinstance(sub_data, dict):
                        # First, handle individual clauses if they exist (for charter_party)
                        if 'clauses' in sub_data and isinstance(sub_data['clauses'], dict):
                            for clause_key, clause_data in sub_data['clauses'].items():
                                if isinstance(clause_data, dict) and 'content' in clause_data:
                                    content = clause_data['content']
                                    
                                    # Handle content as list of strings
                                    if isinstance(content, list):
                                        content_text = '\n'.join(str(item) for item in content if item)
                                    elif isinstance(content, str):
                                        content_text = content
                                    else:
                                        continue
                                    
                                    if content_text and content_text.strip():
                                        # Extract clause number from clause_key (e.g., "clause_1" -> "1")
                                        clause_number = clause_key.replace('clause_', '') if clause_key.startswith('clause_') else clause_key
                                        
                                        chunk = {
                                            "vessel_name": vessel_name,
                                            "source_file": source_file,
                                            "chapter": chapter_key,
                                            "sub_chapter": sub_key,
                                            "clause_title": clause_data.get('title', f"Clause {clause_number}"),
                                            "content": content_text.strip(),
                                            "heading": clause_data.get('title', f"Charter Party Clause {clause_number}"),
                                            "clause_number": clause_number,
                                            "line_count": len(content_text.split('\n')),
                                            "vessel_identifiers": vessel_identifiers,
                                        }
                                        chunks.append(chunk)
                        
                        # Handle rider clause if it exists (for charter_party with rider clauses)
                        if 'rider_clause' in sub_data and isinstance(sub_data['rider_clause'], dict):
                            rider_clause_data = sub_data['rider_clause']
                            
                            # Handle individual rider clauses
                            if 'clauses' in rider_clause_data and isinstance(rider_clause_data['clauses'], dict):
                                for rider_key, rider_data in rider_clause_data['clauses'].items():
                                    if isinstance(rider_data, dict) and 'content' in rider_data:
                                        content = rider_data['content']
                                        
                                        # Handle content as list of strings
                                        if isinstance(content, list):
                                            content_text = '\n'.join(str(item) for item in content if item)
                                        elif isinstance(content, str):
                                            content_text = content
                                        else:
                                            continue
                                        
                                        if content_text and content_text.strip():
                                            # Extract rider clause number from rider_key (e.g., "rider_clause_1" -> "1")
                                            rider_number = rider_key.replace('rider_clause_', '') if rider_key.startswith('rider_clause_') else rider_key
                                            
                                            chunk = {
                                                "vessel_name": vessel_name,
                                                "source_file": source_file,
                                                "chapter": chapter_key,
                                                "sub_chapter": sub_key,
                                                "clause_title": rider_data.get('title', f"Rider Clause {rider_number}"),
                                                "content": content_text.strip(),
                                                "heading": rider_data.get('title', f"Rider Clause {rider_number}"),
                                                "clause_number": rider_number,
                                                "is_rider_clause": True,
                                                "rider_clause_number": rider_number,
                                                "line_count": len(content_text.split('\n')),
                                                "vessel_identifiers": vessel_identifiers,
                                            }
                                            chunks.append(chunk)
                            
                            # Also handle general rider clause content if it exists
                            if 'content' in rider_clause_data:
                                content = rider_clause_data['content']
                                
                                # Handle content as list of strings
                                if isinstance(content, list):
                                    content_text = '\n'.join(str(item) for item in content if item)
                                elif isinstance(content, str):
                                    content_text = content
                                else:
                                    content_text = ""
                                
                                if content_text and content_text.strip():
                                    chunk = {
                                        "vessel_name": vessel_name,
                                        "source_file": source_file,
                                        "chapter": chapter_key,
                                        "sub_chapter": sub_key,
                                        "clause_title": "Rider Clause of Charter Party",
                                        "content": content_text.strip(),
                                        "heading": "Rider Clause of Charter Party - General",
                                        "clause_number": None,
                                        "is_rider_clause": True,
                                        "line_count": len(content_text.split('\n')),
                                        "vessel_identifiers": vessel_identifiers,
                                    }
                                    chunks.append(chunk)
                        
                        # Always handle general sub-chapter content (for fixture_recap, addendum, etc.) in addition to clauses
                        if 'content' in sub_data:
                            content = sub_data['content']
                            
                            # Handle content as list of strings
                            if isinstance(content, list):
                                content_text = '\n'.join(str(item) for item in content if item)
                            elif isinstance(content, str):
                                content_text = content
                            else:
                                continue
                            
                            if content_text and content_text.strip():
                                chunk = {
                                    "vessel_name": vessel_name,
                                    "source_file": source_file,
                                    "chapter": chapter_key,
                                    "sub_chapter": sub_key,
                                    "clause_title": "",
                                    "content": content_text.strip(),
                                    "heading": sub_data.get('title', f"{chapter_key}/{sub_key}".replace('_', ' ').title()),
                                    "clause_number": None,
                                    "line_count": len(content_text.split('\n')),
                                    "vessel_identifiers": vessel_identifiers,
                                }
                                chunks.append(chunk)
            
            # Handle other sub-chapters (fallback for different structures)
            elif not ('content' in chapter_data and chapter_data['content']) and not ('sub_chapters' in chapter_data):
                for sub_key, sub_data in chapter_data.items():
                    if isinstance(sub_data, dict):
                        # Handle sub-chapter with content
                        if 'content' in sub_data:
                            content = sub_data['content']
                            
                            # Handle content as list of strings
                            if isinstance(content, list):
                                content_text = '\n'.join(str(item) for item in content if item)
                            elif isinstance(content, str):
                                content_text = content
                            else:
                                continue
                            
                            if content_text and content_text.strip():
                                chunk = {
                                    "vessel_name": vessel_name,
                                    "source_file": source_file,
                                    "chapter": chapter_key,
                                    "sub_chapter": sub_key,
                                    "clause_title": "",
                                    "content": content_text.strip(),
                                    "heading": sub_data.get('title', f"{chapter_key}/{sub_key}".replace('_', ' ').title()),
                                    "clause_number": None,
                                    "line_count": len(content_text.split('\n')),
                                    "vessel_identifiers": vessel_identifiers,
                                }
                                chunks.append(chunk)
                        
                        # Handle clauses within sub-chapters
                        elif 'clauses' in sub_data:
                            clauses = sub_data['clauses']
                            if isinstance(clauses, dict):
                                for clause_key, clause_data in clauses.items():
                                    if isinstance(clause_data, dict) and 'content' in clause_data:
                                        content = clause_data['content']
                                        
                                        # Handle content as list of strings
                                        if isinstance(content, list):
                                            content_text = '\n'.join(str(item) for item in content if item)
                                        elif isinstance(content, str):
                                            content_text = content
                                        else:
                                            continue
                                        
                                        if content_text and content_text.strip():
                                            chunk = {
                                                "vessel_name": vessel_name,
                                                "source_file": source_file,
                                                "chapter": chapter_key,
                                                "sub_chapter": sub_key,
                                                "clause_title": clause_data.get('clause_title', clause_key),
                                                "content": content_text.strip(),
                                                "heading": clause_data.get('clause_title', clause_key),
                                                "clause_number": clause_data.get('clause_number'),
                                                "line_count": len(content_text.split('\n')),
                                                "vessel_identifiers": vessel_identifiers,
                                            }
                                            chunks.append(chunk)
                    
                    elif isinstance(sub_data, list):
                        # Handle lists of content items
                        for i, item in enumerate(sub_data):
                            if isinstance(item, dict) and 'content' in item:
                                content = item['content']
                                
                                # Handle content as list of strings
                                if isinstance(content, list):
                                    content_text = '\n'.join(str(c) for c in content if c)
                                elif isinstance(content, str):
                                    content_text = content
                                else:
                                    continue
                                
                                if content_text and content_text.strip():
                                    chunk = {
                                        "vessel_name": vessel_name,
                                        "source_file": source_file,
                                        "chapter": chapter_key,
                                        "sub_chapter": sub_key,
                                        "clause_title": item.get('clause_title', ''),
                                        "content": content_text.strip(),
                                        "heading": f"{chapter_key}/{sub_key}/{i+1}".replace('_', ' ').title(),
                                        "clause_number": item.get('clause_number'),
                                        "line_count": len(content_text.split('\n')),
                                        "vessel_identifiers": vessel_identifiers,
                                    }
                                    chunks.append(chunk)
    
    # Fallback: handle old structure if no "chapters" key
    else:
        for chapter_key, chapter_data in json_data.items():
            if not isinstance(chapter_data, dict):
                continue
            
            if 'content' in chapter_data:
                content = chapter_data['content']
                
                # Handle content as list or string
                if isinstance(content, list):
                    content_text = '\n'.join(str(item) for item in content if item)
                elif isinstance(content, str):
                    content_text = content
                else:
                    continue
                
                if content_text and content_text.strip():
                    chunk = {
                        "vessel_name": vessel_name,
                        "source_file": source_file,
                        "chapter": chapter_key,
                        "sub_chapter": "",
                        "clause_title": "",
                        "content": content_text.strip(),
                        "heading": chapter_key.replace('_', ' ').title(),
                        "clause_number": None,
                        "line_count": len(content_text.split('\n')),
                        "vessel_identifiers": vessel_identifiers,
                    }
                    chunks.append(chunk)
    
    return chunks

def deduplicate_chunks(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove duplicate chunks based on content similarity"""
    seen_content = defaultdict(list)
    
    # Group chunks by vessel + content hash
    for chunk in chunks:
        vessel = chunk.get('vessel_name', '')
        content = chunk.get('content', '')
        
        # Create content hash for duplicate detection
        normalized_content = ' '.join(content.lower().split())
        content_hash = hashlib.md5(normalized_content.encode()).hexdigest()[:12]
        
        key = f"{vessel}::{content_hash}"
        seen_content[key].append(chunk)
    
    # Keep only the best chunk from each group
    deduplicated = []
    duplicates_removed = 0
    
    for key, chunk_group in seen_content.items():
        if len(chunk_group) > 1:
            # Multiple chunks with same content - keep the one with most detail
            def sort_key(chunk):
                # Prefer chunks with clause titles
                clause_score = 2 if chunk.get('clause_title') else 0
                # Prefer chunks with sub-chapters
                sub_chapter_score = 1 if chunk.get('sub_chapter') else 0
                # Prefer longer content
                content_score = min(len(chunk.get('content', '')), 1000) / 1000
                
                return (-(clause_score + sub_chapter_score + content_score))
            
            sorted_chunks = sorted(chunk_group, key=sort_key)
            best_chunk = sorted_chunks[0]
            deduplicated.append(best_chunk)
            duplicates_removed += len(chunk_group) - 1
            
            if len(chunk_group) > 1:
                vessel = chunk_group[0].get('vessel_name', 'Unknown')
                print(f"    🔄 Deduplicating: {vessel} ({len(chunk_group)} → 1)")
        else:
            # Unique chunk - keep it
            deduplicated.append(chunk_group[0])
    
    if duplicates_removed > 0:
        print(f"  ✨ Deduplication: Removed {duplicates_removed} duplicates, kept {len(deduplicated)} unique chunks")
    
    return deduplicated

def process_single_vessel(json_file: str, enhancer: ChapterBasedSemanticEnhancer, state: Dict[str, Dict[str, Any]]):
    """Process a single vessel file and upload its embeddings"""
    vessel_name = get_vessel_name_from_file(json_file)
    print(f"\n🚢 Processing vessel: {vessel_name} ({json_file})")
    
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
    except Exception as e:
        print(f"  ❌ Error loading JSON file: {e}")
        return False
    
    # Extract vessel identifiers (IMO, call sign) for embedding disambiguation
    vessel_identifiers = extract_vessel_identifiers(json_data)
    if vessel_identifiers.get("imo_number") or vessel_identifiers.get("call_sign"):
        print(f"  🆔 Extracted identifiers: IMO {vessel_identifiers.get('imo_number', 'N/A')}, Call sign {vessel_identifiers.get('call_sign', 'N/A')}")
    
    # Extract chunks from chapter-based structure
    chunks = extract_chunks_from_chapter_based_json(json_data, vessel_name, os.path.basename(json_file), vessel_identifiers)
    print(f"  📄 Found {len(chunks)} chunks from chapter-based JSON.")
    
    if not chunks:
        print(f"  ⚠️  No valid chunks found for {vessel_name}")
        return False
    
    # Delete existing embeddings for this vessel before uploading new ones
    delete_vessel_embeddings(vessel_name)
    
    # Deduplicate chunks
    chunks = deduplicate_chunks(chunks)
    print(f"  📄 Processing {len(chunks)} unique chunks after deduplication.")
    
    # Apply semantic enhancement
    enhanced_chunks = []
    for chunk in chunks:
        # Enhance content for embedding
        enhanced_content = enhancer.enhance_content_for_embedding(chunk)
        chunk["enhanced_content"] = enhanced_content
        enhanced_chunks.append(chunk)
    
    print(f"  🔧 Enhanced {len(enhanced_chunks)} chunks with semantic boost.")
    
    # Create smart batches
    smart_batches = create_smart_batches(
        enhanced_chunks, 
        max_batch_tokens=7000,
        model=EMBEDDING_MODEL
    )
    print(f"  📦 Created {len(smart_batches)} smart batches based on token limits.")
    
    # Process each smart batch
    for batch_num, batch_chunks in enumerate(smart_batches):
        batch_texts = [chunk["enhanced_content"] for chunk in batch_chunks]
        batch_token_count = count_tokens_in_batch(batch_texts, model=EMBEDDING_MODEL)
        print(f"    🔄 Processing batch {batch_num + 1}/{len(smart_batches)} ({len(batch_chunks)} chunks, {batch_token_count} tokens)")
        
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
                is_rider_clause = chunk.get('is_rider_clause', False)
                rider_clause_number = chunk.get('rider_clause_number')
                
                id_parts = [vessel_name, chapter]
                if sub_chapter:
                    id_parts.append(sub_chapter)
                
                # Handle rider clauses with distinct IDs
                if is_rider_clause and rider_clause_number:
                    id_parts.append(f"rider_clause_{rider_clause_number}")
                # For individual clauses, use clause number for cleaner IDs
                elif clause_number:
                    id_parts.append(f"clause_{clause_number}")
                elif clause_title:
                    id_parts.append(clause_title[:50])  # Limit clause title length
                
                item_id = make_ascii_id('_'.join(id_parts))
                upsert_tuples.append((item_id, embedding, enhanced_metadata))
            
            # Batch upsert to Pinecone
            upsert_to_pinecone_with_retry(upsert_tuples)
            print(f"      ✅ Successfully upserted {len(upsert_tuples)} enhanced items to Pinecone.")
            
        except Exception as e:
            print(f"      ❌ Failed to process batch {batch_num + 1}: {e}")
            continue
    
    # Update state with new hash
    file_hash = calculate_file_hash(json_file)
    state[json_file] = {
        "hash": file_hash,
        "vessel_name": vessel_name,
        "file_path": json_file
    }
    
    return True

def process_and_upsert_chapter_based_vessel_json(force_refresh: bool = False):
    """Process and upsert chapter-based vessel JSON with enhanced structure and change detection"""
    print("🚀 Chapter-Based Vessel Embedding Processing")
    print("=" * 60)
    
    # Initialize enhancer and find files
    enhancer = ChapterBasedSemanticEnhancer()
    json_files = find_vessel_json_files()
    print(f"\n📁 Found {len(json_files)} vessel JSON files.")
    
    # Detect changes
    if force_refresh:
        print("\n🔄 Force refresh mode: Processing all files...")
        changed_files = json_files
        new_files = json_files
        removed_vessels = set()
        files_to_process = json_files
        # Build state from all files
        state = {}
        for json_file in json_files:
            file_hash = calculate_file_hash(json_file)
            vessel_name = get_vessel_name_from_file(json_file)
            state[json_file] = {
                "hash": file_hash,
                "vessel_name": vessel_name,
                "file_path": json_file
            }
    else:
        print("\n🔍 Detecting changes...")
        changed_files, new_files, removed_vessels, state = detect_changes(json_files)
        files_to_process = list(set(changed_files + new_files))
        
        print(f"\n📊 Change Detection Summary:")
        print(f"   • New files: {len(new_files)}")
        print(f"   • Changed files: {len(changed_files)}")
        print(f"   • Removed vessels: {len(removed_vessels)}")
        print(f"   • Files to process: {len(files_to_process)}")
        print(f"   • Files unchanged: {len(json_files) - len(files_to_process)}")
    
    # Handle removed vessels - delete their embeddings
    for vessel_name in removed_vessels:
        delete_vessel_embeddings(vessel_name)
        # Remove from state
        state = {k: v for k, v in state.items() if v.get("vessel_name") != vessel_name}
    
    # Process changed/new files
    if not files_to_process:
        print("\n✅ No changes detected. All files are up to date!")
        print("=" * 60)
        return
    
    print(f"\n🚀 Processing {len(files_to_process)} files...")
    print("=" * 60)
    
    for i, json_file in enumerate(files_to_process):
        # Small delay between vessels to avoid burst rate limits (OpenAI has higher limits than Langdock)
        if i > 0:
            time.sleep(2)  # 2s is sufficient with OpenAI; was 60s for Langdock
        
        # Process the vessel
        process_single_vessel(json_file, enhancer, state)
    
    # Save updated state
    save_state(state)
    
    print(f"\n🎉 Embedding processing complete!")
    if force_refresh:
        print(f"📊 All {len(json_files)} vessel JSON files processed with chapter-based enhancement!")
    else:
        print(f"📊 Processed {len(files_to_process)} changed/new files out of {len(json_files)} total files.")
    print(f"🔍 Pinecone index is ready for enhanced AI search with strikethrough support!")
    print("=" * 60)

if __name__ == "__main__":
    import sys
    
    # Check for force refresh flag
    force_refresh = "--force" in sys.argv or "-f" in sys.argv
    
    if force_refresh:
        print("⚠️  Force refresh mode enabled - all files will be processed")
    
    process_and_upsert_chapter_based_vessel_json(force_refresh=force_refresh)
