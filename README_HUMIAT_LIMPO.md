# HUMIAT — Base limpa

Esta versão mantém somente:

- Login e usuários administrativos
- Painel simples
- Clientes
- Equipamentos vinculados ao cliente

Foram removidos do código, menu e templates:

- Tarefas e projetos
- Itens
- Compras
- Financeiro e bancos
- Vendas
- Manutenções e orçamentos antigos
- Agenda
- Chamados e externos

As tabelas antigas existentes no PostgreSQL não são apagadas automaticamente, para evitar perda acidental. Elas apenas deixam de ser usadas pela aplicação. Clientes e equipamentos existentes continuam preservados.
