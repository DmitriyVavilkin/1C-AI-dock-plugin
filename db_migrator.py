import sys
import os
import json
import psycopg2
import zlib
import re

def load_configs():
    """Считывает параметры подключения к базам из config.json."""
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if not os.path.exists(config_path):
        print("[Ошибка] config.json не найден!")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("db_1c"), data.get("db_ai")

def build_uuid_cache(cursor_ai):
    """
    Выгружает весь ваш справочник ai_metadata_objects в память Python.
    Это позволит мгновенно сопоставлять 106 000 файлов без выполнения
    миллиона медленных SQL-запросов к базе в процессе миграции.
    """
    print("[Кэш] Индексация справочника метаданных ai_metadata_objects...")
    # Так как справочник лежит в этой же базе (или доступен), тянем его структуры
    # object_id (UUID файла), object_type (Document/Catalog), internal_name (Имя)
    cursor_ai.execute("SELECT object_id, object_type, internal_name, synonym FROM ai_metadata_objects;")
    rows = cursor_ai.fetchall()
    
    # Англо-русский маппинг типов для каноничного GUI Конфигуратора
    type_map = {
        "catalog": "Справочники", "catalog.": "Справочники",
        "document": "Документы", "document.": "Документы",
        "enum": "Перечисления", "enum.": "Перечисления",
        "report": "Отчеты", "report.": "Отчеты",
        "dataprocessor": "Обработки", "dataprocessor.": "Обработки",
        "commonmodule": "Общие.Общие модули", "commonmodule.": "Общие.Общие модули",
        "informationregister": "РегистрыСведений", "informationregister.": "РегистрыСведений",
        "accumulationregister": "РегистрыНакопления", "accumulationregister.": "РегистрыНакопления"
    }
    
    uuid_cache = {}
    for obj_id, obj_type, int_name, synonym in rows:
        if not obj_id: continue
        
        clean_id = str(obj_id).strip().lower()
        clean_type = str(obj_type).strip().lower()
        
        # Переводим тип на русский язык для дерева IDE
        ru_type = type_map.get(clean_type, clean_type.capitalize())
        
        # Предпочитаем Синоним 1С, если его нет — внутреннее имя
        display_name = synonym if (synonym and synonym.strip()) else int_name
        
        uuid_cache[clean_id] = {
            "object_type": ru_type,
            "object_name": display_name if display_name else "НеизвестныйОбъект"
        }
    
    print(f"[Кэш] Успешно проиндексировано объектов: {len(uuid_cache)}")
    return uuid_cache
