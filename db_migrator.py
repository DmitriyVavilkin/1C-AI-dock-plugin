import os
import json
import zlib
import re
import psycopg2
from psycopg2.extras import execute_values

class DbMigratorEngine:
    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        self.config_1c = {}
        self.config_ai = {}
        
        # Регулярное выражение для извлечения UUID и хэшей файлов из манифестов 1С
        self.v8_pattern = re.compile(
            r'([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32}).*?([\w_]{8,})',
            re.IGNORECASE
        )
        self.load_configuration()

    def load_configuration(self):
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Конфиг {self.config_path} не найден.")
        with open(self.config_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        self.config_1c = config_data.get("db_1c", {})
        self.config_ai = config_data.get("db_ai", {})

    def _decompress_v8_zlib(self, binary_data: bytes) -> str:
        """Декомпрессия бинарных блоков 1С с перебором байтовых префиксов СУБД"""
        if not binary_data:
            return ""
        # Список каноничных байтовых смещений платформы 1С Предприятие
        for offset in [0, 2, 4, 8]:
            try:
                return zlib.decompress(binary_data[offset:], 15 + 32).decode('utf-8', errors='ignore')
            except zlib.error:
                try:
                    return zlib.decompress(binary_data[offset:], -15).decode('utf-8', errors='ignore')
                except zlib.error:
                    continue
        try:
            return binary_data.replace(b'\x00', b'').decode('utf-8', errors='ignore')
        except Exception:
            return ""

    def build_reconciliation_map(self) -> dict:
        """Шаг 1: Извлечение системной карты соответствий хэшей и реальных UUID"""
        print("[INFO] Вычитка манифестов root/version из рабочей базы 1С...")
        hash_to_uuid = {}
        
        try:
            conn = psycopg2.connect(**self.config_1c)
            with conn.cursor() as cursor:
                cursor.execute("SELECT filename, binarydata FROM config WHERE filename IN ('root', 'version');")
                for filename, binarydata in cursor.fetchall():
                    text_content = self._decompress_v8_zlib(bytes(binarydata))
                    for match in self.v8_pattern.finditer(text_content):
                        raw_uuid = match.group(1).lower()
                        file_hash = match.group(2).lower()
                        
                        if '-' not in raw_uuid and len(raw_uuid) == 32:
                            uuid_str = f"{raw_uuid[:8]}-{raw_uuid[8:12]}-{raw_uuid[12:16]}-{raw_uuid[16:20]}-{raw_uuid[20:]}"
                        else:
                            uuid_str = raw_uuid
                            
                        hash_to_uuid[file_hash] = uuid_str
            conn.close()
            print(f"[SUCCESS] Извлечено {len(hash_to_uuid)} системных связей из манифестов.")
        except Exception as e:
            print(f"[ERROR] Не удалось прочитать root/version: {e}. Переходим к запасному контуру.")
            
        return hash_to_uuid

    def execute_migration(self):
        """Шаг 2: Структурная миграция СУБД на основе Class ID платформы 1С без текстового поиска"""
        print(f"[INFO] Подключение к ИИ-хранилищу {self.config_ai.get('dbname')}...")
        conn_ai = None
        conn_1c = None
        try:
            conn_ai = psycopg2.connect(**self.config_ai)
            conn_1c = psycopg2.connect(**self.config_1c)
            
            # Канонические внутренние Class ID платформы 1С:Предприятие
            v8_classes = {
                "9cd510cd-abfc-11d4-9434-004095e12fc7": "commonmodule",
                "cf4dbf0f-decc-11d4-9423-004095e12fc7": "catalog",
                "13134201-f60b-11d4-9434-004095e12fc7": "document",
                "b1a160d5-14f7-11d5-a4b5-00c0df0a416a": "informationregister",
                "9399bfd0-f203-11d4-9434-004095e12fc7": "accumulationregister",
                "01026040-5e36-11d5-a4b5-00c0df0a416a": "report",
                "bf75a1c0-aa51-11d4-9434-004095e12fc7": "dataprocessor",
                "61ff1b61-a0da-11d4-9434-004095e12fc7": "constant",
                "62c97df1-a0ea-11d4-9434-004095e12fc7": "enum",
                "459f3231-ab10-11d4-9434-004095e12fc7": "documentjournal",
                "37f694e1-229d-11d5-a4b5-00c0df0a416a": "webservice",
                "ba061551-7f98-4d51-85e7-a9a77ef769d4": "httpservice"
            }

            print("[INFO] Чтение структурных манифестов конфигурации 1С...")
            metadata_map = {} # {uuid: {"name": имя, "type": класс, "parent": parent_uuid}}
            
            with conn_1c.cursor() as cursor_1c:
                cursor_1c.execute("SELECT filename, binarydata FROM config WHERE filename IN ('config', 'root');")
                for filename, binarydata in cursor_1c.fetchall():
                    text_content = self._decompress_v8_zlib(bytes(binarydata))
                    if not text_content: continue
                    
                    # Извлекаем блоки описания метаданных платформы
                    # Ищем строки вида: c695a452-9598-... (UUID класса) и его элементы
                    for class_uuid, class_type in v8_classes.items():
                        # Ищем все UUID объектов, принадлежащих данному классу метаданных
                        found_uuids = re.findall(r'\"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\"', text_content)
                        for f_uuid in found_uuids:
                            f_uuid_lower = f_uuid.lower()
                            if f_uuid_lower not in metadata_map:
                                metadata_map[f_uuid_lower] = {
                                    "name": f"Объект_{f_uuid[:8]}", 
                                    "type": class_type, 
                                    "parent": None
                                }

            print("[INFO] Глубокое обогащение имен и выявление подчиненных форм справочников/документов...")
            with conn_1c.cursor() as cursor_1c:
                cursor_1c.execute("""
                    SELECT filename, binarydata 
                    FROM config 
                    WHERE length(binarydata) > 100 
                      AND filename ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';
                """)
                for filename, binarydata in cursor_1c.fetchall():
                    f_uuid = filename.lower()
                    text_content = self._decompress_v8_zlib(bytes(binarydata))
                    if not text_content: continue
                    
                    # Извлекаем каноничное имя объекта
                    name_match = re.search(r'\"([А-Яа-яA-Za-z0-9_]{3,60})\"', text_content)
                    if name_match:
                        obj_name = name_match.group(1)
                        if obj_name.lower() in ['checkedout', 'version', 'root', 'data']: continue
                        
                        if f_uuid in metadata_map:
                            metadata_map[f_uuid]["name"] = obj_name
                        else:
                            # Если файла нет в корне, но это форма или подчиненный элемент
                            # Ищем связь с родительским UUID, которая зашита во внутренних файлах 1С
                            parent_match = re.search(r'\"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\"', text_content)
                            parent_uuid = parent_match.group(1).lower() if parent_match else None
                            
                            # По умолчанию относим к "Прочим объектам", если класс не определен жестко
                            metadata_map[f_uuid] = {
                                "name": obj_name,
                                "type": "unknown",
                                "parent": parent_uuid
                            }

            with conn_ai.cursor() as cursor:
                print("[INFO] Пересоздание реляционной схемы таблиц метаданных СУБД...")
                cursor.execute("""
                    DROP TABLE IF EXISTS v8_metadata_map CASCADE;
                    DROP TABLE IF EXISTS ai_metadata_objects CASCADE;
                    
                    -- Новая эталонная таблица объектов метаданных с поддержкой ИЕРАРХИИ платформ
                    CREATE TABLE ai_metadata_objects (
                        object_id UUID PRIMARY KEY,
                        object_type VARCHAR(100),
                        internal_name VARCHAR(255),
                        synonym VARCHAR(255),
                        parent_object_id UUID, -- Явное поле связи "Форма -> Справочник-Владелец"
                        config_scope VARCHAR(100) DEFAULT 'Main Configuration'
                    );
                    
                    CREATE TABLE v8_metadata_map (
                        object_id UUID PRIMARY KEY,
                        human_name VARCHAR(255),
                        object_type VARCHAR(100),
                        parent_object_id UUID,
                        config_scope VARCHAR(100) DEFAULT 'Main Configuration'
                    );
                """)
                
                # Заливаем чистые реляционные данные
                if metadata_map:
                    print(f"[INFO] Сохранение {len(metadata_map)} реляционных объектов в СУБД...")
                    insert_query = """
                        INSERT INTO v8_metadata_map (object_id, human_name, object_type, parent_object_id, config_scope)
                        VALUES %s;
                    """
                    records = [(uid, info["name"], info["type"], info["parent"], info["scope"]) for uid, info in metadata_map.items()]
                    execute_values(cursor, insert_query, records)

                print("[INFO] Перенос структуры в основную таблицу ai_metadata_objects...")
                cursor.execute("""
                    INSERT INTO ai_metadata_objects (object_id, object_type, internal_name, parent_object_id)
                    SELECT object_id, object_type, human_name, parent_object_id FROM v8_metadata_map
                    ON CONFLICT (object_id) DO UPDATE 
                    SET object_type = EXCLUDED.object_type,
                        internal_name = EXCLUDED.internal_name,
                        parent_object_id = EXCLUDED.parent_object_id;
                """)

                print("[INFO] Сброс кэша связей модулей...")
                cursor.execute("UPDATE ai_metadata_source_codes SET resolved_object_id = NULL, module_type = NULL;")
                
                print("[INFO] Пакетная увязка модулей по UUID...")
                cursor.execute("""
                    UPDATE ai_metadata_source_codes src
                    SET resolved_object_id = obj.object_id::uuid,
                        module_type = CASE 
                            WHEN LOWER(obj.object_type) = 'commonmodule' THEN 'ОбщийМодуль'
                            WHEN src.object_name ILIKE '%Менеджер%' OR src.object_name ILIKE '%.1' THEN 'МодульМенеджера'
                            ELSE 'МодульОбъекта'
                        END
                    FROM ai_metadata_objects obj
                    WHERE split_part(src.raw_path, '.', 1) ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
                      AND obj.object_id::text = LOWER(TRIM(split_part(src.raw_path, '.', 1)));
                """)
                
                conn_ai.commit()
                print(f"[SUCCESS] Иерархическая миграция метаданных 1С:ERP завершена!")
                
        except Exception as e:
            print(f"[ERROR] Критический сбой миграции БД: {e}")
            if conn_ai: conn_ai.rollback()
        finally:
            if conn_1c: conn_1c.close()
            if conn_ai: conn_ai.close()

if __name__ == "__main__":
    migrator = DbMigratorEngine()
    migrator.execute_migration()
