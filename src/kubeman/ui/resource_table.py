from PySide6.QtCore import QSortFilterProxyModel, Qt, Signal, QTimer
from PySide6.QtWidgets import QAbstractItemView, QLineEdit, QMenu, QTableView, QVBoxLayout, QWidget

from .table_model import TableModel


SCALABLE = {"Deployment", "ReplicaSet", "StatefulSet"}


class ResourceTable(QWidget):
    selected = Signal(object)  # row dataclass
    edit_requested = Signal(str, object)  # kind, row dataclass
    scale_requested = Signal(str, object)
    action_requested = Signal(str, object)  # action name, row dataclass
    new_requested = Signal()

    def __init__(self, columns, kind, editable=True):
        super().__init__()
        self.kind = kind
        self.editable = editable
        self.model = TableModel(columns)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.EditRole)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.filter = QLineEdit(placeholderText="Filter…", clearButtonEnabled=True)
        self._filter_timer = QTimer(self, interval=200, singleShot=True)
        self._filter_timer.timeout.connect(lambda: self.proxy.setFilterFixedString(self.filter.text()))
        self.filter.textChanged.connect(lambda _: self._filter_timer.start())
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        # Preserve API order initially; sorting every row through Python/Qt on
        # first load blocks the event loop. Header clicks still enable sorting.
        self.view.sortByColumn(-1, Qt.AscendingOrder)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.horizontalHeader().setStretchLastSection(True)
        # Column sizing runs on the GUI thread. Sample a bounded number of rows.
        self.view.horizontalHeader().setResizeContentsPrecision(100)
        self.view.verticalHeader().hide()
        self.view.verticalHeader().setDefaultSectionSize(26)  # dense rows
        self.view.setShowGrid(False)
        self.view.selectionModel().currentRowChanged.connect(self._on_row)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        lay = QVBoxLayout(self)
        lay.addWidget(self.filter)
        lay.addWidget(self.view)

    def set_rows(self, rows):
        self.model.set_rows(rows)
        self.view.resizeColumnsToContents()

    def _context_menu(self, pos):
        idx = self.view.indexAt(pos)
        menu = QMenu(self)
        if idx.isValid():
            self.view.selectRow(idx.row())
            row = self.model.rows[self.proxy.mapToSource(idx).row()]
            if self.editable:
                menu.addAction("Edit…", lambda: self.edit_requested.emit(self.kind, row))
            if self.kind in SCALABLE:
                menu.addAction("Scale…", lambda: self.scale_requested.emit(self.kind, row))
            if self.kind == "Pod":
                menu.addSeparator()
                menu.addAction("Logs", lambda: self.action_requested.emit("logs", row))
                menu.addAction("Connect (shell)…", lambda: self.action_requested.emit("shell", row))
                menu.addAction("Attach…", lambda: self.action_requested.emit("attach", row))
                menu.addSeparator()
                menu.addAction("Terminate…", lambda: self.action_requested.emit("terminate", row))
        if self.kind == "Namespace":
            menu.addAction("New namespace…", self.new_requested.emit)
        if not menu.isEmpty():
            menu.exec(self.view.viewport().mapToGlobal(pos))

    def _on_row(self, current, _prev):
        if current.isValid():
            src = self.proxy.mapToSource(current)
            self.selected.emit(self.model.rows[src.row()])