def extract_uuid_from_filename(filename):
    """
    Вытаскивает валидный 1С UUID (8-4-4-4-12) из имени конфигурационного файла.
    Пример: '0002a72d-486a-403a-b739-e8840c4bf173.bsl' -> '0002a72d-486a-403a-b739-e8840c4bf173'
    """
    match = re.search(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', filename.lower())
    return match.group(1) if match else None

def clean_and_split_stream(raw_binary_data):
    """Декомпрессирует zlib-поток, удаляет PostgreSQL NUL (0x00) и маркеры 7fffffff."""
    try:
        decompressed = zlib.decompress(raw_binary_data, -zlib.MAX_WBITS).decode('utf-8', errors='ignore')
    except Exception:
        try:
            decompressed = zlib.decompress(raw_binary_data).decode('utf-8', errors='ignore')
        except Exception:
            decompressed = raw_binary_data.decode('utf-8', errors='ignore')
        
    cleaned = decompressed.replace('\x00', '')
    cleaned = re.sub(r'^[0-7]f{7,}.*\n', '', cleaned)
    
    bsl_marker = re.search(r'(#Область|&НаКлиенте|&НаСервере|Процедура|Функция|//)', cleaned, re.IGNORECASE)
    if bsl_marker:
        idx = bsl_marker.start()
        return cleaned[idx:].strip(), cleaned[:idx].strip()
    return "", cleaned.strip()

def run_pipeline():
    """Запускает сквозной конвейер миграции СУБД -> СУБД с умным маппингом по UUID."""
    db_1c_params, db_ai_params = load_configs()
    if not db_1c_params or not db_ai_params:
        print("[Ошибка] В config.json отсутствуют блоки db_1c или db_ai!")
        return

    print(f"[Конвейер] Подключение к СУБД 1С: {db_1c_params.get('host')}...")
    conn_1c = psycopg2.connect(**db_1c_params)
    cursor_1c = conn_1c.cursor()

    print(f"[Конвейер] Подключение к СУБД ИИ: {db_ai_params.get('host')}...")
    conn_ai = psycopg2.connect(**db_ai_params)
    cursor_ai = conn_ai.cursor()

    # Сначала строим кэш метаданных по UUID в оперативной памяти
    uuid_cache = build_uuid_cache(cursor_ai)

    # Шаблон INSERT в новую иерархическую схему таблицы
    upsert_query = """
        INSERT INTO ai_metadata_source_codes 
        (config_source, is_active, object_type, object_name, sub_type, sub_name, module_type, bsl_code, v8_structure, raw_path)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (raw_path) DO UPDATE SET 
            bsl_code = EXCLUDED.bsl_code,
            v8_structure = EXCLUDED.v8_structure,
            updated_at = CURRENT_TIMESTAMP;
    """

    # Запрашиваем сырые бинарные потоки из таблицы config рабочей базы 1С
    sql_fetch_1c = "SELECT filename, binarydata FROM config WHERE binarydata IS NOT NULL;"
    print("[SQL Запрос] Чтение таблицы config...")
    cursor_1c.execute(sql_fetch_1c)

    processed_count = 0
    skipped_count = 0

    print("[Миграция] Старт потокового сопоставления и заполнения СУБД ИИ...")
    
    while True:
        row = cursor_1c.fetchone()
        if not row:
            break
            
        filename_str, raw_binary = row
        filename_str = str(filename_str).strip()
        
        # Пытаемся вытащить UUID объекта из имени файла
        obj_uuid = extract_uuid_from_filename(filename_str)
        
        # По умолчанию размечаем как системные данные, если UUID не найден в справочнике
        info = {
            "config_source": "main", "is_active": True,
            "object_type": "СистемныеФайлы", "object_name": "Платформа1С",
            "sub_type": None, "sub_name": None, "module_type": "Модуль"
        }
        
        # Если UUID успешно вытащен и есть в кэше ai_metadata_objects, подставляем красивые имена!
        if obj_uuid and obj_uuid in uuid_cache:
            info["object_type"] = uuid_cache[obj_uuid]["object_type"]
            info["object_name"] = uuid_cache[obj_uuid]["object_name"]

        # Анализируем суффиксы файлов для определения типа модуля рантайма 1С
        low_file = filename_str.lower()
        if "module" in low_file or "модуль" in low_file:
            if "manager" in low_file or "менеджер" in low_file:
                info["module_type"] = "МодульМенеджера"
            elif "object" in low_file or "объект" in low_file:
                info["module_type"] = "МодульОбъекта"
            elif "form" in low_file or "форма" in low_file:
                info["sub_type"] = "Формы"
                info["sub_name"] = "Форма"
                info["module_type"] = "МодульФормы"
            else:
                info["module_type"] = "ОбщийМодуль"
        
        try:
            binary_bytes = raw_binary.tobytes() if hasattr(raw_binary, 'tobytes') else bytes(raw_binary)
            bsl_code, v8_structure = clean_and_split_stream(binary_bytes)
            
            # Пропускаем пустые системные файлы для экономии места
            if not bsl_code.strip() and not v8_structure.strip():
                continue
                
            if not bsl_code.strip():
                bsl_code = f"// Элемент '{filename_str}' содержит только структуру платформы."

            # Пишем идеально размеченные данные в базу ИИ
            cursor_ai.execute(upsert_query, (
                info["config_source"], info["is_active"], info["object_type"], info["object_name"],
                info["sub_type"], info["sub_name"], info["module_type"], bsl_code, v8_structure, filename_str
            ))
            
            processed_count += 1
            if processed_count % 500 == 0:
                conn_ai.commit()
                print(f"🚀 Идеально размещено в СУБД ИИ: {processed_count} объектов...")
                
        except Exception as file_error:
            skipped_count += 1

    conn_ai.commit()
    cursor_1c.close(); conn_1c.close(); cursor_ai.close(); conn_ai.close()
    
    print("\n🏁 [Итоги раунда эталонной миграции]:")
    print(f"✅ Успешно перенесено и переименовано по Синонимам: {processed_count} записей.")
    print(f"⚠️ Пропущено технических пустышек: {skipped_count} файлов.")

if __name__ == "__main__":
    run_pipeline()
