# Padrão de identificação de equipamentos

## Padrão visual
- Identificação principal: `MAQ 1`, `MAQ 2`, `MAQ 3`...
- Em seguida: tipo e modelo do equipamento.
- Identificação técnica secundária: `Código técnico: KRJ00001`.

## Aplicado em
- Painel principal de pendências.
- Lista geral de manutenções.
- Nova manutenção e seleção do equipamento.
- Detalhe e edição da manutenção.
- Solicitação pública feita pelo cliente.
- Cadastro e edição do equipamento.
- Ficha do cliente e cards dos equipamentos.
- Orçamento público.
- Retirada pública.
- Agenda.
- Cards de vendas.

## Regras
- O número da máquina no cliente pode ser corrigido.
- O código técnico KRJ pode ser corrigido.
- O código técnico deve seguir `KRJ` + 5 números.
- O código técnico não pode se repetir.
- O mesmo cliente não pode ter duas máquinas com o mesmo número.
- O sistema não renumera mais todos os equipamentos ao salvar um cadastro.
