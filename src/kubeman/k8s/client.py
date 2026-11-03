from heapq import nlargest

from kubernetes import client, config
from kubernetes.utils import parse_quantity
from urllib3.exceptions import MaxRetryError

from .models import DeploymentRow, NamespaceRow, NodeRow, PodRow, ReplicaSetRow, StatefulSetRow
from .resources import MIN_TS, SPEC_BY_KEY, SPEC_BY_KIND
from .util import format_age, to_dict as _raw  # format_age is re-exported for callers/tests


# pod states that mean a pod will not become ready without intervention
FAILING_STATES = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "InvalidImageName", "CreateContainerConfigError",
                  "CreateContainerError", "RunContainerError", "Error", "OOMKilled", "Failed", "Evicted"}


def pod_buckets(pods):
    """(running, pending, failed) from mapped pod rows; completed pods count as none of them."""
    running = pending = failed = 0
    for p in pods:
        s = p.status
        if s == "Running":
            running += 1
        elif s in ("Succeeded", "Completed"):
            continue
        elif s in ("Pending", "ContainerCreating", "PodInitializing", "Terminating") or s.startswith("Init:"):
            pending += 1
        else:
            failed += 1
    return running, pending, failed


def map_namespace(ns):
    labels = ",".join(f"{k}={v}" for k, v in (ns.metadata.labels or {}).items())
    return NamespaceRow(ns.metadata.name, ns.status.phase or "", format_age(ns.metadata.creation_timestamp), labels, _raw(ns))


def map_pod(p, include_raw=True):
    statuses = p.status.container_statuses or []
    ready = sum(1 for c in statuses if c.ready)
    total = len(p.spec.containers or [])
    status = p.status.phase or ""
    for c in statuses:
        w = c.state.waiting if c.state else None
        if w and w.reason:
            status = w.reason
            break
    if p.metadata.deletion_timestamp:
        status = "Terminating"
    return PodRow(
        p.metadata.name, p.metadata.namespace, f"{ready}/{total}", status,
        sum(c.restart_count for c in statuses), p.spec.node_name or "",
        format_age(p.metadata.creation_timestamp), _raw(p) if include_raw else {},
    )


def map_node(n, include_raw=True):
    labels = n.metadata.labels or {}
    roles = ",".join(sorted(k.split("/", 1)[1] for k in labels if k.startswith("node-role.kubernetes.io/"))) or "<none>"
    ready = next((c.status for c in n.status.conditions or [] if c.type == "Ready"), "Unknown")
    ip = next((a.address for a in n.status.addresses or [] if a.type == "InternalIP"), "")
    alloc = n.status.allocatable or {}
    return NodeRow(
        n.metadata.name, "Ready" if ready == "True" else "NotReady", roles,
        n.status.node_info.kubelet_version if n.status.node_info else "", ip,
        alloc.get("cpu", ""), alloc.get("memory", ""),
        format_age(n.metadata.creation_timestamp), _raw(n) if include_raw else {},
    )


def map_replicaset(r):
    owner = next((f"{o.kind}/{o.name}" for o in r.metadata.owner_references or []), "")
    return ReplicaSetRow(
        r.metadata.name, r.metadata.namespace, r.spec.replicas or 0, r.status.replicas or 0,
        r.status.ready_replicas or 0, owner, format_age(r.metadata.creation_timestamp), _raw(r),
    )


def map_deployment(d):
    st = d.status
    return DeploymentRow(
        d.metadata.name, d.metadata.namespace, f"{st.ready_replicas or 0}/{d.spec.replicas or 0}",
        st.updated_replicas or 0, st.available_replicas or 0, format_age(d.metadata.creation_timestamp), _raw(d),
    )


def map_statefulset(s):
    return StatefulSetRow(
        s.metadata.name, s.metadata.namespace, f"{s.status.ready_replicas or 0}/{s.spec.replicas or 0}",
        format_age(s.metadata.creation_timestamp), _raw(s),
    )


