"""Serviço central de comunicação do HUMIAT."""
import re
from datetime import datetime
from urllib.parse import quote

PAISES = {
    "BR": {"nome": "Brasil", "ddi": "55", "exemplo": "(21) 97422-5034"},
    "US": {"nome": "Estados Unidos", "ddi": "1", "exemplo": "(954) 665-4922"},
    "PT": {"nome": "Portugal", "ddi": "351", "exemplo": "912 345 678"},
    "AR": {"nome": "Argentina", "ddi": "54", "exemplo": "11 1234-5678"},
    "UY": {"nome": "Uruguai", "ddi": "598", "exemplo": "99 123 456"},
    "PY": {"nome": "Paraguai", "ddi": "595", "exemplo": "981 123456"},
    "CL": {"nome": "Chile", "ddi": "56", "exemplo": "9 1234 5678"},
    "ES": {"nome": "Espanha", "ddi": "34", "exemplo": "612 345 678"},
    "GB": {"nome": "Reino Unido", "ddi": "44", "exemplo": "7700 900123"},
    "CA": {"nome": "Canadá", "ddi": "1", "exemplo": "(416) 555-0123"},
    "MX": {"nome": "México", "ddi": "52", "exemplo": "55 1234 5678"},
    "AO": {"nome": "Angola", "ddi": "244", "exemplo": "923 123 456"},
    "MZ": {"nome": "Moçambique", "ddi": "258", "exemplo": "82 123 4567"},
}

TIPOS_COMUNICACAO = {
    "ORCAMENTO", "PAGAMENTO", "PRAZO", "PRONTO", "GARANTIA",
    "RETIRADA", "PAUSA", "COMPRA", "GERAL"
}

def somente_digitos(valor) -> str:
    return re.sub(r"\D", "", str(valor or ""))

def normalizar_contato(pais: str, ddi: str, telefone: str):
    pais = (pais or "BR").upper().strip()[:2]
    dados = PAISES.get(pais, {})
    ddi = somente_digitos(ddi) or dados.get("ddi", "55")
    telefone = somente_digitos(telefone)
    # Evita duplicar o DDI quando o número foi colado em formato internacional.
    if ddi and telefone.startswith(ddi) and len(telefone) > len(ddi) + 7:
        telefone = telefone[len(ddi):]
    return pais, ddi[:5], telefone[:20]

def numero_internacional(cliente) -> str:
    _, ddi, telefone = normalizar_contato(
        getattr(cliente, "pais", "BR"),
        getattr(cliente, "ddi", "55"),
        getattr(cliente, "telefone", ""),
    )
    return f"{ddi}{telefone}"

def telefone_valido(pais: str, ddi: str, telefone: str) -> bool:
    _, _, numero = normalizar_contato(pais, ddi, telefone)
    # E.164 comporta até 15 dígitos no total; números nacionais variam por país.
    total = len(somente_digitos(ddi)) + len(numero)
    return 7 <= len(numero) <= 14 and total <= 15

def formatar_telefone(pais: str, telefone: str) -> str:
    numero = somente_digitos(telefone)
    if (pais or "BR").upper() == "BR" and len(numero) == 11:
        return f"({numero[:2]}) {numero[2:7]}-{numero[7:]}"
    return numero

class ComunicacaoService:
    @staticmethod
    def url_whatsapp(cliente, mensagem: str = "") -> str:
        numero = numero_internacional(cliente)
        return f"https://wa.me/{numero}" + (f"?text={quote(mensagem)}" if mensagem else "")

    @staticmethod
    def registrar(db, historico_model, manutencao, usuario, tipo: str,
                  status: str = "ENVIADO", mensagem: str = ""):
        tipo = (tipo or "GERAL").upper()
        if tipo not in TIPOS_COMUNICACAO:
            tipo = "GERAL"
        agora = datetime.now()
        registro = historico_model(
            manutencao_id=getattr(manutencao, "id", None),
            cliente_id=getattr(manutencao, "cliente_id", None),
            usuario_id=getattr(usuario, "id", None),
            tipo=tipo,
            status=status,
            mensagem=mensagem,
            enviado_em=agora,
        )
        db.add(registro)
        if manutencao is not None:
            manutencao.comunicado = 1
            manutencao.ultima_comunicacao_em = agora
            manutencao.ultima_comunicacao_tipo = tipo
        db.commit()
        return registro

    @classmethod
    def registrar_e_url(cls, db, historico_model, manutencao, usuario,
                        tipo: str, mensagem: str):
        cls.registrar(db, historico_model, manutencao, usuario, tipo, mensagem=mensagem)
        return cls.url_whatsapp(manutencao.cliente, mensagem)
