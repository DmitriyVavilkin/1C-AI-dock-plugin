import time
import win32gui
import ctypes
from PyQt6.QtCore import QObject, pyqtSignal

# Константы WinAPI для управления клавиатурой
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_C = 0x43

class ConfiguratorBridge(QObject):
    code_captured = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        
    def find_1c_configurator(self):
        found_title = "Конфигуратор 1С"
        def callback(hwnd, extra):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if "конфигуратор" in title.lower():
                    nonlocal found_title
                    found_title = title
            return True
        win32gui.EnumWindows(callback, None)
        return found_title

    def capture_selected_text(self):
        """
        Имитирует Ctrl+C через низкоуровневые системные прерывания Windows
        с честным зажатием и отпусканием клавиш.
        """
        title = self.find_1c_configurator()
        
        # Даем пользователю 150мс полностью отпустить свои клавиши Ctrl+Shift+X,
        # чтобы они не смешивались с нашей командой копирования
        time.sleep(0.15)
        
        print("[ИИ Мост] Низкоуровневая отправка Ctrl+C...")
        
        # 1. Зажимаем Ctrl
        ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
        time.sleep(0.05) # Крошечная пауза, чтобы ОС поняла, что Ctrl зажат
        
        # 2. Нажимаем и отпускаем клавишу C
        ctypes.windll.user32.keybd_event(VK_C, 0, 0, 0)
        time.sleep(0.05)
        ctypes.windll.user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)
        
        # 3. Отпускаем Ctrl
        ctypes.windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        
        # Даем ОС время обновить глобальный буфер обмена
        time.sleep(0.2)
        
        # Просыпаемся в app.py
        self.code_captured.emit("", title)
