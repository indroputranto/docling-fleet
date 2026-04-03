#!/usr/bin/env python3
"""
Optimized Embedding Uploader - Enhanced for AI Search
Automatically applies semantic enhancement during embedding creation for better AI chatbot performance
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

class SemanticEmbeddingEnhancer:
    """Automatically enhances clauses during embedding creation"""
    
    def __init__(self):
        # Vessel specification enhancement templates  
        self.vessel_enhancement_templates = {
            'basic_info': {
                'boost_keywords': ['vessel information', 'vessel name', 'IMO number', 'flag state', 'vessel identification'],
                'context_prefix': 'Vessel Basic Information: Primary identification and registration details including',
                'description_suffix': 'This contains essential vessel identification information including name, IMO, flag, and registration details.'
            },
            'dimensions': {
                'boost_keywords': ['vessel dimensions', 'LOA', 'beam', 'draft', 'vessel size', 'vessel measurements'],
                'context_prefix': 'Vessel Dimensions: Physical measurements and size specifications including',
                'description_suffix': 'This contains detailed vessel dimensions including length, beam, draft, and physical specifications.'
            },
            'capacities': {
                'boost_keywords': ['vessel capacity', 'DWT', 'deadweight', 'cargo capacity', 'tank capacity', 'hold capacity'],
                'context_prefix': 'Vessel Capacities: Cargo and tank capacity specifications including',
                'description_suffix': 'This contains vessel capacity information including deadweight, cargo holds, and tank specifications.'
            },
            'propulsion': {
                'boost_keywords': ['vessel propulsion', 'main engine', 'vessel speed', 'engine power', 'propulsion system'],
                'context_prefix': 'Vessel Propulsion: Engine and propulsion system specifications including',
                'description_suffix': 'This contains vessel propulsion information including engine details and performance specifications.'
            },
            'equipment': {
                'boost_keywords': ['vessel equipment', 'cargo gear', 'cranes', 'deck equipment', 'handling equipment'],
                'context_prefix': 'Vessel Equipment: Cargo handling and deck equipment specifications including',
                'description_suffix': 'This contains vessel equipment information including cranes, cargo gear, and handling equipment.'
            }
        }
        
        self.enhancement_templates = {
            'bunkers': {
                'boost_keywords': ['bunkers clause', 'fuel delivery', 'bunker quantities', 'redelivery bunkers', 'bunker specifications', 'marine fuel'],
                'context_prefix': 'Bunkers Clause: Primary fuel delivery and redelivery requirements including',
                'description_suffix': 'This is the main bunkers clause containing detailed fuel specifications, delivery quantities, and redelivery requirements.'
            },
            'delivery': {
                'boost_keywords': ['delivery clause', 'vessel delivery', 'delivery port', 'delivery condition', 'on-hire procedures'],
                'context_prefix': 'Delivery Clause: Vessel delivery procedures and requirements including',
                'description_suffix': 'This is the primary delivery clause containing vessel delivery location, timing, and conditions.'
            },
            'redelivery': {
                'boost_keywords': ['redelivery clause', 'vessel redelivery', 'redelivery port', 'off-hire procedures', 'redelivery condition'],
                'context_prefix': 'Redelivery Clause: Vessel redelivery procedures and requirements including',
                'description_suffix': 'This is the primary redelivery clause containing vessel redelivery location, timing, and conditions.'
            },
            'hire': {
                'boost_keywords': ['hire clause', 'rate of hire', 'daily hire', 'hire payment', 'hire calculation'],
                'context_prefix': 'Hire Clause: Daily hire rates and payment terms including',
                'description_suffix': 'This is the primary hire clause containing daily hire rates, payment terms, and calculation methods.'
            },
            'performance': {
                'boost_keywords': ['performance clause', 'vessel performance', 'master duties', 'navigation requirements', 'voyage execution'],
                'context_prefix': 'Performance Clause: Vessel performance and operational requirements including',
                'description_suffix': 'This is the primary performance clause containing vessel performance requirements and master responsibilities.'
            },
            'off_hire': {
                'boost_keywords': ['off-hire clause', 'hire suspension', 'breakdown conditions', 'vessel deficiency', 'time lost'],
                'context_prefix': 'Off-Hire Clause: Circumstances for hire suspension including',
                'description_suffix': 'This is the primary off-hire clause containing conditions for hire suspension and time lost procedures.'
            },
            'drydocking': {
                'boost_keywords': ['drydocking clause', 'drydock requirements', 'vessel maintenance', 'docking period', 'drydock survey'],
                'context_prefix': 'Drydocking Clause: Vessel drydocking and maintenance requirements including',
                'description_suffix': 'This is the primary drydocking clause containing vessel maintenance, docking procedures, and scheduling requirements.'
            },
            'ice': {
                'boost_keywords': ['ice clause', 'ice conditions', 'icebound port', 'force ice', 'ice navigation'],
                'context_prefix': 'Ice Clause: Ice navigation restrictions and procedures including',
                'description_suffix': 'This is the primary ice clause containing ice navigation restrictions and winter weather procedures.'
            },
            'laydays': {
                'boost_keywords': ['laydays clause', 'laytime', 'cancelling date', 'notice time', 'charter commencement'],
                'context_prefix': 'Laydays/Cancelling Clause: Charter commencement timing and procedures including',
                'description_suffix': 'This is the primary laydays clause containing charter commencement, cancelling dates, and notice requirements.'
            },
            'cargo_claims': {
                'boost_keywords': ['cargo claims clause', 'cargo damage', 'cargo liability', 'damage claims', 'cargo insurance'],
                'context_prefix': 'Cargo Claims Clause: Cargo damage liability and claims procedures including',
                'description_suffix': 'This is the cargo claims clause containing cargo damage liability allocation and claims procedures.'
            },
            'liens': {
                'boost_keywords': ['liens clause', 'maritime lien', 'security interest', 'lien rights', 'cargo lien'],
                'context_prefix': 'Liens Clause: Maritime liens and security interests including',
                'description_suffix': 'This is the liens clause containing maritime lien rights and security interests in cargo and freight.'
            },
            'requisition': {
                'boost_keywords': ['requisition clause', 'government requisition', 'vessel seizure', 'requisition compensation'],
                'context_prefix': 'Requisition Clause: Government requisition procedures including',
                'description_suffix': 'This is the requisition clause containing government requisition procedures and compensation arrangements.'
            },
            'total_loss': {
                'boost_keywords': ['total loss clause', 'vessel loss', 'constructive total loss', 'loss compensation'],
                'context_prefix': 'Total Loss Clause: Vessel total loss procedures including',
                'description_suffix': 'This is the total loss clause containing total loss procedures and compensation arrangements.'
            },
            'salvage': {
                'boost_keywords': ['salvage clause', 'salvage services', 'general average', 'salvage compensation'],
                'context_prefix': 'Salvage Clause: Salvage operations and procedures including',
                'description_suffix': 'This is the salvage clause containing salvage operations and general average procedures.'
            },
            'pollution': {
                'boost_keywords': ['pollution clause', 'oil pollution', 'environmental compliance', 'MARPOL', 'pollution prevention'],
                'context_prefix': 'Pollution Clause: Environmental compliance and pollution prevention including',
                'description_suffix': 'This is the pollution clause containing pollution prevention and environmental compliance requirements.'
            },
            'taxes': {
                'boost_keywords': ['taxes clause', 'port taxes', 'customs duties', 'local taxes', 'government charges'],
                'context_prefix': 'Taxes Clause: Tax and duty allocation including',
                'description_suffix': 'This is the taxes clause containing tax, duty, and government charge allocation between parties.'
            },
            'liberties': {
                'boost_keywords': ['liberties clause', 'vessel liberties', 'navigation rights', 'routing freedom'],
                'context_prefix': 'Liberties Clause: Vessel navigation liberties including',
                'description_suffix': 'This is the liberties clause containing vessel navigation liberties and routing flexibility.'
            },
            'notices': {
                'boost_keywords': ['notices clause', 'notification requirements', 'communication procedures', 'notice terms'],
                'context_prefix': 'Notices Clause: Communication and notification requirements including',
                'description_suffix': 'This is the notices clause containing notice and communication requirements between parties.'
            },
            'commissions': {
                'boost_keywords': ['commissions clause', 'brokerage fees', 'commission payment', 'broker compensation'],
                'context_prefix': 'Commissions Clause: Commission and brokerage arrangements including',
                'description_suffix': 'This is the commissions clause containing commission and brokerage fee arrangements.'
            },
            'exceptions': {
                'boost_keywords': ['exceptions clause', 'excepted perils', 'force majeure', 'excluded liabilities'],
                'context_prefix': 'Exceptions Clause: Liability exceptions and excepted perils including',
                'description_suffix': 'This is the exceptions clause containing exceptions from liability and excepted perils.'
            },
            'cargo_handling': {
                'boost_keywords': ['cargo handling clause', 'cargo gear', 'loading equipment', 'discharging procedures'],
                'context_prefix': 'Cargo Handling Clause: Cargo handling equipment and procedures including',
                'description_suffix': 'This is the cargo handling clause containing cargo gear, loading, and discharging procedures.'
            },
            'supercargo': {
                'boost_keywords': ['supercargo clause', 'cargo superintendent', 'cargo supervision', 'cargo inspection'],
                'context_prefix': 'Supercargo Clause: Cargo supervision and superintendent duties including',
                'description_suffix': 'This is the supercargo clause containing cargo supervision and superintendent appointment procedures.'
            },
            'stowaways': {
                'boost_keywords': ['stowaways clause', 'stowaway procedures', 'illegal passengers', 'unauthorized persons'],
                'context_prefix': 'Stowaways Clause: Stowaway procedures and cost allocation including',
                'description_suffix': 'This is the stowaways clause containing stowaway procedures and cost allocation arrangements.'
            },
            'smuggling': {
                'boost_keywords': ['smuggling clause', 'contraband prevention', 'illegal cargo', 'customs violations'],
                'context_prefix': 'Smuggling Clause: Smuggling prevention and liability including',
                'description_suffix': 'This is the smuggling clause containing smuggling prevention and liability procedures.'
            },
            'trading_exclusions': {
                'boost_keywords': ['trading exclusions', 'trading limits', 'trading restrictions', 'prohibited ports', 'sanctioned countries', 'excluded territories', 'Iran', 'Cuba', 'North Korea', 'Russia', 'sanctions', 'blacklisted', 'INL/IWL', 'war countries', 'restricted trading'],
                'context_prefix': 'Trading Exclusions: Vessel employment restrictions and prohibited trading areas including',
                'description_suffix': 'This contains detailed trading exclusions, geographical restrictions, prohibited ports, sanctioned territories, and vessel employment limitations.'
            },
            'fixture_recap': {
                'boost_keywords': ['fixture recap', 'charter details', 'delivery terms', 'redelivery terms', 'bunkers clause', 'hire rate', 'trading restrictions'],
                'context_prefix': 'Fixture Recap: Charter party summary and key commercial terms including',
                'description_suffix': 'This fixture recap contains the essential commercial terms, delivery/redelivery arrangements, bunker provisions, and trading restrictions for the charter party.'
            },
            'fixture_recap_complete': {
                'boost_keywords': ['complete fixture recap', 'full fixture recap', 'fixture recap summary', 'charter fixture', 'fixture details', 'charter terms', 'commercial terms'],
                'context_prefix': 'Complete Fixture Recap: Comprehensive charter party terms and conditions including',
                'description_suffix': 'This is the complete fixture recap containing all charter terms, commercial conditions, delivery/redelivery arrangements, bunker provisions, and trading restrictions.'
            },
            'fixture_recap_section': {
                'boost_keywords': ['fixture recap section', 'charter section', 'fixture details', 'charter terms', 'commercial terms'],
                'context_prefix': 'Fixture Recap Section: Specific charter party terms including',
                'description_suffix': 'This is a focused section from the fixture recap containing specific charter terms and conditions.'
            },
            'addendums': {
                'boost_keywords': ['addendum', 'charter amendments', 'additional clauses', 'charter modifications', 'supplementary terms'],
                'context_prefix': 'Charter Addendum: Additional terms and modifications to the charter party including',
                'description_suffix': 'This addendum contains additional terms, modifications, and supplementary clauses that amend or extend the original charter party provisions.'
            }
        }
    
    def should_enhance_content(self, chunk_data: Dict[str, Any]) -> bool:
        """Determine if content should be semantically enhanced"""
        # Check if already enhanced from process_vessel_optimized.py
        if chunk_data.get('enhanced_for_search'):
            return True
        
        # Check if it's a primary clause type
        clause_type = chunk_data.get('clause_type', '')
        if clause_type in self.enhancement_templates:
            return True
        
        # Check if it's tagged as a high-priority clause
        tag = chunk_data.get('tag', '')
        content = chunk_data.get('content', '')
        
        if 'CLAUSE' in tag and any(keyword in content.lower() for keyword in ['bunkers', 'delivery', 'redelivery', 'hire', 'performance']):
            return True
        
        return False
    
    def should_enhance_vessel_spec(self, chunk_data: Dict[str, Any]) -> bool:
        """Determine if vessel specification should be semantically enhanced"""
        spec_type = chunk_data.get('spec_type', '')
        return spec_type in self.vessel_enhancement_templates
    
    def enhance_vessel_spec_for_embedding(self, chunk_data: Dict[str, Any]) -> str:
        """Enhance vessel specification content for better semantic search"""
        original_content = chunk_data.get('content', '')
        spec_type = chunk_data.get('spec_type', 'general')
        vessel_name = chunk_data.get('vessel_name', chunk_data.get('vessel', ''))
        
        if not self.should_enhance_vessel_spec(chunk_data):
            return original_content
        
        # Get enhancement template
        if spec_type in self.vessel_enhancement_templates:
            template = self.vessel_enhancement_templates[spec_type]
        else:
            return original_content
        
        # Create enhanced content with semantic boost
        enhanced_parts = []
        
        # Add context prefix with vessel name
        enhanced_parts.append(f"{template['context_prefix']} for {vessel_name}:")
        
        # Add boost keywords for better semantic matching
        boost_keywords = ' '.join(template['boost_keywords'])
        enhanced_parts.append(f"Keywords: {boost_keywords}")
        
        # Add original content
        enhanced_parts.append(original_content)
        
        # Add description suffix
        enhanced_parts.append(template['description_suffix'])
        
        return '\n\n'.join(enhanced_parts)
    
    def enhance_content_for_embedding(self, chunk_data: Dict[str, Any]) -> str:
        """Enhanced content strategy for consolidated chunks"""
        original_content = chunk_data.get('content', '')
        clause_type = chunk_data.get('clause_type', 'general')
        content_type = chunk_data.get('type', '')
        vessel_name = chunk_data.get('vessel_name', chunk_data.get('vessel', ''))
        is_consolidated = chunk_data.get('is_consolidated', False)
        
        # **SIMPLIFIED ENHANCEMENT**: Only enhance important consolidated chunks
        if not self.should_enhance_consolidated_chunk(chunk_data):
            return original_content
        
        # **TARGETED ENHANCEMENT** for specific content types
        if content_type == 'vessel_specifications':
            return self.enhance_vessel_specifications(original_content, vessel_name)
        elif content_type == 'consolidated_clause':
            return self.enhance_consolidated_clause(original_content, clause_type, vessel_name, is_consolidated)
        elif chunk_data.get('is_special_content'):
            return self.enhance_special_content(original_content, chunk_data, vessel_name)
        
        # Minimal enhancement for other content
        return original_content
    
    def should_enhance_consolidated_chunk(self, chunk_data: Dict[str, Any]) -> bool:
        """Determine if consolidated chunk needs enhancement"""
        content_type = chunk_data.get('type', '')
        clause_type = chunk_data.get('clause_type', '')
        
        # Always enhance vessel specifications
        if content_type == 'vessel_specifications':
            return True
        
        # Always enhance consolidated clauses
        if content_type == 'consolidated_clause':
            return True
            
        # Enhance special content (fixture recap, addendums, trading exclusions)
        if chunk_data.get('is_special_content'):
            return True
        
        # Skip enhancement for simple content
        return False
    
    def enhance_vessel_specifications(self, content: str, vessel_name: str) -> str:
        """Simple enhancement for vessel specifications"""
        enhanced_parts = [
            f"Complete vessel specifications for {vessel_name}:",
            "This contains all technical details including dimensions, capacities, equipment, and propulsion specifications.",
            content
        ]
        return '\n\n'.join(enhanced_parts)
    
    def enhance_consolidated_clause(self, content: str, clause_type: str, vessel_name: str, is_consolidated: bool) -> str:
        """Enhanced content for consolidated clauses"""
        clause_display = clause_type.replace('_', ' ').title()
        
        enhanced_parts = [
            f"Complete {clause_display} clause for {vessel_name}:"
        ]
        
        # Add specific context for important clause types
        if clause_type == 'bunkers':
            enhanced_parts.append("This clause contains all bunker specifications, delivery/redelivery terms, fuel quality requirements, and sampling procedures.")
        elif clause_type in ['cargo_exclusions', 'trading_exclusions']:
            enhanced_parts.append("This clause contains all cargo restrictions, prohibited items, and trading limitations.")
        elif clause_type in ['delivery', 'redelivery']:
            enhanced_parts.append("This clause contains delivery/redelivery procedures, locations, and conditions.")
        else:
            enhanced_parts.append(f"This clause contains all {clause_display.lower()} terms and conditions.")
        
        if is_consolidated:
            enhanced_parts.append("This is the complete consolidated clause with all related provisions.")
        
        enhanced_parts.append(content)
        
        return '\n\n'.join(enhanced_parts)
    
    def enhance_special_content(self, content: str, chunk_data: Dict[str, Any], vessel_name: str) -> str:
        """Enhanced content for special sections"""
        section_tag = chunk_data.get('tag', '')
        clause_type = chunk_data.get('clause_type', '')
        
        enhanced_parts = [
            f"{section_tag} for {vessel_name}:"
        ]
        
        # Add specific context for special sections
        if chunk_data.get('is_trading_exclusions') or clause_type == 'trading_exclusions':
            enhanced_parts.append("This section contains trading exclusions, geographical restrictions, prohibited ports, and sanctioned territories.")
        elif 'cargo' in clause_type.lower():
            enhanced_parts.append("This section contains cargo exclusions and restrictions.")
        elif chunk_data.get('is_fixture_recap'):
            enhanced_parts.append("This section contains key commercial terms and charter conditions.")
        elif chunk_data.get('is_addendum'):
            enhanced_parts.append("This section contains additional terms and modifications to the charter party.")
        
        enhanced_parts.append(content)
        
        return '\n\n'.join(enhanced_parts)
    
    def enhance_metadata(self, chunk_data: Dict[str, Any], enhanced_content: str) -> Dict[str, Any]:
        """Create simplified metadata focused on search essentials"""
        vessel_name = chunk_data.get('vessel_name', chunk_data.get('vessel', ''))
        content_type = chunk_data.get('type', '')
        clause_type = chunk_data.get('clause_type', 'general')
        
        # **SIMPLIFIED METADATA** - Focus on essential search fields
        metadata = {
            # Core identification
            "vessel": vessel_name,
            "heading": chunk_data.get('heading', ''),
            "section": chunk_data.get('section', ''),
            "source_file": chunk_data.get('source_file', ''),
            "content": enhanced_content,
            
            # Content classification
            "content_type": content_type,
            "clause_type": clause_type,
            "tag": chunk_data.get('tag', ''),
            
            # Priority and enhancement flags
            "priority": chunk_data.get('priority', 'medium'),
            "is_enhanced": True,
            "is_consolidated": chunk_data.get('is_consolidated', False)
        }
        
        # **CONTENT-SPECIFIC FLAGS** - Only add what's necessary for search
        if content_type == 'vessel_specifications':
            metadata.update({
                "is_vessel_specifications": True,
                "searchable_content": "vessel specifications technical details dimensions capacities equipment"
            })
        
        elif content_type == 'consolidated_clause':
            metadata.update({
                "is_consolidated_clause": True,
                "is_complete_clause": True
            })
            
            # Add specific flags for critical clause types
            if clause_type == 'bunkers':
                metadata["is_bunkers_clause"] = True
                metadata["searchable_content"] = "bunkers fuel delivery redelivery specifications sampling"
            elif clause_type in ['cargo_exclusions', 'trading_exclusions']:
                metadata["is_exclusions_clause"] = True
                metadata["searchable_content"] = "exclusions restrictions prohibited cargo trading"
            elif clause_type in ['delivery', 'redelivery']:
                metadata["is_delivery_clause"] = True
                metadata["searchable_content"] = "delivery redelivery procedures location conditions"
        
        elif chunk_data.get('is_special_content'):
            metadata.update({
                "is_special_content": True,
                "is_fixture_recap": chunk_data.get('is_fixture_recap', False),
                "is_addendum": chunk_data.get('is_addendum', False),
                "is_trading_exclusions": chunk_data.get('is_trading_exclusions', False)
            })
            
            # Add searchable content for special sections
            if chunk_data.get('is_trading_exclusions') or clause_type == 'trading_exclusions':
                metadata["searchable_content"] = "trading exclusions geographical restrictions prohibited ports sanctions"
            elif 'cargo' in clause_type.lower():
                metadata["searchable_content"] = "cargo exclusions restrictions prohibited items"
            elif chunk_data.get('is_fixture_recap'):
                metadata["searchable_content"] = "fixture recap commercial terms charter conditions"
            elif chunk_data.get('is_addendum'):
                metadata["searchable_content"] = "addendum amendments modifications charter party"
        
        # **PRESERVE IMPORTANT METADATA** from original chunk (filter out None values)
        for key in ['fixture_recap_section', 'parent_section', 'original_chunks_count']:
            if key in chunk_data and chunk_data[key] is not None:
                metadata[key] = chunk_data[key]
        
        # **FILTER OUT ANY REMAINING None VALUES** for Pinecone compatibility
        filtered_metadata = {}
        for key, value in metadata.items():
            if value is not None:
                filtered_metadata[key] = value
        
        return filtered_metadata

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

def create_smart_batches(texts, headings, indices, max_batch_tokens=7000, model="text-embedding-ada-002"):
    """Create smart batches that respect token limits."""
    batches = []
    current_batch_texts = []
    current_batch_headings = []
    current_batch_indices = []
    current_batch_tokens = 0
    enc = tiktoken.encoding_for_model(model)
    
    for text, heading, idx in zip(texts, headings, indices):
        text_tokens = len(enc.encode(str(text)))
        if current_batch_tokens + text_tokens > max_batch_tokens and current_batch_texts:
            batches.append((current_batch_texts, current_batch_headings, current_batch_indices))
            current_batch_texts = []
            current_batch_headings = []
            current_batch_indices = []
            current_batch_tokens = 0
        current_batch_texts.append(text)
        current_batch_headings.append(heading)
        current_batch_indices.append(idx)
        current_batch_tokens += text_tokens
    
    if current_batch_texts:
        batches.append((current_batch_texts, current_batch_headings, current_batch_indices))
    
    return batches

def consolidate_vessel_specifications(items, vessel_name):
    """Consolidate all vessel specification items into a single comprehensive chunk"""
    spec_contents = []
    for item in items:
        if isinstance(item, dict) and item.get('content'):
            if item.get('type') == 'specification':
                spec_contents.append(item.get('content', ''))
    
    if spec_contents:
        consolidated_content = '\n\n'.join(spec_contents)
        return {
            "heading": "vessel_specifications_complete",
            "content": consolidated_content,
            "section": "vessel_details",
            "vessel": vessel_name,
            "vessel_name": vessel_name,
            "type": "vessel_specifications",
            "content_type": "vessel_specifications", 
            "clause_type": "vessel_specifications",
            "priority": "high",
            "enhanced_for_search": True,
            "is_vessel_specification": True,
            "is_consolidated": True,
            "original_chunks_count": len(spec_contents),
            "semantic_keywords": [
                "vessel specifications", "vessel details", "technical specifications",
                "vessel particulars", "ship specifications", "vessel dimensions",
                "vessel capacities", "vessel equipment", "propulsion", "cargo gear"
            ]
        }
    return None

def consolidate_clause_content(items, start_idx, vessel_name):
    """Consolidate all content belonging to the same clause into a single chunk"""
    if start_idx >= len(items):
        return None, start_idx
    
    clause_item = items[start_idx]
    clause_type = clause_item.get('clause_type', 'general')
    clause_tag = clause_item.get('tag', '')
    
    # Collect all content for this clause
    clause_contents = []
    current_idx = start_idx
    
    # Add the initial clause header
    if clause_item.get('content'):
        clause_contents.append(clause_item['content'])
    
    # Continue collecting content until we hit a different clause or section
    current_idx += 1
    while current_idx < len(items):
        item = items[current_idx]
        
        # Stop if we hit a different clause type or major section change
        if (item.get('type') == 'subheader' and 
            item.get('tag', '').startswith('CLAUSE') and 
            item.get('clause_type', '') != clause_type):
            break
        
        # Stop if we hit a non-clause section
        if (item.get('tag') and 
            not item.get('tag', '').startswith('CLAUSE') and
            item.get('tag', '') != clause_tag):
            break
        
        # Add content if it belongs to this clause
        if (item.get('clause_type') == clause_type or 
            item.get('tag') == clause_tag):
            content = item.get('content', '')
            if content and content.strip():
                clause_contents.append(content)
        
        current_idx += 1
    
    # Create consolidated clause chunk
    if clause_contents:
        consolidated_content = convert_strikethrough_to_markdown('\n\n'.join(clause_contents))
        
        consolidated_chunk = {
            "heading": f"contract_details/CLAUSE_{clause_type}",
            "content": consolidated_content,
            "section": "contract_details",
            "vessel": vessel_name,
            "vessel_name": vessel_name,
            "tag": clause_tag,
            "type": "consolidated_clause",
            "content_type": "consolidated_clause",
            "clause_type": clause_type,
            "priority": clause_item.get("priority", "high"),
            "enhanced_for_search": True,
            "is_consolidated": True,
            "original_chunks_count": len(clause_contents),
            "is_primary_clause": True,
            "semantic_keywords": clause_item.get("semantic_keywords", [])
        }
        
        return consolidated_chunk, current_idx
    
    return None, current_idx

def consolidate_special_sections(items, start_idx, vessel_name):
    """Consolidate special sections like ADDENDUMS, FIXTURE_RECAP, etc."""
    if start_idx >= len(items):
        return None, start_idx
    
    section_item = items[start_idx]
    section_tag = section_item.get('tag', '')
    section_type = section_item.get('type', '')
    
    # Collect all content for this special section
    section_contents = []
    current_idx = start_idx
    
    # Add the initial section content
    if section_item.get('content'):
        section_contents.append(section_item['content'])
    
    # For now, keep special sections as single chunks since they're already well-defined
    # This handles ADDENDUMS, FIXTURE_RECAP, etc.
    
    consolidated_content = convert_strikethrough_to_markdown('\n\n'.join(section_contents))
    
    consolidated_chunk = {
        "heading": f"contract_details/{section_tag}",
        "content": consolidated_content,
        "section": "contract_details", 
        "vessel": vessel_name,
        "vessel_name": vessel_name,
        "tag": section_tag,
        "type": section_type,
        "content_type": section_type,
        "clause_type": section_item.get('clause_type', section_type),
        "priority": section_item.get("priority", "medium"),
        "enhanced_for_search": section_item.get("enhanced_for_search", True),
        "is_consolidated": False,  # These are usually already single items
        "is_special_content": True,
        # Preserve special metadata
        "is_fixture_recap": section_item.get("is_fixture_recap", False),
        "is_addendum": section_item.get("is_addendum", False),
        "is_trading_exclusions": section_item.get("is_trading_exclusions", False),
        "fixture_recap_section": section_item.get("fixture_recap_section"),
        "semantic_keywords": section_item.get("semantic_keywords", [])
    }
    
    return consolidated_chunk, current_idx + 1

def convert_strikethrough_to_markdown(text):
    """Convert strikethrough markers to markdown format."""
    text = re.sub(r'\[STRIKETHROUGH\](.*?)\[/STRIKETHROUGH\]', r'~~\1~~', text, flags=re.DOTALL)
    return text

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

def find_vessel_json_files(base_dir="output/vessels"):
    """Find all vessel JSON files."""
    json_files = []
    if not os.path.exists(base_dir):
        print(f"Base directory {base_dir} does not exist.")
        return json_files
    
    for vessel_dir in os.listdir(base_dir):
        vessel_path = os.path.join(base_dir, vessel_dir)
        if os.path.isdir(vessel_path):
            for file in os.listdir(vessel_path):
                if file.endswith('_data.json'):
                    json_files.append(os.path.join(vessel_path, file))
    return json_files

def extract_chunks_from_json_enhanced(json_data, vessel_name, source_file, enhancer):
    """Extract and consolidate chunks from JSON data with intelligent grouping."""
    chunks = []
    
    for key, value in json_data.items():
        if key == "vessel_details" and isinstance(value, dict) and "structured" in value:
            # **CONSOLIDATE VESSEL SPECIFICATIONS** into single comprehensive chunk
            vessel_spec_chunk = consolidate_vessel_specifications(value["structured"], vessel_name)
            if vessel_spec_chunk:
                vessel_spec_chunk["source_file"] = source_file
                chunks.append(vessel_spec_chunk)
                print(f"    ✅ Consolidated vessel specifications: {vessel_spec_chunk['original_chunks_count']} specs → 1 chunk")
        
        elif key == "contract_details" and isinstance(value, dict) and "structured" in value:
            # **CONSOLIDATE CONTRACT CLAUSES** intelligently
            items = value["structured"]
            i = 0
            
            while i < len(items):
                item = items[i]
                
                # Handle CLAUSE subheaders by consolidating all related content
                if item.get("type") == "subheader" and item.get("tag", "").startswith("CLAUSE"):
                    consolidated_clause, next_idx = consolidate_clause_content(items, i, vessel_name)
                    if consolidated_clause:
                        consolidated_clause["source_file"] = source_file
                        chunks.append(consolidated_clause)
                        print(f"    ✅ Consolidated {consolidated_clause['clause_type']} clause: {consolidated_clause['original_chunks_count']} pieces → 1 chunk")
                    i = next_idx
                
                # Handle special sections (ADDENDUMS, FIXTURE_RECAP, etc.)
                elif item.get("tag") and not item.get("tag", "").startswith("CLAUSE"):
                    special_chunk, next_idx = consolidate_special_sections(items, i, vessel_name)
                    if special_chunk:
                        special_chunk["source_file"] = source_file
                        chunks.append(special_chunk)
                        if special_chunk.get("is_trading_exclusions") or special_chunk.get("clause_type") == "trading_exclusions":
                            print(f"    ✅ Added trading exclusions section: {special_chunk['tag']}")
                        elif "cargo" in special_chunk.get("clause_type", "").lower():
                            print(f"    ✅ Added cargo exclusions section: {special_chunk['tag']}")
                        else:
                            print(f"    ✅ Added special section: {special_chunk['tag']}")
                    i = next_idx
                
                else:
                    i += 1
        
        # Handle simple content sections
        elif isinstance(value, dict) and "content" in value:
            chunk_data = {
                "heading": f"{key}",
                "content": convert_strikethrough_to_markdown(value["content"].strip()),
                "section": key,
                "vessel": vessel_name,
                "vessel_name": vessel_name,
                "source_file": source_file,
                "type": "simple_content",
                "clause_type": "general",
                "priority": "low",
                "enhanced_for_search": False,
                "is_consolidated": False
            }
            if chunk_data["content"]:
                chunks.append(chunk_data)
        
        elif isinstance(value, str):
            chunk_data = {
                "heading": f"{key}",
                "content": convert_strikethrough_to_markdown(value.strip()),
                "section": key,
                "vessel": vessel_name,
                "vessel_name": vessel_name,
                "source_file": source_file,
                "type": "simple_text",
                "clause_type": "general",
                "priority": "low",
                "enhanced_for_search": False,
                "is_consolidated": False
            }
            if chunk_data["content"]:
                chunks.append(chunk_data)
    
    return chunks

def create_content_hash(content: str) -> str:
    """Create a hash of the content for duplicate detection"""
    # Normalize content by removing extra whitespace and converting to lowercase
    normalized = ' '.join(content.lower().split())
    return hashlib.md5(normalized.encode()).hexdigest()[:12]

def deduplicate_chunks(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove duplicate chunks with improved logic for consolidated chunks"""
    seen_content = defaultdict(list)
    
    # Group chunks by vessel+content_type+clause_type for better deduplication
    for chunk in chunks:
        vessel = chunk.get('vessel_name', chunk.get('vessel', ''))
        content_type = chunk.get('content_type', '')
        clause_type = chunk.get('clause_type', '')
        is_consolidated = chunk.get('is_consolidated', False)
        
        # Create a more intelligent key for consolidated chunks
        if is_consolidated:
            # For consolidated chunks, use content_type + clause_type
            key = f"{vessel}::{content_type}::{clause_type}::consolidated"
        else:
            # For regular chunks, use heading + content hash
            heading = chunk.get('heading', '')
            content = chunk.get('content', '')
            content_hash = create_content_hash(content)
            key = f"{vessel}::{heading}::{content_hash}::regular"
        
        seen_content[key].append(chunk)
    
    # Keep only the best chunk from each group
    deduplicated = []
    duplicates_removed = 0
    
    for key, chunk_group in seen_content.items():
        if len(chunk_group) > 1:
            # Multiple chunks - keep the best one
            def sort_key(chunk):
                # Prefer consolidated chunks
                consolidated_score = 3 if chunk.get('is_consolidated') else 0
                # Prefer enhanced chunks
                enhanced_score = 2 if chunk.get('enhanced_for_search') else 0
                # Prefer higher priority
                priority_score = {'high': 1, 'medium': 0, 'low': -1}.get(chunk.get('priority'), 0)
                # Prefer longer content (more comprehensive)
                content_score = min(len(chunk.get('content', '')), 1000) / 1000  # Normalize to 0-1
                
                return (-(consolidated_score + enhanced_score + priority_score + content_score))
            
            sorted_chunks = sorted(chunk_group, key=sort_key)
            best_chunk = sorted_chunks[0]
            deduplicated.append(best_chunk)
            duplicates_removed += len(chunk_group) - 1
            
            if len(chunk_group) > 1:
                vessel = chunk_group[0].get('vessel_name', 'Unknown')
                content_type = best_chunk.get('content_type', 'unknown')
                print(f"    🔄 Deduplicating: {vessel} - {content_type} ({len(chunk_group)} → 1)")
        else:
            # Unique chunk - keep it
            deduplicated.append(chunk_group[0])
    
    if duplicates_removed > 0:
        print(f"  ✨ Deduplication: Removed {duplicates_removed} duplicates, kept {len(deduplicated)} unique chunks")
    
    return deduplicated

