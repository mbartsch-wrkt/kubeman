from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt


class TableModel(QAbstractTableModel):
    """Rows are dataclasses; columns is a list of (header, attribute) pairs."""

    def __init__(self, columns):
        super().__init__()
        self.columns, self.rows = columns, []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.EditRole):
            return None
        value = getattr(self.rows[index.row()], self.columns[index.column()][1])
        if role == Qt.DisplayRole:
            return str(value)
        if role == Qt.EditRole:  # sort role: keeps ints numeric
            return value
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.columns[section][0]
        return None
