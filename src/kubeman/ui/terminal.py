import json
import shlex
import shutil
import subprocess
import sys
import uuid


NODE_SHELL_IMAGE = "docker.io/library/alpine"


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


def node_shell_args(context, node, namespace="default", image=NODE_SHELL_IMAGE):
    """Linux pod/namespace setup used by kvaps/kubectl-node-shell.

    Use kubectl's attached run/--rm lifecycle so this works on all local
    platforms without installing the shell plugin or requiring a local shell.
    """
    pod = f"nsenter-{uuid.uuid4().hex[:12]}"
    overrides = {"spec": {
        "nodeName": node,
        "hostPID": True,
        "hostIPC": True,
        "hostNetwork": True,
        "containers": [{
            "name": "nsenter", "image": image,
            "securityContext": {"privileged": True},
            "stdin": True, "stdinOnce": True, "tty": True,
            "command": ["nsenter", "--target", "1", "--mount", "--uts", "--ipc",
                        "--net", "--pid", "bash", "-l"],
        }],
        "tolerations": [
            {"key": "CriticalAddonsOnly", "operator": "Exists"},
            {"effect": "NoExecute", "operator": "Exists"},
        ],
    }}
    return ["kubectl", "--context", context, "--namespace", namespace,
            "run", pod, "--image", image, "--restart=Never", "--rm", "-it",
            "--pod-running-timeout=1m", "--overrides", json.dumps(overrides)]


def open_terminal(argv):
    """Run argv in a new console window that stays open after it exits (so errors are readable)."""
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/k"] + argv, creationflags=subprocess.CREATE_NEW_CONSOLE)
    elif sys.platform == "darwin":
        # Pass the shell command as an AppleScript argument, never as script source.
        script = ('on run argv\n tell application "Terminal"\n activate\n'
                  ' do script (item 1 of argv)\n end tell\nend run')
        subprocess.run(["osascript", "-e", script, shlex.join(argv)], check=True,
                       capture_output=True, text=True, timeout=15)
    else:
        command = (shlex.join(argv) + '; status=$?; printf "\\nSession ended. Press Enter to close."; '
                   'read -r answer; exit "$status"')
        for terminal, args in (("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]),
                               ("konsole", ["-e"]), ("xterm", ["-e"])):
            executable = shutil.which(terminal)
            if executable:
                subprocess.Popen([executable, *args, "bash", "-c", command])
                return
        raise RuntimeError("No supported terminal found (x-terminal-emulator, gnome-terminal, konsole or xterm)")
