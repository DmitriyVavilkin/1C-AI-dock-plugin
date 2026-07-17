import json
import os
import re
import zlib
import uuid  # Критично для генерации field_id на стороне Python
import psycopg2
from psycopg2 import extras

class DBServerManager:
    def __init__(self, config_path="config.json"):
        """
        Инициализация двухконтурного SQL-менеджера ИИ.
        Автоматически подтягивает настройки хостов, баз и доступов из config.json.
        """
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"❌ Конфигурационный файл {config_path} не найден в корне проекта!")
            
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
            
        ibsrv = config_data.get("ibsrv", {})
        pg = config_data.get("postgres", {})
        
        # Контур Чтения (Рабочая СУБД 1С)
        self.db_1c_config = {
            "host": pg.get("host", "172.16.30.204"),
            "database": ibsrv.get("base_name", "mpk_new_vavilkin"),
            "user": pg.get("user", "postgres"),
            "password": pg.get("password", ""),
            "port": pg.get("port", 5432)
        }
        
        # Контур Записи (Изолированная база ИИ)
        self.db_ai_config = {
            "host": pg.get("host", "172.16.30.204"),
            "database": pg.get("database", "1C_AI_Database"),
            "user": pg.get("user", "postgres"),
            "password": pg.get("password", ""),
            "port": pg.get("port", 5432)
        }
        
        self.conn_source = None  # Контур чтения (1С)
        self.conn_ai = None      # Контур записи (ИИ)
        self.connect_db()

    def connect_db(self):
        """Устанавливает стабильное соединение с обоими контурами СУБД"""
        try:
            self.conn_source = psycopg2.connect(**self.db_1c_config)
            self.conn_ai = psycopg2.connect(**self.db_ai_config)
            print("🚀 Двухконтурный SQL-менеджер успешно подключен к базам 1С и ИИ.")
        except Exception as e:
            print(f"❌ Критическая ошибка подключения к базам данных: {e}")
            raise e

    def init_ai_tables(self):
        """Создает необходимую DDL-структуру таблиц в базе ИИ, если их еще нет"""
        with self.conn_ai.cursor() as cur:
            # Таблица объектов
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ai_metadata_objects (
                    object_id VARCHAR(50) PRIMARY KEY,
                    object_type VARCHAR(100),
                    internal_name VARCHAR(255),
                    synonym VARCHAR(255),
                    api_table_name VARCHAR(100)
                );
            """)
            # Таблица реквизитов и полей
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ai_metadata_fields (
                    field_id UUID PRIMARY KEY,
                    object_id VARCHAR(50) REFERENCES ai_metadata_objects(object_id) ON DELETE CASCADE,
                    field_name VARCHAR(255),
                    field_type VARCHAR(100)
                );
            """)
            # Таблица чистых исходных кодов BSL
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ai_source_codes (
                    object_id VARCHAR(50) PRIMARY KEY REFERENCES ai_metadata_objects(object_id) ON DELETE CASCADE,
                    bsl_text TEXT
                );
            """)
            self.conn_ai.commit()

    def decompress_1c_container(self, binary_data):
        """Байтовый сканер для распаковки v8-deflate (zlib raw) напрямую из СУБД 1С"""
        if not binary_data:
            return None
        try:
            try:
                # Стандартный raw deflate (без zlib заголовков, wbits=-15)
                return zlib.decompress(binary_data, -zlib.MAX_WBITS).decode('utf-8-sig', errors='ignore')
            except zlib.error:
                # Обычный zlib поток
                return zlib.decompress(binary_data).decode('utf-8-sig', errors='ignore')
        except Exception:
            return None

    def close(self):
        """Безопасное закрытие пула соединений"""
        if self.conn_source and not self.conn_source.closed: self.conn_source.close()
        if self.conn_ai and not self.conn_ai.closed: self.conn_ai.close()
    # ====================================================================
    # ШАГ 1 И ШАГ 2: ИЗВЛЕЧЕНИЕ ОБЪЕКТОВ И НАСТОЯЩЕГО BSL-КОДА
    # ====================================================================
    def extract_and_cache_source_codes(self):
        """
        Шаг 2: ТОТАЛЬНЫЙ АВТОНОМНЫЙ СБОР КОДА BSL.
        Сканирует таблицу config 1С напрямую, выкачивая ВСЕ существующие модули объектов (.0)
        и модули менеджеров (.m) без привязки к физическим именам таблиц.
        """
        self.init_ai_tables()
        print("\n🚀 Шаг 2: Прямой высокоскоростной сбор BSL-кода из config 1С...")
        
        modules_found = 0
        with self.conn_source.cursor() as cur_src, self.conn_ai.cursor() as cur_ai:
            # Очищаем таблицу кодов перед заливкой
            cur_ai.execute("TRUNCATE ai_source_codes;")
            
            # ВЫБИРАЕМ НАПРЯМУЮ ИЗ 1С: Все файлы, которые являются модулями кода (.0 или .m)
            # Запрос моментально отрабатывает по индексам таблицы config
            query_all_codes = """
                SELECT filename, binarydata 
                FROM config 
                WHERE filename LIKE '%.0' 
                   OR filename LIKE '%.m'
                   OR filename LIKE '%_demo_%.0'
                   OR filename LIKE '%_demo_%.m';
            """
            try:
                cur_src.execute(query_all_codes)
                rows = cur_src.fetchall()
            except Exception as e:
                self.conn_source.rollback()
                print(f"❌ Ошибка прямого запроса кодов к СУБД 1С: {e}")
                return

            print(f"   [Инфо] Найдено {len(rows)} бинарных файлов модулей в СУБД 1С. Начинаем распаковку...")

            for idx, (filename, binarydata) in enumerate(rows, 1):
                # Распаковываем zlib-поток (v8-deflate)
                bsl_text_content = self.decompress_1c_container(binarydata)
                if not bsl_text_content or not bsl_text_content.strip():
                    continue
                
                # Пропускаем служебные заголовки структуры, если они случайно попали
                if bsl_text_content.startswith('{') and ('"#" ' in bsl_text_content or '{"#' in bsl_text_content):
                    continue

                # Идентификатором кода в базе ИИ становится чистый хэш файла (логический UUID 1С)
                clean_obj_id = str(filename).strip().replace('.0', '').replace('.m', '')
                
                # Подстраховка: если объект метаданных для этого кода еще не был создан на Шаге 1,
                # мы автоматически создаем под него заглушку в ai_metadata_objects, чтобы не нарушать foreign key!
                try:
                    cur_ai.execute("""
                        INSERT INTO ai_metadata_objects (object_id, object_type, internal_name, synonym)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (object_id) DO NOTHING;
                    """, (clean_obj_id, "CommonModule", f"Module_{clean_obj_id[:8]}", f"ОбщийМодуль_{clean_obj_id[:8]}"))
                    
                    # Записываем чистый BSL-код в изолированную базу ИИ
                    cur_ai.execute("""
                        INSERT INTO ai_source_codes (object_id, bsl_text)
                        VALUES (%s, %s)
                        ON CONFLICT (object_id) DO UPDATE SET bsl_text = EXCLUDED.bsl_text;
                    """, (clean_uuid, bsl_text_content))
                    
                    modules_found += 1
                except Exception as e:
                    cur_ai.rollback()
                    continue

                if idx % 500 == 0 or idx == len(rows):
                    # Принудительно коммитим пачками для надежности
                    self.conn_ai.commit()
                    print(f" ⏳ Декомпрессия модулей: {idx}/{len(rows)} (Успешно сохранено чистых BSL-файлов: {modules_found})")
            
            self.conn_ai.commit()
            
        print(f"🏁 Шаг 2 завершен! В СУБД ИИ успешно кэшировано {modules_found} чистых BSL-модулей.")
    # ====================================================================
    # ШАГ 3: ИЗВЛЕЧЕНИЕ РЕКВИЗИТОВ С ГЕНЕРАЦИЕЙ UUID В PYTHON
    # ====================================================================
    def extract_and_cache_metadata_fields(self):
        """Шаг 3: Извлекает реквизиты/поля из 1С и кэширует в ai_metadata_fields базы ИИ"""
        self.init_ai_tables()
        
        with self.conn_ai.cursor() as cursor:
            cursor.execute("SELECT object_id, object_type, internal_name FROM ai_metadata_objects;")
            objects = cursor.fetchall()
            
        print(f"\n🚀 Шаг 3: Запуск сканирования реквизитов и полей из 1С для {len(objects)} объектов...")
        
        fields_found = 0
        with self.conn_source.cursor() as cur_src, self.conn_ai.cursor() as cur_ai:
            cur_ai.execute("TRUNCATE ai_metadata_fields;")
            
            for idx, (obj_id, obj_type, obj_name) in enumerate(objects, 1):
                clean_hex = str(obj_id).replace('-', '').lower()
                
                if obj_type == 'Constant':
                    gen_field_id = str(uuid.uuid4())
                    cur_ai.execute("""
                        INSERT INTO ai_metadata_fields (field_id, object_id, field_name, field_type)
                        VALUES (%s, %s, %s, %s);
                    """, (gen_field_id, obj_id, "Значение", "ЛюбойТип"))
                    fields_found += 1
                    continue
                
                # Ищем файл структуры метаданных в 1С по окончанию хэша (обход демо-префиксов)
                suffix_meta = f"%{clean_hex}"
                cur_src.execute("SELECT binarydata FROM config WHERE filename LIKE %s LIMIT 1;", (suffix_meta,))
                row = cur_src.fetchone()
                if not row or not row[0]:
                    continue
                    
                text_container = self.decompress_1c_container(row[0])
                if not text_container:
                    continue
                    
                # Вытаскиваем все дочерние шестнадцатеричные строки (хэши реквизитов)
                all_child_hexs = re.findall(r'[a-f0-9]{32}', text_container, re.IGNORECASE)
                
                for child_hex in set(all_child_hexs):
                    if child_hex == clean_hex:
                        continue
                        
                    # Запрашиваем файл конкретного реквизита в СУБД 1С по LIKE
                    suffix_child = f"%{child_hex.lower()}"
                    cur_src.execute("SELECT binarydata FROM config WHERE filename LIKE %s LIMIT 1;", (suffix_child,))
                    child_row = cur_src.fetchone()
                    if not child_row or not child_row[0]:
                        continue
                        
                    potential_field = self.decompress_1c_container(child_row[0])
                    if not potential_field:
                        continue
                        
                    clean_field_text = re.sub(r'\s+', ' ', potential_field)
                    
                    if '"ru"' in clean_field_text:
                        name_match = re.search(r'\}[\s,]*"([^"]+)"', clean_field_text)
                        if name_match:
                            f_name = name_match.group(1)
                            
                            if len(f_name) > 100 or f_name in [obj_name, "ru", "en", "ODataSettings", "ExtensionsInfo"]:
                                continue
                                
                            f_type = "Ссылка/Составной"
                            if '{"S"}' in clean_field_text or '"S"' in clean_field_text:
                                f_type = "Строка"
                            elif '{"N"}' in clean_field_text or '"N"' in clean_field_text:
                                f_type = "Число"
                            elif '{"D"}' in clean_field_text or '"D"' in clean_field_text:
                                f_type = "Дата"
                            elif '{"B"}' in clean_field_text or '"B"' in clean_field_text:
                                f_type = "Булево"
                                
                            # Генерируем UUID в Python и делаем INSERT
                            gen_field_id = str(uuid.uuid4())
                            cur_ai.execute("""
                                INSERT INTO ai_metadata_fields (field_id, object_id, field_name, field_type)
                                VALUES (%s, %s, %s, %s);
                            """, (gen_field_id, obj_id, f_name, f_type))
                            fields_found += 1                                

                if idx % 200 == 0 or idx == len(objects):
                    print(f" ⏳ Обработано объектов на наличие полей: {idx}/{len(objects)} (Всего полей найдено: {fields_found})")
                    
        self.conn_ai.commit()
        print(f"🏁 Сбор полей завершен! В СУБД ИИ успешно кэшировано {fields_found} реквизитов.")

# Функция для полной сквозной синхронизации баз
def run_full_sync():
    manager = DBServerManager()
    try:
        #manager.and_cache_extract_metadata_objects()
        #manager.extract_and_cache_metadata_objects()
        manager.extract_and_cache_source_codes()
        manager.extract_and_cache_metadata_fields()
        print("\n🎉 ВСЕ ЭТАПЫ ДВУХКОНТУРНОЙ СИНХРОНИЗАЦИИ УСПЕШНО ВЫПОЛНЕНЫ!")
    finally:
        manager.close()

if __name__ == "__main__":
    run_full_sync()
