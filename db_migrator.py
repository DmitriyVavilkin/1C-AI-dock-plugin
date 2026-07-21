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
        """Шаг 2: Массовая реконсиляция, создание таблиц и жесткая увязка метаданных по UUID"""
        print(f"[INFO] Подключение к ИИ-хранилищу {self.config_ai.get('dbname')}...")
        conn_ai = None
        try:
            conn_ai = psycopg2.connect(**self.config_ai)
            with conn_ai.cursor() as cursor:
                # 1. Инициализируем структуру таблиц
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS ai_metadata_objects (
                        object_id UUID PRIMARY KEY,
                        object_type VARCHAR(50),
                        internal_name VARCHAR(255),
                        synonym VARCHAR(255),
                        is_extension BOOLEAN DEFAULT FALSE
                    );
                    ALTER TABLE ai_metadata_source_codes ADD COLUMN IF NOT EXISTS resolved_object_id UUID;
                    ALTER TABLE ai_metadata_source_codes ADD COLUMN IF NOT EXISTS module_type VARCHAR(50);
                    ALTER TABLE ai_metadata_source_codes ALTER COLUMN module_type DROP NOT NULL;
                """)
                
                print("[INFO] Сброс старого кэша связей в базе ИИ...")
                cursor.execute("UPDATE ai_metadata_source_codes SET resolved_object_id = NULL, module_type = NULL;")

                # 2. Создаем скоростные индексы для связи UUID и масок имен
                print("[INFO] Создание скоростных функциональных индексов СУБД...")
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_fast_obj_uuid ON ai_metadata_objects (object_id);
                    
                    CREATE INDEX IF NOT EXISTS idx_fast_src_clean_uuid ON ai_metadata_source_codes (
                        LOWER(TRIM(split_part(raw_path, '.', 1)))
                    );
                    
                    -- СВЕРХБЫСТРЫЕ ФУНКЦИОНАЛЬНЫЕ ИНДЕКСЫ ДЛЯ МЯГКОГО ДОБОРА РАСШИРЕНИЙ
                    CREATE INDEX IF NOT EXISTS idx_fast_obj_regexp_name ON ai_metadata_objects (
                        LOWER(REGEXP_REPLACE(internal_name, '[^а-яА-Яa-zA-Z0-9_]', '', 'g'))
                    );
                    
                    CREATE INDEX IF NOT EXISTS idx_fast_obj_regexp_synonym ON ai_metadata_objects (
                        LOWER(REGEXP_REPLACE(synonym, '[^а-яА-Яa-zA-Z0-9_]', '', 'g'))
                    );
                    
                    CREATE INDEX IF NOT EXISTS idx_fast_src_regexp_name ON ai_metadata_source_codes (
                        LOWER(REGEXP_REPLACE(object_name, '[^а-яА-Яa-zA-Z0-9_]', '', 'g'))
                    ) WHERE resolved_object_id IS NULL;
                """)

                print("[INFO] Запуск точной пакетной увязки ERP по бинарным UUID...")
                # Исправлено: Явный каст к ::uuid в SET, безопасное текстовое сравнение в WHERE
                cursor.execute("""
                    UPDATE ai_metadata_source_codes src
                    SET resolved_object_id = obj.object_id::uuid,
                        module_type = CASE 
                            WHEN obj.object_type = 'CommonModule' THEN 'ОбщийМодуль'
                            WHEN src.object_name ILIKE '%Менеджер%' OR src.object_name ILIKE '%.1' THEN 'МодульМенеджера'
                            ELSE 'МодульОбъекта'
                        END
                    FROM ai_metadata_objects obj
                    WHERE split_part(src.raw_path, '.', 1) ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
                      AND obj.object_id::text = LOWER(TRIM(split_part(src.raw_path, '.', 1)));
                """)
                step1 = cursor.rowcount
                print(f"[SUCCESS] Прямым сопоставлением UUID успешно увязано модулей: {step1}")

                # 3. Интеллектуальный мягкий добор для расширений и форм по именам/синонимам
                print("[INFO] Мягкий добор оставшихся кастомных объектов и расширений по функциональным индексам...")
                # Исправлено: Добавлен каст ::uuid для защиты от пересортицы типов
                cursor.execute("""
                    UPDATE ai_metadata_source_codes src
                    SET resolved_object_id = obj.object_id::uuid,
                        module_type = CASE 
                            WHEN obj.object_type = 'CommonModule' THEN 'ОбщийМодуль'
                            ELSE 'МодульОбъекта'
                        END
                    FROM ai_metadata_objects obj
                    WHERE src.resolved_object_id IS NULL
                      AND (
                        LOWER(REGEXP_REPLACE(src.object_name, '[^а-яА-Яa-zA-Z0-9_]', '', 'g')) = LOWER(REGEXP_REPLACE(obj.internal_name, '[^а-яА-Яa-zA-Z0-9_]', '', 'g'))
                        OR 
                        LOWER(REGEXP_REPLACE(src.object_name, '[^а-яА-Яa-zA-Z0-9_]', '', 'g')) = LOWER(REGEXP_REPLACE(obj.synonym, '[^а-яА-Яa-zA-Z0-9_]', '', 'g'))
                      );
                """)
                step2 = cursor.rowcount
                print(f"[SUCCESS] Дополнительно привязано объектов расширений: {step2}")

                # 4. Изолируем служебные файлы платформы
                print("[INFO] Закрытие контура служебных файлов платформы...")
                cursor.execute("""
                    UPDATE ai_metadata_source_codes 
                    SET module_type = 'СистемныйМодуль' 
                    WHERE resolved_object_id IS NULL;
                    
                    ALTER TABLE ai_metadata_source_codes ALTER COLUMN module_type SET NOT NULL;
                """)
                
                conn_ai.commit()
                print(f"[SUCCESS] Миграция ERP завершена! Всего связано: {step1 + step2} модулей.")
                
        except Exception as e:
            print(f"[ERROR] Критический сбой миграции БД: {e}")
            if conn_ai: conn_ai.rollback()
        finally:
            if conn_ai: conn_ai.close()


if __name__ == "__main__":
    migrator = DbMigratorEngine()
    migrator.execute_migration()
