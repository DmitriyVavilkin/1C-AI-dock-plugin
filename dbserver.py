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
import zlib
import uuid
import json

def extract_and_cache_source_codes(self):
    """
    Шаг 2: Прямое извлечение BSL-кода (.0 и .m) из таблицы config 1С,
    декомпрессия raw deflate и кэширование в базу данных ИИ.
    """
    print("[🔄] Старт автономного извлечения BSL-кодов из config...")
    
    # 1. Подключаемся к базе 1С и к базе ИИ через унифицированную точку
    conn_1c = self._connect_db() # Предполагаем, что метод адаптируется под выбор базы, либо используем два разных подключения
    conn_ai = self._connect_db() # Настройте параметры подключения под ваши реалии config.json
    
    cursor_1c = conn_1c.cursor()
    cursor_ai = conn_ai.cursor()
    
    # SQL-запрос для выкачивания всех модулей
    query_1c = "SELECT filename, binarydata FROM config WHERE filename LIKE '%.0' OR filename LIKE '%.m';"
    
    try:
        cursor_1c.execute(query_1c)
        records = cursor_1c.fetchall()
        print(f"[📊] Найдено {len(records)} бинарных модулей в таблице config.")
        
        cached_count = 0
        for filename, binarydata in records:
            if not binarydata:
                continue
                
            try:
                # Декомпрессия raw deflate (wbits=-zlib.MAX_WBITS игнорирует zlib-заголовки)
                raw_data = zlib.decompress(bytes(binarydata), -zlib.MAX_WBITS)
                
                # Декодируем в UTF-8, корректно обрабатывая BOM-сигнатуру
                source_code = raw_data.decode('utf-8-sig', errors='ignore')
                
                # Генерируем UUID для сущности ИИ
                ai_uuid = str(uuid.uuid4())
                
                # Подготовка к записи в ai_metadata_objects
                # Адаптируйте имена полей (например: id, filename, source_code, object_type) под вашу схему
                query_ai = """
                    INSERT INTO ai_metadata_objects (id, filename, source_code, is_active)
                    VALUES (%s, %s, %s, TRUE)
                    ON CONFLICT (filename) DO UPDATE 
                    SET source_code = EXCLUDED.source_code;
                """
                cursor_ai.execute(query_ai, (ai_uuid, filename, source_code))
                cached_count += 1
                
            except zlib.error as ze:
                # Некоторые файлы в config могут быть не сжаты или иметь другой формат, пропускаем их
                continue
            except Exception as e:
                print(f"[❌] Ошибка обработки файла {filename}: {e}")
                continue
                
        conn_ai.commit()
        print(f"[✅] Успешно синхронизировано и кэшировано модулей в базу ИИ: {cached_count}")
        
    except Exception as e:
        print(f"[💥] Критическая ошибка при работе с СУБД: {e}")
        conn_1c.rollback()
        conn_ai.rollback()
    finally:
        cursor_1c.close()
        cursor_ai.close()
        conn_1c.close()
        conn_ai.close()

        print(f"🏁 Шаг 2 завершен! В СУБД ИИ успешно кэшировано {modules_found} чистых BSL-модулей.")
 
import zlib
import json
import re
import uuid

def sync_metadata_structure(self):
    """
    Шаг 2.1: Извлечение структуры метаданных (типы, имена объектов и их UUID)
    из системных таблиц config 1С для построения дерева GUI.
    """
    print("[🔄] Старт извлечения структуры метаданных из 1С...")
    
    # Подключения к базам (используем вашу точку _connect_db())
    conn_1c = self._connect_db()  
    conn_ai = self._connect_db()  
    
    cursor_1c = conn_1c.cursor()
    cursor_ai = conn_ai.cursor()
    
    # Шаг 1: Проверяем и создаем целевую таблицу в базе ИИ, если её нет
    cursor_ai.execute("""
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
    conn_ai.commit()
    
    # Шаг 2: Читаем корневые метаданные 1С, чтобы понять состав конфигурации
    # В config файлы метаданных лежат под фиксированными UUID или именем 'root'
    query_1c = "SELECT filename, binarydata FROM config WHERE filename IN ('root', 'metadata') OR filename LIKE '__________-____-____-____-____________';"
    
    try:
        cursor_1c.execute(query_1c)
        records = cursor_1c.fetchall()
        print(f"[📊] Прочитано {len(records)} системных структурных файлов из config.")
        
        objects_discovered = 0
        
        for filename, binarydata in records:
            if not binarydata:
                continue
                
            try:
                # Декомпрессия raw deflate
                raw_data = zlib.decompress(bytes(binarydata), -zlib.MAX_WBITS)
                text_content = raw_data.decode('utf-8-sig', errors='ignore')
                
                # Текстовое представление метаданных 1С — это вложенные массивы вида {"#", ...}
                # Используем регулярные выражения, чтобы вытащить связки: Логическое Имя - UUID объекта
                # Ищем паттерны UUID и идущие рядом кириллические/латинские идентификаторы классов 1С
                
                # Поиск всех UUID в текущем блоке конфигурации
                uuids = re.findall(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', text_content)
                
                # Типичные маркеры типов объектов в манифесте 1С
                metadata_types = {
                    "Catalogs": "Справочник",
                    "Documents": "Документ",
                    "Reports": "Отчет",
                    "DataProcessors": "Обработка",
                    "InformationRegisters": "РегистрСведений",
                    "AccumulationRegisters": "РегистрНакопления",
                    "CommonModules": "ОбщийМодуль"
                }
                
                for obj_uuid in set(uuids):
                    obj_uuid_lower = obj_uuid.lower()
                    
                    # Пытаемся определить тип и имя объекта по контексту вокруг UUID
                    # Ищем текстовые строки в пределах 200 символов от найденного UUID
                    pos = text_content.find(obj_uuid)
                    context = text_content[max(0, pos-100):min(len(text_content), pos+200)]
                    
                    # Извлекаем потенциальное имя объекта (строка в кавычках)
                    names = re.findall(r'"([A-Za-zА-Яа-я0-9_]+)"', context)
                    if not names:
                        continue
                        
                    object_name = names[0]
                    
                    # Игнорируем технические системные слова 1С
                    if object_name in ['Metadata', 'Root', 'Version', 'DataHistory']:
                        continue
                        
                    # Определяем тип объекта 1С
                    object_type = "ОбъектМетаданных"
                    for eng_type, rus_type in metadata_types.items():
                        if eng_type.lower() in context.lower():
                            object_type = rus_type
                            break
                    
                    # Формируем виртуальные имена файлов .0 и .m для связи с кодом
                    # 1С привязывает модули к UUID объекта
                    for ext in ['.0', '.m']:
                        virtual_filename = f"{obj_uuid_lower}{ext}"
                        ai_id = str(uuid.uuid4())
                        
                        # Сохраняем каркас метаданных в базу ИИ
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
                continue # Файл не сжат или это не deflate, пропускаем
            except Exception as e:
                print(f"[⚠️] Пропущена запись при парсинге {filename}: {e}")
                continue
                
        conn_ai.commit()
        print(f"[✅] Структура метаданных успешно импортирована. Создано/обновлено связей: {objects_discovered}")
        
    except Exception as e:
        print(f"[💥] Ошибка при чтении структуры метаданных: {e}")
        conn_1c.rollback()
        conn_ai.rollback()
    finally:
        cursor_1c.close()
        cursor_ai.close()
        conn_1c.close()
        conn_ai.close()
 
 
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
