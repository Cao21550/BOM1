from __future__ import annotations

import asyncio
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from bom_tool.adapters.registry import SUPPLIER_CHOICES, create_adapters
from bom_tool.core.bom_pipeline import (
    DEFAULT_CACHE_PATH,
    DEFAULT_CACHE_TTL_HOURS,
    DEFAULT_OUTPUT_FIELDS,
    BomPipeline,
    BomPipelineConfig,
    BomPrecheckResult,
    write_query_task_table,
)
from bom_tool.core.file_parser import FilePreview, read_preview
from bom_tool.core.task_manager import TaskProgress
from bom_tool.db.cache_db import CacheDB
from bom_tool.models import PartResult, SearchType

SUPPLIER_STATUS_HEADERS = {
    "lcsc": ("立创商城_状态", "立创商城_查询关键字", "立创商城_错误信息"),
    "hqchip": ("华秋商城_状态", "华秋商城_查询关键字", "华秋商城_错误信息"),
    "mouser": ("贸泽_状态", "贸泽_查询关键字", "贸泽_错误信息"),
}

SEARCH_HEADER_PRIORITIES = (
    ("供应商完整型号", 100),
    ("报价型号", 95),
    ("器件型号", 90),
    ("厂家型号", 85),
    ("物料型号", 80),
    ("mpn", 75),
    ("partnumber", 70),
    ("型号", 60),
    ("商城编号", 50),
    ("立创编号", 50),
    ("sku", 45),
    ("值", 20),
    ("元件名称", 10),
)


def select_preferred_search_header(headers: list[str]) -> str | None:
    best_header: str | None = None
    best_score = -1

    for header in headers:
        normalized = _normalize_header(header)
        for pattern, score in SEARCH_HEADER_PRIORITIES:
            if _normalize_header(pattern) in normalized and score > best_score:
                best_header = header
                best_score = score
                break

    return best_header


def _normalize_header(value: str) -> str:
    return "".join(char.lower() for char in value.strip() if char not in {" ", "_", "-", "/"})


