from time import perf_counter

from PySide6.QtCore import QThreadPool, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDockWidget, QInputDialog, QLabel, QMainWindow, QMessageBox,
                               QTabWidget, QToolBar)

from ..k8s.resources import SPECS
from ..workers import Worker
from .detail_panel import DetailPanel
from .edit_dialog import EditDialog
from .log_view import LogView
from .namespace_dialog import NamespaceDialog
from .overview import CLUSTER_CARDS, POD_COLUMNS, WORKLOAD_CARDS, OverviewView
from .resource_table import ResourceTable
from .scale_monitor import ScaleMonitor
from .terminal import kubectl_args, open_terminal

ALL_NS = "All namespaces"
KIND_VIEW = {"Deployment": "deployments", "ReplicaSet": "replicasets", "StatefulSet": "statefulsets"}


class ClusterWorkspace(QMainWindow):
    """Everything for one cluster: its own connection, view tabs, namespace filter, details and log docks.

    Embedded as a tab of the main window, so several clusters can be open side by side.
    """

    message = Signal(str)  # status-bar text for the shell
    state = Signal(str)  # connecting | connected | failed
    failed = Signal(str)  # connection error text; the shell closes this workspace
    view_opened = Signal(str)  # view key now showing, so the navigator can follow

    def __init__(self, service, context, parent=None):
        super().__init__(parent)
        self.service = service
        self.context = context
        self.pool = QThreadPool.globalInstance()
        self._workers = set()
        self._loads = {}  # view -> latest requested namespace
        self._loading = set()
        self._pending_loads = set()
        self._closed = False
        self._opened_default = False
        self.setWindowFlags(Qt.Widget)
        # side docks span the full height; the log dock sits under the central tabs only
        self.setCorner(Qt.BottomLeftCorner, Qt.LeftDockWidgetArea)
        self.setCorner(Qt.BottomRightCorner, Qt.RightDockWidgetArea)

        self._build_views()

        # central: closable tabs, one per opened view (the widgets persist when a tab is closed)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.tabs.removeTab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

        # right: object details
        self.detail = DetailPanel()
        self.detail_dock = QDockWidget("Details (YAML)", self)
        self.detail_dock.setWidget(self.detail)
        self.addDockWidget(Qt.RightDockWidgetArea, self.detail_dock)

        # bottom: log tabs, hidden until the first logs are opened
        self.log_tabs = QTabWidget()
        self.log_tabs.setTabsClosable(True)
        self.log_tabs.setDocumentMode(True)
        self.log_tabs.tabCloseRequested.connect(self._close_log_tab)
        self.log_dock = QDockWidget("Logs && activity", self)
        self.log_dock.setWidget(self.log_tabs)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.log_dock.hide()

        for widget, _ in self.views.values():
            tables = widget.tables() if isinstance(widget, OverviewView) else [widget]
            for t in tables:
                t.selected.connect(self.detail.show_row)
            if isinstance(widget, OverviewView):  # pending/failed pods listed on an overview behave like the Pods tab
                widget.pods_table.edit_requested.connect(self.edit_resource)
                widget.pods_table.action_requested.connect(self.pod_action)
            if isinstance(widget, ResourceTable):
                widget.edit_requested.connect(self.edit_resource)
                widget.scale_requested.connect(self.scale_resource)
                widget.action_requested.connect(self.pod_action)
        self.ns_table.new_requested.connect(self.new_namespace)

        bar = QToolBar()
        bar.setMovable(False)
        self.addToolBar(bar)
        self.ns_combo = QComboBox()
        self.ns_combo.addItem(ALL_NS)
        bar.addWidget(QLabel(" Namespace: "))
        bar.addWidget(self.ns_combo)
        refresh = QAction("Refresh", self)
        refresh.setShortcut(QKeySequence.Refresh)
        refresh.triggered.connect(self.refresh)
        bar.addAction(refresh)
        self.ns_combo.activated.connect(lambda _=None: self.load_scoped())

    def start(self):
        """Connect; called by the shell after it has hooked up the signals."""
        self.state.emit("connecting")
        self.message.emit(f"Connecting to {self.context}…")
        self._run(self.service.connect, lambda _: self._connected(), self.context, on_error=self._connect_failed)

    def _build_views(self):
        T = ResourceTable
        self.ns_table = T([("Name", "name"), ("Status", "status"), ("Age", "age"), ("Labels", "labels")], "Namespace")
        self.pod_table = T(POD_COLUMNS, "Pod")
        self.node_table = T([("Name", "name"), ("Status", "status"), ("Roles", "roles"), ("Version", "version"),
                             ("IP", "ip"), ("CPU (cores)", "cpu", lambda v: f"{v:.1f}"),
                             ("Memory (GiB)", "memory", lambda v: f"{v:.2f}"), ("Age", "age")], "Node")
        self.rs_table = T([("Name", "name"), ("Namespace", "namespace"), ("Desired", "desired"), ("Current", "current"),
                           ("Ready", "ready"), ("Owner", "owner"), ("Age", "age")], "ReplicaSet")
        self.deploy_table = T([("Name", "name"), ("Namespace", "namespace"), ("Ready", "ready"),
                               ("Up-to-date", "up_to_date"), ("Available", "available"), ("Age", "age")], "Deployment")
        self.sts_table = T([("Name", "name"), ("Namespace", "namespace"), ("Ready", "ready"), ("Age", "age")], "StatefulSet")
        self.overview = OverviewView(CLUSTER_CARDS)
        self.wl_overview = OverviewView(WORKLOAD_CARDS)

        # navigator key -> (widget, tab title)
        self.views = {
            "overview": (self.overview, "Overview"), "workloads": (self.wl_overview, "Workloads"),
            "nodes": (self.node_table, "Nodes"), "namespaces": (self.ns_table, "Namespaces"),
            "pods": (self.pod_table, "Pods"), "deployments": (self.deploy_table, "Deployments"),
            "replicasets": (self.rs_table, "ReplicaSets"), "statefulsets": (self.sts_table, "StatefulSets"),
        }
        # navigator key -> (fetch(namespace) -> data, namespace_scoped, sink(data)); service methods are
        # looked up at call time so a swapped/faked service keeps working
        svc = lambda: self.service  # noqa: E731
        self.loaders = {
            "overview": (lambda ns: svc().overview_cluster(), False, self.overview.set_data),
            "workloads": (lambda ns: svc().overview_workloads(ns), True, self.wl_overview.set_data),
            "nodes": (lambda ns: svc().list_nodes(), False, self.node_table.set_rows),
            "namespaces": (lambda ns: svc().list_namespaces(), False, self._set_namespaces),
            "pods": (lambda ns: svc().list_pods(ns), True, self.pod_table.set_rows),
            "deployments": (lambda ns: svc().list_deployments(ns), True, self.deploy_table.set_rows),
            "replicasets": (lambda ns: svc().list_replicasets(ns), True, self.rs_table.set_rows),
            "statefulsets": (lambda ns: svc().list_statefulsets(ns), True, self.sts_table.set_rows),
        }
        for spec in SPECS:
            table = T(spec.columns, spec.kind, editable=spec.editable)
            self.views[spec.key] = (table, spec.title)
            self.loaders[spec.key] = (lambda ns, k=spec.key: svc().list_resource(k, ns), spec.namespaced, table.set_rows)
        for key, (widget, _) in self.views.items():
            widget.view_key = key

    # ---- views / tabs -------------------------------------------------
    def _show_view(self, key):
        widget, title = self.views[key]
        idx = self.tabs.indexOf(widget)
        if idx < 0:
            idx = self.tabs.addTab(widget, title)
        self.tabs.setCurrentIndex(idx)
        self.view_opened.emit(key)

    def _on_tab_changed(self, index):
        if index >= 0:
            self.view_opened.emit(self.tabs.widget(index).view_key)

    def open_view(self, key):
        self._show_view(key)
        self.load_view(key)

    def open_keys(self):
        return [self.tabs.widget(i).view_key for i in range(self.tabs.count())]

    def load_view(self, key):
        if self._closed:
            return
        fetch, scoped, sink = self.loaders[key]
        ns = self.ns_combo.currentText()
        namespace = None if (not scoped or ns == ALL_NS) else ns
        self._loads[key] = namespace
        if key in self._loading:
            self._pending_loads.add(key)
            return
        self._loading.add(key)
        title = self.views[key][1]
        widget = self.views[key][0]
        table = widget if isinstance(widget, ResourceTable) else None
        if table is not None:
            table.set_loading()
        started = perf_counter()
        self.message.emit(f"{self.context} / {title}: Loading…")

        def finished(data, error=False):
            self._loading.discard(key)
            if self._closed:
                return
            if self._loads[key] == namespace:
                if error:
                    if table is not None:
                        table.set_error(data)
                    self.message.emit(f"{self.context} / {title}: {data}")
                else:
                    sink(data)
                    count = f"{len(data):,} rows, " if isinstance(data, list) else ""
                    self.message.emit(f"{self.context} / {title}: {count}{perf_counter() - started:.1f}s")
            if key in self._pending_loads:
                self._pending_loads.discard(key)
                self.load_view(key)

        self._run(fetch, finished, namespace, on_error=lambda m: finished(m, error=True))

    def open_logs(self, row, containers):
        key = (row.namespace, row.name)
        for i in range(self.log_tabs.count()):
            if getattr(self.log_tabs.widget(i), "key", None) == key:
                self.log_tabs.setCurrentIndex(i)
                break
        else:
            view = LogView(self.service, row.namespace, row.name, containers)
            view.key = key
            self.log_tabs.setCurrentIndex(self.log_tabs.addTab(view, f"Logs: {row.name}"))
        self.log_dock.show()

    def _close_log_tab(self, index):
        view = self.log_tabs.widget(index)
        self.log_tabs.removeTab(index)
        view.stop()
        view.deleteLater()
        if self.log_tabs.count() == 0:
            self.log_dock.hide()

    def shutdown(self):
        """Stop every log stream; the shell calls this before discarding the workspace."""
        self._closed = True
        self._pending_loads.clear()
        for i in range(self.log_tabs.count()):
            self.log_tabs.widget(i).stop()

    # ---- worker plumbing ----------------------------------------------
    def _run(self, fn, on_result, *args, on_error=None):
        w = Worker(fn, *args)
        w.setAutoDelete(False)  # keep w.signals alive until its queued signal is delivered
        self._workers.add(w)

        def done(cb, *a):
            self._workers.discard(w)
            if not self._closed:  # disconnected while this call was in flight: drop the result
                cb(*a)

        w.signals.result.connect(lambda r: done(on_result, r))
        w.signals.error.connect(lambda m: done(on_error or self._on_error, m))
        self.pool.start(w)

    def _on_error(self, msg):
        self.message.emit(f"{self.context}: {msg}")
        QMessageBox.warning(self, "Kubernetes error", f"{self.context}\n\n{msg}")

    # ---- actions -------------------------------------------------------
    def edit_resource(self, kind, row):
        dlg = EditDialog(f"Edit {kind} {row.name}", row.raw, self)
        if dlg.exec() != EditDialog.Accepted:
            return
        self.message.emit(f"Saving {kind} {row.name}…")
        self._run(self.service.replace, lambda _: self._saved(kind, row.name), kind, dlg.result_body)

    def scale_resource(self, kind, row):
        current = (row.raw.get("spec") or {}).get("replicas", 1)
        n, ok = QInputDialog.getInt(self, f"Scale {kind}", f"Replicas for {row.name}:", current, 0, 10000)
        if not ok:
            return
        self.message.emit(f"Scaling {kind} {row.name} to {n}…")
        self._run(self.service.scale, lambda _: self._scaled(kind, row, current, n), kind, row.name, row.namespace, n)

    def _scaled(self, kind, row, old, new):
        self.message.emit(f"Scaling {kind} {row.name}: {old} → {new}")
        self.open_scale_monitor(kind, row, old, new)
        self.refresh()

    def open_scale_monitor(self, kind, row, old, new):
        """Track a scale in the bottom dock; scaling the same workload again restarts its existing monitor."""
        key = ("scale", kind, row.namespace, row.name)
        for i in range(self.log_tabs.count()):
            view = self.log_tabs.widget(i)
            if getattr(view, "key", None) == key:
                view.restart(old, new)
                self.log_tabs.setCurrentIndex(i)
                self.log_dock.show()
                return view
        mon = ScaleMonitor(self._run, self.service, kind, row.name, row.namespace, old, new)
        mon.key = key
        mon.finished.connect(lambda state, text: self._scale_finished(kind, row.name, text))
        mon.polled.connect(lambda: self._reload_scaled(kind))
        self.log_tabs.setCurrentIndex(self.log_tabs.addTab(mon, f"Scale: {row.name}"))
        self.log_dock.show()
        return mon

    def _reload_scaled(self, kind):
        """Keep the workload and pod lists live while a scale is in flight (only tabs that are open)."""
        open_keys = self.open_keys()
        for key in (KIND_VIEW[kind], "pods"):
            if key in open_keys:
                self.load_view(key)

    def _scale_finished(self, kind, name, text):
        self.message.emit(f"{kind} {name}: {text}")
        self.refresh()

    def pod_action(self, action, row):
        if action == "terminate":
            ok = QMessageBox.question(self, "Terminate pod", f"Delete pod {row.namespace}/{row.name}? "
                                      "Controller-managed pods will be recreated.")
            if ok != QMessageBox.Yes:
                return
            self.message.emit(f"Terminating {row.name}…")
            self._run(self.service.delete_pod, lambda _: self._saved("Pod", row.name), row.name, row.namespace)
            return
        containers = [c["name"] for c in (row.raw.get("spec") or {}).get("containers", [])]
        if action == "logs":
            self.open_logs(row, containers)
            return
        container = None
        if len(containers) > 1:
            container, ok = QInputDialog.getItem(self, "Container", "Container:", containers, 0, False)
            if not ok:
                return
        argv = kubectl_args(action, self.context, row.namespace, row.name, container)
        try:
            open_terminal(argv)
        except Exception as e:
            self._on_error(f"{type(e).__name__}: {e}")

    def new_namespace(self):
        dlg = NamespaceDialog(self)
        if dlg.exec() != NamespaceDialog.Accepted:
            return
        name = dlg.result_body["metadata"]["name"]
        self.message.emit(f"Creating namespace {name}…")
        self._run(self.service.create_namespace, lambda _: self._saved("Namespace", name), dlg.result_body)

    def _saved(self, kind, name):
        self.message.emit(f"Saved {kind} {name}")  # also used after create
        self.refresh()

    # ---- connection / data ---------------------------------------------
    def _connect_failed(self, msg):
        self.state.emit("failed")
        self.failed.emit(msg)

    def _connected(self):
        self.state.emit("connected")
        self.message.emit(f"Connected to {self.context}")
        if not self._opened_default:
            self._opened_default = True
            self._show_view("overview")  # refresh() below loads it
        self.refresh()

    def refresh(self):
        """Reload every open tab, plus the namespace list that feeds the toolbar filter."""
        for key in dict.fromkeys(["namespaces"] + self.open_keys()):
            self.load_view(key)

    def load_scoped(self):
        for key in self.open_keys():
            if self.loaders[key][1]:
                self.load_view(key)

    def _set_namespaces(self, rows):
        self.ns_table.set_rows(rows)
        names = [r.name for r in rows]
        current = self.ns_combo.currentText()
        self.ns_combo.blockSignals(True)
        self.ns_combo.clear()
        self.ns_combo.addItems([ALL_NS] + names)
        self.ns_combo.setCurrentText(current if current in names else ALL_NS)
        self.ns_combo.blockSignals(False)
