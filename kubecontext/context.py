"""Context management UI — show, switch, delete, export, validate kubeconfig contexts."""

import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import questionary
from rich.syntax import Syntax
from rich.table import Table

from . import tools_context
from ._console import console
from .tools_context import (
    cluster_server_map,
    context_refs,
    dump_yaml,
    filter_contexts,
    get_list,
    load_kubeconfig,
    merge_configs,
    save_kubeconfig,
)

# ── Overview Table ────────────────────────────────────────────────────────────

def show_contexts_table() -> None:
    config   = load_kubeconfig()
    contexts = get_list(config, "contexts")
    current  = config.get("current-context", "")
    cluster_servers = cluster_server_map(config)

    if not contexts:
        console.print("[dim]No contexts configured.[/dim]")
        return

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2, 0, 0))
    table.add_column("", width=1)
    table.add_column("Context", style="cyan")
    table.add_column("Server", style="dim")
    table.add_column("User", style="dim")

    for ctx in contexts:
        name    = ctx["name"]
        refs    = context_refs(ctx)
        server  = cluster_servers.get(refs.cluster, "")
        marker  = "[green]→[/green]" if name == current else ""
        table.add_row(marker, name, server, refs.user)

    console.print(table)


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

    ctx_obj = next(c for c in contexts if c["name"] == selected)
    refs    = context_refs(ctx_obj)

    other         = [c for c in contexts if c["name"] != selected]
    used_clusters = {context_refs(c).cluster for c in other}
    used_users    = {context_refs(c).user    for c in other}

    orphan_cluster = refs.cluster and refs.cluster not in used_clusters
    orphan_user    = refs.user    and refs.user    not in used_users

    console.print("\n[bold]Will remove:[/bold]")
    console.print(f"  [red]−[/red] context: {selected}")
    if orphan_cluster:
        console.print(f"  [red]−[/red] cluster: {refs.cluster}")
    if orphan_user:
        console.print(f"  [red]−[/red] user:    {refs.user}")

    if not questionary.confirm("Confirm delete?", default=False).ask():
        console.print("[dim]Aborted.[/dim]")
        return

    tools_context.backup_kubeconfig()

    config["contexts"] = [c for c in contexts if c["name"] != selected]
    if orphan_cluster:
        config["clusters"] = [c for c in get_list(config, "clusters") if c["name"] != refs.cluster]
    if orphan_user:
        config["users"] = [u for u in get_list(config, "users") if u["name"] != refs.user]

    if config.get("current-context") == selected:
        remaining = [c["name"] for c in config["contexts"]]
        config["current-context"] = remaining[0] if remaining else ""

    save_kubeconfig(config)
    console.print(f"[green]✓ Deleted '{selected}'[/green]")


# ── Export Contexts ───────────────────────────────────────────────────────────

def select_contexts(prompt: str, names: list[str]) -> list[str] | None:
    """Prompt to pick from `names` via checkbox; returns None if cancelled/empty."""
    selected = questionary.checkbox(
        prompt,
        choices=names,
        validate=lambda v: True if v else "Select at least one context",
    ).ask()
    return selected or None


def export_contexts_menu() -> None:
    config   = load_kubeconfig()
    contexts = get_list(config, "contexts")
    if not contexts:
        console.print("[yellow]No contexts found.[/yellow]")
        return

    all_names = [c["name"] for c in contexts]

    if len(all_names) == 1:
        selected_names = all_names
    else:
        selected_names = select_contexts("Select contexts to export:", all_names)
        if not selected_names:
            return

    exported = filter_contexts(config, selected_names)
    exported["current-context"] = selected_names[0]

    out_path_str = questionary.text(
        "Export to file (leave empty to print to stdout):",
    ).ask()
    if out_path_str is None:
        return

    export_yaml = dump_yaml(exported)

    if not out_path_str.strip():
        console.print("\n[bold]Exported kubeconfig:[/bold]")
        console.print(Syntax(export_yaml, "yaml", theme="monokai", line_numbers=True))
        return

    out_path = Path(out_path_str.strip()).expanduser()
    if out_path.exists() and not questionary.confirm(
        f"{out_path} already exists — overwrite?", default=False
    ).ask():
        console.print("[dim]Aborted.[/dim]")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(export_yaml)
    out_path.chmod(0o600)
    console.print(f"[green]✓ Exported {len(selected_names)} context(s) to {out_path}[/green]")


