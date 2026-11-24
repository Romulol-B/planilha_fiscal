# Conversor de notas fiscais XML

O conversor gera arquivos locais compatíveis com `velas_sheets compra` e um arquivo
`NFe<chave>-comandos.txt` com as chamadas de prévia e gravação. Não consulta o Google
Sheets, não precisa de credenciais para converter e não executa os comandos gerados.

Na raiz do projeto, com Python 3.11 ou superior e o ambiente ativado:

```bash
source .venv/bin/activate
python -m velas_notas_fiscais exemplos/nota_xml.xml \
  --saida saidas/notas --centro-custo 'Conta  a pagar'
python -m velas_notas_fiscais exemplos/nota_2_xml.xml \
  --saida saidas/notas --centro-custo 'Conta  a pagar'
```

Os dois XMLs são fictícios. A primeira nota gera uma compra de R$ 14,00
com vencimento em 20/11/2026, já com R$ 2,00 de desconto.
A segunda gera três compras, uma por duplicata: R$ 42,00 em 10/12/2026,
R$ 41,50 em 10/01/2027 e R$ 41,51 em 10/02/2027. Os três produtos totalizam
R$ 125,01, incluindo R$ 7,01 de frete e R$ 5,00 de desconto.

Confira os JSONs e execute primeiro os comandos de prévia do arquivo de texto.
Para registrar, execute as versões com `--aplicar`. As chamadas usam caminhos
absolutos e escape para Bash; execute-as na raiz deste projeto, com o ambiente
Python ativado. A CLI existente realiza autenticação, validação do centro de custo,
roteamento por ano, backup e proteção contra duplicidade. Para a segunda nota,
configure também o destino de 2027 conforme o [guia de Sheets](GOOGLE_SHEETS.md).

## Opções

- `xml`: caminho de uma NF-e modelo 55 normal, com namespace oficial.
- `--saida`: pasta obrigatória para os arquivos gerados.
- `--centro-custo`: valor obrigatório usado em todos os produtos. Deve corresponder
  a uma opção aceita pela planilha; o conversor verifica apenas se está preenchido.
- `--config`: configuração usada nas chamadas geradas; padrão `config.sheets.toml`.
  O conversor não abre esse arquivo.
- `--parcelas` e `--primeiro-vencimento`: disponíveis apenas para notas sem
  duplicatas; padrões de uma parcela e data de emissão. As parcelas seguintes
  seguem o calendário mensal já implementado na CLI.

```bash
python -m velas_notas_fiscais exemplos/nota_xml.xml \
  --saida saidas/notas-alternativo --centro-custo 'Conta  a pagar' \
  --parcelas 3 --primeiro-vencimento 2026-12-20
```

## Regras e limites

A data de compra é a data local de emissão. IDs usam a chave da nota e, quando
há duplicatas, seu índice após ordenação por vencimento e número. Produtos usam
`nItem`, preservando descrições e quantidades. Repetir a conversão com os mesmos
parâmetros preserva os arquivos existentes; conteúdos diferentes são rejeitados.
Use outra pasta para revisar uma conversão com parâmetros diferentes.

Cada duplicata vira uma compra independente de parcela única. Seus valores são
distribuídos proporcionalmente aos saldos dos produtos, em centavos, usando os
maiores restos e desempate por `nItem`. A última duplicata recebe o saldo restante.
Alocações de zero centavos são omitidas. A quantidade original do produto aparece
em cada compra: essas linhas representam parcelas financeiras, não novas entradas
de estoque, e suas quantidades não devem ser somadas para medir compras físicas.

O total de cada produto é `vProd - vDesc + vFrete + vSeg + vOutro`. Os totais dos
componentes e da nota devem fechar exatamente, assim como `vItem` e `vNFTot`,
quando presentes. Componentes adicionais como IPI, II, ST e desoneração não nulos
são rejeitados nesta versão. Não há rateio silencioso de diferenças.

São rejeitados XMLs inválidos, DTD/entidades, campos obrigatórios ausentes,
duplicatas inconsistentes, itens repetidos, itens fora do total, valores líquidos
não positivos e quantidades fracionárias. Aplicam-se também os limites da CLI:
até 100 produtos, de 1 a 120 parcelas mensais e ao menos um centavo por parcela.
Esta ferramenta não valida assinatura digital nem consulta autorização ou eventos
fiscais. O grupo de pagamento do XML não gera confirmação de quitação.

O código de saída é 0 em caso de sucesso e 1 em caso de erro de conversão ou
gravação. Todos os JSONs são validados antes da escrita; falhas de disco podem
deixar arquivos parciais, identificados como conflito na próxima execução.

## Testes locais

```bash
python -m unittest discover -s tests -p 'test_notas_fiscais.py'
python -m unittest discover -s tests -p 'test_parcelas.py'
```
