import hashlib
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, Response

from app.config import Settings, get_settings
from app.dependencies import require_gis_data_session
from app.errors import ApiError

router = APIRouter(prefix='/api/mobilemap', tags=['mobilemap'], dependencies=[Depends(require_gis_data_session)])


@router.get('/source')
async def source(settings: Settings = Depends(get_settings)):
    return JSONResponse({'source': settings.map_source}, headers={'Cache-Control': 'no-store'})


@router.get('/config')
async def config(request: Request):
    return JSONResponse(await request.app.state.mobilemap_client.json('config'), headers={'Cache-Control': 'no-store'})


@router.get('/maps')
async def maps(request: Request):
    return JSONResponse(await request.app.state.mobilemap_client.json('maps'), headers={'Cache-Control': 'no-store'})


@router.get('/maps/{map_id}/{operation}')
async def map_data(map_id: UUID, operation: Literal['layers', 'bounds', 'features'], request: Request,
                   bbox: str | None = Query(default=None, pattern=r'^-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?$'),
                   zoom: int = Query(default=15, ge=0, le=24), layers: str | None = Query(default=None, max_length=4096)):
    params = None
    if operation == 'features':
        if bbox is None:
            raise ApiError(422, 'BBOX_REQUIRED', 'Не указана область карты')
        params = {'bbox': bbox, 'zoom': zoom}
        if layers:
            try:
                params['layers'] = ','.join(str(UUID(item)) for item in layers.split(','))
            except ValueError as exc:
                raise ApiError(422, 'LAYERS_INVALID', 'Некорректные слои карты') from exc
    return JSONResponse(await request.app.state.mobilemap_client.json(f'maps/{map_id}/{operation}', params), headers={'Cache-Control': 'no-store'})


@router.get('/features/{feature_id}')
async def feature(feature_id: UUID, request: Request):
    return JSONResponse(await request.app.state.mobilemap_client.json(f'features/{feature_id}'), headers={'Cache-Control': 'no-store'})


@router.get('/maps/{map_id}/tiles/meta')
async def tile_meta(map_id: UUID, request: Request):
    return JSONResponse(await request.app.state.mobilemap_client.json(f'maps/{map_id}/tiles/meta'), headers={'Cache-Control': 'no-store'})


async def image_response(request: Request, path: str, media_type: str):
    upstream = await request.app.state.mobilemap_client.get(path)
    if upstream.headers.get('content-type', '').split(';')[0] != media_type:
        raise ApiError(502, 'MOBILEMAP_IMAGE_INVALID', 'Некорректное изображение карты')
    etag = '"' + hashlib.sha256(upstream.content).hexdigest() + '"'
    headers = {'Cache-Control': 'private, no-cache', 'ETag': etag, 'Vary': 'Cookie'}
    if request.headers.get('if-none-match') == etag:
        return Response(status_code=304, headers=headers)
    return Response(upstream.content, media_type=media_type, headers=headers)


@router.get('/assets/{asset_id}')
async def asset(asset_id: UUID, request: Request):
    return await image_response(request, f'assets/{asset_id}', 'image/png')


@router.get('/maps/{map_id}/tiles/lines/{layer}/{version}/{z}/{x}/{filename}')
async def tile(map_id: UUID, layer: Literal['network', 'poles'], request: Request,
               version: str = Path(pattern=r'^[a-zA-Z0-9_-]{1,64}$'), z: int = Path(ge=0, le=24),
               x: int = Path(ge=0), filename: str = Path(pattern=r'^\d+(?:@2x)?\.webp$')):
    y = int(filename.split('@')[0].split('.')[0])
    if x >= 2 ** z or y >= 2 ** z:
        raise ApiError(404, 'TILE_NOT_FOUND', 'Тайл не найден')
    return await image_response(request, f'maps/{map_id}/tiles/lines/{layer}/{version}/{z}/{x}/{filename}', 'image/webp')
