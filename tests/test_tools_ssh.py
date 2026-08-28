"""Tests for tools_ssh: parse_ssh_config, download_remote_kubeconfig, tunnels."""
import json
import signal
from unittest.mock import MagicMock, patch

import pytest
import yaml
from conftest import REMOTE_SINGLE, make_mock_ssh_client

import kubecontext.tools_ssh as ssh


class TestParseSshConfig:
    def test_returns_named_hosts(self, ssh_config_file):
        with patch("kubecontext.tools_ssh.SSH_CONFIG_PATH", ssh_config_file):
            hosts = ssh.parse_ssh_config()
        assert hosts == ["bastion", "dev-server"]

    def test_filters_wildcards(self, ssh_config_file):
        with patch("kubecontext.tools_ssh.SSH_CONFIG_PATH", ssh_config_file):
            hosts = ssh.parse_ssh_config()
        assert not any("*" in h or "?" in h for h in hosts)

    def test_missing_file_returns_empty(self, tmp_path):
        with patch("kubecontext.tools_ssh.SSH_CONFIG_PATH", tmp_path / "nonexistent"):
            assert ssh.parse_ssh_config() == []

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "ssh_config"
        p.write_text("")
        with patch("kubecontext.tools_ssh.SSH_CONFIG_PATH", p):
            assert ssh.parse_ssh_config() == []


class TestDownloadRemoteKubeconfig:
    def test_returns_parsed_config_on_success(self, tmp_path):
        client = make_mock_ssh_client(yaml.dump(REMOTE_SINGLE).encode())
        with patch("kubecontext.tools_ssh.SSH_CONFIG_PATH", tmp_path / "none"), \
             patch("kubecontext.tools_ssh.paramiko.SSHClient", return_value=client):
            result = ssh.download_remote_kubeconfig("test-host")
        assert result is not None
        assert result["current-context"] == "default"

    def test_returns_none_on_file_not_found(self, tmp_path):
        client = MagicMock()
        client.open_sftp.return_value.__enter__ = MagicMock(side_effect=FileNotFoundError)
        with patch("kubecontext.tools_ssh.SSH_CONFIG_PATH", tmp_path / "none"), \
             patch("kubecontext.tools_ssh.paramiko.SSHClient", return_value=client):
            assert ssh.download_remote_kubeconfig("test-host") is None

    def test_returns_none_on_auth_failure(self, tmp_path):
        client = MagicMock()
        client.connect.side_effect = ssh.paramiko.AuthenticationException()
        with patch("kubecontext.tools_ssh.SSH_CONFIG_PATH", tmp_path / "none"), \
             patch("kubecontext.tools_ssh.paramiko.SSHClient", return_value=client):
            assert ssh.download_remote_kubeconfig("test-host") is None

    def test_returns_none_on_generic_exception(self, tmp_path):
        client = MagicMock()
        client.connect.side_effect = OSError("connection refused")
        with patch("kubecontext.tools_ssh.SSH_CONFIG_PATH", tmp_path / "none"), \
             patch("kubecontext.tools_ssh.paramiko.SSHClient", return_value=client):
            assert ssh.download_remote_kubeconfig("test-host") is None

    def test_uses_identity_file_from_ssh_config(self, tmp_path):
        cfg = tmp_path / "ssh_config"
        cfg.write_text("Host myhost\n    IdentityFile ~/.ssh/id_ed25519\n    User deploy\n")
        client = make_mock_ssh_client(yaml.dump(REMOTE_SINGLE).encode())
        with patch("kubecontext.tools_ssh.SSH_CONFIG_PATH", cfg), \
             patch("kubecontext.tools_ssh.paramiko.SSHClient", return_value=client):
            ssh.download_remote_kubeconfig("myhost")
        kwargs = client.connect.call_args.kwargs
        assert "key_filename" in kwargs
        assert kwargs["username"] == "deploy"


