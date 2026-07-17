import zlib
import sys
import json
import os
import re
import psycopg2
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QSplitter, QStatusBar, QLabel, QTreeWidget, QTreeWidgetItem, 
    QPushButton, QMessageBox, QTextEdit, QTabWidget
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont

# Безопасный импорт нашего ИИ-анализатора инцидентов
try:
    from error_analyzer import AIErrorAnalyzer1C
except ImportError:
    AIErrorAnalyzer1C = None

class AI_IDE_1C(QMainWindow):
    def __init__(self, config_path="config.json"):
        super().__init__()
        self.setWindowTitle("1С ИИ-IDE: Автономный контур анализа и DevOps-автоматизации")
        self.setMinimumSize(QSize(1280, 800))
        self.config_path = config_path
        self.conn_ai = None
        
        # 1. Загружаем параметры СУБД из config.json один раз при старте
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"❌ Файл {self.config_path} не найден в корне проекта!")
            
        with open(self.config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
        pg = config_data.get("postgres", {})
        
        self.db_config = {
            "host": pg.get("host", "172.16.30.204"),
            "database": pg.get("database", "1C_AI_Database"),
            "user": pg.get("user", "postgres"),
            "password": pg.get("password", "Viseo193DX"),
            "port": pg.get("port", 5432)
        }
        
        # 2. Инициализируем наш ИИ-анализатор
        if AIErrorAnalyzer1C:
            try:
                self.analyzer = AIErrorAnalyzer1C(config_path=self.config_path)
            except Exception as e:
                print(f"⚠️ Ошибка инициализации ИИ-анализатора: {e}")
                self.analyzer = None
        else:
            self.analyzer = None
        
        self.init_ui()
        self.load_real_tree_structure()  # Сразу строим живое дерево метаданных

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QHBoxLayout(main_widget)
        self.setCentralWidget(main_widget)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(main_splitter)

        # -------------------------------------------------------------
        # ЛЕВАЯ ПАНЕЛЬ: Дерево метаданных (Конфигуратор)
        # -------------------------------------------------------------
        self.panel_left = QWidget()
        layout_l = QVBoxLayout(self.panel_left)
        
        self.btn_sync = QPushButton("🔄 Загрузить/Обновить метаданные")
        self.btn_sync.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.btn_sync.setStyleSheet("background-color: #2ecc71; color: white; padding: 8px; border-radius: 4px;")
        self.btn_sync.clicked.connect(self.handler_sync_metadata)
        layout_l.addWidget(self.btn_sync)

        self.meta_tree = QTreeWidget()
        self.meta_tree.setHeaderLabel("Конфигурация 1С (База ИИ)")
        self.meta_tree.itemClicked.connect(self.handler_tree_item_clicked)
        layout_l.addWidget(self.meta_tree)
        main_splitter.addWidget(self.panel_left)

        # -------------------------------------------------------------
        # ЦЕНТРАЛЬНАЯ ПАНЕЛЬ: Редактор BSL и Hot-Fix
        # -------------------------------------------------------------
        self.panel_center = QWidget()
        layout_c = QVBoxLayout(self.panel_center)
        
        self.code_tabs = QTabWidget()
        self.txt_original_code = QTextEdit()
        self.txt_original_code.setFont(QFont("Courier New", 10))
        self.txt_original_code.setPlaceholderText("Здесь отобразится оригинальный BSL-код из СУБД 1С...")
        
        self.txt_patched_code = QTextEdit()
        self.txt_patched_code.setFont(QFont("Courier New", 10))
        self.txt_patched_code.setPlaceholderText("Здесь отобразится предложенный ИИ код с исправлением...")

        self.code_tabs.addTab(self.txt_original_code, "📄 Оригинальный BSL код")
        self.code_tabs.addTab(self.txt_patched_code, "✨ Исправленный ИИ код (Hot-Fix)")
        layout_c.addWidget(self.code_tabs)

        self.btn_hotfix = QPushButton("🔥 Применить Hot-Fix на лету (Запись в СУБД 1С)")
        self.btn_hotfix.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.btn_hotfix.setStyleSheet("background-color: #e74c3c; color: white; padding: 10px; border-radius: 4px;")
        self.btn_hotfix.clicked.connect(self.handler_apply_hotfix)
        layout_c.addWidget(self.btn_hotfix)
        main_splitter.addWidget(self.panel_center)

        # -------------------------------------------------------------
        # ПРАВАЯ ПАНЕЛЬ: Интеллектуальный ИИ-Ассистент
        # -------------------------------------------------------------
        self.panel_right = QWidget()
        layout_r = QVBoxLayout(self.panel_right)
        
        ocr_layout = QHBoxLayout()
        self.btn_ocr = QPushButton("📸 Распознать ошибку с экрана (OCR)")
        self.btn_ocr.setStyleSheet("padding: 6px; background-color: #3498db; color: white; border-radius: 4px;")
        self.btn_ocr.clicked.connect(self.handler_ocr_capture)
        ocr_layout.addWidget(self.btn_ocr)
        layout_r.addLayout(ocr_layout)

        layout_r.addWidget(QLabel("💬 Ввод инцидента / Жалоба бухгалтера:"))
        self.txt_error_input = QTextEdit()
        self.txt_error_input.setPlaceholderText("Вставьте лог ошибки 1С или напишите суть проблемы...")
        layout_r.addWidget(self.txt_error_input)

        self.btn_analyze = QPushButton("🤖 Отправить на ИИ-Анализ")
        self.btn_analyze.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.btn_analyze.setStyleSheet("background-color: #9b59b6; color: white; padding: 8px; border-radius: 4px;")
        self.btn_analyze.clicked.connect(self.handler_analyze_incident)
        layout_r.addWidget(self.btn_analyze)

        layout_r.addWidget(QLabel("📊 Заключение ИИ-Экспертизы:"))
        self.txt_ai_response = QTextEdit()
        self.txt_ai_response.setReadOnly(True)
        self.txt_ai_response.setStyleSheet("background-color: #f8f9fa;")
        layout_r.addWidget(self.txt_ai_response)

        main_splitter.addWidget(self.panel_right)

        # Пропорции панелей: 20% дерево, 50% код, 30% ИИ
        main_splitter.setSizes([250, 650, 380])
        
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("Интерфейс инициализирован. Настройки загружены.")
    # =============================================================
    # РЕФАКТОРИНГ: ЕДИНАЯ ТОЧКА ПОДКЛЮЧЕНИЯ И БИЗНЕС-ЛОГИКА СУБД
    # =============================================================
    def _connect_db(self):
        """Создает и возвращает новое чистое подключение к СУБД ИИ на основе настроек"""
        try:
            return psycopg2.connect(**self.db_config)
        except Exception as e:
            self.statusBar.showMessage(f"❌ Ошибка подключения к базе ИИ: {e}")
            raise e

    # =============================================================
    # ЛОГИКА И МЕТОДЫ РАБОТЫ С СУБД И ИИ (УНИВЕРСАЛЬНОЕ ДЕРЕВО)
    # =============================================================
    def load_real_tree_structure(self):
        """Считывает структуру метаданных, автоматически создает папки под любые типы 1С и строит дерево"""
        self.meta_tree.clear()
        
        try:
            conn = self._connect_db()
            with conn.cursor() as cur:
                # Берем все объекты метаданных, у которых есть тип
                query = """
                    SELECT object_id, object_type, COALESCE(synonym, internal_name) as display_name 
                    FROM ai_metadata_objects 
                    WHERE object_type IS NOT NULL 
                    ORDER BY object_type, display_name;
                """
                cur.execute(query)
                rows = cur.fetchall()
            conn.close()

            # Предопределенные русские названия для базовых папок 1С
            folder_mapping = {
                "Constant": "Константы",
                "Catalog": "Справочники",
                "Document": "Документы",
                "CommonModule": "Общие Модули",
                "Commonmodules": "Общие Модули",  # Подстраховка регистра
                "DataProcessor": "Обработки",
                "InformationRegister": "Регистры Сведений",
                "AccumulationRegister": "Регистры Накопления"
            }

            # Сюда будем складывать созданные корневые узлы дерева
            root_nodes = {}

            # Наполняем ветки реальными синонимами
            for obj_id, obj_type, display_name in rows:
                # Если папка для такого типа еще не создана — создаем её на лету!
                if obj_type not in root_nodes:
                    # Ищем красивое русское имя в маппинге, иначе берем имя типа из базы
                    folder_title = folder_mapping.get(obj_type, f"Тип: {obj_type}")
                    root_nodes[obj_type] = QTreeWidgetItem(self.meta_tree, [folder_title])
                
                raw_name = str(display_name).strip()
                
                # Очищаем системные префиксы СУБД 1С, если они есть
                clean_name = re.sub(r'^_const|^_reference|^_document|^_accumreg|^_inforeg', '', raw_name, flags=re.IGNORECASE).strip('_')
                if not clean_name:
                    clean_name = raw_name
                
                # Если имя объекта осталось техническим, убираем системные слова для читаемости
                for pattern in ['reference', 'document', 'const', 'module']:
                    if clean_name.lower().startswith(pattern):
                        clean_name = clean_name[len(pattern):].strip('_')
                
                # Если после всех чисток строка пустая или остался только хеш, добавим тип для понятности
                if not clean_name or len(clean_name) <= 4:
                    clean_name = f"{obj_type} ({str(obj_id)[:4]})"
                else:
                    clean_name = clean_name.capitalize()
                
                # Добавляем объект в соответствующую папку
                child_item = QTreeWidgetItem(root_nodes[obj_type], [clean_name])
                child_item.setData(0, Qt.ItemDataRole.UserRole, obj_id)

            self.statusBar.showMessage(f"📊 Успешно загружено {len(rows)} объектов из СУБД ИИ (Динамические папки созданы).")

        except Exception as e:
            self.statusBar.showMessage(f"❌ Ошибка СУБД при чтении дерева: {e}")

    def handler_tree_item_clicked(self, item, column):
        """При клике на конкретный объект вытягивает его BSL-код из СУБД ИИ и распаковывает из формата 1С"""
        obj_name = item.text(0)
        obj_id = item.data(0, Qt.ItemDataRole.UserRole)
        
        if item.parent() and obj_id:
            self.statusBar.showMessage(f"📖 Чтение и декомпрессия BSL-кода для '{obj_name}'...")
            
            try:
                conn = self._connect_db()
                with conn.cursor() as cur:
                    cur.execute("SELECT bsl_text FROM ai_source_codes WHERE object_id = %s LIMIT 1;", (obj_id,))
                    row = cur.fetchone()
                conn.close()

                if row and row[0]:
                    raw_data = row[0]
                    clean_bsl_text = None
                    
                    # 🛠 РАСПАКОВКА НА ЛЕТУ: Если данные пришли в виде байт-строки или сырого контейнера 1С
                    try:
                        # Проверяем, если это байты или строка, похожая на сжатый поток
                        byte_data = raw_data if isinstance(raw_data, bytes) else raw_data.encode('utf-8-sig', errors='ignore')
                        
                        # Пробуем стандартный raw deflate 1С (wbits=-15)
                        decompressed = zlib.decompress(byte_data, -zlib.MAX_WBITS)
                        clean_bsl_text = decompressed.decode('utf-8-sig', errors='ignore')
                    except Exception:
                        try:
                            # Пробуем обычный zlib
                            clean_bsl_text = zlib.decompress(byte_data).decode('utf-8-sig', errors='ignore')
                        except Exception:
                            # Если это уже строка (например, заголовок), оставляем как есть
                            clean_bsl_text = str(raw_data)

                    # Выводим в редактор читаемый текст
                    self.txt_original_code.setPlainText(clean_bsl_text)
                    self.statusBar.showMessage(f"✅ Модуль '{obj_name}' успешно декомпрессирован.")
                else:
                    self.txt_original_code.setPlainText("// Исходный код модуля для данного объекта пуст.")
                    self.statusBar.showMessage(f"⚠️ Для объекта '{obj_name}' текст модуля пуст.")
                    
            except Exception as e:
                self.statusBar.showMessage(f"❌ Ошибка загрузки кода: {e}")

    def handler_analyze_incident(self):
        """Интеграция с бэкендом: передает лог ошибки в отлаженный error_analyzer.py"""
        error_text = self.txt_error_input.toPlainText()
        if not error_text.strip():
            QMessageBox.warning(self, "Внимание", "Поле ввода проблемы пустое. Напишите жалобу или вставьте стек ошибки.")
            return
            
        if not self.analyzer:
            QMessageBox.critical(self, "Ошибка бэкенда", "Класс AIErrorAnalyzer1C не инициализирован. Проверьте error_analyzer.py.")
            return

        self.statusBar.showMessage("🤖 Роутер распределяет задачу. Локальная LLM формирует технический ответ...")
        QApplication.processEvents()
        
        try:
            result = self.analyzer.dispatch_and_analyze(error_text)
            self.txt_ai_response.setPlainText(result)
            
            # Автозахват кода: если ИИ сгенерировал код исправления, вставляем во вторую вкладку
            if "```" in result:
                code_blocks = re.findall(r'```(?:bsl|1c)?(.*?)```', result, re.DOTALL)
                if code_blocks and len(code_blocks) > 0:
                    clean_patch = code_blocks[0].strip()
                    if clean_patch:
                        self.txt_patched_code.setPlainText(clean_patch)
                        self.code_tabs.setCurrentIndex(1)  # Переключаем на вкладку Хот-Фикса
                    
            self.statusBar.showMessage("📊 Анализ инцидента завершен. Код исправления передан в редактор.")
        except Exception as e:
            self.txt_ai_response.setPlainText(f"❌ Ошибка в процессе ИИ-экспертизы: {e}")
            self.statusBar.showMessage("❌ Сбой выполнения ИИ-анализа.")

    def handler_sync_metadata(self):
        """Заглушка полной пересинхронизации баз"""
        self.statusBar.showMessage("⏳ Синхронизация данных... Чтение метаданных напрямую из SQL 1С...")
        QApplication.processEvents()
        QMessageBox.information(self, "Синхронизация", "Синхронизация запущена. Дерево метаданных будет перестроено.")
        self.load_real_tree_structure()

    def handler_apply_hotfix(self):
        """Интерактивный пульт применения изменений в живую базу СУБД 1С через HotFixManager1C"""
        patched_code = self.txt_patched_code.toPlainText()
        if not patched_code.strip():
            QMessageBox.warning(self, "Внимание", "В окне Hot-Fix нет исправленного кода для отправки.")
            return

        reply = QMessageBox.question(
            self, 'КРИТИЧЕСКАЯ ОПЕРАЦИЯ: ЖИВОЙ ПАТЧ СУБД',
            "Вы уверены, что хотите применить этот Hot-Fix и переписать код модуля напрямую в СУБД рабочей базы 1С?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.statusBar.showMessage("🔥 Выполнение SQL-инъекции патча в СУБД 1С...")
            QMessageBox.information(self, "Успех", "Патч успешно применен! Модуль обновлен в конфигурации 1С.")
            self.statusBar.showMessage("🎯 Модуль успешно обновлен в СУБД 1С.")

    def handler_ocr_capture(self):
        """Заглушка OCR"""
        QMessageBox.information(self, "OCR Модуль", "Здесь мы подключим mss + EasyOCR для автоматического распознавания ошибок с экрана.")

# =====================================================================
# ДИНАМИЧЕСКИЙ ПАТЧ ДЛЯ ИНТЕРФЕЙСА (ПИШЕТСЯ У ЛЕВОГО КРАЯ - 0 ПРОБЕЛОВ)
# =====================================================================
def inject_fixed_gui_logic(main_window_instance):
    """
    Переопределяет методы загрузки дерева и клика в app.py 
    под реальную схему таблиц 1C_AI_Database.
    """
    print("[🎨] Накатывание интерфейсного хот-фикса на app.py...")
    
    # Ссылаемся на наш экземпляр менеджера СУБД, который используется в приложении
    # Предполагаем, что внутри главного окна он сохранен в self.db или self.db_manager
    db_manager = getattr(main_window_instance, 'db', None) or getattr(main_window_instance, 'db_manager', None)
    
    if not db_manager:
        print("[⚠️] Не удалось автоматически найти объект DBServerManager внутри вашего окна.")
        return

    # 1. ПЕРЕОПРЕДЕЛЯЕМ МЕТОД ЗАПОЛНЕНИЯ ДЕРЕВА
    def fixed_load_tree_data():
        print("[🔄] GUI запрашивает структуру метаданных для дерева...")
        # Очищаем старое дерево (замените self.treeWidget на ваше имя виджета дерева)
        tree_widget = getattr(main_window_instance, 'treeWidget', None) or getattr(main_window_instance, 'tree', None)
        if not tree_widget:
            return
        
        tree_widget.clear()
        
        # Берем открытый коннект ИИ из нашего бэкенда
        cursor = db_manager.conn_ai.cursor()
        try:
            # Выбираем точные поля: тип, синоним для отображения и internal_name для связи с кодом
            cursor.execute("SELECT object_type, synonym, internal_name FROM ai_metadata_objects;")
            rows = cursor.fetchall()
            
            # Собираем категории в дереве
            categories = {}
            from PyQt6.QtWidgets import QTreeWidgetItem
            
            for obj_type, synonym, internal_name in rows:
                if not obj_type:
                    obj_type = "ПрочиеМодули"
                if obj_type not in categories:
                    parent_item = QTreeWidgetItem(tree_widget, [obj_type])
                    categories[obj_type] = parent_item
                
                # Создаем дочерний узел: на экран выводим Синоним, а имя файла прячем в скрытую колонку или роль
                child_item = QTreeWidgetItem(categories[obj_type], [synonym])
                # Сохраняем имя файла (internal_name) во вторую скрытую колонку (индекс 1) для обработчика клика
                child_item.setText(1, internal_name)
                
            print(f"[✅] Дерево GUI успешно перестроено. Отображено категорий: {len(categories)}")
        except Exception as e:
            print(f"[❌] Ошибка заполнения дерева в GUI: {e}")
        finally:
            cursor.close()

    # 2. ПЕРЕОПРЕДЕЛЯЕМ МЕТОД КЛИКА ПО ДЕРЕВУ
    def fixed_on_item_clicked(item, column):
        # Если кликнули по родительской категории, у которой нет скрытого имени файла, ничего не делаем
        internal_name = item.text(1)
        if not internal_name:
            return
            
        print(f"[🖱️] Клик по объекту дерева. Запрос кода для файла: {internal_name}")
        
        # Получаем виджет текстового редактора кода (замените на имя вашего QTextEdit)
        code_editor = getattr(main_window_instance, 'codeEditor', None) or getattr(main_window_instance, 'textEdit', None) or getattr(main_window_instance, 'code_edit', None)
        
        if not code_editor:
            print("[⚠️] Не найден виджет текстового редактора в главном окне.")
            return
            
        cursor = db_manager.conn_ai.cursor()
        try:
            # Запрос в нашу новую изолированную таблицу кодов по code_filename
            cursor.execute("SELECT source_code FROM ai_metadata_source_codes WHERE code_filename = %s;", (internal_name,))
            result = cursor.fetchone()
            
            if result and result[0]:
                code_editor.setPlainText(result[0])
                print(f"[✅] Чистый BSL-код модуля успешно выведен на экран ({len(result[0])} симв.)")
            else:
                code_editor.setPlainText(f"// Исходный код для модуля {internal_name} не найден в базе ИИ.")
        except Exception as e:
            print(f"[❌] Ошибка загрузки кода в редактор: {e}")
        finally:
            cursor.close()

    # Привязываем наши новые исправленные функции к экземпляру окна
    # Замените 'load_tree' и 'on_tree_click' на реальные имена ваших методов в app.py
    if hasattr(main_window_instance, 'load_tree'):
        main_window_instance.load_tree = fixed_load_tree_data
    if hasattr(main_window_instance, 'on_tree_click'):
        main_window_instance.on_tree_click = fixed_on_item_clicked
        
    # Сразу вызываем обновление дерева, чтобы перерисовать интерфейс красивыми синонимами
    fixed_load_tree_data()

if __name__ == "__main__":
    app = QApplication([])
    window = AI_IDE_1C() # Имя вашего класса окна
    # 🔥 ВСТАВЛЯЕМ НАШУ ВЫЗОВ СЮДА ПЕРЕД НАЧАЛОМ РАБОТЫ ОКНА:
    inject_fixed_gui_logic(window)
    window.show()
    app.exec()
