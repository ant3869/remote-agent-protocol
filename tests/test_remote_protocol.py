"""The authenticated remote-agent protocol: contract, host, discovery, dispatch.

These run a real host over loopback and drive it with the real client, because
the point of the feature is the wire between two machines -- a mocked transport
would test everything except that.
"""

import asyncio
import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from remote_agent_protocol import agent_bridge, remote_client, remote_protocol
from remote_agent_protocol.remote_client import RemoteHostClient, RemoteRegistry
from remote_agent_protocol.remote_host import RemoteAgentHost

TOKEN = "test-token"
ECHO_AGENT = [
    sys.executable,
    "-u",
    "-c",
    "import sys; print('AGENT SAW:', sys.argv[1][:40]); print('done')",
    "{task}",
]
SLOW_AGENT = [sys.executable, "-u", "-c", "import time; print('working'); time.sleep(30)", "{task}"]


@pytest.fixture
def host_server(tmp_path):
    """Serve a host with two synthetic agents on a loopback port."""
    host = RemoteAgentHost(
        backends={"echo": ECHO_AGENT, "slow": SLOW_AGENT},
        machine="Test Laptop",
        token=TOKEN,
        workspace_dir=str(tmp_path),
        scope_preamble="",
        kill_grace_secs=0.5,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), host.handler_class())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield host, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _get(url, token=TOKEN):
    request = urllib.request.Request(url)
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(request, timeout=5)


# -- contract ---------------------------------------------------------------


def test_a_missing_token_never_means_open_access():
    # A host with no configured secret must refuse everyone rather than
    # everyone: these requests run real tools on someone's machine.
    assert remote_protocol.authorized("Bearer anything", "") is False
    assert remote_protocol.authorized("", "") is False


def test_only_the_exact_bearer_token_is_accepted():
    assert remote_protocol.authorized(f"Bearer {TOKEN}", TOKEN) is True
    assert remote_protocol.authorized(f"Bearer {TOKEN}x", TOKEN) is False
    assert remote_protocol.authorized(TOKEN, TOKEN) is False


def test_qualified_names_separate_a_remote_agent_from_the_local_one():
    name = remote_protocol.remote_backend_name("laptop", "hermes")

    assert name == "laptop:hermes"
    assert remote_protocol.split_backend_name(name) == ("laptop", "hermes")
    assert remote_protocol.split_backend_name("hermes") is None


def test_capabilities_survive_a_round_trip_and_reject_junk():
    original = remote_protocol.HostCapabilities(machine="Laptop", agents=("hermes", "codex"))

    assert remote_protocol.HostCapabilities.from_payload(original.to_payload()) == original
    assert remote_protocol.HostCapabilities.from_payload({"machine": "Laptop"}) is None
    assert remote_protocol.HostCapabilities.from_payload("not a body") is None


# -- host -------------------------------------------------------------------


def test_the_host_refuses_an_unauthenticated_peer(host_server):
    _, url = host_server

    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(f"{url}{remote_protocol.CAPABILITIES_PATH}", token=None)

    assert caught.value.code == 401


def test_the_host_refuses_a_wrong_token(host_server):
    _, url = host_server

    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(f"{url}{remote_protocol.CAPABILITIES_PATH}", token="not-the-token")

    assert caught.value.code == 401


def test_the_host_announces_its_agents_and_machine(host_server):
    _, url = host_server

    payload = json.loads(_get(f"{url}{remote_protocol.CAPABILITIES_PATH}").read())

    assert payload["machine"] == "Test Laptop"
    assert payload["agents"] == ["echo", "slow"]
    assert payload["protocol"] == remote_protocol.PROTOCOL_VERSION


def test_the_host_heartbeat_reports_liveness(host_server):
    _, url = host_server

    payload = json.loads(_get(f"{url}{remote_protocol.HEARTBEAT_PATH}").read())

    assert payload["machine"] == "Test Laptop"
    assert payload["active_jobs"] == 0


def test_an_unknown_agent_is_refused_rather_than_run(host_server):
    host, _ = host_server
    events = []

    host.run_job(remote_protocol.JobRequest(agent="rm-rf", task="anything"), events.append)

    decoded = [remote_protocol.decode_event(event) for event in events]
    assert decoded[0]["type"] == "error"
    assert "unknown agent backend" in decoded[0]["message"]
    assert decoded[-1] == {"type": "exit", "code": 127}


