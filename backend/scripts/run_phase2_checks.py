"""Opt-in real-service checks in a separate PostgreSQL database, never a reset."""
import os
import subprocess
import sys

from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url

from accordtrace_backend.common.core import Settings


def main():
    settings=Settings()
    url=make_url(settings.database_url.get_secret_value())
    database='accordtrace_phase2_checks'
    admin=create_engine(url.set(database='postgres'),isolation_level='AUTOCOMMIT',hide_parameters=True)
    with admin.connect() as conn:
        if not conn.execute(text('SELECT 1 FROM pg_database WHERE datname=:name'),dict(name=database)).scalar():
            conn.exec_driver_sql('CREATE DATABASE accordtrace_phase2_checks')
    admin.dispose()
    env=dict(os.environ,ACCORDTRACE_DATABASE_URL=url.set(database=database).render_as_string(hide_password=False),
             ACCORDTRACE_PHASE2_INTEGRATION='1')
    commands=[['-m','alembic','-c','backend/alembic.ini','upgrade','head'],
              ['-m','pytest','-q','backend/tests/integration/test_phase2_live.py','--basetemp=/tmp/accordtrace-phase2-pytest']]
    for command in commands:
        result=subprocess.run([sys.executable,*command],env=env,check=False)
        if result.returncode:return result.returncode
    return 0


if __name__=='__main__':sys.exit(main())
