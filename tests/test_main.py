"""Tests for main.py menu functions."""
import subprocess
from unittest.mock import MagicMock, patch

import yaml

import main
from conftest import REMOTE_MULTI, REMOTE_SINGLE, SAMPLE_KUBECONFIG, make_mock_ssh_client
from tools_context import load_kubeconfig, _empty_config


class TestSetCurrentContextMenu:
    def test_sets_selected_context(self, kubeconfig_file):
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.questionary.select") as mock_sel:
            mock_sel.return_value.ask.return_value = "dev"
            main.set_current_context_menu()
        assert load_kubeconfig(kubeconfig_file)["current-context"] == "dev"

    def test_no_write_when_already_current(self, kubeconfig_file):
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.questionary.select") as mock_sel, \
             patch("tools_context.save_kubeconfig") as mock_save:
            mock_sel.return_value.ask.return_value = "prod"  # already active
            main.set_current_context_menu()
        mock_save.assert_not_called()

    def test_no_write_when_cancelled(self, kubeconfig_file):
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.questionary.select") as mock_sel, \
             patch("tools_context.save_kubeconfig") as mock_save:
            mock_sel.return_value.ask.return_value = None
            main.set_current_context_menu()
        mock_save.assert_not_called()

    def test_empty_config_does_not_prompt(self, tmp_path):
        with patch("tools_context.KUBECONFIG_PATH", tmp_path / "none"), \
             patch("main.questionary.select") as mock_sel:
            main.set_current_context_menu()
        mock_sel.assert_not_called()


class TestDeleteContextMenu:
    def test_deletes_context_and_orphans(self, kubeconfig_file):
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.questionary.select")  as mock_sel, \
             patch("main.questionary.confirm") as mock_conf:
            mock_sel.return_value.ask.return_value  = "dev"
            mock_conf.return_value.ask.return_value = True
            main.delete_context_menu()

        saved = load_kubeconfig(kubeconfig_file)
        assert "dev"         not in [c["name"] for c in saved["contexts"]]
        assert "dev-cluster" not in [c["name"] for c in saved["clusters"]]
        assert "dev-user"    not in [u["name"] for u in saved["users"]]

    def test_keeps_shared_cluster(self, tmp_path):
        cfg = {
            "apiVersion": "v1", "kind": "Config", "preferences": {},
            "clusters":  [{"name": "shared", "cluster": {"server": "https://x:6443"}}],
            "contexts":  [
                {"name": "ctx-a", "context": {"cluster": "shared", "user": "user-a"}},
                {"name": "ctx-b", "context": {"cluster": "shared", "user": "user-b"}},
            ],
            "users": [{"name": "user-a", "user": {}}, {"name": "user-b", "user": {}}],
            "current-context": "ctx-a",
        }
        p = tmp_path / "config"
        p.write_text(yaml.dump(cfg))

        with patch("tools_context.KUBECONFIG_PATH", p), \
             patch("main.questionary.select")  as mock_sel, \
             patch("main.questionary.confirm") as mock_conf:
            mock_sel.return_value.ask.return_value  = "ctx-a"
            mock_conf.return_value.ask.return_value = True
            main.delete_context_menu()

        saved = load_kubeconfig(p)
        assert "shared" in [c["name"] for c in saved["clusters"]]

    def test_resets_current_context_when_deleted(self, kubeconfig_file):
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.questionary.select")  as mock_sel, \
             patch("main.questionary.confirm") as mock_conf:
            mock_sel.return_value.ask.return_value  = "prod"
            mock_conf.return_value.ask.return_value = True
            main.delete_context_menu()

        saved = load_kubeconfig(kubeconfig_file)
        assert saved["current-context"] != "prod"
        assert saved["current-context"] in [c["name"] for c in saved["contexts"]]

    def test_aborted_does_not_write(self, kubeconfig_file):
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.questionary.select")  as mock_sel, \
             patch("main.questionary.confirm") as mock_conf, \
             patch("tools_context.save_kubeconfig") as mock_save:
            mock_sel.return_value.ask.return_value  = "dev"
            mock_conf.return_value.ask.return_value = False
            main.delete_context_menu()
        mock_save.assert_not_called()

    def test_empty_config_does_not_prompt(self, tmp_path):
        with patch("tools_context.KUBECONFIG_PATH", tmp_path / "none"), \
             patch("main.questionary.select") as mock_sel:
            main.delete_context_menu()
        mock_sel.assert_not_called()

    def test_last_context_clears_current_context(self, tmp_path):
        cfg = {
            "apiVersion": "v1", "kind": "Config", "preferences": {},
            "clusters":  [{"name": "only", "cluster": {"server": "https://x:6443"}}],
            "contexts":  [{"name": "only", "context": {"cluster": "only", "user": "u"}}],
            "users":     [{"name": "u", "user": {}}],
            "current-context": "only",
        }
        p = tmp_path / "config"
        p.write_text(yaml.dump(cfg))

        with patch("tools_context.KUBECONFIG_PATH", p), \
             patch("main.questionary.select")  as mock_sel, \
             patch("main.questionary.confirm") as mock_conf:
            mock_sel.return_value.ask.return_value  = "only"
            mock_conf.return_value.ask.return_value = True
            main.delete_context_menu()

        saved = load_kubeconfig(p)
        assert saved["current-context"] == ""
        assert saved["contexts"] == []


