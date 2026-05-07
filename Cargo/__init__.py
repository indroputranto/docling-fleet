"""
Cargo / packing-list pipeline.

This package is intentionally self-contained.  It does NOT import from the
documents/ pipeline — every helper it needs (object storage, parsing,
templating) lives inside this folder so cargo work can never accidentally
break the document upload flow.

Public modules:
  parser           — parse .xlsx / .xls packing lists into normalized items
  packer           — multi-bin 3D bin-packer with weight + balance constraints
  object_storage   — DO Spaces wrapper (duplicated from documents/, not shared)
  routes           — Flask blueprint mounted at /cargo
"""

__all__ = ["parser", "packer", "object_storage", "routes"]
