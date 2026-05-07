#!/usr/bin/env python3
"""Regenerate OpenAPI spec and docs."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.openapi.utils import get_openapi
from huginn.api import create_app

app = create_app()
spec = get_openapi(title=app.title, version=app.version, routes=app.routes)

os.makedirs("docs", exist_ok=True)
with open("docs/openapi.json", "w") as f:
    json.dump(spec, f, indent=2)

try:
    import yaml
    with open("docs/openapi.yaml", "w") as f:
        yaml.dump(spec, f, default_flow_style=False, sort_keys=False)
except ImportError:
    pass

print(f"Generated docs/openapi.json — {len(spec['paths'])} endpoints, {len(spec.get('components',{}).get('schemas',{}))} schemas")
