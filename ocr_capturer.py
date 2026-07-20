import os
import json
from PIL import ImageGrab, ImageOps
import pytesseract
import pygetwindow as gw # Библиотека для захвата фокуса окон Windows

class OcrErrorCapturer:
    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        
        # Стандартный путь установки Tesseract в Windows
        tesseract_default_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if os.path.exists(tesseract_default_path):
            pytesseract.pytesseract.tesseract_cmd = tesseract_default_path

    def capture_screen_to_text(self) -> str:
        """
        Находит активное окно в ОС Windows, вычисляет его координаты,
        делает точечный снимок области, бинаризирует и извлекает текст ошибки.
        """
        print("[INFO] [OCR] Анализ активного окна Windows...")
        try:
            # 1. Получаем объект активного окна, которое сейчас находится в фокусе разработчика
            active_window = gw.getActiveWindow()
            
            bbox = None
            if active_window and active_window.title:
                print(f"[INFO] [OCR] Обнаружено активное окно: '{active_window.title}'")
                
                # Вычисляем точные экранные координаты окна (Left, Top, Right, Bottom)
                bbox = (
                    active_window.left,
                    active_window.top,
                    active_window.left + active_window.width,
                    active_window.top + active_window.height
                )
            
            # 2. Делаем снимок. Если окно определено — берем только его область, если нет — весь экран (fallback)
            if bbox:
                print(f"[INFO] [OCR] Захват целевой области окна: {bbox}")
                screenshot = ImageGrab.grab(bbox=bbox)
            else:
                print("[WARNING] [OCR] Активное окно не определено. Захват экрана целиком...")
                screenshot = ImageGrab.grab()
            
            # 3. Предобработка: переводим в оттенки серого (убираем цветовые шумы)
            gray_image = ImageOps.grayscale(screenshot)
            
            # Повышаем контрастность (бинаризация по порогу): фон белый, текст строго черный
            threshold = 127
            binarized_image = gray_image.point(lambda p: 255 if p > threshold else 0)
            
            # 4. OCR-анализ движком Tesseract (подключаем русский и английский)
            print("[INFO] [OCR] Распознавание символов движком Tesseract...")
            raw_text = pytesseract.image_to_string(binarized_image, lang="rus+eng")
            
            cleaned_text = raw_text.strip()
            if not cleaned_text:
                return "[INFO] В активном окне текст ошибки не обнаружен."
                
            print("[SUCCESS] [OCR] Текст ошибки успешно извлечен из целевого окна.")
            return cleaned_text
            
        except Exception as e:
            error_msg = f"[ERROR] Сбой выполнения OCR-захвата: {e}"
            print(error_msg)
            return error_msg

if __name__ == "__main__":
    capturer = OcrErrorCapturer()
    print("\n--- Результат точечного теста OCR активного окна ---")
    print(capturer.capture_screen_to_text())
