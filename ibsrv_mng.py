import subprocess
import time
import os
import signal
import requests

class Autonomous1CServer:
    def __init__(self, platform_version="8.3.27.1859", port=8314):
        self.bin_path = f"C:\\Program Files\\1cv8\\{platform_version}\\bin\\ibsrv.exe"
        self.port = port
        self.process = None

    def start(self, db_server, db_name, db_user, db_password, data_dir, name):
        """Запускает ibsrv.exe и передает пароль от СУБД через stdin"""
        
        # Базовые аргументы запуска
        cmd = [
            self.bin_path,
            "--dbms=PostgreSQL",
            f"--database-server={db_server}",
            f"--database-user={db_user}",
            f"--database-name={db_name}",
            f"--name={name}",
            f"--port={self.port}",
            f"--data={data_dir}",
            "-W" # Запрашивать пароль из стандартного ввода (stdin)
        ]

        # Создаем каталог данных, если его нет
        os.makedirs(data_dir, exist_ok=True)

        print(f"[IDE] Запуск автономного сервера 1С на порту {self.port}...")
        
        # Запускаем процесс со скрытием консольного окна (только для Windows)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,       # Открываем поток для отправки пароля
            stdout=subprocess.PIPE,      # Чтение логов сервера (при необходимости)
            stderr=subprocess.PIPE,
            text=True,                   # Работаем со строками, а не байтами
            startupinfo=startupinfo
        )

        try:
            # Вслепую отправляем пароль в stdin процесса и добавляем перенос строки (\n)
            # Мы НЕ используем .communicate(), так как он закроет процесс. Нам нужен write().
            self.process.stdin.write(f"{db_password}\n")
            self.process.stdin.flush()
            
            # Небольшая пауза, чтобы сервер успел инициализироваться
            time.sleep(200)
            
            # Проверяем, не упал ли процесс сразу (например, из-за неверного пароля или занятого порта)
            if self.process.poll() is not None:
                stderr_output = self.process.stderr.read()
                raise RuntimeError(f"Сервер не смог запуститься: {stderr_output}")
                
            print("[IDE] Автономный сервер 1С успешно запущен в фоновом режиме.")
            return True

        except Exception as e:
            print(f"[IDE] Критическая ошибка при старте ibsrv: {e}")
            self.stop()
            return False

    def stop(self):
        """Безопасная остановка сервера"""
        if self.process and self.process.poll() is None:
            print("[IDE] Остановка фонового сервера 1С...")
            try:
                # В Windows корректнее всего посылать сигнал мягкого закрытия через CTRL_BREAK
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Если сервер завис — убиваем принудительно
                print("[IDE] Сервер не ответил на запрос остановки. Принудительное завершение.")
                self.process.kill()
            
            self.process = None
            print("[IDE] Сервер 1С успешно остановлен.")

    def get_metadata(self, target_obj):
        """Получает структуру метаданных для указанного объекта"""
        try:
            # Собираем компоненты ЖИВОГО серверного маршрута 1С
            протокол = "http"
            хост = "127.0.0.1"
            база = "erp_test"

            # Имя метода СТРОГО как на вашем скриншоте из Конфигуратора:
            метод = "hs/ai/get_structure" 

            # Склеиваем монолитный URL без замыкающего слэша
            target_url = f"{протокол}://{хост}/{база}/{метод}"

            res = requests.get(
                target_url, 
                params={"object": target_obj}, 
                timeout=30
            )

            print(f"[GUI] Ответ Apache: Код {res.status_code}")
            if res.status_code == 200:
                try:
                    raw_data = res.json()
                except Exception as json_err:
                    print(f"[GUI] Не удалось распарсить JSON. Сырой ответ сервера:\n{res.text}")
                    raw_data = None
            else:
                print(f"[GUI] Сервер вернул ошибку {res.status_code}. Включаем богатый демо-контекст ERP...")

        except Exception as e:
            print(f"[GUI] Ошибка связи ({str(e)}). Включаем богатый демо-контекст ERP...")
            raw_data = None

        return raw_data