import sys
from error_analyzer import AIErrorAnalyzer1C  # Прямой импорт класса

def get_multiline_input():
    """Считывает многострочный текст ошибки из консоли."""
    print("\n📥 Вставьте или введите текст ошибки 1С ниже.")
    print("(Завершите ввод, нажав Enter на пустой строке или введя слово 'RUN' с новой строки):")
    print("-" * 60)
    
    lines = []
    while True:
        try:
            line = input()
            if line.strip().upper() == "RUN" or (line == "" and lines):
                break
            lines.append(line)
        except EOFError:
            break
            
    return "\n".join(lines)

if __name__ == "__main__":
    print("=" * 60)
    print(" 🛠  ИНТЕРАКТИВНЫЙ ИИ-АНАЛИЗАТОР ИНЦИДЕНТОВ 1С (КОНСОЛЬ) ")
    print("=" * 60)
    
    # Инициализация напрямую через имя класса
    analyzer = AIErrorAnalyzer1C(
        db_config={
            "host": "172.16.30.204",
            "database": "1C_AI_Database",
            "user": "postgres",
            "password": "Viseo193DX"  # Укажите ваш актуальный пароль
        },
        lm_studio_url="http://172.21.0.179:1234/v1"
    )
    
    try:
        while True:
            error_text = get_multiline_input()
            
            if not error_text.strip():
                print("⚠️ Текст ошибки пустой. Выход из программы.")
                break
                
            result = analyzer.dispatch_and_analyze(error_text)
            
            print("\n📊 ОТВЕТ ИИ-АССИСТЕНТА:")
            print("=" * 60)
            print(result)
            print("=" * 60)
            
            action = input("\n🔄 Хотите проанализировать еще одну ошибку? (д/н): ").strip().lower()
            if action not in ['д', 'y', 'yes', 'да']:
                print("👋 Работа завершена. Хорошего дня!")
                break
                
    except Exception as e:
        print(f"\n❌ Критическая ошибка в процессе работы: {e}")
    finally:
        analyzer.close()
        print("🔒 Соединение с СУБД ИИ закрыто.")
