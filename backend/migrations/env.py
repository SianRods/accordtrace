from alembic import context
from accordtrace_backend.common.core import Settings
from accordtrace_backend.persistence.storage import Base, engine
from accordtrace_backend.chat import models as chat_models

if context.is_offline_mode():
    context.configure(url=Settings().database_url.get_secret_value(),target_metadata=Base.metadata,literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
else:
    e=engine(Settings())
    with e.connect() as connection:
        context.configure(connection=connection,target_metadata=Base.metadata)
        with context.begin_transaction(): context.run_migrations()
    e.dispose()
