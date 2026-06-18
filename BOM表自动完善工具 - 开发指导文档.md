# BOM表自动完善工具 - 开发指导文档
本文档是对“BOM表自动完善工具”的需求分析与技术实现方案的系统总结，作为开发过程中的核心参考手册。
---
## 1. 项目概述
本软件旨在解决电子工程师、采购人员在BOM（物料清单）整理过程中的痛点，通过自动化工具读取本地结构化文件（Excel/CSV），批量从立创商城、华秋商城、一博在线等元器件交易平台抓取物料详情（价格、库存、手册等），并将数据以**原格式回填**到表格中。软件同时支持单次型号查询，并采用高度解耦的架构设计以便于后续扩展更多元器件平台。
## 2. 核心需求规格
### 2.1 功能需求
*   **文件解析**：支持读取 `.xlsx`, `.xls`, `.csv` 格式。上传后自动读取表头，不限定固定模板。
*   **动态列映射**：用户上传文件后，软件自动提取表头列名，由用户指定哪一列作为“搜索关键字”（支持按“器件型号”或“商城编号”搜索）。
*   **多源数据抓取**：支持立创商城、华秋商城、一博在线。目标抓取平台由用户在界面上自由勾选。
*   **单次/批量查询**：
    *   批量：整表自动处理，支持异步并发与进度显示。
    *   单次：输入框输入型号/SKU，即时返回结果卡片。