# -- client and registry ----------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_brings_a_hosts_agents_online(host_server):
    _, url = host_server
    registry = RemoteRegistry({"laptop": {"url": url, "token": TOKEN}}, heartbeat_secs=60)

    await registry.discover()

    [state] = registry.states()
    assert state.online is True
    assert state.machine == "Test Laptop"
    assert registry.backend_names() == ["laptop:echo", "laptop:slow"]
    assert registry.machine_for("laptop:echo") == "Test Laptop"


@pytest.mark.asyncio
async def test_an_unreachable_host_offers_nothing(host_server):
    registry = RemoteRegistry(
        {"laptop": {"url": "http://127.0.0.1:9", "token": TOKEN}}, heartbeat_secs=60
    )

    await registry.discover()

    assert registry.backend_names() == []
    assert registry.states()[0].online is False


@pytest.mark.asyncio
async def test_a_host_that_stops_answering_is_dropped_from_delegation(host_server):
    # A laptop that goes to sleep must stop being offered before a job is sent
    # to it, not fail one minutes later.
    server_host, url = host_server
    registry = RemoteRegistry({"laptop": {"url": url, "token": TOKEN}}, heartbeat_secs=60)
    await registry.discover()
    assert registry.backend_names()

    registry._clients["laptop"] = RemoteHostClient("laptop", "http://127.0.0.1:9", TOKEN)
    await registry.discover()

    assert registry.backend_names() == []
    assert registry.client_for("laptop:echo") is None


@pytest.mark.asyncio
async def test_a_wrong_protocol_version_is_not_used(host_server, monkeypatch):
    _, url = host_server
    registry = RemoteRegistry({"laptop": {"url": url, "token": TOKEN}}, heartbeat_secs=60)
    monkeypatch.setattr(remote_protocol, "PROTOCOL_VERSION", "99")

    await registry.discover()

    assert registry.states()[0].online is False


@pytest.mark.asyncio
async def test_a_remote_job_streams_its_output_like_a_local_one(host_server):
    _, url = host_server
    client = RemoteHostClient("laptop", url, TOKEN)

    process = await client.start_job(
        remote_protocol.JobRequest(agent="echo", task="summarize the log")
    )
    lines = []
    while line := await process.stdout.readline():
        lines.append(line.decode().strip())
    code = await process.wait()

    assert code == 0
    assert any("AGENT SAW: summarize the log" in line for line in lines)
    assert lines[-1] == "done"


@pytest.mark.asyncio
async def test_cancelling_a_remote_job_stops_it_on_the_far_machine(host_server):
    _, url = host_server
    client = RemoteHostClient("laptop", url, TOKEN)

    process = await client.start_job(remote_protocol.JobRequest(agent="slow", task="wait forever"))
    assert (await process.stdout.readline()).strip() == b"working"
    process.terminate()
    code = await asyncio.wait_for(process.wait(), timeout=10)

    # However the OS reports the kill, the job is over and the stream is closed.
    assert code != 0
    assert await process.stdout.readline() == b""


# -- bridge dispatch --------------------------------------------------------


@pytest.mark.asyncio
async def test_the_bridge_runs_a_discovered_remote_agent_end_to_end(host_server):
    _, url = host_server
    registry = RemoteRegistry({"laptop": {"url": url, "token": TOKEN}}, heartbeat_secs=60)
    await registry.discover()
    events = []
    bridge = agent_bridge.AgentBridge(
        {"mock": ECHO_AGENT},
        events.append,
        remotes=registry,
        progress_interval_secs=0,
    )

    job_id = await bridge.start("laptop:echo", "check the printer")
    for _ in range(200):
        job = bridge.get(job_id)
        if job.status not in agent_bridge._ACTIVE_STATUSES:
            break
        await asyncio.sleep(0.05)

    job = bridge.get(job_id)
    assert job.status == agent_bridge.STATUS_DONE
    assert job.machine == "Test Laptop"
    assert any("AGENT SAW: check the printer" in line for line in job.lines)
    await bridge.shutdown()