def process_and_upsert_vessel_json_optimized():
    """Process and upsert vessel JSON with automatic semantic enhancement."""
    enhancer = SemanticEmbeddingEnhancer()
    json_files = find_vessel_json_files()
    print(f"🚀 Found {len(json_files)} vessel JSON files for optimized processing.")
    
    for i, json_file in enumerate(json_files):
        vessel_name = os.path.basename(os.path.dirname(json_file))
        print(f"\n🚢 Processing vessel: {vessel_name} ({json_file})")
        
        # Add delay between vessels (except first one)
        if i > 0:
            print("  ⏱️  Waiting 60 seconds before processing next vessel...")
            time.sleep(60)
        
        with open(json_file, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        # Step 1: Extract chunks with enhanced data
        chunks = extract_chunks_from_json_enhanced(json_data, vessel_name, os.path.basename(json_file), enhancer)
        print(f"  📄 Found {len(chunks)} chunks from JSON.")
        
        if not chunks:
            print(f"  ⚠️  No valid chunks found for {vessel_name}")
            continue
        
        # Step 2: Deduplicate chunks before processing
        chunks = deduplicate_chunks(chunks)
        print(f"  📄 Processing {len(chunks)} unique chunks after deduplication.")
        
        # Step 3: Apply semantic enhancement to content and create enhanced texts
        enhanced_chunks = []
        enhanced_texts = []
        chunk_headings = []
        
        for chunk in chunks:
            # Enhance content for embedding
            enhanced_content = enhancer.enhance_content_for_embedding(chunk)
            enhanced_texts.append(enhanced_content)
            chunk_headings.append(chunk["heading"])
            
            # Store enhanced chunk data
            enhanced_chunk = chunk.copy()
            enhanced_chunk["enhanced_content"] = enhanced_content
            enhanced_chunks.append(enhanced_chunk)
        
        print(f"  🔧 Enhanced {len(enhanced_texts)} chunks with semantic boost.")
        
        chunk_indices = list(range(len(enhanced_chunks)))
        
        # Step 4: Create smart batches that respect token limits
        smart_batches = create_smart_batches(
            enhanced_texts, 
            chunk_headings, 
            chunk_indices, 
            max_batch_tokens=7000,
            model=EMBEDDING_MODEL
        )
        print(f"  📦 Created {len(smart_batches)} smart batches based on token limits.")
        
        # Step 5: Process each smart batch
        for batch_num, (batch_texts, batch_headings, batch_indices) in enumerate(smart_batches):
            batch_token_count = count_tokens_in_batch(batch_texts, model=EMBEDDING_MODEL)
            print(f"    🔄 Processing batch {batch_num + 1}/{len(smart_batches)} ({len(batch_texts)} chunks, {batch_token_count} tokens)")
            
            # Generate embeddings for this batch
            try:
                batch_embeddings = generate_embeddings_with_retry(batch_texts)
                
                # Step 6: Prepare batch upsert data with enhanced metadata
                upsert_tuples = []
                for idx, embedding, heading in zip(batch_indices, batch_embeddings, batch_headings):
                    chunk = enhanced_chunks[idx]
                    enhanced_content = chunk["enhanced_content"]
                    
                    # Create enhanced metadata
                    enhanced_metadata = enhancer.enhance_metadata(chunk, enhanced_content)
                    
                    item_id = make_ascii_id(f"{vessel_name}_{heading}_{idx}")
                    upsert_tuples.append((item_id, embedding, enhanced_metadata))
                
                # Step 7: Batch upsert to Pinecone
                upsert_to_pinecone_with_retry(upsert_tuples)
                print(f"      ✅ Successfully upserted {len(upsert_tuples)} enhanced items to Pinecone.")
                
            except Exception as e:
                print(f"      ❌ Failed to process batch {batch_num + 1}: {e}")
                continue
    
    print(f"\n🎉 All vessel JSON files processed with semantic enhancement!")
    print(f"🔍 Ready for optimized AI search across all vessels!")

if __name__ == "__main__":
    process_and_upsert_vessel_json_optimized() 