"""Explicit asset preparation, versioned embeddings and no silent paid fallback."""
import json
import math
import time
from importlib.metadata import version

from tokenizers import Tokenizer

from accordtrace_backend.indexing.chunking import CHUNKER, fingerprint
from accordtrace_backend.common.core import DomainError, digest

TOKENIZER_REPO='BAAI/bge-small-en-v1.5'
TOKENIZER_REVISION='ceaf0832d92c2e84b1f50f5122a9310fd2214019'
SPARSE=dict(model='Qdrant/bm25',k=1.2,b=0.75,avg_len=256.0,language='english')


def sparse_model(settings, download=False, manifest=None):
    from fastembed import SparseTextEmbedding
    if not download:
        manifest,_=assets(settings,manifest)
        path=settings.runtime/'models/bundles'/fingerprint(manifest)/'bm25'
        return SparseTextEmbedding(model_name=SPARSE['model'],cache_dir=str(settings.runtime/'models/bm25'),
            specific_model_path=str(path),local_files_only=True,k=SPARSE['k'],b=SPARSE['b'],avg_len=SPARSE['avg_len'],language='english')
    return SparseTextEmbedding(model_name=SPARSE['model'],cache_dir=str(settings.runtime/'models/bm25'),
        local_files_only=not download,k=SPARSE['k'],b=SPARSE['b'],avg_len=SPARSE['avg_len'],language='english')


def prepare_assets(settings):
    from huggingface_hub import hf_hub_download
    directory=settings.runtime/'models'
    directory.mkdir(parents=True,exist_ok=True)
    path=hf_hub_download(TOKENIZER_REPO,'tokenizer.json',revision=TOKENIZER_REVISION,
                         cache_dir=str(directory/'hf'))
    target=directory/'tokenizer.json'
    target.write_bytes(__import__('pathlib').Path(path).read_bytes())
    sparse=sparse_model(settings,download=True)
    sparse_directory=sparse.model._model_dir
    source_files={p.name:p for p in sparse_directory.glob('*.txt') if p.is_file()}
    files={name:digest(path.read_bytes()) for name,path in sorted(source_files.items())}
    if not files: raise DomainError('bm25_assets_missing')
    manifest=dict(version='asset-bundle-v1',tokenizer_sha=digest(target.read_bytes()),tokenizer_revision=TOKENIZER_REVISION,
                  sparse_revision=sparse_directory.name,
                  sparse_files=files,fastembed_version=version('fastembed'),tokenizers_version=version('tokenizers'))
    from accordtrace_backend.ingestion.pipeline import immutable_write
    bundle=directory/'bundles'/fingerprint(manifest)
    immutable_write(bundle/'tokenizer.json',target.read_bytes())
    for name,path in source_files.items():immutable_write(bundle/'bm25'/name,path.read_bytes())
    immutable_write(bundle/'assets.json',json.dumps(manifest,sort_keys=True,indent=2).encode())
    (directory/'assets.json').write_text(json.dumps(manifest,sort_keys=True,indent=2),encoding='utf-8')
    return manifest


def assets(settings, manifest=None):
    directory=settings.runtime/'models'
    try:
        manifest=manifest or json.loads((directory/'assets.json').read_text())
        bundle=directory/'bundles'/fingerprint(manifest)
        if json.loads((bundle/'assets.json').read_text())!=manifest:raise DomainError('asset_bundle_changed')
        if digest((bundle/'tokenizer.json').read_bytes())!=manifest['tokenizer_sha']:
            raise DomainError('tokenizer_asset_changed')
        for file,sha in manifest['sparse_files'].items():
            if digest((bundle/'bm25'/file).read_bytes())!=sha:raise DomainError('sparse_asset_changed')
        if manifest['fastembed_version']!=version('fastembed') or manifest['tokenizers_version']!=version('tokenizers'):
            raise DomainError('asset_library_version_changed')
        return manifest,Tokenizer.from_file(str(bundle/'tokenizer.json'))
    except (FileNotFoundError,KeyError,json.JSONDecodeError):raise DomainError('index_assets_not_prepared',503) from None


def configuration(settings, verification='live'):
    manifest,_=assets(settings)
    profile=dict(provider='gemini',model='gemini-embedding-001',dimensions=768,normalization='l2-v1',
        document_task='RETRIEVAL_DOCUMENT',query_task='RETRIEVAL_QUERY',preprocessing='whitespace-v1',
        verification=verification,epoch='v1')
    return dict(version='search-v1',parser='structural-v3',graph='structural-graph-v2',
                chunker=CHUNKER,assets=manifest,sparse=SPARSE,embedding=profile)


def validate_vector(vector,dimension=768):
    if len(vector)!=dimension or not all(isinstance(v,(float,int)) and not isinstance(v,bool) and math.isfinite(v) for v in vector):
        raise DomainError('invalid_embedding_vector')
    norm=math.hypot(*(float(v) for v in vector))
    if not math.isfinite(norm):raise DomainError('invalid_embedding_norm')
    if norm==0:raise DomainError('zero_embedding_vector')
    return [float(v)/norm for v in vector]