class TestExportContextsMenu:
    def test_exports_single_context_to_file(self, kubeconfig_file, tmp_path):
        out = tmp_path / "exported.yaml"
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.questionary.checkbox") as mock_chk, \
             patch("main.questionary.text")     as mock_txt:
            mock_chk.return_value.ask.return_value = ["dev"]
            mock_txt.return_value.ask.return_value = str(out)
            main.export_contexts_menu()

        saved = yaml.safe_load(out.read_text())
        assert [c["name"] for c in saved["contexts"]] == ["dev"]
        assert saved["current-context"] == "dev"

    def test_export_includes_only_selected_cluster_and_user(self, kubeconfig_file, tmp_path):
        out = tmp_path / "exported.yaml"
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.questionary.checkbox") as mock_chk, \
             patch("main.questionary.text")     as mock_txt:
            mock_chk.return_value.ask.return_value = ["prod"]
            mock_txt.return_value.ask.return_value = str(out)
            main.export_contexts_menu()

        saved = yaml.safe_load(out.read_text())
        assert [c["name"] for c in saved["clusters"]] == ["prod-cluster"]
        assert [u["name"] for u in saved["users"]]    == ["prod-user"]

    def test_export_sets_permissions_600(self, kubeconfig_file, tmp_path):
        out = tmp_path / "exported.yaml"
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.questionary.checkbox") as mock_chk, \
             patch("main.questionary.text")     as mock_txt:
            mock_chk.return_value.ask.return_value = ["dev"]
            mock_txt.return_value.ask.return_value = str(out)
            main.export_contexts_menu()

        assert oct(out.stat().st_mode)[-3:] == "600"

    def test_export_prints_to_stdout_when_no_path(self, kubeconfig_file):
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.questionary.checkbox") as mock_chk, \
             patch("main.questionary.text")     as mock_txt:
            mock_chk.return_value.ask.return_value = ["dev"]
            mock_txt.return_value.ask.return_value = ""  # empty → stdout
            main.export_contexts_menu()  # must not raise

    def test_export_overwrites_existing_file_on_confirm(self, kubeconfig_file, tmp_path):
        out = tmp_path / "exported.yaml"
        out.write_text("old content")
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.questionary.checkbox") as mock_chk, \
             patch("main.questionary.text")     as mock_txt, \
             patch("main.questionary.confirm")  as mock_conf:
            mock_chk.return_value.ask.return_value  = ["dev"]
            mock_txt.return_value.ask.return_value  = str(out)
            mock_conf.return_value.ask.return_value = True
            main.export_contexts_menu()

        saved = yaml.safe_load(out.read_text())
        assert [c["name"] for c in saved["contexts"]] == ["dev"]

    def test_export_aborts_when_overwrite_declined(self, kubeconfig_file, tmp_path):
        out = tmp_path / "exported.yaml"
        out.write_text("old content")
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.questionary.checkbox") as mock_chk, \
             patch("main.questionary.text")     as mock_txt, \
             patch("main.questionary.confirm")  as mock_conf:
            mock_chk.return_value.ask.return_value  = ["dev"]
            mock_txt.return_value.ask.return_value  = str(out)
            mock_conf.return_value.ask.return_value = False
            main.export_contexts_menu()

        assert out.read_text() == "old content"

    def test_export_cancelled_checkbox_does_not_write(self, kubeconfig_file, tmp_path):
        out = tmp_path / "exported.yaml"
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.questionary.checkbox") as mock_chk:
            mock_chk.return_value.ask.return_value = None
            main.export_contexts_menu()

        assert not out.exists()

    def test_empty_config_does_not_prompt(self, tmp_path):
        with patch("tools_context.KUBECONFIG_PATH", tmp_path / "none"), \
             patch("main.questionary.checkbox") as mock_chk:
            main.export_contexts_menu()
        mock_chk.assert_not_called()

    def test_single_context_skips_checkbox(self, tmp_path):
        cfg = {
            "apiVersion": "v1", "kind": "Config", "preferences": {},
            "clusters":  [{"name": "only", "cluster": {"server": "https://x:6443"}}],
            "contexts":  [{"name": "only", "context": {"cluster": "only", "user": "u"}}],
            "users":     [{"name": "u", "user": {}}],
            "current-context": "only",
        }
        p = tmp_path / "config"
        p.write_text(yaml.dump(cfg))
        out = tmp_path / "exported.yaml"

        with patch("tools_context.KUBECONFIG_PATH", p), \
             patch("main.questionary.checkbox") as mock_chk, \
             patch("main.questionary.text")     as mock_txt:
            mock_txt.return_value.ask.return_value = str(out)
            main.export_contexts_menu()

        mock_chk.assert_not_called()
        assert out.exists()


