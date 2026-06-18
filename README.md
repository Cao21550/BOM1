# BOM 表自动完善工具

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/GUI-PySide6-green)](https://www.qt.io/qt-for-python)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

**BOM Auto-Complete Tool** — 本地桌面工具，批量查询电子元器件供应商的库存、价格、规格等信息，自动回填到 BOM 表。

读取 Excel/CSV BOM 文件 → 批量查询立创/华秋/贸泽 → 将结果追加到原表副本。

---

## 截图

<!-- TODO: Add screenshot -->
![GUI 主界面](docs/screenshot.png)

---

## 功能概览

- 📂 支持 `.xlsx` 和 `.csv` BOM 文件导入/导出
- 🔍 自动识别常见 BOM 表头行（型号、封装、数量等）
- 🏪 **三供应商支持**：
  - **立创商城** (LCSC) — 实时网页/API 查询，含价格阶梯
  - **华秋商城** (HQChip) — 实时网页查询
  - **贸泽** (Mouser) — 搜索链接跳转
- ⚡ **异步并发** — 基于 `asyncio` + `httpx` 的异步查询引擎
- 💾 **SQLite 缓存** — 自动缓存查询结果，减少重复网络请求
- 📋 **命令行模式** — 支持无界面批处理、CI 集成
- 🎨 **保留 Excel 样式** — 追加数据时保持原表格式

---

## 快速开始

### 环境要求

- Python 3.10 或更高版本

### 安装

```powershell
git clone https://github.com/Cao21550/BOM1.git
cd BOM1
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

### 启动桌面界面

```powershell
python -m bom_tool.main
```

### 命令行批处理

```powershell
python -m bom_tool.cli 输入BOM.xlsx 输出BOM_回填.xlsx --search-column 型号 --supplier lcsc
```

---

## 使用示例

### 基本用法

```powershell
# 使用立创商城查询 MPN
python -m bom_tool.cli bom.xlsx bom_completed.xlsx --search-column 2 --supplier lcsc

# 多供应商
python -m bom_tool.cli bom.xlsx bom_completed.xlsx --search-column 器件型号 --supplier lcsc --supplier hqchip
```

### 参数说明

| 参数 | 说明 |
|------|------|
| `--search-column` | 搜索列：表头名（如"型号"）或 1 开始的列号 |
| `--search-type` | 搜索类型：`mpn`（器件型号，默认）或 `sku`（商城编号） |
| `--supplier` | 供应商：`lcsc`、`hqchip`、`mouser`；可重复指定 |
| `--max-concurrent` | 批量查询并发数（建议 1-2，避免被限流） |
| `--field` | 指定导出字段，可重复（默认含品牌、封装、库存、价格等） |
| `--no-cache` | 禁用 SQLite 缓存 |
| `--cache-ttl-hours` | 缓存有效期，默认 168 小时（7 天） |
| `--fast-xlsx` | 快速导出模式（不复制数据行样式） |
| `--retry-max-concurrent` | 失败重试的并发数 |

### GUI 模式

```powershell
python -m bom_tool.main
```

图形界面提供：
- 文件选择与 BOM 预览表格
- 列头点击自动识别搜索列
- 缓存预检查
- 实时进度条和日志输出
- 暂停/取消任务

---

## 项目结构

```
bom_tool/
├── __init__.py
├── main.py               # GUI 入口
├── cli.py                # CLI 入口
├── models.py             # 数据模型 (PartResult, PriceBreak)
├── adapters/             # 供应商适配器
│   ├── base_adapter.py   #   抽象基类
│   ├── lcsc_adapter.py   #   立创商城 (网页 + API)
│   ├── hqchip_adapter.py #   华秋商城 (网页)
│   ├── mock_adapter.py   #   模拟适配器 (测试用)
│   ├── search_link_adapter.py  # 搜索链接适配器
│   └── registry.py       #   适配器注册工厂
├── core/                 # 核心业务逻辑
│   ├── bom_pipeline.py   #   管线编排
│   ├── data_cleaner.py   #   MPN 清洗/标准化
│   ├── file_parser.py    #   Excel/CSV 解析
│   ├── file_writer.py    #   Excel/CSV 写入
│   └── task_manager.py   #   异步任务调度
├── db/
│   └── cache_db.py       #   SQLite 查询缓存
├── ui/
│   └── main_window.py    #   PySide6 主窗口
└── utils/
    └── __init__.py       #   AsyncRateLimiter
```

---

## 测试

```powershell
python -m pytest          # 运行所有测试
python -m pytest tests/ -v --tb=short  # 详细模式
```

Mock 端到端验证：

```powershell
python scripts/create_sample_bom.py
python -m bom_tool.cli samples/sample_bom.xlsx samples/output.xlsx --search-column 2 --supplier mock
```

---

## 编译为 exe

```powershell
pip install pyinstaller
python -m PyInstaller --onefile --windowed --name "BOM自动完善工具" --collect-submodules bom_tool bom_tool/main.py
```

编译产物在 `dist/BOM自动完善工具.exe`，双击即可运行，无需 Python 环境。

---

## 技术栈

- **GUI**: PySide6 (Qt6)
- **HTTP**: httpx (HTTP/2, async)
- **文件**: openpyxl (xlsx), pandas (csv)
- **缓存**: SQLite (WAL 模式, 批量读写)
- **异步**: asyncio + semaphore 并发控制
- **测试**: pytest + pytest-asyncio

---

## License

[MIT](LICENSE)
