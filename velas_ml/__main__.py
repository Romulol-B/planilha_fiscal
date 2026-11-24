import argparse
from contextlib import contextmanager
from getpass import getpass
import os
from pathlib import Path
import sys

from .client import Client
from .config import Config
from .excel import export, fingerprint, read_preferences
from .store import Store
from .sync import collect


@contextmanager
def lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    # O sistema operacional libera o bloqueio mesmo se o processo terminar.
    with (directory / 'integracao.lock').open('a+b') as stream:
        stream.seek(0)
        stream.write(b'0')
        stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('Já existe outra execução nesta pasta de dados.') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Compras do Mercado Livre → planilha financeira Excel')
    parser.add_argument('--config', type=Path, default=Path('config.toml'))
    commands = parser.add_subparsers(dest='command', required=True)
    for name, help_text in [
        ('preparar', 'Prepara a cópia integrada, sem conectar à API'),
        ('autorizar', 'Gera o link para autorizar sua conta'),
        ('concluir-autorizacao', 'Recebe a URL de retorno e guarda os tokens'),
        ('sincronizar', 'Consulta a API, atualiza a base e exporta o Excel'),
        ('exportar', 'Atualiza o Excel a partir da base local'),
    ]:
        commands.add_parser(name, help=help_text)
    for name in ('ignorar', 'restaurar'):
        commands.add_parser(name).add_argument('pedido')
    args = parser.parse_args(argv)
    try:
        config = Config.load(args.config)
        with lock(config.dados):
            client = Client(config)
            if args.command == 'autorizar':
                print('Abra este link no navegador e autorize sua conta:')
                print(client.authorization_url())
                return 0
            if args.command == 'concluir-autorizacao':
                if not os.environ.get('MELI_CLIENT_SECRET'):
                    os.environ['MELI_CLIENT_SECRET'] = getpass('Secret Key do aplicativo (oculta): ')
                callback = getpass('Cole a URL completa de retorno (entrada oculta): ').strip()
                client.complete_authorization(callback)
                print('Conta autorizada. Tokens armazenados localmente.')
                return 0
            current = config.saida if config.saida.exists() else config.planilha
            before = fingerprint(current)
            preferences = read_preferences(current)
            store = Store(config.dados / 'compras.sqlite3')
            try:
                store.preferences(preferences)
                if args.command == 'sincronizar':
                    buyer, order_ids, rows = collect(client)
                    store.upsert(buyer, order_ids, rows)
                    print(f'{len(order_ids)} pedidos consultados; {len(rows)} linhas atualizadas na base.')
                if args.command in ('ignorar', 'restaurar'):
                    store.ignore(args.pedido, args.command == 'ignorar')
                rows = store.rows()
                output = export(config.planilha, config.saida, rows, expected_fingerprint=before)
                pending = sum(r['freight'] is None for r in rows)
                print(f'Planilha atualizada: {output}')
                print(f'{len(rows)} linhas; {pending} com frete pendente. Nenhuma compra é lançada automaticamente nas abas FC.')
            finally:
                store.close()
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f'Erro: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