*   **原格式回填**：查询结果回填至原表格时，必须保留原有的单元格样式（字体、颜色、边框、合并单元格等），采用“另存为新文件”模式输出。
### 2.2 非功能需求
*   **架构解耦**：商城抓取模块必须插件化，新增商城不影响核心业务代码。
*   **性能与反爬**：支持并发限速控制，避免触发目标网站反爬机制被封禁IP。
*   **数据匹配率**：需具备型号清洗能力，自动剔除常见包装后缀（如 `-TR`, `-CT`），提高搜索命中率。
---
## 3. 技术栈选型
| 模块               | 技术选型                             | 选型理由                                                     |
| :----------------- | :----------------------------------- | :----------------------------------------------------------- |
| **桌面UI框架**     | `Python + PySide6 (Qt)`              | 提供原生级别流畅的UI，支持异步线程更新UI，适合复杂的表头映射和进度交互。 |
| **文件读取与回填** | `openpyxl` (Excel), `pandas` (CSV)   | `openpyxl` 能在修改单元格值的同时完美保留原有样式；`pandas` 处理CSV高效。 |
| **网络请求/爬虫**  | `httpx` (异步请求), `BeautifulSoup4` | `httpx` 支持HTTP/2和异步并发，提升抓取速度；体积小且高效。   |
| **并发控制**       | `asyncio` + `asyncio.Semaphore`      | 协程处理大批量查询任务，信号量限制并发数（如5-10次/秒）。    |
| **本地缓存**       | `SQLite`                             | 缓存查询过的器件信息，二次查询相同物料时实现毫秒级响应。     |
---
## 4. 系统架构与解耦设计
### 4.1 目录结构规划
```text
bom_tool/
├── main.py                     # 程序入口
├── ui/                         # PySide6 界面代码
│   ├── main_window.py          # 主窗口
│   ├── batch_tab.py            # 批量处理界面
│   └── single_tab.py           # 单次查询界面
├── core/                       # 核心业务逻辑
│   ├── file_parser.py          # 文件读取与表头提取
│   ├── file_writer.py          # 原格式回填逻辑
│   ├── task_manager.py         # 异步任务调度与并发控制
│   └── data_cleaner.py         # 器件型号清洗（去后缀等）
├── adapters/                   # 适配器层（核心解耦点）
│   ├── base_adapter.py         # 适配器抽象基类
│   ├── lcsc_adapter.py         # 立创商城适配器
│   ├── hqchip_adapter.py       # 华秋商城适配器
│   └── edadoc_adapter.py       # 一博在线适配器
├── db/                         # SQLite 缓存模块
│   └── cache_db.py
└── utils/                      # 工具类
```
### 4.2 适配器模式设计
通过定义统一的抽象基类 `BaseSupplierAdapter`，上层任务调度器只需调用标准接口，无需关心底层是爬虫还是官方API。新增平台只需新增一个 Adapter 文件。
```python
# adapters/base_adapter.py
from abc import ABC, abstractmethod
from typing import Dict, Optional
class BaseSupplierAdapter(ABC):
    def __init__(self, supplier_name: str):
        self.supplier_name = supplier_name
    @abstractmethod
    async def search_by_mpn(self, mpn: str) -> Optional[Dict]:
        """根据器件型号查询"""
        pass
    @abstractmethod
    async def search_by_sku(self, sku: str) -> Optional[Dict]:
        """根据商城SKU查询"""
        pass
    def standardize_data(self, raw_data: Dict) -> Dict:
        """将各商城不同的字段名统一标准化输出"""
        return {
            "supplier": self.supplier_name,
            "mpn": raw_data.get("manufacturer_part_number"),
            "sku": raw_data.get("item_code"),
            "stock": raw_data.get("stock_quantity"),
            "price_unit": raw_data.get("unit_price"),
            "price_breaks": raw_data.get("price_breaks"), # 阶梯价
            "description": raw_data.get("description"),
            "datasheet_url": raw_data.get("datasheet"),
        }
```
---
## 5. 核心难点实现策略
### 5.1 原格式回填实现方案
**痛点**：直接用 `pandas.to_excel` 会丢失所有原表格格式。
**方案**：采用 `openpyxl` 的“加载原对象 -> 修改单元格 -> 另存为新文件”机制。
```python
# core/file_writer.py 示例逻辑
from openpyxl import load_workbook
def fill_bom_data(original_path, output_path, mapping_config, query_results):
    # 加载原文件，保持格式
    wb = load_workbook(original_path)
    ws = wb.active
    # 动态新增列标题（假设在第5列后新增）
    start_col = 5 
    ws.cell(row=1, column=start_col, value="立创_库存")
    ws.cell(row=1, column=start_col+1, value="立创_单价")
    # 写入查询到的数据
    for row_idx, mpn in enumerate(mapping_config['search_data'], start=2):
        result = query_results.get(mpn, {})
        if result and result.get('lcsc'):
            ws.cell(row=row_idx, column=start_col, value=result['lcsc']['stock'])
            ws.cell(row=row_idx, column=start_col+1, value=result['lcsc']['price_unit'])
    wb.save(output_path) # 保存为新文件
```
### 5.2 异步并发与反爬控制
采用 `asyncio` 协程 + `Semaphore` 信号量限速，避免触发反爬。
```python
# core/task_manager.py 示例逻辑
import asyncio
class TaskManager:
    def __init__(self, adapters, max_concurrent=5):
        self.adapters = adapters
        self.semaphore = asyncio.Semaphore(max_concurrent) # 限制并发数为5
    async def process_single_item(self, keyword):
        async with self.semaphore:
            tasks = [adapter.search_by_mpn(keyword) for adapter in self.adapters]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return results
    async def process_bom(self, keywords_list, progress_callback):
        results = {}
        total = len(keywords_list)
        for i, keyword in enumerate(keywords_list):
            # 优先查本地缓存，未命中再走网络请求
            cached = check_cache(keyword)
            if cached:
                results[keyword] = cached
            else:
                res = await self.process_single_item(keyword)
                results[keyword] = res
                save_to_cache(keyword, res)
            
            # 回调更新UI进度条
            progress_callback(int((i+1)/total * 100))
        return results
```
### 5.3 器件型号清洗逻辑
剔除BOM表中常见的包装、无铅等后缀标识，提高搜索匹配率。
```python
# core/data_cleaner.py
import re
def clean_mpn(mpn: str) -> str:
    mpn = mpn.strip()
    # 去除常见的包装后缀：-TR, -CT, -ND, -T, -T&R 等（不区分大小写）
    mpn = re.sub(r'[-/](TR|CT|ND|T|T&R|CT-ND)$', '', mpn, flags=re.IGNORECASE)
    return mpn
```
### 5.4 商城数据抓取策略 (以立创为例)
1.  **接口优先**：优先分析立创后台 JSON 接口（如 `https://search.szlcsc.com/api/v3/product/search`），直接请求接口效率最高。
2.  **请求伪装**：设置合理的 `User-Agent`，必要时携带基础 Cookie。
3.  **降级策略**：若接口增加验证码或失效，Adapter 内部降级使用无头浏览器（如 Playwright）抓取，作为兜底方案。
---
## 6. UI 交互流程设计
### 6.1 批量处理流程
1.  **拖拽上传**：支持拖拽 Excel/CSV 到界面虚线框。
2.  **预览与配置**：
    *   界面显示前 20 行数据预览。
    *   配置区：`搜索列` 下拉框选择（如“Col C - 器件型号”）。
    *   配置区：`勾选商城` 复选框（☑立创 ☑华秋 ☐一博）。
    *   配置区：`需要回填的字段` 复选框（☑库存 ☑单价 ☐描述）。
