import json
import requests
import psycopg2

class AIOrchestrator:
    def __init__(self, pg_config, llm_config):
        """Инициализация оркестратора ИИ-запросов"""
        self.pg_config = pg_config
        self.llm_url = llm_config['url']  # Например, http://localhost:1234/v1
        self.llm_model = llm_config['model']

    def _get_db_connection(self):
        """Создает подключение к вашей базе ИИ"""
        conn = psycopg2.connect(
            host=self.pg_config['host'],
            database=self.pg_config['database'],
            user=self.pg_config['user'],
            password=self.pg_config['password'],
            port=self.pg_config.get('port', 5432)
        )
        return conn

    def get_object_context(self, internal_name):
        """
        Вытаскивает из СУБД полный контекст объекта: его BSL-код и реквизиты.
        """
        conn = self._get_db_connection()
        context = {"name": internal_name, "fields": [], "code_modules": []}

        with conn.cursor() as cursor:
            # 1. Получаем object_id по русскому имени
            cursor.execute("""
                SELECT object_id FROM ai_metadata_objects 
                WHERE internal_name = %s LIMIT 1;
            """, (internal_name,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return None
            obj_id = row[0]

            # 2. Вытаскиваем все реквизиты объекта
            cursor.execute("""
                SELECT field_name, field_type FROM ai_metadata_fields 
                WHERE object_id = %s;
            """, (obj_id,))
            context["fields"] = cursor.fetchall()

            # 3. Вытаскиваем все тексты BSL-кода (ObjectModule, ManagerModule)
            cursor.execute("""
                SELECT module_type, bsl_text FROM ai_source_codes 
                WHERE object_id = %s;
            """, (obj_id,))
            context["code_modules"] = cursor.fetchall()

        conn.close()
        return context

    def analyze_bsl_module(self, object_name):
        """
        Формирует промпт из контекста СУБД и отправляет его в локальную Qwen
        """
        context = self.get_object_context(object_name)
        if not context or not context["code_modules"]:
            return f"❌ Объект '{object_name}' или его BSL-код не найдены в базе данных ИИ."

        # Формируем текстовое описание реквизитов для ИИ
        fields_str = "\n".join([f" - {name} (Тип: {ftype})" for name, ftype in context["fields"]])
        if not fields_str:
            fields_str = " Реквизиты отсутствуют или не заданы."

        # Берём первый найденный модуль (например, Модуль объекта)
        module_type, bsl_text = context["code_modules"][0]

        # Системная инструкция для Qwen2.5-Coder (настраиваем модель на 1С статанализ)
        system_prompt = (
            "Ты — ведущий эксперт по разработке и оптимизации на платформе 1С:Предприятие 8.3. "
            "Твоя задача — проводить статический анализ BSL-кода, искать скрытые баги, "
            "уязвимости, неоптимальные запросы в циклах и предлагать рефакторинг по стандартам 1С. "
            "Отвечай строго на русском языке, лаконично, с примерами исправленного кода."
        )

        # Конструируем пользовательский промпт, скармливая модели весь наш собранный SQL-контекст
        user_prompt = f"""
Проанализируй код модуля объекта 1С. 

КОНТЕКСТ ОБЪЕКТА МЕТАДАННЫХ:
Имя объекта: {object_name}
Доступные реквизиты/поля в СУБД:
{fields_str}

ТИП МОДУЛЯ: {module_type}
ИХОДНЫЙ BSL-КОД ДЛЯ АНАЛИЗА:
```bsl
{bsl_text}
```

Найди критические ошибки, потенциальные падения (например, обращение к пустой ссылке) или неоптимальные конструкции. Выведи список замечаний и покажи улучшенную версию кода.
"""

        # Формируем стандартный JSON-пакет для OpenAI-совместимого API LM Studio
        payload = {
            "model": self.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.2, # Низкая температура для строгих и точных ответов по коду
            "max_tokens": 2048
        }

        print(f"🧠 Отправка контекста '{object_name}' в локальную LLM ({self.llm_model})...")
        
        try:
            # Делаем HTTP POST запрос к локальной нейросети
            response = requests.post(f"{self.llm_url}/chat/completions", json=payload, timeout=90)
            response.raise_for_status()
            result_json = response.json()
            return result_json['choices'][0]['message']['content']
        except Exception as e:
            return f"❌ Ошибка взаимодействия с локальной LLM: {e}"
