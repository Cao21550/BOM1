# BOM 表自动完善工具

本项目是一个本地桌面工具，用于读取 Excel/CSV BOM 文件，批量查询供应商库存、价格、描述、商品链接和数据手册链接，并把结果追加到原表副本中。

## 功能概览

- 支持 `.xlsx` 和 `.csv` BOM 文件。
- 自动识别常见 BOM 表头行。
- 支持按器件型号或商城编号/SKU 查询。
- 支持立创商城、华秋商城和贸泽搜索链接。
- 支持 SQLite 查询缓存，减少重复访问。
- 导出时保留 Excel 原有样式，并禁止覆盖输入文件。

## 环境安装

建议使用 Python 3.10 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

## 启动桌面界面

```powershell
python -m bom_tool.main
```

## 命令行批处理

```powershell
python -m bom_tool.cli 输入BOM.xlsx 输出BOM_回填.xlsx --search-column 器件型号
```

常用参数：

- `--search-column 器件型号`：搜索列，可填写表头名，也可填写 1 开始的列号。
- `--search-type mpn`：按器件型号搜索；按商城编号搜索可改为 `sku`。
- `--supplier lcsc`：供应商，可重复传入。支持 `lcsc`、`hqchip`、`mouser`。
- `--field stock --field price_unit`：只导出指定字段；不传时使用默认字段。
- `--sheet Sheet1`：指定 Excel 工作表；不传时读取第一个工作表。
- `--max-concurrent 1`：批量查询并发数。网页数据源容易限流，建议保持较低。
- `--no-cache`：禁用 SQLite 查询缓存。
- `--cache-ttl-hours 168`：缓存有效期，默认 7 天。

## Mock 端到端验证

```powershell
python scripts/create_sample_bom.py
python -m bom_tool.cli samples/sample_bom.xlsx samples/sample_bom_completed.xlsx --search-column 器件型号 --supplier mock
```

## 测试

```powershell
python -m pytest
```
