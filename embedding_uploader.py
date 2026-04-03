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
PINECONE_API_KEY = "pcsk_3Q5vmu_E7oUn86efDCN2LoKoWnmrAzogTfjNQMXfCe82CR6RQTjRX1JAa1DspBirEKEC5v"
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
            'fixture_recap': {
                'boost_keywords': ['fixture recap', 'charter details', 'delivery terms', 'redelivery terms', 'bunkers clause', 'hire rate', 'trading restrictions'],
                'context_prefix': 'Fixture Recap: Charter party summary and key commercial terms including',
                'description_suffix': 'This fixture recap contains the essential commercial terms, delivery/redelivery arrangements, bunker provisions, and trading restrictions for the charter party.'
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
        """Enhance content for better semantic search"""
        # Check if this is a vessel specification
        if chunk_data.get('type') == 'vessel_spec':
            return self.enhance_vessel_spec_for_embedding(chunk_data)
        
        original_content = chunk_data.get('content', '')
        clause_type = chunk_data.get('clause_type', 'general')
        content_type = chunk_data.get('type', '')
        vessel_name = chunk_data.get('vessel_name', chunk_data.get('vessel', ''))
        
        if not self.should_enhance_content(chunk_data):
            return original_content
        
        # Check for special content types first (fixture_recap, addendums)
        if content_type in self.enhancement_templates:
            template = self.enhancement_templates[content_type]
        # Then check for clause types
        elif clause_type in self.enhancement_templates:
            template = self.enhancement_templates[clause_type]
        else:
            # Fallback enhancement for other important clauses
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
    
    def enhance_metadata(self, chunk_data: Dict[str, Any], enhanced_content: str) -> Dict[str, Any]:
        """Create enhanced metadata for better search results"""
        # Handle vessel specifications
        if chunk_data.get('type') == 'vessel_spec':
            spec_type = chunk_data.get('spec_type', 'general')
            vessel_name = chunk_data.get('vessel_name', chunk_data.get('vessel', ''))
            
            enhanced_metadata = {
                # Basic information
                "vessel": vessel_name,
                "heading": f"vessel_details/{spec_type}",
                "chunk_index": chunk_data.get('chunk_index', 0),
                "section": "vessel_details",
                "source_file": chunk_data.get('source_file', ''),
                "content": enhanced_content,
                
                # Vessel spec classification
                "content_type": "vessel_spec",
                "spec_type": spec_type,
                "vessel_spec_category": spec_type,
                
                # Priority enhancement
                "priority": chunk_data.get('priority', 'medium'),
                "priority_weight": 5 if chunk_data.get('priority') == 'high' else (3 if chunk_data.get('priority') == 'medium' else 1),
                "is_enhanced": True,
                "enhanced_for_search": True,
                
                # Vessel spec flags
                "is_vessel_spec": True,
                "is_primary_spec": chunk_data.get('is_primary_spec', False),
            }
            
            # Add specific flags for important spec types
            if spec_type == 'basic_info':
                enhanced_metadata.update({
                    'is_vessel_basic_info': True,
                    'vessel_info_type': 'primary'
                })
            elif spec_type == 'dimensions':
                enhanced_metadata.update({
                    'is_vessel_dimensions': True,
                    'vessel_dimensions_type': 'primary'
                })
            elif spec_type == 'capacities':
                enhanced_metadata.update({
                    'is_vessel_capacities': True,
                    'vessel_capacities_type': 'primary'
                })
            
            # Add semantic keywords from the enhancement templates
            if spec_type in self.vessel_enhancement_templates:
                template = self.vessel_enhancement_templates[spec_type]
                enhanced_metadata['semantic_keywords'] = template['boost_keywords']
                enhanced_metadata['searchable_terms'] = template['boost_keywords']
            
            return enhanced_metadata
        
        # Handle special content types (fixture_recap, addendums)
        content_type = chunk_data.get('type', '')
        if content_type in ['fixture_recap', 'addendums']:
            vessel_name = chunk_data.get('vessel_name', chunk_data.get('vessel', ''))
            
            enhanced_metadata = {
                # Basic information
                "vessel": vessel_name,
                "heading": chunk_data.get('heading', f"{content_type}"),
                "chunk_index": chunk_data.get('chunk_index', 0),
                "section": content_type,
                "source_file": chunk_data.get('source_file', ''),
                "content": enhanced_content,
                
                # Special content classification
                "content_type": content_type,
                "tag": chunk_data.get('tag', content_type.upper()),
                "clause_category": content_type,
                "clause_type": content_type,
                
                # Priority enhancement (medium priority as requested)
                "priority": chunk_data.get('priority', 'medium'),
                "priority_weight": 3,  # Medium priority
                "is_enhanced": True,
                "enhanced_for_search": True,
                
                # Special content flags
                "is_clause": False,
                "is_special_content": True,
                "is_commercial_terms": content_type == 'fixture_recap',
                "is_addendum": content_type == 'addendums',
            }
            
            # Add specific flags for content types
            if content_type == 'fixture_recap':
                enhanced_metadata.update({
                    'is_fixture_recap': True,
                    'fixture_recap_type': 'primary',
                    'contains_commercial_terms': True
                })
            elif content_type == 'addendums':
                enhanced_metadata.update({
                    'is_addendum': True,
                    'addendum_type': 'primary',
                    'contains_amendments': True
                })
            
            # Add semantic keywords from the enhancement templates
            if content_type in self.enhancement_templates:
                template = self.enhancement_templates[content_type]
                enhanced_metadata['semantic_keywords'] = template['boost_keywords']
                enhanced_metadata['searchable_terms'] = template['boost_keywords']
            
            return enhanced_metadata
        
        # Handle contract clauses (existing logic)
        clause_type = chunk_data.get('clause_type', 'general')
        vessel_name = chunk_data.get('vessel_name', chunk_data.get('vessel', ''))
        
        enhanced_metadata = {
            # Basic information
            "vessel": vessel_name,
            "heading": chunk_data.get('heading', ''),
            "chunk_index": chunk_data.get('chunk_index', 0),
            "section": chunk_data.get('section', ''),
            "source_file": chunk_data.get('source_file', ''),
            "content": enhanced_content,
            
            # Enhanced classification
            "content_type": "clause" if chunk_data.get('tag', '').startswith('CLAUSE') else "text",
            "tag": chunk_data.get('tag', ''),
            "clause_category": clause_type,
            "clause_type": clause_type,
            
                         # Priority enhancement
             "priority": chunk_data.get('priority', 'medium'),
             "priority_weight": 5 if chunk_data.get('priority') == 'high' else (3 if chunk_data.get('priority') == 'medium' else 1),
            "is_enhanced": True,
            "enhanced_for_search": True,
            
            # Specific clause flags
            "is_clause": True if chunk_data.get('tag', '').startswith('CLAUSE') else False,
            "is_primary_clause": chunk_data.get('is_primary_clause', False),
        }
        
        # Add specific flags for important clause types
        if clause_type == 'bunkers':
            enhanced_metadata.update({
                'is_primary_bunkers_clause': True,
                'bunkers_clause_type': 'primary',
                'semantic_boost': True
            })
        elif clause_type == 'delivery':
            enhanced_metadata.update({
                'is_primary_delivery_clause': True,
                'delivery_clause_type': 'primary'
            })
        elif clause_type == 'redelivery':
            enhanced_metadata.update({
                'is_primary_redelivery_clause': True,
                'redelivery_clause_type': 'primary'
            })
        elif clause_type == 'hire':
            enhanced_metadata.update({
                'is_primary_hire_clause': True,
                'hire_clause_type': 'primary'
            })
        elif clause_type == 'performance':
            enhanced_metadata.update({
                'is_primary_performance_clause': True,
                'performance_clause_type': 'primary'
            })
        elif clause_type == 'off_hire':
            enhanced_metadata.update({
                'is_primary_off_hire_clause': True,
                'off_hire_clause_type': 'primary'
            })
        elif clause_type == 'drydocking':
            enhanced_metadata.update({
                'is_primary_drydocking_clause': True,
                'drydocking_clause_type': 'primary',
                'semantic_boost': True
            })
        elif clause_type == 'ice':
            enhanced_metadata.update({
                'is_primary_ice_clause': True,
                'ice_clause_type': 'primary',
                'semantic_boost': True
            })
        elif clause_type == 'laydays':
            enhanced_metadata.update({
                'is_primary_laydays_clause': True,
                'laydays_clause_type': 'primary'
            })
        elif clause_type == 'cargo_claims':
            enhanced_metadata.update({
                'is_cargo_claims_clause': True,
                'cargo_claims_clause_type': 'primary'
            })
        elif clause_type == 'liens':
            enhanced_metadata.update({
                'is_liens_clause': True,
                'liens_clause_type': 'primary'
            })
        elif clause_type == 'requisition':
            enhanced_metadata.update({
                'is_requisition_clause': True,
                'requisition_clause_type': 'primary'
            })
        elif clause_type == 'total_loss':
            enhanced_metadata.update({
                'is_total_loss_clause': True,
                'total_loss_clause_type': 'primary'
            })
        elif clause_type == 'salvage':
            enhanced_metadata.update({
                'is_salvage_clause': True,
                'salvage_clause_type': 'primary'
            })
        elif clause_type == 'pollution':
            enhanced_metadata.update({
                'is_pollution_clause': True,
                'pollution_clause_type': 'primary'
            })
        elif clause_type == 'taxes':
            enhanced_metadata.update({
                'is_taxes_clause': True,
                'taxes_clause_type': 'primary'
            })
        elif clause_type == 'liberties':
            enhanced_metadata.update({
                'is_liberties_clause': True,
                'liberties_clause_type': 'primary'
            })
        elif clause_type == 'notices':
            enhanced_metadata.update({
                'is_notices_clause': True,
                'notices_clause_type': 'primary'
            })
        elif clause_type == 'commissions':
            enhanced_metadata.update({
                'is_commissions_clause': True,
                'commissions_clause_type': 'primary'
            })
        elif clause_type == 'exceptions':
            enhanced_metadata.update({
                'is_exceptions_clause': True,
                'exceptions_clause_type': 'primary'
            })
        elif clause_type == 'cargo_handling':
            enhanced_metadata.update({
                'is_cargo_handling_clause': True,
                'cargo_handling_clause_type': 'primary'
            })
        elif clause_type == 'supercargo':
            enhanced_metadata.update({
                'is_supercargo_clause': True,
                'supercargo_clause_type': 'primary'
            })
        elif clause_type == 'stowaways':
            enhanced_metadata.update({
                'is_stowaways_clause': True,
                'stowaways_clause_type': 'primary'
            })
        elif clause_type == 'smuggling':
            enhanced_metadata.update({
                'is_smuggling_clause': True,
                'smuggling_clause_type': 'primary'
            })
        
        # Add semantic keywords from the enhancement templates
        if clause_type in self.enhancement_templates:
            template = self.enhancement_templates[clause_type]
            enhanced_metadata['semantic_keywords'] = template['boost_keywords']
            enhanced_metadata['searchable_terms'] = template['boost_keywords']
        
        return enhanced_metadata

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
    """Extract and enhance chunks from JSON data with semantic boost."""
    chunks = []
    for key, value in json_data.items():
        if key == "vessel_details" and isinstance(value, dict) and "structured" in value:
            # Handle structured vessel details
            items = value["structured"]
            for i, item in enumerate(items):
                if isinstance(item, dict) and item.get('content'):
                    chunk_data = {
                        "heading": f"vessel_details/{item.get('spec_type', 'general')}",
                        "content": item.get("content", ""),
                        "section": key,
                        "vessel": vessel_name,
                        "vessel_name": vessel_name,
                        "source_file": source_file,
                        "type": "vessel_spec",
                        "spec_type": item.get("spec_type", "general"),
                        "priority": item.get("priority", "medium"),
                        "enhanced_for_search": item.get("enhanced_for_search", False),
                        "is_primary_spec": item.get("is_primary_spec", False),
                        "semantic_keywords": item.get("semantic_keywords", [])
                    }
                    chunks.append(chunk_data)
        elif key == "contract_details" and isinstance(value, dict) and "structured" in value:
            items = value["structured"]
            i = 0
            while i < len(items):
                item = items[i]
                # Group CLAUSE subheader + all following items until next subheader or special tag
                if item.get("type") == "subheader" and (item.get("tag", "").startswith("CLAUSE")):
                    heading = item["content"]
                    content_lines = [heading]
                    j = i + 1
                    while j < len(items):
                        next_item = items[j]
                        # Stop at next subheader (CLAUSE) or non-CLAUSE tag
                        if (next_item.get("type") == "subheader" and next_item.get("tag", "").startswith("CLAUSE")) or \
                           (next_item.get("tag") and not next_item.get("tag", "").startswith("CLAUSE")):
                            break
                        # Add any non-empty content
                        content = next_item.get("content", "")
                        if content and content.strip():
                            content_lines.append(content)
                        j += 1
                    
                    # Create chunk data with enhanced information
                    chunk_data = {
                        "heading": f"contract_details/CLAUSE_{heading}",
                        "content": convert_strikethrough_to_markdown('\n'.join(content_lines).strip()),
                        "section": key,
                        "vessel": vessel_name,
                        "vessel_name": vessel_name,
                        "source_file": source_file,
                        "tag": item.get("tag"),
                        "clause_type": item.get("clause_type", "general"),
                        "priority": item.get("priority", "medium"),
                        "enhanced_for_search": item.get("enhanced_for_search", False),
                        "is_primary_clause": item.get("is_primary_clause", False),
                        "semantic_keywords": item.get("semantic_keywords", [])
                    }
                    
                    chunks.append(chunk_data)
                    i = j
                # Add special tagged sections (ADDENDUMS, FIXTURE RECAP, etc.) as their own chunks
                elif item.get("tag") and not item.get("tag", "").startswith("CLAUSE"):
                    chunk_data = {
                        "heading": f"contract_details/{item.get('tag')}_{i+1}",
                        "content": convert_strikethrough_to_markdown(item.get("content", "").strip()),
                        "section": key,
                        "vessel": vessel_name,
                        "vessel_name": vessel_name,
                        "source_file": source_file,
                        "tag": item.get("tag"),
                        "type": item.get("type", "special_section"),  # Preserve original type
                        "clause_type": item.get("type", "special_section"),  # For backward compatibility
                        "priority": item.get("priority", "medium"),  # Preserve original priority
                        "enhanced_for_search": item.get("enhanced_for_search", True),  # Preserve enhanced flag
                        "is_special_content": True,
                        "is_fixture_recap": item.get("tag") == "FIXTURE_RECAP",
                        "is_addendum": item.get("tag") == "ADDENDUMS"
                    }
                    chunks.append(chunk_data)
                    i += 1
                else:
                    i += 1
        elif isinstance(value, dict) and "content" in value:
            chunk_data = {
                "heading": f"{key}",
                "content": convert_strikethrough_to_markdown(value["content"].strip()),
                "section": key,
                "vessel": vessel_name,
                "vessel_name": vessel_name,
                "source_file": source_file,
                "clause_type": "general",
                "priority": "low",
                "enhanced_for_search": False
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
                "clause_type": "general",
                "priority": "low",
                "enhanced_for_search": False
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
    """Remove duplicate chunks before embedding"""
    seen_content = defaultdict(list)
    
    # Group chunks by vessel+heading+content_hash
    for chunk in chunks:
        vessel = chunk.get('vessel_name', chunk.get('vessel', ''))
        heading = chunk.get('heading', '')
        content = chunk.get('content', '')
        
        # Create a unique key for this chunk
        content_hash = create_content_hash(content)
        key = f"{vessel}::{heading}::{content_hash}"
        
        seen_content[key].append(chunk)
    
    # Keep only the best chunk from each group
    deduplicated = []
    duplicates_removed = 0
    
    for key, chunk_group in seen_content.items():
        if len(chunk_group) > 1:
            # Multiple chunks with same vessel+heading+content - keep the best one
            def sort_key(chunk):
                enhanced_score = 2 if chunk.get('enhanced_for_search') else 0
                priority_score = {'high': 1, 'medium': 0, 'low': -1}.get(chunk.get('priority'), 0)
                return (-enhanced_score, -priority_score)
            
            sorted_chunks = sorted(chunk_group, key=sort_key)
            best_chunk = sorted_chunks[0]
            deduplicated.append(best_chunk)
            duplicates_removed += len(chunk_group) - 1
            
            if len(chunk_group) > 1:
                vessel, heading = key.split('::', 2)[:2]
                print(f"    🔄 Deduplicating: {vessel} - {heading} ({len(chunk_group)} → 1)")
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