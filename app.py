import os
import sys
import json
import zlib
import re
import psycopg2
import keyboard
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QTreeView, QTextEdit, QPlainTextEdit, QPushButton, QSplitter, 
    QLabel, QStatusBar
)
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QFont
from PyQt6.QtCore import Qt, QThread, pyqtSignal

# Импортируем синтаксический подсвечиватель BSL
from bsl_highlighter import BSLHighlighter
from ocr_capturer import OcrErrorCapturer

class ConfigLoader:
    """Загрузчик параметров из единого config.json проекта"""
    @staticmethod
    def load(config_path="config.json"):
        if not os.path.exists(config_path):
            return {
                "db_ai": {
                    "host": "172.16.30.204", "dbname": "1C_AI_Database",
                    "user": "postgres", "password": "", "port": 5432
                },
                "local_llm": {"api_url": "http://172.21.0"}
            }
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

class TreeLoaderWorker(QThread):
    """Фоновый поток сбора каноничной структуры метаданных из СУБД"""
    progress_signal = pyqtSignal(int)
    chunk_received_signal = pyqtSignal(list)
    finished_signal = pyqtSignal()
    error_signal = pyqtSignal(str)

    def __init__(self, db_config):
        super().__init__()
        self.db_config = db_config

    def run(self):
        query_modules = """
            SELECT 
                obj.object_type AS parent_type,
                obj.internal_name AS object_sys_name,
                COALESCE(obj.synonym, obj.internal_name) AS object_rus_name,
                CASE 
                    WHEN src.sub_type ILIKE '%Менеджер%' OR src.object_name LIKE '%.1' THEN 'Модуль менеджера'
                    ELSE 'Модуль объекта'
                END AS module_type_clean,
                src.id AS file_db_id
            FROM ai_metadata_source_codes src
            INNER JOIN ai_metadata_objects obj ON LOWER(TRIM(src.resolved_object_id::text)) = LOWER(TRIM(obj.object_id::text))
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
                    chunk.append((parent_type, object_sys_name, object_rus_name, module_type_clean, file_db_id))
                    if len(chunk) >= 200:
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
        
        # Нативный синтаксический подсвечиватель
        self.highlighter = BSLHighlighter(self.document())
        
        self.xml_pattern = re.compile(r'<([^>]+)>', re.DOTALL)
        self.meta_header_pattern = re.compile(r'\{7fffffff,.*?\}', re.DOTALL)

    def set_clean_bsl_text(self, raw_text: str):
        if not raw_text:
            self.setPlainText("")
            return
        cleaned_text = raw_text.replace('\x00', '')
        
        # Заменитель: считает переносы строк и возвращает столько же пустых строк
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

        config_data = ConfigLoader.load("config.json")
        self.db_ai_config = config_data.get("db_ai", {})

        # Инициализация OCR-модуля и хоткея
        self.ocr_capturer = OcrErrorCapturer(config_path="config.json")
        try:
            keyboard.add_hotkey("ctrl+shift+x", self.trigger_screen_ocr)
        except Exception as e:
            print(f"[ERROR] Не удалось зарегистрировать хоткей: {e}")

        self.tree_model = QStandardItemModel()
        self.root_nodes = {}
        self.object_nodes = {}

        self.tree_view = None
        self.code_editor = None
        self.error_chat_panel = None
        self.custom_query_input = None
        self.ai_output_panel = None
        self.terminal_console = None

        self._init_ui()

    def _init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        global_layout = QVBoxLayout(main_widget)
        global_layout.setContentsMargins(5, 5, 5, 5)

        # Главный вертикальный сплиттер (Верхняя зона / Нижний терминал)
        global_vertical_splitter = QSplitter(Qt.Orientation.Vertical)

        # Верхняя рабочая область (Горизонтальный сплиттер)
        workspace_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Панель 1: Дерево метаданных 1С (Слева)
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

        # Панель 2: Центральный редактор BSL-кода
        self.code_editor = BslCodeEditor()
        workspace_splitter.addWidget(self.code_editor)

        # Панель 3: Трехблочный ИИ-Центр Аналитики (Справа)
        ai_center_widget = QWidget()
        ai_center_layout = QVBoxLayout(ai_center_widget)
        ai_center_layout.setContentsMargins(5, 0, 0, 0)

        # Блок А: Контур ошибок рантайма
        ai_center_layout.addWidget(QLabel("📸 Контур ошибок рантайма:"))
        self.error_chat_panel = QTextEdit()
        self.error_chat_panel.setPlaceholderText("Сюда упадет текст ошибки после нажатия ctrl+shift+x...")
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

        workspace_splitter.setSizes([250, 750, 400])
        global_vertical_splitter.addWidget(workspace_splitter)

        # --- СНИЗУ: СИСТЕМНЫЙ ТЕРМИНАЛ НА ВСЮ ШИРИНУ ОКНА ---
        terminal_container = QWidget()
        terminal_layout = QVBoxLayout(terminal_container)
        terminal_layout.setContentsMargins(0, 5, 0, 0)
        
        terminal_layout.addWidget(QLabel("📟 Системный терминал (Инфраструктура проекта):"))
        self.terminal_console = QTextEdit()
        self.terminal_console.setReadOnly(True)
        self.terminal_console.setStyleSheet("background-color: #121212; color: #d4d4d4; font-family: Consolas;")
        self.terminal_console.setMaximumHeight(150)
        
        terminal_layout.addWidget(self.terminal_console)
        terminal_container.setLayout(terminal_layout)
        
        global_vertical_splitter.addWidget(terminal_container)
        global_vertical_splitter.setSizes([700, 150])
        global_layout.addWidget(global_vertical_splitter)

        self.setStatusBar(QStatusBar(self))
        self.log_terminal("SUCCESS", "Геометрия интерфейса и ИИ-панели полностью обновлены.")
    def start_async_tree_loading(self):
        """Запуск фонового потока для безопасного построения дерева объектов"""
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
            lambda count: self.log_terminal("INFO", f"Отрендерено элементов дерева: {count}")
        )
        self.worker.finished_signal.connect(self._on_tree_loading_finished)
        self.worker.error_signal.connect(
            lambda err: self.log_terminal("ERROR", f"Сбой загрузки структуры: {err}")
        )
        self.worker.start()

    def _handle_tree_chunk(self, chunk: list):
        """
        Финальная интеллектуальная отрисовка дерева метаданных.
        Жестко упаковывает плоскую кашу отчетов в один узел и выводит структуру.
        """
        type_translations = {
            "Catalog": "📁 Справочники", "Document": "📁 Документы",
            "InformationRegister": "📁 Регистры сведений", "AccumulationRegister": "📁 Регистры накопления",
            "Report": "📊 Отчеты", "DataProcessor": "🛠️ Обработки", "CommonModule": "⚙️ Общие модули"
        }

        for parent_type, object_sys_name, object_rus_name, module_type_clean, file_db_id in chunk:
            if parent_type == 'SystemFiles':
                file_label = f"📄 Системный файл (ID: {file_db_id})"
                file_item = QStandardItem(file_label)
                file_item.setData(file_db_id, Qt.ItemDataRole.UserRole)
                self.service_node.appendRow(file_item)
                continue

            if parent_type not in self.root_nodes:
                display_type = type_translations.get(parent_type, f"📁 {parent_type}")
                self.root_nodes[parent_type] = QStandardItem(display_type)
                self.tree_model.appendRow(self.root_nodes[parent_type])
            current_root = self.root_nodes[parent_type]

            # Изолируем кашу регламентированных отчетов
            is_report_garbage = (
                parent_type == "Report" and 
                (len(object_rus_name) > 30 or ":" in object_rus_name or object_rus_name.strip()[:1].isdigit())
            )

            if is_report_garbage:
                object_key = f"{parent_type}_COMPACT_REPORTS_ROOT"
                display_title = "📊 [Разделы и формы регламентированных отчетов]"
                module_label = f"📝 {object_rus_name}"
            else:
                object_key = f"{parent_type}_{object_sys_name}"
                display_title = f"📦 {object_rus_name}"
                module_label = f"📝 {module_type_clean}"

            if object_key not in self.object_nodes:
                obj_node = QStandardItem(display_title)
                current_root.appendRow(obj_node)
                
                props_folder = QStandardItem("📋 Реквизиты")
                props_folder.setSelectable(False)
                if not is_report_garbage:
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
                    module_item.setData(file_db_id, Qt.ItemDataRole.UserRole)
                    obj_main_node.appendRow(module_item)
                    obj_modules_cache[module_type_clean] = module_item

    def _on_tree_loading_finished(self):
        if self.service_node.rowCount() > 0:
            self.tree_model.appendRow(self.service_node)
        self.btn_load_tree.setEnabled(True)
        self.log_terminal("SUCCESS", "Иерархическая структура метаданных 1С полностью построена.")

    def _on_tree_item_clicked(self, index):
        item = self.tree_model.itemFromIndex(index)
        if not item: return
        file_id = item.data(Qt.ItemDataRole.UserRole)
        if not file_id: return

        try:
            conn = psycopg2.connect(**self.db_ai_config)
            with conn.cursor() as cursor:
                cursor.execute("SELECT bsl_code, raw_path FROM ai_metadata_source_codes WHERE id = %s;", (file_id,))
                res = cursor.fetchone()
                if res:
                    bsl_code, raw_path = res
                    self.code_editor.set_clean_bsl_text(bsl_code)
                    self.statusBar().showMessage(f"Текущий файл: {raw_path}")
                    self.log_terminal("SUCCESS", f"Модуль ID {file_id} загружен.")
            conn.close()
        except Exception as e:
            self.log_terminal("ERROR", f"Ошибка чтения кода: {e}")

    def trigger_screen_ocr(self):
        """Вызов внешнего OCR модуля захвата экрана ошибки рантайма 1С"""
        self.log_terminal("INFO", "Снят снимок активного окна. Запуск Tesseract OCR...")
        self.error_chat_panel.setPlaceholderText("Раполнительность распознавания текста ошибки...")
        
        extracted_error_text = self.ocr_capturer.capture_screen_to_text()
        
        self.error_chat_panel.setPlainText(extracted_error_text)
        self.log_terminal("SUCCESS", "Текст ошибки рантайма успешно извлечен и помещен в буфер ИИ.")

    def review_selected_bsl_code(self):
        """Интеллектуальное контекстное ИИ-ревью выделенного участка"""
        cursor = self.code_editor.textCursor()
        selected_text = cursor.selectedText().strip()
        if not selected_text:
            self.log_terminal("ERROR", "Нет выделенного фрагмента кода BSL!")
            self.ai_output_panel.setPlainText("Пожалуйста, выделите участок кода мышкой.")
            return
        self.log_terminal("INFO", f"Запуск ИИ-ревью фрагмента ({len(selected_text)} симв.)...")
        self.ai_output_panel.setPlainText("Анализирую выделенный алгоритм на уязвимости и качество кода...")

    def log_terminal(self, log_type: str, message: str):
        color_map = {"INFO": "#007acc", "SUCCESS": "#4ec9b0", "ERROR": "#f44336"}
        color = color_map.get(log_type, "#d4d4d4")
        self.terminal_console.append(f'<span style="color: {color};">[{log_type}]</span> {message}')

    # Пример интеграции в класс вашего главного окна или ИИ-панели
def init_ai_connections(self):
    # Кнопка в блоке аналитики OCR-ошибок
    self.ui.btn_send_to_ai.clicked.connect(self.handle_ocr_error_analysis)
    
    # Кнопка контекстного меню или панели BslCodeEditor
    self.ui.btn_explain_selected.clicked.connect(self.handle_explain_selection)

def handle_ocr_error_analysis(self):
    """Берет текст из блока ошибок (OCR), отправляет в оркестратор и выводит в панель ответов"""
    error_text = self.ui.txt_ocr_errors.toPlainText()
    if not error_text.strip():
        self.ui.txt_ai_responses.append("⚠️ Ошибка: Поле OCR пустое. Захватите ошибку (Ctrl+Shift+X).")
        return
        
    self.ui.txt_ai_responses.append("🤖 [ИИ] Анализирую контекст ошибки...")
    
    # Вызов вашего оркестратора (запуск в потоке QThread, чтобы UI не фризился!)
    # Результат направляем в self.ui.txt_ai_responses

def handle_explain_selection(self):
    """Вырезка выделенного BSL-кода с сохранением контекста"""
    cursor = self.ui.bsl_editor.textCursor()
    selected_code = cursor.selectedText() # Обратите внимание: PyQt/PySide заменяет \n на \u2029 в selectedText()
    selected_code = selected_code.replace('\u2029', '\n')
    
    if not selected_code.strip():
        self.ui.txt_ai_responses.append("⚠️ Выделите фрагмент BSL-кода для анализа.")
        return
        
    self.ui.txt_ai_responses.append("🔬 [ИИ] Объясняю выделенный фрагмент кода...")
    # Отправка selected_code в ai_orchestrator с промптом типа "EXPLAIN_CODE"
    

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainAiIdeWindow()
    window.show()
    sys.exit(app.exec())
