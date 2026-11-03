from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from ..k8s.resources import SPEC_BY_KEY
from .resource_table import ResourceTable
from .theme import BORDER, MUTED

# (counts key, caption, tone when the value is non-zero)
CLUSTER_CARDS = [("nodes", "Nodes", ""), ("nodes_ready", "Nodes ready", ""), ("namespaces", "Namespaces", ""),
                 ("cpu", "CPU allocatable", ""), ("memory", "Memory allocatable", ""),
                 ("pods", "Pods", ""), ("running", "Running", ""), ("pending", "Pending", "warn"),
                 ("failed", "Failed", "bad"), ("warnings", "Warning events", "warn")]
WORKLOAD_CARDS = [("pods", "Pods", ""), ("running", "Running", ""), ("pending", "Pending", "warn"),
                  ("failed", "Failed", "bad"), ("warnings", "Warning events", "warn"),
                  ("deployments", "Deployments", ""), ("daemonsets", "DaemonSets", ""),
                  ("statefulsets", "StatefulSets", ""), ("replicasets", "ReplicaSets", ""),
                  ("jobs", "Jobs", ""), ("cronjobs", "CronJobs", "")]
TONES = {"warn": "#f5a524", "bad": "#e5484d"}
COLUMNS = 5


class Card(QFrame):
    def __init__(self, title, tone):
        super().__init__()
        self.tone = tone
        self.setStyleSheet(f"Card {{ border: 1px solid {BORDER}; border-radius: 6px; }}")
        self.value = QLabel("–")
        self.value.setStyleSheet("font-size: 22px; font-weight: 600;")
        caption = QLabel(title)
        caption.setStyleSheet(f"color: {MUTED};")
        lay = QVBoxLayout(self)
        lay.addWidget(self.value)
        lay.addWidget(caption)

    def set_value(self, v):
        self.value.setText(str(v))
        color = TONES.get(self.tone) if v not in (0, "0") else None
        self.value.setStyleSheet(f"font-size: 22px; font-weight: 600;{f' color: {color};' if color else ''}")


class OverviewView(QWidget):
    """Summary cards over a recent-events table (Lens-style Overview pages)."""

    def __init__(self, cards):
        super().__init__()
        self.cards = {}
        grid = QGridLayout()
        for i, (key, title, tone) in enumerate(cards):
            self.cards[key] = Card(title, tone)
            grid.addWidget(self.cards[key], i // COLUMNS, i % COLUMNS)
        header = QLabel("Recent events")
        header.setStyleSheet("font-weight: 600; padding-top: 8px;")
        header.setAlignment(Qt.AlignLeft)
        self.events_table = ResourceTable(SPEC_BY_KEY["events"].columns, "Event", editable=False)
        lay = QVBoxLayout(self)
        lay.addLayout(grid)
        lay.addWidget(header)
        lay.addWidget(self.events_table, 1)

    def set_data(self, data):
        for key, card in self.cards.items():
            card.set_value(data["counts"].get(key, "–"))
        self.events_table.set_rows(data["events"])
