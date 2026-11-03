import subprocess
import sys


def kubectl_args(action, context, namespace, pod, container=None):
    """argv for an interactive kubectl session. action: 'shell' (exec) or 'attach'."""
    base = ["kubectl", "--context", context, "-n", namespace]
    if action == "shell":
        cmd = base + ["exec", "-it", pod] + (["-c", container] if container else []) + ["--", "sh"]
    elif action == "attach":
        cmd = base + ["attach", "-it", pod] + (["-c", container] if container else [])
    else:
        raise ValueError(f"Unknown action {action}")
    return cmd


def open_terminal(argv):
    """Run argv in a new console window that stays open after it exits (so errors are readable)."""
    if sys.platform != "win32":
        raise RuntimeError("Opening a terminal is only implemented on Windows")
    subprocess.Popen(["cmd", "/k"] + argv, creationflags=subprocess.CREATE_NEW_CONSOLE)
