import json
import os
import re
import psycopg2
import requests
import uuid

class AIErrorAnalyzer1C:
    def __init__(self, config_path="config.json", cloud_api_url=None, cloud_api_key=None):
        """
        Инициализация анализатора на основе внешнего config.json.
        Автоматически конфигурирует СУБД, адрес локальной LLM и таймауты.
        """
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"❌ Конфигурационный файл {config_path} не найден в корне проекта!")
            
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
            
        pg = config_data.get("postgres", {})
        llm = config_data.get("local_llm", {})
        
        # Настройка подключения к Postgres с учетом порта
        self.db_config = {
            "host": pg.get("host", "172.16.30.204"),
            "database": pg.get("database", "1C_AI_Database"),
            "user": pg.get("user", "postgres"),
            "password": pg.get("password", ""),
            "port": pg.get("port", 5432)
        }
        
        # Настройка параметров локальной LLM из конфига
        self.lm_studio_url = llm.get("api_url", "http://localhost:1234/v1")
        self.model_name = llm.get("model_name", "qwen2.5-coder-7b-instruct")
        self.timeout = llm.get("timeout", 120)
        
        # Настройки внешнего контура (Супермодель)
        self.cloud_api_url = cloud_api_url
        self.cloud_api_key = cloud_api_key
        
        self.conn_ai = None
        self.anonymizer = None
        
        print(f"⚙️ Конфигурация успешно загружена из {config_path}.")
        print(f"🔗 Локальный ИИ: {self.lm_studio_url} (Модель: {self.model_name}, Таймаут: {self.timeout}с)")

    def _connect_db(self):
        """Безопасное подключение к СУБД ИИ с передачей всех параметров"""
        if not self.conn_ai or self.conn_ai.closed:
            try:
                self.conn_ai = psycopg2.connect(**self.db_config)
            except Exception as e:
                print(f"❌ Ошибка подключения к базе данных {self.db_config.get('database')}: {e}")
                raise e

    def close(self):
        """Закрытие соединения с СУБД"""
        if self.conn_ai and not self.conn_ai.closed:
            self.conn_ai.close()
    def parse_1c_error_stack(self, error_text):
        """Извлекает имя модуля и номер строки из технического стека 1С"""
        pattern = r'([\w\.]+)\.Модуль\((\d+)\)'
        match = re.search(pattern, error_text, re.IGNORECASE)
        if match:
            module_name = match.group(1)
            line_number = int(match.group(2))
            return module_name, line_number
        return None, None

    def is_business_logic_error(self, error_text):
        """Двухэтапный роутер с привлечением локальной LLM в роли диспетчера"""
        module_name, _ = self.parse_1c_error_stack(error_text)
        if module_name:
            print("🤖 Роутер: Обнаружен стек вызовов 1С. Приоритет отдан ВНУТРЕННЕМУ техническому контуру.")
            return False
            
        print("🤖 Роутер: Текстовое обращение без стека. Привлекаем локальный ИИ для классификации...")
        system_prompt = (
            "Ты — классификатор обращений техподдержки 1С. Определи тип проблемы.\n"
            "Выведи строго одно слово:\n"
            "TECH — если речь идет о системной ошибке СУБД, падении сервера, ошибке синтаксиса или баге в коде.\n"
            "BUSINESS — если это методологический вопрос, ошибка проведения документов, закрытия месяца, "
            "несоответствие проводок, расчет НДС/налогов или вопрос по бухучету."
        )
        
        try:
            response = requests.post(
                f"{self.lm_studio_url}/chat/completions",
                json={
                    "model": self.model_name,  # Используем имя из конфига
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Классифицируй обращение:\n\"\"\"{error_text}\"\"\""}
                    ],
                    "temperature": 0.0,
                    "max_tokens": 5
                },
                timeout=10  # Для роутинга держим короткий таймаут
            )
            if response.status_code == 200:
                verdict = response.json()['choices']['message']['content'].strip().upper()
                print(f"🤖 Локальный ИИ вынес вердикт категории: {verdict}")
                if "BUSINESS" in verdict:
                    return True
            return False
        except Exception as e:
            print(f"⚠️ Ошибка экспресс-классификации роутера ({e}). Откат к локальному контуру.")
            return False

    def dispatch_and_analyze(self, raw_error_text):
        """Главный диспетчер распределения инцидентов по контурам"""
        print("\n📥 Получен новый инцидент 1С.")
        if self.is_business_logic_error(raw_error_text):
            print("🛑 Категория: Бизнес-логика / Методология закрытия периода.")
            return self._analyze_methodology_error(raw_error_text)
        
        print("🛠 Категория: Технический сбой / Исключение в коде BSL.")
        module_name, line_number = self.parse_1c_error_stack(raw_error_text)
        return self._analyze_technical_error(raw_error_text, module_name, line_number)
    def get_module_code_from_db(self, module_name):
        """Ищет исходный код модуля в локальной базе данных ИИ"""
        if not module_name:
            return None
        self._connect_db()
        with self.conn_ai.cursor() as cur:
            query = """
                SELECT src.source_code 
                FROM ai_source_codes src
                JOIN ai_metadata_objects obj ON src.object_id = obj.object_id
                WHERE obj.internal_name ILIKE %s OR obj.object_name ILIKE %s
                LIMIT 1;
            """
            clean_name = f"%{module_name.split('.')[-1]}%"
            cur.execute(query, (clean_name, clean_name))
            row = cur.fetchone()
            return row[0] if row else None

    def _analyze_technical_error(self, error_text, module_name, line_number):
        """Сборка контекста вокруг проблемной строки и запуск анализа"""
        if not module_name:
            print("⚠️ Имя модуля не определено из стека. Отправляем сырой текст ошибки в локальный ИИ...")
            return self._analyze_raw_tech_error(error_text)

        print(f"📍 Локализация: модуль '{module_name}', строка {line_number}. Ищем код в базе...")
        bsl_code = self.get_module_code_from_db(module_name)
        if not bsl_code:
            return f"⚠️ Техническая ошибка найдена, но код модуля '{module_name}' отсутствует в базе ИИ. Текст ошибки:\n{error_text}"

        code_lines = bsl_code.splitlines()
        start_line = max(0, line_number - 30)
        end_line = min(len(code_lines), line_number + 30)
        context_code = "\n".join(f"{i+1}: {line}" for i, line in enumerate(code_lines[start_line:end_line], start_line))

        system_prompt = (
            "Ты — ведущий эксперт, 1С-Архитектор и Senior BSL Developer. Твоя задача — "
            "проанализировать рантайм-ошибку клиентского приложения 1С, найти уязвимость в предоставленном "
            "исходном коде на указанной строке и выдать техническое заключение. Отвечай строго на русском языке."
        )
        user_content = f"ТЕКСТ ОШИБКИ 1С:\n{error_text}\n\nКОНТЕКСТ КОДА (Строка: {line_number}):\n{context_code}\n\nВыдай отчет: 1.Причина падения, 2.Анализ переменных, 3.Вариант исправления."
        return self._call_local_llm(system_prompt, user_content)

    def _analyze_raw_tech_error(self, error_text):
        system_prompt = "Ты — Senior DevOps и инженер СУБД 1С. Проанализируй системную или техническую ошибку платформы 1С и дай техническое заключение."
        return self._call_local_llm(system_prompt, f"Проанализируй ошибку:\n{error_text}")

    def _call_local_llm(self, system_prompt, user_content):
        """Интерфейс низкоуровневых запросов к LM Studio с проверкой структуры ответа"""
        print("🤖 Запрос к локальной LM Studio...")
        try:
            response = requests.post(
                f"{self.lm_studio_url}/chat/completions",
                json={
                    "model": self.model_name,
                    "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}],
                    "temperature": 0.1, 
                    "max_tokens": 1200
                }, 
                timeout=self.timeout  # Таймаут из config.json
            )
            
            if response.status_code != 200:
                return f"❌ Ошибка LM Studio (Код {response.status_code}): {response.text}"
                
            data = response.json()
            if isinstance(data, dict) and 'choices' in data and len(data['choices']) > 0:
                return data['choices'][0]['message']['content']  # Исправленный индекс массива
            else:
                return f"❌ Неожиданный формат ответа от сервера: {data}"
                
        except Exception as e: 
            return f"❌ Контур 1 недоступен (Исключение): {e}"

    def _analyze_methodology_error(self, error_text):
        """Внешний контур (Методологический анализ) с защитой коммерческих данных"""
        print("🔒 Запуск маскирования коммерческих данных перед выходом во внешний контур...")
        try:
            from anonymizer import Anonymizer
            if not self.anonymizer: 
                self.anonymizer = Anonymizer()
            clean_error = self.anonymizer.mask_data(error_text)
        except ImportError:
            return "❌ Ошибка: Внешний контур заблокирован. Требуется настроить модуль anonymizer.py для защиты КТ."

        if not self.cloud_api_key or not self.cloud_api_url:
            return "⚠️ Текст очищен, но Внешний контур (Cloud API/Супермодель) не настроен в конфигурации."

        print("🌐 Отправка инцидента во Внешний контур...")
        system_prompt = "Ты — высококлассный аудитор, главный методолог учета 1С:ERP и эксперт по законодательству РФ. Проанализируй проблему и предложи алгоритм исправления."
        try:
            response = requests.post(
                self.cloud_api_url, 
                headers={"Authorization": f"Bearer {self.cloud_api_key}"},
                json={"messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": clean_error}], "temperature": 0.3},
                timeout=90
            )
            return response.json()['choices'][0]['message']['content'] if response.status_code == 200 else f"❌ Ошибка Внешнего контура: {response.status_code}"
        except Exception as e: 
            return f"❌ Не удалось связаться с Внешним контуром: {e}"
