import psycopg2
import os
import json

def load_ai_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f).get("db_ai", {})
    return {"host": "localhost", "port": 5432, "user": "postgres", "password": "", "dbname": "1C_AI_Database"}

def main():
    cai = load_ai_config()
    conn = psycopg2.connect(
        host=cai.get("host"), port=cai.get("port"),
        user=cai.get("user"), password=cai.get("password"),
        dbname=cai.get("dbname")
    )
    cursor = conn.cursor()
    
    # Запрашиваем у системного каталога PostgreSQL точный список колонок
    cursor.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'ai_metadata_objects';
    """)
    columns = cursor.fetchall()
    
    print("\n[📊] РЕАЛЬНЫЕ КОЛОНКИ ТАБЛИЦЫ ai_metadata_objects В ВАШЕЙ БАЗЕ:")
    print("="*60)
    for col, dtype in columns:
        print(f"  🔹 {col} ({dtype})")
    print("="*60)
    
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
