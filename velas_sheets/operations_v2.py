"""Diário v2: compara somente lançamentos envolvidos, localizados por identidade."""
import copy
import secrets
from pathlib import Path

from .audit import check_month, locate_total, normalize, report_coverage
from .finance import TECH_HEADERS, update
from .installments import HEADERS, LEDGER, values
from .model import HEADERS as FC_HEADERS, column, find_sheet, validation_options

MONTH_COLUMNS = [0, 1, 2, 3, 4, 5, 7, 8]


def entered(cells, columns):
    return [copy.deepcopy(cells[c].get('userEnteredValue')) for c in columns]


def compile_plan(plan):
    """Descarta snapshots de linhas alheias; mantém valores anteriores para revisão."""
    result = {**plan, 'version': 2, 'steps': []}
    for step in plan['steps']:
        tables = []
        for block in step['blocks']:
            ledger = block['title'] == LEDGER
            cols = list(range(16)) if ledger else MONTH_COLUMNS
            header_cols = list(range(16)) if ledger else [7, 8]
            header_before = block['before'][0] if block['before'] else [{}] * block['width']
            actions = []
            for offset, after in enumerate(block['after'][1:], 1):
                row = block['first'] + offset
                if not ledger and row == block['last']:
                    continue
                before = block['before'][offset] if block['before'] else [{}] * block['width']
                bv, av = entered(before, cols), entered(after, cols)
                touched = any(s['planilha'] == step['book'] and s['aba'] == block['title'] and s['linha'] == row
                              for s in plan['summary'])
                if bv == av and not touched:
                    continue
                identity = values(after)[0 if ledger else 7]
                kind = 'cadastro' if ledger else values(after)[8]
                actions.append({'id': identity, 'type': kind, 'row': row, 'columns': cols,
                                'before': bv if values(before)[0 if ledger else 7] else None, 'after': av})
            tables.append({'title': block['title'], 'sheet_id': block['sheet_id'],
                'new': block['before'] is None, 'last': block['last'], 'width': block['width'],
                'header_row': block['first'], 'header_columns': header_cols,
                'header_before': entered(header_before, header_cols),
                'header_after': entered(block['after'][0], header_cols), 'actions': actions})
        result['steps'].append({'book': step['book'], 'tables': tables,
            'setup': [r for r in step['requests'] if 'updateCells' not in r]})
    return result


def expected(action, side, row, ledger):
    vals = copy.deepcopy(action[side])
    if vals is not None and ledger and vals[14] and 'formulaValue' in vals[14]:
        # Ordenar/mover a linha adapta referências relativas do status.
        old = action['row']
        formula = vals[14]['formulaValue']
        import re
        vals[14]['formulaValue'] = re.sub(rf'\b([MJ]){old}\b', lambda m: f'{m[1]}{row}', formula)
    return vals


def equal(a, b):
    if a is None or b is None:
        return a == b
    def canonical(v):
        return {'formulaValue': normalize(v['formulaValue'])} if v and 'formulaValue' in v else v
    return [canonical(v) for v in a] == [canonical(v) for v in b]


def empty(cells, columns):
    return not any(cells[c].get(k) for c in columns for k in ('userEnteredValue', 'note', 'chipRuns'))


