import psycopg2
from psycopg2.extras import execute_values
import zlib
import re
import uuid

class AIDatabaseManger:
    def __init__(self, source_db_config, ai_db_config):
        """
        Инициализация двухконтурного менеджера СУБД.
        source_db_config: параметры подключения к боевой/тестовой СУБД 1С (mpk_new_vavilkin)
        ai_db_config: параметры подключения к изолированной базе ИИ-IDE (1C_AI_Database)
        """
        self.source_params = source_db_config
        self.ai_params = ai_db_config
        
        self.conn_source = None  # Контур ЧТЕНИЯ (1С)
        self.conn_ai = None      # Контур ЗАПИСИ (ИИ)

    def connect(self):
        """Установка соединений с обеими СУБД"""
        try:
            # Подключаемся к источнику 1С
            self.conn_source = psycopg2.connect(**self.source_params)
            # Подключаемся к базе проекта ИИ
            self.conn_ai = psycopg2.connect(**self.ai_params)
            self.conn_ai.autocommit = True # Автокоммит для ИИ базы, чтобы данные сразу шли на диск
            return True
        except Exception as e:
            print(f"❌ Ошибка инициализации контуров СУБД: {e}")
            return False

    def disconnect(self):
        """Закрытие всех соединений"""
        if self.conn_source:
            self.conn_source.close()
        if self.conn_ai:
            self.conn_ai.close()

    def decompress_1c_container(self, binary_data):
        """Профессиональный распаковщик составных контейнеров 1С (CFont stream)"""
        if not binary_data:
            return ""
        raw_bytes = bytes(binary_data)
        for offset in range(0, min(128, len(raw_bytes))):
            try:
                decompressed = zlib.decompress(raw_bytes[offset:], -zlib.MAX_WBITS)
                text = decompressed.decode('utf-8', errors='ignore')
                if "{" in text:
                    return text[text.find("{"):]
            except Exception:
                try:
                    decompressed = zlib.decompress(raw_bytes[offset:], zlib.MAX_WBITS)
                    text = decompressed.decode('utf-8', errors='ignore')
                    if "{" in text:
                        return text[text.find("{"):]
                except Exception:
                    continue
        return ""
    def get_raw_dbnames(self):
        """Выкачивает и декомпрессирует DBNames из таблицы Params источника 1С"""
        if not self.conn_source:
            return None

        with self.conn_source.cursor() as cursor:
            for table_name in ['Params', 'params', 'PARAMS']:
                try:
                    query = f"SELECT binarydata FROM {table_name} WHERE filename = 'DBNames';"
                    cursor.execute(query)
                    row = cursor.fetchone()
                    if row and row[0]:
                        print(f"📦 Бинарный блок DBNames прочитан из базы 1С (таблица '{table_name}').")
                        return self.decompress_1c_container(row[0])
                except Exception:
                    # КРИТИЧЕСКИ ВАЖНО: сбрасываем ошибку транзакции в Postgres,
                    # чтобы сервер разрешил выполнить следующий запрос к таблице с другим регистром!
                    self.conn_source.rollback()
                    continue
            print("❌ Строка 'DBNames' не найдена в СУБД 1С.")
            return None

    def get_object_real_name(self, object_uuid):
        """Точечно считывает UUID из таблицы Config 1С и вытаскивает русское имя объекта"""
        if not self.conn_source:
            return None

        with self.conn_source.cursor() as cursor:
            for table_name in ['config', 'Config', 'CONFIG']:
                try:
                    query = f"SELECT binarydata FROM {table_name} WHERE filename = %s;"
                    cursor.execute(query, (object_uuid,))
                    row = cursor.fetchone()
                    if row and row:
                        text_data = self.decompress_1c_container(row)
                        if text_data:
                            clean_text = re.sub(r'\s+', ' ', text_data)
                            name_match = re.search(r'\}[\s,]*"([^"]+)"', clean_text)
                            if name_match:
                                return name_match.group(1)
                except Exception:
                    self.conn_source.rollback()
                    continue
        return None

    def parse_and_sync_metadata(self):
        """Оркестратор структуры: читает из 1С, обогащает именами и пишет в базу ИИ"""
        raw_dbnames = self.get_raw_dbnames()
        if not raw_dbnames:
            return
            
        print("🔍 Фильтрация и интеллектуальный анализ бизнес-объектов 1С...")
        clean_text = re.sub(r'\s+', ' ', raw_dbnames)
        pattern = r'\{([a-f0-9\-]{36}),\s*"([^"]+)",\s*(\d+)\}'
        matches = re.findall(pattern, clean_text)

        if not matches:
            print("📭 Не удалось выделить объекты из структуры DBNames.")
            return

        business_objects = []
        for uuid, internal_name, sql_id in matches:
            if uuid == "00000000-0000-0000-0000-000000000000":
                continue

            if internal_name.startswith('Reference') and not 'Field' in internal_name:
                obj_type = "Catalog"
            elif internal_name.startswith('Document') and not any(x in internal_name for x in ['Field', 'ChngR', 'VT']):
                obj_type = "Document"
            elif internal_name.startswith('InfoReg') and not any(x in internal_name for x in ['Field', 'ChngR', 'Dim', 'Rec']):
                obj_type = "InformationRegister"
            elif internal_name.startswith('AccumReg') and not any(x in internal_name for x in ['Field', 'ChngR', 'Dim', 'Rec', 'Rg']):
                obj_type = "AccumulationRegister"
            elif internal_name.startswith('Const') and not 'Field' in internal_name:
                obj_type = "Constant"
            else:
                continue

            business_objects.append({
                'uuid': uuid,
                'type': obj_type,
                'sql_table': f"_{internal_name.lower()}"
            })

        print(f"🎯 Найдено {len(business_objects)} бизнес-объектов. Начинаем обогащение именами из СУБД 1С...")
        
        final_records = []
        for idx, obj in enumerate(business_objects, 1):
            real_name = self.get_object_real_name(obj['uuid'])
            display_name = real_name if real_name else obj['sql_table']
            full_display_name = f"{obj['type']}.{display_name}"
            
            if idx % 50 == 0 or idx == len(business_objects):
                print(f" ⏳ Обработано объектов: {idx}/{len(business_objects)} ({full_display_name})")
                
            final_records.append((obj['uuid'], obj['type'], display_name, display_name))

        self.init_ai_tables()
        
        with self.conn_ai.cursor() as cursor:
            print("🧹 Очистка старой структуры в изолированной базе ИИ...")
            cursor.execute("TRUNCATE ai_metadata_objects CASCADE;")
            
            print(f"💾 Запись {len(final_records)} обогащенных объектов в 1C_AI_Database...")
            execute_values(cursor, """
                INSERT INTO ai_metadata_objects (object_id, object_type, internal_name, synonym)
                VALUES %s
                ON CONFLICT (object_id) DO NOTHING;
            """, final_records)
            
        print("🏁 Синхронизация структуры завершена! Данные сохранены в базе проекта.")
    def extract_and_cache_bsl_modules(self):
        """Выкачивает BSL-код из СУБД 1С и кэширует в хранилище СУБД ИИ"""
        self.init_ai_tables()
        
        # Читаем список объектов из нашей базы ИИ (conn_ai)
        with self.conn_ai.cursor() as cursor:
            cursor.execute("SELECT object_id, object_type, internal_name FROM ai_metadata_objects;")
            objects = cursor.fetchall()
            
        print(f"\n🚀 Запуск сканирования BSL-кода из 1С для {len(objects)} объектов...")
        
        modules_found = 0
        # Читаем из 1С, пишем в ИИ
        with self.conn_source.cursor() as cur_src, self.conn_ai.cursor() as cur_ai:
            cur_ai.execute("TRUNCATE ai_source_codes;")
            
            for idx, (obj_id, obj_type, obj_name) in enumerate(objects, 1):
                if obj_type not in ['Catalog', 'Document']:
                    continue
                    
                cur_src.execute("SELECT binarydata FROM config WHERE filename = %s;", (obj_id,))
                row = cur_src.fetchone()
                if not row or not row[0]:
                    continue
                    
                text_container = self.decompress_1c_container(row[0])
                if not text_container:
                    continue
                    
                all_child_uuids = re.findall(r'[a-f0-9\-]{36}', text_container)
                
                for child_uuid in set(all_child_uuids):
                    if child_uuid == obj_id:
                        continue
                        
                    cur_src.execute("SELECT binarydata FROM config WHERE filename = %s;", (child_uuid,))
                    child_row = cur_src.fetchone()
                    if not child_row or not child_row[0]:
                        continue
                        
                    potential_code = self.decompress_1c_container(child_row[0])
                    
                    if potential_code and any(x in potential_code for x in ['Процедура', 'Функция', 'КонецПроцедуры', 'КонецФункции', '//']):
                        mod_type = "ObjectModule" if "ЭтотОбъект" in potential_code else "ManagerModule"
                        
                        cur_ai.execute("""
                            INSERT INTO ai_source_codes (module_id, object_id, module_type, bsl_text, md5_hash)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (module_id) DO NOTHING;
                        """, (child_uuid, obj_id, mod_type, potential_code, "legacy"))
                        
                        modules_found += 1
                        
                if idx % 100 == 0 or idx == len(objects):
                    print(f" ⏳ Проверено объектов на наличие кода: {idx}/{len(objects)} (Найдено модулей: {modules_found})")
                    
        print(f"🏁 Сбор кода завершен! В СУБД ИИ успешно сохранено {modules_found} BSL-модулей.")

    def extract_and_cache_metadata_fields(self):
        """Извлекает реквизиты/поля из 1С и кэширует в ai_metadata_fields базы ИИ"""
        self.init_ai_tables()
        
        with self.conn_ai.cursor() as cursor:
            cursor.execute("SELECT object_id, object_type, internal_name FROM ai_metadata_objects;")
            objects = cursor.fetchall()
            
        print(f"\n🚀 Запуск сканирования реквизитов и полей из 1С для {len(objects)} объектов...")
        
        fields_found = 0
        with self.conn_source.cursor() as cur_src, self.conn_ai.cursor() as cur_ai:
            cur_ai.execute("TRUNCATE ai_metadata_fields;")
            
            for idx, (obj_id, obj_type, obj_name) in enumerate(objects, 1):
                if obj_type == 'Constant':
                    cur_ai.execute("""
                        INSERT INTO ai_metadata_fields (object_id, field_name, field_type)
                        VALUES (%s, %s, %s);
                    """, (obj_id, "Значение", "ЛюбойТип"))
                    fields_found += 1
                    continue
                
                cur_src.execute("SELECT binarydata FROM config WHERE filename = %s;", (obj_id,))
                row = cur_src.fetchone()
                if not row or not row[0]:
                    continue
                    
                text_container = self.decompress_1c_container(row[0])
                if not text_container:
                    continue
                    
                all_child_uuids = re.findall(r'[a-f0-9\-]{36}', text_container)
                
                for child_uuid in set(all_child_uuids):
                    if child_uuid == obj_id:
                        continue
                        
                    cur_src.execute("SELECT binarydata FROM config WHERE filename = %s;", (child_uuid,))
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
                            cur_ai.execute("""
                                INSERT INTO ai_metadata_fields (object_id, field_name, field_type)
                                VALUES (%s, %s, %s);
                            """, (obj_id, f_name, f_type)) # Передаем строго 3 аргумента под 3 знака %s
                            fields_found += 1                                

                if idx % 200 == 0 or idx == len(objects):
                    print(f" ⏳ Обработано объектов на наличие полей: {idx}/{len(objects)} (Всего полей найдено: {fields_found})")
                    
        print(f"🏁 Сбор полей завершен! В СУБД ИИ успешно кэшировано {fields_found} реквизитов.")

    def init_ai_tables(self):
        """Создает таблицы строго в служебной СУБД ИИ (1C_AI_Database)"""
        with self.conn_ai.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_metadata_objects (
                    object_id VARCHAR(100) PRIMARY KEY,
                    object_type VARCHAR(50),
                    internal_name VARCHAR(255),
                    synonym VARCHAR(255)
                );
                CREATE TABLE IF NOT EXISTS ai_metadata_fields (
                    field_id SERIAL PRIMARY KEY,
                    object_id VARCHAR(100) REFERENCES ai_metadata_objects(object_id) ON DELETE CASCADE,
                    field_name VARCHAR(255),
                    field_type VARCHAR(100)
                );
                CREATE TABLE IF NOT EXISTS ai_source_codes (
                    module_id VARCHAR(100) PRIMARY KEY,
                    object_id VARCHAR(100) REFERENCES ai_metadata_objects(object_id) ON DELETE CASCADE,
                    module_type VARCHAR(50),
                    bsl_text TEXT,
                    md5_hash VARCHAR(32)
                );
            """)
