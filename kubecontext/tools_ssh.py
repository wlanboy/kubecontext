"""SSH helpers: parse ~/.ssh/config, download remote kubeconfigs, manage tunnels."""

import json
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import paramiko
import yaml

from ._console import console
from .tools_context import cluster_server_map, context_refs, get_list, load_kubeconfig

SSH_CONFIG_PATH  = Path.home() / ".ssh" / "config"
TUNNEL_STATE_PATH = Path.home() / ".kube" / "kubecontext_tunnels.json"
REMOTE_PORT_MAP_PATH = Path.home() / ".kube" / "kubecontext_remote_ports.json"
REMOTE_HOST_MAP_PATH = Path.home() / ".kube" / "kubecontext_remote_hosts.json"

# How long to wait for ssh to fail fast (auth error, port in use, unreachable
# host) before we trust the tunnel is actually up.
_TUNNEL_STARTUP_CHECK_SECONDS = 0.6
_TUNNEL_STARTUP_CHECK_INTERVAL = 0.1


# ── SSH-Imported Contexts ─────────────────────────────────────────────────────

@dataclass
class SshContext:
    """A kubeconfig context imported via SSH (name contains '@'), with its
    tunnel-relevant details resolved."""
    context: str
    ssh_host: str
    cluster: str
    remote_host: str
    port: int | None
    remote_port: int | None
    server: str


def ssh_contexts() -> list[SshContext]:
    """Return kubeconfig contexts whose name contains '@' (imported via SSH)."""
    config = load_kubeconfig()
    contexts = get_list(config, "contexts")
    cluster_servers = cluster_server_map(config)
    result = []
    for ctx in contexts:
        name = ctx["name"]
        if "@" not in name:
            continue
        ssh_host, _ = name.split("@", 1)
        cluster_ref = context_refs(ctx).cluster
        server      = cluster_servers.get(cluster_ref, "")
        parsed      = urlparse(server)
        seed_host   = parsed.hostname or "localhost"
        remote_host = remote_host_for(name, seed_host)
        port        = parsed.port
        result.append(SshContext(
            context=name,
            ssh_host=ssh_host,
            cluster=cluster_ref,
            remote_host=remote_host,
            port=port,
            remote_port=remote_port_for(name, port) if port else None,
            server=server,
        ))
    return result


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
    TUNNEL_STATE_PATH.chmod(0o600)


def load_tunnels() -> None:
    """Read persisted tunnel state and restore still-running tunnels."""
    if not TUNNEL_STATE_PATH.exists():
        return
    try:
        data = json.loads(TUNNEL_STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
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
        "ssh", "-N",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "BatchMode=yes",  # never block on an interactive password prompt
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

    # ssh -N with ExitOnForwardFailure exits quickly on auth errors, a busy
    # local port, or an unreachable host — give it a moment to fail before
    # reporting success.
    elapsed = 0.0
    while elapsed < _TUNNEL_STARTUP_CHECK_SECONDS and proc.poll() is None:
        time.sleep(_TUNNEL_STARTUP_CHECK_INTERVAL)
        elapsed += _TUNNEL_STARTUP_CHECK_INTERVAL

    if proc.poll() is not None:
        stderr = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
        msg = stderr.splitlines()[-1] if stderr else f"ssh exited with code {proc.returncode}"
        console.print(f"[red]✗ Tunnel failed: {msg}[/red]")
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


def find_free_local_port(preferred: int) -> int:
    """Return `preferred` if it's free on localhost, else an OS-assigned free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _remember_first_seen(path: Path, key: str, current_value):
    """Return the value first recorded for `key` in the JSON map at `path`,
    recording `current_value` if none is yet stored."""
    try:
        m = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        m = {}
    if key not in m:
        m[key] = current_value
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(m, indent=2))
    return m[key]


def remote_port_for(context_name: str, current_port: int) -> int:
    """Return the true remote-side port to forward to for a context's tunnel.

    A context's local port can be reassigned (see find_free_local_port) when it
    collides with another tunnel, and the kubeconfig's cluster.server is then
    rewritten to that new local port. That would make the remote port
    unrecoverable on the next read, so the first port ever seen for a context
    is remembered here and used as the ssh -L remote-side port from then on.
    """
    return _remember_first_seen(REMOTE_PORT_MAP_PATH, context_name, current_port)


def remote_host_for(context_name: str, current_host: str) -> str:
    """Return the true remote-side host to forward to for a context's tunnel.

    Once a tunnel is opened, the kubeconfig's cluster.server is rewritten to
    127.0.0.1 so kubectl actually goes through the tunnel instead of dialing
    the original host directly (see set_cluster_server_endpoint). That would
    make the real remote host unrecoverable on the next read, so the first
    host ever seen for a context is remembered here and used as the ssh -L
    remote-side host from then on.
    """
    return _remember_first_seen(REMOTE_HOST_MAP_PATH, context_name, current_host)


def parse_ssh_config() -> list[str]:
    """Return all non-wildcard Host entries from ~/.ssh/config."""
    if not SSH_CONFIG_PATH.exists():
        return []
    hosts = []
    for line in SSH_CONFIG_PATH.read_text().splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("host "):
            for name in stripped[5:].split():
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
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

        with client:
            client.connect(**connect_kwargs)
            with client.open_sftp() as sftp, sftp.open(".kube/config") as f:
                content = f.read()

        return yaml.safe_load(content)

    except FileNotFoundError:
        console.print(f"[red]✗ No ~/.kube/config on {hostname}[/red]")
    except paramiko.AuthenticationException:
        console.print(f"[red]✗ SSH auth failed for {hostname}[/red]")
    except paramiko.SSHException as exc:
        console.print(f"[red]✗ {hostname}: host key not trusted ({exc})[/red]")
    except (OSError, yaml.YAMLError) as exc:
        console.print(f"[red]✗ {hostname}: {exc}[/red]")
    return None
