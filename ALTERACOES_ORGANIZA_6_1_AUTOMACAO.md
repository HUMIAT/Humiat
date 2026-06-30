# Organiza 6.1 - Ajustes de fluxo e automação

## Alterações feitas

1. Removido o campo **Tipo** da tela de tarefa.
   - O sistema mantém internamente `tipo = unica` para não quebrar o banco antigo.
   - A recorrência fica desativada no formulário.

2. Separação correta entre **Descrição da tarefa** e **Ação do chamado**.
   - A descrição da tarefa fica salva somente na tarefa.
   - O chamado recebe apenas a ação solicitada ao departamento.
   - Exemplo: tarefa = "Cliente perdeu o comando"; chamado Compras = "Comprar 1 comando PPA"; chamado Financeiro = "Autorizar pagamento".

3. O campo antigo **Descrição opcional** virou **Observação interna da tarefa**.
   - Este campo não alimenta chamado automaticamente.

4. Quando o usuário muda o status para:
   - Aguardando Cliente
   - Aguardando Compras
   - Aguardando Financeiro
   - Aguardando Externo

   O formulário mostra o bloco **Ação do chamado**.

5. Ao salvar tarefa ou chamado, o sistema volta automaticamente para o **Painel**.

6. Chamados foram renomeados visualmente:
   - "Tipo do chamado" virou "Departamento do chamado".
   - "Descrição" virou "Ação solicitada".

## Automações implantadas agora

- Salvar tarefa nova → volta para `/organiza`.
- Salvar edição de tarefa → volta para `/organiza`.
- Abrir chamado → volta para `/organiza`.
- Salvar chamado → volta para `/organiza`.
- Concluir chamado → volta para `/organiza`.
- O campo de ação do chamado aparece somente quando necessário.

## Próximas automações recomendadas

1. Botão rápido no card da tarefa:
   - "Enviar para Compras"
   - "Enviar para Financeiro"
   - "Enviar para Cliente"
   - "Enviar para Externo"

2. Ao concluir chamado:
   - devolver para o responsável original;
   - registrar histórico;
   - destacar no painel como "retornou do chamado".

3. Painel com foco no que precisa de ação hoje:
   - minhas tarefas pendentes;
   - chamados do meu departamento;
   - tarefas devolvidas para mim.

4. Cadastro rápido de chamado direto no card da tarefa, sem abrir nova tela.

5. Futuro WhatsApp:
   - salvar rascunho automático;
   - registrar mensagem enviada no histórico;
   - marcar cliente como aguardando resposta.
