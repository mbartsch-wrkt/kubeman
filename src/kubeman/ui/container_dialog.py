from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout,
                               QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)


def pod_containers(raw):
    spec, status = raw.get("spec") or {}, raw.get("status") or {}
    rows = []
    for field, status_field, kind in (("containers", "containerStatuses", "Container"),
                                     ("initContainers", "initContainerStatuses", "Init"),
                                     ("ephemeralContainers", "ephemeralContainerStatuses", "Ephemeral")):
        states = {c["name"]: c for c in status.get(status_field) or []}
        for container in spec.get(field) or []:
            current = states.get(container["name"], {})
            state = current.get("state") or {}
            phase = next(iter(state), "Unknown")
            reason = (state.get(phase) or {}).get("reason")
            rows.append((container["name"], kind, reason or phase.capitalize(),
                         "Yes" if current.get("ready") else "No", str(current.get("restartCount", 0)),
                         container.get("image", "")))
    return rows


class ContainerDialog(QDialog):
    action_requested = Signal(str, str)  # action, container name

    def __init__(self, context, row, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Containers — {row.namespace}/{row.name}")
        self.resize(850, 350)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Cluster: {context}    Pod: {row.namespace}/{row.name}"))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Name", "Type", "State", "Ready", "Restarts", "Image"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for values in pod_containers(row.raw):
            index = self.table.rowCount()
            self.table.insertRow(index)
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        self.action_buttons = {}
        for action, label in (("logs", "Logs"), ("shell", "Shell…"), ("attach", "Attach…")):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, a=action: self._choose(a))
            self.action_buttons[action] = button
            actions.addWidget(button)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        actions.addStretch()
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        actions.addWidget(close)
        layout.addLayout(actions)
        if self.table.rowCount():
            self.table.selectRow(0)
        self._selection_changed()

    def _selection_changed(self):
        for button in self.action_buttons.values():
            button.setEnabled(bool(self.table.selectionModel().selectedRows()))

    def _choose(self, action):
        selected = self.table.selectionModel().selectedRows()
        if selected:
            name = self.table.item(selected[0].row(), 0).text()
            self.accept()
            self.action_requested.emit(action, name)
