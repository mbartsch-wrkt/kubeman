import yaml
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QPlainTextEdit


class DetailPanel(QPlainTextEdit):
    def __init__(self):
        super().__init__(readOnly=True)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.setLineWrapMode(QPlainTextEdit.NoWrap)

    def show_row(self, row):
        self.setPlainText(yaml.safe_dump(row.raw, sort_keys=False))
