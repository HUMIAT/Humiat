# Organiza 1.1.96 — Humiat ID com empresa única por cliente

- Corrige clientes externos que acumulavam mais de uma empresa no Humiat ID.
- A empresa do Humiat ID continua sendo identificada pelos equipamentos já vinculados ao cliente.
- Quando existe uma empresa específica (ex.: Vivi Karaokê), o vínculo legado/padrão com Karaokê RJ é removido.
- Cliente sem empresa identificável continua usando Karaokê RJ como padrão inicial.
- Os produtos já liberados no cadastro do cliente (ex.: Connect) são habilitados automaticamente na empresa correta.
- O painel de cliente não exibe mais seletor de empresa.
- Links de abertura dos produtos não dependem mais de empresa_id informado pela interface; a empresa única do usuário é resolvida pelo Humiat ID.
- QR Code, licença, transferência e vínculo SolVoz dos equipamentos permanecem inalterados nesta versão.
- Slug global e alias legado do Connect permanecem preservados (vivikaraoke -> vivioke).
