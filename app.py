import sys
import os
import json
from bsl_highlighter import BSLHighlighter
import requests
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QTreeWidget, QTreeWidgetItem, QTextEdit, QPushButton, QSplitter,
    QLabel, QLineEdit, QCheckBox, QFrame
)
from PyQt6.QtCore import Qt
from dbserver import DBServerManager

class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("1C:AI Hot-Fix Console (ERP Edition)")
        self.setGeometry(100, 100, 1400, 800)
        
        # Инициализируем наш проверенный бэкенд СУБД
        print("[🚀] Подключение бэкенда DBServerManager к GUI...")
        self.db = DBServerManager()
        
        # Главный контейнер и разметка
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        
        # Создаем адаптивный трехпанельный сплиттер
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter)
        
        # Инициализируем панели интерфейса
        self.init_left_panel()
        self.init_center_panel()
        self.init_right_panel()
        
        # Первичное заполнение дерева объектов из базы ИИ
        self.load_metadata_tree()

    def init_left_panel(self):
        """Левая панель: дерево объектов метаданных"""
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        # Кнопка принудительной синхронизации
        self.btn_sync = QPushButton("🔄 Синхронизировать метаданные")
        self.btn_sync.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold; padding: 6px;")
        self.btn_sync.clicked.connect(self.load_metadata_tree)
        left_layout.addWidget(self.btn_sync)
        
        # Виджет дерева с двумя колонками (вторая скрытая для internal_name)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Объекты конфигурации ERP", "Скрытое имя"])
        self.tree.setColumnHidden(1, True)  # Прячем технические UUID файлы от глаз
        self.tree.itemClicked.connect(self.on_tree_click)
        left_layout.addWidget(self.tree)
        
        self.splitter.addWidget(left_widget)
    def init_center_panel(self):
        """Центральная панель: Просмотр и редактирование BSL-кода"""
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        
        # Верхний тулбар управления кодом
        toolbar_layout = QHBoxLayout()
        self.btn_save_local = QPushButton("💾 Сохранить локально")
        self.btn_inject_hotfix = QPushButton("🔥 Применить Хот-Фикс в 1С")
        self.btn_inject_hotfix.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold;")
        
        toolbar_layout.addWidget(self.btn_save_local)
        toolbar_layout.addWidget(self.btn_inject_hotfix)
        center_layout.addLayout(toolbar_layout)
        
        # Основной текстовый редактор кода
        self.editor = QTextEdit()
        self.highlighter = BSLHighlighter(self.editor.document())
        self.editor.setPlaceholderText("Выберите модуль из дерева слева для отображения BSL-кода...")
        self.editor.setStyleSheet("font-family: 'Courier New'; font-size: 11pt; background-color: #ffffff;")
        center_layout.addWidget(self.editor)
        
        # Кнопка быстрой отправки выделенного фрагмента в ИИ
        self.btn_analyze_fragment = QPushButton("🔍 Отправить выделенный код на анализ ИИ")
        self.btn_analyze_fragment.clicked.connect(self.send_fragment_to_chat)
        center_layout.addWidget(self.btn_analyze_fragment)
        
        self.splitter.addWidget(center_widget)

    def load_metadata_tree(self):
        """Построение наглядного дерева в стиле Конфигуратора 1С (Класс -> Объект -> Реквизиты/Модули)"""
        print("[🔄] GUI: Построение иерархического дерева Конфигуратора...")
        self.tree.clear()
        
        cursor = self.db.conn_ai.cursor()
        try:
            # Читаем структуру объектов метаданных из базы ИИ
            cursor.execute("""
                SELECT object_type, synonym, internal_name, object_id 
                FROM ai_metadata_objects 
                WHERE internal_name IN (SELECT code_filename FROM ai_metadata_source_codes);
            """)
            rows = cursor.fetchall()
            
            root_folders = {}  # Главные ветки Конфигуратора: "Общие модули", "Документы", "Справочники"
            object_nodes = {}  # Конкретные объекты: "ЗаказКлиента", "Номенклатура"
            
            from PyQt6.QtWidgets import QTreeWidgetItem
            from PyQt6.QtGui import QFont, QColor
            
            bold_font = QFont()
            bold_font.setBold(True)
            
            # Предмаппинг типов для точного соответствия дереву Конфигуратора 1С
            type_mapping = {
                "ОбщиеМодули": "Общие модули",
                "МодулиДокументов": "Документы",
                "МодулиСправочников": "Справочники",
                "Шаблоны и макеты отчетов": "Отчеты (Макеты)",
                "ПрочиеОбъекты": "Прочие объекты"
            }
            
            for obj_type, synonym, internal_name, object_id in rows:
                # Мапим внутренний тип на красивое имя ветки Конфигуратора
                display_type = type_mapping.get(obj_type, "Общие модули")
                
                # Шаг 1: Создаем корневую папку класса 1С (например, "Документы"), если её еще нет
                if display_type not in root_folders:
                    root_item = QTreeWidgetItem(self.tree, [display_type])
                    root_item.setFont(0, bold_font)
                    root_folders[display_type] = root_item
                
                # Очищаем синоним от технических суффиксов
                clean_synonym = synonym.replace(" (Менеджер)", "").replace(" (Объект)", "").strip()
                
                # Если синоним слишком длинный (например, текст отчета Росстата), аккуратно сокращаем его для имени папки
                if len(clean_synonym) > 30:
                    import re
                    short_match = re.split(r'[,.\s:\-]', clean_synonym)
                    if short_match and len(short_match) > 2:
                        base_obj_name = " ".join(short_match[:3]).strip()
                    else:
                        base_obj_name = clean_synonym[:25] + "..."
                else:
                    base_obj_name = clean_synonym
                
                # Уникальный ключ объекта внутри его класса (например, "Документы_ЗаказКлиента")
                obj_key = f"{display_type}_{base_obj_name}"
                
                # Шаг 2: Если это объектный класс (Документы/Справочники), создаем для него отдельную подпапку
                if display_type in ["Документы", "Справочники", "Отчеты (Макеты)"]:
                    if obj_key not in object_nodes:
                        obj_item = QTreeWidgetItem(root_folders[display_type], [base_obj_name])
                        object_nodes[obj_key] = obj_item
                        
                        # 🔍 Шаг 2.1: Выводим папку "Реквизиты" внутри объекта (как в 1С)
                        if object_id:
                            attr_cursor = self.db.conn_ai.cursor()
                            try:
                                attr_cursor.execute("SELECT attribute_name, attribute_type FROM ai_cached_attributes WHERE object_uuid = %s;", (object_id,))
                                attributes = attr_cursor.fetchall()
                                if attributes:
                                    req_folder = QTreeWidgetItem(obj_item, ["Реквизиты"])
                                    req_folder.setForeground(0, QColor("#b8860b"))  # Золотисто-коричневый цвет реквизитов 1С
                                    for attr_name, attr_type in attributes:
                                        QTreeWidgetItem(req_folder, [f"🔹 {attr_name} ({attr_type})"])
                            except Exception:
                                pass
                            finally:
                                attr_cursor.close()
                    
                    # Целевой родитель для листочка модуля — папка этого объекта
                    current_parent = object_nodes[obj_key]
                else:
                    # Для общих модулей промежуточная папка объекта не нужна, кладем сразу в корень класса
                    current_parent = root_folders[display_type]
                
                # Шаг 3: Формируем имя листочка исполняемого файла
                if internal_name.endswith('.m'):
                    module_label = "📄 Модуль менеджера"
                elif display_type == "Отчеты (Макеты)":
                    module_label = f"📊 Макет: {synonym[:30]}..." if len(synonym) > 30 else f"📊 Макет: {synonym}"
                else:
                    module_label = "📄 Модуль объекта"
                    
                # Добавляем модуль в дерево
                module_item = QTreeWidgetItem(current_parent, [module_label])
                # Скрываем internal_name (uuid.0/.m) во вторую колонку для обработки клика
                module_item.setText(1, internal_name)
                
            print(f"[✅] Иерархическое дерево Конфигуратора 1С успешно построено в GUI.")
        except Exception as e:
            print(f"[❌] Ошибка дерева Конфигуратора: {e}")
        finally:
            cursor.close()

    def on_tree_click(self, item, column):
        """Обработчик клика по элементу дерева — загрузка чистого BSL-кода"""
        internal_name = item.text(1)  # Забираем скрытое имя файла из колонки 1
        if not internal_name:
            return
            
        print(f"[🖱️] Клик в GUI. Запрос BSL-кода для: {internal_name}")
        
        cursor = self.db.conn_ai.cursor()
        try:
            # Читаем чистый декомпрессированный код из нашей новой таблицы
            cursor.execute("SELECT source_code FROM ai_metadata_source_codes WHERE code_filename = %s;", (internal_name,))
            result = cursor.fetchone()
            
            if result and result[0]:
                self.editor.setPlainText(result[0])
                print(f"[✅] Код модуля {internal_name} успешно выведен в редактор.")
            else:
                self.editor.setPlainText(f"// Исходный BSL-код для модуля {internal_name} не найден в базе ИИ.")
        except Exception as e:
            print(f"[❌] Ошибка загрузки кода в редактор: {e}")
            self.editor.setPlainText(f"// Ошибка при обращении к базе ИИ: {e}")
        finally:
            cursor.close()

    def send_fragment_to_chat(self):
        """Перенос выделенного текста в поле ввода ИИ-чата"""
        selected_text = self.editor.textCursor().selectedText()
        if selected_text:
            current_prompt = self.chat_input.toPlainText()
            self.chat_input.setPlainText(f"{current_prompt}\nИсходный фрагмент кода:\n{selected_text}\n")
    def init_right_panel(self):
        """Правая панель: ИИ-пульт управления инцидентами и генератор патчей"""
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        # Информационная плашка подключения к модели
        active_model = self.db.config.get("local_llm", {}).get("model_name", "qwen2.5-coder-7b-instruct")
        model_label = QLabel(f"🤖 Локальная LLM: {active_model}")
                
        #model_label = QLabel("🤖 Локальная LLM: qwen2.5-coder-7b-instruct")
        model_label.setStyleSheet("font-weight: bold; color: #2c3e50; padding: 4px;")
        right_layout.addWidget(model_label)
        
        # Окно истории диалога с ИИ
        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        self.chat_history.setPlaceholderText("Здесь будет отображаться лог анализа и сгенерированные Хот-Фиксы...")
        self.chat_history.setStyleSheet("background-color: #f8f9fa; font-family: 'Consolas';")
        right_layout.addWidget(self.chat_history)
        
        # Поле ввода промпта/задачи для ИИ
        self.chat_input = QTextEdit()
        self.chat_input.setMaximumHeight(100)
        self.chat_input.setPlaceholderText("Опишите задачу или инцидент (например: 'Оптимизируй этот запрос' или 'Почему здесь падает расчет?')...")
        right_layout.addWidget(self.chat_input)
        
        # Кнопка отправки запроса в ИИ
        self.btn_send_ai = QPushButton("⚡ Сгенерировать Хот-Фикс / Найти ошибку")
        self.btn_send_ai.setStyleSheet("background-color: #8e44ad; color: white; font-weight: bold; padding: 8px;")
        self.btn_send_ai.clicked.connect(self.ask_local_ai)
        right_layout.addWidget(self.btn_send_ai)
        
        self.splitter.addWidget(right_widget)

    def ask_local_ai(self):
        """Отправка текущего BSL-кода и промпта разработчика в Qwen2.5-Coder по настройкам из config.json"""
        user_prompt = self.chat_input.toPlainText().strip()
        if not user_prompt:
            return
            
        current_code = self.editor.toPlainText().strip()
        
        self.chat_history.append(f"\n👤 Разработчик:\n{user_prompt}")
        self.chat_input.clear()
        QApplication.processEvents()  # Мгновенно обновляем интерфейс
        
        # 🔥 ИЗВЛЕКАЕМ НАСТРОЙКИ ИЗ CONFIG.JSON 🔥
        llm_config = self.db.config.get("local_llm", {})
        base_url = llm_config.get("api_url", "http://172.21.0.179:1234/v1").rstrip('/')
        url = f"{base_url}/chat/completions"  # Идеально склеиваем эндпоинт
        
        model_name = llm_config.get("model_name", "qwen2.5-coder-7b-instruct")
        request_timeout = llm_config.get("timeout", 120)
        
        print(f"[🤖] Запрос к ИИ: {url} | Модель: {model_name} | Таймаут: {request_timeout}с")
        
        system_content = (
            "Ты — ведущий эксперт по разработке, оптимизации и исправлению ошибок в среде 1С:Предприятие 8 (ERP). "
            "Отвечай на русском языке. Если тебя просят написать или исправить код, выдавай готовый, чистый BSL-код "
            "с комментариями, что именно изменено. Будь краток и точен."
        )
        
        user_content = f"Задача/Вопрос: {user_prompt}\n\nТекущий контекст BSL-кода модуля:\n```bsl\n{current_code}\n```"
        
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content}
            ],
            "temperature": 0.2
        }
        
        self.chat_history.append(f"\n🤖 ИИ ({model_name}): Думаю над решением инцидента...")
        QApplication.processEvents()
        
        try:
            # Отправляем запрос с таймаутом из конфига
            response = requests.post(url, json=payload, timeout=request_timeout)
            if response.status_code == 200:
                result = response.json()
                ai_reply = result['choices']['message']['content']
                self.chat_history.append(f"\n🤖 ИИ (Решение):\n{ai_reply}")
            else:
                self.chat_history.append(f"\n❌ Ошибка сервера ИИ (Код {response.status_code}): {response.text}")
        except Exception as e:
            self.chat_history.append(f"\n❌ Не удалось достучаться до ИИ-сервера по адресу {url}: {e}")
            
        self.chat_history.ensureCursorVisible()

    def closeEvent(self, event):
        """Безопасное закрытие подключений СУБД при закрытии окна крестиком"""
        print("[🔒] Закрытие GUI. Вызов деструктора подключений...")
        if hasattr(self, 'db') and self.db:
            self.db.close()
        event.accept()

# =====================================================================
# ТОЧКА ЗАПУСКА ПРИЛОЖЕНИЯ PYQT6 (СТОИТ У ЛЕВОГО КРАЯ - 0 ПРОБЕЛОВ)
# =====================================================================
if __name__ == "__main__":
    # Настройка масштабирования для экранов с высоким разрешением (4K/FullHD)
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    
    app = QApplication(sys.argv)
    
    # Запускаем наше полностью обновленное приложение
    console_app = App()
    console_app.show()
    
    sys.exit(app.exec())