class TestValidateContextsMenu:
    def test_skips_when_kubectl_missing(self):
        with patch("main.shutil.which", return_value=None):
            main.validate_contexts_menu()

    def test_handles_successful_context(self, kubeconfig_file):
        ok = MagicMock(returncode=0, stderr="", stdout="Kubernetes control plane is running")
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.shutil.which", return_value="/usr/bin/kubectl"), \
             patch("main.subprocess.run", return_value=ok):
            main.validate_contexts_menu()

    def test_handles_failed_context(self, kubeconfig_file):
        fail = MagicMock(returncode=1, stderr="Unable to connect", stdout="")
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.shutil.which", return_value="/usr/bin/kubectl"), \
             patch("main.subprocess.run", return_value=fail):
            main.validate_contexts_menu()

    def test_handles_timeout(self, kubeconfig_file):
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.shutil.which", return_value="/usr/bin/kubectl"), \
             patch("main.subprocess.run", side_effect=subprocess.TimeoutExpired("kubectl", 10)):
            main.validate_contexts_menu()

    def test_calls_kubectl_with_context_flag(self, kubeconfig_file):
        ok = MagicMock(returncode=0, stderr="", stdout="")
        with patch("tools_context.KUBECONFIG_PATH", kubeconfig_file), \
             patch("main.shutil.which", return_value="/usr/bin/kubectl"), \
             patch("main.subprocess.run", return_value=ok) as mock_run:
            main.validate_contexts_menu()
        for call in mock_run.call_args_list:
            assert "--context" in call.args[0]

    def test_empty_config_does_not_call_kubectl(self, tmp_path):
        with patch("tools_context.KUBECONFIG_PATH", tmp_path / "none"), \
             patch("main.shutil.which", return_value="/usr/bin/kubectl"), \
             patch("main.subprocess.run") as mock_run:
            main.validate_contexts_menu()
        mock_run.assert_not_called()