class PipelineWorker(QObject):
    progress_changed = Signal(int, int, str)
    log_message = Signal(str)
    finished = Signal(bool, str)

    def __init__(
        self,
        input_path: Path,
        output_path: Path,
        search_column: str,
        search_type: SearchType,
        sheet_name: str | None,
        max_concurrent: int,
        retry_max_concurrent: int,
        supplier_names: list[str],
        enable_cache: bool,
        cache_ttl_hours: int,
        preserve_excel_styles: bool,
        retry_failed: bool,
    ) -> None:
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.search_column = search_column
        self.search_type = search_type
        self.sheet_name = sheet_name
        self.max_concurrent = max_concurrent
        self.retry_max_concurrent = retry_max_concurrent
        self.supplier_names = supplier_names
        self.enable_cache = enable_cache
        self.cache_ttl_hours = cache_ttl_hours
        self.preserve_excel_styles = preserve_excel_styles
        self.retry_failed = retry_failed
        self._cancel_requested = False
        self._pause_requested = False
        self._last_progress_emit = 0.0

    @Slot()
    def request_cancel(self) -> None:
        self._cancel_requested = True

    @Slot(bool)
    def set_paused(self, paused: bool) -> None:
        self._pause_requested = paused

    @Slot()
    def run(self) -> None:
        try:
            result = asyncio.run(self._run_pipeline())
        except Exception as exc:
            self.finished.emit(False, f"处理失败：{type(exc).__name__}: {exc}")
            return

        self._emit_failure_examples(result.row_results)
        self.finished.emit(
            True,
            f"处理完成：{result.total_rows} 行，输出文件：{result.output_path}",
        )

    def _emit_failure_examples(self, row_results: dict[int, dict[str, object]]) -> None:
        examples: list[str] = []
        for row_number, values in row_results.items():
            for supplier_name in self.supplier_names:
                headers = SUPPLIER_STATUS_HEADERS.get(supplier_name)
                if not headers:
                    continue
                status_header, query_header, error_header = headers
                status = values.get(status_header)
                if status not in {"failed", "not_found"}:
                    continue
                query = values.get(query_header) or ""
                error = values.get(error_header) or ""
                label = SUPPLIER_CHOICES.get(supplier_name, supplier_name)
                examples.append(f"第 {row_number} 行 {label} {status}: {query} {error}".strip())
                if len(examples) >= 5:
                    break
            if len(examples) >= 5:
                break

        if examples:
            self.log_message.emit("失败/未匹配样例：")
            for example in examples:
                self.log_message.emit(example)

    async def _run_pipeline(self):
        pipeline = BomPipeline(create_adapters(self.supplier_names))
        config = BomPipelineConfig(
            input_path=self.input_path,
            output_path=self.output_path,
            search_column=self.search_column,
            search_type=self.search_type,
            output_fields=DEFAULT_OUTPUT_FIELDS,
            sheet_name=self.sheet_name,
            max_concurrent=self.max_concurrent,
            retry_max_concurrent=self.retry_max_concurrent,
            enable_cache=self.enable_cache,
            cache_ttl_hours=self.cache_ttl_hours,
            preserve_excel_styles=self.preserve_excel_styles,
            retry_failed=self.retry_failed,
        )

        async def report(progress: TaskProgress) -> None:
            while self._pause_requested and not self._cancel_requested:
                await asyncio.sleep(0.2)
            if self._cancel_requested:
                raise RuntimeError("用户已取消任务")
            now = time.monotonic()
            if progress.completed < progress.total and now - self._last_progress_emit < 0.2:
                return
            self._last_progress_emit = now
            self.progress_changed.emit(
                progress.completed,
                progress.total,
                (
                    f"行={progress.completed_rows}/{progress.total_rows} "
                    f"查询={progress.completed}/{progress.total} "
                    f"成功={progress.success} 失败={progress.failed} "
                    f"未匹配={progress.not_found} "
                    f"缓存={progress.cache_hits} 去重={progress.deduplicated}"
                ),
            )

        supplier_text = "、".join(SUPPLIER_CHOICES.get(name, name) for name in self.supplier_names)
        self.log_message.emit(f"开始访问 {supplier_text} 并回填 BOM...")
        return await pipeline.run(config, report)


