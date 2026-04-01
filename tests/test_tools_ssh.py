"""Tests for tools_ssh: parse_ssh_config and download_remote_kubeconfig."""
from unittest.mock import MagicMock, patch

import yaml

import tools_ssh as ssh
from conftest import REMOTE_SINGLE, make_mock_ssh_client


class TestParseSshConfig:
    def test_returns_named_hosts(self, ssh_config_file):
        with patch("tools_ssh.SSH_CONFIG_PATH", ssh_config_file):
            hosts = ssh.parse_ssh_config()
        assert hosts == ["bastion", "dev-server"]

    def test_filters_wildcards(self, ssh_config_file):
        with patch("tools_ssh.SSH_CONFIG_PATH", ssh_config_file):
            hosts = ssh.parse_ssh_config()
        assert not any("*" in h or "?" in h for h in hosts)

    def test_missing_file_returns_empty(self, tmp_path):
        with patch("tools_ssh.SSH_CONFIG_PATH", tmp_path / "nonexistent"):
            assert ssh.parse_ssh_config() == []

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "ssh_config"
        p.write_text("")
        with patch("tools_ssh.SSH_CONFIG_PATH", p):
            assert ssh.parse_ssh_config() == []


class TestDownloadRemoteKubeconfig:
    def test_returns_parsed_config_on_success(self, tmp_path):
        client = make_mock_ssh_client(yaml.dump(REMOTE_SINGLE).encode())
        with patch("tools_ssh.SSH_CONFIG_PATH", tmp_path / "none"), \
             patch("tools_ssh.paramiko.SSHClient", return_value=client):
            result = ssh.download_remote_kubeconfig("test-host")
        assert result is not None
        assert result["current-context"] == "default"

    def test_returns_none_on_file_not_found(self, tmp_path):
        client = MagicMock()
        client.open_sftp.return_value.__enter__ = MagicMock(side_effect=FileNotFoundError)
        with patch("tools_ssh.SSH_CONFIG_PATH", tmp_path / "none"), \
             patch("tools_ssh.paramiko.SSHClient", return_value=client):
            assert ssh.download_remote_kubeconfig("test-host") is None

    def test_returns_none_on_auth_failure(self, tmp_path):
        client = MagicMock()
        client.connect.side_effect = ssh.paramiko.AuthenticationException()
        with patch("tools_ssh.SSH_CONFIG_PATH", tmp_path / "none"), \
             patch("tools_ssh.paramiko.SSHClient", return_value=client):
            assert ssh.download_remote_kubeconfig("test-host") is None

    def test_returns_none_on_generic_exception(self, tmp_path):
        client = MagicMock()
        client.connect.side_effect = OSError("connection refused")
        with patch("tools_ssh.SSH_CONFIG_PATH", tmp_path / "none"), \
             patch("tools_ssh.paramiko.SSHClient", return_value=client):
            assert ssh.download_remote_kubeconfig("test-host") is None

    def test_uses_identity_file_from_ssh_config(self, tmp_path):
        cfg = tmp_path / "ssh_config"
        cfg.write_text("Host myhost\n    IdentityFile ~/.ssh/id_ed25519\n    User deploy\n")
        client = make_mock_ssh_client(yaml.dump(REMOTE_SINGLE).encode())
        with patch("tools_ssh.SSH_CONFIG_PATH", cfg), \
             patch("tools_ssh.paramiko.SSHClient", return_value=client):
            ssh.download_remote_kubeconfig("myhost")
        kwargs = client.connect.call_args.kwargs
        assert "key_filename" in kwargs
        assert kwargs["username"] == "deploy"
