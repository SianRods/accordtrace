"""Explicit snapshot collections, acknowledged writes and exact-set validation."""
from qdrant_client import QdrantClient, models as m
from contextlib import closing

from accordtrace_backend.common.core import DomainError, identity


def client(settings):
    return closing(QdrantClient(url=settings.qdrant_url,timeout=5,check_compatibility=False))


def collection_name(snapshot_id):return 'accordtrace_'+snapshot_id.replace('-','')


def point_id(snapshot_id,chunk_id):return identity(snapshot_id,'point',chunk_id)


def create(conn,name,dimension):
    if not conn.collection_exists(name):
        conn.create_collection(name,vectors_config={'dense':m.VectorParams(size=dimension,distance=m.Distance.COSINE)},
            sparse_vectors_config={'bm25':m.SparseVectorParams(modifier=m.Modifier.IDF)})
        for key in ('snapshot_id','agreement_id','document_version_id','chunk_id','parent_span_id'):
            conn.create_payload_index(name,key,field_schema=m.PayloadSchemaType.KEYWORD,wait=True)
    info=conn.get_collection(name)
    if info.config.params.vectors['dense'].size!=dimension or info.config.params.vectors['dense'].distance!=m.Distance.COSINE or info.config.params.sparse_vectors['bm25'].modifier!=m.Modifier.IDF:
        raise DomainError('collection_profile_mismatch')


def scope(snapshot_id,agreements,documents):
    return m.Filter(must=[m.FieldCondition(key='snapshot_id',match=m.MatchValue(value=snapshot_id)),
        m.FieldCondition(key='agreement_id',match=m.MatchAny(any=list(agreements))),
        m.FieldCondition(key='document_version_id',match=m.MatchAny(any=list(documents)))])


def upsert(conn,name,sid,rows,dense,sparse,docs,parents):
    points=[]
    for row,dv,sv in zip(rows,dense,sparse):
        doc=docs[row.document_id];parent=parents[row.parent_id]
        points.append(m.PointStruct(id=point_id(sid,row.id),vector={'dense':dv,'bm25':m.SparseVector(
            indices=sv.indices.tolist(),values=sv.values.tolist())},payload=dict(snapshot_id=sid,
            chunk_id=row.id,parent_span_id=row.parent_id,document_version_id=doc.id,agreement_id=doc.agreement_id,
            start=row.start,end=row.end,text_hash=row.text_hash,section_path=parent.label,unit_type=parent.role,is_toc=False)))
    conn.upsert(name,points=points,wait=True)


def reconcile(conn,name,sid,rows,profile,documents):
    expected={point_id(sid,r.id):r for r in rows};seen=set();offset=None
    while True:
        points,offset=conn.scroll(name,limit=256,offset=offset,with_payload=True,with_vectors=True)
        for point in points:
            key=str(point.id);row=expected.get(key)
            if row is None or key in seen:raise DomainError('unexpected_vector_point')
            seen.add(key)
            payload=point.payload
            if any(payload.get(k)!=v for k,v in dict(snapshot_id=sid,chunk_id=row.id,parent_span_id=row.parent_id,
                document_version_id=row.document_id,agreement_id=documents[row.document_id].agreement_id,
                start=row.start,end=row.end,text_hash=row.text_hash,is_toc=False).items()):
                raise DomainError('point_payload_mismatch')
            from accordtrace_backend.indexing.embedding import validate_vector
            vector=point.vector['dense']
            validate_vector(vector,profile['dimensions'])
            if abs(sum(v*v for v in vector)-1)>0.001:raise DomainError('vector_normalization_failed')
            sparse=point.vector.get('bm25')
            if sparse is None:raise DomainError('missing_sparse_vector')
            import math
            if len(sparse.indices)!=len(sparse.values) or len(set(sparse.indices))!=len(sparse.indices) or not all(math.isfinite(v) for v in sparse.values):
                raise DomainError('invalid_sparse_vector')
        if offset is None:break
    if seen!=set(expected):raise DomainError('vector_membership_mismatch')
    return dict(points=len(seen),collection=name)
