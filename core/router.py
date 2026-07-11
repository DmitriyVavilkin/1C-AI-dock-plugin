import json
import os
import re
import requests

class AIRouter:
    def __init__(self, config_path="config.json"):
        # Настройки локальной LLM в LM Studio
        self.api_url = "http://localhost:1234/v1/chat/completions"
        self.model_name = "qwen2.5-coder-7b-instruct"
        self.timeout = 300  # 5 минут ожидания для тяжелых ответов ERP
        
        # Настройки вашего локального HTTP-сервиса в тестовой 1С:ERP
        self.one_c_service_url = "http://localhost/erp_test/hs/ai/v1/metadata"
        
        self.current_super_pattern = (
            "Ты эксперт по архитектуре и разработке в 1С:ERP. Пиши чистый код BSL. "
            "Следуй стандартам фирмы 1С и БСП. Избегай неоптимальных запросов."
        )
        print(f"[Router] Инициализация. Целевой URL: {self.api_url}")
        print(f"[Router] Имя модели: {self.model_name}")

    def parse_object_name_from_title(self, window_title):
        """
        Вычленяет имя объекта метаданных из заголовка окна Конфигуратора 1С.
        Улучшенная версия: ищет точное совпадение типа и следующего за ним слова.
        """
        if not window_title:
            return ""
            
        print(f"[Роутер] Анализ заголовка окна: '{window_title}'")
        
        # Словарь маппинга типов для приведения регистра
        types_map = {
            "Документ": "Документ",
            "Справочник": "Справочник",
            "Регистрсведений": "РегистрСведений",
            "Регистрнакопления": "РегистрНакопления",
            "Отчет": "Отчет",
            "Обработка": "Обработка"
        }
        
        # Регулярка ищет тип объекта, а затем через пробел или точку — имя объекта 1С
        pattern = r"(Документ|Справочник|РегистрСведений|РегистрНакопления|Отчет|Обработка)[\s\.]+(\w+)"
        
        match = re.search(pattern, window_title, re.IGNORECASE)
        if match:
            found_type = match.group(1)
            # Приводим тип к правильному регистру 1С
            correct_type = types_map.get(found_type.capitalize(), found_type.capitalize())
            object_name = match.group(2)
            
            result = f"{correct_type}.{object_name}"
            print(f"[Роутер] Успешно извлечен объект: {result}")
            return result
                                
        print("[Роутер] Не удалось определить объект по регулярному выражению.")
        return ""

    def get_1c_metadata_context(self, object_name):
        """Запрашивает структуру реквизитов из HTTP-сервиса 1С"""
        if not object_name:
            return "Контекст метаданных недоступен: объект 1С не определен."
            
        try:
            print(f"[1С Мост] Запрос метаданных для: {object_name}...")
            res = requests.get(
                self.one_c_service_url, 
                params={"object": object_name}, 
                timeout=30 # Даем 1С:ERP до 30 секунд на сбор тяжелой структуры
            )
            
            if res.status_code == 200:
                print("[1С Мост] Ответ от 1С получен. Форматируем JSON...")
                try:
                    data = res.json()
                    if isinstance(data, dict) and "error" in data:
                        return f"Ошибка внутри 1С BSL: {data['error']}"
                    return json.dumps(data, indent=2, ensure_ascii=False)
                except Exception:
                    return res.text
                    
            return f"❌ Ошибка 1С (Код {res.status_code}): {res.text}"
            
        except Exception as e:
            print(f"[1С Мост] Ошибка связи с 1С: {str(e)}")
            return f"Метаданные живой базы недоступны (анализ выполняется без контекста конфигурации). Причина: {str(e)}"

    def generate_metadata_tree_text(self, window_title):
        """Вызывается фиолетовой кнопкой из app.py для генерации псевдографического дерева"""
        object_name = self.parse_object_name_from_title(window_title)
        if not object_name:
            return "❌ Не удалось определить объект 1С по заголовку текущего окна."
            
        try:
            print(f"[Роутер] Сбор дерева для {object_name}...")
            res = requests.get(
                self.one_c_service_url, 
                params={"object": object_name}, 
                timeout=30
            )
            
            if res.status_code != 200:
                return f"❌ Ошибка 1С (Код {res.status_code}): {res.text}"
                
            data = res.json()
            
            # Если 1С вернула ошибку, обернутую в наш JSON
            if isinstance(data, dict) and "error" in data:
                return f"❌ Ошибка внутри 1С:\n{data['error']}"
                
            # Строим псевдографическое дерево реквизитов
            tree = []
            tree.append(f"📦 {data.get('Имя', object_name)} ({data.get('Синоним', '')})")
            tree.append("┃")
            tree.append("┣━ 🌳 Реквизиты")
            
            revisits = data.get("Реквизиты", [])
            if not revisits:
                tree.append("┃  ┗━ (нет доступных реквизитов)")
            else:
                for i, req in enumerate(revisits):
                    is_last = (i == len(revisits) - 1)
                    prefix = "┃  ┗━ " if is_last else "┃  ┣━ "
                    tree.append(f"{prefix}{req.get('Имя')} [{req.get('Тип')}]")
                    
            return "\n".join(tree)
            
        except Exception as e:
            return f"❌ Сбой построения дерева: {str(e)}"

    def route_request(self, user_query, bsl_code, window_title):
        """Оркестрирует запросы между локальным и внешним контуром"""
        object_name = self.parse_object_name_from_title(window_title)
        metadata_context = self.get_1c_metadata_context(object_name)
        
        if user_query.startswith("/pattern"):
            print("[Роутер] Обнаружен маркер /pattern. Запрос перенаправлен во внешний контур.")
            return f"Контекст для внешней модели собран.\n\nМетаданные:\n{metadata_context}"
            
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.current_super_pattern},
                {"role": "user", "content": f"Контекст метаданных 1С:\n{metadata_context}\n\nТекущий BSL-код:\n{bsl_code}\n\nВопрос/Задача:\n{user_query}"}
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
