"""Evidence files and look-alike screenshots against PostgreSQL, with stand-in graph and S3.

The object storage is a dict behind the few S3 calls Nightloom makes, and
the graph a stand-in that knows each entity's properties, so these run
without SeaweedFS or Neo4j.
"""

import hashlib
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from nightloom_api import evidence
from nightloom_api.audit.models import AuditEvent
from nightloom_api.auth.models import Role, User
from nightloom_api.graph.repository import Screenshot
from nightloom_api.infra import Infra
from nightloom_api.infra.graph import get_graph_repository
from nightloom_api.infra.storage import get_storage
from nightloom_core.entities import EntityType
from support import PREFIX, csrf, login

pytestmark = pytest.mark.db

UserFactory = Callable[..., Coroutine[Any, Any, User]]
ClientFactory = Callable[[], AsyncClient]

CASE: dict[str, Any] = {
    "title": "Example look-alike review",
    "purpose": "Find copies of example.org's login page on other domains, for its owner.",
    "legal_basis": "owned_assets",
    "ethics_acknowledged": True,
}
PNG = b"\x89PNG\r\n\x1a\nfake screenshot"
NAME = f"{hashlib.sha256(PNG).hexdigest()}.png"


class _Body:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def __aenter__(self) -> "_Body":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def read(self) -> bytes:
        return self.data


@dataclass
class FakeS3:
    """The S3 calls evidence.py makes, on a dict."""

    objects: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    fail: bool = False

    async def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:  # noqa: N803
        self.objects[Key] = (Body, ContentType)

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if self.fail:
            raise ClientError({"Error": {"Code": "InternalError"}}, "GetObject")
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": _Body(self.objects[Key][0])}

    async def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        keys = sorted(key for key in self.objects if key.startswith(kwargs["Prefix"]))
        page = keys[: kwargs["MaxKeys"]]
        return {"Contents": [{"Key": key} for key in page], "IsTruncated": len(keys) > len(page)}

    async def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]:  # noqa: N803
        for item in Delete["Objects"]:
            self.objects.pop(item["Key"], None)
        return {}


class FakeStorage:
    bucket = "nightloom-evidence"

    def __init__(self) -> None:
        self.s3 = FakeS3()

    async def client(self) -> FakeS3:
        return self.s3


class FakeGraph:
    """Entities with their properties per case; deleting a case's graph empties it."""

    def __init__(self) -> None:
        self.entities: dict[uuid.UUID, dict[str, dict[str, Any]]] = {}

    def add(self, case_id: uuid.UUID, key: str, **properties: Any) -> None:
        self.entities.setdefault(case_id, {})[key] = properties

    async def get_entity(self, case_id: uuid.UUID, key: str) -> object | None:
        properties = self.entities.get(case_id, {}).get(key)
        if properties is None:
            return None
        return SimpleNamespace(entity=SimpleNamespace(properties=properties), neighbours=0)

    async def screenshots(self, case_id: uuid.UUID, limit: int) -> list[Screenshot]:
        found = []
        for key, properties in sorted(self.entities.get(case_id, {}).items()):
            if "screenshot_phash" in properties:
                kind, _, value = key.partition(":")
                found.append(
                    Screenshot(
                        key=key,
                        type=EntityType(kind),
                        value=value,
                        phash=properties["screenshot_phash"],
                        screenshot=properties.get("screenshot"),
                    )
                )
        return found[:limit]

    async def delete_case_graph(self, case_id: uuid.UUID, **_: Any) -> int:
        return len(self.entities.pop(case_id, {}))


@dataclass
class World:
    infra: Infra
    case_id: uuid.UUID
    clients: dict[str, AsyncClient]
    graph: FakeGraph
    storage: FakeStorage

    @property
    def case_url(self) -> str:
        return f"{PREFIX}/cases/{self.case_id}"


@pytest.fixture
async def world(
    db_app: FastAPI, infra: Infra, make_user: UserFactory, new_client: ClientFactory
) -> World:
    graph, storage = FakeGraph(), FakeStorage()
    db_app.dependency_overrides[get_graph_repository] = lambda: graph
    db_app.dependency_overrides[get_storage] = lambda: storage
    users = {
        "owner": await make_user("owner@example.org", Role.ANALYST),
        "viewer": await make_user("viewer@example.org", Role.ANALYST),
        "outsider": await make_user("outsider@example.org", Role.ANALYST),
    }
    clients = {}
    for role, user in users.items():
        client = new_client()
        assert (await login(client, email=user.email)).status_code == 200
        clients[role] = client
    owner = clients["owner"]
    created = await owner.post(f"{PREFIX}/cases", json=CASE, headers=csrf(owner))
    assert created.status_code == 201, created.text
    case_id = uuid.UUID(created.json()["id"])
    added = await owner.put(
        f"{PREFIX}/cases/{case_id}/members",
        json={"email": users["viewer"].email, "role": "viewer"},
        headers=csrf(owner),
    )
    assert added.status_code == 201, added.text
    return World(infra, case_id, clients, graph, storage)


