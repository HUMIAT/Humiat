# HUMIAT — fluxo simples de manutenção

Esta versão mantém clientes e equipamentos e adiciona somente:

- catálogo de itens com custo e preço normal;
- manutenção vinculada a cliente e equipamento;
- orçamento com itens obrigatórios e opcionais;
- link público para aprovação total, parcial ou cancelamento;
- aprovação manual;
- pagamentos vinculados ao orçamento;
- prazo informado ao cliente;
- comunicação por WhatsApp;
- equipamento pronto e agendamento de retirada.

As novas tabelas usam nomes próprios (`assistencias`, `catalogo_itens` etc.) para não conflitar com tabelas antigas que possam continuar no PostgreSQL.

## Publicação

```bash
git add .
git commit -m "feat: adiciona manutencao simples com orcamento pagamento e WhatsApp"
git push origin main
```
