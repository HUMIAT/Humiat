# Humiat / Organiza 1.1.34

Integração do Connect ao Humiat ID.

- Connect passa a receber ticket SSO central em `/_connect/sso/humiat`.
- Acesso `Usuário` também usa SSO para a equipe interna, permitindo vincular Junior/Débora/Luiz aos usuários já existentes do Connect.
- Para clientes, o ticket leva a empresa e o slug oficial do Organiza.
- O Connect deve possuir a empresa local com o mesmo slug; não há criação automática por SSO.
- `ADM` com empresa representa administração da própria empresa; equipe interna sem empresa pode entrar no ADM global do Connect.
