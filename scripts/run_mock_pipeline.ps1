$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
python scripts/create_sample_bom.py
python -m bom_tool.cli samples/sample_bom.xlsx samples/sample_bom_completed.xlsx --search-column 器件型号 --supplier mock --field status --field mpn --field sku --field stock --field price_unit --field description
