# Organiza 6.2 - Chamados rápidos e banco limpo

## Alterações funcionais

- Incluídos botões rápidos dentro da tarefa:
  - Enviar para Cliente
  - Enviar para Compras
  - Enviar para Financeiro
  - Enviar para Externo
- Cada botão abre o chamado diretamente na tarefa, com campo próprio de **Ação solicitada**.
- Ao abrir o chamado, o sistema altera o status da tarefa automaticamente e volta para o painel.
- A descrição da tarefa continua separada da ação do chamado.

## Banco novo em produção

Para Render + Neon PostgreSQL, use o `DATABASE_URL` do Neon nas variáveis de ambiente do Render.

Em banco vazio, o sistema cria somente 1 usuário administrador inicial.

Variáveis recomendadas no Render:

```env
DATABASE_URL=postgresql://...
SECRET_KEY=trocar-por-uma-chave-grande
ORGANIZA_ADMIN_NOME=Admin
ORGANIZA_ADMIN_SENHA=trocar-essa-senha
```

Depois do primeiro acesso, crie Junior, Debora e Luiz pela tela de Usuários e ajuste permissões/departamentos.

## Observação importante

O arquivo `organiza.db` é apenas banco local de desenvolvimento. Em produção no Render com Neon, quem manda é o `DATABASE_URL`.
