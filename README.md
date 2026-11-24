# Velas · Automação de compras e fluxo de caixa

O Velas transforma notas fiscais eletrônicas (NF-e) em lançamentos de compras para
o Google Sheets. A ferramenta separa a previsão pelo vencimento do pagamento
realizado, para que uma compra parcelada apareça nos meses certos do fluxo de caixa.

**Fluxo da compra:** NF-e XML → JSON com valores e vencimentos → prévia no Google Sheets → gravação confirmada.

## Demonstração local

Requisitos: Python 3.11 ou superior e as dependências do projeto. A instalação
das dependências usa a rede. A conversão e a auditoria local rodam sem credenciais.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

python -m velas_notas_fiscais exemplos/nota_2_xml.xml \
  --saida saidas/demo --centro-custo 'Conta  a pagar'

python -m velas_sheets auditar-modelo --xlsx exemplos/modelo-demonstracao.xlsx
```

No Windows, ative o ambiente com `.venv\Scripts\Activate.ps1` no PowerShell.

A nota fictícia de **R$ 125,01** gera três arquivos JSON e um arquivo de comandos
na pasta `saidas/demo/`. Os valores de R$ 42,00, R$ 41,50 e R$ 41,51 vencem em
dezembro, janeiro e fevereiro, respectivamente. A auditoria deve mostrar
`Modelo local: auditoria OK.`

## Como funciona

| Etapa | Comportamento |
| --- | --- |
| Conversão | Valida totais da NF-e e reparte cada duplicata entre os produtos em centavos. |
| Lançamento | Encaminha cada parcela ao arquivo do ano e à aba do mês de vencimento. A prévia consulta o Google Sheets. A gravação exige `--aplicar`. |
| Pagamento | Registra a baixa integral no mês do pagamento e mantém o vencimento original. |
| Auditoria | Confere fórmulas de saldo, referências e validações do modelo. No Google Sheets, também informa erros de cálculo retornados pela API. |
| Retomada | Usa um diário local para reconhecer etapas já gravadas e concluir só o que falta depois de uma interrupção. |

Para levar a demonstração ao Google Sheets, importe o
[modelo de demonstração](exemplos/modelo-demonstracao.xlsx) como planilha nativa,
configure cópias de teste e execute uma das prévias geradas. O exemplo parcelado
usa os anos de 2026 e 2027. [Configuração e operação no Google Sheets](docs/GOOGLE_SHEETS.md)

- [Conversão de NF-e e opções](docs/NOTAS_FISCAIS.md)
- [Convenções do fluxo de caixa](docs/Organiza%C3%A7%C3%A3o%20da%20Planilha.md)
- [Arquitetura e testes](docs/documentacao.md)

A versão atual aceita NF-e modelo 55 normal e confirma pagamentos integrais.
Importação de CSV, estornos e pagamentos parciais ficam fora do escopo.
Planilhas usadas pela CLI precisam seguir a estrutura e as fórmulas do modelo.

## Testes

```bash
python -m unittest discover -s tests -v
```

Os testes usam dados fictícios e simulam a API. O GitHub Actions roda a suíte e
a demonstração em Python 3.11 e 3.14. A verificação de recálculo com LibreOffice
é obrigatória no CI e opcional localmente.
