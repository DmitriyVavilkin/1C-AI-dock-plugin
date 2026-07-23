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
                    "api_url": "http://172.21.0.179",
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
        # 1. Заранее объявляем эталонный структурный SQL-запрос для исключения ошибок UnboundLocalError
        query_modules = """
            SELECT 
                -- Вычисляем жесткий тип папки Конфигуратора на основе родителя или самого объекта
                COALESCE(parent_obj.object_type, obj.object_type, 'unknown') AS parent_type,
                -- UUID самого объекта (или подчиненной формы)
                obj.internal_name AS object_sys_name,
                -- Имя самого объекта (например, 'ФормаПечати' или 'АвансовыйОтчет')
                COALESCE(obj.internal_name, 'Без имени') AS object_rus_name,
                CASE 
                    WHEN src.module_type = 'МодульМенеджера' THEN 'Модуль менеджера'
                    WHEN src.module_type = 'ОбщийМодуль' THEN 'Общий модуль'
                    ELSE 'Модуль объекта'
                END AS module_type_clean,
                src.id AS file_db_id,
                -- Вытаскиваем имя родителя (владельца), если это подчиненная форма/макет
                parent_obj.internal_name AS parent_object_name
            FROM ai_metadata_source_codes src
            INNER JOIN ai_metadata_objects obj ON src.resolved_object_id = obj.object_id::uuid
            -- Безопасный каст: Принудительно приводим оба ключа к UUID при связи таблиц
            LEFT JOIN ai_metadata_objects parent_obj ON obj.parent_object_id::uuid = parent_obj.object_id::uuid
            ORDER BY parent_type, parent_object_name NULLS FIRST, object_rus_name;
        """

        # 2. Изолированный защитный контур схемы СУБД PostgreSQL
        try:
            conn_patch = psycopg2.connect(**self.db_config)
            with conn_patch.cursor() as patch_cursor:
                # Добавляем поле иерархии, если его еще нет в СУБД
                patch_cursor.execute("ALTER TABLE ai_metadata_objects ADD COLUMN IF NOT EXISTS parent_object_id UUID;")
                # На случай, если поле ранее создалось как VARCHAR, принудительно кастим структуру к UUID
                patch_cursor.execute("""
                    ALTER TABLE ai_metadata_objects 
                    ALTER COLUMN parent_object_id TYPE UUID USING parent_object_id::uuid;
                """)
                conn_patch.commit()
            conn_patch.close()
        except Exception as e:
            # Логируем в консоль разработчика, не прерывая выполнение UI-потока
            print(f"[WARN] Автопатч схемы пропустил изменение типа (поле уже в UUID или СУБД занята): {e}")

        # 3. Основной цикл вычитки чанков данных метаданных
        try:
            conn = psycopg2.connect(**self.db_config)
            with conn.cursor() as cursor:
                cursor.execute(query_modules)
                chunk = []
                counter = 0
                for parent_type, object_sys_name, object_rus_name, module_type_clean, file_db_id, parent_object_name in cursor:
                    counter += 1
                    # Передаем расширенный кортеж из 6 полей в UI-поток
                    chunk.append((
                        str(parent_type) if parent_type else "Unknown",
                        str(object_sys_name),
                        str(object_rus_name),
                        str(module_type_clean),
                        int(file_db_id),
                        str(parent_object_name) if parent_object_name else None
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
            
        # 1. Вычищаем нулевые байты
        cleaned_text = raw_text.replace('\x00', '')
        
        # 2. Агрессивный паттерн для удаления заголовков структуры 1С (включая hex-адреса и 7fffffff)
        # Находит строки вида "00000020 00000020 7fffffff" и текстовые маркеры структуры
        v8_hex_garbage_pattern = re.compile(
            r'(^[0-9a-fA-F]{8}\s+[0-9a-fA-F]{8}\s+7fffffff.*$|^\s*7fffffff,.*$)', 
            re.MULTILINE | re.IGNORECASE
        )
        cleaned_text = v8_hex_garbage_pattern.sub('', cleaned_text)
        
        # 3. Сохраняем топологию строк для XML и оставшихся мета-заголовков платформы
        def preserve_lines_replacer(match):
            return '\n' * match.group(0).count('\n')

        if "<schema" in cleaned_text or "<?xml" in cleaned_text:
            cleaned_text = self.xml_pattern.sub(preserve_lines_replacer, cleaned_text)
            
        self.setPlainText(cleaned_text.strip())

    

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
            self.error_chat_panel.setPlaceholderText("Сюда упадет текст ошибки после нажатия хоткея...")
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
        
        # Корректная обработка разделителей строк Qt (\u2029) для локальных LLM
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
            lambda count: self.log_terminal("INFO", f"Считано элементов дерева: {count}")
        )
        self.worker.finished_signal.connect(self._on_tree_loading_finished)
        self.worker.error_signal.connect(
            lambda err: self.log_terminal("ERROR", f"Сбой структуры: {err}")
        )
        self.worker.start()

    def _handle_tree_chunk(self, chunk: list):
        """Отрисовка дерева метаданных в строгом трехуровневом стиле Конфигуратора 1С"""
        type_translations = {
            "commonmodule": "⚙️ Общие модули", "catalog": "📁 Справочники",
            "document": "📄 Документы", "informationregister": "📋 Регистры сведений",
            "accumulationregister": "📈 Регистры накопления", "report": "📊 Отчеты",
            "dataprocessor": "🛠️ Обработки", "constant": "📌 Константы",
            "enum": "📊 Перечисления", "webservice": "🌐 Web-сервисы",
            "httpservice": "🛸 HTTP-сервисы", "unknown": "📦 Прочие объекты"
        }

        # Словарь для быстрого поиска созданных нод объектов верхнего уровня внутри классов
        # Структура: { "document_АвансовыйОтчет": QStandardItem }
        if not hasattr(self, 'ui_object_nodes_cache'):
            self.ui_object_nodes_cache = {}

        for parent_type_raw, object_sys_name, object_rus_name, module_type_clean, file_db_id, parent_object_name in chunk:
            parent_type_clean = parent_type_raw.strip().lower() if parent_type_raw else "unknown"

            # 1. НАХОДИМ ИЛИ СОЗДАЕМ КОРНЕВУЮ ПАПКУ КЛАССА (Уровень 1: например, "📄 Документы")
            if parent_type_clean not in self.root_nodes:
                display_type = type_translations.get(parent_type_clean, f"📁 {parent_type_raw}")
                root_item = QStandardItem(display_type)
                root_item.setEditable(False)
                self.tree_model.appendRow(root_item)
                self.root_nodes[parent_type_clean] = root_item
                
            class_root_node = self.root_nodes[parent_type_clean]

            # Определяем, к какому объекту верхнего уровня (Владельцу) относится этот модуль
            owner_object_name = parent_object_name if parent_object_name else object_rus_name
            object_cache_key = f"{parent_type_clean}_{owner_object_name}"

            # 2. НАХОДИМ ИЛИ СОЗДАЕМ САМ ОБЪЕКТ КОНФИГУРАЦИИ (Уровень 2: например, "📦 АвансовыйОтчет")
            if object_cache_key not in self.ui_object_nodes_cache:
                main_obj_node = QStandardItem(f"📦 {owner_object_name}")
                main_obj_node.setEditable(False)
                class_root_node.appendRow(main_obj_node)
                
                # Создаем контейнеры под формы/макеты и реквизиты, как в настоящей 1С
                forms_folder = QStandardItem("🖼️ Формы")
                forms_folder.setSelectable(False)
                main_obj_node.appendRow(forms_folder)
                
                props_folder = QStandardItem("📋 Реквизиты")
                props_folder.setSelectable(False)
                main_obj_node.appendRow(props_folder)
                
                self.ui_object_nodes_cache[object_cache_key] = {
                    "main_node": main_obj_node,
                    "forms_folder": forms_folder,
                    "modules_cache": {}
                }
                
            obj_cluster = self.ui_object_nodes_cache[object_cache_key]
            
            # 3. ОТРИСОВКА ПОДЧИНЕННОЙ СТРУКТУРЫ (Уровень 3: Формы, макеты и их модули)
            if parent_object_name:
                # Перед нами подчиненная печатная форма или макет! 
                # Создаем для нее красивую именованную ноду внутри папки "ФОРМЫ"
                sub_element_key = f"{object_cache_key}_{object_rus_name}"
                
                if sub_element_key not in obj_cluster["modules_cache"]:
                    sub_node = QStandardItem(f"🖼️ {object_rus_name}")
                    sub_node.setEditable(False)
                    obj_cluster["forms_folder"].appendRow(sub_node)
                    obj_cluster["modules_cache"][sub_element_key] = sub_node
                
                target_node = obj_cluster["modules_cache"][sub_element_key]
                module_label = f"📝 {module_type_clean}"
            else:
                # Это собственный модуль объекта/менеджера (лежит в корне объекта)
                target_node = obj_cluster["main_node"]
                module_label = f"📝 {module_type_clean}"

            # Кладём BSL-код строго внутрь целевой ноды
            # Проверяем, чтобы модуль не продублировался при чтении чанков
            module_exists = False
            for row in range(target_node.rowCount()):
                if target_node.child(row).text() == module_label:
                    module_exists = True
                    break
                    
            if not module_exists:
                module_item = QStandardItem(module_label)
                module_item.setEditable(False)
                module_item.setData(file_db_id, Qt.ItemDataRole.UserRole)
                target_node.appendRow(module_item)

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
