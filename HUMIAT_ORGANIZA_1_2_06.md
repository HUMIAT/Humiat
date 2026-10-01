# Humiat / Organiza 1.2.06

- Connect passa a receber ticket SSO v2 assinado por HMAC.
- O Connect valida o ticket localmente, sem chamar novamente o Humiat por HTTP.
- Tickets de SolVoz e LokaFest permanecem no fluxo atual.
- Compatível com o fallback legado do Connect.
