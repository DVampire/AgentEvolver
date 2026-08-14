"""Binding the gateway off loopback must require a token.

The gateway drives agents: it starts runs, reads sessions, and reaches the workspace. On
`127.0.0.1` that is the developer's own machine and a token would be ceremony. The moment
`--host` names anything else the same surface is on the network, and the default — no
token — would leave it open to whoever can route to the port.

This is the kind of default that is safe until one flag changes and then is not, and the
change looks routine at the call site. The check belongs at argument parsing, where the
flag is read, rather than at bind time when the socket is already up.
"""

import pytest

from agentevolver.cli import gateway_main


def test_a_non_loopback_host_is_refused_without_a_token(monkeypatch) -> None:
    """Exit rather than bind. Serving unauthenticated is not a lesser failure than not
    serving: a refused start is visible immediately, an open port is not.

    The environment variable is cleared explicitly — a developer who exports it would
    otherwise satisfy the requirement and turn this green without the code doing anything.
    """
    monkeypatch.delenv("AGENTEVOLVER_GATEWAY_TOKEN", raising=False)

    with pytest.raises(SystemExit) as exit_info:
        gateway_main(["--transport", "websocket", "--host", "0.0.0.0"])

    # argparse's own usage-error code. Asserting the code, not merely that it exited,
    # separates "refused the arguments" from a crash on the way up — which would also
    # raise SystemExit and would also leave nothing listening.
    assert exit_info.value.code == 2
