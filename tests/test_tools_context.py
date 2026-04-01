"""Tests for tools_context: IO, backup, rename, merge."""
import copy
from unittest.mock import patch

import yaml

import tools_context as ctx
from conftest import REMOTE_SINGLE, REMOTE_MULTI, SAMPLE_KUBECONFIG


class TestGetList:
    def test_returns_list_for_existing_key(self):
        assert ctx.get_list({"clusters": [1, 2]}, "clusters") == [1, 2]

    def test_returns_empty_for_missing_key(self):
        assert ctx.get_list({}, "clusters") == []

    def test_returns_empty_for_none_value(self):
        assert ctx.get_list({"clusters": None}, "clusters") == []


class TestKubeconfigIO:
    def test_load_existing_file(self, kubeconfig_file):
        config = ctx.load_kubeconfig(kubeconfig_file)
        assert config["current-context"] == "prod"
        assert len(config["contexts"]) == 2

    def test_load_missing_file_returns_empty(self, tmp_path):
        config = ctx.load_kubeconfig(tmp_path / "nonexistent")
        assert config["clusters"] == []
        assert config["contexts"] == []
        assert config["users"]    == []

    def test_load_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "config"
        p.write_text("")
        assert ctx.load_kubeconfig(p)["contexts"] == []

    def test_load_uses_module_default_when_no_arg(self, kubeconfig_file):
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file):
            config = ctx.load_kubeconfig()
        assert config["current-context"] == "prod"

    def test_save_roundtrip(self, tmp_path):
        p = tmp_path / "config"
        ctx.save_kubeconfig(SAMPLE_KUBECONFIG, p)
        loaded = ctx.load_kubeconfig(p)
        assert loaded["current-context"] == "prod"
        assert len(loaded["contexts"]) == 2

    def test_save_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "nested" / "dir" / "config"
        ctx.save_kubeconfig(SAMPLE_KUBECONFIG, p)
        assert p.exists()

    def test_save_sets_permissions_600(self, tmp_path):
        p = tmp_path / "config"
        ctx.save_kubeconfig(SAMPLE_KUBECONFIG, p)
        assert oct(p.stat().st_mode)[-3:] == "600"

    def test_save_uses_module_default_when_no_arg(self, tmp_path):
        p = tmp_path / "config"
        with patch("tools_context.KUBECONFIG_PATH", p):
            ctx.save_kubeconfig(SAMPLE_KUBECONFIG)
        assert ctx.load_kubeconfig(p)["current-context"] == "prod"


class TestBackupKubeconfig:
    def test_creates_timestamped_backup(self, kubeconfig_file):
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file):
            backup = ctx.backup_kubeconfig()
        assert backup is not None
        assert backup.exists()
        assert "config.backup." in backup.name

    def test_returns_none_if_no_config(self, tmp_path):
        with patch("tools_context.KUBECONFIG_PATH", tmp_path / "nonexistent"):
            assert ctx.backup_kubeconfig() is None

    def test_backup_content_matches_original(self, kubeconfig_file):
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file):
            backup = ctx.backup_kubeconfig()
        assert backup is not None
        assert yaml.safe_load(backup.read_text()) == yaml.safe_load(kubeconfig_file.read_text())


