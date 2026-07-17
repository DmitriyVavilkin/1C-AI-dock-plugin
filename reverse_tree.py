import psycopg2
import uuid
import json
import os

def load_ai_config():
    """Загрузка параметров подключения к базе ИИ"""
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f).get("db_ai", {})
    return {"host": "localhost", "port": 5432, "user": "postgres", "password": "", "dbname": "1C_AI_Database"}

def main():
    print("[🚀] Запуск автономного восстановления дерева объектов метаданных...")
    
    cai = load_ai_config()
    conn_ai = psycopg2.connect(
        host=cai.get("host"), port=cai.get("port"),
        user=cai.get("user"), password=cai.get("password"),
        dbname=cai.get("dbname")
    )
    cursor_ai = conn_ai.cursor()
    
    try:
        print("[🔍] Чтение загруженных BSL-модулей из базы ИИ...")
        cursor_ai.execute("SELECT code_filename FROM ai_metadata_source_codes;")
        rows = cursor_ai.fetchall()
        
        print(f"[📊] Найдено {len(rows)} файлов кодов для реверс-инжиниринга структуры.")
        
        # Гарантируем уникальность индекса по полю internal_name, чтобы избежать дублей
        try:
            cursor_ai.execute("ALTER TABLE ai_metadata_objects ADD CONSTRAINT unique_internal_name UNIQUE (internal_name);")
            conn_ai.commit()
        except Exception:
            conn_ai.rollback()
            
        objects_created = 0
        processed_files = set()
        
        print("[🔄] Формирование структуры метаданных строго по вашей схеме СУБД...")
        
        for (code_filename,) in rows:
            if code_filename in processed_files:
                continue
                
            # Выделяем логический UUID из имени файла (например, "2adfcd42-...")
            logical_uuid = code_filename.split('.')[0].lower()
            
            # Базовое определение типа и синонима для дерева PyQt6
            if code_filename.endswith('.m'):
                object_type = "МодульМенеджера"
                synonym = f"Менеджер: {logical_uuid[:8]}"
            else:
                object_type = "МодульОбъекта"
                synonym = f"Объект: {logical_uuid[:8]}"
                
            # Заполняем поля строго по вашей схеме:
            # object_id, object_type, internal_name, synonym, sql_table_name
            query_insert = """
                INSERT INTO ai_metadata_objects (object_id, object_type, internal_name, synonym, sql_table_name)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (internal_name) 
                DO UPDATE SET synonym = EXCLUDED.synonym, object_type = EXCLUDED.object_type;
            """
            
            # Используем logical_uuid как объектный ID, а имя файла как внутреннее имя
            cursor_ai.execute(query_insert, (logical_uuid, object_type, code_filename, synonym, "DEFERRED_MAPPING"))
            processed_files.add(code_filename)
            objects_created += 1
            
            if objects_created % 5000 == 0:
                conn_ai.commit()
                print(f"[🔹] Записано {objects_created} объектов в базу...")
                
        conn_ai.commit()
        print(f"[✅] Реверс-инжиниринг дерева успешно завершен!")
        print(f"[📊] В таблицу ai_metadata_objects заведено/обновлено: {objects_created} записей.")
        
    except Exception as e:
        print(f"[💥] Критическая ошибка генерации дерева: {e}")
        conn_ai.rollback()
    finally:
        cursor_ai.close()
        conn_ai.close()

if __name__ == "__main__":
    main()
