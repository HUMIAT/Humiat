# HUMIAT — fluxo simplificado

## Estrutura principal

- Clientes
- Equipamentos do cliente
- Vendas
- Manutenções
- Orçamentos
- Pagamentos operacionais
- Agenda de retirada
- Cadastro de itens

## Manutenção

Toda manutenção exige cliente, WhatsApp e um equipamento já cadastrado.
Um cliente pode possuir vários equipamentos e cada equipamento pode possuir várias manutenções.
Cada manutenção pode possuir vários orçamentos versionados.

## Orçamento

O orçamento possui itens obrigatórios e opcionais. Internamente são armazenados quantidade, valor unitário e total. O cliente não visualiza valor unitário; recebe somente descrição, quantidade quando aplicável, total obrigatório, valores dos opcionais e total aprovado.

O cliente pode aprovar tudo, aprovar parcialmente escolhendo os opcionais ou cancelar. A equipe também pode registrar aprovação manual.

## Depois da aprovação

Na própria tela da manutenção são registrados pagamento e prazo. Quando o equipamento ficar pronto, a equipe envia a mensagem pelo WhatsApp e registra a data e hora da retirada. O agendamento cria automaticamente um evento na agenda.

## Venda

A venda mantém custo e preço vigentes no momento em que os itens são adicionados. Pagamentos são registrados na própria venda, exibindo valor total, recebido e falta. Ao finalizar, o equipamento é criado dentro do cliente.

## Módulos antigos

Compras, financeiro completo, bancos, projetos e tarefas foram removidos do fluxo e do menu. As tabelas antigas foram preservadas para evitar perda de dados durante a migração.
