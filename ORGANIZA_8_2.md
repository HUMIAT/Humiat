# ORGANIZA 8.2 — Integração SolVoz

## Objetivo
Preparar o Organiza como fonte de implantação das máquinas que poderão usar o catálogo online do SolVoz, sem alterar as regras atuais de licença.

## Decisões aprovadas
- Número da máquina e número do HD continuam com as validações atuais.
- Cada equipamento pode ser vinculado a uma Empresa SolVoz.
- Catálogo online é opcional por equipamento.
- O domínio é derivado automaticamente da empresa:
  - `karaokerj` → `https://www.karaokerj.com.br`
  - demais slugs → `https://www.solvoz.com.br/<slug>`
- O QR do Organiza continua sendo opcional por máquina.
- Se Catálogo online = NÃO, o QR aponta apenas para o domínio da empresa.
- Se Catálogo online = SIM, o QR acrescenta `maquina` e `plano`.
- O plano continua sendo o campo já existente no equipamento.
- O QR oficial da empresa continua sendo responsabilidade do SolVoz.
- A integração futura com o SolVoz usa uma rota privada de sincronização, não consultas ao Organiza durante o uso do cliente.

## Entregas
- Cadastro administrativo de Empresas SolVoz em `/organiza/configuracoes/solvoz-empresas`.
- Campos `Empresa SolVoz` e `Catálogo online` no equipamento.
- Domínio automático mostrado no formulário.
- Geração de licença/QR usando o domínio da Empresa SolVoz.
- Arquivo `URL_CATALOGO.txt` incluído no ZIP de licença para auditoria.
- Rota privada `GET /api/integracoes/solvoz/maquinas`.
- Token da integração via `SOLVOZ_API_TOKEN`.
- Migração automática dos novos campos em bancos existentes.
- Karaokê RJ é criado automaticamente como Empresa SolVoz padrão para preservar a operação histórica.

## Pendências
- Implementar no SolVoz o consumo/sincronização da rota privada.
- Validar em produção um equipamento offline e um online antes de sincronizar o restante da base.

## Próximo passo
Publicar o Organiza 8.2, cadastrar as demais Empresas SolVoz e testar a geração dos dois tipos de QR.
