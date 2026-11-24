# Convenções de preenchimento

### Parcelamento

- Cada produto gera uma linha por parcela, mesmo quando vários produtos pertencem ao mesmo pedido.
- A previsão é lançada na aba FC do mês de **vencimento**; a saída efetiva é registrada no mês em que o **pagamento ocorreu**.
- O primeiro vencimento é informado no cadastro. Os seguintes são mensais, preservando o dia escolhido. Quando o dia não existe, usa-se o último dia do mês; depois, retoma-se o dia original.
- Os produtos de uma mesma compra compartilham o calendário de parcelas. Uma compra à vista é cadastrada com uma parcela.

Exemplo: produto comprado em janeiro por R$ 90, em três parcelas, com primeiro vencimento em 10 de fevereiro:

| Aba | Descritivo | Vencimento | Previsto R$ | Receita/Pagamento |
| --- | --- | --- | ---: | --- |
| FC FEV | Produto — parcela 1/3 | 10/02 | -30,00 | Em branco até pagar |
| FC MAR | Produto — parcela 2/3 | 10/03 | -30,00 | Em branco até pagar |
| FC ABR | Produto — parcela 3/3 | 10/04 | -30,00 | Em branco até pagar |

Não lançar uma saída adicional de R$ 90 em janeiro: isso duplicaria o valor da compra no fluxo de caixa.

### Preenchimento das colunas

| Coluna | Convenção |
| --- | --- |
| Data | Data da compra enquanto a linha tiver apenas previsão; data efetiva quando houver pagamento. |
| Descritivo | Descrição do produto seguida de `— parcela n/total`. |
| Centro de custo | Usar uma categoria existente na lista de validação da planilha, conforme a natureza do produto. |
| Vencimento | Data prevista da parcela, preservada mesmo em caso de atraso ou antecipação. |
| Previsto R$ | Valor previsto da parcela, negativo para compras/saídas. |
| Receita/Pagamento | Valor efetivamente pago, negativo para saídas. Deixar em branco enquanto não houver confirmação. |
| Saldo R$ | Calculado pela fórmula existente; não preencher manualmente. |

### Confirmação do pagamento

- Confirmar pela CLI, informando a parcela e a data efetiva. A baixa atual é integral, pelo valor previsto.
- Se o pagamento ocorrer no mesmo mês do vencimento, atualizar a própria linha, mantendo o valor previsto.
- Se ocorrer em outro mês, manter a previsão na aba original e criar uma linha de pagamento na aba do mês efetivo, com `Previsto R$` em branco. Não registrar o pagamento nas duas abas.
- A aba **Parcelas** preserva a data original da compra e acompanha os estados `pendente`, `atrasada` (vencida e não paga) e `paga`.

### Valores e arredondamento

- No cadastro, informar o **valor total final de cada linha de produto**, já considerando a quantidade, os descontos, o frete e os juros atribuídos a ela. Não usar o preço unitário nesse campo.
- O rateio de frete, descontos e juros entre produtos ainda é informado manualmente.
- Dividir em centavos e distribuir os centavos restantes nas primeiras parcelas: R$ 100 em três parcelas resulta em R$ 33,34 + R$ 33,33 + R$ 33,33.
- Na CLI, informar datas como `AAAA-MM-DD` e valores sem separador de milhar. A planilha armazena datas e valores como células numéricas, com formatação de exibição.

### Organização e identificação

- Usar um arquivo financeiro por ano. A aba auxiliar **Parcelas**, no arquivo principal, concentra o histórico de todos os anos.
- Manter os IDs de compra, produto e parcela. Repetir o mesmo cadastro não deve criar novos lançamentos; trocar o ID identifica outra compra.
- As colunas ocultas H:I das abas mensais identificam a parcela e o tipo de lançamento. Não editar esses identificadores nem sobrescrever fórmulas e totais.
- Para lançamentos gerenciados pela CLI, fazer a baixa pela CLI; alterações manuais podem gerar conflitos nas próximas operações.
- Pagamentos parciais, cancelamentos, estornos e renegociações ainda não são tratados nesta versão.

Instruções de configuração e comandos: [[GOOGLE_SHEETS]].
