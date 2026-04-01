"""Shared fixtures and sample data for all test modules."""
import pytest
import yaml

SAMPLE_KUBECONFIG = {
    "apiVersion": "v1",
    "kind": "Config",
    "clusters": [
        {"name": "prod-cluster", "cluster": {"server": "https://prod:6443", "certificate-authority-data": "abc"}},
        {"name": "dev-cluster",  "cluster": {"server": "https://dev:6443",  "certificate-authority-data": "def"}},
    ],
    "contexts": [
        {"name": "prod", "context": {"cluster": "prod-cluster", "user": "prod-user"}},
        {"name": "dev",  "context": {"cluster": "dev-cluster",  "user": "dev-user"}},
    ],
    "users": [
        {"name": "prod-user", "user": {"client-certificate-data": "x"}},
        {"name": "dev-user",  "user": {"client-certificate-data": "y"}},
    ],
    "current-context": "prod",
    "preferences": {},
}

REMOTE_SINGLE = {
    "apiVersion": "v1",
    "kind": "Config",
    "clusters": [{"name": "default", "cluster": {"server": "https://192.168.1.1:6443"}}],
    "contexts": [{"name": "default", "context": {"cluster": "default", "user": "admin"}}],
    "users":    [{"name": "admin",   "user": {}}],
    "current-context": "default",
    "preferences": {},
}

REMOTE_MULTI = {
    "apiVersion": "v1",
    "kind": "Config",
    "clusters": [
        {"name": "alpha", "cluster": {"server": "https://10.0.0.1:6443"}},
        {"name": "beta",  "cluster": {"server": "https://10.0.0.2:6443"}},
    ],
    "contexts": [
        {"name": "alpha", "context": {"cluster": "alpha", "user": "alpha-user"}},
        {"name": "beta",  "context": {"cluster": "beta",  "user": "beta-user"}},
    ],
    "users": [
        {"name": "alpha-user", "user": {}},
        {"name": "beta-user",  "user": {}},
    ],
    "current-context": "alpha",
    "preferences": {},
}

SSH_CONFIG_TEXT = """\
Host bastion
    HostName bastion.example.com
    User deploy
    IdentityFile ~/.ssh/id_rsa

Host *.internal
    User ops

Host dev-server
    HostName 10.0.1.5
    User ubuntu

Host *
    StrictHostKeyChecking no
"""


@pytest.fixture
def kubeconfig_file(tmp_path):
    p = tmp_path / "config"
    p.write_text(yaml.dump(SAMPLE_KUBECONFIG))
    return p


@pytest.fixture
def ssh_config_file(tmp_path):
    p = tmp_path / "ssh_config"
    p.write_text(SSH_CONFIG_TEXT)
    return p


def make_mock_ssh_client(content: bytes):
    """Build a fully-mocked paramiko SSHClient that returns `content` from SFTP."""
    from unittest.mock import MagicMock

    mock_file = MagicMock()
    mock_file.__enter__ = lambda s: mock_file
    mock_file.__exit__  = MagicMock(return_value=False)
    mock_file.read.return_value = content

    mock_sftp = MagicMock()
    mock_sftp.__enter__ = lambda s: mock_sftp
    mock_sftp.__exit__  = MagicMock(return_value=False)
    mock_sftp.open.return_value = mock_file

    mock_client = MagicMock()
    mock_client.open_sftp.return_value = mock_sftp
    return mock_client
