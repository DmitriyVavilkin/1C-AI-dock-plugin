import win32gui
import win32process
import ctypes
import time
from PyQt6.QtCore import QObject, pyqtSignal

class ConfiguratorBridge(QObject):
    # Сигнал для передачи захваченного кода и заголовка окна в GUI
    code_captured = pyqtSignal(str, str) 

    def __init__(self):
        # ОБЯЗАТЕЛЬНО: инициализируем базовый QObject, чтобы сигналы заработали!
        super().__init__()
        print("[ConfiguratorBridge] Инициализация WinAPI моста с поддержкой PyQt6 сигналов...")

    def _get_window_text_unicode(self, hwnd):
        """Низкоуровневое чтение заголовка окна в Unicode (UTF-16)"""
        if not hwnd:
            return ""
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def get_active_window_title(self):
        """Возвращает заголовок текущего активного окна Windows в Юникоде"""
        hwnd = win32gui.GetForegroundWindow()
        title = self._get_window_text_unicode(hwnd)
        return title

    def capture_selected_text(self):
        """Имитирует нажатие Ctrl+C через WinAPI для копирования BSL-кода"""
        print("[WinAPI] Отправка нажатия Ctrl+C во внешнее окно...")
        ctypes.windll.user32.keybd_event(0x11, 0, 0, 0) # Ctrl down
        ctypes.windll.user32.keybd_event(0x43, 0, 0, 0) # C down
        time.sleep(0.05)
        ctypes.windll.user32.keybd_event(0x43, 0, 2, 0) # C up
        ctypes.windll.user32.keybd_event(0x11, 0, 2, 0) # Ctrl up
        time.sleep(0.05)
