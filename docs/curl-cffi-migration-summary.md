---
name: bom-tool-lcsc-curl-cffi-migration
description: BOM tool LCSC/HQChip adapter swapped httpx for curl_cffi to bypass Akamai anti-bot challenges
metadata: 
  node_type: memory
  type: project
  originSessionId: 45a531aa-9e8f-4308-8a00-f39775de0b07
---

# BOM 表自动完善工具 — LCSC/HQChip 适配器 curl_cffi 迁移

## 项目路径
`E:\Achieve\BOM1`

## 背景
BOM 工具读取 Excel/CSV 物料清单，批量查询电子元器件供应商（立创商城 LCSC、华秋 HQChip）的库存/价格信息。LCSC 使用 Akamai CDN 反爬，Python httpx 的 TLS 指纹与 Chrome 差异大，导致频繁触发挑战（HTTP 429 / 验证页），部分查询耗时 13-14s。

## 本次完成的工作

### 1. LCSC 适配器 (`bom_tool/adapters/lcsc_adapter.py`)
- `httpx.AsyncClient` → `curl_cffi.AsyncSession(impersonate="chrome124")`
- 移除了 `http2`、`follow_redirects`、`Limits` 等 httpx 特有参数
- `httpx.RemoteProtocolError` / `httpx.ConnectError` → `CurlError`
- `.aclose()` → `.close()`
- 保留 `timeout=15` 和 `max_retries=2`（成功率优先）

### 2. HQChip 适配器 (`bom_tool/adapters/hqchip_adapter.py`)
- 同上替换 httpx → curl_cffi
- `timeout` 从 15 → 8（华秋响应更快）
- **新增 API 兜底**：`_fetch_api_payload()` + `_api_item_to_html_item()`
  - 原因：部分型号（如 LMK1C1108PWR）在 HTML 搜索页 `SelfJson.PD` 为空
  - 兜底调用 `search.hqchip.com/search/v5/goods/detail?keyword=...`
  - 注意：需与 HTML 搜索复用同一个 `AsyncSession`（API 依赖 hqchip.com 域名的 cookie）

### 3. 性能对比（LCSC 单适配器）
| 指标 | httpx 基线 | curl_cffi |
|---|---|---|
| 总耗时 (17器件) | 94.5s | 60-75s |
| 平均每器件 | 5.6s | 3.5-4.5s |
| 慢查询 (6个) | 13-14s | 7-8s |
| 成功率 | 17/17 | 17/17 |

### 4. 双适配器并发测试（最终）
- LCSC: 17/17 success
- HQChip: 17/17 success（含 LMK1C1108PWR）
- 总耗时: 75.3s (34次查询, max_concurrent=2)

## 关键发现

### Akamai 挑战规律
- 特定型号（88E1510、ADA4841、XC7A100T、W25Q128、MAX8556ETE+T、SSP7212-ADJ）持续触发挑战
- 非 UA/Header/cookie 问题，而是 Python httpx 的 TLS 指纹（无 grease、TLS 1.3 优先级不同）
- curl_cffi 的 `impersonate="chrome124"` 能模拟 Chrome TLS 指纹，大幅减少挑战

### 已尝试但不生效的方案
- ❌ `_HTML_CLIENT_MAX_REQUESTS=1`（每次请求重建连接）：反而所有请求都触发 Akamai，因为每次都是"新面孔"无 cookie
- ❌ 首页预热 `_ensure_warmed()`：首页本身也触发 Akamai，无帮助
- ❌ 自适应退避 P2（1s/2s/4s）：Akamai 挑战才是瓶颈，不是退避等待
- ❌ 丰富浏览器头 Sec-Fetch-* 等：Akamai 不看这些

### 未完成/待办项
1. **迁移 hqchip_adapter 后未做完整 17 器件计时测试**（只做了正确性验证）
2. `bom_tool/adapters/search_link_adapter.py` 和 `mock_adapter.py` 不使用 httpx，无需修改
3. 可以考虑为 curl_cffi 加安装依赖检查（`pip install curl_cffi`）
4. 如果未来 curl_cffi 不支持更高 Chrome 版本，需更新 `impersonate` 参数

## 相关文件
- `bom_tool/adapters/lcsc_adapter.py` — 立创适配器（主力改动）
- `bom_tool/adapters/hqchip_adapter.py` — 华秋适配器（httpx 替换 + API 兜底）
- `bom_tool/adapters/registry.py` — 适配器注册工厂
- `bom_tool/core/task_manager.py` — 查询任务管理器
- `bom_tool/core/bom_pipeline.py` — 管线编排
