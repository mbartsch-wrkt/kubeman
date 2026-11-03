import shlex
import shutil
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


def node_shell_args(context, node, namespace="default", image="ubuntu:24.04"):
    return ["kubectl", "--context", context, "--namespace", namespace,
            "debug", f"node/{node}", "-it", f"--image={image}", "--profile=sysadmin", "--",
            "nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid",
            "--root=/proc/1/root", "--wd=/", "--", "/bin/sh"]


def open_terminal(argv, mac_terminal="Terminal"):
    """Run argv in a new console window that stays open after it exits (so errors are readable)."""
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/k"] + argv, creationflags=subprocess.CREATE_NEW_CONSOLE)
    elif sys.platform == "darwin":
        # Pass the shell command as an AppleScript argument, never as script source.
        if mac_terminal == "Terminal":
            script = ('on run argv\n tell application "Terminal"\n activate\n'
                      ' do script (item 1 of argv)\n end tell\nend run')
        elif mac_terminal == "iTerm2":
            script = ('on run argv\n tell application "iTerm2"\n activate\n'
                      ' set shellWindow to (create window with default profile)\n'
                      ' tell current session of shellWindow\n'
                      ' write text (item 1 of argv)\n end tell\n end tell\nend run')
        else:
            raise ValueError(f"Unsupported macOS terminal: {mac_terminal}")
        try:
            subprocess.run(["osascript", "-e", script, shlex.join(argv)], check=True,
                           capture_output=True, text=True, timeout=15)
        except subprocess.CalledProcessError as error:
            raise RuntimeError(f"Could not open {mac_terminal}: {error.stderr or error}") from error
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