class TestSshTunnels:
    def _fresh(self):
        """Clear module-level tunnel list between tests."""
        ssh._active_tunnels.clear()

    def test_open_tunnel_spawns_process(self, tmp_path):
        self._fresh()
        mock_proc = MagicMock()
        mock_proc.pid  = 12345
        mock_proc.poll.return_value = None

        with patch("kubecontext.tools_ssh.subprocess.Popen", return_value=mock_proc), \
             patch("kubecontext.tools_ssh.TUNNEL_STATE_PATH", tmp_path / "tunnels.json"):
            t = ssh.open_tunnel("myhost", 6443, "localhost", 6443)

        assert t is not None
        assert t.pid == 12345
        assert t.host == "myhost"
        assert t.local_port == 6443
        assert t in ssh._active_tunnels

    def test_open_tunnel_saves_state(self, tmp_path):
        self._fresh()
        state_file = tmp_path / "tunnels.json"
        mock_proc  = MagicMock()
        mock_proc.pid = 9999
        mock_proc.poll.return_value = None

        with patch("kubecontext.tools_ssh.subprocess.Popen", return_value=mock_proc), \
             patch("kubecontext.tools_ssh.TUNNEL_STATE_PATH", state_file):
            ssh.open_tunnel("edge", 6443, "127.0.0.1", 6443)

        data = json.loads(state_file.read_text())
        assert len(data) == 1
        assert data[0]["pid"]  == 9999
        assert data[0]["host"] == "edge"

    def test_open_tunnel_returns_none_when_ssh_missing(self, tmp_path):
        self._fresh()
        with patch("kubecontext.tools_ssh.subprocess.Popen", side_effect=FileNotFoundError), \
             patch("kubecontext.tools_ssh.TUNNEL_STATE_PATH", tmp_path / "tunnels.json"):
            assert ssh.open_tunnel("host", 1234, "localhost", 1234) is None
        assert ssh._active_tunnels == []

    def test_close_tunnel_terminates_process(self, tmp_path):
        self._fresh()
        mock_proc = MagicMock()
        mock_proc.pid = 1111
        mock_proc.poll.return_value = None

        with patch("kubecontext.tools_ssh.subprocess.Popen", return_value=mock_proc), \
             patch("kubecontext.tools_ssh.TUNNEL_STATE_PATH", tmp_path / "tunnels.json"):
            t = ssh.open_tunnel("host", 6443, "localhost", 6443)
            assert t is not None
            ssh.close_tunnel(t)

        mock_proc.terminate.assert_called_once()
        assert t not in ssh._active_tunnels

    def test_close_tunnel_removes_from_state(self, tmp_path):
        self._fresh()
        state_file = tmp_path / "tunnels.json"
        mock_proc  = MagicMock()
        mock_proc.pid = 2222
        mock_proc.poll.return_value = None

        with patch("kubecontext.tools_ssh.subprocess.Popen", return_value=mock_proc), \
             patch("kubecontext.tools_ssh.TUNNEL_STATE_PATH", state_file):
            t = ssh.open_tunnel("host", 6443, "localhost", 6443)
            assert t is not None
            ssh.close_tunnel(t)

        data = json.loads(state_file.read_text())
        assert data == []

    def test_close_tunnel_uses_os_kill_for_restored_tunnel(self, tmp_path):
        self._fresh()
        state_file = tmp_path / "tunnels.json"
        # Simulate a tunnel restored from state (no _process, only pid)
        t = ssh.SshTunnel("host", 6443, "localhost", 6443, pid=3333)
        ssh._active_tunnels.append(t)

        # make alive return True so close actually sends signal
        with (
            patch("kubecontext.tools_ssh.os.kill") as mock_kill,
            patch("kubecontext.tools_ssh.TUNNEL_STATE_PATH", state_file),
            patch.object(type(t), "alive", new_callable=lambda: property(lambda self: True)),
        ):
            ssh.close_tunnel(t)

        mock_kill.assert_called_once_with(3333, signal.SIGTERM)

    def test_get_tunnels_filters_dead(self, tmp_path):
        self._fresh()
        state_file = tmp_path / "tunnels.json"
        state_file.write_text("[]")

        alive_proc = MagicMock()
        alive_proc.pid = 100
        alive_proc.poll.return_value = None

        dead_proc = MagicMock()
        dead_proc.pid = 101
        dead_proc.poll.return_value = 1  # exited

        ssh._active_tunnels.append(
            ssh.SshTunnel("h", 6443, "localhost", 6443, pid=100, _process=alive_proc)
        )
        ssh._active_tunnels.append(
            ssh.SshTunnel("h", 6444, "localhost", 6444, pid=101, _process=dead_proc)
        )

        with patch("kubecontext.tools_ssh.TUNNEL_STATE_PATH", state_file):
            tunnels = ssh.get_tunnels()

        assert len(tunnels) == 1
        assert tunnels[0].local_port == 6443

    def test_load_tunnels_restores_running(self, tmp_path):
        self._fresh()
        state_file = tmp_path / "tunnels.json"
        state_file.write_text(json.dumps([
            {"host": "kube", "local_port": 6443, "remote_host": "127.0.0.1", "remote_port": 6443, "pid": 5555},
        ]))

        with patch("kubecontext.tools_ssh.TUNNEL_STATE_PATH", state_file), \
             patch("kubecontext.tools_ssh.os.kill", return_value=None):  # pid 5555 "alive"
            ssh.load_tunnels()

        assert len(ssh._active_tunnels) == 1
        assert ssh._active_tunnels[0].host == "kube"
        assert ssh._active_tunnels[0].pid  == 5555

    def test_load_tunnels_ignores_dead_pids(self, tmp_path):
        self._fresh()
        state_file = tmp_path / "tunnels.json"
        state_file.write_text(json.dumps([
            {"host": "kube", "local_port": 6443, "remote_host": "127.0.0.1", "remote_port": 6443, "pid": 9998},
        ]))

        def dead_kill(pid, sig):
            raise OSError("no such process")

        with patch("kubecontext.tools_ssh.TUNNEL_STATE_PATH", state_file), \
             patch("kubecontext.tools_ssh.os.kill", side_effect=dead_kill):
            ssh.load_tunnels()

        assert ssh._active_tunnels == []

    def test_load_tunnels_missing_file_is_noop(self, tmp_path):
        self._fresh()
        with patch("kubecontext.tools_ssh.TUNNEL_STATE_PATH", tmp_path / "nonexistent.json"):
            ssh.load_tunnels()
        assert ssh._active_tunnels == []

    def test_tunnel_alive_uses_popen_poll_when_available(self):
        proc = MagicMock()
        proc.poll.return_value = None
        t = ssh.SshTunnel("h", 1, "l", 1, pid=1, _process=proc)
        assert t.alive is True
        proc.poll.return_value = 0
        assert t.alive is False

    def test_tunnel_alive_uses_os_kill_without_process(self):
        t = ssh.SshTunnel("h", 1, "l", 1, pid=7777)
        with patch("kubecontext.tools_ssh.os.kill", return_value=None):
            assert t.alive is True
        with patch("kubecontext.tools_ssh.os.kill", side_effect=OSError):
            assert t.alive is False

    def test_tunnel_label(self):
        t = ssh.SshTunnel("myhost", 6443, "127.0.0.1", 6443, pid=1)
        assert t.label == "localhost:6443 → myhost:127.0.0.1:6443"


