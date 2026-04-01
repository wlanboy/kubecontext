"""SSH helpers: parse ~/.ssh/config, download remote kubeconfigs, manage tunnels."""

import json
import os
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import paramiko
import yaml
from rich.console import Console

console = Console()

SSH_CONFIG_PATH  = Path.home() / ".ssh" / "config"
TUNNEL_STATE_PATH = Path.home() / ".kube" / "kubecontext_tunnels.json"


# ── SSH Tunnel Management ─────────────────────────────────────────────────────

@dataclass
class SshTunnel:
    host: str          # SSH host alias from ~/.ssh/config
    local_port: int
    remote_host: str   # target host as seen from the SSH host
    remote_port: int
    pid: int
    _process: subprocess.Popen | None = field(repr=False, default=None)

    @property
    def label(self) -> str:
        return f"localhost:{self.local_port} → {self.host}:{self.remote_host}:{self.remote_port}"

    @property
    def alive(self) -> bool:
        if self._process is not None:
            return self._process.poll() is None
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False


_active_tunnels: list[SshTunnel] = []


def _save_state() -> None:
    data = [
        {
            "host":        t.host,
            "local_port":  t.local_port,
            "remote_host": t.remote_host,
            "remote_port": t.remote_port,
            "pid":         t.pid,
        }
        for t in _active_tunnels
    ]
    TUNNEL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TUNNEL_STATE_PATH.write_text(json.dumps(data, indent=2))


def load_tunnels() -> None:
    """Read persisted tunnel state and restore still-running tunnels."""
    if not TUNNEL_STATE_PATH.exists():
        return
    try:
        data = json.loads(TUNNEL_STATE_PATH.read_text())
    except Exception:
        return
    for entry in data:
        t = SshTunnel(
            host=entry["host"],
            local_port=entry["local_port"],
            remote_host=entry["remote_host"],
            remote_port=entry["remote_port"],
            pid=entry["pid"],
        )
        if t.alive:
            _active_tunnels.append(t)
    _save_state()  # prune dead entries from file


def open_tunnel(host: str, local_port: int, remote_host: str, remote_port: int) -> SshTunnel | None:
    """Start an SSH local-port-forward tunnel in the background."""
    cmd = [
        "ssh", "-N", "-o", "ExitOnForwardFailure=yes",
        "-L", f"{local_port}:{remote_host}:{remote_port}",
        host,
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        console.print("[red]✗ ssh binary not found in PATH[/red]")
        return None

    tunnel = SshTunnel(host, local_port, remote_host, remote_port, pid=proc.pid, _process=proc)
    _active_tunnels.append(tunnel)
    _save_state()
    return tunnel


def close_tunnel(tunnel: SshTunnel) -> None:
    """Terminate a running SSH tunnel."""
    if tunnel.alive:
        if tunnel._process is not None:
            tunnel._process.terminate()
            try:
                tunnel._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel._process.kill()
        else:
            try:
                os.kill(tunnel.pid, signal.SIGTERM)
            except OSError:
                pass
    _active_tunnels.remove(tunnel)
    _save_state()


def get_tunnels() -> list[SshTunnel]:
    """Return list of currently tracked tunnels (filters dead ones first)."""
    dead = [t for t in _active_tunnels if not t.alive]
    for t in dead:
        _active_tunnels.remove(t)
    if dead:
        _save_state()
    return list(_active_tunnels)


def parse_ssh_config() -> list[str]:
    """Return all non-wildcard Host entries from ~/.ssh/config."""
    if not SSH_CONFIG_PATH.exists():
        return []
    hosts = []
    for line in SSH_CONFIG_PATH.read_text().splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("host "):
            name = stripped[5:].strip()
            if "*" not in name and "?" not in name:
                hosts.append(name)
    return hosts


def _paramiko_host_config(hostname: str) -> dict:
    cfg = paramiko.SSHConfig()
    if SSH_CONFIG_PATH.exists():
        with SSH_CONFIG_PATH.open() as f:
            cfg.parse(f)
    return cfg.lookup(hostname)


def download_remote_kubeconfig(hostname: str) -> dict | None:
    """SSH into hostname and return parsed ~/.kube/config, or None on error."""
    host_cfg = _paramiko_host_config(hostname)

    connect_kwargs: dict = {
        "hostname": host_cfg.get("hostname", hostname),
        "timeout": 10,
    }
    if "user" in host_cfg:
        connect_kwargs["username"] = host_cfg["user"]
    if "identityfile" in host_cfg:
        connect_kwargs["key_filename"] = host_cfg["identityfile"]

    try:
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(**connect_kwargs)

        with client.open_sftp() as sftp:
            with sftp.open(".kube/config") as f:
                content = f.read()
        client.close()

        return yaml.safe_load(content)

    except FileNotFoundError:
        console.print(f"[red]✗ No ~/.kube/config on {hostname}[/red]")
    except paramiko.AuthenticationException:
        console.print(f"[red]✗ SSH auth failed for {hostname}[/red]")
    except Exception as exc:
        console.print(f"[red]✗ {hostname}: {exc}[/red]")
    return None
