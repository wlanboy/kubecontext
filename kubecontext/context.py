"""Context management UI — show, switch, delete, export, validate kubeconfig contexts."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import questionary
import yaml
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from . import tools_context
from .tools_context import (
    cluster_server_map,
    filter_contexts,
    get_list,
    load_kubeconfig,
    merge_configs,
    save_kubeconfig,
)

console = Console()


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
        name        = ctx["name"]
        cluster_ref = (ctx.get("context") or {}).get("cluster", "")
        user_ref    = (ctx.get("context") or {}).get("user", "")
        server      = cluster_servers.get(cluster_ref, "")
        marker      = "[green]→[/green]" if name == current else ""
        table.add_row(marker, name, server, user_ref)

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


# ── Export Contexts ───────────────────────────────────────────────────────────

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
        selected_names = questionary.checkbox(
            "Select contexts to export:",
            choices=all_names,
            validate=lambda v: True if v else "Select at least one context",
        ).ask()
        if not selected_names:
            return

    exported = filter_contexts(config, selected_names)
    exported["current-context"] = selected_names[0]

    out_path_str = questionary.text(
        "Export to file (leave empty to print to stdout):",
    ).ask()
    if out_path_str is None:
        return

    export_yaml = yaml.dump(exported, default_flow_style=False, allow_unicode=True)

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


# ── Import from File ─────────────────────────────────────────────────────────

def import_and_merge(remote: dict, empty_message: str = "No contexts found.") -> None:
    """Preview `remote` merged into the current kubeconfig and write it on confirm."""
    all_names = [c["name"] for c in get_list(remote, "contexts")]
    if not all_names:
        console.print(f"[yellow]{empty_message}[/yellow]")
        return

    if len(all_names) > 1:
        selected_names = questionary.checkbox(
            "Select contexts to import:",
            choices=all_names,
            validate=lambda v: True if v else "Select at least one context",
        ).ask()
        if not selected_names:
            return
        remote = filter_contexts(remote, selected_names)

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


def import_file_menu() -> None:
    path_str = questionary.text("Path to kubeconfig file:").ask()
    if not path_str:
        return

    path = Path(path_str.strip()).expanduser()
    if not path.exists():
        console.print(f"[red]✗ File not found: {path}[/red]")
        return

    try:
        remote = load_kubeconfig(path)
    except (OSError, yaml.YAMLError) as exc:
        console.print(f"[red]✗ Failed to load {path}: {exc}[/red]")
        return

    import_and_merge(remote, empty_message="No contexts found in file.")


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

    cluster_servers = cluster_server_map(config, default="?")

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
                    check=False,
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
