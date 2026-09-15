# Organiza v1.1.2 / estrutura 8.8 — acesso SolVoz direto pelo cadastro do cliente

## Fluxo novo

Na ficha do cliente existe um card **Acesso SolVoz**.

1. O Organiza usa o e-mail já cadastrado no cliente.
2. Agrupa os equipamentos que possuem **Catálogo Online** e **Empresa SolVoz** vinculados.
3. Envia cadastro + códigos técnicos dos equipamentos para a API privada do SolVoz.
4. O SolVoz cria o usuário, gera a senha provisória e envia o e-mail.
5. No primeiro login o cliente é obrigado a criar uma nova senha.
6. O cliente passa a administrar somente a própria empresa no SolVoz.

O Organiza não recebe, cria nem armazena a senha do cliente.

## Tela do cliente

O card mostra:

- usuário/e-mail;
- status do acesso;
- equipamentos que serão vinculados;
- aviso de primeira troca de senha;
- botão **Criar acesso SolVoz**;
- após a criação, botões **Atualizar equipamentos** e **Reenviar senha**.

O estado do acesso é salvo em cache local (`solvoz_acessos_clientes`) para a ficha do cliente não depender de uma consulta HTTP ao SolVoz a cada abertura.

## Integração

O Organiza chama:

- `POST /api/integracoes/organiza/solvoz/acesso`
- `POST /api/integracoes/organiza/solvoz/acesso/reenviar`

Cabeçalho obrigatório:

`X-SolVoz-Token: <SOLVOZ_API_TOKEN>`

Variáveis:

- `SOLVOZ_API_TOKEN` — mesmo valor do SolVoz;
- `SOLVOZ_BASE_URL=https://www.solvoz.com.br`;
- `SOLVOZ_API_TIMEOUT=12`.

## Compatibilidade

A integração antiga do Humiat ID permanece para usuários já existentes e para a equipe interna, mas novas criações feitas pelo botão do Organiza são provisionadas diretamente no SolVoz conforme solicitado.

## Versões

- Interface: `1.1.2`
- Estrutura/API: `8.8`

## Commit

```bash
git add .
git commit -m "Organiza 1.1.2 - cria acesso SolVoz pelo cadastro e equipamentos do cliente"
git push
```
