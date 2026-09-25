from urllib.parse import quote_plus, urlencode, urlparse, parse_qs
import base64
import hashlib
import csv
import html
import hmac
import os
import re
import json
import secrets
import io
import zipfile
from pathlib import Path
import unicodedata
import urllib.request
import urllib.error
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Header, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import ClientDisconnect
from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text, Float, LargeBinary, func, or_, inspect, text
from sqlalchemy.orm import Session, relationship, selectinload

from config import (
    ADMIN_NOME, ADMIN_SENHA, CHAVE_SESSAO, ORGANIZA_VERSAO, PUBLIC_BASE_URL, LOKAFEST_API_TOKEN,
    SOLVOZ_API_TOKEN, SOLVOZ_BASE_URL, SOLVOZ_API_TIMEOUT,
)
from database import Base, SessionLocal, engine, get_db
from humiat_id import (
    router as humiat_router, seed_humiat_id, migrar_humiat_id_schema,
    humiat_usuario_da_requisicao, TIPO_ADMIN_HUMIAT, TIPO_CLIENTE_EMPRESA,
    HumiatEmpresa, HumiatUsuario, HumiatUsuarioEmpresa, HumiatEmpresaProduto, HumiatProduto,
    HumiatUsuarioProduto, gerar_hash_senha_id,
    garantir_empresa_solvoz_humiat,
    permissoes_usuario_humiat, salvar_permissoes_usuario_humiat,
    usuario_humiat_interno, usuario_humiat_equipe_prioritaria, enviar_link_acesso_humiat,
    enviar_email_solvoz_senha_provisoria, enviar_email_solvoz_recuperacao, _enviar_resend_humiat,
)

from services.comunicacao import (
    ComunicacaoService, PAISES, formatar_telefone as formatar_telefone_internacional,
    normalizar_contato, numero_internacional, telefone_valido as telefone_internacional_valido,
)

app = FastAPI(title="Organiza | Karaokê RJ", version=ORGANIZA_VERSAO)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
ORGANIZA_VERSION = ORGANIZA_VERSAO
templates.env.globals["ORGANIZA_VERSION"] = ORGANIZA_VERSION
NFE_CONSULTA_URL = "https://consultadfe.fazenda.rj.gov.br/consultaDFe/paginas/consultaChaveAcesso.faces"
templates.env.globals["NFE_CONSULTA_URL"] = NFE_CONSULTA_URL

# Integração Google exclusiva do Organiza para atualizações de catálogo.
# Pode reaproveitar o mesmo OAuth Client do Google Cloud usado em outro sistema,
# desde que o callback do Organiza também esteja cadastrado no projeto Google.
ORGANIZA_GOOGLE_CLIENT_ID = (os.getenv("ORGANIZA_GOOGLE_CLIENT_ID") or os.getenv("GOOGLE_CALENDAR_CLIENT_ID") or "").strip()
ORGANIZA_GOOGLE_CLIENT_SECRET = (os.getenv("ORGANIZA_GOOGLE_CLIENT_SECRET") or os.getenv("GOOGLE_CALENDAR_CLIENT_SECRET") or "").strip()
ORGANIZA_GOOGLE_REDIRECT_URI = (os.getenv("ORGANIZA_GOOGLE_REDIRECT_URI") or f"{PUBLIC_BASE_URL.rstrip('/')}/organiza/google/callback").strip()
ORGANIZA_GOOGLE_CALENDAR_ID = (os.getenv("ORGANIZA_GOOGLE_CALENDAR_ID") or "primary").strip() or "primary"
ORGANIZA_GOOGLE_TZ = (os.getenv("ORGANIZA_GOOGLE_TIMEZONE") or "America/Sao_Paulo").strip() or "America/Sao_Paulo"
GOOGLE_OAUTH_SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive",
]

ATUALIZACAO_HORARIOS = {
    "CASA": list(range(10, 21)),  # 10:00 até 20:00, inclusive
    "LOJA": list(range(14, 19)),  # 14:00 até 18:00, inclusive
}
ATUALIZACAO_DURACAO_MINUTOS = 60

NFSE_PORTAL_URL = "https://www.nfse.gov.br/EmissorNacional/DPS/Pessoas"
NFSE_TIPO_MANUTENCAO = "manutencao"
NFSE_TIPO_ALUGUEL = "aluguel"
NFSE_CODIGO_SERVICO_PADRAO = "01.07.01"
NFSE_MUNICIPIO_PADRAO = "Rio de Janeiro"
NFSE_UF_PADRAO = "RJ"

# O usuário escolhe apenas o tipo operacional da nota. Os códigos fiscais ficam
# internos no Organiza e são enviados à extensão sem poluir a tela do usuário.
# A descrição abaixo é apenas um texto inicial: continua totalmente editável antes
# de preparar o rascunho no Emissor Nacional.
NFSE_DADOS_BANCARIOS = """Dados Bancarios:

Transferência
Banco 033 Santander
Ag 2991 - C/C 11.056381-8

Pix: 35.458.112/0001-01"""

NFSE_TIPOS_SERVICO = {
    NFSE_TIPO_MANUTENCAO: {
        "rotulo": "Manutenção",
        "codigo": "01.07.01",
        "descricao_padrao": f"Manutenção de Equipamento.\n\n{NFSE_DADOS_BANCARIOS}",
    },
    NFSE_TIPO_ALUGUEL: {
        "rotulo": "Aluguel",
        "codigo": "12.09.03",
        "descricao_padrao": f"Aluguel de Karaokê\n\n{NFSE_DADOS_BANCARIOS}",
    },
}

NFSE_SERVICO_META = {
    "01.07.01": {
        "codigo_texto": "01.07.01 - Suporte técnico em informática, inclusive instalação, configuração e manutenção de programas de computação e bancos de dados.",
        "nbs_codigo": "115013000",
        "nbs_texto": "115013000 - Serviços de suporte em tecnologia da informação (TI)",
    },
    "12.09.03": {
        "codigo_texto": "12.09.03 - Diversões eletrônicas ou não.",
        # NBS 2.0: locação de equipamentos para diversão e lazer. É o enquadramento
        # operacional adotado para Karaokê, fliperama e Just Dance quando a nota é
        # do tipo Aluguel. O usuário não precisa informar o código na tela.
        "nbs_codigo": "111024000",
        "nbs_texto": "111024000 - Arrendamento mercantil operacional ou locação de equipamentos para diversão e lazer",
    },
    # Mantido apenas para compatibilidade com rascunhos antigos já salvos.
    "14.01.01": {
        "codigo_texto": "14.01.01",
        "nbs_codigo": "120018900",
        "nbs_texto": "120018900",
    },
}
templates.env.globals["NFSE_PORTAL_URL"] = NFSE_PORTAL_URL

def nfse_tipo_por_codigo(codigo: str | None) -> str:
    normalizado = (codigo or "").strip().replace(".000", "")
    for tipo, meta in NFSE_TIPOS_SERVICO.items():
        if meta["codigo"] == normalizado:
            return tipo
    return NFSE_TIPO_MANUTENCAO

def nfse_codigo_por_tipo(tipo: str | None) -> str:
    chave = (tipo or NFSE_TIPO_MANUTENCAO).strip().lower()
    return NFSE_TIPOS_SERVICO.get(chave, NFSE_TIPOS_SERVICO[NFSE_TIPO_MANUTENCAO])["codigo"]

def nfse_rotulo_tipo(tipo: str | None) -> str:
    chave = (tipo or NFSE_TIPO_MANUTENCAO).strip().lower()
    return NFSE_TIPOS_SERVICO.get(chave, NFSE_TIPOS_SERVICO[NFSE_TIPO_MANUTENCAO])["rotulo"]

def nfse_descricao_padrao(tipo: str | None) -> str:
    chave = (tipo or NFSE_TIPO_MANUTENCAO).strip().lower()
    return NFSE_TIPOS_SERVICO.get(chave, NFSE_TIPOS_SERVICO[NFSE_TIPO_MANUTENCAO])["descricao_padrao"]

templates.env.globals["nfse_tipo_por_codigo"] = nfse_tipo_por_codigo
templates.env.globals["nfse_rotulo_tipo"] = nfse_rotulo_tipo
templates.env.globals["nfse_descricao_padrao"] = nfse_descricao_padrao

def nfse_norm_municipio(valor: str) -> str:
    return unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode("ascii").strip().lower()

# Padrão fiscal usado na preparação da NFA-e.
# A regra operacional definida pela Karaokê RJ mantém os campos fiscais padrão e
# calcula o CFOP conforme a UF do destinatário: 5102 para RJ e 6102 para outra UF.
# A primeira tela da NFA-e continua manual, mas o Organiza/Extensão orienta o
# usuário a marcar Interna ou Interestadual antes de iniciar o preenchimento.
NFAE_PADRAO_FISCAL = {
    "natureza_operacao": "Venda de Mercadoria",
    "grupo_cfop": "Venda de Mercadoria",
    "ncm": "95045000",
    "ean": "SEM GTIN",
    "unidade": "UN",
    "quantidade": 1,
    "origem": "0",
    "csosn": "102",
    "pis_cst": "07",
    "cofins_cst": "07",
    "valor_compoe_total": True,
}

NFAE_PRODUTOS_PADRAO = {
    "JUKEBOX": ("00001", "Jukebox"),
    "MALETA": ("00002", "Maletaokê"),
    "IPHONE": ("00003", "Karaokê iPhone"),
    "FLIPERAMA": ("00004", "Fliperama"),
}

app.include_router(humiat_router)

PREFIXOS_EQUIPAMENTO = {
    "JUKEBOX": "JUK",
    "MALETA": "MAL",
    "IPHONE": "IPH",
    "FLIPERAMA": "FLIP",
}

def tipo_equipamento_padrao(tipo: str) -> str:
    valor = unicodedata.normalize("NFKD", (tipo or "").upper()).encode("ascii", "ignore").decode("ascii").strip()
    aliases = {"IPHON": "IPHONE", "FLIPER": "FLIPERAMA", "ARCADE": "FLIPERAMA"}
    return aliases.get(valor, valor)

def prefixo_equipamento(tipo: str) -> str:
    return PREFIXOS_EQUIPAMENTO.get(tipo_equipamento_padrao(tipo), "EQP")

def rotulo_maquina(equipamento) -> str:
    """Identificação operacional definida pelo cliente: JUK1, MAL2, IPH1, FLIP3."""
    numero = getattr(equipamento, "numero_maquina_cliente", None)
    prefixo = prefixo_equipamento(getattr(equipamento, "tipo", None))
    return f"{prefixo}{numero}" if numero else f"{prefixo}?"

def descricao_equipamento(equipamento) -> str:
    partes = [rotulo_maquina(equipamento)]
    tipo = (getattr(equipamento, "tipo", None) or "EQUIPAMENTO").strip()
    modelo = (getattr(equipamento, "modelo", None) or "").strip()
    partes.append(tipo + (f" {modelo}" if modelo else ""))
    return " · ".join(partes)

def codigo_tecnico(equipamento) -> str:
    return (getattr(equipamento, "maquina", None) or f"Equipamento #{getattr(equipamento, 'id', '?')}").strip()

templates.env.filters["rotulo_maquina"] = rotulo_maquina
templates.env.filters["descricao_equipamento"] = descricao_equipamento
templates.env.filters["codigo_tecnico"] = codigo_tecnico


def normalizar_slug_solvoz(valor: str) -> str:
    slug = unicodedata.normalize("NFKD", (valor or "").strip().lower()).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug


def dominio_solvoz_por_slug(slug: str) -> str:
    slug = normalizar_slug_solvoz(slug)
    if slug == "karaokerj":
        return "https://www.karaokerj.com.br/catalogo"
    return f"https://www.solvoz.com.br/{slug}" if slug else "https://www.solvoz.com.br"


def montar_url_qr_solvoz(empresa, maquina: str, plano: str, catalogo_online: bool) -> str:
    """Monta a URL pública do QR sem duplicar regras no restante do sistema."""
    base = (getattr(empresa, "dominio", None) or dominio_solvoz_por_slug(getattr(empresa, "slug", ""))).rstrip("/")
    # Domínios raiz usam barra explícita para manter o padrão dos QR atuais.
    if re.fullmatch(r"https?://[^/]+", base):
        base += "/"
    if not catalogo_online:
        return base
    separador = "&" if "?" in base else "?"
    return f"{base}{separador}maquina={quote(maquina)}&plano={quote(plano.upper())}"


def empresas_solvoz_ativas(db: Session):
    return db.query(SolVozEmpresa).filter(SolVozEmpresa.ativo == 1).order_by(SolVozEmpresa.nome.asc()).all()


# ------------------------------------------------------------------
# Organiza 8.8 / v1.1.2 — criação de acesso diretamente no SolVoz
# ------------------------------------------------------------------
def _solvoz_integracao_configurada() -> bool:
    return bool(SOLVOZ_API_TOKEN and SOLVOZ_BASE_URL)


def _solvoz_api_request(caminho: str, *, metodo: str = "GET", payload: dict | None = None, query: dict | None = None) -> dict:
    """Chamada servidor-servidor para o SolVoz usando o token compartilhado.

    Nunca envia senha do Organiza. O SolVoz cria/gera a própria credencial.
    """
    if not _solvoz_integracao_configurada():
        raise RuntimeError("Integração Organiza → SolVoz não configurada (SOLVOZ_API_TOKEN).")
    url = SOLVOZ_BASE_URL.rstrip("/") + "/" + caminho.lstrip("/")
    if query:
        qs = urllib.parse.urlencode({k: v for k, v in query.items() if v not in (None, "")})
        if qs:
            url += ("&" if "?" in url else "?") + qs
    headers = {
        "Accept": "application/json",
        "X-SolVoz-Token": SOLVOZ_API_TOKEN,
        "User-Agent": f"Organiza/{ORGANIZA_VERSION}",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=metodo.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=SOLVOZ_API_TIMEOUT) as resp:
            bruto = resp.read().decode("utf-8", errors="replace")
            return json.loads(bruto) if bruto else {"ok": True}
    except urllib.error.HTTPError as exc:
        detalhe = exc.read().decode("utf-8", errors="replace")
        try:
            obj = json.loads(detalhe)
            detalhe = obj.get("detail") or obj.get("erro") or detalhe
        except Exception:
            pass
        raise RuntimeError(f"SolVoz respondeu HTTP {exc.code}: {str(detalhe)[:300]}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Não foi possível acessar o SolVoz: {exc.reason}")


def _solvoz_grupos_cliente(cliente) -> list[dict]:
    """Agrupa equipamentos online do cliente pela empresa SolVoz."""
    grupos: dict[int, dict] = {}
    for eq in list(getattr(cliente, "equipamentos", []) or []):
        empresa = getattr(eq, "solvoz_empresa", None)
        codigo = codigo_tecnico(eq)
        if not getattr(eq, "catalogo_online", 0) or not empresa or not getattr(empresa, "id", None) or not codigo:
            continue
        grupo = grupos.setdefault(int(empresa.id), {
            "empresa": empresa,
            "equipamentos": [],
            "codigos": [],
        })
        grupo["equipamentos"].append(eq)
        grupo["codigos"].append(codigo)
    return list(grupos.values())



def _solvoz_entregar_senha_provisoria(cliente, resultados: list[dict], grupos: list[dict]) -> list[dict]:
    """Envia pelo Resend do Organiza a senha provisória criada pelo SolVoz.

    A senha existe em texto puro somente na resposta privada servidor-servidor e
    nesta chamada de envio. Ela não é salva no banco do Organiza nem registrada
    em logs.
    """
    alvo = next((r for r in resultados if str(r.get("senha_provisoria") or "").strip()), None)
    if not alvo:
        # Usuário já existente: houve apenas atualização de vínculos/equipamentos.
        for item in resultados:
            item.pop("senha_provisoria", None)
        return resultados

    senha = str(alvo.get("senha_provisoria") or "")
    empresas = []
    equipamentos = []
    for grupo in grupos:
        empresa = grupo.get("empresa")
        nome_empresa = str(getattr(empresa, "nome", "") or "").strip()
        if nome_empresa and nome_empresa not in empresas:
            empresas.append(nome_empresa)
        for eq in grupo.get("equipamentos") or []:
            descricao = f"{rotulo_maquina(eq)} — {codigo_tecnico(eq)}"
            if descricao not in equipamentos:
                equipamentos.append(descricao)

    empresa_nome = ", ".join(empresas) or str((alvo.get("empresa") or {}).get("nome") or "SolVoz")
    acesso_url = str(alvo.get("acesso_url") or "").strip()
    email = (getattr(cliente, "email", None) or "").strip().lower()
    nome = (getattr(cliente, "nome", None) or "").strip()

    try:
        enviar_email_solvoz_senha_provisoria(
            email,
            nome,
            empresa_nome,
            senha,
            acesso_url,
            equipamentos,
        )
        alvo["email_enviado"] = True
        alvo["email_erro"] = ""
    except Exception as exc:
        alvo["email_enviado"] = False
        alvo["email_erro"] = str(exc)[:500]
    finally:
        # Nunca deixe a senha seguir adiante para cache, template ou mensagens.
        for item in resultados:
            item.pop("senha_provisoria", None)
    return resultados


def _solvoz_provisionar_cliente(cliente, grupos: list[dict]) -> list[dict]:
    email = (getattr(cliente, "email", None) or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("O cliente precisa ter um e-mail válido no Organiza.")
    if not grupos:
        raise ValueError("O cliente não possui equipamento com Catálogo Online e empresa SolVoz vinculados.")
    resultados = []
    grupos_processados = []
    for grupo in grupos:
        empresa = grupo["empresa"]
        payload = {
            "nome": (getattr(cliente, "nome", None) or "").strip(),
            "email": email,
            "documento": (getattr(cliente, "documento", None) or "").strip(),
            "telefone": (getattr(cliente, "telefone", None) or "").strip(),
            "cliente_id": str(getattr(cliente, "id", "") or ""),
            "empresa_slug": (getattr(empresa, "slug", None) or "").strip(),
            "equipamentos": list(dict.fromkeys(grupo["codigos"])),
        }
        try:
            resultado = _solvoz_api_request(
                "/api/integracoes/organiza/solvoz/acesso", metodo="POST", payload=payload
            )
            resultados.append(resultado)
            grupos_processados.append(grupo)
        except Exception:
            # Se a primeira empresa já criou uma senha antes de outra empresa
            # falhar, ainda entregamos essa credencial. Assim um erro parcial não
            # deixa uma senha criada no SolVoz sem chegar ao cliente.
            if resultados:
                _solvoz_entregar_senha_provisoria(cliente, resultados, grupos_processados)
            raise
    return _solvoz_entregar_senha_provisoria(cliente, resultados, grupos)


def _solvoz_cache_salvar(db: Session, cliente, resultados: list[dict], *, erro: str = "") -> None:
    email = (getattr(cliente, "email", None) or "").strip().lower()
    registro = db.query(SolVozAcessoCliente).filter(SolVozAcessoCliente.cliente_id == int(cliente.id)).first()
    if not registro:
        registro = SolVozAcessoCliente(cliente_id=int(cliente.id), email=email)
        db.add(registro)
    primeiro = resultados[0] if resultados else {}
    registro.email = email
    if primeiro.get("usuario_id"):
        registro.solvoz_usuario_id = int(primeiro.get("usuario_id"))
    registro.status = "ATIVO" if resultados else "ERRO"
    registro.trocar_senha = 1 if any(bool(r.get("trocar_senha_primeiro_acesso")) for r in resultados) else 0
    houve_nova_credencial = any(bool(r.get("senha_provisoria_gerada")) for r in resultados)
    if houve_nova_credencial:
        registro.email_enviado = 1 if any(bool(r.get("email_enviado")) for r in resultados) else 0
    erros = [str(r.get("email_erro") or "").strip() for r in resultados if r.get("email_erro")]
    if erro or erros:
        registro.ultimo_erro = (erro or erros[0])[:500] or None
    elif houve_nova_credencial:
        registro.ultimo_erro = None
    registro.atualizado_em = datetime.now()
    db.commit()


def _solvoz_contexto_cliente(cliente, db: Session) -> dict:
    """Monta o card sem bloquear a ficha do cliente com consulta HTTP externa."""
    grupos = _solvoz_grupos_cliente(cliente)
    contexto = {
        "configurado": _solvoz_integracao_configurada(),
        "status": "SEM_ACESSO",
        "usuario": (getattr(cliente, "email", None) or "").strip().lower(),
        "equipamentos": [
            f"{rotulo_maquina(eq)} · {codigo_tecnico(eq)}"
            for grupo in grupos for eq in grupo["equipamentos"]
        ],
        "empresas": [str(getattr(grupo["empresa"], "nome", "") or "") for grupo in grupos],
        "total_equipamentos": sum(len(grupo["equipamentos"]) for grupo in grupos),
        "trocar_senha": False,
        "erro": "",
        "email_enviado": False,
    }
    email = contexto["usuario"]
    if not email or "@" not in email:
        contexto["status"] = "SEM_EMAIL"
        return contexto
    if not grupos:
        contexto["status"] = "SEM_EQUIPAMENTO"
        return contexto
    if not contexto["configurado"]:
        contexto["status"] = "NAO_CONFIGURADO"
        return contexto
    registro = db.query(SolVozAcessoCliente).filter(SolVozAcessoCliente.cliente_id == int(cliente.id)).first()
    if registro and (registro.email or "").strip().lower() == email:
        contexto["status"] = (registro.status or "ATIVO").upper()
        contexto["trocar_senha"] = bool(registro.trocar_senha)
        contexto["email_enviado"] = bool(registro.email_enviado)
        contexto["erro"] = registro.ultimo_erro or ""
        contexto["usuario_id"] = registro.solvoz_usuario_id
        contexto["atualizado_em"] = registro.atualizado_em
    return contexto



STATUS_EQUIPE = {"Aguardando equipamento", "Recebida", "Orçamento em elaboração", "Confirmação pendente", "Em manutenção", "Aguardando peça"}
STATUS_CLIENTE = {"Aguardando aprovação", "Aprovado", "Pronto para retirada", "Retirada agendada"}
STATUS_FINAL = {"Encerrada", "Cancelada"}

def responsabilidade_status(status: str) -> str:
    if status in STATUS_FINAL:
        return "finalizado"
    if status in STATUS_CLIENTE:
        return "cliente"
    return "equipe"

def classe_status(status: str) -> str:
    return f"status-{responsabilidade_status(status)}"

def rotulo_status(status: str) -> str:
    mapa = {
        "Aguardando equipamento": "Entrada agendada",
        "Recebida": "Orçamento",
        "Orçamento em elaboração": "Orçamento",
        "Aguardando aprovação": "Aguardando cliente",
        "Aprovado": "Pagamento ou prazo",
        "Confirmação pendente": "Enviar confirmação",
        "Em manutenção": "Execução do serviço",
        "Aguardando peça": "Execução pausada",
        "Pronto para retirada": "Pronto para retirada",
        "Retirada agendada": "Retirada agendada",
        "Encerrada": "Encerrado",
        "Cancelada": "Cancelado",
    }
    return mapa.get(status, status or "Sem status")

templates.env.filters["classe_status"] = classe_status
templates.env.filters["rotulo_status"] = rotulo_status


class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True)
    nome = Column(String(80), unique=True, nullable=False)
    telefone = Column(String(20), nullable=True)
    email = Column(String(140), nullable=True)
    cargo = Column(String(80), nullable=True)
    senha_hash = Column(String(255), nullable=False)
    is_admin = Column(Integer, nullable=False, default=0)
    ativo = Column(Integer, nullable=False, default=1)
    criado_em = Column(DateTime, server_default=func.now())


class ConfiguracaoSistema(Base):
    __tablename__ = "configuracoes_sistema"
    id = Column(Integer, primary_key=True)
    chave = Column(String(80), unique=True, nullable=False)
    valor = Column(String(120), nullable=True)
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())




class Cliente(Base):
    __tablename__ = "clientes"
    id = Column(Integer, primary_key=True)
    nome = Column(String(140), nullable=False)
    telefone = Column(String(20), nullable=False)
    pais = Column(String(2), nullable=False, default="BR")
    ddi = Column(String(5), nullable=False, default="55")
    empresa = Column(String(140), nullable=True)
    razao_social = Column(String(180), nullable=True)
    documento = Column(String(30), nullable=True)
    cep = Column(String(20), nullable=True)
    cidade = Column(String(120), nullable=True)
    municipio = Column(String(120), nullable=True)
    estado = Column(String(60), nullable=True)
    bairro = Column(String(120), nullable=True)
    endereco = Column(String(255), nullable=True)
    endereco_numero = Column(String(30), nullable=True)
    complemento = Column(String(120), nullable=True)
    # Endereço de entrega opcional. Por padrão, a entrega usa o endereço do cliente.
    entrega_igual_cliente = Column(Integer, nullable=False, default=1)
    entrega_cep = Column(String(20), nullable=True)
    entrega_endereco = Column(String(255), nullable=True)
    entrega_numero = Column(String(30), nullable=True)
    entrega_complemento = Column(String(120), nullable=True)
    entrega_bairro = Column(String(120), nullable=True)
    entrega_municipio = Column(String(120), nullable=True)
    entrega_municipio_ibge = Column(String(12), nullable=True)
    entrega_estado = Column(String(60), nullable=True)
    email = Column(String(140), nullable=True)
    # Identidade central: o cadastro do cliente no Organiza é a fonte de verdade
    # e aponta para o único Humiat ID usado em todos os produtos.
    humiat_usuario_id = Column(Integer, ForeignKey("humiat_usuarios.id"), nullable=True, unique=True, index=True)
    pacote = Column(String(30), nullable=True)
    falta_pacote = Column(Integer, nullable=True)
    plano = Column(String(60), nullable=True)
    observacao = Column(Text, nullable=True)
    # Controle exclusivo de campanhas. Não altera o status operacional do cliente.
    campanhas_ativo = Column(Integer, nullable=False, default=1)
    # Oferta de atualização enviada pelo Organiza e acompanhada pelo SolVoz.
    # O pagamento não muda automaticamente o pacote instalado; apenas registra
    # o estágio comercial da oferta no cadastro do cliente.
    atualizacao_oferta_status = Column(String(30), nullable=True)
    atualizacao_oferta_periodo = Column(String(120), nullable=True)
    atualizacao_oferta_pacotes = Column(Text, nullable=True)
    atualizacao_oferta_valor_normal_centavos = Column(Integer, nullable=True)
    atualizacao_oferta_valor_promocional_centavos = Column(Integer, nullable=True)
    atualizacao_oferta_order_nsu = Column(String(120), nullable=True)
    atualizacao_oferta_atualizado_em = Column(DateTime, nullable=True)
    token_ficha = Column(String(64), nullable=True, unique=True)
    inscricao_estadual = Column(String(30), nullable=True)
    situacao_icms = Column(String(30), nullable=True)
    cnpj_situacao_cadastral = Column(String(40), nullable=True)
    cnpj_fonte = Column(String(80), nullable=True)
    cnpj_consultado_em = Column(DateTime, nullable=True)
    municipio_ibge = Column(String(12), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    equipamentos = relationship("Equipamento", back_populates="cliente", cascade="all, delete-orphan")

    def whatsapp_completo(self):
        return numero_internacional(self)

    def telefone_formatado(self):
        return formatar_telefone_internacional(self.pais, self.telefone)



class SolVozEmpresa(Base):
    """Empresas públicas do SolVoz conhecidas pelo Organiza.

    O domínio é derivado do slug para evitar digitação divergente na implantação:
    karaokerj -> https://www.karaokerj.com.br/catalogo
    demais    -> https://www.solvoz.com.br/<slug>
    """
    __tablename__ = "solvoz_empresas"
    id = Column(Integer, primary_key=True)
    nome = Column(String(140), nullable=False)
    slug = Column(String(100), nullable=False, unique=True)
    dominio = Column(String(255), nullable=False)
    ativo = Column(Integer, nullable=False, default=1)
    criado_em = Column(DateTime, server_default=func.now())


class SolVozAcessoCliente(Base):
    """Cache local do acesso criado no SolVoz.

    Evita consultar o SolVoz toda vez que a ficha do cliente é aberta.
    O SolVoz continua sendo a fonte da autenticação/senha; aqui guardamos apenas
    o estado operacional para exibir o card no Organiza.
    """
    __tablename__ = "solvoz_acessos_clientes"
    id = Column(Integer, primary_key=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False, unique=True, index=True)
    email = Column(String(180), nullable=False)
    solvoz_usuario_id = Column(Integer, nullable=True)
    status = Column(String(30), nullable=False, default="ATIVO")
    trocar_senha = Column(Integer, nullable=False, default=1)
    email_enviado = Column(Integer, nullable=False, default=0)
    ultimo_erro = Column(Text, nullable=True)
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Equipamento(Base):
    __tablename__ = "equipamentos"
    id = Column(Integer, primary_key=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    tipo = Column(String(80), nullable=True)
    modelo = Column(String(180), nullable=True)
    pacote = Column(String(30), nullable=True)
    falta_pacote = Column(Integer, nullable=True)
    plano = Column(String(60), nullable=True)
    valor = Column(String(30), nullable=True)
    preco_custo = Column(String(40), nullable=True)
    preco_venda = Column(String(40), nullable=True)
    pago = Column(String(30), nullable=True)
    falta = Column(String(30), nullable=True)
    data_compra = Column(Date, nullable=True)
    previsao_entrega = Column(Date, nullable=True)
    maquina = Column(String(120), nullable=True)
    rede_instalada = Column(String(120), nullable=True)
    anydesk = Column(String(120), nullable=True)
    status = Column(String(30), nullable=False, default="Ativo")
    observacao = Column(Text, nullable=True)
    garantia_meses = Column(Integer, nullable=True, default=3)
    numero_serie = Column(String(120), nullable=True)  # legado; mantido vazio
    numero_hd = Column(String(160), nullable=True)
    numero_maquina_cliente = Column(Integer, nullable=True)
    fabricante = Column(String(80), nullable=False, default="KARAOKERJ")
    # Integração opcional com o catálogo online SolVoz.
    # O plano já existe no equipamento e continua sendo a fonte oficial.
    solvoz_empresa_id = Column(Integer, ForeignKey("solvoz_empresas.id"), nullable=True)
    catalogo_online = Column(Integer, nullable=False, default=0)
    nota_codigo = Column(String(20), nullable=True)
    nota_descricao = Column(String(180), nullable=True)
    chave_acesso_nfe = Column(String(44), nullable=True)
    som = Column(String(20), nullable=False, default="NA")
    hdmi_tela_2 = Column(String(10), nullable=False, default="NA")
    teclado_bluetooth = Column(String(10), nullable=False, default="NA")
    microfone = Column(String(20), nullable=False, default="Com fio")
    sistema_credito = Column(String(20), nullable=False, default="NA")
    catalogo_impresso = Column(String(10), nullable=False, default="NA")
    criado_em = Column(DateTime, server_default=func.now())
    cliente = relationship("Cliente", back_populates="equipamentos")
    solvoz_empresa = relationship("SolVozEmpresa")


class Campanha(Base):
    __tablename__ = "campanhas"
    id = Column(Integer, primary_key=True)
    nome = Column(String(160), nullable=False)
    lista_tipo = Column(String(30), nullable=False, default="ATUALIZACAO")
    mensagem = Column(Text, nullable=False)
    link = Column(String(1000), nullable=True)
    pacote_alvo = Column(String(30), nullable=True)
    # Para campanhas de aluguel, permite segmentar pelo último mês de locação.
    # NULL = todos os meses.
    aluguel_mes = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="RASCUNHO")
    criado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    imagem_nome = Column(String(180), nullable=True)
    imagem_mime = Column(String(80), nullable=True)
    imagem_bytes = Column(LargeBinary, nullable=True)
    imagem_token = Column(String(64), nullable=True, unique=True, index=True)
    criado_em = Column(DateTime, server_default=func.now())
    iniciado_em = Column(DateTime, nullable=True)
    finalizado_em = Column(DateTime, nullable=True)
    criado_por = relationship("Usuario")
    destinatarios = relationship("CampanhaDestinatario", back_populates="campanha", cascade="all, delete-orphan")


class CampanhaDestinatario(Base):
    __tablename__ = "campanha_destinatarios"
    id = Column(Integer, primary_key=True)
    campanha_id = Column(Integer, ForeignKey("campanhas.id"), nullable=False, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="PENDENTE", index=True)
    reservado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    reservado_em = Column(DateTime, nullable=True)
    enviado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    enviado_em = Column(DateTime, nullable=True)
    # Snapshot calculado ao iniciar a campanha. Evita recalcular a cada cliente
    # e permite dois atendentes trabalharem na mesma fila sem divergência.
    mensagem_pronta = Column(Text, nullable=True)
    telefone_pronto = Column(String(40), nullable=True)
    link_pronto = Column(String(1000), nullable=True)
    pacotes_prontos = Column(Text, nullable=True)
    valor_normal_centavos = Column(Integer, nullable=True)
    valor_promocional_centavos = Column(Integer, nullable=True)
    lote_numero = Column(Integer, nullable=True, index=True)
    criado_em = Column(DateTime, server_default=func.now())
    campanha = relationship("Campanha", back_populates="destinatarios")
    cliente = relationship("Cliente")
    reservado_por = relationship("Usuario", foreign_keys=[reservado_por_id])
    enviado_por = relationship("Usuario", foreign_keys=[enviado_por_id])


class CampanhaAluguelContato(Base):
    """Contato exclusivo da Lista de Aluguel para campanhas.

    Não cria nem altera o cadastro operacional de Cliente. A duplicidade da
    lista é controlada pelo número internacional normalizado.
    """
    __tablename__ = "campanha_aluguel_contatos"
    id = Column(Integer, primary_key=True)
    nome = Column(String(180), nullable=False)
    pais = Column(String(2), nullable=False, default="BR")
    ddi = Column(String(5), nullable=False, default="55")
    telefone = Column(String(20), nullable=False)
    numero_chave = Column(String(30), nullable=False, unique=True, index=True)
    campanhas_ativo = Column(Integer, nullable=False, default=1)
    # Origem histórica mantida por compatibilidade. O campo de negócio exibido
    # na tela é `integracao`: PLANILHA ou CONNECT.
    origem = Column(String(60), nullable=True, default="PLANILHA")
    integracao = Column(String(20), nullable=False, default="PLANILHA", index=True)
    ultimo_mes_aluguel = Column(Integer, nullable=True, index=True)
    ultimo_aluguel_em = Column(Date, nullable=True)
    ultima_sincronizacao_em = Column(DateTime, nullable=True)
    connect_cliente_id = Column(Integer, nullable=True, index=True)
    connect_solicitacao_id = Column(Integer, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def whatsapp_completo(self):
        return f"{self.ddi or ''}{self.telefone or ''}"

    def telefone_formatado(self):
        return formatar_telefone_internacional(self.pais, self.telefone)


class CampanhaAluguelDestinatario(Base):
    __tablename__ = "campanha_aluguel_destinatarios"
    id = Column(Integer, primary_key=True)
    campanha_id = Column(Integer, ForeignKey("campanhas.id"), nullable=False, index=True)
    contato_id = Column(Integer, ForeignKey("campanha_aluguel_contatos.id"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="PENDENTE", index=True)
    reservado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    reservado_em = Column(DateTime, nullable=True)
    enviado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    enviado_em = Column(DateTime, nullable=True)
    # Snapshot calculado ao iniciar a campanha. Evita recalcular a cada cliente
    # e permite dois atendentes trabalharem na mesma fila sem divergência.
    mensagem_pronta = Column(Text, nullable=True)
    telefone_pronto = Column(String(40), nullable=True)
    link_pronto = Column(String(1000), nullable=True)
    pacotes_prontos = Column(Text, nullable=True)
    valor_normal_centavos = Column(Integer, nullable=True)
    valor_promocional_centavos = Column(Integer, nullable=True)
    lote_numero = Column(Integer, nullable=True, index=True)
    criado_em = Column(DateTime, server_default=func.now())
    campanha = relationship("Campanha")
    contato = relationship("CampanhaAluguelContato")
    reservado_por = relationship("Usuario", foreign_keys=[reservado_por_id])
    enviado_por = relationship("Usuario", foreign_keys=[enviado_por_id])


class CampanhaLote(Base):
    __tablename__ = "campanha_lotes"
    id = Column(Integer, primary_key=True)
    campanha_id = Column(Integer, ForeignKey("campanhas.id"), nullable=False, index=True)
    numero = Column(Integer, nullable=False, index=True)
    total = Column(Integer, nullable=False, default=0)
    reservado_por_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True, index=True)
    reservado_em = Column(DateTime, nullable=True)
    concluido_em = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    campanha = relationship("Campanha")
    reservado_por = relationship("Usuario")


CAMPANHA_LOTE_TAMANHO = 100


class TransferenciaEquipamento(Base):
    __tablename__ = "equipamento_transferencias"
    id = Column(Integer, primary_key=True)
    equipamento_id = Column(Integer, ForeignKey("equipamentos.id"), nullable=False)
    cliente_origem_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    cliente_destino_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    equipamento = relationship("Equipamento")
    cliente_origem = relationship("Cliente", foreign_keys=[cliente_origem_id])
    cliente_destino = relationship("Cliente", foreign_keys=[cliente_destino_id])


class Item(Base):
    __tablename__ = "catalogo_itens"
    id = Column(Integer, primary_key=True)
    codigo = Column(String(30), nullable=True)
    nome = Column(String(180), unique=True, nullable=False)
    categoria = Column(String(80), nullable=False, default="Geral")
    preco_custo = Column(Float, nullable=False, default=0)
    preco_venda = Column(Float, nullable=False, default=0)
    ativo = Column(Integer, nullable=False, default=1)
    criado_em = Column(DateTime, server_default=func.now())


class AgendaManual(Base):
    __tablename__ = "agenda_manual"
    id = Column(Integer, primary_key=True)
    titulo = Column(String(180), nullable=False)
    tipo = Column(String(40), nullable=False, default="visita")
    data_hora = Column(DateTime, nullable=False)
    contato = Column(String(120), nullable=True)
    observacao = Column(Text, nullable=True)
    google_event_id = Column(String(255), nullable=True)
    google_sync_status = Column(String(30), nullable=True)
    google_sync_erro = Column(Text, nullable=True)
    google_sync_em = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())


class AtualizacaoPacote(Base):
    __tablename__ = "atualizacao_pacotes"
    id = Column(Integer, primary_key=True)
    pacote = Column(String(30), nullable=False, unique=True, index=True)
    drive_url = Column(String(1000), nullable=True)
    drive_file_id = Column(String(255), nullable=True)
    ativo = Column(Integer, nullable=False, default=1)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AtualizacaoCompra(Base):
    __tablename__ = "atualizacao_compras"
    id = Column(Integer, primary_key=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False, index=True)
    origem = Column(String(30), nullable=False, default="MANUAL")
    order_nsu = Column(String(120), nullable=True, unique=True, index=True)
    pacote_inicio = Column(String(30), nullable=False)
    pacote_fim = Column(String(30), nullable=False)
    pacotes = Column(Text, nullable=False)
    valor_normal_centavos = Column(Integer, nullable=True)
    valor_pago_centavos = Column(Integer, nullable=True)
    forma_pagamento = Column(String(60), nullable=True)
    status = Column(String(30), nullable=False, default="PAGO", index=True)
    pago_em = Column(DateTime, nullable=True)
    arquivos_liberados_em = Column(DateTime, nullable=True)
    email_arquivos_enviado_em = Column(DateTime, nullable=True)
    email_erro = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())
    cliente = relationship("Cliente")


class AtualizacaoAgendamento(Base):
    __tablename__ = "atualizacao_agendamentos"
    id = Column(Integer, primary_key=True)
    compra_id = Column(Integer, ForeignKey("atualizacao_compras.id"), nullable=False, unique=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False, index=True)
    tipo = Column(String(20), nullable=False)
    data_hora = Column(DateTime, nullable=False, index=True)
    duracao_minutos = Column(Integer, nullable=False, default=60)
    status = Column(String(30), nullable=False, default="RESERVADO", index=True)
    google_event_id = Column(String(255), nullable=True)
    google_sync_status = Column(String(30), nullable=True)
    google_sync_erro = Column(Text, nullable=True)
    email_confirmacao_em = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())
    compra = relationship("AtualizacaoCompra")
    cliente = relationship("Cliente")


class OrganizaGoogleIntegracao(Base):
    __tablename__ = "organiza_google_integracao"
    id = Column(Integer, primary_key=True)
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    account_email = Column(String(180), nullable=True)
    calendar_id = Column(String(255), nullable=False, default="primary")
    scopes = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Manutencao(Base):
    __tablename__ = "assistencias"
    id = Column(Integer, primary_key=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    equipamento_id = Column(Integer, ForeignKey("equipamentos.id"), nullable=False)
    defeito = Column(Text, nullable=False)
    diagnostico = Column(Text, nullable=True)
    status = Column(String(40), nullable=False, default="Recebida")
    entrega_prevista_em = Column(DateTime, nullable=True)
    tipo_atendimento = Column(String(20), nullable=False, default="loja")
    recebido_em = Column(DateTime, nullable=True)
    prazo = Column(String(120), nullable=True)
    pronto_em = Column(DateTime, nullable=True)
    retirada_em = Column(DateTime, nullable=True)
    entregue_em = Column(DateTime, nullable=True)
    confirmacao_prazo_em = Column(DateTime, nullable=True)
    servico_pausado_em = Column(DateTime, nullable=True)
    compra_descricao = Column(Text, nullable=True)
    compra_previsao = Column(String(120), nullable=True)
    compra_comunicada_em = Column(DateTime, nullable=True)
    conclusao_comunicada_em = Column(DateTime, nullable=True)
    observacao = Column(Text, nullable=True)
    comunicado = Column(Integer, nullable=False, default=0)
    ultima_comunicacao_em = Column(DateTime, nullable=True)
    ultima_comunicacao_tipo = Column(String(30), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    cliente = relationship("Cliente")
    equipamento = relationship("Equipamento")
    orcamentos = relationship("Orcamento", back_populates="manutencao", cascade="all, delete-orphan")


class HistoricoComunicacao(Base):
    __tablename__ = "historico_comunicacoes"
    id = Column(Integer, primary_key=True)
    manutencao_id = Column(Integer, ForeignKey("assistencias.id"), nullable=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    tipo = Column(String(30), nullable=False, default="GERAL")
    status = Column(String(30), nullable=False, default="ENVIADO")
    mensagem = Column(Text, nullable=True)
    enviado_em = Column(DateTime, nullable=False, default=datetime.now)
    manutencao = relationship("Manutencao")
    cliente = relationship("Cliente")
    usuario = relationship("Usuario")


class Orcamento(Base):
    __tablename__ = "assistencia_orcamentos"
    id = Column(Integer, primary_key=True)
    manutencao_id = Column(Integer, ForeignKey("assistencias.id"), nullable=False)
    versao = Column(Integer, nullable=False, default=1)
    token = Column(String(64), unique=True, nullable=False)
    status = Column(String(40), nullable=False, default="Rascunho")
    observacao = Column(Text, nullable=True)
    desconto = Column(Float, nullable=False, default=0)
    # Quando ativo, o desconto só é concedido se todos os itens opcionais forem aprovados.
    desconto_somente_com_opcionais = Column(Integer, nullable=False, default=0)
    valor_manutencao = Column(Float, nullable=False, default=0)
    forma_pagamento_orcamento = Column(String(80), nullable=True)
    prazo_dias_uteis = Column(Integer, nullable=True)
    aprovado_em = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    manutencao = relationship("Manutencao", back_populates="orcamentos")
    itens = relationship("OrcamentoItem", back_populates="orcamento", cascade="all, delete-orphan")
    pagamentos = relationship("Pagamento", back_populates="orcamento", cascade="all, delete-orphan")


class OrcamentoItem(Base):
    __tablename__ = "assistencia_orcamento_itens"
    id = Column(Integer, primary_key=True)
    orcamento_id = Column(Integer, ForeignKey("assistencia_orcamentos.id"), nullable=False)
    item_id = Column(Integer, ForeignKey("catalogo_itens.id"), nullable=True)
    descricao = Column(String(220), nullable=False)
    quantidade = Column(Integer, nullable=False, default=1)
    preco_custo = Column(Float, nullable=False, default=0)
    preco_venda = Column(Float, nullable=False, default=0)
    opcional = Column(Integer, nullable=False, default=0)
    aprovado = Column(Integer, nullable=False, default=1)
    orcamento = relationship("Orcamento", back_populates="itens")


class Pagamento(Base):
    __tablename__ = "assistencia_pagamentos"
    id = Column(Integer, primary_key=True)
    orcamento_id = Column(Integer, ForeignKey("assistencia_orcamentos.id"), nullable=False)
    data = Column(Date, nullable=False, default=date.today)
    valor = Column(Float, nullable=False)
    forma = Column(String(40), nullable=True)
    banco = Column(String(120), nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    orcamento = relationship("Orcamento", back_populates="pagamentos")


class PagamentoVenda(Base):
    """Recebimentos de venda. Uma venda pode ter vários pagamentos, bancos e datas."""
    __tablename__ = "venda_pagamentos"
    id = Column(Integer, primary_key=True)
    equipamento_id = Column(Integer, ForeignKey("equipamentos.id"), nullable=False, index=True)
    data = Column(Date, nullable=False, default=date.today)
    valor = Column(Float, nullable=False)
    banco = Column(String(120), nullable=False)
    forma = Column(String(40), nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())
    equipamento = relationship("Equipamento")



class NFSERascunho(Base):
    """Rascunho de NFS-e centralizado no Organiza.

    A primeira fase prepara os dados e os envia ao Emissor Nacional pelo Chrome,
    parando antes da emissão. Registros emitidos ficam imutáveis para consulta.
    """
    __tablename__ = "nfse_rascunhos"
    id = Column(Integer, primary_key=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False, index=True)
    origem = Column(String(30), nullable=False, default="manual")
    referencia_externa = Column(String(120), nullable=True, index=True)
    origem_url = Column(String(500), nullable=True)  # manual | manutencao | conect
    manutencao_id = Column(Integer, ForeignKey("assistencias.id"), nullable=True, index=True)
    competencia = Column(Date, nullable=False, default=date.today)
    codigo_servico = Column(String(30), nullable=False, default=NFSE_CODIGO_SERVICO_PADRAO)
    municipio_prestacao = Column(String(120), nullable=False, default=NFSE_MUNICIPIO_PADRAO)
    uf_prestacao = Column(String(2), nullable=False, default=NFSE_UF_PADRAO)
    descricao = Column(Text, nullable=False)
    valor_total = Column(Float, nullable=False, default=0)
    # Campos adicionais exigidos pelos CTNs do item 12 (atividades de evento).
    # Por enquanto são preenchidos manualmente no Organiza; futuramente o Connect
    # enviará datas e endereço do evento automaticamente.
    evento_data_inicio = Column(Date, nullable=True)
    evento_data_fim = Column(Date, nullable=True)
    evento_descricao = Column(String(255), nullable=True)
    # Por padrão, o local do evento usa o mesmo endereço cadastrado do cliente/tomador.
    # O endereço específico do evento só é usado quando esta opção for desmarcada.
    evento_endereco_igual_cliente = Column(Integer, nullable=False, default=1)
    evento_local_tipo = Column(String(20), nullable=True, default="brasil")
    evento_identificador = Column(String(60), nullable=True)
    evento_cep = Column(String(20), nullable=True)
    evento_logradouro = Column(String(255), nullable=True)
    evento_numero = Column(String(30), nullable=True)
    evento_complemento = Column(String(120), nullable=True)
    evento_bairro = Column(String(120), nullable=True)
    evento_municipio = Column(String(120), nullable=True)
    evento_uf = Column(String(2), nullable=True)
    status = Column(String(30), nullable=False, default="RASCUNHO")
    numero_nfse = Column(String(40), nullable=True)
    chave_acesso = Column(String(80), nullable=True)
    enviado_portal_em = Column(DateTime, nullable=True)
    emitido_em = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())
    cliente = relationship("Cliente")
    manutencao = relationship("Manutencao")

class IntegracaoConect(Base):
    """Controle idempotente do que já foi enviado ao Connect."""
    __tablename__ = "integracao_conect"
    id = Column(Integer, primary_key=True)
    origem = Column(String(30), nullable=False)  # venda | manutencao
    registro_id = Column(Integer, nullable=False)
    id_externo = Column(String(120), unique=True, nullable=False, index=True)
    hash_conteudo = Column(String(64), nullable=True)
    enviado_em = Column(DateTime, nullable=True)
    resposta = Column(Text, nullable=True)
    # Quando marcado, o lançamento permanece no histórico, mas não entra em envios automáticos.
    ignorado = Column(Integer, nullable=False, default=0)


def limpar_telefone(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def telefone_valido(valor: str, pais: str = "BR", ddi: str = "55") -> bool:
    return telefone_internacional_valido(pais, ddi, valor)


def formatar_telefone(valor: str, pais: str = "BR") -> str:
    return formatar_telefone_internacional(pais, valor)


def formatar_data(valor):
    return valor.strftime("%d/%m/%Y") if valor else "-"


def formatar_datahora(valor):
    return valor.strftime("%d/%m/%Y às %H:%M") if valor else "-"


def formatar_moeda(valor):
    if valor in (None, ""):
        return "R$ 0,00"
    try:
        texto = str(valor).replace("R$", "").strip()
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        numero = float(texto)
        return f"R$ {numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(valor)


templates.env.filters["telefone"] = formatar_telefone
templates.env.globals["PAISES"] = PAISES
templates.env.globals["whatsapp_url"] = ComunicacaoService.url_whatsapp
templates.env.filters["data_br"] = formatar_data
templates.env.filters["datahora"] = formatar_datahora
templates.env.filters["moeda"] = formatar_moeda


def gerar_hash_senha(senha: str, salt: Optional[str] = None) -> str:
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), 120_000).hex()
    return f"{salt}${digest}"


def verificar_senha(senha: str, senha_hash: str) -> bool:
    try:
        salt, esperado = senha_hash.split("$", 1)
        atual = gerar_hash_senha(senha, salt).split("$", 1)[1]
        return hmac.compare_digest(atual, esperado)
    except Exception:
        return False


def assinatura(nome: str) -> str:
    return hmac.new(CHAVE_SESSAO.encode(), nome.encode(), hashlib.sha256).hexdigest()


def usuario_cookie(request: Request) -> Optional[str]:
    valor = request.cookies.get("humiat_sessao", "")
    if "." not in valor:
        return None
    nome, sig = valor.rsplit(".", 1)
    return nome if hmac.compare_digest(sig, assinatura(nome)) else None


def usuario_logado(request: Request, db: Session = Depends(get_db)) -> Usuario:
    nome = usuario_cookie(request)
    usuario = db.query(Usuario).filter(Usuario.nome == nome, Usuario.ativo == 1).first() if nome else None
    if usuario:
        return usuario

    # Humiat ID: permite abrir o Organiza sem uma segunda tela de login quando
    # o usuário Humiat está explicitamente mapeado a um usuário do Organiza.
    hu = humiat_usuario_da_requisicao(request, db)
    if hu:
        legado = (hu.organiza_usuario or (ADMIN_NOME if hu.tipo == TIPO_ADMIN_HUMIAT else "")).strip()
        if legado:
            usuario = db.query(Usuario).filter(Usuario.nome == legado, Usuario.ativo == 1).first()
            if usuario:
                return usuario
    destino = request.url.path or "/organiza"
    if request.url.query:
        destino += "?" + request.url.query
    raise HTTPException(status_code=303, headers={"Location": "/entrar?" + urllib.parse.urlencode({"next": destino})})


def exigir_admin(usuario: Usuario):
    if not usuario.is_admin:
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador")


def data_form(valor: str):
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date() if valor else None
    except ValueError:
        return None


def datetime_form(valor: str):
    try:
        return datetime.strptime(valor, "%Y-%m-%dT%H:%M") if valor else None
    except ValueError:
        return None


def etapa_manutencao(m):
    """Fonte única de verdade para a etapa operacional.

    O pagamento é independente do fluxo da manutenção: pode ocorrer antes,
    durante, no final ou nem existir. Para entrar em execução é necessário
    apenas recebimento, orçamento aprovado, prazo operacional e, quando
    aplicável, a comunicação desse prazo ao cliente.
    """
    status = (m.status or "").strip()
    if status in {"Encerrada", "Cancelada"} or m.entregue_em:
        return 7
    if status in {"Pronto para retirada", "Retirada agendada"} or m.retirada_em or m.pronto_em:
        return 6

    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None
    aprovado = bool(o and (
        o.status in ("Aprovado", "Aprovado parcialmente", "Aprovado manualmente")
        or (o.status or "").startswith("Aprovado:")
    ))
    recebido = bool(m.recebido_em)

    # Sem recebimento/início do atendimento, permanece na entrada.
    if not recebido:
        return 1

    if aprovado:
        pronto_execucao = bool((m.prazo or "").strip())
        if pronto_execucao:
            return 5
        return 4

    if o and o.status in ("Enviado", "Aguardando aprovação"):
        return 3
    return 2

ETAPAS_MANUTENCAO = {
    1: {"rotulo": "Aguardando equipamento", "titulo": "Entrada", "classe": "status-cliente", "responsavel": "Cliente"},
    2: {"rotulo": "Orçamento pendente", "titulo": "Orçamento", "classe": "status-equipe", "responsavel": "Equipe"},
    3: {"rotulo": "Aguardando aceite", "titulo": "Aceite", "classe": "status-cliente", "responsavel": "Cliente"},
    4: {"rotulo": "Planejamento do serviço", "titulo": "Prazo do serviço", "classe": "status-equipe", "responsavel": "Equipe"},
    5: {"rotulo": "Execução do serviço", "titulo": "Execução do serviço", "classe": "status-equipe", "responsavel": "Equipe"},
    6: {"rotulo": "Aguardando retirada", "titulo": "Aguardando retirada", "classe": "status-cliente", "responsavel": "Cliente"},
    7: {"rotulo": "Encerrado", "titulo": "Encerrado", "classe": "status-finalizado", "responsavel": "Finalizado"},
}

def info_etapa_manutencao(m):
    return ETAPAS_MANUTENCAO[etapa_manutencao(m)]

def data_etapa_manutencao(m):
    """Retorna a data mais representativa da etapa atual."""
    etapa = etapa_manutencao(m)
    orcamento = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if getattr(m, "orcamentos", None) else None
    if etapa == 1:
        return m.entrega_prevista_em or m.criado_em
    if etapa == 2:
        return m.recebido_em or m.criado_em
    if etapa == 3:
        return (orcamento.criado_em if orcamento else None) or m.recebido_em or m.criado_em
    if etapa == 4:
        return (orcamento.aprovado_em if orcamento else None) or m.recebido_em or m.criado_em
    if etapa == 5:
        return m.confirmacao_prazo_em or (orcamento.aprovado_em if orcamento else None) or m.criado_em
    if etapa == 6:
        return m.retirada_em or m.pronto_em or m.criado_em
    return m.entregue_em or m.criado_em

templates.env.globals["etapa_manutencao"] = etapa_manutencao
templates.env.globals["info_etapa_manutencao"] = info_etapa_manutencao
templates.env.globals["data_etapa_manutencao"] = data_etapa_manutencao
templates.env.globals["ETAPAS_MANUTENCAO"] = ETAPAS_MANUTENCAO
templates.env.globals["rotulo_maquina"] = rotulo_maquina
templates.env.globals["codigo_tecnico"] = codigo_tecnico


def _humiat_usuario_do_cliente(cliente: Cliente, db: Session) -> HumiatUsuario | None:
    uid = int(getattr(cliente, "humiat_usuario_id", 0) or 0)
    if uid:
        usuario = db.query(HumiatUsuario).filter(HumiatUsuario.id == uid).first()
        if usuario:
            return usuario
    email = (getattr(cliente, "email", None) or "").strip().lower()
    if email:
        return db.query(HumiatUsuario).filter(func.lower(HumiatUsuario.email) == email).first()
    return None


def _humiat_empresas_alvo_cliente(
    cliente: Cliente, db: Session, *, incluir_karaokerj: bool = False
) -> list[HumiatEmpresa]:
    """Empresas Humiat que dão contexto aos acessos do cliente.

    O cadastro do Organiza continua sendo a fonte da pessoa. Equipamentos/assinaturas
    SolVoz definem a empresa usada pelo Cliente Catálogo. A Karaokê RJ só entra como
    contexto adicional quando o Cliente Site estiver liberado (ou quando não houver
    outra empresa identificável), evitando que uma renovação de catálogo abra na
    empresa errada.
    """
    empresas: dict[int, HumiatEmpresa] = {}
    for grupo in _solvoz_grupos_cliente(cliente):
        origem = grupo.get("empresa")
        slug = str(getattr(origem, "slug", "") or "").strip().lower()
        if not slug:
            continue
        try:
            empresa = garantir_empresa_solvoz_humiat(
                db,
                str(getattr(origem, "nome", "") or slug).strip(),
                slug,
                ativo=int(getattr(origem, "ativo", 1) or 0),
            )
            empresas[int(empresa.id)] = empresa
        except Exception:
            continue

    if incluir_karaokerj or not empresas:
        try:
            padrao = garantir_empresa_solvoz_humiat(db, "Karaokê RJ", "karaokerj", ativo=1)
            empresas[int(padrao.id)] = padrao
        except Exception:
            pass
    return list(empresas.values())


def _humiat_garantir_usuario_cliente(cliente: Cliente, db: Session) -> tuple[HumiatUsuario, bool, bool]:
    email = (cliente.email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("Cadastre um e-mail válido no cliente antes de criar o Humiat ID.")

    usuario = _humiat_usuario_do_cliente(cliente, db)
    criado = False
    if usuario and (usuario.email or "").strip().lower() != email:
        conflito = db.query(HumiatUsuario).filter(
            func.lower(HumiatUsuario.email) == email,
            HumiatUsuario.id != int(usuario.id),
        ).first()
        if conflito:
            raise ValueError("Este e-mail já pertence a outro Humiat ID.")
        usuario.email = email

    if not usuario:
        usuario = HumiatUsuario(
            nome=(cliente.nome or email).strip()[:120],
            email=email,
            senha_hash=gerar_hash_senha_id(secrets.token_urlsafe(32)),
            tipo=TIPO_CLIENTE_EMPRESA,
            ativo=1,
            organiza_usuario=None,
            documento=(cliente.documento or "").strip()[:30] or None,
            telefone=(cliente.whatsapp_completo() or cliente.telefone or "").strip()[:40] or None,
        )
        db.add(usuario)
        db.flush()
        criado = True

    # A equipe do piloto mantém o mesmo Humiat ID interno, mas agora também fica
    # visível no cadastro de Clientes para validar exatamente o fluxo real.
    interno = bool(usuario_humiat_equipe_prioritaria(usuario) or (
        (usuario.organiza_usuario or "").strip() and usuario_humiat_interno(db, usuario)
    ))
    usuario.nome = (cliente.nome or usuario.nome or email).strip()[:120]
    usuario.email = email
    usuario.documento = (cliente.documento or usuario.documento or "").strip()[:30] or None
    usuario.telefone = (cliente.whatsapp_completo() or cliente.telefone or usuario.telefone or "").strip()[:40] or None
    usuario.ativo = 1
    if not interno:
        usuario.tipo = TIPO_CLIENTE_EMPRESA
        usuario.organiza_usuario = None
        for empresa in _humiat_empresas_alvo_cliente(cliente, db):
            vinculo = db.query(HumiatUsuarioEmpresa).filter(
                HumiatUsuarioEmpresa.usuario_id == int(usuario.id),
                HumiatUsuarioEmpresa.empresa_id == int(empresa.id),
            ).first()
            if not vinculo:
                db.add(HumiatUsuarioEmpresa(usuario_id=int(usuario.id), empresa_id=int(empresa.id)))

    cliente.humiat_usuario_id = int(usuario.id)
    db.flush()
    return usuario, criado, interno


def _humiat_contexto_cliente(cliente: Cliente, db: Session) -> dict:
    usuario = _humiat_usuario_do_cliente(cliente, db)
    produtos = db.query(HumiatProduto).filter(HumiatProduto.ativo == 1).order_by(HumiatProduto.nome).all()
    acessos = {}
    if usuario:
        for produto in produtos:
            acessos[produto.codigo] = permissoes_usuario_humiat(db, int(usuario.id), produto.codigo)
    else:
        for produto in produtos:
            acessos[produto.codigo] = {
                "sistema": False, "adm": False, "solvoz_comprado": False, "solvoz_catalogo": False,
            }
    return {
        "usuario": usuario,
        "produtos": produtos,
        "acessos": acessos,
        "ativo": bool(usuario and int(usuario.ativo or 0)),
        "interno": bool(usuario and usuario_humiat_interno(db, usuario)),
        "email": (cliente.email or "").strip().lower(),
    }


def _humiat_salvar_acessos_cliente(cliente: Cliente, form: dict, db: Session, request: Request) -> tuple[HumiatUsuario, bool, str]:
    usuario, criado, interno = _humiat_garantir_usuario_cliente(cliente, db)
    usuario.ativo = 1 if str(form.get("humiat_ativo") or "0") == "1" else 0
    produtos = db.query(HumiatProduto).filter(HumiatProduto.ativo == 1).all()
    precisa_cliente_site = False
    for produto in produtos:
        codigo = (produto.codigo or "").upper()
        if codigo == "SOLVOZ":
            cliente_site = str(form.get(f"produto_{produto.id}_solvoz_site") or "0") == "1"
            cliente_catalogo = str(form.get(f"produto_{produto.id}_solvoz_catalogo") or "0") == "1"
            precisa_cliente_site = cliente_site
            adm = str(form.get(f"produto_{produto.id}_adm") or "0") == "1"
            # O ADM SolVoz é uma função de equipe. Clientes externos usam Site e/ou Catálogo.
            if not interno:
                adm = False
            salvar_permissoes_usuario_humiat(
                db, int(usuario.id), produto,
                sistema=(cliente_site or cliente_catalogo), adm=adm,
                cliente_site=cliente_site, cliente_catalogo=cliente_catalogo,
            )
        else:
            sistema = str(form.get(f"produto_{produto.id}_sistema") or "0") == "1"
            adm = str(form.get(f"produto_{produto.id}_adm") or "0") == "1"
            salvar_permissoes_usuario_humiat(
                db, int(usuario.id), produto, sistema=sistema, adm=adm,
            )

    if not interno:
        empresas_alvo = _humiat_empresas_alvo_cliente(
            cliente, db, incluir_karaokerj=precisa_cliente_site
        )
        # Garante os vínculos do usuário com exatamente os contextos necessários.
        for empresa in empresas_alvo:
            vinculo = db.query(HumiatUsuarioEmpresa).filter(
                HumiatUsuarioEmpresa.usuario_id == int(usuario.id),
                HumiatUsuarioEmpresa.empresa_id == int(empresa.id),
            ).first()
            if not vinculo:
                db.add(HumiatUsuarioEmpresa(
                    usuario_id=int(usuario.id), empresa_id=int(empresa.id)
                ))
        for produto in produtos:
            perm = permissoes_usuario_humiat(db, int(usuario.id), produto.codigo)
            habilitado = any(bool(v) for v in perm.values())
            if not habilitado:
                continue
            for empresa in empresas_alvo:
                item = db.query(HumiatEmpresaProduto).filter(
                    HumiatEmpresaProduto.empresa_id == int(empresa.id),
                    HumiatEmpresaProduto.produto_id == int(produto.id),
                ).first()
                if item:
                    item.ativo = 1
                else:
                    db.add(HumiatEmpresaProduto(
                        empresa_id=int(empresa.id), produto_id=int(produto.id), ativo=1
                    ))

    db.commit()
    mensagem_email = ""
    if criado and usuario.ativo:
        try:
            enviar_link_acesso_humiat(db, usuario, request=request, primeiro_acesso=True)
            mensagem_email = " Link de primeiro acesso enviado por e-mail."
        except Exception as exc:
            mensagem_email = f" O Humiat ID foi criado, mas o e-mail não foi enviado: {str(exc)[:220]}"
    return usuario, criado, mensagem_email


def _vincular_equipe_interna_ao_cadastro_clientes(db: Session) -> int:
    """Coloca Junior/Débora/Luiz no mesmo cadastro usado pelos clientes reais.

    Não cria uma segunda identidade: apenas encontra/cria a ficha de Cliente e
    grava nela o Humiat ID que já existia. Assim o piloto valida a regra final.
    """
    alterados = 0
    for hu in db.query(HumiatUsuario).filter(HumiatUsuario.ativo == 1).all():
        if not usuario_humiat_equipe_prioritaria(hu):
            continue
        local = None
        if (hu.email or "").strip():
            local = db.query(Usuario).filter(func.lower(Usuario.email) == (hu.email or "").strip().lower()).first()
        if not local and (hu.organiza_usuario or "").strip():
            local = db.query(Usuario).filter(Usuario.nome == hu.organiza_usuario.strip()).first()
        telefone_bruto = (getattr(local, "telefone", None) or hu.telefone or "").strip()
        try:
            pais, ddi, telefone = normalizar_contato("BR", "55", telefone_bruto)
        except Exception:
            pais, ddi, telefone = "BR", "55", re.sub(r"\D", "", telefone_bruto)
        if not telefone or not telefone_valido(telefone, pais, ddi):
            # Sem telefone não inventamos dados só para satisfazer a migração.
            continue

        cliente = db.query(Cliente).filter(Cliente.humiat_usuario_id == int(hu.id)).first()
        if not cliente and (hu.email or "").strip():
            cliente = db.query(Cliente).filter(func.lower(Cliente.email) == (hu.email or "").strip().lower()).first()
        if not cliente:
            cliente = db.query(Cliente).filter(Cliente.ddi == ddi, Cliente.telefone == telefone).first()
        if not cliente:
            cliente = Cliente(
                nome=(hu.nome or getattr(local, "nome", None) or "Usuário Humiat").strip(),
                pais=pais, ddi=ddi, telefone=telefone,
                empresa="Karaokê RJ", email=(hu.email or "").strip().lower() or None,
                humiat_usuario_id=int(hu.id), campanhas_ativo=0,
            )
            db.add(cliente)
            alterados += 1
        elif int(cliente.humiat_usuario_id or 0) != int(hu.id):
            cliente.humiat_usuario_id = int(hu.id)
            alterados += 1
        if not hu.telefone:
            hu.telefone = numero_internacional(cliente)
    db.flush()
    return alterados


@app.on_event("startup")
def iniciar_banco():
    Base.metadata.create_all(bind=engine)
    migrar_humiat_id_schema(engine)
    seed_humiat_id()
    # Migração leve para bancos já existentes (SQLite e PostgreSQL)
    insp = inspect(engine)
    if "agenda_manual" in insp.get_table_names():
        existentes_agenda_manual = {c["name"] for c in insp.get_columns("agenda_manual")}
        tipo_dt_agenda = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
        with engine.begin() as conn:
            if "google_event_id" not in existentes_agenda_manual:
                conn.execute(text("ALTER TABLE agenda_manual ADD COLUMN google_event_id VARCHAR(255)"))
            if "google_sync_status" not in existentes_agenda_manual:
                conn.execute(text("ALTER TABLE agenda_manual ADD COLUMN google_sync_status VARCHAR(30)"))
            if "google_sync_erro" not in existentes_agenda_manual:
                conn.execute(text("ALTER TABLE agenda_manual ADD COLUMN google_sync_erro TEXT"))
            if "google_sync_em" not in existentes_agenda_manual:
                conn.execute(text(f"ALTER TABLE agenda_manual ADD COLUMN google_sync_em {tipo_dt_agenda}"))

    if "assistencias" in insp.get_table_names():
        existentes = {c["name"] for c in insp.get_columns("assistencias")}
        tipo_dt = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
        with engine.begin() as conn:
            for coluna in ("entrega_prevista_em", "recebido_em", "entregue_em", "confirmacao_prazo_em", "servico_pausado_em", "compra_comunicada_em", "conclusao_comunicada_em"):
                if coluna not in existentes:
                    conn.execute(text(f"ALTER TABLE assistencias ADD COLUMN {coluna} {tipo_dt}"))
            for coluna in ("compra_descricao", "compra_previsao"):
                if coluna not in existentes:
                    conn.execute(text(f"ALTER TABLE assistencias ADD COLUMN {coluna} TEXT"))
            if "tipo_atendimento" not in existentes:
                conn.execute(text("ALTER TABLE assistencias ADD COLUMN tipo_atendimento VARCHAR(20) NOT NULL DEFAULT 'loja'"))
            if "comunicado" not in existentes:
                conn.execute(text("ALTER TABLE assistencias ADD COLUMN comunicado INTEGER NOT NULL DEFAULT 0"))
            if "ultima_comunicacao_em" not in existentes:
                conn.execute(text(f"ALTER TABLE assistencias ADD COLUMN ultima_comunicacao_em {tipo_dt}"))
            if "ultima_comunicacao_tipo" not in existentes:
                conn.execute(text("ALTER TABLE assistencias ADD COLUMN ultima_comunicacao_tipo VARCHAR(30)"))
    if "assistencia_orcamentos" in insp.get_table_names():
        existentes_orcamento = {c["name"] for c in insp.get_columns("assistencia_orcamentos")}
        with engine.begin() as conn:
            if "desconto" not in existentes_orcamento:
                conn.execute(text("ALTER TABLE assistencia_orcamentos ADD COLUMN desconto FLOAT NOT NULL DEFAULT 0"))
            if "desconto_somente_com_opcionais" not in existentes_orcamento:
                conn.execute(text("ALTER TABLE assistencia_orcamentos ADD COLUMN desconto_somente_com_opcionais INTEGER NOT NULL DEFAULT 0"))
            if "valor_manutencao" not in existentes_orcamento:
                conn.execute(text("ALTER TABLE assistencia_orcamentos ADD COLUMN valor_manutencao FLOAT NOT NULL DEFAULT 0"))
            if "forma_pagamento_orcamento" not in existentes_orcamento:
                conn.execute(text("ALTER TABLE assistencia_orcamentos ADD COLUMN forma_pagamento_orcamento VARCHAR(80)"))
            if "prazo_dias_uteis" not in existentes_orcamento:
                conn.execute(text("ALTER TABLE assistencia_orcamentos ADD COLUMN prazo_dias_uteis INTEGER"))
    if "assistencia_pagamentos" in insp.get_table_names():
        existentes_pagamentos = {c["name"] for c in insp.get_columns("assistencia_pagamentos")}
        with engine.begin() as conn:
            if "banco" not in existentes_pagamentos:
                conn.execute(text("ALTER TABLE assistencia_pagamentos ADD COLUMN banco VARCHAR(120)"))
    if "integracao_conect" in insp.get_table_names():
        existentes_integracao = {c["name"] for c in insp.get_columns("integracao_conect")}
        with engine.begin() as conn:
            if "ignorado" not in existentes_integracao:
                conn.execute(text("ALTER TABLE integracao_conect ADD COLUMN ignorado INTEGER NOT NULL DEFAULT 0"))
    if "campanha_aluguel_contatos" in insp.get_table_names():
        existentes_aluguel = {c["name"] for c in insp.get_columns("campanha_aluguel_contatos")}
        tipo_dt_aluguel = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
        with engine.begin() as conn:
            if "integracao" not in existentes_aluguel:
                conn.execute(text("ALTER TABLE campanha_aluguel_contatos ADD COLUMN integracao VARCHAR(20) NOT NULL DEFAULT 'PLANILHA'"))
            if "ultimo_mes_aluguel" not in existentes_aluguel:
                conn.execute(text("ALTER TABLE campanha_aluguel_contatos ADD COLUMN ultimo_mes_aluguel INTEGER"))
            if "ultimo_aluguel_em" not in existentes_aluguel:
                conn.execute(text("ALTER TABLE campanha_aluguel_contatos ADD COLUMN ultimo_aluguel_em DATE"))
            if "ultima_sincronizacao_em" not in existentes_aluguel:
                conn.execute(text(f"ALTER TABLE campanha_aluguel_contatos ADD COLUMN ultima_sincronizacao_em {tipo_dt_aluguel}"))
            if "connect_cliente_id" not in existentes_aluguel:
                conn.execute(text("ALTER TABLE campanha_aluguel_contatos ADD COLUMN connect_cliente_id INTEGER"))
            if "connect_solicitacao_id" not in existentes_aluguel:
                conn.execute(text("ALTER TABLE campanha_aluguel_contatos ADD COLUMN connect_solicitacao_id INTEGER"))
            # Registros criados pela versão anterior eram CSV; passam a ser PLANILHA.
            conn.execute(text("UPDATE campanha_aluguel_contatos SET integracao = 'PLANILHA' WHERE integracao IS NULL OR TRIM(integracao) = ''"))
            conn.execute(text("UPDATE campanha_aluguel_contatos SET origem = 'PLANILHA' WHERE UPPER(COALESCE(origem, '')) IN ('CSV', 'PLANILHA', '')"))
    if "campanhas" in insp.get_table_names():
        existentes_campanhas = {c["name"] for c in insp.get_columns("campanhas")}
        with engine.begin() as conn:
            if "aluguel_mes" not in existentes_campanhas:
                conn.execute(text("ALTER TABLE campanhas ADD COLUMN aluguel_mes INTEGER"))
    # Campanhas 1.1.41: reserva multiatendente + snapshot da mensagem.
    # Também corrige instalações que já tinham as classes novas, mas ainda não
    # possuíam as colunas físicas no banco existente.
    tipo_dt_campanha = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
    for tabela_dest in ("campanha_destinatarios", "campanha_aluguel_destinatarios"):
        if tabela_dest in insp.get_table_names():
            existentes_dest = {c["name"] for c in insp.get_columns(tabela_dest)}
            campos_dest = {
                "reservado_por_id": "INTEGER",
                "reservado_em": tipo_dt_campanha,
                "enviado_por_id": "INTEGER",
                "enviado_em": tipo_dt_campanha,
                "mensagem_pronta": "TEXT",
                "telefone_pronto": "VARCHAR(40)",
                "link_pronto": "VARCHAR(1000)",
                "pacotes_prontos": "TEXT",
                "valor_normal_centavos": "INTEGER",
                "valor_promocional_centavos": "INTEGER",
                "lote_numero": "INTEGER",
            }
            with engine.begin() as conn:
                for coluna, tipo_sql in campos_dest.items():
                    if coluna not in existentes_dest:
                        conn.execute(text(f"ALTER TABLE {tabela_dest} ADD COLUMN {coluna} {tipo_sql}"))

    if "clientes" in insp.get_table_names():
        existentes_clientes = {c["name"] for c in insp.get_columns("clientes")}
        with engine.begin() as conn:
            if "razao_social" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN razao_social VARCHAR(180)"))
            if "inscricao_estadual" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN inscricao_estadual VARCHAR(30)"))
            if "situacao_icms" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN situacao_icms VARCHAR(30)"))
            if "cnpj_situacao_cadastral" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN cnpj_situacao_cadastral VARCHAR(40)"))
            if "cnpj_fonte" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN cnpj_fonte VARCHAR(80)"))
            if "cnpj_consultado_em" not in existentes_clientes:
                tipo_data_cnpj = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
                conn.execute(text(f"ALTER TABLE clientes ADD COLUMN cnpj_consultado_em {tipo_data_cnpj}"))
            if "municipio_ibge" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN municipio_ibge VARCHAR(12)"))
            if "entrega_igual_cliente" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN entrega_igual_cliente INTEGER NOT NULL DEFAULT 1"))
            if "entrega_cep" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN entrega_cep VARCHAR(20)"))
            if "entrega_endereco" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN entrega_endereco VARCHAR(255)"))
            if "entrega_numero" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN entrega_numero VARCHAR(30)"))
            if "entrega_complemento" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN entrega_complemento VARCHAR(120)"))
            if "entrega_bairro" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN entrega_bairro VARCHAR(120)"))
            if "entrega_municipio" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN entrega_municipio VARCHAR(120)"))
            if "entrega_municipio_ibge" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN entrega_municipio_ibge VARCHAR(12)"))
            if "entrega_estado" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN entrega_estado VARCHAR(60)"))
            if "pais" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN pais VARCHAR(2) NOT NULL DEFAULT 'BR'"))
            if "ddi" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN ddi VARCHAR(5) NOT NULL DEFAULT '55'"))
            if "campanhas_ativo" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN campanhas_ativo INTEGER NOT NULL DEFAULT 1"))
            if "humiat_usuario_id" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN humiat_usuario_id INTEGER"))
            tipo_data_oferta = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
            if "atualizacao_oferta_status" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN atualizacao_oferta_status VARCHAR(30)"))
            if "atualizacao_oferta_periodo" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN atualizacao_oferta_periodo VARCHAR(120)"))
            if "atualizacao_oferta_pacotes" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN atualizacao_oferta_pacotes TEXT"))
            if "atualizacao_oferta_valor_normal_centavos" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN atualizacao_oferta_valor_normal_centavos INTEGER"))
            if "atualizacao_oferta_valor_promocional_centavos" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN atualizacao_oferta_valor_promocional_centavos INTEGER"))
            if "atualizacao_oferta_order_nsu" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN atualizacao_oferta_order_nsu VARCHAR(120)"))
            if "atualizacao_oferta_atualizado_em" not in existentes_clientes:
                conn.execute(text(f"ALTER TABLE clientes ADD COLUMN atualizacao_oferta_atualizado_em {tipo_data_oferta}"))
            conn.execute(text("UPDATE clientes SET pais = 'BR' WHERE pais IS NULL OR pais = ''"))
            conn.execute(text("UPDATE clientes SET ddi = '55' WHERE ddi IS NULL OR ddi = ''"))
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_clientes_humiat_usuario ON clientes (humiat_usuario_id) WHERE humiat_usuario_id IS NOT NULL"))
        except Exception:
            pass
    if "campanhas" in insp.get_table_names():
        existentes_campanhas = {c["name"] for c in insp.get_columns("campanhas")}
        with engine.begin() as conn:
            if "link" not in existentes_campanhas:
                conn.execute(text("ALTER TABLE campanhas ADD COLUMN link VARCHAR(1000)"))
    if "nfse_rascunhos" in insp.get_table_names():
        existentes_nfse = {c["name"] for c in insp.get_columns("nfse_rascunhos")}
        with engine.begin() as conn:
            tipo_data_nfse = "DATE"
            campos_nfse = {
                "referencia_externa": "VARCHAR(120)",
                "origem_url": "VARCHAR(500)",
                "evento_data_inicio": tipo_data_nfse,
                "evento_data_fim": tipo_data_nfse,
                "evento_descricao": "VARCHAR(255)",
                "evento_endereco_igual_cliente": "INTEGER NOT NULL DEFAULT 1",
                "evento_local_tipo": "VARCHAR(20)",
                "evento_identificador": "VARCHAR(60)",
                "evento_cep": "VARCHAR(20)",
                "evento_logradouro": "VARCHAR(255)",
                "evento_numero": "VARCHAR(30)",
                "evento_complemento": "VARCHAR(120)",
                "evento_bairro": "VARCHAR(120)",
                "evento_municipio": "VARCHAR(120)",
                "evento_uf": "VARCHAR(2)",
            }
            for coluna, tipo_sql in campos_nfse.items():
                if coluna not in existentes_nfse:
                    conn.execute(text(f"ALTER TABLE nfse_rascunhos ADD COLUMN {coluna} {tipo_sql}"))
            conn.execute(text("UPDATE nfse_rascunhos SET evento_local_tipo = 'brasil' WHERE evento_local_tipo IS NULL OR evento_local_tipo = ''"))
            # Rascunhos antigos que já tinham endereço específico preenchido continuam usando-o.
            if "evento_endereco_igual_cliente" not in existentes_nfse:
                conn.execute(text("""
                    UPDATE nfse_rascunhos
                       SET evento_endereco_igual_cliente = 0
                     WHERE COALESCE(TRIM(evento_cep), '') <> ''
                        OR COALESCE(TRIM(evento_numero), '') <> ''
                """))
    if "equipamentos" in insp.get_table_names():
        existentes_equipamentos = {c["name"] for c in insp.get_columns("equipamentos")}
        with engine.begin() as conn:
            if "garantia_meses" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN garantia_meses INTEGER DEFAULT 3"))
            if "numero_serie" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN numero_serie VARCHAR(120)"))
            if "fabricante" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN fabricante VARCHAR(80) DEFAULT 'KARAOKERJ'"))
            if "numero_hd" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN numero_hd VARCHAR(160)"))
            if "numero_maquina_cliente" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN numero_maquina_cliente INTEGER"))
            if "solvoz_empresa_id" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN solvoz_empresa_id INTEGER"))
            if "catalogo_online" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN catalogo_online INTEGER NOT NULL DEFAULT 0"))
            if "nota_codigo" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN nota_codigo VARCHAR(20)"))
            if "nota_descricao" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN nota_descricao VARCHAR(180)"))
            if "chave_acesso_nfe" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN chave_acesso_nfe VARCHAR(44)"))
            if "som" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN som VARCHAR(20) NOT NULL DEFAULT 'NA'"))
            if "hdmi_tela_2" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN hdmi_tela_2 VARCHAR(10) NOT NULL DEFAULT 'NA'"))
            if "teclado_bluetooth" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN teclado_bluetooth VARCHAR(10) NOT NULL DEFAULT 'NA'"))
            if "microfone" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN microfone VARCHAR(20) NOT NULL DEFAULT 'Com fio'"))
            if "sistema_credito" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN sistema_credito VARCHAR(20) NOT NULL DEFAULT 'NA'"))
            if "catalogo_impresso" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN catalogo_impresso VARCHAR(10) NOT NULL DEFAULT 'NA'"))
    db = SessionLocal()
    try:
        # Preserva o comportamento histórico do QR sem exigir configuração manual
        # no primeiro deploy. As demais empresas são cadastradas pelo ADM do Organiza.
        if not db.query(SolVozEmpresa).filter(SolVozEmpresa.slug == "karaokerj").first():
            db.add(SolVozEmpresa(
                nome="Karaokê RJ",
                slug="karaokerj",
                dominio=dominio_solvoz_por_slug("karaokerj"),
                ativo=1,
            ))
            db.commit()
        if not db.query(Usuario).filter(Usuario.nome == ADMIN_NOME).first():
            db.add(Usuario(nome=ADMIN_NOME, senha_hash=gerar_hash_senha(ADMIN_SENHA), is_admin=1, ativo=1, cargo="Administrador"))
            db.commit()
        if db.query(Item).count() == 0:
            caminho = os.path.join(os.path.dirname(__file__), "itens_seed.json")
            if os.path.exists(caminho):
                with open(caminho, "r", encoding="utf-8") as arquivo:
                    for dado in json.load(arquivo):
                        db.add(Item(**dado, categoria="Geral", ativo=1))
                db.commit()
        # Remove prefixos antigos usados no código do WhatsApp e mantém somente o nome real.
        for cliente_existente in db.query(Cliente).all():
            cliente_existente.nome = limpar_nome_cliente(cliente_existente.nome)
            cliente_existente.pais, cliente_existente.ddi, cliente_existente.telefone = normalizar_contato(
                cliente_existente.pais, cliente_existente.ddi, cliente_existente.telefone
            )

        eq_corrigidos, clientes_corrigidos, primeiro_pacote = _corrigir_consistencia_pacotes(db)
        if eq_corrigidos or clientes_corrigidos:
            print(f"[PACOTES] 1.1.52: equipamentos ajustados={eq_corrigidos}; clientes sincronizados={clientes_corrigidos}; primeiro pacote={primeiro_pacote}.")

        vinculados_piloto = _vincular_equipe_interna_ao_cadastro_clientes(db)
        if vinculados_piloto:
            print(f"[HUMIAT ID] 1.1.33: {vinculados_piloto} ficha(s) da equipe interna vinculada(s) ao cadastro de Clientes.")

        # Migra o antigo item "Manutenção" para o campo fixo do orçamento.
        for orcamento_existente in db.query(Orcamento).options(selectinload(Orcamento.itens)).all():
            itens_manutencao = [
                item for item in orcamento_existente.itens
                if re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", item.descricao or "").encode("ascii", "ignore").decode("ascii").lower()) == "manutencao"
            ]
            if itens_manutencao:
                if not orcamento_existente.valor_manutencao:
                    orcamento_existente.valor_manutencao = sum(item.preco_venda * item.quantidade for item in itens_manutencao)
                for item in itens_manutencao:
                    db.delete(item)

        # Códigos KRJ existentes são permanentes e nunca são renumerados automaticamente.
        # Apenas completa fabricante e identificações vazias, preservando todo o histórico.
        for equipamento_existente in db.query(Equipamento).all():
            equipamento_existente.fabricante = equipamento_existente.fabricante or "KARAOKERJ"
        db.commit()
    finally:
        db.close()


@app.get("/", response_class=HTMLResponse)
def inicio_publico(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/saude")
def saude():
    return {"status": "ok", "versao": ORGANIZA_VERSAO}


@app.get("/acesso")
def escolher_acesso(request: Request):
    """Compatibilidade: a antiga escolha de áreas foi substituída pelo Humiat ID único."""
    return RedirectResponse("/entrar", status_code=303)


@app.get("/area-restrita/login")
def login(request: Request, erro: str = ""):
    # APP 1.1.31: login único. Links antigos seguem para Humiat ID e retornam ao Organiza.
    return RedirectResponse("/entrar", status_code=303)


@app.post("/area-restrita/login")
def entrar_legado():
    # 1.1.35: a senha local deixou de ser uma porta de entrada exposta.
    # Toda autenticação do Organiza passa pelo Humiat ID central.
    return RedirectResponse("/entrar", status_code=303)


@app.get("/area-restrita/sair")
def sair_local_organiza():
    # Sair dentro do produto é local: limpa a compatibilidade antiga e volta ao Hub.
    resposta = RedirectResponse("/painel", status_code=303)
    resposta.delete_cookie("humiat_sessao", path="/")
    return resposta


def _contexto_operacao(db: Session):
    manutencoes = _manutencoes_operacao(db)
    filas = {
        "atendimento": [], "orcamentos": [], "comunicar_orcamentos": [],
        "aprovacoes": [], "pagamentos": [], "execucao": [],
        "pausados": [], "prontos": [], "retiradas": [],
    }
    for manutencao in manutencoes:
        chave = _fila_operacional_exclusiva(manutencao)
        if chave:
            filas[chave].append(manutencao)

    # Datas úteis para leitura rápida nos cards da operação.
    for lista in filas.values():
        for m in lista:
            if m.retirada_em:
                m.operacao_data = m.retirada_em
            elif m.entrega_prevista_em:
                m.operacao_data = m.entrega_prevista_em
            elif m.pronto_em:
                m.operacao_data = m.pronto_em
            elif m.recebido_em:
                m.operacao_data = m.recebido_em
            else:
                m.operacao_data = m.criado_em

    grupos = {chave: _agrupar_por_cliente(valor) for chave, valor in filas.items()}
    return {
        "filas": filas,
        "grupos": grupos,
        # A Central trabalha por cliente. O número do card representa grupos de ação,
        # enquanto a quantidade de equipamentos continua visível dentro de cada grupo.
        "contagens": {chave: len(valor) for chave, valor in grupos.items()},
        "contagens_equipamentos": {chave: len(valor) for chave, valor in filas.items()},
        "total_operacao": sum(len(valor) for valor in grupos.values()),
        "total_manutencoes": len(manutencoes),
    }



@app.get("/organiza-sw.js", include_in_schema=False)
def organiza_service_worker():
    resposta = FileResponse("static/organiza-sw.js", media_type="application/javascript")
    resposta.headers["Service-Worker-Allowed"] = "/organiza"
    resposta.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resposta

@app.get("/organiza", response_class=HTMLResponse)
def painel(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Dashboard resumido do Organiza.

    Os cards encaminham para a Central Operacional já com o filtro correto.
    """
    contexto = _contexto_operacao(db)
    fases = [
        {"numero": "1", "nome": "Entrada", "chaves": ["atendimento"], "filtro": "atendimento"},
        {"numero": "2", "nome": "Orçamento", "chaves": ["orcamentos", "comunicar_orcamentos"], "filtro": "orcamentos"},
        {"numero": "3", "nome": "Aceite", "chaves": ["aprovacoes"], "filtro": "aprovacoes"},
        {"numero": "4", "nome": "Prazo", "chaves": ["pagamentos"], "filtro": "pagamentos"},
        {"numero": "5", "nome": "Execução", "chaves": ["execucao", "pausados", "prontos"], "filtro": "execucao"},
        {"numero": "6", "nome": "Retirada", "chaves": ["retiradas"], "filtro": "retiradas"},
    ]
    for fase in fases:
        fase["quantidade"] = sum(contexto["contagens_equipamentos"].get(chave, 0) for chave in fase["chaves"])
    return templates.TemplateResponse("organiza/painel.html", {
        "request": request, "usuario": usuario, "fases": fases,
        "total_operacao": contexto["total_manutencoes"],
        "total_clientes_operacao": contexto["total_operacao"],
    })


@app.get("/organiza/central", response_class=HTMLResponse)
def central_operacional(
    request: Request,
    etapa: str = "todos",
    busca: str = "",
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Central Operacional: filtro, ações da etapa e seleção individual ou em lote."""
    contexto = _contexto_operacao(db)
    etapas_meta = {
        "atendimento": ("1", "Entrada", "Aguardando atendimento ou entrega"),
        "orcamentos": ("2", "Orçamento", "Fazer orçamento"),
        "comunicar_orcamentos": ("3", "Comunicar", "Comunicar orçamento"),
        "aprovacoes": ("4", "Aceite", "Aguardando aprovação"),
        "pagamentos": ("5", "Prazo", "Definir prazo do serviço"),
        "execucao": ("6", "Execução", "Em execução"),
        "pausados": ("7", "Peças", "Aguardando item / peça"),
        "prontos": ("8", "Pronto", "Comunicar equipamento pronto"),
        "retiradas": ("9", "Retirada", "Aguardando retirada"),
    }
    etapa = etapa if etapa in etapas_meta or etapa == "todos" else "todos"
    termo = (busca or "").strip().lower()
    selecionadas = []
    for chave, lista in contexto["filas"].items():
        if etapa != "todos" and chave != etapa:
            continue
        numero, nome_curto, titulo = etapas_meta[chave]
        for m in lista:
            texto_busca = " ".join([
                getattr(m.cliente, "nome", "") or "",
                rotulo_maquina(m.equipamento) if m.equipamento else "",
                getattr(m.equipamento, "tipo", "") or "",
                getattr(m.equipamento, "modelo", "") or "",
                getattr(m, "defeito", "") or "",
                str(m.id),
            ]).lower()
            if termo and termo not in texto_busca:
                continue
            m.central_chave = chave
            m.central_numero = numero
            m.central_nome_curto = nome_curto
            m.central_titulo = titulo
            selecionadas.append(m)

    central_grupos = _agrupar_por_cliente(selecionadas)
    contexto.update({
        "request": request, "usuario": usuario, "pagina_inicial": False,
        "etapa_filtro": etapa, "busca": busca, "etapas_meta": etapas_meta,
        "central_grupos": central_grupos, "central_total": len(selecionadas),
    })
    return templates.TemplateResponse("organiza/operacao.html", contexto)



def _lokafest_digitos(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def _lokafest_token_valido(authorization: str | None) -> bool:
    if not LOKAFEST_API_TOKEN:
        return False
    recebido = (authorization or "").strip()
    if recebido.lower().startswith("bearer "):
        recebido = recebido[7:].strip()
    return secrets.compare_digest(recebido, LOKAFEST_API_TOKEN)


def _lokafest_cliente_por_identificador(db: Session, cpf: str = "", whatsapp: str = ""):
    cpf_limpo = _lokafest_digitos(cpf)
    whats_limpo = _lokafest_digitos(whatsapp)

    candidatos = db.query(Cliente).options(selectinload(Cliente.equipamentos)).all()

    if cpf_limpo:
        for cliente in candidatos:
            if _lokafest_digitos(cliente.documento) == cpf_limpo:
                return cliente

    if whats_limpo:
        # Aceita número com/sem DDI 55, mas exige os últimos 10/11 dígitos iguais.
        alvo = whats_limpo[-11:] if len(whats_limpo) >= 11 else whats_limpo
        for cliente in candidatos:
            telefone = _lokafest_digitos(cliente.telefone)
            completo = _lokafest_digitos(cliente.whatsapp_completo())
            comparaveis = {telefone, completo}
            comparaveis |= {x[-11:] for x in list(comparaveis) if len(x) >= 11}
            if alvo in comparaveis or whats_limpo in comparaveis:
                return cliente

    return None


def _lokafest_tipo_modelo(eq: Equipamento) -> str | None:
    tipo = tipo_equipamento_padrao(eq.tipo or "")
    modelo = unicodedata.normalize("NFKD", (eq.modelo or "").upper()).encode("ascii", "ignore").decode("ascii")

    # O tipo cadastrado é a fonte principal da classificação.
    # Isso evita que um IPHONE com modelo "JUKEBOX IPHONE..." seja contado como Jukebox.
    if tipo == "IPHONE":
        return "iphone"
    if tipo in {"MALETA", "PORTATIL"}:
        return "portatil"
    if tipo == "JUKEBOX":
        return "jukebox"

    # Compatibilidade com cadastros antigos ou sem tipo padronizado.
    if "IPHONE" in modelo:
        return "iphone"
    if "PORTATIL" in modelo or "MALETA" in modelo:
        return "portatil"
    if "JUKEBOX" in modelo:
        return "jukebox"
    return None


@app.get("/api/integracoes/lokafest/cliente")
def api_lokafest_cliente(
    cpf: str = "",
    whatsapp: str = "",
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """
    Endpoint privado consumido pelo LokaFest.

    Busca cliente por CPF ou WhatsApp e devolve somente equipamentos
    Karaoke RJ dos tipos Jukebox, Portátil/Maleta e iPhone.

    Header obrigatório:
        Authorization: Bearer <LOKAFEST_API_TOKEN>
    """
    if not _lokafest_token_valido(authorization):
        raise HTTPException(status_code=401, detail="Token de integração inválido.")

    if not _lokafest_digitos(cpf) and not _lokafest_digitos(whatsapp):
        raise HTTPException(status_code=400, detail="Informe CPF ou WhatsApp.")

    cliente = _lokafest_cliente_por_identificador(db, cpf, whatsapp)
    if not cliente:
        return {
            "encontrado": False,
            "cliente_id": None,
            "cpf": _lokafest_digitos(cpf),
            "atualizacao": obter_pacote_atual(db),
            "equipamentos": {"jukebox": 0, "portatil": 0, "iphone": 0},
            "detalhes": [],
        }

    pacote_obrigatorio = obter_pacote_atual(db)
    contagem = {"jukebox": 0, "portatil": 0, "iphone": 0}
    detalhes = []

    for eq in cliente.equipamentos:
        if (eq.status or "").strip().lower() != "ativo":
            continue
        if (eq.fabricante or "").strip().upper() != "KARAOKERJ":
            continue

        classe = _lokafest_tipo_modelo(eq)
        if not classe:
            continue

        contagem[classe] += 1
        pacote_instalado = (eq.pacote or "").strip() or None
        falta = calcular_falta_pacote(pacote_instalado, pacote_obrigatorio)

        detalhes.append({
            "id": eq.id,
            "tipo": classe,
            "tipo_origem": eq.tipo,
            "modelo": eq.modelo,
            "identificacao": rotulo_maquina(eq),
            "numero_maquina": eq.maquina,
            "numero_cliente": eq.numero_maquina_cliente,
            "pacote": pacote_instalado,
            "pacote_obrigatorio": pacote_obrigatorio,
            "falta_pacote": falta,
            "atualizado": bool(pacote_instalado and pacote_instalado == pacote_obrigatorio),
        })

    # Campo resumido mantido para compatibilidade com o LokaFest atual.
    # Representa o pacote obrigatório vigente no Organiza.
    return {
        "encontrado": True,
        "cliente_id": str(cliente.id),
        "nome": cliente.nome,
        "cpf": _lokafest_digitos(cliente.documento),
        "whatsapp": cliente.whatsapp_completo(),
        "atualizacao": pacote_obrigatorio,
        "equipamentos": contagem,
        "detalhes": detalhes,
    }



@app.get("/organiza/clientes", response_class=HTMLResponse)
def clientes(request: Request, busca: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    query = db.query(Cliente).options(selectinload(Cliente.equipamentos))
    termo = busca.strip()
    if termo:
        like = f"%{termo}%"
        query = query.filter(or_(Cliente.nome.ilike(like), Cliente.telefone.ilike(like), Cliente.empresa.ilike(like), Cliente.municipio.ilike(like), Cliente.cidade.ilike(like)))
    lista = query.order_by(Cliente.nome.asc()).all()
    return templates.TemplateResponse("organiza/clientes.html", {
        "request": request, "usuario": usuario, "clientes": lista, "busca": busca,
        "total_clientes": db.query(Cliente).count(), "total_equipamentos": db.query(Equipamento).count(),
    })


def limpar_nome_cliente(nome: str) -> str:
    nome = (nome or "").strip()
    if "_" in nome:
        parte = re.split(r"_+", nome)[-1].strip()
        if parte:
            nome = parte
    return re.sub(r"\s+", " ", nome).strip()


def limpar_documento(documento: str) -> str:
    return re.sub(r"\D", "", documento or "")


def cpf_valido(documento: str) -> bool:
    cpf = limpar_documento(documento)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(cpf[i]) * (tamanho + 1 - i) for i in range(tamanho))
        digito = (soma * 10) % 11
        if digito == 10:
            digito = 0
        if digito != int(cpf[tamanho]):
            return False
    return True



CNPJ_IE_UFS_SUPORTADAS = {"BA", "GO", "MG", "PB", "PR", "PE", "RS", "SC", "SP", "SE"}


def cnpj_valido(documento: str) -> bool:
    cnpj = limpar_documento(documento)
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    def _digito(base: str, pesos: list[int]) -> str:
        total = sum(int(n) * p for n, p in zip(base, pesos))
        resto = total % 11
        return str(0 if resto < 2 else 11 - resto)
    d1 = _digito(cnpj[:12], [5,4,3,2,9,8,7,6,5,4,3,2])
    d2 = _digito(cnpj[:12] + d1, [6,5,4,3,2,9,8,7,6,5,4,3,2])
    return cnpj[-2:] == d1 + d2


def _cnpj_ws_url(cnpj: str) -> str:
    base = (os.getenv("CNPJ_API_BASE_URL") or "https://publica.cnpj.ws/cnpj").strip().rstrip("/")
    return f"{base}/{cnpj}"


def consultar_cnpj_publico(documento: str) -> dict:
    """Consulta pontual de CNPJ para cadastro; nunca executa varredura/lote."""
    cnpj = limpar_documento(documento)
    if not cnpj_valido(cnpj):
        raise ValueError("CNPJ inválido.")
    req = urllib.request.Request(
        _cnpj_ws_url(cnpj),
        headers={"Accept": "application/json", "User-Agent": "HUMIAT-Organiza/1.1.7"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ValueError("CNPJ não encontrado na consulta pública.") from exc
        if exc.code == 429:
            raise RuntimeError("Limite temporário da consulta de CNPJ atingido. Aguarde alguns segundos e tente novamente.") from exc
        raise RuntimeError(f"Consulta de CNPJ indisponível (HTTP {exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("Não foi possível consultar o CNPJ agora. Tente novamente em instantes.") from exc

    est = data.get("estabelecimento") or {}
    estado = est.get("estado") or {}
    cidade = est.get("cidade") or {}
    uf = str(estado.get("sigla") or "").strip().upper()
    # CNPJ.ws já devolve IE em muitos estados, mas o formato pode variar.
    # Varre as localizações conhecidas sem concluir "não contribuinte" só porque
    # a IE não veio na resposta.
    listas_ie = []
    for origem in (
        est.get("inscricoes_estaduais"),
        data.get("inscricoes_estaduais"),
        est.get("inscricoes_estaduais_ativas"),
        data.get("inscricoes_estaduais_ativas"),
    ):
        if isinstance(origem, list):
            listas_ie.extend(origem)
    ie_ativas = []
    for item in listas_ie:
        if not isinstance(item, dict):
            continue
        ativo_raw = item.get("ativo")
        situacao_raw = str(item.get("situacao") or item.get("status") or "").strip().upper()
        ativo = ativo_raw is True or str(ativo_raw).strip().lower() in {"1", "true", "sim", "ativo"} or "ATIV" in situacao_raw
        if ativo_raw is False or situacao_raw in {"INATIVA", "INATIVO", "BAIXADA", "BAIXADO", "CANCELADA", "CANCELADO"}:
            ativo = False
        if not ativo and ativo_raw is not None:
            continue
        item_estado = item.get("estado") or {}
        item_uf = str((item_estado.get("sigla") if isinstance(item_estado, dict) else item_estado) or item.get("uf") or "").strip().upper()
        numero_ie = str(item.get("inscricao_estadual") or item.get("ie") or item.get("numero") or "").strip()
        if numero_ie and (not uf or not item_uf or item_uf == uf):
            ie_ativas.append(numero_ie)
    ie = next((valor for valor in ie_ativas if valor), "")

    # Uma IE ativa no cadastro estadual é evidência suficiente para o Organiza
    # tratar o destinatário como contribuinte. Ausência de IE NÃO vira
    # automaticamente "não contribuinte": fica pendente para conferência.
    situacao_icms = "CONTRIBUINTE" if ie else "NAO_CONFIRMADO"
    if not ie and uf not in CNPJ_IE_UFS_SUPORTADAS:
        situacao_icms = "NAO_CONFIRMADO"

    tipo_logradouro = str(est.get("tipo_logradouro") or "").strip()
    logradouro = str(est.get("logradouro") or "").strip()
    endereco = " ".join(x for x in (tipo_logradouro, logradouro) if x).strip()
    ddd = re.sub(r"\D", "", str(est.get("ddd1") or ""))
    telefone = re.sub(r"\D", "", str(est.get("telefone1") or ""))

    return {
        "cnpj": cnpj,
        "razao_social": str(data.get("razao_social") or "").strip(),
        "nome_fantasia": str(est.get("nome_fantasia") or "").strip(),
        "situacao_cadastral": str(est.get("situacao_cadastral") or "").strip(),
        "inscricao_estadual": ie,
        "situacao_icms": situacao_icms,
        "cep": re.sub(r"\D", "", str(est.get("cep") or "")),
        "endereco": endereco,
        "numero": str(est.get("numero") or "").strip(),
        "complemento": str(est.get("complemento") or "").strip(),
        "bairro": str(est.get("bairro") or "").strip(),
        "municipio": str(cidade.get("nome") or "").strip(),
        "municipio_ibge": str(cidade.get("ibge_id") or "").strip(),
        "uf": uf,
        "email_empresa": str(est.get("email") or "").strip(),
        "telefone_empresa": (ddd + telefone) if (ddd or telefone) else "",
        "fonte": "CNPJ.ws (Receita Federal / cadastros estaduais quando disponíveis)",
        "consultado_em": datetime.now(),
    }


def consultar_cep_publico(cep: str) -> dict:
    """Consulta um CEP e devolve somente os campos que o cliente não pode editar manualmente."""
    numero = re.sub(r"\D", "", cep or "")
    if len(numero) != 8:
        raise ValueError("Informe um CEP válido com 8 dígitos.")
    req = urllib.request.Request(
        f"https://viacep.com.br/ws/{numero}/json/",
        headers={"Accept": "application/json", "User-Agent": f"HUMIAT-Organiza/{ORGANIZA_VERSION}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("Consulta de CEP indisponível. Tente mais tarde.") from exc
    if not isinstance(data, dict) or data.get("erro"):
        raise ValueError("CEP não encontrado.")
    return {
        "cep": numero,
        "endereco": str(data.get("logradouro") or "").strip(),
        "bairro": str(data.get("bairro") or "").strip(),
        "municipio": str(data.get("localidade") or "").strip(),
        "municipio_ibge": re.sub(r"\D", "", str(data.get("ibge") or "")),
        "uf": str(data.get("uf") or "").strip().upper(),
    }


def aplicar_dados_cnpj(cliente: Cliente, dados: dict, *, atualizar_endereco: bool = True) -> None:
    cliente.documento = dados.get("cnpj") or cliente.documento
    if dados.get("razao_social"):
        cliente.razao_social = dados["razao_social"]
    if dados.get("nome_fantasia"):
        cliente.empresa = dados["nome_fantasia"]
    cliente.cnpj_situacao_cadastral = dados.get("situacao_cadastral") or None
    cliente.cnpj_fonte = dados.get("fonte") or None
    cliente.cnpj_consultado_em = dados.get("consultado_em") or datetime.now()
    situacao_api = (dados.get("situacao_icms") or "NAO_CONFIRMADO").strip().upper()
    if dados.get("inscricao_estadual"):
        cliente.inscricao_estadual = dados["inscricao_estadual"]
        cliente.situacao_icms = "CONTRIBUINTE"
    elif not (cliente.situacao_icms or "").strip():
        cliente.situacao_icms = situacao_api
    if atualizar_endereco:
        if dados.get("cep"): cliente.cep = dados["cep"]
        if dados.get("endereco"): cliente.endereco = dados["endereco"]
        if dados.get("numero"): cliente.endereco_numero = dados["numero"]
        if dados.get("complemento"): cliente.complemento = dados["complemento"]
        if dados.get("bairro"): cliente.bairro = dados["bairro"]
        if dados.get("municipio"):
            cliente.municipio = dados["municipio"]
            cliente.cidade = dados["municipio"]
        if dados.get("municipio_ibge"): cliente.municipio_ibge = dados["municipio_ibge"]
        if dados.get("uf"): cliente.estado = dados["uf"]


def situacao_icms_rotulo(valor: str | None) -> str:
    return {
        "CONTRIBUINTE": "Contribuinte do ICMS",
        "NAO_CONTRIBUINTE": "Não contribuinte do ICMS",
        "ISENTO": "Isento de IE",
        "NAO_CONFIRMADO": "Não confirmado",
    }.get((valor or "").strip().upper(), "Não confirmado")


templates.env.globals["situacao_icms_rotulo"] = situacao_icms_rotulo

def preencher_cliente(cliente: Cliente, form: dict):
    cliente.nome = limpar_nome_cliente(form.get("nome") or "")
    cliente.pais, cliente.ddi, cliente.telefone = normalizar_contato(
        form.get("pais") or getattr(cliente, "pais", "BR"),
        form.get("ddi") or getattr(cliente, "ddi", "55"),
        form.get("telefone") or "",
    )
    cliente.empresa = (form.get("empresa") or "").strip() or None
    cliente.razao_social = (form.get("razao_social") or "").strip() or None
    cliente.documento = (form.get("documento") or "").strip() or None
    cliente.inscricao_estadual = (form.get("inscricao_estadual") or "").strip() or None
    if "situacao_icms" in form:
        cliente.situacao_icms = (form.get("situacao_icms") or "NAO_CONFIRMADO").strip().upper() or "NAO_CONFIRMADO"
    cliente.cep = (form.get("cep") or "").strip() or None
    cliente.municipio = (form.get("municipio") or "").strip() or None
    cliente.cidade = cliente.municipio
    cliente.municipio_ibge = re.sub(r"\D", "", (form.get("municipio_ibge") or "").strip()) or None
    cliente.estado = (form.get("estado") or "").strip() or None
    cliente.bairro = (form.get("bairro") or "").strip() or None
    cliente.endereco = (form.get("endereco") or "").strip() or None
    cliente.endereco_numero = (form.get("endereco_numero") or "").strip() or None
    cliente.complemento = (form.get("complemento") or "").strip() or None
    cliente.email = (form.get("email") or "").strip() or None
    cliente.observacao = (form.get("observacao") or "").strip() or None


@app.get("/organiza/clientes/novo", response_class=HTMLResponse)
def cliente_novo(request: Request, usuario: Usuario = Depends(usuario_logado)):
    return templates.TemplateResponse("organiza/cliente_form.html", {"request": request, "usuario": usuario, "cliente": None, "erro": ""})


@app.post("/organiza/clientes/novo")
async def cliente_criar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    pais, ddi, telefone = normalizar_contato(form.get("pais"), form.get("ddi"), form.get("telefone"))
    erro = ""
    if not (form.get("nome") or "").strip():
        erro = "Informe o nome do cliente."
    elif not telefone_valido(telefone, pais, ddi):
        erro = "Informe um WhatsApp válido para o país selecionado."
    elif db.query(Cliente).filter(Cliente.ddi == ddi, Cliente.telefone == telefone).first():
        erro = "Já existe um cliente com este WhatsApp."
    if erro:
        cliente = Cliente()
        preencher_cliente(cliente, form)
        return templates.TemplateResponse("organiza/cliente_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "erro": erro}, status_code=400)
    cliente = Cliente()
    preencher_cliente(cliente, form)
    db.add(cliente); db.commit(); db.refresh(cliente)
    return RedirectResponse(f"/organiza/clientes/{cliente.id}", status_code=303)


@app.get("/organiza/clientes/{cliente_id}", response_class=HTMLResponse)
def cliente_detalhe(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).options(
        selectinload(Cliente.equipamentos).selectinload(Equipamento.solvoz_empresa)
    ).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    if not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)
        db.commit()
    somente_consulta = (request.query_params.get("consulta") or "").strip() == "1"
    status_filtro = (request.query_params.get("status_equipamento") or "Ativo").strip()
    if somente_consulta and (request.query_params.get("venda_id") or "").strip():
        # Ao consultar a partir de Vendas, a venda selecionada precisa aparecer
        # mesmo que o status dela seja Montagem, Entregue etc.
        status_filtro = "Todos"
    tipo_filtro = tipo_equipamento_padrao(request.query_params.get("tipo_equipamento") or "")
    equipamentos = list(cliente.equipamentos)
    if status_filtro != "Todos":
        equipamentos = [eq for eq in equipamentos if (eq.status or "Ativo") == status_filtro]
    if tipo_filtro:
        equipamentos = [eq for eq in equipamentos if tipo_equipamento_padrao(eq.tipo or "") == tipo_filtro]
    equipamentos = ordenar_equipamentos(equipamentos)
    manutencoes = db.query(Manutencao).filter(Manutencao.cliente_id == cliente_id).order_by(Manutencao.criado_em.desc()).all()

    venda_consulta = None
    campanha_manual = None
    destinatario_manual = None
    whatsapp_campanha_manual = ""
    campanha_status_rotulo = ""
    try:
        venda_id_consulta = int(request.query_params.get("venda_id") or 0)
    except (TypeError, ValueError):
        venda_id_consulta = 0
    if venda_id_consulta:
        venda_consulta = next((eq for eq in cliente.equipamentos if int(eq.id) == venda_id_consulta), None)
    try:
        campanha_id_manual = int(request.query_params.get("campanha_id") or 0)
    except (TypeError, ValueError):
        campanha_id_manual = 0
    if somente_consulta and campanha_id_manual:
        campanha_manual = db.query(Campanha).filter(
            Campanha.id == campanha_id_manual,
            func.upper(Campanha.lista_tipo) == "ATUALIZACAO",
        ).first()
        if campanha_manual:
            destinatario_manual = db.query(CampanhaDestinatario).filter(
                CampanhaDestinatario.campanha_id == campanha_manual.id,
                CampanhaDestinatario.cliente_id == cliente.id,
            ).first()
            if destinatario_manual:
                whatsapp_campanha_manual = _whatsapp_url_pronta(
                    destinatario_manual.telefone_pronto or cliente.whatsapp_completo() or "",
                    destinatario_manual.mensagem_pronta or "",
                )
                campanha_status_rotulo = _rotulo_status_envio_campanha(destinatario_manual.status, True)
            else:
                campanha_status_rotulo = _rotulo_status_envio_campanha(None, False)
    retorno_consulta = (request.query_params.get("retorno") or "/organiza/vendas").strip()
    if not retorno_consulta.startswith("/organiza/vendas"):
        retorno_consulta = "/organiza/vendas"
    atualizacoes_ctx = _atualizacao_contexto_admin_cliente(db, cliente) if not somente_consulta else {"compras": [], "agendamentos": {}, "pacotes": [], "gmail_ok": _gmail_valido(cliente.email)}

    return templates.TemplateResponse("organiza/cliente_detalhe.html", {
        "request": request, "usuario": usuario, "cliente": cliente, "manutencoes": manutencoes,
        "equipamentos": equipamentos, "status_filtro": status_filtro, "tipo_filtro": tipo_filtro,
        "solvoz_acesso": _solvoz_contexto_cliente(cliente, db),
        "humiat_acesso": _humiat_contexto_cliente(cliente, db),
        "solvoz_sucesso": request.query_params.get("solvoz_sucesso", ""),
        "solvoz_erro": request.query_params.get("solvoz_erro", ""),
        "humiat_sucesso": request.query_params.get("humiat_sucesso", ""),
        "humiat_erro": request.query_params.get("humiat_erro", ""),
        "cnpj_sucesso": request.query_params.get("cnpj_sucesso", ""),
        "cnpj_erro": request.query_params.get("cnpj_erro", ""),
        "somente_consulta": somente_consulta,
        "venda_consulta": venda_consulta,
        "campanha_manual": campanha_manual,
        "destinatario_manual": destinatario_manual,
        "whatsapp_campanha_manual": whatsapp_campanha_manual,
        "campanha_status_rotulo": campanha_status_rotulo,
        "retorno_consulta": retorno_consulta,
        "manual_sucesso": request.query_params.get("manual_sucesso", ""),
        "atualizacoes_ctx": atualizacoes_ctx,
        "atualizacao_sucesso": request.query_params.get("atualizacao_sucesso", ""),
        "atualizacao_erro": request.query_params.get("atualizacao_erro", ""),
    })


@app.post("/organiza/clientes/{cliente_id}/humiat-acesso")
async def cliente_humiat_salvar_acesso(
    cliente_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not usuario.is_admin:
        raise HTTPException(403)
    cliente = db.query(Cliente).options(
        selectinload(Cliente.equipamentos).selectinload(Equipamento.solvoz_empresa)
    ).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(404)
    try:
        form = dict(await request.form())
        hu, criado, detalhe_email = _humiat_salvar_acessos_cliente(cliente, form, db, request)
        acao = "Humiat ID criado" if criado else "Acessos Humiat ID atualizados"
        msg = f"{acao} para {hu.email}.{detalhe_email}".strip()
        return RedirectResponse(
            f"/organiza/clientes/{cliente_id}?humiat_sucesso={quote_plus(msg)}", status_code=303
        )
    except (ValueError, RuntimeError) as exc:
        db.rollback()
        return RedirectResponse(
            f"/organiza/clientes/{cliente_id}?humiat_erro={quote_plus(str(exc))}", status_code=303
        )


@app.post("/organiza/clientes/{cliente_id}/humiat-acesso/reenviar")
def cliente_humiat_reenviar_acesso(
    cliente_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not usuario.is_admin:
        raise HTTPException(403)
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(404)
    hu = _humiat_usuario_do_cliente(cliente, db)
    if not hu:
        return RedirectResponse(
            f"/organiza/clientes/{cliente_id}?humiat_erro={quote_plus('Salve os acessos do cliente antes de reenviar o link.')}",
            status_code=303,
        )
    if not int(hu.ativo or 0):
        return RedirectResponse(
            f"/organiza/clientes/{cliente_id}?humiat_erro={quote_plus('O Humiat ID está inativo. Ative o acesso e salve antes de reenviar o link.')}",
            status_code=303,
        )
    try:
        enviar_link_acesso_humiat(db, hu, request=request, primeiro_acesso=False)
        return RedirectResponse(
            f"/organiza/clientes/{cliente_id}?humiat_sucesso={quote_plus('Novo link enviado por e-mail. O cliente poderá refazer a senha única e manter todos os acessos liberados.')}",
            status_code=303,
        )
    except (ValueError, RuntimeError) as exc:
        db.rollback()
        return RedirectResponse(
            f"/organiza/clientes/{cliente_id}?humiat_erro={quote_plus(str(exc))}", status_code=303
        )


@app.post("/organiza/clientes/{cliente_id}/solvoz-acesso")
def cliente_solvoz_criar_acesso(
    cliente_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not usuario.is_admin:
        raise HTTPException(403)
    cliente = db.query(Cliente).options(
        selectinload(Cliente.equipamentos).selectinload(Equipamento.solvoz_empresa)
    ).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(404)
    try:
        resultados = _solvoz_provisionar_cliente(cliente, _solvoz_grupos_cliente(cliente))
        _solvoz_cache_salvar(db, cliente, resultados)
        enviados = sum(1 for r in resultados if r.get("email_enviado"))
        criado = any(bool(r.get("criado")) for r in resultados)
        if enviados:
            msg = "Acesso SolVoz criado e senha provisória enviada para o e-mail do cliente."
        elif criado:
            erros_email = [r.get("email_erro") for r in resultados if r.get("email_erro")]
            msg = "Acesso criado no SolVoz, mas o e-mail não foi enviado" + (f": {erros_email[0]}" if erros_email else ".")
            return RedirectResponse(
                f"/organiza/clientes/{cliente_id}?solvoz_erro={quote_plus(msg)}", status_code=303
            )
        else:
            msg = "Acesso SolVoz atualizado. Os equipamentos foram vinculados ao usuário existente."
        return RedirectResponse(
            f"/organiza/clientes/{cliente_id}?solvoz_sucesso={quote_plus(msg)}", status_code=303
        )
    except (ValueError, RuntimeError) as exc:
        return RedirectResponse(
            f"/organiza/clientes/{cliente_id}?solvoz_erro={quote_plus(str(exc))}", status_code=303
        )


@app.post("/organiza/clientes/{cliente_id}/solvoz-acesso/reenviar")
def cliente_solvoz_reenviar_acesso(
    cliente_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not usuario.is_admin:
        raise HTTPException(403)
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(404)
    email = (cliente.email or "").strip().lower()
    if not email or "@" not in email:
        return RedirectResponse(
            f"/organiza/clientes/{cliente_id}?solvoz_erro={quote_plus('O cliente precisa ter um e-mail válido no Organiza.')}",
            status_code=303,
        )
    try:
        resultado = _solvoz_api_request(
            "/api/integracoes/organiza/solvoz/acesso/reenviar",
            metodo="POST", payload={"email": email},
        )
        resultados = _solvoz_entregar_senha_provisoria(
            cliente, [resultado], _solvoz_grupos_cliente(cliente)
        )
        resultado = resultados[0]
        _solvoz_cache_salvar(db, cliente, resultados)
        if resultado.get("email_enviado"):
            msg = "Nova senha provisória enviada para o e-mail do cliente."
            chave = "solvoz_sucesso"
        else:
            msg = "A senha provisória foi gerada, mas o e-mail não foi enviado: " + (resultado.get("email_erro") or "verifique a configuração do Resend no Organiza.")
            chave = "solvoz_erro"
        return RedirectResponse(
            f"/organiza/clientes/{cliente_id}?{chave}={quote_plus(msg)}", status_code=303
        )
    except RuntimeError as exc:
        return RedirectResponse(
            f"/organiza/clientes/{cliente_id}?solvoz_erro={quote_plus(str(exc))}", status_code=303
        )


@app.get("/organiza/clientes/{cliente_id}/editar", response_class=HTMLResponse)
def cliente_editar(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    return templates.TemplateResponse("organiza/cliente_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "erro": ""})


@app.post("/organiza/clientes/{cliente_id}/editar")
async def cliente_salvar(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    form = dict(await request.form())
    pais, ddi, telefone = normalizar_contato(form.get("pais"), form.get("ddi"), form.get("telefone"))
    erro = ""
    if not (form.get("nome") or "").strip(): erro = "Informe o nome do cliente."
    elif not telefone_valido(telefone, pais, ddi): erro = "Informe um WhatsApp válido para o país selecionado."
    elif db.query(Cliente).filter(Cliente.ddi == ddi, Cliente.telefone == telefone, Cliente.id != cliente_id).first(): erro = "Já existe outro cliente com este WhatsApp."
    if erro:
        preencher_cliente(cliente, form)
        return templates.TemplateResponse("organiza/cliente_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "erro": erro}, status_code=400)
    preencher_cliente(cliente, form); db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente.id}", status_code=303)


@app.post("/organiza/clientes/{cliente_id}/atualizar-cnpj")
def cliente_atualizar_cnpj(
    cliente_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(404)
    cnpj = limpar_documento(cliente.documento or "")
    if len(cnpj) != 14:
        return RedirectResponse(
            f"/organiza/clientes/{cliente_id}?cnpj_erro={quote_plus('O cadastro precisa ter um CNPJ válido para atualização automática.')}",
            status_code=303,
        )
    try:
        dados = consultar_cnpj_publico(cnpj)
        aplicar_dados_cnpj(cliente, dados, atualizar_endereco=True)
        db.commit()
        msg = "Dados empresariais e endereço cadastral atualizados pelo CNPJ. Confira número e complemento."
        return RedirectResponse(f"/organiza/clientes/{cliente_id}?cnpj_sucesso={quote_plus(msg)}", status_code=303)
    except (ValueError, RuntimeError) as exc:
        return RedirectResponse(f"/organiza/clientes/{cliente_id}?cnpj_erro={quote_plus(str(exc))}", status_code=303)


@app.get("/organiza/api/cnpj/{cnpj}")
def api_consultar_cnpj_admin(cnpj: str, usuario: Usuario = Depends(usuario_logado)):
    try:
        dados = consultar_cnpj_publico(cnpj)
        serial = {**dados, "consultado_em": dados["consultado_em"].isoformat()}
        return JSONResponse(serial)
    except ValueError as exc:
        return JSONResponse({"erro": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"erro": str(exc)}, status_code=503)


PACOTE_ATUAL_PADRAO = "2026.1"


def _pacote_release_num(valor: str | None) -> int | None:
    texto = (valor or "").strip().replace("-", ".").replace(",", ".")
    m = re.fullmatch(r"(\d{4})\.(\d{1,2})", texto)
    if not m:
        return None
    ano, parte = map(int, m.groups())
    if parte < 0 or parte > 99:
        return None
    return ano * 100 + parte


def _pacote_label_num(valor: int) -> str:
    valor = int(valor)
    return f"{valor // 100}.{valor % 100}"


def obter_pacote_atual(db: Session) -> str:
    configuracao = db.query(ConfiguracaoSistema).filter(ConfiguracaoSistema.chave == "pacote_atual").first()
    valor = (configuracao.valor if configuracao else PACOTE_ATUAL_PADRAO) or PACOTE_ATUAL_PADRAO
    valor = valor.strip().replace("-", ".")
    return valor if _pacote_release_num(valor) is not None else PACOTE_ATUAL_PADRAO


def _pacotes_disponiveis_solvoz() -> list[str]:
    try:
        promo = _solvoz_atualizacao_promocao_config()
    except Exception:
        promo = {}
    bruto = promo.get("pacotes_disponiveis") if isinstance(promo, dict) else []
    vistos = set()
    saida = []
    for item in (bruto or []):
        label = str(item or "").strip().replace("-", ".")
        numero = _pacote_release_num(label)
        if numero is None or numero in vistos:
            continue
        vistos.add(numero)
        saida.append((numero, _pacote_label_num(numero)))
    saida.sort(key=lambda x: x[0])
    return [label for _, label in saida]


def _primeiro_pacote_disponivel(db: Session) -> str:
    """Retorna o primeiro pacote cronológico conhecido.

    O SolVoz é a fonte preferencial. Se a integração estiver indisponível, usa
    os pacotes já existentes no Organiza para nunca deixar uma máquina sem pacote.
    """
    candidatos: list[tuple[int, str]] = []
    try:
        for label in _pacotes_disponiveis_solvoz():
            numero = _pacote_release_num(label)
            if numero is not None:
                candidatos.append((numero, _pacote_label_num(numero)))
    except Exception:
        pass
    if candidatos:
        return min(candidatos, key=lambda item: item[0])[1]

    valores = []
    try:
        valores.extend(x[0] for x in db.query(Equipamento.pacote).filter(Equipamento.pacote.isnot(None)).distinct().all())
        valores.extend(x[0] for x in db.query(Cliente.pacote).filter(Cliente.pacote.isnot(None)).distinct().all())
    except Exception:
        valores = []
    for valor in valores:
        numero = _pacote_release_num(valor)
        if numero is not None:
            candidatos.append((numero, _pacote_label_num(numero)))
    if candidatos:
        return min(candidatos, key=lambda item: item[0])[1]
    return obter_pacote_atual(db)


def _normalizar_pacote_cadastrado(valor: str | None) -> str | None:
    texto = (valor or "").strip().replace("-", ".").replace(",", ".")
    if not texto:
        return None
    numero = _pacote_release_num(texto)
    if numero is not None:
        return _pacote_label_num(numero)
    especial = texto.upper()
    return especial if especial in {"NA", "NE"} else texto


def calcular_falta_pacote(pacote: str | None, pacote_atual: str = PACOTE_ATUAL_PADRAO) -> int | None:
    """Conta pacotes reais do SolVoz, aceitando versões como 2023.3.

    Equipamento ativo sem pacote cadastrado é tratado como origem desconhecida:
    ele precisa receber desde o primeiro pacote disponível até o pacote-alvo.
    """
    origem = _pacote_release_num(pacote)
    alvo = _pacote_release_num(pacote_atual)
    if alvo is None:
        return None
    if origem is not None and origem >= alvo:
        return 0
    disponiveis = _pacotes_disponiveis_solvoz()
    if disponiveis:
        nums = [_pacote_release_num(x) for x in disponiveis]
        if origem is None:
            return sum(1 for n in nums if n is not None and n <= alvo)
        return sum(1 for n in nums if n is not None and origem < n <= alvo)
    # Fallback para indisponibilidade temporária da integração. Sem pacote
    # cadastrado, mantém o equipamento elegível para não perder a campanha.
    return 1


def _pacote_indice(valor: str | None) -> int | None:
    return _pacote_release_num(valor)


def _pacote_por_indice(indice: int) -> str:
    return _pacote_label_num(indice)


def _pacote_url(valor: str) -> str:
    return (valor or "").strip().replace(".", "-")

ATUALIZACAO_PRECO_PACOTE_CENTAVOS = 25000
ATUALIZACAO_PROMO_TOTAL_CENTAVOS = 25000
ATUALIZACAO_PROMO_DESCONTO_UMA_CENTAVOS = 5000
_ATUALIZACAO_PROMO_CACHE = {"expira_em": None, "dados": None}


def _solvoz_atualizacao_promocao_config() -> dict:
    """Lê a promoção no SolVoz para a mensagem usar exatamente o mesmo preço do popup."""
    agora = datetime.now()
    cache_dados = _ATUALIZACAO_PROMO_CACHE.get("dados")
    expira = _ATUALIZACAO_PROMO_CACHE.get("expira_em")
    if cache_dados and isinstance(expira, datetime) and agora < expira:
        return dict(cache_dados)
    padrao = {
        "ativo": False, "vigente": False,
        "valor_total_centavos": ATUALIZACAO_PROMO_TOTAL_CENTAVOS,
        "desconto_uma_centavos": ATUALIZACAO_PROMO_DESCONTO_UMA_CENTAVOS,
        "valida_ate": "", "valida_ate_br": "",
        "preco_pacote_centavos": ATUALIZACAO_PRECO_PACOTE_CENTAVOS,
        "pacotes_disponiveis": [],
    }
    try:
        dados = _solvoz_api_request("/api/integracoes/organiza/atualizacao-promocao")
        if isinstance(dados, dict) and dados.get("ok"):
            promo = dict(padrao)
            promo.update(dados)
            _ATUALIZACAO_PROMO_CACHE["dados"] = promo
            _ATUALIZACAO_PROMO_CACHE["expira_em"] = agora + timedelta(seconds=60)
            return promo
    except Exception as exc:
        print("WARN promoção atualização SolVoz:", repr(exc))
        if cache_dados:
            return dict(cache_dados)
        # Evita repetir a mesma falha centenas de vezes durante migrações/lotes.
        _ATUALIZACAO_PROMO_CACHE["dados"] = dict(padrao)
        _ATUALIZACAO_PROMO_CACHE["expira_em"] = agora + timedelta(seconds=60)
    return padrao


def _valores_atualizacao_cliente(pacotes: list[str], promo_override: dict | None = None) -> dict:
    # Durante uma campanha preparada usamos o snapshot congelado e não
    # consultamos o SolVoz novamente a cada cliente.
    promo = dict(promo_override or _solvoz_atualizacao_promocao_config())
    quantidade = max(1, len(pacotes or []))
    preco_pacote = max(1, int(promo.get("preco_pacote_centavos") or ATUALIZACAO_PRECO_PACOTE_CENTAVOS))
    normal = quantidade * preco_pacote
    cobrado = normal
    if bool(promo.get("vigente")):
        if quantidade == 1:
            cobrado = max(1, preco_pacote - max(0, int(promo.get("desconto_uma_centavos") or 0)))
        else:
            cobrado = min(normal, max(1, int(promo.get("valor_total_centavos") or normal)))
    return {"normal_centavos": normal, "cobrado_centavos": cobrado, "promocao": promo, "tem_desconto": bool(cobrado < normal)}


def _chave_equipamento_mais_antigo(eq: Equipamento):
    data_ref = eq.data_compra or eq.previsao_entrega
    return (
        0 if data_ref else 1,
        data_ref or date.max,
        eq.criado_em or datetime.max,
        eq.id or 0,
    )


def _equipamento_ativo_mais_antigo(cliente: Cliente | None) -> Equipamento | None:
    """Retorna a máquina ativa mais antiga do cliente para campanhas de atualização."""
    if not cliente:
        return None
    ativos = [eq for eq in (cliente.equipamentos or []) if (eq.status or "").strip().lower() == "ativo"]
    return min(ativos, key=_chave_equipamento_mais_antigo) if ativos else None


def _sincronizar_pacote_cliente(db: Session, cliente_id: int, primeiro_pacote: str | None = None) -> tuple[int, int]:
    """Garante pacote em todas as máquinas e espelha no cadastro do cliente."""
    cliente = db.query(Cliente).filter(Cliente.id == int(cliente_id)).first()
    if not cliente:
        return 0, 0
    equipamentos = db.query(Equipamento).filter(Equipamento.cliente_id == int(cliente_id)).all()
    if not equipamentos:
        return 0, 0
    primeiro = _normalizar_pacote_cadastrado(primeiro_pacote) or _primeiro_pacote_disponivel(db)
    pacote_atual = obter_pacote_atual(db)
    eq_alterados = 0
    for eq in equipamentos:
        pacote = _normalizar_pacote_cadastrado(eq.pacote) or primeiro
        if (eq.pacote or "").strip() != pacote:
            eq.pacote = pacote
            eq_alterados += 1
        falta = calcular_falta_pacote(eq.pacote, pacote_atual)
        if eq.falta_pacote != falta:
            eq.falta_pacote = falta
            eq_alterados += 1
    ativos = [eq for eq in equipamentos if (eq.status or "").strip().lower() == "ativo"]
    referencia = min(ativos or equipamentos, key=_chave_equipamento_mais_antigo)
    pacote_cliente = _normalizar_pacote_cadastrado(referencia.pacote) or primeiro
    cliente_alterado = 0
    if (cliente.pacote or "").strip() != pacote_cliente:
        cliente.pacote = pacote_cliente
        cliente_alterado = 1
    falta_cliente = calcular_falta_pacote(cliente.pacote, pacote_atual)
    if cliente.falta_pacote != falta_cliente:
        cliente.falta_pacote = falta_cliente
        cliente_alterado = 1
    return eq_alterados, cliente_alterado


def _corrigir_consistencia_pacotes(db: Session) -> tuple[int, int, str]:
    """Backfill idempotente em lote, sem N+1 de consultas por cliente.

    A rotina continua sendo executada na implantação, mas carrega clientes e
    equipamentos com selectinload. Assim uma base grande não faz duas ou três
    consultas adicionais para cada cliente.
    """
    primeiro = _primeiro_pacote_disponivel(db)
    pacote_atual = obter_pacote_atual(db)
    total_eq = 0
    total_clientes = 0
    clientes = db.query(Cliente).options(selectinload(Cliente.equipamentos)).all()
    for cliente in clientes:
        equipamentos = list(cliente.equipamentos or [])
        if not equipamentos:
            continue
        for eq in equipamentos:
            pacote = _normalizar_pacote_cadastrado(eq.pacote) or primeiro
            if (eq.pacote or "").strip() != pacote:
                eq.pacote = pacote
                total_eq += 1
            falta = calcular_falta_pacote(eq.pacote, pacote_atual)
            if eq.falta_pacote != falta:
                eq.falta_pacote = falta
                total_eq += 1
        ativos = [eq for eq in equipamentos if (eq.status or "").strip().lower() == "ativo"]
        referencia = min(ativos or equipamentos, key=_chave_equipamento_mais_antigo)
        pacote_cliente = _normalizar_pacote_cadastrado(referencia.pacote) or primeiro
        alterou_cliente = False
        if (cliente.pacote or "").strip() != pacote_cliente:
            cliente.pacote = pacote_cliente
            alterou_cliente = True
        falta_cliente = calcular_falta_pacote(cliente.pacote, pacote_atual)
        if cliente.falta_pacote != falta_cliente:
            cliente.falta_pacote = falta_cliente
            alterou_cliente = True
        if alterou_cliente:
            total_clientes += 1
    if total_eq or total_clientes:
        db.commit()
    return total_eq, total_clientes, primeiro


def _pacotes_atualizacao_cliente(
    campanha: Campanha, cliente: Cliente, pacotes_disponiveis: list[str] | None = None
) -> list[str]:
    """Calcula a atualização pela máquina ativa mais antiga do cliente.

    Se a máquina mais antiga não tiver pacote cadastrado, a atualização começa
    no primeiro pacote disponível e segue até o pacote-alvo da campanha.
    """
    if not campanha or (campanha.lista_tipo or "").upper() != "ATUALIZACAO" or not cliente:
        return []
    pacote_alvo = (campanha.pacote_alvo or PACOTE_ATUAL_PADRAO).strip()
    alvo = _pacote_release_num(pacote_alvo)
    if alvo is None:
        return []

    eq_referencia = _equipamento_ativo_mais_antigo(cliente)
    if not eq_referencia:
        return []
    origem = _pacote_release_num((eq_referencia.pacote or "").strip() or None)
    if origem is not None and origem >= alvo:
        return []

    disponiveis = _pacotes_disponiveis_solvoz() if pacotes_disponiveis is None else list(pacotes_disponiveis)
    pacotes = []
    for label in disponiveis:
        numero = _pacote_release_num(label)
        if numero is not None and numero <= alvo and (origem is None or origem < numero):
            pacotes.append(_pacote_label_num(numero))
    if pacotes:
        return pacotes
    return [_pacote_label_num(alvo)]


def _token_contexto_atualizacao(cliente: Cliente, pacotes: list[str]) -> str:
    """Token compacto: os dados pessoais ficam no Organiza e não viajam na URL."""
    if not cliente or not pacotes or not SOLVOZ_API_TOKEN:
        return ""
    inicio_url = _pacote_url(pacotes[0])
    fim_url = _pacote_url(pacotes[-1])
    cliente_id = int(cliente.id)
    assinatura_base = f"v2|{cliente_id}|{inicio_url}|{fim_url}"
    sig = hmac.new(SOLVOZ_API_TOKEN.encode("utf-8"), assinatura_base.encode("utf-8"), hashlib.sha256).hexdigest()[:20]
    return f"{cliente_id}.{sig}"


def _registrar_oferta_atualizacao_cliente(
    cliente: Cliente, campanha: Campanha, status: str, order_nsu: str = "",
    valor_normal_centavos: int | None = None, valor_promocional_centavos: int | None = None,
    pacotes_override: list[str] | None = None,
) -> None:
    if not cliente or not campanha:
        return
    pacotes = list(pacotes_override or []) or _pacotes_atualizacao_cliente(campanha, cliente)
    if not pacotes:
        return
    cliente.atualizacao_oferta_status = (status or "OFERTA_ENVIADA")[:30]
    cliente.atualizacao_oferta_periodo = pacotes[0] if len(pacotes) == 1 else f"{pacotes[0]} a {pacotes[-1]}"
    cliente.atualizacao_oferta_pacotes = json.dumps(pacotes, ensure_ascii=False)
    valores = None
    if valor_normal_centavos is None or valor_promocional_centavos is None:
        valores = _valores_atualizacao_cliente(pacotes)
    cliente.atualizacao_oferta_valor_normal_centavos = int(
        valor_normal_centavos if valor_normal_centavos is not None else valores["normal_centavos"]
    )
    cliente.atualizacao_oferta_valor_promocional_centavos = int(
        valor_promocional_centavos if valor_promocional_centavos is not None else valores["cobrado_centavos"]
    )
    if order_nsu:
        cliente.atualizacao_oferta_order_nsu = order_nsu[:120]
    cliente.atualizacao_oferta_atualizado_em = datetime.now()


def _link_atualizacao_cliente(
    campanha: Campanha, cliente: Cliente, pacotes_override: list[str] | None = None
) -> str:
    """Gera o link público individual com período e contexto assinado do cliente."""
    pacotes = list(pacotes_override or []) or _pacotes_atualizacao_cliente(campanha, cliente)
    if not pacotes:
        return ""
    inicio_url = _pacote_url(pacotes[0])
    fim_url = _pacote_url(pacotes[-1])
    contexto = _token_contexto_atualizacao(cliente, pacotes)
    if contexto:
        return f"{SOLVOZ_BASE_URL.rstrip('/')}/a/{inicio_url}/{fim_url}/{contexto}"
    return f"{SOLVOZ_BASE_URL.rstrip('/')}/atualizacoes/karaokerj/{inicio_url}/{fim_url}"


def _equipamento_tem_atualizacao(
    eq: Equipamento, pacote_alvo: str, pacotes_disponiveis: list[str] | None = None
) -> bool:
    """Lista de Atualização: equipamento ativo e com atualização disponível."""
    if (eq.status or "").strip().lower() != "ativo":
        return False
    if pacotes_disponiveis is not None:
        origem = _pacote_release_num((eq.pacote or "").strip() or None)
        alvo = _pacote_release_num(pacote_alvo)
        if alvo is None or (origem is not None and origem >= alvo):
            return False
        for label in pacotes_disponiveis:
            numero = _pacote_release_num(label)
            if numero is not None and numero <= alvo and (origem is None or origem < numero):
                return True
        return False
    falta = calcular_falta_pacote((eq.pacote or "").strip() or None, pacote_alvo)
    return bool(falta is not None and falta > 0)


def _clientes_lista_atualizacao(
    db: Session, pacote_alvo: str | None = None, pacotes_disponiveis: list[str] | None = None
) -> list[dict]:
    pacote_alvo = pacote_alvo or obter_pacote_atual(db)
    clientes = db.query(Cliente).options(selectinload(Cliente.equipamentos)).order_by(Cliente.nome.asc()).all()
    lista = []
    for cliente in clientes:
        eq_referencia = _equipamento_ativo_mais_antigo(cliente)
        if not eq_referencia or not _equipamento_tem_atualizacao(eq_referencia, pacote_alvo, pacotes_disponiveis):
            continue
        # Um cliente com várias máquinas entra uma única vez e sempre pela
        # máquina ativa mais antiga, que define o pacote inicial da campanha.
        lista.append({"cliente": cliente, "equipamentos": [eq_referencia]})
    return lista


MESES_ALUGUEL = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}


def _nome_aluguel_normalizado(valor: str) -> str:
    """Nome de campanha: sem datas, acentos, emojis ou espaços duplicados."""
    texto = (valor or "").strip()
    # Remove datas comuns que vieram anexadas ao nome na planilha histórica.
    texto = re.sub(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", " ", texto)
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^A-Za-z0-9 .'-]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" -.,")
    return texto[:180]


def _mes_aluguel_numero(valor) -> int | None:
    if valor in (None, ""):
        return None
    if isinstance(valor, int):
        return valor if 1 <= valor <= 12 else None
    texto = unicodedata.normalize("NFKD", str(valor)).encode("ascii", "ignore").decode("ascii").strip().lower()
    encontrado = re.match(r"\s*(\d{1,2})", texto)
    if encontrado:
        numero = int(encontrado.group(1))
        return numero if 1 <= numero <= 12 else None
    nomes = {unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii").lower(): numero for numero, nome in MESES_ALUGUEL.items()}
    return nomes.get(texto)


def _clientes_lista_aluguel(db: Session, mes: int | None = None, integracao: str = "") -> list[CampanhaAluguelContato]:
    consulta = db.query(CampanhaAluguelContato)
    if mes and 1 <= int(mes) <= 12:
        consulta = consulta.filter(CampanhaAluguelContato.ultimo_mes_aluguel == int(mes))
    integracao = (integracao or "").strip().upper()
    if integracao in {"PLANILHA", "CONNECT"}:
        consulta = consulta.filter(func.upper(CampanhaAluguelContato.integracao) == integracao)
    return consulta.order_by(CampanhaAluguelContato.nome.asc(), CampanhaAluguelContato.id.asc()).all()


def _filtrar_lista_atualizacao(lista: list[dict], busca: str = "", status: str = "") -> list[dict]:
    busca_norm = re.sub(r"\D", "", busca or "")
    busca_txt = (busca or "").strip().casefold()
    status = (status or "").strip().upper()
    saida = []
    for item in lista:
        cliente = item.get("cliente")
        if not cliente:
            continue
        ativo = int(getattr(cliente, "campanhas_ativo", 1) or 0) == 1
        if status == "ATIVO" and not ativo:
            continue
        if status == "INATIVO" and ativo:
            continue
        if busca_txt:
            nome = (cliente.nome or "").casefold()
            telefone = re.sub(r"\D", "", f"{cliente.ddi or ''}{cliente.telefone or ''}")
            if busca_txt not in nome and (not busca_norm or busca_norm not in telefone):
                continue
        saida.append(item)
    return saida


def _filtrar_lista_aluguel(lista: list[CampanhaAluguelContato], busca: str = "", status: str = "") -> list[CampanhaAluguelContato]:
    busca_norm = re.sub(r"\D", "", busca or "")
    busca_txt = (busca or "").strip().casefold()
    status = (status or "").strip().upper()
    saida = []
    for contato in lista:
        ativo = int(getattr(contato, "campanhas_ativo", 1) or 0) == 1
        if status == "ATIVO" and not ativo:
            continue
        if status == "INATIVO" and ativo:
            continue
        if busca_txt:
            nome = (contato.nome or "").casefold()
            telefone = re.sub(r"\D", "", f"{contato.ddi or ''}{contato.telefone or ''}")
            if busca_txt not in nome and (not busca_norm or busca_norm not in telefone):
                continue
        saida.append(contato)
    return saida


def _cliente_elegivel_atualizacao(cliente: Cliente, pacote_alvo: str) -> bool:
    if not int(getattr(cliente, "campanhas_ativo", 1) or 0):
        return False
    eq_referencia = _equipamento_ativo_mais_antigo(cliente)
    return bool(eq_referencia and _equipamento_tem_atualizacao(eq_referencia, pacote_alvo))


def _contato_elegivel_aluguel(contato: CampanhaAluguelContato) -> bool:
    return bool(contato and int(getattr(contato, "campanhas_ativo", 1) or 0))


def _rotulo_lista_campanha(campanha: Campanha | None) -> str:
    return "Clientes de Aluguel" if campanha and (campanha.lista_tipo or "").upper() == "ALUGUEL" else "Clientes de Atualização"


def _mensagem_campanha(
    campanha: Campanha, pessoa, pacotes_override: list[str] | None = None,
    valores_override: dict | None = None, link_override: str | None = None,
) -> str:
    mensagem = (campanha.mensagem or "").strip().replace("{nome}", (getattr(pessoa, "nome", "") or "").strip())
    if (campanha.lista_tipo or "").upper() == "ATUALIZACAO":
        pacotes = list(pacotes_override or []) or _pacotes_atualizacao_cliente(campanha, pessoa)
        link = link_override if link_override is not None else _link_atualizacao_cliente(campanha, pessoa, pacotes)
        if pacotes:
            valores = dict(valores_override or _valores_atualizacao_cliente(pacotes))
            pacotes_txt = " / ".join(pacotes)
            total_txt = f"R$ {valores['normal_centavos'] / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            linhas = [
                f"Pacotes a serem atualizados: {pacotes_txt}",
                f"Valor normal: {total_txt}",
            ]
            promo = valores["promocao"]
            if valores["tem_desconto"] and bool(promo.get("vigente")):
                promo_txt = f"R$ {valores['cobrado_centavos'] / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                validade = str(promo.get("valida_ate_br") or "").strip()
                linhas.append(f"Oferta: {promo_txt}" + (f" até {validade}" if validade else ""))
                linhas.append("Veja as músicas e aproveite:")
            else:
                linhas.append("Veja as músicas da atualização:")
            mensagem = f"{mensagem}\n\n" + "\n".join(linhas)
            mensagem = mensagem.strip()
    else:
        link = link_override if link_override is not None else (getattr(campanha, "link", None) or "").strip()
    if link:
        mensagem = f"{mensagem}\n{link}".strip()
    return mensagem


def _whatsapp_url_pronta(telefone: str, mensagem: str) -> str:
    numero = re.sub(r"\D", "", telefone or "")
    return f"https://wa.me/{numero}?{urlencode({'text': mensagem or ''})}"


def _whatsapp_campanha_url(campanha: Campanha, pessoa) -> str:
    return _whatsapp_url_pronta(pessoa.whatsapp_completo() or "", _mensagem_campanha(campanha, pessoa))


def _preparar_snapshot_destinatario(
    campanha: Campanha, destinatario, pessoa,
    promo_snapshot: dict | None = None, pacotes_disponiveis: list[str] | None = None,
) -> None:
    """Congela mensagem/link/preço antes do início do envio rápido.

    Quando recebe o snapshot da promoção e a lista de pacotes, esta função é
    100% local: nenhuma chamada ao SolVoz é feita dentro do loop de clientes.
    """
    if not campanha or not destinatario or not pessoa:
        return
    telefone = re.sub(r"\D", "", pessoa.whatsapp_completo() or "")
    link = ""
    pacotes: list[str] = []
    valor_normal = None
    valor_promo = None
    if (campanha.lista_tipo or "").upper() == "ATUALIZACAO":
        pacotes = _pacotes_atualizacao_cliente(campanha, pessoa, pacotes_disponiveis)
        link = _link_atualizacao_cliente(campanha, pessoa, pacotes) if pacotes else ""
        valores = _valores_atualizacao_cliente(pacotes, promo_snapshot) if pacotes else {}
        valor_normal = int(valores.get("normal_centavos") or 0) if valores else None
        valor_promo = int(valores.get("cobrado_centavos") or 0) if valores else None
        mensagem = _mensagem_campanha(
            campanha, pessoa, pacotes_override=pacotes, valores_override=valores, link_override=link
        )
        if pacotes:
            _registrar_oferta_atualizacao_cliente(
                pessoa, campanha, "PREPARADA",
                valor_normal_centavos=valor_normal,
                valor_promocional_centavos=valor_promo,
                pacotes_override=pacotes,
            )
    else:
        link = (campanha.link or "").strip()
        mensagem = _mensagem_campanha(campanha, pessoa, link_override=link)
    destinatario.telefone_pronto = telefone[:40] or None
    destinatario.mensagem_pronta = mensagem
    destinatario.link_pronto = link[:1000] or None
    destinatario.pacotes_prontos = json.dumps(pacotes, ensure_ascii=False) if pacotes else None
    destinatario.valor_normal_centavos = valor_normal
    destinatario.valor_promocional_centavos = valor_promo


def _precalcular_mensagens_campanha(
    db: Session, campanha: Campanha, promo_snapshot: dict | None = None
) -> None:
    """Monta a campanha inteira antes do primeiro envio.

    Para Atualização, o SolVoz é consultado no máximo uma vez aqui. Depois
    todos os 500+ destinatários são montados somente com dados locais.
    """
    pacotes_disponiveis = None
    if (campanha.lista_tipo or "").upper() == "ATUALIZACAO":
        promo_snapshot = dict(promo_snapshot or _solvoz_atualizacao_promocao_config())
        pacotes_disponiveis = [str(x).strip() for x in (promo_snapshot.get("pacotes_disponiveis") or []) if str(x).strip()]
    if (campanha.lista_tipo or "").upper() == "ALUGUEL":
        destinos = db.query(CampanhaAluguelDestinatario).options(
            selectinload(CampanhaAluguelDestinatario.contato)
        ).filter(CampanhaAluguelDestinatario.campanha_id == campanha.id).all()
        for dest in destinos:
            if not dest.mensagem_pronta and dest.contato:
                _preparar_snapshot_destinatario(campanha, dest, dest.contato)
    else:
        destinos = db.query(CampanhaDestinatario).options(
            selectinload(CampanhaDestinatario.cliente).selectinload(Cliente.equipamentos)
        ).filter(CampanhaDestinatario.campanha_id == campanha.id).all()
        for dest in destinos:
            if not dest.mensagem_pronta and dest.cliente:
                _preparar_snapshot_destinatario(
                    campanha, dest, dest.cliente,
                    promo_snapshot=promo_snapshot, pacotes_disponiveis=pacotes_disponiveis,
                )


def _modelo_destinatario_campanha(campanha: Campanha | None):
    if campanha and (campanha.lista_tipo or "").upper() == "ALUGUEL":
        return CampanhaAluguelDestinatario
    return CampanhaDestinatario


def _contagens_campanha(db: Session, campanha_id: int) -> dict:
    campanha = db.query(Campanha).filter(Campanha.id == campanha_id).first()
    modelo = _modelo_destinatario_campanha(campanha)
    linhas = db.query(modelo.status, func.count(modelo.id)).filter(
        modelo.campanha_id == campanha_id
    ).group_by(modelo.status).all()
    contagens = {status: int(qtd) for status, qtd in linhas}
    total = sum(contagens.values())
    processados = contagens.get("PROCESSADO", 0) + contagens.get("ENVIADO", 0)
    ignorados = contagens.get("IGNORADO", 0)
    pendentes = contagens.get("PENDENTE", 0) + contagens.get("EM_ENVIO", 0)
    return {"total": total, "enviados": processados, "processados": processados, "ignorados": ignorados, "pendentes": pendentes, **contagens}


def _garantir_lotes_campanha(db: Session, campanha: Campanha) -> list[CampanhaLote]:
    """Garante lotes sem renumerar destinatários já atribuídos.

    Isso preserva lotes complementares criados depois da campanha original.
    """
    modelo = _modelo_destinatario_campanha(campanha)
    linhas = db.query(modelo).filter(modelo.campanha_id == campanha.id).order_by(modelo.id.asc()).all()
    if not linhas:
        return []
    alterou = False
    atribuidos = [int(getattr(dest, "lote_numero", 0) or 0) for dest in linhas if int(getattr(dest, "lote_numero", 0) or 0) > 0]
    proximo_numero = max(atribuidos, default=0) + 1
    sem_lote = [dest for dest in linhas if int(getattr(dest, "lote_numero", 0) or 0) <= 0]
    if sem_lote:
        if not atribuidos:
            proximo_numero = 1
        for idx, dest in enumerate(sem_lote):
            dest.lote_numero = proximo_numero + (idx // CAMPANHA_LOTE_TAMANHO)
            alterou = True
    totais: dict[int, int] = {}
    for dest in linhas:
        numero = int(dest.lote_numero or 0)
        if numero > 0:
            totais[numero] = totais.get(numero, 0) + 1
    existentes = {int(l.numero): l for l in db.query(CampanhaLote).filter(CampanhaLote.campanha_id == campanha.id).all()}
    for numero, total in sorted(totais.items()):
        lote = existentes.get(numero)
        if not lote:
            db.add(CampanhaLote(campanha_id=campanha.id, numero=numero, total=total))
            alterou = True
        elif int(lote.total or 0) != total:
            lote.total = total
            alterou = True
    for numero, lote in existentes.items():
        if numero not in totais and int(lote.total or 0) != 0:
            lote.total = 0
            alterou = True
    if alterou:
        db.commit()
    return db.query(CampanhaLote).filter(CampanhaLote.campanha_id == campanha.id, CampanhaLote.total > 0).order_by(CampanhaLote.numero.asc()).all()


def _lote_pendentes(db: Session, campanha: Campanha, lote_numero: int) -> int:
    modelo = _modelo_destinatario_campanha(campanha)
    return int(db.query(func.count(modelo.id)).filter(
        modelo.campanha_id == campanha.id,
        modelo.lote_numero == int(lote_numero),
        modelo.status.in_(["PENDENTE", "EM_ENVIO"]),
    ).scalar() or 0)


def _liberar_lotes_expirados(db: Session, campanha: Campanha) -> None:
    limite = datetime.now() - timedelta(minutes=30)
    expirados = db.query(CampanhaLote).filter(
        CampanhaLote.campanha_id == campanha.id,
        CampanhaLote.concluido_em.is_(None),
        CampanhaLote.reservado_por_id.isnot(None),
        CampanhaLote.reservado_em.isnot(None),
        CampanhaLote.reservado_em < limite,
    ).all()
    if not expirados:
        return
    modelo = _modelo_destinatario_campanha(campanha)
    for lote in expirados:
        db.query(modelo).filter(
            modelo.campanha_id == campanha.id,
            modelo.lote_numero == lote.numero,
            modelo.status == "EM_ENVIO",
        ).update({
            modelo.status: "PENDENTE",
            modelo.reservado_por_id: None,
            modelo.reservado_em: None,
        }, synchronize_session=False)
        lote.reservado_por_id = None
        lote.reservado_em = None
    db.commit()


def _obter_lote_usuario(db: Session, campanha: Campanha, usuario: Usuario) -> CampanhaLote | None:
    """Reserva atomicamente um lote inteiro de até 100 para o usuário."""
    _garantir_lotes_campanha(db, campanha)
    _liberar_lotes_expirados(db, campanha)

    # Continua no lote já reservado por este usuário enquanto houver itens.
    atuais = db.query(CampanhaLote).filter(
        CampanhaLote.campanha_id == campanha.id,
        CampanhaLote.reservado_por_id == usuario.id,
        CampanhaLote.concluido_em.is_(None),
    ).order_by(CampanhaLote.numero.asc()).all()
    for lote in atuais:
        if _lote_pendentes(db, campanha, lote.numero) > 0:
            lote.reservado_em = datetime.now()
            db.commit()
            return lote
        lote.concluido_em = datetime.now()
        db.commit()

    # Pega o próximo lote livre. O UPDATE condicional evita corrida entre usuários.
    while True:
        candidato = db.query(CampanhaLote.id).filter(
            CampanhaLote.campanha_id == campanha.id,
            CampanhaLote.reservado_por_id.is_(None),
            CampanhaLote.concluido_em.is_(None),
        ).order_by(CampanhaLote.numero.asc()).limit(1).scalar()
        if not candidato:
            return None
        agora = datetime.now()
        alterados = db.query(CampanhaLote).filter(
            CampanhaLote.id == candidato,
            CampanhaLote.reservado_por_id.is_(None),
            CampanhaLote.concluido_em.is_(None),
        ).update({
            CampanhaLote.reservado_por_id: usuario.id,
            CampanhaLote.reservado_em: agora,
        }, synchronize_session=False)
        db.commit()
        if not alterados:
            continue
        lote = db.query(CampanhaLote).filter(CampanhaLote.id == candidato).first()
        modelo = _modelo_destinatario_campanha(campanha)
        db.query(modelo).filter(
            modelo.campanha_id == campanha.id,
            modelo.lote_numero == lote.numero,
            modelo.status == "PENDENTE",
        ).update({
            modelo.status: "EM_ENVIO",
            modelo.reservado_por_id: usuario.id,
            modelo.reservado_em: agora,
        }, synchronize_session=False)
        db.commit()
        return lote


def _fila_lote_usuario(db: Session, campanha: Campanha, usuario: Usuario, lote: CampanhaLote) -> list[dict]:
    modelo = _modelo_destinatario_campanha(campanha)
    rel = CampanhaAluguelDestinatario.contato if (campanha.lista_tipo or "").upper() == "ALUGUEL" else CampanhaDestinatario.cliente
    destinos = db.query(modelo).options(selectinload(rel)).filter(
        modelo.campanha_id == campanha.id,
        modelo.lote_numero == lote.numero,
        modelo.status == "EM_ENVIO",
        modelo.reservado_por_id == usuario.id,
    ).order_by(modelo.id.asc()).all()
    fila = []
    for dest in destinos:
        pessoa = dest.contato if (campanha.lista_tipo or "").upper() == "ALUGUEL" else dest.cliente
        if not pessoa:
            continue
        fila.append({
            "id": int(dest.id),
            "nome": (pessoa.nome or "Cliente").strip(),
            "telefone": f"+{pessoa.ddi or ''} {pessoa.telefone_formatado()}",
            "whatsapp_url": _whatsapp_url_pronta(dest.telefone_pronto or "", dest.mensagem_pronta or ""),
            "link": dest.link_pronto or "",
            "processar_url": f"/organiza/campanhas/{campanha.id}/destinatarios/{dest.id}/whatsapp-proximo",
            "pular_url": f"/organiza/campanhas/{campanha.id}/destinatarios/{dest.id}/pular-rapido",
        })
    return fila


def _resumo_lotes_campanha(db: Session, campanha: Campanha) -> list[dict]:
    # Os lotes existem desde a criação da campanha, inclusive no RASCUNHO.
    lotes = _garantir_lotes_campanha(db, campanha)
    saida = []
    modelo = _modelo_destinatario_campanha(campanha)
    for lote in lotes:
        processados = int(db.query(func.count(modelo.id)).filter(
            modelo.campanha_id == campanha.id, modelo.lote_numero == lote.numero,
            modelo.status.in_(["PROCESSADO", "ENVIADO", "IGNORADO"]),
        ).scalar() or 0)
        saida.append({
            "numero": lote.numero,
            "total": lote.total,
            "processados": processados,
            "reservado_por": lote.reservado_por.nome if lote.reservado_por else "",
            "reservado_por_id": int(lote.reservado_por_id or 0),
            "concluido": bool(lote.concluido_em),
            "pendentes": _lote_pendentes(db, campanha, lote.numero),
        })
    return saida


def _preparar_campanha_com_lotes(db: Session, campanha: Campanha) -> int:
    """Congela destinatários, mensagens e lotes antes de qualquer envio.

    Atualização consulta o SolVoz somente nesta preparação. Depois, selecionar
    ou enviar um lote usa exclusivamente o snapshot salvo no Organiza.
    """
    # Recriação de rascunho: remove apenas o preparo desta campanha.
    db.query(CampanhaLote).filter(CampanhaLote.campanha_id == campanha.id).delete(synchronize_session=False)
    db.query(CampanhaDestinatario).filter(CampanhaDestinatario.campanha_id == campanha.id).delete(synchronize_session=False)
    db.query(CampanhaAluguelDestinatario).filter(CampanhaAluguelDestinatario.campanha_id == campanha.id).delete(synchronize_session=False)
    db.flush()

    promo_snapshot = None
    total = 0
    if (campanha.lista_tipo or "").upper() == "ALUGUEL":
        contatos = [c for c in _clientes_lista_aluguel(db, mes=campanha.aluguel_mes) if _contato_elegivel_aluguel(c)]
        for contato in contatos:
            db.add(CampanhaAluguelDestinatario(campanha_id=campanha.id, contato_id=contato.id, status="PENDENTE"))
        total = len(contatos)
    else:
        pacote_alvo = campanha.pacote_alvo or obter_pacote_atual(db)
        promo_snapshot = _solvoz_atualizacao_promocao_config()
        pacotes_disponiveis = [
            str(x).strip() for x in (promo_snapshot.get("pacotes_disponiveis") or []) if str(x).strip()
        ]
        lista = _clientes_lista_atualizacao(db, pacote_alvo, pacotes_disponiveis)
        clientes = [item["cliente"] for item in lista if int(item["cliente"].campanhas_ativo or 0) == 1]
        for cliente in clientes:
            db.add(CampanhaDestinatario(campanha_id=campanha.id, cliente_id=cliente.id, status="PENDENTE"))
        total = len(clientes)

    db.flush()
    if total:
        _precalcular_mensagens_campanha(db, campanha, promo_snapshot)
        db.flush()
        _garantir_lotes_campanha(db, campanha)
    db.commit()
    return total


def _quantidade_nao_enviados_campanha(db: Session, campanha: Campanha) -> int:
    """Conta, apenas com dados locais, quem ainda pode formar lote complementar."""
    if not campanha:
        return 0
    lista_tipo = (campanha.lista_tipo or "ATUALIZACAO").upper()
    if lista_tipo == "ALUGUEL":
        pessoas = [c for c in _clientes_lista_aluguel(db, mes=campanha.aluguel_mes) if _contato_elegivel_aluguel(c)]
        modelo = CampanhaAluguelDestinatario
        pessoa_id_attr = "contato_id"
    else:
        pacote_alvo = campanha.pacote_alvo or obter_pacote_atual(db)
        pessoas = [
            item["cliente"] for item in _clientes_lista_atualizacao(db, pacote_alvo)
            if int(item["cliente"].campanhas_ativo or 0) == 1
        ]
        modelo = CampanhaDestinatario
        pessoa_id_attr = "cliente_id"
    destinos = db.query(modelo).filter(modelo.campanha_id == campanha.id).all()
    por_pessoa = {int(getattr(dest, pessoa_id_attr)): dest for dest in destinos}
    lotes_abertos = {
        int(l.numero) for l in db.query(CampanhaLote).filter(
            CampanhaLote.campanha_id == campanha.id,
            CampanhaLote.concluido_em.is_(None),
        ).all()
    }
    total = 0
    for pessoa in pessoas:
        dest = por_pessoa.get(int(pessoa.id))
        if not dest:
            total += 1
            continue
        status = (dest.status or "").upper()
        if status in CAMPANHA_ENVIO_CONFIRMADO or status in {"EM_ENVIO", "IGNORADO"}:
            continue
        # Se já está PENDENTE em um lote aberto, ele já possui caminho de envio
        # e não deve ser movido repetidamente para novos lotes complementares.
        if status == "PENDENTE" and int(dest.lote_numero or 0) in lotes_abertos:
            continue
        total += 1
    return total


def _criar_lote_nao_enviados(db: Session, campanha: Campanha) -> tuple[int, list[int]]:
    """Cria lote(s) complementar(es) com elegíveis ainda não enviados.

    É uma regra geral de campanhas. Atualização e Aluguel reaproveitam a mesma
    estrutura de destinatários/lotes; somente a origem da lista muda.
    """
    if not campanha:
        return 0, []

    lista_tipo = (campanha.lista_tipo or "ATUALIZACAO").upper()
    promo_snapshot = None
    pacotes_disponiveis = None
    if lista_tipo == "ALUGUEL":
        pessoas = [
            c for c in _clientes_lista_aluguel(db, mes=campanha.aluguel_mes)
            if _contato_elegivel_aluguel(c)
        ]
        modelo = CampanhaAluguelDestinatario
        pessoa_id_attr = "contato_id"
    else:
        pacote_alvo = campanha.pacote_alvo or obter_pacote_atual(db)
        promo_snapshot = _solvoz_atualizacao_promocao_config()
        pacotes_disponiveis = [
            str(x).strip() for x in (promo_snapshot.get("pacotes_disponiveis") or []) if str(x).strip()
        ]
        pessoas = [
            item["cliente"] for item in _clientes_lista_atualizacao(db, pacote_alvo, pacotes_disponiveis)
            if int(item["cliente"].campanhas_ativo or 0) == 1
        ]
        modelo = CampanhaDestinatario
        pessoa_id_attr = "cliente_id"

    destinos = db.query(modelo).filter(modelo.campanha_id == campanha.id).all()
    por_pessoa = {int(getattr(dest, pessoa_id_attr)): dest for dest in destinos}
    lotes_abertos = {
        int(l.numero) for l in db.query(CampanhaLote).filter(
            CampanhaLote.campanha_id == campanha.id,
            CampanhaLote.concluido_em.is_(None),
        ).all()
    }
    candidatos = []
    for pessoa in pessoas:
        dest = por_pessoa.get(int(pessoa.id))
        status_atual = (dest.status or "").upper() if dest else ""
        if dest and status_atual in CAMPANHA_ENVIO_CONFIRMADO:
            continue
        if dest and status_atual in {"EM_ENVIO", "IGNORADO"}:
            continue
        if dest and status_atual == "PENDENTE" and int(dest.lote_numero or 0) in lotes_abertos:
            continue
        if not dest:
            kwargs = {"campanha_id": campanha.id, pessoa_id_attr: pessoa.id, "status": "PENDENTE"}
            dest = modelo(**kwargs)
            db.add(dest)
            por_pessoa[int(pessoa.id)] = dest
        else:
            dest.status = "PENDENTE"
            dest.reservado_por_id = None
            dest.reservado_em = None
            dest.enviado_por_id = None
            dest.enviado_em = None
        _preparar_snapshot_destinatario(
            campanha, dest, pessoa,
            promo_snapshot=promo_snapshot, pacotes_disponiveis=pacotes_disponiveis,
        )
        candidatos.append(dest)

    if not candidatos:
        db.commit()
        return 0, []

    max_lote = int(db.query(func.max(CampanhaLote.numero)).filter(CampanhaLote.campanha_id == campanha.id).scalar() or 0)
    numeros = []
    for idx, dest in enumerate(candidatos):
        numero = max_lote + 1 + (idx // CAMPANHA_LOTE_TAMANHO)
        dest.lote_numero = numero
        if numero not in numeros:
            numeros.append(numero)
    db.flush()
    _garantir_lotes_campanha(db, campanha)
    for numero in numeros:
        lote = db.query(CampanhaLote).filter(
            CampanhaLote.campanha_id == campanha.id, CampanhaLote.numero == numero
        ).first()
        if lote:
            lote.reservado_por_id = None
            lote.reservado_em = None
            lote.concluido_em = None
    campanha.status = "ATIVA"
    campanha.iniciado_em = campanha.iniciado_em or datetime.now()
    campanha.finalizado_em = None
    db.commit()
    return len(candidatos), numeros


@app.post("/organiza/campanhas/{campanha_id}/lote-nao-enviados")
def campanha_criar_lote_nao_enviados(campanha_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    campanha = db.query(Campanha).filter(Campanha.id == campanha_id).first()
    if not campanha:
        raise HTTPException(404)
    total, numeros = _criar_lote_nao_enviados(db, campanha)
    if not total:
        return RedirectResponse(f"/organiza/campanhas/{campanha.id}?recuperacao=nenhum", status_code=303)
    lotes_txt = ",".join(str(n) for n in numeros)
    return RedirectResponse(f"/organiza/campanhas/{campanha.id}?recuperacao={total}&lotes_recuperacao={lotes_txt}", status_code=303)


def _reservar_lote_escolhido(db: Session, campanha: Campanha, usuario: Usuario, lote_numero: int) -> CampanhaLote | None:
    """Reserva somente o lote explicitamente escolhido pelo atendente."""
    _liberar_lotes_expirados(db, campanha)
    lote = db.query(CampanhaLote).filter(
        CampanhaLote.campanha_id == campanha.id,
        CampanhaLote.numero == int(lote_numero),
        CampanhaLote.concluido_em.is_(None),
    ).first()
    if not lote:
        return None

    # Um atendente trabalha em um lote por vez nesta campanha.
    outros = db.query(CampanhaLote).filter(
        CampanhaLote.campanha_id == campanha.id,
        CampanhaLote.reservado_por_id == usuario.id,
        CampanhaLote.concluido_em.is_(None),
        CampanhaLote.numero != int(lote_numero),
    ).all()
    for outro in outros:
        if _lote_pendentes(db, campanha, outro.numero) > 0:
            return None

    agora = datetime.now()
    if int(lote.reservado_por_id or 0) != int(usuario.id):
        alterados = db.query(CampanhaLote).filter(
            CampanhaLote.id == lote.id,
            CampanhaLote.reservado_por_id.is_(None),
            CampanhaLote.concluido_em.is_(None),
        ).update({
            CampanhaLote.reservado_por_id: usuario.id,
            CampanhaLote.reservado_em: agora,
        }, synchronize_session=False)
        db.commit()
        if not alterados:
            return None
        lote = db.query(CampanhaLote).filter(CampanhaLote.id == lote.id).first()
    else:
        lote.reservado_em = agora
        db.commit()

    modelo = _modelo_destinatario_campanha(campanha)
    db.query(modelo).filter(
        modelo.campanha_id == campanha.id,
        modelo.lote_numero == int(lote_numero),
        modelo.status == "PENDENTE",
    ).update({
        modelo.status: "EM_ENVIO",
        modelo.reservado_por_id: usuario.id,
        modelo.reservado_em: agora,
    }, synchronize_session=False)
    db.commit()
    return lote


def _reservar_proximo_atualizacao(db: Session, campanha: Campanha, usuario: Usuario) -> CampanhaDestinatario | None:
    """Reserva somente linhas já preparadas; nenhuma consulta externa ocorre aqui."""
    limite = datetime.now() - timedelta(minutes=30)
    db.query(CampanhaDestinatario).filter(
        CampanhaDestinatario.campanha_id == campanha.id,
        CampanhaDestinatario.status == "EM_ENVIO",
        CampanhaDestinatario.reservado_em.isnot(None),
        CampanhaDestinatario.reservado_em < limite,
    ).update({
        CampanhaDestinatario.status: "PENDENTE",
        CampanhaDestinatario.reservado_por_id: None,
        CampanhaDestinatario.reservado_em: None,
    }, synchronize_session=False)
    db.commit()

    atual = db.query(CampanhaDestinatario).options(
        selectinload(CampanhaDestinatario.cliente)
    ).filter(
        CampanhaDestinatario.campanha_id == campanha.id,
        CampanhaDestinatario.status == "EM_ENVIO",
        CampanhaDestinatario.reservado_por_id == usuario.id,
    ).order_by(CampanhaDestinatario.id.asc()).first()
    if atual:
        return atual

    while True:
        candidato_id = db.query(CampanhaDestinatario.id).filter(
            CampanhaDestinatario.campanha_id == campanha.id,
            CampanhaDestinatario.status == "PENDENTE",
            CampanhaDestinatario.mensagem_pronta.isnot(None),
        ).order_by(CampanhaDestinatario.id.asc()).limit(1).scalar()
        if not candidato_id:
            return None
        agora = datetime.now()
        alterados = db.query(CampanhaDestinatario).filter(
            CampanhaDestinatario.id == candidato_id,
            CampanhaDestinatario.status == "PENDENTE",
        ).update({
            CampanhaDestinatario.status: "EM_ENVIO",
            CampanhaDestinatario.reservado_por_id: usuario.id,
            CampanhaDestinatario.reservado_em: agora,
        }, synchronize_session=False)
        db.commit()
        if not alterados:
            continue
        return db.query(CampanhaDestinatario).options(
            selectinload(CampanhaDestinatario.cliente)
        ).filter(CampanhaDestinatario.id == candidato_id).first()


def _reservar_proximo_aluguel(db: Session, campanha: Campanha, usuario: Usuario) -> CampanhaAluguelDestinatario | None:
    limite = datetime.now() - timedelta(minutes=30)
    db.query(CampanhaAluguelDestinatario).filter(
        CampanhaAluguelDestinatario.campanha_id == campanha.id,
        CampanhaAluguelDestinatario.status == "EM_ENVIO",
        CampanhaAluguelDestinatario.reservado_em.isnot(None),
        CampanhaAluguelDestinatario.reservado_em < limite,
    ).update({
        CampanhaAluguelDestinatario.status: "PENDENTE",
        CampanhaAluguelDestinatario.reservado_por_id: None,
        CampanhaAluguelDestinatario.reservado_em: None,
    }, synchronize_session=False)
    db.commit()
    atual = db.query(CampanhaAluguelDestinatario).options(
        selectinload(CampanhaAluguelDestinatario.contato)
    ).filter(
        CampanhaAluguelDestinatario.campanha_id == campanha.id,
        CampanhaAluguelDestinatario.status == "EM_ENVIO",
        CampanhaAluguelDestinatario.reservado_por_id == usuario.id,
    ).order_by(CampanhaAluguelDestinatario.id.asc()).first()
    if atual:
        if _contato_elegivel_aluguel(atual.contato):
            return atual
        atual.status = "IGNORADO"
        db.commit()

    while True:
        candidato_id = db.query(CampanhaAluguelDestinatario.id).filter(
            CampanhaAluguelDestinatario.campanha_id == campanha.id,
            CampanhaAluguelDestinatario.status == "PENDENTE",
        ).order_by(CampanhaAluguelDestinatario.id.asc()).limit(1).scalar()
        if not candidato_id:
            return None
        agora = datetime.now()
        alterados = db.query(CampanhaAluguelDestinatario).filter(
            CampanhaAluguelDestinatario.id == candidato_id,
            CampanhaAluguelDestinatario.status == "PENDENTE",
        ).update({
            CampanhaAluguelDestinatario.status: "EM_ENVIO",
            CampanhaAluguelDestinatario.reservado_por_id: usuario.id,
            CampanhaAluguelDestinatario.reservado_em: agora,
        }, synchronize_session=False)
        db.commit()
        if not alterados:
            continue
        destinatario = db.query(CampanhaAluguelDestinatario).options(
            selectinload(CampanhaAluguelDestinatario.contato)
        ).filter(CampanhaAluguelDestinatario.id == candidato_id).first()
        if destinatario and _contato_elegivel_aluguel(destinatario.contato):
            return destinatario
        if destinatario:
            destinatario.status = "IGNORADO"
            db.commit()


def _reservar_proximo_campanha(db: Session, campanha: Campanha, usuario: Usuario):
    if (campanha.lista_tipo or "").upper() == "ALUGUEL":
        return _reservar_proximo_aluguel(db, campanha, usuario)
    return _reservar_proximo_atualizacao(db, campanha, usuario)


def _normalizar_telefone_csv_aluguel(valor: str) -> tuple[str, str, str, str] | None:
    texto = (valor or "").strip()
    texto = re.sub(r"(?:,00|\.0+)$", "", texto)
    digitos = re.sub(r"\D", "", texto)
    if digitos.startswith("55") and len(digitos) in (12, 13):
        pais, ddi, telefone = normalizar_contato("BR", "55", digitos)
    elif len(digitos) in (10, 11):
        pais, ddi, telefone = normalizar_contato("BR", "55", digitos)
    else:
        return None
    if not telefone_internacional_valido(pais, ddi, telefone):
        return None
    return pais, ddi, telefone, f"{ddi}{telefone}"


def _ler_csv_aluguel(conteudo: bytes) -> list[dict]:
    texto = None
    for codificacao in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            texto = conteudo.decode(codificacao)
            break
        except UnicodeDecodeError:
            continue
    if texto is None:
        raise ValueError("Não foi possível ler a codificação do CSV.")
    amostra = texto[:4096]
    try:
        dialeto = csv.Sniffer().sniff(amostra, delimiters=";,\t")
        leitor = csv.DictReader(io.StringIO(texto), dialect=dialeto)
    except csv.Error:
        leitor = csv.DictReader(io.StringIO(texto), delimiter=";")
    linhas = []
    for indice, linha in enumerate(leitor, start=2):
        mapa = {str(k or "").strip().upper(): (v or "").strip() for k, v in linha.items()}
        nome = mapa.get("NOME", "").strip()
        telefone_bruto = mapa.get("WHATTSAPP") or mapa.get("WHATSAPP") or mapa.get("TELEFONE") or ""
        ultimo_mes = mapa.get("ULTIMO_MES_ALUGUEL") or mapa.get("ULTIMO MES ALUGUEL") or mapa.get("MES_ALUGUEL") or ""
        ultimo_aluguel = mapa.get("ULTIMO_ALUGUEL") or mapa.get("ULTIMO ALUGUEL") or mapa.get("DATA_ALUGUEL") or ""
        if not nome and not telefone_bruto:
            continue
        linhas.append({
            "linha": indice, "nome": nome, "telefone_bruto": telefone_bruto,
            "ultimo_mes": ultimo_mes, "ultimo_aluguel": ultimo_aluguel,
        })
    return linhas

@app.get("/organiza/campanhas", response_class=HTMLResponse)
def campanhas_lista(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    campanhas = db.query(Campanha).order_by(Campanha.criado_em.desc(), Campanha.id.desc()).all()
    dados = [{"campanha": c, "contagens": _contagens_campanha(db, c.id)} for c in campanhas]
    atualizacao = _clientes_lista_atualizacao(db)
    aluguel = _clientes_lista_aluguel(db)
    return templates.TemplateResponse("organiza/campanhas.html", {
        "request": request, "usuario": usuario, "dados": dados,
        "total_lista_atualizacao": len(atualizacao),
        "total_lista_atualizacao_ativa": sum(1 for item in atualizacao if int(item["cliente"].campanhas_ativo or 0) == 1),
        "total_lista_aluguel": len(aluguel),
        "total_lista_aluguel_ativa": sum(1 for contato in aluguel if int(contato.campanhas_ativo or 0) == 1),
        "pacote_atual": obter_pacote_atual(db),
    })


@app.get("/organiza/campanhas/lista-atualizacao", response_class=HTMLResponse)
def campanha_lista_atualizacao(
    request: Request, q: str = "", status: str = "",
    usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db),
):
    pacote_atual = obter_pacote_atual(db)
    lista_total = _clientes_lista_atualizacao(db, pacote_atual)
    status = (status or "").strip().upper()
    if status not in {"ATIVO", "INATIVO"}:
        status = ""
    lista = _filtrar_lista_atualizacao(lista_total, q, status)
    return templates.TemplateResponse("organiza/lista_atualizacao.html", {
        "request": request, "usuario": usuario, "lista": lista,
        "pacote_atual": pacote_atual,
        "ativos": sum(1 for item in lista if int(item["cliente"].campanhas_ativo or 0) == 1),
        "total_geral": len(lista_total), "q_filtro": (q or "").strip(), "status_filtro": status,
    })


@app.post("/organiza/campanhas/lista-atualizacao/{cliente_id}/alternar")
def campanha_lista_atualizacao_alternar(
    cliente_id: int, q: str = Form(""), status: str = Form(""),
    usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db),
):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(404)
    cliente.campanhas_ativo = 0 if int(cliente.campanhas_ativo or 0) == 1 else 1
    db.commit()
    params = []
    if (q or "").strip():
        params.append("q=" + quote_plus((q or "").strip()))
    status = (status or "").strip().upper()
    if status in {"ATIVO", "INATIVO"}:
        params.append("status=" + status)
    sufixo = ("?" + "&".join(params)) if params else ""
    return RedirectResponse("/organiza/campanhas/lista-atualizacao" + sufixo, status_code=303)


@app.get("/organiza/campanhas/lista-aluguel", response_class=HTMLResponse)
def campanha_lista_aluguel(
    request: Request, mes: int = 0, integracao: str = "", q: str = "", status: str = "",
    usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db),
):
    mes = mes if 1 <= int(mes or 0) <= 12 else 0
    integracao = (integracao or "").strip().upper()
    if integracao not in {"PLANILHA", "CONNECT"}:
        integracao = ""
    status = (status or "").strip().upper()
    if status not in {"ATIVO", "INATIVO"}:
        status = ""
    lista_total = _clientes_lista_aluguel(db, mes=mes or None, integracao=integracao)
    lista = _filtrar_lista_aluguel(lista_total, q, status)
    return templates.TemplateResponse("organiza/lista_aluguel.html", {
        "request": request, "usuario": usuario, "lista": lista,
        "ativos": sum(1 for contato in lista if int(contato.campanhas_ativo or 0) == 1),
        "importados": request.query_params.get("importados", ""),
        "atualizados": request.query_params.get("atualizados", ""),
        "ignorados": request.query_params.get("ignorados", ""),
        "erro": request.query_params.get("erro", ""),
        "connect_novos": request.query_params.get("connect_novos", ""),
        "connect_atualizados": request.query_params.get("connect_atualizados", ""),
        "connect_ignorados": request.query_params.get("connect_ignorados", ""),
        "connect_erro": request.query_params.get("connect_erro", ""),
        "mes_filtro": mes, "integracao_filtro": integracao, "meses_aluguel": MESES_ALUGUEL,
        "q_filtro": (q or "").strip(), "status_filtro": status, "total_geral": len(lista_total),
    })


@app.post("/organiza/campanhas/lista-aluguel/importar")
def campanha_lista_aluguel_importar(
    arquivo: UploadFile = File(...),
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    nome_arquivo = (arquivo.filename or "").lower()
    if nome_arquivo and not nome_arquivo.endswith(".csv"):
        return RedirectResponse("/organiza/campanhas/lista-aluguel?erro=arquivo", status_code=303)
    try:
        linhas = _ler_csv_aluguel(arquivo.file.read())
    except Exception:
        return RedirectResponse("/organiza/campanhas/lista-aluguel?erro=leitura", status_code=303)

    importados = 0
    atualizados = 0
    ignorados = 0
    vistos = set()
    for item in linhas:
        nome = _nome_aluguel_normalizado(item.get("nome") or "")
        normalizado = _normalizar_telefone_csv_aluguel(item.get("telefone_bruto") or "")
        if not nome or not normalizado:
            ignorados += 1
            continue
        pais, ddi, telefone, numero_chave = normalizado
        ultimo_mes = _mes_aluguel_numero(item.get("ultimo_mes"))
        ultimo_aluguel_em = None
        bruto_data = (item.get("ultimo_aluguel") or "").strip()
        if bruto_data:
            for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
                try:
                    ultimo_aluguel_em = datetime.strptime(bruto_data[:10], formato).date()
                    ultimo_mes = ultimo_aluguel_em.month
                    break
                except ValueError:
                    continue
        if numero_chave in vistos:
            ignorados += 1
            continue
        vistos.add(numero_chave)
        existente = db.query(CampanhaAluguelContato).filter(CampanhaAluguelContato.numero_chave == numero_chave).first()
        if existente:
            # CONNECT é a fonte atual: uma reimportação da planilha nunca regride seus dados.
            if (existente.integracao or "PLANILHA").upper() == "CONNECT":
                ignorados += 1
                continue
            mudou = False
            for campo, valor in (("nome", nome), ("pais", pais), ("ddi", ddi), ("telefone", telefone)):
                if getattr(existente, campo) != valor:
                    setattr(existente, campo, valor)
                    mudou = True
            if ultimo_mes and existente.ultimo_mes_aluguel != ultimo_mes:
                existente.ultimo_mes_aluguel = ultimo_mes
                mudou = True
            if ultimo_aluguel_em and existente.ultimo_aluguel_em != ultimo_aluguel_em:
                existente.ultimo_aluguel_em = ultimo_aluguel_em
                mudou = True
            existente.origem = "PLANILHA"
            existente.integracao = "PLANILHA"
            if mudou:
                atualizados += 1
            continue
        db.add(CampanhaAluguelContato(
            nome=nome, pais=pais, ddi=ddi, telefone=telefone,
            numero_chave=numero_chave, campanhas_ativo=1, origem="PLANILHA", integracao="PLANILHA",
            ultimo_mes_aluguel=ultimo_mes, ultimo_aluguel_em=ultimo_aluguel_em,
        ))
        importados += 1
    db.commit()
    return RedirectResponse(
        f"/organiza/campanhas/lista-aluguel?importados={importados}&atualizados={atualizados}&ignorados={ignorados}",
        status_code=303,
    )


def _connect_base_url() -> str:
    bruto = (os.getenv("CONNECT_API_URL") or "").strip().rstrip("/")
    if not bruto:
        return ""
    sufixos = (
        "/api/integracoes/organiza/lancamentos",
        "/api/integracoes/organiza/clientes-aluguel/snapshot",
    )
    for sufixo in sufixos:
        if bruto.endswith(sufixo):
            return bruto[:-len(sufixo)].rstrip("/")
    return bruto


def _buscar_snapshot_clientes_aluguel_connect() -> list[dict]:
    base = _connect_base_url()
    if not base:
        raise RuntimeError("CONNECT_API_URL não configurada.")
    chave = (os.getenv("CONNECT_API_KEY") or os.getenv("ORGANIZA_API_KEY") or "").strip()
    if not chave:
        raise RuntimeError("CONNECT_API_KEY/ORGANIZA_API_KEY não configurada.")
    url = base + "/api/integracoes/organiza/clientes-aluguel/snapshot"
    req = urllib.request.Request(
        url, data=b"{}", method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-Key": chave,
            "User-Agent": f"HUMIAT-Organiza/{ORGANIZA_VERSAO}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            corpo = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detalhe = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Connect respondeu HTTP {exc.code}: {detalhe[:500]}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Não foi possível acessar o Connect: {exc.reason}")
    try:
        dados = json.loads(corpo or "{}")
    except Exception:
        raise RuntimeError("Resposta inválida do Connect.")
    if str(dados.get("empresa_slug") or "").strip().lower() != "karaokerj":
        raise RuntimeError("O Connect não retornou a empresa Karaokê RJ.")
    clientes = dados.get("clientes")
    if not isinstance(clientes, list):
        raise RuntimeError("Resposta do Connect sem lista de clientes.")
    return clientes


def _aplicar_cliente_aluguel_connect(db: Session, dados: dict) -> tuple[CampanhaAluguelContato, bool]:
    slug = str(dados.get("empresa_slug") or "").strip().lower()
    if slug != "karaokerj":
        raise ValueError("Esta lista aceita somente contratos da Karaokê RJ.")

    nome = _nome_aluguel_normalizado(str(dados.get("nome") or ""))
    normalizado = _normalizar_telefone_csv_aluguel(str(dados.get("telefone") or ""))
    if not nome or not normalizado:
        raise ValueError("Nome e telefone válidos são obrigatórios.")
    pais, ddi, telefone, numero_chave = normalizado

    connect_cliente_id = None
    connect_solicitacao_id = None
    try:
        if dados.get("connect_cliente_id") not in (None, ""):
            connect_cliente_id = int(dados.get("connect_cliente_id"))
        if dados.get("connect_solicitacao_id") not in (None, ""):
            connect_solicitacao_id = int(dados.get("connect_solicitacao_id"))
    except (TypeError, ValueError):
        raise ValueError("IDs do Connect inválidos.")

    data_evento = None
    if dados.get("data_evento"):
        try:
            data_evento = date.fromisoformat(str(dados.get("data_evento"))[:10])
        except ValueError:
            raise ValueError("data_evento deve usar AAAA-MM-DD.")

    contato = None
    if connect_cliente_id:
        contato = db.query(CampanhaAluguelContato).filter(
            CampanhaAluguelContato.connect_cliente_id == connect_cliente_id
        ).first()
    if not contato:
        contato = db.query(CampanhaAluguelContato).filter(
            CampanhaAluguelContato.numero_chave == numero_chave
        ).first()

    criado = contato is None
    if criado:
        contato = CampanhaAluguelContato(
            nome=nome, pais=pais, ddi=ddi, telefone=telefone, numero_chave=numero_chave,
            campanhas_ativo=1, origem="CONNECT", integracao="CONNECT",
        )
        db.add(contato)
    else:
        contato.nome = nome
        contato.pais = pais
        contato.ddi = ddi
        contato.telefone = telefone
        contato.numero_chave = numero_chave

    contato.origem = "CONNECT"
    contato.integracao = "CONNECT"
    contato.connect_cliente_id = connect_cliente_id or contato.connect_cliente_id
    contato.connect_solicitacao_id = connect_solicitacao_id or contato.connect_solicitacao_id
    contato.ultima_sincronizacao_em = datetime.now()
    if data_evento:
        contato.ultimo_aluguel_em = data_evento
        contato.ultimo_mes_aluguel = data_evento.month
    return contato, criado


def _validar_chave_connect(request: Request):
    esperada = (os.getenv("CONNECT_API_KEY") or os.getenv("ORGANIZA_API_KEY") or "").strip()
    if not esperada:
        raise HTTPException(status_code=503, detail="Chave Connect → Organiza não configurada.")
    recebida = (request.headers.get("X-API-Key") or "").strip()
    if not recebida or not secrets.compare_digest(recebida, esperada):
        raise HTTPException(status_code=401, detail="Chave de integração inválida.")


@app.post("/api/integracoes/connect/clientes-aluguel")
async def integrar_cliente_aluguel_connect(request: Request, db: Session = Depends(get_db)):
    """Upsert da lista de aluguel. Exclusiva da Karaokê RJ; CONNECT prevalece."""
    _validar_chave_connect(request)
    try:
        dados = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido.")
    try:
        contato, criado = _aplicar_cliente_aluguel_connect(db, dados)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    db.refresh(contato)
    return {
        "ok": True, "acao": "criado" if criado else "atualizado", "id": contato.id,
        "integracao": contato.integracao, "ultimo_mes_aluguel": contato.ultimo_mes_aluguel,
    }


@app.post("/organiza/campanhas/lista-aluguel/atualizar-connect")
def campanha_lista_aluguel_atualizar_connect(
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Atualização manual em lote, sempre limitada à empresa Karaokê RJ."""
    try:
        clientes = _buscar_snapshot_clientes_aluguel_connect()
    except RuntimeError as exc:
        return RedirectResponse(
            "/organiza/campanhas/lista-aluguel?connect_erro=" + quote_plus(str(exc)),
            status_code=303,
        )

    novos = atualizados = ignorados = 0
    for dados in clientes:
        try:
            _, criado = _aplicar_cliente_aluguel_connect(db, dados)
            if criado:
                novos += 1
            else:
                atualizados += 1
        except ValueError:
            ignorados += 1
    db.commit()
    return RedirectResponse(
        f"/organiza/campanhas/lista-aluguel?connect_novos={novos}&connect_atualizados={atualizados}&connect_ignorados={ignorados}",
        status_code=303,
    )


@app.post("/organiza/campanhas/lista-aluguel/{contato_id}/alternar")
def campanha_lista_aluguel_alternar(
    contato_id: int, mes: int = Form(0), integracao: str = Form(""), q: str = Form(""), status: str = Form(""),
    usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db),
):
    contato = db.query(CampanhaAluguelContato).filter(CampanhaAluguelContato.id == contato_id).first()
    if not contato:
        raise HTTPException(404)
    contato.campanhas_ativo = 0 if int(contato.campanhas_ativo or 0) == 1 else 1
    db.commit()
    params = []
    mes = int(mes or 0) if str(mes or "0").isdigit() else 0
    if 1 <= mes <= 12:
        params.append(f"mes={mes}")
    integracao = (integracao or "").strip().upper()
    if integracao in {"PLANILHA", "CONNECT"}:
        params.append("integracao=" + integracao)
    if (q or "").strip():
        params.append("q=" + quote_plus((q or "").strip()))
    status = (status or "").strip().upper()
    if status in {"ATIVO", "INATIVO"}:
        params.append("status=" + status)
    sufixo = ("?" + "&".join(params)) if params else ""
    return RedirectResponse("/organiza/campanhas/lista-aluguel" + sufixo, status_code=303)


@app.get("/organiza/campanhas/nova", response_class=HTMLResponse)
def campanha_nova(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    atualizacao = _clientes_lista_atualizacao(db)
    aluguel = _clientes_lista_aluguel(db)
    return templates.TemplateResponse("organiza/campanha_form.html", {
        "request": request, "usuario": usuario, "erro": "",
        "pacote_atual": obter_pacote_atual(db),
        "total_atualizacao": sum(1 for item in atualizacao if int(item["cliente"].campanhas_ativo or 0) == 1),
        "total_aluguel": sum(1 for contato in aluguel if int(contato.campanhas_ativo or 0) == 1),
        "total_aluguel_por_mes": {m: sum(1 for contato in aluguel if int(contato.campanhas_ativo or 0) == 1 and contato.ultimo_mes_aluguel == m) for m in MESES_ALUGUEL},
        "meses_aluguel": MESES_ALUGUEL,
        "form_lista_tipo": "ATUALIZACAO", "form_aluguel_mes": 0,
        "form_link": "", "campanha": None, "modo_edicao": False,
    })


@app.post("/organiza/campanhas/nova")
def campanha_criar(
    request: Request,
    nome: str = Form(...),
    lista_tipo: str = Form("ATUALIZACAO"),
    aluguel_mes: int = Form(0),
    mensagem: str = Form(...),
    link: str = Form(""),
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    nome = (nome or "").strip()
    mensagem = (mensagem or "").strip()
    link = (link or "").strip()
    lista_tipo = (lista_tipo or "ATUALIZACAO").strip().upper()
    aluguel_mes = int(aluguel_mes or 0) if str(aluguel_mes or "0").isdigit() else 0
    aluguel_mes = aluguel_mes if 1 <= aluguel_mes <= 12 else 0
    erro = ""
    if not nome:
        erro = "Informe o nome da campanha."
    elif lista_tipo not in {"ATUALIZACAO", "ALUGUEL"}:
        erro = "Selecione uma lista válida."
    elif not mensagem:
        erro = "Informe a mensagem da campanha."
    elif len(link) > 1000:
        erro = "O link deve ter no máximo 1000 caracteres."

    if erro:
        atualizacao = _clientes_lista_atualizacao(db)
        aluguel = _clientes_lista_aluguel(db)
        return templates.TemplateResponse("organiza/campanha_form.html", {
            "request": request, "usuario": usuario, "erro": erro,
            "pacote_atual": obter_pacote_atual(db),
            "total_atualizacao": sum(1 for item in atualizacao if int(item["cliente"].campanhas_ativo or 0) == 1),
            "total_aluguel": sum(1 for contato in aluguel if int(contato.campanhas_ativo or 0) == 1),
            "total_aluguel_por_mes": {m: sum(1 for contato in aluguel if int(contato.campanhas_ativo or 0) == 1 and contato.ultimo_mes_aluguel == m) for m in MESES_ALUGUEL},
            "meses_aluguel": MESES_ALUGUEL,
            "form_nome": nome, "form_lista_tipo": lista_tipo, "form_aluguel_mes": aluguel_mes,
            "form_mensagem": mensagem, "form_link": link,
            "campanha": None, "modo_edicao": False,
        }, status_code=400)

    campanha = Campanha(
        nome=nome, lista_tipo=lista_tipo, mensagem=mensagem, link=(link or None) if lista_tipo == "ALUGUEL" else None,
        pacote_alvo=obter_pacote_atual(db) if lista_tipo == "ATUALIZACAO" else None,
        aluguel_mes=(aluguel_mes or None) if lista_tipo == "ALUGUEL" else None,
        status="RASCUNHO", criado_por_id=usuario.id,
    )
    db.add(campanha)
    db.commit()
    db.refresh(campanha)
    # 1.1.47: ao criar, já congela a lista inteira e cria lotes de até 100.
    total_preparado = _preparar_campanha_com_lotes(db, campanha)
    sufixo = "?erro=nenhum_cliente" if total_preparado == 0 else ""
    return RedirectResponse(f"/organiza/campanhas/{campanha.id}{sufixo}", status_code=303)


@app.get("/organiza/campanhas/{campanha_id}/editar", response_class=HTMLResponse)
def campanha_editar(
    campanha_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    campanha = db.query(Campanha).filter(Campanha.id == campanha_id).first()
    if not campanha:
        raise HTTPException(404)
    atualizacao = _clientes_lista_atualizacao(db, campanha.pacote_alvo or obter_pacote_atual(db))
    aluguel = _clientes_lista_aluguel(db)
    return templates.TemplateResponse("organiza/campanha_form.html", {
        "request": request, "usuario": usuario, "erro": "",
        "pacote_atual": campanha.pacote_alvo or obter_pacote_atual(db),
        "total_atualizacao": sum(1 for item in atualizacao if int(item["cliente"].campanhas_ativo or 0) == 1),
        "total_aluguel": sum(1 for contato in aluguel if int(contato.campanhas_ativo or 0) == 1),
        "total_aluguel_por_mes": {m: sum(1 for contato in aluguel if int(contato.campanhas_ativo or 0) == 1 and contato.ultimo_mes_aluguel == m) for m in MESES_ALUGUEL},
        "meses_aluguel": MESES_ALUGUEL,
        "form_nome": campanha.nome, "form_lista_tipo": campanha.lista_tipo, "form_aluguel_mes": campanha.aluguel_mes or 0,
        "form_mensagem": campanha.mensagem, "form_link": campanha.link or "",
        "campanha": campanha, "modo_edicao": True,
    })


@app.post("/organiza/campanhas/{campanha_id}/editar")
def campanha_salvar_edicao(
    campanha_id: int,
    request: Request,
    nome: str = Form(...),
    lista_tipo: str = Form("ATUALIZACAO"),
    aluguel_mes: int = Form(0),
    mensagem: str = Form(...),
    link: str = Form(""),
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    campanha = db.query(Campanha).filter(Campanha.id == campanha_id).first()
    if not campanha:
        raise HTTPException(404)

    nome = (nome or "").strip()
    mensagem = (mensagem or "").strip()
    link = (link or "").strip()
    lista_tipo = (lista_tipo or campanha.lista_tipo or "ATUALIZACAO").strip().upper()
    aluguel_mes = int(aluguel_mes or 0) if str(aluguel_mes or "0").isdigit() else 0
    aluguel_mes = aluguel_mes if 1 <= aluguel_mes <= 12 else 0
    erro = ""
    if not nome:
        erro = "Informe o nome da campanha."
    elif lista_tipo not in {"ATUALIZACAO", "ALUGUEL"}:
        erro = "Selecione uma lista válida."
    elif campanha.status != "RASCUNHO" and lista_tipo != (campanha.lista_tipo or "ATUALIZACAO").upper():
        erro = "A lista não pode ser alterada depois que a campanha foi iniciada."
    elif campanha.status != "RASCUNHO" and lista_tipo == "ALUGUEL" and aluguel_mes != int(campanha.aluguel_mes or 0):
        erro = "O mês da lista não pode ser alterado depois que a campanha foi iniciada."
    elif not mensagem:
        erro = "Informe a mensagem da campanha."
    elif len(link) > 1000:
        erro = "O link deve ter no máximo 1000 caracteres."

    if erro:
        atualizacao = _clientes_lista_atualizacao(db, campanha.pacote_alvo or obter_pacote_atual(db))
        aluguel = _clientes_lista_aluguel(db)
        return templates.TemplateResponse("organiza/campanha_form.html", {
            "request": request, "usuario": usuario, "erro": erro,
            "pacote_atual": campanha.pacote_alvo or obter_pacote_atual(db),
            "total_atualizacao": sum(1 for item in atualizacao if int(item["cliente"].campanhas_ativo or 0) == 1),
            "total_aluguel": sum(1 for contato in aluguel if int(contato.campanhas_ativo or 0) == 1),
            "total_aluguel_por_mes": {m: sum(1 for contato in aluguel if int(contato.campanhas_ativo or 0) == 1 and contato.ultimo_mes_aluguel == m) for m in MESES_ALUGUEL},
            "meses_aluguel": MESES_ALUGUEL,
            "form_nome": nome, "form_lista_tipo": lista_tipo, "form_aluguel_mes": aluguel_mes,
            "form_mensagem": mensagem, "form_link": link,
            "campanha": campanha, "modo_edicao": True,
        }, status_code=400)

    campanha.nome = nome
    campanha.lista_tipo = lista_tipo
    campanha.pacote_alvo = obter_pacote_atual(db) if lista_tipo == "ATUALIZACAO" else None
    campanha.aluguel_mes = (aluguel_mes or None) if lista_tipo == "ALUGUEL" else None
    campanha.mensagem = mensagem
    campanha.link = (link or None) if lista_tipo == "ALUGUEL" else None
    era_rascunho = campanha.status == "RASCUNHO"
    db.commit()
    if era_rascunho:
        total_preparado = _preparar_campanha_com_lotes(db, campanha)
        sufixo = "?erro=nenhum_cliente" if total_preparado == 0 else ""
        return RedirectResponse(f"/organiza/campanhas/{campanha.id}{sufixo}", status_code=303)
    return RedirectResponse(f"/organiza/campanhas/{campanha.id}", status_code=303)


@app.post("/organiza/campanhas/{campanha_id}/iniciar")
def campanha_iniciar(
    campanha_id: int,
    lote_numero: int = Form(...),
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    campanha = db.query(Campanha).filter(Campanha.id == campanha_id).first()
    if not campanha:
        raise HTTPException(404)
    if campanha.status not in {"RASCUNHO", "ATIVA"}:
        return RedirectResponse(f"/organiza/campanhas/{campanha.id}", status_code=303)

    modelo = _modelo_destinatario_campanha(campanha)
    total = int(db.query(func.count(modelo.id)).filter(modelo.campanha_id == campanha.id).scalar() or 0)
    if total == 0 and campanha.status == "RASCUNHO":
        total = _preparar_campanha_com_lotes(db, campanha)
    if total == 0:
        return RedirectResponse(f"/organiza/campanhas/{campanha.id}?erro=nenhum_cliente", status_code=303)

    lote = _reservar_lote_escolhido(db, campanha, usuario, int(lote_numero))
    if not lote:
        return RedirectResponse(f"/organiza/campanhas/{campanha.id}?erro=lote_ocupado", status_code=303)

    if campanha.status == "RASCUNHO":
        campanha.status = "ATIVA"
        campanha.iniciado_em = campanha.iniciado_em or datetime.now()
        db.commit()
    return RedirectResponse(f"/organiza/campanhas/{campanha.id}/proximo?lote={lote.numero}", status_code=303)


@app.get("/organiza/campanhas/{campanha_id}", response_class=HTMLResponse)
def campanha_detalhe(campanha_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    campanha = db.query(Campanha).filter(Campanha.id == campanha_id).first()
    if not campanha:
        raise HTTPException(404)

    # Compatibilidade: rascunhos criados antes da 1.1.47 são preparados uma vez.
    if campanha.status == "RASCUNHO":
        modelo = _modelo_destinatario_campanha(campanha)
        qtd = int(db.query(func.count(modelo.id)).filter(modelo.campanha_id == campanha.id).scalar() or 0)
        if qtd == 0:
            try:
                _preparar_campanha_com_lotes(db, campanha)
            except Exception:
                db.rollback()
    if campanha.status == "ATIVA":
        _liberar_lotes_expirados(db, campanha)

    contagens = _contagens_campanha(db, campanha.id)
    total_previsto = contagens["total"]
    lotes = _resumo_lotes_campanha(db, campanha) if total_previsto else []
    nao_enviados_disponiveis = _quantidade_nao_enviados_campanha(db, campanha) if campanha.status != "RASCUNHO" else 0
    return templates.TemplateResponse("organiza/campanha_detalhe.html", {
        "request": request, "usuario": usuario, "campanha": campanha,
        "rotulo_lista": _rotulo_lista_campanha(campanha),
        "contagens": contagens,
        "total_previsto": total_previsto, "meses_aluguel": MESES_ALUGUEL,
        "lotes": lotes,
        "tamanho_lote": CAMPANHA_LOTE_TAMANHO,
        "nao_enviados_disponiveis": nao_enviados_disponiveis,
        "erro": request.query_params.get("erro", ""),
        "recuperacao": request.query_params.get("recuperacao", ""),
        "lotes_recuperacao": request.query_params.get("lotes_recuperacao", ""),
    })


@app.get("/organiza/campanhas/{campanha_id}/proximo", response_class=HTMLResponse)
def campanha_proximo(
    campanha_id: int,
    request: Request,
    lote: int = 0,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    campanha = db.query(Campanha).filter(Campanha.id == campanha_id).first()
    if not campanha:
        raise HTTPException(404)
    if campanha.status != "ATIVA":
        return RedirectResponse(f"/organiza/campanhas/{campanha.id}", status_code=303)
    if int(lote or 0) <= 0:
        return RedirectResponse(f"/organiza/campanhas/{campanha.id}?erro=selecione_lote", status_code=303)

    lote_obj = db.query(CampanhaLote).filter(
        CampanhaLote.campanha_id == campanha.id,
        CampanhaLote.numero == int(lote),
        CampanhaLote.reservado_por_id == usuario.id,
        CampanhaLote.concluido_em.is_(None),
    ).first()
    if not lote_obj:
        return RedirectResponse(f"/organiza/campanhas/{campanha.id}?erro=lote_ocupado", status_code=303)

    fila = _fila_lote_usuario(db, campanha, usuario, lote_obj)
    contagens = _contagens_campanha(db, campanha.id)
    if not fila:
        lote_obj.concluido_em = lote_obj.concluido_em or datetime.now()
        db.commit()
        if contagens["pendentes"] == 0:
            campanha.status = "FINALIZADA"
            campanha.finalizado_em = datetime.now()
            db.commit()
        return RedirectResponse(f"/organiza/campanhas/{campanha.id}", status_code=303)

    return templates.TemplateResponse("organiza/campanha_envio.html", {
        "request": request, "usuario": usuario, "campanha": campanha,
        "fila": fila, "contagens": contagens, "lote": lote_obj,
        "total_lotes": len(_garantir_lotes_campanha(db, campanha)),
        "tamanho_lote": CAMPANHA_LOTE_TAMANHO,
    })


def _fechar_lote_se_concluido(db: Session, campanha: Campanha, lote_numero: int) -> None:
    if not lote_numero or _lote_pendentes(db, campanha, lote_numero) > 0:
        return
    lote = db.query(CampanhaLote).filter(
        CampanhaLote.campanha_id == campanha.id,
        CampanhaLote.numero == int(lote_numero),
    ).first()
    if lote and not lote.concluido_em:
        lote.concluido_em = datetime.now()
    if _contagens_campanha(db, campanha.id)["pendentes"] == 0:
        campanha.status = "FINALIZADA"
        campanha.finalizado_em = campanha.finalizado_em or datetime.now()
    db.commit()


@app.post("/organiza/campanhas/{campanha_id}/destinatarios/{destinatario_id}/whatsapp-proximo")
def campanha_whatsapp_proximo(campanha_id: int, destinatario_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    """Marca como processado com uma gravação local mínima.

    Não monta mensagem, não consulta SolVoz, não abre página-ponte e não
    espera qualquer retorno do WhatsApp.
    """
    campanha = db.query(Campanha).filter(Campanha.id == campanha_id).first()
    if not campanha:
        raise HTTPException(404)
    modelo = _modelo_destinatario_campanha(campanha)
    destinatario = db.query(modelo).filter(
        modelo.id == destinatario_id,
        modelo.campanha_id == campanha_id,
        modelo.status == "EM_ENVIO",
        modelo.reservado_por_id == usuario.id,
    ).first()
    if not destinatario:
        return Response(status_code=204)
    destinatario.status = "PROCESSADO"
    destinatario.enviado_por_id = usuario.id
    destinatario.enviado_em = datetime.now()
    # Atualiza apenas o cadastro LOCAL usando o snapshot já salvo.
    if (campanha.lista_tipo or "").upper() == "ATUALIZACAO" and getattr(destinatario, "cliente", None):
        cliente = destinatario.cliente
        cliente.atualizacao_oferta_status = "OFERTA_ENVIADA"
        cliente.atualizacao_oferta_periodo = None
        try:
            pacotes = json.loads(destinatario.pacotes_prontos or "[]")
        except Exception:
            pacotes = []
        if pacotes:
            cliente.atualizacao_oferta_periodo = pacotes[0] if len(pacotes) == 1 else f"{pacotes[0]} a {pacotes[-1]}"
            cliente.atualizacao_oferta_pacotes = json.dumps(pacotes, ensure_ascii=False)
        cliente.atualizacao_oferta_valor_normal_centavos = destinatario.valor_normal_centavos
        cliente.atualizacao_oferta_valor_promocional_centavos = destinatario.valor_promocional_centavos
        cliente.atualizacao_oferta_atualizado_em = datetime.now()
    lote_numero = int(getattr(destinatario, "lote_numero", 0) or 0)
    if lote_numero:
        db.query(CampanhaLote).filter(
            CampanhaLote.campanha_id == campanha.id,
            CampanhaLote.numero == lote_numero,
            CampanhaLote.reservado_por_id == usuario.id,
        ).update({CampanhaLote.reservado_em: datetime.now()}, synchronize_session=False)
    db.commit()
    _fechar_lote_se_concluido(db, campanha, lote_numero)
    return Response(status_code=204)


@app.post("/organiza/campanhas/{campanha_id}/destinatarios/{destinatario_id}/manual-enviado")
async def campanha_manual_marcar_enviado(
    campanha_id: int,
    destinatario_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Confirma um envio/reenvio feito manualmente a partir da consulta em Vendas."""
    campanha = db.query(Campanha).filter(
        Campanha.id == campanha_id,
        func.upper(Campanha.lista_tipo) == "ATUALIZACAO",
    ).first()
    if not campanha:
        raise HTTPException(404)
    destinatario = db.query(CampanhaDestinatario).options(
        selectinload(CampanhaDestinatario.cliente)
    ).filter(
        CampanhaDestinatario.id == destinatario_id,
        CampanhaDestinatario.campanha_id == campanha_id,
    ).first()
    if not destinatario or not destinatario.cliente:
        raise HTTPException(404)

    destinatario.status = "ENVIADO"
    destinatario.enviado_por_id = usuario.id
    destinatario.enviado_em = datetime.now()
    destinatario.reservado_por_id = None
    destinatario.reservado_em = None

    cliente = destinatario.cliente
    cliente.atualizacao_oferta_status = "OFERTA_ENVIADA"
    cliente.atualizacao_oferta_periodo = None
    try:
        pacotes = json.loads(destinatario.pacotes_prontos or "[]")
    except Exception:
        pacotes = []
    if pacotes:
        cliente.atualizacao_oferta_periodo = pacotes[0] if len(pacotes) == 1 else f"{pacotes[0]} a {pacotes[-1]}"
        cliente.atualizacao_oferta_pacotes = json.dumps(pacotes, ensure_ascii=False)
    cliente.atualizacao_oferta_valor_normal_centavos = destinatario.valor_normal_centavos
    cliente.atualizacao_oferta_valor_promocional_centavos = destinatario.valor_promocional_centavos
    cliente.atualizacao_oferta_atualizado_em = datetime.now()

    lote_numero = int(destinatario.lote_numero or 0)
    db.commit()
    _fechar_lote_se_concluido(db, campanha, lote_numero)

    form = dict(await request.form())
    venda_id = int(form.get("venda_id") or 0)
    retorno = (form.get("retorno") or "/organiza/vendas").strip()
    if not retorno.startswith("/organiza/vendas"):
        retorno = "/organiza/vendas"
    consulta_url = (
        f"/organiza/clientes/{cliente.id}?consulta=1&venda_id={venda_id}"
        f"&campanha_id={campanha.id}&manual_sucesso=1&retorno={quote_plus(retorno)}"
    )
    return RedirectResponse(consulta_url, status_code=303)


@app.post("/organiza/campanhas/{campanha_id}/destinatarios/{destinatario_id}/pular-rapido")
def campanha_pular_rapido(campanha_id: int, destinatario_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    campanha = db.query(Campanha).filter(Campanha.id == campanha_id).first()
    if not campanha:
        raise HTTPException(404)
    modelo = _modelo_destinatario_campanha(campanha)
    destinatario = db.query(modelo).filter(
        modelo.id == destinatario_id, modelo.campanha_id == campanha_id,
        modelo.status == "EM_ENVIO", modelo.reservado_por_id == usuario.id,
    ).first()
    if destinatario:
        destinatario.status = "IGNORADO"
        lote_numero = int(getattr(destinatario, "lote_numero", 0) or 0)
        if lote_numero:
            db.query(CampanhaLote).filter(
                CampanhaLote.campanha_id == campanha.id, CampanhaLote.numero == lote_numero,
                CampanhaLote.reservado_por_id == usuario.id,
            ).update({CampanhaLote.reservado_em: datetime.now()}, synchronize_session=False)
        db.commit()
        _fechar_lote_se_concluido(db, campanha, lote_numero)
    return Response(status_code=204)


@app.post("/organiza/campanhas/{campanha_id}/destinatarios/{destinatario_id}/pular")
def campanha_pular(campanha_id: int, destinatario_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    campanha = db.query(Campanha).filter(Campanha.id == campanha_id).first()
    if not campanha:
        raise HTTPException(404)
    modelo = _modelo_destinatario_campanha(campanha)
    destinatario = db.query(modelo).filter(modelo.id == destinatario_id, modelo.campanha_id == campanha_id).first()
    if not destinatario:
        raise HTTPException(404)
    if destinatario.status == "EM_ENVIO" and destinatario.reservado_por_id == usuario.id:
        destinatario.status = "IGNORADO"
        db.commit()
    return RedirectResponse(f"/organiza/campanhas/{campanha_id}/proximo", status_code=303)


@app.post("/organiza/campanhas/{campanha_id}/destinatarios/{destinatario_id}/enviado")
def campanha_marcar_enviado(campanha_id: int, destinatario_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    campanha = db.query(Campanha).filter(Campanha.id == campanha_id).first()
    if not campanha:
        raise HTTPException(404)
    modelo = _modelo_destinatario_campanha(campanha)
    destinatario = db.query(modelo).filter(modelo.id == destinatario_id, modelo.campanha_id == campanha_id).first()
    if not destinatario:
        raise HTTPException(404)
    if destinatario.status == "EM_ENVIO" and destinatario.reservado_por_id == usuario.id:
        destinatario.status = "ENVIADO"
        destinatario.enviado_por_id = usuario.id
        destinatario.enviado_em = datetime.now()
        if (campanha.lista_tipo or "").upper() == "ATUALIZACAO" and getattr(destinatario, "cliente", None):
            _registrar_oferta_atualizacao_cliente(destinatario.cliente, campanha, "OFERTA_ENVIADA")
        db.commit()
    return RedirectResponse(f"/organiza/campanhas/{campanha_id}/proximo", status_code=303)


@app.post("/organiza/campanhas/{campanha_id}/destinatarios/{destinatario_id}/nao-receber")
def campanha_nao_receber(campanha_id: int, destinatario_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    campanha = db.query(Campanha).filter(Campanha.id == campanha_id).first()
    if not campanha:
        raise HTTPException(404)
    if (campanha.lista_tipo or "").upper() == "ALUGUEL":
        destinatario = db.query(CampanhaAluguelDestinatario).options(selectinload(CampanhaAluguelDestinatario.contato)).filter(
            CampanhaAluguelDestinatario.id == destinatario_id,
            CampanhaAluguelDestinatario.campanha_id == campanha_id,
        ).first()
        if not destinatario:
            raise HTTPException(404)
        if destinatario.reservado_por_id == usuario.id and destinatario.status == "EM_ENVIO":
            destinatario.contato.campanhas_ativo = 0
            destinatario.status = "IGNORADO"
            db.query(CampanhaAluguelDestinatario).filter(
                CampanhaAluguelDestinatario.contato_id == destinatario.contato_id,
                CampanhaAluguelDestinatario.status == "PENDENTE",
            ).update({CampanhaAluguelDestinatario.status: "IGNORADO"}, synchronize_session=False)
            db.commit()
    else:
        destinatario = db.query(CampanhaDestinatario).options(selectinload(CampanhaDestinatario.cliente)).filter(
            CampanhaDestinatario.id == destinatario_id,
            CampanhaDestinatario.campanha_id == campanha_id,
        ).first()
        if not destinatario:
            raise HTTPException(404)
        if destinatario.reservado_por_id == usuario.id and destinatario.status == "EM_ENVIO":
            destinatario.cliente.campanhas_ativo = 0
            destinatario.status = "IGNORADO"
            db.query(CampanhaDestinatario).filter(
                CampanhaDestinatario.cliente_id == destinatario.cliente_id,
                CampanhaDestinatario.status == "PENDENTE",
            ).update({CampanhaDestinatario.status: "IGNORADO"}, synchronize_session=False)
            db.commit()
    return RedirectResponse(f"/organiza/campanhas/{campanha_id}/proximo", status_code=303)


@app.get("/campanhas/midia/{token}", response_class=HTMLResponse)
def campanha_midia_publica(token: str, db: Session = Depends(get_db)):
    campanha = db.query(Campanha).filter(Campanha.imagem_token == token).first()
    if not campanha or not campanha.imagem_bytes:
        raise HTTPException(404)
    titulo = html.escape(campanha.nome or "Karaokê RJ")
    imagem_url = f"{PUBLIC_BASE_URL}/campanhas/midia/{token}/arquivo"
    conteudo = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{titulo}</title><meta property="og:title" content="{titulo}"><meta property="og:type" content="website"><meta property="og:image" content="{imagem_url}"></head><body style="margin:0;background:#111;display:grid;place-items:center;min-height:100vh"><img src="{imagem_url}" alt="{titulo}" style="max-width:100%;height:auto"></body></html>"""
    return HTMLResponse(conteudo)


@app.get("/campanhas/midia/{token}/arquivo")
def campanha_midia_arquivo(token: str, db: Session = Depends(get_db)):
    campanha = db.query(Campanha).filter(Campanha.imagem_token == token).first()
    if not campanha or not campanha.imagem_bytes:
        raise HTTPException(404)
    return Response(content=campanha.imagem_bytes, media_type=campanha.imagem_mime or "image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


def nfse_descricao_manutencao(manutencao: Manutencao, orcamento: Orcamento | None) -> str:
    linhas = ["Serviço de manutenção:"]
    principal = (manutencao.diagnostico or manutencao.defeito or "Manutenção de equipamento de karaokê").strip()
    if principal:
        linhas.extend(["", f"- {principal}"])
    if orcamento:
        for item in orcamento.itens:
            if item.opcional and not item.aprovado:
                continue
            descricao = (item.descricao or "").strip()
            if not descricao:
                continue
            qtd = max(int(item.quantidade or 1), 1)
            prefixo = f"{qtd}x " if qtd > 1 else ""
            linha = f"- {prefixo}{descricao}"
            if linha not in linhas:
                linhas.append(linha)
    return "\n".join(linhas).strip()


def nfse_payload(rascunho: NFSERascunho) -> dict:
    cliente = rascunho.cliente
    documento = re.sub(r"\D", "", (cliente.documento or ""))
    return {
        "tipo": "NFSE_RASCUNHO",
        "rascunho_id": rascunho.id,
        "origem": rascunho.origem,
        "competencia": rascunho.competencia.isoformat() if rascunho.competencia else date.today().isoformat(),
        "tomador": {
            "documento": documento,
            "tipo_documento": "CNPJ" if len(documento) == 14 else "CPF" if len(documento) == 11 else "",
            "nome": (cliente.razao_social or cliente.nome or "").strip(),
            "nome_contato": (cliente.nome or "").strip(),
            "email": (cliente.email or "").strip(),
            "telefone": (cliente.whatsapp_completo() or "").strip(),
            "cep": re.sub(r"\D", "", (cliente.cep or "")),
            "logradouro": (cliente.endereco or "").strip(),
            "numero": (cliente.endereco_numero or "").strip(),
            "complemento": (cliente.complemento or "").strip(),
            "bairro": (cliente.bairro or "").strip(),
            "municipio": (cliente.municipio or cliente.cidade or "").strip(),
            "uf": (cliente.estado or "").strip().upper(),
        },
        "servico": {
            "codigo": (rascunho.codigo_servico or NFSE_CODIGO_SERVICO_PADRAO).strip(),
            "codigo_texto": NFSE_SERVICO_META.get((rascunho.codigo_servico or NFSE_CODIGO_SERVICO_PADRAO).strip().replace(".000", ""), {}).get("codigo_texto", ""),
            "nbs_codigo": NFSE_SERVICO_META.get((rascunho.codigo_servico or NFSE_CODIGO_SERVICO_PADRAO).strip().replace(".000", ""), {}).get("nbs_codigo", ""),
            "nbs_texto": NFSE_SERVICO_META.get((rascunho.codigo_servico or NFSE_CODIGO_SERVICO_PADRAO).strip().replace(".000", ""), {}).get("nbs_texto", ""),
            "descricao": (rascunho.descricao or "").strip(),
            "municipio": (rascunho.municipio_prestacao or NFSE_MUNICIPIO_PADRAO).strip(),
            "uf": (rascunho.uf_prestacao or NFSE_UF_PADRAO).strip().upper(),
            "municipio_ibge": (
                getattr(cliente, "municipio_ibge", None)
                if nfse_norm_municipio(rascunho.municipio_prestacao or NFSE_MUNICIPIO_PADRAO) == nfse_norm_municipio(cliente.municipio or cliente.cidade or "")
                and (rascunho.uf_prestacao or NFSE_UF_PADRAO).strip().upper() == (cliente.estado or "").strip().upper()
                else ("3304557" if nfse_norm_municipio(rascunho.municipio_prestacao or NFSE_MUNICIPIO_PADRAO) == nfse_norm_municipio("Rio de Janeiro") and (rascunho.uf_prestacao or NFSE_UF_PADRAO).strip().upper() == "RJ" else "3303500" if nfse_norm_municipio(rascunho.municipio_prestacao or NFSE_MUNICIPIO_PADRAO) == nfse_norm_municipio("Nova Iguaçu") and (rascunho.uf_prestacao or NFSE_UF_PADRAO).strip().upper() == "RJ" else "")
            ) or "",
            "valor": round(float(rascunho.valor_total or 0), 2),
        },
        "evento": {
            "ativo": nfse_tipo_por_codigo(rascunho.codigo_servico) == NFSE_TIPO_ALUGUEL,
            "data_inicio": rascunho.evento_data_inicio.isoformat() if rascunho.evento_data_inicio else "",
            "data_fim": rascunho.evento_data_fim.isoformat() if rascunho.evento_data_fim else "",
            "descricao": (rascunho.evento_descricao or "").strip(),
            "endereco_igual_cliente": bool(getattr(rascunho, "evento_endereco_igual_cliente", 1) != 0),
            "local_tipo": "brasil",
            "identificador": "",
            # Igual à regra da NFA-e: marcado usa o endereço principal do cliente;
            # desmarcado usa o endereço específico informado para o evento.
            "cep": re.sub(r"\D", "", ((cliente.cep or "") if getattr(rascunho, "evento_endereco_igual_cliente", 1) != 0 else (rascunho.evento_cep or ""))),
            "logradouro": ((cliente.endereco or "") if getattr(rascunho, "evento_endereco_igual_cliente", 1) != 0 else (rascunho.evento_logradouro or "")).strip(),
            "numero": ((cliente.endereco_numero or "") if getattr(rascunho, "evento_endereco_igual_cliente", 1) != 0 else (rascunho.evento_numero or "")).strip(),
            "complemento": ((cliente.complemento or "") if getattr(rascunho, "evento_endereco_igual_cliente", 1) != 0 else (rascunho.evento_complemento or "")).strip(),
            "bairro": ((cliente.bairro or "") if getattr(rascunho, "evento_endereco_igual_cliente", 1) != 0 else (rascunho.evento_bairro or "")).strip(),
            "municipio": ((cliente.municipio or cliente.cidade or "") if getattr(rascunho, "evento_endereco_igual_cliente", 1) != 0 else (rascunho.evento_municipio or "")).strip(),
            "uf": ((cliente.estado or "") if getattr(rascunho, "evento_endereco_igual_cliente", 1) != 0 else (rascunho.evento_uf or "")).strip().upper(),
        },
        "tributacao": {
            "issqn_operacao": "TRIBUTAVEL",
            "regime_especial": "NENHUM",
            "exigibilidade_suspensa": False,
            "retencao_issqn": False,
            "beneficio_municipal": False,
            "deducao_reducao": False,
            "valor_aproximado_tributos": "NAO_INFORMAR",
        },
        "parar_antes_emitir": True,
        "portal_url": NFSE_PORTAL_URL,
    }


def nfse_campos_faltantes(payload: dict) -> list[str]:
    faltantes = []
    tomador = payload.get("tomador") or {}
    servico = payload.get("servico") or {}
    if len(re.sub(r"\D", "", tomador.get("documento") or "")) not in (11, 14): faltantes.append("CPF/CNPJ do cliente")
    if not tomador.get("nome"): faltantes.append("nome/razão social")
    if not servico.get("codigo"): faltantes.append("código do serviço")
    if not servico.get("descricao"): faltantes.append("descrição do serviço")
    if float(servico.get("valor") or 0) <= 0: faltantes.append("valor total")
    evento = payload.get("evento") or {}
    if evento.get("ativo"):
        if not servico.get("nbs_codigo"): faltantes.append("NBS do aluguel")
        if not evento.get("data_inicio"): faltantes.append("data inicial do evento")
        if not evento.get("data_fim"): faltantes.append("data final do evento")
        if not evento.get("descricao"): faltantes.append("descrição da atividade de evento")
        local_tipo = (evento.get("local_tipo") or "brasil").lower()
        if local_tipo == "identificador":
            if not evento.get("identificador"): faltantes.append("identificador municipal do evento")
        elif local_tipo == "brasil":
            if len(re.sub(r"\D", "", evento.get("cep") or "")) != 8: faltantes.append("CEP do evento")
            if not evento.get("numero"): faltantes.append("número do endereço do evento")
        else:
            faltantes.append("local do evento")
    return faltantes


def nfae_padrao_produto(tipo: str | None) -> tuple[str, str]:
    tipo_n = tipo_equipamento_padrao(tipo or "")
    return NFAE_PRODUTOS_PADRAO.get(tipo_n, ("00005", tipo_n.title() if tipo_n else "Produto"))


def nfae_codigo_produto(eq: Equipamento) -> str:
    codigo_padrao, _ = nfae_padrao_produto(eq.tipo)
    return (getattr(eq, "nota_codigo", None) or codigo_padrao).strip()


def nfae_descricao_produto(eq: Equipamento) -> str:
    _, descricao_padrao = nfae_padrao_produto(eq.tipo)
    return (getattr(eq, "nota_descricao", None) or descricao_padrao).strip()


def _endereco_cliente_nfae(cliente: Cliente | None) -> dict:
    if not cliente:
        return {"cep":"", "logradouro":"", "numero":"", "complemento":"", "bairro":"", "municipio":"", "municipio_ibge":"", "uf":""}
    return {
        "cep": cliente.cep or "",
        "logradouro": cliente.endereco or "",
        "numero": cliente.endereco_numero or "",
        "complemento": cliente.complemento or "",
        "bairro": cliente.bairro or "",
        "municipio": cliente.municipio or cliente.cidade or "",
        "municipio_ibge": getattr(cliente, "municipio_ibge", None) or "",
        "uf": cliente.estado or "",
    }


def _endereco_entrega_nfae(cliente: Cliente | None) -> dict:
    if not cliente:
        return {}
    return {
        "cep": getattr(cliente, "entrega_cep", None) or "",
        "logradouro": getattr(cliente, "entrega_endereco", None) or "",
        "numero": getattr(cliente, "entrega_numero", None) or "",
        "complemento": getattr(cliente, "entrega_complemento", None) or "",
        "bairro": getattr(cliente, "entrega_bairro", None) or "",
        "municipio": getattr(cliente, "entrega_municipio", None) or "",
        "municipio_ibge": getattr(cliente, "entrega_municipio_ibge", None) or "",
        "uf": getattr(cliente, "entrega_estado", None) or "",
    }


def _endereco_efetivo_nfae(cliente: Cliente | None) -> dict:
    endereco_cliente = _endereco_cliente_nfae(cliente)
    if not cliente or getattr(cliente, "entrega_igual_cliente", 1) != 0:
        return endereco_cliente
    entrega = _endereco_entrega_nfae(cliente)
    # Se um cadastro antigo estiver inconsistente, não inventa endereço: os
    # campos faltantes serão apontados na prévia da NFA-e.
    return entrega


def nfae_cfop(cliente: Cliente | None) -> str:
    uf = str(_endereco_efetivo_nfae(cliente).get("uf") or "").strip().upper()
    return "5102" if uf == "RJ" else "6102"


def nfae_tipo_documento(documento: str | None) -> str:
    digitos = re.sub(r"\D", "", documento or "")
    return "CPF" if len(digitos) == 11 else "CNPJ" if len(digitos) == 14 else ""


def nfae_observacao_simples(eq: Equipamento) -> str:
    """Texto curto e sempre derivado do equipamento do Organiza.

    Não usa texto fixo por modelo para evitar que uma Jukebox seja descrita como
    Maletaokê (ou vice-versa). O pacote instalado é a atualização exibida no
    cadastro do equipamento.
    """
    equipamento = nfae_descricao_produto(eq) or tipo_equipamento_padrao(eq.tipo or "").title() or "Equipamento"
    atualizacao = (eq.pacote or "").strip()
    return f"{equipamento} - atualização {atualizacao}" if atualizacao else equipamento


def nfae_dados_equipamento(eq: Equipamento, db: Session) -> dict:
    cliente = eq.cliente
    endereco_cliente = _endereco_cliente_nfae(cliente)
    endereco_entrega = _endereco_entrega_nfae(cliente)
    endereco_nota = _endereco_efetivo_nfae(cliente)
    entrega_igual_cliente = bool(getattr(cliente, "entrega_igual_cliente", 1) != 0)
    total = round(moeda_num(eq.valor or eq.preco_venda or "0"), 2)
    pagamentos = db.query(PagamentoVenda).filter(
        PagamentoVenda.equipamento_id == eq.id
    ).order_by(PagamentoVenda.data.asc(), PagamentoVenda.id.asc()).all() if eq.id else []
    pagamentos_dados = [
        {
            "data": p.data.strftime("%d/%m/%Y") if p.data else "",
            "forma": (p.forma or p.banco or "").strip(),
            "banco": (p.banco or "").strip(),
            "valor": round(float(p.valor or 0), 2),
        }
        for p in pagamentos
    ]
    if not pagamentos_dados and moeda_num(eq.pago) > 0:
        pagamentos_dados.append({
            "data": eq.data_compra.strftime("%d/%m/%Y") if eq.data_compra else "",
            "forma": "Histórico", "banco": "Histórico",
            "valor": round(moeda_num(eq.pago), 2),
        })
    recebido = round(sum(p["valor"] for p in pagamentos_dados), 2)
    return {
        "versao_layout": "ORGANIZA-NFAE-6",
        "operacao": {
            "natureza": NFAE_PADRAO_FISCAL["natureza_operacao"],
            "tipo": "Saída",
            "destino": "Interna" if (str(endereco_nota.get("uf") or "").strip().upper() == "RJ") else "Interestadual",
            "consumidor_final": True,
            "finalidade": "NF-e normal",
            "tipo_atendimento": "Operação NÃO Presencial, pela INTERNET",
            "intermediador": "Operação sem intermediador",
        },
        "destinatario": {
            "nome": cliente.nome or "",
            "razao_social": (cliente.razao_social or cliente.empresa or "") if nfae_tipo_documento(cliente.documento) == "CNPJ" else "",
            "cpf_cnpj": cliente.documento or "",
            "tipo_documento": nfae_tipo_documento(cliente.documento),
            "inscricao_estadual": cliente.inscricao_estadual or "",
            "situacao_icms": (cliente.situacao_icms or "NAO_CONFIRMADO").strip().upper(),
            "ind_ie_dest": {"CONTRIBUINTE": "1", "ISENTO": "2", "NAO_CONTRIBUINTE": "9"}.get((cliente.situacao_icms or "").strip().upper(), ""),
            "sem_inscricao_estadual": not bool((cliente.inscricao_estadual or "").strip().strip("-")),
            "email": cliente.email or "",
            "telefone": cliente.telefone or "",
            "cep": endereco_nota.get("cep") or "",
            "logradouro": endereco_nota.get("logradouro") or "",
            "numero": endereco_nota.get("numero") or "",
            "complemento": endereco_nota.get("complemento") or "",
            "bairro": endereco_nota.get("bairro") or "",
            "municipio": endereco_nota.get("municipio") or "",
            "municipio_ibge": endereco_nota.get("municipio_ibge") or "",
            "uf": endereco_nota.get("uf") or "",
            "endereco_entrega_igual_cliente": entrega_igual_cliente,
            "endereco_cliente": endereco_cliente,
            "endereco_entrega": endereco_entrega,
        },
        "produto": {
            "codigo": nfae_codigo_produto(eq),
            "descricao": nfae_descricao_produto(eq),
            "grupo_cfop": NFAE_PADRAO_FISCAL["grupo_cfop"],
            "cfop": nfae_cfop(cliente),
            "ncm": NFAE_PADRAO_FISCAL["ncm"],
            "ean": NFAE_PADRAO_FISCAL["ean"],
            "unidade": NFAE_PADRAO_FISCAL["unidade"],
            "quantidade": NFAE_PADRAO_FISCAL["quantidade"],
            "valor_unitario": total,
            "valor_total": total,
            "ean_tributavel": NFAE_PADRAO_FISCAL["ean"],
            "unidade_tributavel": NFAE_PADRAO_FISCAL["unidade"],
            "origem": NFAE_PADRAO_FISCAL["origem"],
            "csosn": NFAE_PADRAO_FISCAL["csosn"],
            "pis_cst": NFAE_PADRAO_FISCAL["pis_cst"],
            "cofins_cst": NFAE_PADRAO_FISCAL["cofins_cst"],
            "valor_compoe_total": NFAE_PADRAO_FISCAL["valor_compoe_total"],
            "numero_serie": eq.numero_serie or "",
            "atualizacao": (eq.pacote or "").strip(),
            "informacao_adicional": nfae_observacao_simples(eq),
        },
        "observacao_fiscal": nfae_observacao_simples(eq),
        "pagamentos": pagamentos_dados,
        "totais": {
            "venda": total,
            "recebido": recebido,
            "saldo": max(round(total - recebido, 2), 0),
        },
        "pagamento_nfae": {
            "forma": (pagamentos_dados[0].get("forma") or pagamentos_dados[0].get("banco") or "") if pagamentos_dados else "",
            "valor": recebido if recebido > 0 else total,
            "a_vista": True,
        },
        "referencia": {
            "equipamento_id": eq.id,
            "codigo_tecnico": eq.maquina or "",
            "identificacao": rotulo_maquina(eq),
            "data_compra": eq.data_compra.strftime("%d/%m/%Y") if eq.data_compra else "",
        },
    }


def nfae_campos_faltantes(dados: dict) -> list[str]:
    d = dados["destinatario"]
    p = dados["produto"]
    faltantes = []
    for chave, rotulo in [
        ("nome", "nome do cliente"), ("cpf_cnpj", "CPF/CNPJ"), ("cep", "CEP"),
        ("logradouro", "endereço"), ("numero", "número"), ("bairro", "bairro"),
        ("municipio", "município"), ("uf", "UF"),
    ]:
        if not str(d.get(chave) or "").strip():
            faltantes.append(rotulo)
    if d.get("tipo_documento") == "CNPJ":
        if not str(d.get("razao_social") or "").strip():
            faltantes.append("razão social")
        sit = str(d.get("situacao_icms") or "").strip().upper()
        if sit not in {"CONTRIBUINTE", "NAO_CONTRIBUINTE", "ISENTO"}:
            faltantes.append("situação ICMS confirmada")
        if sit == "CONTRIBUINTE" and not str(d.get("inscricao_estadual") or "").strip():
            faltantes.append("inscrição estadual")
    if not p.get("codigo"):
        faltantes.append("código fiscal do produto")
    if not p.get("descricao"):
        faltantes.append("descrição fiscal do produto")
    if float(p.get("valor_total") or 0) <= 0:
        faltantes.append("valor da venda")
    return faltantes


def preencher_equipamento(eq: Equipamento, form: dict, db: Session):
    eq.tipo = tipo_equipamento_padrao((form.get("tipo") or "").strip()) or None
    eq.modelo = (form.get("modelo") or "").strip() or None
    codigo_padrao_nfae, descricao_padrao_nfae = nfae_padrao_produto(eq.tipo)
    eq.nota_codigo = re.sub(r"[^A-Za-z0-9._-]", "", (form.get("nota_codigo") or "").strip()) or codigo_padrao_nfae
    eq.nota_descricao = (form.get("nota_descricao") or "").strip() or descricao_padrao_nfae
    pacote_informado = _normalizar_pacote_cadastrado(form.get("pacote"))
    pacote_existente = _normalizar_pacote_cadastrado(eq.pacote)
    eq.pacote = pacote_informado or pacote_existente or _primeiro_pacote_disponivel(db)
    # Nenhuma máquina fica sem pacote. O valor ausente recebe o primeiro pacote disponível.
    # Este valor é derivado do pacote instalado e nunca é informado manualmente.
    eq.falta_pacote = calcular_falta_pacote(eq.pacote, obter_pacote_atual(db))
    eq.plano = (form.get("plano") or "").strip() or None
    try:
        solvoz_empresa_id = int(form.get("solvoz_empresa_id") or 0)
    except (TypeError, ValueError):
        solvoz_empresa_id = 0
    eq.solvoz_empresa_id = solvoz_empresa_id or None
    eq.catalogo_online = 1 if str(form.get("catalogo_online") or "").strip().lower() in ("1", "true", "on", "sim") else 0

    # Opcionais operacionais da venda. São salvos no próprio equipamento para
    # acompanhar a configuração entregue ao cliente e aparecer no card principal.
    def opcao(valor, permitidos, padrao):
        texto = (valor or "").strip()
        return texto if texto in permitidos else padrao

    eq.som = opcao(form.get("som"), {"Premium", "JBL", "NA"}, "NA")
    eq.hdmi_tela_2 = opcao(form.get("hdmi_tela_2"), {"Sim", "Não", "NA"}, "NA")
    eq.teclado_bluetooth = opcao(form.get("teclado_bluetooth"), {"Sim", "Não", "NA"}, "NA")
    eq.microfone = opcao(form.get("microfone"), {"Com fio", "Sem fio", "NA"}, "Com fio")
    eq.sistema_credito = opcao(form.get("sistema_credito"), {"Moedeiro", "Ficheiro", "Teclado", "NA"}, "NA")
    eq.catalogo_impresso = opcao(form.get("catalogo_impresso"), {"Sim", "Não", "NA"}, "NA")

    eq.valor = (form.get("valor") or "").strip() or None
    eq.preco_custo = (form.get("preco_custo") or "").strip() or None
    eq.preco_venda = (form.get("preco_venda") or "").strip() or None
    eq.pago = (form.get("pago") or "").strip() or None
    # O saldo é sempre calculado no servidor para não depender do navegador.
    total = moeda_num(eq.valor)
    recebido = moeda_num(eq.pago)
    eq.falta = f"{max(total - recebido, 0):.2f}" if (eq.valor or eq.pago) else None
    eq.data_compra = data_form(form.get("data_compra") or "")
    eq.previsao_entrega = data_form(form.get("previsao_entrega") or "")
    # Identificadores podem ser corrigidos sem alterar os vínculos históricos.
    maquina_informada = re.sub(r"[^A-Z0-9]", "", (form.get("maquina") or "").strip().upper())
    if maquina_informada:
        eq.maquina = maquina_informada
    try:
        numero_cliente = int(form.get("numero_maquina_cliente") or 0)
        if numero_cliente > 0:
            eq.numero_maquina_cliente = numero_cliente
    except (TypeError, ValueError):
        pass
    eq.numero_hd = re.sub(r"[^A-Z0-9]", "", (form.get("numero_hd") or "").strip().upper()) or None
    eq.numero_serie = None
    eq.status = (form.get("status") or "Ativo").strip()
    eq.observacao = (form.get("observacao") or "").strip() or None
    fabricante = (form.get("fabricante") or "KARAOKERJ").strip().upper()
    eq.fabricante = fabricante if fabricante in ("KARAOKERJ", "OUTROS") else "KARAOKERJ"
    try:
        eq.garantia_meses = max(int(form.get("garantia_meses") or 3), 0)
    except ValueError:
        eq.garantia_meses = 3


def _chave_ordenacao_equipamento(equipamento: Equipamento):
    """Ordena pela entrega; registros sem entrega ficam depois, mantendo ordem estável."""
    data_referencia = equipamento.previsao_entrega or equipamento.data_compra
    data_ordem = data_referencia.toordinal() if data_referencia else 9999999
    criado_ordem = equipamento.criado_em.timestamp() if equipamento.criado_em else 0
    return data_ordem, criado_ordem, equipamento.id or 0


def _chave_ordenacao_equipamento(equipamento: Equipamento):
    """Tipo, número do cliente e data de entrega; usada em todas as listas."""
    ordem_tipo = {"JUKEBOX": 1, "MALETA": 2, "IPHONE": 3, "FLIPERAMA": 4}
    tipo = tipo_equipamento_padrao(equipamento.tipo or "")
    numero = equipamento.numero_maquina_cliente or 999999
    data_referencia = equipamento.previsao_entrega or equipamento.data_compra or date.max
    return ordem_tipo.get(tipo, 99), numero, data_referencia, equipamento.id or 0


def ordenar_equipamentos(equipamentos):
    return sorted(equipamentos, key=_chave_ordenacao_equipamento)


def proximo_codigo_maquina(db: Session) -> str:
    """Cria o próximo KRJ livre sem alterar códigos já cadastrados."""
    usados = {
        int(m.group(1))
        for (codigo,) in db.query(Equipamento.maquina).filter(Equipamento.maquina.isnot(None)).all()
        if (m := re.fullmatch(r"KRJ(\d{5})", (codigo or "").strip().upper()))
    }
    numero = 41
    while numero in usados:
        numero += 1
    return f"KRJ{numero:05d}"


def proximo_numero_cliente(db: Session, cliente_id: int, tipo: str = "JUKEBOX") -> int:
    tipo = tipo_equipamento_padrao(tipo)
    numeros = [
        n for (n,) in db.query(Equipamento.numero_maquina_cliente)
        .filter(Equipamento.cliente_id == cliente_id, func.upper(Equipamento.tipo) == tipo)
        .all() if n
    ]
    return (max(numeros) if numeros else 0) + 1




def reordenar_series_cliente(db: Session, cliente_id: int):
    """Mantido por compatibilidade. A identificação do cliente não é mais renumerada automaticamente."""
    return None

def garantir_identificacao_equipamento(db: Session, equipamento: Equipamento):
    equipamento.tipo = tipo_equipamento_padrao(equipamento.tipo or "")
    equipamento.fabricante = equipamento.fabricante or "KARAOKERJ"
    if not equipamento.maquina:
        equipamento.maquina = proximo_codigo_maquina(db)
    if not equipamento.numero_maquina_cliente:
        equipamento.numero_maquina_cliente = proximo_numero_cliente(db, equipamento.cliente_id, equipamento.tipo)


def validar_codigo_monitor(db: Session, equipamento: Equipamento, codigo_anterior: str = "") -> str:
    codigo = (equipamento.maquina or "").strip().upper()
    if not re.fullmatch(r"KRJ\d{5}", codigo):
        return "O campo “Código da máquina para o monitor” deve seguir o padrão KRJ00001."

    duplicado = db.query(Equipamento).filter(
        func.upper(Equipamento.maquina) == codigo,
        Equipamento.id != (equipamento.id or 0)
    ).first()
    if duplicado:
        return f"O código do monitor {codigo} já está sendo utilizado e não pode ser repetido."

    anterior = (codigo_anterior or "").strip().upper()

    # A faixa histórica KRJ00001..KRJ00040 só é protegida na EDIÇÃO.
    # Cadastros novos devem aceitar normalmente a sequência automática atual
    # (ex.: KRJ00671, KRJ00672...), sem limitar o número a 40.
    if anterior and codigo != anterior:
        numero_anterior = int(anterior[3:]) if re.fullmatch(r"KRJ\d{5}", anterior) else None
        numero_novo = int(codigo[3:])

        # Equipamentos antigos/reservados devem permanecer dentro da faixa histórica
        # quando o código técnico for alterado manualmente.
        if numero_anterior is not None and 1 <= numero_anterior <= 40 and not 1 <= numero_novo <= 40:
            return (
                "Este equipamento utiliza um código histórico entre "
                "KRJ00001 e KRJ00040. Ao editar o código técnico, mantenha-o nessa faixa."
            )

    return ""


def equipamento_codigo_duplicado(db: Session, equipamento: Equipamento):
    return db.query(Equipamento).filter(
        Equipamento.cliente_id == equipamento.cliente_id,
        func.upper(Equipamento.tipo) == tipo_equipamento_padrao(equipamento.tipo or ""),
        Equipamento.numero_maquina_cliente == equipamento.numero_maquina_cliente,
        Equipamento.id != (equipamento.id or 0)
    ).first()


def opcoes_equipamentos(db: Session):
    tipos_bd = [x[0] for x in db.query(Equipamento.tipo).filter(Equipamento.tipo.isnot(None), Equipamento.tipo != "").distinct().order_by(Equipamento.tipo).all()]
    pacotes_bd = [x[0] for x in db.query(Equipamento.pacote).filter(Equipamento.pacote.isnot(None), Equipamento.pacote != "").distinct().all()]
    tipos = list(dict.fromkeys(["JUKEBOX", "MALETA", "IPHONE", "FLIPERAMA"] + tipos_bd))
    candidatos = list(pacotes_bd)
    try:
        candidatos.extend(_pacotes_disponiveis_solvoz())
    except Exception:
        pass
    candidatos.append(obter_pacote_atual(db))
    numericos: dict[int, str] = {}
    especiais = set()
    for pacote in candidatos:
        valor = _normalizar_pacote_cadastrado(pacote)
        numero = _pacote_release_num(valor)
        if numero is not None:
            numericos[numero] = _pacote_label_num(numero)
        elif valor in {"NE", "NA"}:
            especiais.add(valor)
    pacotes_validos = [numericos[n] for n in sorted(numericos, reverse=True)]
    return tipos, pacotes_validos + sorted(especiais)


@app.get("/organiza/clientes/{cliente_id}/equipamentos/novo", response_class=HTMLResponse)
def equipamento_novo(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    tipos, pacotes = opcoes_equipamentos(db)
    return templates.TemplateResponse("organiza/equipamento_form.html", {
        "request": request, "usuario": usuario, "cliente": cliente, "equipamento": None,
        "erro": "", "tipos": tipos, "pacotes": pacotes, "primeiro_pacote": _primeiro_pacote_disponivel(db),
        "proxima_maquina": proximo_codigo_maquina(db),
        "proximo_numero_cliente": proximo_numero_cliente(db, cliente_id),
        "pacote_atual": obter_pacote_atual(db),
        "solvoz_empresas": empresas_solvoz_ativas(db),
    })


@app.post("/organiza/clientes/{cliente_id}/equipamentos/novo")
async def equipamento_criar(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    form = dict(await request.form())
    if not (form.get("tipo") or "").strip():
        eq = Equipamento(cliente_id=cliente_id); preencher_equipamento(eq, form, db)
        tipos, pacotes = opcoes_equipamentos(db)
        return templates.TemplateResponse("organiza/equipamento_form.html", {
            "request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq,
            "erro": "Informe o tipo do equipamento.", "tipos": tipos, "pacotes": pacotes, "primeiro_pacote": _primeiro_pacote_disponivel(db),
            "proxima_maquina": proximo_codigo_maquina(db),
            "proximo_numero_cliente": proximo_numero_cliente(db, cliente_id),
            "solvoz_empresas": empresas_solvoz_ativas(db),
        }, status_code=400)
    eq = Equipamento(cliente_id=cliente_id); preencher_equipamento(eq, form, db)
    garantir_identificacao_equipamento(db, eq)
    erro_identificacao = validar_codigo_monitor(db, eq)
    duplicado_cliente = equipamento_codigo_duplicado(db, eq)
    confirmou_duplicado = form.get("confirmar_codigo_cliente") == "1"
    if duplicado_cliente and not confirmou_duplicado:
        erro_identificacao = (
            f"Este cliente já possui {rotulo_maquina(eq)}. "
            "Confirme abaixo para utilizar o mesmo código; depois altere ou exclua o cadastro antigo."
        )
    if erro_identificacao:
        tipos, pacotes = opcoes_equipamentos(db)
        return templates.TemplateResponse("organiza/equipamento_form.html", {
            "request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq,
            "erro": erro_identificacao, "tipos": tipos, "pacotes": pacotes, "primeiro_pacote": _primeiro_pacote_disponivel(db),
            "proxima_maquina": eq.maquina,
            "proximo_numero_cliente": eq.numero_maquina_cliente,
            "confirmar_duplicado": bool(duplicado_cliente and not confirmou_duplicado),
            "solvoz_empresas": empresas_solvoz_ativas(db),
        }, status_code=400)
    db.add(eq)
    db.flush()
    _sincronizar_pacote_cliente(db, cliente_id)
    db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente_id}", status_code=303)


@app.get("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/editar", response_class=HTMLResponse)
def equipamento_editar(cliente_id: int, equipamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id, Equipamento.cliente_id == cliente_id).first()
    if not cliente or not eq: raise HTTPException(404)
    tipos, pacotes = opcoes_equipamentos(db)
    clientes_transferencia = db.query(Cliente).filter(Cliente.id != cliente_id).order_by(Cliente.nome.asc()).all()
    transferencias = db.query(TransferenciaEquipamento).filter(TransferenciaEquipamento.equipamento_id == equipamento_id).order_by(TransferenciaEquipamento.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/equipamento_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq, "erro": "", "tipos": tipos, "pacotes": pacotes, "primeiro_pacote": _primeiro_pacote_disponivel(db), "clientes_transferencia": clientes_transferencia, "transferencias": transferencias, "solvoz_empresas": empresas_solvoz_ativas(db)})



@app.get("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/recibo", response_class=HTMLResponse)
def equipamento_recibo(
    cliente_id: int,
    equipamento_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Recibo imprimível vinculado ao cliente e ao equipamento."""
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    eq = db.query(Equipamento).filter(
        Equipamento.id == equipamento_id,
        Equipamento.cliente_id == cliente_id,
    ).first()
    if not cliente or not eq:
        raise HTTPException(404)

    valor_recebido = moeda_num(eq.pago)
    return templates.TemplateResponse("organiza/equipamento_recibo.html", {
        "request": request,
        "usuario": usuario,
        "cliente": cliente,
        "equipamento": eq,
        "valor_recebido": valor_recebido,
        "data_recibo": date.today(),
    })


@app.post("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/editar")
async def equipamento_salvar(cliente_id: int, equipamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id, Equipamento.cliente_id == cliente_id).first()
    if not eq: raise HTTPException(404)
    form = dict(await request.form())
    codigo_anterior = (eq.maquina or "").strip().upper()
    preencher_equipamento(eq, form, db)
    garantir_identificacao_equipamento(db, eq)
    erro_identificacao = validar_codigo_monitor(db, eq, codigo_anterior)
    duplicado_cliente = equipamento_codigo_duplicado(db, eq)
    confirmou_duplicado = form.get("confirmar_codigo_cliente") == "1"
    if duplicado_cliente and not confirmou_duplicado:
        erro_identificacao = (
            f"Este cliente já possui {rotulo_maquina(eq)}. "
            "Confirme abaixo para utilizar o mesmo código; depois altere ou exclua o cadastro antigo."
        )
    if erro_identificacao:
        cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
        tipos, pacotes = opcoes_equipamentos(db)
        clientes_transferencia = db.query(Cliente).filter(Cliente.id != cliente_id).order_by(Cliente.nome.asc()).all()
        transferencias = db.query(TransferenciaEquipamento).filter(TransferenciaEquipamento.equipamento_id == equipamento_id).order_by(TransferenciaEquipamento.criado_em.desc()).all()
        return templates.TemplateResponse("organiza/equipamento_form.html", {
            "request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq,
            "erro": erro_identificacao, "tipos": tipos, "pacotes": pacotes, "primeiro_pacote": _primeiro_pacote_disponivel(db),
            "clientes_transferencia": clientes_transferencia, "transferencias": transferencias,
            "confirmar_duplicado": bool(duplicado_cliente and not confirmou_duplicado),
            "solvoz_empresas": empresas_solvoz_ativas(db)
        }, status_code=400)
    db.flush()
    _sincronizar_pacote_cliente(db, cliente_id)
    db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente_id}", status_code=303)



# ---------------------------------------------------------
# EMPRESAS SOLVOZ
# Cadastro operacional no Organiza. Não replica usuários do SolVoz.
# ---------------------------------------------------------

def _clientes_vinculados_empresa_solvoz(db: Session, empresa_id: int):
    return (
        db.query(Cliente)
        .join(Equipamento, Equipamento.cliente_id == Cliente.id)
        .filter(Equipamento.solvoz_empresa_id == empresa_id)
        .distinct()
        .order_by(Cliente.nome.asc())
        .all()
    )


def _emails_com_acesso_solvoz(db: Session, slug: str) -> set[str]:
    """Une acessos Humiat legados e novos acessos diretos SolVoz."""
    slug_n = normalizar_slug_solvoz(slug)
    emails: set[str] = set()
    h_empresa = db.query(HumiatEmpresa).filter(HumiatEmpresa.slug == slug_n).first()
    if h_empresa:
        linhas = (
            db.query(HumiatUsuario.email)
            .join(HumiatUsuarioEmpresa, HumiatUsuarioEmpresa.usuario_id == HumiatUsuario.id)
            .filter(HumiatUsuarioEmpresa.empresa_id == h_empresa.id, HumiatUsuario.ativo == 1)
            .all()
        )
        emails.update({(x[0] or "").strip().lower() for x in linhas if x[0]})
    diretos = (
        db.query(SolVozAcessoCliente.email)
        .join(Cliente, Cliente.id == SolVozAcessoCliente.cliente_id)
        .join(Equipamento, Equipamento.cliente_id == Cliente.id)
        .join(SolVozEmpresa, SolVozEmpresa.id == Equipamento.solvoz_empresa_id)
        .filter(SolVozEmpresa.slug == slug_n, SolVozAcessoCliente.status == "ATIVO")
        .distinct()
        .all()
    )
    emails.update({(x[0] or "").strip().lower() for x in diretos if x[0]})
    return emails


@app.get("/organiza/configuracoes/solvoz-empresas", response_class=HTMLResponse)
def solvoz_empresas_lista(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not usuario.is_admin:
        raise HTTPException(403)
    empresas = db.query(SolVozEmpresa).order_by(SolVozEmpresa.nome.asc()).all()
    linhas_empresas = []
    for empresa in empresas:
        clientes = _clientes_vinculados_empresa_solvoz(db, empresa.id)
        linhas_empresas.append({
            "empresa": empresa,
            "clientes": clientes,
            "emails_acesso": _emails_com_acesso_solvoz(db, empresa.slug),
        })
    editar = None
    try:
        editar_id = int(request.query_params.get("editar") or 0)
    except (TypeError, ValueError):
        editar_id = 0
    if editar_id:
        editar = db.query(SolVozEmpresa).filter(SolVozEmpresa.id == editar_id).first()
    return templates.TemplateResponse("organiza/solvoz_empresas.html", {
        "request": request,
        "usuario": usuario,
        "empresas": empresas,
        "linhas_empresas": linhas_empresas,
        "editar": editar,
        "erro": request.query_params.get("erro", ""),
        "sucesso": request.query_params.get("sucesso", ""),
    })


@app.post("/organiza/configuracoes/solvoz-empresas")
async def solvoz_empresa_salvar(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not usuario.is_admin:
        raise HTTPException(403)
    form = dict(await request.form())
    nome = (form.get("nome") or "").strip()
    slug = normalizar_slug_solvoz(form.get("slug") or nome)
    if not nome or not slug:
        return RedirectResponse("/organiza/configuracoes/solvoz-empresas?erro=Informe+nome+e+slug", status_code=303)
    existente = db.query(SolVozEmpresa).filter(SolVozEmpresa.slug == slug).first()
    if existente:
        return RedirectResponse("/organiza/configuracoes/solvoz-empresas?erro=Este+slug+já+está+cadastrado", status_code=303)
    db.add(SolVozEmpresa(
        nome=nome,
        slug=slug,
        dominio=dominio_solvoz_por_slug(slug),
        ativo=1,
    ))
    db.commit()
    return RedirectResponse("/organiza/configuracoes/solvoz-empresas?sucesso=Empresa+SolVoz+cadastrada", status_code=303)


@app.post("/organiza/configuracoes/solvoz-empresas/{empresa_id}/editar")
async def solvoz_empresa_editar(
    empresa_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not usuario.is_admin:
        raise HTTPException(403)
    empresa = db.query(SolVozEmpresa).filter(SolVozEmpresa.id == empresa_id).first()
    if not empresa:
        raise HTTPException(404)
    form = dict(await request.form())
    nome = (form.get("nome") or "").strip()
    slug = normalizar_slug_solvoz(form.get("slug") or nome)
    if not nome or not slug:
        return RedirectResponse(f"/organiza/configuracoes/solvoz-empresas?editar={empresa_id}&erro=Informe+nome+e+slug", status_code=303)
    existente = db.query(SolVozEmpresa).filter(
        SolVozEmpresa.slug == slug, SolVozEmpresa.id != empresa_id
    ).first()
    if existente:
        return RedirectResponse(f"/organiza/configuracoes/solvoz-empresas?editar={empresa_id}&erro=Este+slug+já+está+cadastrado", status_code=303)
    empresa.nome = nome
    empresa.slug = slug
    empresa.dominio = dominio_solvoz_por_slug(slug)
    db.commit()
    return RedirectResponse("/organiza/configuracoes/solvoz-empresas?sucesso=Empresa+SolVoz+atualizada", status_code=303)


@app.post("/organiza/configuracoes/solvoz-empresas/{empresa_id}/criar-acesso")
async def solvoz_empresa_criar_acesso(
    empresa_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not usuario.is_admin:
        raise HTTPException(403)
    empresa = db.query(SolVozEmpresa).filter(SolVozEmpresa.id == empresa_id).first()
    if not empresa:
        raise HTTPException(404)
    clientes = _clientes_vinculados_empresa_solvoz(db, empresa.id)
    if not clientes:
        msg = quote_plus("Vincule primeiro pelo menos um equipamento desta empresa a um cliente.")
        return RedirectResponse(f"/organiza/configuracoes/solvoz-empresas?erro={msg}", status_code=303)
    form = dict(await request.form())
    cliente = None
    informado = str(form.get("cliente_id") or "").strip()
    if informado.isdigit():
        cid = int(informado)
        cliente = next((c for c in clientes if c.id == cid), None)
    elif len(clientes) == 1:
        cliente = clientes[0]
    if not cliente:
        msg = quote_plus("Selecione o cliente que receberá o acesso ao SolVoz.")
        return RedirectResponse(f"/organiza/configuracoes/solvoz-empresas?erro={msg}", status_code=303)
    # O Organiza mantém o cadastro e o transporte de e-mail. O SolVoz cria a
    # credencial/senha provisória e devolve a senha somente pela API privada.
    cliente = db.query(Cliente).options(
        selectinload(Cliente.equipamentos).selectinload(Equipamento.solvoz_empresa)
    ).filter(Cliente.id == cliente.id).first()
    grupos = [g for g in _solvoz_grupos_cliente(cliente) if int(g["empresa"].id) == int(empresa.id)]
    try:
        resultados = _solvoz_provisionar_cliente(cliente, grupos)
        _solvoz_cache_salvar(db, cliente, resultados)
    except (ValueError, RuntimeError) as exc:
        return RedirectResponse(
            f"/organiza/configuracoes/solvoz-empresas?erro={quote_plus(str(exc))}", status_code=303
        )
    resultado = resultados[0] if resultados else {}
    if resultado.get("email_enviado"):
        msg = f"Acesso SolVoz criado para {cliente.nome}. A senha provisória foi enviada por e-mail."
        chave = "sucesso"
    elif resultado.get("criado"):
        msg = f"Acesso criado para {cliente.nome}, mas o e-mail não foi enviado: {resultado.get('email_erro') or 'verifique a configuração do Resend no Organiza.'}"
        chave = "erro"
    else:
        msg = f"Acesso SolVoz de {cliente.nome} atualizado e equipamentos vinculados."
        chave = "sucesso"
    return RedirectResponse(
        f"/organiza/configuracoes/solvoz-empresas?{chave}={quote_plus(msg)}", status_code=303
    )


@app.post("/organiza/configuracoes/solvoz-empresas/{empresa_id}/status")
async def solvoz_empresa_status(
    empresa_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not usuario.is_admin:
        raise HTTPException(403)
    empresa = db.query(SolVozEmpresa).filter(SolVozEmpresa.id == empresa_id).first()
    if not empresa:
        raise HTTPException(404)
    form = dict(await request.form())
    empresa.ativo = 1 if str(form.get("ativo") or "0") == "1" else 0
    db.commit()
    return RedirectResponse("/organiza/configuracoes/solvoz-empresas", status_code=303)



# -----------------------------------------------------------------------------
# Atualizações de catálogo: compras, Google Drive, e-mail e agenda
# -----------------------------------------------------------------------------
def _atualizacao_pacotes_lista(valor) -> list[str]:
    if isinstance(valor, list):
        bruto = valor
    else:
        try:
            bruto = json.loads(str(valor or "[]"))
        except Exception:
            bruto = [x.strip() for x in str(valor or "").replace(" a ", "/").split("/") if x.strip()]
    if not isinstance(bruto, list):
        return []
    saida = []
    for item in bruto:
        label = str(item or "").strip()
        if label and label not in saida:
            saida.append(label[:30])
    return saida


def _gmail_valido(email: str | None) -> bool:
    valor = (email or "").strip().lower()
    return bool(re.fullmatch(r"[^\s@]+@(gmail\.com|googlemail\.com)", valor))


def _atualizacao_compras_cliente(db: Session, cliente_id: int) -> list[AtualizacaoCompra]:
    return (
        db.query(AtualizacaoCompra)
        .filter(AtualizacaoCompra.cliente_id == int(cliente_id), AtualizacaoCompra.status == "PAGO")
        .order_by(AtualizacaoCompra.pago_em.desc(), AtualizacaoCompra.id.desc())
        .all()
    )


def _atualizacao_pacotes_comprados(db: Session, cliente_id: int) -> list[str]:
    saida = []
    for compra in reversed(_atualizacao_compras_cliente(db, cliente_id)):
        for pacote in _atualizacao_pacotes_lista(compra.pacotes):
            if pacote not in saida:
                saida.append(pacote)
    return saida


def _atualizacao_compra_cobrindo(db: Session, cliente_id: int, pacotes: list[str]) -> AtualizacaoCompra | None:
    desejados = set(_atualizacao_pacotes_lista(pacotes))
    if not desejados:
        return None
    compras = _atualizacao_compras_cliente(db, cliente_id)
    for compra in compras:
        if desejados.issubset(set(_atualizacao_pacotes_lista(compra.pacotes))):
            return compra
    # Compatibilidade para compras antigas separadas em mais de um registro.
    uniao = set()
    for compra in compras:
        uniao.update(_atualizacao_pacotes_lista(compra.pacotes))
    return compras[0] if desejados.issubset(uniao) and compras else None


def _atualizacao_fluxo_url(cliente: Cliente, compra: AtualizacaoCompra) -> str:
    if not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)
    return f"{PUBLIC_BASE_URL.rstrip('/')}/atualizacao/{cliente.token_ficha}/{compra.id}"


def _atualizacao_cadastro_url(cliente: Cliente, compra: AtualizacaoCompra) -> str:
    fluxo = _atualizacao_fluxo_url(cliente, compra)
    return f"{PUBLIC_BASE_URL.rstrip('/')}/cadastro/{cliente.token_ficha}?{urlencode({'next': fluxo})}"


def _atualizacao_drive_file_id(valor: str | None) -> str:
    texto = (valor or "").strip()
    if not texto:
        return ""
    if re.fullmatch(r"[A-Za-z0-9_-]{15,}", texto):
        return texto
    try:
        parsed = urlparse(texto)
        path = parsed.path or ""
        for padrao in (r"/file/d/([A-Za-z0-9_-]+)", r"/folders/([A-Za-z0-9_-]+)", r"/d/([A-Za-z0-9_-]+)"):
            m = re.search(padrao, path)
            if m:
                return m.group(1)
        query = parse_qs(parsed.query)
        if query.get("id"):
            return str(query["id"][0])
    except Exception:
        pass
    return ""


def _atualizacao_pacotes_sync_solvoz(db: Session) -> list[AtualizacaoPacote]:
    labels = []
    try:
        labels = _pacotes_disponiveis_solvoz()
    except Exception:
        labels = []
    existentes = {p.pacote: p for p in db.query(AtualizacaoPacote).all()}
    alterou = False
    for label in labels:
        if label not in existentes:
            pacote = AtualizacaoPacote(pacote=label, ativo=1)
            db.add(pacote)
            existentes[label] = pacote
            alterou = True
    if alterou:
        db.commit()
    return db.query(AtualizacaoPacote).order_by(AtualizacaoPacote.pacote.asc()).all()


def _atualizacao_pacotes_intervalo(db: Session, inicio: str, fim: str) -> list[str]:
    candidatos = [p.pacote for p in _atualizacao_pacotes_sync_solvoz(db) if p.ativo]
    if not candidatos:
        try:
            candidatos = _pacotes_disponiveis_solvoz()
        except Exception:
            candidatos = []
    ini = _pacote_release_num(inicio)
    end = _pacote_release_num(fim)
    if ini is None or end is None or ini > end:
        return []
    lista = []
    for label in candidatos:
        numero = _pacote_release_num(label)
        if numero is not None and ini <= numero <= end:
            lista.append((numero, _pacote_label_num(numero)))
    lista.sort(key=lambda x: x[0])
    if not lista:
        return [_pacote_label_num(ini)] if ini == end else [_pacote_label_num(ini), _pacote_label_num(end)]
    return [x[1] for x in lista]


def _google_integracao(db: Session, criar: bool = False) -> OrganizaGoogleIntegracao | None:
    item = db.query(OrganizaGoogleIntegracao).order_by(OrganizaGoogleIntegracao.id.asc()).first()
    if not item and criar:
        item = OrganizaGoogleIntegracao(calendar_id=ORGANIZA_GOOGLE_CALENDAR_ID)
        db.add(item)
        db.commit()
        db.refresh(item)
    return item


def _google_configurado() -> bool:
    return bool(ORGANIZA_GOOGLE_CLIENT_ID and ORGANIZA_GOOGLE_CLIENT_SECRET and ORGANIZA_GOOGLE_REDIRECT_URI)


def _google_http_json(url: str, *, method: str = "GET", token: str = "", payload=None, form=None, timeout: int = 20) -> dict:
    data = None
    headers = {"Accept": "application/json", "User-Agent": f"HUMIAT-Organiza/{ORGANIZA_VERSION}"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body or "{}") if body else {}
    except urllib.error.HTTPError as exc:
        detalhe = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google HTTP {exc.code}: {detalhe[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Falha de rede Google: {exc.reason}") from exc


def _google_access_token(db: Session) -> str:
    integ = _google_integracao(db)
    if not integ or not integ.refresh_token and not integ.access_token:
        raise RuntimeError("Google ainda não conectado no Organiza.")
    agora = datetime.now()
    if integ.access_token and (not integ.expires_at or integ.expires_at > agora + timedelta(minutes=2)):
        return integ.access_token
    if not integ.refresh_token:
        raise RuntimeError("Conexão Google expirada. Reconecte a conta no Organiza.")
    if not _google_configurado():
        raise RuntimeError("Credenciais Google do Organiza não configuradas.")
    data = _google_http_json(
        "https://oauth2.googleapis.com/token", method="POST",
        form={
            "client_id": ORGANIZA_GOOGLE_CLIENT_ID,
            "client_secret": ORGANIZA_GOOGLE_CLIENT_SECRET,
            "refresh_token": integ.refresh_token,
            "grant_type": "refresh_token",
        },
    )
    token = str(data.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Google não retornou novo access token.")
    integ.access_token = token
    integ.expires_at = agora + timedelta(seconds=max(int(data.get("expires_in") or 3600) - 30, 60))
    db.commit()
    return token


def _google_drive_conceder_acesso(db: Session, file_id: str, email: str) -> None:
    if not file_id:
        raise RuntimeError("Arquivo/pasta do Google Drive sem ID.")
    if not _gmail_valido(email):
        raise RuntimeError("Cadastre um endereço Gmail válido antes de liberar os arquivos.")
    token = _google_access_token(db)
    encoded = urllib.parse.quote(file_id, safe="")
    try:
        dados = _google_http_json(
            f"https://www.googleapis.com/drive/v3/files/{encoded}/permissions?fields=permissions(id,emailAddress,type,role)&supportsAllDrives=true",
            token=token,
        )
        for permissao in dados.get("permissions") or []:
            if str(permissao.get("emailAddress") or "").strip().lower() == email.strip().lower():
                return
    except Exception:
        # A criação abaixo continua sendo a validação definitiva da permissão.
        pass
    _google_http_json(
        f"https://www.googleapis.com/drive/v3/files/{encoded}/permissions?sendNotificationEmail=false&supportsAllDrives=true",
        method="POST", token=token,
        payload={"type": "user", "role": "reader", "emailAddress": email.strip().lower()},
    )


def _atualizacao_links_compra(db: Session, compra: AtualizacaoCompra) -> list[dict]:
    links = []
    faltando = []
    for label in _atualizacao_pacotes_lista(compra.pacotes):
        pacote = db.query(AtualizacaoPacote).filter(AtualizacaoPacote.pacote == label, AtualizacaoPacote.ativo == 1).first()
        if not pacote or not (pacote.drive_file_id or pacote.drive_url):
            faltando.append(label)
            continue
        file_id = (pacote.drive_file_id or _atualizacao_drive_file_id(pacote.drive_url)).strip()
        if not file_id:
            faltando.append(label)
            continue
        url = (pacote.drive_url or f"https://drive.google.com/open?id={file_id}").strip()
        links.append({"pacote": label, "file_id": file_id, "url": url})
    if faltando:
        raise RuntimeError("Cadastre o link do Google Drive dos pacotes: " + ", ".join(faltando))
    return links


def _atualizacao_horario_valido(tipo: str, momento: datetime | None) -> bool:
    tipo = (tipo or "").strip().upper()
    if not momento or tipo not in ATUALIZACAO_HORARIOS or momento.weekday() >= 5:
        return False
    return momento.minute == 0 and momento.second == 0 and momento.hour in ATUALIZACAO_HORARIOS[tipo]


def _atualizacao_horario_ocupado(db: Session, momento: datetime, ignorar_agendamento_id: int = 0) -> bool:
    inicio = momento - timedelta(minutes=59)
    fim = momento + timedelta(minutes=59)
    q = db.query(AtualizacaoAgendamento).filter(
        AtualizacaoAgendamento.status == "RESERVADO",
        AtualizacaoAgendamento.data_hora >= inicio,
        AtualizacaoAgendamento.data_hora <= fim,
    )
    if ignorar_agendamento_id:
        q = q.filter(AtualizacaoAgendamento.id != ignorar_agendamento_id)
    if q.first():
        return True
    # A agenda de atualização respeita também os compromissos já existentes do Organiza.
    if db.query(AgendaManual).filter(AgendaManual.data_hora >= inicio, AgendaManual.data_hora <= fim).first():
        return True
    if db.query(Manutencao).filter(
        ~Manutencao.status.in_(("Encerrada", "Cancelada")),
        or_(
            Manutencao.entrega_prevista_em.between(inicio, fim),
            Manutencao.retirada_em.between(inicio, fim),
        ),
    ).first():
        return True
    return False


def _atualizacao_horarios_disponiveis(db: Session, tipo: str, dia: date) -> list[str]:
    tipo = (tipo or "").strip().upper()
    if tipo not in ATUALIZACAO_HORARIOS or dia.weekday() >= 5:
        return []
    saida = []
    agora = datetime.now()
    for hora in ATUALIZACAO_HORARIOS[tipo]:
        momento = datetime.combine(dia, time(hour=hora, minute=0))
        if momento <= agora:
            continue
        if not _atualizacao_horario_ocupado(db, momento):
            saida.append(momento.strftime("%H:%M"))
    return saida


def _google_calendar_event_payload(cliente: Cliente, compra: AtualizacaoCompra, ag: AtualizacaoAgendamento) -> dict:
    tz = ZoneInfo(ORGANIZA_GOOGLE_TZ)
    inicio = ag.data_hora.replace(tzinfo=tz) if ag.data_hora.tzinfo is None else ag.data_hora.astimezone(tz)
    fim = inicio + timedelta(minutes=int(ag.duracao_minutos or ATUALIZACAO_DURACAO_MINUTOS))
    tipo_rotulo = "Em casa / AnyDesk" if ag.tipo == "CASA" else "Na loja"
    pacotes = " / ".join(_atualizacao_pacotes_lista(compra.pacotes))
    descricao = (
        f"Atualização Karaokê RJ\nCliente: {cliente.nome}\n"
        f"WhatsApp: +{cliente.ddi or ''}{cliente.telefone or ''}\n"
        f"Gmail: {cliente.email or '-'}\nPacotes: {pacotes}\n"
        f"Atendimento: {tipo_rotulo}\nCompra Organiza #{compra.id}"
    )
    return {
        "summary": f"Atualização Karaokê RJ - {tipo_rotulo} - {cliente.nome}"[:180],
        "description": descricao,
        "start": {"dateTime": inicio.isoformat(), "timeZone": ORGANIZA_GOOGLE_TZ},
        "end": {"dateTime": fim.isoformat(), "timeZone": ORGANIZA_GOOGLE_TZ},
        "reminders": {"useDefault": True},
    }


def _google_calendar_sincronizar(db: Session, ag: AtualizacaoAgendamento, cliente: Cliente, compra: AtualizacaoCompra) -> None:
    try:
        token = _google_access_token(db)
        integ = _google_integracao(db, criar=True)
        calendar_id = urllib.parse.quote((integ.calendar_id or ORGANIZA_GOOGLE_CALENDAR_ID), safe="")
        payload = _google_calendar_event_payload(cliente, compra, ag)
        if ag.google_event_id:
            event_id = urllib.parse.quote(ag.google_event_id, safe="")
            dados = _google_http_json(
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}",
                method="PUT", token=token, payload=payload,
            )
        else:
            dados = _google_http_json(
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                method="POST", token=token, payload=payload,
            )
            ag.google_event_id = str(dados.get("id") or "").strip() or None
        ag.google_sync_status = "SINCRONIZADO"
        ag.google_sync_erro = None
    except Exception as exc:
        ag.google_sync_status = "ERRO"
        ag.google_sync_erro = str(exc)[:1200]


def _google_calendar_excluir(db: Session, ag: AtualizacaoAgendamento) -> None:
    if not ag.google_event_id:
        return
    try:
        token = _google_access_token(db)
        integ = _google_integracao(db, criar=True)
        calendar_id = urllib.parse.quote((integ.calendar_id or ORGANIZA_GOOGLE_CALENDAR_ID), safe="")
        event_id = urllib.parse.quote(ag.google_event_id, safe="")
        req = urllib.request.Request(
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}",
            method="DELETE",
            headers={"Authorization": f"Bearer {token}", "User-Agent": f"HUMIAT-Organiza/{ORGANIZA_VERSION}"},
        )
        try:
            urllib.request.urlopen(req, timeout=20).read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 410):
                raise
        ag.google_event_id = None
        ag.google_sync_status = "REMOVIDO"
        ag.google_sync_erro = None
    except Exception as exc:
        ag.google_sync_status = "ERRO"
        ag.google_sync_erro = str(exc)[:1200]


def _google_calendar_manual_event_payload(evento: AgendaManual) -> dict:
    tz = ZoneInfo(ORGANIZA_GOOGLE_TZ)
    inicio = evento.data_hora.replace(tzinfo=tz) if evento.data_hora.tzinfo is None else evento.data_hora.astimezone(tz)
    fim = inicio + timedelta(minutes=60)
    tipo_rotulos = {
        "visita": "Visita",
        "entrada": "Cliente vai trazer",
        "online": "Atendimento online",
        "retirada": "Cliente vem buscar",
        "fornecedor": "Fornecedor",
        "outro": "Outro",
    }
    tipo_rotulo = tipo_rotulos.get((evento.tipo or "").strip().lower(), (evento.tipo or "Compromisso").strip().title())
    descricao = [f"Compromisso criado no Organiza", f"Tipo: {tipo_rotulo}"]
    if evento.contato:
        descricao.append(f"Contato: {evento.contato}")
    if evento.observacao:
        descricao.append(f"Observação: {evento.observacao}")
    if evento.id:
        descricao.append(f"Agenda Organiza #{evento.id}")
    return {
        "summary": str(evento.titulo or "Compromisso Organiza")[:180],
        "description": "\n".join(descricao),
        "start": {"dateTime": inicio.isoformat(), "timeZone": ORGANIZA_GOOGLE_TZ},
        "end": {"dateTime": fim.isoformat(), "timeZone": ORGANIZA_GOOGLE_TZ},
        "reminders": {"useDefault": True},
    }


def _google_calendar_manual_sincronizar(db: Session, evento: AgendaManual) -> None:
    """Cria ou atualiza no Google Agenda o mesmo compromisso manual do Organiza.

    A falha do Google nunca impede salvar a agenda local; o status fica registrado
    no próprio compromisso para permitir correção/reconexão depois.
    """
    try:
        token = _google_access_token(db)
        integ = _google_integracao(db, criar=True)
        calendar_id = urllib.parse.quote((integ.calendar_id or ORGANIZA_GOOGLE_CALENDAR_ID), safe="")
        payload = _google_calendar_manual_event_payload(evento)
        if evento.google_event_id:
            event_id = urllib.parse.quote(evento.google_event_id, safe="")
            dados = _google_http_json(
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}",
                method="PUT", token=token, payload=payload,
            )
        else:
            dados = _google_http_json(
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                method="POST", token=token, payload=payload,
            )
            evento.google_event_id = str(dados.get("id") or "").strip() or None
        evento.google_sync_status = "SINCRONIZADO"
        evento.google_sync_erro = None
        evento.google_sync_em = datetime.now()
    except Exception as exc:
        evento.google_sync_status = "ERRO"
        evento.google_sync_erro = str(exc)[:1200]
        evento.google_sync_em = datetime.now()


def _google_calendar_manual_excluir(db: Session, evento: AgendaManual) -> None:
    if not evento.google_event_id:
        return
    try:
        token = _google_access_token(db)
        integ = _google_integracao(db, criar=True)
        calendar_id = urllib.parse.quote((integ.calendar_id or ORGANIZA_GOOGLE_CALENDAR_ID), safe="")
        event_id = urllib.parse.quote(evento.google_event_id, safe="")
        req = urllib.request.Request(
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}",
            method="DELETE",
            headers={"Authorization": f"Bearer {token}", "User-Agent": f"HUMIAT-Organiza/{ORGANIZA_VERSION}"},
        )
        try:
            urllib.request.urlopen(req, timeout=20).read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 410):
                raise
        evento.google_event_id = None
        evento.google_sync_status = "REMOVIDO"
        evento.google_sync_erro = None
        evento.google_sync_em = datetime.now()
    except Exception as exc:
        evento.google_sync_status = "ERRO"
        evento.google_sync_erro = str(exc)[:1200]
        evento.google_sync_em = datetime.now()


def _atualizacao_enviar_email_casa(db: Session, cliente: Cliente, compra: AtualizacaoCompra) -> None:
    gmail = (cliente.email or "").strip().lower()
    if not _gmail_valido(gmail):
        raise RuntimeError("O cliente precisa atualizar o cadastro com um Gmail válido.")
    links = _atualizacao_links_compra(db, compra)
    for item in links:
        _google_drive_conceder_acesso(db, item["file_id"], gmail)
    if not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)
    agenda_url = f"{PUBLIC_BASE_URL.rstrip('/')}/atualizacao/{cliente.token_ficha}/{compra.id}/agenda?tipo=CASA"
    pacotes_txt = " / ".join(_atualizacao_pacotes_lista(compra.pacotes))
    lista_texto = "\n".join(f"- Pacote {x['pacote']}: {x['url']}" for x in links)
    lista_html = "".join(
        f'<p style="margin:8px 0"><a href="{html.escape(x["url"])}" style="display:inline-block;padding:11px 16px;background:#e6003c;color:white;text-decoration:none;border-radius:8px;font-weight:700">Abrir pacote {html.escape(x["pacote"])}</a></p>'
        for x in links
    )
    texto = (
        f"Olá, {cliente.nome}!\n\nSua atualização Karaokê RJ está pronta.\n"
        f"Pacotes: {pacotes_txt}\nGmail liberado: {gmail}\n\n"
        "IMPORTANTE: abra este e-mail no PC ou notebook que será utilizado pelo técnico via AnyDesk. Não faça o download pelo celular.\n\n"
        "Como baixar:\n1. Entre no Google com o mesmo Gmail acima.\n2. Abra cada link abaixo.\n3. Clique em Baixar e aguarde o download terminar completamente.\n4. Não altere nem mova os arquivos antes do atendimento.\n"
        f"\n{lista_texto}\n\nDepois que TODOS os arquivos estiverem baixados no computador, agende o atendimento pelo AnyDesk:\n{agenda_url}\n\n"
        "Atendimentos em casa: segunda a sexta, das 10:00 às 20:00, com horários de 1 em 1 hora.\n\nKaraokê RJ"
    )
    corpo = f"""
    <div style="font-family:Arial,sans-serif;max-width:640px;margin:auto;color:#20242a">
      <h2 style="color:#e6003c">Sua atualização Karaokê RJ está pronta</h2>
      <p>Pacotes adquiridos: <strong>{html.escape(pacotes_txt)}</strong></p>
      <p>Acesso liberado para: <strong>{html.escape(gmail)}</strong></p>
      <div style="padding:14px;border-radius:10px;background:#fff3f6;border:1px solid #ffd0dc"><strong>Abra este e-mail no PC ou notebook que será utilizado pelo técnico via AnyDesk.</strong><br>Não faça o download pelo celular.</div>
      <h3>Como baixar</h3>
      <ol><li>Entre no Google com o mesmo Gmail acima.</li><li>Abra cada pacote.</li><li>Clique em <strong>Baixar</strong> e aguarde terminar completamente.</li><li>Não altere nem mova os arquivos antes do atendimento.</li></ol>
      {lista_html}
      <h3>Depois de baixar todos os arquivos</h3>
      <p>Somente depois que os arquivos estiverem no computador, marque o atendimento do técnico.</p>
      <p><a href="{html.escape(agenda_url)}" style="display:inline-block;padding:12px 18px;background:#111827;color:#fff;text-decoration:none;border-radius:8px;font-weight:700">Agendar atendimento pelo AnyDesk</a></p>
      <p style="color:#687180;font-size:13px">Segunda a sexta, das 10:00 às 20:00. Os horários são reservados de 1 em 1 hora.</p>
    </div>"""
    _enviar_resend_humiat(gmail, "Sua atualização Karaokê RJ está pronta", texto, corpo, user_agent=f"Organiza/{ORGANIZA_VERSION}")
    compra.arquivos_liberados_em = compra.arquivos_liberados_em or datetime.now()
    compra.email_arquivos_enviado_em = datetime.now()
    compra.email_erro = None
    db.commit()


def _atualizacao_enviar_email_agendamento(db: Session, cliente: Cliente, compra: AtualizacaoCompra, ag: AtualizacaoAgendamento) -> None:
    gmail = (cliente.email or "").strip().lower()
    if not _gmail_valido(gmail):
        return
    tipo_rotulo = "em casa / AnyDesk" if ag.tipo == "CASA" else "na loja"
    data_txt = ag.data_hora.strftime("%d/%m/%Y às %H:%M")
    pacotes_txt = " / ".join(_atualizacao_pacotes_lista(compra.pacotes))
    lembrete = "Os arquivos devem estar completamente baixados no PC/notebook antes do horário marcado." if ag.tipo == "CASA" else "Leve o equipamento no horário reservado."
    texto = (
        f"Olá, {cliente.nome}!\n\nSeu atendimento para atualização foi reservado.\n"
        f"Atendimento: {tipo_rotulo}\nData e hora: {data_txt}\nPacotes: {pacotes_txt}\n\n{lembrete}\n\nKaraokê RJ"
    )
    corpo = f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;color:#20242a">
      <h2 style="color:#e6003c">Atualização agendada</h2>
      <p><strong>{html.escape(tipo_rotulo.title())}</strong></p>
      <p>Data e hora: <strong>{html.escape(data_txt)}</strong></p>
      <p>Pacotes: {html.escape(pacotes_txt)}</p>
      <div style="padding:12px;border-radius:10px;background:#f5f7fa">{html.escape(lembrete)}</div>
    </div>"""
    _enviar_resend_humiat(gmail, "Atualização Karaokê RJ agendada", texto, corpo, user_agent=f"Organiza/{ORGANIZA_VERSION}")
    ag.email_confirmacao_em = datetime.now()
    db.commit()


def _atualizacao_registrar_compra(
    db: Session, cliente: Cliente, pacotes: list[str], *, origem: str, order_nsu: str = "",
    valor_normal_centavos: int = 0, valor_pago_centavos: int = 0, forma_pagamento: str = "",
) -> AtualizacaoCompra:
    pacotes = _atualizacao_pacotes_lista(pacotes)
    if not pacotes:
        raise ValueError("Informe os pacotes adquiridos.")
    existente = None
    if order_nsu:
        existente = db.query(AtualizacaoCompra).filter(AtualizacaoCompra.order_nsu == order_nsu).first()
    if not existente:
        desejados = set(pacotes)
        for item in _atualizacao_compras_cliente(db, cliente.id):
            if desejados == set(_atualizacao_pacotes_lista(item.pacotes)):
                existente = item
                break
    compra = existente or AtualizacaoCompra(cliente_id=cliente.id)
    if not existente:
        db.add(compra)
    compra.origem = (origem or "MANUAL")[:30]
    if order_nsu:
        compra.order_nsu = order_nsu[:120]
    compra.pacote_inicio = pacotes[0]
    compra.pacote_fim = pacotes[-1]
    compra.pacotes = json.dumps(pacotes, ensure_ascii=False)
    compra.valor_normal_centavos = int(valor_normal_centavos or 0) or None
    compra.valor_pago_centavos = int(valor_pago_centavos or 0) or None
    compra.forma_pagamento = (forma_pagamento or "")[:60] or None
    compra.status = "PAGO"
    compra.pago_em = compra.pago_em or datetime.now()
    cliente.atualizacao_oferta_status = "PAGO"
    cliente.atualizacao_oferta_periodo = pacotes[0] if len(pacotes) == 1 else f"{pacotes[0]} a {pacotes[-1]}"
    cliente.atualizacao_oferta_pacotes = json.dumps(pacotes, ensure_ascii=False)
    if valor_normal_centavos:
        cliente.atualizacao_oferta_valor_normal_centavos = int(valor_normal_centavos)
    if valor_pago_centavos:
        cliente.atualizacao_oferta_valor_promocional_centavos = int(valor_pago_centavos)
    if order_nsu:
        cliente.atualizacao_oferta_order_nsu = order_nsu[:120]
    cliente.atualizacao_oferta_atualizado_em = datetime.now()
    db.commit()
    db.refresh(compra)
    return compra


def _atualizacao_contexto_admin_cliente(db: Session, cliente: Cliente) -> dict:
    compras = _atualizacao_compras_cliente(db, cliente.id)
    ag_por_compra = {
        a.compra_id: a for a in db.query(AtualizacaoAgendamento).filter(
            AtualizacaoAgendamento.compra_id.in_([c.id for c in compras] or [-1])
        ).all()
    }
    pacotes = _atualizacao_pacotes_sync_solvoz(db)
    return {
        "compras": compras,
        "agendamentos": ag_por_compra,
        "pacotes": pacotes,
        "gmail_ok": _gmail_valido(cliente.email),
    }


def _validar_token_solvoz(x_solvoz_token: Optional[str]) -> None:
    esperado = SOLVOZ_API_TOKEN
    if not esperado:
        raise HTTPException(503, "Integração SolVoz ainda não configurada.")
    recebido = (x_solvoz_token or "").strip()
    if not recebido or not hmac.compare_digest(recebido, esperado):
        raise HTTPException(401, "Token SolVoz inválido.")



@app.get("/api/integracoes/solvoz/clientes/{cliente_id}/atualizacao-contexto")
def api_solvoz_atualizacao_contexto(
    cliente_id: int,
    x_solvoz_token: Optional[str] = Header(default=None, alias="X-SolVoz-Token"),
    db: Session = Depends(get_db),
):
    """Dados atuais do cliente + histórico pago para o link compacto da campanha."""
    _validar_token_solvoz(x_solvoz_token)
    cliente = db.query(Cliente).filter(Cliente.id == int(cliente_id)).first()
    if not cliente:
        raise HTTPException(404, "Cliente não encontrado.")
    if not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)
        db.commit()
    pacotes_oferta = _atualizacao_pacotes_lista(cliente.atualizacao_oferta_pacotes)
    compras = []
    for compra in _atualizacao_compras_cliente(db, cliente.id):
        ag = db.query(AtualizacaoAgendamento).filter(AtualizacaoAgendamento.compra_id == compra.id).first()
        compras.append({
            "id": int(compra.id),
            "pacotes": _atualizacao_pacotes_lista(compra.pacotes),
            "pacote_inicio": compra.pacote_inicio,
            "pacote_fim": compra.pacote_fim,
            "status": compra.status,
            "pago_em": compra.pago_em.isoformat() if compra.pago_em else None,
            "origem": compra.origem,
            "order_nsu": compra.order_nsu,
            "fluxo_url": _atualizacao_fluxo_url(cliente, compra),
            "cadastro_url": _atualizacao_cadastro_url(cliente, compra),
            "arquivos_liberados": bool(compra.arquivos_liberados_em),
            "email_arquivos_enviado": bool(compra.email_arquivos_enviado_em),
            "agendamento": ({
                "tipo": ag.tipo,
                "data_hora": ag.data_hora.isoformat() if ag.data_hora else None,
                "status": ag.status,
                "google_sync_status": ag.google_sync_status,
            } if ag else None),
        })
    return JSONResponse({
        "ok": True,
        "cliente_id": int(cliente.id),
        "nome": (cliente.nome or "").strip(),
        "email": (cliente.email or "").strip().lower(),
        "gmail_ok": _gmail_valido(cliente.email),
        "whatsapp": re.sub(r"\D", "", cliente.whatsapp_completo() or ""),
        "pacotes": pacotes_oferta,
        "pacotes_comprados": _atualizacao_pacotes_comprados(db, cliente.id),
        "compras": compras,
    })


@app.post("/api/integracoes/solvoz/clientes/{cliente_id}/atualizacao-oferta")
async def api_solvoz_atualizacao_oferta(
    cliente_id: int,
    request: Request,
    x_solvoz_token: Optional[str] = Header(default=None, alias="X-SolVoz-Token"),
    db: Session = Depends(get_db),
):
    """Recebe do SolVoz o estágio comercial da oferta de atualização total."""
    _validar_token_solvoz(x_solvoz_token)
    try:
        if "application/json" in (request.headers.get("content-type") or "").lower():
            data = await request.json()
        else:
            data = dict(await request.form())
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    cliente = db.query(Cliente).filter(Cliente.id == int(cliente_id)).first()
    if not cliente:
        raise HTTPException(404, "Cliente não encontrado.")
    status = str(data.get("status") or "").strip().upper()[:30]
    permitidos = {"OFERTA_ENVIADA", "PAGINA_ABERTA", "CHECKOUT_INICIADO", "PAGO", "ERRO_CHECKOUT"}
    if status not in permitidos:
        raise HTTPException(400, "Status da oferta inválido.")
    pacotes = data.get("pacotes")
    if isinstance(pacotes, str):
        try:
            pacotes = json.loads(pacotes)
        except Exception:
            pacotes = [p.strip() for p in pacotes.split("/") if p.strip()]
    pacotes = [str(p).strip()[:30] for p in (pacotes or []) if str(p).strip()]
    # Depois que uma atualização foi paga, abrir novamente o mesmo link não
    # rebaixa o cadastro para PAGINA_ABERTA/CHECKOUT_INICIADO.
    ja_pago = bool(_atualizacao_compra_cobrindo(db, cliente.id, pacotes)) if pacotes else False
    if status == "PAGO":
        compra = _atualizacao_registrar_compra(
            db, cliente, pacotes, origem="SOLVOZ", order_nsu=str(data.get("order_nsu") or ""),
            valor_normal_centavos=int(data.get("valor_normal_centavos") or 0),
            valor_pago_centavos=int(data.get("valor_promocional_centavos") or 0),
            forma_pagamento="InfinitePay",
        )
        return JSONResponse({"ok": True, "cliente_id": cliente.id, "status": "PAGO", "compra_id": compra.id})
    if ja_pago or (cliente.atualizacao_oferta_status or "").upper() == "PAGO":
        return JSONResponse({"ok": True, "cliente_id": cliente.id, "status": "PAGO", "preservado": True})
    cliente.atualizacao_oferta_status = status
    if pacotes:
        cliente.atualizacao_oferta_pacotes = json.dumps(pacotes, ensure_ascii=False)
        cliente.atualizacao_oferta_periodo = pacotes[0] if len(pacotes) == 1 else f"{pacotes[0]} a {pacotes[-1]}"
    try:
        if data.get("valor_normal_centavos") not in (None, ""):
            cliente.atualizacao_oferta_valor_normal_centavos = int(data.get("valor_normal_centavos"))
        if data.get("valor_promocional_centavos") not in (None, ""):
            cliente.atualizacao_oferta_valor_promocional_centavos = int(data.get("valor_promocional_centavos"))
    except Exception:
        pass
    order_nsu = str(data.get("order_nsu") or "").strip()
    if order_nsu:
        cliente.atualizacao_oferta_order_nsu = order_nsu[:120]
    cliente.atualizacao_oferta_atualizado_em = datetime.now()
    db.commit()
    return JSONResponse({"ok": True, "cliente_id": cliente.id, "status": status})



def _google_oauth_state(usuario: Usuario) -> str:
    bruto = f"{int(usuario.id)}|{int(datetime.now().timestamp())}"
    sig = hmac.new(CHAVE_SESSAO.encode(), bruto.encode(), hashlib.sha256).hexdigest()[:24]
    return base64.urlsafe_b64encode(f"{bruto}|{sig}".encode()).decode().rstrip("=")


def _google_oauth_state_valido(state: str, usuario: Usuario) -> bool:
    try:
        raw = base64.urlsafe_b64decode(state + "=" * (-len(state) % 4)).decode()
        uid, ts, sig = raw.split("|", 2)
        bruto = f"{uid}|{ts}"
        esperado = hmac.new(CHAVE_SESSAO.encode(), bruto.encode(), hashlib.sha256).hexdigest()[:24]
        return int(uid) == int(usuario.id) and abs(datetime.now().timestamp() - int(ts)) <= 900 and hmac.compare_digest(sig, esperado)
    except Exception:
        return False


@app.get("/organiza/atualizacoes", response_class=HTMLResponse)
def atualizacoes_admin(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    pacotes = _atualizacao_pacotes_sync_solvoz(db)
    compras = db.query(AtualizacaoCompra).options(selectinload(AtualizacaoCompra.cliente)).order_by(AtualizacaoCompra.pago_em.desc(), AtualizacaoCompra.id.desc()).limit(100).all()
    agendamentos = db.query(AtualizacaoAgendamento).filter(AtualizacaoAgendamento.status == "RESERVADO").order_by(AtualizacaoAgendamento.data_hora.asc()).all()
    google = _google_integracao(db)
    return templates.TemplateResponse("organiza/atualizacoes.html", {
        "request": request, "usuario": usuario, "pacotes": pacotes, "compras": compras,
        "agendamentos": agendamentos, "google": google, "google_configurado": _google_configurado(),
        "mensagem": request.query_params.get("mensagem", ""), "erro": request.query_params.get("erro", ""),
    })


@app.post("/organiza/atualizacoes/pacotes/{pacote_id}")
async def atualizacao_pacote_salvar(pacote_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_admin(usuario)
    pacote = db.get(AtualizacaoPacote, pacote_id)
    if not pacote:
        raise HTTPException(404)
    form = dict(await request.form())
    drive_url = (form.get("drive_url") or "").strip()
    file_id = _atualizacao_drive_file_id(drive_url)
    if drive_url and not file_id:
        return RedirectResponse("/organiza/atualizacoes?erro=" + quote_plus(f"Não consegui identificar o arquivo/pasta do Google Drive do pacote {pacote.pacote}."), status_code=303)
    pacote.drive_url = drive_url or None
    pacote.drive_file_id = file_id or None
    pacote.ativo = 1 if str(form.get("ativo") or "") in ("1", "on", "true") else 0
    db.commit()
    return RedirectResponse("/organiza/atualizacoes?mensagem=" + quote_plus(f"Pacote {pacote.pacote} atualizado."), status_code=303)


@app.get("/organiza/google/conectar")
def organiza_google_conectar(usuario: Usuario = Depends(usuario_logado)):
    exigir_admin(usuario)
    if not _google_configurado():
        return RedirectResponse("/organiza/atualizacoes?erro=" + quote_plus("Configure ORGANIZA_GOOGLE_CLIENT_ID e ORGANIZA_GOOGLE_CLIENT_SECRET no servidor."), status_code=303)
    params = {
        "client_id": ORGANIZA_GOOGLE_CLIENT_ID,
        "redirect_uri": ORGANIZA_GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(GOOGLE_OAUTH_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": _google_oauth_state(usuario),
    }
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params), status_code=302)


@app.get("/organiza/google/callback")
def organiza_google_callback(code: str = "", state: str = "", error: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_admin(usuario)
    if error:
        return RedirectResponse("/organiza/atualizacoes?erro=" + quote_plus(f"Google não autorizou a conexão: {error}"), status_code=303)
    if not code or not _google_oauth_state_valido(state, usuario):
        return RedirectResponse("/organiza/atualizacoes?erro=" + quote_plus("Retorno do Google inválido ou expirado."), status_code=303)
    try:
        data = _google_http_json("https://oauth2.googleapis.com/token", method="POST", form={
            "client_id": ORGANIZA_GOOGLE_CLIENT_ID,
            "client_secret": ORGANIZA_GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": ORGANIZA_GOOGLE_REDIRECT_URI,
        })
        token = str(data.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Google não retornou access token.")
        perfil = _google_http_json("https://openidconnect.googleapis.com/v1/userinfo", token=token)
        integ = _google_integracao(db, criar=True)
        integ.access_token = token
        if data.get("refresh_token"):
            integ.refresh_token = str(data.get("refresh_token"))
        integ.expires_at = datetime.now() + timedelta(seconds=max(int(data.get("expires_in") or 3600) - 30, 60))
        integ.account_email = str(perfil.get("email") or "").strip().lower() or None
        integ.calendar_id = integ.calendar_id or ORGANIZA_GOOGLE_CALENDAR_ID
        integ.scopes = str(data.get("scope") or " ".join(GOOGLE_OAUTH_SCOPES))
        db.commit()
        return RedirectResponse("/organiza/atualizacoes?mensagem=" + quote_plus("Google Drive e Google Agenda conectados ao Organiza."), status_code=303)
    except Exception as exc:
        return RedirectResponse("/organiza/atualizacoes?erro=" + quote_plus(str(exc)), status_code=303)


@app.post("/organiza/google/desconectar")
def organiza_google_desconectar(usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_admin(usuario)
    integ = _google_integracao(db)
    if integ:
        integ.access_token = None
        integ.refresh_token = None
        integ.expires_at = None
        integ.account_email = None
        db.commit()
    return RedirectResponse("/organiza/atualizacoes?mensagem=" + quote_plus("Conta Google desconectada."), status_code=303)


@app.post("/organiza/clientes/{cliente_id}/atualizacoes/compra")
async def atualizacao_compra_manual(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(404)
    form = dict(await request.form())
    inicio = (form.get("pacote_inicio") or "").strip()
    fim = (form.get("pacote_fim") or inicio).strip()
    pacotes = _atualizacao_pacotes_intervalo(db, inicio, fim)
    if not pacotes:
        return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_erro=" + quote_plus("Intervalo de pacotes inválido."), status_code=303)
    if _atualizacao_compra_cobrindo(db, cliente_id, pacotes):
        return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_erro=" + quote_plus("Esses pacotes já constam como comprados para este cliente."), status_code=303)
    valor = moeda_num(form.get("valor_pago") or "0")
    normal = moeda_num(form.get("valor_normal") or "0")
    compra = _atualizacao_registrar_compra(
        db, cliente, pacotes, origem="MANUAL", valor_normal_centavos=int(round(normal * 100)),
        valor_pago_centavos=int(round(valor * 100)), forma_pagamento=(form.get("forma_pagamento") or "PIX direto").strip(),
    )
    return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_sucesso=" + quote_plus(f"Compra #{compra.id} registrada. O SolVoz não cobrará novamente esses pacotes."), status_code=303)


@app.post("/organiza/clientes/{cliente_id}/atualizacoes/{compra_id}/enviar-casa")
def atualizacao_admin_enviar_casa(cliente_id: int, compra_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.get(Cliente, cliente_id)
    compra = db.query(AtualizacaoCompra).filter(AtualizacaoCompra.id == compra_id, AtualizacaoCompra.cliente_id == cliente_id, AtualizacaoCompra.status == "PAGO").first()
    if not cliente or not compra:
        raise HTTPException(404)
    try:
        _atualizacao_enviar_email_casa(db, cliente, compra)
        return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_sucesso=" + quote_plus("Arquivos liberados no Gmail e e-mail enviado."), status_code=303)
    except Exception as exc:
        compra.email_erro = str(exc)[:1200]
        db.commit()
        return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_erro=" + quote_plus(str(exc)), status_code=303)


@app.post("/organiza/clientes/{cliente_id}/atualizacoes/{compra_id}/agendar")
async def atualizacao_admin_agendar(cliente_id: int, compra_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.get(Cliente, cliente_id)
    compra = db.query(AtualizacaoCompra).filter(AtualizacaoCompra.id == compra_id, AtualizacaoCompra.cliente_id == cliente_id, AtualizacaoCompra.status == "PAGO").first()
    if not cliente or not compra:
        raise HTTPException(404)
    form = dict(await request.form())
    tipo = (form.get("tipo") or "LOJA").strip().upper()
    momento = datetime_form(form.get("data_hora") or "")
    ag = db.query(AtualizacaoAgendamento).filter(AtualizacaoAgendamento.compra_id == compra.id).first()
    if not _atualizacao_horario_valido(tipo, momento):
        horario = "10:00 às 20:00" if tipo == "CASA" else "14:00 às 18:00"
        return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_erro=" + quote_plus(f"Horário inválido. {tipo.title()}: segunda a sexta, {horario}, de 1 em 1 hora."), status_code=303)
    if _atualizacao_horario_ocupado(db, momento, ag.id if ag else 0):
        return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_erro=" + quote_plus("Esse horário conflita com outro compromisso do Organiza."), status_code=303)
    if not ag:
        ag = AtualizacaoAgendamento(compra_id=compra.id, cliente_id=cliente.id)
        db.add(ag)
    ag.tipo = tipo
    ag.data_hora = momento
    ag.duracao_minutos = ATUALIZACAO_DURACAO_MINUTOS
    ag.status = "RESERVADO"
    _google_calendar_sincronizar(db, ag, cliente, compra)
    db.commit()
    try:
        _atualizacao_enviar_email_agendamento(db, cliente, compra, ag)
    except Exception as exc:
        ag.google_sync_erro = ((ag.google_sync_erro or "") + f" | E-mail: {exc}")[:1200]
        db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_sucesso=" + quote_plus("Atendimento reservado na Agenda do Organiza."), status_code=303)


@app.post("/organiza/clientes/{cliente_id}/atualizacoes/{compra_id}/cancelar-agendamento")
def atualizacao_admin_cancelar_agendamento(cliente_id: int, compra_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    ag = db.query(AtualizacaoAgendamento).filter(AtualizacaoAgendamento.compra_id == compra_id, AtualizacaoAgendamento.cliente_id == cliente_id).first()
    if not ag:
        return RedirectResponse(f"/organiza/clientes/{cliente_id}", status_code=303)
    _google_calendar_excluir(db, ag)
    ag.status = "CANCELADO"
    db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_sucesso=" + quote_plus("Agendamento cancelado."), status_code=303)


@app.get("/atualizacao/{token}/{compra_id}", response_class=HTMLResponse)
def atualizacao_publica_fluxo(token: str, compra_id: int, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    compra = db.query(AtualizacaoCompra).filter(AtualizacaoCompra.id == compra_id, AtualizacaoCompra.status == "PAGO").first()
    if not cliente or not compra or compra.cliente_id != cliente.id:
        raise HTTPException(404)
    ag = db.query(AtualizacaoAgendamento).filter(AtualizacaoAgendamento.compra_id == compra.id, AtualizacaoAgendamento.status == "RESERVADO").first()
    return templates.TemplateResponse("organiza/atualizacao_publica.html", {
        "request": request, "cliente": cliente, "compra": compra, "pacotes": _atualizacao_pacotes_lista(compra.pacotes),
        "gmail_ok": _gmail_valido(cliente.email), "cadastro_url": _atualizacao_cadastro_url(cliente, compra), "agendamento": ag,
        "mensagem": request.query_params.get("mensagem", ""), "erro": request.query_params.get("erro", ""),
    }, headers={"Cache-Control": "private, no-store"})


@app.post("/atualizacao/{token}/{compra_id}/modo")
async def atualizacao_publica_modo(token: str, compra_id: int, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    compra = db.query(AtualizacaoCompra).filter(AtualizacaoCompra.id == compra_id, AtualizacaoCompra.status == "PAGO").first()
    if not cliente or not compra or compra.cliente_id != cliente.id:
        raise HTTPException(404)
    if not _gmail_valido(cliente.email):
        return RedirectResponse(_atualizacao_cadastro_url(cliente, compra), status_code=303)
    form = dict(await request.form())
    modo = (form.get("modo") or "").strip().upper()
    if modo == "LOJA":
        return RedirectResponse(f"/atualizacao/{token}/{compra.id}/agenda?tipo=LOJA", status_code=303)
    if modo != "CASA":
        return RedirectResponse(_atualizacao_fluxo_url(cliente, compra) + "?erro=" + quote_plus("Escolha como deseja realizar a atualização."), status_code=303)
    try:
        _atualizacao_enviar_email_casa(db, cliente, compra)
        return RedirectResponse(_atualizacao_fluxo_url(cliente, compra) + "?mensagem=" + quote_plus("Arquivos liberados. Abra o e-mail no PC, baixe tudo e depois use o link de agendamento."), status_code=303)
    except Exception as exc:
        compra.email_erro = str(exc)[:1200]
        db.commit()
        return RedirectResponse(_atualizacao_fluxo_url(cliente, compra) + "?erro=" + quote_plus(str(exc)), status_code=303)


@app.get("/atualizacao/{token}/{compra_id}/agenda", response_class=HTMLResponse)
def atualizacao_publica_agenda(token: str, compra_id: int, request: Request, tipo: str = "LOJA", db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    compra = db.query(AtualizacaoCompra).filter(AtualizacaoCompra.id == compra_id, AtualizacaoCompra.status == "PAGO").first()
    tipo = (tipo or "LOJA").strip().upper()
    if not cliente or not compra or compra.cliente_id != cliente.id or tipo not in ATUALIZACAO_HORARIOS:
        raise HTTPException(404)
    if tipo == "CASA" and not compra.email_arquivos_enviado_em:
        return RedirectResponse(_atualizacao_fluxo_url(cliente, compra) + "?erro=" + quote_plus("Primeiro receba e baixe os arquivos no computador. Depois faça o agendamento."), status_code=303)
    ag = db.query(AtualizacaoAgendamento).filter(AtualizacaoAgendamento.compra_id == compra.id, AtualizacaoAgendamento.status == "RESERVADO").first()
    return templates.TemplateResponse("organiza/atualizacao_agenda_publica.html", {
        "request": request, "cliente": cliente, "compra": compra, "tipo": tipo, "agendamento": ag,
        "hoje": date.today().isoformat(), "erro": request.query_params.get("erro", ""), "mensagem": request.query_params.get("mensagem", ""),
    }, headers={"Cache-Control": "private, no-store"})


@app.get("/atualizacao/{token}/{compra_id}/horarios")
def atualizacao_publica_horarios(token: str, compra_id: int, tipo: str, data: str, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    compra = db.query(AtualizacaoCompra).filter(AtualizacaoCompra.id == compra_id, AtualizacaoCompra.status == "PAGO").first()
    if not cliente or not compra or compra.cliente_id != cliente.id:
        raise HTTPException(404)
    tipo = (tipo or "").strip().upper()
    try:
        dia = datetime.strptime(data, "%Y-%m-%d").date()
    except Exception:
        return JSONResponse({"horarios": []})
    if dia < date.today() or tipo not in ATUALIZACAO_HORARIOS:
        return JSONResponse({"horarios": []})
    return JSONResponse({"horarios": _atualizacao_horarios_disponiveis(db, tipo, dia)})


@app.post("/atualizacao/{token}/{compra_id}/agenda")
async def atualizacao_publica_agenda_salvar(token: str, compra_id: int, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    compra = db.query(AtualizacaoCompra).filter(AtualizacaoCompra.id == compra_id, AtualizacaoCompra.status == "PAGO").first()
    if not cliente or not compra or compra.cliente_id != cliente.id:
        raise HTTPException(404)
    form = dict(await request.form())
    tipo = (form.get("tipo") or "LOJA").strip().upper()
    if tipo == "CASA" and not compra.email_arquivos_enviado_em:
        return RedirectResponse(_atualizacao_fluxo_url(cliente, compra) + "?erro=" + quote_plus("Baixe os arquivos antes de marcar o AnyDesk."), status_code=303)
    try:
        momento = datetime.strptime(f"{form.get('data') or ''} {form.get('hora') or ''}", "%Y-%m-%d %H:%M")
    except Exception:
        momento = None
    ag = db.query(AtualizacaoAgendamento).filter(AtualizacaoAgendamento.compra_id == compra.id).first()
    if not _atualizacao_horario_valido(tipo, momento):
        return RedirectResponse(f"/atualizacao/{token}/{compra.id}/agenda?tipo={tipo}&erro=" + quote_plus("Escolha um dia útil e um horário disponível."), status_code=303)
    if _atualizacao_horario_ocupado(db, momento, ag.id if ag else 0):
        return RedirectResponse(f"/atualizacao/{token}/{compra.id}/agenda?tipo={tipo}&erro=" + quote_plus("Esse horário acabou de ser reservado. Escolha outro."), status_code=303)
    if not ag:
        ag = AtualizacaoAgendamento(compra_id=compra.id, cliente_id=cliente.id)
        db.add(ag)
    ag.tipo = tipo
    ag.data_hora = momento
    ag.duracao_minutos = ATUALIZACAO_DURACAO_MINUTOS
    ag.status = "RESERVADO"
    _google_calendar_sincronizar(db, ag, cliente, compra)
    db.commit()
    email_erro = ""
    try:
        _atualizacao_enviar_email_agendamento(db, cliente, compra, ag)
    except Exception as exc:
        email_erro = str(exc)
    msg = "Horário reservado com sucesso."
    if email_erro:
        msg += " A reserva foi salva, mas a confirmação por e-mail não pôde ser enviada."
    return RedirectResponse(_atualizacao_fluxo_url(cliente, compra) + "?mensagem=" + quote_plus(msg), status_code=303)


@app.post("/api/integracoes/solvoz/email/recuperacao")
async def api_solvoz_email_recuperacao(
    request: Request,
    x_solvoz_token: Optional[str] = Header(default=None, alias="X-SolVoz-Token"),
):
    """Entrega pelo Resend do Organiza um reset cuja credencial pertence ao SolVoz."""
    _validar_token_solvoz(x_solvoz_token)
    try:
        if "application/json" in (request.headers.get("content-type") or "").lower():
            data = await request.json()
        else:
            data = dict(await request.form())
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    email = str(data.get("email") or "").strip().lower()
    nome = str(data.get("nome") or "cliente").strip()
    empresa_nome = str(data.get("empresa_nome") or "SolVoz").strip()
    link = str(data.get("link") or "").strip()
    try:
        validade = max(1, min(120, int(data.get("validade_minutos") or 30)))
    except Exception:
        validade = 30

    if not email or "@" not in email:
        raise HTTPException(400, "E-mail inválido.")
    if not link:
        raise HTTPException(400, "Link de recuperação ausente.")

    # Impede que a rota privada vire um relay para links externos.
    base = urllib.parse.urlparse(SOLVOZ_BASE_URL)
    alvo = urllib.parse.urlparse(link)
    if alvo.scheme.lower() != "https" or alvo.netloc.lower() != base.netloc.lower():
        raise HTTPException(400, "Link de recuperação fora do domínio SolVoz configurado.")

    try:
        enviar_email_solvoz_recuperacao(
            email,
            nome,
            empresa_nome,
            link,
            validade_minutos=validade,
        )
    except Exception as exc:
        raise HTTPException(502, f"Falha ao enviar e-mail pelo Resend do Organiza: {str(exc)[:300]}")
    return {"ok": True, "email_enviado": True, "responsavel": "ORGANIZA"}


@app.post("/api/integracoes/solvoz/empresas")
def api_solvoz_empresa_criar(
    nome: str = Form(...),
    slug: str = Form(...),
    x_solvoz_token: Optional[str] = Header(default=None, alias="X-SolVoz-Token"),
    db: Session = Depends(get_db),
):
    """Cria/garante no Organiza a empresa clonada no SolVoz (idempotente)."""
    _validar_token_solvoz(x_solvoz_token)
    nome_n = (nome or "").strip()
    slug_n = normalizar_slug_solvoz(slug or nome_n)
    if not nome_n or not slug_n:
        raise HTTPException(400, "Nome e slug são obrigatórios.")
    empresa = db.query(SolVozEmpresa).filter(SolVozEmpresa.slug == slug_n).first()
    criada = False
    if not empresa:
        empresa = SolVozEmpresa(
            nome=nome_n,
            slug=slug_n,
            dominio=dominio_solvoz_por_slug(slug_n),
            ativo=1,
        )
        db.add(empresa)
        db.flush()
        criada = True
    else:
        empresa.nome = nome_n
        empresa.dominio = dominio_solvoz_por_slug(slug_n)
        # Preserve o status existente; esta integração não reativa decisão manual.
    h_empresa = garantir_empresa_solvoz_humiat(db, nome_n, slug_n, ativo=1)
    db.commit()
    return {
        "ok": True,
        "criada": criada,
        "empresa_id": empresa.id,
        "humiat_empresa_id": h_empresa.id,
        "nome": empresa.nome,
        "slug": empresa.slug,
        "dominio": empresa.dominio,
    }


@app.get("/api/integracoes/solvoz/maquinas")
def api_solvoz_maquinas(
    empresa: str = "",
    x_solvoz_token: Optional[str] = Header(default=None, alias="X-SolVoz-Token"),
    db: Session = Depends(get_db),
):
    """Snapshot das máquinas implantadas para futura sincronização com o SolVoz.

    O uso em tempo real deve acontecer no banco do SolVoz; esta rota é de implantação/sincronização.
    """
    _validar_token_solvoz(x_solvoz_token)
    consulta = (
        db.query(Equipamento)
        .options(selectinload(Equipamento.solvoz_empresa), selectinload(Equipamento.cliente))
        .filter(
            Equipamento.catalogo_online == 1,
            Equipamento.solvoz_empresa_id.isnot(None),
        )
    )
    slug = normalizar_slug_solvoz(empresa)
    if slug:
        consulta = consulta.join(SolVozEmpresa).filter(SolVozEmpresa.slug == slug)
    equipamentos = consulta.order_by(Equipamento.maquina.asc()).all()
    return {
        "total": len(equipamentos),
        "maquinas": [
            {
                "codigo": (eq.maquina or "").upper(),
                "numero_hd": (eq.numero_hd or "").upper(),
                "identificacao": rotulo_maquina(eq),
                "empresa": eq.solvoz_empresa.slug if eq.solvoz_empresa else None,
                "empresa_nome": eq.solvoz_empresa.nome if eq.solvoz_empresa else None,
                "dominio": eq.solvoz_empresa.dominio if eq.solvoz_empresa else None,
                "plano": normalizar_plano_qr(eq.plano),
                "pacote": eq.pacote,
                "catalogo_online": bool(eq.catalogo_online),
                "status": eq.status,
                "cliente_id": eq.cliente_id,
                "cliente_nome": eq.cliente.nome if eq.cliente else None,
                "atualizado_em": datetime.now().isoformat(timespec="seconds"),
            }
            for eq in equipamentos
        ],
    }


PASTA_LICENCAS = Path(os.getenv("PASTA_LICENCAS", Path(__file__).resolve().parent / "licencas_geradas"))


def normalizar_plano_qr(plano: Optional[str]) -> str:
    valor = unicodedata.normalize("NFKD", (plano or "PLUS").upper()).encode("ascii", "ignore").decode()
    return "BASICO" if valor == "BASICO" else "PLUS"


def validar_dados_licenca(equipamento: Equipamento, db: Session):
    """Mantém as validações existentes e acrescenta somente o vínculo SolVoz."""
    maquina = re.sub(r"[^A-Z0-9]", "", (equipamento.maquina or "").upper())
    numero_hd = re.sub(r"[^A-Z0-9]", "", (equipamento.numero_hd or "").upper())
    if not re.fullmatch(r"KRJ\d{5}", maquina):
        raise HTTPException(400, "O número da máquina deve seguir o padrão KRJ00040.")
    if not numero_hd.startswith("KRJHD"):
        raise HTTPException(400, "O campo NR HD deve conter o código completo iniciado por KRJHD.")

    empresa = None
    if equipamento.solvoz_empresa_id:
        empresa = db.query(SolVozEmpresa).filter(
            SolVozEmpresa.id == equipamento.solvoz_empresa_id,
            SolVozEmpresa.ativo == 1,
        ).first()
    if not empresa:
        raise HTTPException(400, "Selecione a Empresa SolVoz antes de gerar a licença e o QR Code.")

    plano = normalizar_plano_qr(equipamento.plano)
    if not equipamento.plano:
        raise HTTPException(400, "Selecione o plano do equipamento antes de gerar a licença e o QR Code.")
    return maquina, numero_hd, plano, empresa


def criar_arte_qr(maquina: str, plano: str, url: str, destino: Path):
    """Reproduz a arte do gerador AU3: 900x1100, textos e QR centralizado."""
    try:
        import qrcode
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise HTTPException(
            500,
            "Dependências do QR ausentes. Execute: pip install qrcode[pil] Pillow"
        ) from exc

    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=12, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((620, 620))

    arte = Image.new("RGB", (900, 1100), "white")
    arte.paste(qr_img, (140, 410))
    desenho = ImageDraw.Draw(arte)

    def fonte(tamanho: int, negrito: bool = False):
        candidatos = [
            "C:/Windows/Fonts/arialbd.ttf" if negrito else "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if negrito else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for caminho in candidatos:
            if Path(caminho).exists():
                return ImageFont.truetype(caminho, tamanho)
        return ImageFont.load_default()

    def central(texto: str, y: int, tamanho: int, negrito: bool = True):
        f = fonte(tamanho, negrito)
        caixa = desenho.textbbox((0, 0), texto, font=f)
        x = (900 - (caixa[2] - caixa[0])) // 2
        desenho.text((x, y), texto, fill="black", font=f)

    central("Escaneie para enviar musicas", 70, 42)
    central("Equipamento:", 190, 34)
    central(maquina, 255, 50)
    central(f"Catalogo: {plano}", 335, 30)
    arte.save(destino, "PNG")


def criar_qr_id_equipamento(maquina: str, url: str, destino: Path):
    """Gera arte 200x220 sem margens extras: QR apontando para a URL e codigo da maquina abaixo."""
    try:
        import qrcode
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise HTTPException(
            500,
            "Dependencia do QR ausente. Execute: pip install qrcode[pil] Pillow"
        ) from exc

    largura_arte = 200
    altura_arte = 220
    altura_qr = 200
    altura_codigo = 20

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=1,
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    resample_nearest = getattr(Image, "Resampling", Image).NEAREST
    qr_img = qr_img.resize((largura_arte, altura_qr), resample=resample_nearest)

    arte = Image.new("RGB", (largura_arte, altura_arte), "white")
    arte.paste(qr_img, (0, 0))
    desenho = ImageDraw.Draw(arte)

    def fonte_codigo(tamanho: int):
        candidatos = [
            "C:/Windows/Fonts/arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        for caminho in candidatos:
            if Path(caminho).exists():
                return ImageFont.truetype(caminho, tamanho)
        return ImageFont.load_default()

    tamanho_fonte = 14
    while tamanho_fonte > 8:
        f = fonte_codigo(tamanho_fonte)
        caixa = desenho.textbbox((0, 0), maquina, font=f)
        largura_texto = caixa[2] - caixa[0]
        altura_texto = caixa[3] - caixa[1]
        if largura_texto <= largura_arte - 2 and altura_texto <= altura_codigo:
            break
        tamanho_fonte -= 1
    x_texto = (largura_arte - largura_texto) // 2
    y_texto = altura_qr + max(0, (altura_codigo - altura_texto) // 2) - caixa[1]
    desenho.text((x_texto, y_texto), maquina, fill="black", font=f)

    arte.save(destino, "PNG")

def validar_dados_qr(equipamento: Equipamento, db: Session):
    """Valida somente os dados necessários para gerar os QR Codes antes da licença."""
    maquina = re.sub(r"[^A-Z0-9]", "", (equipamento.maquina or "").upper())
    if not re.fullmatch(r"KRJ\d{5}", maquina):
        raise HTTPException(400, "O número da máquina deve seguir o padrão KRJ00040.")

    empresa = None
    if equipamento.solvoz_empresa_id:
        empresa = db.query(SolVozEmpresa).filter(
            SolVozEmpresa.id == equipamento.solvoz_empresa_id,
            SolVozEmpresa.ativo == 1,
        ).first()
    if not empresa:
        raise HTTPException(400, "Selecione a Empresa SolVoz antes de gerar o QR Code.")

    plano = normalizar_plano_qr(equipamento.plano)
    if not equipamento.plano:
        raise HTTPException(400, "Selecione o plano do equipamento antes de gerar o QR Code.")
    return maquina, plano, empresa


def gerar_pacote_qr(equipamento: Equipamento, db: Session) -> Path:
    """Gera os QR Codes antes da licença, sem exigir NR HD."""
    maquina, plano, empresa = validar_dados_qr(equipamento, db)
    url_qr = montar_url_qr_solvoz(
        empresa,
        maquina,
        plano,
        bool(equipamento.catalogo_online),
    )

    pasta = PASTA_LICENCAS / maquina
    pasta.mkdir(parents=True, exist_ok=True)

    qr_catalogo = pasta / f"QR_{maquina}_{plano}.png"
    criar_arte_qr(maquina, plano, url_qr, qr_catalogo)

    qr_id = pasta / f"QR_ID_{maquina}_500x500.png"
    criar_qr_id_equipamento(maquina, url_qr, qr_id)

    url_catalogo = pasta / "URL_CATALOGO.txt"
    url_catalogo.write_text(url_qr, encoding="utf-8")

    zip_path = PASTA_LICENCAS / f"{maquina}_QR.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as pacote:
        for arquivo in [url_catalogo, qr_catalogo, qr_id]:
            pacote.write(arquivo, arcname=f"{maquina}/{arquivo.name}")
    return zip_path


def gerar_pasta_licenca(equipamento: Equipamento, db: Session) -> tuple[Path, Path]:
    maquina, numero_hd, plano, empresa = validar_dados_licenca(equipamento, db)
    url_qr = montar_url_qr_solvoz(
        empresa,
        maquina,
        plano,
        bool(equipamento.catalogo_online),
    )
    pasta = PASTA_LICENCAS / maquina
    pasta.mkdir(parents=True, exist_ok=True)

    (pasta / "MAQUINA_KRJ.txt").write_text(maquina, encoding="utf-8")
    (pasta / "LICENCA_HD_KRJ.txt").write_text(numero_hd, encoding="utf-8")
    png = pasta / f"QR_{maquina}_{plano}.png"
    criar_arte_qr(maquina, plano, url_qr, png)
    qr_id = pasta / f"QR_ID_{maquina}_500x500.png"
    criar_qr_id_equipamento(maquina, url_qr, qr_id)
    (pasta / "URL_CATALOGO.txt").write_text(url_qr, encoding="utf-8")

    zip_path = PASTA_LICENCAS / f"{maquina}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as pacote:
        for arquivo in [pasta / "MAQUINA_KRJ.txt", pasta / "LICENCA_HD_KRJ.txt", pasta / "URL_CATALOGO.txt", png, qr_id]:
            pacote.write(arquivo, arcname=f"{maquina}/{arquivo.name}")
    return pasta, zip_path


@app.post("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/gerar-qr")
async def equipamento_gerar_qr(cliente_id: int, equipamento_id: int, request: Request,
                               usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).filter(
        Equipamento.id == equipamento_id,
        Equipamento.cliente_id == cliente_id
    ).first()
    if not eq:
        raise HTTPException(404)

    # Salva as alterações feitas na ficha antes de gerar o QR, igual ao fluxo da licença.
    form = dict(await request.form())
    preencher_equipamento(eq, form, db)
    garantir_identificacao_equipamento(db, eq)
    db.flush()
    _sincronizar_pacote_cliente(db, cliente_id)
    db.commit()

    zip_path = gerar_pacote_qr(eq, db)
    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=zip_path.name,
        headers={"X-Pasta-Gerada": str(PASTA_LICENCAS / (eq.maquina or ""))}
    )


@app.post("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/gerar-licenca")
async def equipamento_gerar_licenca(cliente_id: int, equipamento_id: int, request: Request,
                                    usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).filter(
        Equipamento.id == equipamento_id,
        Equipamento.cliente_id == cliente_id
    ).first()
    if not eq:
        raise HTTPException(404)

    # O mesmo botão salva as correções feitas nos campos antes de gerar.
    form = dict(await request.form())
    preencher_equipamento(eq, form, db)
    garantir_identificacao_equipamento(db, eq)
    db.flush()
    _sincronizar_pacote_cliente(db, cliente_id)
    db.commit()
    _, zip_path = gerar_pasta_licenca(eq, db)
    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=zip_path.name,
        headers={"X-Pasta-Gerada": str(PASTA_LICENCAS / (eq.maquina or ""))}
    )


@app.post("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/transferir")
async def equipamento_transferir(cliente_id: int, equipamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id, Equipamento.cliente_id == cliente_id).first()
    if not eq:
        raise HTTPException(404)
    form = dict(await request.form())
    destino_id = int(form.get("cliente_destino_id") or 0)
    destino = db.query(Cliente).filter(Cliente.id == destino_id).first()
    if not destino or destino_id == cliente_id:
        return RedirectResponse(f"/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/editar?erro_transferencia=Selecione outro cliente", status_code=303)
    manutencao_aberta = db.query(Manutencao).filter(
        Manutencao.equipamento_id == equipamento_id,
        Manutencao.entregue_em.is_(None),
        Manutencao.status.notin_(("Encerrada", "Cancelada", "Entregue")),
    ).first()
    if manutencao_aberta:
        return RedirectResponse(f"/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/editar?erro_transferencia=Existe manutenção aberta para este equipamento", status_code=303)
    db.add(TransferenciaEquipamento(
        equipamento_id=eq.id, cliente_origem_id=cliente_id, cliente_destino_id=destino_id,
        observacao=(form.get("observacao_transferencia") or "").strip() or None,
    ))
    eq.cliente_id = destino_id
    db.flush()
    reordenar_series_cliente(db, cliente_id)
    reordenar_series_cliente(db, destino_id)
    _sincronizar_pacote_cliente(db, cliente_id)
    _sincronizar_pacote_cliente(db, destino_id)
    db.commit()
    return RedirectResponse(f"/organiza/clientes/{destino_id}/equipamentos/{equipamento_id}/editar?transferido=1", status_code=303)


# ---------------------------------------------------------
# VENDAS SIMPLES
# Uma venda cria (ou reutiliza) o cliente e já cadastra o equipamento.
# ---------------------------------------------------------

STATUS_VENDA = ("Solicitar gabinete", "Montagem", "Pronto para entrega", "Entregue")


def equipamento_eh_venda(eq: Equipamento) -> bool:
    return bool(eq.data_compra or eq.previsao_entrega or eq.valor or eq.pago or eq.status in STATUS_VENDA)


def _migrar_pagamentos_legados_vendas(db: Session, equipamentos: list[Equipamento]) -> int:
    """Converte recebimentos legados sem executar uma consulta por equipamento."""
    candidatos = [eq for eq in equipamentos if round(moeda_num(eq.pago), 2) > 0.009]
    if not candidatos:
        return 0

    ids = [eq.id for eq in candidatos]
    existentes = {
        equipamento_id for (equipamento_id,) in
        db.query(PagamentoVenda.equipamento_id)
        .filter(PagamentoVenda.equipamento_id.in_(ids))
        .distinct().all()
    }
    alterados = 0
    for eq in candidatos:
        if eq.id in existentes:
            continue
        pagamento = PagamentoVenda(
            equipamento_id=eq.id,
            data=eq.data_compra or eq.previsao_entrega or date.today(),
            valor=round(moeda_num(eq.pago), 2),
            banco="Histórico",
            forma="Histórico",
            observacao=_obs_pagamento_padrao(eq, eq.cliente, "Saldo recebido antes do controle detalhado"),
        )
        db.add(pagamento)
        db.flush()
        db.add(IntegracaoConect(
            origem="venda", registro_id=pagamento.id,
            id_externo=f"ORGANIZA-VENDA-PAG-{pagamento.id}", ignorado=1,
            resposta="Pagamento histórico migrado do campo legado pago; não enviar ao Connect.",
        ))
        alterados += 1
    if alterados:
        db.commit()
    return alterados



CAMPANHA_ENVIO_CONFIRMADO = {"PROCESSADO", "ENVIADO"}


def _campanhas_atualizacao_para_vendas(db: Session) -> list[Campanha]:
    """Campanhas de atualização disponíveis para conferência na tela de Vendas."""
    return (
        db.query(Campanha)
        .filter(func.upper(Campanha.lista_tipo) == "ATUALIZACAO")
        .order_by(Campanha.criado_em.desc(), Campanha.id.desc())
        .all()
    )


def _rotulo_status_envio_campanha(status: str | None, tem_destinatario: bool = True) -> str:
    if not tem_destinatario:
        return "Fora da campanha"
    status = (status or "PENDENTE").upper()
    if status in CAMPANHA_ENVIO_CONFIRMADO:
        return "Enviado pelo fluxo"
    if status == "IGNORADO":
        return "Pulado / não enviado"
    if status == "EM_ENVIO":
        return "Em envio / não confirmado"
    return "Pendente / não enviado"


def _vendas_filtradas(request: Request, db: Session) -> dict:
    """Carrega as vendas e aplica exatamente os mesmos cálculos e filtros da tela e do relatório."""
    equipamentos = (
        db.query(Equipamento)
        .options(selectinload(Equipamento.cliente))
        .filter(or_(
            Equipamento.data_compra.isnot(None),
            Equipamento.previsao_entrega.isnot(None),
            Equipamento.valor.isnot(None),
            Equipamento.pago.isnot(None),
            Equipamento.status.in_(STATUS_VENDA),
        ))
        .all()
    )
    equipamentos = [eq for eq in equipamentos if equipamento_eh_venda(eq)]
    status_opcoes = sorted({eq.status for eq in equipamentos if eq.status})
    campanhas_atualizacao = _campanhas_atualizacao_para_vendas(db)
    campanha_id = 0
    try:
        campanha_id = int(request.query_params.get("campanha_id") or 0)
    except (TypeError, ValueError):
        campanha_id = 0
    campanha_selecionada = next((c for c in campanhas_atualizacao if int(c.id) == campanha_id), None)
    if not campanha_selecionada and campanhas_atualizacao:
        campanha_selecionada = campanhas_atualizacao[0]
        campanha_id = int(campanha_selecionada.id)
    campanha_envio = (request.query_params.get("campanha_envio") or "todos").strip().lower()
    if campanha_envio not in {"todos", "nao_enviado", "enviado", "fora"}:
        campanha_envio = "todos"

    # Sincroniza automaticamente vendas antigas ou recém-cadastradas em que o
    # valor recebido ainda existe apenas no campo legado `pago`.
    # Depois disso, tela e relatório usam a mesma fonte: PagamentoVenda.
    _migrar_pagamentos_legados_vendas(db, equipamentos)

    ids = [eq.id for eq in equipamentos]
    pagamentos_por_equipamento = {}
    if ids:
        for p in db.query(PagamentoVenda).filter(PagamentoVenda.equipamento_id.in_(ids)).all():
            pagamentos_por_equipamento.setdefault(p.equipamento_id, 0.0)
            pagamentos_por_equipamento[p.equipamento_id] += float(p.valor or 0)

    for eq in equipamentos:
        total = moeda_num(eq.valor)
        recebido = round(pagamentos_por_equipamento.get(eq.id, 0.0), 2)
        eq.total_calculado = total
        eq.recebido_calculado = recebido
        eq.falta_calculada = max(round(total - recebido, 2), 0)
        eq.excesso_calculado = max(round(recebido - total, 2), 0)

    q = (request.query_params.get("q") or "").strip().lower()
    pagamento = (request.query_params.get("pagamento") or "todos").strip()
    valor_filtro = (request.query_params.get("valor") or "todos").strip()
    status_filtros = [
        valor.strip()
        for valor in request.query_params.getlist("status")
        if valor and valor.strip()
    ]
    status_filtros = [valor for valor in status_filtros if valor in status_opcoes]
    ordem = (request.query_params.get("ordem") or "recentes").strip()

    if q:
        equipamentos = [eq for eq in equipamentos if q in " ".join([
            eq.cliente.nome or "", eq.tipo or "", eq.modelo or "",
            codigo_tecnico(eq), rotulo_maquina(eq),
        ]).lower()]

    if pagamento == "pendente":
        equipamentos = [eq for eq in equipamentos if eq.falta_calculada > 0.009]
    elif pagamento == "quitado":
        equipamentos = [
            eq for eq in equipamentos
            if eq.total_calculado > 0
            and eq.falta_calculada <= 0.009
            and eq.excesso_calculado <= 0.009
        ]
    elif pagamento == "sem_pagamento":
        equipamentos = [eq for eq in equipamentos if eq.recebido_calculado <= 0.009]
    elif pagamento == "excesso":
        equipamentos = [eq for eq in equipamentos if eq.excesso_calculado > 0.009]

    if valor_filtro == "acima_5000":
        equipamentos = [eq for eq in equipamentos if eq.total_calculado > 5000]

    if status_filtros:
        status_selecionados = set(status_filtros)
        equipamentos = [eq for eq in equipamentos if (eq.status or "") in status_selecionados]

    # Situação real registrada pela campanha escolhida. PROCESSADO/ENVIADO
    # significam que o fluxo do Organiza foi acionado; o WhatsApp não fornece
    # confirmação de entrega ao Organiza.
    destinatarios_por_cliente = {}
    if campanha_selecionada and equipamentos:
        cliente_ids = sorted({int(eq.cliente_id) for eq in equipamentos if eq.cliente_id})
        if cliente_ids:
            destinos = db.query(CampanhaDestinatario).filter(
                CampanhaDestinatario.campanha_id == campanha_selecionada.id,
                CampanhaDestinatario.cliente_id.in_(cliente_ids),
            ).all()
            destinatarios_por_cliente = {int(d.cliente_id): d for d in destinos}

    for eq in equipamentos:
        dest = destinatarios_por_cliente.get(int(eq.cliente_id or 0))
        eq.campanha_destinatario_id = int(dest.id) if dest else 0
        eq.campanha_status = (dest.status or "PENDENTE") if dest else "FORA"
        eq.campanha_status_rotulo = _rotulo_status_envio_campanha(dest.status if dest else None, bool(dest))
        eq.campanha_enviado_em = dest.enviado_em if dest else None
        eq.campanha_foi_enviado = bool(dest and (dest.status or "").upper() in CAMPANHA_ENVIO_CONFIRMADO)

    if campanha_selecionada and campanha_envio == "nao_enviado":
        equipamentos = [eq for eq in equipamentos if eq.campanha_destinatario_id and not eq.campanha_foi_enviado]
    elif campanha_selecionada and campanha_envio == "enviado":
        equipamentos = [eq for eq in equipamentos if eq.campanha_destinatario_id and eq.campanha_foi_enviado]
    elif campanha_selecionada and campanha_envio == "fora":
        equipamentos = [eq for eq in equipamentos if not eq.campanha_destinatario_id]

    if ordem == "antigos":
        equipamentos.sort(key=lambda eq: (eq.data_compra or date.min, eq.criado_em or datetime.min, eq.id))
    elif ordem == "maior_valor":
        equipamentos.sort(
            key=lambda eq: (eq.total_calculado, eq.data_compra or date.min, eq.id),
            reverse=True,
        )
    elif ordem == "maior_saldo":
        equipamentos.sort(
            key=lambda eq: (eq.falta_calculada, eq.data_compra or date.min, eq.id),
            reverse=True,
        )
    else:
        equipamentos.sort(
            key=lambda eq: (eq.data_compra or date.min, eq.criado_em or datetime.min, eq.id),
            reverse=True,
        )

    parametros_filtro = [
        ("q", request.query_params.get("q", "")),
        ("pagamento", pagamento),
        ("valor", valor_filtro),
        *[("status", item) for item in status_filtros],
        ("campanha_id", str(campanha_id or "")),
        ("campanha_envio", campanha_envio),
        ("ordem", ordem),
    ]

    return {
        "equipamentos": equipamentos,
        "q": request.query_params.get("q", ""),
        "pagamento_filtro": pagamento,
        "valor_filtro": valor_filtro,
        "status_filtros": status_filtros,
        "ordem": ordem,
        "status_opcoes": status_opcoes,
        "campanhas_atualizacao": campanhas_atualizacao,
        "campanha_selecionada": campanha_selecionada,
        "campanha_id": campanha_id,
        "campanha_envio_filtro": campanha_envio,
        "filtro_query": urlencode(parametros_filtro),
    }




@app.get("/organiza/nfse/importar-connect")
def nfse_importar_connect(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    q = request.query_params
    empresa_id = (q.get("connect_empresa_id") or "").strip()
    contrato_id = (q.get("connect_contrato_id") or "").strip()
    referencia = f"connect:{empresa_id}:{contrato_id}" if empresa_id and contrato_id else ""
    if referencia:
        existente = db.query(NFSERascunho).filter(
            NFSERascunho.origem == "connect",
            NFSERascunho.referencia_externa == referencia,
        ).order_by(NFSERascunho.id.desc()).first()
        if existente:
            return RedirectResponse(f"/organiza/nfse/{existente.id}?duplicada=1", status_code=303)

    documento = re.sub(r"\D", "", q.get("cliente_documento") or "")
    if len(documento) not in (11, 14):
        raise HTTPException(400, "CPF/CNPJ do contrato inválido")
    cliente = db.query(Cliente).filter(Cliente.documento == documento).order_by(Cliente.id.desc()).first()
    if not cliente:
        nome = (q.get("cliente_nome") or "Cliente Connect").strip() or "Cliente Connect"
        telefone = re.sub(r"\D", "", q.get("cliente_telefone") or "")[-11:] or "00000000000"
        cliente = Cliente(nome=nome, telefone=telefone, documento=documento, pais="BR", ddi="55")
        db.add(cliente)
        db.flush()
    # O contrato é a origem operacional. Atualiza somente dados enviados pelo Connect.
    mapa = {
        "nome": "cliente_nome", "razao_social": "cliente_nome", "email": "cliente_email",
        "cep": "cliente_cep", "endereco": "cliente_logradouro", "endereco_numero": "cliente_numero",
        "complemento": "cliente_complemento", "bairro": "cliente_bairro",
        "municipio": "cliente_municipio", "cidade": "cliente_municipio", "estado": "cliente_uf",
    }
    for campo, parametro in mapa.items():
        valor = (q.get(parametro) or "").strip()
        if valor:
            setattr(cliente, campo, valor)
    telefone = re.sub(r"\D", "", q.get("cliente_telefone") or "")
    if telefone:
        cliente.telefone = telefone[-11:]
    cliente.documento = documento

    data_inicio = data_form(q.get("evento_data_inicio")) or date.today()
    data_fim = data_form(q.get("evento_data_fim")) or data_inicio
    valor_total = max(moeda_num(q.get("valor_total")), 0)
    igual = (q.get("evento_endereco_igual_cliente") or "0") == "1"
    descricao_evento = (q.get("evento_descricao") or "Aluguel de Karaokê").strip() or "Aluguel de Karaokê"
    municipio_evento = (q.get("evento_municipio") or cliente.municipio or cliente.cidade or NFSE_MUNICIPIO_PADRAO).strip()
    uf_evento = (q.get("evento_uf") or cliente.estado or NFSE_UF_PADRAO).strip().upper()[:2]
    nota = NFSERascunho(
        cliente_id=cliente.id, origem="connect", referencia_externa=referencia or None,
        origem_url=str(request.url), competencia=data_inicio, codigo_servico=nfse_codigo_por_tipo(NFSE_TIPO_ALUGUEL),
        municipio_prestacao=municipio_evento, uf_prestacao=uf_evento,
        descricao=nfse_descricao_padrao(NFSE_TIPO_ALUGUEL), valor_total=valor_total,
        evento_data_inicio=data_inicio, evento_data_fim=data_fim, evento_descricao=descricao_evento[:255],
        evento_endereco_igual_cliente=1 if igual else 0, evento_local_tipo="brasil",
        evento_cep=(q.get("evento_cep") or "").strip(),
        evento_logradouro=(q.get("evento_logradouro") or "").strip(),
        evento_numero=(q.get("evento_numero") or "").strip(),
        evento_complemento=(q.get("evento_complemento") or "").strip(),
        evento_bairro=(q.get("evento_bairro") or "").strip(),
        evento_municipio=municipio_evento, evento_uf=uf_evento, status="RASCUNHO"
    )
    db.add(nota)
    db.commit()
    db.refresh(nota)
    return RedirectResponse(f"/organiza/nfse/{nota.id}?connect=1", status_code=303)

@app.get("/organiza/nfse", response_class=HTMLResponse)
def nfse_lista(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    notas = db.query(NFSERascunho).options(selectinload(NFSERascunho.cliente)).order_by(NFSERascunho.id.desc()).limit(300).all()
    return templates.TemplateResponse("organiza/nfse_lista.html", {"request": request, "usuario": usuario, "notas": notas})


@app.get("/organiza/nfse/nova", response_class=HTMLResponse)
def nfse_nova(request: Request, manutencao_id: int = 0, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    clientes = db.query(Cliente).order_by(Cliente.nome.asc()).all()
    dados = {
        "cliente_id": "", "origem": "manual", "manutencao_id": "", "competencia": date.today().isoformat(),
        "tipo_servico": NFSE_TIPO_MANUTENCAO, "municipio_prestacao": NFSE_MUNICIPIO_PADRAO,
        "uf_prestacao": NFSE_UF_PADRAO, "descricao": nfse_descricao_padrao(NFSE_TIPO_MANUTENCAO), "valor_total": "",
        "evento_data_inicio": date.today().isoformat(), "evento_data_fim": date.today().isoformat(),
        "evento_descricao": "Aluguel de Karaokê", "evento_endereco_igual_cliente": 1, "evento_local_tipo": "brasil",
        "evento_identificador": "", "evento_cep": "", "evento_logradouro": "",
        "evento_numero": "", "evento_complemento": "", "evento_bairro": "",
        "evento_municipio": "", "evento_uf": "RJ"
    }
    manutencao = None
    if manutencao_id:
        manutencao = carregar_manutencao(db, manutencao_id)
        if not manutencao: raise HTTPException(404)
        orcamento = sorted(manutencao.orcamentos, key=lambda x: x.versao)[-1] if manutencao.orcamentos else None
        totais = totais_orcamento(orcamento) if orcamento else {}
        dados.update({
            "cliente_id": manutencao.cliente_id, "origem": "manutencao", "manutencao_id": manutencao.id,
            "tipo_servico": NFSE_TIPO_MANUTENCAO,
            "descricao": nfse_descricao_padrao(NFSE_TIPO_MANUTENCAO),
            "valor_total": f"{float(totais.get('aprovado') or 0):.2f}",
        })
    return templates.TemplateResponse("organiza/nfse_form.html", {"request": request, "usuario": usuario, "clientes": clientes, "dados": dados, "manutencao": manutencao, "nota": None})


@app.post("/organiza/nfse/nova")
async def nfse_criar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    cliente_id = int(form.get("cliente_id") or 0)
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(400, "Cliente inválido")
    origem = (form.get("origem") or "manual").strip().lower()
    manutencao_id = int(form.get("manutencao_id") or 0) or None
    if origem != "manutencao": manutencao_id = None
    competencia = data_form(form.get("competencia")) or date.today()
    valor_total = max(moeda_num(form.get("valor_total")), 0)
    tipo_servico = (form.get("tipo_servico") or NFSE_TIPO_MANUTENCAO).strip().lower()
    if tipo_servico not in NFSE_TIPOS_SERVICO:
        tipo_servico = NFSE_TIPO_MANUTENCAO
    # Uma NFS-e criada a partir de Manutenção sempre usa o enquadramento interno de manutenção.
    if origem == "manutencao":
        tipo_servico = NFSE_TIPO_MANUTENCAO
    codigo_servico = nfse_codigo_por_tipo(tipo_servico)
    descricao = (form.get("descricao") or "").strip()
    if not descricao:
        descricao = NFSE_TIPOS_SERVICO[tipo_servico]["descricao_padrao"]
    if not descricao or valor_total <= 0:
        return RedirectResponse(f"/organiza/nfse/nova?manutencao_id={manutencao_id or 0}&erro=1", status_code=303)
    nota = NFSERascunho(
        cliente_id=cliente_id, origem=origem, manutencao_id=manutencao_id, competencia=competencia,
        codigo_servico=codigo_servico,
        municipio_prestacao=(form.get("municipio_prestacao") or NFSE_MUNICIPIO_PADRAO).strip(),
        uf_prestacao=(form.get("uf_prestacao") or NFSE_UF_PADRAO).strip().upper()[:2],
        descricao=descricao, valor_total=valor_total,
        evento_data_inicio=data_form(form.get("evento_data_inicio")) if tipo_servico == NFSE_TIPO_ALUGUEL else None,
        evento_data_fim=data_form(form.get("evento_data_fim")) if tipo_servico == NFSE_TIPO_ALUGUEL else None,
        evento_descricao=(form.get("evento_descricao") or "").strip() if tipo_servico == NFSE_TIPO_ALUGUEL else None,
        evento_endereco_igual_cliente=(1 if form.get("evento_endereco_igual_cliente") else 0) if tipo_servico == NFSE_TIPO_ALUGUEL else 1,
        evento_local_tipo="brasil" if tipo_servico == NFSE_TIPO_ALUGUEL else None,
        evento_identificador=None,
        evento_cep=(form.get("evento_cep") or "").strip() if tipo_servico == NFSE_TIPO_ALUGUEL else None,
        evento_logradouro=(form.get("evento_logradouro") or "").strip() if tipo_servico == NFSE_TIPO_ALUGUEL else None,
        evento_numero=(form.get("evento_numero") or "").strip() if tipo_servico == NFSE_TIPO_ALUGUEL else None,
        evento_complemento=(form.get("evento_complemento") or "").strip() if tipo_servico == NFSE_TIPO_ALUGUEL else None,
        evento_bairro=(form.get("evento_bairro") or "").strip() if tipo_servico == NFSE_TIPO_ALUGUEL else None,
        evento_municipio=(form.get("evento_municipio") or "").strip() if tipo_servico == NFSE_TIPO_ALUGUEL else None,
        evento_uf=(form.get("evento_uf") or "").strip().upper()[:2] if tipo_servico == NFSE_TIPO_ALUGUEL else None,
        status="RASCUNHO"
    )
    db.add(nota); db.commit(); db.refresh(nota)
    return RedirectResponse(f"/organiza/nfse/{nota.id}", status_code=303)


@app.get("/organiza/nfse/{nota_id}", response_class=HTMLResponse)
def nfse_detalhe(nota_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    nota = db.query(NFSERascunho).options(selectinload(NFSERascunho.cliente), selectinload(NFSERascunho.manutencao)).filter(NFSERascunho.id == nota_id).first()
    if not nota: raise HTTPException(404)
    payload = nfse_payload(nota)
    return templates.TemplateResponse("organiza/nfse_detalhe.html", {"request": request, "usuario": usuario, "nota": nota, "payload": payload, "faltantes": nfse_campos_faltantes(payload)})


@app.post("/organiza/nfse/{nota_id}/salvar")
async def nfse_salvar(nota_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    nota = db.query(NFSERascunho).filter(NFSERascunho.id == nota_id).first()
    if not nota: raise HTTPException(404)
    if nota.status == "EMITIDA":
        return RedirectResponse(f"/organiza/nfse/{nota.id}?bloqueada=1", status_code=303)
    form = dict(await request.form())
    nota.competencia = data_form(form.get("competencia")) or nota.competencia
    tipo_servico = (form.get("tipo_servico") or nfse_tipo_por_codigo(nota.codigo_servico)).strip().lower()
    if nota.origem == "manutencao":
        tipo_servico = NFSE_TIPO_MANUTENCAO
    nota.codigo_servico = nfse_codigo_por_tipo(tipo_servico)
    nota.municipio_prestacao = (form.get("municipio_prestacao") or nota.municipio_prestacao or NFSE_MUNICIPIO_PADRAO).strip()
    nota.uf_prestacao = (form.get("uf_prestacao") or nota.uf_prestacao or NFSE_UF_PADRAO).strip().upper()[:2]
    nota.descricao = (form.get("descricao") or nota.descricao or "").strip()
    nota.valor_total = max(moeda_num(form.get("valor_total")), 0)
    if tipo_servico == NFSE_TIPO_ALUGUEL:
        nota.evento_data_inicio = data_form(form.get("evento_data_inicio")) or nota.evento_data_inicio
        nota.evento_data_fim = data_form(form.get("evento_data_fim")) or nota.evento_data_fim
        nota.evento_descricao = (form.get("evento_descricao") or nota.evento_descricao or "Aluguel de Karaokê").strip()
        nota.evento_endereco_igual_cliente = 1 if form.get("evento_endereco_igual_cliente") else 0
        nota.evento_local_tipo = "brasil"
        nota.evento_identificador = None
        nota.evento_cep = (form.get("evento_cep") or "").strip()
        nota.evento_logradouro = (form.get("evento_logradouro") or "").strip()
        nota.evento_numero = (form.get("evento_numero") or "").strip()
        nota.evento_complemento = (form.get("evento_complemento") or "").strip()
        nota.evento_bairro = (form.get("evento_bairro") or "").strip()
        nota.evento_municipio = (form.get("evento_municipio") or "").strip()
        nota.evento_uf = (form.get("evento_uf") or "").strip().upper()[:2]
    db.commit()
    return RedirectResponse(f"/organiza/nfse/{nota.id}?salva=1", status_code=303)


@app.get("/organiza/nfse/{nota_id}/payload")
def nfse_dados_payload(nota_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    nota = db.query(NFSERascunho).options(selectinload(NFSERascunho.cliente)).filter(NFSERascunho.id == nota_id).first()
    if not nota: raise HTTPException(404)
    payload = nfse_payload(nota)
    payload["campos_faltantes"] = nfse_campos_faltantes(payload)
    return JSONResponse(payload)


@app.post("/organiza/nfse/{nota_id}/portal-preparado")
def nfse_portal_preparado(nota_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    nota = db.query(NFSERascunho).filter(NFSERascunho.id == nota_id).first()
    if not nota: raise HTTPException(404)
    if nota.status != "EMITIDA":
        nota.status = "PORTAL_RASCUNHO"
        nota.enviado_portal_em = datetime.now()
        db.commit()
    return {"ok": True}


@app.get("/organiza/vendas", response_class=HTMLResponse)
def vendas(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    dados = _vendas_filtradas(request, db)
    equipamentos = dados["equipamentos"]

    # Paginação: não renderizar centenas de cards em uma única resposta.
    total_vendas = len(equipamentos)
    por_pagina = 50
    try:
        pagina = max(int(request.query_params.get("pagina") or 1), 1)
    except (TypeError, ValueError):
        pagina = 1
    total_paginas = max((total_vendas + por_pagina - 1) // por_pagina, 1)
    pagina = min(pagina, total_paginas)
    inicio = (pagina - 1) * por_pagina
    equipamentos_pagina = equipamentos[inicio:inicio + por_pagina]

    return templates.TemplateResponse("organiza/vendas.html", {
        "request": request,
        "usuario": usuario,
        "vendas": equipamentos_pagina,
        "q": dados["q"],
        "pagamento_filtro": dados["pagamento_filtro"],
        "valor_filtro": dados["valor_filtro"],
        "status_filtros": dados["status_filtros"],
        "ordem": dados["ordem"],
        "status_opcoes": dados["status_opcoes"],
        "campanhas_atualizacao": dados["campanhas_atualizacao"],
        "campanha_selecionada": dados["campanha_selecionada"],
        "campanha_id": dados["campanha_id"],
        "campanha_envio_filtro": dados["campanha_envio_filtro"],
        "total_vendas": total_vendas,
        "pagina": pagina,
        "total_paginas": total_paginas,
        "filtro_query": dados["filtro_query"],
    })


@app.get("/organiza/relatorios/vendas", response_class=HTMLResponse)
def vendas_relatorio(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    dados = _vendas_filtradas(request, db)
    vendas = dados["equipamentos"]

    total_vendido = round(sum(eq.total_calculado for eq in vendas), 2)
    total_recebido = round(sum(eq.recebido_calculado for eq in vendas), 2)
    total_falta = round(sum(eq.falta_calculada for eq in vendas), 2)
    total_excesso = round(sum(eq.excesso_calculado for eq in vendas), 2)

    return templates.TemplateResponse("organiza/vendas_relatorio.html", {
        "request": request,
        "usuario": usuario,
        "titulo": "Relatório de vendas",
        "vendas": vendas,
        "total_vendas": len(vendas),
        "total_vendido": total_vendido,
        "total_recebido": total_recebido,
        "total_falta": total_falta,
        "total_excesso": total_excesso,
        "q": dados["q"],
        "pagamento_filtro": dados["pagamento_filtro"],
        "valor_filtro": dados["valor_filtro"],
        "status_filtros": dados["status_filtros"],
        "ordem": dados["ordem"],
        "filtro_query": dados["filtro_query"],
        "gerado_em": datetime.now(),
    })


@app.get("/organiza/vendas/nova", response_class=HTMLResponse)
def venda_nova(request: Request, cliente_id: int = 0, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    clientes = db.query(Cliente).order_by(Cliente.nome.asc()).all()
    tipos, pacotes = opcoes_equipamentos(db)
    return templates.TemplateResponse("organiza/venda_nova.html", {
        "request": request, "usuario": usuario, "clientes": clientes,
        "cliente_id": cliente_id, "erro": "", "dados": {}, "status_venda": STATUS_VENDA, "tipos": tipos, "pacotes": pacotes
    })


@app.post("/organiza/vendas/nova")
async def venda_criar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    tipos, pacotes = opcoes_equipamentos(db)
    telefone = limpar_telefone(form.get("telefone") or "")
    tipo = tipo_equipamento_padrao((form.get("tipo") or "").strip())

    if not telefone_valido(telefone):
        return templates.TemplateResponse("organiza/venda_nova.html", {
            "request": request, "usuario": usuario, "clientes": [], "cliente_id": 0,
            "erro": "Informe um WhatsApp válido com DDD.", "dados": form,
            "status_venda": STATUS_VENDA, "tipos": tipos, "pacotes": pacotes,
        }, status_code=400)
    if not tipo:
        return templates.TemplateResponse("organiza/venda_nova.html", {
            "request": request, "usuario": usuario, "clientes": [], "cliente_id": 0,
            "erro": "Informe o equipamento vendido.", "dados": form,
            "status_venda": STATUS_VENDA, "tipos": tipos, "pacotes": pacotes,
        }, status_code=400)

    cliente = db.query(Cliente).filter(Cliente.telefone == telefone).first()
    if not cliente:
        cliente = Cliente(
            nome=f"Cadastro pendente {telefone[-4:]}",
            telefone=telefone,
            pais="BR",
            ddi="55",
            token_ficha=secrets.token_urlsafe(24),
        )
        db.add(cliente)
        db.flush()
    elif not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)
        db.flush()

    # Na abertura da venda o atendente informa somente WhatsApp + equipamento.
    # Os demais dados pertencem ao cliente e são preenchidos no link público.
    eq = Equipamento(cliente_id=cliente.id)
    preencher_equipamento(eq, {
        "tipo": tipo,
        "status": "Solicitar gabinete",
        "fabricante": "KARAOKERJ",
        "garantia_meses": "3",
    }, db)
    garantir_identificacao_equipamento(db, eq)
    db.add(eq)
    db.flush()
    reordenar_series_cliente(db, cliente.id)
    _sincronizar_pacote_cliente(db, cliente.id)
    db.commit()
    db.refresh(eq)
    return RedirectResponse(f"/organiza/vendas/{eq.id}/cadastro", status_code=303)


@app.get("/organiza/vendas/{equipamento_id}/cadastro", response_class=HTMLResponse)
def venda_link_cadastro(equipamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).options(selectinload(Equipamento.cliente)).filter(Equipamento.id == equipamento_id).first()
    if not eq or not eq.cliente:
        raise HTTPException(404)
    cliente = eq.cliente
    if not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)
        db.commit()
    cadastro_url = f"{PUBLIC_BASE_URL}/cadastro/{cliente.token_ficha}"
    mensagem = (
        "Olá! Para concluir o cadastro da sua compra na Karaokê RJ, preencha seus dados no link abaixo:\n\n"
        f"{cadastro_url}\n\n"
        "O cadastro será usado para entrega, garantia e emissão da nota fiscal."
    )
    return templates.TemplateResponse("organiza/venda_cadastro_link.html", {
        "request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq,
        "cadastro_url": cadastro_url, "mensagem": mensagem,
    })


def _humiat_acesso_organiza_rapido(db: Session, usuario_id: int) -> bool:
    produto = db.query(HumiatProduto).filter(HumiatProduto.codigo == "ORGANIZA", HumiatProduto.ativo == 1).first()
    if not produto:
        return False
    acesso = db.query(HumiatUsuarioProduto).filter(
        HumiatUsuarioProduto.usuario_id == int(usuario_id),
        HumiatUsuarioProduto.produto_id == int(produto.id),
    ).first()
    return bool(acesso and (acesso.acesso_sistema or acesso.acesso_adm))


def _cliente_humiat_atual(request: Request, db: Session) -> Cliente | None:
    hu = humiat_usuario_da_requisicao(request, db)
    if not hu or not _humiat_acesso_organiza_rapido(db, int(hu.id)):
        return None
    return db.query(Cliente).filter(Cliente.humiat_usuario_id == int(hu.id)).order_by(Cliente.id).first()


@app.get("/humiat/organiza/cadastro")
def humiat_organiza_atualizar_cadastro(request: Request, db: Session = Depends(get_db)):
    """Tarefa rápida do portal Humiat: abre o cadastro público já vinculado."""
    cliente = _cliente_humiat_atual(request, db)
    if not cliente:
        return RedirectResponse("/entrar", status_code=303) if not humiat_usuario_da_requisicao(request, db) else RedirectResponse("/painel?erro=Cadastro de cliente não vinculado ao Humiat ID", status_code=303)
    if not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)
        db.commit()
    return RedirectResponse(f"/cadastro/{cliente.token_ficha}", status_code=303)


@app.get("/humiat/organiza/chamado", response_class=HTMLResponse)
def humiat_organiza_abrir_chamado(request: Request, db: Session = Depends(get_db)):
    """Tarefa rápida do portal Humiat: inicia chamado sem pedir o telefone de novo."""
    hu = humiat_usuario_da_requisicao(request, db)
    if not hu:
        return RedirectResponse("/entrar", status_code=303)
    if not _humiat_acesso_organiza_rapido(db, int(hu.id)):
        raise HTTPException(status_code=403, detail="Tarefas rápidas do Organiza não liberadas para este Humiat ID")
    cliente = db.query(Cliente).filter(Cliente.humiat_usuario_id == int(hu.id)).order_by(Cliente.id).first()
    if not cliente:
        return RedirectResponse("/painel?erro=Cadastro de cliente não vinculado ao Humiat ID", status_code=303)
    telefone = limpar_telefone(cliente.telefone or "")
    return templates.TemplateResponse("organiza/manutencao_publica.html", {
        "request": request,
        "etapa": "revisar_dados",
        "erro": "",
        "telefone": telefone,
        "cliente": cliente,
        "ano_atual": date.today().year,
        "horarios": HORARIOS_ENTREGA_PUBLICA,
    })


@app.get("/cadastro/{token}", response_class=HTMLResponse)
def cadastro_publico(token: str, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    return templates.TemplateResponse("organiza/cadastro_publico.html", {
        "request": request, "cliente": cliente, "erro": "",
        "salvo": request.query_params.get("salvo"),
        "aviso": request.query_params.get("aviso", ""),
    })


@app.post("/cadastro/{token}/consultar-cnpj")
async def cadastro_publico_consultar_cnpj(token: str, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    body = await request.json()
    cnpj = limpar_documento(str((body or {}).get("cnpj") or ""))
    try:
        dados = consultar_cnpj_publico(cnpj)
        # A consulta de CNPJ também traz o endereço cadastral da empresa.
        # Ele é salvo como ponto de partida e, na página pública, o CEP continua
        # sendo a fonte de validação de logradouro/bairro/município/UF.
        aplicar_dados_cnpj(cliente, dados, atualizar_endereco=True)
        db.commit()
        serial = {**dados, "consultado_em": dados["consultado_em"].isoformat()}
        if not dados.get("inscricao_estadual") and dados.get("situacao_icms") == "NAO_CONFIRMADO":
            serial["aviso_ie"] = "SINTEGRA não disponível. Tente mais tarde."
        return JSONResponse(serial)
    except ValueError as exc:
        return JSONResponse({"erro": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"erro": str(exc)}, status_code=503)


@app.post("/cadastro/{token}")
async def cadastro_publico_salvar(token: str, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    form = dict(await request.form())
    telefone_original = cliente.telefone
    documento_original = limpar_documento(cliente.documento or "")

    cliente.nome = limpar_nome_cliente(form.get("nome") or "")
    cliente.pais, cliente.ddi, cliente.telefone = normalizar_contato(
        form.get("pais") or getattr(cliente, "pais", "BR"),
        form.get("ddi") or getattr(cliente, "ddi", "55"),
        form.get("telefone") or "",
    )
    cliente.documento = (form.get("documento") or "").strip() or None
    cliente.email = (form.get("email") or "").strip() or None
    proximo_fluxo = (request.query_params.get("next") or "").strip()

    if proximo_fluxo and "/atualizacao/" in proximo_fluxo and not _gmail_valido(cliente.email):
        return templates.TemplateResponse("organiza/cadastro_publico.html", {
            "request": request, "cliente": cliente, "erro": "Para receber a atualização, informe um endereço Gmail válido.", "salvo": False, "aviso": ""
        }, status_code=400)

    if not cliente.nome or not telefone_valido(cliente.telefone):
        cliente.telefone = telefone_original
        return templates.TemplateResponse("organiza/cadastro_publico.html", {
            "request": request, "cliente": cliente, "erro": "Informe nome e WhatsApp válidos.", "salvo": False, "aviso": ""
        }, status_code=400)
    duplicado = db.query(Cliente).filter(Cliente.telefone == cliente.telefone, Cliente.id != cliente.id).first()
    if duplicado:
        cliente.telefone = telefone_original
        return templates.TemplateResponse("organiza/cadastro_publico.html", {
            "request": request, "cliente": cliente, "erro": "Este WhatsApp já pertence a outro cadastro.", "salvo": False, "aviso": ""
        }, status_code=400)

    aviso = ""
    doc = limpar_documento(cliente.documento or "")
    if len(doc) == 14:
        if not cnpj_valido(doc):
            return templates.TemplateResponse("organiza/cadastro_publico.html", {
                "request": request, "cliente": cliente, "erro": "Informe um CNPJ válido.", "salvo": False, "aviso": ""
            }, status_code=400)
        try:
            recente = cliente.cnpj_consultado_em and documento_original == doc and (datetime.now() - cliente.cnpj_consultado_em) < timedelta(minutes=10)
            if not recente:
                aplicar_dados_cnpj(cliente, consultar_cnpj_publico(doc), atualizar_endereco=True)
        except (RuntimeError, ValueError):
            aviso = "SINTEGRA não disponível. Tente mais tarde."

        # Se a consulta automática não trouxe a situação fiscal, o cliente pode
        # informar os dados no próprio cadastro público.
        razao_manual = (form.get("razao_social") or "").strip()
        fantasia_manual = (form.get("empresa") or "").strip()
        if razao_manual:
            cliente.razao_social = razao_manual
        if fantasia_manual:
            cliente.empresa = fantasia_manual
        situacao_manual = (form.get("situacao_icms") or "").strip().upper()
        situacao_confirmada_api = (cliente.situacao_icms or "").strip().upper() in {"CONTRIBUINTE", "NAO_CONTRIBUINTE", "ISENTO"}
        if situacao_manual in {"CONTRIBUINTE", "NAO_CONTRIBUINTE", "ISENTO"}:
            cliente.situacao_icms = situacao_manual
        elif not situacao_confirmada_api:
            cliente.situacao_icms = "NAO_CONFIRMADO"
        ie_manual = (form.get("inscricao_estadual") or "").strip()
        if ie_manual:
            cliente.inscricao_estadual = ie_manual
        if (cliente.situacao_icms or "").strip().upper() == "NAO_CONFIRMADO":
            aviso = "SINTEGRA não disponível. Tente mais tarde."
        if cliente.situacao_icms == "CONTRIBUINTE" and not (cliente.inscricao_estadual or "").strip():
            aviso = "SINTEGRA não disponível. Tente mais tarde."
        if not (cliente.razao_social or "").strip():
            return templates.TemplateResponse("organiza/cadastro_publico.html", {
                "request": request, "cliente": cliente, "erro": "Informe a Razão Social ou use Validar CNPJ.", "salvo": False, "aviso": aviso
            }, status_code=400)
    elif len(doc) == 11:
        if not cpf_valido(doc):
            return templates.TemplateResponse("organiza/cadastro_publico.html", {
                "request": request, "cliente": cliente, "erro": "Informe um CPF válido.", "salvo": False, "aviso": ""
            }, status_code=400)
        cliente.razao_social = None
        cliente.inscricao_estadual = None
        cliente.situacao_icms = None
    else:
        return templates.TemplateResponse("organiza/cadastro_publico.html", {
            "request": request, "cliente": cliente, "erro": "Informe um CPF ou CNPJ válido.", "salvo": False, "aviso": ""
        }, status_code=400)

    # Endereço do cliente: CEP é a fonte. O navegador não decide logradouro,
    # bairro, município ou UF; o servidor consulta novamente o CEP ao salvar.
    try:
        end = consultar_cep_publico(form.get("cep") or "")
    except (ValueError, RuntimeError) as exc:
        return templates.TemplateResponse("organiza/cadastro_publico.html", {
            "request": request, "cliente": cliente, "erro": str(exc), "salvo": False, "aviso": aviso
        }, status_code=400)
    numero = (form.get("endereco_numero") or "").strip()
    if not numero:
        return templates.TemplateResponse("organiza/cadastro_publico.html", {
            "request": request, "cliente": cliente, "erro": "Informe o número do endereço do cliente.", "salvo": False, "aviso": aviso
        }, status_code=400)
    cliente.cep = end["cep"]
    cliente.endereco = end["endereco"]
    cliente.endereco_numero = numero
    cliente.complemento = (form.get("complemento") or "").strip() or None
    cliente.bairro = end["bairro"]
    cliente.municipio = end["municipio"]
    cliente.cidade = end["municipio"]
    cliente.municipio_ibge = end["municipio_ibge"] or None
    cliente.estado = end["uf"]

    cliente.entrega_igual_cliente = 1 if form.get("entrega_igual_cliente") else 0
    if cliente.entrega_igual_cliente == 0:
        try:
            ent = consultar_cep_publico(form.get("entrega_cep") or "")
        except (ValueError, RuntimeError) as exc:
            return templates.TemplateResponse("organiza/cadastro_publico.html", {
                "request": request, "cliente": cliente, "erro": f"Endereço de entrega: {exc}", "salvo": False, "aviso": aviso
            }, status_code=400)
        numero_entrega = (form.get("entrega_numero") or "").strip()
        if not numero_entrega:
            return templates.TemplateResponse("organiza/cadastro_publico.html", {
                "request": request, "cliente": cliente, "erro": "Informe o número do endereço de entrega.", "salvo": False, "aviso": aviso
            }, status_code=400)
        cliente.entrega_cep = ent["cep"]
        cliente.entrega_endereco = ent["endereco"]
        cliente.entrega_numero = numero_entrega
        cliente.entrega_complemento = (form.get("entrega_complemento") or "").strip() or None
        cliente.entrega_bairro = ent["bairro"]
        cliente.entrega_municipio = ent["municipio"]
        cliente.entrega_municipio_ibge = ent["municipio_ibge"] or None
        cliente.entrega_estado = ent["uf"]

    db.commit()
    proximo_fluxo = (request.query_params.get("next") or "").strip()
    base_publica = PUBLIC_BASE_URL.rstrip("/")
    if proximo_fluxo.startswith(base_publica + "/atualizacao/") or proximo_fluxo.startswith("/atualizacao/"):
        separador = "&" if "?" in proximo_fluxo else "?"
        return RedirectResponse(proximo_fluxo + separador + "cadastro=ok", status_code=303)
    destino = f"/cadastro/{token}?salvo=1"
    if aviso:
        destino += "&aviso=" + quote_plus(aviso)
    return RedirectResponse(destino, status_code=303)


def _nome_arquivo(texto: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", texto or "cliente").strip("_") or "cliente"


@app.get("/organiza/equipamentos/{equipamento_id}/garantia.pdf")
def garantia_pdf(equipamento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).options(selectinload(Equipamento.cliente)).filter(Equipamento.id == equipamento_id).first()
    if not eq:
        raise HTTPException(404)

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    fonte_regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    fonte_negrito = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    nome_fonte = "DejaVu"
    nome_fonte_bold = "DejaVu-Bold"
    if os.path.exists(fonte_regular) and os.path.exists(fonte_negrito):
        if nome_fonte not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(nome_fonte, fonte_regular))
            pdfmetrics.registerFont(TTFont(nome_fonte_bold, fonte_negrito))
    else:
        nome_fonte, nome_fonte_bold = "Helvetica", "Helvetica-Bold"

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm,
        topMargin=12 * mm, bottomMargin=14 * mm, title="Venda de Equipamentos - Termo de Garantia"
    )
    styles = getSampleStyleSheet()
    corpo = ParagraphStyle("ContratoCorpo", parent=styles["BodyText"], fontName=nome_fonte, fontSize=9.2, leading=13, alignment=TA_JUSTIFY, spaceAfter=7)
    titulo = ParagraphStyle("ContratoTitulo", parent=styles["Title"], fontName=nome_fonte_bold, fontSize=13, leading=16, alignment=TA_CENTER, spaceAfter=12)
    cabecalho = ParagraphStyle("ContratoCabecalho", parent=corpo, fontSize=8.5, leading=11)

    cliente = eq.cliente
    data_entrega = eq.previsao_entrega
    data_texto = data_entrega.strftime("%d de %B de %Y") if data_entrega else "data de entrega não informada"
    meses_pt = {1:"janeiro",2:"fevereiro",3:"março",4:"abril",5:"maio",6:"junho",7:"julho",8:"agosto",9:"setembro",10:"outubro",11:"novembro",12:"dezembro"}
    if data_entrega:
        data_texto = f"{data_entrega.day:02d} de {meses_pt[data_entrega.month]} de {data_entrega.year}"

    endereco_partes = [cliente.endereco, cliente.endereco_numero]
    endereco = ", ".join(str(x).strip() for x in endereco_partes if x)
    if cliente.complemento:
        endereco += (", " if endereco else "") + cliente.complemento
    if cliente.bairro:
        endereco += (" - " if endereco else "") + cliente.bairro
    cidade_uf = " - ".join(x for x in [cliente.municipio or cliente.cidade, cliente.estado] if x)
    if cidade_uf:
        endereco += (", " if endereco else "") + cidade_uf
    endereco = endereco or "endereço não informado"

    equipamento = " ".join(x for x in [eq.tipo, eq.modelo] if x).strip() or "equipamento de karaokê"
    pacote = f", atualizado até o pacote {eq.pacote}" if eq.pacote else ""
    valor = formatar_moeda(eq.valor)
    meses = eq.garantia_meses if eq.garantia_meses is not None else 3

    logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "logo-karaoke-rj.png")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "karaoke-rj-garantia.jpeg")
    logo = Image(logo_path, width=30 * mm, height=22 * mm) if os.path.exists(logo_path) else Spacer(30 * mm, 22 * mm)
    dados_empresa = Paragraph(
        "<b>KARAOKE &amp; GAMES RJ</b><br/>CNPJ: 35.458.112/0001-75 · IM: 1213508-4<br/>"
        "Rua João Romariz, 313 - Ramos - Rio de Janeiro/RJ - CEP: 21031-700<br/>"
        "WhatsApp: (21) 99507-9690 / (21) 99650-4516<br/>www.karaokerj.com.br · contato@karaokerj.com.br", cabecalho
    )
    header = Table([[logo, dados_empresa]], colWidths=[35 * mm, 145 * mm])
    header.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("LINEBELOW", (0,0), (-1,-1), 0.8, colors.HexColor("#555555")), ("LEFTPADDING", (0,0), (-1,-1), 0), ("RIGHTPADDING", (0,0), (-1,-1), 0), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))

    story = [header, Spacer(1, 8), Paragraph("VENDA DE EQUIPAMENTOS - TERMO DE GARANTIA", titulo)]
    story.append(Paragraph(
        f"Pelo presente instrumento particular, de um lado <b>KARAOKE &amp; GAMES RJ</b>, inscrita no CNPJ "
        f"35.458.112/0001-75, estabelecida à Rua João Romariz, 313, Fundos, Ramos, Rio de Janeiro/RJ, "
        f"doravante denominada <b>VENDEDORA</b>, e de outro lado <b>{cliente.nome}</b>, CPF/CNPJ "
        f"<b>{cliente.documento or 'não informado'}</b>, residente e domiciliado(a) em <b>{endereco}</b>, "
        f"doravante denominado(a) <b>COMPRADOR(A)</b>, firmam o presente contrato de venda e garantia.", corpo
    ))
    clausulas = [
        f"<b>Cláusula 1ª.</b> O presente contrato tem como objeto a venda do equipamento <b>{equipamento}</b>{pacote}, máquina/código <b>{eq.maquina or 'não informado'}</b> e número de série <b>{eq.numero_serie or 'não informado'}</b>.",
        f"<b>Cláusula 2ª.</b> O equipamento será entregue pela VENDEDORA em <b>{data_texto}</b>. Esta é a data de referência para o início da garantia.",
        f"<b>Cláusula 3ª.</b> O endereço de instalação informado pelo(a) COMPRADOR(A) é <b>{endereco}</b>.",
        f"<b>Cláusula 4ª.</b> O valor total da venda é <b>{valor}</b>.",
        f"<b>Cláusula 5ª.</b> A garantia do equipamento é de <b>{meses} meses a partir da data de entrega</b>. Para atendimento em garantia, o equipamento deverá ser levado à loja, salvo acordo diferente registrado por escrito.",
        "<b>Cláusula 6ª.</b> A garantia não cobre cabos, acessórios consumíveis, mau uso, quedas, líquidos, violação, intervenção de terceiros ou danos causados por falha e surto elétrico.",
        "<b>Cláusula 7ª.</b> Máquinas Premium, portáteis ou fliperamas devem utilizar estabilizador TS Shara 9101. Máquinas JBL e fliperamas de maior potência devem utilizar estabilizador TS Shara 9116, conforme orientação técnica da VENDEDORA.",
        "<b>Cláusula 8ª.</b> Recomenda-se a utilização de cabos de microfone Santo Ângelo XLR x P10 ou equivalentes de qualidade técnica compatível.",
        "<b>Cláusula 9ª.</b> Este contrato obriga as partes, seus herdeiros e sucessores.",
    ]
    for texto_clausula in clausulas:
        story.append(Paragraph(texto_clausula, corpo))
    story += [
        Spacer(1, 9),
        Paragraph("Por estarem justos e contratados, firmam o presente instrumento em duas vias de igual teor.", corpo),
        Spacer(1, 12),
        Paragraph(f"Rio de Janeiro, {data_texto}.", corpo),
        Spacer(1, 26),
        KeepTogether(Table([
            ["________________________________________", "________________________________________"],
            ["KARAOKE & GAMES RJ", cliente.nome],
            ["VENDEDORA", "COMPRADOR(A)"],
        ], colWidths=[85 * mm, 85 * mm], style=TableStyle([("ALIGN", (0,0), (-1,-1), "CENTER"), ("FONTNAME", (0,0), (-1,-1), nome_fonte), ("FONTSIZE", (0,0), (-1,-1), 8.5), ("TOPPADDING", (0,0), (-1,-1), 2), ("BOTTOMPADDING", (0,0), (-1,-1), 2)])))
    ]
    doc.build(story)
    nome = _nome_arquivo(cliente.nome)
    return Response(buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="contrato_garantia_{nome}_{eq.id}.pdf"'})


@app.get("/organiza/equipamentos/{equipamento_id}/nfae", response_class=HTMLResponse)
def dados_nfae_previa(equipamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).options(selectinload(Equipamento.cliente)).filter(Equipamento.id == equipamento_id).first()
    if not eq:
        raise HTTPException(404)
    dados = nfae_dados_equipamento(eq, db)
    return templates.TemplateResponse("organiza/nfae_previa.html", {
        "request": request, "usuario": usuario, "equipamento": eq, "cliente": eq.cliente,
        "dados": dados, "faltantes": nfae_campos_faltantes(dados),
    })


@app.post("/organiza/equipamentos/{equipamento_id}/nfae/chave")
async def salvar_chave_acesso_nfe(
    equipamento_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id).first()
    if not eq:
        raise HTTPException(404)
    form = dict(await request.form())
    chave = re.sub(r"\D", "", (form.get("chave_acesso_nfe") or "").strip())
    if len(chave) != 44:
        return RedirectResponse(
            f"/organiza/equipamentos/{equipamento_id}/nfae?erro_chave=1",
            status_code=303,
        )
    eq.chave_acesso_nfe = chave
    db.commit()
    return RedirectResponse(
        f"/organiza/equipamentos/{equipamento_id}/nfae?chave_salva=1",
        status_code=303,
    )


@app.get("/organiza/equipamentos/{equipamento_id}/nfae/payload")
def dados_nfae_payload(equipamento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id).first()
    if not eq:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    dados = nfae_dados_equipamento(eq, db)
    dados["campos_faltantes"] = nfae_campos_faltantes(dados)
    dados["automacao"] = {
        "origem": "Organiza",
        "versao": ORGANIZA_VERSION,
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "expira_em_minutos": 240,
    }
    return JSONResponse(dados, headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/organiza/configuracoes/pacotes", response_class=HTMLResponse)
def configuracao_pacotes(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    exigir_admin(usuario)
    return templates.TemplateResponse(
        "organiza/configuracao_pacotes.html",
        {
            "request": request,
            "usuario": usuario,
            "pacote_atual": obter_pacote_atual(db),
            "mensagem": request.query_params.get("mensagem", ""),
            "erro": "",
        },
    )


@app.post("/organiza/configuracoes/pacotes", response_class=HTMLResponse)
async def configuracao_pacotes_salvar(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    exigir_admin(usuario)
    form = dict(await request.form())
    pacote_atual = (form.get("pacote_atual") or "").strip()

    if not re.fullmatch(r"\d{4}\.[12]", pacote_atual):
        return templates.TemplateResponse(
            "organiza/configuracao_pacotes.html",
            {
                "request": request,
                "usuario": usuario,
                "pacote_atual": pacote_atual,
                "mensagem": "",
                "erro": "Informe o pacote no formato AAAA.1 ou AAAA.2.",
            },
            status_code=400,
        )

    configuracao = db.query(ConfiguracaoSistema).filter(
        ConfiguracaoSistema.chave == "pacote_atual"
    ).first()
    if not configuracao:
        configuracao = ConfiguracaoSistema(chave="pacote_atual")
        db.add(configuracao)
    configuracao.valor = pacote_atual

    for equipamento in db.query(Equipamento).all():
        equipamento.falta_pacote = calcular_falta_pacote(
            equipamento.pacote, pacote_atual
        )

    db.commit()
    return RedirectResponse(
        "/organiza/configuracoes/pacotes?mensagem=Pacote+atual+salvo+e+cálculos+atualizados.",
        status_code=303,
    )


@app.get("/organiza/usuarios", response_class=HTMLResponse)
def usuarios(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_admin(usuario)
    return templates.TemplateResponse("organiza/usuarios.html", {"request": request, "usuario": usuario, "usuarios": db.query(Usuario).order_by(Usuario.nome).all()})


def moeda_num(valor) -> float:
    """Converte valores monetários sem perder casas decimais.

    Aceita tanto o padrão brasileiro (1.234,56) quanto valores internos/HTML
    com ponto decimal (1234.56). O parser anterior removia todo ponto e podia
    transformar 180.00 em 18.000,00.
    """
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

    texto = str(valor or "0").replace("R$", "").replace(" ", "").strip()
    if not texto:
        return 0.0

    # Quando há os dois separadores, o último indica as casas decimais.
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif texto.count(".") > 1:
        partes = texto.split(".")
        if len(partes[-1]) in (1, 2):
            texto = "".join(partes[:-1]) + "." + partes[-1]
        else:
            texto = "".join(partes)
    elif texto.count(".") == 1:
        inteiro, decimal = texto.split(".", 1)
        # 5.000 é normalmente milhar em pt-BR; 2530.00 é decimal interno.
        if len(decimal) == 3:
            texto = inteiro + decimal

    try:
        numero = Decimal(texto).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return float(numero)
    except (InvalidOperation, ValueError):
        return 0.0


def totais_orcamento(orcamento: Orcamento):
    manutencao = max(float(orcamento.valor_manutencao or 0), 0)
    itens_obrigatorios = sum(i.preco_venda * i.quantidade for i in orcamento.itens if not i.opcional)
    obrigatorio = manutencao + itens_obrigatorios

    itens_opcionais = [i for i in orcamento.itens if i.opcional]
    opcionais = sum(i.preco_venda * i.quantidade for i in itens_opcionais)
    opcionais_aprovados = [i for i in itens_opcionais if i.aprovado]
    todos_opcionais_aprovados = len(opcionais_aprovados) == len(itens_opcionais)

    subtotal_aprovado = manutencao + sum(
        i.preco_venda * i.quantidade
        for i in orcamento.itens
        if (not i.opcional) or i.aprovado
    )

    desconto_informado = max(float(orcamento.desconto or 0), 0)
    desconto_condicional = bool(orcamento.desconto_somente_com_opcionais)

    # Desconto no valor efetivamente aprovado:
    # - normal: aplica sobre qualquer combinação aprovada;
    # - condicional: aplica apenas quando todos os opcionais forem aprovados.
    pode_aplicar_desconto = (not desconto_condicional) or todos_opcionais_aprovados
    desconto_aplicado = min(desconto_informado, subtotal_aprovado) if pode_aplicar_desconto else 0
    aprovado = max(subtotal_aprovado - desconto_aplicado, 0)

    geral_bruto = obrigatorio + opcionais
    # O total com todos os opcionais sempre atende à condição.
    geral = max(geral_bruto - min(desconto_informado, geral_bruto), 0)

    # No total obrigatório, o desconto condicional não é aplicado.
    desconto_no_obrigatorio = 0 if desconto_condicional else min(desconto_informado, obrigatorio)
    obrigatorio_final = max(obrigatorio - desconto_no_obrigatorio, 0)

    recebido = sum(p.valor for p in orcamento.pagamentos)
    return {
        "manutencao": manutencao,
        "itens_obrigatorios": itens_obrigatorios,
        "obrigatorio": obrigatorio,
        "obrigatorio_final": obrigatorio_final,
        "opcionais": opcionais,
        "geral_bruto": geral_bruto,
        "geral": geral,
        "subtotal_aprovado": subtotal_aprovado,
        "desconto": desconto_aplicado,
        "desconto_informado": desconto_informado,
        "desconto_condicional": desconto_condicional,
        "desconto_disponivel": pode_aplicar_desconto,
        "todos_opcionais_aprovados": todos_opcionais_aprovados,
        "aprovado": aprovado,
        "recebido": recebido,
        "falta": max(aprovado - recebido, 0),
    }


def carregar_manutencao(db: Session, manutencao_id: int):
    return db.query(Manutencao).options(
        selectinload(Manutencao.cliente),
        selectinload(Manutencao.equipamento),
        selectinload(Manutencao.orcamentos).selectinload(Orcamento.itens),
        selectinload(Manutencao.orcamentos).selectinload(Orcamento.pagamentos),
    ).filter(Manutencao.id == manutencao_id).first()


@app.get("/organiza/itens", response_class=HTMLResponse)
def itens_lista(request: Request, busca: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    q = db.query(Item)
    termo = busca.strip()
    if termo:
        filtro = f"%{termo}%"
        q = q.filter(or_(Item.nome.ilike(filtro), Item.codigo.ilike(filtro), Item.categoria.ilike(filtro)))
    itens = q.order_by(Item.ativo.desc(), Item.categoria, Item.nome).all()
    categorias = [r[0] for r in db.query(Item.categoria).filter(Item.categoria.isnot(None)).distinct().order_by(Item.categoria).all() if r[0]]
    return templates.TemplateResponse("organiza/itens.html", {"request": request, "usuario": usuario, "itens": itens, "categorias": categorias, "busca": busca})


@app.post("/organiza/itens/novo")
async def item_novo(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    nome = (form.get("nome") or "").strip()
    destino = (form.get("next") or "/organiza/itens").strip()
    if not destino.startswith("/"):
        destino = "/organiza/itens"
    if nome:
        existente = db.query(Item).filter(func.lower(Item.nome) == nome.lower()).first()
        if not existente:
            db.add(Item(
                nome=nome,
                codigo=(form.get("codigo") or "").strip() or None,
                categoria=(form.get("categoria") or "Geral").strip() or "Geral",
                preco_custo=moeda_num(form.get("preco_custo")),
                preco_venda=moeda_num(form.get("preco_venda")),
                ativo=1,
            ))
            db.commit()
    return RedirectResponse(destino, status_code=303)


@app.post("/organiza/itens/{item_id}/editar")
async def item_editar(item_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(404)
    form = dict(await request.form())
    nome = (form.get("nome") or "").strip()
    repetido = db.query(Item).filter(func.lower(Item.nome) == nome.lower(), Item.id != item_id).first() if nome else None
    if nome and not repetido:
        item.nome = nome
        item.codigo = (form.get("codigo") or "").strip() or None
        item.categoria = (form.get("categoria") or "Geral").strip() or "Geral"
        item.preco_custo = moeda_num(form.get("preco_custo"))
        item.preco_venda = moeda_num(form.get("preco_venda"))
        db.commit()
    return RedirectResponse("/organiza/itens", status_code=303)


@app.post("/organiza/itens/{item_id}/status")
def item_status(item_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(404)
    item.ativo = 0 if item.ativo else 1
    db.commit()
    return RedirectResponse("/organiza/itens", status_code=303)


@app.get("/organiza/manutencoes", response_class=HTMLResponse)
def manutencoes_lista(request: Request, status: str = "orcamento", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    """Lista usando a mesma regra operacional do painel.

    Evita divergência entre o número exibido no card e os registros encontrados
    ao abrir a etapa. Os aliases antigos continuam funcionando.
    """
    query = db.query(Manutencao).options(
        selectinload(Manutencao.cliente),
        selectinload(Manutencao.equipamento),
        selectinload(Manutencao.orcamentos).selectinload(Orcamento.itens),
        selectinload(Manutencao.orcamentos).selectinload(Orcamento.pagamentos),
    )
    lista = query.order_by(Manutencao.criado_em.desc()).all()

    etapas_por_filtro = {
        "entrada": 1,
        "orcamento": 2,
        "aceite": 3,
        "aprovacao": 3,
        "pagamento": 4,
        "producao": 5,
        "execucao": 5,
        "agenda": 6,
        "retirada": 6,
    }
    if status in etapas_por_filtro:
        etapa = etapas_por_filtro[status]
        lista = [m for m in lista if etapa_manutencao(m) == etapa]
    elif status == "encerradas":
        lista = [m for m in lista if etapa_manutencao(m) == 7]

    # Informações prontas para a interface, sem recalcular regras no template.
    for m in lista:
        m.etapa_operacional = etapa_manutencao(m)
        o = _orcamento_atual(m)
        m.orcamento_comunicado = bool(
            o and o.status in ("Enviado", "Aguardando aprovação")
        )

    return templates.TemplateResponse("organiza/manutencoes.html", {
        "request": request,
        "usuario": usuario,
        "manutencoes": lista,
        "filtro_status": status,
    })


@app.get("/organiza/manutencoes/nova", response_class=HTMLResponse)
def manutencao_nova(request: Request, cliente_id: int = 0, equipamento_id: int = 0, erro: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    clientes = db.query(Cliente).options(selectinload(Cliente.equipamentos)).order_by(Cliente.nome).all()
    equipamentos_ativos = {
        cliente.id: ordenar_equipamentos([eq for eq in cliente.equipamentos if (eq.status or "Ativo") == "Ativo"])
        for cliente in clientes
    }
    return templates.TemplateResponse("organiza/manutencao_form.html", {
        "request": request, "usuario": usuario, "clientes": clientes,
        "equipamentos_ativos": equipamentos_ativos,
        "cliente_id": cliente_id, "equipamento_id": equipamento_id, "erro": erro
    })


@app.post("/organiza/manutencoes/nova")
async def manutencao_criar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    cliente_id = int(form.get("cliente_id") or 0); equipamento_id = int(form.get("equipamento_id") or 0)
    eq = db.query(Equipamento).filter(
        Equipamento.id == equipamento_id,
        Equipamento.cliente_id == cliente_id,
        Equipamento.status == "Ativo",
    ).first()
    if not eq or not (form.get("defeito") or "").strip():
        return RedirectResponse(f"/organiza/manutencoes/nova?cliente_id={cliente_id}&equipamento_id={equipamento_id}", status_code=303)
    tipo_atendimento = (form.get("tipo_atendimento") or "loja").strip().lower()
    if tipo_atendimento not in ("loja", "online"):
        tipo_atendimento = "loja"
    agendamento = datetime_form(form.get("entrega_prevista_em") or "")
    if not horario_atendimento_valido(tipo_atendimento, agendamento):
        return RedirectResponse(f"/organiza/manutencoes/nova?cliente_id={cliente_id}&equipamento_id={equipamento_id}&erro=horario", status_code=303)
    if horario_atendimento_ocupado(db, agendamento):
        return RedirectResponse(f"/organiza/manutencoes/nova?cliente_id={cliente_id}&equipamento_id={equipamento_id}&erro=ocupado", status_code=303)
    status_inicial = "Aguardando equipamento"
    m = Manutencao(cliente_id=cliente_id, equipamento_id=equipamento_id, defeito=form.get("defeito").strip(), observacao=(form.get("observacao") or "").strip() or None, entrega_prevista_em=agendamento, tipo_atendimento=tipo_atendimento, status=status_inicial)
    db.add(m); db.commit(); db.refresh(m)
    o = Orcamento(manutencao_id=m.id, versao=1, token=secrets.token_urlsafe(24), status="Rascunho")
    db.add(o); db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{m.id}", status_code=303)


@app.get("/organiza/manutencoes/{manutencao_id}", response_class=HTMLResponse)
def manutencao_detalhe(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m: raise HTTPException(404)
    itens = db.query(Item).filter(Item.ativo == 1).order_by(Item.nome).all()
    equipamentos_cliente = ordenar_equipamentos(
        db.query(Equipamento).filter(
            Equipamento.cliente_id == m.cliente_id,
            or_(Equipamento.status == "Ativo", Equipamento.id == m.equipamento_id),
        ).all()
    )
    orcamento = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None
    totais = totais_orcamento(orcamento) if orcamento else {}
    prontas_cliente = (
        db.query(Manutencao)
        .options(selectinload(Manutencao.equipamento))
        .filter(
            Manutencao.cliente_id == m.cliente_id,
            Manutencao.pronto_em.isnot(None),
            Manutencao.entregue_em.is_(None),
            Manutencao.status.in_(("Pronto para retirada", "Retirada agendada")),
        )
        .order_by(Manutencao.pronto_em.asc(), Manutencao.id.asc())
        .all()
    )
    linhas_prontas = []
    for pronta in prontas_cliente:
        equipamento = pronta.equipamento
        identificacao = rotulo_maquina(equipamento)
        descricao = f"{equipamento.tipo} {equipamento.modelo or ''}".strip()
        linhas_prontas.append(f"• {identificacao} · {descricao}\n  Código técnico: {codigo_tecnico(equipamento)}")
    mensagem_retirada = (
        f"Olá, {m.cliente.nome}. Os equipamentos abaixo estão prontos para retirada:\n"
        + "\n".join(linhas_prontas)
        + f"\n\n📄 Garantia do serviço (30 dias): {PUBLIC_BASE_URL}/garantia-servico/{orcamento.token}.pdf"
        + f"\n\n📅 Escolha a data e o horário da retirada: {PUBLIC_BASE_URL}/retirada/{orcamento.token}"
        + "\n\nRetiradas de segunda a sexta-feira, somente das 14:00 às 17:00."
        + "\n\nKaraokê RJ"
    ) if orcamento and prontas_cliente else ""
    return templates.TemplateResponse("organiza/manutencao_detalhe.html", {"request": request, "usuario": usuario, "m": m, "orcamento": orcamento, "itens_catalogo": itens, "equipamentos_cliente": equipamentos_cliente, "totais": totais, "etapa_atual": etapa_manutencao(m), "manutencoes_prontas_cliente": prontas_cliente, "mensagem_retirada": mensagem_retirada})


@app.post("/organiza/manutencoes/{manutencao_id}/encerrar-pendente")
async def manutencao_encerrar_pendente(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Permite encerrar/cancelar uma manutenção sem deixar pendência antes da aprovação.

    A ação é permitida somente até a etapa de aceite (1, 2 ou 3) e bloqueada
    assim que houver orçamento aprovado, preservando a integridade financeira.
    """
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)

    etapa = etapa_manutencao(m)
    orcamento = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None
    aprovado = bool(orcamento and (
        orcamento.status in ("Aprovado", "Aprovado parcialmente", "Aprovado manualmente")
        or (orcamento.status or "").startswith("Aprovado:")
    ))
    if etapa > 3 or aprovado:
        return RedirectResponse(
            f"/organiza/manutencoes/{m.id}?erro_encerramento=Após a aprovação do orçamento, use o fluxo normal da manutenção.",
            status_code=303,
        )

    form = await request.form()
    acao = (form.get("acao") or "cancelar").strip().lower()
    if acao == "finalizar":
        m.status = "Encerrada"
        m.entregue_em = m.entregue_em or datetime.now()
    else:
        # Não apaga fisicamente: remove das filas e preserva todo o histórico.
        m.status = "Cancelada"
    m.entrega_prevista_em = None
    m.retirada_em = None
    db.commit()

    destino = (form.get("destino") or "").strip()
    if destino.startswith("/organiza/"):
        return RedirectResponse(destino, status_code=303)
    return RedirectResponse("/organiza/manutencoes", status_code=303)


@app.get("/organiza/manutencoes/{manutencao_id}/relatorio.pdf")
def manutencao_relatorio_pdf(
    manutencao_id: int,
    mostrar_valores: int = 1,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Relatório técnico da manutenção, com opção de exibir ou ocultar valores."""
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    orcamento = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None
    exibir_valores = bool(mostrar_valores)

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    fonte_regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    fonte_negrito = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    nome_fonte, nome_fonte_bold = "Helvetica", "Helvetica-Bold"
    if os.path.exists(fonte_regular) and os.path.exists(fonte_negrito):
        nome_fonte, nome_fonte_bold = "DejaVu", "DejaVu-Bold"
        if nome_fonte not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(nome_fonte, fonte_regular))
            pdfmetrics.registerFont(TTFont(nome_fonte_bold, fonte_negrito))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=14*mm, leftMargin=14*mm,
                            topMargin=12*mm, bottomMargin=14*mm,
                            title=f"Relatório de Manutenção #{m.id}")
    styles = getSampleStyleSheet()
    corpo = ParagraphStyle("RelatorioCorpo", parent=styles["BodyText"], fontName=nome_fonte, fontSize=9, leading=12)
    pequeno = ParagraphStyle("RelatorioPequeno", parent=corpo, fontSize=8, leading=10)
    titulo = ParagraphStyle("RelatorioTitulo", parent=styles["Title"], fontName=nome_fonte_bold, fontSize=14, leading=17, alignment=TA_CENTER)
    secao = ParagraphStyle("RelatorioSecao", parent=corpo, fontName=nome_fonte_bold, fontSize=10.5, leading=13, spaceBefore=7, spaceAfter=5)

    logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "logo-karaoke-rj.png")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "karaoke-rj-garantia.jpeg")
    logo = Image(logo_path, width=30*mm, height=22*mm) if os.path.exists(logo_path) else Spacer(30*mm, 22*mm)
    empresa = Paragraph(
        "<b>KARAOKE &amp; GAMES RJ</b><br/>CNPJ: 35.458.112/0001-75 · IM: 1213508-4<br/>"
        "Rua João Romariz, 313 - Ramos - Rio de Janeiro/RJ - CEP: 21031-700<br/>"
        "WhatsApp: (21) 99507-9690 / (21) 99650-4516<br/>www.karaokerj.com.br · contato@karaokerj.com.br", pequeno)
    header = Table([[logo, empresa]], colWidths=[35*mm, 145*mm])
    header.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LINEBELOW",(0,0),(-1,-1),0.8,colors.HexColor("#555555")),
                                ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),5)]))

    def ptxt(v):
        return Paragraph(str(v or "-"), corpo)
    def dt(v):
        return v.strftime("%d/%m/%Y %H:%M") if v else "-"

    eq = m.equipamento
    cliente = m.cliente
    story = [header, Spacer(1,7), Paragraph(f"RELATÓRIO DE MANUTENÇÃO Nº {m.id}", titulo), Spacer(1,7)]
    dados = [
        [Paragraph("<b>Cliente</b>", corpo), ptxt(cliente.nome), Paragraph("<b>WhatsApp</b>", corpo), ptxt(formatar_telefone(cliente.telefone) if cliente.telefone else "-")],
        [Paragraph("<b>Equipamento</b>", corpo), ptxt(f"{rotulo_maquina(eq)} · {eq.tipo or ''} {eq.modelo or ''}".strip()), Paragraph("<b>Cód. técnico</b>", corpo), ptxt(codigo_tecnico(eq))],
        [Paragraph("<b>Entrada</b>", corpo), ptxt(dt(m.recebido_em or m.criado_em)), Paragraph("<b>Status</b>", corpo), ptxt(m.status)],
    ]
    tab = Table(dados, colWidths=[24*mm, 68*mm, 24*mm, 64*mm])
    tab.setStyle(TableStyle([("GRID",(0,0),(-1,-1),0.35,colors.HexColor("#bbbbbb")),("VALIGN",(0,0),(-1,-1),"TOP"),
                             ("BACKGROUND",(0,0),(0,-1),colors.HexColor("#f2f2f2")),("BACKGROUND",(2,0),(2,-1),colors.HexColor("#f2f2f2")),
                             ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
    story += [tab, Paragraph("Problema informado", secao), ptxt(m.defeito), Paragraph("Diagnóstico técnico", secao), ptxt(m.diagnostico or "Não informado")]

    if orcamento:
        story.append(Paragraph("Serviços e itens", secao))
        if exibir_valores:
            linhas = [[ptxt("Descrição"), ptxt("Qtd."), ptxt("Valor unit."), ptxt("Total")]]
            if float(orcamento.valor_manutencao or 0) > 0:
                linhas.append([ptxt("Serviço de manutenção"), ptxt("1"), ptxt(formatar_moeda(orcamento.valor_manutencao)), ptxt(formatar_moeda(orcamento.valor_manutencao))])
            for item in orcamento.itens:
                linhas.append([ptxt(item.descricao + (" (opcional)" if item.opcional else "")), ptxt(item.quantidade), ptxt(formatar_moeda(item.preco_venda)), ptxt(formatar_moeda(item.preco_venda * item.quantidade))])
            tabela_itens = Table(linhas, colWidths=[92*mm, 16*mm, 34*mm, 38*mm], repeatRows=1)
        else:
            linhas = [[ptxt("Descrição"), ptxt("Qtd.")]]
            if float(orcamento.valor_manutencao or 0) > 0:
                linhas.append([ptxt("Serviço de manutenção"), ptxt("1")])
            for item in orcamento.itens:
                linhas.append([ptxt(item.descricao + (" (opcional)" if item.opcional else "")), ptxt(item.quantidade)])
            tabela_itens = Table(linhas, colWidths=[155*mm, 25*mm], repeatRows=1)
        tabela_itens.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eeeeee")),("FONTNAME",(0,0),(-1,0),nome_fonte_bold),
                                          ("GRID",(0,0),(-1,-1),0.35,colors.HexColor("#bbbbbb")),("VALIGN",(0,0),(-1,-1),"TOP"),
                                          ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
        story.append(tabela_itens)
        if exibir_valores:
            totais = totais_orcamento(orcamento)
            story += [Spacer(1,5), Paragraph(f"<b>Total do orçamento: {formatar_moeda(totais.get('aprovado', totais.get('geral', 0)))}</b>", corpo)]
            if float(totais.get("desconto_informado", 0) or 0) > 0:
                story.append(Paragraph(f"Desconto informado: {formatar_moeda(totais['desconto_informado'])}", corpo))
        if orcamento.forma_pagamento_orcamento:
            story.append(Paragraph(f"<b>Condição de pagamento:</b> {orcamento.forma_pagamento_orcamento}", corpo))
        if orcamento.prazo_dias_uteis:
            story.append(Paragraph(f"<b>Prazo:</b> {orcamento.prazo_dias_uteis} dias úteis após a confirmação do pagamento", corpo))

    if m.observacao:
        story += [Paragraph("Observações", secao), ptxt(m.observacao)]
    story += [Spacer(1,18), Table([["____________________________________________"],["Responsável / Karaokê RJ"]], colWidths=[90*mm],
                                  style=TableStyle([("ALIGN",(0,0),(-1,-1),"CENTER"),("FONTNAME",(0,0),(-1,-1),nome_fonte),("FONTSIZE",(0,0),(-1,-1),8)]))]
    doc.build(story)
    nome = _nome_arquivo(cliente.nome)
    sufixo = "com_valores" if exibir_valores else "sem_valores"
    return Response(buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="manutencao_{m.id}_{nome}_{sufixo}.pdf"'})


@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/item")
async def orcamento_adicionar_item(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id); form = dict(await request.form())
    if not m: raise HTTPException(404)
    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    item = db.query(Item).filter(Item.id == int(form.get("item_id") or 0)).first()
    descricao = item.nome if item else (form.get("descricao") or "").strip()
    descricao_normalizada = unicodedata.normalize("NFKD", descricao).encode("ascii", "ignore").decode("ascii").strip().lower()
    if descricao_normalizada == "manutencao":
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro=Use o campo Valor obrigatório da manutenção", status_code=303)
    if descricao:
        db.add(OrcamentoItem(orcamento_id=o.id, item_id=item.id if item else None, descricao=descricao, quantidade=max(int(form.get("quantidade") or 1),1), preco_custo=item.preco_custo if item else moeda_num(form.get("preco_custo")), preco_venda=moeda_num(form.get("preco_venda")) or (item.preco_venda if item else 0), opcional=1 if form.get("opcional") else 0, aprovado=0 if form.get("opcional") else 1))
        db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/item/{orcamento_item_id}/excluir")
def orcamento_excluir_item(manutencao_id: int, orcamento_item_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    orcamento_ids = [o.id for o in m.orcamentos]
    item = db.query(OrcamentoItem).filter(OrcamentoItem.id == orcamento_item_id, OrcamentoItem.orcamento_id.in_(orcamento_ids)).first()
    if item:
        db.delete(item)
        db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)



@app.post("/organiza/manutencoes/{manutencao_id}/etapa-2/salvar")
async def manutencao_etapa2_salvar(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Salva os dados principais do orçamento de uma só vez.

    A inclusão/remoção de itens continua sendo uma ação de lista, mas valor da
    manutenção, condições e desconto são persistidos em um único salvamento.
    """
    m = carregar_manutencao(db, manutencao_id)
    if not m or not m.orcamentos:
        raise HTTPException(404)
    form = dict(await request.form())
    o = _orcamento_atual(m) or sorted(m.orcamentos, key=lambda x: x.versao)[-1]

    valor_manutencao = max(moeda_num(form.get("valor_manutencao")), 0)
    forma = (form.get("forma_pagamento_orcamento") or "").strip()
    try:
        prazo = int(form.get("prazo_dias_uteis") or 0)
    except (TypeError, ValueError):
        prazo = 0
    desconto = max(moeda_num(form.get("desconto")), 0)

    erros = []
    if valor_manutencao <= 0:
        erros.append("Informe o valor da manutenção.")
    if forma not in ("À vista", "50% de sinal + 50% na entrega"):
        erros.append("Selecione a forma de pagamento.")
    if prazo <= 0:
        erros.append("Informe o prazo em dias úteis.")

    if erros:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_etapa2={quote_plus(' '.join(erros))}#etapa-2",
            status_code=303,
        )

    o.valor_manutencao = valor_manutencao
    o.forma_pagamento_orcamento = forma
    o.prazo_dias_uteis = prazo
    subtotal = valor_manutencao + sum(float(i.preco_venda or 0) * int(i.quantidade or 0) for i in o.itens)
    o.desconto = min(desconto, subtotal)
    o.desconto_somente_com_opcionais = 1 if form.get("desconto_somente_com_opcionais") else 0
    db.commit()
    return RedirectResponse(
        f"/organiza/manutencoes/{manutencao_id}?salvo_etapa2=1#etapa-2",
        status_code=303,
    )


@app.post("/organiza/manutencoes/{manutencao_id}/etapa-2/avancar")
async def manutencao_etapa2_avancar(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    m = carregar_manutencao(db, manutencao_id)
    if not m or not m.orcamentos:
        raise HTTPException(404)
    o = _orcamento_atual(m)
    erros = []
    if not o or float(o.valor_manutencao or 0) <= 0:
        erros.append("Informe e salve o valor da manutenção.")
    if not o or not o.forma_pagamento_orcamento:
        erros.append("Informe e salve a forma de pagamento.")
    if not o or not o.prazo_dias_uteis:
        erros.append("Informe e salve o prazo.")
    if erros:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_etapa2={quote_plus(' '.join(erros))}#etapa-2",
            status_code=303,
        )
    m.status = "Aguardando aprovação"
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-3", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/manutencao")
async def orcamento_salvar_manutencao(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m or not m.orcamentos:
        raise HTTPException(404)
    form = dict(await request.form())
    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    o.valor_manutencao = max(moeda_num(form.get("valor_manutencao")), 0)
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/desconto")
async def orcamento_salvar_desconto(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m or not m.orcamentos:
        raise HTTPException(404)
    form = dict(await request.form())
    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    subtotal = max(float(o.valor_manutencao or 0), 0) + sum(i.preco_venda * i.quantidade for i in o.itens)
    o.desconto = min(max(moeda_num(form.get("desconto")), 0), subtotal)
    o.desconto_somente_com_opcionais = 1 if form.get("desconto_somente_com_opcionais") else 0
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/condicoes")
async def orcamento_salvar_condicoes(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m or not m.orcamentos:
        raise HTTPException(404)
    form = dict(await request.form())
    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    forma = (form.get("forma_pagamento_orcamento") or "").strip()
    if forma not in ("À vista", "50% de sinal + 50% na entrega"):
        forma = ""
    try:
        prazo = int(form.get("prazo_dias_uteis") or 0)
    except (TypeError, ValueError):
        prazo = 0
    o.forma_pagamento_orcamento = forma or None
    o.prazo_dias_uteis = prazo if prazo > 0 else None
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/enviar")
def orcamento_enviar(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id); o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    if float(o.valor_manutencao or 0) <= 0: return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro=Informe o valor obrigatório da manutenção", status_code=303)
    if not o.forma_pagamento_orcamento: return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro=Informe a forma de pagamento do orçamento", status_code=303)
    if not o.prazo_dias_uteis or o.prazo_dias_uteis <= 0: return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro=Informe o prazo em dias úteis após o pagamento", status_code=303)
    o.status = "Enviado"; m.status = "Aguardando aprovação"; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


def registrar_aprovacao_orcamento(o: Orcamento, modalidade: str, origem: str) -> None:
    """Grava exatamente o que foi autorizado para a execução técnica."""
    aprovar_tudo = modalidade == "todos"
    for item in o.itens:
        item.aprovado = 1 if (not item.opcional or aprovar_tudo) else 0

    # O campo status possui limite de 40 caracteres no PostgreSQL.
    # A origem da aprovação não deve ser concatenada aqui para evitar erro 500.
    o.status = "Aprovado: todos" if aprovar_tudo else "Aprovado: obrigatórios"
    o.aprovado_em = datetime.now()
    o.manutencao.status = "Aprovado"


@app.post("/organiza/manutencoes/{manutencao_id}/aprovar-manual")
@app.post("/organiza/manutencoes/{manutencao_id}/corrigir-aprovacao")
async def aprovar_manual(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    m = carregar_manutencao(db, manutencao_id)
    if not m or not m.orcamentos:
        raise HTTPException(404, "Orçamento não encontrado.")
    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    form = dict(await request.form())
    modalidade = form.get("modalidade", "obrigatorios")
    if modalidade not in {"obrigatorios", "todos"}:
        modalidade = "obrigatorios"

    origem = "correção administrativa" if o.aprovado_em else "aprovação manual"
    registrar_aprovacao_orcamento(o, modalidade, origem)
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-3", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/pagamento")
async def pagamento_registrar(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id); form = dict(await request.form()); o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    valor = moeda_num(form.get("valor"))
    forma = (form.get("forma") or "PIX").strip()
    banco = forma
    nome_comprovante = (form.get("observacao") or "").strip()
    observacao = _obs_pagamento_padrao(m.equipamento, m.cliente, nome_comprovante)
    total, recebido, saldo = _saldo_manutencao(m)
    if valor > saldo + 0.009:
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=pagamento_excedente", status_code=303)
    if valor > 0:
        db.add(Pagamento(
            orcamento_id=o.id,
            data=data_form(form.get("data") or "") or date.today(),
            valor=valor,
            forma=forma,
            banco=banco,
            observacao=observacao or None,
        ))
        m.status = "Confirmação pendente"; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)



@app.post("/organiza/manutencoes/{manutencao_id}/pagamentos/{pagamento_id}/editar")
async def manutencao_pagamento_editar(
    manutencao_id: int,
    pagamento_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    ids_orcamentos = [o.id for o in m.orcamentos]
    p = db.query(Pagamento).filter(
        Pagamento.id == pagamento_id,
        Pagamento.orcamento_id.in_(ids_orcamentos),
    ).first()
    if not p:
        raise HTTPException(404)

    form = dict(await request.form())
    valor = moeda_num(form.get("valor"))
    forma = (form.get("forma") or "").strip()
    banco = forma
    data_pag = data_form(form.get("data") or "")
    if valor <= 0 or not data_pag or not forma:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=edicao_pagamento",
            status_code=303,
        )

    o = db.query(Orcamento).filter(Orcamento.id == p.orcamento_id).first()
    total = totais_orcamento(o)["aprovado"] if o else 0
    outros = sum(float(item.valor or 0) for item in db.query(Pagamento).filter(
        Pagamento.orcamento_id == p.orcamento_id, Pagamento.id != pagamento_id
    ).all())
    if valor > round(total - outros, 2) + 0.009:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=pagamento_excedente", status_code=303
        )

    p.valor = round(valor, 2)
    p.data = data_pag
    p.forma = forma
    p.banco = banco
    nome_comprovante = (form.get("observacao") or "").strip()
    prefixo = _obs_pagamento_padrao(m.equipamento, m.cliente)
    p.observacao = (nome_comprovante if nome_comprovante.startswith(prefixo) else _obs_pagamento_padrao(m.equipamento, m.cliente, nome_comprovante)) or None
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-4", status_code=303)



@app.post("/organiza/manutencoes/{manutencao_id}/pagamento/registrar")
async def manutencao_pagamento_registrar(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Registra pagamento sem alterar ou bloquear a etapa operacional."""
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    o = _orcamento_atual(m)
    if not o:
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_pagamento=Orçamento ainda não disponível#pagamento-independente", status_code=303)
    form = dict(await request.form())
    valor = moeda_num((form.get("valor") or "").strip())
    data_pag = data_form(form.get("data") or "") or date.today()
    forma = (form.get("forma") or "").strip()
    if valor <= 0 or not forma:
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_pagamento=Informe valor e forma de pagamento#pagamento-independente", status_code=303)
    totais = totais_orcamento(o)
    falta = float(totais.get("falta", 0) or 0)
    if valor > falta + 0.01:
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_pagamento=O pagamento não pode ser maior que o saldo a receber#pagamento-independente", status_code=303)
    nome_comprovante = (form.get("observacao") or "").strip()
    prefixo = _obs_pagamento_padrao(m.equipamento, m.cliente)
    observacao = (nome_comprovante if nome_comprovante.startswith(prefixo) else _obs_pagamento_padrao(m.equipamento, m.cliente, nome_comprovante)) or None
    db.add(Pagamento(orcamento_id=o.id, data=data_pag, valor=round(valor, 2), forma=forma, banco=forma, observacao=observacao))
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?pagamento_salvo=1#pagamento-independente", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/etapa-4/salvar")
async def manutencao_etapa4_salvar(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Salva pagamento (quando informado) e prazo prometido em um único formulário."""
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    o = _orcamento_atual(m)
    if not o:
        raise HTTPException(400, "Orçamento não encontrado.")
    form = dict(await request.form())

    prazo_texto = (form.get("prazo") or "").strip()
    try:
        prazo_data = datetime.strptime(prazo_texto, "%Y-%m-%d").date() if prazo_texto else None
    except ValueError:
        prazo_data = None

    valor_texto = (form.get("valor") or "").strip()
    valor = moeda_num(valor_texto) if valor_texto else 0
    recebido_atual = sum(float(p.valor or 0) for p in o.pagamentos)
    totais = totais_orcamento(o)
    total_aprovado = float(totais.get("aprovado", 0) or 0)
    manutencao_sem_cobranca = total_aprovado <= 0.009
    erros = []
    if not prazo_data:
        erros.append("Informe uma data válida para o prazo prometido.")
    if valor < 0:
        erros.append("O valor do pagamento é inválido.")

    falta = float(totais.get("falta", 0) or 0)
    if valor > falta + 0.01:
        erros.append("O pagamento não pode ser maior que o valor que falta receber.")

    if erros:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_etapa4={quote_plus(' '.join(erros))}#etapa-4",
            status_code=303,
        )

    if valor > 0:
        data_pag = data_form(form.get("data") or "") or date.today()
        banco = (form.get("forma") or "").strip() or None
        nome_comprovante = (form.get("observacao") or "").strip()
        prefixo = _obs_pagamento_padrao(m.equipamento, m.cliente)
        observacao = (nome_comprovante if nome_comprovante.startswith(prefixo)
                      else _obs_pagamento_padrao(m.equipamento, m.cliente, nome_comprovante)) or None
        db.add(Pagamento(
            orcamento_id=o.id,
            data=data_pag,
            valor=round(valor, 2),
            forma=banco,
            banco=banco,
            observacao=observacao,
        ))


    m.prazo = prazo_data.strftime("%d/%m/%Y")
    db.commit()
    return RedirectResponse(
        f"/organiza/manutencoes/{manutencao_id}?salvo_etapa4=1#etapa-4",
        status_code=303,
    )


@app.post("/organiza/manutencoes/{manutencao_id}/etapa-4/avancar")
def manutencao_etapa4_avancar(
    manutencao_id: int,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    o = _orcamento_atual(m)
    erros = []
    if not m.prazo:
        erros.append("Informe o prazo prometido.")
    if erros:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_etapa4={quote_plus(' '.join(erros))}#etapa-4",
            status_code=303,
        )
    m.status = "Em manutenção"
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-5", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/prazo")
async def prazo_salvar(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first(); form = dict(await request.form())
    m.prazo = (form.get("prazo") or "").strip() or None; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)



def _orcamento_atual(m):
    return sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None


@app.get("/organiza/manutencoes/{manutencao_id}/confirmar-prazo-whatsapp")
def confirmar_prazo_whatsapp(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    o = _orcamento_atual(m)
    if not o or not m.prazo:
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=prazo", status_code=303)
    m.confirmacao_prazo_em = datetime.now()
    db.commit()
    mensagem = (
        f"Olá, {m.cliente.nome}!\n\n"
        "✅ Prazo do serviço confirmado.\n\n"
        f"Equipamento: {descricao_equipamento(m.equipamento)}\n"
        f"Código técnico: {codigo_tecnico(m.equipamento)}\n"
        f"Prazo previsto: {m.prazo}\n\n"
        "O prazo foi registrado. O pagamento é tratado separadamente e não interfere no andamento da manutenção.\n\nKaraokê RJ"
    )
    url = ComunicacaoService.registrar_e_url(db, HistoricoComunicacao, m, usuario, "PRAZO", mensagem)
    return RedirectResponse(url, status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/pausar-servico")
async def pausar_servico(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m or etapa_manutencao(m) != 5:
        raise HTTPException(400, "Etapa indisponível.")
    form = dict(await request.form())
    descricao = (form.get("compra_descricao") or "").strip()
    previsao = (form.get("compra_previsao") or "").strip()
    if not descricao:
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=compra", status_code=303)
    m.compra_descricao = descricao
    m.compra_previsao = previsao or None
    m.servico_pausado_em = datetime.now()
    m.compra_comunicada_em = None
    m.status = "Aguardando peça"
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-5", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/retomar-servico")
def retomar_servico(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    m.servico_pausado_em = None
    m.status = "Em manutenção"
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-5", status_code=303)


@app.get("/organiza/manutencoes/{manutencao_id}/comunicar-pausa-whatsapp")
def comunicar_pausa_whatsapp(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m or not m.servico_pausado_em or not m.compra_descricao:
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=compra", status_code=303)
    m.compra_comunicada_em = datetime.now()
    db.commit()
    mensagem = (
        f"Olá, {m.cliente.nome}!\n\n"
        "⏸️ Durante a execução do serviço identificamos a necessidade de comprar:\n"
        f"{m.compra_descricao}\n"
        + (f"Previsão: {m.compra_previsao}\n" if m.compra_previsao else "")
        + "\nO serviço ficará pausado até a chegada do item. Manteremos você informado.\n\nKaraokê RJ"
    )
    url = ComunicacaoService.registrar_e_url(db, HistoricoComunicacao, m, usuario, "COMPRA", mensagem)
    return RedirectResponse(url, status_code=303)


@app.get("/organiza/manutencoes/{manutencao_id}/concluir-whatsapp")
def concluir_servico_whatsapp(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    """Conclui a Etapa 5 ou reenvia a comunicação sem bloquear o fluxo.

    A rota é idempotente: depois que o serviço já avançou, pode ser usada
    novamente para reenviar a garantia e o link de retirada.
    """
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)

    etapa = etapa_manutencao(m)
    if etapa < 5 or m.servico_pausado_em or not (m.diagnostico or "").strip():
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=conclusao", status_code=303)

    o = _orcamento_atual(m)
    if not o:
        raise HTTPException(400, "Orçamento não encontrado.")

    # Avança somente na primeira conclusão. Em reenvios, preserva datas e etapa.
    if etapa == 5:
        agora = datetime.now()
        if not m.pronto_em:
            m.pronto_em = agora
        m.conclusao_comunicada_em = agora
        m.status = "Pronto para retirada"
        db.commit()

    mensagem = (
        f"Olá, {m.cliente.nome}!\n\n"
        "✅ Seu serviço foi concluído.\n\n"
        f"Equipamento: {descricao_equipamento(m.equipamento)}\n"
        f"Código técnico: {codigo_tecnico(m.equipamento)}\n\n"
        f"📄 Garantia de 30 dias: {PUBLIC_BASE_URL}/garantia-servico/{o.token}.pdf\n"
        f"📅 Agende a retirada: {PUBLIC_BASE_URL}/retirada/{o.token}\n\n"
        "Karaokê RJ"
    )
    url = ComunicacaoService.registrar_e_url(db, HistoricoComunicacao, m, usuario, "PRONTO", mensagem)
    return RedirectResponse(url, status_code=303)


@app.get("/garantia-servico/{token}.pdf")
def garantia_servico_pdf(token: str, db: Session = Depends(get_db)):
    """Certificado público de 30 dias, acessível pelo link enviado ao cliente."""
    orcamento = db.query(Orcamento).filter(Orcamento.token == token).first()
    if not orcamento:
        raise HTTPException(404)
    m = (
        db.query(Manutencao)
        .options(selectinload(Manutencao.cliente), selectinload(Manutencao.equipamento), selectinload(Manutencao.orcamentos))
        .filter(Manutencao.id == orcamento.manutencao_id)
        .first()
    )
    if not m or not m.pronto_em:
        raise HTTPException(404)
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
    from reportlab.lib import colors
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=15*mm, bottomMargin=15*mm)
    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle("TituloGarantia", parent=estilos["Title"], alignment=TA_CENTER, fontSize=18, leading=22, spaceAfter=8)
    centro = ParagraphStyle("CentroGarantia", parent=estilos["BodyText"], alignment=TA_CENTER, fontSize=10, leading=14)
    corpo = ParagraphStyle("CorpoGarantia", parent=estilos["BodyText"], fontSize=10, leading=15, spaceAfter=8)
    elementos = []
    logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "logo-karaoke-rj.png")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "karaoke-rj-garantia.jpeg")
    logo = Image(logo_path, width=30*mm, height=22*mm) if os.path.exists(logo_path) else Spacer(30*mm, 22*mm)
    empresa = Paragraph(
        "<b>KARAOKE &amp; GAMES RJ</b><br/>CNPJ: 35.458.112/0001-75 · IM: 1213508-4<br/>"
        "Rua João Romariz, 313 - Ramos - Rio de Janeiro/RJ - CEP: 21031-700<br/>"
        "WhatsApp: (21) 99507-9690 / (21) 99650-4516<br/>www.karaokerj.com.br · contato@karaokerj.com.br",
        ParagraphStyle("CabecalhoGarantiaServico", parent=corpo, fontSize=8, leading=10, spaceAfter=0),
    )
    header = Table([[logo, empresa]], colWidths=[35*mm, 139*mm])
    header.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("LINEBELOW",(0,0),(-1,-1),0.8,colors.HexColor("#555555")),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),5),
    ]))
    elementos += [header, Spacer(1, 7), Paragraph("CERTIFICADO DE GARANTIA DO SERVIÇO", titulo), Spacer(1, 4*mm)]
    eq = m.equipamento
    dados = [
        ["Ordem de serviço", f"#{m.id}"],
        ["Cliente", m.cliente.nome],
        ["Equipamento", descricao_equipamento(eq)],
        ["Código técnico", codigo_tecnico(eq)],
        ["Serviço concluído em", m.pronto_em.strftime("%d/%m/%Y")],
        ["Garantia válida até", (m.pronto_em.date() + timedelta(days=30)).strftime("%d/%m/%Y")],
    ]
    tabela = Table(dados, colWidths=[48*mm, 110*mm])
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#F2F4F7")),
        ("TEXTCOLOR", (0,0), (0,-1), colors.HexColor("#344054")),
        ("FONTNAME", (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTNAME", (1,0), (1,-1), "Helvetica"),
        ("GRID", (0,0), (-1,-1), .4, colors.HexColor("#D0D5DD")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("PADDING", (0,0), (-1,-1), 7),
    ]))
    elementos += [tabela, Spacer(1, 8*mm)]
    elementos += [
        Paragraph("<b>Prazo e cobertura</b>", corpo),
        Paragraph("Garantia de 30 dias sobre os serviços executados nesta ordem de serviço, contados a partir da data de conclusão. A garantia cobre exclusivamente o serviço realizado e não inclui mau uso, quedas, líquidos, ligação em tensão incorreta, intervenção de terceiros ou defeitos diferentes do reparo executado.", corpo),
        Spacer(1, 7*mm),
        Paragraph("Karaokê RJ · Rua João Romariz, 313 - Ramos - Rio de Janeiro/RJ<br/>WhatsApp: (21) 99507-9690 / (21) 99650-4516 · www.karaokerj.com.br", centro),
    ]
    doc.build(elementos)
    return Response(buffer.getvalue(), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="garantia-os-{m.id}.pdf"'})

@app.post("/organiza/manutencoes/{manutencao_id}/pronto")
def manutencao_pronto(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first(); m.status = "Pronto para retirada"; m.pronto_em = datetime.now(); db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/retirada")
async def retirada_agendar(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Agenda a retirada e mantém a manutenção na Etapa 6.

    O lançamento interno não deve apagar silenciosamente horários fora da janela
    pública de retirada. Depois de salvo, o agendamento libera a ação Entregar.
    """
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
    if not m:
        raise HTTPException(404)

    form = dict(await request.form())
    retirada_em = datetime_form(form.get("retirada_em") or "")
    if not retirada_em:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_retirada=Informe uma data e hora válidas.#etapa-6",
            status_code=303,
        )

    m.retirada_em = retirada_em
    m.status = "Retirada agendada"
    db.commit()
    return RedirectResponse(
        f"/organiza/manutencoes/{manutencao_id}?retirada_salva=1#etapa-6",
        status_code=303,
    )



@app.post("/organiza/manutencoes/{manutencao_id}/diagnostico")
async def manutencao_diagnostico_salvar(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Salva o diagnóstico final dentro da etapa de execução, sem alterar dados da entrada."""
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
    if not m:
        raise HTTPException(404)
    form = dict(await request.form())
    m.diagnostico = (form.get("diagnostico") or "").strip() or None
    m.observacao = (form.get("observacao") or m.observacao or "").strip() or None
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?diagnostico_salvo=1#etapa-5", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/editar")
async def manutencao_editar(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
    if not m: raise HTTPException(404)
    form = dict(await request.form())
    # O equipamento é corrigido por uma ação exclusiva e confirmada.
    # Este formulário altera apenas os demais dados da Etapa 1.
    m.defeito = (form.get("defeito") or "").strip()
    m.observacao = (form.get("observacao") or "").strip() or None
    m.diagnostico = (form.get("diagnostico") or "").strip() or None
    tipo_atendimento = (form.get("tipo_atendimento") or m.tipo_atendimento or "loja").strip().lower()
    agendamento_informado = (form.get("entrega_prevista_em") or "").strip()
    agendamento = datetime_form(agendamento_informado) if agendamento_informado else m.entrega_prevista_em

    # Depois que o equipamento entrou, o técnico pode corrigir diagnóstico e
    # observações sem ser bloqueado por uma previsão antiga, vazia ou vencida.
    alterou_agendamento = agendamento != m.entrega_prevista_em or tipo_atendimento != (m.tipo_atendimento or "loja")
    if tipo_atendimento not in ("loja", "online"):
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_agendamento=1", status_code=303)
    if alterou_agendamento and not m.recebido_em:
        if not horario_atendimento_valido(tipo_atendimento, agendamento) or horario_atendimento_ocupado(db, agendamento, m.id):
            return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_agendamento=1", status_code=303)

    m.tipo_atendimento = tipo_atendimento
    m.entrega_prevista_em = agendamento
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-1", status_code=303)

@app.post("/organiza/manutencoes/{manutencao_id}/corrigir-equipamento")
async def manutencao_corrigir_equipamento(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Corrige o equipamento da OS e mantém todo o histórico ligado ao equipamento correto."""
    m = (
        db.query(Manutencao)
        .options(selectinload(Manutencao.equipamento), selectinload(Manutencao.cliente))
        .filter(Manutencao.id == manutencao_id)
        .first()
    )
    if not m:
        raise HTTPException(404)

    form = dict(await request.form())
    try:
        equipamento_id = int(form.get("equipamento_id") or 0)
    except (TypeError, ValueError):
        equipamento_id = 0

    equipamento_novo = db.query(Equipamento).filter(
        Equipamento.id == equipamento_id,
        Equipamento.status == "Ativo",
    ).first()
    if not equipamento_novo:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_equipamento=1#etapa-1",
            status_code=303,
        )

    # A manutenção pertence ao equipamento. O cliente é sempre derivado do dono atual dele.
    equipamento_antigo = m.equipamento
    if equipamento_antigo and equipamento_antigo.id == equipamento_novo.id:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?equipamento_inalterado=1#etapa-1",
            status_code=303,
        )

    identificacao_antiga = (
        f"{rotulo_maquina(equipamento_antigo)} / {codigo_tecnico(equipamento_antigo)}"
        if equipamento_antigo else "não informado"
    )
    identificacao_nova = f"{rotulo_maquina(equipamento_novo)} / {codigo_tecnico(equipamento_novo)}"
    registro = (
        f"[{datetime.now().strftime('%d/%m/%Y %H:%M')}] "
        f"Equipamento corrigido de {identificacao_antiga} para {identificacao_nova}."
    )

    m.equipamento_id = equipamento_novo.id
    m.cliente_id = equipamento_novo.cliente_id
    observacao_atual = (m.observacao or "").strip()
    m.observacao = f"{observacao_atual}\n{registro}".strip()
    db.commit()

    return RedirectResponse(
        f"/organiza/manutencoes/{manutencao_id}?equipamento_corrigido=1#etapa-1",
        status_code=303,
    )


@app.post("/organiza/manutencoes/{manutencao_id}/receber")
def manutencao_receber(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
    if not m: raise HTTPException(404)
    m.recebido_em = datetime.now(); m.status = "Orçamento em elaboração"; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/item/{orcamento_item_id}/editar")
async def orcamento_item_editar(manutencao_id: int, orcamento_item_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    item = db.query(OrcamentoItem).filter(OrcamentoItem.id == orcamento_item_id).first()
    if not item: raise HTTPException(404)
    item.descricao = (form.get("descricao") or item.descricao).strip()
    item.quantidade = max(int(form.get("quantidade") or 1), 1)
    item.preco_venda = moeda_num(form.get("preco_venda"))
    item.opcional = 1 if form.get("opcional") else 0
    if not item.opcional: item.aprovado = 1
    db.commit(); return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

@app.post("/organiza/manutencoes/{manutencao_id}/pagamento/{pagamento_id}/editar")
async def pagamento_editar(manutencao_id: int, pagamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    p = db.query(Pagamento).filter(Pagamento.id == pagamento_id).first()
    if not p: raise HTTPException(404)
    form = dict(await request.form())
    valor = moeda_num(form.get("valor"))
    o = db.query(Orcamento).filter(Orcamento.id == p.orcamento_id).first()
    total = totais_orcamento(o)["aprovado"] if o else 0
    outros = sum(float(item.valor or 0) for item in db.query(Pagamento).filter(
        Pagamento.orcamento_id == p.orcamento_id, Pagamento.id != pagamento_id
    ).all())
    if valor <= 0 or valor > round(total - outros, 2) + 0.009:
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=pagamento_excedente", status_code=303)
    p.data = data_form(form.get("data") or "") or p.data
    p.valor = round(valor, 2)
    p.forma = (form.get("forma") or "PIX").strip()
    p.banco = p.forma
    p.observacao = (form.get("observacao") or "").strip() or None
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

@app.post("/organiza/manutencoes/{manutencao_id}/pagamento/{pagamento_id}/excluir")
def pagamento_excluir(manutencao_id: int, pagamento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    p = db.query(Pagamento).filter(Pagamento.id == pagamento_id).first()
    if not p:
        raise HTTPException(404)
    integ = _registro_integracao(db, "manutencao", p.id)
    if integ and integ.enviado_em:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_fluxo=pagamento_enviado_connect", status_code=303
        )
    if integ:
        db.delete(integ)
    db.delete(p)
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

@app.post("/organiza/manutencoes/{manutencao_id}/encerrar")
def manutencao_encerrar(
    manutencao_id: int,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Confirma a entrega e encerra todas as pendências operacionais da OS."""
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    if not m.retirada_em:
        return RedirectResponse(
            f"/organiza/manutencoes/{manutencao_id}?erro_retirada=Salve a data e hora da retirada antes de entregar.#etapa-6",
            status_code=303,
        )

    agora = datetime.now()
    m.entregue_em = agora
    m.status = "Encerrada"
    m.servico_pausado_em = None

    # O equipamento volta imediatamente ao estoque operacional.
    if m.equipamento:
        m.equipamento.status = "Ativo"

    # A agenda é derivada da própria manutenção e deixa de exibir a OS assim
    # que entregue_em/status Encerrada são gravados. Mantemos retirada_em como
    # histórico do agendamento que levou à entrega.
    db.commit()
    return RedirectResponse(
        f"/organiza/manutencoes/{manutencao_id}?entregue=1#etapa-7",
        status_code=303,
    )


def _manutencoes_operacao(db: Session):
    return (
        db.query(Manutencao)
        .options(
            selectinload(Manutencao.cliente),
            selectinload(Manutencao.equipamento),
            selectinload(Manutencao.orcamentos).selectinload(Orcamento.itens),
            selectinload(Manutencao.orcamentos).selectinload(Orcamento.pagamentos),
        )
        .filter(Manutencao.entregue_em.is_(None), ~Manutencao.status.in_(("Encerrada", "Cancelada")))
        .order_by(Manutencao.criado_em.asc(), Manutencao.id.asc())
        .all()
    )


def _saldo_manutencao(m: Manutencao):
    o = _orcamento_atual(m)
    if not o:
        return 0.0, 0.0, 0.0
    totais = totais_orcamento(o)
    total = float(totais.get("aprovado", 0) or 0)
    recebido = sum(float(p.valor or 0) for p in o.pagamentos)
    return total, recebido, max(total - recebido, 0)


def _agrupar_por_cliente(manutencoes):
    grupos = {}
    for m in manutencoes:
        grupo = grupos.setdefault(m.cliente_id, {
            "cliente": m.cliente,
            "manutencoes": [],
            "total": 0.0,
            "recebido": 0.0,
            "saldo": 0.0,
        })
        grupo["manutencoes"].append(m)
        total, recebido, saldo = _saldo_manutencao(m)
        grupo["total"] += total
        grupo["recebido"] += recebido
        grupo["saldo"] += saldo
    return sorted(grupos.values(), key=lambda g: (g["cliente"].nome or "").lower())


templates.env.globals["_saldo_manutencao"] = _saldo_manutencao


def _orcamento_pronto_para_comunicar(m):
    """Orçamento completo, salvo e ainda não enviado ao cliente."""
    o = _orcamento_atual(m)
    if not o:
        return False
    tem_valor = float(o.valor_manutencao or 0) > 0 or any(float(i.preco_venda or 0) > 0 for i in o.itens)
    tem_condicoes = bool(o.forma_pagamento_orcamento) and o.prazo_dias_uteis is not None
    nao_enviado = o.status in ("Rascunho", "Em elaboração", "Pronto", None, "")
    return etapa_manutencao(m) == 2 and bool(o.itens or float(o.valor_manutencao or 0) > 0) and tem_valor and tem_condicoes and nao_enviado


def _tipo_comunicacao(m):
    if _orcamento_pronto_para_comunicar(m):
        return "orcamento"
    if etapa_manutencao(m) == 6 and m.pronto_em:
        return "pronto"
    return None


def _status_comunicacao(m, tipo):
    o = _orcamento_atual(m)
    if tipo == "orcamento":
        return bool(o and o.status in ("Enviado", "Aguardando aprovação"))
    if tipo == "pronto":
        return bool(m.conclusao_comunicada_em)
    return False


def _grupos_comunicacao(manutencoes, incluir_comunicados=True):
    grupos = {}
    for m in manutencoes:
        tipo = _tipo_comunicacao(m)
        # Também traz os já comunicados da etapa correspondente para permitir reenvio.
        if not tipo:
            o = _orcamento_atual(m)
            if etapa_manutencao(m) == 3 and o and o.status in ("Enviado", "Aguardando aprovação"):
                tipo = "orcamento"
            elif etapa_manutencao(m) == 6 and m.pronto_em:
                tipo = "pronto"
        if not tipo:
            continue
        comunicado = _status_comunicacao(m, tipo)
        if comunicado and not incluir_comunicados:
            continue
        chave = (m.cliente_id, tipo)
        grupo = grupos.setdefault(chave, {
            "cliente": m.cliente, "tipo": tipo, "manutencoes": [],
            "comunicado": True, "ultima_comunicacao": None,
        })
        grupo["manutencoes"].append(m)
        grupo["comunicado"] = grupo["comunicado"] and comunicado
        datas = []
        o = _orcamento_atual(m)
        if tipo == "orcamento" and o and o.status in ("Enviado", "Aguardando aprovação"):
            datas.append(o.criado_em)
        if tipo == "pronto" and m.conclusao_comunicada_em:
            datas.append(m.conclusao_comunicada_em)
        for d in datas:
            if d and (grupo["ultima_comunicacao"] is None or d > grupo["ultima_comunicacao"]):
                grupo["ultima_comunicacao"] = d
    resultado = list(grupos.values())
    for grupo in resultado:
        grupo["manutencoes"].sort(key=lambda m: (prefixo_equipamento(m.equipamento.tipo), m.equipamento.numero_maquina_cliente or 999999))
    return sorted(resultado, key=lambda g: (g["comunicado"], (g["cliente"].nome or "").lower(), g["tipo"]))


def _montar_mensagem_comunicacao(cliente, selecionadas, tipo):
    if not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)

    linhas = [f"Olá, {cliente.nome}!"]

    if tipo == "orcamento":
        linhas += ["", "*Seus orçamentos estão prontos:*"]
        total_geral = 0.0

        for m in selecionadas:
            orcamento = _orcamento_atual(m)
            if not orcamento:
                continue

            totais = totais_orcamento(orcamento)
            total_obrigatorio = totais["obrigatorio_final"]
            total_completo = totais["geral"]
            total_geral += total_obrigatorio

            linhas += ["", f"*{rotulo_maquina(m.equipamento)}*"]

            obrigatorios = []
            if orcamento.valor_manutencao and orcamento.valor_manutencao > 0:
                obrigatorios.append("Serviço de manutenção")
            obrigatorios.extend(i.descricao for i in orcamento.itens if not i.opcional)

            if obrigatorios:
                linhas.append("*Itens obrigatórios:*")
                for descricao in dict.fromkeys(obrigatorios):
                    linhas.append(f"• {descricao}")

            opcionais = [i for i in orcamento.itens if i.opcional]
            if opcionais:
                linhas.append("*Opcionais:*")
                for item in opcionais:
                    valor_opcional = float(item.preco_venda or 0) * int(item.quantidade or 0)
                    quantidade = int(item.quantidade or 0)
                    complemento_quantidade = f" ({quantidade}x)" if quantidade > 1 else ""
                    linhas.append(
                        f"• {item.descricao}{complemento_quantidade} — {formatar_moeda(valor_opcional)}"
                    )

            linhas.append(f"Total obrigatório: {formatar_moeda(total_obrigatorio)}")
            if totais["desconto_condicional"] or totais["opcionais"] > 0:
                if totais["desconto_informado"] > 0:
                    if totais["desconto_condicional"]:
                        rotulo_desconto = "Desconto ao aprovar todos os opcionais"
                    else:
                        rotulo_desconto = "Desconto aplicado ao total do orçamento"
                    linhas.append(
                        f"{rotulo_desconto}: - {formatar_moeda(totais['desconto_informado'])}"
                    )
                linhas.append(
                    f"Total com opcionais e desconto: {formatar_moeda(total_completo)}"
                )

        if len(selecionadas) > 1:
            totais_validos = [
                totais_orcamento(_orcamento_atual(item))
                for item in selecionadas
                if _orcamento_atual(item)
            ]
            total_geral_completo = sum(item["geral"] for item in totais_validos)
            linhas += ["", f"*Total geral obrigatório: {formatar_moeda(total_geral)}*"]
            if any(item["desconto_condicional"] or item["opcionais"] > 0 for item in totais_validos):
                linhas.append(
                    f"*Total geral com opcionais e descontos: {formatar_moeda(total_geral_completo)}*"
                )

        linhas += ["", "Abra o link para revisar e responder cada orçamento."]
    else:
        linhas += ["", f"✅ Seus {len(selecionadas)} equipamento(s) estão prontos:"]
        for m in selecionadas:
            linhas.append(f"• {descricao_equipamento(m.equipamento)}")
        linhas += ["", "Acesse para consultar as garantias e agendar uma única retirada."]

    linhas += ["", "Acompanhe tudo aqui:", f"{PUBLIC_BASE_URL}/acompanhar/{cliente.token_ficha}", "", "Karaokê RJ"]
    return "\n".join(linhas)

def _fila_operacional_exclusiva(m):
    """Retorna uma única fila operacional para cada manutenção."""
    etapa = etapa_manutencao(m)

    if etapa == 1:
        return "atendimento"

    if etapa == 2:
        return "comunicar_orcamentos" if _orcamento_pronto_para_comunicar(m) else "orcamentos"

    if etapa == 3:
        return "aprovacoes"

    if etapa == 4:
        return "pagamentos"

    if etapa == 5:
        return "pausados" if m.servico_pausado_em else "execucao"

    if etapa == 6:
        return "prontos" if not m.conclusao_comunicada_em else "retiradas"

    return None




ETAPAS_OPERACAO_AGRUPADA = {
    "atendimento": {
        "titulo": "Aguardando atendimento ou entrega",
        "descricao": "Selecione os equipamentos entregues pelo cliente e confirme todos de uma vez.",
    },
    "aprovacoes": {
        "titulo": "Aguardando aprovação",
        "descricao": "Acompanhe e reenvie, em uma única mensagem, os orçamentos selecionados do cliente.",
    },
    "execucao": {
        "titulo": "Em execução",
        "descricao": "Somente equipamentos aprovados e liberados para execução.",
    },
    "pausados": {
        "titulo": "Aguardando item",
        "descricao": "Equipamentos em execução pausados por peça ou material.",
    },
    "retiradas": {
        "titulo": "Aguardando retirada",
        "descricao": "Selecione os equipamentos entregues ao cliente e finalize todos de uma vez.",
    },
}


@app.get("/organiza/operacao/etapa/{chave}", response_class=HTMLResponse)
def operacao_etapa_agrupada(
    chave: str,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    configuracao = ETAPAS_OPERACAO_AGRUPADA.get(chave)
    if not configuracao:
        raise HTTPException(404)
    manutencoes = [
        m for m in _manutencoes_operacao(db)
        if _fila_operacional_exclusiva(m) == chave
    ]
    grupos = _agrupar_por_cliente(manutencoes)
    if chave == "aprovacoes":
        for grupo in grupos:
            grupo["comunicado"] = all(_status_comunicacao(m, "orcamento") for m in grupo["manutencoes"])
    return templates.TemplateResponse("organiza/operacao_etapa_agrupada.html", {
        "request": request,
        "usuario": usuario,
        "chave": chave,
        "titulo": configuracao["titulo"],
        "descricao": configuracao["descricao"],
        "grupos": grupos,
        "data_hoje": datetime.now().date().isoformat(),
        "sucesso": request.query_params.get("sucesso", ""),
        "erro": request.query_params.get("erro", ""),
    })


@app.post("/organiza/operacao/execucao/concluir")
async def operacao_execucao_concluir(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    form = await request.form()
    try:
        ids = [int(x) for x in form.getlist("manutencao_id")]
    except ValueError:
        ids = []
    selecionadas = [m for m in _manutencoes_operacao(db) if m.id in ids and _fila_operacional_exclusiva(m) == "execucao"]
    if not selecionadas:
        return RedirectResponse("/organiza/operacao/etapa/execucao?erro=Selecione pelo menos um equipamento", status_code=303)

    agora = datetime.now()
    for m in selecionadas:
        # A conclusão pela Central apenas avança para a etapa de comunicação.
        # O cliente ainda não é marcado como comunicado aqui.
        if not m.pronto_em:
            m.pronto_em = agora
        m.status = "Pronto para retirada"
        m.conclusao_comunicada_em = None
    db.commit()
    return RedirectResponse(
        f"/organiza/operacao/etapa/execucao?sucesso={quote_plus(str(len(selecionadas)) + ' equipamento(s) enviado(s) para Comunicar equipamentos prontos')}",
        status_code=303,
    )


@app.get("/organiza/operacao/execucao/imprimir", response_class=HTMLResponse)
def operacao_execucao_imprimir(
    request: Request,
    ids: str = "",
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    manutencao_ids = {int(valor) for valor in ids.split(",") if valor.strip().isdigit()}
    manutencoes = [
        m for m in _manutencoes_operacao(db)
        if m.id in manutencao_ids and _fila_operacional_exclusiva(m) == "execucao"
    ]
    manutencoes.sort(key=lambda m: ((m.cliente.nome or "").lower(), m.id))

    fichas = []
    for m in manutencoes:
        orcamento = _orcamento_atual(m)
        itens = []
        if orcamento:
            itens = [
                item for item in orcamento.itens
                if (not item.opcional) or item.aprovado
            ]
        total, recebido, saldo = _saldo_manutencao(m)
        fichas.append({
            "manutencao": m,
            "itens": itens,
            "total": total,
            "recebido": recebido,
            "saldo": saldo,
        })

    return templates.TemplateResponse("organiza/operacao_execucao_impressao.html", {
        "request": request,
        "usuario": usuario,
        "fichas": fichas,
        "data_impressao": datetime.now(),
    })


@app.post("/organiza/operacao/receber")
async def operacao_receber_em_lote(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    form = await request.form()
    try:
        ids = sorted({int(x) for x in form.getlist("manutencao_id")})
    except ValueError:
        ids = []
    selecionadas = [
        m for m in _manutencoes_operacao(db)
        if m.id in ids and _fila_operacional_exclusiva(m) == "atendimento"
    ]
    if not selecionadas:
        return RedirectResponse(
            "/organiza/operacao/etapa/atendimento?erro=Selecione pelo menos um equipamento",
            status_code=303,
        )
    data_texto = (form.get("data_recebimento") or "").strip()
    try:
        data_recebimento = datetime.strptime(data_texto, "%Y-%m-%d").date()
    except ValueError:
        return RedirectResponse(
            "/organiza/operacao/etapa/atendimento?erro=Informe uma data de recebimento válida",
            status_code=303,
        )

    horario_atual = datetime.now().time().replace(microsecond=0)
    recebido_em = datetime.combine(data_recebimento, horario_atual)
    for m in selecionadas:
        m.recebido_em = recebido_em
        m.status = "Orçamento em elaboração"
    db.commit()
    return RedirectResponse(
        f"/organiza/operacao/etapa/atendimento?sucesso={len(selecionadas)} equipamento(s) recebido(s)",
        status_code=303,
    )


@app.post("/organiza/operacao/finalizar-retiradas")
async def operacao_finalizar_retiradas(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    form = await request.form()
    try:
        ids = sorted({int(x) for x in form.getlist("manutencao_id")})
    except ValueError:
        ids = []

    selecionadas = [
        m for m in _manutencoes_operacao(db)
        if m.id in ids and _fila_operacional_exclusiva(m) == "retiradas"
    ]
    if not selecionadas:
        return RedirectResponse(
            "/organiza/operacao/etapa/retiradas?erro=Selecione pelo menos um equipamento",
            status_code=303,
        )

    data_texto = (form.get("data_entrega") or "").strip()
    try:
        data_entrega = datetime.strptime(data_texto, "%Y-%m-%d").date()
    except ValueError:
        return RedirectResponse(
            "/organiza/operacao/etapa/retiradas?erro=Informe uma data de entrega válida",
            status_code=303,
        )

    horario_atual = datetime.now().time().replace(microsecond=0)
    entregue_em = datetime.combine(data_entrega, horario_atual)

    for m in selecionadas:
        m.entregue_em = entregue_em
        if not m.retirada_em:
            m.retirada_em = entregue_em
        m.status = "Encerrada"
        if m.equipamento:
            m.equipamento.status = "Ativo"

    db.commit()
    return RedirectResponse(
        f"/organiza/operacao/etapa/retiradas?sucesso={len(selecionadas)} equipamento(s) finalizado(s)",
        status_code=303,
    )


@app.get("/organiza/operacao/orcamentos", response_class=HTMLResponse)
def operacao_orcamentos(
    request: Request,
    abrir: int | None = None,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Fila de orçamentos editável sem entrar na manutenção."""
    manutencoes = [
        m for m in _manutencoes_operacao(db)
        if _fila_operacional_exclusiva(m) == "orcamentos"
    ]
    itens_catalogo = (
        db.query(Item)
        .filter(Item.ativo == 1)
        .order_by(Item.categoria.asc(), Item.nome.asc())
        .all()
    )
    grupos_por_cliente = {}
    for manutencao in manutencoes:
        grupo = grupos_por_cliente.setdefault(
            manutencao.cliente_id,
            {"cliente": manutencao.cliente, "manutencoes": []},
        )
        grupo["manutencoes"].append(manutencao)

    return templates.TemplateResponse("organiza/operacao_orcamentos.html", {
        "request": request,
        "usuario": usuario,
        "manutencoes": manutencoes,
        "grupos": list(grupos_por_cliente.values()),
        "itens_catalogo": itens_catalogo,
        "abrir": abrir,
        "sucesso": request.query_params.get("sucesso", ""),
        "erro": request.query_params.get("erro", ""),
    })


@app.post("/organiza/operacao/orcamentos/salvar-lote")
async def operacao_orcamentos_salvar_lote(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Salva diagnósticos e orçamentos selecionados em uma única ação."""
    form = await request.form()
    try:
        ids = sorted({int(valor) for valor in form.getlist("manutencao_id")})
    except (TypeError, ValueError):
        ids = []

    if not ids:
        return RedirectResponse(
            "/organiza/operacao/orcamentos?erro=Selecione pelo menos um equipamento",
            status_code=303,
        )

    manutencoes = [
        m for m in _manutencoes_operacao(db)
        if m.id in ids and _fila_operacional_exclusiva(m) == "orcamentos"
    ]
    if len(manutencoes) != len(ids):
        return RedirectResponse(
            "/organiza/operacao/orcamentos?erro=Um ou mais equipamentos não estão aguardando orçamento",
            status_code=303,
        )

    erros = []
    preparados = []
    for m in manutencoes:
        diagnostico = (form.get(f"diagnostico_{m.id}") or "").strip()
        valor_manutencao = max(moeda_num(form.get(f"valor_manutencao_{m.id}")), 0)
        forma = (form.get(f"forma_pagamento_orcamento_{m.id}") or "").strip()
        try:
            prazo = int(form.get(f"prazo_dias_uteis_{m.id}") or 0)
        except (TypeError, ValueError):
            prazo = 0

        if not diagnostico:
            erros.append(f"{m.equipamento.codigo}: informe o diagnóstico")
        if valor_manutencao <= 0:
            erros.append(f"{m.equipamento.codigo}: informe o valor da manutenção")
        if forma not in ("À vista", "50% de sinal + 50% na entrega"):
            erros.append(f"{m.equipamento.codigo}: informe a forma de pagamento")
        if prazo <= 0:
            erros.append(f"{m.equipamento.codigo}: informe o prazo em dias úteis")

        item_ids = form.getlist(f"item_id_{m.id}")
        quantidades = form.getlist(f"quantidade_{m.id}")
        opcionais = set(form.getlist(f"opcional_{m.id}"))
        novos_itens = []
        for indice, item_id_texto in enumerate(item_ids):
            if not item_id_texto:
                continue
            try:
                item_id = int(item_id_texto)
                quantidade = max(int(quantidades[indice] if indice < len(quantidades) else 1), 1)
            except (TypeError, ValueError):
                continue
            item = db.query(Item).filter(Item.id == item_id, Item.ativo == 1).first()
            if item:
                novos_itens.append((item, quantidade, str(indice) in opcionais))

        preparados.append({
            "manutencao": m, "diagnostico": diagnostico,
            "valor": valor_manutencao, "forma": forma, "prazo": prazo,
            "desconto": max(moeda_num(form.get(f"desconto_{m.id}")), 0),
            "desconto_opcionais": bool(form.get(f"desconto_somente_com_opcionais_{m.id}")),
            "itens": novos_itens,
        })

    if erros:
        return RedirectResponse(
            f"/organiza/operacao/orcamentos?erro={quote_plus(' | '.join(erros[:5]))}",
            status_code=303,
        )

    for dados in preparados:
        m = dados["manutencao"]
        o = _orcamento_atual(m)
        if not o:
            o = Orcamento(manutencao_id=m.id, versao=1, token=secrets.token_urlsafe(24), status="Rascunho")
            db.add(o); db.flush()
        for antigo in list(o.itens):
            db.delete(antigo)
        db.flush()
        total_itens = 0
        for item, quantidade, opcional in dados["itens"]:
            total_itens += float(item.preco_venda or 0) * quantidade
            db.add(OrcamentoItem(
                orcamento_id=o.id, item_id=item.id, descricao=item.nome,
                quantidade=quantidade, preco_custo=float(item.preco_custo or 0),
                preco_venda=float(item.preco_venda or 0),
                opcional=1 if opcional else 0, aprovado=0 if opcional else 1,
            ))
        m.diagnostico = dados["diagnostico"]
        o.valor_manutencao = dados["valor"]
        o.forma_pagamento_orcamento = dados["forma"]
        o.prazo_dias_uteis = dados["prazo"]
        o.desconto = min(dados["desconto"], dados["valor"] + total_itens)
        o.desconto_somente_com_opcionais = 1 if dados["desconto_opcionais"] else 0
        o.status = "Pronto"
        m.status = "Orçamento pronto"

    db.commit()
    return RedirectResponse(
        f"/organiza/operacao/orcamentos?sucesso={len(preparados)} orçamento(s) salvo(s) e enviado(s) para a próxima etapa",
        status_code=303,
    )


@app.post("/organiza/operacao/orcamentos/{manutencao_id}/salvar")
async def operacao_orcamento_salvar(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Salva o orçamento completo em uma única ação e move para comunicação."""
    m = carregar_manutencao(db, manutencao_id)
    if not m or etapa_manutencao(m) != 2:
        return RedirectResponse(
            "/organiza/operacao/orcamentos?erro=Este equipamento não está aguardando orçamento",
            status_code=303,
        )

    o = _orcamento_atual(m)
    if not o:
        o = Orcamento(
            manutencao_id=m.id,
            versao=1,
            token=secrets.token_urlsafe(24),
            status="Rascunho",
        )
        db.add(o)
        db.flush()

    form = await request.form()
    valor_manutencao = max(moeda_num(form.get("valor_manutencao")), 0)
    forma = (form.get("forma_pagamento_orcamento") or "").strip()
    if forma not in ("À vista", "50% de sinal + 50% na entrega"):
        forma = ""
    try:
        prazo = int(form.get("prazo_dias_uteis") or 0)
    except (TypeError, ValueError):
        prazo = 0

    item_ids = form.getlist("item_id")
    quantidades = form.getlist("quantidade")
    opcionais = set(form.getlist("opcional"))

    novos_itens = []
    for indice, item_id_texto in enumerate(item_ids):
        if not item_id_texto:
            continue
        try:
            item_id = int(item_id_texto)
            quantidade = max(int(quantidades[indice] if indice < len(quantidades) else 1), 1)
        except (TypeError, ValueError):
            continue
        item = db.query(Item).filter(Item.id == item_id, Item.ativo == 1).first()
        if not item:
            continue
        opcional = str(indice) in opcionais
        novos_itens.append(OrcamentoItem(
            orcamento_id=o.id,
            item_id=item.id,
            descricao=item.nome,
            quantidade=quantidade,
            preco_custo=float(item.preco_custo or 0),
            preco_venda=float(item.preco_venda or 0),
            opcional=1 if opcional else 0,
            aprovado=0 if opcional else 1,
        ))

    if valor_manutencao <= 0:
        return RedirectResponse(
            f"/organiza/operacao/orcamentos?abrir={m.id}&erro=Informe o valor da manutenção",
            status_code=303,
        )
    if not forma:
        return RedirectResponse(
            f"/organiza/operacao/orcamentos?abrir={m.id}&erro=Informe a forma de pagamento",
            status_code=303,
        )
    if prazo <= 0:
        return RedirectResponse(
            f"/organiza/operacao/orcamentos?abrir={m.id}&erro=Informe o prazo em dias úteis",
            status_code=303,
        )

    # Substitui o rascunho em uma única gravação para evitar itens duplicados.
    for item_antigo in list(o.itens):
        db.delete(item_antigo)
    db.flush()
    for novo in novos_itens:
        db.add(novo)

    o.valor_manutencao = valor_manutencao
    o.forma_pagamento_orcamento = forma
    o.prazo_dias_uteis = prazo
    o.desconto = min(max(moeda_num(form.get("desconto")), 0), valor_manutencao + sum(i.preco_venda * i.quantidade for i in novos_itens))
    o.desconto_somente_com_opcionais = 1 if form.get("desconto_somente_com_opcionais") else 0
    o.status = "Pronto"
    m.status = "Orçamento pronto"
    db.commit()

    return RedirectResponse(
        "/organiza/operacao/orcamentos?sucesso=Orçamento salvo e enviado para a fila de comunicação",
        status_code=303,
    )


@app.get("/organiza/operacao", response_class=HTMLResponse)
def operacao_painel(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    contexto = _contexto_operacao(db)
    contexto.update({"request": request, "usuario": usuario, "pagina_inicial": False})
    return templates.TemplateResponse("organiza/operacao.html", contexto)


@app.get("/organiza/operacao/comunicacoes", response_class=HTMLResponse)
def operacao_comunicacoes(request: Request, tipo: str = "todos", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    manutencoes = _manutencoes_operacao(db)
    grupos = _grupos_comunicacao(manutencoes, incluir_comunicados=True)
    if tipo in ("orcamento", "pronto"):
        grupos = [g for g in grupos if g["tipo"] == tipo]
    return templates.TemplateResponse("organiza/operacao_comunicacoes.html", {
        "request": request, "usuario": usuario, "grupos": grupos, "tipo_filtro": tipo
    })


@app.post("/organiza/operacao/comunicacoes/preparar")
async def operacao_comunicacoes_preparar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = await request.form()
    try:
        ids = sorted({int(x) for x in form.getlist("manutencao_id")})
    except ValueError:
        ids = []
    tipo = (form.get("tipo") or "").strip()
    selecionadas = [m for m in _manutencoes_operacao(db) if m.id in ids]
    if not selecionadas or tipo not in ("orcamento", "pronto"):
        return JSONResponse({"ok": False, "erro": "Seleção inválida."}, status_code=400)
    if len({m.cliente_id for m in selecionadas}) != 1:
        return JSONResponse({"ok": False, "erro": "Selecione equipamentos de um único cliente."}, status_code=400)
    cliente = selecionadas[0].cliente
    if tipo == "orcamento":
        for m in selecionadas:
            o = _orcamento_atual(m)
            if o:
                o.status = "Enviado"
                m.status = "Aguardando aprovação"
    else:
        agora = datetime.now()
        for m in selecionadas:
            m.conclusao_comunicada_em = m.conclusao_comunicada_em or agora
    mensagem = _montar_mensagem_comunicacao(cliente, selecionadas, tipo)
    db.commit()
    for manutencao in selecionadas:
        ComunicacaoService.registrar(db, HistoricoComunicacao, manutencao, usuario, tipo.upper(), mensagem=mensagem)
    return JSONResponse({
        "ok": True,
        "whatsapp_url": ComunicacaoService.url_whatsapp(cliente, mensagem),
        "cliente": cliente.nome,
        "quantidade": len(selecionadas),
        "tipo": tipo,
    })


@app.get("/organiza/operacao/comunicacoes/enviar")
def operacao_comunicacoes_enviar(ids: str, tipo: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    """Compatibilidade com links antigos."""
    try:
        manutencao_ids = sorted({int(x) for x in ids.split(",") if x.strip()})
    except ValueError:
        raise HTTPException(400, "Seleção inválida.")
    selecionadas = [m for m in _manutencoes_operacao(db) if m.id in manutencao_ids]
    if not selecionadas:
        return RedirectResponse("/organiza/operacao/comunicacoes", status_code=303)
    if not tipo:
        tipo = _tipo_comunicacao(selecionadas[0]) or "orcamento"
    cliente = selecionadas[0].cliente
    if tipo == "orcamento":
        for m in selecionadas:
            o = _orcamento_atual(m)
            if o:
                o.status = "Enviado"
                m.status = "Aguardando aprovação"
    else:
        for m in selecionadas:
            m.conclusao_comunicada_em = m.conclusao_comunicada_em or datetime.now()
    mensagem = _montar_mensagem_comunicacao(cliente, selecionadas, tipo)
    db.commit()
    for manutencao in selecionadas:
        ComunicacaoService.registrar(db, HistoricoComunicacao, manutencao, usuario, tipo.upper(), mensagem=mensagem)
    return RedirectResponse(ComunicacaoService.url_whatsapp(cliente, mensagem), status_code=303)



def _mensagem_central_generica(cliente, manutencoes, tipo):
    nome = (cliente.nome or "cliente").strip().split()[0]
    itens = "\n".join(f"• {rotulo_maquina(m.equipamento)} - {(m.equipamento.modelo or m.equipamento.tipo or 'equipamento')}" for m in manutencoes)
    cab = f"Olá, {nome}! Aqui é da Karaoke RJ."
    mensagens = {
        "lembrete": f"{cab}\n\nEstamos lembrando do atendimento/entrega dos equipamentos abaixo:\n{itens}\n\nCaso precise alterar a data, fale conosco.",
        "cobranca": f"{cab}\n\nSegue um lembrete sobre o pagamento da manutenção dos equipamentos abaixo:\n{itens}\n\nApós o pagamento, envie o comprovante por aqui.",
        "previsao": f"{cab}\n\nAtualização da manutenção dos equipamentos abaixo:\n{itens}\n\nEm caso de dúvida sobre o prazo, fale conosco.",
    }
    return mensagens.get(tipo, f"{cab}\n\nAtualização sobre:\n{itens}")


@app.get("/organiza/central/comunicar")
def central_comunicar(ids: str, tipo: str, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    try:
        manutencao_ids = sorted({int(x) for x in ids.split(",") if x.strip()})
    except ValueError:
        raise HTTPException(400, "Seleção inválida.")
    selecionadas = [m for m in _manutencoes_operacao(db) if m.id in manutencao_ids]
    if not selecionadas:
        raise HTTPException(404, "Nenhum chamado encontrado.")
    if len({m.cliente_id for m in selecionadas}) != 1:
        raise HTTPException(400, "Selecione equipamentos do mesmo cliente.")
    if tipo in ("orcamento", "pronto"):
        return operacao_comunicacoes_enviar(ids=ids, tipo=tipo, usuario=usuario, db=db)
    if tipo not in ("lembrete", "cobranca", "previsao"):
        raise HTTPException(400, "Tipo de comunicação inválido.")
    cliente = selecionadas[0].cliente
    mensagem = _mensagem_central_generica(cliente, selecionadas, tipo)
    for manutencao in selecionadas:
        ComunicacaoService.registrar(db, HistoricoComunicacao, manutencao, usuario, tipo.upper(), mensagem=mensagem)
    return RedirectResponse(ComunicacaoService.url_whatsapp(cliente, mensagem), status_code=303)

@app.get("/acompanhar/{token}", response_class=HTMLResponse)
def acompanhamento_cliente(token: str, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    manutencoes = (
        db.query(Manutencao)
        .options(
            selectinload(Manutencao.equipamento),
            selectinload(Manutencao.orcamentos).selectinload(Orcamento.itens),
        )
        .filter(Manutencao.cliente_id == cliente.id)
        .order_by(Manutencao.criado_em.desc())
        .all()
    )
    registros = []
    for m in manutencoes:
        o = _orcamento_atual(m)
        if m.entregue_em or (o and o.status in ("Enviado", "Aguardando aprovação", "Aprovado", "Aprovado parcialmente", "Aprovado manualmente")) or m.pronto_em:
            registros.append({
                "m": m,
                "orcamento": o,
                "totais": totais_orcamento(o) if o else None,
                "etapa": etapa_manutencao(m),
                "meta": info_etapa_manutencao(m),
                "retirada_agendada": bool(m.retirada_em),
                "retirada_em": m.retirada_em,
            })
    return templates.TemplateResponse("organiza/acompanhamento_cliente.html", {
        "request": request,
        "cliente": cliente,
        "registros": registros,
        "tem_prontos": any(bool(r["m"].pronto_em) for r in registros),
        "usuario": None,
    })



@app.post("/organiza/operacao/aprovacoes/manual")
async def operacao_aprovacao_manual(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Registra no painel a mesma decisão disponível ao cliente no portal público."""
    form = await request.form()
    try:
        ids = sorted({int(x) for x in form.getlist("manutencao_id")})
    except (TypeError, ValueError):
        ids = []

    acao = (form.get("acao") or "").strip().lower()
    if acao not in {"obrigatorios", "todos", "cancelar"}:
        return JSONResponse(
            {"ok": False, "erro": "Escolha uma ação válida."},
            status_code=400,
        )
    if not ids:
        return JSONResponse(
            {"ok": False, "erro": "Selecione pelo menos um equipamento."},
            status_code=400,
        )

    manutencoes = [
        m for m in _manutencoes_operacao(db)
        if m.id in ids and _fila_operacional_exclusiva(m) == "aprovacoes"
    ]
    if len(manutencoes) != len(ids):
        return JSONResponse(
            {"ok": False, "erro": "Um ou mais equipamentos não estão aguardando aprovação."},
            status_code=400,
        )
    if len({m.cliente_id for m in manutencoes}) != 1:
        return JSONResponse(
            {"ok": False, "erro": "A aprovação manual deve ser feita para um cliente por vez."},
            status_code=400,
        )

    for manutencao in manutencoes:
        orcamento = _orcamento_atual(manutencao)
        if not orcamento:
            return JSONResponse(
                {"ok": False, "erro": f"{codigo_tecnico(manutencao.equipamento)} está sem orçamento."},
                status_code=400,
            )
        if acao == "cancelar":
            orcamento.status = "Cancelado"
            manutencao.status = "Cancelado"
        else:
            registrar_aprovacao_orcamento(
                orcamento,
                "todos" if acao == "todos" else "obrigatorios",
                f"manual por {usuario.nome}",
            )

    db.commit()

    mensagens = {
        "obrigatorios": "Itens obrigatórios aprovados manualmente.",
        "todos": "Orçamento completo aprovado manualmente.",
        "cancelar": "Orçamento cancelado manualmente.",
    }
    return JSONResponse({
        "ok": True,
        "mensagem": mensagens[acao],
        "quantidade": len(manutencoes),
    })


@app.get("/organiza/operacao/pagamentos", response_class=HTMLResponse)
def operacao_pagamentos(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    # Mantém visíveis todos os equipamentos da etapa 4, inclusive os já quitados
    # que ainda aguardam prazo e confirmação. Isso evita que desapareçam da
    # Central antes de avançarem para execução.
    pendentes = [m for m in _manutencoes_operacao(db) if etapa_manutencao(m) == 4]
    return templates.TemplateResponse("organiza/operacao_pagamentos.html", {
        "request": request, "usuario": usuario, "grupos": _agrupar_por_cliente(pendentes),
        "erro": request.query_params.get("erro", ""), "sucesso": request.query_params.get("sucesso", ""), "hoje": date.today().isoformat()
    })


@app.post("/organiza/operacao/pagamentos/registrar")
async def operacao_pagamentos_registrar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = await request.form()
    try:
        ids = [int(x) for x in form.getlist("manutencao_id")]
    except ValueError:
        ids = []

    selecionadas = [m for m in _manutencoes_operacao(db) if m.id in ids and etapa_manutencao(m) == 4]
    if not selecionadas:
        return RedirectResponse("/organiza/operacao/pagamentos?erro=Selecione pelo menos um equipamento", status_code=303)
    if len({m.cliente_id for m in selecionadas}) != 1:
        return RedirectResponse("/organiza/operacao/pagamentos?erro=Selecione equipamentos do mesmo cliente", status_code=303)

    previsao = data_form(form.get("previsao"))
    if not previsao:
        return RedirectResponse("/organiza/operacao/pagamentos?erro=Informe a previsão de conclusão", status_code=303)

    saldos = [(m, _orcamento_atual(m), _saldo_manutencao(m)[2]) for m in selecionadas]
    saldo_total = sum(s for _, _, s in saldos)

    # Se os equipamentos já estiverem quitados, esta ação serve apenas para
    # registrar a previsão, confirmar o cliente e liberar a execução.
    valor = moeda_num(form.get("valor")) if saldo_total > 0.009 else 0
    data_pagamento = data_form(form.get("data")) or date.today()
    forma = (form.get("forma") or "").strip()
    banco = forma
    observacao = (form.get("observacao") or "").strip()

    if saldo_total > 0.009 and valor <= 0:
        return RedirectResponse("/organiza/operacao/pagamentos?erro=Informe o valor recebido", status_code=303)
    if valor > saldo_total + 0.009:
        return RedirectResponse("/organiza/operacao/pagamentos?erro=O valor é maior que o saldo dos equipamentos selecionados", status_code=303)

    restante = max(valor, 0)
    for m, o, saldo in saldos:
        if restante <= 0.009 or not o or saldo <= 0:
            continue
        aplicado = min(restante, saldo)
        db.add(Pagamento(
            orcamento_id=o.id,
            data=data_pagamento,
            valor=round(aplicado, 2),
            forma=forma or None,
            banco=banco or None,
            observacao=(f"Pagamento agrupado. {observacao}".strip()),
        ))
        restante -= aplicado

    agora = datetime.now()
    prazo_texto = previsao.strftime("%d/%m/%Y")
    for m, _, _ in saldos:
        m.entrega_prevista_em = datetime.combine(previsao, time(17, 0))
        m.prazo = prazo_texto
        m.confirmacao_prazo_em = agora
        m.status = "Em manutenção"

    db.commit()

    # Confere a transição depois da gravação para nunca deixar o equipamento
    # entre a etapa de pagamento e a execução.
    ids_nao_liberados = []
    for manutencao_id in ids:
        atual = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
        if atual and etapa_manutencao(atual) != 5:
            ids_nao_liberados.append(str(manutencao_id))
    if ids_nao_liberados:
        return RedirectResponse(
            "/organiza/operacao/pagamentos?erro=Não foi possível liberar alguns equipamentos para execução. Abra a manutenção e revise pagamento e previsão.",
            status_code=303,
        )

    linhas = [
        f"Olá, {selecionadas[0].cliente.nome}!",
        "",
        "✅ Pagamento e prazo confirmados.",
        "",
        "Equipamentos liberados para execução:",
    ]
    for m in selecionadas:
        linhas.append(f"• {rotulo_maquina(m.equipamento)} — previsão {prazo_texto}")
    linhas.extend(["", "Agora iniciaremos a execução dos serviços.", "", "Karaokê RJ"])
    mensagem = "\n".join(linhas)

    # Registra a mesma comunicação em cada manutenção selecionada e abre uma
    # única conversa do cliente, mantendo a operação agrupada.
    for m in selecionadas:
        ComunicacaoService.registrar(
            db, HistoricoComunicacao, m, usuario, "PRAZO",
            mensagem=mensagem,
        )

    url = ComunicacaoService.url_whatsapp(selecionadas[0].cliente, mensagem)
    return RedirectResponse(url, status_code=303)



# ---------------------------------------------------------
# CENTRAL FINANCEIRO -> CONNECT
# ---------------------------------------------------------

def _connect_configurado():
    return bool((os.getenv("CONNECT_API_URL") or "").strip())


def _connect_endpoint():
    base = (os.getenv("CONNECT_API_URL") or "").strip().rstrip("/")
    if base.endswith("/api/integracoes/organiza/lancamentos"):
        return base
    return base + "/api/integracoes/organiza/lancamentos"


def _payload_hash(payload: dict) -> str:
    bruto = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


def _enviar_para_connect(payload: dict):
    if not _connect_configurado():
        raise RuntimeError("CONNECT_API_URL não configurada.")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    chave = (os.getenv("CONNECT_API_KEY") or os.getenv("ORGANIZA_API_KEY") or "").strip()
    if chave:
        headers["X-API-Key"] = chave
    req = urllib.request.Request(
        _connect_endpoint(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            corpo = resp.read().decode("utf-8", errors="replace")
            return json.loads(corpo) if corpo else {"ok": True}
    except urllib.error.HTTPError as exc:
        detalhe = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Connect respondeu HTTP {exc.code}: {detalhe[:500]}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Não foi possível acessar o Connect: {exc.reason}")


def _registro_integracao(db: Session, origem: str, registro_id: int):
    return db.query(IntegracaoConect).filter(
        IntegracaoConect.origem == origem,
        IntegracaoConect.registro_id == registro_id,
    ).first()


def _obs_pagamento_padrao(equipamento, cliente, nome_comprovante: str = "") -> str:
    partes = []
    if equipamento:
        partes.append(rotulo_maquina(equipamento))
    if cliente and getattr(cliente, "nome", None):
        partes.append(cliente.nome.strip())
    nome = (nome_comprovante or "").strip()
    if nome:
        partes.append(nome)
    return " - ".join([p for p in partes if p])


def _payload_venda(p: PagamentoVenda, db: Session, total_recebido_operacao=None):
    eq = p.equipamento
    cliente = eq.cliente if eq else None
    descricao = f"Venda {rotulo_maquina(eq)}" if eq else f"Venda #{p.equipamento_id}"

    total_operacao = moeda_num(eq.valor) if eq else 0.0
    total_recebido = total_recebido_operacao
    if total_recebido is None:
        total_recebido = sum(
            float(v or 0)
            for (v,) in db.query(PagamentoVenda.valor)
            .filter(PagamentoVenda.equipamento_id == p.equipamento_id)
            .all()
        )
    falta_receber = max(float(total_operacao) - total_recebido, 0.0)

    return {
        "id_externo": f"ORGANIZA-VENDA-PAG-{p.id}",
        "tipo": "venda",
        "cliente": cliente.nome if cliente else "",
        "descricao": descricao,
        "valor": round(float(p.valor or 0), 2),
        "falta_receber": round(falta_receber, 2),
        "data_pagamento": p.data.isoformat(),
        "banco": p.banco or p.forma or "",
        "observacao": p.observacao or "",
    }


def _payload_manutencao(p: Pagamento, db: Session):
    o = p.orcamento
    m = o.manutencao if o else None
    cliente = m.cliente if m else None
    equipamento = m.equipamento if m else None
    descricao = f"Manutenção {rotulo_maquina(equipamento)}" if equipamento else f"Manutenção #{getattr(m, 'id', p.id)}"

    falta_receber = 0.0
    if m:
        _, _, falta_receber = _saldo_manutencao(m)

    return {
        "id_externo": f"ORGANIZA-MANUTENCAO-PAG-{p.id}",
        "tipo": "manutencao",
        "cliente": cliente.nome if cliente else "",
        "descricao": descricao,
        "valor": round(float(p.valor or 0), 2),
        "falta_receber": round(float(falta_receber or 0), 2),
        "data_pagamento": p.data.isoformat(),
        "banco": p.banco or p.forma or "",
        "observacao": p.observacao or "",
    }


def _linhas_central_financeiro(db: Session):
    linhas = []

    vendas = db.query(PagamentoVenda).options(
        selectinload(PagamentoVenda.equipamento).selectinload(Equipamento.cliente)
    ).order_by(PagamentoVenda.data.desc(), PagamentoVenda.id.desc()).all()

    totais_venda = dict(
        db.query(PagamentoVenda.equipamento_id, func.sum(PagamentoVenda.valor))
        .group_by(PagamentoVenda.equipamento_id).all()
    )
    integracoes = {
        (i.origem, i.registro_id): i for i in db.query(IntegracaoConect).all()
    }

    for p in vendas:
        payload = _payload_venda(p, db, float(totais_venda.get(p.equipamento_id) or 0))
        integ = integracoes.get(("venda", p.id))
        hash_atual = _payload_hash(payload)
        status = (
            "ignorado" if integ and integ.ignorado
            else "enviado" if integ and integ.hash_conteudo == hash_atual and integ.enviado_em
            else "atualizado" if integ and integ.enviado_em
            else "novo"
        )
        linhas.append({"origem": "Venda", "registro": p, "payload": payload, "status_sync": status, "integracao": integ, "editar_url": f"/organiza/vendas/{p.equipamento_id}/pagamentos", "operacao_chave": f"venda:{p.equipamento_id}"})

    manutencoes = db.query(Pagamento).options(
        selectinload(Pagamento.orcamento).selectinload(Orcamento.manutencao).selectinload(Manutencao.cliente),
        selectinload(Pagamento.orcamento).selectinload(Orcamento.manutencao).selectinload(Manutencao.equipamento),
        selectinload(Pagamento.orcamento).selectinload(Orcamento.manutencao).selectinload(Manutencao.orcamentos).selectinload(Orcamento.itens),
        selectinload(Pagamento.orcamento).selectinload(Orcamento.manutencao).selectinload(Manutencao.orcamentos).selectinload(Orcamento.pagamentos),
    ).order_by(Pagamento.data.desc(), Pagamento.id.desc()).all()
    for p in manutencoes:
        payload = _payload_manutencao(p, db)
        integ = integracoes.get(("manutencao", p.id))
        hash_atual = _payload_hash(payload)
        status = (
            "ignorado" if integ and integ.ignorado
            else "enviado" if integ and integ.hash_conteudo == hash_atual and integ.enviado_em
            else "atualizado" if integ and integ.enviado_em
            else "novo"
        )
        linhas.append({"origem": "Manutenção", "registro": p, "payload": payload, "status_sync": status, "integracao": integ, "editar_url": f"/organiza/manutencoes/{p.orcamento.manutencao.id}#etapa-4", "operacao_chave": f"manutencao:{p.orcamento.manutencao_id}"})
    linhas.sort(key=lambda x: (x["registro"].data, x["registro"].id), reverse=True)
    return linhas


@app.get("/organiza/financeiro/conect", response_class=HTMLResponse)
def central_financeiro_conect(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    linhas_todas = _linhas_central_financeiro(db)
    pendentes = sum(1 for l in linhas_todas if l["status_sync"] not in ("enviado", "ignorado"))
    filtro_status = (request.query_params.get("status") or "nao_enviados").strip().lower()
    if filtro_status == "enviados":
        linhas = [l for l in linhas_todas if l["status_sync"] == "enviado"]
    elif filtro_status == "todos":
        linhas = linhas_todas
    elif filtro_status == "ignorados":
        linhas = [l for l in linhas_todas if l["status_sync"] == "ignorado"]
    else:
        filtro_status = "nao_enviados"
        linhas = [l for l in linhas_todas if l["status_sync"] not in ("enviado", "ignorado")]

    # Evita gerar uma tabela HTML gigantesca. Mantém o filtro completo, mas
    # entrega somente uma página por vez ao navegador.
    total_filtrado = len(linhas)
    por_pagina = 100
    try:
        pagina = max(int(request.query_params.get("pagina") or 1), 1)
    except (TypeError, ValueError):
        pagina = 1
    total_paginas = max((total_filtrado + por_pagina - 1) // por_pagina, 1)
    pagina = min(pagina, total_paginas)
    inicio = (pagina - 1) * por_pagina
    linhas = linhas[inicio:inicio + por_pagina]

    return templates.TemplateResponse("organiza/central_financeiro_conect.html", {
        "request": request,
        "usuario": usuario,
        "linhas": linhas,
        "pendentes": pendentes,
        "filtro_status": filtro_status,
        "total_registros": len(linhas_todas),
        "total_filtrado": total_filtrado,
        "pagina": pagina,
        "total_paginas": total_paginas,
        "connect_configurado": _connect_configurado(),
        "sucesso": request.query_params.get("sucesso", ""),
        "erro": request.query_params.get("erro", ""),
    })


@app.post("/organiza/financeiro/conect/enviar")
def central_financeiro_conect_enviar(
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not _connect_configurado():
        return RedirectResponse("/organiza/financeiro/conect?erro=Configure CONNECT_API_URL no ambiente.", status_code=303)

    linhas = _linhas_central_financeiro(db)
    enviar = [l for l in linhas if l["status_sync"] not in ("enviado", "ignorado")]
    enviados = 0
    erros = []
    for linha in enviar:
        payload = linha["payload"]
        try:
            resposta = _enviar_para_connect(payload)
            origem = "venda" if linha["origem"] == "Venda" else "manutencao"
            integ = _registro_integracao(db, origem, linha["registro"].id)
            if not integ:
                integ = IntegracaoConect(
                    origem=origem,
                    registro_id=linha["registro"].id,
                    id_externo=payload["id_externo"],
                )
                db.add(integ)
            integ.hash_conteudo = _payload_hash(payload)
            integ.enviado_em = datetime.now()
            integ.ignorado = 0
            integ.resposta = json.dumps(resposta, ensure_ascii=False)[:4000]
            db.commit()
            enviados += 1
        except Exception as exc:
            db.rollback()
            erros.append(f'{payload["id_externo"]}: {str(exc)}')
            break

    if erros:
        msg = quote_plus(f"{enviados} enviado(s). Erro: {erros[0]}")
        return RedirectResponse(f"/organiza/financeiro/conect?erro={msg}", status_code=303)
    msg = quote_plus(f"{enviados} lançamento(s) enviado(s) ao Connect." if enviados else "Tudo já estava sincronizado.")
    return RedirectResponse(f"/organiza/financeiro/conect?sucesso={msg}", status_code=303)



def _chave_linha_connect(linha) -> str:
    origem = "venda" if linha["origem"] == "Venda" else "manutencao"
    return f"{origem}:{linha['registro'].id}"


@app.post("/organiza/financeiro/conect/enviar-selecionados")
async def central_financeiro_conect_enviar_selecionados(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not _connect_configurado():
        return RedirectResponse("/organiza/financeiro/conect?erro=Configure CONNECT_API_URL no ambiente.", status_code=303)

    form = await request.form()
    selecionados = set(form.getlist("selecionados"))
    if not selecionados:
        return RedirectResponse("/organiza/financeiro/conect?erro=Selecione pelo menos um lançamento.", status_code=303)

    linhas = [l for l in _linhas_central_financeiro(db) if _chave_linha_connect(l) in selecionados]
    enviados = 0
    for linha in linhas:
        payload = linha["payload"]
        try:
            resposta = _enviar_para_connect(payload)
            origem = "venda" if linha["origem"] == "Venda" else "manutencao"
            integ = _registro_integracao(db, origem, linha["registro"].id)
            if not integ:
                integ = IntegracaoConect(
                    origem=origem,
                    registro_id=linha["registro"].id,
                    id_externo=payload["id_externo"],
                )
                db.add(integ)
            integ.hash_conteudo = _payload_hash(payload)
            integ.enviado_em = datetime.now()
            integ.resposta = json.dumps(resposta, ensure_ascii=False)[:4000]
            integ.ignorado = 0
            db.commit()
            enviados += 1
        except Exception as exc:
            db.rollback()
            msg = quote_plus(f"{enviados} enviado(s). Erro em {payload['id_externo']}: {str(exc)}")
            return RedirectResponse(f"/organiza/financeiro/conect?erro={msg}", status_code=303)

    msg = quote_plus(f"{enviados} lançamento(s) selecionado(s) enviado(s) ao Connect.")
    return RedirectResponse(f"/organiza/financeiro/conect?sucesso={msg}", status_code=303)



def _normalizar_texto_agrupamento(valor) -> str:
    return re.sub(r"\s+", " ", (str(valor or "").strip().lower()))


def _grupos_connect_selecionados(linhas):
    """
    Agrupa somente lançamentos compatíveis com um único lançamento no Connect:
    mesmo tipo, cliente, data de pagamento e banco.
    """
    grupos = {}
    for linha in linhas:
        payload = linha["payload"]
        chave = (
            payload["tipo"],
            _normalizar_texto_agrupamento(payload.get("cliente")),
            payload["data_pagamento"],
            _normalizar_texto_agrupamento(payload.get("banco")),
        )
        grupos.setdefault(chave, []).append(linha)
    return list(grupos.values())


def _payload_grupo_connect(grupo):
    primeiro = grupo[0]["payload"]
    chaves_origem = sorted(_chave_linha_connect(l) for l in grupo)
    assinatura = hashlib.sha256("|".join(chaves_origem).encode("utf-8")).hexdigest()[:16]
    tipo = primeiro["tipo"]
    cliente = primeiro.get("cliente") or ""
    quantidade = len(grupo)
    valor_total = round(sum(float(l["payload"].get("valor") or 0) for l in grupo), 2)

    # O saldo é apenas informativo. Pagamentos da mesma operação
    # contam o saldo dessa operação uma única vez.
    saldos_por_operacao = {}
    for linha in grupo:
        chave_operacao = linha.get("operacao_chave") or _chave_linha_connect(linha)
        saldos_por_operacao[chave_operacao] = float(linha["payload"].get("falta_receber") or 0)
    falta_receber = round(sum(saldos_por_operacao.values()), 2)

    rotulo_tipo = "Venda" if tipo == "venda" else "Manutenção"
    descricao = f"{rotulo_tipo} agrupada - {quantidade} lançamento(s)"
    if cliente:
        descricao += f" - {cliente}"

    observacoes = []
    for linha in grupo:
        obs = (linha["payload"].get("observacao") or "").strip()
        if obs and obs not in observacoes:
            observacoes.append(obs)

    return {
        "id_externo": f"ORGANIZA-GRUPO-{tipo.upper()}-{assinatura}",
        "tipo": tipo,
        "cliente": cliente,
        "descricao": descricao,
        "valor": valor_total,
        "falta_receber": falta_receber,
        "data_pagamento": primeiro["data_pagamento"],
        "banco": primeiro.get("banco") or "",
        "observacao": " | ".join(observacoes),
    }


@app.post("/organiza/financeiro/conect/enviar-agrupado")
async def central_financeiro_conect_enviar_agrupado(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not _connect_configurado():
        return RedirectResponse(
            "/organiza/financeiro/conect?erro=Configure CONNECT_API_URL no ambiente.",
            status_code=303,
        )

    form = await request.form()
    selecionados = set(form.getlist("selecionados"))
    if not selecionados:
        return RedirectResponse(
            "/organiza/financeiro/conect?erro=Selecione pelo menos um lançamento para agrupar.",
            status_code=303,
        )

    linhas = [
        l for l in _linhas_central_financeiro(db)
        if _chave_linha_connect(l) in selecionados
    ]
    if not linhas:
        return RedirectResponse(
            "/organiza/financeiro/conect?erro=Nenhum lançamento válido foi selecionado.",
            status_code=303,
        )

    grupos = _grupos_connect_selecionados(linhas)
    enviados = 0

    for grupo in grupos:
        payload_grupo = _payload_grupo_connect(grupo)

        try:
            resposta = _enviar_para_connect(payload_grupo)

            # Cada origem continua controlada individualmente no Organiza,
            # embora o Connect receba apenas um lançamento com o total agrupado.
            for linha in grupo:
                origem = "venda" if linha["origem"] == "Venda" else "manutencao"
                payload_individual = linha["payload"]
                integ = _registro_integracao(db, origem, linha["registro"].id)

                if not integ:
                    integ = IntegracaoConect(
                        origem=origem,
                        registro_id=linha["registro"].id,
                        id_externo=payload_individual["id_externo"],
                    )
                    db.add(integ)

                integ.hash_conteudo = _payload_hash(payload_individual)
                integ.enviado_em = datetime.now()
                integ.ignorado = 0
                integ.resposta = json.dumps({
                    "modo": "agrupado",
                    "id_externo_grupo": payload_grupo["id_externo"],
                    "valor_grupo": payload_grupo["valor"],
                    "quantidade_grupo": len(grupo),
                    "resposta_connect": resposta,
                }, ensure_ascii=False)[:4000]

            db.commit()
            enviados += 1

        except Exception as exc:
            db.rollback()
            msg = quote_plus(
                f"{enviados} grupo(s) enviado(s). Erro no grupo "
                f"{payload_grupo['cliente']}: {str(exc)}"
            )
            return RedirectResponse(
                f"/organiza/financeiro/conect?erro={msg}",
                status_code=303,
            )

    total_origens = len(linhas)
    msg = quote_plus(
        f"{total_origens} lançamento(s) agrupado(s) em "
        f"{enviados} lançamento(s) enviado(s) ao Connect."
    )
    return RedirectResponse(
        f"/organiza/financeiro/conect?sucesso={msg}",
        status_code=303,
    )


@app.post("/organiza/financeiro/conect/nao-enviar")
async def central_financeiro_conect_nao_enviar(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    form = await request.form()
    selecionados = set(form.getlist("selecionados"))
    if not selecionados:
        return RedirectResponse("/organiza/financeiro/conect?erro=Selecione pelo menos um lançamento.", status_code=303)

    alterados = 0
    for linha in _linhas_central_financeiro(db):
        if _chave_linha_connect(linha) not in selecionados:
            continue
        origem = "venda" if linha["origem"] == "Venda" else "manutencao"
        integ = _registro_integracao(db, origem, linha["registro"].id)
        if not integ:
            integ = IntegracaoConect(
                origem=origem,
                registro_id=linha["registro"].id,
                id_externo=linha["payload"]["id_externo"],
            )
            db.add(integ)
        integ.ignorado = 1
        alterados += 1
    db.commit()
    msg = quote_plus(f"{alterados} lançamento(s) marcado(s) como 'Não enviar'.")
    return RedirectResponse(f"/organiza/financeiro/conect?sucesso={msg}", status_code=303)


@app.get("/organiza/vendas/{equipamento_id}/pagamentos", response_class=HTMLResponse)
def venda_pagamentos(
    equipamento_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    eq = db.query(Equipamento).options(selectinload(Equipamento.cliente)).filter(Equipamento.id == equipamento_id).first()
    if not eq or not equipamento_eh_venda(eq):
        raise HTTPException(404)
    pagamentos = db.query(PagamentoVenda).filter(PagamentoVenda.equipamento_id == equipamento_id).order_by(PagamentoVenda.data.desc(), PagamentoVenda.id.desc()).all()
    total = moeda_num(eq.valor)
    recebido = sum(float(p.valor or 0) for p in pagamentos)
    return templates.TemplateResponse("organiza/venda_pagamentos.html", {
        "request": request, "usuario": usuario, "venda": eq, "pagamentos": pagamentos,
        "total": total, "recebido": recebido, "saldo": max(total - recebido, 0),
        "hoje": date.today().isoformat(), "erro": request.query_params.get("erro", ""),
        "observacao_padrao": _obs_pagamento_padrao(eq, eq.cliente),
    })


@app.post("/organiza/vendas/{equipamento_id}/pagamentos")
async def venda_pagamento_registrar(
    equipamento_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id).first()
    if not eq or not equipamento_eh_venda(eq):
        raise HTTPException(404)
    form = dict(await request.form())
    valor = moeda_num(form.get("valor"))
    data_pag = data_form(form.get("data")) or date.today()
    forma = (form.get("forma") or "PIX").strip()
    banco = forma
    nome_comprovante = (form.get("observacao") or "").strip()
    observacao = _obs_pagamento_padrao(eq, eq.cliente, nome_comprovante)
    nao_enviar_connect = bool(form.get("nao_enviar_connect"))
    if valor <= 0:
        return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos?erro=Informe um valor válido.", status_code=303)
    total = moeda_num(eq.valor)
    recebido = sum(float(p.valor or 0) for p in db.query(PagamentoVenda).filter(PagamentoVenda.equipamento_id == equipamento_id).all())
    saldo = round(total - recebido, 2)
    if total <= 0:
        return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos?erro=A venda não possui um valor total válido. Corrija a venda antes de registrar pagamentos.", status_code=303)
    if saldo <= 0.009:
        return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos?erro=Esta venda já está totalmente paga.", status_code=303)
    if valor > saldo + 0.009:
        return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos?erro=O pagamento não pode ser maior que o saldo da venda.", status_code=303)
    pagamento = PagamentoVenda(
        equipamento_id=equipamento_id, data=data_pag, valor=round(valor, 2),
        banco=banco, forma=forma, observacao=observacao or None,
    )
    db.add(pagamento)
    db.flush()
    if nao_enviar_connect:
        db.add(IntegracaoConect(
            origem="venda", registro_id=pagamento.id,
            id_externo=f"ORGANIZA-VENDA-PAG-{pagamento.id}",
            ignorado=1,
        ))
    db.commit()
    return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos", status_code=303)



@app.post("/organiza/vendas/{equipamento_id}/pagamentos/{pagamento_id}/editar")
async def venda_pagamento_editar(
    equipamento_id: int,
    pagamento_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    p = db.query(PagamentoVenda).filter(
        PagamentoVenda.id == pagamento_id,
        PagamentoVenda.equipamento_id == equipamento_id,
    ).first()
    if not p:
        raise HTTPException(404)

    form = dict(await request.form())
    valor = moeda_num(form.get("valor"))
    forma = (form.get("forma") or "").strip()
    banco = forma
    data_pag = data_form(form.get("data") or "")
    if valor <= 0 or not data_pag or not forma:
        return RedirectResponse(
            f"/organiza/vendas/{equipamento_id}/pagamentos?erro=Informe data, valor e banco válidos.",
            status_code=303,
        )

    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id).first()
    total = moeda_num(eq.valor if eq else 0)
    outros = sum(float(item.valor or 0) for item in db.query(PagamentoVenda).filter(
        PagamentoVenda.equipamento_id == equipamento_id, PagamentoVenda.id != pagamento_id
    ).all())
    if total <= 0 or valor > round(total - outros, 2) + 0.009:
        return RedirectResponse(
            f"/organiza/vendas/{equipamento_id}/pagamentos?erro=O valor informado ultrapassa o saldo disponível da venda.",
            status_code=303,
        )

    p.valor = round(valor, 2)
    p.data = data_pag
    p.forma = forma
    p.banco = banco
    nome_comprovante = (form.get("observacao") or "").strip()
    prefixo = _obs_pagamento_padrao(eq, eq.cliente if eq else None)
    p.observacao = (nome_comprovante if nome_comprovante.startswith(prefixo) else _obs_pagamento_padrao(eq, eq.cliente if eq else None, nome_comprovante)) or None
    db.commit()
    return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos", status_code=303)


@app.post("/organiza/vendas/{equipamento_id}/pagamentos/{pagamento_id}/excluir")
def venda_pagamento_excluir(
    equipamento_id: int,
    pagamento_id: int,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    p = db.query(PagamentoVenda).filter(PagamentoVenda.id == pagamento_id, PagamentoVenda.equipamento_id == equipamento_id).first()
    if not p:
        raise HTTPException(404)
    integ = _registro_integracao(db, "venda", p.id)
    if integ and integ.enviado_em:
        return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos?erro=Pagamento já enviado ao Connect. Ajuste o registro em vez de excluir.", status_code=303)
    if integ:
        db.delete(integ)
    db.delete(p)
    db.commit()
    return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos", status_code=303)


@app.get("/organiza/agenda", response_class=HTMLResponse)
def agenda(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    manutencoes = (
        db.query(Manutencao)
        .options(
            selectinload(Manutencao.cliente),
            selectinload(Manutencao.equipamento),
            selectinload(Manutencao.orcamentos).selectinload(Orcamento.pagamentos),
        )
        .filter(
            Manutencao.entregue_em.is_(None),
            ~Manutencao.status.in_(("Encerrada", "Cancelada")),
            or_(
                Manutencao.entrega_prevista_em.isnot(None),
                Manutencao.retirada_em.isnot(None),
                Manutencao.pronto_em.isnot(None),
                Manutencao.status.in_(("Pronto para retirada", "Retirada agendada")),
            ),
        )
        .all()
    )

    eventos = []
    for m in manutencoes:
        etapa = etapa_manutencao(m)
        if etapa == 1 and m.entrega_prevista_em:
            online = (m.tipo_atendimento or "loja") == "online"
            eventos.append({
                "tipo": "online" if online else "entrada",
                "titulo": "Atendimento online" if online else "Cliente vai trazer",
                "data_hora": m.entrega_prevista_em,
                "cliente": m.cliente.nome,
                "equipamento": descricao_equipamento(m.equipamento),
                "link": f"/organiza/manutencoes/{m.id}#etapa-1",
                "manual": False,
                "manutencao_id": m.id,
                "agendamento_tipo": "entrada",
            })
        elif etapa == 6 and m.retirada_em:
            eventos.append({
                "tipo": "retirada",
                "titulo": "Cliente vem buscar",
                "data_hora": m.retirada_em,
                "cliente": m.cliente.nome,
                "equipamento": descricao_equipamento(m.equipamento),
                "link": f"/organiza/manutencoes/{m.id}#etapa-6",
                "manual": False,
                "manutencao_id": m.id,
                "agendamento_tipo": "retirada",
            })

    for e in db.query(AgendaManual).order_by(AgendaManual.data_hora.asc()).all():
        eventos.append({
            "tipo": e.tipo,
            "titulo": e.titulo,
            "data_hora": e.data_hora,
            "cliente": e.contato or "Compromisso manual",
            "equipamento": " · ".join(filter(None, [e.observacao or "", f"Google {e.google_sync_status or 'aguardando'}"])),
            "link": f"/organiza/agenda/manual/{e.id}/editar",
            "manual": True,
            "evento_id": e.id,
            "google_sync_status": e.google_sync_status,
            "google_sync_erro": e.google_sync_erro,
        })

    for a in db.query(AtualizacaoAgendamento).filter(AtualizacaoAgendamento.status == "RESERVADO").order_by(AtualizacaoAgendamento.data_hora.asc()).all():
        compra = db.get(AtualizacaoCompra, a.compra_id)
        cliente_at = db.get(Cliente, a.cliente_id)
        if not compra or not cliente_at:
            continue
        eventos.append({
            "tipo": "atualizacao-casa" if a.tipo == "CASA" else "atualizacao-loja",
            "titulo": "Atualização AnyDesk" if a.tipo == "CASA" else "Atualização na loja",
            "data_hora": a.data_hora,
            "cliente": cliente_at.nome,
            "equipamento": f"Pacotes {compra.pacote_inicio} a {compra.pacote_fim} · Google {a.google_sync_status or 'aguardando'}",
            "link": f"/organiza/clientes/{cliente_at.id}#atualizacoes",
            "manual": True,
            "evento_id": a.id,
            "atualizacao": True,
        })

    eventos.sort(key=lambda e: (e["data_hora"], e["titulo"]))
    google = _google_integracao(db)
    return templates.TemplateResponse("organiza/agenda.html", {
        "request": request, "usuario": usuario, "eventos": eventos,
        "google": google, "google_configurado": _google_configurado(),
        "mensagem": request.query_params.get("mensagem", ""), "erro": request.query_params.get("erro", ""),
    })



@app.get("/agendamento/{token}/{manutencao_id}", response_class=HTMLResponse)
def agendamento_cliente_publico(
    token: str,
    manutencao_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    m = db.query(Manutencao).filter(
        Manutencao.id == manutencao_id,
        Manutencao.cliente_id == cliente.id,
    ).first()
    if not m:
        raise HTTPException(404)
    return templates.TemplateResponse("organiza/agendamento_cliente_publico.html", {
        "request": request,
        "cliente": cliente,
        "m": m,
        "erro": request.query_params.get("erro", ""),
        "ok": request.query_params.get("ok", ""),
        "hoje": date.today().isoformat(),
    })


@app.post("/agendamento/{token}/{manutencao_id}")
async def agendamento_cliente_publico_salvar(
    token: str,
    manutencao_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    m = db.query(Manutencao).filter(
        Manutencao.id == manutencao_id,
        Manutencao.cliente_id == cliente.id,
    ).first()
    if not m:
        raise HTTPException(404)
    form = await request.form()
    acao = (form.get("acao") or "reagendar").strip()

    if acao == "cancelar":
        m.status = "Cancelada"
        m.entrega_prevista_em = None
        db.commit()
        return RedirectResponse(f"/agendamento/{token}/{manutencao_id}?ok=cancelado", status_code=303)

    nova_data = datetime_form(form.get("data_hora") or "")
    if not nova_data or nova_data < datetime.now():
        return RedirectResponse(
            f"/agendamento/{token}/{manutencao_id}?erro=Escolha uma data e horário futuros.",
            status_code=303,
        )
    if horario_atendimento_ocupado(db, nova_data, m.id):
        return RedirectResponse(
            f"/agendamento/{token}/{manutencao_id}?erro=Este horário não está disponível. Escolha outro.",
            status_code=303,
        )
    m.entrega_prevista_em = nova_data
    m.status = "Aguardando equipamento"
    db.commit()
    return RedirectResponse(f"/agendamento/{token}/{manutencao_id}?ok=reagendado", status_code=303)


@app.get("/organiza/manutencoes/{manutencao_id}/nao-compareceu-whatsapp")
def manutencao_nao_compareceu_whatsapp(
    manutencao_id: int,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    cliente = m.cliente
    if not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)
        db.commit()
    link = f"{PUBLIC_BASE_URL}/agendamento/{cliente.token_ficha}/{m.id}"
    mensagem = (
        f"Olá, {cliente.nome}!\\n\\n"
        "Hoje estava prevista a entrega/atendimento do seu equipamento, mas não conseguimos concluir o recebimento.\\n\\n"
        f"Equipamento: {descricao_equipamento(m.equipamento)}\\n"
        f"Ordem de serviço: #{m.id}\\n\\n"
        "Para não deixar uma pendência em aberto, escolha uma opção no link abaixo:\\n"
        "• Reagendar uma nova data e horário\\n"
        "• Cancelar esta solicitação\\n\\n"
        f"{link}\\n\\nKaraokê RJ"
    )
    url = ComunicacaoService.registrar_e_url(db, HistoricoComunicacao, m, usuario, "NAO_COMPARECEU", mensagem)
    return RedirectResponse(url, status_code=303)


@app.post("/organiza/agenda/manutencao/{manutencao_id}/reagendar")
async def agenda_manutencao_reagendar(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
    if not m:
        raise HTTPException(404)
    form = await request.form()
    nova_data = datetime_form(form.get("data_hora") or "")
    tipo = (form.get("tipo") or "entrada").strip()
    if not nova_data:
        return RedirectResponse("/organiza/agenda?erro=Informe uma nova data e horário.", status_code=303)
    if tipo == "retirada":
        m.retirada_em = nova_data
        m.status = "Retirada agendada"
    else:
        if horario_atendimento_ocupado(db, nova_data, m.id):
            return RedirectResponse("/organiza/agenda?erro=Este horário já está ocupado.", status_code=303)
        m.entrega_prevista_em = nova_data
        if not m.recebido_em:
            m.status = "Aguardando equipamento"
    db.commit()
    return RedirectResponse("/organiza/agenda", status_code=303)


@app.post("/organiza/agenda/manutencao/{manutencao_id}/excluir")
def agenda_manutencao_excluir(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
    if not m:
        raise HTTPException(404)
    if etapa_manutencao(m) == 1 and not m.recebido_em:
        # Cliente não trouxe o equipamento: encerra a pendência sem apagar o histórico.
        m.status = "Cancelada"
        m.entrega_prevista_em = None
    elif etapa_manutencao(m) == 6:
        # Remove apenas a retirada agendada; a manutenção continua pronta para retirada.
        m.retirada_em = None
        m.status = "Pronto para retirada"
    db.commit()
    return RedirectResponse("/organiza/agenda", status_code=303)


@app.post("/organiza/agenda/google/sincronizar")
def agenda_google_sincronizar(usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_admin(usuario)
    integ = _google_integracao(db)
    if not integ or not integ.refresh_token:
        return RedirectResponse("/organiza/agenda?erro=" + quote_plus("Conecte a conta Google antes de sincronizar a agenda."), status_code=303)
    sincronizados = 0
    erros = 0
    agora = datetime.now() - timedelta(hours=1)
    manuais = (
        db.query(AgendaManual)
        .filter(AgendaManual.data_hora >= agora)
        .order_by(AgendaManual.data_hora.asc())
        .limit(150)
        .all()
    )
    for evento in manuais:
        _google_calendar_manual_sincronizar(db, evento)
        if evento.google_sync_status == "SINCRONIZADO":
            sincronizados += 1
        else:
            erros += 1
    atualizacoes = (
        db.query(AtualizacaoAgendamento)
        .filter(AtualizacaoAgendamento.status == "RESERVADO", AtualizacaoAgendamento.data_hora >= agora)
        .order_by(AtualizacaoAgendamento.data_hora.asc())
        .limit(150)
        .all()
    )
    for ag in atualizacoes:
        compra = db.get(AtualizacaoCompra, ag.compra_id)
        cliente = db.get(Cliente, ag.cliente_id)
        if not compra or not cliente:
            continue
        _google_calendar_sincronizar(db, ag, cliente, compra)
        if ag.google_sync_status == "SINCRONIZADO":
            sincronizados += 1
        else:
            erros += 1
    db.commit()
    mensagem = f"Google Agenda atualizado: {sincronizados} compromisso(s) sincronizado(s)."
    if erros:
        mensagem += f" {erros} ficaram com erro; abra o compromisso para consultar o status."
    return RedirectResponse("/organiza/agenda?mensagem=" + quote_plus(mensagem), status_code=303)


@app.get("/organiza/agenda/manual/novo", response_class=HTMLResponse)
def agenda_manual_novo(request: Request, usuario: Usuario = Depends(usuario_logado)):
    return templates.TemplateResponse("organiza/agenda_manual_form.html", {
        "request": request, "usuario": usuario, "evento": None, "erro": "",
    })


@app.post("/organiza/agenda/manual/novo")
async def agenda_manual_criar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = await request.form()
    data_hora = datetime_form(form.get("data_hora"))
    titulo = (form.get("titulo") or "").strip()
    if not titulo or not data_hora:
        return templates.TemplateResponse("organiza/agenda_manual_form.html", {
            "request": request, "usuario": usuario, "evento": None,
            "erro": "Informe o título e a data com horário.",
        }, status_code=400)
    evento = AgendaManual(
        titulo=titulo, tipo=(form.get("tipo") or "visita").strip(),
        data_hora=data_hora, contato=(form.get("contato") or "").strip() or None,
        observacao=(form.get("observacao") or "").strip() or None,
    )
    db.add(evento)
    db.flush()
    _google_calendar_manual_sincronizar(db, evento)
    db.commit()
    mensagem = "Compromisso salvo e enviado ao Google Agenda." if evento.google_sync_status == "SINCRONIZADO" else "Compromisso salvo no Organiza. A sincronização com o Google Agenda ficou pendente."
    return RedirectResponse("/organiza/agenda?mensagem=" + quote_plus(mensagem), status_code=303)


@app.get("/organiza/agenda/manual/{evento_id}/editar", response_class=HTMLResponse)
def agenda_manual_editar(evento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    evento = db.get(AgendaManual, evento_id)
    if not evento:
        raise HTTPException(404)
    return templates.TemplateResponse("organiza/agenda_manual_form.html", {
        "request": request, "usuario": usuario, "evento": evento, "erro": "",
    })


@app.post("/organiza/agenda/manual/{evento_id}/editar")
async def agenda_manual_salvar(evento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    evento = db.get(AgendaManual, evento_id)
    if not evento:
        raise HTTPException(404)
    form = await request.form()
    data_hora = datetime_form(form.get("data_hora"))
    titulo = (form.get("titulo") or "").strip()
    if not titulo or not data_hora:
        return templates.TemplateResponse("organiza/agenda_manual_form.html", {
            "request": request, "usuario": usuario, "evento": evento,
            "erro": "Informe o título e a data com horário.",
        }, status_code=400)
    evento.titulo = titulo
    evento.tipo = (form.get("tipo") or "visita").strip()
    evento.data_hora = data_hora
    evento.contato = (form.get("contato") or "").strip() or None
    evento.observacao = (form.get("observacao") or "").strip() or None
    _google_calendar_manual_sincronizar(db, evento)
    db.commit()
    mensagem = "Compromisso atualizado no Organiza e no Google Agenda." if evento.google_sync_status == "SINCRONIZADO" else "Compromisso atualizado no Organiza. A sincronização com o Google Agenda ficou pendente."
    return RedirectResponse("/organiza/agenda?mensagem=" + quote_plus(mensagem), status_code=303)


@app.post("/organiza/agenda/manual/{evento_id}/excluir")
def agenda_manual_excluir(evento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    evento = db.get(AgendaManual, evento_id)
    if evento:
        _google_calendar_manual_excluir(db, evento)
        if evento.google_sync_status == "ERRO":
            erro = evento.google_sync_erro or "Não foi possível remover o evento do Google Agenda."
            db.commit()
            return RedirectResponse("/organiza/agenda?erro=" + quote_plus(erro), status_code=303)
        db.delete(evento)
        db.commit()
    return RedirectResponse("/organiza/agenda?mensagem=" + quote_plus("Compromisso removido da Agenda do Organiza e do Google Agenda."), status_code=303)


def manutencoes_prontas_cliente(db: Session, cliente_id: int):
    return (
        db.query(Manutencao)
        .options(selectinload(Manutencao.cliente), selectinload(Manutencao.equipamento))
        .filter(
            Manutencao.cliente_id == cliente_id,
            Manutencao.pronto_em.isnot(None),
            Manutencao.entregue_em.is_(None),
            Manutencao.status.in_(("Pronto para retirada", "Retirada agendada")),
        )
        .order_by(Manutencao.pronto_em.asc(), Manutencao.id.asc())
        .all()
    )


def contexto_retirada_publica(request: Request, o: Orcamento, prontas, erro: str = ""):
    return {
        "request": request,
        "orcamento": o,
        "m": o.manutencao,
        "manutencoes_prontas": prontas,
        "erro": erro,
        "hoje": date.today().isoformat(),
    }



def _ids_selecionados_publicos(ids_texto: str) -> list[int]:
    try:
        return sorted({int(x) for x in (ids_texto or "").split(",") if x.strip()})
    except ValueError:
        raise HTTPException(400, "Seleção inválida.")


def _manutencoes_publicas_selecionadas(db: Session, cliente: Cliente, ids_texto: str, somente_prontas: bool = True):
    ids = _ids_selecionados_publicos(ids_texto)
    if not ids:
        raise HTTPException(400, "Selecione ao menos um equipamento.")
    consulta = (
        db.query(Manutencao)
        .options(
            selectinload(Manutencao.cliente),
            selectinload(Manutencao.equipamento),
            selectinload(Manutencao.orcamentos).selectinload(Orcamento.itens),
        )
        .filter(Manutencao.cliente_id == cliente.id, Manutencao.id.in_(ids))
    )
    if somente_prontas:
        consulta = consulta.filter(Manutencao.pronto_em.isnot(None), Manutencao.entregue_em.is_(None))
    manutencoes = consulta.order_by(Manutencao.id.asc()).all()
    if len(manutencoes) != len(ids):
        raise HTTPException(400, "Um ou mais equipamentos selecionados não estão disponíveis.")
    return manutencoes


@app.get("/garantias/{token}.pdf")
def garantias_agrupadas_pdf(token: str, ids: str, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    manutencoes = _manutencoes_publicas_selecionadas(db, cliente, ids, somente_prontas=True)

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak
    from reportlab.lib import colors

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=15*mm, bottomMargin=15*mm)
    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle("TituloGarantiaGrupo", parent=estilos["Title"], alignment=TA_CENTER, fontSize=18, leading=22, spaceAfter=8)
    centro = ParagraphStyle("CentroGarantiaGrupo", parent=estilos["BodyText"], alignment=TA_CENTER, fontSize=10, leading=14)
    corpo = ParagraphStyle("CorpoGarantiaGrupo", parent=estilos["BodyText"], fontSize=10, leading=15, spaceAfter=8)
    elementos = []
    logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "logo-karaoke-rj.png")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "karaoke-rj-garantia.jpeg")

    for indice, m in enumerate(manutencoes):
        if indice:
            elementos.append(PageBreak())
        logo = Image(logo_path, width=30*mm, height=22*mm) if os.path.exists(logo_path) else Spacer(30*mm, 22*mm)
        empresa = Paragraph(
            "<b>KARAOKE &amp; GAMES RJ</b><br/>CNPJ: 35.458.112/0001-75 · IM: 1213508-4<br/>"
            "Rua João Romariz, 313 - Ramos - Rio de Janeiro/RJ - CEP: 21031-700<br/>"
            "WhatsApp: (21) 99507-9690 / (21) 99650-4516<br/>www.karaokerj.com.br · contato@karaokerj.com.br",
            ParagraphStyle(f"CabecalhoGarantiaGrupo{indice}", parent=corpo, fontSize=8, leading=10, spaceAfter=0),
        )
        header = Table([[logo, empresa]], colWidths=[35*mm, 139*mm])
        header.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("LINEBELOW",(0,0),(-1,-1),0.8,colors.HexColor("#555555")),
            ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),5),
        ]))
        elementos += [header, Spacer(1, 7), Paragraph("CERTIFICADO DE GARANTIA DO SERVIÇO", titulo), Spacer(1, 4*mm)]
        eq = m.equipamento
        dados = [
            ["Ordem de serviço", f"#{m.id}"],
            ["Cliente", cliente.nome],
            ["Equipamento", descricao_equipamento(eq)],
            ["Código técnico", codigo_tecnico(eq)],
            ["Serviço concluído em", m.pronto_em.strftime("%d/%m/%Y")],
            ["Garantia válida até", (m.pronto_em.date() + timedelta(days=30)).strftime("%d/%m/%Y")],
        ]
        tabela = Table(dados, colWidths=[48*mm, 110*mm])
        tabela.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#F2F4F7")),
            ("TEXTCOLOR", (0,0), (0,-1), colors.HexColor("#344054")),
            ("FONTNAME", (0,0), (0,-1), "Helvetica-Bold"),
            ("FONTNAME", (1,0), (1,-1), "Helvetica"),
            ("GRID", (0,0), (-1,-1), .4, colors.HexColor("#D0D5DD")),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("PADDING", (0,0), (-1,-1), 7),
        ]))
        elementos += [
            tabela, Spacer(1, 8*mm),
            Paragraph("<b>Garantia de 30 dias</b>", corpo),
            Paragraph(
                "A garantia cobre exclusivamente os serviços executados e os itens descritos na ordem de serviço. "
                "Não cobre mau uso, quedas, líquidos, ligação em tensão incorreta, intervenção de terceiros ou defeitos diferentes do serviço realizado.",
                corpo,
            ),
        ]
    doc.build(elementos)
    buffer.seek(0)
    return Response(
        buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="garantias-{cliente.id}.pdf"'},
    )


@app.get("/retirada-cliente/{token}", response_class=HTMLResponse)
def retirada_cliente_publica(token: str, ids: str, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    manutencoes = _manutencoes_publicas_selecionadas(db, cliente, ids, somente_prontas=True)
    agendamento_atual = next((m.retirada_em for m in manutencoes if m.retirada_em), None)
    return templates.TemplateResponse("organiza/retirada_cliente_publica.html", {
        "request": request,
        "cliente": cliente,
        "manutencoes": manutencoes,
        "ids": ids,
        "hoje": date.today().isoformat(),
        "erro": "",
        "agendamento_atual": agendamento_atual,
    })


@app.post("/retirada-cliente/{token}")
async def retirada_cliente_publica_salvar(token: str, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    form = await request.form()
    ids = (form.get("ids") or "").strip()
    manutencoes = _manutencoes_publicas_selecionadas(db, cliente, ids, somente_prontas=True)
    data_retirada = data_form(form.get("data_retirada") or "")
    hora_texto = (form.get("hora_retirada") or "").strip()
    dt = None
    try:
        hora_retirada = datetime.strptime(hora_texto, "%H:%M").time()
        if data_retirada:
            dt = datetime.combine(data_retirada, hora_retirada)
    except ValueError:
        pass

    agora = datetime.now()
    if dt and dt >= agora and dt.weekday() < 5 and time(14, 0) <= dt.time() <= time(17, 0):
        for manutencao in manutencoes:
            manutencao.retirada_em = dt
            manutencao.status = "Retirada agendada"
        db.commit()
        return RedirectResponse(f"/retirada-cliente/{token}?ids={ids}&ok=1", status_code=303)

    agendamento_atual = next((m.retirada_em for m in manutencoes if m.retirada_em), None)
    return templates.TemplateResponse("organiza/retirada_cliente_publica.html", {
        "request": request,
        "cliente": cliente,
        "manutencoes": manutencoes,
        "ids": ids,
        "hoje": date.today().isoformat(),
        "erro": "Escolha uma data e um horário válidos, de segunda a sexta, entre 14:00 e 17:00.",
        "agendamento_atual": agendamento_atual,
    }, status_code=400)


@app.get("/retirada/{token}", response_class=HTMLResponse)
def retirada_publica(token: str, request: Request, db: Session = Depends(get_db)):
    o = (
        db.query(Orcamento)
        .options(
            selectinload(Orcamento.manutencao).selectinload(Manutencao.cliente),
            selectinload(Orcamento.manutencao).selectinload(Manutencao.equipamento),
        )
        .filter(Orcamento.token == token)
        .first()
    )
    if not o or not o.manutencao.pronto_em:
        raise HTTPException(404)
    prontas = manutencoes_prontas_cliente(db, o.manutencao.cliente_id)
    if not prontas:
        raise HTTPException(404)
    return templates.TemplateResponse(
        "organiza/retirada_publica.html",
        contexto_retirada_publica(request, o, prontas),
    )


@app.post("/retirada/{token}")
async def retirada_publica_salvar(token: str, request: Request, db: Session = Depends(get_db)):
    o = (
        db.query(Orcamento)
        .options(selectinload(Orcamento.manutencao))
        .filter(Orcamento.token == token)
        .first()
    )
    if not o or not o.manutencao.pronto_em:
        raise HTTPException(404)

    prontas = manutencoes_prontas_cliente(db, o.manutencao.cliente_id)
    if not prontas:
        raise HTTPException(404)

    form = dict(await request.form())
    data_retirada = data_form(form.get("data_retirada") or "")
    hora_texto = (form.get("hora_retirada") or "").strip()
    dt = None
    try:
        hora_retirada = datetime.strptime(hora_texto, "%H:%M").time()
        if data_retirada:
            dt = datetime.combine(data_retirada, hora_retirada)
    except ValueError:
        pass

    erro = "Escolha uma data e um horário válidos, de segunda a sexta, entre 14:00 e 17:00."
    agora = datetime.now()
    if (
        dt
        and dt >= agora
        and dt.weekday() < 5
        and time(14, 0) <= dt.time() <= time(17, 0)
    ):
        for manutencao in prontas:
            manutencao.retirada_em = dt
            manutencao.status = "Retirada agendada"
        db.commit()
        return RedirectResponse(f"/retirada/{token}?ok=1", status_code=303)

    return templates.TemplateResponse(
        "organiza/retirada_publica.html",
        contexto_retirada_publica(request, o, prontas, erro),
        status_code=400,
    )

def linha_tempo_publica(m):
    etapa_atual = etapa_manutencao(m)
    datas = {
        1: m.entrega_prevista_em or m.criado_em,
        2: m.recebido_em,
        3: None,
        4: None,
        5: m.confirmacao_prazo_em,
        6: m.pronto_em or m.retirada_em,
        7: m.entregue_em,
    }
    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None
    if o:
        datas[3] = o.criado_em
        datas[4] = o.aprovado_em
    itens = []
    for numero in range(1, 8):
        dados = ETAPAS_MANUTENCAO[numero]
        itens.append({
            "numero": numero,
            "titulo": dados["titulo"],
            "rotulo": dados["rotulo"],
            "classe": dados["classe"],
            "data": datas.get(numero),
            "concluida": numero < etapa_atual,
            "atual": numero == etapa_atual,
            "bloqueada": numero > etapa_atual,
        })
    return itens


@app.get("/orcamento/{token}", response_class=HTMLResponse)
def orcamento_publico(token: str, request: Request, db: Session = Depends(get_db)):
    o = db.query(Orcamento).options(selectinload(Orcamento.itens), selectinload(Orcamento.manutencao).selectinload(Manutencao.cliente), selectinload(Orcamento.manutencao).selectinload(Manutencao.equipamento)).filter(Orcamento.token == token).first()
    if not o: raise HTTPException(404)
    return templates.TemplateResponse("organiza/orcamento_publico.html", {
        "request": request,
        "orcamento": o,
        "m": o.manutencao,
        "totais": totais_orcamento(o),
        "linha_tempo": linha_tempo_publica(o.manutencao),
        "etapa_atual": etapa_manutencao(o.manutencao),
    })


@app.post("/orcamento/{token}/responder")
async def orcamento_responder(token: str, request: Request, db: Session = Depends(get_db)):
    o = db.query(Orcamento).options(selectinload(Orcamento.itens), selectinload(Orcamento.manutencao)).filter(Orcamento.token == token).first(); form = dict(await request.form())
    if not o: raise HTTPException(404)
    acao = form.get("acao")
    if acao == "cancelar":
        o.status = "Cancelado"
        o.manutencao.status = "Cancelado"
    else:
        modalidade = "todos" if acao == "aprovar" else "obrigatorios"
        registrar_aprovacao_orcamento(o, modalidade, "cliente")
    db.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        if acao == "cancelar":
            mensagem = "Orçamento cancelado."
            status = "Cancelado"
        elif acao == "aprovar":
            mensagem = "Orçamento completo aprovado."
            status = "Tudo aprovado"
        else:
            mensagem = "Itens obrigatórios aprovados."
            status = "Obrigatórios aprovados"
        return JSONResponse({"ok": True, "mensagem": mensagem, "status": status})
    return RedirectResponse(f"/orcamento/{token}?ok=1", status_code=303)

# -----------------------------------------------------------------------------
# Portal público simplificado para solicitação de manutenção
# -----------------------------------------------------------------------------
HORARIOS_LOJA = ["14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00"]
HORARIOS_ONLINE = ["11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00"]
HORARIOS_ENTREGA_PUBLICA = HORARIOS_LOJA

def horarios_atendimento(tipo: str):
    return HORARIOS_ONLINE if tipo == "online" else HORARIOS_LOJA

def horario_atendimento_valido(tipo: str, momento):
    return bool(momento and momento.weekday() < 5 and momento.strftime("%H:%M") in horarios_atendimento(tipo))

def horario_atendimento_ocupado(db: Session, momento, ignorar_id: int = 0):
    q = db.query(Manutencao).filter(Manutencao.entrega_prevista_em == momento, Manutencao.status != "Encerrada")
    if ignorar_id:
        q = q.filter(Manutencao.id != ignorar_id)
    return q.first() is not None
STATUS_MANUTENCAO_ENCERRADOS = ["Encerrada", "Cancelada"]


def cliente_por_whatsapp(db: Session, telefone: str):
    numero = limpar_telefone(telefone)
    if not telefone_valido(numero):
        return None
    # Os telefones antigos podem estar formatados de maneiras diferentes.
    # A comparação normalizada preserva compatibilidade com os cadastros atuais.
    for cliente in db.query(Cliente).options(selectinload(Cliente.equipamentos)).all():
        if limpar_telefone(cliente.telefone) == numero:
            return cliente
    return None


def equipamento_com_manutencao_aberta(db: Session, equipamento_id: int):
    return db.query(Manutencao).filter(
        Manutencao.equipamento_id == equipamento_id,
        Manutencao.status.notin_(STATUS_MANUTENCAO_ENCERRADOS),
    ).order_by(Manutencao.criado_em.desc()).first()


def equipamentos_portal(db: Session, cliente: Cliente):
    """No chamado público aparecem somente equipamentos ativos."""
    resultado = []
    ativos = [e for e in cliente.equipamentos if (e.status or "Ativo") == "Ativo"]
    for equipamento in ordenar_equipamentos(ativos):
        aberta = equipamento_com_manutencao_aberta(db, equipamento.id)
        resultado.append({"equipamento": equipamento, "manutencao_aberta": aberta})
    return resultado


@app.get("/solicitar-manutencao", response_class=HTMLResponse)
def manutencao_publica_inicio(request: Request):
    return templates.TemplateResponse("organiza/manutencao_publica.html", {
        "request": request,
        "etapa": "telefone",
        "erro": "",
        "telefone": "",
        "horarios": HORARIOS_ENTREGA_PUBLICA,
    })


def renderizar_equipamentos_publicos(request: Request, db: Session, cliente: Cliente, telefone: str, erro: str = "", form_anterior=None, status_code: int = 200):
    return templates.TemplateResponse("organiza/manutencao_publica.html", {
        "request": request,
        "etapa": "equipamentos",
        "erro": erro,
        "telefone": telefone,
        "cliente": cliente,
        "equipamentos": equipamentos_portal(db, cliente),
        "horarios": HORARIOS_LOJA,
        "horarios_loja": HORARIOS_LOJA,
        "horarios_online": HORARIOS_ONLINE,
        "data_minima": date.today().isoformat(),
        "form_anterior": form_anterior,
    }, status_code=status_code)


@app.post("/solicitar-manutencao/pesquisar", response_class=HTMLResponse)
async def manutencao_publica_pesquisar(request: Request, db: Session = Depends(get_db)):
    try:
        form = dict(await request.form())
    except ClientDisconnect:
        return RedirectResponse("/solicitar-manutencao", status_code=303)
    telefone = limpar_telefone(form.get("telefone") or "")
    cliente = cliente_por_whatsapp(db, telefone)
    if not cliente:
        return templates.TemplateResponse("organiza/manutencao_publica.html", {
            "request": request,
            "etapa": "telefone",
            "erro": "WhatsApp não encontrado. Informe o mesmo número utilizado no cadastro, com DDD.",
            "telefone": telefone,
            "horarios": HORARIOS_ENTREGA_PUBLICA,
        }, status_code=400)

    return templates.TemplateResponse("organiza/manutencao_publica.html", {
        "request": request,
        "etapa": "revisar_dados",
        "erro": "",
        "telefone": telefone,
        "cliente": cliente,
        "ano_atual": date.today().year,
        "horarios": HORARIOS_ENTREGA_PUBLICA,
    })


@app.post("/solicitar-manutencao/continuar", response_class=HTMLResponse)
async def manutencao_publica_continuar(request: Request, db: Session = Depends(get_db)):
    try:
        form = dict(await request.form())
    except ClientDisconnect:
        return RedirectResponse("/solicitar-manutencao", status_code=303)
    telefone = limpar_telefone(form.get("telefone") or "")
    cliente = cliente_por_whatsapp(db, telefone)
    if not cliente:
        return RedirectResponse("/solicitar-manutencao", status_code=303)
    return renderizar_equipamentos_publicos(request, db, cliente, telefone)


@app.post("/solicitar-manutencao/revisar-dados", response_class=HTMLResponse)
async def manutencao_publica_revisar_dados(request: Request, db: Session = Depends(get_db)):
    try:
        form = dict(await request.form())
    except ClientDisconnect:
        return RedirectResponse("/solicitar-manutencao", status_code=303)

    telefone_original = limpar_telefone(form.get("telefone_original") or form.get("telefone") or "")
    cliente = cliente_por_whatsapp(db, telefone_original)
    if not cliente:
        return RedirectResponse("/solicitar-manutencao", status_code=303)

    nome = limpar_nome_cliente(form.get("nome") or "")
    documento = limpar_documento(form.get("documento") or "")
    telefone_novo = limpar_telefone(form.get("telefone") or "")
    email = (form.get("email") or "").strip()
    obrigatorios = {
        "nome completo": nome, "CPF": documento, "WhatsApp": telefone_novo, "e-mail": email,
        "CEP": (form.get("cep") or "").strip(), "endereço": (form.get("endereco") or "").strip(),
        "número": (form.get("endereco_numero") or "").strip(), "bairro": (form.get("bairro") or "").strip(),
        "município": (form.get("municipio") or "").strip(), "estado": (form.get("estado") or "").strip(),
    }
    faltantes = [rotulo for rotulo, valor in obrigatorios.items() if not valor]
    erro = ""
    if faltantes:
        erro = "Preencha os campos obrigatórios: " + ", ".join(faltantes) + "."
    elif not cpf_valido(documento):
        erro = "Informe um CPF válido."
    elif not telefone_valido(telefone_novo):
        erro = "Informe um WhatsApp válido com 11 dígitos, incluindo DDD."
    elif db.query(Cliente).filter(Cliente.documento == documento, Cliente.id != cliente.id).first():
        erro = "Este CPF já pertence a outro cadastro."
    elif db.query(Cliente).filter(Cliente.telefone == telefone_novo, Cliente.id != cliente.id).first():
        erro = "Este WhatsApp já pertence a outro cadastro."

    if erro:
        for campo, valor in form.items():
            if hasattr(cliente, campo) and campo not in ("id",):
                setattr(cliente, campo, valor)
        cliente.nome = nome
        cliente.documento = documento
        cliente.telefone = telefone_novo
        return templates.TemplateResponse("organiza/manutencao_publica.html", {
            "request": request, "etapa": "revisar_dados", "erro": erro,
            "telefone": telefone_original, "cliente": cliente, "ano_atual": date.today().year,
            "horarios": HORARIOS_ENTREGA_PUBLICA,
        }, status_code=400)

    cliente.nome = nome
    cliente.documento = documento
    cliente.telefone = telefone_novo
    cliente.email = email
    cliente.empresa = (form.get("empresa") or "").strip() or None
    cliente.cep = (form.get("cep") or "").strip()
    cliente.municipio = (form.get("municipio") or "").strip()
    cliente.cidade = cliente.municipio
    cliente.estado = (form.get("estado") or "").strip().upper()
    cliente.endereco = (form.get("endereco") or "").strip()
    cliente.endereco_numero = (form.get("endereco_numero") or "").strip()
    cliente.complemento = (form.get("complemento") or "").strip() or None
    cliente.bairro = (form.get("bairro") or "").strip()
    db.commit()
    db.refresh(cliente)

    return renderizar_equipamentos_publicos(request, db, cliente, telefone_novo)


@app.post("/solicitar-manutencao/criar", response_class=HTMLResponse)
async def manutencao_publica_criar(request: Request, db: Session = Depends(get_db)):
    try:
        raw_form = await request.form()
    except ClientDisconnect:
        return RedirectResponse("/solicitar-manutencao?erro=Conexão interrompida. Tente novamente.", status_code=303)
    form = dict(raw_form)
    telefone = limpar_telefone(form.get("telefone") or "")
    cliente = cliente_por_whatsapp(db, telefone)
    if not cliente:
        return RedirectResponse("/solicitar-manutencao", status_code=303)

    selecionados = [int(v) for v in raw_form.getlist("equipamento_id") if str(v).isdigit()]

    tipo_atendimento = (form.get("tipo_atendimento") or "loja").strip().lower()
    if tipo_atendimento not in ("loja", "online"):
        tipo_atendimento = "loja"
    data_texto = (form.get("data_entrega") or "").strip()
    hora_texto = (form.get("hora_entrega") or "").strip()
    erro = ""
    data_entrega = None

    try:
        data_entrega = datetime.strptime(data_texto, "%Y-%m-%d").date()
    except ValueError:
        erro = "Informe uma data válida para a entrega."

    if not erro and (data_entrega < date.today() or data_entrega.weekday() > 4):
        erro = "A entrega deve ser agendada de segunda a sexta-feira."
    if not erro and hora_texto not in horarios_atendimento(tipo_atendimento):
        erro = "Escolha um horário válido: loja das 14:00 às 17:00 ou WhatsApp/online das 11:00 às 17:00."
    if not erro and not selecionados:
        erro = "Selecione pelo menos um equipamento."

    equipamentos_validos = db.query(Equipamento).filter(
        Equipamento.cliente_id == cliente.id,
        Equipamento.id.in_(selecionados or [-1]),
    ).all()
    if not erro and len(equipamentos_validos) != len(set(selecionados)):
        erro = "Um dos equipamentos selecionados não pertence ao cadastro informado."

    if not erro:
        for equipamento in equipamentos_validos:
            if equipamento_com_manutencao_aberta(db, equipamento.id):
                erro = f"O equipamento {equipamento.tipo or 'Equipamento'} {equipamento.modelo or ''} já possui uma manutenção em aberto."
                break
            descricao = (form.get(f"descricao_{equipamento.id}") or "").strip()
            if not descricao:
                erro = f"Descreva o problema do equipamento {equipamento.tipo or ''} {equipamento.modelo or ''}."
                break

    if erro:
        return renderizar_equipamentos_publicos(request, db, cliente, telefone, erro, form, 400)

    entrega_em = datetime.combine(data_entrega, datetime.strptime(hora_texto, "%H:%M").time())
    if horario_atendimento_ocupado(db, entrega_em):
        return renderizar_equipamentos_publicos(request, db, cliente, telefone, "Este horário já está reservado. Escolha outro horário disponível.", form, 409)
    criadas = []
    for equipamento in equipamentos_validos:
        manutencao = Manutencao(
            cliente_id=cliente.id,
            equipamento_id=equipamento.id,
            defeito=(form.get(f"descricao_{equipamento.id}") or "").strip(),
            entrega_prevista_em=entrega_em,
            tipo_atendimento=tipo_atendimento,
            status="Aguardando equipamento",
            observacao="Solicitação criada pelo link público.",
        )
        db.add(manutencao)
        db.flush()
        db.add(Orcamento(
            manutencao_id=manutencao.id,
            versao=1,
            token=secrets.token_urlsafe(24),
            status="Rascunho",
        ))
        criadas.append(manutencao)
    db.commit()

    return templates.TemplateResponse("organiza/manutencao_publica.html", {
        "request": request,
        "etapa": "concluido",
        "erro": "",
        "telefone": telefone,
        "cliente": cliente,
        "criadas": criadas,
        "data_entrega": entrega_em,
        "tipo_atendimento": tipo_atendimento,
        "horarios": horarios_atendimento(tipo_atendimento),
    })
