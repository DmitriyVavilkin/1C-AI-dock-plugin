import os
import psycopg2
from dbserver import DB_PARAMS, decompose_1c_path, clean_and_split_stream

def scan_and_build_tree(root_directory_path):
    """
    Сканирует локальный каталог с исходниками 1С, парсит их 
    и загружает в БД в соответствии с новой иерархической структурой.
    """
    if not os.path.exists(root_directory_path):
        print(f"[Ошибка] Указанный путь не существует: {root_directory_path}")
        return

    print(f"[Сканирование] Старт анализа директории: {root_directory_path}")
    
    # Подключаемся к базе для пакетной обработки
    conn = psycopg2.connect(**DB_PARAMS)
    cursor = conn.cursor()
    
    processed_count = 0
    skipped_count = 0
    
    # Шаблон SQL-запроса для пакетного UPSERT
    upsert_query = """
        INSERT INTO ai_metadata_source_codes 
        (object_type, object_name, sub_type, sub_name, module_type, bsl_code, v8_structure, raw_path)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (raw_path) 
        DO UPDATE SET 
            bsl_code = EXCLUDED.bsl_code,
            v8_structure = EXCLUDED.v8_structure,
            updated_at = CURRENT_TIMESTAMP;
    """

    # Рекурсивный обход папок проекта
    for root, dirs, files in os.walk(root_directory_path):
        for file in files:
            # Нас интересуют как чистые bsl, так и текстовые структуры метаданных
            if file.endswith('.bsl') or file.endswith('.txt') or file.lower() == 'text':
                full_path = os.path.join(root, file)
                
                # Создаем относительный или виртуальный путь для 1С-координат
                # Например: Документ.РеализацияТоваровУслуг.МодульОбъекта
                relative_path = os.path.relpath(full_path, root_directory_path)
                
                try:
                    with open(full_path, 'rb') as f:
                        raw_binary = f.read()
                        
                    # Очищаем бинарный поток от 0x00, 7fffffff и делим на код/структуру
                    bsl_code, v8_structure = clean_and_split_stream(raw_binary)
                    
                    # Интеллектуальный контекстный анализ: ищем маркеры расширений 1С
                    if "&Вместо" in bsl_code:
                        # Если это расширение, мы можем пометить тип или имя объекта для ИИ
                        pass # При необходимости сюда добавляется логика тегирования аспектного кода
                        
                    # Парсим канонические координаты Конфигуратора 1С
                    path_info = decompose_1c_path(relative_path)
                    
                    # Если полезного кода нет, пишем аккуратный комментарий-заглушку
                    if not bsl_code.strip():
                        bsl_code = f"// Модуль '{relative_path}' не содержит исполняемого кода BSL."
                        
                    # Выполняем запись в базу
                    cursor.execute(upsert_query, (
                        path_info["object_type"],
                        path_info["object_name"],
                        path_info["sub_type"],
                        path_info["sub_name"],
                        path_info["module_type"],
                        bsl_code,
                        v8_structure,
                        relative_path
                    ))
                    
                    processed_count += 1
                    
                    # Коммитим пачками по 500 элементов, чтобы не перегружать память
                    if processed_count % 500 == 0:
                        conn.commit()
                        print(f"[Успех] Обработано файлов: {processed_count}...")
                        
                except Exception as e:
                    print(f"[Пропущено] Ошибка обработки файла {relative_path}: {e}")
                    skipped_count += 1

    # Финальный коммит оставшихся данных
    conn.commit()
    cursor.close()
    conn.close()
    
    print("\n[Результаты раунда контекстного анализа]:")
    print(f"🚀 Успешно разложено по папкам и загружено: {processed_count} файлов.")
    print(f"⚠️ Пропущено из-за ошибок чтения: {skipped_count} файлов.")

if __name__ == "__main__":
    # Укажите путь к вашей локальной папке, куда выгружен прототип/конфигурация ERP
    TARGET_DIR = "./ext_source_1c" 
    
    # Запуск сканирования
    if os.path.exists(TARGET_DIR):
        scan_and_build_tree(TARGET_DIR)
    else:
        print(f"[Внимание] Перед запуском обновите TARGET_DIR в скрипте или создайте папку '{TARGET_DIR}'")