def inspect_step(client, step, skip_balance_check=False):
    """Prévia de retomada. Não modifica o diário nem envia gravações."""
    meta = client.metadata()
    names = {s['properties']['title']: s for s in meta['sheets']}
    reports, options, findings, requests, locations = {}, {}, [], [], []
    states = []
    for table in step['tables']:
        title, ledger = table['title'], table['title'] == LEDGER
        exists = title in names
        if not exists and not table['new']:
            raise ValueError(f'{title}: aba removida.')
        if exists:
            sheet = names[title]
            props = sheet['properties']
            if props['sheetId'] != table['sheet_id']:
                raise ValueError(f'{title}: ID da aba mudou.')
            grid = props['gridProperties']
            if grid['columnCount'] < table['width']:
                raise ValueError(f'{title}: colunas insuficientes.')
            if ledger:
                if grid['rowCount'] > 10000:
                    raise ValueError('Parcelas: limite de 10000 linhas.')
                last = grid['rowCount']
            else:
                last = locate_total(client, title, grid['rowCount'])
                if not skip_balance_check:
                    check_month(client, title, last)
                report_coverage(client, meta, title, last, reports)
            rows = client.cells(title, table['header_row'], last, 0, table['width'])
            if not ledger and values(rows[0][:7]) != FC_HEADERS:
                raise ValueError(f'{title}: cabeçalhos diferentes do modelo.')
        else:
            last = table['last']
            sheet = {'merges': []}
            rows = [[{} for _ in range(table['width'])] for _ in range(last - table['header_row'] + 1)]
        header = entered(rows[0], table['header_columns'])
        hb, ha = table['header_before'], table['header_after']
        header_state = 'unchanged' if equal(hb, ha) and equal(header, ha) else 'after' if equal(header, ha) else 'before' if equal(header, hb) else 'conflict'
        states.append(header_state)
        if header_state == 'conflict':
            findings.append({'aba': title, 'id': '@cabecalho', 'estado': 'conflict', 'anterior': hb, 'esperado': ha, 'atual': header})
        if header_state == 'before':
            if not ledger:
                # A reserva de H:I ainda precisa estar inteiramente livre.
                for start in range(1, grid['rowCount'] + 1, 250):
                    if any(c.get(k) for r in client.cells(title, start, min(start+249, grid['rowCount']), 7, 9)
                           for c in r for k in ('userEnteredValue', 'note', 'chipRuns', 'dataValidation')):
                        raise ValueError(f'{title}: H:I ocupadas antes da reserva.')
            for col, val in zip(table['header_columns'], ha):
                requests.append(update(table['sheet_id'], table['header_row'], col, [{'userEnteredValue': val} if val else {}]))
        if header_state != 'unchanged':
            findings.append({'aba': title, 'id': '@cabecalho', 'estado': header_state})
        # Inclui a criação de aba no diagnóstico, mesmo se valores ainda estiverem vazios.
        if table['new']:
            states.append('after' if exists else 'before')
            findings.append({'aba': title, 'id': '@aba', 'estado': 'after' if exists else 'before'})
        reserved = set()
        candidates = list(enumerate(rows[1:] if ledger else rows[1:-1], table['header_row']+1))
        for action in table['actions']:
            identity_columns = [0] if ledger else [7, 8]
            key = [action['id']] if ledger else [action['id'], action['type']]
            found = [(r, c) for r, c in candidates if values([c[i] for i in identity_columns]) == key]
            if len(found) > 1:
                raise ValueError(f'{title}: ID duplicado {action["id"]} ({action["type"]}).')
            if found:
                row, cells = found[0]
            elif action['before'] is not None:
                findings.append({'aba': title, 'id': action['id'], 'tipo': action['type'], 'estado': 'conflict', 'motivo': 'ID desapareceu'})
                states.append('conflict')
                continue
            else:
                free = [(r,c) for r,c in candidates if r not in reserved and empty(c, action['columns'])]
                if not free:
                    raise ValueError(f'{title}: sem espaço para {action["id"]}.')
                row, cells = next(((r,c) for r,c in free if r == action['row']), free[0])
            reserved.add(row)
            before, after = expected(action, 'before', row, ledger), expected(action, 'after', row, ledger)
            current = entered(cells, action['columns'])
            state = 'unchanged' if equal(before, after) and equal(current, after) else 'after' if equal(current, after) else 'before' if equal(current, before) or (before is None and not found) else 'conflict'
            states.append(state)
            for col, bv, av, cv in zip(action['columns'], before or [None]*len(after), after, current):
                findings.append({'aba': title, 'id': action['id'], 'tipo': action['type'], 'linha': row,
                    'campo': (HEADERS if ledger else FC_HEADERS + TECH_HEADERS)[col],
                    'celula': f'{column(col)}{row}', 'anterior': bv, 'esperado': av, 'atual': cv, 'estado': state})
            if state == 'conflict':
                continue
            for merged in sheet.get('merges', []):
                if merged.get('startRowIndex', 0) <= row-1 < merged.get('endRowIndex', last) and merged.get('startColumnIndex', 0) < table['width']:
                    raise ValueError(f'{title}, linha {row}: mesclagem no lançamento.')
            for col, val in zip(action['columns'], after):
                rule = cells[col].get('dataValidation')
                if cells[col].get('chipRuns'):
                    raise ValueError(f'{title}, linha {row}: chip no lançamento.')
                if rule and val and 'formulaValue' not in val:
                    import json
                    key_rule = json.dumps(rule, sort_keys=True)
                    if key_rule not in options:
                        options[key_rule] = validation_options(client, meta, rule)
                    if next(iter(val.values())) not in options[key_rule]:
                        raise ValueError(f'{title}!{column(col)}{row}: valor fora da validação.')
                if state == 'before' and not equal([cells[col].get('userEnteredValue')], [val]):
                    requests.append(update(table['sheet_id'], row, col, [{'userEnteredValue': val} if val else {}]))
            locations.append((action, row, before, after))
    setup = []
    # Sheets aplica todo o batch de um arquivo atomicamente. Setup somente onde falta.
    for request in step['setup']:
        if 'addSheet' in request:
            if request['addSheet']['properties']['title'] not in names:
                setup.append(request)
        elif requests:
            setup.append(request)
    return {'requests': setup + requests, 'states': states, 'findings': findings, 'locations': locations}


