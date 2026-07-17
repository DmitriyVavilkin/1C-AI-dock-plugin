# test_sql_read.py
from dbserver import DBServer # Или как называется ваш основной класс управления СУБД

def main():
    db = DBServer()
    # Запускаем наш автономный метод Шага 2
    db.extract_and_cache_source_codes()

if __name__ == "__main__":
    main()
# 
# 
