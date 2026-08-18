# Humiat / Organiza 8.10 — Atualização do banco de músicas pelo Excel

No Administrador Humiat foi criada a área:

**SolVoz • Banco de músicas → Atualizar músicas pelo Excel**

Fluxo:
1. Escolher o arquivo `.xlsx`.
2. Clicar em **Atualizar banco**.
3. O Humiat envia o arquivo ao endpoint privado do SolVoz.
4. O SolVoz reutiliza `load_excel()` + `upsert(..., replace=False)`.
5. O resultado volta para o Humiat.

Regras:
- código novo: INSERT;
- código existente: UPDATE;
- código ausente da planilha: não é removido;
- não existe versão global do catálogo;
- a versão continua sendo um campo de cada música;
- Git e comando manual deixam de fazer parte da atualização de músicas.
