from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Literal

Platform = Literal["macos", "linux"]

CACHELENS_DIR = Path.home() / ".cachelens"
DEFAULT_CONFIG_TOML = """\
[retention]
raw_days = 1
daily_days = 365
aggregate = true
"""

LAUNCHD_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.cachelens</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>-m</string>
        <string>cachelens</string>
        <string>daemon</string>
        <string>--port</string>
        <string>{port}</string>
        <string>--base-path</string>
        <string>{base_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_dir}/cachelens.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/cachelens.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{path}</string>
    </dict>
</dict>
</plist>
"""

SYSTEMD_SERVICE_TEMPLATE = """\
[Unit]
Description=CacheLens AI usage tracking daemon
After=network.target

[Service]
Type=simple
ExecStart={python_path} -m cachelens daemon --port {port} --base-path {base_path}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""

ENV_VARS = {
    "ANTHROPIC_BASE_URL": "http://localhost:{port}/proxy/anthropic",
    "OPENAI_BASE_URL": "http://localhost:{port}/proxy/openai",
    "GOOGLE_AI_BASE_URL": "http://localhost:{port}/proxy/google",
}

SHELL_FILES = {
    "zsh": Path.home() / ".zshrc",
    "bash": Path.home() / ".bashrc",
    "profile": Path.home() / ".profile",
}

# Markers used to delimit cachelens-managed block in shell files
_CACHELENS_START = "# >>> cachelens >>>"
_CACHELENS_END = "# <<< cachelens <<<"


