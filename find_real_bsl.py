import psycopg2
import zlib
import os
import json

def load_1c_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f).get("db_1c", {})
    return {}

def main():
    c1c = load_1c_config()
    conn = psycopg2.connect(
        host=c1c.get("host"), port=c1c.get("port"),
        user=c1c.get("user"), password=c1c.get("password"),
        dbname=c1c.get("dbname")
    )
    cursor = conn.cursor()
    
    # Ищем в config записи, которые гарантированно содержат ключевые слова BSL
    # (например: Процедура, Функция, КонецПроцедуры, Перем)
    print("[🔍] Поиск реальных текстовых BSL-модулей в таблице config 1С...")
    cursor.execute("SELECT filename, binarydata FROM config WHERE filename LIKE '%.0' OR filename LIKE '%.m' LIMIT 200;")
    records = cursor.fetchall()
    
    print(f"Проверяем {len(records)} файлов на наличие BSL-синтаксиса...")
    
    bsl_extensions = set()
    found_samples = 0
    
    for filename, binarydata in records:
        if not binarydata:
            continue
        try:
            raw_data = zlib.decompress(bytes(binarydata), -zlib.MAX_WBITS)
            text = raw_data.decode('utf-8-sig', errors='ignore')
            
            # Если в тексте есть маркеры кода 1С
            if "Процедура" in text or "Функция" in text or "КонецПроцедуры" in text:
                print(f"  ✅ НАЙДЕН РЕАЛЬНЫЙ КОД: файл = {filename} (Длина: {len(text)} симв.)")
                print(f"  📝 Начало кода:\n{text[:150]}\n" + "-"*40)
                found_samples += 1
                if found_samples >= 3:
                    break
        except Exception:
            continue
            
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
