from datetime import datetime

import pytest
from fake_decisions import FakeDecisions
from fake_live import FakeLive, fake_summary
from fastapi.testclient import TestClient

from muthoot_collections import decisions
from muthoot_collections import live_session as live
from muthoot_collections.main import create_app
from muthoot_collections.models import Session
from muthoot_collections.store import IST, Store

TOKEN = "test-token"


class Clock:
    """Fixed IST clock: workshop day, 11:00, inside the call window."""

    def __init__(self) -> None:
        self.at = datetime(2026, 10, 7, 11, 0, tzinfo=IST)

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture(autouse=True)
def fake_decisions(request, monkeypatch) -> FakeDecisions | None:
    """Replace the two model calls in every offline test. The Live tests use the real models."""
    if request.node.get_closest_marker("live"):
        return None
    fake = FakeDecisions()
    # The links and callbacks of the scripted flows need the evidence that the gates ask for.
    fake.agrees("pay it today", 8612)
    fake.agrees("two months, 3,274", 3274)
    fake.borrower("one month, 1,637", reply_act="OFFER", offered_amount="A1", payment_time="TODAY")
    monkeypatch.setattr(decisions, "real_assess", fake.assess)
    monkeypatch.setattr(decisions, "real_consent", fake.consent)
    # Short waits keep the tests fast. The order of the steps stays the same.
    monkeypatch.setattr(live, "DEBOUNCE_S", 0.01)
    monkeypatch.setattr(live, "WAIT_FRAGMENT_S", 0.3)
    monkeypatch.setattr(live, "WAIT_CONSENT_S", 0.3)
    monkeypatch.setattr(live, "WAIT_DECISION_S", 0.2)
    return fake


@pytest.fixture
def store(clock: Clock) -> Store:
    return Store(now=clock)


class FakeControl:
    """A connected /ws/mobile control socket, for tests that do not drive the phone."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)


@pytest.fixture
def app_client(store: Store, monkeypatch):
    monkeypatch.setenv("DEMO_AGENT_TOKEN", TOKEN)
    monkeypatch.setenv("VIEW_TOKEN_KEY", "test-view-key")
    app = create_app(store, connect=FakeLive(), summarize=fake_summary)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


@pytest.fixture
def api(app_client: TestClient) -> TestClient:
    app_client.app.state.phone.control = FakeControl()
    return app_client


def live_session(api: TestClient, loan_id: str) -> Session:
    """Trigger a call the way the GE agent does, and return the bound Live session."""
    r = api.post("/api/agent/calls", json={"loan_id": loan_id})
    assert r.status_code == 200, r.json()
    return Session(call_id=r.json()["call_id"], loan_id=loan_id)
