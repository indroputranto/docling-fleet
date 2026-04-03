# Document Extraction System

This document extraction system implements a comprehensive pipeline for processing vessel specification documents. It focuses on the first 4 steps of document processing: document loading, text extraction, content parsing, and structured data extraction.

## Features

### 1. Document Loading
- Supports `.docx` and `.doc` files
- Automatic directory scanning
- Temporary file filtering
- Error handling and logging

### 2. Text Extraction
- Raw text extraction using `mammoth`
- Text cleaning and normalization
- Preservation of important formatting
- Handling of special characters and patterns

### 3. Content Parsing
- Section identification using regex patterns
- Support for numbered and unnumbered sections
- Table detection and extraction
- Key-value pair extraction

### 4. Structured Data Extraction
- Organized data structure with metadata
- Section-based content organization
- Table data extraction
- Key-value pair mapping

## Installation

1. Install required dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Basic Usage

```python
from process_vessel_new import DocumentExtractor

# Initialize the extractor
extractor = DocumentExtractor(
    source_dir="source/vessels",
    output_dir="output/vessels"
)

# Process all documents
extractor.process_all_documents()
```

### Single Document Processing

```python
from pathlib import Path
from process_vessel_new import DocumentExtractor

extractor = DocumentExtractor()

# Process a single document
file_path = Path("source/vessels/ARA_ROTTERDAMDONE.docx")
data = extractor.process_document(file_path)

# Save outputs
vessel_name = data.get("vessel_info", {}).get("name", "UNNAMED_VESSEL")
extractor.save_output(vessel_name, data, file_path)
```

### Testing

Run the test suite to verify functionality:

```bash
python test_extraction.py
```

## Output Formats

The system generates three output formats for each processed document:

### 1. JSON Output (`{vessel_name}_data.json`)
Structured data with metadata, sections, and extracted information:

```json
{
  "metadata": {
    "extraction_date": "2024-01-15T10:30:00",
    "total_lines": 150,
    "document_type": "vessel_specification"
  },
  "vessel_info": {
    "name": "ARA ROTTERDAM",
    "source_file": "ARA_ROTTERDAMDONE.docx",
    "file_size": 245760,
    "processing_timestamp": "2024-01-15T10:30:00"
  },
  "sections": {
    "Vessel Details": {
      "content": ["1. Vessel Details", "Vessel Name: ARA ROTTERDAM"],
      "tables": [],
      "key_value_pairs": {
        "Vessel Name": "ARA ROTTERDAM"
      }
    }
  }
}
```

### 2. Markdown Output (`{vessel_name}_data.md`)
Human-readable format with sections, tables, and key information:

```markdown
# ARA ROTTERDAM - Vessel Specification

**Source File:** ARA_ROTTERDAMDONE.docx
**Processing Date:** 2024-01-15 10:30:00

## Vessel Details

1. Vessel Details
Vessel Name: ARA ROTTERDAM

### Key Information

**Vessel Name:** ARA ROTTERDAM
```

### 3. HTML Output (`{vessel_name}_data.html`)
Web-ready format with styling and structured layout.

## Supported Document Sections

The system recognizes and extracts the following sections:

1. **Vessel Details** - Basic vessel information
2. **Contract Details** - Contract and agreement information
3. **Delivery Figures** - Delivery specifications and figures
4. **Speed and Consumption** - Speed and fuel consumption data
5. **Lifting Equipment** - Equipment specifications
6. **HSEQ Documentation** - Health, Safety, Environment, and Quality documentation

## Configuration

### Section Patterns

You can customize section detection by modifying the `section_patterns` in the `DocumentExtractor` class:

```python
self.section_patterns = {
    "Vessel Details": [
        r"1\.\s*Vessel Details",
        r"Vessel Details",
        r"1\.1\s*Vessel Details"
    ],
    # Add more sections as needed
}
```

### Table Patterns

Configure table extraction for specific sections:

```python
self.table_patterns = {
    "Speed and Consumption": {
        "headers": ["Speed (knots)", "Condition (B/L)", "Engine Load (%)", "Fuel Consumption (HFO mt/day)"],
        "row_pattern": r"(\d+(?:\.\d+)?)\s+(\w+)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)"
    }
}
```

## Error Handling

The system includes comprehensive error handling:

- **File Processing Errors**: Individual file errors are logged and processing continues
- **Text Extraction Errors**: Graceful handling of corrupted or unsupported files
- **Section Parsing Errors**: Fallback mechanisms for unrecognized sections
- **Output Generation Errors**: Separate error handling for each output format

## Logging

The system uses Python's logging module with configurable levels:

```python
import logging

# Set logging level
logging.basicConfig(level=logging.INFO)

# Custom logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('extraction.log'),
        logging.StreamHandler()
    ]
)
```

## Performance Considerations

- **Memory Usage**: Documents are processed one at a time to minimize memory usage
- **Processing Speed**: Optimized regex patterns and efficient text processing
- **File I/O**: Minimal file operations with efficient output generation
- **Error Recovery**: Continues processing even if individual files fail

## Extending the System

### Adding New Document Types

To support additional document formats, extend the `process_document` method:

```python
def process_document(self, file_path: Path) -> Dict[str, Any]:
    if file_path.suffix.lower() == '.pdf':
        return self.process_pdf_document(file_path)
    elif file_path.suffix.lower() in ['.docx', '.doc']:
        return self.process_word_document(file_path)
    else:
        raise ValueError(f"Unsupported file type: {file_path.suffix}")
```

### Adding New Section Types

To add new sections, update the `section_patterns` and add corresponding processing logic:

```python
# Add to section_patterns
"New Section": [
    r"7\.\s*New Section",
    r"New Section"
]

# Add to table_patterns if needed
"New Section": {
    "headers": ["Column 1", "Column 2"],
    "row_pattern": r"(\w+)\s+(\w+)"
}
```

## Troubleshooting

### Common Issues

1. **No documents found**: Check the source directory path and file extensions
2. **Section not detected**: Verify the section patterns match your document format
3. **Table extraction fails**: Check table structure and update patterns accordingly
4. **Output directory errors**: Ensure write permissions for the output directory

### Debug Mode

Enable debug logging for detailed processing information:

```python
logging.basicConfig(level=logging.DEBUG)
```

## Future Enhancements

- PDF document support
- OCR for scanned documents
- Machine learning-based section detection
- Real-time processing capabilities
- API integration for web-based processing
