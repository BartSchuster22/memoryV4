from app.core import GovernanceCore
from app.schemas import Lifecycle, MemoryRecord, MemoryRole


class FakeStore:
    def __init__(self) -> None:
        self.created: list[tuple[MemoryRecord, str]] = []
        self.transitions: list[tuple[str, Lifecycle, str]] = []

    def create_record(self, record: MemoryRecord, *, actor: str) -> None:
        self.created.append((record, actor))

    def transition_lifecycle(self, record_id: str, target: Lifecycle, *, actor: str) -> None:
        self.transitions.append((record_id, target, actor))


def test_governance_core_depends_on_store_port_for_writes() -> None:
    fake = FakeStore()
    core = GovernanceCore(store=fake)
    record = MemoryRecord(
        id="rec_core",
        role=MemoryRole.ACTIVE,
        lifecycle=Lifecycle.WORKING,
        scope="tenant/project",
        content="core writes through store port",
        author_actor="chatboard",
        write_policy="verification_required",
    )

    core.create_record(record, actor="chatboard")
    core.promote_to_live("rec_core", actor="verifier")

    assert fake.created == [(record, "chatboard")]
    assert fake.transitions == [("rec_core", Lifecycle.LIVE, "verifier")]
