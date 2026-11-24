import argparse
import json
from pathlib import Path
import shlex
import sys

from .converter import convert


def generate(result, output, config):
    output = output.resolve()
    files, commands = {}, ['# Prévia (consulta o Google Sheets):']
    apply_commands = ['# Gravação:']
    for raw in result['compras']:
        path = output / (raw['id'] + '.json')
        files[path] = json.dumps(raw, ensure_ascii=False, indent=2) + '\n'
        command = shlex.join(['python', '-m', 'velas_sheets', '--config', str(config.resolve()), 'compra', str(path)])
        commands.append(command)
        apply_commands.append(command + ' --aplicar')
    command_text = '\n'.join(commands + [''] + apply_commands) + '\n'
    files[output / (result['chave'] + '-comandos.txt')] = command_text
    # Detecta qualquer conflito antes de criar arquivos; nunca sobrescreve.
    for path, content in files.items():
        if path.exists() and path.read_text(encoding='utf-8') != content:
            raise ValueError(f'Arquivo existente com conteúdo diferente: {path}. Use outra pasta de saída.')
    output.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        try:
            with path.open('x', encoding='utf-8') as stream:
                stream.write(content)
        except FileExistsError:
            if path.read_text(encoding='utf-8') != content:
                raise ValueError(f'Arquivo alterado durante a geração: {path}.')
    return command_text


def main(argv=None):
    parser = argparse.ArgumentParser(description='Converte NF-e em JSONs e comandos para velas_sheets, sem acesso à rede.')
    parser.add_argument('xml', type=Path)
    parser.add_argument('--saida', type=Path, required=True)
    parser.add_argument('--centro-custo', required=True)
    parser.add_argument('--config', type=Path, default=Path('config.sheets.toml'))
    parser.add_argument('--parcelas', type=int)
    parser.add_argument('--primeiro-vencimento')
    args = parser.parse_args(argv)
    try:
        result = convert(args.xml, args.centro_custo, args.parcelas, args.primeiro_vencimento)
        commands = generate(result, args.saida, args.config)
        print(f"NF-e {result['numero']} — {result['fornecedor']}: R$ {result['total']}; {len(result['compras'])} compra(s).")
        print('Conversão local concluída. Execute os comandos a partir do projeto, com o ambiente Python ativado.')
        print(commands)
        return 0
    except (OSError, ValueError) as error:
        print(f'Erro: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
