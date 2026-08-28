# Humiat ID - Recuperação de senha 1.0.6

- Adiciona **Esqueci minha senha** na tela de entrada do Humiat ID.
- Usuário informa o e-mail e recebe link temporário por e-mail.
- Token é salvo somente como hash, expira por padrão em 30 minutos e só pode ser usado uma vez.
- A resposta não revela se um e-mail existe no cadastro.
- Após redefinição, todas as sessões antigas daquele usuário são encerradas.
- A senha nova exige no mínimo 8 caracteres.
- Envio usa SMTP configurável por variáveis `HUMIAT_SMTP_*`.
