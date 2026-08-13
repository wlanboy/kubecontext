"""kubecontext — Kubeconfig manager with SSH import, merge, and context switching."""

import sys
from urllib.parse import urlparse

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .context import (
    delete_context_menu,
    export_contexts_menu,
    import_and_merge,
    import_file_menu,
    set_current_context_menu,
    show_contexts_table,
    validate_contexts_menu,
)
from .tools_context import (
    cluster_server_map,
    get_list,
    load_kubeconfig,
    rename_config_for_host,
    save_kubeconfig,
    set_cluster_server_endpoint,
)
from .tools_ssh import (
    close_tunnel,
    download_remote_kubeconfig,
    find_free_local_port,
    get_tunnels,
    load_tunnels,
    open_tunnel,
    parse_ssh_config,
    remote_host_for,
    remote_port_for,
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

    renamed = rename_config_for_host(remote, hostname)
    import_and_merge(renamed)


# ── SSH Tunnels ───────────────────────────────────────────────────────────────

def _ssh_contexts() -> list[dict]:
    """Return contexts whose name contains '@' (imported via SSH)."""
    config = load_kubeconfig()
    contexts = get_list(config, "contexts")
    cluster_servers = cluster_server_map(config)
    result = []
    for ctx in contexts:
        name = ctx["name"]
        if "@" not in name:
            continue
        ssh_host, _ = name.split("@", 1)
        cluster_ref  = (ctx.get("context") or {}).get("cluster", "")
        server       = cluster_servers.get(cluster_ref, "")
        parsed       = urlparse(server)
        seed_host    = parsed.hostname or "localhost"
        remote_host  = remote_host_for(name, seed_host)
        port         = parsed.port
        result.append({
            "context":     name,
            "ssh_host":    ssh_host,
            "cluster":     cluster_ref,
            "remote_host": remote_host,
            "port":        port,
            "remote_port": remote_port_for(name, port) if port else None,
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
        tunnels_by_port = {t.local_port: t for t in active_tunnels}

        def _matching_tunnel(c: dict, tunnels_by_port=tunnels_by_port):
            """Tunnel that actually forwards for this context (same SSH host + target)."""
            t = tunnels_by_port.get(c["port"])
            if t and t.host == c["ssh_host"] and t.remote_host == c["remote_host"]:
                return t
            return None

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2, 0, 0))
        table.add_column("",        width=2)
        table.add_column("Context", style="cyan")
        table.add_column("SSH host", style="dim")
        table.add_column("Server",   style="dim")
        table.add_column("Tunnel")

        for c in ssh_ctxs:
            if not c["port"]:
                status = "[yellow]? no port[/yellow]"
            elif _matching_tunnel(c):
                status = "[green]● open[/green]"
            elif c["port"] in tunnels_by_port:
                status = "[yellow]○ port in use[/yellow]"
            else:
                status = "[dim]○ closed[/dim]"
            table.add_row("", c["context"], c["ssh_host"], c["server"], status)

        console.print(table)
        console.print()

        openable   = [c for c in ssh_ctxs if c["port"] and not _matching_tunnel(c)]
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
            local_port = find_free_local_port(selected["port"])
            already_tunneled = urlparse(selected["server"]).hostname == "127.0.0.1"
            if not already_tunneled or local_port != selected["port"]:
                config = load_kubeconfig()
                set_cluster_server_endpoint(config, selected["cluster"], "127.0.0.1", local_port)
                save_kubeconfig(config)
                console.print(
                    f"[dim]{selected['context']} now points to 127.0.0.1:{local_port} "
                    f"(tunneled via {selected['ssh_host']}) — kubeconfig updated[/dim]"
                )
            tunnel = open_tunnel(
                selected["ssh_host"],
                local_port,
                selected["remote_host"],
                selected["remote_port"],
            )
            if tunnel:
                console.print(f"[green]✓ Tunnel open: {tunnel.label}[/green]")

        elif action == "close":
            opts = [questionary.Choice(t.label, value=t) for t in closeable]
            opts += [questionary.Separator(), questionary.Choice("  Back", value=None)]
            selected = questionary.select("Close tunnel:", choices=opts).ask()
            if selected:
                close_tunnel(selected)
                console.print("[green]✓ Tunnel closed[/green]")


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
        questionary.Choice("  File Import  load & merge local kubeconfig file", value="file_import"),
        questionary.Choice("  Tunnels      manage SSH port forwarding",          value="tunnels"),
        questionary.Choice("  Set context  switch active context",               value="set"),
        questionary.Choice("  Export       write context(s) to a file",          value="export"),
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
            case "file_import":
                import_file_menu()
            case "tunnels":
                ssh_tunnel_menu()
            case "set":
                set_current_context_menu()
            case "export":
                export_contexts_menu()
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