async def test_the_store_writes_under_the_case() -> None:
    storage = FakeStorage()
    case_id = uuid.uuid4()
    store = evidence.S3EvidenceStore(storage, case_id)  # type: ignore[arg-type]
    await store.save(NAME, PNG, "image/png")
    assert storage.s3.objects == {f"cases/{case_id}/evidence/{NAME}": (PNG, "image/png")}
    with pytest.raises(ValueError):
        await store.save(NAME, PNG, "text/html")
    with pytest.raises(ValueError):
        evidence.object_key(case_id, "../other-case/x.png")


async def test_members_read_the_case_evidence(world: World) -> None:
    key = evidence.object_key(world.case_id, NAME)
    world.storage.s3.objects[key] = (PNG, "image/png")
    response = await world.clients["viewer"].get(f"{world.case_url}/evidence/{NAME}")
    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "private" in response.headers["cache-control"]
    assert "sandbox" in response.headers["content-security-policy"]
    # Non-members get the case's usual 404.
    outsider = await world.clients["outsider"].get(f"{world.case_url}/evidence/{NAME}")
    assert outsider.status_code == 404


async def test_unknown_or_malformed_names_are_not_found(world: World) -> None:
    viewer = world.clients["viewer"]
    for name in (NAME, "x.png", "..%2F..%2Fsecret.png", f"{'a' * 64}.gif"):
        response = await viewer.get(f"{world.case_url}/evidence/{name}")
        assert response.status_code == 404, name


async def test_storage_failure_is_503(world: World) -> None:
    world.storage.s3.fail = True
    response = await world.clients["viewer"].get(f"{world.case_url}/evidence/{NAME}")
    assert response.status_code == 503


async def test_similar_screenshots(world: World) -> None:
    base = 0x0F0F_0F0F_0F0F_0F0F
    world.graph.add(
        world.case_id,
        "url:https://example.org/login",
        screenshot_phash=f"{base:016x}",
        screenshot=NAME,
    )
    world.graph.add(
        world.case_id,
        "url:https://examp1e.org/login",
        screenshot_phash=f"{base ^ 0b11:016x}",
        screenshot=NAME,
    )
    world.graph.add(
        world.case_id, "url:https://example.net/", screenshot_phash=f"{base ^ 0x3FF:016x}"
    )
    world.graph.add(
        world.case_id, "url:https://unrelated.org/", screenshot_phash=f"{~base & (2**64 - 1):016x}"
    )
    world.graph.add(world.case_id, "domain:example.org")
    viewer = world.clients["viewer"]
    response = await viewer.get(
        f"{world.case_url}/screenshots/similar", params={"key": "url:https://example.org/login"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"] == [
        {
            "key": "url:https://examp1e.org/login",
            "type": "url",
            "value": "https://examp1e.org/login",
            "distance": 2,
            "screenshot": NAME,
        },
        {
            "key": "url:https://example.net/",
            "type": "url",
            "value": "https://example.net/",
            "distance": 10,
            "screenshot": None,
        },
    ]
    none = await viewer.get(
        f"{world.case_url}/screenshots/similar", params={"key": "domain:example.org"}
    )
    assert none.status_code == 404
    bad = await viewer.get(f"{world.case_url}/screenshots/similar", params={"key": "nope"})
    assert bad.status_code == 422


async def test_deleting_the_case_deletes_its_evidence(world: World) -> None:
    mine = evidence.object_key(world.case_id, NAME)
    other = evidence.object_key(uuid.uuid4(), NAME)
    world.storage.s3.objects[mine] = (PNG, "image/png")
    world.storage.s3.objects[other] = (PNG, "image/png")
    owner = world.clients["owner"]
    response = await owner.request(
        "DELETE",
        world.case_url,
        json={"confirm_title": CASE["title"]},
        headers=csrf(owner),
    )
    assert response.status_code == 204, response.text
    assert list(world.storage.s3.objects) == [other]
    async with world.infra.sessionmaker() as db:
        event = (
            await db.execute(select(AuditEvent).where(AuditEvent.action == "case.graph_delete"))
        ).scalar_one()
    assert event.outcome == "success"
    assert event.details == {"deleted_nodes": 0, "deleted_evidence": 1}


async def test_deleting_pages_through_many_files() -> None:
    storage = FakeStorage()
    case_id = uuid.uuid4()
    for n in range(evidence.DELETE_BATCH + 5):
        storage.s3.objects[f"cases/{case_id}/evidence/{n:064x}.png"] = (b"x", "image/png")
    assert await evidence.delete_case(storage, case_id) == evidence.DELETE_BATCH + 5  # type: ignore[arg-type]
    assert storage.s3.objects == {}