class TestRemoteEndpointMaps:
    def test_remote_port_for_seeds_and_persists(self, tmp_path):
        path = tmp_path / "ports.json"
        with patch("kubecontext.tools_ssh.REMOTE_PORT_MAP_PATH", path):
            assert ssh.remote_port_for("host@ctx", 6443) == 6443
            # A later call with a different (e.g. reassigned local) port must
            # keep returning the first-ever-seen value, not the new one.
            assert ssh.remote_port_for("host@ctx", 7000) == 6443
        assert json.loads(path.read_text()) == {"host@ctx": 6443}

    def test_remote_host_for_seeds_and_persists(self, tmp_path):
        path = tmp_path / "hosts.json"
        with patch("kubecontext.tools_ssh.REMOTE_HOST_MAP_PATH", path):
            assert ssh.remote_host_for("host@ctx", "192.168.1.50") == "192.168.1.50"
            # Once a tunnel rewrites the kubeconfig's server to 127.0.0.1, the
            # next read must still resolve to the true original remote host.
            assert ssh.remote_host_for("host@ctx", "127.0.0.1") == "192.168.1.50"
        assert json.loads(path.read_text()) == {"host@ctx": "192.168.1.50"}

    def test_remote_host_for_keeps_contexts_independent(self, tmp_path):
        path = tmp_path / "hosts.json"
        with patch("kubecontext.tools_ssh.REMOTE_HOST_MAP_PATH", path):
            ssh.remote_host_for("a@ctx1", "10.0.0.1")
            ssh.remote_host_for("b@ctx2", "10.0.0.2")
        data = json.loads(path.read_text())
        assert data == {"a@ctx1": "10.0.0.1", "b@ctx2": "10.0.0.2"}


