import json
import os
import re
import requests

class AIRouter:
    def __init__(self, config_path="config.json"):
        # Настройки локальной LLM в LM Studio
        # ТУТ: Когда настроите домашний сервер, замените localhost на домашний IP!
        self.api_url = "http://localhost:1234/v1/chat/completions"
        self.model_name = "qwen2.5-coder-7b-instruct"
        self.timeout = 300  # 5 минут ожидания для тяжелых ответов ERP
        
        # Настройки локального HTTP-сервиса в тестовой 1С:ERP
        # КРИТИЧЕСКИ ВАЖНО: Добавлен слэш / в конец пути для обхода редиректа 301!
        self.one_c_service_url = "http://localhost/erp_test/hs/ai/get_structure"
        
        self.current_super_pattern = (
            "Ты эксперт по архитектуре и разработке в 1С:ERP. Пиши чистый код BSL. "
            "Следуй стандартам фирмы 1С и БСП. Избегай неоптимальных запросов."
        )
        print(f"[Router] Инициализация. Целевой URL: {self.api_url}")
        print(f"[Router] Имя модели: {self.model_name}")

    def parse_object_name_from_title(self, window_title):
        """Вычленяет имя объекта метаданных из заголовка окна Конфигуратора 1С"""
        if not window_title:
            return "Документ.РеализацияТоваровУслуг"
            
        print(f"[Роутер] Анализ заголовка окна: '{window_title}'")
        
        if "конфигуратор -" in window_title.lower():
            print("[Роутер] Перехвачено главное окно 1С:ERP. Авто-подстановка контекста по умолчанию.")
            return "Документ.РеализацияТоваровУслуг"
        
        types_map = {
            "Документ": "Документ",
            "Справочник": "Справочник",
            "Регистрсведений": "РегистрСведений",
            "Регистрнакопления": "РегистрНакопления",
            "Отчет": "Отчет",
            "Обработка": "Обработка"
        }
        
        pattern = r"(Документ|Справочник|РегистрСведений|РегистрНакопления|Отчет|Обработка)[\s\.]+(\w+)"
        match = re.search(pattern, window_title, re.IGNORECASE)
        if match:
            found_type = match.group(1)
            correct_type = types_map.get(found_type.capitalize(), found_type.capitalize())
            object_name = match.group(2)
            return f"{correct_type}.{object_name}"
                                
        return "Документ.РеализацияТоваровУслуг"

    def get_1c_metadata_raw(self, object_name):
        """Запрашивает структуру реквизитов из 1С и возвращает словарь (dict)"""
        # БРОНИРОВАННАЯ ЗАЩИТА: если имя пустое, принудительно пишем объект тестов
        if not object_name or str(object_name).strip() == "":
            object_name = "Документ.РеализацияТоваровУслуг"
            
        if "." not in object_name:
            object_name = f"Документ.{object_name}"
            
        try:
            print(f"[1С Мост] Запрос метаданных для: {object_name}...")
            
            res = requests.get(
                self.one_c_service_url, 
                params={"object": object_name.strip()}, 
                timeout=30
            )
            
            # Выводим ПОЛНЫЙ сгенерированный URL-адрес в консоль для точечного контроля
            print(f"[1С Мост] Лог отправки URL: {res.url} (Код ответа Apache: {res.status_code})")
            
            if res.status_code == 200:
                return res.json()
            else:
                return {"error": f"Ошибка 1С (Код {res.status_code}): {res.text}"}
        except Exception as e:
            return {"error": f"Ошибка связи с веб-сервером: {str(e)}"}

    def route_request(self, user_query, bsl_code, window_title, selected_metadata_item=""):
        """Оркестрирует запросы между локальным и внешним контуром"""
        object_name = self.parse_object_name_from_title(window_title)
        
        # Запрашиваем метаданные в сыром виде (словарь)
        metadata_dict = self.get_1c_metadata_raw(object_name)
        metadata_context = json.dumps(metadata_dict, indent=2, ensure_ascii=False)
        
        # Дополнительный акцент для ИИ на элементе метаданных, который выбрал юзер в дереве
        user_focus = f"\nВНИМАНИЕ: Разработчик выделил мышкой реквизит: {selected_metadata_item}" if selected_metadata_item else ""
        
        if user_query.startswith("/pattern"):
            print("[Роутер] Обнаружен маркер /pattern. Запрос перенаправлен во внешний контур.")
            return f"Контекст для внешней модели собран.\n\nМетаданные:\n{metadata_context}{user_focus}"
            
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.current_super_pattern},
                {"role": "user", "content": f"Контекст метаданных 1С:\n{metadata_context}\n{user_focus}\n\nТекущий BSL-код:\n{bsl_code}\n\nВопрос/Задача:\n{user_query}"}
            ],
            "temperature": 0.2
        }
        
        try:
            print(f"[Роутер] Отправка запроса в LM Studio ({self.api_url})...")
            response = requests.post(self.api_url, json=payload, timeout=self.timeout)
            if response.status_code == 200:
                result = response.json()
                return result["choices"]["message"]["content"]
            else:
                return f"Ошибка локальной модели (Код {response.status_code}): {response.text}"
        except Exception as e:
            return f"Критическая ошибка подключения к LM Studio: {str(e)}"
