import sys
import os
import time
import ctypes
from PyQt6.QtCore import Qt, pyqtSlot, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QTextEdit, QLineEdit, QPushButton, QLabel, QHBoxLayout
import keyboard

from core.configurator import ConfiguratorBridge
from core.router import AIRouter

# Константы WinAPI для эмуляции вставки кода
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56

class AIWorker(QThread):
    """Фоновый поток для отправки запросов к ИИ, чтобы GUI не зависал"""
    result_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, router, query, code, window_title):
        super().__init__()
        self.router = router
        self.query = query
        self.code = code
        self.window_title = window_title

    def run(self):
        try:
            result = self.router.route_request(self.query, self.code, self.window_title)
            self.result_ready.emit(result)
        except Exception as e:
            self.error_occurred.emit(str(e))


class MainAIDockApp(QWidget):
    def __init__(self):
        super().__init__()
        self.captured_code = ""
        self.last_ai_response = ""  # Хранилище для кода от ИИ (для авто-вставки)
        self.current_window_title = ""  # Точный заголовок окна 1С
        
        # Инициализация ядра
        self.bridge = ConfiguratorBridge()
        self.router = AIRouter()
        
        self.bridge.code_captured.connect(self.on_code_captured)
        
        self.init_ui()
        self.setup_hotkeys()
        
    def init_ui(self):
        self.setWindowTitle("1С AI Dock v2.0")
        self.resize(450, 600)
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        
        layout = QVBoxLayout()
        
        self.status_label = QLabel("Статус: Готов к работе")
        self.status_label.setStyleSheet("color: green; font-weight: bold;")
        layout.addWidget(self.status_label)
        
        self.code_preview = QTextEdit()
        self.code_preview.setPlaceholderText("Здесь появится код, выделенный в Конфигураторе...")
        self.code_preview.setMaximumHeight(100)
        self.code_preview.setStyleSheet("background-color: #f5f5f5; font-family: Consolas;")
        layout.addWidget(QLabel("Контекст кода 1С:"))
        layout.addWidget(self.code_preview)
        
        self.chat_area = QTextEdit()
        self.chat_area.setReadOnly(True)
        self.chat_area.setStyleSheet("background-color: #ffffff; font-size: 12px;")
        layout.addWidget(QLabel("История диалога / Метаданные:"))
        layout.addWidget(self.chat_area)
        
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Введите ваш вопрос здесь...")
        self.input_field.returnPressed.connect(self.process_input)
        layout.addWidget(self.input_field)
        
        # Блок кнопок управления
        btn_layout = QHBoxLayout()
        
        self.send_btn = QPushButton("🚀 Выполнить")
        self.send_btn.clicked.connect(self.process_input)
        btn_layout.addWidget(self.send_btn)

        self.tree_btn = QPushButton("🌳 Структура")
        self.tree_btn.setStyleSheet("background-color: #673ab7; color: white; font-weight: bold;")
        self.tree_btn.clicked.connect(self.show_metadata_tree)
        btn_layout.addWidget(self.tree_btn)
        
        self.paste_btn = QPushButton("📋 Вставить в 1С")
        self.paste_btn.setStyleSheet("background-color: #4caf50; color: white; font-weight: bold;")
        self.paste_btn.clicked.connect(self.paste_code_to_1c)
        btn_layout.addWidget(self.paste_btn)
        
        layout.addLayout(btn_layout)
        self.setLayout(layout)
        
    def setup_hotkeys(self):
        hotkey = "ctrl+shift+x"
        try:
            keyboard.add_hotkey(hotkey, self.trigger_capture)
            self.status_label.setText(f"Статус: Нажмите {hotkey.upper()} в Конфигураторе")
        except Exception as e:
            self.status_label.setText(f"Ошибка хоткея: {e}")

    def trigger_capture(self):
        self.bridge.capture_selected_text()

    @pyqtSlot(str, str)
    def on_code_captured(self, dummy_text, window_title):
        self.raise_()
        self.activateWindow()
        time.sleep(0.12)
        
        self.current_window_title = window_title
        clipboard = QApplication.clipboard()
        self.captured_code = clipboard.text()
        
        if self.captured_code.strip():
            self.code_preview.setPlainText(self.captured_code)
            self.status_label.setText(f"Код успешно захвачен из 1С!")
        else:
            self.code_preview.setPlainText("⚠️ Внимание: Буфер пуст.")
            self.status_label.setText("Ошибка: Текст в 1С не выделен")
            
        self.input_field.setFocus()

    def process_input(self):
        """Запуск фоновой обработки запроса через ИИ"""
        query = self.input_field.text().strip()
        if not query:
            return
            
        self.chat_area.append(f"<b>Вы:</b> {query}")
        self.input_field.clear()
        
        if query.startswith("/pattern "):
            new_pattern = query.replace("/pattern ", "")
            self.router.set_pattern_from_supermodel(new_pattern)
            self.chat_area.append("<font color='green'><b>Системный паттерн обновлен!</b></font><br>")
            return
        
        self.status_label.setText("⏳ ИИ думает... (Окно доступно)")
        self.status_label.setStyleSheet("color: orange; font-weight: bold;")
        self.chat_area.append("<font color='gray'><i>🤖 Запрос обрабатывается в фоне. Вы можете продолжать кодить в 1С...</i></font>")
        self.send_btn.setEnabled(False)
        self.tree_btn.setEnabled(False)
        self.input_field.setEnabled(False)
        
        self.worker = AIWorker(self.router, query, self.captured_code, self.current_window_title)
        self.worker.result_ready.connect(self.on_ai_response_received)
        self.worker.error_occurred.connect(self.on_ai_error)
        self.worker.start()

    @pyqtSlot(dict)
    def on_ai_response_received(self, result):
        self.send_btn.setEnabled(True)
        self.tree_btn.setEnabled(True)
        self.input_field.setEnabled(True)
        self.status_label.setText("Статус: Ответ получен")
        self.status_label.setStyleSheet("color: green; font-weight: bold;")
        
        if result["contour"] == "internal":
            self.last_ai_response = result["payload"]
            self.chat_area.append(f"<b>Qwen:</b> {result['payload']}<br>")
        else:
            self.chat_area.append(f"<pre style='background:#fff3cd; padding:5px;'>{result['payload']}</pre><br>")
            self.chat_area.append("<i>Скопируйте текст выше супермодели.</i>")
            
        self.input_field.setFocus()

    @pyqtSlot(str)
    def on_ai_error(self, error_msg):
        self.send_btn.setEnabled(True)
        self.tree_btn.setEnabled(True)
        self.input_field.setEnabled(True)
        self.status_label.setText("Статус: Ошибка сети")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        self.chat_area.append(f"<font color='red'><b>Ошибка:</b> {error_msg}</font><br>")

    def show_metadata_tree(self):
        """Прямой синхронный запрос дерева структуры из 1С без участия ИИ"""
        self.status_label.setText("⏳ Считывание структуры из базы 1С...")
        self.status_label.setStyleSheet("color: blue; font-weight: bold;")
        QApplication.processEvents()
        
        # Получаем сгенерированный псевдографический текст из router.py
        tree_text = self.router.generate_metadata_tree_text(self.current_window_title)
        
        self.chat_area.append("<font color='purple'><b>--- СТРУКТУРА ОБЪЕКТА (ЖИВОЙ КОНТЕКСТ) ---</b></font>")
        self.chat_area.append(f"<pre style='background:#f3e5f5; font-family:Consolas; font-size:11px; padding:8px;'>{tree_text}</pre><br>")
        
        self.status_label.setText("Статус: Дерево метаданных построено")
        self.status_label.setStyleSheet("color: green; font-weight: bold;")

    def paste_code_to_1c(self):
        if not self.last_ai_response:
            self.status_label.setText("Ошибка: Нечего вставлять.")
            return
            
        clean_code = self.last_ai_response
        if "```" in clean_code:
            lines = clean_code.split("\n")
            clean_lines = [l for l in lines if not l.strip().startswith("```")]
            clean_code = "\n".join(clean_lines)
            
        clipboard = QApplication.clipboard()
        clipboard.setText(clean_code.strip())
        
        self.status_label.setText("Вставка кода...")
        time.sleep(0.3)
        
        ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
        time.sleep(0.05)
        ctypes.windll.user32.keybd_event(VK_V, 0, 0, 0)
        time.sleep(0.05)
        ctypes.windll.user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)
        ctypes.windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        
        self.status_label.setText("Код успешно передан в Конфигуратор!")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    main_win = MainAIDockApp()
    main_win.show()
    sys.exit(app.exec())
