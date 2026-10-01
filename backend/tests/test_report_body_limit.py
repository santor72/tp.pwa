import json

from fastapi import Request
import pytest

from app.main import MAX_CONNECTION_COMPLETION_BODY_BYTES, request_logging


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/api/gis/reports', '/api/tickets/1/connection-completion', '/api/tickets/1/ticket-completion'])
async def test_oversized_photo_request_returns_json_before_parsing_body(path):
    request = Request({
        'type': 'http', 'method': 'POST', 'path': path,
        'headers': [(b'content-length', str(MAX_CONNECTION_COMPLETION_BODY_BYTES + 1).encode())],
    })

    async def do_not_read_body(_):
        pytest.fail('oversized request should be rejected before parsing')

    response = await request_logging(request, do_not_read_body)
    assert response.status_code == 413
    assert json.loads(response.body)['code'] == 'REPORT_SIZE_LIMIT'