3.  **执行与进度**：点击“开始匹配”，显示进度条（`0/350 已完成`）和滚动日志。
4.  **完成导出**：按钮变为“下载完善后的BOM”，点击保存至本地。
### 6.2 单次查询流程
*   输入框输入型号或SKU。
*   多选框勾选目标商城。
*   点击查询，各商城结果以多列卡片形式展示价格、库存及购买链接。
---
## 7. 开发阶段与里程碑规划
| 阶段                     | 周期      | 核心任务                                                     | 里程碑                                                       |
| :----------------------- | :-------- | :----------------------------------------------------------- | :----------------------------------------------------------- |
| **阶段一：架构搭建**     | 第1-2周   | 确定各商城抓取字段；调研立创/华秋API接口；搭建项目骨架与 `BaseAdapter` 接口。 | 输出接口设计文档，完成项目基础框架。                         |
| **阶段二：采集层开发**   | 第3-5周   | 开发立创、华秋、一博 Adapter；编写型号清洗逻辑；实现SQLite本地缓存。 | 命令行输入单个型号，成功返回多平台标准JSON数据。             |
| **阶段三：文件解析回填** | 第6-7周   | 实现Excel/CSV动态表头读取；实现openpyxl原格式回填与另存机制。 | 给定测试Excel和模拟数据，完美输出格式不变且数据已填入的新表。 |
| **阶段四：UI与业务串联** | 第8-10周  | 开发PySide6主界面；串联批量处理与单次查询流程；接入异步任务调度器。 | MVP版本打包完成，可内部端到端试用。                          |
| **阶段五：测试与发布**   | 第11-12周 | 500+行BOM表压测；优化并发限速策略；异常处理与UI标红提示；打包发布。 | V1.0 正式版发布。                                            |
---
## 8. 风险与对策
| 风险点               | 影响评估               | 应对策略                                                     |
| :------------------- | :--------------------- | :----------------------------------------------------------- |
| **IP被封禁**         | 导致抓取失败，软件卡死 | 1. 内置请求频率限制（如5次/秒）。<br>2. 支持用户在设置中配置代理IP池。<br>3. 失败自动重试与降级处理。 |
| **商城网页改版**     | Adapter失效，抓取报错  | 1. 优先使用官方API或稳定的JSON接口。<br>2. 爬虫选择器配置化，改版时只需更新配置。 |
| **多平台数据不统一** | 回填时数据错乱         | 1. 在 Adapter 层强制进行数据标准化，统一单位（如价格统一为“元”，库存统一为“个”）。 |
| **原格式破坏**       | 用户体验极差           | 1. 坚决使用 `openpyxl` 原生加载修改机制，禁用 `pandas` 直接覆盖写入。 |
## 9. 后续迭代规划 (V2.0+)
*   **BOM成本估算**：根据输入数量自动匹配阶梯价，计算总成本，支持多平台比价导出。
*   **历史BOM管理**：本地保存查询记录，支持历史BOM快速复用。
*   **扩展海外平台**：接入 Mouser、DigiKey 等海外大型分销商。
*   **SaaS版本**：提供云端版本，支持团队协作与BOM云端共享。