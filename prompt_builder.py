# prompt_builder.py
import re

class AiPromptBuilder:
    """Промышленный сборщик контекста: сшивает BSL-код, OCR-ошибку рантайма и правила 1С"""
    
    @staticmethod
    def build_hotfix_prompt(prompt_type: str, context_code: str, error_text: str, custom_query: str = "") -> str:
        # Системный гайдлайн, удерживающий модель в рамках стандартов разработки 1С
        system_instructions = (
            "Ты — ведущий эксперт по оптимизации и исправлению кода в 1С:ERP 2.5.\n"
            "Твоя задача — проанализировать предоставленный BSL-код и контекст ошибки рантайма,\n"
            "после чего выдать готовое, безопасное исправление.\n\n"
            "ЖЕСТКИЕ ПРАВИЛА:\n"
            "1. Не изменяй логику проведения документов, если это не требуется для устранения бага.\n"
            "2. Пиши код строго в синтаксисе 1С (BSL). Используй правильные конструкции.\n"
            "3. Выведи только исправленный кусок кода или весь модуль с изменениями. Никакой лишней болтовни.\n"
        )
        
        # Динамическая подмена контекста в зависимости от типа ошибки, распознанной OCR
        error_context = ""
        if prompt_type == "OUT_OF_BOUNDS":
            error_context = "ТИП ОШИБКИ: Индекс находится за пределами границы массива/таблицы значений.\n"
        elif prompt_type == "DB_CONCURRENCY":
            error_context = "ТИП ОШИБКИ: Конфликт объектных блокировок СУБД (Concurrency error).\n"
        elif prompt_type == "NULL_POINTER":
            error_context = "ТИП ОШИБКИ: Попытка обращения к методу объекта через Неопределено/ЗначениеНеЗаполнено.\n"

        # Сборка финального пакета для LM Studio (Qwen2.5-Coder)
        full_prompt = f"{system_instructions}\n"
        full_prompt += f"=== СИСТЕМНЫЙ КОНТЕКСТ ===\n{error_context}"
        full_prompt += f"СЫРОЙ ТЕКСТ ОШИБКИ ИЗ OCR:\n{error_text}\n\n"
        
        if custom_query:
            full_prompt += f"ДОПОЛНИТЕЛЬНЫЙ ЗАПРОС РАЗРАБОТЧИКА:\n{custom_query}\n\n"
            
        full_prompt += f"=== ТЕКУЩИЙ ИСХОДНЫЙ BSL-КОД МОДУЛЯ ===\n{context_code}\n\n"
        full_prompt += "ОТВЕТ АССИСТЕНТА (Исправленный BSL-код):"
        
        return full_prompt
