from dataclasses import dataclass
from datetime import date
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Config:
    client_id: str
    redirect_uri: str
    planilha: Path
    saida: Path
    dados: Path
    desde: str
    ate: str

    @classmethod
    def load(cls, path: Path):
        with path.open('rb') as f:
            raw = tomllib.load(f)
        root = path.resolve().parent
        ml, files, sync = (raw[k] for k in ('mercado_livre', 'arquivos', 'sincronizacao'))
        since, until = str(sync['desde']), str(sync['ate'])
        if date.fromisoformat(since) > date.fromisoformat(until):
            raise ValueError('A data inicial deve ser anterior à data final.')
        source, output = (root / files[k] for k in ('planilha', 'saida'))
        if source.resolve() == output.resolve():
            raise ValueError('Use uma saída diferente da planilha original.')
        if source.suffix.lower() != '.xlsx' or output.suffix.lower() != '.xlsx':
            raise ValueError('Esta integração trabalha com arquivos .xlsx.')
        return cls(str(ml['client_id']), ml['redirect_uri'], source, output,
                   root / files['dados'], since, until)

