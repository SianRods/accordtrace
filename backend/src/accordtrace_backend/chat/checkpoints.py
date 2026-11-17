"""Explicit PostgreSQL checkpointer setup; never runs during API startup."""
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from sqlalchemy import text
from sqlalchemy.engine import make_url
from langgraph.checkpoint.postgres import PostgresSaver

SCHEMA='accordtrace_chat_checkpoint'


def dsn(settings):
    return make_url(settings.database_url.get_secret_value()).set(drivername='postgresql').render_as_string(hide_password=False)


@contextmanager
def saver(settings):
    with psycopg.connect(dsn(settings),autocommit=True,prepare_threshold=0,row_factory=dict_row,
                         connect_timeout=3,options='-c search_path='+SCHEMA+',public') as connection:
        yield PostgresSaver(connection)


def setup(settings,db_engine):
    with db_engine.begin() as connection:connection.execute(text('CREATE SCHEMA IF NOT EXISTS '+SCHEMA))
    with saver(settings) as checkpoint:checkpoint.setup()


def ready(db_engine):
    try:
        with db_engine.connect() as connection:
            return all(connection.execute(text('SELECT to_regclass(:name)'),{'name':SCHEMA+'.'+name}).scalar()
                for name in ('checkpoints','checkpoint_blobs','checkpoint_writes','checkpoint_migrations'))
    except Exception:return False
