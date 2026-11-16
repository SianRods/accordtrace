"""Generate local-only credentials once, without printing or overwriting them."""
import secrets
from pathlib import Path


def main():
    backend = Path(__file__).resolve().parents[1]
    destination = backend / '.env'
    (backend / '.runtime').mkdir(exist_ok=True)
    if destination.exists():
        print('backend/.env already exists; preserved existing credentials.')
        return
    postgres, neo4j, token = (secrets.token_hex(24) for _ in range(3))
    template = (backend / '.env.example').read_text(encoding='utf-8')
    lines = []
    for line in template.splitlines():
        if line.startswith(('POSTGRES_PASSWORD=', 'ACCORDTRACE_DATABASE_URL=')):
            line = line.replace('REPLACE_WITH_RANDOM_PASSWORD', postgres)
        elif line.startswith(('NEO4J_PASSWORD=', 'ACCORDTRACE_NEO4J_PASSWORD=')):
            line = line.replace('REPLACE_WITH_RANDOM_PASSWORD', neo4j)
        elif line.startswith('ACCORDTRACE_API_TOKEN='):
            line = line.replace('REPLACE_WITH_RANDOM_TOKEN', token)
        lines.append(line)
    with destination.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write('\n'.join(lines) + '\n')
    print('Created ignored backend/.env and backend/.runtime; credentials were not printed.')


if __name__ == '__main__':
    main()