def retry_seconds(error):
    """Honor Google's structured RetryInfo as well as the HTTP header."""
    response=getattr(error,'response',None)
    value=getattr(response,'headers',{}).get('retry-after','0')
    try:delay=float(value)
    except (ValueError,TypeError):delay=0
    def visit(value):
        nonlocal delay
        if isinstance(value,dict):
            if 'retryDelay' in value:
                try:delay=max(delay,float(str(value['retryDelay']).removesuffix('s')))
                except (ValueError,TypeError):pass
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
    visit(getattr(error,'details',None))
    visit(getattr(error,'response_json',None))
    return delay


class Gemini:
    def __init__(self,settings):
        self.settings=settings
        self.last_call=0.0
        self.heartbeat=lambda:None
        self.query_timeout_seconds=5

    def wait(self,seconds):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            self.heartbeat()
            time.sleep(min(5,max(0,deadline-time.monotonic())))

    def embed(self,inputs,task):
        if not self.settings.gemini_api_key.get_secret_value():raise DomainError('gemini_credentials_missing',503)
        if not self.settings.gemini_free_tier_confirmed:raise DomainError('gemini_free_tier_not_confirmed',503)
        if any(len(value.encode())>1536 for value in inputs):raise DomainError('embedding_input_too_large',422)
        from google import genai
        from google.genai import types,errors
        attempts=1 if task=='RETRIEVAL_QUERY' else 3
        for attempt in range(attempts):
            if task!='RETRIEVAL_QUERY':
                self.wait(max(0,60/self.settings.embedding_requests_per_minute-(time.monotonic()-self.last_call)))
            try:
                self.heartbeat()
                self.last_call=time.monotonic()
                with genai.Client(api_key=self.settings.gemini_api_key.get_secret_value(),
                    http_options=types.HttpOptions(timeout=(int(self.query_timeout_seconds*1000) if task=='RETRIEVAL_QUERY' else self.settings.embedding_timeout_seconds*1000),
                                                   retry_options=types.HttpRetryOptions(attempts=1))) as client:
                    response=client.models.embed_content(model='gemini-embedding-001',contents=inputs,
                        config=types.EmbedContentConfig(task_type=task,output_dimensionality=768))
                if not response.embeddings or len(response.embeddings)!=len(inputs):raise DomainError('embedding_count_mismatch')
                return [validate_vector(e.values) for e in response.embeddings]
            except errors.APIError as exc:
                if exc.code not in (429,500,502,503,504) or attempt+1==attempts:
                    raise DomainError('embedding_quota_exhausted' if exc.code==429 else 'embedding_provider_unavailable',503) from None
                import random
                delay=retry_seconds(exc)
                if delay>30:raise DomainError('embedding_quota_exhausted',503) from None
                self.wait(max(delay,min(15,2**attempt+random.random())))
            except DomainError:raise
            except Exception:raise DomainError('embedding_provider_unavailable',503) from None


def cached_embeddings(db_engine,profile,inputs,task,provider,heartbeat=lambda:None):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from sqlalchemy.dialects.postgresql import insert
    from accordtrace_backend.persistence.storage import EmbeddingCache, EmbeddingProfile
    pid=fingerprint(profile)
    with Session(db_engine) as db,db.begin():
        db.execute(insert(EmbeddingProfile).values(id=pid,configuration=profile).on_conflict_do_nothing())
    keys=[fingerprint(dict(profile=pid,input=digest(value.encode()),task=task)) for value in inputs]
    found={}
    with Session(db_engine) as db:
        for row in db.scalars(select(EmbeddingCache).where(EmbeddingCache.id.in_(set(keys)))):
            found[row.id]=validate_vector(row.vector,profile['dimensions'])
    missing=list(dict.fromkeys(k for k in keys if k not in found))
    by_key=dict(zip(keys,inputs))
    def complete(batch):
        # Recheck after acquiring the shared provider lock: another worker may
        # have populated these exact inputs while this one was waiting.
        with Session(db_engine) as db:
            for row in db.scalars(select(EmbeddingCache).where(EmbeddingCache.id.in_(batch))):
                found[row.id]=validate_vector(row.vector,profile['dimensions'])
        batch=[key for key in batch if key not in found]
        if not batch:return
        heartbeat()
        vectors=provider.embed([by_key[k] for k in batch],task)
        if len(vectors)!=len(batch):raise DomainError('embedding_count_mismatch')
        heartbeat()
        rows=[]
        for key,vector in zip(batch,vectors):
            normalized=validate_vector(vector,profile['dimensions'])
            rows.append(dict(id=key,profile_id=pid,input_hash=digest(by_key[key].encode()),task=task,vector=normalized))
            found[key]=normalized
        with Session(db_engine) as db,db.begin():
            db.execute(insert(EmbeddingCache).values(rows).on_conflict_do_nothing())
    for offset in range(0,len(missing),16):
        batch=missing[offset:offset+16]
        heartbeat()
        if isinstance(provider,Gemini):provider.heartbeat=heartbeat
        # One hosted batch across worker processes. Interactive requests degrade
        # rather than wait behind an indexing batch.
        if isinstance(provider,Gemini):
            from sqlalchemy import text
            with db_engine.connect() as lock:
                deadline=time.monotonic()+30
                while not lock.execute(text('SELECT pg_try_advisory_lock(71240612)')).scalar():
                    if task=='RETRIEVAL_QUERY' or time.monotonic()>deadline:raise DomainError('embedding_provider_busy',503)
                    heartbeat();time.sleep(1)
                try:complete(batch)
                finally:lock.execute(text('SELECT pg_advisory_unlock(71240612)'))
        else:complete(batch)
    return [found[key] for key in keys]
