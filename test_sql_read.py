# ====================================================================
# ОБНОВЛЕННЫЙ DEVOPS-СКРИПТ ПОЛНОЙ СИНХРОНИЗАЦИИ (test_sql_read.py)
# ====================================================================
from dbserver import DBServerManager

def run_sync():
    print("⏳ Инициализация двухконтурной синхронизации СУБД...")
    # Создаем экземпляр менеджера, он сам прочитает config.json
    manager = DBServerManager()
    
    try:
        # На Шаге 1 вы задействуете свою оригинальную функцию, 
        # а Шаг 2 и Шаг 3 мы берем из обновленного бэкенда
        #    manager.extract_and_cache_metadata_objects()
        manager.extract_and_cache_source_codes()
        manager.extract_and_cache_metadata_fields()
        
        print("\n🎉 СИНХРОНИЗАЦИЯ ПРОШЛА ИДЕАЛЬНО! ДАННЫЕ В БАЗЕ ИИ ОБНОВЛЕНЫ.")
    except Exception as e:
        print(f"\n❌ Произошел сбой во время синхронизации: {e}")
        raise e
    finally:
        manager.close()
        print("🔒 Соединения с контурами СУБД безопасно закрыты.")

if __name__ == "__main__":
    run_sync()
