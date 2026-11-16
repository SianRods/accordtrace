from fastapi.testclient import TestClient

from accordtrace_backend.api.app import create_app
from accordtrace_backend.core import Settings


def test_import_health_and_auth_do_not_need_services():
    with TestClient(create_app(Settings(api_token='test-only'))) as client:
        response=client.get('/health/live')
        assert response.json()=={'status':'alive'}
        assert response.headers['X-Request-ID']
        assert client.get('/api/v1/documents').status_code==401
        assert client.post('/api/v1/admin/ingestions',json={}).status_code==401
        assert client.get('/api/v1/evidence/not-a-uuid',headers={'Authorization':'Bearer test-only'}).status_code==422


def test_unconfigured_credential_is_not_anonymous_mode():
    with TestClient(create_app(Settings(api_token=''))) as client:
        assert client.get('/api/v1/documents').status_code==503


def test_ingestion_rejects_unknown_paths_before_work():
    with TestClient(create_app(Settings(api_token='test-only'))) as client:
        response=client.post('/api/v1/admin/ingestions',json={'manifest':'../../secret'},
            headers={'Authorization':'Bearer test-only','Idempotency-Key':'test'})
        assert response.status_code==422
