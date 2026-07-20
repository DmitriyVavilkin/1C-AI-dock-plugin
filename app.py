import os
import sys
import json
import zlib
import re
import psycopg2
import keyboard
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QTreeView, QTextEdit, QPlainTextEdit, QPushButton, QSplitter, 
    QLabel, QStatusBar
)
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QFont

# Импортируем синтаксический подсвечиватель BSL, OCR-захватчик и ИИ-оркестратор
from bsl_highlighter import BSLHighlighter
from ocr_capturer import OcrErrorCapturer
from ai_orchestrator import LocalAiOrchestrator

class AIQueryThread(QThread):
    """Поток для изоляции тяжелых запросов к LM Studio, чтобы не фризить UI"""
    response_received = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, orchestrator, prompt_type, context_text, additional_query=""):
        super().__init__()
        self.orchestrator = orchestrator
        self.prompt_type = prompt_type
        self.context_text = context_text
        self.additional_query = additional_query

    def run(self):
        try:
            response = self.orchestrator.route_and_predict(
                prompt_type=self.prompt_type,
                context=self.context_text,
                query=self.additional_query
            )
            self.response_received.emit(response)
        except Exception as e:
            self.error_occurred.emit(str(e))


class ConfigLoader:
    """Загрузчик параметров из единого config.json проекта"""
    @staticmethod
    def load(config_path="config.json"):
        if not os.path.exists(config_path):
            return {
                "db_1c": {
                    "host": "172.16.30.204", "dbname": "mpk_new_vavilkin",
                    "user": "postgres", "password": "", "port": 5432
                },
                "db_ai": {
                    "host": "172.16.30.204", "dbname": "1C_AI_Database",
                    "user": "postgres", "password": "", "port": 5432
                },
                "local_llm": {
                    "api_url": "http://172.21.0",
                    "model_name": "qwen2.5-coder-7b-instruct",
                    "timeout": 120
                },
                "hotkeys": {
                    "capture_code": "ctrl+shift+x"
                }
            }
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)


class TreeLoaderWorker(QThread):
    """Фоновый поток сбора каноничной структуры метаданных из СУБД 1С:ERP"""
    progress_signal = pyqtSignal(int)
    chunk_received_signal = pyqtSignal(list)
    finished_signal = pyqtSignal()
    error_signal = pyqtSignal(str)

    def __init__(self, db_config):
        super().__init__()
        self.db_config = db_config

    def run(self):
        
                # Обновленный скоростной запрос для TreeLoaderWorker в app.py
        query_modules = """
            SELECT 
                obj.object_type AS parent_type,
                -- Выводим синоним категории (например, Общие модули) для читаемости в UI
                COALESCE(obj.synonym, 'Служебные модули') AS object_sys_name,
                -- Выводим очищенный UUID как имя объекта метаданных
                obj.internal_name AS object_rus_name,
                CASE 
                    WHEN src.module_type = 'МодульМенеджера' THEN 'Модуль менеджера'
                    WHEN src.module_type = 'ОбщийМодуль' THEN 'Общий модуль'
                    ELSE 'Модуль объекта'
                END AS module_type_clean,
                src.id AS file_db_id
            FROM ai_metadata_source_codes src
            INNER JOIN ai_metadata_objects obj ON src.resolved_object_id = obj.object_id::uuid
            ORDER BY parent_type, object_sys_name;
        """

        try:
            conn = psycopg2.connect(**self.db_config)
            with conn.cursor() as cursor:
                cursor.execute(query_modules)
                chunk = []
                counter = 0
                for parent_type, object_sys_name, object_rus_name, module_type_clean, file_db_id in cursor:
                    counter += 1
                    chunk.append((
                        str(parent_type) if parent_type else "Unknown",
                        str(object_sys_name),
                        str(object_rus_name),
                        str(module_type_clean),
                        int(file_db_id)
                    ))
                    if len(chunk) >= 500:
                        self.chunk_received_signal.emit(chunk)
                        self.progress_signal.emit(counter)
                        chunk = []
                if chunk:
                    self.chunk_received_signal.emit(chunk)
                    self.progress_signal.emit(counter)
            conn.close()
            self.finished_signal.emit()
        except Exception as e:
            self.error_signal.emit(str(e))


