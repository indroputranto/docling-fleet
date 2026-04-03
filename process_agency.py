import os
import yaml
import json
import csv
import pandas as pd
from typing import Dict, Any, List
from pathlib import Path
from datetime import datetime
import re
import requests
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class AgencyDataExtractor:
    def __init__(self, source_dir: str = 'source/agencies'):
        self.source_dir = source_dir
        self.schema_path = 'agency_schema.yaml'
        self.schema = self._load_schema()
        # Load existing attachment ID mappings
        self.attachment_mappings = self.load_attachment_mappings()
        
    def _load_schema(self) -> Dict:
        """Load the YAML schema file."""
        with open(self.schema_path, 'r') as f:
            return yaml.safe_load(f)
    
    def _normalize_filename(self, filename: str) -> str:
        """Normalize filename for consistent mapping and upload (lowercase, underscores)."""
        return filename.strip().replace(' ', '_').lower()

    def load_attachment_mappings(self) -> Dict[str, str]:
        """Load existing attachment ID mappings from JSON file."""
        mapping_file = 'agency_attachment_mappings.json'
        if os.path.exists(mapping_file):
            try:
                with open(mapping_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logging.warning(f"Could not load attachment mappings: {e}")
        return {}

    def save_attachment_mappings(self):
        """Save current attachment ID mappings to JSON file."""
        mapping_file = 'agency_attachment_mappings.json'
        try:
            with open(mapping_file, 'w') as f:
                json.dump(self.attachment_mappings, f, indent=2)
            logging.info(f"Saved attachment mappings to {os.path.abspath(mapping_file)}")
            logging.info(f"[DEBUG] Mapping file written: {os.path.abspath(mapping_file)}")
        except Exception as e:
            logging.error(f"Could not save attachment mappings: {e}")
            logging.error(f"[ERROR] Could not save attachment mappings: {e}")

    def upload_or_update_to_langdock(self, file_path: str, folder_id: str, api_key: str) -> bool:
        """Upload or update a file in Langdock Knowledge Folder using attachmentId if available."""
        filename = os.path.basename(file_path)
        norm_filename = self._normalize_filename(filename)
        attachment_id = self.attachment_mappings.get(norm_filename)
        
        try:
            headers = {'Authorization': f'Bearer {api_key}'}
            with open(file_path, 'rb') as f:
                files = {'file': (filename, f, 'application/octet-stream')}
                
                if attachment_id:
                    # Update existing file
                    url = f"https://api.langdock.com/knowledge/{folder_id}"
                    data = {'attachmentId': attachment_id}
                    response = requests.patch(url, headers=headers, files=files, data=data)
                    action = 'updated'
                else:
                    # Upload new file
                    url = f"https://api.langdock.com/knowledge/{folder_id}"
                    response = requests.post(url, headers=headers, files=files)
                    action = 'uploaded'
                
            if response.status_code == 200:
                logging.info(f"Successfully {action} {file_path} to Langdock Knowledge Folder")
                # If this was a new upload, try to extract attachmentId from response
                mapping_updated = False
                if not attachment_id and action == 'uploaded':
                    try:
                        response_data = response.json()
                        new_attachment_id = None
                        
                        # Check different possible response formats
                        if 'attachmentId' in response_data:
                            new_attachment_id = response_data['attachmentId']
                        elif 'result' in response_data and 'id' in response_data['result']:
                            new_attachment_id = response_data['result']['id']
                        elif 'id' in response_data:
                            new_attachment_id = response_data['id']
                        
                        if new_attachment_id:
                            self.attachment_mappings[norm_filename] = new_attachment_id
                            logging.info(f"Stored new attachmentId for {norm_filename}: {new_attachment_id}")
                            mapping_updated = True
                        else:
                            logging.warning(f"No attachmentId/id found in response for {norm_filename}")
                            logging.info(f"[DEBUG] Full API response for {norm_filename}: {response_data}")
                    except Exception as e:
                        logging.warning(f"Could not extract attachmentId from response for {norm_filename}: {e}")
                        logging.error(f"[ERROR] Could not extract attachmentId from response for {norm_filename}: {e}")
                # Always save mapping after upload/update
                try:
                    self.save_attachment_mappings()
                except Exception as e:
                    logging.error(f"Failed to save attachment mappings after {action} for {norm_filename}: {e}")
                    logging.error(f"[ERROR] Failed to save attachment mappings after {action} for {norm_filename}: {e}")
                # Log mapping for debugging
                logging.info(f"[DEBUG] Current attachment_mappings: {json.dumps(self.attachment_mappings, indent=2)}")
                if not mapping_updated and action == 'uploaded':
                    logging.warning(f"[WARNING] Mapping was not updated for {norm_filename} after upload!")
                return True
            else:
                logging.error(f"Failed to {action} {file_path}. Status code: {response.status_code}, Response: {response.text}")
                logging.error(f"[ERROR] Failed to {action} {file_path}. Status code: {response.status_code}, Response: {response.text}")
                return False
                
        except Exception as e:
            logging.error(f"Error uploading/updating {file_path} to Langdock: {str(e)}")
            logging.error(f"[ERROR] Exception during upload/update: {e}")
            return False
    
    def _clean_value(self, value: Any) -> str:
        """Clean a value by converting to string and stripping whitespace."""
        if pd.isna(value):
            return ""
        return str(value).strip()
    
    def _extract_agency_name(self, excel_path: str) -> str:
        """Extract agency name from the Excel file path or content."""
        # Try to extract from filename first
        filename = os.path.basename(excel_path)
        if 'Ocean7' in filename:
            return 'Ocean7'
        elif 'Agency' in filename:
            return 'Agency_List'
        else:
            # Default name based on filename without extension
            return os.path.splitext(filename)[0].replace(' ', '_')
    
    def _process_agents_section(self, df: pd.DataFrame, start_row: int, end_row: int) -> List[Dict]:
        """Process the agents section of the Excel file."""
        agents = []
        
        # Get the header row
        header_row = df.iloc[start_row]
        headers = {i: self._clean_value(col) for i, col in enumerate(header_row)}
        logging.info(f"[DEBUG] Agents section headers: {headers}")
        logging.info(f"[DEBUG] DataFrame shape: {df.shape}, Columns available: {df.shape[1]}")
        
        # Process each row
        for idx in range(start_row + 1, end_row):
            if idx >= len(df):  # Safety check
                break
                
            row = df.iloc[idx]
            # Skip empty rows - check first two columns only to be safe
            if len(row) >= 2 and pd.isna(row[0]) and pd.isna(row[1]):
                continue
            
            # Safely extract values with bounds checking
            def safe_get(row, col_idx, default=""):
                try:
                    if col_idx < len(row):
                        return self._clean_value(row[col_idx])
                    return default
                except:
                    return default
                
            agent = {
                'country': safe_get(row, 0),  # Changed from 1 to 0
                'port': safe_get(row, 1),     # Changed from 2 to 1
                'agent_name': safe_get(row, 2),  # Changed from 3 to 2
                'status': safe_get(row, 3),    # Changed from 4 to 3
                'sanctioned': safe_get(row, 4),  # Changed from 5 to 4
                'contact': {
                    'email': safe_get(row, 5),   # Changed from 6 to 5
                    'phone': safe_get(row, 6),   # Changed from 7 to 6
                    'port3': safe_get(row, 7)    # Changed from 8 to 7
                },
                'contact_person': {
                    'name': safe_get(row, 8)     # Changed from 9 to 8
                },
                'remarks': safe_get(row, 9)      # Changed from 10 to 9
            }
            agents.append(agent)
        
        logging.info(f"[DEBUG] Processed {len(agents)} agents")
        return agents
    
    def _process_port_captains_section(self, df: pd.DataFrame, start_row: int, end_row: int) -> List[Dict]:
        """Process the port captains section of the Excel file."""
        port_captains = []
        
        # Get the header row
        header_row = df.iloc[start_row]
        headers = {i: self._clean_value(col) for i, col in enumerate(header_row)}
        logging.info(f"[DEBUG] Port Captains section headers: {headers}")
        
        # Process each row
        for idx in range(start_row + 1, end_row):
            if idx >= len(df):  # Safety check
                break
                
            row = df.iloc[idx]
            # Skip empty rows - check first two columns only to be safe
            if len(row) >= 2 and pd.isna(row[0]) and pd.isna(row[1]):
                continue
            
            # Safely extract values with bounds checking
            def safe_get(row, col_idx, default=""):
                try:
                    if col_idx < len(row):
                        return self._clean_value(row[col_idx])
                    return default
                except:
                    return default
                
            port_captain = {
                'country_resident': safe_get(row, 0),  # Column A
                'company_name': safe_get(row, 1),      # Column B
                'status': safe_get(row, 2),            # Column C
                'contact': {
                    'name': safe_get(row, 3),          # Column D
                    'phone': safe_get(row, 4),         # Column E
                    'email': safe_get(row, 5)          # Column F
                },
                'regions': safe_get(row, 6),           # Column G
                'remarks': safe_get(row, 7)            # Column H
            }
            port_captains.append(port_captain)
        
        logging.info(f"[DEBUG] Processed {len(port_captains)} port captains")
        return port_captains
    
    def _process_suppliers_section(self, df: pd.DataFrame, start_row: int, end_row: int) -> List[Dict]:
        """Process the suppliers section of the Excel file."""
        suppliers = []
        
        # Get the header row
        header_row = df.iloc[start_row]
        headers = {i: self._clean_value(col) for i, col in enumerate(header_row)}
        logging.info(f"[DEBUG] Suppliers section headers: {headers}")
        
        # Process each row
        for idx in range(start_row + 1, end_row):
            if idx >= len(df):  # Safety check
                break
                
            row = df.iloc[idx]
            # Skip empty rows - check first two columns only to be safe
            if len(row) >= 2 and pd.isna(row[0]) and pd.isna(row[1]):
                continue
            
            # Safely extract values with bounds checking
            def safe_get(row, col_idx, default=""):
                try:
                    if col_idx < len(row):
                        return self._clean_value(row[col_idx])
                    return default
                except:
                    return default
                
            supplier = {
                'country': safe_get(row, 0),  # Changed from 1 to 0
                'services': {
                    'lashing_lifting': safe_get(row, 1).upper() == 'YES',  # Changed from 2 to 1
                    'manpower': safe_get(row, 2).upper() == 'YES',         # Changed from 3 to 2
                    'other': safe_get(row, 3)                              # Changed from 4 to 3
                },
                'company_name': safe_get(row, 4),  # Changed from 5 to 4
                'contact': {
                    'email': safe_get(row, 5),     # Changed from 6 to 5
                    'phone': safe_get(row, 6)      # Changed from 7 to 6
                },
                'contact_person': {
                    'primary': safe_get(row, 7),   # Changed from 8 to 7
                    'secondary': safe_get(row, 8)  # Changed from 9 to 8
                },
                'remarks': safe_get(row, 9)        # Changed from 10 to 9
            }
            suppliers.append(supplier)
        
        logging.info(f"[DEBUG] Processed {len(suppliers)} suppliers")
        return suppliers
    
    def process_excel(self, excel_path: str) -> Dict[str, List[Dict]]:
        """Process the Excel file and return structured data."""
        try:
            logging.info(f"[DEBUG] Attempting to read Excel file: {excel_path}")
            logging.info(f"[DEBUG] File exists: {os.path.exists(excel_path)}")
            logging.info(f"[DEBUG] File size: {os.path.getsize(excel_path) if os.path.exists(excel_path) else 'N/A'}")
            
            # Read Excel file without headers
            df = pd.read_excel(excel_path, sheet_name='Agent list', header=None)
            logging.info(f"[DEBUG] Successfully read Excel file. Shape: {df.shape}")
            
            # Find section boundaries
            agents_start = 4  # Row 5 (0-based) contains the agents header
            port_captains_start = 203  # Row 204 (0-based) contains the port captains header
            suppliers_start = 235  # Row 236 (0-based) contains the suppliers header
            
            logging.info(f"[DEBUG] Processing sections - Agents: {agents_start}-{port_captains_start}, Port Captains: {port_captains_start}-{suppliers_start}, Suppliers: {suppliers_start}-{len(df)}")
            
            # Process each section
            agents = self._process_agents_section(df, agents_start, port_captains_start)
            port_captains = self._process_port_captains_section(df, port_captains_start, suppliers_start)
            suppliers = self._process_suppliers_section(df, suppliers_start, len(df))
            
            logging.info(f"[DEBUG] Processed sections - Agents: {len(agents)}, Port Captains: {len(port_captains)}, Suppliers: {len(suppliers)}")
            
            return {
                'agents': agents,
                'port_captains': port_captains,
                'suppliers': suppliers
            }
        except Exception as e:
            logging.error(f"[ERROR] Failed to process Excel file {excel_path}: {str(e)}")
            logging.error(f"[ERROR] Exception details:", exc_info=True)
            raise
    
    def process_files(self, file_paths: List[str]) -> Dict[str, Dict[str, List[Dict]]]:
        """Process multiple Excel files and return data organized by agency."""
        all_agencies = {}
        
        for excel_path in file_paths:
            logging.info(f"[DEBUG] Processing agency file: {excel_path}")
            
            try:
                # Extract agency name
                agency_name = self._extract_agency_name(excel_path)
                logging.info(f"[DEBUG] Extracted agency name: {agency_name}")
                
                # Process Excel file
                agency_data = self.process_excel(excel_path)
                
                # Store data by agency name
                all_agencies[agency_name] = agency_data
                logging.info(f"[DEBUG] Successfully processed {agency_name}")
                
            except Exception as e:
                logging.error(f"[ERROR] Error processing file {excel_path}: {e}")
                logging.error(f"[ERROR] Exception details:", exc_info=True)
                continue
        
        logging.info(f"[DEBUG] process_files completed. Total agencies processed: {len(all_agencies)}")
        return all_agencies
    
    def save_outputs(self, agencies_data: Dict[str, Dict[str, List[Dict]]], output_dir: str = 'output/agencies'):
        """Save outputs for each agency in separate folders."""
        os.makedirs(output_dir, exist_ok=True)
        for agency_name, agency_data in agencies_data.items():
            agency_output_dir = os.path.join(output_dir, agency_name)
            os.makedirs(agency_output_dir, exist_ok=True)
            json_path = os.path.join(agency_output_dir, f'{agency_name}_data.json')
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(agency_data, f, indent=2, ensure_ascii=False)
            
            # Save Markdown
            md_path = os.path.join(agency_output_dir, f'{agency_name}_data.md')
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(f"# {agency_name} Agency List\n\n")
                
                # Write Agents section
                f.write("## Agents\n\n")
                f.write("| Country | Port | Agent Name | Status | Sanctioned | Email | Phone | Port3 | PIC | Remarks |\n")
                f.write("|---------|------|------------|--------|------------|-------|-------|-------|-----|----------|\n")
                
                for agent in agency_data['agents']:
                    country = agent['country']
                    port = agent['port']
                    name = agent['agent_name']
                    status = agent['status']
                    sanctioned = agent['sanctioned']
                    email = agent['contact']['email']
                    phone = agent['contact']['phone']
                    port3 = agent['contact']['port3']
                    pic = agent['contact_person']['name']
                    remarks = agent['remarks']
                    
                    f.write(f"| {country} | {port} | {name} | {status} | {sanctioned} | {email} | {phone} | {port3} | {pic} | {remarks} |\n")
                
                # Write Port Captains section
                f.write("\n## Port Captains\n\n")
                f.write("| Country (Resident) | Company Name | Status | Contact Name | Phone | Email | Regions | Remarks |\n")
                f.write("|-------------------|--------------|--------|--------------|-------|-------|---------|----------|\n")
                
                for pc in agency_data['port_captains']:
                    country = pc['country_resident']
                    company = pc['company_name']
                    status = pc['status']
                    contact = pc['contact']['name']
                    phone = pc['contact']['phone']
                    email = pc['contact']['email']
                    regions = pc['regions']
                    remarks = pc['remarks']
                    
                    f.write(f"| {country} | {company} | {status} | {contact} | {phone} | {email} | {regions} | {remarks} |\n")
                
                # Write Suppliers section
                f.write("\n## Suppliers\n\n")
                f.write("| Country | Lashing/Lifting | Manpower | Other Services | Company Name | Email | Phone | Primary Contact | Secondary Contact | Remarks |\n")
                f.write("|---------|----------------|----------|----------------|--------------|-------|-------|-----------------|-------------------|----------|\n")
                
                for supplier in agency_data['suppliers']:
                    country = supplier['country']
                    lashing = "True" if supplier['services']['lashing_lifting'] else "False"
                    manpower = "True" if supplier['services']['manpower'] else "False"
                    other = supplier['services']['other']
                    company = supplier['company_name']
                    email = supplier['contact']['email']
                    phone = supplier['contact']['phone']
                    primary = supplier['contact_person']['primary']
                    secondary = supplier['contact_person']['secondary']
                    remarks = supplier['remarks']
                    
                    f.write(f"| {country} | {lashing} | {manpower} | {other} | {company} | {email} | {phone} | {primary} | {secondary} | {remarks} |\n")
            
            # Save CSV files
            csv_dir = os.path.join(agency_output_dir, 'csv')
            os.makedirs(csv_dir, exist_ok=True)
            
            # Write Agents CSV
            with open(f'{csv_dir}/agents.csv', 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['Country', 'Port', 'Agent name', 'Status', 'Sanction', 'Email', 'Phone', 'Port3', 'PIC', 'Remarks'])
                
                for agent in agency_data['agents']:
                    writer.writerow([
                        agent['country'],
                        agent['port'],
                        agent['agent_name'],
                        agent['status'],
                        agent['sanctioned'],
                        agent['contact']['email'],
                        agent['contact']['phone'],
                        agent['contact']['port3'],
                        agent['contact_person']['name'],
                        agent['remarks']
                    ])
            
            # Write Port Captains CSV
            with open(f'{csv_dir}/port_captains.csv', 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['Country (Resident)', 'Company Name', 'Status', 'Phone', 'Email2', 'Remarks', 'Regions', 'PIC'])
                
                for pc in agency_data['port_captains']:
                    writer.writerow([
                        pc['country_resident'],
                        pc['company_name'],
                        pc['status'],
                        pc['contact']['phone'],
                        pc['contact']['email'],
                        pc['remarks'],
                        pc['regions'],
                        pc['contact']['name']
                    ])
            
            # Write Suppliers CSV
            with open(f'{csv_dir}/suppliers.csv', 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['Country', 'Supply of Lashing/Lifting equipment', 'Manpower Supply (lashing/welding)', 
                               'Supplier (Other)', 'Company Name', 'Email', 'Phone', 'PIC', 'PIC2', 'Remarks'])
                
                for supplier in agency_data['suppliers']:
                    writer.writerow([
                        supplier['country'],
                        'YES' if supplier['services']['lashing_lifting'] else 'NO',
                        'YES' if supplier['services']['manpower'] else 'NO',
                        supplier['services']['other'],
                        supplier['company_name'],
                        supplier['contact']['email'],
                        supplier['contact']['phone'],
                        supplier['contact_person']['primary'],
                        supplier['contact_person']['secondary'],
                        supplier['remarks']
                    ])
            
            print(f"Saved outputs for {agency_name} in {agency_output_dir}")

def main():
    # Configure logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Create output directory if it doesn't exist
    output_dir = 'output/agencies'
    os.makedirs(output_dir, exist_ok=True)
    
    # Initialize extractor
    extractor = AgencyDataExtractor('source/agencies')
    
    # Process all Excel files in source/agencies directory
    source_files = []
    for file in os.listdir('source/agencies'):
        if file.startswith('~$'):  # Skip temporary files
            continue
        if file.endswith('.xlsx'):
            source_files.append(os.path.join('source/agencies', file))
    
    if not source_files:
        logging.error("No .xlsx files found in source/agencies directory")
        return
    
    print(f"Found {len(source_files)} files to process:")
    for file in source_files:
        print(f"  - {file}")
    
    # Process files and get data organized by agency
    agencies_data = extractor.process_files(source_files)
    
    # Save outputs for each agency
    extractor.save_outputs(agencies_data, output_dir)
    
    print(f"\nProcessing complete! Processed {len(agencies_data)} agencies:")
    for agency_name in agencies_data.keys():
        print(f"  - {agency_name}")
    
    # Upload files to Langdock Knowledge Folder (optional)
    try:
        folder_id = "69c0327b-24fe-4cd8-97d8-92688b526bac"
        api_key = os.getenv('LANGDOCK_API_KEY')
        
        if api_key:
            # Collect all generated files for upload
            files_to_upload = []
            for agency_name in agencies_data.keys():
                agency_dir = os.path.join(output_dir, agency_name)
                files_to_upload.extend([
                    os.path.join(agency_dir, f'{agency_name}_data.json'),
                    os.path.join(agency_dir, f'{agency_name}_data.md'),
                    os.path.join(agency_dir, 'csv', 'agents.csv'),
                    os.path.join(agency_dir, 'csv', 'port_captains.csv'),
                    os.path.join(agency_dir, 'csv', 'suppliers.csv')
                ])
            
            # Upload files with deduplication
            for file_path in files_to_upload:
                if os.path.exists(file_path):
                    extractor.upload_or_update_to_langdock(file_path, folder_id, api_key)
                else:
                    logging.warning(f"File {file_path} not found, skipping upload")
            
            # Save updated mappings after all uploads
            extractor.save_attachment_mappings()
            logging.info(f"[DEBUG] Final attachment_mappings: {json.dumps(extractor.attachment_mappings, indent=2)}")
        else:
            logging.warning("LANGDOCK_API_KEY environment variable not set. Skipping upload to Langdock.")
            
    except Exception as e:
        logging.error(f"Error uploading files to Langdock: {str(e)}")
        logging.error(f"[ERROR] Error uploading files to Langdock: {e}")

if __name__ == '__main__':
    main() 