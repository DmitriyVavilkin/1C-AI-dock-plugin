from dbserver import AIDatabaseManger

# Общие сетевые реквизиты СУБД
DB_HOST = "172.16.30.204"
DB_USER = "postgres"
DB_PASS = " "  # Укажите здесь ваш реальный пароль к Postgres

# Контур 1: Откуда читаем (Рабочая база 1С)
SOURCE_1C_CONFIG = {
    "host": DB_HOST,
    "database": "mpk_new_vavilkin",
    "user": DB_USER,
    "password": DB_PASS,
    "port": 5432
}

# Контур 2: Куда записываем (Изолированная база вашего ИИ-проекта)
TARGET_AI_CONFIG = {
    "host": DB_HOST,
    "database": "1C_AI_Database",
    "user": DB_USER,
    "password": DB_PASS,
    "port": 5432
}

def run_sync():
    print("📡 Инициализация двухконтурного подключения к СУБД...")
    # Передаем конфигурации строго по именованным аргументам нового конструктора
    manager = AIDatabaseManger(source_db_config=SOURCE_1C_CONFIG, ai_db_config=TARGET_AI_CONFIG)
    
    if not manager.connect():
        print("❌ Не удалось подключиться к базам данных. Проверьте параметры.")
        return
        
    print("\nШаг 1: Синхронизация корневой структуры метаданных из 1С в ИИ-БД...")
    manager.parse_and_sync_metadata()
    
    print("\nШаг 2: Извлечение и заливка чистых BSL-кода модулей...")
    manager.extract_and_cache_bsl_modules()
    
    print("\nШаг 3: Извлечение реквизитов и полей объектов...")
    manager.extract_and_cache_metadata_fields()
    
    manager.disconnect()
    print("\n🎉 Все операции успешно завершены!")

if __name__ == "__main__":
    run_sync()