def inspect(factory, step, skip_balance_check=False):
    try:
        return inspect_step(factory(step['book']), step, skip_balance_check)
    except ValueError as error:
        return {'states': ['conflict'], 'requests': [], 'locations': [],
                'findings': [{'estado': 'conflict', 'motivo': str(error)}]}


def ensure_safe(result, step):
    if 'conflict' in result['states'] or (step.get('done') and any(s == 'before' for s in result['states'])):
        raise RuntimeError(f'Conflito no arquivo {step["book"]}. Execute diagnosticar-operacao; diário preservado.')


def apply(plan, directory, factory, path=None):
    from .operations import pending, save
    directory = Path(directory)
    if path is None:
        if pending(directory):
            raise ValueError('Há uma operação pendente. Execute retomar antes de outra gravação.')
        if not plan['steps']:
            return None
        for step in plan['steps']:
            ensure_safe(inspect(factory, step, plan.get('skip_balance_check', False)), step)
        path = directory / (secrets.token_hex(12) + '.json')
        save(path, plan)
    # Falha em um destino posterior não deve deixar novas gravações nos anteriores.
    for step in plan['steps']:
        ensure_safe(inspect(factory, step, plan.get('skip_balance_check', False)), step)
    for step in plan['steps']:
        result = inspect(factory, step, plan.get('skip_balance_check', False))
        ensure_safe(result, step)
        for action, row, before, after in result['locations']:
            action.update(row=row, before=before, after=after)
        step['requests'] = result['requests']
        # Reposicionamento e pedidos recalculados ficam duráveis ANTES do POST.
        save(path, plan)
        if result['requests']:
            factory(step['book']).batch(result['requests'])
            result = inspect(factory, step, plan.get('skip_balance_check', False))
            ensure_safe(result, step)
            if result['requests']:
                raise RuntimeError(f'Gravação não conferida em {step["book"]}; diário preservado em {path}.')
        step['done'] = True
        save(path, plan)
    plan['state'] = 'complete'
    save(path, plan)
    return path


def diagnose(plan, factory):
    return [{'planilha': step['book'], **finding} for step in plan['steps']
            for finding in inspect(factory, step, plan.get('skip_balance_check', False))['findings']]


def cancellable(plan, factory):
    for step in plan['steps']:
        result = inspect(factory, step, plan.get('skip_balance_check', False))
        if step.get('done') or any(s not in ('before', 'unchanged') for s in result['states']):
            return False
    return True
