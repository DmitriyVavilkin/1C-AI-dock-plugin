# test_sql_read.py
from dbserver import DBServer

def main():
    print("[🚀] Инициализация DBServer для выгрузки структуры...")
    db = DBServer()
    
    # Запускаем прямую синхронизацию метаданных
    db.sync_metadata_structure()

if __name__ == "__main__":
    main()
