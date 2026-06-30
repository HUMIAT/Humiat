# Organiza V2 — HUMIAT

## Decisões aplicadas

- Área Restrita com usuário e senha.
- Usuários iniciais: Junior, Debora e Luiz.
- Nome do módulo: Organiza.
- Projeto continua sendo o agrupador principal.
- "Missão" foi trocada na interface por "Departamento / Etapa".
- O campo principal da tarefa agora aparece como "Tarefa".
- "Descrição" fica somente como campo opcional no final.
- Status disponíveis:
  - Aberta
  - Em andamento
  - Aguardando
  - Pronta
  - Entregue
- Somente tarefas com status "Entregue" contam como concluídas no percentual.
- O percentual do projeto é recalculado automaticamente com base nas tarefas entregues.
- Tarefa única oculta e limpa os dias da semana.
- Tarefa recorrente marca todos os dias por padrão, permitindo edição.
- Dependência só aparece para tarefas do mesmo projeto.
- Para criar uma dependência inexistente, crie primeiro a tarefa que será dependência e depois edite a tarefa principal.
- Painel principal permanece simples, sem filtros.
- Os números do painel são clicáveis e mostram tarefas organizadas por projeto.
- Botão Voltar adicionado nas principais telas.
- Edição de projeto, departamento/etapa e tarefa.

## Observação técnica

A classe e a tabela interna `missoes` foram mantidas para preservar compatibilidade com bancos já criados na V1. Na interface, o usuário vê apenas "Departamento / Etapa".
