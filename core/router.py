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
        # ВНИМАНИЕ: Убедитесь, что 'erp_test' совпадает с именем вашей публикации!
        self.one_c_service_url = "http://localhost/erp_test/hs/ai/v1/metadata"
        
        self.current_super_pattern = (
            "Ты эксперт по архитектуре и разработке в 1С:ERP. Пиши чистый код BSL. "
            "Следуй стандартам фирмы 1С и БСП. Избегай неоптимальных запросов."
        )
        print(f"[Router] Инициализация. Целевой URL: {self.api_url}")
        print(f"[Router] Имя модели: {self.model_name}")

    def parse_object_name_from_title(self, window_title):
        """
        Парсит заголовок окна Конфигуратора 1С любой сложности.
        'Документ РеализацияТоваровУслуг: Модуль...' -> 'Документ.РеализацияТоваровУслуг'
        """
        if not window_title:
            return "Документ.РеализацияТоваровУслуг"
            
        # Заменяем двоеточие на пробел для корректной работы регулярного выражения
        clean_title = window_title.replace(":", " ")
        match = re.search(r'(Документ|Справочник|РегистрНакопления|РегистрСведений|ЖурналДокументов)\s+([a-zA-Z0-9_а-яА-Я]+)', clean_title)
        if match:
            obj_type = match.group(1)
            obj_name = match.group(2)
            return f"{obj_type}.{obj_name}"
            
        return "Документ.РеализацияТоваровУслуг"

    def get_1c_metadata_context(self, object_name):
        """Запрашивает структуру реквизитов из HTTP-сервиса 1С без авторизации"""
        try:
            print(f"[1С Мост] Запрос метаданных для: {object_name}...")
            res = requests.get(
                self.one_c_service_url, 
                params={"object": object_name}, 
                timeout=30
            )
            if res.status_code == 200:
                print("[1С Мост] Ответ от 1С получен. Форматируем JSON...")
                try:
                    data = res.json()
                    return json.dumps(data, indent=2, ensure_ascii=False)
                except Exception:
                    return res.text
            
            # Если код не 200, просто возвращаем raw-текст ошибки (там будет JSON из нашей Попытки в BSL)
            return f"❌ Ошибка 1С (Код {res.status_code}): {res.text}"
            
        except Exception as e:
            print(f"[1С Мост] Ошибка связи с 1С: {str(e)}")
            return "Метаданные живой базы недоступны (анализ выполняется без контекста конфигурации)."
  
    
    def generate_metadata_tree_text(self, window_title):
        """Строит дерево метаданных из 1С без авторизации и без участия ИИ"""
        target_object = self.parse_object_name_from_title(window_title)
        
        try:
            print(f"[1С Мост Дерево] Запрос структуры для: {target_object}...")
            res = requests.get(
                self.one_c_service_url, 
                params={"object": target_object}, 
                timeout=30
            )
    if res.status_code != 200:
        if res.status_code == 500:
        try:
            # Пытаемся достать описание ошибки из JSON, который вернула наша Попытка...Исключение
            error_data = res.json()
            return f"❌ Ошибка внутри BSL 1С:\n{error_data.get('error', res.text)}"
        except Exception:
            return f"❌ Ошибка 1С (Код 500): {res.text}"
    return f"❌ Ошибка 1С: Не удалось получить данные (Код {res.status_code})"
        

     data = res.json()
            
            # Строим псевдографическое дерево
            tree = []
            tree.append(f"📦 {data.get('Имя')} ({data.get('Синоним')})")
            
            # Реквизиты шапки
            reqs = data.get("РеквизитыШапки", [])
            if reqs:
                tree.append(" ├── 📑 Реквизиты шапки:")
                for i, r in enumerate(reqs):
                    has_tchs = len(data.get("ТабличныеЧасти", [])) > 0
                    char = "└──" if (i == len(reqs) - 1 and not has_tchs) else "├──"
                    tree.append(f" │    {char} 🔹 {r['Имя']} [{r['Тип']}]")
                
            # Табличные части
            tchs = data.get("ТабличныеЧасти", [])
            if tchs:
                tree.append(" └── 📊 Табличные части:")
                for t_idx, t in enumerate(tchs):
                    t_char = "    └──" if t_idx == len(tchs) - 1 else "    ├──"
                    tree.append(f"{t_char} 📁 {t['ИмяТЧ']}")
                    
                    # Реквизиты конкретной ТЧ
                    t_reqs = t.get("Реквизиты", [])
                    for r_idx, tr in enumerate(t_reqs):
                        r_space = "        " if t_idx == len(tchs) - 1 else "    │   "
                        r_char = "└──" if r_idx == len(t_reqs) - 1 else "├──"
                        tree.append(f"{r_space}{r_char} 🔸 {tr['Имя']} [{tr['Тип']}]")
                        
            return "\n".join(tree)
            
        except Exception as e:
            return f"❌ Сбой построения дерева: {str(e)}"

    def route_request(self, user_query, code_context="", window_title=""):
        query_lower = user_query.lower()
        
        # Автоопределение объекта
        target_object = self.parse_object_name_from_title(window_title)
        print(f"[Роутер] Активный объект 1С: {target_object}")
        
        # Запрос контекста метаданных
        metadata_context = self.get_1c_metadata_context(target_object)
        
        # Если нужен глубокий анализ "ПОЧЕМУ" (Нормативная база, ФСБУ, НК РФ)
        if any(w in query_lower for w in ["/law", "закон", "постановлен", "фсбу", "нк рф", "методолог", "почему"]):
            return {
                "contour": "external",
                "action": "Сформирован запрос для Супермодели с метаданными.",
                "payload": self._build_supermodel_prompt(user_query, code_context, metadata_context)
            }
            
        # Локальное ревью кода через Qwen
        return {
            "contour": "internal",
            "action": "Умное локальное Code Review",
            "payload": self._call_lm_studio_review(user_query, code_context, metadata_context)
        }

    def _build_supermodel_prompt(self, query, code, metadata):
        return (
            f"--- ЗАПРОС К СУПЕРМОДЕЛИ (КОНТЕКСТ: АРХИТЕКТУРА И ЗАКОНОДАТЕЛЬСТВО) ---\n"
            f"Вопрос пользователя: {query}\n\n"
            f"1. ИЗМЕНЕННЫЙ КОД В КОНФИГУРАТОРЕ:\n```bsl\n{code}\n```\n"
            f"2. ЖИВЫЕ МЕТАДАННЫЕ ОБЪЕКТА И З БАЗЫ 1С:\n```json\n{metadata}\n```\n\n"
            f"Задание для Супермодели: Дай экспертный ответ, ПОЧЕМУ код написан именно так. "
            f"Свяжи структуру реквизитов 1С с законами, НК РФ или ФСБУ. Напиши паттерн-инструкцию для Qwen."
        )

    def _call_lm_studio_review(self, query, code, metadata):
        url = self.api_url
        
        system_prompt = (
            f"{self.current_super_pattern}\n\n"
            f"Тебе доступна живая структура метаданных этого объекта из базы 1С:\n{metadata}\n"
            f"Используй эти типы данных для точного анализа и исключения галлюцинаций в реквизитах."
        )
        
        user_content = f"Выполни ревью кода 1С:\n```bsl\n{code}\n```\nЗадача: {query}"
        
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            "temperature": 0.1,
            "stream": False
        }
        
        try:
            res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=self.timeout)
            print(f"[Router] Ответ от LM Studio получен. Статус: {res.status_code}")
            
            if res.status_code == 200:
                response_json = res.json()
                choices = response_json.get("choices", [])
                if choices:
                    return choices.get("message", {}).get("content", "")
                return "⚠️ Ошибка: LM Studio вернула пустой массив choices."
            return f"❌ Ошибка API LM Studio: Код {res.status_code}"
        except Exception as e:
            return f"❌ Ошибка отправки запроса в LM Studio: {str(e)}"
