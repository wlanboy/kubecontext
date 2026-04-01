"""SSH helpers: parse ~/.ssh/config and download remote kubeconfigs."""

from pathlib import Path

import paramiko
import yaml
from rich.console import Console

console = Console()

SSH_CONFIG_PATH = Path.home() / ".ssh" / "config"


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
