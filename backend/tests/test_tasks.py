from app.workers.tasks import ping


def test_ping_returns_pong() -> None:
    assert ping.apply().get() == "pong"
