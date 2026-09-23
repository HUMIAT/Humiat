# Organiza 1.1.24 — Campanhas para Clientes de Atualização

## Etapa 1

Implementada a primeira etapa do módulo de Campanhas usando exclusivamente o cadastro já existente de clientes de atualização do Organiza.

### Lista de Atualização

A lista é automática e inclui somente clientes que tenham pelo menos um equipamento que atenda simultaneamente às duas regras:

1. equipamento com status `Ativo`;
2. atualização disponível em relação ao Pacote atual configurado no Organiza.

O cliente aparece uma única vez na lista, mesmo possuindo vários equipamentos elegíveis.

O campo `campanhas_ativo` controla apenas a participação do cliente em campanhas. Inativar campanhas não altera nem inativa o cliente ou seus equipamentos no Organiza.

### Campanhas

Fluxo simples:

1. criar campanha;
2. escolher a lista `Clientes de Atualização`;
3. informar mensagem;
4. anexar foto opcional;
5. salvar;
6. iniciar campanha;
7. abrir o WhatsApp do cliente atual;
8. após o envio manual, marcar `Enviado → Próximo`.

### Dois aparelhos ao mesmo tempo

Cada destinatário é reservado atomicamente no banco antes de ser apresentado ao usuário. Se dois usuários estiverem trabalhando na mesma campanha, cada aparelho recebe um cliente diferente. Um destinatário já reservado ou enviado não é entregue ao outro usuário.

### Não receber campanhas

Na tela de envio existe a opção `Não receber mais campanhas`. Ela desativa apenas campanhas futuras para o cliente e não altera o cadastro operacional.

### Imagem opcional

A imagem é armazenada no banco da campanha, com limite de 5 MB. Quando houver imagem, a mensagem do WhatsApp recebe um link público protegido por token para permitir visualização/preview da arte sem depender do WhatsApp Web.

### Etapa 2

O cadastro/lista de Clientes de Aluguel ficará para a próxima etapa.
