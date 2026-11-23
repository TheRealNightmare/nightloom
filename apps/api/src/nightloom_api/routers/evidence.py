"""A case's evidence files (screenshots) and the pages that look alike.

``GET /cases/{id}/evidence/{name}`` serves one evidence file (members only;
the name is the file's SHA-256 and extension). ``GET
/cases/{id}/screenshots/similar?key=…`` lists the case's entities whose
screenshot's perceptual hash is at most :data:`SIMILAR_BITS` bits from the
given entity's: the *Looks like* pivot.
"""

import logging
from typing import Annotated

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import BaseModel

from nightloom_api import evidence
from nightloom_api.cases.access import Capability, CaseAccess, case_access
from nightloom_api.graph.repository import GraphRepository
from nightloom_api.infra.graph import get_graph_repository
from nightloom_api.infra.storage import Storage, get_storage
from nightloom_api.routers.graph import GRAPH_UNAVAILABLE, canonical_key
from nightloom_core.entities import EntityType
from nightloom_sdk.evidence import EVIDENCE_NAME

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cases/{case_id}", tags=["evidence"])

Readable = Annotated[CaseAccess, Depends(case_access(Capability.READ))]
Graph = Annotated[GraphRepository, Depends(get_graph_repository)]
ObjectStorage = Annotated[Storage, Depends(get_storage)]

# Pages at most this many of the hash's 64 bits apart look alike.
SIMILAR_BITS = 10
MAX_SIMILAR = 20
# Screenshots compared per request (a case rarely has more).
MAX_SCREENSHOTS = 5000

NO_FILE = "No such evidence file in this case."
NO_SCREENSHOT = "This entity has no screenshot."
STORAGE_UNAVAILABLE = "The evidence store is unavailable. Try again later."


@router.get(
    "/evidence/{name}",
    response_class=Response,
    responses={
        status.HTTP_200_OK: {"content": {"image/png": {}, "image/jpeg": {}}},
        status.HTTP_404_NOT_FOUND: {"description": "No such file in this case"},
    },
)
async def get_evidence(name: str, access: Readable, storage: ObjectStorage) -> Response:
    """One of the case's evidence files, such as a screenshot (members)."""
    if not EVIDENCE_NAME.fullmatch(name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_FILE)
    try:
        found = await evidence.read(storage, access.case.id, name)
    except (BotoCoreError, ClientError) as error:
        logger.error("Evidence read failed: %s", type(error).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, STORAGE_UNAVAILABLE) from error
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_FILE)
    return Response(
        content=found.data,
        media_type=found.content_type,
        headers={
            # Named by its content: it never changes, but it is the case's, not the public's.
            "Cache-Control": "private, max-age=86400, immutable",
            "Content-Disposition": f'inline; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


class SimilarItem(BaseModel):
    key: str
    type: EntityType
    value: str
    # How many of the 64 bits differ (0: the same picture as far as the hash can tell).
    distance: int
    screenshot: str | None


class SimilarList(BaseModel):
    items: list[SimilarItem]


def bits_apart(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


@router.get("/screenshots/similar", response_model=SimilarList)
async def similar_screenshots(
    access: Readable,
    graph: Graph,
    key: Annotated[str, Query(min_length=1, max_length=2200)],
) -> SimilarList:
    """The case's pages that look like this entity's screenshot, closest first (members)."""
    canonical_key(key)
    try:
        detail = await graph.get_entity(access.case.id, key)
        phash = (detail.entity.properties.get("screenshot_phash") if detail else None) or None
        if detail is None or not isinstance(phash, str):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NO_SCREENSHOT)
        others = await graph.screenshots(access.case.id, MAX_SCREENSHOTS)
    except (Neo4jError, DriverError) as error:
        logger.error("Graph read failed: %s", type(error).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, GRAPH_UNAVAILABLE) from error
    items: list[SimilarItem] = []
    for other in others:
        if other.key == key:
            continue
        try:
            apart = bits_apart(phash, other.phash)
        except ValueError:
            continue
        if apart <= SIMILAR_BITS:
            items.append(
                SimilarItem(
                    key=other.key,
                    type=other.type,
                    value=other.value,
                    distance=apart,
                    screenshot=other.screenshot,
                )
            )
    items.sort(key=lambda item: (item.distance, item.key))
    return SimilarList(items=items[:MAX_SIMILAR])
