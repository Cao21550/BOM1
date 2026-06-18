# BOM Auto-Complete Tool · BOM 表自动完善工具

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/GUI-PySide6-41cd52?logo=qt&logoColor=white)](https://www.qt.io/qt-for-python)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078d7?logo=windows&logoColor=white)](https://www.microsoft.com/windows)

> 本地桌面工具，批量查询电子元器件供应商的库存、价格、规格等信息，自动回填到 BOM（物料清单）表中。  
> 支持 **立创商城 (LCSC)** 与 **华秋商城 (HQChip)** 双供应商查询，提供图形界面与命令行两种操作模式。

---

## 📸 截图

> *截图待补充 — 可通过 `docs/screenshot.png` 添加*

![GUI 主界面预览](docs/screenshot.png)

---

## ✨ 功能概览

| 功能 | 说明 |
|------|------|
| 📂 **文件支持** | 读取/导出 `.xlsx` 和 `.csv` 格式 BOM 文件，保留原 Excel 样式 |
| 🔍 **智能表头识别** | 自动识别常见 BOM 表头（型号、封装、数量等），一键选择搜索列 |
| 🏪 **双供应商查询** | **立创商城** (LCSC) — 实时网页搜索，含价格阶梯；**华秋商城** (HQChip) — HTML 优先 + API 兜底 |
| ⚡ **异步并发引擎** | 基于 `asyncio` + `curl_cffi`，HTTP/1.1 长连接，Akamai 反爬规避 |
| 🧠 **SQLite 缓存** | 自动缓存查询结果（默认 7 天），重复查询毫秒级响应，支持跳过/清空 |
| 🎛️ **两种查询模式** | **稳定优先**（慢但稳，间隔 1.2s） vs **速度优先**（快但风险稍高，间隔 0.8s） |
| 🔄 **失败重试** | 批量完成后自动重试失败的型号，支持单独配置重试并发数 |
| 🔎 **单次查询** | 快速查询单个器件型号，结果实时展示，支持点击复制 |
| 📋 **命令行模式** | 无需图形界面，支持批处理脚本和 CI 集成 |
| ⏸️ **暂停/取消** | 查询过程中随时暂停或取消任务 |

---

## 🚀 快速开始

### 环境要求

- **操作系统**：Windows 10 / Windows 11（64 位）
- **Python**：3.10 或更高版本
- **网络**：需要互联网连接（访问供应商网站）

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
# 立创商城查询
python -m bom_tool.cli 输入BOM.xlsx 输出BOM_回填.xlsx --search-column 型号 --supplier lcsc

# 多供应商查询
python -m bom_tool.cli 输入BOM.xlsx 输出BOM_回填.xlsx --search-column 2 --supplier lcsc --supplier hqchip
```

---

## 📖 详细文档

完整的软件使用说明（含操作步骤、缓存机制、常见问题等）请参阅：

👉 **[BOM 自动完善工具使用说明书](docs/BOM自动完善工具_使用说明书.md)**

---

## 🎮 GUI 模式

图形界面提供以下功能：

| 功能 | 位置 |
|------|------|
| 文件选择与 BOM 预览表格（前 20 行） | 主页面 |
| 多工作表切换 | 主页面 |
| 列头点击自动识别搜索列 | 主页面 |
| 查询模式选择（稳定优先 / 速度优先） | 主页面 |
| 缓存预检查 | 主页面 |
| 实时进度条和日志输出 | 主页面 |
| 暂停 / 取消任务 | 主页面 |
| 单次快速查询 | 单次查询标签页 |
| 清空缓存 | 设置区域 |

```powershell
python -m bom_tool.main
```

---

## 💻 命令行模式

### CLI 参数说明

| 参数 | 说明 |
|------|------|
| `input` | 输入文件路径（必需） |
| `output` | 输出文件路径（必需） |
| `--search-column` | 搜索列：表头名（如"型号"）或 1 开始的列号（必需） |
| `--search-type` | 搜索类型：`mpn`（器件型号，默认）或 `sku`（商城编号） |
| `--supplier` | 供应商：`lcsc`、`hqchip`；可重复指定（默认 `lcsc`） |
| `--sheet` | Excel 工作表名称 |
| `--max-concurrent` | 并发数（默认 1） |
| `--retry-max-concurrent` | 失败重试的并发数（默认 1） |
| `--field` | 指定输出字段，可重复 |
| `--no-cache` | 禁用 SQLite 缓存 |
| `--cache-path` | SQLite 缓存文件路径 |
| `--cache-ttl-hours` | 缓存有效期（默认 168 小时 = 7 天） |
| `--fast-xlsx` | 快速导出模式（不复制数据行样式） |
| `--lcsc-interval` | LCSC 请求间隔秒数（默认 1.2，降低可提速但可能触发反爬） |

### 使用示例

```powershell
# 基本用法
python -m bom_tool.cli bom.xlsx bom_completed.xlsx --search-column 器件型号 --supplier lcsc

# 多供应商 + 自定义字段
python -m bom_tool.cli bom.xlsx out.xlsx ^
    --search-column 型号 ^
    --supplier lcsc --supplier hqchip ^
    --field 品牌 --field 封装 --field 库存 --field 单价 ^
    --max-concurrent 2

# 禁用缓存 + 快速导出
python -m bom_tool.cli bom.xlsx out.xlsx --search-column 2 --supplier lcsc --no-cache --fast-xlsx

# 编译版直接使用
BOM自动完善工具_20260619.exe 输入BOM.xlsx 输出BOM_回填.xlsx --search-column 型号 --supplier lcsc
```

---

## 🏗️ 项目结构

```
bom_tool/
├── __init__.py              # 包入口
├── main.py                  # GUI 入口
├── cli.py                   # CLI 入口
├── models.py                # 数据模型 (PartResult, PriceBreak)
├── adapters/                # 供应商适配器
│   ├── base_adapter.py      #   抽象基类
│   ├── lcsc_adapter.py      #   立创商城 (curl_cffi 网页搜索)
│   ├── hqchip_adapter.py    #   华秋商城 (HTML优先 + API兜底)
│   ├── mock_adapter.py      #   模拟适配器 (测试用)
│   └── registry.py          #   适配器注册工厂
├── core/                    # 核心业务逻辑
│   ├── bom_pipeline.py      #   管线编排
│   ├── data_cleaner.py      #   MPN 清洗/标准化
│   ├── file_parser.py       #   Excel/CSV 解析
│   ├── file_writer.py       #   Excel/CSV 写入
│   └── task_manager.py      #   异步任务调度
├── db/
│   └── cache_db.py          #   SQLite 查询缓存
├── ui/
│   └── main_window.py       #   PySide6 主窗口
└── utils/
    └── __init__.py          #   AsyncRateLimiter
```

---

## 🧪 测试

```powershell
# 运行所有测试
python -m pytest

# 详细模式
python -m pytest tests/ -v --tb=short
```

Mock 端到端验证：

```powershell
python scripts/create_sample_bom.py
python -m bom_tool.cli samples/sample_bom.xlsx samples/output.xlsx --search-column 2 --supplier mock
```

---

## 📦 编译为 exe

```powershell
pip install pyinstaller
python -m PyInstaller --onefile --windowed --name "BOM自动完善工具_20260619" --collect-submodules bom_tool bom_tool/main.py
```

编译产物在 `dist/BOM自动完善工具_20260619.exe`，双击即可运行，无需 Python 环境。

> **注意**：PyInstaller 打包的程序可能存在杀软误报，添加信任/排除项即可。

---

## 🧰 技术栈

| 层次 | 技术 |
|------|------|
| **GUI** | PySide6 (Qt6) |
| **HTTP** | curl_cffi（浏览器指纹模拟，Akamai 规避） |
| **文件** | openpyxl（xlsx）、pandas（csv） |
| **缓存** | SQLite（WAL 模式，批量读写） |
| **异步** | asyncio + asyncio.Semaphore 并发控制 |
| **测试** | pytest + pytest-asyncio |

### 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                      PySide6 GUI                            │
│  ┌─────────────┐  ┌─────────────┐  ┌───────────────────┐  │
│  │  批量处理页   │  │  单次查询页  │  │ 日志/进度显示      │  │
│  └──────┬──────┘  └──────┬──────┘  └───────────────────┘  │
└─────────┼────────────────┼─────────────────────────────────┘
          │                │
          ▼                ▼
┌─────────────────────────────────────────────────────────────┐
│                    核心业务层                                │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────────┐  │
│  │ BomPipeline  │ │ TaskManager  │ │  CacheDB(SQLite)   │  │
│  └──────┬───────┘ └──────┬───────┘ └────────────────────┘  │
└─────────┼────────────────┼─────────────────────────────────┘
          │                │
          ▼                ▼
┌─────────────────────────────────────────────────────────────┐
│                    适配器层                                  │
│  ┌──────────────────┐ ┌──────────────────────────────────┐ │
│  │  LcscAdapter     │ │  HqchipAdapter                   │ │
│  │  (curl_cffi)     │ │  (curl_cffi, HTML优先+API兜底)   │ │
│  └──────────────────┘ └──────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## 📄 License

[MIT](LICENSE) © 2026
