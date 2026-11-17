from fastapi.testclient import TestClient
from accordtrace_backend.api.app import create_app
from accordtrace_backend.core import Settings


def test_phase2_auth_and_validation_do_not_call_providers():
    headers={'Authorization':'Bearer test-only'}
    with TestClient(create_app(Settings(api_token='test-only'))) as client:
        assert client.post('/api/v1/search',json={'query':'q','agreement_ids':['contract_0']}).status_code==401
        assert client.get('/api/v1/snapshots').status_code==401
        assert client.post('/api/v1/search',headers=headers,json={'query':'q','agreement_ids':[]}).status_code==422
        assert client.post('/api/v1/admin/index-builds',headers=headers,
            json={'base_snapshot_id':'bad'},).status_code==422
        assert client.post('/api/v1/admin/snapshots/not-a-uuid/activate',headers=headers,
            json={'expected_generation':0}).status_code==422
