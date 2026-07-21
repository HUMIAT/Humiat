# Alteração — desconto condicionado aos opcionais

## O que foi criado

Na área **Desconto do orçamento**, foi adicionado o campo:

> Aplicar somente com todos os opcionais

Quando marcado:

- o desconto não é aplicado se o cliente aprovar apenas os itens obrigatórios;
- o desconto é aplicado somente quando todos os itens opcionais também forem aprovados;
- o orçamento público informa claramente essa condição;
- os totais obrigatórios e completos são calculados separadamente;
- a regra também é respeitada no valor aprovado, pagamentos e saldo restante.

Quando desmarcado, o comportamento anterior é mantido.

## Arquivos alterados

- `app.py`
- `templates/organiza/manutencao_detalhe.html`
- `templates/organiza/orcamento_publico.html`
- `static/css/organiza.css`

## Banco de dados

Na primeira inicialização, o sistema cria automaticamente a coluna:

```sql
desconto_somente_com_opcionais INTEGER NOT NULL DEFAULT 0
```

Não é necessário executar uma migração manual.
