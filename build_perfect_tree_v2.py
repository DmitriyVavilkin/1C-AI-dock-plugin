import psycopg2
import uuid
import json
import os
import re

def load_ai_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f).get("db_ai", {})
    return {"host": "localhost", "port": 5432, "user": "postgres", "password": "", "dbname": "1C_AI_Database"}

def main():
    print("[🚀] Запуск прецизионного контекстного анализа кодов для дерева 1С...")
    cai = load_ai_config()
    
    conn_ai = psycopg2.connect(**cai)
    cursor_ai = conn_ai.cursor()
    
    try:
        # Каскадно очищаем старую нечитаемую структуру
        cursor_ai.execute("TRUNCATE TABLE ai_metadata_objects CASCADE;")
        conn_ai.commit()
        
        # Читаем все успешно загруженные модули
        print("[🔍] Извлечение текстов BSL-модулей из базы ИИ...")
        cursor_ai.execute("SELECT code_filename, source_code FROM ai_metadata_source_codes WHERE source_code IS NOT NULL;")
        rows = cursor_ai.fetchall()
        print(f"[📊] Доступно {len(rows)} модулей для контекстного анализа.")
        
        mapped_count = 0
        
        for code_filename, source_code in rows:
            filename_lower = code_filename.lower()
            # 🔥 ИСПРАВЛЕНО: Безопасно отрезаем последние 2 символа (".0" или ".m"), 
            # чтобы получить чистый логический UUID без всяких split()
            logical_uuid = filename_lower[:-2]
            
            # Дефолтные значения на случай, если маркеры не найдутся
            # object_type = "Общие модули"
            
            # 🔥 ИСПРАВЛЕНО: Сначала берем UUID по индексу 0, а потом приводим к нижнему регистру
            #logical_uuid = filename_lower.split('')[0].lower()
                        
            # Дефолтные значения на случай, если маркеры не найдутся
            object_type = "Общие модули"
            synonym = f"Модуль_{logical_uuid[:8]}"
            
            # Берем первые 20 строк кода для поиска метаданных
            header_lines = "\n".join(source_code.split("\n")[:20])
            
            # 🔥 АЛГОРИТМ 1: Ищем директивы расширений конфигурации (самый точный маппинг)
            # &Вместо("Документ.ЗаказКлиента.МодульОбъекта") или &ИзменениеОригинальногоМетода
            ext_match = re.search(r'(?:Вместо|Перед|После)\s*\(\s*"([^"]+)"', header_lines, re.IGNORECASE)
            
            # 🔥 АЛГОРИТМ 2: Ищем штатные комментарии платформы или разработчиков
            # // Документ.ПередачаТоваровХранителю или // Справочник.Номенклатура.МодульМенеджера
            comment_match = re.search(r'//\s*(Документ|Справочник|Отчет|Обработка|РегистрСведений)\.([A-Za-zА-Яа-я0-9_]+)', header_lines, re.IGNORECASE)
            
            if ext_match:
                # Найдена директива расширения: "Документ.ЗаказКлиента.МодульОбъекта"
                meta_path = ext_match.group(1).split('.')
                if len(meta_path) >= 2:
                    class_name = meta_path
                    synonym = meta_path[1]
                    
                    if "document" in class_name.lower(): object_type = "Документы"
                    elif "catalog" in class_name.lower(): object_type = "Справочники"
                    elif "report" in class_name.lower(): object_type = "Отчеты"
                    elif "processor" in class_name.lower(): object_type = "Обработки"
            
            elif comment_match:
                # Найден комментарий структуры
                rus_class = comment_match.group(1).lower()
                synonym = comment_match.group(2)
                
                if "документ" in rus_class: object_type = "Документы"
                elif "справочник" in rus_class: object_type = "Справочники"
                elif "отчет" in rus_class: object_type = "Отчеты"
                elif "обработка" in rus_class: object_type = "Обработки"
                elif "регистр" in rus_class: object_type = "Регистры"
            
            else:
                # 🔥 АЛГОРИТМ 3: Если это Общий модуль, ищем ключевые маркеры бизнес-логики или имя первой экспортной функции
                if filename_lower.endswith('.m'):
                    object_type = "Общие модули"
                    # Пытаемся вытащить имя функции как имя модуля менеджера
                    biz_fn = re.search(r'(?:Процедура|Функция)\s+([A-Za-zА-Яа-я0-9_]+)', header_lines)
                    if biz_fn:
                        synonym = biz_fn.group(1)
                        if "документ" in header_lines.lower() or "проведение" in header_lines.lower():
                            object_type = "Документы"
                else:
                    # Если это макет регламентированного отчета (наш прошлый случай)
                    if "ФЕДЕРАЛЬНОЕ СТАТИСТИЧЕСКОЕ НАБЛЮДЕНИЕ" in source_code or '{"ru","' in source_code:
                        object_type = "Отчеты (Макеты)"
                        ru_names = re.findall(r'{"ru",\s*"([^"]+)"}', source_code[:2000])
                        if ru_names:
                            synonym = max(ru_names, key=len)
                        else:
                            synonym = f"Форма_Росстата_{logical_uuid[:4]}"
            
            # Формируем окончательный синоним для дерева
            if filename_lower.endswith('.m') and object_type in ["Документы", "Справочники"]:
                synonym = f"{synonym} (Менеджер)"
            elif filename_lower.endswith('.0') and object_type in ["Документы", "Справочники"]:
                synonym = f"{synonym} (Объект)"
                
            # Записываем эталонную запись строго по схеме вашей таблицы СУБД из pgAdmin:
            query_insert = """
                INSERT INTO ai_metadata_objects (object_id, object_type, internal_name, synonym, sql_table_name)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (internal_name) 
                DO UPDATE SET synonym = EXCLUDED.synonym, object_type = EXCLUDED.object_type;
            """
            cursor_ai.execute(query_insert, (logical_uuid, object_type, filename_lower, synonym, "ERP_CONTEXT_MAPPED"))
            mapped_count += 1
            
            if mapped_count % 5000 == 0:
                conn_ai.commit()
                print(f"[🔹] Обработано {mapped_count} модулей...")
                
        conn_ai.commit()
        print(f"[✅] Контекстный анализ завершен! В структуру Конфигуратора успешно заведено {mapped_count} объектов.")
        
    except Exception as e:
        print(f"[💥] Ошибка генерации дерева: {e}")
        conn_ai.rollback()
    finally:
        cursor_ai.close()
        conn_ai.close()

if __name__ == "__main__":
    main()
