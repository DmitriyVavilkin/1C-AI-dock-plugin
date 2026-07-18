import psycopg2
import re
import zlib

DB_PARAMS = {
    "dbname": "1C_AI_Database", "user": "postgres", "password": "your_password", "host": "localhost", "port": 5432
}

def init_database():
    """Создает отказоустойчивую структуру БД с поддержкой расширений."""
    print("[БД] Пересоздание универсальной таблицы...")
    conn = psycopg2.connect(**DB_PARAMS); cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS ai_metadata_source_codes CASCADE;")
    cursor.execute("""
        CREATE TABLE ai_metadata_source_codes (
            id SERIAL PRIMARY KEY,
            config_source VARCHAR(100) NOT NULL DEFAULT 'main',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            object_type VARCHAR(100) NOT NULL,
            object_name VARCHAR(255) NOT NULL,
            sub_type VARCHAR(100),
            sub_name VARCHAR(255),
            module_type VARCHAR(100) NOT NULL,
            bsl_code TEXT NOT NULL,
            v8_structure TEXT,
            raw_path TEXT UNIQUE NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("CREATE INDEX idx_config_source ON ai_metadata_source_codes (config_source, is_active);")
    cursor.execute("CREATE INDEX idx_metadata_tree ON ai_metadata_source_codes (config_source, object_type, object_name);")
    conn.commit(); cursor.close(); conn.close()
    print("[БД] База данных готова к любой версии выгрузки 1С.")

def decompose_1c_path(raw_path, config_source='main'):
    """
    Адаптивный маркерный парсер путей 1С. 
    Распознает старый Конфигуратор, новый формат платформ и выгрузки EDT (английские имена папок).
    """
    normalized = raw_path.replace('/', '.').replace('\\', '.').replace('.bsl', '').replace('.txt', '')
    parts = [p for p in normalized.split('.') if p]
    
    # Базовый конфиг на случай полной неопределенности
    info = {
        "config_source": config_source, "is_active": True,
        "object_type": "Общие.Модули", "object_name": "ГлобальныйКонтекст",
        "sub_type": None, "sub_name": None, "module_type": "Модуль"
    }
    if not parts: return info

    # Словари нормализации типов (EDT/Новая платформа -> Канонический русский GUI)
    type_lexicon = {
        "catalog": "Справочники", "catalogs": "Справочники", "справочник": "Справочники",
        "document": "Документы", "documents": "Документы", "документ": "Документы",
        "enum": "Перечисления", "enums": "Перечисления", "перечисление": "Перечисления",
        "report": "Отчеты", "reports": "Отчеты", "отчет": "Отчеты",
        "dataprocessor": "Обработки", "dataprocessors": "Обработки", "обработка": "Обработки",
        "commonmodule": "Общие.Общие модули", "commonmodules": "Общие.Общие модули", "общиймодуль": "Общие.Общие модули",
        "informationregister": "РегистрыСведений", "informationregisters": "РегистрыСведений", "регистрсведений": "РегистрыСведений",
        "accumulationregister": "РегистрыНакопления", "accumulationregisters": "РегистрыНакопления", "регистрнакопления": "РегистрыНакопления"
    }

    # Поиск ключевых маркеров типов объектов внутри пути
    detected_type = None
    type_idx = -1
    for i, part in enumerate(parts):
        lower_part = part.lower()
        if lower_part in type_lexicon:
            detected_type = type_lexicon[lower_part]
            type_idx = i
            break

    # Специфичные глобальные модули платформы
    root_modules = {
        "managedapplicationmodule": "МодульУправляемогоПриложения",
        "ordinaryapplicationmodule": "МодульОбычногоПриложения",
        "sessionmodule": "МодульСеанса",
        "externalconnectionmodule": "МодульВнешнегоСоединения"
    }

    # Проверяем модули корня
    for part in parts:
        if part.lower() in root_modules:
            info["object_type"] = "Конфигурация"
            info["object_name"] = f"Корень ({config_source})"
            info["module_type"] = root_modules[part.lower()]
            return info

    if detected_type and type_idx != -1:
        info["object_type"] = detected_type
        
        # Имя объекта обычно идет сразу ПОСЛЕ типа (например: /Catalogs/Номенклатура/...)
        if type_idx + 1 < len(parts):
            info["object_name"] = parts[type_idx + 1]
            
        # Ищем вложенные сущности: Формы или Макеты
        for j in range(type_idx + 2, len(parts)):
            p_low = parts[j].lower()
            if p_low in ["form", "forms", "форма", "формы"]:
                info["sub_type"] = "Формы"
                if j + 1 < len(parts): info["sub_name"] = parts[j + 1]
                info["module_type"] = "МодульФормы"
                break
            elif p_low in ["template", "templates", "макет", "макеты"]:
                info["sub_type"] = "Макеты"
                if j + 1 < len(parts): info["sub_name"] = parts[j + 1]
                info["module_type"] = "МодульМакета"
                break
                
        # Если это просто общий модуль или модуль объекта без форм
        if not info["sub_type"]:
            last_part = parts[-1].lower()
            if "manager" in last_part or "менеджер" in last_part:
                info["module_type"] = "МодульМенеджера"
            elif "object" in last_part or "объект" in last_part:
                info["module_type"] = "МодульОбъекта"
            else:
                info["module_type"] = parts[-1] # сохраняем как есть
    else:
        # Резервный плоский разбор, если маркеры не найдены
        info["object_type"] = parts[0]
        if len(parts) > 1: info["object_name"] = parts[1]
        if len(parts) > 2: info["module_type"] = parts[-1]

    return info

def clean_and_split_stream(raw_binary_data):
    """Декомпрессирует поток, жестко вырезает NUL-байты и отделяет BSL от v8text метаданных."""
    try: decompressed = zlib.decompress(raw_binary_data, -zlib.MAX_WBITS).decode('utf-8', errors='ignore')
    except Exception: decompressed = raw_binary_data.decode('utf-8', errors='ignore')
        
    cleaned = decompressed.replace('\x00', '')
    cleaned = re.sub(r'^[0-7]f{7,}.*\n', '', cleaned)
    
    bsl_marker = re.search(r'(#Область|&НаКлиенте|&НаСервере|Процедура|Функция|//)', cleaned, re.IGNORECASE)
    if bsl_marker:
        idx = bsl_marker.start()
        return cleaned[idx:].strip(), cleaned[:idx].strip()
    return "", cleaned.strip()

def save_metadata_node(raw_path, raw_binary, config_source='main'):
    """Безопасный UPSERT узла метаданных в иерархическую БД."""
    bsl_code, v8_structure = clean_and_split_stream(raw_binary)
    if not bsl_code.strip():
        bsl_code = f"// Модуль '{raw_path}' не содержит исполняемого кода BSL."

    info = decompose_1c_path(raw_path, config_source)
    conn = psycopg2.connect(**DB_PARAMS); cursor = conn.cursor()
    
    query = """
        INSERT INTO ai_metadata_source_codes 
        (config_source, is_active, object_type, object_name, sub_type, sub_name, module_type, bsl_code, v8_structure, raw_path)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (raw_path) DO UPDATE SET 
            bsl_code = EXCLUDED.bsl_code, v8_structure = EXCLUDED.v8_structure, updated_at = CURRENT_TIMESTAMP;
    """
    cursor.execute(query, (info["config_source"], info["is_active"], info["object_type"], info["object_name"], info["sub_type"], info["sub_name"], info["module_type"], bsl_code, v8_structure, raw_path))
    conn.commit(); cursor.close(); conn.close()

if __name__ == "__main__":
    init_database()
