from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

STATE_COLORS = {"idle": "#6b7280", "connecting": "#f5a524", "connected": "#2fbf71", "failed": "#e5484d"}

# Shown under the connected cluster, Lens order. A leaf is (key, label); a group is
# (label, [leaves], expanded_by_default).
VIEWS = [
    ("overview", "Overview"),
    ("nodes", "Nodes"),
    ("Workloads", [("workloads", "Overview"), ("pods", "Pods"), ("deployments", "Deployments"),
                   ("daemonsets", "DaemonSets"), ("statefulsets", "StatefulSets"),
                   ("replicasets", "ReplicaSets"), ("jobs", "Jobs"), ("cronjobs", "CronJobs")], True),
    ("Config", [("configmaps", "ConfigMaps"), ("secrets", "Secrets")], False),
    ("Network", [("services", "Services"), ("ingresses", "Ingresses")], False),
    ("Storage", [("persistentvolumeclaims", "Persistent Volume Claims"),
                 ("persistentvolumes", "Persistent Volumes"), ("storageclasses", "Storage Classes")], False),
    ("namespaces", "Namespaces"),
    ("events", "Events"),
]


def dot_icon(color):
    pm = QPixmap(12, 12)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(color)))
    p.setPen(Qt.NoPen)
    p.drawEllipse(1, 1, 10, 10)
    p.end()
    return QIcon(pm)


class Navigator(QTreeWidget):
    """Lens-style sidebar: clusters with a status dot; the connected cluster expands into resource views."""

    context_clicked = Signal(str)
    view_requested = Signal(str, str)  # context name, view key

    ROLE_KIND = Qt.UserRole
    ROLE_KEY = Qt.UserRole + 1

    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self._clusters = {}
        self.itemClicked.connect(self._on_click)

    def set_contexts(self, names):
        self.clear()
        self._clusters = {}
        for n in names:
            it = QTreeWidgetItem([n])
            it.setData(0, self.ROLE_KIND, "cluster")
            it.setData(0, self.ROLE_KEY, n)
            it.setIcon(0, dot_icon(STATE_COLORS["idle"]))
            self.addTopLevelItem(it)
            self._clusters[n] = it

    def set_state(self, name, state):
        if name in self._clusters:
            self._clusters[name].setIcon(0, dot_icon(STATE_COLORS[state]))

    def show_views(self, name):
        """Expand `name` into its resource views. Any number of clusters can be expanded at once."""
        item = self._clusters.get(name)
        if item is None or item.childCount():
            return
        for entry in VIEWS:
            if len(entry) == 3:
                label, leaves, expanded = entry
                group = QTreeWidgetItem([label])
                item.addChild(group)
                for k, lab in leaves:
                    self._leaf(group, k, lab)
                group.setExpanded(expanded)
            else:
                self._leaf(item, *entry)
        item.setExpanded(True)

    def _leaf(self, parent, key, label):
        it = QTreeWidgetItem([label])
        it.setData(0, self.ROLE_KIND, "view")
        it.setData(0, self.ROLE_KEY, key)
        parent.addChild(it)

    def hide_views(self, name):
        item = self._clusters.get(name)
        if item is not None:
            item.takeChildren()

    def select_cluster(self, name):
        item = self._clusters.get(name)
        if item is not None:
            self.setCurrentItem(item)

    def select_view(self, name, key):
        def walk(item):
            for i in range(item.childCount()):
                c = item.child(i)
                if c.data(0, self.ROLE_KEY) == key:
                    self.setCurrentItem(c)
                    return True
                if walk(c):
                    return True
            return False

        item = self._clusters.get(name)
        if item:
            walk(item)

    def _on_click(self, item, _col):
        kind = item.data(0, self.ROLE_KIND)
        if kind == "cluster":
            self.context_clicked.emit(item.data(0, self.ROLE_KEY))
        elif kind == "view":
            top = item
            while top.parent() is not None:  # views live under groups under the cluster item
                top = top.parent()
            self.view_requested.emit(top.data(0, self.ROLE_KEY), item.data(0, self.ROLE_KEY))
