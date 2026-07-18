import sys
import os
import json
import psycopg2
import requests
import re
import zlib
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QTreeWidget, QTreeWidgetItem, QTextEdit, QPushButton, QSplitter,
    QLabel, QLineEdit, QCheckBox, QFrame, QMessageBox
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont

# Импортируем параметры по умолчанию на случай сбоя config.json
from dbserver import DB_PARAMS

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("1C:AI Dock-Plugin — ИИ-IDE ERP")
        self.setMinimumSize(QSize(1200, 800))
        
        # Контекстные переменные рантайма IDE
        self.current_db_id = None
        self.db_params = {}
        self.terminal_visible = True
        
        # Настройки ИИ по умолчанию (перезаписываются из config.json)
        self.llm_config = {
            "api_url": "http://localhost:1234/v1/chat/completions",
            "model_name": "qwen2.5-coder-7b-instruct", 
            "timeout": 120
        }
        
        # СТРОГАЯ ПОСЛЕДОВАТЕЛЬНОСТЬ ИНИЦИАЛИЗАЦИИ:
        # 1. Сначала строим визуальные панели и черную консоль логов
        self.init_ui()
        
        # 2. Только после этого считываем сеть и подключаем VPN СУБД
        self.load_config_json()
        self.init_db_connection()
        
        # Инициализируем пустой каркас дерева (загрузка будет строго по кнопке)
        self.init_metadata_tree_frame()

    def load_config_json(self):
        """Динамически считывает сетевые параметры СУБД и ИИ-сервера из config.json."""
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Чтение параметров ИИ-базы данных (блок db_ai)
                    if "db_ai" in data: 
                        self.db_params = data["db_ai"]
                        
                    # Чтение параметров домашнего ИИ-сервера (блок local_llm)
                    if "local_llm" in data:
                        llm = data["local_llm"]
                        base_url = llm.get("api_url", "http://localhost:1234/v1").rstrip('/')
                        self.llm_config["api_url"] = f"{base_url}/chat/completions"
                        self.llm_config["model_name"] = llm.get("model_name", "qwen2.5-coder-7b-instruct")
                        self.llm_config["timeout"] = llm.get("timeout", 120)
                        
                self.log_message(f"Конфигурация сети успешно считана. Целевой ИИ: {self.llm_config['model_name']}", "INFO")
            except Exception as e: 
                print(f"[Предупреждение] Ошибка чтения config.json: {e}")

    def init_db_connection(self):
        """Устанавливает прямое VPN-соединение с удаленной базой данных PostgreSQL."""
        if not self.db_params: 
            self.db_params = DB_PARAMS
        try: 
            self.db_connection = psycopg2.connect(**self.db_params)
            self.log_message(f"Успешное VPN-подключение к удаленной СУБД: {self.db_params.get('host')}", "SUCCESS")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка СУБД", f"Нет связи с PostgreSQL через VPN туннель:\n{e}")
            sys.exit(1)
    def init_ui(self):
        """Создает элементы управления, нижний спойлер-терминал и строку состояния."""
        main_window_widget = QWidget()
        self.setCentralWidget(main_window_widget)
        window_layout = QVBoxLayout(main_window_widget)
        window_layout.setContentsMargins(2, 2, 2, 2)
        window_layout.setSpacing(2)

        # СТРОКА СОСТОЯНИЯ (Status Bar) в самом верху для оперативной индикации
        self.status_label = QLabel(" Инициализация системы...")
        self.status_label.setStyleSheet("background-color: #f0f0f0; border-bottom: 1px solid #ccc; padding: 2px;")
        window_layout.addWidget(self.status_label)

        # Основной вертикальный сплиттер (Разделяет верхнюю рабочую зону и нижний лог)
        vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        window_layout.addWidget(vertical_splitter)

        # Верхняя рабочая зона (Дерево, Редактор, ИИ)
        top_workspace = QWidget()
        top_layout = QHBoxLayout(top_workspace)
        top_layout.setContentsMargins(0, 0, 0, 0)
        
        m_splitter = QSplitter(Qt.Orientation.Horizontal)
        top_layout.addWidget(m_splitter)

        # ЛЕВАЯ СТОРОНА: Дерево структуры СУБД и Редактор кода BSL
        left_win = QWidget()
        left_layout = QHBoxLayout(left_win)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_splitter = QSplitter(Qt.Orientation.Horizontal)
        left_layout.addWidget(left_splitter)

        # Контейнер дерева объектов
        tree_container = QWidget()
        tree_layout = QVBoxLayout(tree_container)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderLabel("Объекты метаданных 1С:ERP")
        self.tree_widget.setMinimumWidth(300)
        self.tree_widget.itemClicked.connect(self.on_item_clicked)
        
        self.btn_refresh_tree = QPushButton("🔄 Перечитать структуру из базы данных")
        self.btn_refresh_tree.clicked.connect(self.load_metadata_tree)
        
        tree_layout.addWidget(self.tree_widget)
        tree_layout.addWidget(self.btn_refresh_tree)
        left_splitter.addWidget(tree_container)

        # Контейнер редактора BSL с подключенной подсветкой синтаксиса
        ed_container = QWidget()
        ed_layout = QVBoxLayout(ed_container)
        ed_layout.setContentsMargins(0, 0, 0, 0)
        ed_layout.addWidget(QLabel("Встроенный язык (BSL):"))
        
        self.editor = QTextEdit()
        self.editor.setFont(QFont("Courier New", 10))
        self.editor.setPlainText("// Выберите модуль в дереве метаданных.")
        self.editor.setReadOnly(True)
        
        # Подключаем класс подсветки (bsl_highlighter.py)
        from bsl_highlighter import BSLHighlighter
        self.highlighter = BSLHighlighter(self.editor.document())
        
        ed_layout.addWidget(self.editor)

        self.btn_save_code = QPushButton("Сохранить изменения в 1C_AI_Database")
        self.btn_save_code.setEnabled(False)
        self.btn_save_code.clicked.connect(self.save_current_code_to_db)
        ed_layout.addWidget(self.btn_save_code)
        
        left_splitter.addWidget(ed_container)
        m_splitter.addWidget(left_win)

        # ПРАВАЯ СТОРОНА: Многострочный ИИ-чат
        ai_win = QWidget()
        ai_layout = QVBoxLayout(ai_win)
        ai_layout.setContentsMargins(5, 0, 0, 0)
        ai_layout.addWidget(QLabel(f"ИИ-Ассистент ({self.llm_config['model_name']})"))
        
        self.ai_chat_log = QTextEdit()
        self.ai_chat_log.setReadOnly(True)
        self.ai_chat_log.setPlaceholderText("Ответы ИИ-модели...")
        ai_layout.addWidget(self.ai_chat_log)

        inp_frame = QFrame()
        inp_layout = QVBoxLayout(inp_frame)
        inp_layout.setContentsMargins(0, 5, 0, 0)
        
        self.user_prompt_input = QTextEdit()
        self.user_prompt_input.setPlaceholderText("Введите вопрос или вставьте длинный лог ошибки рантайма 1С...")
        self.user_prompt_input.setMaximumHeight(80)
        
        self.btn_send_ai = QPushButton("Отправить запрос ИИ")
        self.btn_send_ai.clicked.connect(self.send_to_ai_assistant)
        
        inp_layout.addWidget(self.user_prompt_input)
        inp_layout.addWidget(self.btn_send_ai)
        ai_layout.addWidget(inp_frame)

        opt_frame = QFrame()
        opt_layout = QHBoxLayout(opt_frame)
        opt_layout.setContentsMargins(0, 2, 0, 0)
        
        self.cb_include_context = QCheckBox("Передавать текущий модуль целиком")
        self.cb_include_context.setChecked(True)
        self.btn_ocr_screenshot = QPushButton("📸 Скриншот ошибки (OCR)")
        
        opt_layout.addWidget(self.cb_include_context)
        opt_layout.addStretch()
        opt_layout.addWidget(self.btn_ocr_screenshot)
        ai_layout.addWidget(opt_frame)
        
        m_splitter.addWidget(ai_win)
        vertical_splitter.addWidget(top_workspace)

        # НИЖНЯЯ ПАНЕЛЬ: Раскрывающийся Лог Системы в стиле LM Studio
        terminal_container = QWidget()
        terminal_layout = QVBoxLayout(terminal_container)
        terminal_layout.setContentsMargins(0, 2, 0, 0)
        terminal_layout.setSpacing(2)

        terminal_header_frame = QFrame()
        header_layout = QHBoxLayout(terminal_header_frame)
        header_layout.setContentsMargins(5, 0, 5, 0)
        
        terminal_label = QLabel("📟 Консоль разработчика (Лог рантайма IDE):")
        terminal_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        
        self.btn_toggle_terminal = QPushButton("🔽 Свернуть / Развернуть консоль")
        self.btn_toggle_terminal.setMaximumWidth(200)
        self.btn_toggle_terminal.clicked.connect(self.toggle_terminal_window)
        
        header_layout.addWidget(terminal_label)
        header_layout.addStretch()
        header_layout.addWidget(self.btn_toggle_terminal)
        terminal_layout.addWidget(terminal_header_frame)

        self.terminal_output = QTextEdit()
        self.terminal_output.setReadOnly(True)
        self.terminal_output.setFont(QFont("Consolas", 9))
        self.terminal_output.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #333;")
        self.terminal_output.setPlaceholderText("Здесь будут отображаться SQL транзакции и сетевые логи...")
        terminal_layout.addWidget(self.terminal_output)
        
        vertical_splitter.addWidget(terminal_container)
        vertical_splitter.setSizes([680, 120])

    def log_message(self, text, level="INFO"):
        """Выводит отформатированное техническое сообщение в нижний терминал."""
        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        color_map = {"INFO": "#4fc1ff", "SUCCESS": "#6a9955", "WARNING": "#cca700", "ERROR": "#f44747"}
        log_color = color_map.get(level, "#d4d4d4")
        
        log_html = f"<font color='#808080'>[{current_time}]</font> <font color='{log_color}'>[{level}]</font> {text}"
        self.terminal_output.append(log_html)
        self.terminal_output.ensureCursorVisible()
        self.status_label.setText(f" Status: {text}")
        QApplication.processEvents()

    def toggle_terminal_window(self):
        """Скрытие или раскрытие окна нижнего терминала."""
        if self.terminal_visible:
            self.terminal_output.hide()
            self.btn_toggle_terminal.setText("🔼 Развернуть консоль")
            self.terminal_visible = False
        else:
            self.terminal_output.show()
            self.btn_toggle_terminal.setText("🔽 Свернуть консоль")
            self.terminal_visible = True
    # =====================================================================
    # ЛОГИКА ОТРИСОВКИ ДЕРЕВА МЕТАДАННЫХ ИЗ СУБД (БЕЗ JOIN В РАНТАЙМЕ)
    # =====================================================================
    def init_metadata_tree_frame(self):
        """Создает первичные пустые вершины Корней Основной и Расширений."""
        self.tree_widget.clear()
        self.tree_widget.setSortingEnabled(False)
        
        self.r_main = QTreeWidgetItem(self.tree_widget)
        self.r_main.setText(0, "Основная конфигурация")
        self.r_main.setData(0, 32, "root_main")
        QTreeWidgetItem(self.r_main).setText(0, "Загрузка...")

        self.r_ext = QTreeWidgetItem(self.tree_widget)
        self.r_ext.setText(0, "Расширения конфигурации")
        self.r_ext.setData(0, 32, "root_extensions")
        QTreeWidgetItem(self.r_ext).setText(0, "Загрузка...")

        try: 
            self.tree_widget.itemExpanded.disconnect(self.on_item_expanded)
        except Exception: 
            pass
        self.tree_widget.itemExpanded.connect(self.on_item_expanded)
        
        self.log_message("Среда ИИ-IDE инициализирована. Нажмите кнопку обновления под деревом.", "SUCCESS")

    def load_metadata_tree(self):
        """Принудительно перечитывает дерево метаданных из базы по кнопке."""
        self.log_message("Перезапуск каркаса дерева метаданных по требованию...", "INFO")
        self.init_metadata_tree_frame()

    def on_item_expanded(self, item):
        """Динамически подгружает объекты метаданных 1С из СУБД (Lazy Loading) по UUID."""
        if item.data(0, 32) in ["loaded_main", "loaded_ext_root", "loaded_folder"]:
            return

        node_type = item.data(0, 32)
        cursor = self.db_connection.cursor()
        item.takeChildren()

        # СЦЕНАРИЙ 1: Разворачиваем корень "Основная конфигурация"
        if node_type == "root_main":
            sql = """
                SELECT object_type FROM ai_metadata_source_codes 
                WHERE config_source = 'main' 
                GROUP BY object_type ORDER BY object_type
            """
            self.log_message("СУБД Запрос: Чтение списка канонических категорий 1С...", "INFO")
            try:
                cursor.execute(sql)
                types = cursor.fetchall()
                for (obj_type,) in types:
                    if obj_type in ["0", "root", "version", "config", "СистемныеФайлы"]: 
                        continue
                    
                    display_name = f"📁 {obj_type.split('.', 1)[-1]}" if obj_type.startswith("Общие.") else f"📁 {obj_type}"
                    
                    child = QTreeWidgetItem(item)
                    child.setText(0, display_name)
                    child.setData(0, 32, "main_type_folder")
                    child.setData(0, 34, obj_type)
                    QTreeWidgetItem(child).setText(0, "Загрузка...")
                    
                item.setData(0, 32, "loaded_main")
                self.log_message(f"Категории построены. Найдено типов метаданных: {len(types)}", "SUCCESS")
            except Exception as e: 
                self.log_message(f"Сбой SQL в Сценарии 1: {e}", "ERROR")

       
               # =====================================================================
        # СЦЕНАРИЙ 2: Разворачиваем категорию метаданных (с фильтрацией UUID мусора)
        # =====================================================================
        elif node_type == "main_type_folder":
            obj_type = item.data(0, 34)
            
            # Делаем чистый LEFT JOIN: вытаскиваем только те объекты, у которых есть 
            # сопоставление со справочником ai_metadata_objects. 
            sql = """
                SELECT 
                    src.object_name as raw_name,
                    COALESCE(obj.synonym, obj.internal_name) as display_name
                FROM ai_metadata_source_codes src
                LEFT JOIN ai_metadata_objects obj ON (
                    src.object_name = obj.object_id 
                    OR src.object_name = obj.internal_name
                    OR REPLACE(src.object_name, 'Модуль_', '') = obj.object_id
                    OR REPLACE(src.object_name, 'Модуль_', '') = obj.internal_name
                )
                WHERE src.config_source = 'main' AND src.object_type = %s
                GROUP BY src.object_name, display_name 
                ORDER BY display_name NULLS LAST
            """
            self.log_message(f"СУБД Запрос: Построение списка для папки '{obj_type}'...", "INFO")
            
            try:
                cursor.execute(sql, (obj_type,))
                objects = cursor.fetchall()
                total_objects = len(objects)
                
                # Создаем специальную единую скрытую папку для системного мусора 1С внутри этой категории
                system_trash_node = None
                
                processed = 0
                for row in objects:
                    raw_name, display_name = row
                    
                    if raw_name in ["0", "root", "version", "config"]: 
                        continue
                        
                    # ЕСЛИ СУБД НЕ НАШЛА ЧЕЛОВЕЧЕСКОГО ИМЕНИ (display_name IS NULL)
                    # Значит это технический внутренний файл платформы (картинка, макет, схема)
                    if not display_name or (len(display_name) > 30 and "-" in display_name):
                        if not system_trash_node:
                            system_trash_node = QTreeWidgetItem(item)
                            system_trash_node.setText(0, "⚙️ [Служебные файлы платформы]")
                            system_trash_node.setData(0, 32, "loaded_folder") # блокируем ленивую загрузку для неё
                        
                        # Прячем мусорный UUID внутрь служебной папки, чтобы разгрузить основное дерево
                        trash_child = QTreeWidgetItem(system_trash_node)
                        trash_child.setText(0, f"Файл: {raw_name[:14]}...")
                        trash_child.setData(0, 32, "object_folder")
                        trash_child.setData(0, 34, obj_type)
                        trash_child.setData(0, 35, raw_name)
                        QTreeWidgetItem(trash_child).setText(0, "Загрузка...")
                        continue
                    
                    # ДЛЯ РЕАЛЬНЫХ ОБЪЕКТОВ (у которых есть Синоним или Имя в 1С):
                    if obj_type == "Общие.Общие модули" and not display_name.startswith("⚙️"):
                        display_name = f"⚙️ {display_name}"
                        
                    child = QTreeWidgetItem(item)
                    child.setText(0, display_name)
                    child.setData(0, 32, "object_folder")
                    child.setData(0, 34, obj_type)
                    child.setData(0, 35, raw_name)
                    QTreeWidgetItem(child).setText(0, "Загрузка...")
                    
                    processed += 1
                    if processed % 200 == 0 or processed == total_objects:
                        self.log_message(f"Рендеринг бизнес-имен: {processed} из {total_objects}...", "INFO")
                        
                item.setData(0, 32, "loaded_folder")
                self.log_message(f"Категория '{obj_type}' успешно очищена от мусора и перестроена.", "SUCCESS")
            except Exception as e:
                self.log_message(f"КРИТИЧЕСКИЙ СБОЙ SQL в Сценарии 2: {e}", "ERROR")
       
        # СЦЕНАРИЙ 3: Разворачиваем конкретный объект (Документ, Справочник и т.д.)
        elif node_type == "object_folder":
            obj_type = item.data(0, 34)
            obj_name = item.data(0, 35)
            sql = """
                SELECT id, sub_type, sub_name, module_type FROM ai_metadata_source_codes 
                WHERE config_source = 'main' AND object_type = %s AND object_name = %s
                ORDER BY sub_type NULLS FIRST, module_type
            """
            try:
                cursor.execute(sql, (obj_type, obj_name))
                modules = cursor.fetchall()
                sub_folders = {}
                for db_id, sub_type, sub_name, mod_type in modules:
                    if sub_type:
                        if sub_type not in sub_folders:
                            sf = QTreeWidgetItem(item); sf.setText(0, f"📁 {sub_type}"); sf.setData(0, 32, "loaded_folder")
                            sub_folders[sub_type] = sf
                        parent_node = sub_folders[sub_type]
                        if sub_name:
                            found = False
                            for i in range(parent_node.childCount()):
                                if parent_node.child(i).text(0) == sub_name: parent_node = parent_node.child(i); found = True; break
                            if not found:
                                sn_node = QTreeWidgetItem(parent_node); sn_node.setText(0, sub_name); sn_node.setData(0, 32, "loaded_folder")
                                parent_node = sn_node
                        m_node = QTreeWidgetItem(parent_node); m_node.setText(0, f"⚙️ {mod_type}"); m_node.setData(0, 32, "editable_bsl"); m_node.setData(0, 33, db_id)
                    else:
                        m_node = QTreeWidgetItem(item); m_node.setText(0, f"⚙️ {mod_type}"); m_node.setData(0, 32, "editable_bsl"); m_node.setData(0, 33, db_id)
                item.setData(0, 32, "loaded_folder")
            except Exception as e: self.log_message(f"Сбой SQL в Сценарии 3: {e}", "ERROR")

        # СЦЕНАРИЙ 4: Ленивая загрузка для Расширений конфигурации
        elif node_type == "root_extensions":
            sql = """
                SELECT config_source, is_active FROM ai_metadata_source_codes 
                WHERE config_source != 'main' GROUP BY config_source, is_active ORDER BY config_source
            """
            try:
                cursor.execute(sql)
                exts = cursor.fetchall()
                for src, active in exts:
                    child = QTreeWidgetItem(item)
                    child.setText(0, f"🧩 {src} " + ("[Активно]" if active else "[Отключено]"))
                    child.setData(0, 32, "ext_root_folder"); child.setData(0, 36, src)
                    QTreeWidgetItem(child).setText(0, "Загрузка...")
                item.setData(0, 32, "loaded_ext_root")
            except Exception as e: self.log_message(f"Сбой SQL в Сценарии 4: {e}", "ERROR")

        cursor.close()
        self.status_label.setText(" Готово.")
    # =====================================================================
    # ОБРАБОТЧИКИ СОБЫТИЙ КЛИКОВ И ТРАНЗАКЦИИ СУБД (ИНТЕЛЛЕКТУАЛЬНЫЙ СКАЛЬПЕЛЬ)
    # =====================================================================
    def on_item_clicked(self, item, col):
        """Точечно загружает BSL код модуля из СУБД по ID, полностью очищая от XML и 7fffffff мусора."""
        if item.data(0, 32) == "editable_bsl":
            self.current_db_id = item.data(0, 33)
            self.editor.setReadOnly(False)
            self.btn_save_code.setEnabled(True)
            
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT bsl_code FROM ai_metadata_source_codes WHERE id = %s", (self.current_db_id,))
            row = cursor.fetchone()
            cursor.close()
            
            if row:
                raw_code = str(row[0]) if isinstance(row, tuple) else str(row)
                
                # ИНТЕЛЛЕКТУАЛЬНЫЙ СКАЛЬПЕЛЬ ОЧИСТКИ: Ищем маркеры начала BSL-кода
                bsl_markers = [r"#Область", r"&НаКлиенте", r"&НаСервере", r"Процедура", r"Функция", r"Перем", r"//"]
                clean_code = raw_code
                
                first_idx = len(raw_code)
                for marker in bsl_markers:
                    match = re.search(f"(?i){marker}", raw_code)
                    if match and match.start() < first_idx:
                        # Проверяем, что это не часть XML-тега
                        if not raw_code[max(0, match.start()-20):match.start()].strip().startswith("xmlns"):
                            first_idx = match.start()
                
                # Если маркер кода найден ниже XML-схем, отсекаем весь верхний мусор
                if first_idx < len(raw_code) and first_idx > 0:
                    clean_code = raw_code[first_idx:]
                else:
                    # Резервный вариант: построчная фильтрация XML-тегов и 7fffffff
                    lines = raw_code.split('\n')
                    filtered_lines = [
                        line for line in lines 
                        if "7fffffff" not in line.lower() and not line.strip().startswith("<") and not line.strip().endswith(">")
                    ]
                    clean_code = '\n'.join(filtered_lines)
                
                self.editor.setPlainText(clean_code.strip())
                self.log_message(f"BSL-модуль [ID: {self.current_db_id}] очищен от мета-мусора и загружен.", "SUCCESS")
        else:
            self.current_db_id = None
            self.editor.clear()
            self.editor.setPlainText(f"// Узел '{item.text(0)}' отображает структуру.\n// Выберите модуль внутри.")
            self.editor.setReadOnly(True)
            self.btn_save_code.setEnabled(False)

    def save_current_code_to_db(self):
        """Безопасно сохраняет измененный BSL-код с автоматическим созданием точки отката (бэкапа)."""
        if not self.current_db_id: return
        try:
            cursor = self.db_connection.cursor()
            cursor.execute("SELECT raw_path, bsl_code, v8_structure FROM ai_metadata_source_codes WHERE id = %s", (self.current_db_id,))
            row = cursor.fetchone()
            if not row:
                self.log_message("Ошибка: модуль не найден в базе для бэкапа", "ERROR"); cursor.close(); return
                
            raw_path, old_bsl, v8_structure = row
            new_bsl = self.editor.toPlainText()
            
            # 1. ТОЧКА ОТКАТА: Сохраняем оригинальный бинарный zlib-поток
            full_old_text = f"{v8_structure}\n{old_bsl}" if v8_structure else old_bsl
            original_zlib_binary = zlib.compress(full_old_text.encode('utf-8'))
            cursor.execute("""
                INSERT INTO ai_metadata_backups (raw_path, original_binary, backup_bsl)
                VALUES (%s, %s, %s) ON CONFLICT (raw_path) DO NOTHING;
            """, (raw_path, psycopg2.Binary(original_zlib_binary), old_bsl))
            
            # 2. ВАЛИДАЦИЯ ZLIB СЖАТИЯ:
            try:
                full_new_text = f"{v8_structure}\n{new_bsl}" if v8_structure else new_bsl
                test_compress = zlib.compress(full_new_text.encode('utf-8'))
                zlib.decompress(test_compress)
            except Exception as compress_err:
                self.log_message(f"Валидация провалена: {compress_err}", "ERROR")
                QMessageBox.critical(self, "Критическая ошибка", f"Код содержит недопустимые символы для zlib:\n{compress_err}")
                cursor.close(); return

            # 3. ФИКСАЦИЯ В БАЗУ ИИ:
            cursor.execute("UPDATE ai_metadata_source_codes SET bsl_code = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s", (new_bsl, self.current_db_id))
            self.db_connection.commit(); cursor.close()
            self.log_message(f"Изменения модуля [ID: {self.current_db_id}] зафиксированы. Создана точка отката.", "SUCCESS")
            QMessageBox.information(self, "Успех", "Изменения зафиксированы! Резервная копия оригинала создана.")
        except Exception as e: self.log_message(f"Ошибка сохранения: {e}", "ERROR")

    # =====================================================================
    # ВЗАИМОДЕЙСТВИЕ С ДОМАШНИМ ИИ-СЕРВЕРОМ (RTX 2060)
    # =====================================================================
    def send_to_ai_assistant(self):
        """Формирует промт, добавляет контекст BSL-кода и шлет на домашнюю RTX 2060."""
        user_text = self.user_prompt_input.toPlainText().strip()
        if not user_text: return
        self.ai_chat_log.append(f"<b>Вы:</b> {user_text}<br>")
        self.user_prompt_input.clear()

        code = self.editor.toPlainText() if (self.cb_include_context.isChecked() and self.current_db_id) else ""
        sys_p = (
            "Ты — ИИ-архитектор и Senior-разработчик 1С (BSL). Анализируй код ERP.\n"
            "В расширениях используются смешанные директивы:\n"
            "- &Вместо(\"Имя\") - полностью заменяет оригинальный метод.\n"
            "- &Перед(\"Имя\") и &После(\"Имя\") - добавляют логику до и после.\n"
            "Отвечай кратко, на русском, используя markdown-код."
        )
        msgs = [{"role": "system", "content": sys_p}]
        if code: msgs.append({"role": "user", "content": f"Контекст BSL:\n```bsl\n{code}\n```"})
        msgs.append({"role": "user", "content": user_text})

        self.ai_chat_log.append("<i>ИИ думает...</i>")
        self.log_message(f"Отправка запроса на домашний ИИ-сервер ({self.llm_config['model_name']})...", "INFO")
        
        try:
            res = requests.post(self.llm_config["api_url"], json={"model": self.llm_config["model_name"], "messages": msgs, "temperature": 0.2}, timeout=self.llm_config["timeout"])
            c = self.ai_chat_log.textCursor(); c.movePosition(c.MoveOperation.End); c.select(c.SelectionType.LineUnderCursor); c.removeSelectedText()
            if res.status_code == 200:
                self.ai_chat_log.append(f"<b>ИИ:</b><br>{res.json()['choices']['message']['content']}<br><hr>")
                self.log_message("Ответ от домашней видеокарты успешно получен.", "SUCCESS")
            else: self.log_message(f"Ошибка API: {res.status_code}", "ERROR")
        except Exception as e: self.log_message(f"Ошибка связи с ИИ: {e}", "ERROR")
        self.ai_chat_log.ensureCursorVisible()

    def closeEvent(self, ev):
        if hasattr(self, 'db_connection') and self.db_connection: self.db_connection.close()
        ev.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
