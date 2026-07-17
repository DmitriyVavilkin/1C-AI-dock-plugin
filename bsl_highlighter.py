import re
from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont

class BSLHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.highlighting_rules = []

        # 1. Форматирование для ключевых слов 1С (Синий цвет)
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#0000FF"))
        keyword_format.setFontWeight(QFont.Weight.Bold)
        keywords = [
            r"\bПроцедура\b", r"\bКонецПроцедуры\b", r"\bФункция\b", r"\bКонецФункции\b",
            r"\bЕсли\b", r"\bТогда\b", r"\bИначеЕсли\b", r"\bИначе\b", r"\bКонецЕсли\b",
            r"\bДля\b", r"\bКаждого\b", r"\bИз\b", r"\bЦикл\b", r"\bКонецЦикла\b",
            r"\bПока\b", r"\bПерем\b", r"\bЭкспорт\b", r"\bВозврат\b", r"\bИстина\b", r"\bЛожь\b"
        ]
        for word in keywords:
            self.highlighting_rules.append((re.compile(word, re.IGNORECASE), keyword_format))

        # 2. Форматирование для комментариев (Зеленый цвет)
        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#008000"))
        self.highlighting_rules.append((re.compile(r"//.*"), comment_format))

        # 3. Форматирование для строк в кавычках (Тёмно-красный цвет)
        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#a31515"))
        self.highlighting_rules.append((re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"'), string_format))

    def highlightBlock(self, text):
        for pattern, format in self.highlighting_rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), format)