class TestRenameConfigForHost:
    def test_single_context_becomes_hostname(self):
        renamed = ctx.rename_config_for_host(REMOTE_SINGLE, "my-server")
        assert renamed["contexts"][0]["name"] == "my-server"
        assert renamed["clusters"][0]["name"] == "my-server"
        assert renamed["users"][0]["name"]    == "my-server"

    def test_single_context_cross_references_updated(self):
        renamed = ctx.rename_config_for_host(REMOTE_SINGLE, "my-server")
        ref = renamed["contexts"][0]["context"]
        assert ref["cluster"] == "my-server"
        assert ref["user"]    == "my-server"

    def test_single_context_current_context_updated(self):
        renamed = ctx.rename_config_for_host(REMOTE_SINGLE, "my-server")
        assert renamed["current-context"] == "my-server"

    def test_multi_context_prefixed_with_hostname(self):
        renamed = ctx.rename_config_for_host(REMOTE_MULTI, "edge")
        names = [c["name"] for c in renamed["contexts"]]
        assert "edge-alpha" in names
        assert "edge-beta"  in names

    def test_multi_context_clusters_and_users_prefixed(self):
        renamed = ctx.rename_config_for_host(REMOTE_MULTI, "edge")
        assert "edge-alpha"      in [c["name"] for c in renamed["clusters"]]
        assert "edge-beta"       in [c["name"] for c in renamed["clusters"]]
        assert "edge-alpha-user" in [u["name"] for u in renamed["users"]]
        assert "edge-beta-user"  in [u["name"] for u in renamed["users"]]

    def test_multi_context_cross_references_consistent(self):
        renamed = ctx.rename_config_for_host(REMOTE_MULTI, "edge")
        c = next(x for x in renamed["contexts"] if x["name"] == "edge-alpha")
        assert c["context"]["cluster"] == "edge-alpha"
        assert c["context"]["user"]    == "edge-alpha-user"

    def test_multi_context_current_context_updated(self):
        renamed = ctx.rename_config_for_host(REMOTE_MULTI, "edge")
        assert renamed["current-context"] == "edge-alpha"

    def test_does_not_mutate_original(self):
        original = copy.deepcopy(REMOTE_SINGLE)
        ctx.rename_config_for_host(REMOTE_SINGLE, "x")
        assert REMOTE_SINGLE["contexts"][0]["name"] == original["contexts"][0]["name"]


class TestMergeConfigs:
    def test_adds_new_contexts_from_overlay(self):
        base    = ctx._empty_config()
        overlay = ctx.rename_config_for_host(REMOTE_SINGLE, "new-host")
        merged  = ctx.merge_configs(base, overlay)
        assert "new-host" in [c["name"] for c in merged["contexts"]]

    def test_preserves_existing_base_contexts(self):
        base = {
            "clusters": [], "users": [], "preferences": {},
            "contexts": [{"name": "existing", "context": {}}],
            "current-context": "existing",
        }
        overlay = ctx.rename_config_for_host(REMOTE_SINGLE, "new-host")
        merged  = ctx.merge_configs(base, overlay)
        names   = [c["name"] for c in merged["contexts"]]
        assert "existing" in names
        assert "new-host" in names

    def test_overlay_overwrites_same_name(self):
        base_ctx = {"name": "my-server", "context": {"cluster": "old", "user": "old-user"}}
        base     = {
            "clusters": [], "users": [], "preferences": {},
            "contexts": [base_ctx],
            "current-context": "my-server",
        }
        overlay = ctx.rename_config_for_host(REMOTE_SINGLE, "my-server")
        merged  = ctx.merge_configs(base, overlay)
        c       = next(x for x in merged["contexts"] if x["name"] == "my-server")
        assert c["context"]["cluster"] == "my-server"  # from overlay, not "old"

    def test_no_duplicate_entries(self):
        overlay = ctx.rename_config_for_host(REMOTE_SINGLE, "host-a")
        merged  = ctx.merge_configs(overlay, overlay)
        assert len(merged["contexts"]) == 1

    def test_does_not_mutate_base(self):
        base    = copy.deepcopy(SAMPLE_KUBECONFIG)
        overlay = ctx.rename_config_for_host(REMOTE_SINGLE, "host-a")
        ctx.merge_configs(base, overlay)
        assert len(base["contexts"]) == 2

    def test_merges_all_three_sections(self):
        base    = ctx._empty_config()
        overlay = ctx.rename_config_for_host(REMOTE_SINGLE, "host-a")
        merged  = ctx.merge_configs(base, overlay)
        assert len(merged["clusters"]) == 1
        assert len(merged["contexts"]) == 1
        assert len(merged["users"])    == 1