class TestSshImportMenu:
    def test_full_import_writes_merged_config(self, tmp_path, ssh_config_file):
        kubeconfig = tmp_path / "config"
        kubeconfig.write_text(yaml.dump(SAMPLE_KUBECONFIG))
        client = make_mock_ssh_client(yaml.dump(REMOTE_SINGLE).encode())

        with patch("tools_context.KUBECONFIG_PATH", kubeconfig), \
             patch("tools_ssh.SSH_CONFIG_PATH",     ssh_config_file), \
             patch("tools_ssh.paramiko.SSHClient",  return_value=client), \
             patch("main.questionary.select")  as mock_sel, \
             patch("main.questionary.confirm") as mock_conf:
            mock_sel.return_value.ask.return_value  = "bastion"
            mock_conf.return_value.ask.return_value = True
            main.ssh_import_menu()

        saved = load_kubeconfig(kubeconfig)
        names = [c["name"] for c in saved["contexts"]]
        assert "bastion@default" in names
        assert "prod"            in names

    def test_aborted_does_not_change_config(self, tmp_path, ssh_config_file):
        kubeconfig = tmp_path / "config"
        kubeconfig.write_text(yaml.dump(SAMPLE_KUBECONFIG))
        client = make_mock_ssh_client(yaml.dump(REMOTE_SINGLE).encode())

        with patch("tools_context.KUBECONFIG_PATH", kubeconfig), \
             patch("tools_ssh.SSH_CONFIG_PATH",     ssh_config_file), \
             patch("tools_ssh.paramiko.SSHClient",  return_value=client), \
             patch("main.questionary.select")  as mock_sel, \
             patch("main.questionary.confirm") as mock_conf:
            mock_sel.return_value.ask.return_value  = "bastion"
            mock_conf.return_value.ask.return_value = False
            main.ssh_import_menu()

        saved = load_kubeconfig(kubeconfig)
        assert "bastion@default" not in [c["name"] for c in saved["contexts"]]

    def test_no_hosts_does_not_prompt(self, tmp_path):
        empty = tmp_path / "ssh_config"
        empty.write_text("")
        with patch("tools_ssh.SSH_CONFIG_PATH", empty), \
             patch("main.questionary.select") as mock_sel:
            main.ssh_import_menu()
        mock_sel.assert_not_called()

    def test_ssh_failure_does_not_write(self, tmp_path, ssh_config_file):
        kubeconfig = tmp_path / "config"
        kubeconfig.write_text(yaml.dump(SAMPLE_KUBECONFIG))
        client = MagicMock()
        client.connect.side_effect = OSError("refused")

        with patch("tools_ssh.SSH_CONFIG_PATH",    ssh_config_file), \
             patch("tools_ssh.paramiko.SSHClient", return_value=client), \
             patch("main.questionary.select") as mock_sel, \
             patch("tools_context.save_kubeconfig") as mock_save:
            mock_sel.return_value.ask.return_value = "bastion"
            main.ssh_import_menu()
        mock_save.assert_not_called()

    def test_creates_backup_on_confirm(self, tmp_path, ssh_config_file):
        kubeconfig = tmp_path / "config"
        kubeconfig.write_text(yaml.dump(SAMPLE_KUBECONFIG))
        client = make_mock_ssh_client(yaml.dump(REMOTE_SINGLE).encode())

        with patch("tools_context.KUBECONFIG_PATH", kubeconfig), \
             patch("tools_ssh.SSH_CONFIG_PATH",     ssh_config_file), \
             patch("tools_ssh.paramiko.SSHClient",  return_value=client), \
             patch("main.questionary.select")   as mock_sel, \
             patch("main.questionary.confirm")  as mock_conf, \
             patch("tools_context.backup_kubeconfig") as mock_backup:
            mock_sel.return_value.ask.return_value  = "bastion"
            mock_conf.return_value.ask.return_value = True
            main.ssh_import_menu()
        mock_backup.assert_called_once()

    def test_overwrites_existing_context_with_same_name(self, tmp_path, ssh_config_file):
        # context name now uses @ scheme: "bastion@default"
        existing = {
            **_empty_config(),
            "clusters":  [{"name": "bastion@default", "cluster": {"server": "https://old:6443"}}],
            "contexts":  [{"name": "bastion@default", "context": {"cluster": "bastion@default", "user": "bastion@admin"}}],
            "users":     [{"name": "bastion@admin", "user": {}}],
            "current-context": "bastion@default",
        }
        kubeconfig = tmp_path / "config"
        kubeconfig.write_text(yaml.dump(existing))
        client = make_mock_ssh_client(yaml.dump(REMOTE_SINGLE).encode())

        with patch("tools_context.KUBECONFIG_PATH", kubeconfig), \
             patch("tools_ssh.SSH_CONFIG_PATH",     ssh_config_file), \
             patch("tools_ssh.paramiko.SSHClient",  return_value=client), \
             patch("main.questionary.select")  as mock_sel, \
             patch("main.questionary.confirm") as mock_conf:
            mock_sel.return_value.ask.return_value  = "bastion"
            mock_conf.return_value.ask.return_value = True
            main.ssh_import_menu()

        saved   = load_kubeconfig(kubeconfig)
        cluster = next(c for c in saved["clusters"] if c["name"] == "bastion@default")
        assert cluster["cluster"]["server"] == "https://192.168.1.1:6443"
        assert len([c for c in saved["contexts"] if c["name"] == "bastion@default"]) == 1

    def test_multi_context_shows_checkbox_and_imports_selection(self, tmp_path, ssh_config_file):
        kubeconfig = tmp_path / "config"
        kubeconfig.write_text(yaml.dump(SAMPLE_KUBECONFIG))
        client = make_mock_ssh_client(yaml.dump(REMOTE_MULTI).encode())

        with patch("tools_context.KUBECONFIG_PATH", kubeconfig), \
             patch("tools_ssh.SSH_CONFIG_PATH",     ssh_config_file), \
             patch("tools_ssh.paramiko.SSHClient",  return_value=client), \
             patch("main.questionary.select")    as mock_sel, \
             patch("main.questionary.checkbox")  as mock_chk, \
             patch("main.questionary.confirm")   as mock_conf:
            mock_sel.return_value.ask.return_value  = "bastion"
            mock_chk.return_value.ask.return_value  = ["bastion@alpha"]
            mock_conf.return_value.ask.return_value = True
            main.ssh_import_menu()

        saved = load_kubeconfig(kubeconfig)
        names = [c["name"] for c in saved["contexts"]]
        assert "bastion@alpha" in names
        assert "bastion@beta"  not in names

    def test_multi_context_cancelled_checkbox_does_not_write(self, tmp_path, ssh_config_file):
        kubeconfig = tmp_path / "config"
        kubeconfig.write_text(yaml.dump(SAMPLE_KUBECONFIG))
        client = make_mock_ssh_client(yaml.dump(REMOTE_MULTI).encode())

        with patch("tools_context.KUBECONFIG_PATH", kubeconfig), \
             patch("tools_ssh.SSH_CONFIG_PATH",     ssh_config_file), \
             patch("tools_ssh.paramiko.SSHClient",  return_value=client), \
             patch("main.questionary.select")   as mock_sel, \
             patch("main.questionary.checkbox") as mock_chk, \
             patch("tools_context.save_kubeconfig") as mock_save:
            mock_sel.return_value.ask.return_value = "bastion"
            mock_chk.return_value.ask.return_value = None  # Ctrl+C / cancelled
            main.ssh_import_menu()

        mock_save.assert_not_called()


