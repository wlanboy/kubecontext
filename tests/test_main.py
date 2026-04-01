"""Tests for main.py menu functions."""
import subprocess
from unittest.mock import MagicMock, patch

import yaml

import main
from conftest import REMOTE_SINGLE, SAMPLE_KUBECONFIG, make_mock_ssh_client
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
        assert "bastion" in names
        assert "prod"    in names

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
        assert "bastion" not in [c["name"] for c in saved["contexts"]]

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
        existing = {
            **_empty_config(),
            "clusters":  [{"name": "bastion", "cluster": {"server": "https://old:6443"}}],
            "contexts":  [{"name": "bastion", "context": {"cluster": "bastion", "user": "bastion"}}],
            "users":     [{"name": "bastion", "user": {}}],
            "current-context": "bastion",
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

        saved = load_kubeconfig(kubeconfig)
        cluster = next(c for c in saved["clusters"] if c["name"] == "bastion")
        assert cluster["cluster"]["server"] == "https://192.168.1.1:6443"
        assert len([c for c in saved["contexts"] if c["name"] == "bastion"]) == 1
