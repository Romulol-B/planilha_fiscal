"""Diário durável, verificação otimista e retomada de batches por arquivo."""
import json
import os
from pathlib import Path
import secrets

from .finance import stable
from .installments import values


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    if os.name != 'nt':
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def pending(directory):
    return [p for p in sorted(directory.glob('*.json'))
            if json.loads(p.read_text(encoding='utf-8')).get('state') != 'complete']


def matches(client, step, side):
    meta = client.metadata()
    sheets = {s['properties']['title']: s['properties'] for s in meta['sheets']}
    for guard in step.get('guards', []):
        if guard['title'] not in sheets or stable(client.cells(guard['title'], guard['first'], guard['last'],
                guard['start_col'], guard['end_col'])) != guard['rows']:
            return False
    for title, props in step['sheets'].items():
        if props is None:
            if side == 'before' and title in sheets:
                return False
            if side == 'after' and title not in sheets:
                return False
        elif title not in sheets or sheets[title]['sheetId'] != props['sheetId'] or sheets[title]['gridProperties'] != props['gridProperties']:
            return False
    for b in step['blocks']:
        if side == 'after' and sheets[b['title']]['sheetId'] != b['sheet_id']:
            return False
        expected = b[side]
        if expected is None:
            continue
        current = stable(client.cells(b['title'], b['first'], b['last'], 0, b['width']))
        if b['before'] is None:
            # A API pode acrescentar propriedades de formatação na nova aba.
            if [values(r) for r in current] != [values(r) for r in expected]:
                return False
        elif current != expected:
            return False
    return True


def apply(plan, directory, factory, path=None):
    directory = Path(directory)
    if path is None:
        if pending(directory):
            raise ValueError('Há uma operação pendente. Execute retomar --aplicar antes de outra gravação.')
        if not plan['steps']:
            return None
        # Preflight de TODOS os arquivos antes de registrar e iniciar a operação.
        for step in plan['steps']:
            if not matches(factory(step['book']), step, 'before'):
                raise RuntimeError('A planilha mudou durante a prévia. Nenhuma gravação iniciada.')
        path = directory / (secrets.token_hex(12) + '.json')
        save(path, plan)
    for step in plan['steps']:
        client = factory(step['book'])
        if matches(client, step, 'after'):
            step['done'] = True
            save(path, plan)
            continue
        if step.get('done'):
            raise RuntimeError(f'Arquivo já concluído foi alterado: {step["book"]}. Revisão manual necessária.')
        if not matches(client, step, 'before'):
            raise RuntimeError(f'Conflito no arquivo {step["book"]}. Diário preservado em {path}.')
        # Não repetir automaticamente um POST após timeout. retomar verifica o resultado.
        client.batch(step['requests'])
        if not matches(client, step, 'after'):
            raise RuntimeError(f'Conferência da gravação divergiu. Diário preservado em {path}.')
        step['done'] = True
        save(path, plan)
    plan['state'] = 'complete'
    save(path, plan)
    return path
