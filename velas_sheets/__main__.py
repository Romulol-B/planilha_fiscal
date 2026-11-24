import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tomllib

from .client import Client
from .model import (check_headers, find_sheet, load_edits, plan_edits,
                    stable_cells, verify_edits)


def load_config(path):
    with path.open('rb') as stream:
        raw = tomllib.load(stream)
        config = raw['google_sheets']
    config['anos'] = raw.get('anos', {})
    for year, book in config['anos'].items():
        if not re.fullmatch(r'\d{4}', str(year)) or not isinstance(book, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', book):
            raise ValueError('Configure [anos] com ano e ID de planilha válidos.')
    if 'principal_id' in config and not re.fullmatch(r'[A-Za-z0-9_-]+', config['principal_id']):
        raise ValueError('principal_id deve conter um ID de planilha.')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', config['planilha_id']):
        raise ValueError('planilha_id deve conter o ID, não a URL completa.')
    credentials = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') or config['credenciais']
    config['credenciais'] = path.resolve().parent / Path(credentials).expanduser()
    config['backups'] = path.resolve().parent / 'dados' / 'sheets_backups'
    if not config['aba_teste'].startswith('TESTE '):
        raise ValueError('aba_teste deve começar com "TESTE ".')
    if config['aba_teste'] == config['aba_modelo']:
        raise ValueError('Use abas diferentes para o modelo e o teste.')
    return config


def backup(config, title, snapshots, changes):
    directory = config['backups']
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = directory / f'{stamp}-{secrets.token_hex(4)}.json'
    payload = {'planilha_id': config['planilha_id'], 'aba': title,
               'linhas_antes': snapshots, 'alteracoes': changes}
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    return path


def prepare_test(client, metadata, config):
    title = config['aba_teste']
    if any(s['properties']['title'] == title for s in metadata['sheets']):
        check_headers(client.cells(title, 2, 2))
        print(f'Aba {title} já existe; conteúdo preservado.')
        return
    source = find_sheet(metadata, config['aba_modelo'])
    check_headers(client.cells(config['aba_modelo'], 2, 2))
    used_ids = {s['properties']['sheetId'] for s in metadata['sheets']}
    new_id = secrets.randbelow(2**31 - 1)
    while new_id in used_ids:
        new_id = secrets.randbelow(2**31 - 1)
    client.batch([
        {'duplicateSheet': {'sourceSheetId': source['properties']['sheetId'],
                            'newSheetId': new_id, 'newSheetName': title}},
        {'updateCells': {'range': {'sheetId': new_id, 'startRowIndex': 0, 'endRowIndex': 1,
                                   'startColumnIndex': 0, 'endColumnIndex': 1},
                         'rows': [{'values': [{'userEnteredValue': {'stringValue': 'TESTE CLI - Fluxo de caixa'}}]}],
                         'fields': 'userEnteredValue'}},
    ])
    find_sheet(client.metadata(), title)
    check_headers(client.cells(title, 2, 2))
    print(f'Aba {title} criada como cópia de {config["aba_modelo"]}, com fórmulas e validações.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Teste de leitura e edição do modelo financeiro no Google Sheets')
    parser.add_argument('--config', type=Path, default=Path('config.sheets.toml'))
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('conectar', help='Testa a leitura e lista as abas (não grava dados)')
    commands.add_parser('preparar-teste', help='Duplica a aba modelo em TESTE CLI, preservando a original')
    read = commands.add_parser('ler', help='Lê um trecho de A:G')
    read.add_argument('--aba', help='Padrão: aba de teste configurada')
    read.add_argument('--inicio', type=int, default=1)
    read.add_argument('--fim', type=int, default=10)
    edit = commands.add_parser('editar', help='Mostra as alterações de um JSON; grava somente com --aplicar')
    edit.add_argument('arquivo', type=Path)
    edit.add_argument('--aplicar', action='store_true')
    audit_command = commands.add_parser('auditar-modelo', help='Audita fórmulas e estrutura sem gravar')
    audit_command.add_argument('--xlsx', type=Path, help='Audita arquivo local sem credenciais')
    from .finance_cli import add_commands, run
    add_commands(commands)
    args = parser.parse_args(argv)
    try:
        if args.command == 'auditar-modelo' and args.xlsx:
            from .audit import XlsxReader, audit
            issues = audit(XlsxReader(args.xlsx))
            print('\n'.join(issues) if issues else 'Modelo local: auditoria OK.')
            return 1 if issues else 0
        config = load_config(args.config)
        if args.command == 'auditar-modelo':
            from .audit import audit
            issues = []
            for book in dict.fromkeys([config.get('principal_id', config['planilha_id']), *config['anos'].values()]):
                snapshot = Client(book, config['credenciais']).audit_snapshot()
                title = snapshot.meta['properties']['title']
                issues.extend(f'{title}: {issue}' for issue in audit(snapshot))
            print('\n'.join(issues) if issues else 'Google Sheets: auditoria OK.')
            return 1 if issues else 0
        if args.command in ('compra', 'pagar', 'parcelas', 'retomar', 'diagnosticar-operacao', 'cancelar-operacao'):
            return run(args, config)
        edits = load_edits(args.arquivo) if args.command == 'editar' else None
        client = Client(config['planilha_id'], config['credenciais'])
        metadata = client.metadata()
        if args.command == 'conectar':
            print(f'Conexão de leitura OK: {metadata["properties"]["title"]}')
            print(f'Conta de serviço: {client.email}')
            for sheet in metadata['sheets']:
                print(f'  - {sheet["properties"]["title"]}')
            print('A permissão de escrita será testada com preparar-teste ou editar --aplicar.')
        elif args.command == 'preparar-teste':
            prepare_test(client, metadata, config)
        elif args.command == 'ler':
            title = args.aba or config['aba_teste']
            grid = find_sheet(metadata, title)['properties']['gridProperties']
            if not 1 <= args.inicio <= args.fim <= grid['rowCount'] or args.fim - args.inicio >= 100 or grid['columnCount'] < 7:
                raise ValueError('Selecione até 100 linhas existentes em uma aba com pelo menos 7 colunas.')
            for row, cells in enumerate(client.cells(title, args.inicio, args.fim), args.inicio):
                values = [c.get('formattedValue', '') for c in cells]
                print(f'{row:>3} | ' + ' | '.join(values))
        elif args.command == 'editar':
            title = config['aba_teste']
            changes, snapshots, requests = plan_edits(client, metadata, title, edits)
            print(f'{metadata["properties"]["title"]} / {title}')
            if not requests:
                print('Os valores já são iguais. Nenhuma alteração necessária.')
                return 0
            print(json.dumps(changes, ensure_ascii=False, indent=2))
            if not args.aplicar:
                print('Prévia: nenhuma gravação. Repita com --aplicar para salvar estas alterações.')
                return 0
            # Revalida estrutura, regras e conteúdo antes da gravação.
            _, current, new_requests = plan_edits(client, client.metadata(), title, edits)
            if new_requests != requests or any(
                stable_cells([snapshots[row]]) != stable_cells([current[row]]) for row in snapshots
            ):
                raise RuntimeError('A planilha mudou durante a leitura. Gere uma nova prévia.')
            path = backup(config, title, snapshots, changes)
            print(f'Cópia dos valores anteriores: {path}')
            client.batch(requests)
            verify_edits(client, title, edits, snapshots)
            print(f'{len(changes)} células gravadas e conferidas. Fórmulas e formatação preservadas.')
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f'Erro: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
