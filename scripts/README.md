# Scripts

## 生成样例 BOM

```powershell
python scripts/create_sample_bom.py
```

## 跑通 Mock 端到端闭环

```powershell
python -m bom_tool.cli samples/sample_bom.xlsx samples/sample_bom_completed.xlsx --search-column 器件型号 --supplier mock --field status --field mpn --field sku --field stock --field price_unit --field description
```

或执行：

```powershell
.\scripts\run_mock_pipeline.ps1
```

## 跑通立创端到端闭环

```powershell
python -m bom_tool.cli samples/sample_bom.xlsx samples/sample_bom_lcsc_completed.xlsx --search-column 器件型号
```
