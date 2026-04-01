"""Kubeconfig helpers: load, save, backup, rename, and merge configs."""

import copy
import shutil
from datetime import datetime
from pathlib import Path

import yaml
from rich.console import Console

console = Console()

KUBECONFIG_PATH = Path.home() / ".kube" / "config"


def _empty_config() -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [],
        "contexts": [],
        "users": [],
        "current-context": "",
        "preferences": {},
    }


def get_list(config: dict, key: str) -> list:
    return config.get(key) or []


def load_kubeconfig(path: Path | None = None) -> dict:
    if path is None:
        path = KUBECONFIG_PATH
    if not path.exists():
        return _empty_config()
    with path.open() as f:
        return yaml.safe_load(f) or _empty_config()


def save_kubeconfig(config: dict, path: Path | None = None) -> None:
    if path is None:
        path = KUBECONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    path.chmod(0o600)


def backup_kubeconfig() -> Path | None:
    if not KUBECONFIG_PATH.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = KUBECONFIG_PATH.parent / f"config.backup.{ts}"
    shutil.copy2(KUBECONFIG_PATH, backup)
    console.print(f"[dim]  Backup → {backup}[/dim]")
    return backup


def rename_config_for_host(remote: dict, hostname: str) -> dict:
    """
    Rename all contexts/clusters/users to use hostname as the base name.
    Single context  → 'hostname'
    Multiple contexts → 'hostname-{original}'
    """
    cfg = copy.deepcopy(remote)
    contexts = get_list(cfg, "contexts")
    clusters = get_list(cfg, "clusters")
    users    = get_list(cfg, "users")
    def new_name(original: str) -> str:
        return f"{hostname}@{original}"

    cluster_map = {c["name"]: new_name(c["name"]) for c in clusters}
    user_map    = {u["name"]: new_name(u["name"]) for u in users}
    context_map = {c["name"]: new_name(c["name"]) for c in contexts}

    for c in clusters:
        c["name"] = cluster_map[c["name"]]
    for u in users:
        u["name"] = user_map[u["name"]]
    for ctx in contexts:
        ctx["name"] = context_map[ctx["name"]]
        ref = ctx.get("context") or {}
        if ref.get("cluster") in cluster_map:
            ref["cluster"] = cluster_map[ref["cluster"]]
        if ref.get("user") in user_map:
            ref["user"] = user_map[ref["user"]]

    old_current = cfg.get("current-context", "")
    cfg["current-context"] = context_map.get(old_current, old_current)
    cfg["clusters"]  = clusters
    cfg["contexts"]  = contexts
    cfg["users"]     = users
    return cfg


def filter_contexts(config: dict, keep: list[str]) -> dict:
    """Return a copy of config containing only the named contexts and their clusters/users."""
    cfg      = copy.deepcopy(config)
    keep_set = set(keep)
    contexts = [c for c in get_list(cfg, "contexts") if c["name"] in keep_set]
    used_clusters = {(c.get("context") or {}).get("cluster") for c in contexts}
    used_users    = {(c.get("context") or {}).get("user")    for c in contexts}
    cfg["contexts"] = contexts
    cfg["clusters"] = [c for c in get_list(cfg, "clusters") if c["name"] in used_clusters]
    cfg["users"]    = [u for u in get_list(cfg, "users")    if u["name"] in used_users]
    if cfg.get("current-context") not in keep_set:
        cfg["current-context"] = keep[0] if keep else ""
    return cfg


def merge_configs(base: dict, overlay: dict) -> dict:
    """Merge overlay into a deep copy of base. Items with the same name are overwritten."""
    merged = copy.deepcopy(base)
    for key in ("clusters", "contexts", "users"):
        by_name = {item["name"]: i for i, item in enumerate(get_list(merged, key))}
        if key not in merged:
            merged[key] = []
        for item in get_list(overlay, key):
            if item["name"] in by_name:
                merged[key][by_name[item["name"]]] = item
            else:
                merged[key].append(item)
    return merged
