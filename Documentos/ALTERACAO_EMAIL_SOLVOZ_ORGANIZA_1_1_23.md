# Organiza v1.1.23 — e-mail de acesso SolVoz centralizado no Organiza

## Decisão aprovada

O **Organiza é a fonte do cadastro do cliente e o responsável pelo envio dos e-mails**.
O **SolVoz é responsável pelas credenciais e pela autenticação**.

Esta alteração substitui apenas a parte de e-mail do fluxo documentado em
`ALTERACAO_ACESSO_SOLVOZ_DIRETO_1_1_2.md`.

## Responsabilidades

### Organiza

- mantém o cadastro do cliente: nome, e-mail, documento, telefone e equipamentos;
- solicita ao SolVoz a criação/atualização do acesso;
- recebe a senha provisória somente na resposta privada servidor-servidor;
- envia a senha provisória pelo **Resend** já configurado no HUMIAT;
- não grava a senha provisória em banco, cache ou log;
- também funciona como transporte de e-mail para a recuperação de senha do SolVoz.

### SolVoz

- cria o usuário local;
- gera a senha provisória;
- grava somente `hash + salt`, nunca a senha em texto puro;
- obriga a troca da senha no primeiro acesso;
- depois da troca, mantém exclusivamente a senha definitiva;
- gera e valida tokens de recuperação de senha;
- altera a senha definitiva quando o token válido é utilizado.

## Primeiro acesso

```text
Organiza
   │ cadastro + equipamentos
   ▼
SolVoz
   │ cria usuário
   │ gera senha provisória
   │ salva somente hash + salt
   ▼
Organiza
   │ recebe a senha somente na resposta privada
   │ não persiste a senha
   │ envia pelo Resend
   ▼
Cliente
   │ entra com senha provisória
   ▼
SolVoz
   │ exige nova senha
   └── salva a senha definitiva
```

## Reenviar senha

O botão **Reenviar senha** continua no Organiza.

1. Organiza solicita ao SolVoz uma nova senha provisória.
2. SolVoz invalida a credencial anterior ao gerar a nova senha.
3. SolVoz devolve a senha apenas pela API privada protegida por `SOLVOZ_API_TOKEN`.
4. Organiza envia a nova senha pelo Resend.
5. A senha provisória é removida da memória do fluxo antes de salvar o estado local.

## Esqueci minha senha

A recuperação pertence ao **SolVoz**, porque a senha definitiva existe somente nele.

O Organiza participa apenas como serviço de envio:

```text
Cliente -> SolVoz: "Esqueci minha senha"
SolVoz -> gera token de uso único
SolVoz -> Organiza: solicita envio privado
Organiza -> Resend -> Cliente
Cliente -> SolVoz: abre o link e cria nova senha
```

O endpoint interno de transporte é:

`POST /api/integracoes/solvoz/email/recuperacao`

Cabeçalho obrigatório:

`X-SolVoz-Token: <SOLVOZ_API_TOKEN>`

A rota aceita somente links HTTPS do domínio configurado em `SOLVOZ_BASE_URL`, evitando uso como relay de e-mail para links externos.

## Configuração

No Organiza:

```env
SOLVOZ_API_TOKEN=...
SOLVOZ_BASE_URL=https://www.solvoz.com.br
SOLVOZ_API_TIMEOUT=12

HUMIAT_RESEND_API_KEY=...
HUMIAT_EMAIL_FROM=Humiat <acesso@humiat.com.br>
```

Não é necessário configurar SMTP no SolVoz para os e-mails de acesso de usuários.

## Segurança

- a senha provisória não é persistida pelo Organiza;
- a senha provisória só trafega em HTTPS na integração privada;
- a resposta privada do SolVoz usa `Cache-Control: no-store`;
- a senha definitiva nunca volta ao Organiza;
- tokens de recuperação são de uso único e expiram;
- respostas públicas de recuperação não revelam se um e-mail existe.

## Versão

`1.1.23`

## Commit sugerido

```bash
git add .
git commit -m "Organiza 1.1.23 - centraliza email SolVoz no Resend"
git push
```
