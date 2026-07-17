import zlib
import psycopg2
import json
import os

class CHotFixManager:
    def __init__(self, config_path="config.json"):
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Конфигурационный файл {config_path} не найден.")
            
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
            
        pg = config_data.get("postgres", {})
        # ВНИМАНИЕ: Для хотфикса нам нужны права на ЗАПИСЬ в контур 1С (mpk_new_vavilkin)
        # Убедитесь, что у пользователя postgres есть туда доступ на запись.
        self.db_config_1c = {
            "host": pg.get("host", "172.16.30.204"),
            "database": config_data.get("ibsrv", {}).get("base_name", "mpk_new_vavilkin"),
            "user": pg.get("user", "postgres"),
            "password": pg.get("password", ""),
            "port": pg.get("port", 5432)
        }

    def _compress_to_1c_format(self, bsl_text):
        """
        Упаковывает чистый текст BSL-кода обратно в бинарный формат v8-deflate (zlib raw).
        1С требует кодировку UTF-8 с сигнатурой (BOM).
        """
        # Кодируем с BOM файлом (\xef\xbb\xbf)
        bom_utf8_data = bsl_text.encode('utf-8-sig')
        # Сжимаем без zlib-заголовков (wbits=-15 задает raw deflate)
        return zlib.compress(bom_utf8_data, level=9, wbits=-15)

    def apply_hotfix(self, object_id, new_bsl_text):
        """
        Записывает исправленный код модуля напрямую в рабочую СУБД 1С.
        """
        print(f"📦 Подготовка бинарного контейнера для объекта {object_id}...")
        binary_blob = self._compress_to_1c_format(new_bsl_text)
        
        conn = None
        try:
            conn = psycopg2.connect(**self.db_config_1c)
            with conn.cursor() as cur:
                # 1. Делаем бэкап старого модуля в лог или временный файл на случай отката!
                cur.execute("SELECT binarydata FROM config WHERE filename = %s;", (object_id,))
                backup_row = cur.fetchone()
                if backup_row:
                    self._save_backup(object_id, backup_row[0])

                # 2. Обновляем тело модуля в живой базе 1С
                print(f"🚀 Запись патча в таблицу config базы {self.db_config_1c['database']}...")
                cur.execute("""
                    UPDATE config 
                    SET binarydata = %s 
                    WHERE filename = %s;
                """, (psycopg2.Binary(binary_blob), object_id))
                
                # 3. Инвалидация кэша сервера 1С (Партизанский метод)
                # Обновляем системную строку конфигурации, заставляя rphost перечитать config
                cur.execute("""
                    UPDATE config 
                    SET binarydata = binarydata 
                    WHERE filename = 'version';
                """)
                
                conn.commit()
                print("🎯 Хотфикс успешно применен! Кэш сервера 1С спровоцирован на обновление.")
                return True
                
        except Exception as e:
            if conn:
                conn.rollback()
            print(f"❌ Критическая ошибка при живой записи в СУБД 1С: {e}")
            return False
        finally:
            if conn:
                conn.close()

    def _save_backup(self, object_id, original_binary):
        """Сохраняет исходный модуль на диск перед перезаписью для мгновенного отката"""
        os.makedirs("hotfix_backups", exist_ok=True)
        backup_path = f"hotfix_backups/{object_id}.bak"
        with open(backup_path, "wb") as f:
            f.write(original_binary)
        print(f"💾 Оригинальный модуль сохранен в {backup_path} (Резервная копия для отката).")