class TestSshContexts:
    @pytest.fixture(autouse=True)
    def _isolate_remote_maps(self, tmp_path, monkeypatch):
        monkeypatch.setattr("kubecontext.tools_ssh.REMOTE_HOST_MAP_PATH", tmp_path / "remote_hosts.json")
        monkeypatch.setattr("kubecontext.tools_ssh.REMOTE_PORT_MAP_PATH", tmp_path / "remote_ports.json")

    def _make_config(self, contexts, clusters):
        return {
            "apiVersion": "v1", "kind": "Config", "preferences": {},
            "contexts": contexts,
            "clusters": clusters,
            "users": [],
            "current-context": "",
        }

    def test_returns_ssh_contexts_with_at_in_name(self):
        cfg = self._make_config(
            contexts=[{"name": "bastion@default", "context": {"cluster": "bastion@default", "user": "u"}}],
            clusters=[{"name": "bastion@default", "cluster": {"server": "https://192.168.1.1:6443"}}],
        )
        with patch("kubecontext.tools_ssh.load_kubeconfig", return_value=cfg):
            result = ssh.ssh_contexts()
        assert len(result) == 1
        assert result[0].context == "bastion@default"
        assert result[0].ssh_host == "bastion"
        assert result[0].remote_host == "192.168.1.1"
        assert result[0].port == 6443

    def test_excludes_contexts_without_at(self):
        cfg = self._make_config(
            contexts=[{"name": "local", "context": {"cluster": "local", "user": "u"}}],
            clusters=[{"name": "local", "cluster": {"server": "https://localhost:6443"}}],
        )
        with patch("kubecontext.tools_ssh.load_kubeconfig", return_value=cfg):
            result = ssh.ssh_contexts()
        assert result == []

    def test_handles_missing_port_in_server_url(self):
        cfg = self._make_config(
            contexts=[{"name": "host@ctx", "context": {"cluster": "host@ctx", "user": "u"}}],
            clusters=[{"name": "host@ctx", "cluster": {"server": "https://10.0.0.1"}}],
        )
        with patch("kubecontext.tools_ssh.load_kubeconfig", return_value=cfg):
            result = ssh.ssh_contexts()
        assert result[0].port is None

    def test_handles_unknown_cluster_ref(self):
        cfg = self._make_config(
            contexts=[{"name": "host@ctx", "context": {"cluster": "missing-cluster", "user": "u"}}],
            clusters=[],
        )
        with patch("kubecontext.tools_ssh.load_kubeconfig", return_value=cfg):
            result = ssh.ssh_contexts()
        assert result[0].server == ""
        assert result[0].remote_host == "localhost"

    def test_multiple_ssh_contexts_returned(self):
        cfg = self._make_config(
            contexts=[
                {"name": "a@ctx1", "context": {"cluster": "a@ctx1", "user": "u"}},
                {"name": "b@ctx2", "context": {"cluster": "b@ctx2", "user": "u"}},
                {"name": "local",  "context": {"cluster": "local",  "user": "u"}},
            ],
            clusters=[
                {"name": "a@ctx1", "cluster": {"server": "https://10.0.0.1:6443"}},
                {"name": "b@ctx2", "cluster": {"server": "https://10.0.0.2:6443"}},
                {"name": "local",  "cluster": {"server": "https://localhost:6443"}},
            ],
        )
        with patch("kubecontext.tools_ssh.load_kubeconfig", return_value=cfg):
            result = ssh.ssh_contexts()
        assert len(result) == 2
        assert {r.ssh_host for r in result} == {"a", "b"}

    def test_remote_host_survives_tunnel_rewrite_to_localhost(self):
        """Regression test: once a tunnel rewrites cluster.server to 127.0.0.1
        (see set_cluster_server_endpoint), re-deriving remote_host straight
        from the kubeconfig would wrongly return 127.0.0.1 instead of the
        node's real address — the ssh -L target would then point at the
        wrong host. remote_host_for must keep returning the original.
        """
        cfg = self._make_config(
            contexts=[{"name": "bastion@default", "context": {"cluster": "bastion@default", "user": "u"}}],
            clusters=[{"name": "bastion@default", "cluster": {"server": "https://192.168.1.1:6443"}}],
        )
        with patch("kubecontext.tools_ssh.load_kubeconfig", return_value=cfg):
            first = ssh.ssh_contexts()
        assert first[0].remote_host == "192.168.1.1"

        # Simulate what opening a tunnel does: rewrite the local kubeconfig's
        # cluster.server to point at the tunnel's local endpoint.
        cfg["clusters"][0]["cluster"]["server"] = "https://127.0.0.1:6443"
        with patch("kubecontext.tools_ssh.load_kubeconfig", return_value=cfg):
            second = ssh.ssh_contexts()
        assert second[0].remote_host == "192.168.1.1"