def detect_platform() -> Platform:
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def is_port_in_use(port: int) -> bool:
    """Return True if port is already bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def get_daemon_pid(port: int = 8420) -> int | None:
    """Return PID of running cachelens daemon, or None."""
    pid_file = CACHELENS_DIR / "cachelens.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            # Verify process is still alive
            os.kill(pid, 0)
            return pid
        except (ValueError, ProcessLookupError, PermissionError):
            return None
    # Fall back to checking /api/status
    if is_port_in_use(port):
        try:
            import httpx
            r = httpx.get(f"http://127.0.0.1:{port}/api/status", timeout=2.0)
            data = r.json()
            return data.get("pid")
        except Exception:
            return None
    return None


def _build_cachelens_block(port: int, backups: list[str] | None = None) -> str:
    """Build the shell block of export statements for the given port."""
    lines = [_CACHELENS_START, ""]
    if backups:
        for b in backups:
            lines.append(b)
        lines.append("")
    for var, url_template in ENV_VARS.items():
        lines.append(f'export {var}="{url_template.format(port=port)}"')
    lines.append("")
    lines.append(_CACHELENS_END)
    return "\n".join(lines)


def write_env_to_shell_file(shell_file: Path, port: int) -> None:
    """Write or update env vars in a shell file, backing up existing values."""
    existing_content = shell_file.read_text() if shell_file.exists() else ""

    # Check if cachelens block already exists
    if _CACHELENS_START in existing_content and _CACHELENS_END in existing_content:
        # Replace existing block (idempotent update), preserving backup lines
        pattern = re.compile(
            re.escape(_CACHELENS_START) + r"(.*?)" + re.escape(_CACHELENS_END) + r"\n?",
            re.DOTALL,
        )
        match = pattern.search(existing_content)
        existing_backups: list[str] = []
        if match:
            for l in match.group(1).splitlines():
                if "# cachelens-backup:" in l:
                    existing_backups.append(l.strip())
        new_block = _build_cachelens_block(port=port, backups=existing_backups or None)
        new_content = pattern.sub(new_block, existing_content)
        shell_file.write_text(new_content)
        return

    # Scan for existing values of our env vars and back them up
    backup_lines: list[str] = []
    cleaned_lines: list[str] = []
    for line in existing_content.splitlines(keepends=True):
        matched_var = None
        for var in ENV_VARS:
            if re.match(rf"^\s*export\s+{re.escape(var)}\s*=", line):
                matched_var = var
                break
        if matched_var is not None:
            value_stripped = line.strip()
            backup_lines.append(f"# cachelens-backup: {value_stripped}")
        else:
            cleaned_lines.append(line)

    base = "".join(cleaned_lines)
    # Ensure there's a trailing newline before the block
    if base and not base.endswith("\n"):
        base += "\n"

    block = _build_cachelens_block(port=port, backups=backup_lines or None)

    shell_file.write_text(base + block)


def remove_env_from_shell_file(shell_file: Path) -> None:
    """Remove cachelens env vars from a shell file, restoring backed-up values."""
    if not shell_file.exists():
        return

    content = shell_file.read_text()

    if _CACHELENS_START not in content:
        # Nothing to remove
        return

    # Extract the cachelens block
    pattern = re.compile(
        re.escape(_CACHELENS_START) + r"(.*?)" + re.escape(_CACHELENS_END) + r"\n?",
        re.DOTALL,
    )
    match = pattern.search(content)
    restore_lines: list[str] = []
    if match:
        block_inner = match.group(1)
        for line in block_inner.splitlines():
            stripped = line.strip()
            if stripped.startswith("# cachelens-backup:"):
                original = stripped.removeprefix("# cachelens-backup:").strip()
                restore_lines.append(original)

    # Remove the block from content
    new_content = pattern.sub("", content)

    # Append restored values if any
    if restore_lines:
        if new_content and not new_content.endswith("\n"):
            new_content += "\n"
        new_content += "\n".join(restore_lines) + "\n"

    shell_file.write_text(new_content)


def _write_macos_plist(port: int, base_path: str = "") -> Path:
    """Write LaunchAgent plist and return its path."""
    launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
    launch_agents_dir.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents_dir / "com.cachelens.plist"
    log_dir = CACHELENS_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    plist_content = LAUNCHD_PLIST_TEMPLATE.format(
        python_path=sys.executable,
        port=port,
        base_path=base_path,
        log_dir=str(log_dir),
        path=os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    )
    plist_path.write_text(plist_content)
    return plist_path


def _write_linux_service(port: int, base_path: str = "") -> Path:
    """Write systemd user service and return its path."""
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_path = systemd_dir / "cachelens.service"
    service_content = SYSTEMD_SERVICE_TEMPLATE.format(
        python_path=sys.executable,
        port=port,
        base_path=base_path,
    )
    service_path.write_text(service_content)
    return service_path


def install(port: int = 8420, base_path: str = "") -> None:
    """Run the full install sequence. Print each step."""
    platform = detect_platform()

    # 1. Create ~/.cachelens/ and default config
    CACHELENS_DIR.mkdir(parents=True, exist_ok=True)
    config_path = CACHELENS_DIR / "config.toml"
    if not config_path.exists():
        config_path.write_text(DEFAULT_CONFIG_TOML)
        print(f"  Created {config_path}")
    else:
        print(f"  Config already exists: {config_path}")

    # 2. Write service file
    if platform == "macos":
        service_path = _write_macos_plist(port, base_path=base_path)
        print(f"  Written LaunchAgent plist: {service_path}")
    else:
        service_path = _write_linux_service(port, base_path=base_path)
        print(f"  Written systemd service: {service_path}")

    # 3. Set env vars in shell config files
    written_shell_files: list[Path] = []
    for shell_name, shell_file in SHELL_FILES.items():
        write_env_to_shell_file(shell_file, port=port)
        written_shell_files.append(shell_file)
        print(f"  Updated {shell_file}")

    # Set env vars via launchctl on macOS
    if platform == "macos":
        for var, url_template in ENV_VARS.items():
            url = url_template.format(port=port)
            try:
                subprocess.run(["launchctl", "setenv", var, url], check=True, capture_output=True)
                print(f"  Set env via launchctl: {var}={url}")
            except (subprocess.CalledProcessError, FileNotFoundError):
                print(f"  Warning: could not set {var} via launchctl")

    # 4. Start daemon
    if platform == "macos":
        try:
            subprocess.run(["launchctl", "load", str(service_path)], check=True, capture_output=True)
            print("  Started daemon via launchctl load")
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"  Warning: could not start daemon via launchctl: {exc}")
    else:
        try:
            subprocess.run(["systemctl", "--user", "enable", "--now", "cachelens.service"], check=True, capture_output=True)
            print("  Started daemon via systemctl")
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"  Warning: could not start daemon via systemctl: {exc}")

    # 5. Print summary
    print("\nCacheLens installed successfully.")
    print(f"  Port: {port}")
    print(f"  Config: {config_path}")
    print(f"  Service: {service_path}")
    print("  Shell files updated:")
    for f in written_shell_files:
        print(f"    {f}")
    print("\nEnv vars set:")
    for var, url_template in ENV_VARS.items():
        print(f"  {var}={url_template.format(port=port)}")
    print("\nRestart your shell or run: source ~/.zshrc")


def uninstall(purge: bool = False) -> None:
    """Remove installation. Optionally purge data."""
    platform = detect_platform()

    # Stop and remove service
    if platform == "macos":
        plist_path = Path.home() / "Library" / "LaunchAgents" / "com.cachelens.plist"
        if plist_path.exists():
            try:
                subprocess.run(["launchctl", "unload", str(plist_path)], check=False, capture_output=True)
                print(f"  Unloaded launchd service")
            except FileNotFoundError:
                pass
            plist_path.unlink()
            print(f"  Removed {plist_path}")
        else:
            print("  No LaunchAgent plist found (already removed)")
    else:
        service_path = Path.home() / ".config" / "systemd" / "user" / "cachelens.service"
        if service_path.exists():
            try:
                subprocess.run(["systemctl", "--user", "disable", "--now", "cachelens.service"], check=False, capture_output=True)
                print("  Stopped and disabled systemd service")
            except FileNotFoundError:
                pass
            service_path.unlink()
            print(f"  Removed {service_path}")
        else:
            print("  No systemd service found (already removed)")

    # Restore env vars in shell files
    for shell_name, shell_file in SHELL_FILES.items():
        if shell_file.exists():
            remove_env_from_shell_file(shell_file)
            print(f"  Restored env vars in {shell_file}")

    # Unset via launchctl on macOS
    if platform == "macos":
        for var in ENV_VARS:
            try:
                subprocess.run(["launchctl", "unsetenv", var], check=False, capture_output=True)
            except FileNotFoundError:
                pass

    # Purge data directory if requested
    if purge and CACHELENS_DIR.exists():
        import shutil
        shutil.rmtree(CACHELENS_DIR)
        print(f"  Purged data directory: {CACHELENS_DIR}")
    else:
        print(f"  Data directory preserved: {CACHELENS_DIR}")

    print("\nCacheLens uninstalled.")
    if not purge:
        print("  Run with --purge to also delete usage data.")
