# Humiat ID - Recuperação de senha 1.0.7

- Troca o envio SMTP por API HTTPS do Resend.
- Compatível com Render Free, sem uso das portas SMTP 25/465/587.
- Variáveis: `HUMIAT_RESEND_API_KEY`, `HUMIAT_EMAIL_FROM` e `HUMIAT_RESET_MINUTES`.
- Mantém token temporário, uso único e invalidação das sessões após a redefinição.
- O domínio do remetente precisa estar verificado no Resend para envio real aos usuários.
