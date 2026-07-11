import sys
import os
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from core.configurator import ConfiguratorBridge
from core.router import AIRouter

class AIWorker(QThread):
    result_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, router, query, code, window_title, metadata_item=""):
        super().__init__()
        self.router = router
        self.query = query
        self.code = code
        self.window_title = window_title
        self.metadata_item = metadata_item

    def run(self):
        try:
            result = self.router.route_request(self.query, self.code, self.window_title, self.metadata_item)
            self.result_ready.emit(result)
        except Exception as e:
            self.error_occurred.emit(str(e))

class MainAIDockApp(QMainWindow):
    def __init__(self):
        super().__init__()
        print("[GUI] Запуск интерфейса 1С AI DOCK v2.0 с интерактивным деревом...")
        
        # Инфраструктура
        self.bridge = ConfiguratorBridge()
        self.router = AIRouter()
        
        # Хранилище контекста
        self.current_window_title = ""
        self.selected_metadata_string = "" # Хранит выбранный кликом реквизит
        
        # Инициализация интерфейса
        self.init_ui()
        
        # Подписка на сигналы WinAPI моста
        self.bridge.code_captured.connect(self.on_code_captured)

    def init_ui(self):
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("1C AI Dock v2.0 (IDE Mode)")
        self.resize(500, 700)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Статус-бар сверху
        self.status_label = QLabel("Статус: Ожидание...")
        self.status_label.setStyleSheet("color: #7b1fa2; font-weight: bold;")
        main_layout.addWidget(self.status_label)

        # Область захваченного BSL-кода
        main_layout.addWidget(QLabel("Контекст захваченного кода BSL:"))
        self.code_area = QTextEdit()
        self.code_area.setPlaceholderText("Здесь появится код из Конфигуратора...")
        self.code_area.setStyleSheet("background-color: #fafafa; font-family: Consolas; font-size: 10pt;")
        self.code_area.setMaximumHeight(150) # Ограничим по высоте, чтобы уступить место дереву
        main_layout.addWidget(self.code_area)

        # Графическое дерево метаданных (Вместо старого текстового поля)
        main_layout.addWidget(QLabel("🌳 Живая структура метаданных (Кликабельная):"))
        self.tree_widget = QTreeWidget()
        self.tree_widget.setColumnCount(2)
        self.tree_widget.setHeaderLabels(["Имя реквизита / Объекта", "Тип данных"])
        self.tree_widget.setStyleSheet("background-color: #ffffff; font-size: 9pt;")
        self.tree_widget.itemClicked.connect(self.on_tree_item_clicked) # Слот клика мышкой!
        main_layout.addWidget(self.tree_widget)

        # Окно ответов ИИ
        main_layout.addWidget(QLabel("🤖 Анализ и решения ИИ:"))
        self.output_area = QTextEdit()
        self.output_area.setReadOnly(True)
        self.output_area.setStyleSheet("background-color: #f4f5f7; font-family: Consolas; font-size: 10pt;")
        self.output_area.setMaximumHeight(200)
        main_layout.addWidget(self.output_area)

        # Поле ввода вопроса разработчика
        self.query_input = QLineEdit()
        self.query_input.setPlaceholderText("Введите вопрос (например: Как заполнить этот реквизит?) и кликните по дереву...")
        main_layout.addWidget(self.query_input)

        # Панель кнопок управления
        btn_layout = QHBoxLayout()
        
        self.run_btn = QPushButton("🚀 Выполнить")
        self.run_btn.setStyleSheet("background-color: #1e88e5; color: white; font-weight: bold; padding: 6px;")
        self.run_btn.clicked.connect(self.start_ai_generation)
        
        self.tree_btn = QPushButton("🔄 Загрузить Структуру")
        self.tree_btn.setStyleSheet("background-color: #7b1fa2; color: white; font-weight: bold; padding: 6px;")
        self.tree_btn.clicked.connect(self.show_metadata_tree)
        
        self.paste_btn = QPushButton("📥 Вставить в 1С")
        self.paste_btn.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px;")
        self.paste_btn.clicked.connect(self.paste_code_to_1c)
        
        btn_layout.addWidget(self.run_btn)
        btn_layout.addWidget(self.tree_btn)
        btn_layout.addWidget(self.paste_btn)
        main_layout.addLayout(btn_layout)

    def trigger_capture(self):
        self.status_label.setText("Статус: Захват кода...")
        self.bridge.capture_selected_text()
        title = self.bridge.get_active_window_title()
        self.current_window_title = title
        QApplication.clipboard().dataChanged.connect(self.read_clipboard_safely)

    def read_clipboard_safely(self):
        try:
            QApplication.clipboard().dataChanged.disconnect(self.read_clipboard_safely)
            text = QApplication.clipboard().text()
            if text:
                self.bridge.code_captured.emit(text, self.current_window_title)
        except Exception:
            pass

    def on_code_captured(self, code_text, window_title):
        self.code_area.setText(code_text)
        self.current_window_title = window_title
        self.status_label.setText(f"Статус: Код захвачен")
        self.output_area.append(f"\n[Система] Захвачен контекст окна: {window_title}")
        # Автоматически обновляем дерево при захвате нового кода
        self.show_metadata_tree()

    def show_metadata_tree(self):
        """Запрашивает данные из 1С и строит настоящее графическое дерево элементов"""
        self.tree_widget.clear()
        self.status_label.setText("Статус: Сбор метаданных из 1С...")
        
        # Получаем имя объекта из заголовка или берем то, что ввел юзер в поле ввода
        target_obj = self.query_input.text().strip()
        if not target_obj:
            target_obj = self.router.parse_object_name_from_title(self.current_window_title)
            
        # Стучимся в HTTP-сервис 1С за словарем
        raw_data = self.router.get_1c_metadata_raw(target_obj)
        
        if "error" in raw_data:
            self.status_label.setText("Статус: Ошибка сбора")
            root_item = QTreeWidgetItem(self.tree_widget, [raw_data["error"], ""])
            return

        # Строим корень дерева (Сам Объект)
        obj_name = raw_data.get("Имя", "Неопределено")
        obj_synonym = raw_data.get("Синоним", "")
        root_item = QTreeWidgetItem(self.tree_widget, [f"📦 {obj_name} ({obj_synonym})", "Объект Метаданных"])
        root_item.setExpanded(True) # Сразу разворачиваем корень
        
        # Создаем ветку Реквизиты
        req_folder = QTreeWidgetItem(root_item, ["📂 Реквизиты шапки", ""])
        req_folder.setExpanded(True)
        
        for req in raw_data.get("Реквизиты", []):
            QTreeWidgetItem(req_folder, [f"🔹 {req.get('Имя')}", req.get('Тиpt', req.get('Тип', ''))])
            
        self.status_label.setText("Статус: Графическое дерево построено")

    def on_tree_item_clicked(self, item, column):
        """СЛОТ-МАГИЯ: Вызывается автоматически при клике на элемент дерева мышкой"""
        # Если кликнули по реквизиту (у него есть тип во второй колонке и это не папка)
        item_name = item.text(0)
        item_type = item.text(1)
        
        if item_type and not item_type in ["Объект Метаданных", ""]:
            # Очищаем имя от иконки
            clean_name = item_name.replace("🔹 ", "").strip()
            self.selected_metadata_string = f"{clean_name} [{item_type}]"
            self.status_label.setText(f"Фокус ИИ на реквизите: {clean_name}")
            
            # Если в поле ввода вопроса уже что-то написано, автоматически запускаем ИИ!
            if self.query_input.text().strip():
                print(f"[GUI] Интерактивный клик по реквизиту: {clean_name}. Запуск ИИ...")
                self.start_ai_generation()

    def start_ai_generation(self):
        query = self.query_input.text().strip()
        code = self.code_area.toPlainText().strip()
        
        # Защитная заглушка: если кода нет, даем ИИ работать просто по метаданным!
        if not query:
            query = f"Сделай обзор реквизита {self.selected_metadata_string} и напиши пример работы с ним в BSL."
            
        self.status_label.setText("Статус: Домашний ИИ думает...")
        self.run_btn.setEnabled(False)
        
        # Передаем задачу в поток воркера, включая имя выбранного реквизита
        self.worker = AIWorker(self.router, query, code, self.current_window_title, self.selected_metadata_string)
        self.worker.result_ready.connect(self.on_ai_result_ready)
        self.worker.error_occurred.connect(self.on_ai_error)
        self.worker.start()

    def on_ai_result_ready(self, result_text):
        self.output_area.setText(result_text) # Выводим чистый ответ ИИ
        self.status_label.setText("Статус: Ответ готов")
        self.run_btn.setEnabled(True)

    def on_ai_error(self, error_text):
        self.output_area.setText(f"❌ Критическая ошибка ИИ: {error_text}")
        self.status_label.setText("Статус: Сбой генерации")
        self.run_btn.setEnabled(True)

    def paste_code_to_1c(self):
        self.status_label.setText("Статус: Вставка кода в 1С...")
        ai_code = self.output_area.toPlainText()
        if not ai_code.strip():
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(ai_code)
        self.status_label.setText("Статус: Код в буфере. Нажмите Ctrl+V в 1С.")

