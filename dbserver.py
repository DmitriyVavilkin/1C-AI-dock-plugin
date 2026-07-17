import os
import json
import zlib
import re
import uuid
import psycopg2

class DBServerManager:
    def __init__(self):
        """Инициализация менеджера баз данных и загрузка настроек из config.json"""
        print("[🚀] Инициализация процесса изнутри dbserver.py...")
        self.config_path = os.path.join(os.path.dirname(__file__), 'config.json')
        self.config = self._load_config()
        
        # Переменные для хранения открытых соединений СУБД
        self.conn_1c = None
        self.conn_ai = None
        
        # Открываем сессии при создании экземпляра класса
        self._establish_all_connections()

    def _load_config(self):
        """Загрузка параметров подключения"""
        if not os.path.exists(self.config_path):
            # Дефолтный каркас, если файла нет
            return {
                "db_1c": {"host": "localhost", "port": 5432, "user": "postgres", "password": "", "dbname": "trade"},
                "db_ai": {"host": "localhost", "port": 5432, "user": "postgres", "password": "", "dbname": "1C_AI_Database"}
            }
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _establish_all_connections(self):
        """Прямое подключение к обоим контурам СУБД"""
        try:
            # 1. Подключаемся к рабочей базе 1С
            c1c = self.config.get("db_1c", {})
            self.conn_1c = psycopg2.connect(
                host=c1c.get("host"), port=c1c.get("port"),
                user=c1c.get("user"), password=c1c.get("password"),
                dbname=c1c.get("dbname")
            )
            
            # 2. Подключаемся к базе ИИ (1C_AI_Database)
            cai = self.config.get("db_ai", {})
            self.conn_ai = psycopg2.connect(
                host=cai.get("host"), port=cai.get("port"),
                user=cai.get("user"), password=cai.get("password"),
                dbname=cai.get("dbname")
            )
            print("🚀 Двухконтурный SQL-менеджер успешно подключен к базам 1С и ИИ.")
        except Exception as e:
            print(f"[❌] Критическая ошибка при подключении к СУБД: {e}")
    def init_ai_database_tables(self):
        """Создание необходимых таблиц в базе данных 1C_AI_Database"""
        cursor = self.conn_ai.cursor()
        try:
            # Основная таблица для хранения структуры конфигурации и исходных BSL-кодов
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_metadata_objects (
                    id UUID PRIMARY KEY,
                    filename VARCHAR(255) UNIQUE,
                    object_name VARCHAR(255),
                    object_type VARCHAR(100),
                    logical_uuid VARCHAR(50),
                    source_code TEXT,
                    is_active BOOLEAN DEFAULT TRUE
                );
            """)
            
            # Дополнительная таблица для хранения кэшированных реквизитов (Шаг 3)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_cached_attributes (
                    id UUID PRIMARY KEY,
                    object_uuid VARCHAR(50),
                    attribute_name VARCHAR(255),
                    attribute_type VARCHAR(100),
                    meta_data JSONB
                );
            """)
            
            cursor.execute("""     
                 CREATE TABLE IF NOT EXISTS ai_hotfix_history (
                    patch_id UUID PRIMARY KEY,
                    target_filename VARCHAR(255),       -- имя файла в config (например, uuid.0)
                    original_binary_backup BYTEA,       -- СЫРОЙ бинарник 1С до исправления (для отката)
                    patch_bsl_code TEXT,                -- чистый BSL-код, который внедрил ИИ
                    developer_comment TEXT,             -- описание инцидента / промпт
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_rolled_back BOOLEAN DEFAULT FALSE
                );
            """)
            self.conn_ai.commit()
            print("[✅] Служебные таблицы в базе 1C_AI_Database успешно проверены/созданы.")
        except Exception as e:
            self.conn_ai.rollback()
            print(f"[❌] Ошибка инициализации таблиц базы ИИ: {e}")
        finally:
            cursor.close()

    def cache_attributes_batch(self, attributes_list):
        """
        Массовое кэширование реквизитов в базу ИИ (устраняет падение NotNullViolation
        за счет генерации UUID на стороне Python)
        """
        if not attributes_list:
            return
            
        cursor = self.conn_ai.cursor()
        query = """
            INSERT INTO ai_cached_attributes (id, object_uuid, attribute_name, attribute_type, meta_data)
            VALUES (%s, %s, %s, %s, %s);
        """
        try:
            # attributes_list содержит кортежи/списки: (object_uuid, name, attr_type, meta_json)
            batch_data = []
            for item in attributes_list:
                ai_uuid = str(uuid.uuid4())  # Генерация стабильного UUID4 на стороне Python
                batch_data.append((ai_uuid, item[0], item[1], item[2], json.dumps(item[3])))
                
            cursor.executemany(query, batch_data)
            self.conn_ai.commit()
            print(f"[📊] Успешно закэшировано {len(batch_data)} реквизитов в базу ИИ.")
        except Exception as e:
            self.conn_ai.rollback()
            print(f"[❌] Ошибка пакетного сохранения реквизитов: {e}")
        finally:
            cursor.close()
    def sync_metadata_structure(self):
        """
        Шаг 2.1: Автономный парсинг структуры метаданных конфигурации 1С из таблицы config 
        и ее сохранение в базу данных 1C_AI_Database.
        """
        print("[🔄] Шаг 1: Запуск парсинга структуры метаданных конфигурации 1С...")
        
        # Гарантируем, что целевые таблицы существуют перед выгрузкой
        self.init_ai_database_tables()
        
        cursor_1c = self.conn_1c.cursor()
        cursor_ai = self.conn_ai.cursor()
        
        # Запрос выкачивает манифесты 'root', 'metadata' и системные файлы описания классов
        query_1c = "SELECT filename, binarydata FROM config WHERE filename IN ('root', 'metadata') OR filename LIKE '__________-____-____-____-____________';"
        
        try:
            cursor_1c.execute(query_1c)
            records = cursor_1c.fetchall()
            print(f"[📊] Из таблицы config 1С получено {len(records)} системных структурных манифестов.")
            
            objects_discovered = 0
            uuid_pattern = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
            
            # Англо-русская карта метаданных для красивого дерева в PyQt6 GUI
            metadata_map = {
                "Catalogs": "Справочник",
                "Documents": "Документ",
                "Reports": "Отчет",
                "DataProcessors": "Обработка",
                "InformationRegisters": "РегистрСведений",
                "AccumulationRegisters": "РегистрНакопления",
                "CommonModules": "ОбщийМодуль",
                "ChartsOfCharacteristicTypes": "ПланВидовХарактеристик",
                "BusinessProcesses": "БизнесПроцесс"
            }
            
            for filename, binarydata in records:
                if not binarydata:
                    continue
                    
                try:
                    # Распаковываем бинарные данные raw deflate (wbits=-zlib.MAX_WBITS)
                    raw_data = zlib.decompress(bytes(binarydata), -zlib.MAX_WBITS)
                    text_content = raw_data.decode('utf-8-sig', errors='ignore')
                    
                    found_uuids = uuid_pattern.findall(text_content)
                    
                    for obj_uuid in set(found_uuids):
                        obj_uuid_lower = obj_uuid.lower()
                        
                        pos = text_content.find(obj_uuid)
                        context = text_content[max(0, pos-150):min(len(text_content), pos+200)]
                        
                        # Извлекаем строковые русские/английские имена объектов 1С в кавычках
                        names = re.findall(r'"([A-Za-zА-Яа-я0-9_]+)"', context)
                        if not names:
                            continue
                            
                        object_name = names[0]
                        
                        # Фильтруем системный мусор платформы 1С
                        if object_name in ['Metadata', 'Root', 'Version', 'DataHistory', 'Container']:
                            continue
                        
                        # Определяем класс метаданных по английскому префиксу в контексте
                        object_type = "ОбъектМетаданных"
                        for eng_key, rus_name in metadata_map.items():
                            if eng_key.lower() in context.lower():
                                object_type = rus_name
                                break
                        
                        # Регистрируем виртуальные файлы под оба возможных типа исполняемых модулей (.0 и .m)
                        for ext in ['.0', '.m']:
                            virtual_filename = f"{obj_uuid_lower}{ext}"
                            ai_id = str(uuid.uuid4())
                            
                            query_ai = """
                                INSERT INTO ai_metadata_objects (id, filename, object_name, object_type, logical_uuid, is_active)
                                VALUES (%s, %s, %s, %s, %s, TRUE)
                                ON CONFLICT (filename) DO UPDATE 
                                SET object_name = EXCLUDED.object_name,
                                    object_type = EXCLUDED.object_type,
                                    logical_uuid = EXCLUDED.logical_uuid;
                            """
                            cursor_ai.execute(query_ai, (ai_id, virtual_filename, object_name, object_type, obj_uuid_lower))
                            objects_discovered += 1
                            
                except zlib.error:
                    continue  # Если файл не сжат алгоритмом deflate, идем дальше
                except Exception:
                    continue
                    
            self.conn_ai.commit()
            print(f"[✅] Синхронизация структуры завершена. В базу записано {objects_discovered} объектов метаданных.")
            
        except Exception as e:
            print(f"[💥] Критический сбой при парсинге метаданных: {e}")
            self.conn_1c.rollback()
            self.conn_ai.rollback()
        finally:
            cursor_1c.close()
            cursor_ai.close()
    def extract_and_cache_source_codes(self):
        """
        Шаг 2.2 (Интеллектуальный): Разделение BSL-кода и шаблонов/макетов 1С.
        Вытаскивает названия форм Росстата и относит их к правильному родителю в дереве.
        """
        print("[🔄] Шаг 2: Глубокий анализ config 1С и категоризация объектов...")
        
        cursor_1c = self.conn_1c.cursor()
        
        # Гарантируем структуру таблиц
        cursor_ai = self.conn_ai.cursor()
        cursor_ai.execute("""
            CREATE TABLE IF NOT EXISTS ai_metadata_source_codes (
                id UUID PRIMARY KEY,
                code_filename VARCHAR(255) UNIQUE,
                source_code TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        self.conn_ai.commit()
        cursor_ai.close()
        
        # Индексный запрос к 1С
        query_1c = "SELECT filename, binarydata FROM config WHERE filename LIKE '%.0' OR filename LIKE '%.m';"
        
        try:
            cursor_1c.execute(query_1c)
            records = cursor_1c.fetchall()
            print(f"[📊] В таблице config 1С найдено {len(records)} потенциальных объектов.")
            
            codes_cached = 0
            
            for filename, binarydata in records:
                if not binarydata:
                    continue
                
                filename_lower = filename.lower()
                
                try:
                    # Декомпрессия сырого deflate
                    raw_data = zlib.decompress(bytes(binarydata), -zlib.MAX_WBITS)
                    text_content = raw_data.decode('utf-8-sig', errors='ignore').replace('\x00', '')
                    
                    pure_bsl_code = ""
                    friendly_name = f"Объект: {filename_lower[:8]}"
                    object_type = "ПрочиеОбъекты"
                    
                    # --- СЦЕНАРИЙ А: Это структурный шаблон/макет (как на скриншоте) ---
                    if "ФЕДЕРАЛЬНОЕ СТАТИСТИЧЕСКОЕ НАБЛЮДЕНИЕ" in text_content or '{"ru","' in text_content:
                        object_type = "Шаблоны и макеты отчетов"
                        
                        # Вытаскиваем самое длинное русское наименование отчета Росстата из структуры {"ru","..."}
                        ru_names = re.findall(r'{"ru",\s*"([^"]+)"}', text_content)
                        if ru_names:
                            # Берем самое длинное и содержательное имя (чтобы отсечь технические маркеры)
                            friendly_name = max(ru_names, key=len)
                        else:
                            friendly_name = f"Шаблон отчета {filename_lower[:8]}"
                            
                        # Для макетов сохраняем текст структуры как есть, чтобы его можно было изучать
                        pure_bsl_code = text_content
                        
                    # --- СЦЕНАРИЙ Б: Это реальный исполняемый BSL-код ---
                    else:
                        if text_content.strip().startswith("{"):
                            string_blocks = re.findall(r'"((?:[^"\\]|\\.)*)"', text_content, re.DOTALL)
                            best_block = ""
                            for block in string_blocks:
                                if "Процедура " in block or "Функция " in block or "КонецПроцедуры" in block:
                                    if len(block) > len(best_block):
                                        best_block = block
                            if best_block:
                                pure_bsl_code = best_block.replace('\\"', '"').replace('""', '"')
                        else:
                            if "Процедура " in text_content or "Функция " in text_content:
                                pure_bsl_code = text_content
                                
                        if pure_bsl_code:
                            # Отрезаем бинарный заголовок смещений
                            match_start = re.search(r'(//|#Область|Процедура|Функция|Перем)', pure_bsl_code, re.IGNORECASE)
                            if match_start:
                                pure_bsl_code = pure_bsl_code[match_start.start():]
                                
                            # Определяем родителя для кода
                            object_type = "ОбщиеМодули"
                            biz_name = re.search(r'Функция\s+([A-Za-zА-Яа-я0-9_]+)', pure_bsl_code)
                            if biz_name:
                                friendly_name = biz_name.group(1)
                                if "Документ" in pure_bsl_code or "ПередачаТоваров" in pure_bsl_code:
                                    object_type = "МодулиДокументов"
                                    
                            if filename_lower.endswith('.m'):
                                friendly_name = f"{friendly_name} (Менеджер)"
                            else:
                                friendly_name = f"{friendly_name} (Объект)"

                    # Записываем результаты в базу ИИ через изолированные транзакции
                    if pure_bsl_code.strip():
                        cursor_save = self.conn_ai.cursor()
                        try:
                            # 1. Обновляем текст/структуру
                            cursor_save.execute("""
                                INSERT INTO ai_metadata_source_codes (id, code_filename, source_code)
                                VALUES (%s, %s, %s)
                                ON CONFLICT (code_filename) 
                                DO UPDATE SET source_code = EXCLUDED.source_code, updated_at = CURRENT_TIMESTAMP;
                            """, (str(uuid.uuid4()), filename_lower, pure_bsl_code))
                            
                            # 2. Перепривязываем к правильному родителю и пишем красивый Синоним
                            cursor_save.execute("""
                                INSERT INTO ai_metadata_objects (object_id, object_type, internal_name, synonym, sql_table_name)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (internal_name) 
                                DO UPDATE SET synonym = EXCLUDED.synonym, object_type = EXCLUDED.object_type;
                            """, (str(uuid.uuid4()), object_type, filename_lower, friendly_name, "ERP_PROCESSED"))
                            
                            self.conn_ai.commit()
                            codes_cached += 1
                        except Exception:
                            self.conn_ai.rollback()
                        finally:
                            cursor_save.close()
                            
                except zlib.error:
                    continue
                except Exception:
                    continue
            
            print(f"[✅] Категоризация завершена! Распределено по родителям: {codes_cached} объектов.")
            
        except Exception as e:
            print(f"[💥] Ошибка: {e}")
        finally:
            cursor_1c.close()

    def close(self):
        """Безопасное закрытие пула соединений при уничтожении объекта"""
        try:
            if self.conn_1c:
                self.conn_1c.close()
            if self.conn_ai:
                self.conn_ai.close()
            print("[🔒] Соединения с базами данных успешно закрыты.")
        except Exception:
            pass

# =====================================================================
# ТОЧКА ВХОДА (ПИШЕТСЯ У САМОГО ЛЕВОГО КРАЯ - 0 ПРОБЕЛОВ)
# =====================================================================
print("[🔍] Отладка: Python прочитал весь файл dbserver.py до конца.")

if __name__ == "__main__":
    print("[🚀] Запуск автономного конвейера миграции...")
    db = None
    try:
        # Инициализируем менеджер СУБД (откроются коннекты из Части 1)
        db = DBServerManager()
        
        # Выполняем Шаг 1: Собираем структуру и закладываем UUID связи (Часть 3)
        db.sync_metadata_structure()
        
        # Выполняем Шаг 2: Накатываем сверху чистый BSL-код (Часть 4)
        db.extract_and_cache_source_codes()
        
    except Exception as e:
        print(f"[💥] Критическая ошибка конвейера: {e}")
    finally:
        if db:
            db.close()
