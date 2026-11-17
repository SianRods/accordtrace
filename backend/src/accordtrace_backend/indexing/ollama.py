"""Explicit local EmbeddingGemma inference; no pulls or cloud fallback."""
from urllib.parse import urlsplit
import httpx
from accordtrace_backend.common.core import DomainError

DOCUMENT_PROMPT='title: none | text: '
QUERY_PROMPT='task: search result | query: '


class Ollama:
    def __init__(self,settings,profile=None):
        self.settings,self.profile=settings,profile
        self.heartbeat=lambda:None
        self.before_call=lambda:None
        self.query_timeout_seconds=5
        parsed=urlsplit(settings.ollama_url)
        if parsed.scheme not in ('http','https') or parsed.hostname not in ('127.0.0.1','localhost','::1','host.docker.internal') or parsed.username or parsed.password:
            raise DomainError('ollama_url_must_be_local')
    def metadata(self,client):
        version=client.get('/api/version');version.raise_for_status()
        tags=client.get('/api/tags');tags.raise_for_status()
        name=self.profile['model'] if self.profile else self.settings.ollama_embedding_model
        model=next((item for item in tags.json()['models'] if item['name']==name),None)
        if model is None:raise DomainError('ollama_model_not_installed',503)
        return model,version.json()['version']
    def describe(self):
        try:
            with httpx.Client(base_url=self.settings.ollama_url,timeout=5,trust_env=False) as client:
                model,version=self.metadata(client)
                details=client.post('/api/show',json={'model':model['name']});details.raise_for_status()
                if 'embedding' not in details.json().get('capabilities',[]):raise DomainError('ollama_model_not_embedding_capable')
            return dict(provider='ollama',model=model['name'],model_digest=model['digest'],runtime_version=version,
                dimensions=768,normalization='l2-v1',document_task='RETRIEVAL_DOCUMENT',query_task='RETRIEVAL_QUERY',
                document_prompt=DOCUMENT_PROMPT,query_prompt=QUERY_PROMPT,preprocessing='whitespace-v1',verification='live',epoch='v1')
        except DomainError:raise
        except Exception:raise DomainError('ollama_unavailable',503) from None
    def embed(self,inputs,task):
        from .embedding import validate_vector
        profile=self.profile
        if not profile or profile.get('provider')!='ollama' or profile.get('dimensions')!=768 or profile.get('document_prompt')!=DOCUMENT_PROMPT or profile.get('query_prompt')!=QUERY_PROMPT:
            raise DomainError('unsupported_ollama_profile')
        if task not in ('RETRIEVAL_DOCUMENT','RETRIEVAL_QUERY'):raise DomainError('unsupported_embedding_task')
        if any(len(text.encode())>1536 for text in inputs):raise DomainError('embedding_input_too_large',422)
        self.before_call();self.heartbeat()
        prefix=profile['query_prompt'] if task=='RETRIEVAL_QUERY' else profile['document_prompt']
        timeout=self.query_timeout_seconds if task=='RETRIEVAL_QUERY' else self.settings.ollama_embedding_timeout_seconds
        try:
            with httpx.Client(base_url=self.settings.ollama_url,timeout=timeout,trust_env=False) as client:
                model,version=self.metadata(client)
                if model['digest']!=profile['model_digest']:raise DomainError('ollama_model_digest_changed')
                if version!=profile['runtime_version']:raise DomainError('ollama_runtime_version_changed')
                response=client.post('/api/embed',json=dict(model=profile['model'],input=[prefix+text for text in inputs],
                    truncate=False,keep_alive=self.settings.ollama_keep_alive))
                response.raise_for_status();vectors=response.json().get('embeddings',[])
            if len(vectors)!=len(inputs):raise DomainError('embedding_count_mismatch')
            self.heartbeat()
            return [validate_vector(vector,profile['dimensions']) for vector in vectors]
        except DomainError:raise
        except Exception:raise DomainError('ollama_embedding_unavailable',503) from None