@pytest.mark.asyncio
async def test_remote_agents_join_the_backend_list_only_while_online(host_server):
    _, url = host_server
    registry = RemoteRegistry({"laptop": {"url": url, "token": TOKEN}}, heartbeat_secs=60)
    bridge = agent_bridge.AgentBridge({"mock": ECHO_AGENT}, lambda event: None, remotes=registry)

    assert bridge.backend_names() == ["mock"]

    await registry.discover()

    assert bridge.backend_names() == ["laptop:echo", "laptop:slow", "mock"]
    assert bridge.remote_hosts()[0]["online"] is True


@pytest.mark.asyncio
async def test_a_job_for_an_offline_host_fails_immediately(host_server):
    registry = RemoteRegistry(
        {"laptop": {"url": "http://127.0.0.1:9", "token": TOKEN}}, heartbeat_secs=60
    )
    await registry.discover()
    events = []
    bridge = agent_bridge.AgentBridge({"mock": ECHO_AGENT}, events.append, remotes=registry)

    job_id = await bridge.start("laptop:echo", "anything")
    job = bridge.get(job_id)

    assert job.status == agent_bridge.STATUS_FAILED
    # Named as what it is: a host the operator configured that is not answering.
    assert "remote host 'laptop' is offline" in job.summary


@pytest.mark.asyncio
async def test_killing_a_job_whose_host_vanished_does_not_hang(host_server):
    # terminate() asks the host politely; kill() is the escalation, and it
    # cannot depend on the machine that has already stopped answering.
    _, url = host_server
    client = RemoteHostClient("laptop", url, TOKEN)
    process = await client.start_job(remote_protocol.JobRequest(agent="slow", task="wait"))
    assert (await process.stdout.readline()).strip() == b"working"

    client.url = "http://127.0.0.1:9"  # the host is gone now
    process.kill()

    assert await asyncio.wait_for(process.wait(), timeout=5) == -1
    assert await asyncio.wait_for(process.stdout.readline(), timeout=5) == b""


def test_a_lost_peer_stops_the_agent_instead_of_leaking_it(host_server):
    host, _ = host_server
    events = []

    def emit(event: str) -> None:
        events.append(event)
        if len(events) >= 2:  # let the job start, then hang up like a lost peer
            raise BrokenPipeError("peer went away")

    with pytest.raises(BrokenPipeError):
        host.run_job(remote_protocol.JobRequest(agent="slow", task="wait forever"), emit)

    # Nothing is left running, and nothing is left in the registry to cancel.
    assert host.heartbeat().active_jobs == 0


@pytest.mark.asyncio
async def test_a_flood_of_output_waits_for_the_reader_instead_of_buffering(host_server):
    # A local pipe gets backpressure from the OS. Without an equivalent here a
    # chatty agent's output accumulates in memory as fast as the network gives it.
    _, url = host_server
    client = RemoteHostClient("laptop", url, TOKEN)
    process = await client.start_job(remote_protocol.JobRequest(agent="echo", task="x"))
    for index in range(remote_client._MAX_BUFFERED_LINES + 50):
        feed = asyncio.ensure_future(process._feed(f"line {index}"))
        if index < remote_client._MAX_BUFFERED_LINES:
            await asyncio.wait_for(feed, timeout=2)
            continue
        # The buffer is full: this line cannot land until the reader takes one.
        await asyncio.sleep(0)
        assert not feed.done()
        assert await process.stdout.readline() != b""
        await asyncio.wait_for(feed, timeout=2)


@pytest.mark.asyncio
async def test_a_finished_job_always_reaches_its_reader(host_server):
    # The end marker has to land even when the buffer is full, or a reader waits
    # forever on a job that is already over.
    _, url = host_server
    client = RemoteHostClient("laptop", url, TOKEN)
    process = await client.start_job(remote_protocol.JobRequest(agent="echo", task="x"))
    while not process._lines.full():
        process._lines.put_nowait(b"filler\n")

    process._finish(0)

    while (line := await asyncio.wait_for(process.stdout.readline(), timeout=2)) != b"":
        pass
    assert await asyncio.wait_for(process.wait(), timeout=2) == 0