class KubeService:
    """Thin wrapper over the kubernetes client; returns plain row objects the UI can render."""

    def __init__(self):
        self._core = None
        self._apps = None

    @staticmethod
    def load_contexts():
        """Returns (context names, active context name or None)."""
        try:
            contexts, active = config.list_kube_config_contexts()
        except config.ConfigException:
            return [], None
        return [c["name"] for c in contexts], (active or {}).get("name")

    def connect(self, context):
        cfg = client.Configuration()
        config.load_kube_config(context=context, client_configuration=cfg)
        cfg.retries = 1  # the default (3 retries with backoff) makes a dead cluster take ~9s to report
        api = client.ApiClient(cfg)
        core = client.CoreV1Api(api)
        try:
            core.get_api_resources(_request_timeout=10)  # fail fast on bad creds/unreachable
        except MaxRetryError as e:
            raise ConnectionError(f"Cannot reach the API server at {cfg.host}") from e
        self._core = core
        self._apps = client.AppsV1Api(api)
        self._apis = {"core": core, "apps": self._apps, "batch": client.BatchV1Api(api),
                      "net": client.NetworkingV1Api(api), "storage": client.StorageV1Api(api)}

    def create_namespace(self, body):
        return map_namespace(self._core.create_namespace(body))

    def replace(self, kind, body):
        """Replace a resource with an edited manifest (like `kubectl replace`); returns the stored row."""
        meta, api = body["metadata"], self._core
        if kind == "Namespace":
            return map_namespace(api.replace_namespace(meta["name"], body))
        if kind == "Pod":
            return map_pod(api.replace_namespaced_pod(meta["name"], meta["namespace"], body))
        if kind == "Node":
            return map_node(api.replace_node(meta["name"], body))
        apps = self._apps
        ns = meta.get("namespace")
        if kind == "ReplicaSet":
            return map_replicaset(apps.replace_namespaced_replica_set(meta["name"], ns, body))
        if kind == "Deployment":
            return map_deployment(apps.replace_namespaced_deployment(meta["name"], ns, body))
        if kind == "StatefulSet":
            return map_statefulset(apps.replace_namespaced_stateful_set(meta["name"], ns, body))
        spec = SPEC_BY_KIND.get(kind)
        if spec is None or not spec.editable:
            raise ValueError(f"Editing {kind} is not supported")
        api = self._apis[spec.api]
        if spec.namespaced:
            return spec.mapper(getattr(api, f"replace_namespaced_{spec.snake}")(meta["name"], ns, body))
        return spec.mapper(getattr(api, f"replace_{spec.snake}")(meta["name"], body))

    def scale(self, kind, name, namespace, replicas):
        """Set spec.replicas via the scale subresource."""
        body = {"spec": {"replicas": replicas}}
        apps = self._apps
        fn = {"Deployment": apps.patch_namespaced_deployment_scale,
              "ReplicaSet": apps.patch_namespaced_replica_set_scale,
              "StatefulSet": apps.patch_namespaced_stateful_set_scale}.get(kind)
        if fn is None:
            raise ValueError(f"Scaling {kind} is not supported")
        fn(name, namespace, body)

    def open_log_stream(self, namespace, pod, container=None, tail=500):
        """Returns a streaming urllib3 response (follow=True); the caller reads and closes it."""
        return self._core.read_namespaced_pod_log(pod, namespace, container=container, follow=True,
                                                  tail_lines=tail, _preload_content=False)

    def delete_pod(self, name, namespace):
        self._core.delete_namespaced_pod(name, namespace)

    @staticmethod
    def _iter_list(fn, *args, **kwargs):
        """Bound each API response and release each page before fetching the next."""
        token = None
        while True:
            page = fn(*args, limit=500, _continue=token, _request_timeout=30, **kwargs)
            yield from page.items
            token = page.metadata._continue if page.metadata else None
            del page
            if not token:
                break

    def list_namespaces(self):
        return [map_namespace(n) for n in self._iter_list(self._core.list_namespace)]

    def list_pods(self, namespace=None):
        api = self._core
        items = (self._iter_list(api.list_namespaced_pod, namespace) if namespace
                 else self._iter_list(api.list_pod_for_all_namespaces))
        return [map_pod(p) for p in items]

    def list_nodes(self):
        return [map_node(n) for n in self._iter_list(self._core.list_node)]

    def _list_apps(self, all_fn, ns_fn, mapper, namespace):
        items = self._iter_list(ns_fn, namespace) if namespace else self._iter_list(all_fn)
        return [mapper(i) for i in items]

    def list_replicasets(self, namespace=None):
        a = self._apps
        return self._list_apps(a.list_replica_set_for_all_namespaces, a.list_namespaced_replica_set, map_replicaset, namespace)

    def list_deployments(self, namespace=None):
        a = self._apps
        return self._list_apps(a.list_deployment_for_all_namespaces, a.list_namespaced_deployment, map_deployment, namespace)

    def list_statefulsets(self, namespace=None):
        a = self._apps
        return self._list_apps(a.list_stateful_set_for_all_namespaces, a.list_namespaced_stateful_set, map_statefulset, namespace)

    def list_resource(self, key, namespace=None):
        """Generic list for the specs in resources.py; events come back newest first."""
        spec = SPEC_BY_KEY[key]
        rows = [spec.mapper(i) for i in self._resource_items(key, namespace)]
        if key == "events":
            rows.sort(key=lambda r: r.ts, reverse=True)
        return rows

    def _resource_items(self, key, namespace=None):
        spec = SPEC_BY_KEY[key]
        api = self._apis[spec.api]
        if not spec.namespaced:
            return self._iter_list(getattr(api, f"list_{spec.snake}"))
        if namespace:
            return self._iter_list(getattr(api, f"list_namespaced_{spec.snake}"), namespace)
        return self._iter_list(getattr(api, f"list_{spec.snake}_for_all_namespaces"))

    def _overview_events(self, namespace=None):
        warnings = 0

        def events():
            nonlocal warnings
            for event in self._resource_items("events", namespace):
                warnings += event.type == "Warning"
                yield event

        recent = nlargest(25, events(), key=lambda e: (
            e.last_timestamp or e.event_time or e.metadata.creation_timestamp or MIN_TS))
        return [SPEC_BY_KEY["events"].mapper(e) for e in recent], warnings

    def _overview_pods(self, namespace=None):
        api = self._core
        items = (self._iter_list(api.list_namespaced_pod, namespace) if namespace
                 else self._iter_list(api.list_pod_for_all_namespaces))
        total = 0

        def rows():
            nonlocal total
            for pod in items:
                total += 1
                yield map_pod(pod, include_raw=False)

        running, pending, failed = pod_buckets(rows())
        return {"pods": total, "running": running, "pending": pending, "failed": failed}

    def overview_cluster(self):
        nodes = [map_node(n, include_raw=False) for n in self._iter_list(self._core.list_node)]
        counts = self._overview_pods()
        events, warnings = self._overview_events()
        cpu = sum(parse_quantity(n.cpu) for n in nodes if n.cpu)
        mem = sum(parse_quantity(n.memory) for n in nodes if n.memory)
        counts.update({
            "nodes": len(nodes), "nodes_ready": sum(1 for n in nodes if n.status == "Ready"),
            "namespaces": sum(1 for _ in self._iter_list(self._core.list_namespace)),
            "cpu": f"{float(cpu):g} cores", "memory": f"{float(mem) / 2**30:.1f} GiB",
            "warnings": warnings,
        })
        return {"counts": counts, "events": events}

    def overview_workloads(self, namespace=None):
        counts = self._overview_pods(namespace)
        events, warnings = self._overview_events(namespace)
        apps = self._apps
        for key, all_fn, ns_fn in (
            ("deployments", apps.list_deployment_for_all_namespaces, apps.list_namespaced_deployment),
            ("replicasets", apps.list_replica_set_for_all_namespaces, apps.list_namespaced_replica_set),
            ("statefulsets", apps.list_stateful_set_for_all_namespaces, apps.list_namespaced_stateful_set),
        ):
            items = self._iter_list(ns_fn, namespace) if namespace else self._iter_list(all_fn)
            counts[key] = sum(1 for _ in items)
        for key in ("daemonsets", "jobs", "cronjobs"):
            counts[key] = sum(1 for _ in self._resource_items(key, namespace))
        counts["warnings"] = warnings
        return {"counts": counts, "events": events}

    def scale_status(self, kind, name, namespace):
        """Snapshot used by the scale monitor: replica counts, per-pod state with failure reasons, warnings."""
        apps = self._apps
        read = {"Deployment": apps.read_namespaced_deployment, "ReplicaSet": apps.read_namespaced_replica_set,
                "StatefulSet": apps.read_namespaced_stateful_set}[kind]
        obj = read(name, namespace, _request_timeout=15)
        st = obj.status
        selector = ",".join(f"{k}={v}" for k, v in (obj.spec.selector.match_labels or {}).items())
        items = self._core.list_namespaced_pod(namespace, label_selector=selector, _request_timeout=15).items if selector else []
        pods = []
        for p in items:
            row = map_pod(p)
            issue = row.status if row.status in FAILING_STATES else ""
            for c in p.status.conditions or []:
                if c.type == "PodScheduled" and c.status == "False":
                    issue = f"Unschedulable: {(c.message or c.reason or '')[:120]}"
            for cs in p.status.container_statuses or []:
                t = cs.last_state.terminated if cs.last_state else None
                if t and t.reason in ("OOMKilled", "Error") and cs.restart_count and not issue:
                    issue = f"{t.reason} (restarted {cs.restart_count}x)"
            pods.append({"name": row.name, "status": row.status, "ready": row.ready, "restarts": row.restarts,
                         "issue": issue, "deleting": bool(p.metadata.deletion_timestamp)})
        names = {p["name"] for p in pods} | {name}
        warnings = []
        try:  # events are best effort; a failure here must not break the monitor
            evs = self._core.list_namespaced_event(namespace, field_selector="type=Warning", _request_timeout=15).items
            evs = [e for e in evs if e.involved_object and e.involved_object.name in names]
            evs.sort(key=lambda e: e.last_timestamp or e.event_time or e.metadata.creation_timestamp, reverse=True)
            warnings = [f"{e.involved_object.name}: {e.reason}: {(e.message or '')[:160]}" for e in evs[:5]]
        except Exception:
            pass
        deadline = any(c.type == "Progressing" and c.reason == "ProgressDeadlineExceeded" for c in st.conditions or [])
        return {"desired": obj.spec.replicas or 0, "ready": st.ready_replicas or 0,
                "available": getattr(st, "available_replicas", None) or 0, "pods": pods, "warnings": warnings,
                "observed": (st.observed_generation or 0) >= (obj.metadata.generation or 0), "deadline_exceeded": deadline}
