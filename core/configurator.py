import win32gui
import win32process
import ctypes
import time

class ConfiguratorBridge:
    def __init__(self):
        print("[ConfiguratorBridge] Инициализация WinAPI моста...")

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
        """
        Возвращает заголовок окна Конфигуратора 1С на основе его процесса.
        Если активен плагин, функция ищет окно, принадлежащее процессу Конфигуратора.
        """
        # 1. Проверяем текущее активное окно в Windows
        hwnd = win32gui.GetForegroundWindow()
        title = self._get_window_text_unicode(hwnd)
        
        # 2. Если фокус на плагине, VS Code или пустой — ищем окно по привязке к процессу 1С
        if not title or "1C AI Dock" in title or "app.py" in title or "visual studio" in title.lower():
            print("[WinAPI] Фокус потерян или на плагине. Ищем окно через процессы...")
            
            found_titles = []
            
            def enum_windows_callback(window_hwnd, extra):
                if win32gui.IsWindowVisible(window_hwnd):
                    # Получаем PID процесса, которому принадлежит это окно
                    _, pid = win32process.GetWindowThreadProcessId(window_hwnd)
                    
                    try:
                        PROCESS_QUERY_INFORMATION = 0x0400
                        PROCESS_VM_READ = 0x0010
                        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
                        if handle:
                            buffer = ctypes.create_unicode_buffer(260)
                            ctypes.windll.psapi.GetModuleBaseNameW(handle, None, buffer, 260)
                            exe_name = buffer.value.lower()
                            ctypes.windll.kernel32.CloseHandle(handle)
                            
                            # Если окно принадлежит Конфигуратору 1С
                            if exe_name == "1cv8.exe":
                                w_title = self._get_window_text_unicode(window_hwnd)
                                if w_title and not w_title.startswith("Панель"):
                                    # Нам нужен заголовок конкретного модуля, а не главное окно "Конфигуратор"
                                    if "модуль" in w_title.lower() or "документ" in w_title.lower() or "справочник" in w_title.lower():
                                        found_titles.insert(0, w_title) # Приоритет окнам кода
                                    else:
                                        found_titles.append(w_title)
                    except Exception:
                        pass
                return True
                
            win32gui.EnumWindows(enum_windows_callback, None)
            
            if found_titles:
                print(f"[WinAPI] Найдено окно процесса 1cv8.exe: '{found_titles[0]}'")
                return found_titles[0]
                
            # Резервный вариант: если процессы заблокированы, ищем по маске 'Конфигуратор'
            hwnd_backup = []
            win32gui.EnumWindows(lambda h, e: hwnd_backup.append(self._get_window_text_unicode(h)) if "конфигуратор" in self._get_window_text_unicode(h).lower() else True, None)
            if hwnd_backup:
                return hwnd_backup[0]

        return title

    def copy_selected_text(self):
        """Имитирует нажатие Ctrl+C через WinAPI для копирования BSL-кода"""
        # Эмулируем нажатие Ctrl+C
        ctypes.windll.user32.keybd_event(0x11, 0, 0, 0) # Нажать Ctrl
        ctypes.windll.user32.keybd_event(0x43, 0, 0, 0) # Нажать C
        time.sleep(0.05)                                # Задержка для ОС
        ctypes.windll.user32.keybd_event(0x43, 0, 2, 0) # Отпустить C
        ctypes.windll.user32.keybd_event(0x11, 0, 2, 0) # Отпустить Ctrl
        time.sleep(0.05)
