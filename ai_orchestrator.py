import os
import json
import requests

class LocalAiOrchestrator:
    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        self.api_url = ""
        self.model_name = ""
        self.timeout = 120
        self.load_settings()

    def load_settings(self):
        """Загрузка параметров LM Studio из config.json проекта"""
        if not os.path.exists(self.config_path):
            self.api_url = "http://172.21.0"
            self.model_name = "qwen2.5-coder-7b-instruct"
            return
            
        with open(self.config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        llm_sec = data.get("local_llm", {})
        self.api_url = llm_sec.get("api_url", "http://172.21.0").rstrip('/')
        self.model_name = llm_sec.get("model_name", "qwen2.5-coder-7b-instruct")
        self.timeout = llm_sec.get("timeout", 120)

    def classify_error_and_get_prompt(self, error_text: str) -> str:
        """
        Алгоритм ветвления проблемы. Анализирует стек ошибки 
        и подсовывает модели Qwen специализированный системный промпт.
        """
        err_lower = error_text.lower()

        # Ветка 1: Ошибка объектного типа (Null Pointer)
        if "значение не является значением объектного типа" in err_lower or "неопределено" in err_lower:
            return (
                "Ты — эксперт по оптимизации BSL кода 1С:ERP. Обнаружена ошибка обращения к пустому объекту. "
                "Проанализируй переданный BSL-код и стек ошибки. Найди переменную, которая вызывает сбой. "
                "Сгенерируй хотфикс: добавь строгую проверку 'ЗначениеЗаполнено()' или 'Если ... <> Неопределено Тогда'. "
                "В ответе выведи ТОЛЬКО готовый исправленный фрагмент кода BSL без лишних пояснений."
            )

        # Ветка 2: Выход за границы массива
        if "индекс находится за пределами границы" in err_lower or "массив" in err_lower:
            return (
                "Ты — эксперт по разработке на BSL 1С. Обнаружен выход за границы индекса коллекции (Массив/Структура/ТаблицаЗначений). "
                "Изучи переданный BSL-код. Добавь проверку на количество элементов '.Количество()' перед обращением по индексу. "
                "В ответе верни ИСКЛЮЧИТЕЛЬНО готовый исправленный блок кода BSL."
            )

        # Ветка 3: Блокировки СУБД и транзакции
        if "блокировка" in err_lower or "deadlock" in err_lower or "конфликт" in err_lower:
            return (
                "Ты — senior 1С-архитектор. Произошел транзакционный конфликт или взаимоблокировка в СУБД PostgreSQL. "
                "Проанализируй код модуля. Предложи расстановку Управляемых Блокировок ('БлокировкаДанных') или "
                "оптимизацию запроса для уменьшения времени транзакции. Выведи патч кода."
            )

        # Ветка 4: Дефолтный промпт для всех остальных ошибок
        return (
            "Ты — встроенный ИИ-ассистент в IDE для 1С:ERP. Твоя задача — исправить баг рантайма. "
            "Изучи предоставленный текст ошибки и исходный код BSL модуля. Сгенерируй точечное исправление (hotfix). "
            "Выведи только исправленный код BSL в формате plain text."
        )

    def analyze_and_fix_code(self, error_logs: str, current_bsl_code: str) -> dict:
        """
        Отправляет структурированный JSON-запрос в локальную LM Studio.
        Реализует двухконтурную сборку контекста.
        """
        # 1. Получаем системный промпт через ветвление проблем
        system_prompt = self.classify_error_and_get_prompt(error_logs)
        
        # 2. Формируем пользовательский контент (User Prompt)
        user_content = (
            f"=== СТЕК ОШИБКИ ИЗ РАНТАЙМА 1С ===\n{error_logs}\n\n"
            f"=== ИСХОДНЫЙ BSL КОД МОДУЛЯ ===\n{current_bsl_code}\n\n"
            f"Инструкция: Найди ошибку и выдай исправленный BSL-код."
        )

        # 3. Сборка стандартного OpenAI-совместимого payload
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            "temperature": 0.2, # Низкая температура для строгой генерации кода без фантазий
            "stream": False
        }

        endpoint = f"{self.api_url}/chat/completions"
        print(f"[INFO] [LLM] Отправка запроса к локальной модели {self.model_name}...")
        
        try:
            response = requests.post(endpoint, json=payload, timeout=self.timeout)
            if response.status_code == 200:
                result_json = response.json()
                # Безопасно вытягиваем ответ согласно структуре Chat Completion API
                # (Исправляет баг 'list indices must be integers or slices, not str')
                ai_reply = result_json["choices"][0]["message"]["content"]
                return {"status": "SUCCESS", "reply": ai_reply}
            else:
                return {
                    "status": "ERROR", 
                    "reply": f"Ошибка сервера LM Studio (Код: {response.status_code}): {response.text}"
                }
        except requests.exceptions.Timeout:
            return {"status": "ERROR", "reply": f"Превышен таймаут ожидания локального ИИ ({self.timeout} сек)."}
        except Exception as e:
            return {"status": "ERROR", "reply": f"Не удалось связаться с инференс-сервером: {e}"}