class BslCodeEditor(QPlainTextEdit):
    """Текстовый редактор для 1С (BSL) с сохранением топологии строк 1С"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(QFont("Courier New", 10))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        
        self.highlighter = BSLHighlighter(self.document())
        
        self.xml_pattern = re.compile(r'<([^>]+)>', re.DOTALL)
        self.meta_header_pattern = re.compile(r'\{7fffffff,.*?\}', re.DOTALL)

    def set_clean_bsl_text(self, raw_text: str):
        if not raw_text:
            self.setPlainText("")
            return
        cleaned_text = raw_text.replace('\x00', '')
        
        def preserve_lines_replacer(match):
            return '\n' * match.group(0).count('\n')

        if "<schema" in cleaned_text or "<?xml" in cleaned_text:
            cleaned_text = self.xml_pattern.sub(preserve_lines_replacer, cleaned_text)
        cleaned_text = self.meta_header_pattern.sub(preserve_lines_replacer, cleaned_text)
        
        self.setPlainText(cleaned_text)

    def get_dirty_runtime_code(self) -> str:
        return self.toPlainText()
class MainAiIdeWindow(QMainWindow):
    """Главное окно IDE с консолью во всю ширину и трехблочным ИИ-центром"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("1C-AI-DOCK-PLUGIN (IDE FOR 1C:ERP)")
        self.resize(1400, 850)

        # Загрузка конфигурации проекта
        config_data = ConfigLoader.load("config.json")
        self.db_ai_config = config_data.get("db_ai", {})
        self.db_1c_config = config_data.get("db_1c", {})

        # Инициализация ИИ-оркестратора
        self.ai_orchestrator = LocalAiOrchestrator(config_path="config.json")
        self.ai_thread = None

        # Инициализация OCR-модуля и динамического хоткея
        self.ocr_capturer = OcrErrorCapturer(config_path="config.json")
        hotkey_str = config_data.get("hotkeys", {}).get("capture_code", "ctrl+shift+x")
        try:
            keyboard.add_hotkey(hotkey_str, self.trigger_screen_ocr)
        except Exception as e:
            print(f"[ERROR] Не удалось зарегистрировать хоткей {hotkey_str}: {e}")

        # Структурные модели дерева объектов (ЖЕСТКАЯ ПРИВЯЗКА КОНТЕКСТА ОКНА)
        self.tree_model = QStandardItemModel(self)
        self.root_nodes = {}
        self.object_nodes = {}
        self.service_node = QStandardItem("⚙️ [Служебные файлы платформы]")

        # Явные указатели на элементы интерфейса
        self.tree_view = None
        self.code_editor = None
        self.error_chat_panel = None
        self.custom_query_input = None
        self.ai_output_panel = None
        self.terminal_console = None

        self._init_ui()
        self.start_async_tree_loading() # Автозапуск построения дерева при инициализации окна
    
    def _init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        global_layout = QVBoxLayout(main_widget)
        global_layout.setContentsMargins(5, 5, 5, 5)

        # Главный вертикальный сплиттер (Верхняя рабочая зона / Нижний терминал)
        global_vertical_splitter = QSplitter(Qt.Orientation.Vertical)

        # Верхняя рабочая область (Горизонтальный сплиттер на три панели)
        workspace_splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- ПАНЕЛЬ 1: ДЕРЕВО МЕТАДАННЫХ 1С (СЛЕВА) ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        self.tree_view = QTreeView()
        self.tree_model.setHorizontalHeaderLabels(["Объекты метаданных 1С / Модули"])
        self.tree_view.setModel(self.tree_model)
        self.tree_view.clicked.connect(self._on_tree_item_clicked)
        left_layout.addWidget(self.tree_view)

        self.btn_load_tree = QPushButton("🔄 Перестроить структуру метаданных")
        self.btn_load_tree.clicked.connect(self.start_async_tree_loading)
        left_layout.addWidget(self.btn_load_tree)
        workspace_splitter.addWidget(left_widget)

        # --- ПАНЕЛЬ 2: ЦЕНТРАЛЬНЫЙ РЕДАКТОР BSL-КОДА ---
        self.code_editor = BslCodeEditor()
        workspace_splitter.addWidget(self.code_editor)

        # --- ПАНЕЛЬ 3: ТРЕХБЛОЧНЫЙ ИИ-ЦЕНТР АНАЛИТИКИ (СПРАВА) ---
        ai_center_widget = QWidget()
        ai_center_layout = QVBoxLayout(ai_center_widget)
        ai_center_layout.setContentsMargins(5, 0, 0, 0)

        # Блок А: Контур ошибок рантайма
        ai_center_layout.addWidget(QLabel("📸 Контур ошибок рантайма:"))
        self.error_chat_panel = QTextEdit()
        self.error_chat_panel.setPlaceholderText("Сюда упадет текст ошибки после нажатия хоткея...")
        self.error_chat_panel.setMaximumHeight(120)
        ai_center_layout.addWidget(self.error_chat_panel)

        btn_ocr_capture = QPushButton("📸 Скриншот ошибки (OCR)")
        btn_ocr_capture.clicked.connect(self.trigger_screen_ocr)
        ai_center_layout.addWidget(btn_ocr_capture)

        # Блок Б: Произвольные запросы и поиск зависимостей
        ai_center_layout.addWidget(QLabel("🔍 Свободный диалог / Зависимости кода:"))
        self.custom_query_input = QTextEdit()
        self.custom_query_input.setPlaceholderText("Например: В каких модулях вызывается эта процедура?...")
        self.custom_query_input.setMaximumHeight(80)
        ai_center_layout.addWidget(self.custom_query_input)

        # Управляющие кнопки ИИ-центра
        actions_layout = QHBoxLayout()
        btn_send_query = QPushButton("🚀 Отправить ИИ")
        btn_review_code = QPushButton("🔬 Объяснить выделенное")
        
        # Биндинг к методам класса
        btn_send_query.clicked.connect(self.handle_ocr_error_analysis)
        btn_review_code.clicked.connect(self.review_selected_bsl_code)
        
        actions_layout.addWidget(btn_send_query)
        actions_layout.addWidget(btn_review_code)
        ai_center_layout.addLayout(actions_layout)

        # Блок В: Окно ответов моделей ИИ
        ai_center_layout.addWidget(QLabel("🤖 Ответ ассистента Qwen / Аналитика:"))
        self.ai_output_panel = QTextEdit()
        self.ai_output_panel.setReadOnly(True)
        self.ai_output_panel.setStyleSheet("background-color: #fcfcfc; border: 1px solid #ccc;")
        ai_center_layout.addWidget(self.ai_output_panel)

        ai_center_widget.setLayout(ai_center_layout)
        workspace_splitter.addWidget(ai_center_widget)

        # ЖЕСТКАЯ ФИКСАЦИЯ ГОРИЗОНТАЛЬНЫХ РАЗМЕРОВ (300px дерево, 700px редактор, 400px ИИ)
        workspace_splitter.setSizes([300, 700, 400])
        global_vertical_splitter.addWidget(workspace_splitter)

        # --- СНИЗУ: СИСТЕМНЫЙ ТЕРМИНАЛ НА ВСЮ ШИРИНУ ОКНА ---
        terminal_container = QWidget()
        terminal_layout = QVBoxLayout(terminal_container)
        terminal_layout.setContentsMargins(0, 5, 0, 0)
        
        terminal_layout.addWidget(QLabel("📟 Системный terminal (Инфраструктура проекта):"))
        self.terminal_console = QTextEdit()
        self.terminal_console.setReadOnly(True)
        self.terminal_console.setStyleSheet("background-color: #121212; color: #d4d4d4; font-family: Consolas;")
        self.terminal_console.setMaximumHeight(150)
        
        terminal_layout.addWidget(self.terminal_console)
        terminal_container.setLayout(terminal_layout)
        
        global_vertical_splitter.addWidget(terminal_container)
        
        # ЖЕСТКАЯ ФИКСАЦИЯ ВЕРТИКАЛЬНЫХ РАЗМЕРОВ (650px рабочая область, 150px подвал)
        global_vertical_splitter.setSizes([650, 150])
        global_layout.addWidget(global_vertical_splitter)

        self.setStatusBar(QStatusBar(self))
        self.log_terminal("SUCCESS", "Геометрия интерфейса и ИИ-панели полностью обновлены.")
    def log_terminal(self, log_type: str, message: str):
        """Вывод статусных логов проекта в нижнюю консоль с вашими цветами"""
        color_map = {"INFO": "#007acc", "SUCCESS": "#4ec9b0", "ERROR": "#f44336", "WARN": "#fd971f"}
        color = color_map.get(log_type, "#d4d4d4")
        log_line = f'<span style="color: {color};">[{log_type}]</span> {message}'
        self.terminal_console.append(log_line)

    def trigger_screen_ocr(self):
        """Вызов внешнего OCR модуля захвата экрана ошибки рантайма 1С"""
        self.log_terminal("INFO", "Снят снимок активного окна. Запуск Tesseract OCR...")
        self.error_chat_panel.setPlaceholderText("Работоспособность распознавания текста ошибки...")
        try:
            extracted_error_text = self.ocr_capturer.capture_screen_to_text()
            self.error_chat_panel.setPlainText(extracted_error_text)
            self.log_terminal("SUCCESS", "Текст ошибки рантайма успешно извлечен и помещен в буфер ИИ.")
        except Exception as e:
            self.log_terminal("ERROR", f"Сбой подсистемы OCR: {str(e)}")

    def handle_ocr_error_analysis(self):
        """Кнопка '🚀 Отправить ИИ' в блоке OCR-аналитики"""
        error_text = self.error_chat_panel.toPlainText().strip()
        free_query = self.custom_query_input.toPlainText().strip()
        
        if not error_text and not free_query:
            self.ai_output_panel.setPlainText("⚠️ Ошибка: Направьте контекст (OCR-текст ошибки) или напишите свободный запрос.")
            return
            
        self.log_terminal("INFO", "Асинхронный запуск анализа контекста в Qwen2.5-Coder...")
        self.ai_output_panel.setPlainText("🤖 [ИИ-Центр] Разворачиваю контекст, анализирую дерево зависимостей...")
        
        prompt_type = "NULL_POINTER"
        if "индекс находится за пределами" in error_text.lower():
            prompt_type = "OUT_OF_BOUNDS"
        elif "блокировк" in error_text.lower() or "concurrency" in error_text.lower():
            prompt_type = "DB_CONCURRENCY"

        self.ai_thread = AIQueryThread(self.ai_orchestrator, prompt_type, error_text, free_query)
        self.ai_thread.response_received.connect(self.display_ai_response)
        self.ai_thread.error_occurred.connect(self.display_ai_error)
        self.ai_thread.start()

    def review_selected_bsl_code(self):
        """Интеллектуальное контекстное ИИ-ревью выделенного участка БСЛ-кода"""
        cursor = self.code_editor.textCursor()
        selected_text = cursor.selectedText()
        
        selected_text = selected_text.replace('\u2029', '\n').strip()
        
        if not selected_text:
            self.log_terminal("ERROR", "Нет выделенного фрагмента кода BSL!")
            self.ai_output_panel.setPlainText("⚠️ Пожалуйста, выделите участок кода мышкой.")
            return
            
        self.log_terminal("INFO", f"Запуск ИИ-ревью фрагмента ({len(selected_text)} симв.)...")
        self.ai_output_panel.setPlainText("Анализирую выделенный алгоритм на уязвимости и качество кода...")
        
        self.ai_thread = AIQueryThread(self.ai_orchestrator, "EXPLAIN_CODE", selected_text)
        self.ai_thread.response_received.connect(self.display_ai_response)
        self.ai_thread.error_occurred.connect(self.display_ai_error)
        self.ai_thread.start()

    def display_ai_response(self, text):
        """Вывод успешного ответа модели в правую панель"""
        self.ai_output_panel.clear()
        self.ai_output_panel.setPlainText(text)
        self.log_terminal("SUCCESS", "Аналитика ИИ успешно сформирована.")

    def display_ai_error(self, error_msg):
        """Вывод сетевых ошибок или сбоев парсинга"""
        self.ai_output_panel.setPlainText(f"❌ Ошибка оркестратора:\n{error_msg}")
        self.log_terminal("ERROR", f"Сбой LM Studio / Оркестратора: {error_msg}")

    def start_async_tree_loading(self):
        """Запуск фонового поока для безопасного построения дерева объектов"""
        self.log_terminal("INFO", "Запуск асинхронного сканирования таблиц метаданных...")
        self.btn_load_tree.setEnabled(False)
        self.tree_model.clear()
        self.tree_model.setHorizontalHeaderLabels(["Объекты метаданных 1С / Модули"])
        self.root_nodes.clear()
        self.object_nodes.clear()
        
        self.service_node = QStandardItem("⚙️ [Служебные файлы платформы]")

        self.worker = TreeLoaderWorker(self.db_ai_config)
        self.worker.chunk_received_signal.connect(self._handle_tree_chunk)
        self.worker.progress_signal.connect(
            lambda count: self.log_terminal("INFO", f"Считано элементов дерева: {count}")
        )
        self.worker.finished_signal.connect(self._on_tree_loading_finished)
        self.worker.error_signal.connect(
            lambda err: self.log_terminal("ERROR", f"Сбой структуры: {err}")
        )
        self.worker.start()
    def _handle_tree_chunk(self, chunk: list):
        """Потокобезопасная отрисовка дерева метаданных 1С в главном потоке окна"""
        type_translations = {
            "commonmodule": "⚙️ Общие модули",
            "catalog": "📁 Справочники",
            "document": "📄 Документы",
            "informationregister": "📋 Регистры сведений",
            "accumulationregister": "📈 Регистры накопления",
            "report": "📊 Отчеты",
            "dataprocessor": "🛠️ Обработки",
            "constant": "📌 Константы",
            "enum": "📊 Перечисления",
            "documentjournal": "📑 Журналы документов",
            "accountingregister": "🏦 Регистры бухгалтерии",
            "calculationregister": "🧮 Регистры расчета",
            "businessprocess": "🌿 Бизнес-процессы",
            "task": "📅 Задачи",
            "общиймодуль": "⚙️ Общие модули",
            "справочник": "📁 Справочники",
            "документ": "📄 Документы",
            "регистрсведений": "📋 Регистры сведений",
            "регистрнакопления": "📈 Регистры накопления"
        }

        for parent_type_raw, object_sys_name, object_rus_name, module_type_clean, file_db_id in chunk:
            parent_type_clean = parent_type_raw.strip().lower()

            if parent_type_clean in ['systemfiles', 'системныефайлы', 'system']:
                file_label = f"📄 Служебный файл платформы (ID: {file_db_id})"
                file_item = QStandardItem(file_label)
                file_item.setData(file_db_id, Qt.ItemDataRole.UserRole)
                self.service_node.appendRow(file_item)
                continue

            if parent_type_clean not in self.root_nodes:
                display_type = type_translations.get(parent_type_clean, f"📁 {parent_type_raw}")
                root_item = QStandardItem(display_type)
                root_item.setEditable(False)
                self.tree_model.appendRow(root_item)
                self.root_nodes[parent_type_clean] = root_item
                
            current_root = self.root_nodes[parent_type_clean]

            is_report_garbage = (
                parent_type_clean == "report" and 
                (len(object_rus_name) > 35 or ":" in object_rus_name or object_rus_name.strip()[:1].isdigit())
            )

            if is_report_garbage:
                object_key = f"{parent_type_clean}_COMPACT_REPORTS_ROOT"
                display_title = "📊 [Разделы и формы регламентированных отчетов ERP]"
                module_label = f"📝 {object_rus_name}"
            else:
                object_key = f"{parent_type_clean}_{object_sys_name}"
                display_title = f"📦 {object_rus_name} ({object_sys_name})"
                module_label = f"📝 {module_type_clean}"

            if object_key not in self.object_nodes:
                obj_node = QStandardItem(display_title)
                obj_node.setEditable(False)
                current_root.appendRow(obj_node)
                
                if not is_report_garbage:
                    props_folder = QStandardItem("📋 Реквизиты (Только чтение)")
                    props_folder.setSelectable(False)
                    obj_node.appendRow(props_folder)
                
                self.object_nodes[object_key] = {"main_node": obj_node, "modules": {}}
            
            cached_obj = self.object_nodes[object_key]
            obj_main_node = cached_obj["main_node"]
            obj_modules_cache = cached_obj["modules"]

            if is_report_garbage:
                report_item = QStandardItem(module_label)
                report_item.setData(file_db_id, Qt.ItemDataRole.UserRole)
                obj_main_node.appendRow(report_item)
            else:
                if module_type_clean not in obj_modules_cache:
                    module_item = QStandardItem(module_label)
                    module_item.setEditable(False)
                    module_item.setData(file_db_id, Qt.ItemDataRole.UserRole)
                    obj_main_node.appendRow(module_item)
                    obj_modules_cache[module_type_clean] = module_item

        self.tree_view.update()

    def _on_tree_loading_finished(self):
        """Вызывается главным графическим потоком при завершении передачи данных"""
        if hasattr(self, 'service_node') and self.service_node.rowCount() > 0:
            self.tree_model.appendRow(self.service_node)
        
        self.tree_view.setSortingEnabled(True)
        self.tree_model.sort(0, Qt.SortOrder.AscendingOrder)
        
        # Авто-разворачивание первой папки для гарантированного рендеринга в Windows
        first_index = self.tree_model.index(0, 0)
        if first_index.isValid():
            self.tree_view.expand(first_index)
            
        self.btn_load_tree.setEnabled(True)
        self.log_terminal("SUCCESS", "Иерархическая структура метаданных 1С:Configurator успешно выведена.")

    def _on_tree_item_clicked(self, index):
        """Загрузка исходного BSL-кода выбранного модуля из PostgreSQL"""
        item = self.tree_model.itemFromIndex(index)
        if not item: return
        file_id = item.data(Qt.ItemDataRole.UserRole)
        if not file_id: return

        self.log_terminal("INFO", f"Запрос исходного кода модуля ID: {file_id}...")
        try:
            conn = psycopg2.connect(**self.db_ai_config)
            with conn.cursor() as cursor:
                cursor.execute("SELECT bsl_code, raw_path FROM ai_metadata_source_codes WHERE id = %s;", (file_id,))
                res = cursor.fetchone()
                if res:
                    bsl_code, raw_path = res
                    self.code_editor.set_clean_bsl_text(bsl_code)
                    self.statusBar().showMessage(f"Текущий файл: {raw_path}")
                    self.log_terminal("SUCCESS", f"Модуль ID {file_id} загружен. Строк: {bsl_code.count('\n') + 1}")
                else:
                    self.code_editor.setPlainText("// Исходный код модуля отсутствует в хранилище.")
            conn.close()
        except Exception as e:
            self.log_terminal("ERROR", f"Ошибка чтения кода из СУБД: {e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = MainAiIdeWindow()
    window.show()
    sys.exit(app.exec())
