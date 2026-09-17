from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.dependencies import require_session
from app.schemas import SessionData

router = APIRouter(prefix='/api/gis', tags=['gis'])


@router.get('/maps')
async def maps(request: Request, _: tuple[str, SessionData] = Depends(require_session)):
    return await request.app.state.gis_client.maps()


@router.get('/maps/{map_id}/layers')
async def layers(map_id: UUID, request: Request, _: tuple[str, SessionData] = Depends(require_session)):
    return await request.app.state.gis_client.layers(str(map_id))


@router.get('/maps/{map_id}/bounds')
async def bounds(map_id: UUID, request: Request, _: tuple[str, SessionData] = Depends(require_session)):
    return await request.app.state.gis_client.bounds(str(map_id))


@router.get('/maps/{map_id}/features')
async def features(map_id: UUID, request: Request, bbox: str = Query(pattern=r'^-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?$'), layers: str | None = Query(default=None, max_length=2048), _: tuple[str, SessionData] = Depends(require_session)):
    return await request.app.state.gis_client.features(str(map_id), bbox=bbox, layers=layers)


@router.get('/maps/{map_id}/search')
async def search(map_id: UUID, request: Request, q: str = Query(min_length=1, max_length=200), _: tuple[str, SessionData] = Depends(require_session)):
    return await request.app.state.gis_client.search(str(map_id), q)


@router.get('/features/{feature_id}')
async def feature(feature_id: UUID, request: Request, _: tuple[str, SessionData] = Depends(require_session)):
    return await request.app.state.gis_client.feature(str(feature_id))
