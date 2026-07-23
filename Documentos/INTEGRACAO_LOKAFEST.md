# Integração Organiza -> LokaFest

Endpoint privado:

GET /api/integracoes/lokafest/cliente?cpf=...&whatsapp=...

Header:
Authorization: Bearer <LOKAFEST_API_TOKEN>

A busca aceita CPF ou WhatsApp.

Retorna somente equipamentos:
- JUKEBOX -> jukebox
- MALETA/PORTATIL -> portatil
- IPHONE -> iphone

Somente equipamentos:
- status = Ativo
- fabricante = KARAOKERJ

Exemplo:

{
  "encontrado": true,
  "cliente_id": "123",
  "nome": "Cliente",
  "cpf": "00000000000",
  "whatsapp": "5521999999999",
  "atualizacao": "2026.1",
  "equipamentos": {
    "jukebox": 2,
    "portatil": 1,
    "iphone": 0
  },
  "detalhes": [
    {
      "id": 10,
      "tipo": "jukebox",
      "identificacao": "JUK1",
      "pacote": "2026.1",
      "pacote_obrigatorio": "2026.1",
      "falta_pacote": 0,
      "atualizado": true
    }
  ]
}

Configure no Render/servidor:
LOKAFEST_API_TOKEN=<token longo>

No LokaFest:
ORGANIZA_API_URL=https://SEU-DOMINIO/api/integracoes/lokafest/cliente
ORGANIZA_API_TOKEN=<mesmo token>
