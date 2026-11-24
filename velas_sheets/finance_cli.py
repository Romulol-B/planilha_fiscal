import json
from contextlib import nullcontext
from pathlib import Path

from .client import Client
from .finance import Planner
from .installments import purchase
from .operations import apply, pending, diagnose, cancel
from velas_common.locking import lock


def add_commands(commands):
    buy = commands.add_parser('compra', help='Planeja parcelas por produto; --aplicar grava')
    buy.add_argument('arquivo', type=Path)
    buy.add_argument('--aplicar', action='store_true')
    buy.add_argument('--sem-checagem-saldo', action='store_true', help='Teste: ignora a auditoria das fórmulas de saldo')
    pay = commands.add_parser('pagar', help='Confirma o pagamento integral de uma parcela')
    pay.add_argument('parcela')
    pay.add_argument('--data', required=True)
    pay.add_argument('--aplicar', action='store_true')
    pay.add_argument('--sem-checagem-saldo', action='store_true', help='Teste: ignora a auditoria das fórmulas de saldo')
    listing = commands.add_parser('parcelas', help='Consulta o histórico de parcelas')
    listing.add_argument('--compra')
    listing.add_argument('--desde', help='Vencimento inicial AAAA-MM-DD')
    listing.add_argument('--ate', help='Vencimento final AAAA-MM-DD')
    listing.add_argument('--status', choices=['pendente', 'atrasada', 'paga'])
    resume = commands.add_parser('retomar', help='Inspeciona ou retoma a operação interrompida')
    resume.add_argument('--aplicar', action='store_true')
    commands.add_parser('diagnosticar-operacao', help='Compara os campos da operação pendente sem gravar')
    cancel_command = commands.add_parser('cancelar-operacao', help='Encerra o diário somente se nada foi aplicado')
    cancel_command.add_argument('--aplicar', action='store_true')


def run(args, config, factory=None):
    factory = factory or (lambda book: Client(book, config['credenciais']))
    directory = config['backups'].parent / 'parcelas_operacoes'
    # A prévia não cria diretórios, backups ou bloqueios.
    with lock(directory) if getattr(args, 'aplicar', False) else nullcontext():
        interrupted = pending(directory)
        if args.command in ('retomar', 'diagnosticar-operacao', 'cancelar-operacao'):
            if len(interrupted) != 1:
                raise ValueError(f'Esperada uma operação pendente; encontradas {len(interrupted)}.')
            path = interrupted[0]
            plan = json.loads(path.read_text(encoding='utf-8'))
            if args.command == 'diagnosticar-operacao':
                print(json.dumps(diagnose(plan, factory), ensure_ascii=False, indent=2))
                return 0
            if args.command == 'cancelar-operacao':
                cancel(plan, path, factory, args.aplicar)
                print(f'Diário cancelado e preservado: {path}' if args.aplicar else 'Nenhuma alteração aplicada. Use cancelar-operacao --aplicar para encerrar o diário.')
                return 0
            print(json.dumps(plan['summary'], ensure_ascii=False, indent=2))
            if args.aplicar:
                apply(plan, directory, factory, path)
                print(f'Operação retomada e conferida. Diário: {path}')
            else:
                print('Prévia da operação pendente; use retomar --aplicar.')
            return 0
        if interrupted and args.command != 'parcelas':
            raise ValueError('Há uma operação pendente. Execute retomar antes de iniciar outra.')
        planner = Planner(config, factory, skip_balance_check=getattr(args, 'sem_checagem_saldo', False))
        if args.command == 'parcelas':
            if interrupted:
                print('ATENÇÃO: há operação pendente; histórico pode estar incompleto.')
            print(json.dumps(planner.listing(args.compra, args.desde, args.ate, args.status), ensure_ascii=False, indent=2))
            return 0
        if args.command == 'compra':
            raw = json.loads(args.arquivo.read_text(encoding='utf-8'))
            purchase(raw)  # Validação local antes de autenticar.
            plan = planner.register(raw)
        else:
            plan = planner.pay(args.parcela, args.data)
        print(json.dumps(plan['summary'], ensure_ascii=False, indent=2))
        if not plan['steps']:
            print('Já registrado. Nenhuma alteração necessária.')
        elif args.aplicar:
            path = apply(plan, directory, factory)
            print(f'Gravação conferida. Cópia anterior e diário: {path}')
        else:
            print('Prévia validada. Nenhuma gravação; use --aplicar para confirmar.')
        return 0