class SingleQueryWorker(QObject):
    finished = Signal(bool, object, str)

    def __init__(self, keyword: str, search_type: SearchType, supplier_names: list[str]) -> None:
        super().__init__()
        self.keyword = keyword
        self.search_type = search_type
        self.supplier_names = supplier_names

    @Slot()
    def run(self) -> None:
        try:
            results = asyncio.run(self._run_query())
        except Exception as exc:
            self.finished.emit(False, [], f"查询失败：{type(exc).__name__}: {exc}")
            return
        self.finished.emit(True, results, "查询完成")

    async def _run_query(self) -> list[PartResult]:
        adapters = create_adapters(self.supplier_names)
        try:
            tasks = [
                adapter.search_by_sku(self.keyword)
                if self.search_type == SearchType.SKU
                else adapter.search_by_mpn(self.keyword)
                for adapter in adapters
            ]
            return list(await asyncio.gather(*tasks))
        finally:
            for adapter in adapters:
                close = getattr(adapter, "close", None)
                if close:
                    close_result = close()
                    if close_result:
                        await close_result


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BOM 表自动完善工具")
        self.resize(1180, 760)
        self.preview: FilePreview | None = None
        self.worker_thread: QThread | None = None
        self.worker: PipelineWorker | None = None
        self.single_thread: QThread | None = None
        self.single_worker: SingleQueryWorker | None = None
        self.setCentralWidget(self._build_tabs())

    def _build_tabs(self) -> QTabWidget:
        tabs = QTabWidget()
        tabs.addTab(self._build_batch_tab(), "批量处理")
        tabs.addTab(self._build_single_tab(), "单次查询")
        return tabs

    def _build_batch_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        file_group = QGroupBox("BOM 文件")
        file_layout = QVBoxLayout(file_group)
        file_row = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("选择 .xlsx 或 .csv BOM 文件")
        browse_button = QPushButton("选择文件")
        browse_button.clicked.connect(self.choose_file)
        read_headers_button = QPushButton("读取表头")
        read_headers_button.clicked.connect(self.load_headers_from_current_file)
        file_row.addWidget(self.input_edit, 1)
        file_row.addWidget(browse_button)
        file_row.addWidget(read_headers_button)
        file_layout.addLayout(file_row)

        output_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("输出副本路径")
        output_button = QPushButton("另存为")
        output_button.clicked.connect(self.choose_output_file)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(output_button)
        file_layout.addLayout(output_row)
        layout.addWidget(file_group)

        config_group = QGroupBox("查询配置")
        config_layout = QFormLayout(config_group)
        self.sheet_combo = QComboBox()
        self.sheet_combo.currentTextChanged.connect(self.reload_headers_for_selected_sheet)
        self.search_column_combo = QComboBox()
        self.search_type_combo = QComboBox()
        self.search_type_combo.addItem("器件型号", SearchType.MPN.value)
        self.search_type_combo.addItem("商城编号/SKU", SearchType.SKU.value)
        self.query_mode_combo = QComboBox()
        self.query_mode_combo.addItem("稳定优先（首轮并发 1）", "stable")
        self.query_mode_combo.addItem("速度优先（首轮并发 2，失败串行补跑）", "fast")
        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 4)
        self.concurrent_spin.setValue(1)
        self.cache_check = QCheckBox("启用 SQLite 查询缓存")
        self.cache_check.setChecked(True)
        cache_row = QHBoxLayout()
        cache_row.addWidget(self.cache_check)
        self.clear_cache_button = QPushButton("清空缓存")
        self.clear_cache_button.clicked.connect(self.clear_sqlite_cache)
        cache_row.addWidget(self.clear_cache_button)
        cache_row.addStretch(1)
        self.cache_ttl_spin = QSpinBox()
        self.cache_ttl_spin.setRange(1, 24 * 30)
        self.cache_ttl_spin.setValue(DEFAULT_CACHE_TTL_HOURS)
        self.cache_ttl_spin.setSuffix(" 小时")
        self.auto_retry_check = QCheckBox("自动重试失败项")
        self.auto_retry_check.setChecked(False)
        self.fast_export_check = QCheckBox("快速导出模式（不复制 Excel 数据行样式）")
        config_layout.addRow("工作表", self.sheet_combo)
        config_layout.addRow("查询关键字列", self.search_column_combo)
        config_layout.addRow("查询方式", self.search_type_combo)
        config_layout.addRow("查询模式", self.query_mode_combo)
        config_layout.addRow("并发数", self.concurrent_spin)
        config_layout.addRow("缓存", cache_row)
        config_layout.addRow("缓存有效期", self.cache_ttl_spin)
        config_layout.addRow("失败处理", self.auto_retry_check)
        config_layout.addRow("导出性能", self.fast_export_check)
        layout.addWidget(config_group)

        self.batch_supplier_checks = self._build_supplier_checks(defaults={"lcsc"})
        layout.addWidget(self.batch_supplier_checks["group"])

        self.preview_table = QTableWidget()
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.preview_table.horizontalHeader().sectionClicked.connect(
            self.select_search_column_by_table
        )
        layout.addWidget(QLabel("BOM 预览"))
        layout.addWidget(self.preview_table, 1)

        action_row = QHBoxLayout()
        self.precheck_button = QPushButton("预检查缓存")
        self.precheck_button.clicked.connect(self.precheck_cache)
        self.retry_failed_button = QPushButton("重试失败项")
        self.retry_failed_button.clicked.connect(self.retry_failed_items)
        self.start_button = QPushButton("开始回填")
        self.start_button.clicked.connect(self.start_pipeline)
        self.pause_button = QPushButton("暂停")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self.toggle_pause)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_pipeline)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        action_row.addWidget(self.precheck_button)
        action_row.addWidget(self.retry_failed_button)
        action_row.addWidget(self.start_button)
        action_row.addWidget(self.pause_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.progress_bar, 1)
        layout.addLayout(action_row)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 1)
        return page

    def _build_single_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        form_group = QGroupBox("元件查询")
        form = QFormLayout(form_group)
        self.single_keyword_edit = QLineEdit()
        self.single_keyword_edit.setPlaceholderText("输入器件型号或商城编号")
        self.single_search_type_combo = QComboBox()
        self.single_search_type_combo.addItem("器件型号", SearchType.MPN.value)
        self.single_search_type_combo.addItem("商城编号/SKU", SearchType.SKU.value)
        form.addRow("关键字", self.single_keyword_edit)
        form.addRow("查询方式", self.single_search_type_combo)
        layout.addWidget(form_group)

        self.single_supplier_checks = self._build_supplier_checks(
            defaults={"lcsc", "hqchip", "mouser"}
        )
        layout.addWidget(self.single_supplier_checks["group"])

        query_row = QHBoxLayout()
        self.single_query_button = QPushButton("查询")
        self.single_query_button.clicked.connect(self.start_single_query)
        query_row.addWidget(self.single_query_button)
        query_row.addStretch(1)
        layout.addLayout(query_row)

        self.single_result_table = QTableWidget()
        self.single_result_table.setAlternatingRowColors(True)
        self.single_result_table.setColumnCount(12)
        self.single_result_table.setHorizontalHeaderLabels(
            [
                "商城",
                "状态",
                "查询关键字",
                "型号",
                "编号",
                "品牌",
                "封装",
                "库存",
                "最小起订量",
                "单价",
                "描述",
                "商品链接",
            ]
        )
        self.single_result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.single_result_table.horizontalHeader().setStretchLastSection(True)
        self.single_result_table.cellClicked.connect(self.copy_single_result_cell)
        for index, width in enumerate((90, 90, 160, 180, 120, 120, 120, 90, 100, 90, 260, 360)):
            self.single_result_table.setColumnWidth(index, width)
        layout.addWidget(self.single_result_table, 1)
        return page

    def _build_supplier_checks(self, defaults: set[str]) -> dict[str, object]:
        group = QGroupBox("商城网站")
        row = QHBoxLayout(group)
        checks: dict[str, QCheckBox] = {}
        for name, label in SUPPLIER_CHOICES.items():
            checkbox = QCheckBox(label)
            checkbox.setChecked(name in defaults)
            checks[name] = checkbox
            row.addWidget(checkbox)
        row.addStretch(1)
        return {"group": group, "checks": checks}

    def _selected_suppliers(self, checks_bundle: dict[str, object]) -> list[str]:
        checks = checks_bundle["checks"]
        assert isinstance(checks, dict)
        selected = [name for name, checkbox in checks.items() if checkbox.isChecked()]
        return selected or ["lcsc"]

    @Slot()
    def choose_file(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "选择 BOM 文件",
            "",
            "BOM Files (*.xlsx *.csv);;All Files (*)",
        )
        if not file_name:
            return
        self.input_edit.setText(file_name)
        self.output_edit.setText(str(self._default_output_path(Path(file_name))))
        self.load_headers_from_file(Path(file_name))

    @Slot()
    def choose_output_file(self) -> None:
        start_path = self.output_edit.text().strip() or str(
            self._default_output_path(self._input_path())
        )
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "选择输出副本",
            start_path,
            "Excel Files (*.xlsx);;CSV Files (*.csv);;All Files (*)",
        )
        if file_name:
            self.output_edit.setText(file_name)

    @Slot()
    def load_headers_from_current_file(self) -> None:
        path = self._input_path()
        if not path.exists():
            self.show_error("请先选择存在的 BOM 文件")
            return
        self.load_headers_from_file(path)

    def load_headers_from_file(self, path: Path) -> None:
        try:
            sheet_name = self.sheet_combo.currentText().strip() or None
            self.preview = read_preview(path, sheet_name=sheet_name)
        except Exception as exc:
            self.show_error(f"读取表头失败：{exc}")
            return

        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        self.sheet_combo.addItems(self.preview.sheet_names or [""])
        if self.preview.active_sheet:
            self.sheet_combo.setCurrentText(self.preview.active_sheet)
        self.sheet_combo.blockSignals(False)

        self.search_column_combo.clear()
        self.search_column_combo.addItems(self.preview.headers)
        self._auto_select_search_column(self.preview.headers)
        self.populate_preview_table(self.preview)
        self.log(
            f"已读取表头：第 {self.preview.header_row} 行，{len(self.preview.headers)} 列，"
            f"工作表 {self.preview.active_sheet or '-'}"
        )

    @Slot(str)
    def reload_headers_for_selected_sheet(self, _: str) -> None:
        path = self._input_path()
        if path.exists():
            self.load_headers_from_file(path)

    def populate_preview_table(self, preview: FilePreview) -> None:
        max_columns = min(len(preview.headers), 12)
        max_rows = min(len(preview.rows), 20)
        self.preview_table.clear()
        self.preview_table.setColumnCount(max_columns)
        self.preview_table.setRowCount(max_rows)
        self.preview_table.setHorizontalHeaderLabels(preview.headers[:max_columns])
        for row_index, row in enumerate(preview.rows[:max_rows]):
            for column_index, value in enumerate(row[:max_columns]):
                item = QTableWidgetItem("" if value is None else str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.preview_table.setItem(row_index, column_index, item)

    @Slot(int)
    def select_search_column_by_table(self, column_index: int) -> None:
        if not self.preview or column_index >= len(self.preview.headers):
            return
        self.search_column_combo.setCurrentText(self.preview.headers[column_index])
        self.log(f"已选择查询关键字列：{self.preview.headers[column_index]}")

    @Slot()
    def start_pipeline(
        self,
        checked: bool = False,
        retry_failed_override: bool | None = None,
    ) -> None:
        _ = checked
        input_path = self._input_path()
        output_text = self.output_edit.text().strip()
        search_column = self.search_column_combo.currentText().strip()
        if not input_path.exists():
            self.show_error("请先选择存在的 BOM 文件")
            return
        if not output_text:
            self.show_error("请选择输出副本路径")
            return
        if not search_column:
            self.show_error("请选择查询关键字列")
            return

        self.progress_bar.setValue(0)
        self.start_button.setEnabled(False)
        self.precheck_button.setEnabled(False)
        self.retry_failed_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.clear_cache_button.setEnabled(False)
        self.pause_button.setText("暂停")

        supplier_names = self._selected_suppliers(self.batch_supplier_checks)
        query_mode = self.query_mode_combo.currentData()
        retry_max_concurrent = 1
        if "lcsc" in supplier_names and query_mode == "stable":
            max_concurrent = 1
            self.log("查询模式：稳定优先，首轮并发 1，失败项串行补跑。")
        elif "lcsc" in supplier_names and query_mode == "fast":
            max_concurrent = 2
            self.log("查询模式：速度优先，首轮并发 2，失败项串行补跑。")
        else:
            max_concurrent = self.concurrent_spin.value()

        self.worker_thread = QThread(self)
        self.worker = PipelineWorker(
            input_path=input_path,
            output_path=Path(output_text),
            search_column=search_column,
            search_type=SearchType(self.search_type_combo.currentData()),
            sheet_name=self.sheet_combo.currentText().strip() or None,
            max_concurrent=max_concurrent,
            retry_max_concurrent=retry_max_concurrent,
            supplier_names=supplier_names,
            enable_cache=self.cache_check.isChecked(),
            cache_ttl_hours=self.cache_ttl_spin.value(),
            preserve_excel_styles=not self.fast_export_check.isChecked(),
            retry_failed=(
                self.auto_retry_check.isChecked()
                if retry_failed_override is None
                else retry_failed_override
            ),
        )
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress_changed.connect(self.on_progress_changed)
        self.worker.log_message.connect(self.log)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    @Slot()
    def retry_failed_items(self) -> None:
        self.log("开始重试失败项：缓存命中的成功项会直接跳过联网。")
        self.start_pipeline(retry_failed_override=True)

    @Slot()
    def precheck_cache(self) -> None:
        input_path = self._input_path()
        output_text = self.output_edit.text().strip()
        search_column = self.search_column_combo.currentText().strip()
        if not input_path.exists():
            self.show_error("请先选择存在的 BOM 文件")
            return
        if not search_column:
            self.show_error("请选择查询关键字列")
            return

        output_path = Path(output_text) if output_text else self._default_output_path(input_path)
        task_table_path = output_path.with_name(f"{output_path.stem}_query_tasks.csv")
        supplier_names = self._selected_suppliers(self.batch_supplier_checks)
        pipeline = BomPipeline(create_adapters(supplier_names))
        config = BomPipelineConfig(
            input_path=input_path,
            output_path=output_path,
            search_column=search_column,
            search_type=SearchType(self.search_type_combo.currentData()),
            output_fields=DEFAULT_OUTPUT_FIELDS,
            sheet_name=self.sheet_combo.currentText().strip() or None,
            max_concurrent=self.concurrent_spin.value(),
            retry_max_concurrent=1,
            enable_cache=self.cache_check.isChecked(),
            cache_ttl_hours=self.cache_ttl_spin.value(),
            preserve_excel_styles=not self.fast_export_check.isChecked(),
            retry_failed=self.auto_retry_check.isChecked(),
        )

        try:
            result = pipeline.precheck(config)
            write_query_task_table(result.task_records, task_table_path)
        except Exception as exc:
            self.show_error(f"预检查失败：{type(exc).__name__}: {exc}")
            return

        self.log(self._format_precheck_result(result, task_table_path))

    @Slot()
    def start_single_query(self) -> None:
        keyword = self.single_keyword_edit.text().strip()
        if not keyword:
            self.show_error("请输入查询关键字")
            return
        self.single_query_button.setEnabled(False)
        self.single_result_table.setRowCount(0)

        self.single_thread = QThread(self)
        self.single_worker = SingleQueryWorker(
            keyword,
            SearchType(self.single_search_type_combo.currentData()),
            self._selected_suppliers(self.single_supplier_checks),
        )
        self.single_worker.moveToThread(self.single_thread)
        self.single_thread.started.connect(self.single_worker.run)
        self.single_worker.finished.connect(self.on_single_query_finished)
        self.single_worker.finished.connect(self.single_thread.quit)
        self.single_thread.finished.connect(self.single_thread.deleteLater)
        self.single_thread.start()

    @Slot(bool, object, str)
    def on_single_query_finished(self, ok: bool, results: object, message: str) -> None:
        self.single_query_button.setEnabled(True)
        if not ok:
            self.show_error(message)
            return
        assert isinstance(results, list)
        self.single_result_table.setRowCount(len(results))
        for row, result in enumerate(results):
            assert isinstance(result, PartResult)
            values = [
                SUPPLIER_CHOICES.get(result.supplier, result.supplier),
                result.status.value,
                result.query,
                result.mpn or "",
                result.sku or "",
                result.brand or "",
                result.package or "",
                "" if result.stock is None else str(result.stock),
                "" if result.moq is None else str(result.moq),
                "" if result.price_unit is None else str(result.price_unit),
                result.description or "",
                result.product_url or "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.single_result_table.setItem(row, column, item)
        self.single_worker = None
        self.single_thread = None

    @Slot(int, int)
    def copy_single_result_cell(self, row: int, column: int) -> None:
        item = self.single_result_table.item(row, column)
        if item is None:
            return
        text = item.text()
        if not text:
            return
        QApplication.clipboard().setText(text)
        header = self.single_result_table.horizontalHeaderItem(column)
        label = header.text() if header else f"第 {column + 1} 列"
        self.log(f"已复制单元格：{label}")

    @Slot(int, int, str)
    def on_progress_changed(self, completed: int, total: int, message: str) -> None:
        percent = int(completed / total * 100) if total else 0
        self.progress_bar.setValue(percent)
        self.log(message)

    @Slot(bool, str)
    def on_worker_finished(self, ok: bool, message: str) -> None:
        self.start_button.setEnabled(True)
        self.precheck_button.setEnabled(True)
        self.retry_failed_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.clear_cache_button.setEnabled(True)
        self.pause_button.setText("暂停")
        self.log(message)
        if ok:
            self.progress_bar.setValue(100)
            QMessageBox.information(self, "完成", message)
        else:
            self.show_error(message)
        self.worker = None
        self.worker_thread = None

    def _format_precheck_result(
        self,
        result: BomPrecheckResult,
        task_table_path: Path,
    ) -> str:
        hit_rate = (
            result.cached_adapter_results / result.total_adapter_results * 100
            if result.total_adapter_results
            else 0
        )
        return (
            "预检查完成："
            f"总行数={result.total_rows}，"
            f"可查询行={result.searchable_rows}，"
            f"去重后查询={result.unique_queries}，"
            f"去重节省={result.deduplicated}，"
            f"缓存命中={result.cached_adapter_results}/{result.total_adapter_results} "
            f"({hit_rate:.1f}%)，"
            f"需要联网={result.network_adapter_results}，"
            f"查询任务表={task_table_path}"
        )

    @Slot()
    def toggle_pause(self) -> None:
        if not self.worker:
            return
        paused = self.pause_button.text() == "暂停"
        self.worker.set_paused(paused)
        self.pause_button.setText("继续" if paused else "暂停")
        self.log("任务已暂停" if paused else "任务已继续")

    @Slot()
    def cancel_pipeline(self) -> None:
        if not self.worker:
            return
        self.worker.request_cancel()
        self.cancel_button.setEnabled(False)
        self.log("正在取消任务...")

    @Slot()
    def clear_sqlite_cache(self) -> None:
        if self.worker:
            self.show_error("任务运行中，请等待完成或取消后再清空缓存")
            return

        answer = QMessageBox.question(
            self,
            "清空缓存",
            f"确定要清空 SQLite 查询缓存吗？\n{DEFAULT_CACHE_PATH}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        cache = CacheDB(DEFAULT_CACHE_PATH)
        try:
            cache.clear()
        finally:
            cache.close()

        message = f"已清空 SQLite 查询缓存：{DEFAULT_CACHE_PATH}"
        self.log(message)
        QMessageBox.information(self, "清空缓存", message)

    def _auto_select_search_column(self, headers: list[str]) -> None:
        preferred = select_preferred_search_header(headers)
        if preferred:
            self.search_column_combo.setCurrentText(preferred)
            return
        if headers:
            self.search_column_combo.setCurrentIndex(0)

    def _input_path(self) -> Path:
        return Path(self.input_edit.text().strip())

    def _default_output_path(self, input_path: Path) -> Path:
        suffix = input_path.suffix or ".xlsx"
        return input_path.with_name(f"{input_path.stem}_supplier_completed{suffix}")

    def log(self, message: str) -> None:
        self.log_text.append(message)

    def show_error(self, message: str) -> None:
        QMessageBox.critical(self, "错误", message)