class TestSshContexts:
    def _make_config(self, contexts, clusters):
        return {
            "apiVersion": "v1", "kind": "Config", "preferences": {},
            "contexts": contexts,
            "clusters": clusters,
            "users": [],
            "current-context": "",
        }

    def test_returns_ssh_contexts_with_at_in_name(self, tmp_path):
        cfg = self._make_config(
            contexts=[{"name": "bastion@default", "context": {"cluster": "bastion@default", "user": "u"}}],
            clusters=[{"name": "bastion@default", "cluster": {"server": "https://192.168.1.1:6443"}}],
        )
        with patch("tools_context.KUBECONFIG_PATH", tmp_path / "c"):
            (tmp_path / "c").write_text(yaml.dump(cfg))
            with patch("main.load_kubeconfig", return_value=cfg):
                result = main._ssh_contexts()
        assert len(result) == 1
        assert result[0]["context"] == "bastion@default"
        assert result[0]["ssh_host"] == "bastion"
        assert result[0]["remote_host"] == "192.168.1.1"
        assert result[0]["port"] == 6443

    def test_excludes_contexts_without_at(self, tmp_path):
        cfg = self._make_config(
            contexts=[{"name": "local", "context": {"cluster": "local", "user": "u"}}],
            clusters=[{"name": "local", "cluster": {"server": "https://localhost:6443"}}],
        )
        with patch("main.load_kubeconfig", return_value=cfg):
            result = main._ssh_contexts()
        assert result == []

    def test_handles_missing_port_in_server_url(self):
        cfg = {
            "apiVersion": "v1", "kind": "Config", "preferences": {},
            "contexts": [{"name": "host@ctx", "context": {"cluster": "host@ctx", "user": "u"}}],
            "clusters": [{"name": "host@ctx", "cluster": {"server": "https://10.0.0.1"}}],
            "users": [],
            "current-context": "",
        }
        with patch("main.load_kubeconfig", return_value=cfg):
            result = main._ssh_contexts()
        assert result[0]["port"] is None

    def test_handles_unknown_cluster_ref(self):
        cfg = {
            "apiVersion": "v1", "kind": "Config", "preferences": {},
            "contexts": [{"name": "host@ctx", "context": {"cluster": "missing-cluster", "user": "u"}}],
            "clusters": [],
            "users": [],
            "current-context": "",
        }
        with patch("main.load_kubeconfig", return_value=cfg):
            result = main._ssh_contexts()
        assert result[0]["server"] == ""
        assert result[0]["remote_host"] == "localhost"

    def test_multiple_ssh_contexts_returned(self):
        cfg = {
            "apiVersion": "v1", "kind": "Config", "preferences": {},
            "contexts": [
                {"name": "a@ctx1", "context": {"cluster": "a@ctx1", "user": "u"}},
                {"name": "b@ctx2", "context": {"cluster": "b@ctx2", "user": "u"}},
                {"name": "local",  "context": {"cluster": "local",  "user": "u"}},
            ],
            "clusters": [
                {"name": "a@ctx1", "cluster": {"server": "https://10.0.0.1:6443"}},
                {"name": "b@ctx2", "cluster": {"server": "https://10.0.0.2:6443"}},
                {"name": "local",  "cluster": {"server": "https://localhost:6443"}},
            ],
            "users": [],
            "current-context": "",
        }
        with patch("main.load_kubeconfig", return_value=cfg):
            result = main._ssh_contexts()
        assert len(result) == 2
        assert {r["ssh_host"] for r in result} == {"a", "b"}
