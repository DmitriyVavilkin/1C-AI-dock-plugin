import psycopg2
import zlib
from datetime import datetime

class HotfixBackupManager:
    def __init__(self, db_1c_config, db_ai_config):
        """
        db_1c_config: параметры СУБД боевой/тестовой базы 1С:ERP
        db_ai_config: параметры СУБД вашего ИИ-хранилища (1C_AI_Database)
        """
        self.db_1c = db_1c_config
        self.db_ai = db_ai_config

    def create_safe_backup(self, metadata_uuid: str, filename_key: str) -> bool:
        """
        Шаг 1: Вычитывает сырой BLOB из боевой Config 1С и делает
        слепок в таблицу бэкапов ИИ-хранилища перед внесением изменений.
        """
        try:
            # 1. Читаем оригинальный бинарник из базы 1С
            conn_1c = psycopg2.connect(**self.db_1c)
            with conn_1c.cursor() as cursor_1c:
                cursor_1c.execute(
                    "SELECT binarydata FROM config WHERE filename = %s;", 
                    (filename_key,)
                )
                row = cursor_1c.fetchone()
                if not row or not row[0]:
                    conn_1c.close()
                    raise Exception(f"Запись {filename_key} не найдена в таблице Config СУБД 1С.")
                original_blob = row[0]
            conn_1c.close()

            # 2. Пытаемся распаковать текст для сохранения текстовой копии (для аналитики)
            try:
                # 1С часто сжимает без заголовков zlib (Raw Deflate, wbits=-15)
                backup_text = zlib.decompress(bytes(original_blob), -15).decode('utf-8', errors='ignore')
            except Exception:
                backup_text = "// [Бинарные данные: не удалось распаковать как сырой текст BSL]"

            # 3. Сохраняем слепок в ИИ-хранилище
            conn_ai = psycopg2.connect(**self.db_ai)
            with conn_ai.cursor() as cursor_ai:
                query_ai = """
                    INSERT INTO ai_hotfix_backups (metadata_uuid, filename_key, original_binary, backup_text)
                    VALUES (%s, %s, %s, %s);
                """
                cursor_ai.execute(query_ai, (metadata_uuid, filename_key, psycopg2.Binary(original_blob), backup_text))
            conn_ai.commit()
            conn_ai.close()
            return True
            
        except Exception as e:
            print(f"[BACKUP ERROR] Сбой создания точки восстановления: {e}")
            return False

    def rollback_last_hotfix(self, metadata_uuid: str, filename_key: str) -> tuple[bool, str]:
        """
        Шаг 2: Механизм отката. Берет последний сохраненный бэкап 
        из ИИ-базы и принудительно возвращает его на место в Config 1С.
        """
        try:
            # 1. Извлекаем последний бэкап из ИИ-хранилища
            conn_ai = psycopg2.connect(**self.db_ai)
            with conn_ai.cursor() as cursor_ai:
                query_ai = """
                    SELECT original_binary FROM ai_hotfix_backups 
                    WHERE metadata_uuid = %s AND filename_key = %s
                    ORDER BY created_at DESC LIMIT 1;
                """
                cursor_ai.execute(query_ai, (metadata_uuid, filename_key))
                row = cursor_ai.fetchone()
                if not row:
                    conn_ai.close()
                    return False, "История бэкапов для данного объекта пуста."
                backup_blob = row[0]
            conn_ai.close()

            # 2. Возвращаем оригинальный BLOB обратно в СУБД 1С
            conn_1c = psycopg2.connect(**self.db_1c)
            with conn_1c.cursor() as cursor_1c:
                cursor_1c.execute(
                    "UPDATE config SET binarydata = %s WHERE filename = %s;",
                    (psycopg2.Binary(backup_blob), filename_key)
                )
                
                # Инициируем сброс кэша конфигурации rphost
                cursor_1c.execute("UPDATE config SET binarydata = binarydata WHERE filename = 'version';")
            conn_1c.commit()
            conn_1c.close()
            
            return True, "Успешный откат! Оригинальный модуль восстановлен в СУБД 1С."
            
        except Exception as e:
            return False, f"Критическая ошибка восстановления из бэкапа: {str(e)}"
