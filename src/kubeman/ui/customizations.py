import json

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QCompleter, QDialog, QDialogButtonBox, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout)


DEFAULT_LABELS = {"Node": ["karpenter.sh/nodepool"], "Pod": []}


def normalize_label_keys(keys):
    result = []
    for value in keys:
        key = value.strip()
        if not key:
            continue
        if "=" in key or any(char.isspace() for char in key):
            raise ValueError("Enter label keys without values, one per line.")
        if key not in result:
            result.append(key)
    return result


def parse_label_columns(lines):
    keys, headers = [], {}
    for line in lines:
        key, _, heading = line.partition("=")
        key = key.strip()
        if not key:
            if line.strip():
                raise ValueError("A column heading must have a label key before '='.")
            continue
        keys.append(key)
        if heading.strip():
            headers[key] = heading.strip()
        else:
            headers.pop(key, None)
    return normalize_label_keys(keys), headers


class LabelSettings(QObject):
    changed = Signal()

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.store = settings if settings is not None else QSettings(
            QSettings.IniFormat, QSettings.UserScope, "Kubeman", "Kubeman")
        self._labels = {kind: list(keys) for kind, keys in DEFAULT_LABELS.items()}
        self._headers = {kind: {} for kind in DEFAULT_LABELS}
        try:
            saved = json.loads(self.store.value("columns/labels", "{}"))
        except (ValueError, TypeError):
            saved = {}
        if isinstance(saved, dict):
            for kind in DEFAULT_LABELS:
                keys = saved.get(kind)
                if isinstance(keys, list) and all(isinstance(k, str) for k in keys):
                    try:
                        self._labels[kind] = normalize_label_keys(keys)
                    except ValueError:
                        pass
            headers = saved.get("headers", {})
            if isinstance(headers, dict):
                for kind in DEFAULT_LABELS:
                    aliases = headers.get(kind, {})
                    if isinstance(aliases, dict):
                        self._headers[kind] = {
                            key: title.strip() for key, title in aliases.items()
                            if key in self._labels[kind] and isinstance(title, str) and title.strip()
                            and "\n" not in title and "\r" not in title}

    def labels(self, kind):
        return list(self._labels.get(kind, []))

    def headers(self, kind):
        return dict(self._headers.get(kind, {}))

    def set_labels(self, kind, keys, headers=None):
        if kind not in DEFAULT_LABELS:
            raise ValueError(f"Unsupported label column kind: {kind}")
        updated = {**self._labels, kind: normalize_label_keys(keys)}
        aliases = self._headers[kind] if headers is None else headers
        if any(not isinstance(title, str) or "\n" in title or "\r" in title for title in aliases.values()):
            raise ValueError("Column headings must be single-line text.")
        updated_headers = {**self._headers, kind: {
            key: title.strip() for key, title in aliases.items() if key in updated[kind] and title.strip()}}
        self.store.setValue("columns/labels", json.dumps({**updated, "headers": updated_headers}))
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save customizations to {self.store.fileName()}")
        self._labels = updated
        self._headers = updated_headers
        self.changed.emit()


class LabelColumnsDialog(QDialog):
    def __init__(self, kind, settings, available=(), parent=None):
        super().__init__(parent)
        self.kind, self.settings = kind, settings
        self.setWindowTitle(f"Customizations — {kind} label columns")
        self.resize(650, 400)
        layout = QVBoxLayout(self)
        hint = QLabel("One label key per line, in column order. Use key = Short heading to rename a column.\n"
                      "Leave empty to hide label columns.\n"
                      "Saved for all clusters and future sessions.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.available = QComboBox()
        self.available.setEditable(True)
        self.available.setInsertPolicy(QComboBox.NoInsert)
        self.available.setMaxVisibleItems(12)
        self.available.addItems(sorted(available))
        self.available.setCurrentIndex(-1)
        self.available.lineEdit().setPlaceholderText("Choose or enter a label key…")
        self.available.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.available.completer().setFilterMode(Qt.MatchContains)
        self.available.completer().setCaseSensitivity(Qt.CaseInsensitive)
        add = QPushButton("Add label")
        add.clicked.connect(self._add)
        picker = QHBoxLayout()
        picker.addWidget(self.available, 1)
        self.heading = QLineEdit(placeholderText="Short heading (optional)")
        picker.addWidget(self.heading, 1)
        picker.addWidget(add)
        layout.addLayout(picker)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("karpenter.sh/nodepool = Pool\nexample.com/team = Team")
        headings = settings.headers(kind)
        self.editor.setPlainText("\n".join(
            f"{key} = {headings[key]}" if key in headings else key for key in settings.labels(kind)))
        layout.addWidget(self.editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(
            lambda: self.editor.setPlainText("\n".join(DEFAULT_LABELS[kind])))
        layout.addWidget(buttons)

    def _add(self):
        key = self.available.currentText().strip()
        heading = self.heading.text().strip()
        entry = f"{key} = {heading}" if heading else key
        if not key:
            return
        lines = self.editor.toPlainText().splitlines()
        for i, line in enumerate(lines):
            if line.partition("=")[0].strip() == key:
                if heading:
                    lines[i] = entry
                    self.editor.setPlainText("\n".join(lines))
                return
        if key:
            text = self.editor.toPlainText().rstrip()
            self.editor.setPlainText(text + ("\n" if text else "") + entry)

    def _save(self):
        try:
            keys, headings = parse_label_columns(self.editor.toPlainText().splitlines())
            self.settings.set_labels(self.kind, keys, headings)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Cannot save customizations", str(error))
            return
        self.accept()
