import psycopg2
import zlib
import re
import uuid
import json
import os

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            return cfg.get("db_1c", {}), cfg.get("db_ai", {})
    return {}, {}

def main():
    print("[🚀] Старт прецизионного восстановления структуры Конфигуратора 1С...")
    cfg_1c, cfg_ai = load_config()
    
    # Открываем коннекты
    conn_1c = psycopg2.connect(**cfg_1c)
    conn_ai = psycopg2.connect(**cfg_ai)
    
    cursor_1c = conn_1c.cursor()
    cursor_ai = conn_ai.cursor()
    
    # 1. Выкачиваем главный системный манифест конфигурации 1С
    print("[🔍] Чтение системного манифеста ConfigMFT из таблицы config 1С...")
    cursor_1c.execute("SELECT binarydata FROM config WHERE filename = 'ConfigMFT';")
    result = cursor_1c.fetchone()
    
    if not result or not result[0]:
        # Если ConfigMFT нет, ищем корневой файл конфигурации 'root'
        cursor_1c.execute("SELECT binarydata FROM config WHERE filename = 'root';")
        result = cursor_1c.fetchone()
        
    if not result or not result[0]:
        print("[❌] Не удалось найти системные манифесты 1С для восстановления имен.")
        return
        
    try:
        # Декомпрессируем манифест
        raw_data = zlib.decompress(bytes(result[0]), -zlib.MAX_WBITS)
        manifest_text = raw_data.decode('utf-8-sig', errors='ignore')
        
        # 2. 🔥 ХАКЕРСКИЙ ПАРСИНГ СТРУКТУРЫ 1С 🔥
        # Ищем вложенные блоки метаданных, где рядом лежат UUID объекта и его реальное имя в Конфигураторе
        # Формат обычно: {"Metadata", ..., "ИмяОбъекта", "СинонимОбъекта", "UUID"}
        print("[🔄] Парсинг логической карты объектов ERP...")
        
        # Находим все UUID и текстовые идентификаторы вокруг них
        uuid_pattern = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
        
        # Карта для распределения по классам Конфигуратора
        class_map = {
            "Catalog.": "Справочники",
            "Document.": "Документы",
            "Report.": "Отчеты",
            "DataProcessor.": "Обработки",
            "CommonModule.": "Общие модули",
            "InformationRegister.": "Регистраторы"
        }
        
        # Очищаем старую нечитаемую структуру в базе ИИ перед заливкой эталона
        cursor_ai.execute("TRUNCATE TABLE ai_metadata_objects CASCADE;")
        conn_ai.commit()
        
        # Извлекаем все строки, похожие на декларацию объектов 1С
        # Платформа описывает их как метаданные классов (например, Document.ЗаказКлиента)
        records_found = 0
        
        # Ищем внутренние системные декларации путей метаданных 1С
        meta_declarations = re.findall(r'"([A-Za-zА-Яа-я0-9_\.]+)"', manifest_text)
        
        # Достаем список всех файлов кодов, которые мы реально загрузили в базу ИИ
        cursor_ai.execute("SELECT code_filename FROM ai_metadata_source_codes;")
        loaded_codes = {row[0] for row in cursor_ai.fetchall()}
        
        print(f"[📊] В базе ИИ найдено {len(loaded_codes)} очищенных BSL-модулей. Начинаем точечный маппинг...")
        
        # Пробегаемся по тексту манифеста регулярным выражением для поиска связок: тип.Имя и UUID
        # Платформа хранит их в виде массивов: {"Document.ЗаказКлиента", "00000000-0000-..."}
        matches = re.findall(r'"([A-Za-z0-9_]+\.[A-Za-z0-9_А-Яа-я]+)".*?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})', manifest_text, re.DOTALL)
        
        mapped_count = 0
        for full_meta_name, obj_uuid in matches:
            obj_uuid_lower = obj_uuid.lower()
            
            # Определяем родительский класс 1С (Документы, Справочники и т.д.)
            object_type = "Общие модули"
            clean_name = full_meta_name
            
            for prefix, rus_class in class_map.items():
                if full_meta_name.startswith(prefix):
                    object_type = rus_class
                    clean_name = full_meta_name.replace(prefix, "")
                    break
            
            # Для найденного объекта проверяем, есть ли у нас для него реальные файлы кодов (.0 или .m)
            for ext in ['.0', '.m']:
                virtual_filename = f"{obj_uuid_lower}{ext}"
                
                # Пишем в дерево ТОЛЬКО если код этого модуля реально существует в базе ИИ!
                if virtual_filename in loaded_codes:
                    # Формируем имя для вывода в ветку: красивое русское имя объекта 1С
                    synonym = clean_name
                    
                    # Записываем эталонную запись строго по схеме вашей таблицы СУБД из pgAdmin:
                    # object_id, object_type, internal_name, synonym, sql_table_name
                    query_insert = """
                        INSERT INTO ai_metadata_objects (object_id, object_type, internal_name, synonym, sql_table_name)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (internal_name) 
                        DO UPDATE SET synonym = EXCLUDED.synonym, object_type = EXCLUDED.object_type;
                    """
                    cursor_ai.execute(query_insert, (obj_uuid_lower, object_type, virtual_filename, synonym, "ERP_STABLE"))
                    mapped_count += 1
                    
        conn_ai.commit()
        print(f"[✅] Идеальное сопоставление завершено! В структуру Конфигуратора успешно заведено {mapped_count} эталонных имен объектов 1С.")
        
    except Exception as e:
        print(f"[💥] Ошибка реверс-инжиниринга манифеста 1С: {e}")
        conn_ai.rollback()
    finally:
        cursor_1c.close()
        cursor_ai.close()
        conn_1c.close()
        conn_ai.close()

if __name__ == "__main__":
    main()
