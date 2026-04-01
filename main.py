#!/usr/bin/env python3
"""kubecontext — Kubeconfig manager with SSH import, merge, and context switching."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import questionary
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

import tools_context
from tools_context import get_list, load_kubeconfig, merge_configs, rename_config_for_host, save_kubeconfig
from tools_ssh import (
    close_tunnel,
    download_remote_kubeconfig,
    get_tunnels,
    load_tunnels,
    open_tunnel,
    parse_ssh_config,
)

console = Console()


# ── SSH Import ────────────────────────────────────────────────────────────────

def ssh_import_menu() -> None:
    hosts = parse_ssh_config()
    if not hosts:
        console.print("[yellow]No hosts found in ~/.ssh/config[/yellow]")
        return

    hostname = questionary.select("Select SSH host:", choices=hosts).ask()
    if not hostname:
        return

    with console.status(f"Connecting to {hostname}…"):
        remote = download_remote_kubeconfig(hostname)
    if not remote:
        return

    renamed   = rename_config_for_host(remote, hostname)
    new_names = [c["name"] for c in get_list(renamed, "contexts")]

    console.print("\n[bold]Contexts to import:[/bold]")
    for n in new_names:
        console.print(f"  [cyan]+[/cyan] {n}")

    base = load_kubeconfig()
    existing = {c["name"] for c in get_list(base, "contexts")}
    overwrites = [n for n in new_names if n in existing]
    if overwrites:
        console.print(f"\n[yellow]Will overwrite existing:[/yellow] {', '.join(overwrites)}")

    merged = merge_configs(base, renamed)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, prefix="kubeconfig_preview_"
    ) as tmp:
        yaml.dump(merged, tmp, default_flow_style=False, allow_unicode=True)
        tmp_path = Path(tmp.name)

    console.print("\n[bold]Preview — merged config:[/bold]")
    console.print(Syntax(tmp_path.read_text(), "yaml", theme="monokai", line_numbers=True))

    if questionary.confirm(f"Write to {tools_context.KUBECONFIG_PATH}?", default=False).ask():
        tools_context.backup_kubeconfig()
        shutil.copy2(tmp_path, tools_context.KUBECONFIG_PATH)
        tools_context.KUBECONFIG_PATH.chmod(0o600)
        console.print(f"[green]✓ Config updated. Contexts: {', '.join(new_names)}[/green]")
    else:
        console.print("[dim]Aborted — no changes.[/dim]")

    tmp_path.unlink(missing_ok=True)


# ── Set Current Context ───────────────────────────────────────────────────────

def set_current_context_menu() -> None:
    config   = load_kubeconfig()
    contexts = [c["name"] for c in get_list(config, "contexts")]
    if not contexts:
        console.print("[yellow]No contexts found.[/yellow]")
        return

    current = config.get("current-context", "")
    choices = [
        questionary.Choice(title=f"{'→ ' if n == current else '  '}{n}", value=n)
        for n in contexts
    ]

    selected = questionary.select("Activate context:", choices=choices).ask()
    if not selected:
        return
    if selected == current:
        console.print("[dim]Already active.[/dim]")
        return

    config["current-context"] = selected
    save_kubeconfig(config)
    console.print(f"[green]✓ Active context: {selected}[/green]")


# ── Delete Context ────────────────────────────────────────────────────────────

def delete_context_menu() -> None:
    config   = load_kubeconfig()
    contexts = get_list(config, "contexts")
    if not contexts:
        console.print("[yellow]No contexts found.[/yellow]")
        return

    current = config.get("current-context", "")
    choices = [
        questionary.Choice(
            title=f"{'[current] ' if c['name'] == current else ''}{c['name']}",
            value=c["name"],
        )
        for c in contexts
    ]

    selected = questionary.select("Delete context:", choices=choices).ask()
    if not selected:
        return

    ctx_obj     = next(c for c in contexts if c["name"] == selected)
    cluster_ref = (ctx_obj.get("context") or {}).get("cluster", "")
    user_ref    = (ctx_obj.get("context") or {}).get("user", "")

    other         = [c for c in contexts if c["name"] != selected]
    used_clusters = {(c.get("context") or {}).get("cluster") for c in other}
    used_users    = {(c.get("context") or {}).get("user")    for c in other}

    orphan_cluster = cluster_ref and cluster_ref not in used_clusters
    orphan_user    = user_ref    and user_ref    not in used_users

    console.print("\n[bold]Will remove:[/bold]")
    console.print(f"  [red]−[/red] context: {selected}")
    if orphan_cluster:
        console.print(f"  [red]−[/red] cluster: {cluster_ref}")
    if orphan_user:
        console.print(f"  [red]−[/red] user:    {user_ref}")

    if not questionary.confirm("Confirm delete?", default=False).ask():
        console.print("[dim]Aborted.[/dim]")
        return

    tools_context.backup_kubeconfig()

    config["contexts"] = [c for c in contexts if c["name"] != selected]
    if orphan_cluster:
        config["clusters"] = [c for c in get_list(config, "clusters") if c["name"] != cluster_ref]
    if orphan_user:
        config["users"] = [u for u in get_list(config, "users") if u["name"] != user_ref]

    if config.get("current-context") == selected:
        remaining = [c["name"] for c in config["contexts"]]
        config["current-context"] = remaining[0] if remaining else ""

    save_kubeconfig(config)
    console.print(f"[green]✓ Deleted '{selected}'[/green]")


# ── SSH Tunnels ───────────────────────────────────────────────────────────────

def _ssh_contexts() -> list[dict]:
    """Return contexts whose name contains '@' (imported via SSH)."""
    config = load_kubeconfig()
    contexts = get_list(config, "contexts")
    cluster_servers = {
        c["name"]: (c.get("cluster") or {}).get("server", "")
        for c in get_list(config, "clusters")
    }
    result = []
    for ctx in contexts:
        name = ctx["name"]
        if "@" not in name:
            continue
        ssh_host, _ = name.split("@", 1)
        cluster_ref  = (ctx.get("context") or {}).get("cluster", "")
        server       = cluster_servers.get(cluster_ref, "")
        parsed       = urlparse(server)
        remote_host  = parsed.hostname or "localhost"
        port         = parsed.port
        result.append({
            "context":     name,
            "ssh_host":    ssh_host,
            "remote_host": remote_host,
            "port":        port,
            "server":      server,
        })
    return result


def ssh_tunnel_menu() -> None:
    while True:
        ssh_ctxs = _ssh_contexts()
        if not ssh_ctxs:
            console.print("[yellow]No SSH-imported contexts found (name must contain '@').[/yellow]")
            return

        active_tunnels = get_tunnels()
        tunneled_ports = {t.local_port for t in active_tunnels}

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2, 0, 0))
        table.add_column("",        width=2)
        table.add_column("Context", style="cyan")
        table.add_column("SSH host", style="dim")
        table.add_column("Server",   style="dim")
        table.add_column("Tunnel")

        for c in ssh_ctxs:
            if c["port"] and c["port"] in tunneled_ports:
                status = "[green]● open[/green]"
            elif c["port"]:
                status = "[dim]○ closed[/dim]"
            else:
                status = "[yellow]? no port[/yellow]"
            table.add_row("", c["context"], c["ssh_host"], c["server"], status)

        console.print(table)
        console.print()

        openable   = [c for c in ssh_ctxs if c["port"] and c["port"] not in tunneled_ports]
        closeable  = [t for t in active_tunnels]

        choices = []
        if openable:
            choices.append(questionary.Choice("  Open tunnel", value="open"))
        if closeable:
            choices.append(questionary.Choice("  Close tunnel", value="close"))
        choices += [questionary.Separator(), questionary.Choice("  Back", value="back")]

        action = questionary.select("Tunnels:", choices=choices).ask()
        if action is None or action == "back":
            break

        if action == "open":
            opts = [questionary.Choice(c["context"], value=c) for c in openable]
            opts += [questionary.Separator(), questionary.Choice("  Back", value=None)]
            selected = questionary.select("Open tunnel for:", choices=opts).ask()
            if not selected:
                continue
            tunnel = open_tunnel(
                selected["ssh_host"],
                selected["port"],
                selected["remote_host"],
                selected["port"],
            )
            if tunnel:
                console.print(f"[green]✓ Tunnel open: {tunnel.label}[/green]")

        elif action == "close":
            opts = [questionary.Choice(t.label, value=t) for t in closeable]
            selected = questionary.select("Close tunnel:", choices=opts).ask()
            if selected:
                close_tunnel(selected)
                console.print("[green]✓ Tunnel closed[/green]")


# ── Validate Contexts ─────────────────────────────────────────────────────────

def validate_contexts_menu() -> None:
    if not shutil.which("kubectl"):
        console.print("[red]kubectl not found in PATH[/red]")
        return

    config   = load_kubeconfig()
    contexts = get_list(config, "contexts")
    if not contexts:
        console.print("[yellow]No contexts found.[/yellow]")
        return

    cluster_servers = {
        c["name"]: (c.get("cluster") or {}).get("server", "?")
        for c in get_list(config, "clusters")
    }

    table = Table(title="Context Validation", show_header=True, header_style="bold")
    table.add_column("Context", style="cyan", no_wrap=True)
    table.add_column("Server", style="dim")
    table.add_column("Status")

    for ctx in contexts:
        name        = ctx["name"]
        cluster_ref = (ctx.get("context") or {}).get("cluster", "")
        server      = cluster_servers.get(cluster_ref, "?")

        with console.status(f"Checking [cyan]{name}[/cyan]…"):
            try:
                result = subprocess.run(
                    ["kubectl", "cluster-info", "--context", name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    status = "[green]✓ OK[/green]"
                else:
                    lines = (result.stderr or result.stdout).strip().splitlines()
                    msg   = lines[0][:70] if lines else "failed"
                    status = f"[red]✗ {msg}[/red]"
            except subprocess.TimeoutExpired:
                status = "[red]✗ timeout[/red]"

        table.add_row(name, server, status)

    console.print(table)


# ── Overview Table ────────────────────────────────────────────────────────────

def show_contexts_table() -> None:
    config   = load_kubeconfig()
    contexts = get_list(config, "contexts")
    current  = config.get("current-context", "")
    cluster_servers = {
        c["name"]: (c.get("cluster") or {}).get("server", "")
        for c in get_list(config, "clusters")
    }

    if not contexts:
        console.print("[dim]No contexts configured.[/dim]")
        return

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2, 0, 0))
    table.add_column("", width=1)
    table.add_column("Context", style="cyan")
    table.add_column("Server", style="dim")
    table.add_column("User", style="dim")

    for ctx in contexts:
        name        = ctx["name"]
        cluster_ref = (ctx.get("context") or {}).get("cluster", "")
        user_ref    = (ctx.get("context") or {}).get("user", "")
        server      = cluster_servers.get(cluster_ref, "")
        marker      = "[green]→[/green]" if name == current else ""
        table.add_row(marker, name, server, user_ref)

    console.print(table)


# ── Main ──────────────────────────────────────────────────────────────────────

def _exit_tunnel_check() -> None:
    tunnels = get_tunnels()
    if not tunnels:
        return
    console.print(f"\n[yellow]{len(tunnels)} SSH tunnel(s) still running:[/yellow]")
    for t in tunnels:
        console.print(f"  [dim]·[/dim] {t.label}  [dim](PID {t.pid})[/dim]")
    console.print()
    keep = questionary.confirm("Keep tunnels running after exit?", default=True).ask()
    if not keep:
        for t in list(tunnels):
            close_tunnel(t)
        console.print("[dim]Tunnels closed.[/dim]")


def main() -> None:
    console.print(Panel("[bold cyan]kubecontext[/bold cyan]  Kubeconfig Manager", expand=False))
    load_tunnels()

    menu = [
        questionary.Choice("  SSH Import   download & merge remote kubeconfig", value="import"),
        questionary.Choice("  Tunnels      manage SSH port forwarding",          value="tunnels"),
        questionary.Choice("  Set context  switch active context",               value="set"),
        questionary.Choice("  Delete       remove a context",                    value="delete"),
        questionary.Choice("  Validate     check cluster connectivity",          value="validate"),
        questionary.Separator(),
        questionary.Choice("  Exit",                                             value="exit"),
    ]

    while True:
        console.print()
        show_contexts_table()
        console.print()

        action = questionary.select("Action:", choices=menu).ask()
        if action is None or action == "exit":
            _exit_tunnel_check()
            break

        console.print()
        match action:
            case "import":
                ssh_import_menu()
            case "tunnels":
                ssh_tunnel_menu()
            case "set":
                set_current_context_menu()
            case "delete":
                delete_context_menu()
            case "validate":
                validate_contexts_menu()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print()
        _exit_tunnel_check()
        console.print("[dim]Bye.[/dim]")
        sys.exit(0)