# ── Import ────────────────────────────────────────────────────────────────────

def _maybe_repoint_localhost(remote: dict, default_host: str | None) -> dict:
    """If a cluster's API endpoint is 127.0.0.1, offer to replace it with the real host IP.

    A kubeconfig pulled from a remote machine often has its server set to
    127.0.0.1 — correct on that machine, but unreachable once imported here.
    """
    for cluster in get_list(remote, "clusters"):
        server = (cluster.get("cluster") or {}).get("server", "")
        parsed = urlparse(server)
        if parsed.hostname != "127.0.0.1" or parsed.port is None:
            continue

        if not questionary.confirm(
            f"Cluster '{cluster['name']}' points at 127.0.0.1 — replace with the host IP?",
            default=True,
        ).ask():
            continue

        host_ip = questionary.text("Host IP:", default=default_host or "").ask()
        if not host_ip:
            continue

        tools_context.set_cluster_server_endpoint(remote, cluster["name"], host_ip.strip(), parsed.port)
        console.print(f"[green]✓ {cluster['name']}: 127.0.0.1 → {host_ip.strip()}:{parsed.port}[/green]")

    return remote


def import_and_merge(
    remote: dict,
    empty_message: str = "No contexts found.",
    default_host: str | None = None,
) -> None:
    """Preview `remote` merged into the current kubeconfig and write it on confirm."""
    all_names = [c["name"] for c in get_list(remote, "contexts")]
    if not all_names:
        console.print(f"[yellow]{empty_message}[/yellow]")
        return

    if len(all_names) > 1:
        selected_names = select_contexts("Select contexts to import:", all_names)
        if not selected_names:
            return
        remote = filter_contexts(remote, selected_names)

    remote = _maybe_repoint_localhost(remote, default_host)

    new_names = [c["name"] for c in get_list(remote, "contexts")]

    console.print("\n[bold]Contexts to import:[/bold]")
    for n in new_names:
        console.print(f"  [cyan]+[/cyan] {n}")

    base = load_kubeconfig()
    existing = {c["name"] for c in get_list(base, "contexts")}
    overwrites = [n for n in new_names if n in existing]
    if overwrites:
        console.print(f"\n[yellow]Will overwrite existing:[/yellow] {', '.join(overwrites)}")

    merged = merge_configs(base, remote)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, prefix="kubeconfig_preview_"
    ) as tmp:
        tmp.write(dump_yaml(merged))
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


# ── Validate Contexts ─────────────────────────────────────────────────────────

def _check_context(name: str) -> str:
    try:
        result = subprocess.run(
            ["kubectl", "cluster-info", "--context", name],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return "[green]✓ OK[/green]"
        lines = (result.stderr or result.stdout).strip().splitlines()
        msg   = lines[0][:70] if lines else "failed"
        return f"[red]✗ {msg}[/red]"
    except subprocess.TimeoutExpired:
        return "[red]✗ timeout[/red]"


def validate_contexts_menu() -> None:
    if not shutil.which("kubectl"):
        console.print("[red]kubectl not found in PATH[/red]")
        return

    config   = load_kubeconfig()
    contexts = get_list(config, "contexts")
    if not contexts:
        console.print("[yellow]No contexts found.[/yellow]")
        return

    cluster_servers = cluster_server_map(config, default="?")
    names = [ctx["name"] for ctx in contexts]

    with console.status(f"Checking {len(names)} context(s)…"), \
         ThreadPoolExecutor(max_workers=min(len(names), 10)) as executor:
        statuses = list(executor.map(_check_context, names))

    table = Table(title="Context Validation", show_header=True, header_style="bold")
    table.add_column("Context", style="cyan", no_wrap=True)
    table.add_column("Server", style="dim")
    table.add_column("Status")

    for ctx, status in zip(contexts, statuses):
        name   = ctx["name"]
        server = cluster_servers.get(context_refs(ctx).cluster, "?")
        table.add_row(name, server, status)

    console.print(table)
