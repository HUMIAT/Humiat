from urllib.parse import quote_plus, urlencode, urlparse, parse_qs
import base64
import calendar
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
from sqlalchemy.orm import Session, relationship, selectinload, load_only

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
    usuario_humiat_interno, usuario_humiat_equipe_prioritaria, enviar_link_acesso_humiat, aplicar_rotinas_cliente_humiat,
    enviar_email_solvoz_senha_provisoria, enviar_email_solvoz_recuperacao, _enviar_resend_humiat, _solvoz_api,
)

from organiza_performance import (
    PerformanceMiddleware, install_sql_monitor, perf_stage,
    performance_summary, monitor_status, clear_records,
)

from services.comunicacao import (
    ComunicacaoService, PAISES, formatar_telefone as formatar_telefone_internacional,
    normalizar_contato, numero_internacional, telefone_valido as telefone_internacional_valido,
)

from services.theme import HUMIAT_DEFAULT_THEME, normalize_theme

app = FastAPI(title="Organiza | Karaokê RJ", version=ORGANIZA_VERSAO)
app.add_middleware(PerformanceMiddleware)
install_sql_monitor(engine)
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

# InfinitePay no Organiza. Usa a mesma InfiniteTag da Karaokê RJ já adotada
# nos demais sistemas, podendo ser alterada por variável de ambiente.
INFINITEPAY_HANDLE = (os.getenv("INFINITEPAY_HANDLE") or "karaokerj").strip().lstrip("$")
INFINITEPAY_LINKS_URL = "https://api.checkout.infinitepay.io/links"
INFINITEPAY_PAYMENT_CHECK_URL = "https://api.checkout.infinitepay.io/payment_check"
INFINITEPAY_TIMEOUT_SECONDS = max(3, int(os.getenv("INFINITEPAY_TIMEOUT_SECONDS", "15") or 15))

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


# ------------------------------------------------------------------
# HUMIAT Design System v1 — identidade visual por empresa
# ------------------------------------------------------------------
ORGANIZA_THEME_SLUG = normalizar_slug_solvoz(os.getenv("ORGANIZA_THEME_EMPRESA_SLUG", "karaokerj")) or "karaokerj"
_ORGANIZA_THEME_RUNTIME = normalize_theme(company_slug=ORGANIZA_THEME_SLUG)


def _publicar_tema_visual_runtime(tema: dict | None) -> dict:
    global _ORGANIZA_THEME_RUNTIME
    _ORGANIZA_THEME_RUNTIME = dict(tema or normalize_theme(company_slug=ORGANIZA_THEME_SLUG))
    templates.env.globals["HUMIAT_THEME"] = _ORGANIZA_THEME_RUNTIME
    return _ORGANIZA_THEME_RUNTIME


def _tema_visual_local(db: Session, slug: str) -> dict:
    slug_n = normalizar_slug_solvoz(slug)
    row = db.query(TemaVisualEmpresa).filter(TemaVisualEmpresa.slug == slug_n).first()
    if not row or str(row.fonte or "").upper() != "SOLVOZ":
        return normalize_theme(company_slug=slug_n)
    return normalize_theme({
        "tema": row.tema or "solvoz",
        "brand": row.brand,
        "brand_2": row.brand_2,
        "accent": row.accent,
        "bg": row.bg,
        "surface": row.surface,
        "text": row.text,
    }, source="solvoz", company_slug=slug_n)


def _carregar_tema_visual_runtime(db: Session) -> dict:
    return _publicar_tema_visual_runtime(_tema_visual_local(db, ORGANIZA_THEME_SLUG))


def _sincronizar_tema_visual_solvoz(db: Session, slug: str) -> dict:
    """Sincroniza a paleta sem criar dependência externa na renderização.

    É chamada apenas por rotinas administrativas/manuais. Depois disso as telas
    usam exclusivamente o cache local.
    """
    slug_n = normalizar_slug_solvoz(slug)
    if not slug_n:
        raise ValueError("Slug SolVoz inválido para sincronizar tema")
    dados = _solvoz_api(f"/_sv/api/humiat/empresa/{urllib.parse.quote(slug_n)}")
    empresa = dados.get("empresa") or {}
    cores = empresa.get("cores") or {}
    tema = normalize_theme({
        "tema": empresa.get("tema") or "solvoz",
        "brand": cores.get("brand"),
        "brand_2": cores.get("brand_2"),
        "accent": cores.get("accent"),
        "bg": cores.get("bg"),
        "surface": cores.get("surface"),
        "text": cores.get("text"),
    }, source="solvoz", company_slug=slug_n)
    row = db.query(TemaVisualEmpresa).filter(TemaVisualEmpresa.slug == slug_n).first()
    if not row:
        row = TemaVisualEmpresa(slug=slug_n)
        db.add(row)
    row.fonte = "SOLVOZ"
    row.tema = str(tema.get("tema") or "solvoz")[:100]
    row.brand = tema["brand"]
    row.brand_2 = tema["brand_2"]
    row.accent = tema["accent"]
    row.bg = tema["bg"]
    row.surface = tema["surface"]
    row.text = tema["text"]
    db.flush()
    if slug_n == ORGANIZA_THEME_SLUG:
        _publicar_tema_visual_runtime(tema)
    return tema


# Fallback imediato antes do startup carregar o cache do banco.
_publicar_tema_visual_runtime(_ORGANIZA_THEME_RUNTIME)


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


class TemaVisualEmpresa(Base):
    """Cache local da identidade visual mestre vinda do SolVoz.

    Nenhuma tela consulta o SolVoz para renderizar. A identidade é sincronizada
    manualmente e depois lida localmente; se não existir, o padrão HUMIAT assume.
    """
    __tablename__ = "temas_visuais_empresas"
    id = Column(Integer, primary_key=True)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    fonte = Column(String(20), nullable=False, default="HUMIAT")
    tema = Column(String(100), nullable=True)
    brand = Column(String(7), nullable=True)
    brand_2 = Column(String(7), nullable=True)
    accent = Column(String(7), nullable=True)
    bg = Column(String(7), nullable=True)
    surface = Column(String(7), nullable=True)
    text = Column(String(7), nullable=True)
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
    # ID estável da empresa no SolVoz. O slug pode mudar; este ID mantém o vínculo.
    solvoz_id = Column(Integer, nullable=True, unique=True, index=True)
    nome = Column(String(140), nullable=False)
    slug = Column(String(100), nullable=False, unique=True)
    # Slug global: sempre vem do SolVoz e é a identidade mestre da empresa.
    # connect_slug preserva somente exceções legadas de URL do Connect, como
    # vivikaraoke (global/SolVoz) -> vivioke (Connect), sem quebrar links antigos.
    connect_slug = Column(String(100), nullable=True, unique=True)
    dominio = Column(String(255), nullable=False)
    ativo = Column(Integer, nullable=False, default=1)
    # Toda Empresa SolVoz deve ter um responsável no Organiza. Para empresas de
    # clientes guardamos também o cliente de origem; Karaokê RJ pode apontar
    # diretamente para um Humiat ID interno (Junior/Débora/Luiz).
    responsavel_cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=True, index=True)
    responsavel_humiat_usuario_id = Column(Integer, ForeignKey("humiat_usuarios.id"), nullable=True, index=True)
    # Miniatura pública usada pela LokaFest. A origem continua sendo o SolVoz;
    # o Organiza apenas mantém uma cópia leve para evitar dependência externa na Home.
    logo_mini_data = Column(LargeBinary, nullable=True)
    logo_mini_mime = Column(String(80), nullable=True)
    logo_mini_hash = Column(String(64), nullable=True)
    logo_mini_atualizado_em = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    responsavel_cliente = relationship("Cliente", foreign_keys=[responsavel_cliente_id])
    responsavel_humiat_usuario = relationship("HumiatUsuario", foreign_keys=[responsavel_humiat_usuario_id])


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


class VendaCupom(Base):
    """Cupom comercial aplicado às vendas de equipamentos."""
    __tablename__ = "venda_cupons"
    id = Column(Integer, primary_key=True)
    codigo = Column(String(80), nullable=False, unique=True, index=True)
    descricao = Column(String(180), nullable=True)
    tipo = Column(String(20), nullable=False, default="PERCENTUAL")
    valor = Column(Float, nullable=False, default=0)
    ativo = Column(Integer, nullable=False, default=1)
    criado_em = Column(DateTime, server_default=func.now())
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
    catalogo_impresso = Column(String(20), nullable=False, default="NA")
    # Opcionais dinâmicos criados no cadastro de Opcionais. Mantém os campos legados acima por compatibilidade.
    opcionais_json = Column(Text, nullable=True)
    # 1.1.62: vínculo comercial/custo de produção. O cadastro mestre fica separado
    # da máquina física do cliente para permitir composição e custos por modelo.
    produto_venda_id = Column(Integer, ForeignKey("venda_modelos_equipamento.id"), nullable=True, index=True)
    catalogo_venda = Column(String(20), nullable=False, default="BASICO")
    produto_venda_nome_snapshot = Column(String(180), nullable=True)
    custo_base_snapshot = Column(Float, nullable=True)
    custo_opcionais_snapshot = Column(Float, nullable=True)
    custo_final_snapshot = Column(Float, nullable=True)
    preco_venda_snapshot = Column(Float, nullable=True)
    lucro_snapshot = Column(Float, nullable=True)
    margem_snapshot = Column(Float, nullable=True)
    custo_snapshot_em = Column(DateTime, nullable=True)
    # 1.1.64: desconto/cupom da venda. preco_venda é o valor bruto; valor é o total final.
    cupom_id = Column(Integer, ForeignKey("venda_cupons.id"), nullable=True, index=True)
    cupom_codigo_snapshot = Column(String(80), nullable=True)
    cupom_desconto_snapshot = Column(Float, nullable=False, default=0)
    desconto_manual = Column(Float, nullable=False, default=0)
    # 1.1.79: frete da venda é separado do preço do equipamento e não recebe desconto/cupom.
    frete_venda = Column(Float, nullable=False, default=0)
    # Link público da venda é separado do link simples de cadastro do cliente.
    venda_token = Column(String(64), nullable=True, unique=True, index=True)
    # 1.1.74: correção manual do material efetivamente usado nesta máquina.
    # Quando manual=0, o consumo continua acompanhando composição + Opcionais.
    estoque_uso_override = Column(Text, nullable=True)
    estoque_uso_manual = Column(Integer, nullable=False, default=0)
    # 1.2.44: marcado = venda gera saída física; desmarcado = não movimenta estoque.
    descontar_estoque = Column(Integer, nullable=False, default=1)
    criado_em = Column(DateTime, server_default=func.now())
    cliente = relationship("Cliente", back_populates="equipamentos")
    solvoz_empresa = relationship("SolVozEmpresa")
    produto_venda = relationship("VendaModeloEquipamento")
    cupom = relationship("VendaCupom")


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


class Fornecedor(Base):
    __tablename__ = "fornecedores"
    id = Column(Integer, primary_key=True)
    nome = Column(String(180), unique=True, nullable=False, index=True)
    ativo = Column(Integer, nullable=False, default=1)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Item(Base):
    __tablename__ = "catalogo_itens"
    id = Column(Integer, primary_key=True)
    codigo = Column(String(30), nullable=True)
    nome = Column(String(180), unique=True, nullable=False)
    categoria = Column(String(80), nullable=False, default="Geral")
    # 1.1.74: controle individual. Categorias lógicas ainda podem forçar fora do estoque.
    controla_estoque = Column(Integer, nullable=False, default=1)
    fornecedor_id = Column(Integer, ForeignKey("fornecedores.id"), nullable=True, index=True)
    unidade = Column(String(10), nullable=False, default="UN")
    preco_custo = Column(Float, nullable=False, default=0)
    preco_venda = Column(Float, nullable=False, default=0)
    ativo = Column(Integer, nullable=False, default=1)
    criado_em = Column(DateTime, server_default=func.now())
    fornecedor = relationship("Fornecedor")


class VendaModeloEquipamento(Base):
    """Produto comercial vendido, alinhado ao nome/slug do SolVoz."""
    __tablename__ = "venda_modelos_equipamento"
    id = Column(Integer, primary_key=True)
    nome = Column(String(180), nullable=False, unique=True)
    sku = Column(String(80), nullable=True, unique=True, index=True)
    tipo = Column(String(80), nullable=False, default="JUKEBOX")
    solvoz_slug = Column(String(120), nullable=True, unique=True, index=True)
    preco_basico = Column(Float, nullable=False, default=0)
    prazo_producao_dias = Column(Integer, nullable=False, default=20)
    ativo = Column(Integer, nullable=False, default=1)
    ordem = Column(Integer, nullable=False, default=0)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())


class VendaModeloComposicao(Base):
    """Quantidade de cada Item que compõe o custo-base do modelo de venda."""
    __tablename__ = "venda_modelo_composicao"
    id = Column(Integer, primary_key=True)
    modelo_id = Column(Integer, ForeignKey("venda_modelos_equipamento.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("catalogo_itens.id"), nullable=False, index=True)
    quantidade = Column(Float, nullable=False, default=1)
    modelo = relationship("VendaModeloEquipamento")
    item = relationship("Item")


class VendaOpcionalConfig(Base):
    """Mapeia cada escolha de Opcional a um Item do Organiza e sua quantidade."""
    __tablename__ = "venda_opcionais_config"
    id = Column(Integer, primary_key=True)
    campo = Column(String(50), nullable=False, index=True)
    grupo = Column(String(100), nullable=False)
    valor = Column(String(80), nullable=False)
    rotulo = Column(String(100), nullable=False)
    item_id = Column(Integer, ForeignKey("catalogo_itens.id"), nullable=True, index=True)
    quantidade = Column(Float, nullable=False, default=1)
    ordem = Column(Integer, nullable=False, default=0)
    ativo = Column(Integer, nullable=False, default=1)
    padrao = Column(Integer, nullable=False, default=0)
    item = relationship("Item")


class VendaOpcionalModelo(Base):
    """Define se uma categoria de Opcional pode ser usada em cada modelo comercial."""
    __tablename__ = "venda_opcionais_modelos"
    id = Column(Integer, primary_key=True)
    campo = Column(String(50), nullable=False, index=True)
    modelo_id = Column(Integer, ForeignKey("venda_modelos_equipamento.id"), nullable=False, index=True)
    habilitado = Column(Integer, nullable=False, default=1)
    modelo = relationship("VendaModeloEquipamento")


class AgendaManual(Base):
    __tablename__ = "agenda_manual"
    id = Column(Integer, primary_key=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=True, index=True)
    titulo = Column(String(180), nullable=False)
    # tipo é mantido para compatibilidade visual com compromissos antigos.
    tipo = Column(String(40), nullable=False, default="visita")
    categoria = Column(String(30), nullable=True, index=True)
    local_atendimento = Column(String(20), nullable=True, index=True)
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


class AtualizacaoLinkPadrao(Base):
    __tablename__ = "atualizacao_links_padrao"
    id = Column(Integer, primary_key=True)
    chave = Column(String(80), nullable=False, unique=True, index=True)
    nome = Column(String(160), nullable=False)
    drive_url = Column(String(1000), nullable=True)
    drive_file_id = Column(String(255), nullable=True)
    ativo = Column(Integer, nullable=False, default=1)
    ordem = Column(Integer, nullable=False, default=0)
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
    valor_a_pagar_centavos = Column(Integer, nullable=True)
    frete_centavos = Column(Integer, nullable=True)
    valor_pago_centavos = Column(Integer, nullable=True)
    forma_pagamento = Column(String(60), nullable=True)
    status = Column(String(30), nullable=False, default="PAGO", index=True)
    pago_em = Column(DateTime, nullable=True)
    arquivos_liberados_em = Column(DateTime, nullable=True)
    email_arquivos_enviado_em = Column(DateTime, nullable=True)
    email_erro = Column(Text, nullable=True)
    concluido_em = Column(DateTime, nullable=True, index=True)
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


class AtualizacaoPreReserva(Base):
    __tablename__ = "atualizacao_pre_reservas"
    id = Column(Integer, primary_key=True)
    token = Column(String(80), nullable=False, unique=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False, index=True)
    tipo = Column(String(20), nullable=False, index=True)
    data_hora = Column(DateTime, nullable=False, index=True)
    expira_em = Column(DateTime, nullable=False, index=True)
    status = Column(String(30), nullable=False, default="PENDENTE", index=True)
    order_nsu = Column(String(120), nullable=True, index=True)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())


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
    # 1.2.44: quando 0, a manutenção não gera saída física; quando 1, orçamento aprovado desconta.
    descontar_estoque = Column(Integer, nullable=False, default=1)
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


class InfinitePayCobrancaOrganiza(Base):
    """Cobranças InfinitePay de vendas/manutenções do Organiza.

    O pagamento confirmado vira o mesmo Pagamento/PagamentoVenda já usado pelas
    telas manuais. A cobrança existe apenas para idempotência e auditoria.
    """
    __tablename__ = "organiza_infinitepay_cobrancas"
    id = Column(Integer, primary_key=True)
    origem_tipo = Column(String(20), nullable=False, index=True)  # VENDA | MANUTENCAO
    origem_id = Column(Integer, nullable=False, index=True)
    order_nsu = Column(String(140), nullable=False, unique=True, index=True)
    valor_centavos = Column(Integer, nullable=False)
    status = Column(String(40), nullable=False, default="AGUARDANDO_PAGAMENTO", index=True)
    checkout_url = Column(String(1200), nullable=True)
    transaction_nsu = Column(String(180), nullable=True, unique=True, index=True)
    invoice_slug = Column(String(180), nullable=True)
    receipt_url = Column(String(1200), nullable=True)
    capture_method = Column(String(60), nullable=True)
    installments = Column(Integer, nullable=True)
    paid_amount_centavos = Column(Integer, nullable=True)
    pagamento_id = Column(Integer, nullable=True, index=True)
    pago_em = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())



def _hoje_organiza() -> date:
    """Data operacional do Rio/São Paulo, independente do fuso UTC do servidor."""
    try:
        return datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    except Exception:
        return date.today()


def _infinitepay_public_url(path: str) -> str:
    return f"{PUBLIC_BASE_URL.rstrip('/')}/{str(path or '').lstrip('/')}"


def _infinitepay_post_organiza(url: str, payload: dict) -> dict:
    dados = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=dados,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"HUMIAT-Organiza/{ORGANIZA_VERSION}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=INFINITEPAY_TIMEOUT_SECONDS) as resp:
            bruto = resp.read().decode("utf-8", errors="replace")
            obj = json.loads(bruto or "{}")
            return obj if isinstance(obj, dict) else {}
    except urllib.error.HTTPError as exc:
        detalhe = exc.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"InfinitePay respondeu HTTP {exc.code}: {detalhe}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Não foi possível acessar a InfinitePay: {exc.reason}") from exc


def _infinitepay_forma(capture_method: str) -> str:
    metodo = str(capture_method or "").strip().lower()
    if "pix" in metodo:
        return "PIX"
    if any(x in metodo for x in ("credit", "card", "credito", "cartao", "cartão")):
        return "Cartão"
    return "InfinitePay"


def _infinitepay_customer(cliente: Cliente | None) -> dict | None:
    if not cliente:
        return None
    email = str(cliente.email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return None
    telefone = re.sub(r"\D", "", str(cliente.whatsapp_completo() or cliente.telefone or ""))
    if telefone and not telefone.startswith("55") and (cliente.pais or "BR").upper() == "BR":
        telefone = "55" + telefone
    customer = {
        "name": str(cliente.nome or "Cliente")[:160],
        "email": email[:180],
        "phone_number": ("+" + telefone) if telefone else "",
    }
    return {k: v for k, v in customer.items() if v}


def _infinitepay_cobranca_pendente(db: Session, origem_tipo: str, origem_id: int):
    return (
        db.query(InfinitePayCobrancaOrganiza)
        .filter(
            InfinitePayCobrancaOrganiza.origem_tipo == str(origem_tipo).upper(),
            InfinitePayCobrancaOrganiza.origem_id == int(origem_id),
            InfinitePayCobrancaOrganiza.status == "AGUARDANDO_PAGAMENTO",
            InfinitePayCobrancaOrganiza.checkout_url.isnot(None),
        )
        .order_by(InfinitePayCobrancaOrganiza.id.desc())
        .first()
    )


def _infinitepay_criar_cobranca_organiza(
    db: Session,
    *,
    origem_tipo: str,
    origem_id: int,
    valor: float,
    cliente: Cliente | None,
    descricao: str,
    itens_checkout: list[dict] | None = None,
) -> InfinitePayCobrancaOrganiza:
    origem_tipo = str(origem_tipo or "").strip().upper()
    valor_centavos = int(round(max(float(valor or 0), 0) * 100))
    if origem_tipo not in {"VENDA", "MANUTENCAO"}:
        raise ValueError("Origem de cobrança inválida.")
    if valor_centavos <= 0:
        raise ValueError("Informe um valor válido para a cobrança.")

    pendente = _infinitepay_cobranca_pendente(db, origem_tipo, origem_id)
    if pendente:
        if int(pendente.valor_centavos or 0) != valor_centavos:
            raise ValueError(
                f"Já existe uma cobrança InfinitePay pendente de R$ {float(pendente.valor_centavos or 0)/100:.2f}. "
                "Abra essa cobrança antes de gerar outra."
            )
        return pendente

    order_nsu = (
        f"ORGANIZA-{origem_tipo[:3]}-{int(origem_id)}-"
        f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4).upper()}"
    )
    cobranca = InfinitePayCobrancaOrganiza(
        origem_tipo=origem_tipo,
        origem_id=int(origem_id),
        order_nsu=order_nsu,
        valor_centavos=valor_centavos,
        status="AGUARDANDO_PAGAMENTO",
    )
    db.add(cobranca)
    db.flush()

    itens_validos = []
    for item in (itens_checkout or []):
        try:
            quantidade = max(int(item.get("quantity") or 1), 1)
            preco = max(int(item.get("price") or 0), 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if preco <= 0:
            continue
        itens_validos.append({
            "quantity": quantidade,
            "price": preco,
            "description": str(item.get("description") or descricao or f"{origem_tipo} #{origem_id}")[:180],
        })
    total_itens = sum(int(i["quantity"]) * int(i["price"]) for i in itens_validos)
    if total_itens != valor_centavos:
        # Cobrança parcial ou valor diferente do detalhamento completo: envia um único item
        # para garantir que o checkout tenha exatamente o valor solicitado.
        itens_validos = [{
            "quantity": 1,
            "price": valor_centavos,
            "description": str(descricao or f"{origem_tipo} #{origem_id}")[:180],
        }]

    payload = {
        "handle": INFINITEPAY_HANDLE,
        "order_nsu": order_nsu,
        "redirect_url": _infinitepay_public_url("/organiza/infinitepay/retorno"),
        "webhook_url": _infinitepay_public_url("/api/integracoes/infinitepay/organiza/webhook"),
        "items": itens_validos,
    }
    customer = _infinitepay_customer(cliente)
    if customer:
        payload["customer"] = customer

    try:
        resposta = _infinitepay_post_organiza(INFINITEPAY_LINKS_URL, payload)
        checkout_url = str(resposta.get("url") or "").strip()
        if not checkout_url.startswith("https://"):
            raise RuntimeError("InfinitePay não retornou uma URL válida de checkout.")
        cobranca.checkout_url = checkout_url
        db.commit()
        db.refresh(cobranca)
        return cobranca
    except Exception:
        cobranca.status = "ERRO_CHECKOUT"
        db.commit()
        raise


def _infinitepay_registrar_pagamento_organiza(
    db: Session,
    cobranca: InfinitePayCobrancaOrganiza,
    *,
    transaction_nsu: str,
    invoice_slug: str = "",
    receipt_url: str = "",
    capture_method: str = "",
    installments: int = 0,
    paid_amount_centavos: int = 0,
) -> bool:
    cobranca = (
        db.query(InfinitePayCobrancaOrganiza)
        .filter(InfinitePayCobrancaOrganiza.id == int(cobranca.id))
        .with_for_update()
        .first()
    )
    if not cobranca:
        return False
    if cobranca.status == "PAGO" and cobranca.pagamento_id:
        return True

    transaction_nsu = str(transaction_nsu or "").strip()
    if not transaction_nsu:
        return False
    duplicada = db.query(InfinitePayCobrancaOrganiza).filter(
        InfinitePayCobrancaOrganiza.id != cobranca.id,
        InfinitePayCobrancaOrganiza.transaction_nsu == transaction_nsu,
    ).first()
    if duplicada:
        return False

    esperado = max(int(cobranca.valor_centavos or 0), 0)
    informado = max(int(paid_amount_centavos or esperado), 0)
    efetivo = min(informado, esperado) if esperado > 0 else informado
    if efetivo <= 0:
        return False
    valor = round(efetivo / 100.0, 2)
    forma = _infinitepay_forma(capture_method)
    observacao = f"InfinitePay • {cobranca.order_nsu} • {transaction_nsu}"[:1000]

    if cobranca.origem_tipo == "VENDA":
        eq = db.query(Equipamento).filter(Equipamento.id == cobranca.origem_id).first()
        if not eq:
            return False
        pagamento = PagamentoVenda(
            equipamento_id=eq.id,
            data=_hoje_organiza(),
            valor=valor,
            banco="InfinitePay",
            forma=forma,
            observacao=observacao,
        )
        db.add(pagamento)
        db.flush()
        atualizar_datas_producao_venda(eq, pagamento.data)
        cobranca.pagamento_id = pagamento.id
    elif cobranca.origem_tipo == "MANUTENCAO":
        m = db.query(Manutencao).filter(Manutencao.id == cobranca.origem_id).first()
        if not m or not m.orcamentos:
            return False
        o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
        pagamento = Pagamento(
            orcamento_id=o.id,
            data=_hoje_organiza(),
            valor=valor,
            forma=forma,
            banco="InfinitePay",
            observacao=observacao,
        )
        db.add(pagamento)
        db.flush()
        cobranca.pagamento_id = pagamento.id
    else:
        return False

    cobranca.transaction_nsu = transaction_nsu[:180]
    cobranca.invoice_slug = str(invoice_slug or "")[:180] or None
    cobranca.receipt_url = str(receipt_url or "")[:1200] or None
    cobranca.capture_method = str(capture_method or "")[:60] or None
    cobranca.installments = int(installments or 0) or None
    cobranca.paid_amount_centavos = efetivo
    cobranca.pago_em = datetime.now()
    cobranca.status = "PAGO"
    db.commit()
    return True


class EstoqueMovimento(Base):
    """Movimento físico de estoque. Entrada soma; saída reduz o saldo físico."""
    __tablename__ = "estoque_movimentos"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("catalogo_itens.id"), nullable=False, index=True)
    tipo = Column(String(20), nullable=False, index=True)  # ENTRADA | SAIDA
    quantidade = Column(Float, nullable=False, default=0)
    cor = Column(String(80), nullable=True, index=True)
    origem_tipo = Column(String(30), nullable=False, default="MANUAL", index=True)
    origem_id = Column(Integer, nullable=True, index=True)
    origem_item_id = Column(Integer, nullable=True, index=True)
    custo_unitario = Column(Float, nullable=True)
    observacao = Column(Text, nullable=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())
    item = relationship("Item")
    usuario = relationship("Usuario")


class EstoqueReserva(Base):
    """Reserva operacional. Não altera o físico; reduz somente o saldo disponível."""
    __tablename__ = "estoque_reservas"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("catalogo_itens.id"), nullable=False, index=True)
    quantidade = Column(Float, nullable=False, default=0)
    cor = Column(String(80), nullable=True, index=True)
    origem_tipo = Column(String(30), nullable=False, default="MANUTENCAO", index=True)
    origem_id = Column(Integer, nullable=False, index=True)
    origem_item_id = Column(Integer, nullable=True, index=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())
    item = relationship("Item")


class EstoqueCorUso(Base):
    """Distribuição por cor dos itens que variam conforme a arte da máquina."""
    __tablename__ = "estoque_cor_usos"
    id = Column(Integer, primary_key=True)
    origem_tipo = Column(String(30), nullable=False, index=True)  # VENDA | MANUTENCAO
    origem_id = Column(Integer, nullable=False, index=True)       # Equipamento.id ou OrcamentoItem.id
    item_id = Column(Integer, ForeignKey("catalogo_itens.id"), nullable=False, index=True)
    cor = Column(String(80), nullable=False)
    quantidade = Column(Float, nullable=False, default=0)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())
    item = relationship("Item")


class EstoqueMinimo(Base):
    """Estoque mínimo por item e, quando aplicável, por cor."""
    __tablename__ = "estoque_minimos"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("catalogo_itens.id"), nullable=False, index=True)
    cor = Column(String(80), nullable=False, default="", index=True)
    quantidade = Column(Float, nullable=False, default=0)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())
    item = relationship("Item")


class EstoqueContagemProgresso(Base):
    """Marca o progresso da contagem física para permitir implantação por etapas."""
    __tablename__ = "estoque_contagem_progresso"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("catalogo_itens.id"), nullable=False, index=True)
    cor = Column(String(80), nullable=False, default="", index=True)
    quantidade = Column(Float, nullable=False, default=0)
    minimo = Column(Float, nullable=True)
    observacao = Column(Text, nullable=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())
    item = relationship("Item")
    usuario = relationship("Usuario")


class EstoqueCompraPedido(Base):
    """Compra de material aguardando chegada. Só aumenta o físico quando confirmada."""
    __tablename__ = "estoque_compras_pedidos"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("catalogo_itens.id"), nullable=False, index=True)
    fornecedor_id = Column(Integer, ForeignKey("fornecedores.id"), nullable=True, index=True)
    cor = Column(String(80), nullable=True, index=True)
    quantidade = Column(Float, nullable=False, default=0)
    valor_total = Column(Float, nullable=False, default=0)
    previsao_entrega = Column(Date, nullable=True, index=True)
    status = Column(String(20), nullable=False, default="AGUARDANDO", index=True)
    observacao = Column(Text, nullable=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    recebido_em = Column(DateTime, nullable=True)
    item = relationship("Item")
    fornecedor = relationship("Fornecedor")
    usuario = relationship("Usuario")


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



# -----------------------------------------------------------------------------
# ESTOQUE 1.1.66
# Primeira implantação operacional do estoque:
# - físico nasce da contagem/entradas;
# - Vendas a Fazer e Manutenções a Fazer são reservas, não saídas físicas;
# - ao concluir uma operação nova, a reserva vira saída física;
# - histórico anterior à implantação nunca é baixado retroativamente;
# - todo item pode ter estoque mínimo; BOTÕES, COOLER 12 MM e FITA LED/LED
#   controlam mínimo e contagem também por cor.
# -----------------------------------------------------------------------------
ESTOQUE_ITENS_COR = {"BOTOES", "BOTAO", "COOLER 12 MM", "FITA LED", "LED"}
# Categorias padronizadas para leitura de estoque/cadastro.
CATEGORIAS_ITENS_PADRAO = ["Sistema", "Manutenção", "Info e Eletrônicos", "Som", "Cabos e Conectores", "Botões e LEDs", "Gabinetes", "Espelhos", "Fliperama", "Geral"]
UNIDADES_ITEM = ("UN", "M")
# Itens destas categorias são serviços/valores lógicos e não representam material físico.
ESTOQUE_CATEGORIAS_SEM_CONTROLE = {"SISTEMA", "MANUTENCAO", "MANUTENCOES"}
ESTOQUE_ITENS_SEM_CONTROLE = {"ATUALIZACAO", "CATALOGO ENCARDENADO"}
ESTOQUE_COR_PENDENTE = "SEM COR DEFINIDA"
ESTOQUE_VENDA_A_FAZER = {"Solicitar gabinete", "Montagem", "Pronto para entrega"}
ESTOQUE_MANUTENCAO_FINAL = {"Encerrada"}
ESTOQUE_MANUTENCAO_CANCELADA = {"Cancelada", "Cancelado"}


def _texto_sem_acento(valor: str) -> str:
    return unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode("ascii").strip().upper()


def _normalizar_unidade_item(valor: str | None) -> str:
    unidade = str(valor or "UN").strip().upper()
    return unidade if unidade in UNIDADES_ITEM else "UN"


def item_controla_estoque(item: Item | None) -> bool:
    if not item or not bool(item.ativo):
        return False
    categoria = _texto_sem_acento(item.categoria)
    nome = _texto_sem_acento(item.nome)
    if categoria in ESTOQUE_CATEGORIAS_SEM_CONTROLE:
        return False
    if nome in ESTOQUE_ITENS_SEM_CONTROLE:
        return False
    return bool(getattr(item, "controla_estoque", 1))


def _categoria_sugerida_item_1174(nome: str) -> str | None:
    n = _texto_sem_acento(nome)
    if n in {"ATUALIZACAO", "CATALOGO ENCARDENADO"}:
        return "Sistema"
    if "GABINETE" in n:
        return "Gabinetes"
    if "ESPELHO" in n:
        return "Espelhos"
    if "FLIPERAMA" in n:
        return "Fliperama"
    if n.startswith("CABO ") or n.startswith("CONECTOR ") or n in {"EXTENSAO", "EXTENSOR HDMI"}:
        return "Cabos e Conectores"
    if "BOTAO" in n or "BOTOE" in n or n in {"FITA LED", "LED"}:
        return "Botões e LEDs"
    if any(chave in n for chave in ("AMPLIFICADOR", "CAIXA DE SOM", "DRIVER", "FALANTE", "MICROFONE", "TWEETER", "SUPORTE DE MIC")):
        return "Som"
    if any(chave in n for chave in ("BLUET", "CARTA SD", "COOLER", "HD", "MEMORIA", "MONITOR", "MONITO", "PLACA MAE", "RASPBERRY", "PANDORA", "TECLADO", "INTERFACE", "TV 32", "FONTE")):
        return "Info e Eletrônicos"
    return None


def item_controla_cor(item: Item | None) -> bool:
    if not item_controla_estoque(item):
        return False
    return _texto_sem_acento(item.nome) in ESTOQUE_ITENS_COR


def normalizar_cor(valor: str | None) -> str:
    cor = re.sub(r"\s+", " ", str(valor or "").strip().upper())
    return cor[:80]


def _consumo_venda_itens_base(eq: Equipamento, db: Session) -> dict[int, float]:
    """Quantidade calculada pela composição atual + Opcionais atuais."""
    desejado: dict[int, float] = {}
    if not eq or not eq.produto_venda_id:
        return desejado
    composicao = db.query(VendaModeloComposicao).filter(
        VendaModeloComposicao.modelo_id == eq.produto_venda_id
    ).all()
    for comp in composicao:
        if comp.item_id and float(comp.quantidade or 0) > 0:
            desejado[comp.item_id] = desejado.get(comp.item_id, 0.0) + float(comp.quantidade or 0)
    configs = db.query(VendaOpcionalConfig).filter(
        VendaOpcionalConfig.ativo == 1,
        VendaOpcionalConfig.item_id.isnot(None),
    ).all()
    for cfg in configs:
        if not _opcional_habilitado_modelo(db, eq.produto_venda_id, cfg.campo):
            continue
        valor_atual = _valor_opcional_equipamento(eq, cfg.campo, db)
        if str(valor_atual) != str(cfg.valor or ""):
            continue
        # Microfone com fio já vem da composição e nunca soma de novo.
        if cfg.campo == "microfone" and cfg.valor == "Com fio":
            continue
        if cfg.campo == "microfone" and cfg.valor == "Sem fio":
            padrao = _opcional_config_por_escolha(db, "microfone", "Com fio")
            if padrao and padrao.item_id:
                desejado[padrao.item_id] = max(desejado.get(padrao.item_id, 0.0) - float(padrao.quantidade or 0), 0.0)
                if desejado.get(padrao.item_id, 0) <= 0:
                    desejado.pop(padrao.item_id, None)
        qtd = float(cfg.quantidade or 0)
        if qtd > 0:
            desejado[cfg.item_id] = desejado.get(cfg.item_id, 0.0) + qtd
    return {k: round(v, 4) for k, v in desejado.items() if v > 0}


def _consumo_venda_itens(eq: Equipamento, db: Session) -> dict[int, float]:
    """Consumo efetivo da máquina. Override manual vence a composição automática."""
    if eq and bool(getattr(eq, "estoque_uso_manual", 0)) and (getattr(eq, "estoque_uso_override", None) or "").strip():
        try:
            dados = json.loads(eq.estoque_uso_override or "{}")
            saida = {}
            for item_id, qtd in (dados or {}).items():
                try:
                    iid = int(item_id)
                    quantidade = round(max(float(qtd or 0), 0), 4)
                except (TypeError, ValueError):
                    continue
                if iid > 0 and quantidade > 0:
                    saida[iid] = quantidade
            return saida
        except Exception:
            return {}
    return _consumo_venda_itens_base(eq, db)


def contexto_estoque_utilizado_venda(db: Session, equipamento: Equipamento | None) -> dict:
    quantidades = _consumo_venda_itens(equipamento, db) if equipamento else {}
    ids = list(quantidades.keys())
    itens_map = {i.id: i for i in db.query(Item).filter(Item.id.in_(ids)).all()} if ids else {}
    linhas = []
    for item_id, qtd in quantidades.items():
        item = itens_map.get(item_id)
        if not item or not item_controla_estoque(item):
            continue
        linhas.append({
            "item": item, "quantidade": float(qtd or 0),
            "custo_unitario": float(item.preco_custo or 0),
            "custo_total": round(float(qtd or 0) * float(item.preco_custo or 0), 2),
        })
    linhas.sort(key=lambda x: ((_texto_sem_acento(x["item"].categoria)), (_texto_sem_acento(x["item"].nome))))
    itens_disponiveis = [
        i for i in db.query(Item).filter(Item.ativo == 1).order_by(func.upper(Item.categoria), func.upper(Item.nome)).all()
        if item_controla_estoque(i)
    ]
    return {
        "linhas": linhas, "itens": itens_disponiveis,
        "manual": bool(equipamento and getattr(equipamento, "estoque_uso_manual", 0)),
        "total": round(sum(x["custo_total"] for x in linhas), 2),
    }


def salvar_estoque_utilizado_venda(eq: Equipamento, form: dict, db: Session) -> None:
    if not eq or not eq.id:
        return
    if str(form.get("estoque_uso_recalcular") or "0") == "1":
        eq.estoque_uso_manual = 0
        eq.estoque_uso_override = None
        return
    if str(form.get("estoque_uso_editado") or "0") != "1":
        return
    quantidades: dict[int, float] = {}
    for idx in range(1, 61):
        try:
            item_id = int(form.get(f"estoque_uso_item_{idx}") or 0)
        except (TypeError, ValueError):
            item_id = 0
        try:
            qtd = float(str(form.get(f"estoque_uso_qtd_{idx}") or "0").replace(",", "."))
        except (TypeError, ValueError):
            qtd = 0
        if item_id <= 0 or qtd <= 0:
            continue
        item = db.query(Item).filter(Item.id == item_id, Item.ativo == 1).first()
        if not item or not item_controla_estoque(item):
            continue
        quantidades[item_id] = round(quantidades.get(item_id, 0.0) + qtd, 4)
    eq.estoque_uso_override = json.dumps({str(k): v for k, v in quantidades.items()}, ensure_ascii=False, sort_keys=True)
    eq.estoque_uso_manual = 1


def _usos_cor(db: Session, origem_tipo: str, origem_id: int, item_id: int) -> list[EstoqueCorUso]:
    return db.query(EstoqueCorUso).filter(
        EstoqueCorUso.origem_tipo == origem_tipo,
        EstoqueCorUso.origem_id == int(origem_id),
        EstoqueCorUso.item_id == int(item_id),
    ).order_by(EstoqueCorUso.id.asc()).all()


def _distribuir_por_cor(db: Session, origem_tipo: str, origem_id: int, item: Item, quantidade: float) -> dict[str | None, float]:
    quantidade = max(float(quantidade or 0), 0)
    if quantidade <= 0:
        return {}
    if not item_controla_cor(item):
        return {None: round(quantidade, 4)}
    restante = quantidade
    resultado: dict[str | None, float] = {}
    for uso in _usos_cor(db, origem_tipo, origem_id, item.id):
        if restante <= 0:
            break
        cor = normalizar_cor(uso.cor)
        qtd = max(float(uso.quantidade or 0), 0)
        if not cor or qtd <= 0:
            continue
        qtd_usada = min(qtd, restante)
        resultado[cor] = resultado.get(cor, 0.0) + qtd_usada
        restante -= qtd_usada
    if restante > 0.0001:
        resultado[ESTOQUE_COR_PENDENTE] = resultado.get(ESTOQUE_COR_PENDENTE, 0.0) + restante
    return {k: round(v, 4) for k, v in resultado.items() if v > 0}


def _sincronizar_saidas_origem(db: Session, origem_tipo: str, origem_id: int, desejado: dict[tuple[int, str | None, int | None], float], observacao: str = "") -> None:
    """Mantém uma saída atual por item/cor/origem sem duplicar em novas edições."""
    atuais = db.query(EstoqueMovimento).filter(
        EstoqueMovimento.tipo == "SAIDA",
        EstoqueMovimento.origem_tipo == origem_tipo,
        EstoqueMovimento.origem_id == int(origem_id),
    ).all()
    mapa = {(m.item_id, m.cor or None, m.origem_item_id or None): m for m in atuais}
    chaves = set()
    for chave, quantidade in desejado.items():
        item_id, cor, origem_item_id = chave
        quantidade = round(max(float(quantidade or 0), 0), 4)
        if quantidade <= 0:
            continue
        chaves.add(chave)
        mov = mapa.get(chave)
        if not mov:
            mov = EstoqueMovimento(
                item_id=item_id, tipo="SAIDA", quantidade=quantidade, cor=cor,
                origem_tipo=origem_tipo, origem_id=int(origem_id), origem_item_id=origem_item_id,
                observacao=observacao or None,
            )
            db.add(mov)
        else:
            mov.quantidade = quantidade
            mov.observacao = observacao or mov.observacao
    for chave, mov in mapa.items():
        if chave not in chaves:
            db.delete(mov)


def _sincronizar_reservas_origem(db: Session, origem_tipo: str, origem_id: int, desejado: dict[tuple[int, str | None, int | None], float], observacao: str = "") -> None:
    atuais = db.query(EstoqueReserva).filter(
        EstoqueReserva.origem_tipo == origem_tipo,
        EstoqueReserva.origem_id == int(origem_id),
    ).all()
    mapa = {(r.item_id, r.cor or None, r.origem_item_id or None): r for r in atuais}
    chaves = set()
    for chave, quantidade in desejado.items():
        item_id, cor, origem_item_id = chave
        quantidade = round(max(float(quantidade or 0), 0), 4)
        if quantidade <= 0:
            continue
        chaves.add(chave)
        reserva = mapa.get(chave)
        if not reserva:
            reserva = EstoqueReserva(
                item_id=item_id, quantidade=quantidade, cor=cor,
                origem_tipo=origem_tipo, origem_id=int(origem_id), origem_item_id=origem_item_id,
                observacao=observacao or None,
            )
            db.add(reserva)
        else:
            reserva.quantidade = quantidade
            reserva.observacao = observacao or reserva.observacao
    for chave, reserva in mapa.items():
        if chave not in chaves:
            db.delete(reserva)


def _desejado_venda_estoque(eq: Equipamento, db: Session) -> dict[tuple[int, str | None, int | None], float]:
    quantidades = _consumo_venda_itens(eq, db)
    desejado: dict[tuple[int, str | None, int | None], float] = {}
    if not quantidades:
        return desejado
    itens = {i.id: i for i in db.query(Item).filter(Item.id.in_(list(quantidades.keys()))).all()}
    for item_id, qtd in quantidades.items():
        item = itens.get(item_id)
        if not item or not item_controla_estoque(item):
            continue
        for cor, qtd_cor in _distribuir_por_cor(db, "VENDA", eq.id, item, qtd).items():
            desejado[(item_id, cor, None)] = qtd_cor
    return desejado


def sincronizar_estoque_venda(eq: Equipamento, db: Session) -> None:
    """Regra única: venda marcada para descontar gera SAÍDA física imediatamente.

    Não existe mais reserva de venda na posição oficial do estoque. A composição
    efetivamente usada é baixada do físico assim que a venda é registrada/salva,
    independentemente da etapa comercial. Editar a venda atualiza a mesma saída,
    sem duplicar. Ao marcar "não descontar", a saída automática é removida.
    """
    if not eq or not eq.id:
        return

    # Limpa qualquer reserva legada. A fonte oficial passa a ser estoque_movimentos.
    _sincronizar_reservas_origem(db, "VENDA", eq.id, {})

    if int(getattr(eq, "descontar_estoque", 1) or 0) == 0:
        _sincronizar_saidas_origem(db, "VENDA", eq.id, {})
        return

    desejado = _desejado_venda_estoque(eq, db)
    ids_antes = {int(mid) for (mid,) in db.query(EstoqueMovimento.id).filter(
        EstoqueMovimento.tipo == "SAIDA", EstoqueMovimento.origem_tipo == "VENDA", EstoqueMovimento.origem_id == eq.id
    ).all()}
    _sincronizar_saidas_origem(
        db, "VENDA", eq.id, desejado,
        observacao=f"Venda #{eq.id} · baixa física · {eq.modelo or eq.produto_venda_nome_snapshot or ''}".strip(),
    )
    db.flush()
    # No extrato, a primeira baixa usa a data da venda quando ela foi informada.
    if eq.data_compra:
        for mov in db.query(EstoqueMovimento).filter(
            EstoqueMovimento.tipo == "SAIDA", EstoqueMovimento.origem_tipo == "VENDA", EstoqueMovimento.origem_id == eq.id
        ).all():
            if int(mov.id or 0) not in ids_antes:
                mov.criado_em = datetime.combine(eq.data_compra, time(hour=12))


def salvar_cores_venda(eq: Equipamento, form: dict, db: Session) -> None:
    """Salva distribuição por cor informada na mesma edição de equipamento da venda."""
    if not eq or not eq.id:
        return
    quantidades = _consumo_venda_itens(eq, db)
    if not quantidades:
        return
    itens = {i.id: i for i in db.query(Item).filter(Item.id.in_(list(quantidades.keys()))).all()}
    for item_id, item in itens.items():
        if not item_controla_cor(item):
            continue
        prefixo = f"estoque_cor_{item_id}_"
        if not any(str(k).startswith(prefixo) for k in form.keys()):
            continue
        db.query(EstoqueCorUso).filter(
            EstoqueCorUso.origem_tipo == "VENDA",
            EstoqueCorUso.origem_id == int(eq.id),
            EstoqueCorUso.item_id == int(item_id),
        ).delete(synchronize_session=False)
        for idx in range(1, 13):
            cor = normalizar_cor(form.get(f"estoque_cor_{item_id}_{idx}"))
            try:
                qtd = float(str(form.get(f"estoque_qtd_{item_id}_{idx}") or "0").replace(",", "."))
            except (TypeError, ValueError):
                qtd = 0
            if cor and qtd > 0:
                db.add(EstoqueCorUso(origem_tipo="VENDA", origem_id=eq.id, item_id=item_id, cor=cor, quantidade=qtd))


def _orcamento_aprovado(o: Orcamento | None) -> bool:
    return bool(o and (
        (o.status or "") in ("Aprovado", "Aprovado parcialmente", "Aprovado manualmente")
        or (o.status or "").startswith("Aprovado:")
    ))


def _desejado_manutencao_estoque(m: Manutencao, o: Orcamento, db: Session, somente_aprovados: bool) -> dict[tuple[int, str | None, int | None], float]:
    itens_orcamento = [oi for oi in o.itens if oi.item_id and int(oi.quantidade or 0) > 0 and (not somente_aprovados or oi.aprovado)]
    itens_ids = {oi.item_id for oi in itens_orcamento}
    itens = {i.id: i for i in db.query(Item).filter(Item.id.in_(list(itens_ids))).all()} if itens_ids else {}
    desejado: dict[tuple[int, str | None, int | None], float] = {}
    for oi in itens_orcamento:
        item = itens.get(oi.item_id)
        if not item or not item_controla_estoque(item):
            continue
        for cor, qtd_cor in _distribuir_por_cor(db, "MANUTENCAO", oi.id, item, oi.quantidade).items():
            desejado[(oi.item_id, cor, oi.id)] = qtd_cor
    return desejado


def resumo_estoque_manutencao(m: Manutencao | None, o: Orcamento | None, db: Session) -> list[dict]:
    """Resumo operacional: mostra o que realmente desconta do estoque.

    Manutenção não reserva mais material. Antes da aprovação, ou quando a própria
    manutenção estiver marcada como "não descontar", o item aparece como
    NÃO DESCONTA e não existe em estoque_reservas/estoque_movimentos.
    """
    if not o:
        return []
    item_ids = [oi.item_id for oi in o.itens if oi.item_id]
    itens = {i.id: i for i in db.query(Item).filter(Item.id.in_(item_ids)).all()} if item_ids else {}
    aprovado = _orcamento_aprovado(o)
    manut_desconta = bool(m and int(getattr(m, "descontar_estoque", 1) or 0) != 0)
    linhas = []
    for oi in o.itens:
        item = itens.get(oi.item_id) if oi.item_id else None
        controla = item_controla_estoque(item)
        desconta = bool(controla and manut_desconta and aprovado and bool(oi.aprovado))
        linhas.append({
            "item_id": oi.item_id,
            "descricao": oi.descricao,
            "quantidade": float(oi.quantidade or 0),
            "unidade": _normalizar_unidade_item(getattr(item, "unidade", "UN")),
            "controla_estoque": bool(controla),
            "aprovado": bool(oi.aprovado),
            "estado": "DESCONTA" if desconta else "NAO_DESCONTA",
        })
    return linhas


def sincronizar_estoque_manutencao(m: Manutencao, db: Session) -> None:
    """Sincroniza a manutenção com uma regra única de estoque (1.2.35).

    - manutenção pendente/não aprovada: não reserva e não movimenta;
    - manutenção aprovada: baixa fisicamente os itens aprovados;
    - "não descontar estoque": exclui reservas/saídas automáticas da manutenção;
    - cancelada/encerrada sem aprovação: não movimenta;
    - nunca cria linha de estorno para corrigir: remove a própria movimentação automática.
    """
    if not m or not m.id:
        return

    # Reserva é exclusiva de VENDA. Remove qualquer legado da manutenção sempre.
    _sincronizar_reservas_origem(db, "MANUTENCAO", m.id, {})

    if int(getattr(m, "descontar_estoque", 1) or 0) == 0:
        _sincronizar_saidas_origem(db, "MANUTENCAO", m.id, {})
        return

    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None
    status = (m.status or "").strip()
    cancelada = status in ESTOQUE_MANUTENCAO_CANCELADA or (o and (o.status or "").strip() == "Cancelado")
    if not o or cancelada or not _orcamento_aprovado(o):
        _sincronizar_saidas_origem(db, "MANUTENCAO", m.id, {})
        return

    desejado_saida = _desejado_manutencao_estoque(m, o, db, somente_aprovados=True)
    _sincronizar_saidas_origem(
        db, "MANUTENCAO", m.id, desejado_saida,
        observacao=f"Manutenção #{m.id} aprovada · baixa física",
    )


def salvar_cores_manutencao(orcamento_item: OrcamentoItem, form: dict, db: Session) -> None:
    if not orcamento_item or not orcamento_item.id or not orcamento_item.item_id:
        return
    item = db.query(Item).filter(Item.id == orcamento_item.item_id).first()
    if not item_controla_cor(item):
        return
    db.query(EstoqueCorUso).filter(
        EstoqueCorUso.origem_tipo == "MANUTENCAO",
        EstoqueCorUso.origem_id == int(orcamento_item.id),
        EstoqueCorUso.item_id == int(orcamento_item.item_id),
    ).delete(synchronize_session=False)
    for idx in range(1, 13):
        cor = normalizar_cor(form.get(f"cor_{idx}"))
        try:
            qtd = float(str(form.get(f"qtd_{idx}") or "0").replace(",", "."))
        except (TypeError, ValueError):
            qtd = 0
        if cor and qtd > 0:
            db.add(EstoqueCorUso(origem_tipo="MANUTENCAO", origem_id=orcamento_item.id, item_id=orcamento_item.item_id, cor=cor, quantidade=qtd))


def _mapa_minimos_estoque(db: Session) -> dict[tuple[int, str], float]:
    return {(m.item_id, normalizar_cor(m.cor)): max(float(m.quantidade or 0), 0) for m in db.query(EstoqueMinimo).all()}


def _salvar_minimo_estoque(db: Session, item_id: int, cor: str | None, quantidade: float) -> None:
    cor_n = normalizar_cor(cor)
    registro = db.query(EstoqueMinimo).filter(
        EstoqueMinimo.item_id == int(item_id), EstoqueMinimo.cor == cor_n
    ).first()
    if not registro:
        registro = EstoqueMinimo(item_id=int(item_id), cor=cor_n, quantidade=0)
        db.add(registro)
    registro.quantidade = max(float(quantidade or 0), 0)


def estoque_saldos(db: Session) -> tuple[list[dict], dict[int, list[dict]]]:
    """Posição única de estoque baseada exclusivamente em movimentos físicos.

    Regra 1.2.44:
    - ENTRADA soma;
    - SAÍDA de VENDA reduz o físico no momento em que a venda está marcada para descontar;
    - SAÍDA de MANUTENÇÃO reduz o físico quando o orçamento aprovado deve descontar;
    - não existe segunda subtração por reserva/necessidade.

    As colunas Vendas e Manutenções apenas abrem as saídas já incluídas no saldo
    físico. ``disponivel`` é o próprio saldo físico atual.
    """
    itens = [
        i for i in db.query(Item).filter(Item.ativo == 1).order_by(func.upper(Item.categoria).asc(), func.upper(Item.nome).asc()).all()
        if item_controla_estoque(i)
    ]
    movimentos = db.query(EstoqueMovimento).all()
    minimos = _mapa_minimos_estoque(db)
    por_item: dict[int, dict] = {
        i.id: {
            "item": i, "fisico": 0.0, "vendas_a_fazer": 0.0, "manutencoes_a_fazer": 0.0,
            "reservado": 0.0, "disponivel": 0.0,
        } for i in itens
    }
    cores: dict[int, dict[str, dict]] = {}

    for mov in movimentos:
        if mov.item_id not in por_item:
            continue
        tipo_mov = (mov.tipo or "").upper()
        origem_mov = (mov.origem_tipo or "").upper()
        qtd_mov = float(mov.quantidade or 0)
        sinal = 1 if tipo_mov == "ENTRADA" else -1
        por_item[mov.item_id]["fisico"] += sinal * qtd_mov
        if tipo_mov == "SAIDA" and origem_mov == "VENDA":
            por_item[mov.item_id]["vendas_a_fazer"] += qtd_mov
        elif tipo_mov == "SAIDA" and origem_mov == "MANUTENCAO":
            por_item[mov.item_id]["manutencoes_a_fazer"] += qtd_mov

        if mov.cor:
            c = cores.setdefault(mov.item_id, {}).setdefault(
                mov.cor, {"fisico": 0.0, "vendas_a_fazer": 0.0, "manutencoes_a_fazer": 0.0}
            )
            c["fisico"] += sinal * qtd_mov
            if tipo_mov == "SAIDA" and origem_mov == "VENDA":
                c["vendas_a_fazer"] += qtd_mov
            elif tipo_mov == "SAIDA" and origem_mov == "MANUTENCAO":
                c["manutencoes_a_fazer"] += qtd_mov

    for (item_id, cor), _qtd in minimos.items():
        if cor:
            cores.setdefault(item_id, {}).setdefault(
                cor, {"fisico": 0.0, "vendas_a_fazer": 0.0, "manutencoes_a_fazer": 0.0}
            )

    linhas = []
    cores_saida: dict[int, list[dict]] = {}
    for item in itens:
        dados = por_item[item.id]
        dados["fisico"] = round(dados["fisico"], 4)
        dados["vendas_a_fazer"] = round(dados["vendas_a_fazer"], 4)
        dados["manutencoes_a_fazer"] = round(dados["manutencoes_a_fazer"], 4)
        dados["reservado"] = 0.0
        dados["disponivel"] = dados["fisico"]

        linhas_cor = []
        if item_controla_cor(item):
            for cor, c in sorted(cores.get(item.id, {}).items(), key=lambda x: x[0]):
                fisico = round(c["fisico"], 4)
                vendas = round(c["vendas_a_fazer"], 4)
                manut = round(c["manutencoes_a_fazer"], 4)
                disponivel = fisico
                minimo = round(float(minimos.get((item.id, cor), 0) or 0), 4)
                comprar = round(max(minimo - disponivel, 0), 4) if cor != ESTOQUE_COR_PENDENTE else 0.0
                linhas_cor.append({
                    "cor": cor, "fisico": fisico, "vendas_a_fazer": vendas, "manutencoes_a_fazer": manut,
                    "reservado": 0.0, "disponivel": disponivel, "minimo": minimo,
                    "comprar": comprar, "custo_compra": round(comprar * float(item.preco_custo or 0), 2),
                })
            minimo_total = round(sum(c["minimo"] for c in linhas_cor if c["cor"] != ESTOQUE_COR_PENDENTE), 4)
            comprar_total = round(sum(c["comprar"] for c in linhas_cor), 4)
        else:
            minimo_total = round(float(minimos.get((item.id, ""), 0) or 0), 4)
            comprar_total = round(max(minimo_total - dados["fisico"], 0), 4)
        dados["minimo"] = minimo_total
        dados["comprar"] = comprar_total
        dados["custo_compra"] = round(comprar_total * float(item.preco_custo or 0), 2)
        linhas.append(dados)
        cores_saida[item.id] = linhas_cor
    return linhas, cores_saida


def _mapa_progresso_contagem(db: Session) -> dict[tuple[int, str], EstoqueContagemProgresso]:
    return {
        (p.item_id, normalizar_cor(p.cor)): p
        for p in db.query(EstoqueContagemProgresso).order_by(EstoqueContagemProgresso.id.asc()).all()
    }


def _salvar_progresso_contagem(
    db: Session, item_id: int, cor: str | None, quantidade: float, minimo: float | None,
    observacao: str | None, usuario_id: int | None,
) -> EstoqueContagemProgresso:
    cor_n = normalizar_cor(cor)
    registro = db.query(EstoqueContagemProgresso).filter(
        EstoqueContagemProgresso.item_id == int(item_id), EstoqueContagemProgresso.cor == cor_n
    ).first()
    if not registro:
        registro = EstoqueContagemProgresso(item_id=int(item_id), cor=cor_n)
        db.add(registro)
    registro.quantidade = max(float(quantidade or 0), 0)
    # 1.2.36: a contagem física não edita o mínimo. Enquanto existir dado legado
    # ainda não migrado, não apague esse valor ao salvar uma contagem parcial.
    if minimo is not None:
        registro.minimo = max(float(minimo or 0), 0)
    registro.observacao = (observacao or '').strip() or None
    registro.usuario_id = usuario_id
    registro.atualizado_em = datetime.now()
    return registro


def _linhas_contagem_estoque(db: Session) -> list[dict]:
    linhas, cores = estoque_saldos(db)
    progresso = _mapa_progresso_contagem(db)
    saida = []
    for l in linhas:
        item = l["item"]
        if item_controla_cor(item):
            por_cor = {c["cor"]: c for c in cores.get(item.id, []) if c["cor"] != ESTOQUE_COR_PENDENTE}
            # Uma cor já contada precisa continuar aparecendo mesmo que seu saldo e mínimo sejam zero.
            for (pid, pcor), prog in progresso.items():
                if pid == item.id and pcor:
                    por_cor.setdefault(pcor, {"cor": pcor, "fisico": _estoque_fisico_chave(db, item.id, pcor), "minimo": _mapa_minimos_estoque(db).get((item.id, pcor), 0)})
            conhecidas = [por_cor[k] for k in sorted(por_cor)]
            if conhecidas:
                for c in conhecidas:
                    prog = progresso.get((item.id, normalizar_cor(c["cor"])))
                    saida.append({
                        "item": item, "cor": c["cor"], "fisico": c["fisico"], "minimo": c["minimo"],
                        "custo_unitario": float(item.preco_custo or 0),
                        "controla_cor": True, "salvo": bool(prog),
                        "contagem_salva": float(prog.quantidade) if prog else None,
                        "observacao_salva": (prog.observacao or "") if prog else "",
                        "atualizado_em": prog.atualizado_em if prog else None,
                    })
            else:
                saida.append({
                    "item": item, "cor": "", "fisico": 0.0, "minimo": 0.0,
                    "custo_unitario": float(item.preco_custo or 0), "controla_cor": True,
                    "salvo": False, "contagem_salva": None, "observacao_salva": "", "atualizado_em": None,
                })
        else:
            prog = progresso.get((item.id, ""))
            saida.append({
                "item": item, "cor": "", "fisico": l["fisico"], "minimo": l["minimo"],
                "custo_unitario": float(item.preco_custo or 0),
                "controla_cor": False, "salvo": bool(prog),
                "contagem_salva": float(prog.quantidade) if prog else None,
                "observacao_salva": (prog.observacao or "") if prog else "",
                "atualizado_em": prog.atualizado_em if prog else None,
            })
    return saida


def _estoque_fisico_chave(db: Session, item_id: int, cor: str | None = None) -> float:
    cor_n = normalizar_cor(cor)
    q = db.query(EstoqueMovimento).filter(EstoqueMovimento.item_id == int(item_id))
    if cor_n:
        q = q.filter(EstoqueMovimento.cor == cor_n)
    else:
        q = q.filter(or_(EstoqueMovimento.cor.is_(None), EstoqueMovimento.cor == ""))
    total = 0.0
    for mov in q.all():
        total += (1 if (mov.tipo or "").upper() == "ENTRADA" else -1) * float(mov.quantidade or 0)
    return round(total, 4)


def relatorio_compras_estoque(db: Session) -> list[dict]:
    linhas, cores = estoque_saldos(db)
    compras = []
    for l in linhas:
        item = l["item"]
        if item_controla_cor(item):
            for c in cores.get(item.id, []):
                if c["cor"] == ESTOQUE_COR_PENDENTE or c["comprar"] <= 0:
                    continue
                compras.append({
                    "item": item, "fornecedor_nome": item.fornecedor.nome if item.fornecedor else "Sem fornecedor",
                    "cor": c["cor"], "fisico": c["fisico"], "vendas_a_fazer": c["vendas_a_fazer"],
                    "manutencoes_a_fazer": c["manutencoes_a_fazer"], "disponivel": c["disponivel"],
                    "minimo": c["minimo"], "comprar": c["comprar"], "custo_unitario": float(item.preco_custo or 0),
                    "custo_total": c["custo_compra"],
                })
        elif l["comprar"] > 0:
            compras.append({
                "item": item, "fornecedor_nome": item.fornecedor.nome if item.fornecedor else "Sem fornecedor",
                "cor": "", "fisico": l["fisico"], "vendas_a_fazer": l["vendas_a_fazer"],
                "manutencoes_a_fazer": l["manutencoes_a_fazer"], "disponivel": l["disponivel"],
                "minimo": l["minimo"], "comprar": l["comprar"], "custo_unitario": float(item.preco_custo or 0),
                "custo_total": l["custo_compra"],
            })
    return sorted(compras, key=lambda x: ((_texto_sem_acento(x["fornecedor_nome"])), (_texto_sem_acento(x["item"].categoria)), (_texto_sem_acento(x["item"].nome)), x["cor"]))


FORNECEDORES_INICIAIS_1226 = [
    "Marcelo - KN",
    "Walter - Canaã",
    "Cleyton - Gabinetes",
    "Mercado livre",
    "Nelio - Entronix",
    "Boa dica",
    "Aliexpress",
    "Magazine Luiza",
    "Shoppe",
    "Frankil Microfones",
]

FORNECEDOR_INICIAL_POR_CATEGORIA_1226 = {
    "ESPELHOS": "Marcelo - KN",
    "FLIPERAMA": "Mercado livre",
    "GABINETES": "Walter - Canaã",
    "GERAL": "Mercado livre",
    "INFO E ELETRONICOS": "Boa dica",
    "SOM": "Mercado livre",
    "BOTOES": "Mercado livre",
    "BOTOES E LEDS": "Mercado livre",
}


def _seed_fornecedores_itens_1226(db: Session) -> tuple[int, int]:
    """Cria fornecedores e vincula itens atuais apenas na implantação inicial.

    Não existe vínculo Categoria -> Fornecedor. Depois da implantação, fornecedor é
    uma propriedade individual de cada Item.
    """
    criados = 0
    fornecedores = {}
    for nome in FORNECEDORES_INICIAIS_1226:
        fornecedor = db.query(Fornecedor).filter(func.lower(Fornecedor.nome) == nome.lower()).first()
        if not fornecedor:
            fornecedor = Fornecedor(nome=nome, ativo=1)
            db.add(fornecedor)
            db.flush()
            criados += 1
        fornecedores[_texto_sem_acento(nome)] = fornecedor

    chave = "fornecedor_item_inicial_1_2_26"
    marcador = db.query(ConfiguracaoSistema).filter(ConfiguracaoSistema.chave == chave).first()
    if marcador and (marcador.valor or "").strip().lower() == "ok":
        return criados, 0

    vinculados = 0
    for item in db.query(Item).filter(Item.fornecedor_id.is_(None)).all():
        nome_fornecedor = FORNECEDOR_INICIAL_POR_CATEGORIA_1226.get(_texto_sem_acento(item.categoria))
        fornecedor = fornecedores.get(_texto_sem_acento(nome_fornecedor)) if nome_fornecedor else None
        if fornecedor:
            item.fornecedor_id = fornecedor.id
            vinculados += 1
    if not marcador:
        marcador = ConfiguracaoSistema(chave=chave)
        db.add(marcador)
    marcador.valor = "ok"
    db.flush()
    return criados, vinculados


def _linhas_minimos_itens(db: Session) -> list[dict]:
    """Estoque mínimo é mantido em Itens, separado da contagem física."""
    linhas, cores = estoque_saldos(db)
    saida = []
    for linha in linhas:
        item = linha["item"]
        if item_controla_cor(item):
            conhecidas = [c for c in cores.get(item.id, []) if c.get("cor") != ESTOQUE_COR_PENDENTE]
            if conhecidas:
                for c in conhecidas:
                    saida.append({"item": item, "cor": c.get("cor") or "", "minimo": float(c.get("minimo") or 0), "controla_cor": True})
            else:
                saida.append({"item": item, "cor": "", "minimo": 0.0, "controla_cor": True})
        else:
            saida.append({"item": item, "cor": "", "minimo": float(linha.get("minimo") or 0), "controla_cor": False})
    return saida


def _migrar_minimos_estoque_legado_1236(db: Session) -> int:
    """Restaura os mínimos definidos na antiga planilha de contagem.

    Até a 1.2.25 o estoque mínimo ficava em ``estoque_contagem_progresso.minimo``.
    Na 1.2.26 ele foi separado da contagem e passou para ``estoque_minimos``, mas
    os valores já existentes não foram copiados. O efeito era a posição mostrar
    MÍNIMO=0 e, por consequência, o relatório de compras não sugerir reposição,
    mesmo com saídas de manutenção e reservas de venda corretamente refletidas
    no disponível.

    A migração é conservadora: só preenche uma chave item/cor que ainda não tem
    registro na tabela nova. Assim, qualquer mínimo já definido na tela de Itens
    continua sendo a fonte oficial e nunca é sobrescrito.
    """
    chave_migracao = "estoque_minimos_legado_contagem_1_2_36"
    marcador = db.query(ConfiguracaoSistema).filter(ConfiguracaoSistema.chave == chave_migracao).first()
    if marcador and (marcador.valor or "").strip().lower() == "ok":
        return 0

    atuais = {
        (int(m.item_id), normalizar_cor(m.cor))
        for m in db.query(EstoqueMinimo).all()
    }

    # Mantém somente o registro legado mais recente de cada item/cor.
    # Isso respeita inclusive um último valor 0/None, que significa não restaurar
    # um mínimo antigo daquela chave.
    legados_por_chave: dict[tuple[int, str], EstoqueContagemProgresso] = {}
    legados = db.query(EstoqueContagemProgresso).order_by(
        EstoqueContagemProgresso.atualizado_em.asc(),
        EstoqueContagemProgresso.id.asc(),
    ).all()
    for legado in legados:
        legados_por_chave[(int(legado.item_id), normalizar_cor(legado.cor))] = legado

    migrados = 0
    for (item_id, cor), legado in legados_por_chave.items():
        if (item_id, cor) in atuais:
            continue
        try:
            minimo = float(legado.minimo) if legado.minimo is not None else 0.0
        except (TypeError, ValueError):
            minimo = 0.0
        if minimo <= 0:
            continue
        item = db.query(Item).filter(Item.id == item_id).first()
        if not item or not item_controla_estoque(item):
            continue
        _salvar_minimo_estoque(db, item_id, cor, minimo)
        atuais.add((item_id, cor))
        migrados += 1

    if not marcador:
        marcador = ConfiguracaoSistema(chave=chave_migracao)
        db.add(marcador)
    marcador.valor = "ok"
    db.flush()
    return migrados


def _migrar_estoque_primeira_implantacao_1166(db: Session) -> int:
    """Zera baixas automáticas anteriores e inicia somente com operações A FAZER reservadas."""
    chave = "estoque_primeira_implantacao_1_1_66"
    marcador = db.query(ConfiguracaoSistema).filter(ConfiguracaoSistema.chave == chave).first()
    if marcador and (marcador.valor or "").strip().lower() == "ok":
        return 0
    removidos = db.query(EstoqueMovimento).filter(
        EstoqueMovimento.tipo == "SAIDA", EstoqueMovimento.origem_tipo.in_(("VENDA", "MANUTENCAO"))
    ).delete(synchronize_session=False)
    db.query(EstoqueReserva).filter(EstoqueReserva.origem_tipo.in_(("VENDA", "MANUTENCAO"))).delete(synchronize_session=False)
    if not marcador:
        marcador = ConfiguracaoSistema(chave=chave)
        db.add(marcador)
    marcador.valor = "ok"
    db.flush()

    vendas_abertas = db.query(Equipamento).filter(
        Equipamento.produto_venda_id.isnot(None), Equipamento.status.in_(tuple(ESTOQUE_VENDA_A_FAZER))
    ).all()
    for venda in vendas_abertas:
        sincronizar_estoque_venda(venda, db)
    manutencoes_abertas_ids = [mid for (mid,) in db.query(Manutencao.id).filter(
        ~Manutencao.status.in_(("Encerrada", "Cancelada", "Cancelado"))
    ).all()]
    for manutencao_id in manutencoes_abertas_ids:
        manutencao = carregar_manutencao(db, manutencao_id)
        if manutencao:
            sincronizar_estoque_manutencao(manutencao, db)
    db.commit()
    return int(removidos or 0)


def _migrar_reservas_manutencao_aprovada_1168(db: Session) -> int:
    """Recria reservas de manutenção usando a regra 1.1.68: somente aprovadas."""
    chave = "estoque_manutencao_somente_aprovada_1_1_68"
    marcador = db.query(ConfiguracaoSistema).filter(ConfiguracaoSistema.chave == chave).first()
    if marcador and (marcador.valor or "").strip().lower() == "ok":
        return 0

    removidas = db.query(EstoqueReserva).filter(
        EstoqueReserva.origem_tipo == "MANUTENCAO"
    ).delete(synchronize_session=False)
    if not marcador:
        marcador = ConfiguracaoSistema(chave=chave)
        db.add(marcador)
    marcador.valor = "ok"
    db.flush()

    manutencoes_abertas_ids = [mid for (mid,) in db.query(Manutencao.id).filter(
        ~Manutencao.status.in_(("Encerrada", "Cancelada", "Cancelado"))
    ).all()]
    for manutencao_id in manutencoes_abertas_ids:
        manutencao = carregar_manutencao(db, manutencao_id)
        if manutencao:
            sincronizar_estoque_manutencao(manutencao, db)
    db.commit()
    return int(removidas or 0)



def _migrar_estoque_manutencao_aprovacao_1233(db: Session) -> tuple[int, int]:
    """Aplica uma vez a regra 1.2.33 às manutenções existentes.

    Corrige inclusive OS antigas já aprovadas manualmente que ficaram somente em reserva
    (caso Jonathan e qualquer outro registro na mesma situação). A rotina usa as funções
    idempotentes de sincronização, portanto não duplica saídas já existentes.
    """
    chave = "estoque_manutencao_aprovacao_baixa_1_2_33"
    marcador = db.query(ConfiguracaoSistema).filter(ConfiguracaoSistema.chave == chave).first()
    if marcador and (marcador.valor or "").strip().lower() == "ok":
        return 0, 0

    manutencao_ids = [mid for (mid,) in db.query(Manutencao.id).all()]
    aprovadas_corrigidas = 0
    pendentes_recalculadas = 0
    for manutencao_id in manutencao_ids:
        manutencao = carregar_manutencao(db, manutencao_id)
        if not manutencao:
            continue
        orcamento = sorted(manutencao.orcamentos, key=lambda x: x.versao)[-1] if manutencao.orcamentos else None
        status = (manutencao.status or "").strip()
        cancelada = status in ESTOQUE_MANUTENCAO_CANCELADA or (orcamento and (orcamento.status or "").strip() == "Cancelado")
        finalizada = status in ESTOQUE_MANUTENCAO_FINAL or bool(manutencao.entregue_em)
        if orcamento and not cancelada and _orcamento_aprovado(orcamento):
            aprovadas_corrigidas += 1
        elif orcamento and not cancelada and not finalizada:
            pendentes_recalculadas += 1
        sincronizar_estoque_manutencao(manutencao, db)

    if not marcador:
        marcador = ConfiguracaoSistema(chave=chave)
        db.add(marcador)
    marcador.valor = "ok"
    db.commit()
    return aprovadas_corrigidas, pendentes_recalculadas


def _migrar_estoque_regra_unica_1235(db: Session) -> tuple[int, int]:
    """Uma única verdade: reserva só em venda; manutenção só sai após aprovação."""
    chave = "estoque_regra_unica_venda_reserva_manut_saida_1_2_35"
    marcador = db.query(ConfiguracaoSistema).filter(ConfiguracaoSistema.chave == chave).first()
    if marcador and (marcador.valor or "").strip().lower() == "ok":
        return 0, 0
    reservas_removidas = db.query(EstoqueReserva).filter(
        EstoqueReserva.origem_tipo == "MANUTENCAO"
    ).delete(synchronize_session=False)
    manutencao_ids = [mid for (mid,) in db.query(Manutencao.id).all()]
    revisadas = 0
    for manutencao_id in manutencao_ids:
        manutencao = carregar_manutencao(db, manutencao_id)
        if not manutencao:
            continue
        sincronizar_estoque_manutencao(manutencao, db)
        revisadas += 1
    if not marcador:
        marcador = ConfiguracaoSistema(chave=chave)
        db.add(marcador)
    marcador.valor = "ok"
    db.flush()
    return int(reservas_removidas or 0), revisadas

def _migrar_estoque_baixa_imediata_1244(db: Session) -> tuple[int, int, int]:
    """Converte a regra antiga de reserva para baixa física única.

    Reservas de venda existentes viram saídas físicas, vendas do fluxo comercial
    são ressincronizadas e manutenções aprovadas são conferidas. No fim não resta
    reserva operacional: todos os relatórios leem somente estoque_movimentos.
    """
    chave = "estoque_baixa_imediata_vendas_extrato_1_2_44"
    marcador = db.query(ConfiguracaoSistema).filter(ConfiguracaoSistema.chave == chave).first()
    if marcador and (marcador.valor or "").strip().lower() == "ok":
        return 0, 0, 0

    reservas_venda = db.query(EstoqueReserva).filter(EstoqueReserva.origem_tipo == "VENDA").all()
    datas_reserva: dict[tuple[int, int, str, int | None], datetime] = {}
    for r in reservas_venda:
        if r.origem_id:
            datas_reserva[(int(r.origem_id), int(r.item_id), r.cor or "", r.origem_item_id or None)] = r.criado_em or datetime.now()

    venda_ids = {int(r.origem_id) for r in reservas_venda if r.origem_id}
    venda_ids.update(int(x) for (x,) in db.query(Equipamento.id).filter(
        Equipamento.produto_venda_id.isnot(None),
        Equipamento.status.in_(("Solicitar gabinete", "Montagem", "Pronto para entrega", "Entregue")),
        Equipamento.descontar_estoque != 0,
    ).all())

    vendas = db.query(Equipamento).filter(Equipamento.id.in_(list(venda_ids) or [-1])).all()
    vendas_revisadas = 0
    for eq in vendas:
        sincronizar_estoque_venda(eq, db)
        db.flush()
        for mov in db.query(EstoqueMovimento).filter(
            EstoqueMovimento.tipo == "SAIDA", EstoqueMovimento.origem_tipo == "VENDA", EstoqueMovimento.origem_id == eq.id
        ).all():
            antiga = datas_reserva.get((eq.id, mov.item_id, mov.cor or "", mov.origem_item_id or None))
            if antiga:
                mov.criado_em = antiga
            elif eq.data_compra and mov.criado_em and mov.criado_em.date() == date.today():
                mov.criado_em = datetime.combine(eq.data_compra, time(hour=12))
        vendas_revisadas += 1

    manut_revisadas = 0
    for mid, in db.query(Manutencao.id).all():
        m = carregar_manutencao(db, int(mid))
        if m:
            sincronizar_estoque_manutencao(m, db)
            manut_revisadas += 1

    reservas_removidas = db.query(EstoqueReserva).delete(synchronize_session=False)
    if not marcador:
        marcador = ConfiguracaoSistema(chave=chave); db.add(marcador)
    marcador.valor = "ok"
    db.flush()
    return int(reservas_removidas or 0), vendas_revisadas, manut_revisadas


def _migrar_itens_e_reservas_1174(db: Session) -> tuple[int, int]:
    """Padroniza categorias/controle de estoque e refaz reservas abertas de venda.

    Não altera saídas físicas históricas. A correção vale apenas para posição futura/aberta.
    """
    chave = "estoque_itens_categorias_e_reservas_1_1_74"
    marcador = db.query(ConfiguracaoSistema).filter(ConfiguracaoSistema.chave == chave).first()
    if marcador and (marcador.valor or "").strip().lower() == "ok":
        return 0, 0
    alterados = 0
    for item in db.query(Item).all():
        categoria_atual = _texto_sem_acento(item.categoria)
        sugerida = _categoria_sugerida_item_1174(item.nome)
        if categoria_atual in {"", "GERAL", "COMPOSICAO DE EQUIPAMENTOS", "OPCIONAIS DE VENDA"}:
            categoria_nova = sugerida or ("Geral" if categoria_atual in {"", "COMPOSICAO DE EQUIPAMENTOS", "OPCIONAIS DE VENDA"} else item.categoria)
            if categoria_nova and item.categoria != categoria_nova:
                item.categoria = categoria_nova
                alterados += 1
        if _texto_sem_acento(item.categoria) in ESTOQUE_CATEGORIAS_SEM_CONTROLE or _texto_sem_acento(item.nome) in ESTOQUE_ITENS_SEM_CONTROLE:
            if int(getattr(item, "controla_estoque", 1) or 0) != 0:
                item.controla_estoque = 0
                alterados += 1
        elif getattr(item, "controla_estoque", None) is None:
            item.controla_estoque = 1
            alterados += 1

    # Recalcula somente reservas de vendas ainda a fazer. Isso corrige vínculos de
    # Opcionais/Recursos alterados sem mexer em equipamentos já entregues.
    vendas = db.query(Equipamento).filter(
        Equipamento.produto_venda_id.isnot(None),
        Equipamento.status.in_(tuple(ESTOQUE_VENDA_A_FAZER)),
    ).all()
    ressincronizadas = 0
    for eq in vendas:
        sincronizar_estoque_venda(eq, db)
        ressincronizadas += 1

    if not marcador:
        marcador = ConfiguracaoSistema(chave=chave)
        db.add(marcador)
    marcador.valor = "ok"
    db.flush()
    return alterados, ressincronizadas



def _migrar_categorias_itens_1175(db: Session) -> int:
    """Cria a nova organização de categorias sem exigir edição item a item."""
    chave = "categorias_itens_planilha_1_1_75"
    marcador = db.query(ConfiguracaoSistema).filter(ConfiguracaoSistema.chave == chave).first()
    if marcador and (marcador.valor or "").strip().lower() == "ok":
        return 0
    alterados = 0
    for item in db.query(Item).all():
        cat = _texto_sem_acento(item.categoria)
        nome = _texto_sem_acento(item.nome)
        nova = None
        if cat == "INFORMATICA":
            nova = "Info e Eletrônicos"
        elif "GABINETE" in nome:
            nova = "Gabinetes"
        elif "ESPELHO" in nome:
            nova = "Espelhos"
        elif "FLIPERAMA" in nome:
            nova = "Fliperama"
        if nova and item.categoria != nova:
            item.categoria = nova
            alterados += 1
    if not marcador:
        marcador = ConfiguracaoSistema(chave=chave)
        db.add(marcador)
    marcador.valor = "ok"
    db.flush()
    return alterados

def contexto_cores_venda(db: Session, equipamento: Equipamento | None) -> list[dict]:
    if not equipamento or not equipamento.id:
        return []
    quantidades = _consumo_venda_itens(equipamento, db)
    if not quantidades:
        return []
    _, cores_saldo = estoque_saldos(db)
    itens = {i.id: i for i in db.query(Item).filter(Item.id.in_(list(quantidades.keys()))).all()}
    saida = []
    for item_id, qtd in quantidades.items():
        item = itens.get(item_id)
        if not item_controla_cor(item):
            continue
        usos = _usos_cor(db, "VENDA", equipamento.id, item_id)
        linhas = [{"cor": u.cor, "quantidade": float(u.quantidade or 0)} for u in usos]
        while len(linhas) < 6:
            linhas.append({"cor": "", "quantidade": ""})
        saida.append({
            "item": item, "quantidade": qtd, "usos": linhas[:12],
            "cores_saldo": [c for c in cores_saldo.get(item_id, []) if c["cor"] != ESTOQUE_COR_PENDENTE],
            "informado": round(sum(float(u.quantidade or 0) for u in usos), 4),
        })
    return saida


def contexto_cores_manutencao(db: Session, orcamento: Orcamento | None) -> dict[int, dict]:
    if not orcamento:
        return {}
    itens_ids = {oi.item_id for oi in orcamento.itens if oi.item_id}
    itens = {i.id: i for i in db.query(Item).filter(Item.id.in_(list(itens_ids))).all()} if itens_ids else {}
    _, cores_saldo = estoque_saldos(db)
    saida = {}
    for oi in orcamento.itens:
        item = itens.get(oi.item_id)
        if not item_controla_cor(item):
            continue
        usos = _usos_cor(db, "MANUTENCAO", oi.id, item.id)
        linhas = [{"cor": u.cor, "quantidade": float(u.quantidade or 0)} for u in usos]
        while len(linhas) < 6:
            linhas.append({"cor": "", "quantidade": ""})
        saida[oi.id] = {
            "item": item, "quantidade": float(oi.quantidade or 0), "usos": linhas[:12],
            "cores_saldo": [c for c in cores_saldo.get(item.id, []) if c["cor"] != ESTOQUE_COR_PENDENTE],
            "informado": round(sum(float(u.quantidade or 0) for u in usos), 4),
        }
    return saida

def limpar_telefone(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def telefone_valido(valor: str, pais: str = "BR", ddi: str = "55") -> bool:
    return telefone_internacional_valido(pais, ddi, valor)


def formatar_telefone(valor: str, pais: str = "BR") -> str:
    return formatar_telefone_internacional(pais, valor)


def localizar_cliente_por_contato(db: Session, pais: str = "BR", ddi: str = "55", telefone: str = "") -> Cliente | None:
    """Localiza cliente pelo WhatsApp normalizado, inclusive cadastros legados formatados."""
    pais_norm, ddi_norm, telefone_norm = normalizar_contato(pais, ddi, telefone)
    if not telefone_norm or not telefone_valido(telefone_norm, pais_norm, ddi_norm):
        return None

    # Caminho rápido para os cadastros já normalizados.
    cliente = db.query(Cliente).filter(
        Cliente.ddi == ddi_norm,
        Cliente.telefone == telefone_norm,
    ).first()
    if cliente:
        return cliente

    # Compatibilidade com registros antigos que podem conter máscara ou DDI
    # gravado dentro do campo telefone.
    for candidato in db.query(Cliente).all():
        c_pais, c_ddi, c_telefone = normalizar_contato(
            getattr(candidato, "pais", None) or pais_norm,
            getattr(candidato, "ddi", None) or ddi_norm,
            getattr(candidato, "telefone", "") or "",
        )
        if c_ddi == ddi_norm and c_telefone == telefone_norm:
            return candidato
    return None


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
    """Retorna a única empresa Humiat do cliente.

    APP 1.1.96: cliente externo não troca de empresa no Humiat ID. A empresa é
    inferida pelos equipamentos já vinculados no Organiza. Se ainda não houver
    empresa identificável, Karaokê RJ é usada somente como padrão inicial.

    ``incluir_karaokerj`` é mantido na assinatura por compatibilidade com chamadas
    antigas, mas não cria mais uma segunda empresa para o mesmo usuário.
    """
    encontradas: list[tuple[object, str]] = []
    vistos: set[str] = set()
    for grupo in _solvoz_grupos_cliente(cliente):
        origem = grupo.get("empresa")
        slug = str(getattr(origem, "slug", "") or "").strip().lower()
        if not slug or slug in vistos:
            continue
        vistos.add(slug)
        encontradas.append((origem, slug))

    # Em dados legados pode existir Karaokê RJ junto com uma única empresa real
    # por causa do fallback antigo. Nesse caso a empresa específica prevalece.
    if len(encontradas) > 1:
        nao_padrao = [item for item in encontradas if item[1] != "karaokerj"]
        if len(nao_padrao) == 1:
            encontradas = nao_padrao
        else:
            # Regra de segurança: o Humiat ID aceita apenas uma empresa por cliente.
            # Mantemos uma escolha determinística e registramos a inconsistência.
            encontradas = sorted(encontradas, key=lambda item: item[1])[:1]
            print(
                f"[HUMIAT ID] 1.1.96: cliente {getattr(cliente, 'id', '?')} possui "
                f"equipamentos em mais de uma empresa; usando {encontradas[0][1]}."
            )

    if encontradas:
        origem, slug = encontradas[0]
        try:
            empresa = garantir_empresa_solvoz_humiat(
                db,
                str(getattr(origem, "nome", "") or slug).strip(),
                slug,
                ativo=int(getattr(origem, "ativo", 1) or 0),
            )
            return [empresa]
        except Exception:
            pass

    try:
        return [garantir_empresa_solvoz_humiat(db, "Karaokê RJ", "karaokerj", ativo=1)]
    except Exception:
        return []


def _humiat_sincronizar_empresa_unica_cliente(cliente: Cliente, db: Session) -> int:
    """Sincroniza o usuário externo para exatamente uma empresa.

    Remove vínculos antigos (inclusive o fallback Karaokê RJ) e habilita, na
    empresa correta, todos os produtos que já estão liberados para o usuário.
    """
    usuario = _humiat_usuario_do_cliente(cliente, db)
    if not usuario:
        return 0
    # Não confundir cliente externo recém-criado (ainda sem empresa) com equipe
    # interna: a regra antiga de "sem empresa = interno" não pode bloquear o
    # primeiro vínculo do cliente.
    interno_real = bool(usuario_humiat_equipe_prioritaria(usuario) or (
        (usuario.organiza_usuario or "").strip() and usuario_humiat_interno(db, usuario)
    ))
    if interno_real:
        return 0
    alvos = _humiat_empresas_alvo_cliente(cliente, db)
    if not alvos:
        return 0
    empresa = alvos[0]
    alteracoes = 0

    vinculos = db.query(HumiatUsuarioEmpresa).filter(
        HumiatUsuarioEmpresa.usuario_id == int(usuario.id)
    ).all()
    manteve = False
    for vinculo in vinculos:
        if int(vinculo.empresa_id) == int(empresa.id) and not manteve:
            manteve = True
            continue
        db.delete(vinculo)
        alteracoes += 1
    if not manteve:
        db.add(HumiatUsuarioEmpresa(usuario_id=int(usuario.id), empresa_id=int(empresa.id)))
        alteracoes += 1

    produtos = db.query(HumiatProduto).filter(HumiatProduto.ativo == 1).all()
    if produtos:
        produto_ids = [int(p.id) for p in produtos]
        acessos_usuario = db.query(HumiatUsuarioProduto).filter(
            HumiatUsuarioProduto.usuario_id == int(usuario.id),
            HumiatUsuarioProduto.produto_id.in_(produto_ids),
        ).all()
        liberados = {
            int(item.produto_id) for item in acessos_usuario
            if bool(item.acesso_sistema) or bool(item.acesso_adm)
            or bool(getattr(item, "acesso_solvoz_comprado", 0))
            or bool(getattr(item, "acesso_solvoz_catalogo", 0))
        }
        existentes = db.query(HumiatEmpresaProduto).filter(
            HumiatEmpresaProduto.empresa_id == int(empresa.id),
            HumiatEmpresaProduto.produto_id.in_(produto_ids),
        ).all()
        por_produto = {int(item.produto_id): item for item in existentes}
        for produto_id in liberados:
            item = por_produto.get(produto_id)
            if item:
                if not int(item.ativo or 0):
                    item.ativo = 1
                    alteracoes += 1
            else:
                db.add(HumiatEmpresaProduto(
                    empresa_id=int(empresa.id), produto_id=produto_id, ativo=1
                ))
                alteracoes += 1
    return alteracoes


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

    cliente.humiat_usuario_id = int(usuario.id)
    db.flush()
    # A sincronização de empresa/produtos é feita uma única vez no final do
    # salvamento dos acessos, depois de aplicar as rotinas padrão do cliente.
    return usuario, criado, interno


def _humiat_contexto_cliente(cliente: Cliente, db: Session) -> dict:
    """Monta os acessos do cliente em lote, sem N+1 por produto."""
    usuario = _humiat_usuario_do_cliente(cliente, db)
    produtos = db.query(HumiatProduto).filter(HumiatProduto.ativo == 1).order_by(HumiatProduto.nome).all()
    padrao = {"sistema": False, "adm": False, "solvoz_comprado": False, "solvoz_catalogo": False}
    acessos = {p.codigo: dict(padrao) for p in produtos}
    if usuario and produtos:
        itens = db.query(HumiatUsuarioProduto).filter(
            HumiatUsuarioProduto.usuario_id == int(usuario.id),
            HumiatUsuarioProduto.produto_id.in_([int(p.id) for p in produtos]),
        ).all()
        por_produto = {int(item.produto_id): item for item in itens}
        for produto in produtos:
            item = por_produto.get(int(produto.id))
            if not item:
                continue
            acessos[produto.codigo] = {
                "sistema": bool(item.acesso_sistema),
                "adm": bool(item.acesso_adm),
                "solvoz_comprado": bool(getattr(item, "acesso_solvoz_comprado", 0)),
                "solvoz_catalogo": bool(getattr(item, "acesso_solvoz_catalogo", 0)),
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
    existentes = db.query(HumiatUsuarioProduto).filter(
        HumiatUsuarioProduto.usuario_id == int(usuario.id),
        HumiatUsuarioProduto.produto_id.in_([int(p.id) for p in produtos]) if produtos else False,
    ).all() if produtos else []
    por_produto = {int(item.produto_id): item for item in existentes}
    for produto in produtos:
        item = por_produto.get(int(produto.id))
        if not item:
            item = HumiatUsuarioProduto(usuario_id=int(usuario.id), produto_id=int(produto.id))
            db.add(item)
            por_produto[int(produto.id)] = item
        codigo = (produto.codigo or "").upper()
        if codigo == "SOLVOZ":
            cliente_site = str(form.get(f"produto_{produto.id}_solvoz_site") or "0") == "1"
            cliente_catalogo = str(form.get(f"produto_{produto.id}_solvoz_catalogo") or "0") == "1"
            adm = str(form.get(f"produto_{produto.id}_adm") or "0") == "1" if interno else False
            item.acesso_sistema = 1 if (cliente_site or cliente_catalogo) else 0
            item.acesso_adm = 1 if adm else 0
            item.acesso_solvoz_comprado = 1 if cliente_site else 0
            item.acesso_solvoz_catalogo = 1 if cliente_catalogo else 0
        else:
            sistema = str(form.get(f"produto_{produto.id}_sistema") or "0") == "1"
            adm = str(form.get(f"produto_{produto.id}_adm") or "0") == "1"
            item.acesso_sistema = 1 if sistema else 0
            item.acesso_adm = 1 if adm else 0
            item.acesso_solvoz_comprado = 0
            item.acesso_solvoz_catalogo = 0

    if not interno:
        # Regras padrão do cliente Humiat: Organiza Tarefas rápidas e LokaFest sempre ativos;
        # SolVoz é reconhecido automaticamente pelo vínculo já existente no Organiza.
        aplicar_rotinas_cliente_humiat(db, usuario, int(cliente.id), garantir_lokafest=True)

        # APP 1.1.96: um cliente externo possui uma única empresa no Humiat ID.
        # A sincronização também remove vínculos legados e habilita os produtos
        # já liberados no cadastro para essa empresa.
        _humiat_sincronizar_empresa_unica_cliente(cliente, db)

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


PLUS_ACRESCIMO = 400.0
VENDA_STATUS_FINALIZADO = {"ENTREGUE", "VENDIDO"}


def _pascoa(ano: int) -> date:
    """Data da Páscoa pelo algoritmo gregoriano, sem dependência externa."""
    a = ano % 19; b = ano // 100; c = ano % 100; d = b // 4; e = b % 4
    f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def feriados_producao(ano: int) -> set[date]:
    """Calendário usado para impedir entrega final em feriado.

    A contagem continua corrida; somente a data final é empurrada ao próximo
    dia útil. Inclui feriados nacionais e os feriados usuais do Rio de Janeiro.
    """
    fixos = {
        (1, 1),   # Confraternização Universal
        (1, 20),  # São Sebastião - Rio de Janeiro
        (4, 21),  # Tiradentes
        (4, 23),  # São Jorge - RJ
        (5, 1),   # Trabalho
        (9, 7),   # Independência
        (10, 12), # N. Sra. Aparecida
        (11, 2),  # Finados
        (11, 15), # República
        (11, 20), # Consciência Negra
        (12, 25), # Natal
    }
    feriados = {date(ano, m, d) for m, d in fixos}
    pascoa = _pascoa(ano)
    # Dias em que a operação normalmente não faz entrega no RJ.
    feriados.update({
        pascoa - timedelta(days=48), # segunda de Carnaval
        pascoa - timedelta(days=47), # terça de Carnaval
        pascoa - timedelta(days=2),  # Sexta-feira Santa
        pascoa + timedelta(days=60), # Corpus Christi
    })
    return feriados


def ajustar_entrega_para_dia_util(data_prevista: date | None) -> date | None:
    if not data_prevista:
        return None
    data_final = data_prevista
    while data_final.weekday() >= 5 or data_final in feriados_producao(data_final.year):
        data_final += timedelta(days=1)
    return data_final


def calcular_previsao_venda(data_compra: date | None, prazo_dias: int | None) -> date | None:
    if not data_compra:
        return None
    try:
        prazo = max(int(prazo_dias or 0), 0)
    except (TypeError, ValueError):
        prazo = 0
    # Compra no dia X: o primeiro dia contado é X+1. Em dias corridos, X+prazo
    # representa exatamente o último dia da contagem.
    prevista = data_compra + timedelta(days=prazo)
    return ajustar_entrega_para_dia_util(prevista)


def _prazo_venda_equipamento(eq: Equipamento | None) -> int:
    if not eq or not eq.produto_venda:
        return 20
    try:
        return max(int(eq.produto_venda.prazo_producao_dias or 20), 0)
    except (TypeError, ValueError):
        return 20


def atualizar_datas_producao_venda(eq: Equipamento, data_compra: date | None = None) -> None:
    if data_compra and not eq.data_compra:
        eq.data_compra = data_compra
    if eq.data_compra:
        eq.previsao_entrega = calcular_previsao_venda(eq.data_compra, _prazo_venda_equipamento(eq))


def _valor_venda_opcional_config(config: VendaOpcionalConfig | None) -> float:
    if not config or not config.item:
        return 0.0
    return max(float(config.item.preco_venda or 0), 0) * max(float(config.quantidade or 0), 0)


def valor_opcionais_venda_equipamento(eq: Equipamento, db: Session) -> float:
    """Valor comercial extra dos opcionais em relação ao padrão de cada categoria."""
    if not eq or not eq.produto_venda_id:
        return 0.0
    total = 0.0
    campos = [x[0] for x in db.query(VendaOpcionalConfig.campo).filter(VendaOpcionalConfig.ativo == 1).distinct().all()]
    for campo in campos:
        if not _opcional_habilitado_modelo(db, eq.produto_venda_id, campo):
            continue
        atual = _opcional_config_por_escolha(db, campo, _valor_opcional_equipamento(eq, campo, db))
        padrao = db.query(VendaOpcionalConfig).options(selectinload(VendaOpcionalConfig.item)).filter(
            VendaOpcionalConfig.campo == campo, VendaOpcionalConfig.ativo == 1, VendaOpcionalConfig.padrao == 1
        ).order_by(VendaOpcionalConfig.ordem).first()
        atual_v = _valor_venda_opcional_config(atual)
        padrao_v = _valor_venda_opcional_config(padrao)
        total += max(atual_v - padrao_v, 0.0)
    return round(total, 2)


def _pagamentos_venda_totais(db: Session, equipamento_id: int) -> tuple[float, float, float]:
    eq = db.query(Equipamento).filter(Equipamento.id == int(equipamento_id)).first()
    total = max(float(moeda_num(eq.valor)) if eq else 0.0, 0.0)
    recebido = round(sum(float(p.valor or 0) for p in db.query(PagamentoVenda).filter(PagamentoVenda.equipamento_id == int(equipamento_id)).all()), 2)
    saldo = max(round(total - recebido, 2), 0.0)
    return round(total, 2), recebido, saldo


def normalizar_nome_item(nome: str | None) -> str:
    return re.sub(r"\s+", " ", (nome or "").strip()).upper()


def calcular_desconto_cupom(cupom: VendaCupom | None, preco_bruto: float) -> float:
    preco = max(float(preco_bruto or 0), 0)
    if not cupom or not cupom.ativo or preco <= 0:
        return 0.0
    valor = max(float(cupom.valor or 0), 0)
    if (cupom.tipo or "").upper() == "PERCENTUAL":
        desconto = preco * min(valor, 100.0) / 100.0
    else:
        desconto = valor
    return round(min(desconto, preco), 2)


def _garantir_item_venda(db: Session, nome: str, categoria: str = "Composição de equipamentos") -> Item:
    nome = normalizar_nome_item(nome)
    item = db.query(Item).filter(Item.nome == nome).first()
    if not item:
        item = db.query(Item).filter(func.lower(Item.nome) == nome.lower()).first()
    if item:
        return item
    item = Item(nome=nome, categoria=categoria, preco_custo=0, preco_venda=0, ativo=1)
    db.add(item)
    db.flush()
    return item


def _seed_modelos_e_opcionais_venda(db: Session):
    """Cria a estrutura inicial sem importar nenhum preço da planilha.

    A planilha é usada apenas para quantidade/composição. Custos sempre vêm de
    catalogo_itens. Itens inexistentes entram com custo zero para cadastro posterior.
    """
    caminho = os.path.join(os.path.dirname(__file__), "equipamentos_venda_seed.json")
    if os.path.exists(caminho):
        with open(caminho, "r", encoding="utf-8") as arquivo:
            produtos = json.load(arquivo)
        for dado in produtos:
            modelo = None
            if dado.get("sku"):
                modelo = db.query(VendaModeloEquipamento).filter(VendaModeloEquipamento.sku == dado["sku"]).first()
            if not modelo and dado.get("solvoz_slug"):
                modelo = db.query(VendaModeloEquipamento).filter(VendaModeloEquipamento.solvoz_slug == dado["solvoz_slug"]).first()
            novo = modelo is None
            if novo:
                modelo = VendaModeloEquipamento(
                    nome=dado["nome"], sku=dado.get("sku"), tipo=dado.get("tipo") or "JUKEBOX",
                    solvoz_slug=dado.get("solvoz_slug"), preco_basico=float(dado.get("preco_basico") or 0),
                    ativo=1, ordem=int(dado.get("ordem") or 0), observacao=dado.get("observacao"),
                )
                db.add(modelo)
                db.flush()
            # A composição automática é aplicada somente quando o produto nasce.
            # Depois, o cadastro do Organiza é a fonte e não é sobrescrito em deploys.
            if novo:
                for linha in dado.get("composicao") or []:
                    nome_item = (linha.get("item") or "").strip()
                    qtd = float(linha.get("quantidade") or 0)
                    if not nome_item or qtd <= 0:
                        continue
                    # Regra consolidada: qualquer Stereo vira Mono.
                    if nome_item.upper() == "AMPLIFICADOR STEREO":
                        nome_item = "AMPLIFICADOR MONO C/ BLUETOOTH"
                    item = _garantir_item_venda(db, nome_item)
                    db.add(VendaModeloComposicao(modelo_id=modelo.id, item_id=item.id, quantidade=qtd))

    opcionais = [
        ("hdmi_tela_2", "HDMI", "NA", "NA", None, 0, 10, 1),
        ("hdmi_tela_2", "HDMI", "Extensor", "Extensor", "EXTENSOR HDMI", 1, 20, 0),
        ("hdmi_tela_2", "HDMI", "Divisor", "Divisor", "DIVISOR HDMI", 1, 30, 0),
        ("teclado_bluetooth", "Teclado Bluetooth", "NA", "NA", None, 0, 40, 1),
        ("teclado_bluetooth", "Teclado Bluetooth", "Sim", "Com Teclado Bluetooth", "TECLADO BLUETOOTH", 1, 50, 0),
        # Com fio é padrão e já está na composição do equipamento. A quantidade 2 serve como referência para a troca.
        ("microfone", "Microfone", "Com fio", "Com fio", "MICROFONE", 2, 60, 1),
        ("microfone", "Microfone", "Sem fio", "Sem fio", "MICROFONE SEM FIO", 1, 70, 0),
        ("sistema_credito", "Sistema de Crédito", "NA", "NA", None, 0, 80, 1),
        ("sistema_credito", "Sistema de Crédito", "Moedeiro", "Moedeiro", "MOEDEIRO", 1, 90, 0),
        ("sistema_credito", "Sistema de Crédito", "Ficheiro", "Ficheiro", "FICHEIRO", 1, 100, 0),
        ("sistema_credito", "Sistema de Crédito", "Teclado", "Teclado", "TECLADO SISTEMA DE CREDITO", 1, 110, 0),
        ("catalogo_impresso", "Encadernado / Pasta Plástica", "NA", "NA", None, 0, 120, 1),
        ("catalogo_impresso", "Encadernado / Pasta Plástica", "Encadernado", "Encadernado", "CATALOGO ENCARDENADO", 1, 130, 0),
        ("catalogo_impresso", "Encadernado / Pasta Plástica", "Pasta", "Pasta Plástica", "PASTA PLASTICA", 1, 140, 0),
        ("pilhas", "Pilhas", "NA", "NA", None, 0, 150, 1),
        ("pilhas", "Pilhas", "Recarregaveis", "Recarregáveis", "PILHAS RECARREGAVEIS", 1, 160, 0),
        ("estabilizador", "Estabilizador", "NA", "NA", None, 0, 170, 1),
        ("estabilizador", "Estabilizador", "TS Shara 9101", "TS Shara 9101", "TS SHARA 9101", 1, 180, 0),
        ("estabilizador", "Estabilizador", "TS Shara 9116", "TS Shara 9116", "TS SHARA 9116", 1, 190, 0),
    ]
    for campo, grupo, valor, rotulo, item_nome, quantidade, ordem, padrao in opcionais:
        existente = db.query(VendaOpcionalConfig).filter(
            VendaOpcionalConfig.campo == campo, VendaOpcionalConfig.valor == valor
        ).first()
        item = _garantir_item_venda(db, item_nome, "Opcionais de venda") if item_nome else None
        if existente:
            # Atualiza nomenclaturas/regras desta versão sem apagar custo cadastrado no Item.
            existente.grupo = grupo
            existente.rotulo = rotulo
            existente.quantidade = float(quantidade or 0)
            existente.ordem = ordem
            existente.padrao = padrao
            if item and not existente.item_id:
                existente.item_id = item.id
            continue
        db.add(VendaOpcionalConfig(
            campo=campo, grupo=grupo, valor=valor, rotulo=rotulo,
            item_id=item.id if item else None, quantidade=float(quantidade or 0), ordem=ordem, ativo=1, padrao=padrao,
        ))

    # Migração dos nomes antigos para as novas escolhas.
    antigo_microfone_na = db.query(VendaOpcionalConfig).filter(VendaOpcionalConfig.campo == "microfone", VendaOpcionalConfig.valor == "NA").first()
    if antigo_microfone_na:
        antigo_microfone_na.ativo = 0
    antigo_hdmi = db.query(VendaOpcionalConfig).filter(VendaOpcionalConfig.campo == "hdmi_tela_2", VendaOpcionalConfig.valor == "Sim").first()
    if antigo_hdmi:
        antigo_hdmi.ativo = 0
    antigo_catalogo = db.query(VendaOpcionalConfig).filter(VendaOpcionalConfig.campo == "catalogo_impresso", VendaOpcionalConfig.valor == "Sim").first()
    if antigo_catalogo:
        antigo_catalogo.ativo = 0
    db.query(Equipamento).filter(Equipamento.hdmi_tela_2 == "Sim").update({Equipamento.hdmi_tela_2: "Extensor"}, synchronize_session=False)
    db.query(Equipamento).filter(Equipamento.catalogo_impresso == "Sim").update({Equipamento.catalogo_impresso: "Encadernado"}, synchronize_session=False)

    # A composição oficial de todos os equipamentos comerciais inclui 2 microfones com fio.
    # O opcional Microfone apenas mantém essa composição ou a substitui pelo kit sem fio.
    mic_item = _garantir_item_venda(db, "MICROFONE", "Som")
    campos = sorted({x[0] for x in opcionais})
    modelos = db.query(VendaModeloEquipamento).all()
    for modelo in modelos:
        comp_mic = db.query(VendaModeloComposicao).filter(VendaModeloComposicao.modelo_id == modelo.id, VendaModeloComposicao.item_id == mic_item.id).first()
        if comp_mic:
            comp_mic.quantidade = 2
        else:
            db.add(VendaModeloComposicao(modelo_id=modelo.id, item_id=mic_item.id, quantidade=2))
        for campo in campos:
            regra = db.query(VendaOpcionalModelo).filter(VendaOpcionalModelo.modelo_id == modelo.id, VendaOpcionalModelo.campo == campo).first()
            nome_normalizado = unicodedata.normalize("NFKD", f"{modelo.nome or ''} {modelo.tipo or ''}").encode("ascii", "ignore").decode("ascii").upper()
            portatil_sem_hdmi = campo == "hdmi_tela_2" and "PORTAT" in nome_normalizado
            if not regra:
                db.add(VendaOpcionalModelo(modelo_id=modelo.id, campo=campo, habilitado=0 if portatil_sem_hdmi else 1))
    # Aplicação única da regra inicial do Portátil; depois disso a matriz fica totalmente editável pelo usuário.
    chave_portatil = "opcionais_portatil_hdmi_1_1_81"
    marcador_portatil = db.query(ConfiguracaoSistema).filter(ConfiguracaoSistema.chave == chave_portatil).first()
    if not marcador_portatil:
        for modelo in modelos:
            nome_normalizado = unicodedata.normalize("NFKD", f"{modelo.nome or ''} {modelo.tipo or ''}").encode("ascii", "ignore").decode("ascii").upper()
            if "PORTAT" in nome_normalizado:
                regra = db.query(VendaOpcionalModelo).filter(VendaOpcionalModelo.modelo_id == modelo.id, VendaOpcionalModelo.campo == "hdmi_tela_2").first()
                if regra:
                    regra.habilitado = 0
        db.add(ConfiguracaoSistema(chave=chave_portatil, valor="ok"))
    db.commit()


def _migrar_composicoes_venda_1163(db: Session) -> int:
    """Corrige uma única vez as composições definidas após a 1.1.62.

    - Guitarrinha 19 usa a base da Jukebox/Bipartido 17, trocando gabinete e monitor.
    - As colunas GUITARRA 19 da planilha correspondem à Guitarra 22, com monitor 22.
    A marca de migração evita sobrescrever ajustes manuais feitos depois pelo usuário.
    """
    chave = "venda_composicoes_1_1_63"
    marcador = db.query(ConfiguracaoSistema).filter(ConfiguracaoSistema.chave == chave).first()
    if marcador and (marcador.valor or "").strip().lower() == "ok":
        return 0

    caminho = os.path.join(os.path.dirname(__file__), "equipamentos_venda_seed.json")
    if not os.path.exists(caminho):
        return 0
    with open(caminho, "r", encoding="utf-8") as arquivo:
        produtos = json.load(arquivo)

    alvos = {"Guitarrinha 19 Premium", "Guitarrinha 19 JBL", "Guitarra 22 Premium", "Guitarra 22 JBL"}
    corrigidos = 0
    for dado in produtos:
        if dado.get("nome") not in alvos:
            continue
        modelo = None
        if dado.get("sku"):
            modelo = db.query(VendaModeloEquipamento).filter(VendaModeloEquipamento.sku == dado["sku"]).first()
        if not modelo and dado.get("solvoz_slug"):
            modelo = db.query(VendaModeloEquipamento).filter(VendaModeloEquipamento.solvoz_slug == dado["solvoz_slug"]).first()
        if not modelo:
            modelo = db.query(VendaModeloEquipamento).filter(VendaModeloEquipamento.nome == dado["nome"]).first()
        if not modelo:
            continue

        db.query(VendaModeloComposicao).filter(VendaModeloComposicao.modelo_id == modelo.id).delete(synchronize_session=False)
        for linha in dado.get("composicao") or []:
            nome_item = (linha.get("item") or "").strip()
            qtd = float(linha.get("quantidade") or 0)
            if not nome_item or qtd <= 0:
                continue
            if nome_item.upper() == "AMPLIFICADOR STEREO":
                nome_item = "AMPLIFICADOR MONO C/ BLUETOOTH"
            item = _garantir_item_venda(db, nome_item)
            db.add(VendaModeloComposicao(modelo_id=modelo.id, item_id=item.id, quantidade=qtd))
        modelo.observacao = dado.get("observacao")
        corrigidos += 1

    if not marcador:
        marcador = ConfiguracaoSistema(chave=chave)
        db.add(marcador)
    marcador.valor = "ok"
    db.commit()
    return corrigidos


def _migrar_itens_maiusculos_1164(db: Session) -> int:
    """Normaliza os nomes dos Itens para MAIÚSCULAS uma única vez."""
    chave = "itens_maiusculos_1_1_64"
    marcador = db.query(ConfiguracaoSistema).filter(ConfiguracaoSistema.chave == chave).first()
    if marcador and (marcador.valor or "").strip().lower() == "ok":
        return 0
    alterados = 0
    for item in db.query(Item).order_by(Item.id.asc()).all():
        novo = normalizar_nome_item(item.nome)
        if novo and novo != item.nome:
            repetido = db.query(Item).filter(func.upper(Item.nome) == novo, Item.id != item.id).first()
            if not repetido:
                item.nome = novo
                alterados += 1
    if not marcador:
        marcador = ConfiguracaoSistema(chave=chave)
        db.add(marcador)
    marcador.valor = "ok"
    db.commit()
    return alterados


def _migrar_espelho_teclado_1164(db: Session) -> int:
    """Adiciona 1 ESPELHO DE TECLADO a todos os equipamentos comerciais."""
    chave = "espelho_teclado_composicoes_1_1_64"
    marcador = db.query(ConfiguracaoSistema).filter(ConfiguracaoSistema.chave == chave).first()
    item = _garantir_item_venda(db, "ESPELHO DE TECLADO", "Composição de equipamentos")
    item.preco_custo = 20.0
    item.preco_venda = 50.0
    item.ativo = 1
    # Mesmo com a migração marcada, reforçamos a regra fixa em todos os modelos.
    # Isso também cobre equipamentos comerciais criados em versões futuras.
    adicionados = 0
    for modelo in db.query(VendaModeloEquipamento).all():
        comp = db.query(VendaModeloComposicao).filter(
            VendaModeloComposicao.modelo_id == modelo.id,
            VendaModeloComposicao.item_id == item.id,
        ).first()
        if comp:
            comp.quantidade = 1
        else:
            db.add(VendaModeloComposicao(modelo_id=modelo.id, item_id=item.id, quantidade=1))
            adicionados += 1
    if not marcador:
        marcador = ConfiguracaoSistema(chave=chave)
        db.add(marcador)
    marcador.valor = "ok"
    db.commit()
    return adicionados


def custo_base_modelo(db: Session, modelo_id: int | None) -> float:
    if not modelo_id:
        return 0.0
    linhas = (
        db.query(VendaModeloComposicao)
        .options(selectinload(VendaModeloComposicao.item))
        .filter(VendaModeloComposicao.modelo_id == modelo_id)
        .all()
    )
    return round(sum(float(linha.quantidade or 0) * float(linha.item.preco_custo or 0) for linha in linhas if linha.item), 2)


def _opcional_config_por_escolha(db: Session, campo: str, valor: str | None):
    valor = (valor or "NA").strip()
    return db.query(VendaOpcionalConfig).options(selectinload(VendaOpcionalConfig.item)).filter(
        VendaOpcionalConfig.campo == campo,
        VendaOpcionalConfig.valor == valor,
        VendaOpcionalConfig.ativo == 1,
    ).first()


def _opcionais_dinamicos(eq: Equipamento | None) -> dict[str, str]:
    if not eq or not (getattr(eq, "opcionais_json", None) or "").strip():
        return {}
    try:
        dados = json.loads(eq.opcionais_json or "{}")
        return {str(k): str(v) for k, v in (dados or {}).items()}
    except Exception:
        return {}


def _valor_opcional_equipamento(eq: Equipamento, campo: str, db: Session) -> str:
    if hasattr(eq, campo):
        valor = str(getattr(eq, campo, "") or "").strip()
        if valor:
            return valor
    dados = _opcionais_dinamicos(eq)
    if campo in dados:
        return dados[campo]
    padrao = db.query(VendaOpcionalConfig).filter(VendaOpcionalConfig.campo == campo, VendaOpcionalConfig.ativo == 1, VendaOpcionalConfig.padrao == 1).order_by(VendaOpcionalConfig.ordem).first()
    return padrao.valor if padrao else "NA"


def _opcional_habilitado_modelo(db: Session, modelo_id: int | None, campo: str) -> bool:
    if not modelo_id:
        return True
    regra = db.query(VendaOpcionalModelo).filter(VendaOpcionalModelo.modelo_id == modelo_id, VendaOpcionalModelo.campo == campo).first()
    return True if regra is None else bool(regra.habilitado)


def _custo_config(config: VendaOpcionalConfig | None) -> float:
    return float(config.quantidade or 0) * float(config.item.preco_custo or 0) if config and config.item else 0.0


def custo_opcionais_equipamento(eq: Equipamento, db: Session) -> float:
    if not eq.produto_venda_id:
        return 0.0
    total = 0.0
    campos = [x[0] for x in db.query(VendaOpcionalConfig.campo).filter(VendaOpcionalConfig.ativo == 1).distinct().all()]
    for campo in campos:
        if not _opcional_habilitado_modelo(db, eq.produto_venda_id, campo):
            continue
        valor = _valor_opcional_equipamento(eq, campo, db)
        config = _opcional_config_por_escolha(db, campo, valor)
        if campo == "microfone":
            # Com fio já está no custo-base. Sem fio substitui 2 com fio: cobra apenas a diferença.
            if valor == "Sem fio":
                padrao = _opcional_config_por_escolha(db, "microfone", "Com fio")
                total += _custo_config(config) - _custo_config(padrao)
            continue
        total += _custo_config(config)
    return round(total, 2)


def _contexto_custos_vendas_em_lote(db: Session, equipamentos: list[Equipamento]) -> dict:
    """Pré-carrega custos/opcionais de todas as vendas em poucas consultas.

    Evita o N+1 histórico da tela de Vendas, onde cada equipamento consultava
    novamente composição, opcionais e regras por modelo.
    """
    pendentes = [eq for eq in equipamentos if eq.custo_final_snapshot is None and eq.produto_venda_id]
    modelo_ids = sorted({int(eq.produto_venda_id) for eq in pendentes if eq.produto_venda_id})

    bases = {mid: 0.0 for mid in modelo_ids}
    if modelo_ids:
        linhas = (
            db.query(VendaModeloComposicao)
            .options(selectinload(VendaModeloComposicao.item))
            .filter(VendaModeloComposicao.modelo_id.in_(modelo_ids))
            .all()
        )
        for linha in linhas:
            if linha.item:
                bases[int(linha.modelo_id)] = bases.get(int(linha.modelo_id), 0.0) + (
                    float(linha.quantidade or 0) * float(linha.item.preco_custo or 0)
                )

    configs = (
        db.query(VendaOpcionalConfig)
        .options(selectinload(VendaOpcionalConfig.item))
        .filter(VendaOpcionalConfig.ativo == 1)
        .order_by(VendaOpcionalConfig.ordem)
        .all()
    )
    por_escolha = {(c.campo, c.valor): c for c in configs}
    padroes = {}
    campos = []
    for c in configs:
        if c.campo not in campos:
            campos.append(c.campo)
        if c.padrao and c.campo not in padroes:
            padroes[c.campo] = c.valor

    regras = {}
    if modelo_ids:
        for r in db.query(VendaOpcionalModelo).filter(VendaOpcionalModelo.modelo_id.in_(modelo_ids)).all():
            regras[(int(r.modelo_id), r.campo)] = bool(r.habilitado)

    return {
        "bases": {k: round(v, 2) for k, v in bases.items()},
        "configs": por_escolha,
        "padroes": padroes,
        "campos": campos,
        "regras": regras,
    }


def _resumo_custo_venda_em_lote(eq: Equipamento, contexto: dict) -> dict:
    """Mesmo cálculo de resumo_custo_venda, usando somente dados pré-carregados."""
    if eq.custo_final_snapshot is not None:
        base = float(eq.custo_base_snapshot or 0)
        opcionais = float(eq.custo_opcionais_snapshot or 0)
        custo = float(eq.custo_final_snapshot or 0)
        preco = float(eq.preco_venda_snapshot if eq.preco_venda_snapshot is not None else max(moeda_num(eq.valor) - float(eq.frete_venda or 0), 0))
        lucro = float(eq.lucro_snapshot if eq.lucro_snapshot is not None else preco - custo)
        margem = float(eq.margem_snapshot if eq.margem_snapshot is not None else ((lucro / preco * 100) if preco else 0))
        bruto = moeda_num(eq.preco_venda) or preco
        frete = max(float(eq.frete_venda or 0), 0)
        desconto = max(round(bruto - preco, 2), 0)
        return {"base": round(base,2), "opcionais": round(opcionais,2), "custo": round(custo,2), "preco": round(preco,2), "bruto": round(bruto,2), "desconto": desconto, "frete": round(frete,2), "total": round(preco+frete,2), "lucro": round(lucro,2), "margem": round(margem,2), "snapshot": True}

    if eq.produto_venda_id:
        modelo_id = int(eq.produto_venda_id)
        base = float(contexto.get("bases", {}).get(modelo_id, 0.0))
        dinamicos = _opcionais_dinamicos(eq)
        opcionais = 0.0
        configs = contexto.get("configs", {})
        padroes = contexto.get("padroes", {})
        regras = contexto.get("regras", {})
        for campo in contexto.get("campos", []):
            if regras.get((modelo_id, campo), True) is False:
                continue
            valor = ""
            if hasattr(eq, campo):
                valor = str(getattr(eq, campo, "") or "").strip()
            if not valor:
                valor = dinamicos.get(campo) or padroes.get(campo) or "NA"
            cfg = configs.get((campo, valor))
            if campo == "microfone":
                if valor == "Sem fio":
                    padrao_cfg = configs.get(("microfone", "Com fio"))
                    opcionais += _custo_config(cfg) - _custo_config(padrao_cfg)
                continue
            opcionais += _custo_config(cfg)
        opcionais = round(opcionais, 2)
        custo = round(base + opcionais, 2)
    else:
        base = moeda_num(eq.preco_custo)
        opcionais = 0.0
        custo = round(base, 2)

    bruto = moeda_num(eq.preco_venda or eq.valor)
    frete = max(float(eq.frete_venda or 0), 0)
    total = moeda_num(eq.valor or eq.preco_venda)
    preco = max(round(total - frete, 2), 0)
    desconto = max(round(bruto - preco, 2), 0)
    lucro = round(preco - custo, 2)
    margem = round((lucro / preco * 100) if preco else 0, 2)
    return {"base": round(base,2), "opcionais": opcionais, "custo": custo, "preco": preco, "bruto": bruto, "desconto": desconto, "frete": round(frete,2), "total": round(total,2), "lucro": lucro, "margem": margem, "snapshot": False}


def resumo_custo_venda(eq: Equipamento, db: Session, usar_snapshot: bool = True) -> dict:
    if usar_snapshot and eq.custo_final_snapshot is not None:
        base = float(eq.custo_base_snapshot or 0)
        opcionais = float(eq.custo_opcionais_snapshot or 0)
        custo = float(eq.custo_final_snapshot or 0)
        preco = float(eq.preco_venda_snapshot if eq.preco_venda_snapshot is not None else max(moeda_num(eq.valor) - float(eq.frete_venda or 0), 0))
        lucro = float(eq.lucro_snapshot if eq.lucro_snapshot is not None else preco - custo)
        margem = float(eq.margem_snapshot if eq.margem_snapshot is not None else ((lucro / preco * 100) if preco else 0))
        bruto = moeda_num(eq.preco_venda) or preco
        frete = max(float(eq.frete_venda or 0), 0)
        desconto = max(round(bruto - preco, 2), 0)
        return {"base": round(base,2), "opcionais": round(opcionais,2), "custo": round(custo,2), "preco": round(preco,2), "bruto": round(bruto,2), "desconto": desconto, "frete": round(frete,2), "total": round(preco+frete,2), "lucro": round(lucro,2), "margem": round(margem,2), "snapshot": True}
    if eq.produto_venda_id:
        base = custo_base_modelo(db, eq.produto_venda_id)
        opcionais = custo_opcionais_equipamento(eq, db)
        custo = round(base + opcionais, 2)
    else:
        # Legado: o custo digitado era o custo total; não somar opcionais para não duplicar histórico.
        base = moeda_num(eq.preco_custo)
        opcionais = 0.0
        custo = round(base, 2)
    bruto = moeda_num(eq.preco_venda or eq.valor)
    frete = max(float(eq.frete_venda or 0), 0)
    total = moeda_num(eq.valor or eq.preco_venda)
    preco = max(round(total - frete, 2), 0)
    desconto = max(round(bruto - preco, 2), 0)
    lucro = round(preco - custo, 2)
    margem = round((lucro / preco * 100) if preco else 0, 2)
    return {"base": base, "opcionais": opcionais, "custo": custo, "preco": preco, "bruto": bruto, "desconto": desconto, "frete": round(frete,2), "total": round(total,2), "lucro": lucro, "margem": margem, "snapshot": False}


def congelar_custo_venda_se_finalizada(eq: Equipamento, db: Session):
    if (eq.status or "").strip().upper() not in VENDA_STATUS_FINALIZADO or eq.custo_final_snapshot is not None:
        return
    resumo = resumo_custo_venda(eq, db, usar_snapshot=False)
    eq.produto_venda_nome_snapshot = (eq.produto_venda.nome if eq.produto_venda else eq.modelo) or None
    eq.custo_base_snapshot = resumo["base"]
    eq.custo_opcionais_snapshot = resumo["opcionais"]
    eq.custo_final_snapshot = resumo["custo"]
    eq.preco_venda_snapshot = resumo["preco"]
    eq.lucro_snapshot = resumo["lucro"]
    eq.margem_snapshot = resumo["margem"]
    eq.custo_snapshot_em = datetime.now()


def contexto_configuracao_venda(db: Session, equipamento: Equipamento | None = None) -> dict:
    modelos = db.query(VendaModeloEquipamento).filter(VendaModeloEquipamento.ativo == 1).order_by(VendaModeloEquipamento.ordem, VendaModeloEquipamento.nome).all()
    custos_modelos = {str(m.id): custo_base_modelo(db, m.id) for m in modelos}
    precos_modelos = {str(m.id): float(m.preco_basico or 0) for m in modelos}
    tipos_modelos = {str(m.id): m.tipo for m in modelos}
    nomes_modelos = {str(m.id): m.nome for m in modelos}
    prazos_modelos = {str(m.id): int(m.prazo_producao_dias or 20) for m in modelos}
    configs = db.query(VendaOpcionalConfig).options(selectinload(VendaOpcionalConfig.item)).filter(VendaOpcionalConfig.ativo == 1).order_by(VendaOpcionalConfig.ordem).all()
    custos_opcionais = {}
    precos_opcionais = {}
    opcoes_por_campo = {}
    grupos_opcionais = []
    padroes_opcionais = {}
    for c in configs:
        custo = _custo_config(c)
        custos_opcionais.setdefault(c.campo, {})[c.valor] = round(custo, 2)
        opcoes_por_campo.setdefault(c.campo, []).append(c)
        if c.campo not in [g[0] for g in grupos_opcionais]:
            grupos_opcionais.append((c.campo, c.grupo))
        if c.padrao:
            padroes_opcionais[c.campo] = c.valor
    for campo, opcoes in opcoes_por_campo.items():
        padrao_cfg = next((x for x in opcoes if x.padrao), opcoes[0] if opcoes else None)
        padrao_preco = _valor_venda_opcional_config(padrao_cfg)
        for cfg in opcoes:
            precos_opcionais.setdefault(campo, {})[cfg.valor] = round(max(_valor_venda_opcional_config(cfg) - padrao_preco, 0), 2)
    # No microfone o custo exibido do Sem fio é a diferença para os 2 com fio já presentes no equipamento.
    mic_padrao = _opcional_config_por_escolha(db, "microfone", "Com fio")
    mic_sem_fio = _opcional_config_por_escolha(db, "microfone", "Sem fio")
    if mic_padrao and mic_sem_fio:
        custos_opcionais.setdefault("microfone", {})["Com fio"] = 0.0
        custos_opcionais["microfone"]["Sem fio"] = round(_custo_config(mic_sem_fio) - _custo_config(mic_padrao), 2)
    opcionais_atuais = {}
    for campo, _grupo in grupos_opcionais:
        opcionais_atuais[campo] = _valor_opcional_equipamento(equipamento, campo, db) if equipamento else padroes_opcionais.get(campo, "NA")
    habilitados_modelo = {}
    regras = db.query(VendaOpcionalModelo).all()
    for r in regras:
        if r.habilitado:
            habilitados_modelo.setdefault(r.campo, []).append(r.modelo_id)
    resumo = resumo_custo_venda(equipamento, db) if equipamento else {"base":0,"opcionais":0,"custo":0,"preco":0,"bruto":0,"desconto":0,"frete":0,"total":0,"lucro":0,"margem":0,"snapshot":False}
    cupons = db.query(VendaCupom).filter(VendaCupom.ativo == 1).order_by(VendaCupom.codigo.asc()).all()
    if equipamento and equipamento.cupom_id and not any(c.id == equipamento.cupom_id for c in cupons):
        atual = db.query(VendaCupom).filter(VendaCupom.id == equipamento.cupom_id).first()
        if atual:
            cupons.append(atual)
    cupons_dados = {str(c.id): {"codigo": c.codigo, "tipo": c.tipo, "valor": float(c.valor or 0)} for c in cupons}
    return {
        "modelos_venda": modelos, "custos_modelos": custos_modelos, "precos_modelos": precos_modelos,
        "tipos_modelos": tipos_modelos, "nomes_modelos": nomes_modelos, "prazos_modelos": prazos_modelos, "custos_opcionais": custos_opcionais,
        "precos_opcionais": precos_opcionais,
        "opcoes_por_campo": opcoes_por_campo, "grupos_opcionais": grupos_opcionais, "padroes_opcionais": padroes_opcionais,
        "opcionais_atuais": opcionais_atuais, "opcionais_modelos_habilitados": habilitados_modelo,
        "resumo_custo": resumo, "plus_acrescimo": PLUS_ACRESCIMO,
        "cupons_venda": sorted(cupons, key=lambda c: (c.codigo or "")), "cupons_dados": cupons_dados,
        "estoque_cores_venda": contexto_cores_venda(db, equipamento),
        "estoque_utilizado_venda": contexto_estoque_utilizado_venda(db, equipamento),
    }


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
            if "cliente_id" not in existentes_agenda_manual:
                conn.execute(text("ALTER TABLE agenda_manual ADD COLUMN cliente_id INTEGER"))
            if "categoria" not in existentes_agenda_manual:
                conn.execute(text("ALTER TABLE agenda_manual ADD COLUMN categoria VARCHAR(30)"))
            if "local_atendimento" not in existentes_agenda_manual:
                conn.execute(text("ALTER TABLE agenda_manual ADD COLUMN local_atendimento VARCHAR(20)"))

    if "solvoz_empresas" in insp.get_table_names():
        existentes_solvoz_empresas = {c["name"] for c in insp.get_columns("solvoz_empresas")}
        with engine.begin() as conn:
            if "connect_slug" not in existentes_solvoz_empresas:
                conn.execute(text("ALTER TABLE solvoz_empresas ADD COLUMN connect_slug VARCHAR(100)"))
            if "solvoz_id" not in existentes_solvoz_empresas:
                conn.execute(text("ALTER TABLE solvoz_empresas ADD COLUMN solvoz_id INTEGER"))
            if "responsavel_cliente_id" not in existentes_solvoz_empresas:
                conn.execute(text("ALTER TABLE solvoz_empresas ADD COLUMN responsavel_cliente_id INTEGER"))
            if "responsavel_humiat_usuario_id" not in existentes_solvoz_empresas:
                conn.execute(text("ALTER TABLE solvoz_empresas ADD COLUMN responsavel_humiat_usuario_id INTEGER"))
            tipo_blob_solvoz = "BYTEA" if engine.dialect.name == "postgresql" else "BLOB"
            tipo_dt_solvoz = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
            if "logo_mini_data" not in existentes_solvoz_empresas:
                conn.execute(text(f"ALTER TABLE solvoz_empresas ADD COLUMN logo_mini_data {tipo_blob_solvoz}"))
            if "logo_mini_mime" not in existentes_solvoz_empresas:
                conn.execute(text("ALTER TABLE solvoz_empresas ADD COLUMN logo_mini_mime VARCHAR(80)"))
            if "logo_mini_hash" not in existentes_solvoz_empresas:
                conn.execute(text("ALTER TABLE solvoz_empresas ADD COLUMN logo_mini_hash VARCHAR(64)"))
            if "logo_mini_atualizado_em" not in existentes_solvoz_empresas:
                conn.execute(text(f"ALTER TABLE solvoz_empresas ADD COLUMN logo_mini_atualizado_em {tipo_dt_solvoz}"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_solvoz_empresas_solvoz_id ON solvoz_empresas (solvoz_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_solvoz_empresas_responsavel_cliente ON solvoz_empresas (responsavel_cliente_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_solvoz_empresas_responsavel_humiat ON solvoz_empresas (responsavel_humiat_usuario_id)"))
            # O slug do SolVoz é mestre. O Connect mantém apenas alias legado.
            conn.execute(text("UPDATE solvoz_empresas SET connect_slug = slug WHERE connect_slug IS NULL OR TRIM(connect_slug) = ''"))
            conn.execute(text("UPDATE solvoz_empresas SET connect_slug = 'vivioke' WHERE LOWER(slug) = 'vivikaraoke'"))

    if "atualizacao_compras" in insp.get_table_names():
        existentes_atualizacao_compras = {c["name"] for c in insp.get_columns("atualizacao_compras")}
        with engine.begin() as conn:
            if "valor_a_pagar_centavos" not in existentes_atualizacao_compras:
                conn.execute(text("ALTER TABLE atualizacao_compras ADD COLUMN valor_a_pagar_centavos INTEGER"))
            if "frete_centavos" not in existentes_atualizacao_compras:
                conn.execute(text("ALTER TABLE atualizacao_compras ADD COLUMN frete_centavos INTEGER"))
            if "concluido_em" not in existentes_atualizacao_compras:
                tipo_dt_atualizacao = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
                conn.execute(text(f"ALTER TABLE atualizacao_compras ADD COLUMN concluido_em {tipo_dt_atualizacao}"))

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
            if "descontar_estoque" not in existentes:
                conn.execute(text("ALTER TABLE assistencias ADD COLUMN descontar_estoque INTEGER NOT NULL DEFAULT 1"))
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
    if "catalogo_itens" in insp.get_table_names():
        existentes_itens = {c["name"] for c in insp.get_columns("catalogo_itens")}
        with engine.begin() as conn:
            if "controla_estoque" not in existentes_itens:
                conn.execute(text("ALTER TABLE catalogo_itens ADD COLUMN controla_estoque INTEGER NOT NULL DEFAULT 1"))
            if "fornecedor_id" not in existentes_itens:
                conn.execute(text("ALTER TABLE catalogo_itens ADD COLUMN fornecedor_id INTEGER"))
            unidade_criada = "unidade" not in existentes_itens
            if unidade_criada:
                conn.execute(text("ALTER TABLE catalogo_itens ADD COLUMN unidade VARCHAR(10) NOT NULL DEFAULT 'UN'"))
                # Carga inicial 1.2.28: todos os itens começam como UN. CABO BIPOLAR
                # e FITA LED já entram como M. Depois da primeira migração, a unidade
                # é sempre editável por Item e não é mais sobrescrita automaticamente.
                conn.execute(text("UPDATE catalogo_itens SET unidade = 'M' WHERE UPPER(TRIM(nome)) IN ('CABO BIPOLAR', 'FITA LED')"))
            else:
                conn.execute(text("UPDATE catalogo_itens SET unidade = 'UN' WHERE unidade IS NULL OR TRIM(unidade) = ''"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_catalogo_itens_fornecedor_id ON catalogo_itens (fornecedor_id)"))

    if "estoque_compras_pedidos" in insp.get_table_names():
        existentes_pedidos = {c["name"] for c in insp.get_columns("estoque_compras_pedidos")}
        with engine.begin() as conn:
            if "fornecedor_id" not in existentes_pedidos:
                conn.execute(text("ALTER TABLE estoque_compras_pedidos ADD COLUMN fornecedor_id INTEGER"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_estoque_compras_fornecedor_id ON estoque_compras_pedidos (fornecedor_id)"))

    if "venda_opcionais_config" in insp.get_table_names():
        existentes_opcionais = {c["name"] for c in insp.get_columns("venda_opcionais_config")}
        with engine.begin() as conn:
            if "padrao" not in existentes_opcionais:
                conn.execute(text("ALTER TABLE venda_opcionais_config ADD COLUMN padrao INTEGER NOT NULL DEFAULT 0"))

    if "venda_modelos_equipamento" in insp.get_table_names():
        existentes_modelos_venda = {c["name"] for c in insp.get_columns("venda_modelos_equipamento")}
        with engine.begin() as conn:
            if "prazo_producao_dias" not in existentes_modelos_venda:
                conn.execute(text("ALTER TABLE venda_modelos_equipamento ADD COLUMN prazo_producao_dias INTEGER NOT NULL DEFAULT 20"))

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
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN catalogo_impresso VARCHAR(20) NOT NULL DEFAULT 'NA'"))
            elif engine.dialect.name == "postgresql":
                # Versões antigas criaram esta coluna como VARCHAR(10).
                # Antes de migrar o valor "Sim" para "Encadernado", aumenta o campo.
                conn.execute(text("ALTER TABLE equipamentos ALTER COLUMN catalogo_impresso TYPE VARCHAR(20)"))
            if "opcionais_json" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN opcionais_json TEXT"))
            if "produto_venda_id" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN produto_venda_id INTEGER"))
            if "catalogo_venda" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN catalogo_venda VARCHAR(20) NOT NULL DEFAULT 'BASICO'"))
            if "produto_venda_nome_snapshot" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN produto_venda_nome_snapshot VARCHAR(180)"))
            for coluna in ("custo_base_snapshot", "custo_opcionais_snapshot", "custo_final_snapshot", "preco_venda_snapshot", "lucro_snapshot", "margem_snapshot"):
                if coluna not in existentes_equipamentos:
                    conn.execute(text(f"ALTER TABLE equipamentos ADD COLUMN {coluna} FLOAT"))
            if "custo_snapshot_em" not in existentes_equipamentos:
                tipo_dt_snapshot = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
                conn.execute(text(f"ALTER TABLE equipamentos ADD COLUMN custo_snapshot_em {tipo_dt_snapshot}"))
            if "cupom_id" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN cupom_id INTEGER"))
            if "cupom_codigo_snapshot" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN cupom_codigo_snapshot VARCHAR(80)"))
            if "cupom_desconto_snapshot" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN cupom_desconto_snapshot FLOAT NOT NULL DEFAULT 0"))
            if "desconto_manual" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN desconto_manual FLOAT NOT NULL DEFAULT 0"))
            if "frete_venda" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN frete_venda FLOAT NOT NULL DEFAULT 0"))
            if "venda_token" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN venda_token VARCHAR(64)"))
            if "estoque_uso_override" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN estoque_uso_override TEXT"))
            if "estoque_uso_manual" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN estoque_uso_manual INTEGER NOT NULL DEFAULT 0"))
            if "descontar_estoque" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN descontar_estoque INTEGER NOT NULL DEFAULT 1"))
    db = SessionLocal()
    try:
        # Preserva o comportamento histórico do QR sem exigir configuração manual
        # no primeiro deploy. As demais empresas são cadastradas pelo ADM do Organiza.
        if not db.query(SolVozEmpresa).filter(SolVozEmpresa.slug == "karaokerj").first():
            db.add(SolVozEmpresa(
                nome="Karaokê RJ",
                slug="karaokerj",
                connect_slug="karaokerj",
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
        # 1.1.63: catálogo de produtos/composição e mapa de Opcionais.
        # Preços da planilha nunca são importados; custos vêm exclusivamente de Itens.
        _seed_modelos_e_opcionais_venda(db)
        corrigidos_1163 = _migrar_composicoes_venda_1163(db)
        if corrigidos_1163:
            print(f"[VENDAS] 1.1.63: {corrigidos_1163} composição(ões) corrigida(s): Guitarrinha 19 e Guitarra 22.")
        itens_maiusculos = _migrar_itens_maiusculos_1164(db)
        espelhos_adicionados = _migrar_espelho_teclado_1164(db)
        if itens_maiusculos or espelhos_adicionados:
            print(f"[VENDAS] 1.1.64: itens em maiúsculas={itens_maiusculos}; espelhos adicionados={espelhos_adicionados}.")
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

        # 1.1.96: clientes externos possuem uma única empresa no Humiat ID.
        # Corrige vínculos antigos criados pelo fallback Karaokê RJ e garante que
        # os produtos já liberados (ex.: Connect) estejam ativos na empresa correta.
        humiat_empresa_corrigidos = 0
        for cliente_humiat in db.query(Cliente).options(
            selectinload(Cliente.equipamentos).selectinload(Equipamento.solvoz_empresa)
        ).all():
            humiat_empresa_corrigidos += _humiat_sincronizar_empresa_unica_cliente(cliente_humiat, db)
        if humiat_empresa_corrigidos:
            print(f"[HUMIAT ID] 1.1.96: {humiat_empresa_corrigidos} vínculo(s)/produto(s) de empresa corrigido(s).")

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

        # 1.1.66: primeira implantação oficial do estoque.
        # Remove baixas automáticas de testes/versões anteriores e passa a considerar
        # somente Vendas a Fazer e Manutenções a Fazer como reservas operacionais.
        removidos_estoque_1166 = _migrar_estoque_primeira_implantacao_1166(db)
        if removidos_estoque_1166:
            print(f"[ESTOQUE] 1.1.66: {removidos_estoque_1166} baixa(s) automática(s) anterior(es) removida(s); operações abertas reservadas.")
        removidas_manut_1168 = _migrar_reservas_manutencao_aprovada_1168(db)
        if removidas_manut_1168:
            print(f"[ESTOQUE] 1.1.68: {removidas_manut_1168} reserva(s) de manutenção refeita(s); somente aprovadas permanecem.")
        itens_1174, vendas_1174 = _migrar_itens_e_reservas_1174(db)
        if itens_1174 or vendas_1174:
            print(f"[ESTOQUE] 1.1.74: itens/categorias ajustados={itens_1174}; vendas abertas ressincronizadas={vendas_1174}.")
        categorias_1175 = _migrar_categorias_itens_1175(db)
        if categorias_1175:
            print(f"[ESTOQUE] 1.1.75: categorias reorganizadas={categorias_1175}.")
        fornecedores_1226, vinculos_1226 = _seed_fornecedores_itens_1226(db)
        if fornecedores_1226 or vinculos_1226:
            print(f"[ITENS] 1.2.26: fornecedores criados={fornecedores_1226}; vínculos iniciais={vinculos_1226}.")
        minimos_restaurados_1236 = _migrar_minimos_estoque_legado_1236(db)
        if minimos_restaurados_1236:
            print(f"[ESTOQUE] 1.2.36: mínimos legados restaurados={minimos_restaurados_1236}.")
        manut_aprovadas_1233, manut_pendentes_1233 = _migrar_estoque_manutencao_aprovacao_1233(db)
        if manut_aprovadas_1233 or manut_pendentes_1233:
            print(
                f"[ESTOQUE] 1.2.33: manutenções aprovadas corrigidas={manut_aprovadas_1233}; "
                f"pendentes recalculadas={manut_pendentes_1233}."
            )
        reservas_manut_1235, manut_revisadas_1235 = _migrar_estoque_regra_unica_1235(db)
        if reservas_manut_1235 or manut_revisadas_1235:
            print(
                f"[ESTOQUE] 1.2.35: reservas de manutenção removidas={reservas_manut_1235}; "
                f"manutenções revisadas={manut_revisadas_1235}."
            )
        reservas_1244, vendas_1244, manut_1244 = _migrar_estoque_baixa_imediata_1244(db)
        if reservas_1244 or vendas_1244 or manut_1244:
            print(
                f"[ESTOQUE] 1.2.44: reservas removidas={reservas_1244}; "
                f"vendas revisadas={vendas_1244}; manutenções revisadas={manut_1244}."
            )
        db.commit()
        # Tema é carregado apenas do cache local; nenhuma tela faz consulta externa.
        _carregar_tema_visual_runtime(db)
    finally:
        db.close()


@app.get("/", response_class=HTMLResponse)
def inicio_publico(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/privacidade", response_class=HTMLResponse)
def politica_privacidade(request: Request):
    """Política pública usada também na configuração OAuth das integrações HUMIAT."""
    return templates.TemplateResponse("privacidade.html", {"request": request})


@app.get("/termos", response_class=HTMLResponse)
def termos_servico(request: Request):
    """Termos públicos de uso das plataformas HUMIAT."""
    return templates.TemplateResponse("termos.html", {"request": request})


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

@app.get("/organiza/diagnostico-performance", response_class=HTMLResponse)
def organiza_diagnostico_performance(
        request: Request,
        limit: int = 80,
        usuario: Usuario = Depends(usuario_logado),
):
    exigir_admin(usuario)
    limit = max(20, min(limit, 150))
    resumo = performance_summary(limit)
    return templates.TemplateResponse("organiza/diagnostico_performance.html", {
        "request": request,
        "usuario": usuario,
        "monitor": monitor_status(),
        "ranking": resumo["ranking"],
        "tabelas": resumo["tables"],
        "sugestoes": resumo["suggestions"],
        "registros": resumo["records"],
        "limite": limit,
        "limpos": request.query_params.get("limpos"),
    })


@app.get("/organiza/diagnostico-performance/dados", response_class=JSONResponse)
def organiza_diagnostico_performance_dados(
        limit: int = 80,
        detalhes: bool = False,
        usuario: Usuario = Depends(usuario_logado),
):
    exigir_admin(usuario)
    limit = max(20, min(limit, 150))
    resumo = performance_summary(limit)
    if detalhes:
        return {"monitor": monitor_status(), **resumo}
    # JSON leve por padrão: a tela/integração recebe somente o necessário.
    return {
        "monitor": monitor_status(),
        "ranking": resumo.get("ranking", [])[:20],
        "tables": resumo.get("tables", [])[:20],
        "suggestions": resumo.get("suggestions", [])[:30],
        "records_count": len(resumo.get("records", [])),
    }


@app.post("/organiza/diagnostico-performance/limpar")
def organiza_diagnostico_performance_limpar(
        usuario: Usuario = Depends(usuario_logado),
):
    exigir_admin(usuario)
    total = clear_records()
    return RedirectResponse(f"/organiza/diagnostico-performance?limpos={total}", status_code=303)


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


def _lokafest_cliente_por_identificador(
    db: Session,
    cpf: str = "",
    whatsapp: str = "",
    cliente_id: str = "",
    maquina: str = "",
):
    cpf_limpo = _lokafest_digitos(cpf)
    whats_limpo = _lokafest_digitos(whatsapp)
    cliente_id_limpo = _lokafest_digitos(cliente_id)
    maquina_limpa = (maquina or "").strip().upper()

    # Recuperação manual: o número técnico da máquina (ex.: KRJ00786)
    # identifica diretamente o equipamento e, por consequência, seu cliente.
    if maquina_limpa:
        equipamento = (
            db.query(Equipamento)
            .filter(func.upper(func.trim(Equipamento.maquina)) == maquina_limpa)
            .first()
        )
        if equipamento:
            return (
                db.query(Cliente)
                .options(selectinload(Cliente.equipamentos))
                .filter(Cliente.id == equipamento.cliente_id)
                .first()
            )

    # Em uma revalidação do LokaFest, o cliente_id salvo anteriormente é a
    # referência mais estável. Telefone/CPF podem ter sido corrigidos depois.
    if cliente_id_limpo:
        cliente = (
            db.query(Cliente)
            .options(selectinload(Cliente.equipamentos))
            .filter(Cliente.id == int(cliente_id_limpo))
            .first()
        )
        if cliente:
            return cliente

    candidatos = db.query(Cliente).options(selectinload(Cliente.equipamentos)).all()

    # WhatsApp é a chave principal entre LokaFest e Organiza. O LokaFest pode
    # ter CPF da pessoa enquanto o Organiza guarda o CNPJ da empresa; nesses
    # casos o telefone continua identificando corretamente o mesmo cadastro.
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

    # CPF/CNPJ fica como fallback para cadastros sem telefone utilizável.
    if cpf_limpo:
        for cliente in candidatos:
            if _lokafest_digitos(cliente.documento) == cpf_limpo:
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
    if tipo == "FLIPERAMA":
        return "fliperama"

    # Compatibilidade com cadastros antigos ou sem tipo padronizado.
    if "IPHONE" in modelo:
        return "iphone"
    if "PORTATIL" in modelo or "MALETA" in modelo:
        return "portatil"
    if "JUKEBOX" in modelo:
        return "jukebox"
    if "FLIPERAMA" in modelo or "ARCADE" in modelo:
        return "fliperama"
    return None


@app.get("/api/integracoes/lokafest/cliente")
def api_lokafest_cliente(
    cpf: str = "",
    whatsapp: str = "",
    cliente_id: str = "",
    maquina: str = "",
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """
    Endpoint privado consumido pelo LokaFest.

    Busca cliente por cliente_id salvo, CPF, WhatsApp ou número técnico da
    máquina e devolve equipamentos ativos Karaoke RJ dos tipos
    Jukebox, Portátil/Maleta, iPhone e Fliperama.

    Header obrigatório:
        Authorization: Bearer <LOKAFEST_API_TOKEN>
    """
    if not _lokafest_token_valido(authorization):
        raise HTTPException(status_code=401, detail="Token de integração inválido.")

    if (
        not _lokafest_digitos(cpf)
        and not _lokafest_digitos(whatsapp)
        and not _lokafest_digitos(cliente_id)
        and not (maquina or "").strip()
    ):
        raise HTTPException(status_code=400, detail="Informe CPF, WhatsApp, cliente ou máquina.")

    cliente = _lokafest_cliente_por_identificador(
        db, cpf, whatsapp, cliente_id=cliente_id, maquina=maquina
    )
    if not cliente:
        return {
            "encontrado": False,
            "cliente_id": None,
            "cpf": _lokafest_digitos(cpf),
            "atualizacao": obter_pacote_atual(db),
            "equipamentos": {"jukebox": 0, "portatil": 0, "iphone": 0, "fliperama": 0},
            "detalhes": [],
        }

    pacote_obrigatorio = obter_pacote_atual(db)
    contagem = {"jukebox": 0, "portatil": 0, "iphone": 0, "fliperama": 0}
    detalhes = []

    for eq in cliente.equipamentos:
        if (eq.status or "").strip().lower() != "ativo":
            continue

        classe = _lokafest_tipo_modelo(eq)
        if not classe:
            continue

        # Karaokê continua restrito aos equipamentos Karaokê RJ. Para Fliperama
        # não existe outra restrição: basta o cliente possuir um Fliperama ativo.
        if classe != "fliperama" and (eq.fabricante or "").strip().upper() != "KARAOKERJ":
            continue

        contagem[classe] += 1
        pacote_instalado = (eq.pacote or "").strip() or None
        if classe == "fliperama":
            # Fliperama não usa catálogo/pacote de músicas. Para o LokaFest
            # basta existir ao menos um equipamento ativo.
            falta = 0
            pacote_alvo = None
            atualizado = True
        else:
            falta = calcular_falta_pacote(pacote_instalado, pacote_obrigatorio)
            pacote_alvo = pacote_obrigatorio
            atualizado = bool(pacote_instalado and pacote_instalado == pacote_obrigatorio)

        detalhes.append({
            "id": eq.id,
            "tipo": classe,
            "tipo_origem": eq.tipo,
            "modelo": eq.modelo,
            "identificacao": rotulo_maquina(eq),
            "numero_maquina": eq.maquina,
            "numero_cliente": eq.numero_maquina_cliente,
            "pacote": pacote_instalado,
            "pacote_obrigatorio": pacote_alvo,
            "falta_pacote": falta,
            "atualizado": atualizado,
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





def _cliente_resumo_visual(cliente: Cliente | None) -> dict:
    # Lista de clientes precisa ser leve: nenhuma validação de cadastro é feita aqui.
    # A checagem completa acontece somente ao clicar em “Atualizar cadastro”.
    equipamento = _equipamento_ativo_mais_antigo(cliente)
    pacote = ((equipamento.pacote or '').strip() if equipamento else '') or '-'
    identificacao = rotulo_maquina(equipamento) if equipamento else '-'
    email = (getattr(cliente, 'email', '') or '').strip() or None
    return {
        'equipamento_base': equipamento,
        'pacote_base': pacote,
        'identificacao_base': identificacao,
        'email': email,
        'municipio': (getattr(cliente, 'municipio', None) or getattr(cliente, 'cidade', None) or '').strip() or None,
        'empresa': (getattr(cliente, 'empresa', None) or '').strip() or None,
    }


def _cliente_cadastro_parece_atualizado(cliente: Cliente | None) -> bool:
    if not cliente:
        return False
    email = (cliente.email or '').strip()
    documento = limpar_documento(cliente.documento or '')
    return bool(
        (cliente.nome or '').strip()
        and telefone_valido(cliente.telefone, cliente.pais, cliente.ddi)
        and _gmail_valido(email)
        and len(documento) in (11, 14)
        and (cliente.cep or '').strip()
        and (cliente.endereco or '').strip()
        and (cliente.endereco_numero or '').strip()
    )


def _ultima_campanha_atualizacao_cliente(db: Session, cliente: Cliente | None) -> dict | None:
    if not cliente:
        return None
    dest = (
        db.query(CampanhaDestinatario)
        .join(Campanha, Campanha.id == CampanhaDestinatario.campanha_id)
        .filter(
            CampanhaDestinatario.cliente_id == int(cliente.id),
            func.upper(Campanha.lista_tipo) == 'ATUALIZACAO',
        )
        .order_by(CampanhaDestinatario.id.desc())
        .first()
    )
    if not dest:
        return None
    campanha = db.query(Campanha).filter(Campanha.id == dest.campanha_id).first()
    telefone = (dest.telefone_pronto or cliente.whatsapp_completo() or '').strip()
    mensagem = (dest.mensagem_pronta or '').strip()
    return {
        'campanha_id': int(dest.campanha_id),
        'destinatario_id': int(dest.id),
        'nome': (campanha.nome if campanha else 'Campanha de atualização'),
        'status': dest.status or 'PENDENTE',
        'link_pronto': (dest.link_pronto or '').strip(),
        'mensagem_pronta': mensagem,
        'whatsapp_url': _whatsapp_url_pronta(telefone, mensagem) if telefone and mensagem else '',
        'consulta_url': f'/organiza/clientes/{cliente.id}?consulta=1&campanha_id={dest.campanha_id}',
        'enviado_em': dest.enviado_em,
    }

@app.get("/organiza/clientes", response_class=HTMLResponse)
def clientes(request: Request, busca: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    query = db.query(Cliente).options(selectinload(Cliente.equipamentos))
    termo = busca.strip()
    if termo:
        like = f"%{termo}%"
        query = query.filter(or_(Cliente.nome.ilike(like), Cliente.telefone.ilike(like), Cliente.empresa.ilike(like), Cliente.municipio.ilike(like), Cliente.cidade.ilike(like), Cliente.email.ilike(like)))
    lista = query.order_by(Cliente.nome.asc()).all()
    resumos_clientes = {int(cliente.id): _cliente_resumo_visual(cliente) for cliente in lista}
    return templates.TemplateResponse("organiza/clientes.html", {
        "request": request, "usuario": usuario, "clientes": lista, "busca": busca,
        "total_clientes": db.query(Cliente).count(), "total_equipamentos": db.query(Equipamento).count(),
        "resumos_clientes": resumos_clientes,
    })



@app.get("/organiza/clientes/{cliente_id}/cadastro-whatsapp", response_class=HTMLResponse)
def cliente_cadastro_whatsapp(
    cliente_id: int, request: Request, forcar: int = 0,
    usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db),
):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(404)
    if not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)
        db.commit()
    cadastro_url = f"{PUBLIC_BASE_URL.rstrip('/')}/cadastro/{cliente.token_ficha}"
    mensagem = (
        f"Olá, {cliente.nome}!\n\n"
        "Precisamos confirmar/atualizar seu cadastro para prosseguir com o atendimento. "
        "Acesse o link abaixo e revise seus dados:\n\n"
        f"{cadastro_url}\n\nKaraokê RJ"
    )
    whatsapp = _whatsapp_url_pronta(cliente.whatsapp_completo() or '', mensagem)
    if not whatsapp:
        return RedirectResponse(f"/organiza/clientes/{cliente_id}?humiat_erro=" + quote_plus("Cliente sem WhatsApp válido."), status_code=303)
    if not forcar and _cliente_cadastro_parece_atualizado(cliente):
        return templates.TemplateResponse("organiza/cliente_confirmar_cadastro.html", {
            "request": request, "usuario": usuario, "cliente": cliente, "whatsapp_url": whatsapp,
        })
    return RedirectResponse(whatsapp, status_code=303)

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
def cliente_novo(request: Request, retorno: str = "", usuario: Usuario = Depends(usuario_logado)):
    retorno = "venda" if (retorno or "").strip().lower() == "venda" else ""
    return templates.TemplateResponse("organiza/cliente_form.html", {
        "request": request, "usuario": usuario, "cliente": None, "erro": "", "retorno": retorno,
    })


@app.post("/organiza/clientes/novo")
async def cliente_criar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    retorno = "venda" if (form.get("retorno") or "").strip().lower() == "venda" else ""
    pais, ddi, telefone = normalizar_contato(form.get("pais"), form.get("ddi"), form.get("telefone"))
    existente = localizar_cliente_por_contato(db, pais, ddi, telefone) if telefone else None
    erro = ""
    if not (form.get("nome") or "").strip():
        erro = "Informe o nome do cliente."
    elif not telefone_valido(telefone, pais, ddi):
        erro = "Informe um WhatsApp válido para o país selecionado."
    elif existente:
        erro = "Já existe um cliente com este WhatsApp."
    if erro:
        cliente = Cliente()
        preencher_cliente(cliente, form)
        return templates.TemplateResponse("organiza/cliente_form.html", {
            "request": request, "usuario": usuario, "cliente": cliente, "erro": erro, "retorno": retorno,
        }, status_code=400)
    cliente = Cliente()
    preencher_cliente(cliente, form)
    db.add(cliente); db.commit(); db.refresh(cliente)
    if retorno == "venda":
        return RedirectResponse(f"/organiza/vendas/nova?cliente_id={cliente.id}", status_code=303)
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
    secao_cliente = (request.query_params.get("secao") or "").strip().lower()
    if secao_cliente not in {"agenda", "campanhas", "atualizacoes", "manutencoes", "estoque"}:
        secao_cliente = ""
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
    for eq_item in equipamentos:
        eq_item.estoque_utilizado_linhas = []
        eq_item.estoque_utilizado_total = 0
        eq_item.estoque_utilizado_manual = False
        if secao_cliente == "estoque" and eq_item.produto_venda_id:
            estoque_ctx = contexto_estoque_utilizado_venda(db, eq_item)
            eq_item.estoque_utilizado_linhas = estoque_ctx["linhas"]
            eq_item.estoque_utilizado_total = estoque_ctx["total"]
            eq_item.estoque_utilizado_manual = estoque_ctx["manual"]
    manutencoes = []
    if secao_cliente == "manutencoes" or somente_consulta:
        manutencoes = db.query(Manutencao).filter(Manutencao.cliente_id == cliente_id).order_by(Manutencao.criado_em.desc()).limit(30).all()

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
    atualizacoes_ctx = {"compras": [], "agendamentos": {}, "pacotes": [], "gmail_ok": _gmail_valido(cliente.email)}
    if not somente_consulta and secao_cliente == "atualizacoes":
        atualizacoes_ctx = _atualizacao_contexto_admin_cliente(db, cliente)
    elif not somente_consulta:
        # A lista de pacotes é leve e necessária no bloco principal de equipamentos.
        atualizacoes_ctx["pacotes"] = db.query(AtualizacaoPacote).filter(AtualizacaoPacote.ativo == 1).order_by(AtualizacaoPacote.pacote.asc()).all()
    resumo_visual = _cliente_resumo_visual(cliente)
    campanhas_cliente = []
    agendamentos_cliente = []
    if not somente_consulta and secao_cliente == "campanhas":
        destinos = (
            db.query(CampanhaDestinatario)
            .options(selectinload(CampanhaDestinatario.campanha))
            .filter(CampanhaDestinatario.cliente_id == cliente.id)
            .order_by(CampanhaDestinatario.id.desc())
            .limit(12)
            .all()
        )
        for dest in destinos:
            campanha = dest.campanha
            if not campanha:
                continue
            campanhas_cliente.append({
                "campanha": campanha,
                "destinatario": dest,
                "status_rotulo": _rotulo_status_envio_campanha(dest.status, True),
                "whatsapp_url": _whatsapp_url_pronta(dest.telefone_pronto or cliente.whatsapp_completo() or "", dest.mensagem_pronta or ""),
            })
    if not somente_consulta and secao_cliente == "agenda":
        agendamentos_cliente = (
            db.query(AgendaManual)
            .filter(AgendaManual.cliente_id == cliente.id)
            .order_by(AgendaManual.data_hora.desc())
            .limit(8)
            .all()
        )

    return templates.TemplateResponse("organiza/cliente_detalhe.html", {
        "request": request, "usuario": usuario, "cliente": cliente, "manutencoes": manutencoes,
        "equipamentos": equipamentos, "status_filtro": status_filtro, "tipo_filtro": tipo_filtro,
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
        "resumo_visual": resumo_visual,
        "secao_cliente": secao_cliente,
        "campanhas_cliente": campanhas_cliente,
        "agendamentos_cliente": agendamentos_cliente,
        "agenda_sucesso": request.query_params.get("agenda_sucesso", ""),
        "agenda_erro": request.query_params.get("agenda_erro", ""),
        "campanha_cliente_sucesso": request.query_params.get("campanha_cliente_sucesso", ""),
        "editar_compra_id": int(request.query_params.get("editar_compra") or 0) if str(request.query_params.get("editar_compra") or "").isdigit() else 0,
    })


@app.post("/organiza/clientes/{cliente_id}/agendar-atendimento")
async def cliente_agendar_atendimento_rapido(
    cliente_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(404)
    form = await request.form()
    categoria = _agenda_categoria(form.get("categoria"))
    local = _agenda_local(form.get("local_atendimento"))
    data_hora = datetime_form(form.get("data_hora") or "")
    descricao = (form.get("descricao") or "").strip()
    if not data_hora or data_hora <= datetime.now():
        return RedirectResponse(
            f"/organiza/clientes/{cliente.id}?agenda_erro={quote_plus('Escolha uma data e horário futuros.')}#agenda-cliente",
            status_code=303,
        )
    evento = AgendaManual(
        cliente_id=cliente.id,
        titulo=_agenda_titulo(categoria, local, cliente.nome),
        tipo=_agenda_tipo_visual(categoria, local),
        categoria=categoria,
        local_atendimento=local,
        data_hora=data_hora,
        contato=f"{cliente.nome} · +{cliente.ddi} {cliente.telefone_formatado()}",
        observacao=descricao or "Atendimento agendado pela ficha do cliente.",
    )
    db.add(evento)
    db.flush()
    _google_calendar_manual_sincronizar(db, evento)
    db.commit()
    msg = "Atendimento reservado no Organiza e enviado ao Google Agenda." if evento.google_sync_status == "SINCRONIZADO" else "Atendimento reservado no Organiza; a sincronização com o Google Agenda ficou pendente."
    return RedirectResponse(
        f"/organiza/clientes/{cliente.id}?agenda_sucesso={quote_plus(msg)}#agenda-cliente",
        status_code=303,
    )


@app.post("/organiza/clientes/{cliente_id}/campanhas/{destinatario_id}/confirmar-reenvio")
def cliente_campanha_confirmar_reenvio(
    cliente_id: int,
    destinatario_id: int,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    dest = db.query(CampanhaDestinatario).filter(
        CampanhaDestinatario.id == destinatario_id,
        CampanhaDestinatario.cliente_id == cliente_id,
    ).first()
    if not dest:
        raise HTTPException(404)
    dest.status = "ENVIADO"
    dest.enviado_por_id = usuario.id
    dest.enviado_em = datetime.now()
    dest.reservado_por_id = None
    dest.reservado_em = None
    db.commit()
    return RedirectResponse(
        f"/organiza/clientes/{cliente_id}?campanha_cliente_sucesso=1#campanhas-cliente",
        status_code=303,
    )


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
    existente = localizar_cliente_por_contato(db, pais, ddi, telefone) if telefone else None
    erro = ""
    if not (form.get("nome") or "").strip(): erro = "Informe o nome do cliente."
    elif not telefone_valido(telefone, pais, ddi): erro = "Informe um WhatsApp válido para o país selecionado."
    elif existente and int(existente.id) != int(cliente_id): erro = "Já existe outro cliente com este WhatsApp."
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
        voltar_nfse = (request.query_params.get("voltar_nfse") or "").strip()
        if voltar_nfse.isdigit():
            return RedirectResponse(f"/organiza/nfse/{voltar_nfse}?cnpj_atualizado=1", status_code=303)
        return RedirectResponse(f"/organiza/clientes/{cliente_id}?cnpj_sucesso={quote_plus(msg)}", status_code=303)
    except (ValueError, RuntimeError) as exc:
        voltar_nfse = (request.query_params.get("voltar_nfse") or "").strip()
        if voltar_nfse.isdigit():
            return RedirectResponse(f"/organiza/nfse/{voltar_nfse}?cnpj_erro={quote_plus(str(exc))}", status_code=303)
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
    # Extensão especial do mês de aniversário solicitada para as campanhas atuais.
    # Mantém os mesmos valores promocionais e estende somente o prazo até 10/10/2026.
    if date.today() <= CAMPANHA_ANIVERSARIO_VALIDA_ATE:
        promo["ativo"] = True
        promo["vigente"] = True
        promo["valida_ate"] = CAMPANHA_ANIVERSARIO_VALIDA_ATE.isoformat()
        promo["valida_ate_br"] = CAMPANHA_ANIVERSARIO_VALIDA_ATE.strftime("%d/%m/%Y")
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
    """Garante pacote nas máquinas que usam catálogo e mantém Fliperama como N/A."""
    cliente = db.query(Cliente).filter(Cliente.id == int(cliente_id)).first()
    if not cliente:
        return 0, 0
    equipamentos = db.query(Equipamento).filter(Equipamento.cliente_id == int(cliente_id)).all()
    if not equipamentos:
        return 0, 0
    primeiro = _normalizar_pacote_cadastrado(primeiro_pacote) or _primeiro_pacote_disponivel(db)
    pacote_atual = obter_pacote_atual(db)
    eq_alterados = 0
    maquinas_com_pacote = []
    for eq in equipamentos:
        if tipo_equipamento_padrao(eq.tipo or "") == "FLIPERAMA":
            if (eq.pacote or "").strip().upper() != "NA":
                eq.pacote = "NA"
                eq_alterados += 1
            if eq.falta_pacote != 0:
                eq.falta_pacote = 0
                eq_alterados += 1
            continue
        maquinas_com_pacote.append(eq)
        pacote = _normalizar_pacote_cadastrado(eq.pacote) or primeiro
        if (eq.pacote or "").strip() != pacote:
            eq.pacote = pacote
            eq_alterados += 1
        falta = calcular_falta_pacote(eq.pacote, pacote_atual)
        if eq.falta_pacote != falta:
            eq.falta_pacote = falta
            eq_alterados += 1
    cliente_alterado = 0
    if maquinas_com_pacote:
        ativos = [eq for eq in maquinas_com_pacote if (eq.status or "").strip().lower() == "ativo"]
        referencia = min(ativos or maquinas_com_pacote, key=_chave_equipamento_mais_antigo)
        pacote_cliente = _normalizar_pacote_cadastrado(referencia.pacote) or primeiro
        falta_cliente = calcular_falta_pacote(pacote_cliente, pacote_atual)
    else:
        pacote_cliente = "NA"
        falta_cliente = 0
    if (cliente.pacote or "").strip() != pacote_cliente:
        cliente.pacote = pacote_cliente
        cliente_alterado = 1
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
        maquinas_com_pacote = []
        for eq in equipamentos:
            if tipo_equipamento_padrao(eq.tipo or "") == "FLIPERAMA":
                if (eq.pacote or "").strip().upper() != "NA":
                    eq.pacote = "NA"
                    total_eq += 1
                if eq.falta_pacote != 0:
                    eq.falta_pacote = 0
                    total_eq += 1
                continue
            maquinas_com_pacote.append(eq)
            pacote = _normalizar_pacote_cadastrado(eq.pacote) or primeiro
            if (eq.pacote or "").strip() != pacote:
                eq.pacote = pacote
                total_eq += 1
            falta = calcular_falta_pacote(eq.pacote, pacote_atual)
            if eq.falta_pacote != falta:
                eq.falta_pacote = falta
                total_eq += 1
        if maquinas_com_pacote:
            ativos = [eq for eq in maquinas_com_pacote if (eq.status or "").strip().lower() == "ativo"]
            referencia = min(ativos or maquinas_com_pacote, key=_chave_equipamento_mais_antigo)
            pacote_cliente = _normalizar_pacote_cadastrado(referencia.pacote) or primeiro
            falta_cliente = calcular_falta_pacote(pacote_cliente, pacote_atual)
        else:
            pacote_cliente = "NA"
            falta_cliente = 0
        alterou_cliente = False
        if (cliente.pacote or "").strip() != pacote_cliente:
            cliente.pacote = pacote_cliente
            alterou_cliente = True
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


CAMPANHA_ANIVERSARIO_VALIDA_ATE = date(2026, 10, 10)
CAMPANHA_ANIVERSARIO_SITE = "www.karaokerj.com.br"
CAMPANHA_ANIVERSARIO_DESTAQUE = (
    "🎉 NOVIDADE! PRORROGAMOS A PROMOÇÃO!\n"
    "Agora você pode aproveitar as condições especiais da Karaokê RJ até 10/10/2026.\n"
    "🎤 Mês de aniversário Karaokê RJ: o Karaokê Plus sai pelo preço do Básico!\n"
    f"{CAMPANHA_ANIVERSARIO_SITE}"
)


def _campanha_mensagem_base_promocional(campanha: Campanha) -> str:
    """Mantém a mensagem original, retirando o prazo antigo e adicionando o aviso novo acima."""
    mensagem = (campanha.mensagem or "").strip()
    # Remove somente referências ao encerramento antigo. O restante do anúncio é preservado.
    for padrao in (
        r"(?i)\b30/09/2026\b", r"(?i)\b30/09/26\b", r"(?i)\b30/09\b",
        r"(?i)\b30-09-2026\b", r"(?i)\b2026-09-30\b",
    ):
        mensagem = re.sub(padrao, "", mensagem)
    mensagem = re.sub(r"[ \t]+\n", "\n", mensagem)
    mensagem = re.sub(r"\n{3,}", "\n\n", mensagem).strip()
    return f"{CAMPANHA_ANIVERSARIO_DESTAQUE}\n\n{mensagem}".strip()


def _mensagem_campanha(
    campanha: Campanha, pessoa, pacotes_override: list[str] | None = None,
    valores_override: dict | None = None, link_override: str | None = None,
) -> str:
    mensagem = _campanha_mensagem_base_promocional(campanha).replace("{nome}", (getattr(pessoa, "nome", "") or "").strip())
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


def _ids_fechados_campanha(db: Session, campanha: Campanha) -> set[int]:
    """Retorna IDs de clientes/contatos que já fecharam desde o início da campanha."""
    inicio = campanha.iniciado_em or campanha.criado_em
    if (campanha.lista_tipo or "ATUALIZACAO").upper() == "ALUGUEL":
        q = db.query(CampanhaAluguelContato.id).filter(CampanhaAluguelContato.ultimo_aluguel_em.isnot(None))
        if inicio:
            q = q.filter(CampanhaAluguelContato.ultimo_aluguel_em >= inicio.date())
        return {int(x[0]) for x in q.all()}

    q = db.query(AtualizacaoCompra.cliente_id).filter(AtualizacaoCompra.status.in_(("PAGO", "A_PAGAR")))
    if inicio:
        q = q.filter(AtualizacaoCompra.criado_em >= inicio)
    return {int(x[0]) for x in q.distinct().all() if x[0] is not None}


def _quantidade_nao_fechados_campanha(db: Session, campanha: Campanha) -> int:
    modelo = _modelo_destinatario_campanha(campanha)
    pessoa_id_attr = "contato_id" if (campanha.lista_tipo or "").upper() == "ALUGUEL" else "cliente_id"
    fechados = _ids_fechados_campanha(db, campanha)
    destinos = db.query(modelo).filter(
        modelo.campanha_id == campanha.id,
        ~modelo.status.in_(["IGNORADO"]),
    ).all()
    return sum(1 for d in destinos if int(getattr(d, pessoa_id_attr) or 0) not in fechados)


def _criar_lotes_reenvio_nao_fechados(db: Session, campanha: Campanha) -> tuple[int, list[int]]:
    """Cria novos lotes somente com quem participou e ainda não fechou.

    Preserva os lotes anteriores como histórico e regenera a mensagem pronta com
    o novo aviso/prazo antes do reenvio.
    """
    lista_tipo = (campanha.lista_tipo or "ATUALIZACAO").upper()
    modelo = _modelo_destinatario_campanha(campanha)
    pessoa_id_attr = "contato_id" if lista_tipo == "ALUGUEL" else "cliente_id"
    rel_attr = "contato" if lista_tipo == "ALUGUEL" else "cliente"
    fechados = _ids_fechados_campanha(db, campanha)
    destinos = db.query(modelo).filter(
        modelo.campanha_id == campanha.id,
        ~modelo.status.in_(["IGNORADO"]),
    ).order_by(modelo.id.asc()).all()

    candidatos = [d for d in destinos if int(getattr(d, pessoa_id_attr) or 0) not in fechados]
    if not candidatos:
        return 0, []

    promo_snapshot = _solvoz_atualizacao_promocao_config() if lista_tipo == "ATUALIZACAO" else None
    pacotes_disponiveis = None
    if promo_snapshot is not None:
        pacotes_disponiveis = [str(x).strip() for x in (promo_snapshot.get("pacotes_disponiveis") or []) if str(x).strip()]

    max_lote = int(db.query(func.max(CampanhaLote.numero)).filter(CampanhaLote.campanha_id == campanha.id).scalar() or 0)
    numeros: list[int] = []
    for idx, dest in enumerate(candidatos):
        pessoa = getattr(dest, rel_attr, None)
        if not pessoa:
            continue
        numero = max_lote + 1 + (idx // CAMPANHA_LOTE_TAMANHO)
        if numero not in numeros:
            numeros.append(numero)
        dest.status = "PENDENTE"
        dest.lote_numero = numero
        dest.reservado_por_id = None
        dest.reservado_em = None
        _preparar_snapshot_destinatario(
            campanha, dest, pessoa, promo_snapshot=promo_snapshot, pacotes_disponiveis=pacotes_disponiveis
        )

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
    campanha.finalizado_em = None
    db.commit()
    return len(candidatos), numeros


@app.post("/organiza/campanhas/{campanha_id}/reenvio-nao-fechados")
def campanha_reenvio_nao_fechados(campanha_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    campanha = db.query(Campanha).filter(Campanha.id == campanha_id).first()
    if not campanha:
        raise HTTPException(404)
    total, numeros = _criar_lotes_reenvio_nao_fechados(db, campanha)
    if not total:
        return RedirectResponse(f"/organiza/campanhas/{campanha.id}?reenvio=nenhum", status_code=303)
    lotes_txt = ",".join(str(n) for n in numeros)
    return RedirectResponse(f"/organiza/campanhas/{campanha.id}?reenvio={total}&lotes_reenvio={lotes_txt}", status_code=303)


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


@app.get("/api/integracoes/connect/campanha-aluguel/cliente")
def api_connect_campanha_aluguel_cliente(
    request: Request, telefone: str = "", db: Session = Depends(get_db)
):
    """Informa ao Connect se o telefone participou de campanha de aluguel do Organiza."""
    _validar_chave_connect(request)
    normalizado = _normalizar_telefone_csv_aluguel(telefone)
    if not normalizado:
        return {"ok": True, "participou": False, "motivo": "telefone_invalido"}
    _pais, _ddi, _telefone, numero_chave = normalizado
    contato = db.query(CampanhaAluguelContato).filter(
        CampanhaAluguelContato.numero_chave == numero_chave
    ).first()
    if not contato:
        return {"ok": True, "participou": False}
    dest = (
        db.query(CampanhaAluguelDestinatario)
        .join(Campanha, Campanha.id == CampanhaAluguelDestinatario.campanha_id)
        .filter(
            CampanhaAluguelDestinatario.contato_id == contato.id,
            func.upper(Campanha.lista_tipo) == "ALUGUEL",
            ~CampanhaAluguelDestinatario.status.in_(["IGNORADO"]),
        )
        .order_by(
            CampanhaAluguelDestinatario.enviado_em.desc().nullslast(),
            Campanha.iniciado_em.desc().nullslast(),
            Campanha.criado_em.desc(),
            Campanha.id.desc(),
        )
        .first()
    )
    if not dest or not dest.campanha:
        return {"ok": True, "participou": False}
    campanha = dest.campanha
    return {
        "ok": True,
        "participou": True,
        "campanha_id": campanha.id,
        "campanha_nome": campanha.nome,
        "campanha_status": campanha.status,
        "destinatario_status": dest.status,
        "enviado_em": dest.enviado_em.isoformat() if dest.enviado_em else None,
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
    nao_fechados_disponiveis = _quantidade_nao_fechados_campanha(db, campanha) if campanha.status != "RASCUNHO" else 0
    return templates.TemplateResponse("organiza/campanha_detalhe.html", {
        "request": request, "usuario": usuario, "campanha": campanha,
        "rotulo_lista": _rotulo_lista_campanha(campanha),
        "contagens": contagens,
        "total_previsto": total_previsto, "meses_aluguel": MESES_ALUGUEL,
        "lotes": lotes,
        "tamanho_lote": CAMPANHA_LOTE_TAMANHO,
        "nao_enviados_disponiveis": nao_enviados_disponiveis,
        "nao_fechados_disponiveis": nao_fechados_disponiveis,
        "mensagem_preview": _campanha_mensagem_base_promocional(campanha),
        "erro": request.query_params.get("erro", ""),
        "recuperacao": request.query_params.get("recuperacao", ""),
        "lotes_recuperacao": request.query_params.get("lotes_recuperacao", ""),
        "reenvio": request.query_params.get("reenvio", ""),
        "lotes_reenvio": request.query_params.get("lotes_reenvio", ""),
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
    try:
        produto_venda_id = int(form.get("produto_venda_id") or 0)
    except (TypeError, ValueError):
        produto_venda_id = 0
    modelo_venda = db.query(VendaModeloEquipamento).filter(VendaModeloEquipamento.id == produto_venda_id, VendaModeloEquipamento.ativo == 1).first() if produto_venda_id else None
    if modelo_venda:
        eq.produto_venda = modelo_venda
        eq.produto_venda_id = modelo_venda.id
        eq.tipo = tipo_equipamento_padrao(modelo_venda.tipo) or modelo_venda.tipo
        eq.modelo = modelo_venda.nome
    else:
        eq.tipo = tipo_equipamento_padrao((form.get("tipo") or "").strip()) or None
        eq.modelo = (form.get("modelo") or "").strip() or None
    catalogo_form = (form.get("catalogo_venda") or getattr(eq, "catalogo_venda", None) or "BASICO").strip().upper()
    eq.catalogo_venda = "PLUS" if catalogo_form == "PLUS" else "BASICO"
    codigo_padrao_nfae, descricao_padrao_nfae = nfae_padrao_produto(eq.tipo)
    eq.nota_codigo = re.sub(r"[^A-Za-z0-9._-]", "", (form.get("nota_codigo") or "").strip()) or codigo_padrao_nfae
    eq.nota_descricao = (form.get("nota_descricao") or "").strip() or descricao_padrao_nfae
    # Fliperama não utiliza pacote de músicas. Para os demais equipamentos o pacote continua obrigatório.
    if tipo_equipamento_padrao(eq.tipo or "") == "FLIPERAMA":
        eq.pacote = "NA"
        eq.falta_pacote = 0
    else:
        pacote_informado = _normalizar_pacote_cadastrado(form.get("pacote"))
        pacote_existente = _normalizar_pacote_cadastrado(eq.pacote)
        eq.pacote = pacote_informado or pacote_existente or _primeiro_pacote_disponivel(db)
        eq.falta_pacote = calcular_falta_pacote(eq.pacote, obter_pacote_atual(db))
    if modelo_venda:
        eq.plano = "PLUS" if eq.catalogo_venda == "PLUS" else "BÁSICO"
    else:
        eq.plano = (form.get("plano") or "").strip() or None
    try:
        solvoz_empresa_id = int(form.get("solvoz_empresa_id") or 0)
    except (TypeError, ValueError):
        solvoz_empresa_id = 0
    # Regra de integridade: se o cliente é o responsável de uma Empresa SolVoz,
    # todos os equipamentos ativos dele pertencem à mesma empresa. O formulário
    # não pode gravar outra empresa por engano.
    empresa_responsavel = None
    if int(getattr(eq, "cliente_id", 0) or 0):
        empresa_responsavel = db.query(SolVozEmpresa).filter(
            SolVozEmpresa.responsavel_cliente_id == int(eq.cliente_id),
            SolVozEmpresa.ativo == 1,
        ).order_by(SolVozEmpresa.id).first()
    eq.solvoz_empresa_id = int(empresa_responsavel.id) if empresa_responsavel else (solvoz_empresa_id or None)
    eq.catalogo_online = 1 if str(form.get("catalogo_online") or "").strip().lower() in ("1", "true", "on", "sim") else 0
    # Em edição de venda, o checkbox é a fonte explícita. Novos/legados mantêm o padrão 1.
    if "descontar_estoque" in form or "descontar_estoque_presente" in form:
        eq.descontar_estoque = 1 if str(form.get("descontar_estoque") or "").strip().lower() in ("1", "true", "on", "sim") else 0

    # Opcionais operacionais da venda. São salvos no próprio equipamento para
    # acompanhar a configuração entregue ao cliente e aparecer no card principal.
    def opcao(valor, permitidos, padrao):
        texto = (valor or "").strip()
        return texto if texto in permitidos else padrao

    # Som deixou de ser Opcional: Premium/JBL pertence ao próprio modelo comercial.
    if modelo_venda:
        eq.som = "NA"
    # Salva qualquer categoria criada em Opcionais. Categorias desabilitadas para o modelo voltam ao padrão e não movimentam estoque.
    dinamicos = _opcionais_dinamicos(eq)
    configs_ativos = db.query(VendaOpcionalConfig).filter(VendaOpcionalConfig.ativo == 1).order_by(VendaOpcionalConfig.ordem).all()
    por_campo = {}
    for cfg in configs_ativos:
        por_campo.setdefault(cfg.campo, []).append(cfg)
    for campo, opcoes_cfg in por_campo.items():
        padrao_cfg = next((c for c in opcoes_cfg if c.padrao), opcoes_cfg[0] if opcoes_cfg else None)
        padrao_valor = padrao_cfg.valor if padrao_cfg else "NA"
        permitidos = {c.valor for c in opcoes_cfg}
        valor_form = (form.get(f"opcional__{campo}") if f"opcional__{campo}" in form else form.get(campo))
        valor_atual = _valor_opcional_equipamento(eq, campo, db)
        valor = (valor_form or valor_atual or padrao_valor).strip()
        if valor not in permitidos:
            valor = padrao_valor
        if modelo_venda and not _opcional_habilitado_modelo(db, modelo_venda.id, campo):
            valor = padrao_valor
        if hasattr(eq, campo):
            setattr(eq, campo, valor)
        else:
            dinamicos[campo] = valor
    eq.opcionais_json = json.dumps(dinamicos, ensure_ascii=False, sort_keys=True) if dinamicos else None

    # Preço bruto, cupom e desconto manual. `valor` passa a ser sempre o total final.
    # Em vendas já finalizadas, o preço histórico permanece congelado junto com o snapshot.
    if eq.custo_final_snapshot is None:
        preco_bruto = moeda_num(form.get("preco_venda") or form.get("valor") or eq.preco_venda or eq.valor)
        if modelo_venda and preco_bruto <= 0:
            preco_bruto = float(modelo_venda.preco_basico or 0) + (PLUS_ACRESCIMO if eq.catalogo_venda == "PLUS" else 0)

        try:
            cupom_id_form = int(form.get("cupom_id") or 0) if "cupom_id" in form else int(eq.cupom_id or 0)
        except (TypeError, ValueError):
            cupom_id_form = 0
        cupom = None
        if cupom_id_form:
            cupom = db.query(VendaCupom).filter(VendaCupom.id == cupom_id_form).first()
            if cupom and not cupom.ativo and int(eq.cupom_id or 0) != int(cupom.id):
                cupom = None
        eq.cupom_id = cupom.id if cupom else None

        desconto_manual = moeda_num(form.get("desconto_manual")) if "desconto_manual" in form else float(eq.desconto_manual or 0)
        desconto_manual = max(float(desconto_manual or 0), 0)
        desconto_cupom = calcular_desconto_cupom(cupom, preco_bruto)
        desconto_total = min(preco_bruto, desconto_manual + desconto_cupom)
        subtotal_produto = max(round(preco_bruto - desconto_total, 2), 0)
        frete_venda = moeda_num(form.get("frete_venda")) if "frete_venda" in form else float(eq.frete_venda or 0)
        frete_venda = max(float(frete_venda or 0), 0)
        total_final = round(subtotal_produto + frete_venda, 2)

        eq.preco_venda = f"{preco_bruto:.2f}" if preco_bruto or modelo_venda else None
        eq.desconto_manual = round(min(desconto_manual, preco_bruto), 2)
        eq.frete_venda = round(frete_venda, 2)
        eq.cupom_codigo_snapshot = cupom.codigo if cupom else None
        eq.cupom_desconto_snapshot = round(min(desconto_cupom, max(preco_bruto - eq.desconto_manual, 0)), 2)
        # Cupom/desconto incidem apenas no equipamento. O frete é somado depois.
        eq.valor = f"{total_final:.2f}" if (eq.preco_venda is not None or frete_venda > 0) else None

    # Para modelos vinculados, custo é sempre calculado pelos Itens; custo manual só permanece no legado.
    if not modelo_venda:
        eq.preco_custo = (form.get("preco_custo") or "").strip() or eq.preco_custo
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

    if modelo_venda:
        resumo_atual = resumo_custo_venda(eq, db, usar_snapshot=False)
        eq.preco_custo = f"{resumo_atual['custo']:.2f}"
    congelar_custo_venda_se_finalizada(eq, db)


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


@app.post("/organiza/clientes/{cliente_id}/equipamentos/atualizar-pacote")
async def cliente_atualizar_pacote_equipamentos(
    cliente_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(404)
    form = await request.form()
    pacote = _normalizar_pacote_cadastrado(form.get("pacote"))
    if not pacote:
        return RedirectResponse(
            f"/organiza/clientes/{cliente_id}?atualizacao_erro={quote_plus('Selecione um pacote válido.')}#equipamentos-cliente",
            status_code=303,
        )

    equipamentos = db.query(Equipamento).filter(Equipamento.cliente_id == cliente_id).all()
    if not equipamentos:
        return RedirectResponse(
            f"/organiza/clientes/{cliente_id}?atualizacao_erro={quote_plus('Este cliente não possui equipamentos cadastrados.')}#equipamentos-cliente",
            status_code=303,
        )

    pacote_atual = obter_pacote_atual(db)
    atualizados = 0
    fliperamas = 0
    for eq in equipamentos:
        if tipo_equipamento_padrao(eq.tipo or "") == "FLIPERAMA":
            eq.pacote = "NA"
            eq.falta_pacote = 0
            fliperamas += 1
            continue
        eq.pacote = pacote
        eq.falta_pacote = calcular_falta_pacote(pacote, pacote_atual)
        atualizados += 1

    cliente.pacote = pacote if atualizados else "NA"
    cliente.falta_pacote = calcular_falta_pacote(pacote, pacote_atual) if atualizados else 0
    db.commit()

    msg = f"Pacote do cliente atualizado para {pacote} em {atualizados} equipamento(s)."
    if fliperamas:
        msg += f" {fliperamas} Fliperama(s) permaneceram como NA."
    return RedirectResponse(
        f"/organiza/clientes/{cliente_id}?atualizacao_sucesso={quote_plus(msg)}#equipamentos-cliente",
        status_code=303,
    )


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
        **contexto_configuracao_venda(db),
    })


@app.post("/organiza/clientes/{cliente_id}/equipamentos/novo")
async def equipamento_criar(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    form = dict(await request.form())
    if not (form.get("tipo") or "").strip() and not (form.get("produto_venda_id") or "").strip():
        eq = Equipamento(cliente_id=cliente_id); preencher_equipamento(eq, form, db)
        tipos, pacotes = opcoes_equipamentos(db)
        return templates.TemplateResponse("organiza/equipamento_form.html", {
            "request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq,
            "erro": "Informe o tipo do equipamento.", "tipos": tipos, "pacotes": pacotes, "primeiro_pacote": _primeiro_pacote_disponivel(db),
            "proxima_maquina": proximo_codigo_maquina(db),
            "proximo_numero_cliente": proximo_numero_cliente(db, cliente_id),
            "solvoz_empresas": empresas_solvoz_ativas(db),
            **contexto_configuracao_venda(db, eq),
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
            **contexto_configuracao_venda(db, eq),
        }, status_code=400)
    db.add(eq)
    db.flush()
    salvar_estoque_utilizado_venda(eq, form, db)
    salvar_cores_venda(eq, form, db)
    sincronizar_estoque_venda(eq, db)
    _sincronizar_pacote_cliente(db, cliente_id)
    db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente_id}", status_code=303)


@app.get("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/editar", response_class=HTMLResponse)
def equipamento_editar(cliente_id: int, equipamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id, Equipamento.cliente_id == cliente_id).first()
    if not cliente or not eq: raise HTTPException(404)
    retorno = (request.query_params.get("retorno") or f"/organiza/clientes/{cliente_id}").strip()
    if not (retorno.startswith("/organiza/vendas") or retorno.startswith(f"/organiza/clientes/{cliente_id}")):
        retorno = f"/organiza/clientes/{cliente_id}"
    tipos, pacotes = opcoes_equipamentos(db)
    clientes_transferencia = db.query(Cliente).filter(Cliente.id != cliente_id).order_by(Cliente.nome.asc()).all()
    transferencias = db.query(TransferenciaEquipamento).filter(TransferenciaEquipamento.equipamento_id == equipamento_id).order_by(TransferenciaEquipamento.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/equipamento_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq, "erro": "", "tipos": tipos, "pacotes": pacotes, "primeiro_pacote": _primeiro_pacote_disponivel(db), "clientes_transferencia": clientes_transferencia, "transferencias": transferencias, "solvoz_empresas": empresas_solvoz_ativas(db), "retorno": retorno, **contexto_configuracao_venda(db, eq)})



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
    retorno = (form.get("retorno") or f"/organiza/clientes/{cliente_id}").strip()
    if not (retorno.startswith("/organiza/vendas") or retorno.startswith(f"/organiza/clientes/{cliente_id}")):
        retorno = f"/organiza/clientes/{cliente_id}"
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
            "solvoz_empresas": empresas_solvoz_ativas(db), "retorno": retorno,
            **contexto_configuracao_venda(db, eq)
        }, status_code=400)
    db.flush()
    salvar_estoque_utilizado_venda(eq, form, db)
    salvar_cores_venda(eq, form, db)
    sincronizar_estoque_venda(eq, db)
    _sincronizar_pacote_cliente(db, cliente_id)
    db.commit()
    return RedirectResponse(retorno, status_code=303)



# ---------------------------------------------------------
# EMPRESAS SOLVOZ
# SolVoz é a fonte mestre de empresa/nome/slug/status. O Organiza mantém o
# vínculo operacional com responsável, Humiat ID e equipamentos.
# ---------------------------------------------------------

def _logo_mini_lokafest(data: bytes, mime: str = "image/png") -> tuple[bytes, str]:
    """Normaliza a logo do SolVoz para uma miniatura leve com transparência."""
    from PIL import Image

    if not data:
        raise ValueError("Logo vazia")
    with Image.open(io.BytesIO(data)) as img:
        try:
            img.seek(0)
        except Exception:
            pass
        # Mantém transparência quando existir; logos sem alpha usam RGB.
        tem_alpha = img.mode in {"RGBA", "LA"} or (img.mode == "P" and "transparency" in img.info)
        img = img.convert("RGBA" if tem_alpha else "RGB")
        img.thumbnail((260, 120), Image.Resampling.LANCZOS)
        saida = io.BytesIO()
        img.save(saida, format="WEBP", quality=84, method=6)
        return saida.getvalue(), "image/webp"


def _baixar_logo_empresa_solvoz(slug: str) -> tuple[bytes, str] | None:
    """Busca a logo pública do SolVoz somente durante rotinas de sincronização."""
    slug_n = normalizar_slug_solvoz(slug)
    if not slug_n:
        return None
    url = f"{SOLVOZ_BASE_URL.rstrip('/')}/_sv/media/empresa/{quote(slug_n)}/logo"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "image/webp,image/png,image/jpeg,image/*;q=0.8",
            "User-Agent": f"Organiza/{ORGANIZA_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=max(2, min(int(SOLVOZ_API_TIMEOUT or 8), 10))) as resp:
            mime = str(resp.headers.get("Content-Type") or "image/png").split(";", 1)[0].strip().lower()
            if not mime.startswith("image/"):
                raise RuntimeError("SolVoz retornou um arquivo que não é imagem")
            data = resp.read(5 * 1024 * 1024 + 1)
            if len(data) > 5 * 1024 * 1024:
                raise RuntimeError("Logo do SolVoz maior que 5 MB")
            if not data:
                return None
            return data, mime
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"SolVoz respondeu HTTP {exc.code} ao buscar a logo") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Não foi possível buscar a logo no SolVoz: {exc.reason}") from exc


def _sincronizar_logo_empresa_solvoz(db: Session, empresa: SolVozEmpresa) -> tuple[bool, str]:
    """Copia uma miniatura da logo do SolVoz para o cadastro local da empresa."""
    origem = _baixar_logo_empresa_solvoz(empresa.slug)
    if not origem:
        tinha = bool(empresa.logo_mini_data)
        if tinha:
            empresa.logo_mini_data = None
            empresa.logo_mini_mime = None
            empresa.logo_mini_hash = None
            empresa.logo_mini_atualizado_em = datetime.now()
            db.flush()
        return tinha, "sem_logo"

    mini, mime = _logo_mini_lokafest(origem[0], origem[1])
    digest = hashlib.sha256(mini).hexdigest()
    if digest == str(empresa.logo_mini_hash or "") and empresa.logo_mini_data:
        return False, "igual"
    empresa.logo_mini_data = mini
    empresa.logo_mini_mime = mime
    empresa.logo_mini_hash = digest
    empresa.logo_mini_atualizado_em = datetime.now()
    db.flush()
    return True, "atualizada"


def _sincronizar_humiat_empresa_solvoz(db: Session, empresa: SolVozEmpresa, old_slug: str = "") -> HumiatEmpresa:
    """Mantém a mesma empresa Humiat quando nome/slug mudam no SolVoz."""
    slug_n = normalizar_slug_solvoz(empresa.slug)
    old_n = normalizar_slug_solvoz(old_slug)
    atual = db.query(HumiatEmpresa).filter(func.lower(HumiatEmpresa.slug) == slug_n).first()
    antiga = None
    if old_n and old_n != slug_n:
        antiga = db.query(HumiatEmpresa).filter(func.lower(HumiatEmpresa.slug) == old_n).first()
    if antiga and atual and int(antiga.id) != int(atual.id):
        # Mescla vínculos/produtos antigos para evitar empresa Humiat duplicada.
        for v in db.query(HumiatUsuarioEmpresa).filter(HumiatUsuarioEmpresa.empresa_id == antiga.id).all():
            existe = db.query(HumiatUsuarioEmpresa).filter(
                HumiatUsuarioEmpresa.usuario_id == v.usuario_id,
                HumiatUsuarioEmpresa.empresa_id == atual.id,
            ).first()
            if not existe:
                db.add(HumiatUsuarioEmpresa(usuario_id=v.usuario_id, empresa_id=atual.id))
            db.delete(v)
        for ep in db.query(HumiatEmpresaProduto).filter(HumiatEmpresaProduto.empresa_id == antiga.id).all():
            existe = db.query(HumiatEmpresaProduto).filter(
                HumiatEmpresaProduto.empresa_id == atual.id,
                HumiatEmpresaProduto.produto_id == ep.produto_id,
            ).first()
            if existe:
                existe.ativo = max(int(existe.ativo or 0), int(ep.ativo or 0))
            else:
                db.add(HumiatEmpresaProduto(empresa_id=atual.id, produto_id=ep.produto_id, ativo=ep.ativo))
            db.delete(ep)
        db.delete(antiga)
    elif antiga and not atual:
        antiga.slug = slug_n
        antiga.nome = empresa.nome
        antiga.ativo = int(empresa.ativo or 0)
        atual = antiga
    if not atual:
        atual = garantir_empresa_solvoz_humiat(db, empresa.nome, slug_n, ativo=int(empresa.ativo or 0))
    else:
        atual.nome = empresa.nome
        atual.ativo = int(empresa.ativo or 0)
    return atual


def _solvoz_empresa_upsert_origem(
    db: Session, *, solvoz_id: int | None, nome: str, slug: str, ativo: int | bool = 1
) -> tuple[SolVozEmpresa, bool, bool]:
    """Upsert idempotente vindo do SolVoz usando ID estável e slug como fallback."""
    nome_n = (nome or "").strip()
    slug_n = normalizar_slug_solvoz(slug or nome_n)
    sid = int(solvoz_id or 0) or None
    ativo_i = 1 if bool(int(ativo)) else 0
    if not nome_n or not slug_n:
        raise ValueError("Nome e slug da empresa SolVoz são obrigatórios.")

    empresa = None
    if sid:
        empresa = db.query(SolVozEmpresa).filter(SolVozEmpresa.solvoz_id == sid).first()
    por_slug = db.query(SolVozEmpresa).filter(func.lower(SolVozEmpresa.slug) == slug_n).first()
    if empresa and por_slug and int(empresa.id) != int(por_slug.id):
        # Registro legado duplicado: preserva o registro do ID estável e move os vínculos.
        db.query(Equipamento).filter(Equipamento.solvoz_empresa_id == por_slug.id).update(
            {Equipamento.solvoz_empresa_id: empresa.id}, synchronize_session=False
        )
        if not empresa.responsavel_cliente_id and por_slug.responsavel_cliente_id:
            empresa.responsavel_cliente_id = por_slug.responsavel_cliente_id
        if not empresa.responsavel_humiat_usuario_id and por_slug.responsavel_humiat_usuario_id:
            empresa.responsavel_humiat_usuario_id = por_slug.responsavel_humiat_usuario_id
        db.delete(por_slug)
        por_slug = None
    if not empresa:
        empresa = por_slug

    criada = empresa is None
    alterada = False
    if criada:
        empresa = SolVozEmpresa(
            solvoz_id=sid,
            nome=nome_n,
            slug=slug_n,
            connect_slug=("vivioke" if slug_n == "vivikaraoke" else slug_n),
            dominio=dominio_solvoz_por_slug(slug_n),
            ativo=ativo_i,
        )
        db.add(empresa)
        db.flush()
        old_slug = ""
        alterada = True
    else:
        old_slug = str(empresa.slug or "")
        old_connect = normalizar_slug_solvoz(empresa.connect_slug or old_slug)
        if sid and int(empresa.solvoz_id or 0) != sid:
            empresa.solvoz_id = sid
            alterada = True
        if empresa.nome != nome_n:
            empresa.nome = nome_n
            alterada = True
        if normalizar_slug_solvoz(empresa.slug) != slug_n:
            empresa.slug = slug_n
            # Se o Connect seguia o slug global, acompanha a alteração. Alias legado é preservado.
            if not old_connect or old_connect == normalizar_slug_solvoz(old_slug):
                empresa.connect_slug = "vivioke" if slug_n == "vivikaraoke" else slug_n
            alterada = True
        novo_dominio = dominio_solvoz_por_slug(slug_n)
        if empresa.dominio != novo_dominio:
            empresa.dominio = novo_dominio
            alterada = True
        if int(empresa.ativo or 0) != ativo_i:
            empresa.ativo = ativo_i
            alterada = True
        if not (empresa.connect_slug or "").strip():
            empresa.connect_slug = "vivioke" if slug_n == "vivikaraoke" else slug_n
            alterada = True
    _sincronizar_humiat_empresa_solvoz(db, empresa, old_slug=old_slug)
    return empresa, criada, alterada


def _humiat_vincular_responsavel_solvoz(db: Session, empresa: SolVozEmpresa, cliente: Cliente) -> tuple[HumiatUsuario, bool]:
    """Garante Humiat ID somente para o responsável real da empresa SolVoz."""
    usuario_h, criado, interno = _humiat_garantir_usuario_cliente(cliente, db)
    if interno:
        raise ValueError("Cliente externo não pode reutilizar um Humiat ID da equipe interna.")

    outra = db.query(SolVozEmpresa).filter(
        SolVozEmpresa.responsavel_humiat_usuario_id == int(usuario_h.id),
        SolVozEmpresa.id != int(empresa.id),
    ).first()
    if outra:
        raise ValueError(f"Este Humiat ID já é responsável pela empresa {outra.nome}.")

    empresa_h = garantir_empresa_solvoz_humiat(db, empresa.nome, empresa.slug, ativo=int(empresa.ativo or 0))
    # Cliente empresa trabalha em uma única empresa no Humiat ID.
    db.query(HumiatUsuarioEmpresa).filter(HumiatUsuarioEmpresa.usuario_id == int(usuario_h.id)).delete(synchronize_session=False)
    db.add(HumiatUsuarioEmpresa(usuario_id=int(usuario_h.id), empresa_id=int(empresa_h.id)))

    produto = db.query(HumiatProduto).filter(HumiatProduto.codigo == "SOLVOZ").first()
    if produto:
        atual = permissoes_usuario_humiat(db, int(usuario_h.id), "SOLVOZ")
        salvar_permissoes_usuario_humiat(
            db, int(usuario_h.id), produto,
            sistema=bool(atual.get("sistema")), adm=bool(atual.get("adm")),
            cliente_site=bool(atual.get("solvoz_comprado")), cliente_catalogo=True,
        )
    empresa.responsavel_humiat_usuario_id = int(usuario_h.id)
    return usuario_h, criado


def _solvoz_empresas_contexto_batched(db: Session, empresas: list[SolVozEmpresa]) -> list[dict]:
    """Monta a tela em lote; elimina o N+1 que fazia a rota chegar a ~13s."""
    if not empresas:
        return []
    ids = [int(e.id) for e in empresas]
    clientes_por_empresa: dict[int, list[Cliente]] = {i: [] for i in ids}
    vistos: dict[int, set[int]] = {i: set() for i in ids}
    rows = (
        db.query(Equipamento.solvoz_empresa_id, Cliente)
        .join(Cliente, Cliente.id == Equipamento.cliente_id)
        .filter(Equipamento.solvoz_empresa_id.in_(ids))
        .order_by(Cliente.nome.asc())
        .all()
    )
    for empresa_id, cliente in rows:
        eid, cid = int(empresa_id or 0), int(cliente.id)
        if eid in vistos and cid not in vistos[eid]:
            vistos[eid].add(cid)
            clientes_por_empresa[eid].append(cliente)

    resp_client_ids = {int(e.responsavel_cliente_id) for e in empresas if e.responsavel_cliente_id}
    resp_h_ids = {int(e.responsavel_humiat_usuario_id) for e in empresas if e.responsavel_humiat_usuario_id}
    clientes_resp = {int(c.id): c for c in db.query(Cliente).filter(Cliente.id.in_(resp_client_ids)).all()} if resp_client_ids else {}
    humiat_resp = {int(u.id): u for u in db.query(HumiatUsuario).filter(HumiatUsuario.id.in_(resp_h_ids)).all()} if resp_h_ids else {}

    equipamentos_por_cliente: dict[int, list[tuple[int, int | None]]] = {cid: [] for cid in resp_client_ids}
    if resp_client_ids:
        eq_rows = db.query(Equipamento.id, Equipamento.cliente_id, Equipamento.solvoz_empresa_id, Equipamento.status).filter(
            Equipamento.cliente_id.in_(resp_client_ids)
        ).all()
        for eqid, cid, seid, status in eq_rows:
            if str(status or "").strip().upper() == "INATIVO":
                continue
            equipamentos_por_cliente.setdefault(int(cid), []).append((int(eqid), int(seid) if seid else None))

    linhas = []
    for empresa in empresas:
        cliente_resp = clientes_resp.get(int(empresa.responsavel_cliente_id or 0))
        usuario_resp = humiat_resp.get(int(empresa.responsavel_humiat_usuario_id or 0))
        eqs = equipamentos_por_cliente.get(int(empresa.responsavel_cliente_id or 0), []) if cliente_resp else []
        divergentes = [eqid for eqid, seid in eqs if seid != int(empresa.id)]
        clientes_detectados = clientes_por_empresa.get(int(empresa.id), [])
        responsavel_sugerido = clientes_detectados[0] if (not cliente_resp and not usuario_resp and len(clientes_detectados) == 1) else None
        linhas.append({
            "empresa": empresa,
            "clientes": clientes_detectados,
            "responsavel_cliente": cliente_resp,
            "responsavel_humiat": usuario_resp,
            "responsavel_sugerido": responsavel_sugerido,
            "equipamentos_divergentes": divergentes,
            "equipamentos_responsavel": len(eqs),
            "responsavel_ok": bool(usuario_resp),
        })
    return linhas


@app.get("/organiza/configuracoes/solvoz-empresas", response_class=HTMLResponse)
def solvoz_empresas_lista(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not usuario.is_admin:
        raise HTTPException(403)
    empresas = db.query(SolVozEmpresa).order_by(SolVozEmpresa.nome.asc()).all()
    linhas_empresas = _solvoz_empresas_contexto_batched(db, empresas)
    editar = None
    responsavel_editar = None
    try:
        editar_id = int(request.query_params.get("editar") or 0)
    except (TypeError, ValueError):
        editar_id = 0
    try:
        responsavel_id = int(request.query_params.get("responsavel") or 0)
    except (TypeError, ValueError):
        responsavel_id = 0
    if editar_id:
        editar = db.query(SolVozEmpresa).filter(SolVozEmpresa.id == editar_id).first()
    candidatos_clientes = []
    candidatos_internos = []
    if responsavel_id:
        responsavel_editar = db.query(SolVozEmpresa).filter(SolVozEmpresa.id == responsavel_id).first()
        if responsavel_editar:
            if normalizar_slug_solvoz(responsavel_editar.slug) == "karaokerj":
                todos = db.query(HumiatUsuario).filter(HumiatUsuario.ativo == 1).order_by(HumiatUsuario.nome.asc()).all()
                candidatos_internos = [u for u in todos if usuario_humiat_equipe_prioritaria(u)]
            else:
                candidatos_clientes = db.query(Cliente).order_by(Cliente.nome.asc()).all()
    return templates.TemplateResponse("organiza/solvoz_empresas.html", {
        "request": request,
        "usuario": usuario,
        "empresas": empresas,
        "linhas_empresas": linhas_empresas,
        "editar": editar,
        "responsavel_editar": responsavel_editar,
        "candidatos_clientes": candidatos_clientes,
        "candidatos_internos": candidatos_internos,
        "erro": request.query_params.get("erro", ""),
        "sucesso": request.query_params.get("sucesso", ""),
    })


@app.post("/organiza/configuracoes/solvoz-empresas/{empresa_id}/editar")
async def solvoz_empresa_editar(
    empresa_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """No Organiza só o alias legado do Connect é editável; o resto vem do SolVoz."""
    if not usuario.is_admin:
        raise HTTPException(403)
    empresa = db.query(SolVozEmpresa).filter(SolVozEmpresa.id == empresa_id).first()
    if not empresa:
        raise HTTPException(404)
    form = dict(await request.form())
    slug = normalizar_slug_solvoz(empresa.slug)
    connect_slug = normalizar_slug_solvoz(form.get("connect_slug") or slug)
    if not connect_slug:
        return RedirectResponse(f"/organiza/configuracoes/solvoz-empresas?editar={empresa_id}&erro=Informe+o+slug+do+Connect", status_code=303)
    existente_alias = db.query(SolVozEmpresa).filter(
        SolVozEmpresa.connect_slug == connect_slug, SolVozEmpresa.id != empresa_id
    ).first()
    if existente_alias:
        return RedirectResponse(f"/organiza/configuracoes/solvoz-empresas?editar={empresa_id}&erro=Este+slug+do+Connect+já+está+vinculado+a+outra+empresa", status_code=303)
    empresa.connect_slug = connect_slug
    db.commit()
    return RedirectResponse("/organiza/configuracoes/solvoz-empresas?sucesso=Alias+do+Connect+atualizado", status_code=303)


@app.post("/organiza/configuracoes/solvoz-empresas/sincronizar")
def solvoz_empresas_sincronizar_manual(
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Reconciliação manual: só consulta o SolVoz quando o administrador clicar."""
    if not usuario.is_admin:
        raise HTTPException(403)
    try:
        dados = _solvoz_api_request("/_sv/api/organiza/empresas")
        itens = dados.get("empresas") or []
        criadas = atualizadas = inativadas_ausentes = 0
        temas_sincronizados = temas_erro = 0
        logos_sincronizadas = logos_erro = 0
        ids_origem: set[int] = set()
        slugs_origem: set[str] = set()
        for item in itens:
            sid = int(item.get("id") or 0) or None
            slug_item = normalizar_slug_solvoz(str(item.get("slug") or ""))
            if sid:
                ids_origem.add(int(sid))
            if slug_item:
                slugs_origem.add(slug_item)
            empresa_local, criada, alterada = _solvoz_empresa_upsert_origem(
                db,
                solvoz_id=sid,
                nome=str(item.get("nome") or "").strip(),
                slug=slug_item,
                ativo=int(item.get("ativo") or 0),
            )
            criadas += int(criada)
            atualizadas += int(alterada and not criada)
            if slug_item:
                try:
                    _sincronizar_tema_visual_solvoz(db, slug_item)
                    temas_sincronizados += 1
                except Exception as exc_tema:
                    temas_erro += 1
                    print(f"[TEMA HUMIAT] Não foi possível sincronizar {slug_item}: {exc_tema}")
                try:
                    mudou_logo, _ = _sincronizar_logo_empresa_solvoz(db, empresa_local)
                    logos_sincronizadas += int(mudou_logo)
                except Exception as exc_logo:
                    logos_erro += 1
                    print(f"[LOGO LOKAFEST] Não foi possível sincronizar {slug_item}: {exc_logo}")
        # Não apaga histórico local. Registros que não existem mais na lista mestre
        # ficam inativos até revisão manual.
        for local in db.query(SolVozEmpresa).all():
            existe_origem = (int(local.solvoz_id or 0) in ids_origem) if local.solvoz_id else (normalizar_slug_solvoz(local.slug) in slugs_origem)
            if not existe_origem and int(local.ativo or 0):
                local.ativo = 0
                _sincronizar_humiat_empresa_solvoz(db, local, old_slug=local.slug)
                inativadas_ausentes += 1
        db.commit()
        _carregar_tema_visual_runtime(db)
        msg = quote_plus(
            f"Sincronização concluída: {len(itens)} empresa(s), {criadas} nova(s), {atualizadas} atualizada(s), "
            f"{inativadas_ausentes} ausente(s) inativada(s), {temas_sincronizados} identidade(s) visual(is) sincronizada(s), "
            f"{logos_sincronizadas} logo(s) LokaFest atualizada(s)"
            + (f", {temas_erro} com falha de tema" if temas_erro else "")
            + (f", {logos_erro} com falha de logo." if logos_erro else ".")
        )
        return RedirectResponse(f"/organiza/configuracoes/solvoz-empresas?sucesso={msg}", status_code=303)
    except Exception as exc:
        db.rollback()
        return RedirectResponse(f"/organiza/configuracoes/solvoz-empresas?erro={quote_plus(str(exc)[:300])}", status_code=303)


@app.post("/organiza/configuracoes/solvoz-empresas/copiar-responsaveis-equipamentos")
def solvoz_empresas_copiar_responsaveis_equipamentos(
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Preenche em lote o responsável quando a empresa tem um único cliente por equipamentos.

    A ação não cria Humiat ID nem envia e-mail: ela corrige somente o vínculo
    obrigatório Empresa SolVoz -> Cliente. O onboarding fica para "Usuários novos".
    """
    if not usuario.is_admin:
        raise HTTPException(403)

    empresas = db.query(SolVozEmpresa).filter(
        SolVozEmpresa.ativo == 1,
        SolVozEmpresa.responsavel_cliente_id.is_(None),
        SolVozEmpresa.responsavel_humiat_usuario_id.is_(None),
    ).all()
    corrigidas = ambiguas = sem_cliente = conflitos = 0
    for empresa in empresas:
        if normalizar_slug_solvoz(empresa.slug) == "karaokerj":
            continue
        rows = db.query(Equipamento.cliente_id).filter(
            Equipamento.solvoz_empresa_id == int(empresa.id),
            func.upper(func.coalesce(Equipamento.status, "")) != "INATIVO",
        ).distinct().all()
        cliente_ids = sorted({int(r[0]) for r in rows if r and r[0]})
        if not cliente_ids:
            sem_cliente += 1
            continue
        if len(cliente_ids) != 1:
            ambiguas += 1
            continue
        cid = cliente_ids[0]
        outra = db.query(SolVozEmpresa).filter(
            SolVozEmpresa.responsavel_cliente_id == cid,
            SolVozEmpresa.id != int(empresa.id),
            SolVozEmpresa.ativo == 1,
        ).first()
        if outra:
            conflitos += 1
            continue
        empresa.responsavel_cliente_id = cid
        empresa.responsavel_humiat_usuario_id = None
        corrigidas += 1

    db.commit()
    msg = (
        f"Responsáveis copiados: {corrigidas}. "
        f"Sem cliente pelos equipamentos: {sem_cliente}. "
        f"Com mais de um cliente: {ambiguas}. Conflitos: {conflitos}."
    )
    return RedirectResponse(
        f"/organiza/configuracoes/solvoz-empresas?sucesso={quote_plus(msg)}",
        status_code=303,
    )


@app.post("/organiza/configuracoes/solvoz-empresas/{empresa_id}/responsavel")
async def solvoz_empresa_definir_responsavel(
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
    ref = str(form.get("responsavel_ref") or "").strip()
    if ":" not in ref:
        return RedirectResponse(f"/organiza/configuracoes/solvoz-empresas?responsavel={empresa_id}&erro=Selecione+o+responsável", status_code=303)
    tipo, raw_id = ref.split(":", 1)
    if not raw_id.isdigit():
        return RedirectResponse(f"/organiza/configuracoes/solvoz-empresas?responsavel={empresa_id}&erro=Responsável+inválido", status_code=303)

    slug = normalizar_slug_solvoz(empresa.slug)
    try:
        if tipo == "h":
            if slug != "karaokerj":
                raise ValueError("Responsável interno direto é permitido somente para Karaokê RJ.")
            usuario_h = db.query(HumiatUsuario).filter(HumiatUsuario.id == int(raw_id), HumiatUsuario.ativo == 1).first()
            if not usuario_h or not usuario_humiat_equipe_prioritaria(usuario_h):
                raise ValueError("Selecione Junior, Débora ou Luiz para a Karaokê RJ.")
            empresa.responsavel_cliente_id = None
            empresa.responsavel_humiat_usuario_id = int(usuario_h.id)
            db.commit()
            return RedirectResponse("/organiza/configuracoes/solvoz-empresas?sucesso=Responsável+da+Karaokê+RJ+atualizado", status_code=303)

        if tipo != "c" or slug == "karaokerj":
            raise ValueError("Clientes comuns da Karaokê RJ não podem receber Humiat ID por este vínculo.")
        cliente = db.query(Cliente).filter(Cliente.id == int(raw_id)).first()
        if not cliente:
            raise ValueError("Cliente não encontrado.")
        outra_cliente = db.query(SolVozEmpresa).filter(
            SolVozEmpresa.responsavel_cliente_id == int(cliente.id),
            SolVozEmpresa.id != int(empresa.id),
        ).first()
        if outra_cliente:
            raise ValueError(f"Este cliente já é responsável pela empresa {outra_cliente.nome}.")
        empresa.responsavel_cliente_id = int(cliente.id)
        empresa.responsavel_humiat_usuario_id = None
        db.flush()
        try:
            usuario_h, criado = _humiat_vincular_responsavel_solvoz(db, empresa, cliente)
        except ValueError as exc:
            # O responsável fica registrado mesmo se faltar e-mail; a tela oferece o link público de cadastro.
            db.commit()
            return RedirectResponse(
                f"/organiza/configuracoes/solvoz-empresas?erro={quote_plus('Responsável vinculado. ' + str(exc))}", status_code=303
            )
        db.commit()
        aviso = f"Responsável {cliente.nome} vinculado"
        if criado:
            try:
                enviar_link_acesso_humiat(db, usuario_h, request=request, primeiro_acesso=True)
                aviso += " e Humiat ID criado; link de primeiro acesso enviado"
            except Exception as exc:
                aviso += f"; Humiat ID criado, mas o e-mail falhou: {str(exc)[:120]}"
        return RedirectResponse(f"/organiza/configuracoes/solvoz-empresas?sucesso={quote_plus(aviso)}", status_code=303)
    except Exception as exc:
        db.rollback()
        return RedirectResponse(f"/organiza/configuracoes/solvoz-empresas?responsavel={empresa_id}&erro={quote_plus(str(exc)[:260])}", status_code=303)


@app.post("/organiza/configuracoes/solvoz-empresas/{empresa_id}/corrigir-vinculo")
def solvoz_empresa_corrigir_vinculo_equipamentos(
    empresa_id: int,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    if not usuario.is_admin:
        raise HTTPException(403)
    empresa = db.query(SolVozEmpresa).filter(SolVozEmpresa.id == empresa_id).first()
    if not empresa or not empresa.responsavel_cliente_id:
        return RedirectResponse("/organiza/configuracoes/solvoz-empresas?erro=Defina+primeiro+o+responsável+da+empresa", status_code=303)
    equipamentos = db.query(Equipamento).filter(Equipamento.cliente_id == int(empresa.responsavel_cliente_id)).all()
    alterados = 0
    for eq in equipamentos:
        if str(eq.status or "").strip().upper() == "INATIVO":
            continue
        if int(eq.solvoz_empresa_id or 0) != int(empresa.id):
            eq.solvoz_empresa_id = int(empresa.id)
            alterados += 1
    db.commit()
    return RedirectResponse(
        f"/organiza/configuracoes/solvoz-empresas?sucesso={quote_plus(f'{alterados} equipamento(s) corrigido(s) para {empresa.nome}.')}",
        status_code=303,
    )


@app.get("/organiza/clientes/{cliente_id}/cadastro-publico")
def cliente_cadastro_publico(
    cliente_id: int,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(404)
    if not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)
        db.commit()
    return RedirectResponse(f"{PUBLIC_BASE_URL.rstrip('/')}/cadastro/{cliente.token_ficha}", status_code=303)


# Compatibilidade: o botão antigo não cria mais usuário/acesso SolVoz a partir de
# qualquer cliente vinculado por equipamento. A responsabilidade é explícita.
@app.post("/organiza/configuracoes/solvoz-empresas/{empresa_id}/criar-acesso")
def solvoz_empresa_criar_acesso_legado(
    empresa_id: int,
    usuario: Usuario = Depends(usuario_logado),
):
    if not usuario.is_admin:
        raise HTTPException(403)
    return RedirectResponse(
        f"/organiza/configuracoes/solvoz-empresas?responsavel={empresa_id}&erro=Defina+o+responsável+da+empresa.+O+Humiat+ID+só+é+criado+para+esse+responsável.",
        status_code=303,
    )


# Status/nome/slug pertencem ao SolVoz. Mantemos a rota antiga apenas para não
# quebrar favoritos/formulários de versões anteriores.
@app.post("/organiza/configuracoes/solvoz-empresas/{empresa_id}/status")
def solvoz_empresa_status_legado(
    empresa_id: int,
    usuario: Usuario = Depends(usuario_logado),
):
    if not usuario.is_admin:
        raise HTTPException(403)
    return RedirectResponse(
        "/organiza/configuracoes/solvoz-empresas?erro=Ative+ou+inative+a+empresa+no+SolVoz.+O+Organiza+recebe+a+alteração+automaticamente.",
        status_code=303,
    )


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
        # 2021.0 é somente a versão-base; nunca representa uma atualização comprável.
        if label == "2021.0":
            continue
        if label and label not in saida:
            saida.append(label[:30])
    return saida


def _gmail_valido(email: str | None) -> bool:
    valor = (email or "").strip().lower()
    return bool(re.fullmatch(r"[^\s@]+@(gmail\.com|googlemail\.com)", valor))


def _atualizacao_compras_cliente(db: Session, cliente_id: int) -> list[AtualizacaoCompra]:
    return (
        db.query(AtualizacaoCompra)
        .filter(AtualizacaoCompra.cliente_id == int(cliente_id), AtualizacaoCompra.status.in_(("PAGO", "A_PAGAR")))
        .order_by(AtualizacaoCompra.criado_em.desc(), AtualizacaoCompra.id.desc())
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


def _atualizacao_links_padrao_sync(db: Session) -> list[AtualizacaoLinkPadrao]:
    padroes = [
        ("banco_dados", "Banco de Dados", 10),
        ("atualiza_pendrive", "Atualiza pendrive", 20),
        ("pendrive_cliente", "Pendrive Cliente", 30),
    ]
    existentes = {x.chave: x for x in db.query(AtualizacaoLinkPadrao).all()}
    alterou = False
    for chave, nome, ordem in padroes:
        item = existentes.get(chave)
        if not item:
            db.add(AtualizacaoLinkPadrao(chave=chave, nome=nome, ativo=1, ordem=ordem))
            alterou = True
        else:
            # Mantém nome/ordem padronizados sem alterar URL nem status escolhido pelo usuário.
            if item.nome != nome:
                item.nome = nome
                alterou = True
            if int(item.ordem or 0) != ordem:
                item.ordem = ordem
                alterou = True
    if alterou:
        db.commit()
    return db.query(AtualizacaoLinkPadrao).order_by(AtualizacaoLinkPadrao.ordem.asc(), AtualizacaoLinkPadrao.id.asc()).all()


def _atualizacao_pacotes_sync_solvoz(db: Session) -> list[AtualizacaoPacote]:
    labels = []
    try:
        labels = _pacotes_disponiveis_solvoz()
    except Exception:
        labels = []
    # 2021.0 é a base do sistema, não um pacote de atualização.
    labels = [label for label in labels if (_pacote_release_num(label) or 0) > (_pacote_release_num("2021.0") or 0)]
    existentes = {p.pacote: p for p in db.query(AtualizacaoPacote).all()}
    alterou = False
    for label in labels:
        if label not in existentes:
            pacote = AtualizacaoPacote(pacote=label, ativo=1)
            db.add(pacote)
            existentes[label] = pacote
            alterou = True
    # Se uma versão antiga 2021.0 já foi criada em produção, ela deixa de ser utilizável.
    base = existentes.get("2021.0")
    if base and base.ativo:
        base.ativo = 0
        alterou = True
    if alterou:
        db.commit()
    return (
        db.query(AtualizacaoPacote)
        .filter(AtualizacaoPacote.pacote != "2021.0")
        .order_by(AtualizacaoPacote.pacote.asc())
        .all()
    )


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
        # 2021.0 é base e não pode ser registrado como pacote adquirido.
        base = _pacote_release_num("2021.0") or 0
        if end <= base:
            return []
        return [_pacote_label_num(ini)] if ini == end and ini > base else ([_pacote_label_num(end)] if ini <= base else [_pacote_label_num(ini), _pacote_label_num(end)])
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


def _atualizacao_links_padrao_ativos(db: Session) -> list[dict]:
    links = []
    faltando = []
    for item in _atualizacao_links_padrao_sync(db):
        if not item.ativo:
            continue
        if not (item.drive_file_id or item.drive_url):
            faltando.append(item.nome)
            continue
        file_id = (item.drive_file_id or _atualizacao_drive_file_id(item.drive_url)).strip()
        if not file_id:
            faltando.append(item.nome)
            continue
        url = (item.drive_url or f"https://drive.google.com/open?id={file_id}").strip()
        links.append({"tipo": "padrao", "nome": item.nome, "file_id": file_id, "url": url})
    if faltando:
        raise RuntimeError("Cadastre o link do Google Drive dos arquivos padrão ativos: " + ", ".join(faltando))
    return links


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
        links.append({"tipo": "pacote", "pacote": label, "nome": f"Pacote {label}", "file_id": file_id, "url": url})
    if faltando:
        raise RuntimeError("Cadastre o link do Google Drive dos pacotes: " + ", ".join(faltando))
    return links


def _atualizacao_horario_valido(tipo: str, momento: datetime | None) -> bool:
    tipo = (tipo or "").strip().upper()
    if not momento or momento <= datetime.now():
        return False
    # Atendimento presencial na casa do cliente depende da disponibilidade do técnico.
    # Não há grade fixa de horário; apenas evitamos conflitos com a agenda existente.
    if tipo == "CLIENTE":
        return True
    if tipo not in ATUALIZACAO_HORARIOS or momento.weekday() >= 5:
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
    agora = datetime.now()
    if db.query(AtualizacaoPreReserva).filter(
        AtualizacaoPreReserva.status == "PENDENTE",
        AtualizacaoPreReserva.expira_em > agora,
        AtualizacaoPreReserva.data_hora >= inicio,
        AtualizacaoPreReserva.data_hora <= fim,
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
    tipo_rotulo = "Online / AnyDesk" if ag.tipo == "CASA" else ("Casa do cliente" if ag.tipo == "CLIENTE" else "Loja")
    pacotes = " / ".join(_atualizacao_pacotes_lista(compra.pacotes))
    descricao = (
        f"Atualização Karaokê RJ\nCliente: {cliente.nome}\n"
        f"WhatsApp: +{cliente.ddi or ''}{cliente.telefone or ''}\n"
        f"Gmail: {cliente.email or '-'}\nPacotes: {pacotes}\n"
        f"Categoria: Atualização\nLocal: {tipo_rotulo}\nCompra Organiza #{compra.id}"
    )
    return {
        "summary": f"Atualização | {tipo_rotulo} | {cliente.nome}"[:180],
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


AGENDA_CATEGORIAS = {
    "ATUALIZACAO": "Atualização",
    "MANUTENCAO": "Manutenção",
    "VENDA": "Venda",
    "VISITA": "Visita / Atendimento",
    "OUTRO": "Outro",
}
AGENDA_LOCAIS = {
    "CLIENTE": "Casa do cliente",
    "LOJA": "Loja",
    "ONLINE": "Online",
}

def _agenda_categoria(valor: str | None) -> str:
    v = (valor or "").strip().upper()
    return v if v in AGENDA_CATEGORIAS else "VISITA"

def _agenda_local(valor: str | None) -> str:
    v = (valor or "").strip().upper()
    return v if v in AGENDA_LOCAIS else "LOJA"

def _agenda_categoria_evento(evento: AgendaManual) -> str:
    if (evento.categoria or "").strip():
        return _agenda_categoria(evento.categoria)
    legado = (evento.tipo or "").strip().lower()
    if legado in {"online", "loja", "entrada", "retirada", "visita"}:
        return "VISITA"
    return "OUTRO"

def _agenda_local_evento(evento: AgendaManual) -> str:
    if (evento.local_atendimento or "").strip():
        return _agenda_local(evento.local_atendimento)
    legado = (evento.tipo or "").strip().lower()
    if legado == "online":
        return "ONLINE"
    if legado in {"entrada", "retirada", "loja", "visita"}:
        return "LOJA"
    return "LOJA"

def _agenda_tipo_visual(categoria: str, local: str) -> str:
    categoria = _agenda_categoria(categoria)
    local = _agenda_local(local)
    local_classe = local.lower()
    if categoria == "ATUALIZACAO":
        base = "atualizacao-casa" if local == "ONLINE" else ("atualizacao-loja" if local == "LOJA" else "atualizacao-cliente")
        return f"{base} {local_classe}"
    categoria_classe = {
        "MANUTENCAO": "manutencao", "VENDA": "venda", "VISITA": "visita", "OUTRO": "outro"
    }.get(categoria, "visita")
    return f"{categoria_classe} {local_classe}"

def _agenda_titulo(categoria: str, local: str, nome: str = "") -> str:
    cat = AGENDA_CATEGORIAS[_agenda_categoria(categoria)]
    loc = AGENDA_LOCAIS[_agenda_local(local)]
    base = f"{cat} | {loc}"
    return f"{base} | {nome}"[:180] if nome else base[:180]

def _google_calendar_manual_event_payload(evento: AgendaManual) -> dict:
    tz = ZoneInfo(ORGANIZA_GOOGLE_TZ)
    inicio = evento.data_hora.replace(tzinfo=tz) if evento.data_hora.tzinfo is None else evento.data_hora.astimezone(tz)
    fim = inicio + timedelta(minutes=60)
    categoria = _agenda_categoria_evento(evento)
    local = _agenda_local_evento(evento)
    descricao = [
        "Compromisso criado no Organiza",
        f"Categoria: {AGENDA_CATEGORIAS[categoria]}",
        f"Local: {AGENDA_LOCAIS[local]}",
    ]
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


def _atualizacao_email_link(item: dict) -> tuple[str, str]:
    """Retorna URL e rótulo amigáveis para o e-mail de atualização.

    Arquivos do Drive usam a rota de download. Pastas continuam abrindo no Drive,
    pois o Google não oferece download direto de pasta sem compactação prévia.
    """
    url = str(item.get("url") or "").strip()
    file_id = str(item.get("file_id") or "").strip()
    eh_pasta = "/folders/" in url
    if file_id and not eh_pasta:
        return f"https://drive.google.com/uc?export=download&id={urllib.parse.quote(file_id, safe='')}", "Baixar arquivo"
    return url, ("Abrir pasta" if eh_pasta else "Abrir arquivo")


def _atualizacao_enviar_email_casa(db: Session, cliente: Cliente, compra: AtualizacaoCompra) -> None:
    gmail = (cliente.email or "").strip().lower()
    if not _gmail_valido(gmail):
        raise RuntimeError("O cliente precisa atualizar o cadastro com um Gmail válido.")
    links_padrao = _atualizacao_links_padrao_ativos(db)
    links_pacotes = _atualizacao_links_compra(db, compra)
    links = links_padrao + links_pacotes
    for item in links:
        _google_drive_conceder_acesso(db, item["file_id"], gmail)
    if not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)
    agenda_url = f"{PUBLIC_BASE_URL.rstrip('/')}/atualizacao/{cliente.token_ficha}/{compra.id}/agenda?tipo=CASA"
    pacotes_txt = " / ".join(_atualizacao_pacotes_lista(compra.pacotes))
    links_email = [dict(x, email_url=_atualizacao_email_link(x)[0], email_acao=_atualizacao_email_link(x)[1]) for x in links]
    lista_texto = "\n".join(f"- {x['nome']}: {x['email_url']}" for x in links_email)
    lista_html = "".join(
        f'<div style="margin:10px 0;padding:12px;border:1px solid #e5e7eb;border-radius:10px"><strong style="display:block;margin-bottom:8px">{html.escape(x["nome"])}</strong><a href="{html.escape(x["email_url"])}" style="display:inline-block;padding:11px 16px;background:#e6003c;color:white;text-decoration:none;border-radius:8px;font-weight:700">{html.escape(x["email_acao"])}</a></div>'
        for x in links_email
    )
    texto = (
        f"Olá, {cliente.nome}!\n\nSua atualização Karaokê RJ está pronta.\n"
        f"Pacotes: {pacotes_txt}\nGmail liberado: {gmail}\n\n"
        "IMPORTANTE: abra este e-mail no PC ou notebook que será utilizado pelo técnico via AnyDesk. Não faça o download pelo celular.\n\n"
        "Como baixar:\n1. Entre no Google com o mesmo Gmail acima.\n2. Abra cada link abaixo.\n3. Clique em Baixar e aguarde o download terminar completamente.\n4. Não altere nem mova os arquivos antes do atendimento.\n"
        f"\n{lista_texto}\n\nDepois que TODOS os arquivos estiverem baixados no computador, agende o atendimento pelo AnyDesk:\n{agenda_url}\n\n"
        "Atendimentos online / AnyDesk: segunda a sexta, das 10:00 às 20:00, com horários de 1 em 1 hora.\n\nKaraokê RJ"
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
    tipo_rotulo = "Atualização · Online / AnyDesk" if ag.tipo == "CASA" else ("Atualização · Casa do cliente" if ag.tipo == "CLIENTE" else "Atualização · Loja")
    data_txt = ag.data_hora.strftime("%d/%m/%Y às %H:%M")
    pacotes_txt = " / ".join(_atualizacao_pacotes_lista(compra.pacotes))
    if ag.tipo == "CASA":
        lembrete = "Os arquivos devem estar completamente baixados no PC/notebook antes do horário marcado."
    elif ag.tipo == "CLIENTE":
        lembrete = "Atendimento na casa do cliente, em horário combinado conforme disponibilidade do técnico."
    else:
        lembrete = "Leve o equipamento no horário reservado."
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
    valor_normal_centavos: int = 0, valor_a_pagar_centavos: int = 0, frete_centavos: int = 0,
    valor_pago_centavos: int = 0, forma_pagamento: str = "", status: str = "",
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
    # Valor contratado da atualização, sem deslocamento. Em registros antigos/InfinitePay,
    # usa o valor pago como referência quando não vier explicitamente.
    valor_a_pagar_centavos = int(valor_a_pagar_centavos or 0) or int(valor_pago_centavos or 0)
    compra.valor_a_pagar_centavos = valor_a_pagar_centavos or None
    compra.frete_centavos = int(frete_centavos or 0) or None
    compra.valor_pago_centavos = int(valor_pago_centavos or 0) or None
    compra.forma_pagamento = (forma_pagamento or "")[:60] or None
    total_centavos = int(valor_a_pagar_centavos or 0) + int(frete_centavos or 0)
    pago_centavos = int(valor_pago_centavos or 0)
    status_solicitado = (status or "").strip().upper()
    compra.status = "PAGO" if status_solicitado == "PAGO" or (total_centavos > 0 and pago_centavos >= total_centavos) else "A_PAGAR"
    compra.pago_em = (compra.pago_em or datetime.now()) if compra.status == "PAGO" else None
    cliente.atualizacao_oferta_status = compra.status
    cliente.atualizacao_oferta_periodo = pacotes[0] if len(pacotes) == 1 else f"{pacotes[0]} a {pacotes[-1]}"
    cliente.atualizacao_oferta_pacotes = json.dumps(pacotes, ensure_ascii=False)
    if valor_normal_centavos:
        cliente.atualizacao_oferta_valor_normal_centavos = int(valor_normal_centavos)
    if valor_a_pagar_centavos:
        cliente.atualizacao_oferta_valor_promocional_centavos = int(valor_a_pagar_centavos)
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
        "cadastro_ok": bool(_gmail_valido(cliente.email) and telefone_valido(cliente.telefone)),
        "cadastro_url": f"{PUBLIC_BASE_URL.rstrip('/')}/cadastro/{cliente.token_ficha}",
        "whatsapp": re.sub(r"\D", "", cliente.whatsapp_completo() or ""),
        "pacotes": pacotes_oferta,
        "pacotes_comprados": _atualizacao_pacotes_comprados(db, cliente.id),
        "compras": compras,
    })


@app.post("/api/integracoes/solvoz/clientes/{cliente_id}/atualizacao-pre-reserva")
async def api_solvoz_atualizacao_pre_reserva(
    cliente_id: int,
    request: Request,
    x_solvoz_token: Optional[str] = Header(default=None, alias="X-SolVoz-Token"),
    db: Session = Depends(get_db),
):
    """Bloqueia um horário por 30 minutos enquanto o cliente conclui a InfinitePay."""
    _validar_token_solvoz(x_solvoz_token)
    cliente = db.query(Cliente).filter(Cliente.id == int(cliente_id)).first()
    if not cliente:
        raise HTTPException(404, "Cliente não encontrado.")
    try:
        data = await request.json() if "application/json" in (request.headers.get("content-type") or "").lower() else dict(await request.form())
    except Exception:
        data = {}
    tipo = str((data or {}).get("tipo") or "").strip().upper()
    if tipo not in {"LOJA", "CLIENTE"}:
        raise HTTPException(400, "Escolha Loja ou Casa do cliente.")
    raw = str((data or {}).get("data_hora") or "").strip()
    try:
        momento = datetime.fromisoformat(raw)
    except Exception:
        raise HTTPException(400, "Informe uma data e hora válidas.")
    if not _atualizacao_horario_valido(tipo, momento):
        regra = "segunda a sexta, das 14:00 às 18:00" if tipo == "LOJA" else "um horário futuro combinado com o técnico"
        raise HTTPException(409, f"Escolha {regra}.")
    agora = datetime.now()
    db.query(AtualizacaoPreReserva).filter(
        AtualizacaoPreReserva.status == "PENDENTE", AtualizacaoPreReserva.expira_em <= agora
    ).update({"status": "EXPIRADA"}, synchronize_session=False)
    db.commit()
    if _atualizacao_horario_ocupado(db, momento):
        raise HTTPException(409, "Esse horário não está disponível. Escolha outro.")
    token = secrets.token_urlsafe(28)
    pre = AtualizacaoPreReserva(
        token=token, cliente_id=cliente.id, tipo=tipo, data_hora=momento,
        expira_em=agora + timedelta(minutes=30), status="PENDENTE",
    )
    db.add(pre); db.commit(); db.refresh(pre)
    return JSONResponse({
        "ok": True, "token": token, "tipo": tipo, "data_hora": momento.isoformat(),
        "expira_em": pre.expira_em.isoformat(), "minutos": 30,
    })


def _atualizacao_confirmar_pre_reserva(db: Session, cliente: Cliente, compra: AtualizacaoCompra, token: str) -> AtualizacaoAgendamento | None:
    token = (token or "").strip()
    if not token:
        return None
    pre = db.query(AtualizacaoPreReserva).filter(
        AtualizacaoPreReserva.token == token, AtualizacaoPreReserva.cliente_id == cliente.id
    ).first()
    if not pre or pre.status == "CANCELADA":
        return None
    ag = db.query(AtualizacaoAgendamento).filter(AtualizacaoAgendamento.compra_id == compra.id).first()
    if not ag:
        # Mesmo se os 30 minutos expiraram durante a InfinitePay, tenta preservar
        # o horário se ninguém o ocupou depois.
        ocupado = _atualizacao_horario_ocupado(db, pre.data_hora)
        # A própria pré-reserva pode ser a linha encontrada; neutraliza antes da nova checagem.
        status_original = pre.status
        pre.status = "CONFIRMANDO"
        db.flush()
        ocupado = _atualizacao_horario_ocupado(db, pre.data_hora)
        if ocupado:
            pre.status = "EXPIRADA"
            db.commit()
            return None
        ag = AtualizacaoAgendamento(
            compra_id=compra.id, cliente_id=cliente.id, tipo=pre.tipo,
            data_hora=pre.data_hora, duracao_minutos=ATUALIZACAO_DURACAO_MINUTOS, status="RESERVADO",
        )
        db.add(ag); db.flush()
    pre.status = "CONFIRMADA"
    pre.order_nsu = compra.order_nsu
    db.commit(); db.refresh(ag)
    _google_calendar_sincronizar(db, ag, cliente, compra)
    db.commit()
    try:
        _atualizacao_enviar_email_agendamento(db, cliente, compra, ag)
    except Exception:
        pass
    return ag


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
    # Depois que a compra foi registrada (PAGO ou A_PAGAR), abrir novamente o mesmo
    # link não rebaixa o cadastro nem oferece os mesmos pacotes de novo.
    compra_existente = _atualizacao_compra_cobrindo(db, cliente.id, pacotes) if pacotes else None
    if status == "PAGO":
        modalidade = str(data.get("modalidade") or "ONLINE").strip().upper()
        if modalidade not in {"ONLINE", "LOJA", "CLIENTE"}:
            modalidade = "ONLINE"
        valor_base = int(data.get("valor_promocional_centavos") or 0)
        frete_centavos = max(0, int(data.get("frete_centavos") or 0))
        total_pago = int(data.get("valor_pago_centavos") or (valor_base + frete_centavos))
        compra = _atualizacao_registrar_compra(
            db, cliente, pacotes, origem="SOLVOZ", order_nsu=str(data.get("order_nsu") or ""),
            valor_normal_centavos=int(data.get("valor_normal_centavos") or 0),
            valor_a_pagar_centavos=valor_base, frete_centavos=frete_centavos,
            valor_pago_centavos=total_pago, forma_pagamento="InfinitePay", status="PAGO",
        )
        pos_status = "PAGO"
        if modalidade == "ONLINE":
            try:
                _atualizacao_enviar_email_casa(db, cliente, compra)
                pos_status = "ARQUIVOS_ENVIADOS"
            except Exception as exc:
                compra.email_erro = str(exc)[:1200]
                db.commit()
                pos_status = "PAGO_EMAIL_PENDENTE"
        else:
            ag = _atualizacao_confirmar_pre_reserva(db, cliente, compra, str(data.get("pre_reserva_token") or ""))
            pos_status = "AGENDADO" if ag else "PAGO_AGENDAMENTO_PENDENTE"
        return JSONResponse({"ok": True, "cliente_id": cliente.id, "status": "PAGO", "pos_status": pos_status, "compra_id": compra.id})
    if compra_existente:
        return JSONResponse({"ok": True, "cliente_id": cliente.id, "status": compra_existente.status or "A_PAGAR", "preservado": True, "compra_id": compra_existente.id})
    if (cliente.atualizacao_oferta_status or "").upper() in ("PAGO", "A_PAGAR"):
        return JSONResponse({"ok": True, "cliente_id": cliente.id, "status": (cliente.atualizacao_oferta_status or "A_PAGAR").upper(), "preservado": True})
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




def _validar_integracao_connect(request: Request) -> None:
    esperada = (os.getenv("CONNECT_API_KEY") or os.getenv("ORGANIZA_API_KEY") or "").strip()
    recebida = (request.headers.get("X-API-Key") or "").strip()
    if not esperada:
        raise HTTPException(status_code=503, detail="CONNECT_API_KEY/ORGANIZA_API_KEY não configurada no Organiza.")
    if not recebida or not hmac.compare_digest(recebida, esperada):
        raise HTTPException(status_code=401, detail="Chave de integração inválida.")


@app.get("/api/integracoes/connect/campanha-karaoke10")
def api_connect_campanha_karaoke10(telefone: str, request: Request, db: Session = Depends(get_db)):
    """Confirma se o telefone participou da campanha de aluguel mais recente do Organiza."""
    _validar_integracao_connect(request)
    campanha = (
        db.query(Campanha)
        .filter(func.upper(Campanha.lista_tipo) == "ALUGUEL")
        .order_by(func.coalesce(Campanha.iniciado_em, Campanha.criado_em).desc(), Campanha.id.desc())
        .first()
    )
    if not campanha:
        return JSONResponse({"ok": True, "participou": False})
    digitos = re.sub(r"\D", "", telefone or "")
    if digitos.startswith("55") and len(digitos) >= 12:
        sem_ddi = digitos[2:]
    else:
        sem_ddi = digitos
    candidatos = {digitos, sem_ddi, ("55" + sem_ddi) if sem_ddi else ""}
    candidatos.discard("")
    destinos = (
        db.query(CampanhaAluguelDestinatario)
        .options(joinedload(CampanhaAluguelDestinatario.contato))
        .filter(CampanhaAluguelDestinatario.campanha_id == campanha.id)
        .all()
    )
    dest = None
    for item in destinos:
        contato = item.contato
        if not contato:
            continue
        nums = {
            re.sub(r"\D", "", contato.numero_chave or ""),
            re.sub(r"\D", "", contato.whatsapp_completo() or ""),
            re.sub(r"\D", "", contato.telefone or ""),
        }
        if candidatos & nums:
            dest = item
            break
    if not dest:
        return JSONResponse({
            "ok": True, "participou": False, "campanha_id": int(campanha.id), "campanha_nome": campanha.nome
        })
    return JSONResponse({
        "ok": True, "participou": True, "campanha_id": int(campanha.id),
        "campanha_nome": campanha.nome, "status_envio": dest.status or "PENDENTE",
        "enviado_em": dest.enviado_em.isoformat() if dest.enviado_em else None,
    })


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


def _atualizacao_meta_compra(db: Session, compra: AtualizacaoCompra, agendamento: AtualizacaoAgendamento | None = None) -> dict:
    cliente = compra.cliente or db.get(Cliente, compra.cliente_id)
    ag = agendamento
    cadastro_ok = bool(cliente and _gmail_valido(cliente.email) and telefone_valido(cliente.telefone, cliente.pais, cliente.ddi))
    if compra.concluido_em:
        etapa = 3
        etapa_rotulo = "Concluído"
    elif not cadastro_ok:
        etapa = 1
        etapa_rotulo = "Etapa 1/3 · Cadastro pendente"
    elif (compra.status or '').upper() == 'A_PAGAR':
        etapa = 2
        etapa_rotulo = "Etapa 2/3 · Aguardando pagamento"
    else:
        etapa = 3
        if compra.email_arquivos_enviado_em or compra.arquivos_liberados_em:
            etapa_rotulo = "Etapa 3/3 · Arquivos enviados / atendimento"
        elif ag and ag.status in ('RESERVADO', 'CONCLUIDO'):
            etapa_rotulo = "Etapa 3/3 · Atendimento agendado" if ag.status == 'RESERVADO' else "Concluído"
        else:
            etapa_rotulo = "Etapa 3/3 · Aguardando atendimento"
    local = 'indefinido'
    local_rotulo = 'Atendimento a definir'
    if ag:
        if ag.tipo == 'CASA':
            local, local_rotulo = 'online', 'Online / AnyDesk'
        elif ag.tipo == 'CLIENTE':
            local, local_rotulo = 'casa', 'Casa do cliente'
        else:
            local, local_rotulo = 'loja', 'Loja'
    elif compra.email_arquivos_enviado_em or compra.arquivos_liberados_em:
        local, local_rotulo = 'online', 'Online / AnyDesk'
    total = int(compra.valor_a_pagar_centavos or 0) + int(compra.frete_centavos or 0)
    pago = int(compra.valor_pago_centavos or 0)
    return {
        'etapa': etapa, 'etapa_rotulo': etapa_rotulo, 'local': local, 'local_rotulo': local_rotulo,
        'agendamento': ag, 'concluido': bool(compra.concluido_em),
        'saldo': max(total - pago, 0), 'total': total, 'pago': pago,
    }


@app.get("/organiza/atualizacoes", response_class=HTMLResponse)
def atualizacoes_admin(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    # Configurações de Drive/Google quase nunca mudam. Só carregamos/sincronizamos
    # quando o administrador abre explicitamente a área técnica.
    config_aberta = (request.query_params.get("config") or "") == "1"
    links_padrao = _atualizacao_links_padrao_sync(db) if config_aberta else []
    pacotes = _atualizacao_pacotes_sync_solvoz(db) if config_aberta else []
    filtro = (request.query_params.get("filtro") or "").strip().lower()
    busca_compras = (request.query_params.get("busca_compras") or "").strip()
    etapas_filtro = {x for x in request.query_params.getlist("etapa") if x in {'1','2','3'}}
    status_filtro = {x for x in request.query_params.getlist("status_fin") if x in {'pago','a_pagar'}}
    locais_filtro = {x for x in request.query_params.getlist("local") if x in {'online','loja','casa','indefinido'}}
    mostrar_concluidos = (request.query_params.get("concluidos") or "") == "1"

    compras_base = (
        db.query(AtualizacaoCompra)
        .options(selectinload(AtualizacaoCompra.cliente))
        .order_by(AtualizacaoCompra.criado_em.desc(), AtualizacaoCompra.id.desc())
        .limit(300)
        .all()
    )
    agendamentos = db.query(AtualizacaoAgendamento).filter(AtualizacaoAgendamento.status == "RESERVADO").order_by(AtualizacaoAgendamento.data_hora.asc()).all()
    campanha_atual = (
        db.query(Campanha)
        .options(load_only(Campanha.id, Campanha.nome, Campanha.lista_tipo, Campanha.pacote_alvo, Campanha.status, Campanha.criado_em, Campanha.iniciado_em))
        .filter(func.upper(Campanha.lista_tipo) == "ATUALIZACAO")
        .order_by(func.coalesce(Campanha.iniciado_em, Campanha.criado_em).desc(), Campanha.id.desc())
        .first()
    )
    campanha_resultado = None
    campanha_valores = {}
    compras_campanha = []
    if campanha_atual:
        destinos = db.query(CampanhaDestinatario).filter(CampanhaDestinatario.campanha_id == campanha_atual.id).all()
        destinos_por_cliente = {int(d.cliente_id): d for d in destinos}
        total_dest = len(destinos)
        enviados = sum(1 for d in destinos if (d.status or "").upper() in ("ENVIADO", "PROCESSADO"))
        cliente_ids = list(destinos_por_cliente.keys())
        q_compras = db.query(AtualizacaoCompra).options(selectinload(AtualizacaoCompra.cliente)).filter(
            AtualizacaoCompra.cliente_id.in_(cliente_ids or [-1]),
            AtualizacaoCompra.status.in_(("PAGO", "A_PAGAR")),
        )
        if campanha_atual.iniciado_em:
            q_compras = q_compras.filter(AtualizacaoCompra.criado_em >= campanha_atual.iniciado_em)
        compras_campanha = q_compras.order_by(AtualizacaoCompra.criado_em.desc(), AtualizacaoCompra.id.desc()).all()

        total_contratado = 0
        total_recebido = 0
        total_frete = 0
        for c in compras_campanha:
            dest = destinos_por_cliente.get(int(c.cliente_id))
            oferta_centavos = int(getattr(dest, "valor_promocional_centavos", 0) or 0)
            cadastrado_centavos = int(c.valor_a_pagar_centavos or c.valor_pago_centavos or 0)
            valor_campanha = min(cadastrado_centavos, oferta_centavos) if oferta_centavos > 0 else cadastrado_centavos
            recebido_campanha = min(int(c.valor_pago_centavos or 0), valor_campanha)
            total_contratado += valor_campanha
            total_recebido += recebido_campanha
            total_frete += int(c.frete_centavos or 0)
            campanha_valores[int(c.id)] = {
                "oferta": oferta_centavos,
                "cadastrado": cadastrado_centavos,
                "valor_campanha": valor_campanha,
                "recebido_campanha": recebido_campanha,
                "excede_oferta": bool(oferta_centavos > 0 and cadastrado_centavos > oferta_centavos),
            }

        campanha_resultado = {
            "campanha": campanha_atual, "total": total_dest, "enviados": enviados, "pendentes": max(total_dest - enviados, 0),
            "compras": len(compras_campanha), "pagas": sum(1 for c in compras_campanha if c.status == "PAGO"),
            "a_pagar": sum(1 for c in compras_campanha if c.status == "A_PAGAR"), "total_contratado": total_contratado,
            "recebido": total_recebido, "em_aberto": max(total_contratado - total_recebido, 0), "frete": total_frete,
        }

    if filtro in ("compras", "pagas", "a_pagar"):
        compras = list(compras_campanha)
        if filtro == "pagas":
            compras = [c for c in compras if c.status == "PAGO"]
        elif filtro == "a_pagar":
            compras = [c for c in compras if c.status == "A_PAGAR"]
    else:
        compras = list(compras_base)

    compra_ids = [int(c.id) for c in compras]
    ag_por_compra = {}
    if compra_ids:
        for ag in db.query(AtualizacaoAgendamento).filter(AtualizacaoAgendamento.compra_id.in_(compra_ids)).all():
            # O fluxo trabalha com um agendamento corrente por compra; em legado, prioriza o mais recente.
            atual = ag_por_compra.get(int(ag.compra_id))
            if atual is None or int(ag.id) > int(atual.id):
                ag_por_compra[int(ag.compra_id)] = ag
    compra_meta = {int(c.id): _atualizacao_meta_compra(db, c, ag_por_compra.get(int(c.id))) for c in compras}
    if busca_compras:
        termo = busca_compras.casefold()
        compras = [c for c in compras if termo in ((c.cliente.nome if c.cliente else '') or '').casefold()]
    if etapas_filtro:
        compras = [c for c in compras if str(compra_meta.get(int(c.id), {}).get('etapa')) in etapas_filtro]
    if status_filtro:
        compras = [c for c in compras if (c.status or '').lower() in status_filtro]
    if locais_filtro:
        compras = [c for c in compras if compra_meta.get(int(c.id), {}).get('local') in locais_filtro]
    if not mostrar_concluidos:
        compras = [c for c in compras if not compra_meta.get(int(c.id), {}).get('concluido')]

    google = _google_integracao(db) if config_aberta else None
    return templates.TemplateResponse("organiza/atualizacoes.html", {
        "request": request, "usuario": usuario, "links_padrao": links_padrao, "pacotes": pacotes, "compras": compras,
        "agendamentos": agendamentos, "campanha_resultado": campanha_resultado, "campanha_valores": campanha_valores,
        "compra_meta": compra_meta, "filtro": filtro, "busca_compras": busca_compras, "etapas_filtro": etapas_filtro,
        "status_filtro": status_filtro, "locais_filtro": locais_filtro, "mostrar_concluidos": mostrar_concluidos,
        "google": google, "google_configurado": _google_configurado() if config_aberta else False, "config_aberta": config_aberta,
        "mensagem": request.query_params.get("mensagem", ""), "erro": request.query_params.get("erro", ""),
    })


@app.post("/organiza/atualizacoes/links-padrao/novo")
async def atualizacao_link_padrao_novo(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_admin(usuario)
    form = dict(await request.form())
    nome = (form.get("nome") or "").strip()
    drive_url = (form.get("drive_url") or "").strip()
    if not nome:
        return RedirectResponse("/organiza/atualizacoes?erro=" + quote_plus("Informe o nome do link padrão."), status_code=303)
    file_id = _atualizacao_drive_file_id(drive_url)
    if drive_url and not file_id:
        return RedirectResponse("/organiza/atualizacoes?erro=" + quote_plus(f"Não consegui identificar o arquivo/pasta do Google Drive de {nome}."), status_code=303)
    chave_base = re.sub(r"[^a-z0-9]+", "_", unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()).strip("_") or "link"
    chave = chave_base
    i = 2
    while db.query(AtualizacaoLinkPadrao).filter(AtualizacaoLinkPadrao.chave == chave).first():
        chave = f"{chave_base}_{i}"
        i += 1
    maior_ordem = db.query(func.max(AtualizacaoLinkPadrao.ordem)).scalar() or 30
    item = AtualizacaoLinkPadrao(
        chave=chave[:80], nome=nome[:160], drive_url=drive_url or None, drive_file_id=file_id or None,
        ativo=1 if str(form.get("ativo") or "") in ("1", "on", "true") else 0, ordem=int(maior_ordem) + 10,
    )
    db.add(item)
    db.commit()
    return RedirectResponse("/organiza/atualizacoes?mensagem=" + quote_plus(f"Link padrão {nome} incluído."), status_code=303)


@app.post("/organiza/atualizacoes/links-padrao/{link_id}")
async def atualizacao_link_padrao_salvar(link_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_admin(usuario)
    item = db.get(AtualizacaoLinkPadrao, link_id)
    if not item:
        raise HTTPException(404)
    form = dict(await request.form())
    nome = (form.get("nome") or item.nome or "Link padrão").strip()
    drive_url = (form.get("drive_url") or "").strip()
    file_id = _atualizacao_drive_file_id(drive_url)
    if drive_url and not file_id:
        return RedirectResponse("/organiza/atualizacoes?erro=" + quote_plus(f"Não consegui identificar o arquivo/pasta do Google Drive de {nome}."), status_code=303)
    item.nome = nome[:160]
    item.drive_url = drive_url or None
    item.drive_file_id = file_id or None
    item.ativo = 1 if str(form.get("ativo") or "") in ("1", "on", "true") else 0
    db.commit()
    return RedirectResponse("/organiza/atualizacoes?mensagem=" + quote_plus(f"Link padrão {item.nome} atualizado."), status_code=303)


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
    valor_a_pagar = moeda_num(form.get("valor_a_pagar") or "0")
    frete = moeda_num(form.get("frete") or "0")
    valor_pago = moeda_num(form.get("valor_pago") or "0")
    normal = moeda_num(form.get("valor_normal") or "0")
    total = valor_a_pagar + frete
    status = "PAGO" if total > 0 and valor_pago >= total else "A_PAGAR"
    compra = _atualizacao_registrar_compra(
        db, cliente, pacotes, origem="MANUAL", valor_normal_centavos=int(round(normal * 100)),
        valor_a_pagar_centavos=int(round(valor_a_pagar * 100)), frete_centavos=int(round(frete * 100)),
        valor_pago_centavos=int(round(valor_pago * 100)), forma_pagamento=(form.get("forma_pagamento") or "A definir").strip(), status=status,
    )
    rotulo = "PAGO" if compra.status == "PAGO" else "A PAGAR"
    return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_sucesso=" + quote_plus(f"Compra #{compra.id} registrada como {rotulo}. O SolVoz não cobrará novamente esses pacotes."), status_code=303)


@app.post("/organiza/clientes/{cliente_id}/atualizacoes/{compra_id}/editar")
async def atualizacao_admin_editar_lancamento(cliente_id: int, compra_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    compra = db.query(AtualizacaoCompra).filter(
        AtualizacaoCompra.id == compra_id,
        AtualizacaoCompra.cliente_id == cliente_id,
        AtualizacaoCompra.status.in_(("PAGO", "A_PAGAR")),
    ).first()
    cliente = db.get(Cliente, cliente_id)
    if not compra or not cliente:
        raise HTTPException(404)
    form = dict(await request.form())

    inicio = (form.get("pacote_inicio") or compra.pacote_inicio or "").strip()
    fim = (form.get("pacote_fim") or compra.pacote_fim or inicio).strip()
    pacotes = _atualizacao_pacotes_intervalo(db, inicio, fim)
    if not pacotes:
        return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_erro=" + quote_plus("Intervalo de pacotes inválido."), status_code=303)

    desejados = set(_atualizacao_pacotes_lista(pacotes))
    for outra in _atualizacao_compras_cliente(db, cliente_id):
        if int(outra.id) == int(compra.id):
            continue
        if desejados & set(_atualizacao_pacotes_lista(outra.pacotes)):
            return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_erro=" + quote_plus("O intervalo informado possui pacote já registrado em outra compra."), status_code=303)

    valor_atualizacao = moeda_num(form.get("valor_a_pagar") or "0")
    frete = moeda_num(form.get("frete") or "0")
    valor_pago = moeda_num(form.get("valor_pago") or "0")
    normal = moeda_num(form.get("valor_normal") or "0")
    total = valor_atualizacao + frete

    compra.pacote_inicio = pacotes[0]
    compra.pacote_fim = pacotes[-1]
    compra.pacotes = json.dumps(pacotes, ensure_ascii=False)
    compra.valor_a_pagar_centavos = int(round(valor_atualizacao * 100)) or None
    compra.frete_centavos = int(round(frete * 100)) or None
    compra.valor_pago_centavos = int(round(valor_pago * 100)) or None
    compra.valor_normal_centavos = int(round(normal * 100)) or None
    compra.forma_pagamento = ((form.get("forma_pagamento") or compra.forma_pagamento or "A definir").strip()[:60] or None)
    compra.status = "PAGO" if total > 0 and valor_pago >= total else "A_PAGAR"
    compra.pago_em = (compra.pago_em or datetime.now()) if compra.status == "PAGO" else None

    cliente.atualizacao_oferta_status = compra.status
    cliente.atualizacao_oferta_periodo = pacotes[0] if len(pacotes) == 1 else f"{pacotes[0]} a {pacotes[-1]}"
    cliente.atualizacao_oferta_pacotes = json.dumps(pacotes, ensure_ascii=False)
    cliente.atualizacao_oferta_valor_normal_centavos = int(round(normal * 100)) or None
    cliente.atualizacao_oferta_valor_promocional_centavos = int(round(valor_atualizacao * 100)) or None
    cliente.atualizacao_oferta_atualizado_em = datetime.now()
    db.commit()

    saldo = max(int(round(total * 100)) - int(compra.valor_pago_centavos or 0), 0)
    msg = f"Lançamento manual atualizado. Status: {'PAGO' if compra.status == 'PAGO' else 'A PAGAR'} · saldo R$ {saldo/100:.2f}".replace('.', ',')
    return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_sucesso=" + quote_plus(msg), status_code=303)



@app.post("/organiza/atualizacoes/{compra_id}/concluir")
def atualizacao_admin_concluir(
    compra_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)
):
    compra = db.query(AtualizacaoCompra).filter(
        AtualizacaoCompra.id == compra_id,
        AtualizacaoCompra.status.in_(("PAGO", "A_PAGAR")),
    ).first()
    if not compra:
        raise HTTPException(404)
    cliente = db.get(Cliente, compra.cliente_id)
    if not cliente:
        raise HTTPException(404)
    pacote_final = (compra.pacote_fim or '').strip()
    if not pacote_final:
        return RedirectResponse("/organiza/atualizacoes?erro=" + quote_plus("Compra sem pacote final definido."), status_code=303)
    indice_final = _pacote_indice(pacote_final)
    alteradas = 0
    for eq in db.query(Equipamento).filter(Equipamento.cliente_id == cliente.id).all():
        if (eq.status or 'Ativo').strip().lower() != 'ativo':
            continue
        indice_atual = _pacote_indice(eq.pacote)
        if indice_final is None or indice_atual is None or indice_atual <= indice_final:
            eq.pacote = pacote_final
            eq.falta_pacote = calcular_falta_pacote(pacote_final, obter_pacote_atual(db))
            alteradas += 1
    cliente.pacote = pacote_final
    cliente.falta_pacote = calcular_falta_pacote(pacote_final, obter_pacote_atual(db))
    cliente.atualizacao_oferta_status = "CONCLUIDO"
    cliente.atualizacao_oferta_atualizado_em = datetime.now()
    compra.concluido_em = compra.concluido_em or datetime.now()
    ag = db.query(AtualizacaoAgendamento).filter(AtualizacaoAgendamento.compra_id == compra.id).first()
    if ag and ag.status == "RESERVADO":
        ag.status = "CONCLUIDO"
    db.commit()
    msg = f"Atualização concluída. Cliente e {alteradas} máquina(s) ativa(s) foram atualizados para {pacote_final}."
    return RedirectResponse("/organiza/atualizacoes?mensagem=" + quote_plus(msg) + "#compras-campanha", status_code=303)

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


@app.post("/organiza/clientes/{cliente_id}/atualizacoes/{compra_id}/pagamento")
async def atualizacao_admin_registrar_pagamento(cliente_id: int, compra_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    compra = db.query(AtualizacaoCompra).filter(AtualizacaoCompra.id == compra_id, AtualizacaoCompra.cliente_id == cliente_id, AtualizacaoCompra.status.in_(("PAGO", "A_PAGAR"))).first()
    if not compra:
        raise HTTPException(404)
    form = dict(await request.form())
    recebido = moeda_num(form.get("valor_recebido") or "0")
    if recebido <= 0:
        return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_erro=" + quote_plus("Informe um valor recebido maior que zero."), status_code=303)
    atual = int(compra.valor_pago_centavos or 0)
    compra.valor_pago_centavos = atual + int(round(recebido * 100))
    forma = (form.get("forma_pagamento") or "").strip()
    if forma:
        compra.forma_pagamento = forma[:60]
    total = int(compra.valor_a_pagar_centavos or 0) + int(compra.frete_centavos or 0)
    if total > 0 and int(compra.valor_pago_centavos or 0) >= total:
        compra.status = "PAGO"
        compra.pago_em = compra.pago_em or datetime.now()
        cliente = db.get(Cliente, cliente_id)
        if cliente:
            cliente.atualizacao_oferta_status = "PAGO"
            cliente.atualizacao_oferta_atualizado_em = datetime.now()
    else:
        compra.status = "A_PAGAR"
    db.commit()
    saldo = max(total - int(compra.valor_pago_centavos or 0), 0)
    msg = "Pagamento concluído." if compra.status == "PAGO" else f"Recebimento registrado. Saldo a pagar: R$ {saldo/100:.2f}".replace('.', ',')
    return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_sucesso=" + quote_plus(msg), status_code=303)


@app.post("/organiza/clientes/{cliente_id}/atualizacoes/{compra_id}/agendar")
async def atualizacao_admin_agendar(cliente_id: int, compra_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.get(Cliente, cliente_id)
    compra = db.query(AtualizacaoCompra).filter(AtualizacaoCompra.id == compra_id, AtualizacaoCompra.cliente_id == cliente_id, AtualizacaoCompra.status.in_(("PAGO", "A_PAGAR"))).first()
    if not cliente or not compra:
        raise HTTPException(404)
    form = dict(await request.form())
    tipo = (form.get("tipo") or "LOJA").strip().upper()
    if tipo not in ("LOJA", "CASA", "CLIENTE"):
        tipo = "LOJA"
    momento = datetime_form(form.get("data_hora") or "")
    ag = db.query(AtualizacaoAgendamento).filter(AtualizacaoAgendamento.compra_id == compra.id).first()
    if not _atualizacao_horario_valido(tipo, momento):
        if tipo == "CLIENTE":
            mensagem_horario = "Escolha um horário futuro para a visita do técnico."
        else:
            horario = "10:00 às 20:00" if tipo == "CASA" else "14:00 às 18:00"
            mensagem_horario = f"Horário inválido. Segunda a sexta, {horario}, de 1 em 1 hora."
        return RedirectResponse(f"/organiza/clientes/{cliente_id}?atualizacao_erro=" + quote_plus(mensagem_horario), status_code=303)
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
def api_solvoz_empresa_sincronizar(
    nome: str = Form(...),
    slug: str = Form(...),
    solvoz_id: int = Form(0),
    ativo: int = Form(1),
    logo_mini_b64: str = Form(""),
    logo_mini_mime: str = Form(""),
    x_solvoz_token: Optional[str] = Header(default=None, alias="X-SolVoz-Token"),
    db: Session = Depends(get_db),
):
    """Cria ou atualiza no Organiza a empresa mestre do SolVoz (idempotente)."""
    _validar_token_solvoz(x_solvoz_token)
    try:
        empresa, criada, alterada = _solvoz_empresa_upsert_origem(
            db,
            solvoz_id=int(solvoz_id or 0) or None,
            nome=nome,
            slug=slug,
            ativo=int(ativo or 0),
        )
        logo_status = "nao_enviada"
        if str(logo_mini_b64 or "").strip():
            try:
                raw_logo = base64.b64decode(str(logo_mini_b64).strip(), validate=True)
                if len(raw_logo) > 2 * 1024 * 1024:
                    raise ValueError("Miniatura maior que 2 MB")
                mini_logo, mini_mime = _logo_mini_lokafest(raw_logo, logo_mini_mime or "image/webp")
                digest = hashlib.sha256(mini_logo).hexdigest()
                if digest != str(empresa.logo_mini_hash or "") or not empresa.logo_mini_data:
                    empresa.logo_mini_data = mini_logo
                    empresa.logo_mini_mime = mini_mime
                    empresa.logo_mini_hash = digest
                    empresa.logo_mini_atualizado_em = datetime.now()
                    logo_status = "atualizada"
                else:
                    logo_status = "igual"
            except Exception as exc_logo:
                logo_status = "erro"
                print(f"[LOGO LOKAFEST] Miniatura recebida do SolVoz para {empresa.slug}: {exc_logo}")
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    except Exception:
        db.rollback()
        raise
    h_empresa = db.query(HumiatEmpresa).filter(func.lower(HumiatEmpresa.slug) == normalizar_slug_solvoz(empresa.slug)).first()
    return {
        "ok": True,
        "criada": criada,
        "alterada": alterada,
        "empresa_id": empresa.id,
        "solvoz_id": empresa.solvoz_id,
        "humiat_empresa_id": h_empresa.id if h_empresa else None,
        "nome": empresa.nome,
        "slug": empresa.slug,
        "connect_slug": empresa.connect_slug or empresa.slug,
        "dominio": empresa.dominio,
        "ativo": int(empresa.ativo or 0),
        "logo_lokafest": logo_status,
        "responsavel_pendente": not bool(empresa.responsavel_humiat_usuario_id),
    }


@app.get("/api/publico/lokafest/empresas")
def api_publico_lokafest_empresas(request: Request, db: Session = Depends(get_db)):
    """Lista pública e leve de empresas ativas para a faixa de parceiros da LokaFest."""
    empresas = (
        db.query(SolVozEmpresa)
        .filter(SolVozEmpresa.ativo == 1, SolVozEmpresa.logo_mini_data.isnot(None))
        .order_by(SolVozEmpresa.nome.asc())
        .all()
    )
    base = PUBLIC_BASE_URL.rstrip("/") or str(request.base_url).rstrip("/")
    itens = []
    for empresa in empresas:
        versao = str(empresa.logo_mini_hash or "")[:12]
        logo_url = f"{base}/api/publico/lokafest/empresas/{int(empresa.id)}/logo"
        if versao:
            logo_url += f"?v={versao}"
        itens.append({
            "id": int(empresa.id),
            "nome": str(empresa.nome or "").strip(),
            "slug": str(empresa.slug or "").strip(),
            "logo_url": logo_url,
        })
    return JSONResponse(
        {"ok": True, "total": len(itens), "empresas": itens},
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=900"},
    )


@app.get("/api/publico/lokafest/empresas/{empresa_id}/logo")
def api_publico_lokafest_empresa_logo(empresa_id: int, db: Session = Depends(get_db)):
    empresa = db.query(SolVozEmpresa).filter(
        SolVozEmpresa.id == int(empresa_id),
        SolVozEmpresa.ativo == 1,
    ).first()
    if not empresa or not empresa.logo_mini_data:
        raise HTTPException(404, "Logo não disponível")
    data = bytes(empresa.logo_mini_data)
    etag = str(empresa.logo_mini_hash or hashlib.sha256(data).hexdigest())
    return Response(
        content=data,
        media_type=str(empresa.logo_mini_mime or "image/webp"),
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{etag}"',
        },
    )


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
    """Campanhas de atualização sem carregar imagem/BLOB na tela de Vendas."""
    return (
        db.query(Campanha)
        .options(load_only(
            Campanha.id, Campanha.nome, Campanha.lista_tipo, Campanha.pacote_alvo,
            Campanha.status, Campanha.criado_em, Campanha.iniciado_em, Campanha.finalizado_em,
        ))
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

    contexto_custos = _contexto_custos_vendas_em_lote(db, equipamentos)
    for eq in equipamentos:
        total = moeda_num(eq.valor)
        recebido = round(pagamentos_por_equipamento.get(eq.id, 0.0), 2)
        eq.total_calculado = total
        eq.recebido_calculado = recebido
        eq.falta_calculada = max(round(total - recebido, 2), 0)
        eq.excesso_calculado = max(round(recebido - total, 2), 0)
        resumo_venda = _resumo_custo_venda_em_lote(eq, contexto_custos)
        eq.custo_base_calculado = resumo_venda["base"]
        eq.custo_opcionais_calculado = resumo_venda["opcionais"]
        eq.custo_final_calculado = resumo_venda["custo"]
        eq.lucro_calculado = resumo_venda["lucro"]
        eq.margem_calculada = resumo_venda["margem"]
        eq.desconto_calculado = resumo_venda.get("desconto", 0.0)
        eq.preco_bruto_calculado = resumo_venda.get("bruto", total)
        eq.cupom_codigo_calculado = (eq.cupom_codigo_snapshot or "").strip()
        eq.custo_snapshot_ativo = resumo_venda["snapshot"]
        eq.catalogo_venda_rotulo = "Plus" if (eq.catalogo_venda or "").upper() == "PLUS" else "Básico"

    q = (request.query_params.get("q") or "").strip().lower()
    pagamento = (request.query_params.get("pagamento") or "todos").strip()
    valor_filtro = (request.query_params.get("valor") or "todos").strip()
    status_filtros = [
        valor.strip()
        for valor in request.query_params.getlist("status")
        if valor and valor.strip()
    ]
    status_filtros = [valor for valor in status_filtros if valor in status_opcoes]
    visao_vendas = (request.query_params.get("visao") or "operacional").strip().lower()
    if visao_vendas not in {"operacional", "todos"}:
        visao_vendas = "operacional"
    if not request.query_params.getlist("status") and visao_vendas == "operacional":
        status_filtros = [s for s in ("Solicitar gabinete", "Montagem", "Pronto para entrega") if s in status_opcoes]
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
        ("visao", visao_vendas),
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
        "visao_vendas": visao_vendas,
        "filtro_query": urlencode(parametros_filtro),
    }




def nfse_normalizar_uf(valor: str | None) -> str:
    texto = (valor or "").strip()
    if not texto:
        return ""
    sigla = texto.upper()
    if len(sigla) == 2:
        return sigla
    mapa = {
        "ACRE":"AC", "ALAGOAS":"AL", "AMAPA":"AP", "AMAZONAS":"AM", "BAHIA":"BA",
        "CEARA":"CE", "DISTRITO FEDERAL":"DF", "ESPIRITO SANTO":"ES", "GOIAS":"GO",
        "MARANHAO":"MA", "MATO GROSSO":"MT", "MATO GROSSO DO SUL":"MS",
        "MINAS GERAIS":"MG", "PARA":"PA", "PARAIBA":"PB", "PARANA":"PR",
        "PERNAMBUCO":"PE", "PIAUI":"PI", "RIO DE JANEIRO":"RJ", "RIO GRANDE DO NORTE":"RN",
        "RIO GRANDE DO SUL":"RS", "RONDONIA":"RO", "RORAIMA":"RR", "SANTA CATARINA":"SC",
        "SAO PAULO":"SP", "SERGIPE":"SE", "TOCANTINS":"TO",
    }
    chave = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").upper()
    return mapa.get(chave, sigla[:2])


@app.get("/organiza/nfse/importar-connect")
def nfse_importar_connect(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    """Cria o rascunho de NFS-e a partir do Connect.

    O Connect informa o CNPJ escolhido como tomador, o nome/WhatsApp do contato
    do contrato e os dados operacionais do evento. O cadastro fiscal do CNPJ
    continua sendo responsabilidade do Organiza.
    """
    q = request.query_params
    empresa_id = (q.get("connect_empresa_id") or "").strip()
    contrato_id = (q.get("connect_contrato_id") or "").strip()
    referencia = f"connect:{empresa_id}:{contrato_id}" if empresa_id and contrato_id else ""

    documento = re.sub(r"\D", "", q.get("cliente_cnpj") or q.get("cliente_documento") or "")
    if len(documento) != 14:
        raise HTTPException(400, "Informe um CNPJ válido para o tomador da NFS-e")

    nome_connect = (q.get("cliente_nome") or "").strip()
    telefone_connect = re.sub(r"\D", "", q.get("cliente_telefone") or "")
    if telefone_connect.startswith("55") and len(telefone_connect) > 11:
        telefone_connect = telefone_connect[2:]
    if len(telefone_connect) > 20:
        telefone_connect = telefone_connect[-20:]

    cliente = db.query(Cliente).filter(Cliente.documento == documento).order_by(Cliente.id.desc()).first()
    if not cliente:
        cliente = Cliente(
            nome=nome_connect or f"CNPJ {documento}",
            telefone=telefone_connect or "00000000000",
            documento=documento,
            pais="BR",
            ddi="55",
        )
        db.add(cliente)
        db.flush()
    else:
        alterado = False
        nome_atual = (cliente.nome or "").strip()
        telefone_atual = re.sub(r"\D", "", cliente.telefone or "")
        # Corrige somente cadastros provisórios criados pela integração anterior.
        # Dados reais já existentes no Organiza continuam sendo preservados.
        if nome_connect and (not nome_atual or nome_atual.upper().startswith("CNPJ ")):
            cliente.nome = nome_connect[:140]
            alterado = True
        if telefone_connect and (not telefone_atual or set(telefone_atual) <= {"0"}):
            cliente.telefone = telefone_connect[:20]
            alterado = True
        if alterado:
            db.flush()

    if referencia:
        existente = db.query(NFSERascunho).filter(
            NFSERascunho.origem == "connect",
            NFSERascunho.referencia_externa == referencia,
        ).order_by(NFSERascunho.id.desc()).first()
        if existente:
            db.commit()
            return RedirectResponse(f"/organiza/nfse/{existente.id}?duplicada=1", status_code=303)

    data_inicio = data_form(q.get("evento_data_inicio")) or date.today()
    data_fim = data_form(q.get("evento_data_fim")) or data_inicio
    valor_total = max(moeda_num(q.get("valor_total")), 0)
    descricao_evento = (q.get("evento_descricao") or "Aluguel de Karaokê").strip() or "Aluguel de Karaokê"
    municipio_evento = (q.get("evento_municipio") or NFSE_MUNICIPIO_PADRAO).strip()
    uf_evento = nfse_normalizar_uf(q.get("evento_uf") or NFSE_UF_PADRAO)

    # O endereço do evento é responsabilidade do Connect e nunca é substituído
    # pelo endereço fiscal do CNPJ. Mesmo quando coincidir, gravamos o endereço do
    # contrato no rascunho para preservar o local real daquele evento.
    nota = NFSERascunho(
        cliente_id=cliente.id,
        origem="connect",
        referencia_externa=referencia or None,
        origem_url=str(request.url),
        competencia=data_inicio,
        codigo_servico=nfse_codigo_por_tipo(NFSE_TIPO_ALUGUEL),
        municipio_prestacao=municipio_evento,
        uf_prestacao=uf_evento,
        descricao=nfse_descricao_padrao(NFSE_TIPO_ALUGUEL),
        valor_total=valor_total,
        evento_data_inicio=data_inicio,
        evento_data_fim=data_fim,
        evento_descricao=descricao_evento[:255],
        evento_endereco_igual_cliente=0,
        evento_local_tipo="brasil",
        evento_cep=(q.get("evento_cep") or "").strip(),
        evento_logradouro=(q.get("evento_logradouro") or "").strip(),
        evento_numero=(q.get("evento_numero") or "").strip(),
        evento_complemento=(q.get("evento_complemento") or "").strip(),
        evento_bairro=(q.get("evento_bairro") or "").strip(),
        evento_municipio=municipio_evento,
        evento_uf=uf_evento,
        status="RASCUNHO",
    )
    db.add(nota)
    db.commit()
    db.refresh(nota)
    return RedirectResponse(f"/organiza/nfse/{nota.id}?connect=1", status_code=303)

@app.get("/organiza/nfse", response_class=HTMLResponse)
def nfse_lista(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    notas = db.query(NFSERascunho).options(selectinload(NFSERascunho.cliente)).order_by(NFSERascunho.id.desc()).limit(300).all()
    return templates.TemplateResponse("organiza/nfse_lista.html", {"request": request, "usuario": usuario, "notas": notas})


@app.post("/organiza/nfse/limpar-rascunhos")
def nfse_limpar_rascunhos(usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    notas = db.query(NFSERascunho).filter(NFSERascunho.status != "EMITIDA").all()
    total = len(notas)
    for nota in notas:
        db.delete(nota)
    db.commit()
    return RedirectResponse(f"/organiza/nfse?limpos={total}", status_code=303)


@app.post("/organiza/nfse/{nota_id}/excluir")
def nfse_excluir(nota_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    nota = db.query(NFSERascunho).filter(NFSERascunho.id == nota_id).first()
    if not nota:
        raise HTTPException(404)
    if nota.status == "EMITIDA":
        return RedirectResponse("/organiza/nfse?emitida_bloqueada=1", status_code=303)
    db.delete(nota)
    db.commit()
    return RedirectResponse("/organiza/nfse?excluida=1", status_code=303)


@app.post("/organiza/nfse/{nota_id}/emitida")
async def nfse_marcar_emitida(nota_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    nota = db.query(NFSERascunho).filter(NFSERascunho.id == nota_id).first()
    if not nota:
        raise HTTPException(404)
    if nota.status == "EMITIDA":
        return RedirectResponse("/organiza/nfse?ja_emitida=1", status_code=303)

    form = dict(await request.form())
    numero_nfse = (form.get("numero_nfse") or "").strip()
    if not numero_nfse:
        return RedirectResponse("/organiza/nfse?numero_nfse_obrigatorio=1", status_code=303)

    nota.numero_nfse = numero_nfse[:40]
    nota.status = "EMITIDA"
    nota.emitido_em = datetime.now()
    db.commit()
    return RedirectResponse(f"/organiza/nfse?marcada_emitida={nota.id}", status_code=303)


def nfse_cadastro_cnpj_pendente(cliente: Cliente | None) -> bool:
    if not cliente:
        return True
    documento = re.sub(r"\D", "", cliente.documento or "")
    if len(documento) != 14:
        return True
    campos = [
        cliente.razao_social or cliente.empresa,
        cliente.cep, cliente.endereco, cliente.endereco_numero, cliente.bairro,
        cliente.municipio or cliente.cidade, cliente.estado,
    ]
    return not getattr(cliente, "cnpj_consultado_em", None) or any(not str(v or "").strip() for v in campos)


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
    return templates.TemplateResponse("organiza/nfse_detalhe.html", {
        "request": request, "usuario": usuario, "nota": nota, "payload": payload,
        "faltantes": nfse_campos_faltantes(payload),
        "cadastro_cnpj_pendente": nfse_cadastro_cnpj_pendente(nota.cliente) if nota.origem == "connect" else False,
    })


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




def _venda_pode_excluir(db: Session, eq: Equipamento) -> tuple[bool, str]:
    if not eq or not equipamento_eh_venda(eq):
        return False, "Venda inválida."
    if db.query(PagamentoVenda).filter(PagamentoVenda.equipamento_id == eq.id).first():
        return False, "Esta venda possui pagamento registrado. Use cancelamento/estorno antes de excluir."
    cobranca_paga = db.query(InfinitePayCobrancaOrganiza).filter(
        InfinitePayCobrancaOrganiza.origem_tipo == "VENDA",
        InfinitePayCobrancaOrganiza.origem_id == eq.id,
        or_(
            InfinitePayCobrancaOrganiza.pago_em.isnot(None),
            InfinitePayCobrancaOrganiza.status.in_(("PAGO", "PAID", "CONFIRMADO", "CONCLUIDO")),
        ),
    ).first()
    if cobranca_paga:
        return False, "Esta venda possui cobrança InfinitePay paga/confirmada. Não é permitido apagar o histórico."
    if db.query(Manutencao).filter(Manutencao.equipamento_id == eq.id).first():
        return False, "Este equipamento já possui manutenção vinculada e não pode ser apagado como venda desistida."
    if db.query(TransferenciaEquipamento).filter(TransferenciaEquipamento.equipamento_id == eq.id).first():
        return False, "Este equipamento já possui histórico de transferência e não pode ser apagado."
    return True, ""


def _excluir_venda_desistida(db: Session, eq: Equipamento) -> None:
    # Venda sem pagamento/histórico bloqueante pode ser desfeita; remove a própria
    # saída automática para devolver o material ao saldo sem criar lançamento lixo.
    db.query(EstoqueReserva).filter(
        EstoqueReserva.origem_tipo == "VENDA", EstoqueReserva.origem_id == eq.id
    ).delete(synchronize_session=False)
    db.query(EstoqueMovimento).filter(
        EstoqueMovimento.origem_tipo == "VENDA", EstoqueMovimento.origem_id == eq.id, EstoqueMovimento.tipo == "SAIDA"
    ).delete(synchronize_session=False)
    db.query(EstoqueCorUso).filter(
        EstoqueCorUso.origem_tipo == "VENDA", EstoqueCorUso.origem_id == eq.id
    ).delete(synchronize_session=False)
    db.query(InfinitePayCobrancaOrganiza).filter(
        InfinitePayCobrancaOrganiza.origem_tipo == "VENDA", InfinitePayCobrancaOrganiza.origem_id == eq.id
    ).delete(synchronize_session=False)
    db.query(PagamentoVenda).filter(PagamentoVenda.equipamento_id == eq.id).delete(synchronize_session=False)
    cliente_id = eq.cliente_id
    db.delete(eq)
    db.flush()
    if cliente_id:
        reordenar_series_cliente(db, cliente_id)
        _sincronizar_pacote_cliente(db, cliente_id)


def _cliente_pode_ser_excluido_apos_venda(db: Session, cliente_id: int) -> tuple[bool, str]:
    if db.query(Equipamento).filter(Equipamento.cliente_id == cliente_id).first():
        return False, "O cliente ainda possui outro equipamento cadastrado."
    verificacoes = (
        (Manutencao, Manutencao.cliente_id, "manutenção"),
        (AtualizacaoCompra, AtualizacaoCompra.cliente_id, "compra de atualização"),
        (AtualizacaoAgendamento, AtualizacaoAgendamento.cliente_id, "agendamento de atualização"),
        (AtualizacaoPreReserva, AtualizacaoPreReserva.cliente_id, "pré-reserva de atualização"),
        (NFSERascunho, NFSERascunho.cliente_id, "NFS-e"),
        (HistoricoComunicacao, HistoricoComunicacao.cliente_id, "histórico de comunicação"),
        (AgendaManual, AgendaManual.cliente_id, "agendamento"),
    )
    for modelo, campo, nome in verificacoes:
        if db.query(modelo).filter(campo == cliente_id).first():
            return False, f"O cadastro possui {nome} vinculada e será mantido."
    if db.query(TransferenciaEquipamento).filter(or_(
        TransferenciaEquipamento.cliente_origem_id == cliente_id,
        TransferenciaEquipamento.cliente_destino_id == cliente_id,
    )).first():
        return False, "O cadastro possui histórico de transferência e será mantido."
    return True, ""


@app.post("/organiza/vendas/{equipamento_id}/excluir")
async def venda_excluir(
    equipamento_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id).first()
    if not eq:
        raise HTTPException(404)
    pode, motivo = _venda_pode_excluir(db, eq)
    if not pode:
        return RedirectResponse(f"/organiza/vendas?erro={quote_plus(motivo)}", status_code=303)
    _excluir_venda_desistida(db, eq)
    db.commit()
    return RedirectResponse("/organiza/vendas?excluida=1", status_code=303)


@app.post("/organiza/vendas/{equipamento_id}/excluir-com-cliente")
async def venda_excluir_com_cliente(
    equipamento_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id).first()
    if not eq:
        raise HTTPException(404)
    pode, motivo = _venda_pode_excluir(db, eq)
    if not pode:
        return RedirectResponse(f"/organiza/vendas?erro={quote_plus(motivo)}", status_code=303)
    cliente_id = eq.cliente_id
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    _excluir_venda_desistida(db, eq)
    pode_cliente, motivo_cliente = _cliente_pode_ser_excluido_apos_venda(db, cliente_id)
    if pode_cliente and cliente:
        # Vínculos auxiliares sem valor fiscal/financeiro não devem impedir a limpeza
        # de um cadastro criado apenas para uma venda desistida.
        db.query(CampanhaDestinatario).filter(CampanhaDestinatario.cliente_id == cliente_id).delete(synchronize_session=False)
        db.query(SolVozAcessoCliente).filter(SolVozAcessoCliente.cliente_id == cliente_id).delete(synchronize_session=False)
        db.delete(cliente)
        db.commit()
        return RedirectResponse("/organiza/vendas?excluida_cliente=1", status_code=303)
    db.commit()
    return RedirectResponse(
        f"/organiza/vendas?excluida=1&aviso={quote_plus(motivo_cliente)}", status_code=303
    )


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
        "visao_vendas": dados["visao_vendas"],
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
    total_custo = round(sum(eq.custo_final_calculado for eq in vendas), 2)
    total_lucro = round(sum(eq.lucro_calculado for eq in vendas), 2)
    margem_total = round((total_lucro / total_vendido * 100) if total_vendido else 0, 2)

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
        "total_custo": total_custo,
        "total_lucro": total_lucro,
        "margem_total": margem_total,
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
    cliente_selecionado = db.query(Cliente).filter(Cliente.id == int(cliente_id)).first() if cliente_id else None
    contexto = contexto_configuracao_venda(db)
    return templates.TemplateResponse("organiza/venda_nova.html", {
        "request": request, "usuario": usuario, "clientes": clientes,
        "cliente_id": cliente_id, "cliente_selecionado": cliente_selecionado,
        "erro": "", "dados": {}, "status_venda": STATUS_VENDA,
        **contexto,
    })


@app.post("/organiza/vendas/nova")
async def venda_criar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    contexto = contexto_configuracao_venda(db)
    pais_telefone, ddi_telefone, telefone = normalizar_contato("BR", "55", form.get("telefone") or "")
    try:
        cliente_id_form = int(form.get("cliente_id") or 0)
    except (TypeError, ValueError):
        cliente_id_form = 0
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id_form).first() if cliente_id_form else None
    try:
        produto_venda_id = int(form.get("produto_venda_id") or 0)
    except (TypeError, ValueError):
        produto_venda_id = 0
    produto = db.query(VendaModeloEquipamento).filter(VendaModeloEquipamento.id == produto_venda_id, VendaModeloEquipamento.ativo == 1).first() if produto_venda_id else None
    catalogo_venda = "PLUS" if (form.get("catalogo_venda") or "").strip().upper() == "PLUS" else "BASICO"

    if not cliente and not telefone_valido(telefone, pais_telefone, ddi_telefone):
        clientes = db.query(Cliente).order_by(Cliente.nome.asc()).all()
        return templates.TemplateResponse("organiza/venda_nova.html", {
            "request": request, "usuario": usuario, "clientes": clientes, "cliente_id": cliente_id_form,
            "cliente_selecionado": None,
            "erro": "Selecione um cliente ou informe um WhatsApp válido com DDD.", "dados": form,
            "status_venda": STATUS_VENDA, **contexto,
        }, status_code=400)
    if not produto:
        return templates.TemplateResponse("organiza/venda_nova.html", {
            "request": request, "usuario": usuario, "clientes": db.query(Cliente).order_by(Cliente.nome.asc()).all(), "cliente_id": cliente_id_form,
            "cliente_selecionado": cliente,
            "erro": "Informe o equipamento vendido.", "dados": form,
            "status_venda": STATUS_VENDA, **contexto,
        }, status_code=400)

    if not cliente:
        cliente = localizar_cliente_por_contato(db, pais_telefone, ddi_telefone, telefone)
    if not cliente:
        cliente = Cliente(
            nome=f"Cadastro pendente {telefone[-4:]}",
            telefone=telefone,
            pais=pais_telefone,
            ddi=ddi_telefone,
            token_ficha=secrets.token_urlsafe(24),
        )
        db.add(cliente)
        db.flush()
    elif not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)
        db.flush()

    # Na abertura da venda o atendente informa somente WhatsApp + equipamento.
    # Os demais dados pertencem ao cliente e são preenchidos no link público.
    eq = Equipamento(cliente_id=cliente.id, venda_token=secrets.token_urlsafe(32))
    preco_padrao = float(produto.preco_basico or 0) + (PLUS_ACRESCIMO if catalogo_venda == "PLUS" else 0)
    dados_venda = {
        "produto_venda_id": str(produto.id),
        "catalogo_venda": catalogo_venda,
        "valor": f"{preco_padrao:.2f}",
        "preco_venda": f"{preco_padrao:.2f}",
        "cupom_id": str(form.get("cupom_id") or ""),
        "desconto_manual": str(form.get("desconto_manual") or "0"),
        "frete_venda": str(form.get("frete_venda") or "0"),
        "status": "Solicitar gabinete",
        "fabricante": "KARAOKERJ",
        "garantia_meses": "3",
        "microfone": "Com fio",
        "hdmi_tela_2": "NA",
        "teclado_bluetooth": "NA",
        "sistema_credito": "NA",
        "catalogo_impresso": "NA",
    }
    for campo, _grupo in contexto.get("grupos_opcionais", []):
        if f"opcional__{campo}" in form:
            dados_venda[f"opcional__{campo}"] = form.get(f"opcional__{campo}")
    preencher_equipamento(eq, dados_venda, db)
    extra_opcionais = valor_opcionais_venda_equipamento(eq, db)
    if extra_opcionais > 0:
        dados_venda["preco_venda"] = f"{preco_padrao + extra_opcionais:.2f}"
        preencher_equipamento(eq, dados_venda, db)
    garantir_identificacao_equipamento(db, eq)
    db.add(eq)
    db.flush()
    sincronizar_estoque_venda(eq, db)
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
    if not eq.venda_token:
        eq.venda_token = secrets.token_urlsafe(32)
        db.commit()
    cadastro_url = f"{PUBLIC_BASE_URL}/cadastro/{cliente.token_ficha}"
    venda_url = f"{PUBLIC_BASE_URL}/venda/{eq.venda_token}"
    mensagem_cadastro = (
        "Olá! Para atualizar seu cadastro na Karaokê RJ, use o link abaixo:\n\n"
        f"{cadastro_url}"
    )
    mensagem_venda = (
        "Olá! Preparamos sua venda na Karaokê RJ. Confira seus dados, equipamento, opcionais, prazo e depois siga para o pagamento:\n\n"
        f"{venda_url}"
    )
    return templates.TemplateResponse("organiza/venda_cadastro_link.html", {
        "request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq,
        "cadastro_url": cadastro_url, "venda_url": venda_url,
        "mensagem_cadastro": mensagem_cadastro, "mensagem_venda": mensagem_venda,
    })


def _venda_publica_por_token(db: Session, token: str) -> Equipamento | None:
    return db.query(Equipamento).options(
        selectinload(Equipamento.cliente), selectinload(Equipamento.produto_venda)
    ).filter(Equipamento.venda_token == str(token or "").strip()).first()


def _contexto_venda_publica(eq: Equipamento, db: Session, erro: str = "", salvo: str = "") -> dict:
    total, recebido, saldo = _pagamentos_venda_totais(db, eq.id)
    contexto = contexto_configuracao_venda(db, eq)
    prazo = _prazo_venda_equipamento(eq)
    if recebido > 0.009 and not eq.data_compra:
        primeiro = db.query(PagamentoVenda).filter(PagamentoVenda.equipamento_id == eq.id).order_by(PagamentoVenda.data.asc(), PagamentoVenda.id.asc()).first()
        if primeiro:
            atualizar_datas_producao_venda(eq, primeiro.data)
            db.commit()
    if eq.data_compra and not eq.previsao_entrega:
        eq.previsao_entrega = calcular_previsao_venda(eq.data_compra, prazo)
    return {
        "equipamento": eq, "cliente": eq.cliente, "erro": erro, "salvo": salvo,
        "total": total, "recebido": recebido, "saldo": saldo,
        "prazo_producao_dias": prazo, "previsao_entrega": eq.previsao_entrega,
        "infinitepay_habilitada": bool(INFINITEPAY_HANDLE),
        **contexto,
    }


@app.get("/venda/{token}", response_class=HTMLResponse)
def venda_publica(token: str, request: Request, db: Session = Depends(get_db)):
    eq = _venda_publica_por_token(db, token)
    if not eq or not equipamento_eh_venda(eq):
        raise HTTPException(404)
    return templates.TemplateResponse("organiza/venda_publica.html", {
        "request": request,
        "erro_pagamento": request.query_params.get("erro_pagamento", ""),
        **_contexto_venda_publica(eq, db, salvo=request.query_params.get("salvo", ""))
    }, headers={"Cache-Control": "no-store"})


@app.post("/venda/{token}")
async def venda_publica_salvar(token: str, request: Request, db: Session = Depends(get_db)):
    eq = _venda_publica_por_token(db, token)
    if not eq or not equipamento_eh_venda(eq) or not eq.cliente:
        raise HTTPException(404)
    form = dict(await request.form())
    cliente = eq.cliente
    # Cadastro dentro do link da venda. O link simples /cadastro continua separado.
    nome = (form.get("nome") or "").strip()
    telefone = limpar_telefone(form.get("telefone") or "")
    email = (form.get("email") or "").strip()
    documento = re.sub(r"\D", "", form.get("documento") or "")
    if not nome:
        return templates.TemplateResponse("organiza/venda_publica.html", {
            "request": request, **_contexto_venda_publica(eq, db, erro="Informe seu nome.")
        }, status_code=400)
    if telefone and not telefone_valido(telefone):
        return templates.TemplateResponse("organiza/venda_publica.html", {
            "request": request, **_contexto_venda_publica(eq, db, erro="Informe um telefone válido com DDD.")
        }, status_code=400)
    if documento and len(documento) not in (11, 14):
        return templates.TemplateResponse("organiza/venda_publica.html", {
            "request": request, **_contexto_venda_publica(eq, db, erro="CPF/CNPJ inválido.")
        }, status_code=400)
    cliente.nome = nome
    if telefone: cliente.telefone = telefone
    cliente.email = email or None
    cliente.documento = documento or None
    cliente.cep = (form.get("cep") or "").strip() or None
    cliente.endereco = (form.get("endereco") or "").strip() or None
    cliente.endereco_numero = (form.get("endereco_numero") or "").strip() or None
    cliente.complemento = (form.get("complemento") or "").strip() or None
    cliente.bairro = (form.get("bairro") or "").strip() or None
    cliente.municipio = (form.get("municipio") or "").strip() or None
    cliente.cidade = cliente.municipio
    cliente.estado = (form.get("estado") or "").strip() or None

    _total_atual, recebido_atual, _saldo_atual = _pagamentos_venda_totais(db, eq.id)
    # Depois que existe qualquer pagamento, a proposta comercial fica congelada
    # para o cliente. Cadastro ainda pode ser atualizado; opcionais ficam somente leitura.
    if recebido_atual > 0.009:
        db.commit()
        return RedirectResponse(f"/venda/{token}?salvo=1", status_code=303)

    # Cliente só altera opcionais habilitados para o equipamento principal.
    valor_opcionais_antes = valor_opcionais_venda_equipamento(eq, db)
    bruto_atual = max(float(moeda_num(eq.preco_venda)), 0.0)
    preservar_operacional = {
        "solvoz_empresa_id": eq.solvoz_empresa_id, "catalogo_online": eq.catalogo_online,
        "nota_codigo": eq.nota_codigo, "nota_descricao": eq.nota_descricao,
        "numero_hd": eq.numero_hd, "maquina": eq.maquina,
        "numero_maquina_cliente": eq.numero_maquina_cliente, "pago": eq.pago,
        "fabricante": eq.fabricante, "observacao": eq.observacao,
    }
    dados_eq = {
        "produto_venda_id": str(eq.produto_venda_id or ""),
        "catalogo_venda": eq.catalogo_venda or "BASICO",
        "preco_venda": f"{bruto_atual:.2f}",
        "cupom_id": str(eq.cupom_id or ""),
        "desconto_manual": str(eq.desconto_manual or 0),
        "frete_venda": str(eq.frete_venda or 0),
        "data_compra": eq.data_compra.isoformat() if eq.data_compra else "",
        "previsao_entrega": eq.previsao_entrega.isoformat() if eq.previsao_entrega else "",
        "status": eq.status or "Solicitar gabinete",
        "garantia_meses": str(eq.garantia_meses or 3),
        "pacote": eq.pacote or "",
    }
    for campo, _grupo in contexto_configuracao_venda(db, eq).get("grupos_opcionais", []):
        if _opcional_habilitado_modelo(db, eq.produto_venda_id, campo):
            dados_eq[f"opcional__{campo}"] = form.get(f"opcional__{campo}") or _valor_opcional_equipamento(eq, campo, db)
    preencher_equipamento(eq, dados_eq, db)
    valor_opcionais_depois = valor_opcionais_venda_equipamento(eq, db)
    delta = round(valor_opcionais_depois - valor_opcionais_antes, 2)
    if abs(delta) > 0.009 and eq.custo_final_snapshot is None:
        # Aplica somente a diferença comercial dos opcionais; preserva preço manual da proposta.
        dados_eq["preco_venda"] = f"{max(bruto_atual + delta, 0):.2f}"
        preencher_equipamento(eq, dados_eq, db)
    for campo_preservado, valor_preservado in preservar_operacional.items():
        setattr(eq, campo_preservado, valor_preservado)
    sincronizar_estoque_venda(eq, db)
    db.commit()
    return RedirectResponse(f"/venda/{token}?salvo=1", status_code=303)


@app.post("/venda/{token}/pagar")
def venda_publica_pagar(token: str, request: Request, db: Session = Depends(get_db)):
    eq = _venda_publica_por_token(db, token)
    if not eq or not equipamento_eh_venda(eq):
        raise HTTPException(404)
    total, recebido, saldo = _pagamentos_venda_totais(db, eq.id)
    if total <= 0:
        return RedirectResponse(f"/venda/{token}?erro_pagamento=Venda+sem+valor+válido", status_code=303)
    if saldo <= 0.009:
        return RedirectResponse(f"/venda/{token}?salvo=pago", status_code=303)
    # Nunca reutiliza automaticamente uma cobrança de valor diferente do saldo atual.
    pendente = _infinitepay_cobranca_pendente(db, "VENDA", eq.id)
    if pendente and int(pendente.valor_centavos or 0) != int(round(saldo * 100)):
        pendente.status = "SUBSTITUIDA_SALDO_ATUAL"
        db.commit()
    produto_nome = eq.produto_venda_nome_snapshot or eq.modelo or eq.tipo or "Equipamento"
    catalogo_rotulo = "Plus" if (eq.catalogo_venda or "").upper() == "PLUS" else "Básico"
    descricao = f"Venda #{eq.id} - {eq.cliente.nome if eq.cliente else 'Cliente'} - {produto_nome} - Catálogo {catalogo_rotulo}"
    try:
        cobranca = _infinitepay_criar_cobranca_organiza(
            db, origem_tipo="VENDA", origem_id=eq.id, valor=saldo,
            cliente=eq.cliente, descricao=descricao,
            itens_checkout=[{"quantity": 1, "price": int(round(saldo * 100)), "description": descricao}],
        )
    except Exception as exc:
        return RedirectResponse(f"/venda/{token}?erro_pagamento={quote_plus(str(exc))}", status_code=303)
    # A URL da InfinitePay só é entregue depois de consultar o saldo real nesta requisição.
    return RedirectResponse(cobranca.checkout_url, status_code=303)




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

    email_publico = str(cliente.email or "").strip().lower()
    if not email_publico or "@" not in email_publico or "." not in email_publico.split("@", 1)[-1]:
        return templates.TemplateResponse("organiza/cadastro_publico.html", {
            "request": request, "cliente": cliente,
            "erro": "Informe um e-mail válido. O e-mail é obrigatório para concluir a atualização do cadastro e receber seu primeiro acesso.",
            "salvo": False, "aviso": ""
        }, status_code=400)

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

        # A consulta de CNPJ não é mais executada automaticamente ao salvar o cadastro.
        # Ela só deve acontecer por ação manual do usuário nos botões/rotas específicos
        # de consulta. Assim, endereço e demais dados editados manualmente permanecem
        # intactos até que o usuário escolha atualizar o cadastro pelo CNPJ.

        # Os dados fiscais podem ser informados/ajustados manualmente no próprio cadastro.
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
    solvoz_base = SOLVOZ_BASE_URL.rstrip("/")
    destino_atualizacao = (
        proximo_fluxo.startswith(base_publica + "/atualizacao/") or proximo_fluxo.startswith("/atualizacao/")
        or (solvoz_base and proximo_fluxo.startswith(solvoz_base + "/atualizacoes/karaokerj/"))
    )
    if destino_atualizacao:
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


@app.get("/organiza/equipamentos-venda", response_class=HTMLResponse)
def modelos_venda_lista(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    modelos = db.query(VendaModeloEquipamento).order_by(VendaModeloEquipamento.ativo.desc(), VendaModeloEquipamento.ordem, VendaModeloEquipamento.nome).all()
    for modelo in modelos:
        modelo.custo_base_calculado = custo_base_modelo(db, modelo.id)
        modelo.preco_plus_calculado = float(modelo.preco_basico or 0) + PLUS_ACRESCIMO
        modelo.lucro_basico_calculado = round(float(modelo.preco_basico or 0) - modelo.custo_base_calculado, 2)
        modelo.margem_basico_calculada = round((modelo.lucro_basico_calculado / float(modelo.preco_basico or 0) * 100) if float(modelo.preco_basico or 0) else 0, 2)
        modelo.lucro_plus_calculado = round(modelo.preco_plus_calculado - modelo.custo_base_calculado, 2)
        modelo.margem_plus_calculada = round((modelo.lucro_plus_calculado / modelo.preco_plus_calculado * 100) if modelo.preco_plus_calculado else 0, 2)
        modelo.qtd_itens_composicao = db.query(VendaModeloComposicao).filter(VendaModeloComposicao.modelo_id == modelo.id).count()
    return templates.TemplateResponse("organiza/equipamentos_venda.html", {
        "request": request, "usuario": usuario, "modelos": modelos, "plus_acrescimo": PLUS_ACRESCIMO,
    })


@app.post("/organiza/equipamentos-venda/novo")
async def modelo_venda_novo(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    nome = (form.get("nome") or "").strip()
    sku = (form.get("sku") or "").strip() or None
    if not nome:
        return RedirectResponse("/organiza/equipamentos-venda?erro=nome", status_code=303)
    existente = db.query(VendaModeloEquipamento).filter(func.lower(VendaModeloEquipamento.nome) == nome.lower()).first()
    if existente:
        return RedirectResponse(f"/organiza/equipamentos-venda/{existente.id}/editar", status_code=303)
    if sku and db.query(VendaModeloEquipamento).filter(VendaModeloEquipamento.sku == sku).first():
        sku = None
    modelo = VendaModeloEquipamento(
        nome=nome, sku=sku, solvoz_slug=(form.get("solvoz_slug") or "").strip() or None,
        tipo=tipo_equipamento_padrao((form.get("tipo") or "JUKEBOX").strip()) or "JUKEBOX",
        preco_basico=moeda_num(form.get("preco_basico")),
        prazo_producao_dias=max(int(form.get("prazo_producao_dias") or 20), 0), ativo=1,
        ordem=(db.query(func.max(VendaModeloEquipamento.ordem)).scalar() or 0) + 10,
    )
    db.add(modelo); db.flush()
    espelho = _garantir_item_venda(db, "ESPELHO DE TECLADO", "Composição de equipamentos")
    espelho.preco_custo = 20.0; espelho.preco_venda = 50.0; espelho.ativo = 1
    db.add(VendaModeloComposicao(modelo_id=modelo.id, item_id=espelho.id, quantidade=1))
    db.commit(); db.refresh(modelo)
    return RedirectResponse(f"/organiza/equipamentos-venda/{modelo.id}/editar?novo=1", status_code=303)


@app.get("/organiza/equipamentos-venda/{modelo_id}/editar", response_class=HTMLResponse)
def modelo_venda_editar(modelo_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    modelo = db.query(VendaModeloEquipamento).filter(VendaModeloEquipamento.id == modelo_id).first()
    if not modelo:
        raise HTTPException(404)
    itens = db.query(Item).filter(Item.ativo == 1).order_by(Item.nome.asc()).all()
    composicoes = db.query(VendaModeloComposicao).filter(VendaModeloComposicao.modelo_id == modelo_id).all()
    qtd_por_item = {c.item_id: float(c.quantidade or 0) for c in composicoes}
    custo_base = custo_base_modelo(db, modelo.id)
    preco_basico = float(modelo.preco_basico or 0)
    lucro_basico = round(preco_basico - custo_base, 2)
    margem_basico = round((lucro_basico / preco_basico * 100) if preco_basico else 0, 2)
    preco_plus = round(preco_basico + PLUS_ACRESCIMO, 2)
    lucro_plus = round(preco_plus - custo_base, 2)
    margem_plus = round((lucro_plus / preco_plus * 100) if preco_plus else 0, 2)
    return templates.TemplateResponse("organiza/equipamento_venda_form.html", {
        "request": request, "usuario": usuario, "modelo": modelo, "itens": itens,
        "qtd_por_item": qtd_por_item, "custo_base": custo_base,
        "lucro_basico": lucro_basico, "margem_basico": margem_basico,
        "preco_plus": preco_plus, "lucro_plus": lucro_plus, "margem_plus": margem_plus,
        "plus_acrescimo": PLUS_ACRESCIMO,
    })


@app.post("/organiza/equipamentos-venda/{modelo_id}/editar")
async def modelo_venda_salvar(modelo_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    modelo = db.query(VendaModeloEquipamento).filter(VendaModeloEquipamento.id == modelo_id).first()
    if not modelo:
        raise HTTPException(404)
    form = dict(await request.form())
    nome = (form.get("nome") or "").strip()
    sku = (form.get("sku") or "").strip() or None
    slug = (form.get("solvoz_slug") or "").strip() or None
    if nome:
        duplicado_nome = db.query(VendaModeloEquipamento).filter(func.lower(VendaModeloEquipamento.nome) == nome.lower(), VendaModeloEquipamento.id != modelo_id).first()
        if not duplicado_nome:
            modelo.nome = nome
    modelo.sku = sku
    modelo.solvoz_slug = slug
    modelo.tipo = tipo_equipamento_padrao((form.get("tipo") or modelo.tipo or "JUKEBOX").strip()) or "JUKEBOX"
    modelo.preco_basico = moeda_num(form.get("preco_basico"))
    try:
        modelo.prazo_producao_dias = max(int(form.get("prazo_producao_dias") or 20), 0)
    except (TypeError, ValueError):
        modelo.prazo_producao_dias = 20
    modelo.ativo = 1 if str(form.get("ativo") or "").lower() in {"1", "on", "true", "sim"} else 0
    modelo.observacao = (form.get("observacao") or "").strip() or None

    db.query(VendaModeloComposicao).filter(VendaModeloComposicao.modelo_id == modelo_id).delete(synchronize_session=False)
    itens = db.query(Item).filter(Item.ativo == 1).all()
    for item in itens:
        raw = str(form.get(f"qtd_{item.id}") or "").strip().replace(",", ".")
        try:
            qtd = float(raw or 0)
        except ValueError:
            qtd = 0
        if qtd > 0:
            # Garantia extra no cadastro: Stereo jamais entra em composição nova.
            item_usado = item
            if item.nome.strip().upper() == "AMPLIFICADOR STEREO":
                item_usado = _garantir_item_venda(db, "AMPLIFICADOR MONO C/ BLUETOOTH")
            existente = db.query(VendaModeloComposicao).filter(
                VendaModeloComposicao.modelo_id == modelo_id,
                VendaModeloComposicao.item_id == item_usado.id,
            ).first()
            if existente:
                existente.quantidade += qtd
            else:
                db.add(VendaModeloComposicao(modelo_id=modelo_id, item_id=item_usado.id, quantidade=qtd))
    # Regra fixa: toda máquina comercial tem 1 ESPELHO DE TECLADO.
    espelho = _garantir_item_venda(db, "ESPELHO DE TECLADO", "Composição de equipamentos")
    espelho.preco_custo = 20.0; espelho.preco_venda = 50.0; espelho.ativo = 1
    comp_espelho = db.query(VendaModeloComposicao).filter(
        VendaModeloComposicao.modelo_id == modelo_id, VendaModeloComposicao.item_id == espelho.id
    ).first()
    if comp_espelho:
        comp_espelho.quantidade = 1
    else:
        db.add(VendaModeloComposicao(modelo_id=modelo_id, item_id=espelho.id, quantidade=1))
    db.flush()
    # Atualiza reservas abertas que ainda seguem a composição automática.
    for eq in db.query(Equipamento).filter(
        Equipamento.produto_venda_id == modelo_id,
        Equipamento.status.in_(tuple(ESTOQUE_VENDA_A_FAZER)),
    ).all():
        if not bool(eq.estoque_uso_manual):
            sincronizar_estoque_venda(eq, db)
    db.commit()
    return RedirectResponse(f"/organiza/equipamentos-venda/{modelo_id}/editar?salvo=1", status_code=303)


@app.get("/organiza/opcionais-venda", response_class=HTMLResponse)
def opcionais_venda_lista(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    configs = db.query(VendaOpcionalConfig).options(selectinload(VendaOpcionalConfig.item)).order_by(VendaOpcionalConfig.ordem, VendaOpcionalConfig.grupo, VendaOpcionalConfig.valor).all()
    itens = db.query(Item).filter(Item.ativo == 1).order_by(Item.nome.asc()).all()
    modelos = db.query(VendaModeloEquipamento).filter(VendaModeloEquipamento.ativo == 1).order_by(VendaModeloEquipamento.ordem, VendaModeloEquipamento.nome).all()
    for config in configs:
        config.custo_calculado = round(float(config.quantidade or 0) * float(config.item.preco_custo or 0), 2) if config.item else 0.0
    categorias = []
    vistos = set()
    for c in configs:
        if c.campo not in vistos:
            categorias.append((c.campo, c.grupo)); vistos.add(c.campo)
    habilitacao = {(r.campo, r.modelo_id): bool(r.habilitado) for r in db.query(VendaOpcionalModelo).all()}
    return templates.TemplateResponse("organiza/opcionais_venda.html", {
        "request": request, "usuario": usuario, "configs": configs, "itens": itens, "modelos": modelos,
        "categorias": categorias, "habilitacao": habilitacao,
    })


@app.post("/organiza/opcionais-venda/categoria/nova")
async def opcional_categoria_nova(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    grupo = (form.get("grupo") or "").strip()
    if not grupo:
        return RedirectResponse("/organiza/opcionais-venda?erro=Informe+a+categoria", status_code=303)
    base = unicodedata.normalize("NFKD", grupo).encode("ascii", "ignore").decode("ascii").lower()
    campo = re.sub(r"[^a-z0-9]+", "_", base).strip("_")[:50] or f"opcional_{secrets.token_hex(3)}"
    if db.query(VendaOpcionalConfig).filter(VendaOpcionalConfig.campo == campo).first():
        return RedirectResponse("/organiza/opcionais-venda?erro=Categoria+ja+existe", status_code=303)
    ordem = (db.query(func.max(VendaOpcionalConfig.ordem)).scalar() or 0) + 10
    db.add(VendaOpcionalConfig(campo=campo, grupo=grupo, valor="NA", rotulo="NA", quantidade=0, ordem=ordem, ativo=1, padrao=1))
    db.flush()
    for modelo in db.query(VendaModeloEquipamento).filter(VendaModeloEquipamento.ativo == 1).all():
        db.add(VendaOpcionalModelo(campo=campo, modelo_id=modelo.id, habilitado=1))
    db.commit()
    return RedirectResponse("/organiza/opcionais-venda?salvo=1", status_code=303)


@app.post("/organiza/opcionais-venda/item/novo")
async def opcional_item_novo(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    campo = (form.get("campo") or "").strip()
    categoria = db.query(VendaOpcionalConfig).filter(VendaOpcionalConfig.campo == campo).order_by(VendaOpcionalConfig.ordem).first()
    rotulo = (form.get("rotulo") or "").strip()
    if not categoria or not rotulo:
        return RedirectResponse("/organiza/opcionais-venda?erro=Informe+categoria+e+item", status_code=303)
    valor = (form.get("valor") or rotulo).strip()[:80]
    if db.query(VendaOpcionalConfig).filter(VendaOpcionalConfig.campo == campo, VendaOpcionalConfig.valor == valor).first():
        return RedirectResponse("/organiza/opcionais-venda?erro=Item+ja+existe+nesta+categoria", status_code=303)
    item_id = int(form.get("item_id") or 0) if str(form.get("item_id") or "").isdigit() else 0
    item = db.query(Item).filter(Item.id == item_id, Item.ativo == 1).first() if item_id else None
    qtd = max(moeda_num(form.get("quantidade")), 0)
    ordem = (db.query(func.max(VendaOpcionalConfig.ordem)).filter(VendaOpcionalConfig.campo == campo).scalar() or categoria.ordem) + 1
    padrao = 1 if form.get("padrao") else 0
    if padrao:
        db.query(VendaOpcionalConfig).filter(VendaOpcionalConfig.campo == campo).update({VendaOpcionalConfig.padrao: 0}, synchronize_session=False)
    db.add(VendaOpcionalConfig(campo=campo, grupo=categoria.grupo, valor=valor, rotulo=rotulo, item_id=item.id if item else None, quantidade=qtd, ordem=ordem, ativo=1, padrao=padrao))
    db.commit()
    return RedirectResponse("/organiza/opcionais-venda?salvo=1", status_code=303)


@app.post("/organiza/opcionais-venda/{config_id}/editar")
async def opcional_venda_salvar(config_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    config = db.query(VendaOpcionalConfig).filter(VendaOpcionalConfig.id == config_id).first()
    if not config:
        raise HTTPException(404)
    form = dict(await request.form())
    try:
        item_id = int(form.get("item_id") or 0)
    except (TypeError, ValueError):
        item_id = 0
    item = db.query(Item).filter(Item.id == item_id, Item.ativo == 1).first() if item_id else None
    config.item_id = item.id if item else None
    config.rotulo = (form.get("rotulo") or config.rotulo or config.valor).strip()[:100]
    try:
        config.quantidade = max(float(str(form.get("quantidade") or "0").replace(",", ".")), 0)
    except ValueError:
        config.quantidade = 0
    config.ativo = 1 if str(form.get("ativo") or "").lower() in {"1", "on", "true", "sim"} else 0
    if form.get("padrao"):
        db.query(VendaOpcionalConfig).filter(VendaOpcionalConfig.campo == config.campo).update({VendaOpcionalConfig.padrao: 0}, synchronize_session=False)
        config.padrao = 1
    elif config.padrao:
        config.padrao = 0
    db.flush()
    for eq in db.query(Equipamento).filter(Equipamento.status.in_(tuple(ESTOQUE_VENDA_A_FAZER))).all():
        if eq.produto_venda_id and _valor_opcional_equipamento(eq, config.campo, db) == config.valor and not bool(eq.estoque_uso_manual):
            sincronizar_estoque_venda(eq, db)
    db.commit()
    return RedirectResponse("/organiza/opcionais-venda?salvo=1", status_code=303)


@app.post("/organiza/opcionais-venda/habilitacao")
async def opcional_habilitacao_salvar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    campos = [x[0] for x in db.query(VendaOpcionalConfig.campo).distinct().all()]
    modelos = db.query(VendaModeloEquipamento).filter(VendaModeloEquipamento.ativo == 1).all()
    for campo in campos:
        for modelo in modelos:
            habilitado = 1 if form.get(f"hab__{campo}__{modelo.id}") else 0
            regra = db.query(VendaOpcionalModelo).filter(VendaOpcionalModelo.campo == campo, VendaOpcionalModelo.modelo_id == modelo.id).first()
            if regra:
                regra.habilitado = habilitado
            else:
                db.add(VendaOpcionalModelo(campo=campo, modelo_id=modelo.id, habilitado=habilitado))
    db.flush()
    for eq in db.query(Equipamento).filter(Equipamento.status.in_(tuple(ESTOQUE_VENDA_A_FAZER))).all():
        if not bool(eq.estoque_uso_manual):
            sincronizar_estoque_venda(eq, db)
    db.commit()
    return RedirectResponse("/organiza/opcionais-venda?salvo=1", status_code=303)


@app.get("/organiza/cupons-venda", response_class=HTMLResponse)
def cupons_venda_lista(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cupons = db.query(VendaCupom).order_by(VendaCupom.ativo.desc(), VendaCupom.codigo.asc()).all()
    return templates.TemplateResponse("organiza/cupons_venda.html", {
        "request": request, "usuario": usuario, "cupons": cupons,
    })


@app.post("/organiza/cupons-venda/novo")
async def cupom_venda_novo(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    codigo = re.sub(r"\s+", "", (form.get("codigo") or "").strip().upper())
    tipo = (form.get("tipo") or "PERCENTUAL").strip().upper()
    tipo = "VALOR" if tipo == "VALOR" else "PERCENTUAL"
    valor = max(moeda_num(form.get("valor")), 0)
    if tipo == "PERCENTUAL":
        valor = min(valor, 100)
    if codigo:
        existente = db.query(VendaCupom).filter(func.upper(VendaCupom.codigo) == codigo).first()
        if not existente:
            db.add(VendaCupom(
                codigo=codigo, descricao=(form.get("descricao") or "").strip() or None,
                tipo=tipo, valor=valor, ativo=1,
            ))
            db.commit()
    return RedirectResponse("/organiza/cupons-venda?salvo=1", status_code=303)


@app.post("/organiza/cupons-venda/{cupom_id}/editar")
async def cupom_venda_editar(cupom_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cupom = db.query(VendaCupom).filter(VendaCupom.id == cupom_id).first()
    if not cupom:
        raise HTTPException(404)
    form = dict(await request.form())
    codigo = re.sub(r"\s+", "", (form.get("codigo") or "").strip().upper())
    repetido = db.query(VendaCupom).filter(func.upper(VendaCupom.codigo) == codigo, VendaCupom.id != cupom_id).first() if codigo else None
    if codigo and not repetido:
        cupom.codigo = codigo
    cupom.descricao = (form.get("descricao") or "").strip() or None
    tipo = (form.get("tipo") or cupom.tipo or "PERCENTUAL").strip().upper()
    cupom.tipo = "VALOR" if tipo == "VALOR" else "PERCENTUAL"
    valor = max(moeda_num(form.get("valor")), 0)
    cupom.valor = min(valor, 100) if cupom.tipo == "PERCENTUAL" else valor
    cupom.ativo = 1 if str(form.get("ativo") or "").lower() in {"1", "on", "true", "sim"} else 0
    db.commit()
    return RedirectResponse("/organiza/cupons-venda?salvo=1", status_code=303)



def _data_filtro_estoque(valor: str | None):
    try:
        return datetime.strptime((valor or "").strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _historico_estoque(db: Session, data_inicio=None, data_fim=None, item_id: int | None = None,
                       tipo: str = "", origem: str = "") -> list[dict]:
    """Histórico oficial do estoque: somente movimentos físicos.

    Todos os relatórios usam ``estoque_movimentos`` como fonte única. Reserva não
    entra mais na posição, compras ou extrato.
    """
    tipo = (tipo or "").strip().upper()
    origem = (origem or "").strip().upper()
    inicio_dt = datetime.combine(data_inicio, time.min) if data_inicio else None
    fim_dt = datetime.combine(data_fim, time.max) if data_fim else None

    q_mov = db.query(EstoqueMovimento).options(selectinload(EstoqueMovimento.item), selectinload(EstoqueMovimento.usuario))
    if item_id:
        q_mov = q_mov.filter(EstoqueMovimento.item_id == int(item_id))
    if inicio_dt:
        q_mov = q_mov.filter(EstoqueMovimento.criado_em >= inicio_dt)
    if fim_dt:
        q_mov = q_mov.filter(EstoqueMovimento.criado_em <= fim_dt)
    if tipo in {"ENTRADA", "SAIDA"}:
        q_mov = q_mov.filter(EstoqueMovimento.tipo == tipo)
    if origem:
        q_mov = q_mov.filter(EstoqueMovimento.origem_tipo == origem)
    movimentos = [
        m for m in q_mov.order_by(EstoqueMovimento.criado_em.desc(), EstoqueMovimento.id.desc()).all()
        if item_controla_estoque(m.item)
    ]

    venda_ids = {int(x.origem_id) for x in movimentos if (x.origem_tipo or "").upper() == "VENDA" and x.origem_id}
    manut_ids = {int(x.origem_id) for x in movimentos if (x.origem_tipo or "").upper() == "MANUTENCAO" and x.origem_id}
    vendas = {e.id: e for e in db.query(Equipamento).options(selectinload(Equipamento.cliente)).filter(Equipamento.id.in_(venda_ids)).all()} if venda_ids else {}
    manutencoes = {m.id: m for m in db.query(Manutencao).options(selectinload(Manutencao.cliente)).filter(Manutencao.id.in_(manut_ids)).all()} if manut_ids else {}

    def origem_info(origem_tipo, origem_id):
        ot = (origem_tipo or "").upper()
        oid = int(origem_id) if origem_id else None
        if ot == "VENDA" and oid:
            eq = vendas.get(oid)
            return f"Venda #{oid}", (eq.cliente.nome if eq and eq.cliente else ""), (eq.status if eq else ""), (f"/organiza/clientes/{eq.cliente_id}/equipamentos/{eq.id}/editar" if eq else "")
        if ot == "MANUTENCAO" and oid:
            m = manutencoes.get(oid)
            return f"Manutenção #{oid}", (m.cliente.nome if m and m.cliente else ""), (m.status if m else ""), f"/organiza/manutencoes/{oid}"
        if ot == "CONTAGEM": return "Contagem física", "", "", ""
        if ot == "COMPRA": return (f"Compra #{oid}" if oid else "Compra"), "", "", ""
        if ot == "ESTORNO": return (f"Estorno #{oid}" if oid else "Estorno"), "", "", ""
        return ("Entrada manual" if ot == "MANUAL" else (ot or "Manual")), "", "", ""

    linhas = []
    for m in movimentos:
        rotulo, cliente, status, url = origem_info(m.origem_tipo, m.origem_id)
        linhas.append({
            "id": m.id, "data": m.criado_em, "tipo": (m.tipo or "").upper(), "item": m.item,
            "cor": m.cor or "", "quantidade": float(m.quantidade or 0), "origem_tipo": (m.origem_tipo or "").upper(),
            "origem": rotulo, "cliente": cliente, "status": status, "url": url,
            "observacao": m.observacao or "", "fisico": True, "pode_estornar": (m.origem_tipo or "").upper() == "MANUAL",
        })
    linhas.sort(key=lambda x: (x["data"] or datetime.min, x["id"] or 0), reverse=True)
    return linhas


def _extrato_estoque(db: Session, data_inicio=None, data_fim=None, item_id: int | None = None,
                     tipo: str = "", origem: str = "") -> list[dict]:
    """Extrato estilo conta-corrente com saldo real após cada movimento."""
    tipo = (tipo or "").strip().upper()
    origem = (origem or "").strip().upper()
    inicio_dt = datetime.combine(data_inicio, time.min) if data_inicio else None
    fim_dt = datetime.combine(data_fim, time.max) if data_fim else None

    q = db.query(EstoqueMovimento).options(selectinload(EstoqueMovimento.item))
    if item_id:
        q = q.filter(EstoqueMovimento.item_id == int(item_id))
    if fim_dt:
        q = q.filter(EstoqueMovimento.criado_em <= fim_dt)
    todos = [m for m in q.order_by(EstoqueMovimento.criado_em.asc(), EstoqueMovimento.id.asc()).all() if item_controla_estoque(m.item)]

    venda_ids = {int(m.origem_id) for m in todos if (m.origem_tipo or "").upper() == "VENDA" and m.origem_id}
    manut_ids = {int(m.origem_id) for m in todos if (m.origem_tipo or "").upper() == "MANUTENCAO" and m.origem_id}
    vendas = {e.id: e for e in db.query(Equipamento).options(selectinload(Equipamento.cliente)).filter(Equipamento.id.in_(venda_ids)).all()} if venda_ids else {}
    manutencoes = {m.id: m for m in db.query(Manutencao).options(selectinload(Manutencao.cliente)).filter(Manutencao.id.in_(manut_ids)).all()} if manut_ids else {}

    def info_origem(m):
        ot = (m.origem_tipo or "").upper(); oid = int(m.origem_id) if m.origem_id else None
        if ot == "VENDA" and oid:
            eq = vendas.get(oid); return f"Venda #{oid}", (eq.cliente.nome if eq and eq.cliente else ""), (f"/organiza/clientes/{eq.cliente_id}/equipamentos/{eq.id}/editar" if eq else "")
        if ot == "MANUTENCAO" and oid:
            man = manutencoes.get(oid); return f"Manutenção #{oid}", (man.cliente.nome if man and man.cliente else ""), f"/organiza/manutencoes/{oid}"
        if ot == "CONTAGEM": return "Contagem física", "", ""
        if ot == "COMPRA": return (f"Compra #{oid}" if oid else "Compra"), "", ""
        if ot == "ESTORNO": return (f"Estorno #{oid}" if oid else "Estorno"), "", ""
        return ("Entrada manual" if ot == "MANUAL" else (ot or "Manual")), "", ""

    def chave(m): return (int(m.item_id), (m.cor or "").strip().upper())
    saldos = {}
    for m in todos:
        if inicio_dt and m.criado_em and m.criado_em >= inicio_dt: break
        k=chave(m); sinal=1 if (m.tipo or "").upper()=="ENTRADA" else -1
        saldos[k]=round(saldos.get(k,0.0)+sinal*float(m.quantidade or 0),4)
    saldos_iniciais=dict(saldos)

    linhas=[]; chaves_exibidas=set()
    for m in [x for x in todos if not inicio_dt or (x.criado_em and x.criado_em >= inicio_dt)]:
        k=chave(m); sinal=1 if (m.tipo or "").upper()=="ENTRADA" else -1
        saldo_depois=round(saldos.get(k,0.0)+sinal*float(m.quantidade or 0),4); saldos[k]=saldo_depois
        tipo_ok = not tipo or tipo not in {"ENTRADA","SAIDA"} or (m.tipo or "").upper()==tipo
        origem_ok = not origem or (m.origem_tipo or "").upper()==origem
        if not (tipo_ok and origem_ok): continue
        chaves_exibidas.add(k); rotulo,cliente,url=info_origem(m)
        linhas.append({
            "data":m.criado_em,"tipo":(m.tipo or "").upper(),"item":m.item,"cor":m.cor or "",
            "entrada":float(m.quantidade or 0) if (m.tipo or "").upper()=="ENTRADA" else 0.0,
            "saida":float(m.quantidade or 0) if (m.tipo or "").upper()=="SAIDA" else 0.0,
            "saldo":saldo_depois,"origem":rotulo,"cliente":cliente,"url":url,"observacao":m.observacao or "","linha_inicial":False,
        })
    if data_inicio and chaves_exibidas:
        itens_map={i.id:i for i in db.query(Item).filter(Item.id.in_([k[0] for k in chaves_exibidas])).all()}
        iniciais=[]
        for k in sorted(chaves_exibidas,key=lambda x:((_texto_sem_acento(itens_map.get(x[0]).categoria if itens_map.get(x[0]) else "")),(_texto_sem_acento(itens_map.get(x[0]).nome if itens_map.get(x[0]) else "")),x[1])):
            item=itens_map.get(k[0])
            if not item: continue
            iniciais.append({"data":inicio_dt,"tipo":"SALDO","item":item,"cor":k[1],"entrada":0.0,"saida":0.0,"saldo":round(saldos_iniciais.get(k,0.0),4),"origem":"Saldo inicial","cliente":"","url":"","observacao":f"Saldo no início de {data_inicio.strftime('%d/%m/%Y')}","linha_inicial":True})
        linhas=iniciais+linhas
    return linhas


@app.get("/organiza/estoque", response_class=HTMLResponse)
def estoque_painel(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    linhas, cores = estoque_saldos(db)
    valor_estoque_fisico_total = round(sum(
        float(l.get("fisico") or 0) * float(l["item"].preco_custo or 0) for l in linhas
    ), 2)
    q = (request.query_params.get("q") or "").strip().upper()
    if q:
        linhas = [l for l in linhas if q in (l["item"].nome or "").upper() or q in (l["item"].categoria or "").upper()]
    itens = [
        i for i in db.query(Item).filter(Item.ativo == 1).order_by(func.upper(Item.categoria).asc(), func.upper(Item.nome).asc()).all()
        if item_controla_estoque(i)
    ]
    itens_cor_ids = [i.id for i in itens if item_controla_cor(i)]
    compras = relatorio_compras_estoque(db)
    return templates.TemplateResponse("organiza/estoque.html", {
        "request": request, "usuario": usuario, "linhas": linhas, "cores_estoque": cores,
        "itens": itens, "itens_cor_ids": itens_cor_ids,
        "q": q, "erro": request.query_params.get("erro", ""), "ok": request.query_params.get("ok", ""),
        "cor_pendente": ESTOQUE_COR_PENDENTE,
        "total_compras": round(sum(float(x["custo_total"] or 0) for x in compras), 2),
        "qtd_compras": len(compras), "valor_estoque_fisico": valor_estoque_fisico_total,
    })



def _agrupar_movimentacoes_cliente(linhas: list[dict]) -> list[dict]:
    """Uma linha por Venda/Manutenção, sem explodir item a item."""
    grupos: dict[tuple[str, str], dict] = {}
    for l in linhas:
        if l["origem_tipo"] not in {"VENDA", "MANUTENCAO"}:
            continue
        chave = (l["origem_tipo"], l["origem"])
        g = grupos.setdefault(chave, {
            "data": l["data"], "origem_tipo": l["origem_tipo"], "origem": l["origem"],
            "cliente": l["cliente"], "status": l["status"], "url": l["url"],
            "itens_ids": set(), "quantidade": 0.0, "tem_saida": False, "tem_reserva": False,
        })
        if l["data"] and (not g["data"] or l["data"] > g["data"]):
            g["data"] = l["data"]
        if l["item"]:
            g["itens_ids"].add(l["item"].id)
        g["quantidade"] += float(l["quantidade"] or 0)
        g["tem_saida"] = g["tem_saida"] or l["tipo"] == "SAIDA"
        g["tem_reserva"] = g["tem_reserva"] or l["tipo"] == "RESERVA"
    saida = []
    for g in grupos.values():
        g["itens"] = len(g.pop("itens_ids"))
        g["tipo"] = "SAIDA" if g.pop("tem_saida") else ("RESERVA" if g.pop("tem_reserva") else "MOVIMENTO")
        saida.append(g)
    return sorted(saida, key=lambda x: x["data"] or datetime.min, reverse=True)


def _agrupar_movimentacoes_item(linhas: list[dict]) -> list[dict]:
    """Resumo físico por item/cor do período, sem conceito de reserva."""
    grupos: dict[tuple[int, str], dict] = {}
    for l in linhas:
        item = l.get("item")
        if not item:
            continue
        cor = (l.get("cor") or "").strip().upper()
        chave = (item.id, cor)
        g = grupos.setdefault(chave, {"item": item, "cor": cor, "entradas": 0.0, "saidas": 0.0, "reservas": 0.0})
        qtd = float(l.get("quantidade") or 0)
        if l["tipo"] == "ENTRADA": g["entradas"] += qtd
        elif l["tipo"] == "SAIDA": g["saidas"] += qtd
    saida = []
    for g in grupos.values():
        g["movimento_liquido"] = round(g["entradas"] - g["saidas"], 4)
        saida.append(g)
    return sorted(saida, key=lambda x: ((_texto_sem_acento(x["item"].categoria)), (_texto_sem_acento(x["item"].nome)), x["cor"]))


@app.get("/organiza/estoque/movimentacoes", response_class=HTMLResponse)
def estoque_movimentacoes(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    inicio_txt = (request.query_params.get("data_inicio") or "").strip()
    fim_txt = (request.query_params.get("data_fim") or "").strip()
    tipo = (request.query_params.get("tipo") or "").strip().upper()
    origem = (request.query_params.get("origem") or "").strip().upper()
    try:
        item_id = int(request.query_params.get("item_id") or 0)
    except (TypeError, ValueError):
        item_id = 0
    linhas = _historico_estoque(
        db, _data_filtro_estoque(inicio_txt), _data_filtro_estoque(fim_txt),
        item_id=item_id or None, tipo=tipo, origem=origem,
    )
    grupos_clientes = _agrupar_movimentacoes_cliente(linhas)
    movimentos_itens = _agrupar_movimentacoes_item(linhas)
    extrato = _extrato_estoque(
        db, _data_filtro_estoque(inicio_txt), _data_filtro_estoque(fim_txt),
        item_id=item_id or None, tipo=tipo, origem=origem,
    )
    itens = [
        i for i in db.query(Item).filter(Item.ativo == 1).order_by(func.upper(Item.categoria).asc(), func.upper(Item.nome).asc()).all()
        if item_controla_estoque(i)
    ]
    return templates.TemplateResponse("organiza/estoque_movimentacoes.html", {
        "request": request, "usuario": usuario, "linhas": linhas, "itens": itens,
        "grupos_clientes": grupos_clientes, "movimentos_itens": movimentos_itens, "extrato": extrato,
        "data_inicio": inicio_txt, "data_fim": fim_txt, "tipo": tipo, "origem": origem,
        "item_id": item_id, "total_clientes": len(grupos_clientes), "total_itens": len(movimentos_itens),
        "total_extrato": len(extrato),
    })


@app.get("/organiza/estoque/movimentacoes.csv")
def estoque_movimentacoes_csv(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    inicio_txt = (request.query_params.get("data_inicio") or "").strip()
    fim_txt = (request.query_params.get("data_fim") or "").strip()
    tipo = (request.query_params.get("tipo") or "").strip().upper()
    origem = (request.query_params.get("origem") or "").strip().upper()
    try:
        item_id = int(request.query_params.get("item_id") or 0)
    except (TypeError, ValueError):
        item_id = 0
    extrato = _extrato_estoque(db, _data_filtro_estoque(inicio_txt), _data_filtro_estoque(fim_txt), item_id=item_id or None, tipo=tipo, origem=origem)
    buffer = io.StringIO(); writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(["DATA", "CATEGORIA", "ITEM", "UNIDADE", "COR", "ORIGEM", "CLIENTE", "ENTRADA", "SAIDA", "SALDO", "OBSERVACAO"])
    for l in extrato:
        writer.writerow([
            l["data"].strftime("%d/%m/%Y %H:%M") if l.get("data") else "",
            l["item"].categoria if l.get("item") else "", l["item"].nome if l.get("item") else "",
            _normalizar_unidade_item(getattr(l.get("item"), "unidade", "UN")) if l.get("item") else "UN",
            l.get("cor") or "", l.get("origem") or "", l.get("cliente") or "",
            f'{float(l.get("entrada") or 0):g}' if l.get("entrada") else "",
            f'{float(l.get("saida") or 0):g}' if l.get("saida") else "",
            f'{float(l.get("saldo") or 0):g}', l.get("observacao") or "",
        ])
    conteudo = "\ufeff" + buffer.getvalue()
    return Response(content=conteudo, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=extrato_estoque.csv"})



def _entradas_estoque_linhas(db: Session, data_inicio=None, data_fim=None, item_id: int | None = None, origem: str = "") -> list[dict]:
    origem = (origem or "").strip().upper()
    inicio_dt = datetime.combine(data_inicio, time.min) if data_inicio else None
    fim_dt = datetime.combine(data_fim, time.max) if data_fim else None
    q = db.query(EstoqueMovimento).options(
        selectinload(EstoqueMovimento.item),
        selectinload(EstoqueMovimento.usuario),
    ).filter(EstoqueMovimento.tipo == "ENTRADA")
    if item_id:
        q = q.filter(EstoqueMovimento.item_id == int(item_id))
    if inicio_dt:
        q = q.filter(EstoqueMovimento.criado_em >= inicio_dt)
    if fim_dt:
        q = q.filter(EstoqueMovimento.criado_em <= fim_dt)
    if origem:
        q = q.filter(EstoqueMovimento.origem_tipo == origem)

    movimentos = [
        m for m in q.order_by(EstoqueMovimento.criado_em.desc(), EstoqueMovimento.id.desc()).all()
        if item_controla_estoque(m.item)
    ]
    linhas = []
    for m in movimentos:
        origem_tipo = (m.origem_tipo or "MANUAL").upper()
        if origem_tipo == "COMPRA":
            origem_rotulo = f"Compra #{m.origem_id}" if m.origem_id else "Compra"
            origem_url = "/organiza/estoque/compras"
        elif origem_tipo == "CONTAGEM":
            origem_rotulo = "Contagem física"
            origem_url = "/organiza/estoque/contagem"
        elif origem_tipo == "ESTORNO":
            origem_rotulo = f"Estorno #{m.origem_id}" if m.origem_id else "Estorno"
            origem_url = ""
        elif origem_tipo == "MANUAL":
            origem_rotulo = "Entrada manual"
            origem_url = ""
        else:
            origem_rotulo = origem_tipo.title() if origem_tipo else "Entrada"
            origem_url = ""
        custo = float(m.custo_unitario) if m.custo_unitario is not None else None
        quantidade = float(m.quantidade or 0)
        linhas.append({
            "id": m.id,
            "data": m.criado_em,
            "item": m.item,
            "cor": (m.cor or "").strip(),
            "quantidade": quantidade,
            "origem_tipo": origem_tipo,
            "origem": origem_rotulo,
            "origem_url": origem_url,
            "custo_unitario": custo,
            "valor_total": round(quantidade * custo, 2) if custo is not None else None,
            "observacao": (m.observacao or "").strip(),
            "usuario": m.usuario.nome if m.usuario else "",
        })
    return linhas


def _resumo_entradas_estoque(linhas: list[dict]) -> list[dict]:
    grupos: dict[tuple[int, str], dict] = {}
    for l in linhas:
        item = l.get("item")
        if not item:
            continue
        cor = (l.get("cor") or "").strip().upper()
        chave = (item.id, cor)
        g = grupos.setdefault(chave, {
            "item": item,
            "cor": cor,
            "quantidade": 0.0,
            "lancamentos": 0,
            "quantidade_com_custo": 0.0,
            "valor_total": 0.0,
        })
        qtd = float(l.get("quantidade") or 0)
        g["quantidade"] += qtd
        g["lancamentos"] += 1
        if l.get("custo_unitario") is not None:
            g["quantidade_com_custo"] += qtd
            g["valor_total"] += float(l.get("valor_total") or 0)
    saida = []
    for g in grupos.values():
        qtd_custo = float(g.pop("quantidade_com_custo") or 0)
        g["custo_medio"] = round(float(g["valor_total"]) / qtd_custo, 2) if qtd_custo > 0 else None
        g["valor_total"] = round(float(g["valor_total"]), 2)
        saida.append(g)
    return sorted(saida, key=lambda x: ((_texto_sem_acento(x["item"].categoria)), (_texto_sem_acento(x["item"].nome)), x["cor"]))


@app.get("/organiza/estoque/entradas", response_class=HTMLResponse)
def estoque_entradas(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    inicio_txt = (request.query_params.get("data_inicio") or "").strip()
    fim_txt = (request.query_params.get("data_fim") or "").strip()
    origem = (request.query_params.get("origem") or "").strip().upper()
    try:
        item_id = int(request.query_params.get("item_id") or 0)
    except (TypeError, ValueError):
        item_id = 0
    linhas = _entradas_estoque_linhas(
        db,
        _data_filtro_estoque(inicio_txt),
        _data_filtro_estoque(fim_txt),
        item_id=item_id or None,
        origem=origem,
    )
    resumo = _resumo_entradas_estoque(linhas)
    itens = [
        i for i in db.query(Item).filter(Item.ativo == 1).order_by(func.upper(Item.categoria).asc(), func.upper(Item.nome).asc()).all()
        if item_controla_estoque(i)
    ]
    itens_cor_ids = [i.id for i in itens if item_controla_cor(i)]
    total_quantidade = round(sum(float(l.get("quantidade") or 0) for l in linhas), 4)
    total_valor = round(sum(float(l.get("valor_total") or 0) for l in linhas if l.get("valor_total") is not None), 2)
    return templates.TemplateResponse("organiza/estoque_entradas.html", {
        "request": request,
        "usuario": usuario,
        "linhas": linhas,
        "resumo": resumo,
        "itens": itens,
        "itens_cor_ids": itens_cor_ids,
        "data_inicio": inicio_txt,
        "data_fim": fim_txt,
        "item_id": item_id,
        "origem": origem,
        "total_lancamentos": len(linhas),
        "total_itens": len(resumo),
        "total_quantidade": total_quantidade,
        "total_valor": total_valor,
        "erro": request.query_params.get("erro", ""),
        "ok": request.query_params.get("ok", ""),
    })


@app.get("/organiza/estoque/entradas.csv")
def estoque_entradas_csv(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    inicio_txt = (request.query_params.get("data_inicio") or "").strip()
    fim_txt = (request.query_params.get("data_fim") or "").strip()
    origem = (request.query_params.get("origem") or "").strip().upper()
    try:
        item_id = int(request.query_params.get("item_id") or 0)
    except (TypeError, ValueError):
        item_id = 0
    linhas = _entradas_estoque_linhas(
        db,
        _data_filtro_estoque(inicio_txt),
        _data_filtro_estoque(fim_txt),
        item_id=item_id or None,
        origem=origem,
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(["DATA", "CATEGORIA", "ITEM", "UNIDADE", "COR", "ORIGEM", "QUANTIDADE", "CUSTO_UNITARIO", "VALOR_TOTAL", "USUARIO", "OBSERVACAO"])
    for l in linhas:
        writer.writerow([
            l["data"].strftime("%d/%m/%Y %H:%M") if l.get("data") else "",
            l["item"].categoria if l.get("item") else "",
            l["item"].nome if l.get("item") else "",
            _normalizar_unidade_item(getattr(l.get("item"), "unidade", "UN")) if l.get("item") else "UN",
            l.get("cor") or "",
            l.get("origem") or "",
            f'{float(l.get("quantidade") or 0):g}',
            f'{float(l["custo_unitario"]):.2f}' if l.get("custo_unitario") is not None else "",
            f'{float(l["valor_total"]):.2f}' if l.get("valor_total") is not None else "",
            l.get("usuario") or "",
            l.get("observacao") or "",
        ])
    conteudo = "\ufeff" + buffer.getvalue()
    return Response(
        content=conteudo,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=entradas_estoque_detalhadas.csv"},
    )


@app.get("/organiza/estoque/contagem", response_class=HTMLResponse)
def estoque_contagem(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    linhas = _linhas_contagem_estoque(db)
    total_salvos = sum(1 for l in linhas if l.get("salvo"))
    valor_contado = round(sum(
        float(l.get("contagem_salva") or 0) * float(l.get("custo_unitario") or 0)
        for l in linhas if l.get("salvo")
    ), 2)
    valor_fisico_atual = round(sum(
        float(l.get("fisico") or 0) * float(l.get("custo_unitario") or 0)
        for l in linhas
    ), 2)
    return templates.TemplateResponse("organiza/estoque_contagem.html", {
        "request": request, "usuario": usuario, "linhas": linhas,
        "total_salvos": total_salvos, "total_pendentes": max(len(linhas) - total_salvos, 0),
        "valor_contado": valor_contado, "valor_fisico_atual": valor_fisico_atual,
        "erro": request.query_params.get("erro", ""), "ok": request.query_params.get("ok", ""),
    })


@app.post("/organiza/estoque/contagem")
async def estoque_contagem_aplicar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = await request.form()
    item_ids = form.getlist("item_id")
    cores = form.getlist("cor")
    contagens = form.getlist("contagem")
    observacoes = form.getlist("observacao_linha")
    salvos_originais = form.getlist("salvo_original")
    if not item_ids:
        return RedirectResponse("/organiza/estoque/contagem?erro=" + quote_plus("Nenhuma linha de contagem recebida."), status_code=303)

    desejados: dict[tuple[int, str], dict] = {}
    erros = []
    for idx, bruto_id in enumerate(item_ids):
        if idx < len(salvos_originais) and str(salvos_originais[idx] or "0").strip() == "1":
            # Linha já contada e não reaberta para correção: preserva o progresso sem reaplicar saldo antigo.
            continue
        try:
            item_id = int(bruto_id or 0)
        except (TypeError, ValueError):
            continue
        item = db.query(Item).filter(Item.id == item_id, Item.ativo == 1).first()
        if not item or not item_controla_estoque(item):
            continue
        cor = normalizar_cor(cores[idx] if idx < len(cores) else "") if item_controla_cor(item) else ""
        contagem_txt = str(contagens[idx] if idx < len(contagens) else "").strip()
        observacao = str(observacoes[idx] if idx < len(observacoes) else "").strip()

        # Linha totalmente em branco = ainda não contada. Estoque mínimo não é alterado nesta tela.
        if item_controla_cor(item) and contagem_txt and not cor:
            erros.append(f"Informe a cor de {item.nome} somente na linha que estiver contando.")
            continue
        if not contagem_txt and not cor:
            continue

        chave = (item.id, cor)
        registro = desejados.setdefault(chave, {"item": item, "cor": cor, "contagem": None, "observacao": observacao})
        if contagem_txt != "":
            try:
                registro["contagem"] = max(float(contagem_txt.replace(",", ".")), 0)
            except ValueError:
                erros.append(f"Contagem inválida para {item.nome} {cor}.".strip())

    if erros:
        db.rollback()
        return RedirectResponse("/organiza/estoque/contagem?erro=" + quote_plus(erros[0]), status_code=303)

    ajustes = 0
    salvos = 0
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    for registro in desejados.values():
        item = registro["item"]
        cor = registro["cor"]
        if registro["contagem"] is None:
            continue
        atual = _estoque_fisico_chave(db, item.id, cor)
        diferenca = round(float(registro["contagem"]) - atual, 4)
        if abs(diferenca) > 0.0001:
            db.add(EstoqueMovimento(
                item_id=item.id,
                tipo="ENTRADA" if diferenca > 0 else "SAIDA",
                quantidade=abs(diferenca),
                cor=cor or None,
                origem_tipo="CONTAGEM",
                custo_unitario=float(item.preco_custo or 0),
                observacao=f"Ajuste por contagem física em {agora}",
                usuario_id=usuario.id,
            ))
            ajustes += 1
        _salvar_progresso_contagem(
            db, item.id, cor, registro["contagem"], None,
            registro.get("observacao"), usuario.id,
        )
        salvos += 1
    db.commit()
    if salvos == 0:
        msg = "Nenhuma nova contagem preenchida. Os campos em branco foram mantidos pendentes."
    else:
        msg = f"Progresso salvo: {salvos} linha(s) contada(s); {ajustes} ajuste(s) de estoque realizado(s)."
    return RedirectResponse("/organiza/estoque/contagem?ok=" + quote_plus(msg), status_code=303)


@app.post("/organiza/estoque/contagem/nova")
def estoque_contagem_nova(usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    qtd = db.query(EstoqueContagemProgresso).delete(synchronize_session=False)
    db.commit()
    return RedirectResponse(
        "/organiza/estoque/contagem?ok=" + quote_plus(f"Nova contagem iniciada. {int(qtd or 0)} marcação(ões) de progresso foram liberadas; o estoque físico não foi alterado."),
        status_code=303,
    )


@app.get("/organiza/estoque/contagem.csv")
def estoque_contagem_csv(usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(["CATEGORIA", "ITEM", "UNIDADE", "COR", "STATUS", "CUSTO UNITARIO", "ESTOQUE SISTEMA", "CONTAGEM FISICA", "VALOR CONTADO", "DIFERENCA", "OBSERVACAO"])
    for linha in _linhas_contagem_estoque(db):
        contagem = linha.get("contagem_salva") if linha.get("salvo") else None
        diferenca = (float(contagem) - float(linha["fisico"])) if contagem is not None else None
        writer.writerow([
            linha["item"].categoria, linha["item"].nome, _normalizar_unidade_item(getattr(linha["item"], "unidade", "UN")), linha["cor"], "CONTADO" if linha.get("salvo") else "PENDENTE",
            f'{float(linha.get("custo_unitario") or 0):.2f}',
            f'{float(linha["fisico"]):g}', f'{float(contagem):g}' if contagem is not None else "",
            f'{float(contagem) * float(linha.get("custo_unitario") or 0):.2f}' if contagem is not None else "",
            f'{float(diferenca):g}' if diferenca is not None else "", linha.get("observacao_salva") or "",
        ])
    conteudo = "\ufeff" + buffer.getvalue()
    return Response(
        content=conteudo.encode("utf-8"), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="contagem_estoque.csv"'},
    )


@app.get("/organiza/estoque/compras", response_class=HTMLResponse)
def estoque_relatorio_compras(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    compras = relatorio_compras_estoque(db)
    pedidos = db.query(EstoqueCompraPedido).options(
        selectinload(EstoqueCompraPedido.item).selectinload(Item.fornecedor),
        selectinload(EstoqueCompraPedido.fornecedor),
    ).all()
    pedidos.sort(key=lambda p: (
        1 if (p.status or "").upper() == "RECEBIDA" else 0,
        _texto_sem_acento(p.fornecedor.nome if p.fornecedor else (p.item.fornecedor.nome if p.item and p.item.fornecedor else "Sem fornecedor")),
        p.previsao_entrega or date.max, -(p.id or 0),
    ))
    itens = [
        i for i in db.query(Item).filter(Item.ativo == 1).order_by(func.upper(Item.categoria), func.upper(Item.nome)).all()
        if item_controla_estoque(i)
    ]
    return templates.TemplateResponse("organiza/estoque_compras.html", {
        "request": request, "usuario": usuario, "compras": compras, "pedidos": pedidos, "itens": itens,
        "itens_cor_ids": [i.id for i in itens if item_controla_cor(i)],
        "total": round(sum(float(x["custo_total"] or 0) for x in compras), 2),
        "ok": request.query_params.get("ok", ""), "erro": request.query_params.get("erro", ""),
    })


@app.post("/organiza/estoque/compras/nova")
async def estoque_compra_nova(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    try:
        item_id = int(form.get("item_id") or 0)
    except (TypeError, ValueError):
        item_id = 0
    item = db.query(Item).filter(Item.id == item_id, Item.ativo == 1).first()
    try:
        quantidade = float(str(form.get("quantidade") or "0").replace(",", "."))
    except (TypeError, ValueError):
        quantidade = 0
    valor_total = moeda_num(form.get("valor_total"))
    previsao = None
    previsao_txt = (form.get("previsao_entrega") or "").strip()
    if previsao_txt:
        try:
            previsao = date.fromisoformat(previsao_txt)
        except ValueError:
            return RedirectResponse("/organiza/estoque/compras?erro=" + quote_plus("Informe uma data de chegada válida."), status_code=303)
    if not item or not item_controla_estoque(item) or quantidade <= 0 or valor_total < 0:
        return RedirectResponse("/organiza/estoque/compras?erro=" + quote_plus("Informe item, quantidade e valor total válidos."), status_code=303)
    cor = normalizar_cor(form.get("cor")) if item_controla_cor(item) else ""
    if item_controla_cor(item) and not cor:
        return RedirectResponse("/organiza/estoque/compras?erro=" + quote_plus(f"Informe a cor para {item.nome}."), status_code=303)
    pedido = EstoqueCompraPedido(
        item_id=item.id, fornecedor_id=item.fornecedor_id, cor=cor or None, quantidade=quantidade, valor_total=valor_total,
        previsao_entrega=previsao, status="AGUARDANDO", observacao=(form.get("observacao") or "").strip() or None,
        usuario_id=usuario.id,
    )
    db.add(pedido)
    db.commit()
    return RedirectResponse("/organiza/estoque/compras?ok=" + quote_plus(f"Compra registrada: {item.nome} × {quantidade:g}."), status_code=303)


@app.post("/organiza/estoque/compras/lote")
async def estoque_compra_lote(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    """Registra vários itens de uma compra com uma única data prevista.

    A quantidade é escolhida na planilha de reposição. O valor do pedido é
    calculado pelo custo atual do item no momento em que o lote é salvo.
    """
    form = dict(await request.form())
    bruto = (form.get("linhas_json") or "").strip()
    try:
        linhas = json.loads(bruto) if bruto else []
    except (TypeError, ValueError, json.JSONDecodeError):
        linhas = []
    if not isinstance(linhas, list) or not linhas:
        return RedirectResponse("/organiza/estoque/compras?erro=" + quote_plus("Marque pelo menos um item para comprar."), status_code=303)

    previsao_txt = (form.get("previsao_entrega") or "").strip()
    try:
        previsao = date.fromisoformat(previsao_txt)
    except (TypeError, ValueError):
        return RedirectResponse("/organiza/estoque/compras?erro=" + quote_plus("Informe a data prevista de chegada."), status_code=303)
    observacao = (form.get("observacao") or "").strip() or None

    consolidadas: dict[tuple[int, str], float] = {}
    for linha in linhas:
        if not isinstance(linha, dict):
            continue
        try:
            item_id = int(linha.get("item_id") or 0)
            quantidade = float(linha.get("quantidade") or 0)
        except (TypeError, ValueError):
            continue
        if item_id <= 0 or quantidade <= 0:
            continue
        item = db.query(Item).filter(Item.id == item_id, Item.ativo == 1).first()
        if not item or not item_controla_estoque(item):
            continue
        cor = normalizar_cor(linha.get("cor")) if item_controla_cor(item) else ""
        if item_controla_cor(item) and not cor:
            return RedirectResponse("/organiza/estoque/compras?erro=" + quote_plus(f"Informe a cor para {item.nome}."), status_code=303)
        chave = (item.id, cor)
        consolidadas[chave] = consolidadas.get(chave, 0.0) + quantidade

    if not consolidadas:
        return RedirectResponse("/organiza/estoque/compras?erro=" + quote_plus("Nenhum item válido foi selecionado para compra."), status_code=303)

    itens_map = {i.id: i for i in db.query(Item).filter(Item.id.in_([k[0] for k in consolidadas])).all()}
    total_lote = 0.0
    criados = 0
    for (item_id, cor), quantidade in consolidadas.items():
        item = itens_map.get(item_id)
        if not item:
            continue
        valor_total = round(float(item.preco_custo or 0) * float(quantidade), 2)
        total_lote += valor_total
        db.add(EstoqueCompraPedido(
            item_id=item.id, fornecedor_id=item.fornecedor_id, cor=cor or None, quantidade=quantidade, valor_total=valor_total,
            previsao_entrega=previsao, status="AGUARDANDO", observacao=observacao, usuario_id=usuario.id,
        ))
        criados += 1
    db.commit()
    total_fmt = f"{total_lote:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return RedirectResponse(
        "/organiza/estoque/compras?ok=" + quote_plus(f"Compra salva: {criados} item(ns), total estimado de R$ {total_fmt}."),
        status_code=303,
    )


@app.post("/organiza/estoque/compras/{pedido_id}/receber")
def estoque_compra_receber(pedido_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    pedido = db.query(EstoqueCompraPedido).options(selectinload(EstoqueCompraPedido.item)).filter(EstoqueCompraPedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(404)
    if (pedido.status or "").upper() == "RECEBIDA":
        return RedirectResponse("/organiza/estoque/compras?ok=" + quote_plus("Esta compra já foi recebida."), status_code=303)
    existente = db.query(EstoqueMovimento.id).filter(
        EstoqueMovimento.origem_tipo == "COMPRA", EstoqueMovimento.origem_id == pedido.id, EstoqueMovimento.tipo == "ENTRADA"
    ).first()
    if not existente:
        qtd = float(pedido.quantidade or 0)
        custo_unitario = (float(pedido.valor_total or 0) / qtd) if qtd > 0 else None
        db.add(EstoqueMovimento(
            item_id=pedido.item_id, tipo="ENTRADA", quantidade=qtd, cor=pedido.cor,
            origem_tipo="COMPRA", origem_id=pedido.id, custo_unitario=custo_unitario,
            observacao=f"Compra #{pedido.id} recebida" + (f" · {pedido.observacao}" if pedido.observacao else ""),
            usuario_id=usuario.id,
        ))
    pedido.status = "RECEBIDA"
    pedido.recebido_em = datetime.now()
    db.commit()
    return RedirectResponse("/organiza/estoque/compras?ok=" + quote_plus(f"Chegada confirmada. Estoque de {pedido.item.nome} aumentado em {float(pedido.quantidade or 0):g}."), status_code=303)


@app.post("/organiza/estoque/entrada")
async def estoque_entrada(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    try:
        item_id = int(form.get("item_id") or 0)
    except (TypeError, ValueError):
        item_id = 0
    item = db.query(Item).filter(Item.id == item_id, Item.ativo == 1).first()
    try:
        quantidade = float(str(form.get("quantidade") or "0").replace(",", "."))
    except (TypeError, ValueError):
        quantidade = 0
    if not item or not item_controla_estoque(item) or quantidade <= 0:
        return RedirectResponse("/organiza/estoque/entradas?erro=" + quote_plus("Informe um item de estoque e uma quantidade válida."), status_code=303)
    cor = normalizar_cor(form.get("cor")) if item_controla_cor(item) else ""
    if item_controla_cor(item) and not cor:
        return RedirectResponse("/organiza/estoque/entradas?erro=" + quote_plus(f"Informe a cor para {item.nome}."), status_code=303)
    custo = moeda_num(form.get("custo_unitario")) if (form.get("custo_unitario") or "").strip() else None
    obs = (form.get("observacao") or "").strip() or None
    db.add(EstoqueMovimento(
        item_id=item.id, tipo="ENTRADA", quantidade=quantidade, cor=cor or None,
        origem_tipo="MANUAL", custo_unitario=custo, observacao=obs, usuario_id=usuario.id,
    ))
    db.commit()
    return RedirectResponse("/organiza/estoque/entradas?ok=" + quote_plus(f"Entrada registrada: {item.nome} × {quantidade:g}."), status_code=303)


@app.post("/organiza/estoque/movimentos/{movimento_id}/excluir")
def estoque_movimento_excluir(movimento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    mov = db.query(EstoqueMovimento).filter(EstoqueMovimento.id == movimento_id).first()
    if not mov or (mov.origem_tipo or "").upper() != "MANUAL":
        return RedirectResponse("/organiza/estoque?erro=" + quote_plus("Somente lançamentos manuais podem ser estornados."), status_code=303)
    ja = db.query(EstoqueMovimento.id).filter(EstoqueMovimento.origem_tipo == "ESTORNO", EstoqueMovimento.origem_id == mov.id).first()
    if ja:
        return RedirectResponse("/organiza/estoque?erro=" + quote_plus("Este lançamento já foi estornado."), status_code=303)
    db.add(EstoqueMovimento(
        item_id=mov.item_id, tipo="SAIDA" if (mov.tipo or "").upper() == "ENTRADA" else "ENTRADA",
        quantidade=float(mov.quantidade or 0), cor=mov.cor, origem_tipo="ESTORNO", origem_id=mov.id,
        custo_unitario=mov.custo_unitario, observacao=f"Estorno do lançamento manual #{mov.id}", usuario_id=usuario.id,
    ))
    mov.observacao = ((mov.observacao or "") + f" | ESTORNADO em {datetime.now().strftime('%d/%m/%Y %H:%M')}").strip(" |")
    db.commit()
    return RedirectResponse("/organiza/estoque?ok=" + quote_plus("Lançamento manual estornado e mantido no histórico."), status_code=303)


@app.get("/organiza/itens", response_class=HTMLResponse)
def itens_lista(request: Request, busca: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    q = db.query(Item)
    termo = busca.strip()
    if termo:
        filtro = f"%{termo}%"
        q = q.filter(or_(Item.nome.ilike(filtro), Item.categoria.ilike(filtro)))
    itens = q.options(selectinload(Item.fornecedor)).order_by(func.upper(Item.categoria).asc(), func.upper(Item.nome).asc()).all()
    saldos_itens, _cores_itens = estoque_saldos(db)
    saldos_por_id = {l["item"].id: l for l in saldos_itens}
    for item in itens:
        item.controla_estoque_ui = item_controla_estoque(item)
        item.estoque_minimo_ui = float((saldos_por_id.get(item.id) or {}).get("minimo") or 0)
    fornecedores = db.query(Fornecedor).filter(Fornecedor.ativo == 1).order_by(func.upper(Fornecedor.nome)).all()
    minimos_linhas = _linhas_minimos_itens(db)
    itens_cor_ids = {l["item"].id for l in minimos_linhas if l.get("controla_cor")}
    existentes = [r[0] for r in db.query(Item.categoria).filter(Item.categoria.isnot(None)).distinct().order_by(Item.categoria).all() if r[0]]
    categorias = []
    for categoria in CATEGORIAS_ITENS_PADRAO + existentes:
        if categoria and categoria not in categorias:
            categorias.append(categoria)
    return templates.TemplateResponse("organiza/itens.html", {
        "request": request, "usuario": usuario, "itens": itens, "categorias": categorias, "busca": busca,
        "fornecedores": fornecedores, "minimos_linhas": minimos_linhas, "itens_cor_ids": itens_cor_ids,
        "ok": request.query_params.get("ok", ""), "erro": request.query_params.get("erro", ""),
    })


@app.post("/organiza/itens/salvar-planilha")
async def itens_salvar_planilha(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    """Salva a planilha de Itens em lote, como uma planilha de Excel.

    A interface envia nome, categoria, custo, preço, controle de estoque e status
    de todas as linhas visíveis. Não há mais necessidade de abrir cada item ou
    clicar em Ativar/Inativar individualmente.
    """
    form = dict(await request.form())
    ids = []
    for chave in form.keys():
        if chave.startswith("nome_"):
            try:
                ids.append(int(chave.split("_", 1)[1]))
            except (TypeError, ValueError):
                pass
    ids = sorted(set(ids))
    if not ids:
        return RedirectResponse("/organiza/itens?erro=" + quote_plus("Nenhum item foi enviado para salvar."), status_code=303)

    itens_edicao = db.query(Item).filter(Item.id.in_(ids)).all()
    por_id = {i.id: i for i in itens_edicao}

    # Valida os nomes finais antes de gravar qualquer linha para evitar conflito
    # com a restrição UNIQUE do banco e manter o salvamento realmente em lote.
    nomes_finais = {}
    for item_id in ids:
        item = por_id.get(item_id)
        if not item:
            continue
        nome = normalizar_nome_item(form.get(f"nome_{item_id}")) or item.nome
        nomes_finais[item_id] = nome
    nomes_editados_normalizados = {}
    for item_id, nome in nomes_finais.items():
        chave = nome.casefold()
        if chave in nomes_editados_normalizados and nomes_editados_normalizados[chave] != item_id:
            return RedirectResponse("/organiza/itens?erro=" + quote_plus(f"Nome duplicado na planilha: {nome}."), status_code=303)
        nomes_editados_normalizados[chave] = item_id
    existentes_fora = db.query(Item.id, Item.nome).filter(~Item.id.in_(ids)).all()
    existentes_fora = {str(nome or "").casefold(): iid for iid, nome in existentes_fora}
    for item_id, nome in nomes_finais.items():
        if nome.casefold() in existentes_fora:
            return RedirectResponse("/organiza/itens?erro=" + quote_plus(f"Já existe outro item chamado {nome}."), status_code=303)

    saldos_1226, _cores_1226 = estoque_saldos(db)
    saldos_por_id_1226 = {l["item"].id: l for l in saldos_1226}
    alterados = 0
    estoque_alterado = False
    for item_id in ids:
        item = por_id.get(item_id)
        if not item:
            continue
        nome = nomes_finais[item_id]
        categoria = (form.get(f"categoria_{item.id}") or item.categoria or "Geral").strip() or "Geral"
        if _texto_sem_acento(categoria) == "INFORMATICA":
            categoria = "Info e Eletrônicos"
        controla = 1 if str(form.get(f"controla_estoque_{item.id}") or "0") == "1" else 0
        ativo = 1 if str(form.get(f"ativo_{item.id}") or "0") == "1" else 0
        custo = moeda_num(form.get(f"custo_{item.id}"))
        preco = moeda_num(form.get(f"preco_{item.id}"))
        unidade = _normalizar_unidade_item(form.get(f"unidade_{item.id}"))
        try:
            fornecedor_id = int(form.get(f"fornecedor_{item.id}") or 0) or None
        except (TypeError, ValueError):
            fornecedor_id = None
        if fornecedor_id and not db.query(Fornecedor.id).filter(Fornecedor.id == fornecedor_id, Fornecedor.ativo == 1).first():
            fornecedor_id = None
        minimo_txt = str(form.get(f"minimo_{item.id}") or "").strip()
        minimo_novo = None
        if minimo_txt != "" and not item_controla_cor(item):
            try:
                minimo_novo = max(float(minimo_txt.replace(",", ".")), 0)
            except ValueError:
                return RedirectResponse("/organiza/itens?erro=" + quote_plus(f"Estoque mínimo inválido para {nome}."), status_code=303)

        if _texto_sem_acento(categoria) in ESTOQUE_CATEGORIAS_SEM_CONTROLE or _texto_sem_acento(nome) in ESTOQUE_ITENS_SEM_CONTROLE:
            controla = 0

        antes_controlava = item_controla_estoque(item)
        mudou = (
            item.nome != nome or item.categoria != categoria or int(item.controla_estoque or 0) != controla
            or float(item.preco_custo or 0) != float(custo or 0) or float(item.preco_venda or 0) != float(preco or 0)
            or int(item.ativo or 0) != ativo or item.fornecedor_id != fornecedor_id
            or _normalizar_unidade_item(getattr(item, "unidade", "UN")) != unidade
            or (minimo_novo is not None and abs(float((saldos_por_id_1226.get(item.id) or {}).get("minimo") or 0) - minimo_novo) > 0.0001)
        )
        if not mudou:
            continue

        item.nome = nome
        item.categoria = categoria
        item.controla_estoque = controla
        item.preco_custo = custo
        item.preco_venda = preco
        item.fornecedor_id = fornecedor_id
        item.unidade = unidade
        item.ativo = ativo
        if minimo_novo is not None:
            _salvar_minimo_estoque(db, item.id, "", minimo_novo)
        depois_controla = item_controla_estoque(item)
        if antes_controlava != depois_controla:
            estoque_alterado = True
        if not depois_controla:
            db.query(EstoqueReserva).filter(EstoqueReserva.item_id == item.id).delete(synchronize_session=False)
        alterados += 1

    # Recalcula movimentos das vendas em produção quando a participação no estoque mudou.
    if estoque_alterado:
        for eq_aberto in db.query(Equipamento).filter(Equipamento.status.in_(tuple(ESTOQUE_VENDA_A_FAZER))).all():
            sincronizar_estoque_venda(eq_aberto, db)
        manut_ids = [mid for (mid,) in db.query(Manutencao.id).filter(~Manutencao.status.in_(tuple(ESTOQUE_MANUTENCAO_FINAL | ESTOQUE_MANUTENCAO_CANCELADA))).all()]
        for mid in manut_ids:
            manut = carregar_manutencao(db, mid)
            if manut:
                sincronizar_estoque_manutencao(manut, db)

    db.commit()
    return RedirectResponse("/organiza/itens?ok=" + quote_plus(f"Planilha salva. {alterados} item(ns) alterado(s)."), status_code=303)


@app.post("/organiza/itens/novo")
async def item_novo(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    nome = normalizar_nome_item(form.get("nome"))
    destino = (form.get("next") or "/organiza/itens").strip()
    if not destino.startswith("/"):
        destino = "/organiza/itens"
    if nome:
        existente = db.query(Item).filter(func.lower(Item.nome) == nome.lower()).first()
        if not existente:
            categoria = (form.get("categoria") or "Geral").strip() or "Geral"
            if _texto_sem_acento(nome) in ESTOQUE_ITENS_SEM_CONTROLE:
                categoria = "Sistema"
            controla = 1 if str(form.get("controla_estoque") or "1").strip() == "1" else 0
            if _texto_sem_acento(categoria) in ESTOQUE_CATEGORIAS_SEM_CONTROLE or _texto_sem_acento(nome) in ESTOQUE_ITENS_SEM_CONTROLE:
                controla = 0
            try:
                fornecedor_id = int(form.get("fornecedor_id") or 0) or None
            except (TypeError, ValueError):
                fornecedor_id = None
            if fornecedor_id and not db.query(Fornecedor.id).filter(Fornecedor.id == fornecedor_id, Fornecedor.ativo == 1).first():
                fornecedor_id = None
            novo_item = Item(
                nome=nome,
                codigo=None,
                categoria=categoria,
                controla_estoque=controla,
                fornecedor_id=fornecedor_id,
                unidade=_normalizar_unidade_item(form.get("unidade")),
                preco_custo=moeda_num(form.get("preco_custo")),
                preco_venda=moeda_num(form.get("preco_venda")),
                ativo=1,
            )
            db.add(novo_item)
            db.flush()
            minimo_txt = str(form.get("estoque_minimo") or "").strip()
            if minimo_txt and not item_controla_cor(novo_item):
                try:
                    _salvar_minimo_estoque(db, novo_item.id, "", max(float(minimo_txt.replace(",", ".")), 0))
                except ValueError:
                    pass
            db.commit()
    return RedirectResponse(destino, status_code=303)


@app.post("/organiza/fornecedores/novo")
async def fornecedor_novo(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    nome = (form.get("nome") or "").strip()
    if not nome:
        return RedirectResponse("/organiza/itens?erro=" + quote_plus("Informe o nome do fornecedor."), status_code=303)
    existente = db.query(Fornecedor).filter(func.lower(Fornecedor.nome) == nome.lower()).first()
    if existente:
        if not existente.ativo:
            existente.ativo = 1
            db.commit()
            return RedirectResponse("/organiza/itens?ok=" + quote_plus(f"Fornecedor {existente.nome} reativado."), status_code=303)
        return RedirectResponse("/organiza/itens?erro=" + quote_plus("Este fornecedor já está cadastrado."), status_code=303)
    db.add(Fornecedor(nome=nome, ativo=1))
    db.commit()
    return RedirectResponse("/organiza/itens?ok=" + quote_plus(f"Fornecedor {nome} criado."), status_code=303)


@app.post("/organiza/itens/minimos")
async def itens_minimos_salvar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = await request.form()
    item_ids = form.getlist("item_id")
    cores = form.getlist("cor")
    minimos = form.getlist("minimo")
    alterados = 0
    for idx, bruto_id in enumerate(item_ids):
        try:
            item_id = int(bruto_id or 0)
        except (TypeError, ValueError):
            continue
        item = db.query(Item).filter(Item.id == item_id).first()
        if not item or not item_controla_estoque(item):
            continue
        cor = normalizar_cor(cores[idx] if idx < len(cores) else "") if item_controla_cor(item) else ""
        minimo_txt = str(minimos[idx] if idx < len(minimos) else "").strip()
        if minimo_txt == "":
            continue
        try:
            minimo = max(float(minimo_txt.replace(",", ".")), 0)
        except ValueError:
            return RedirectResponse("/organiza/itens?erro=" + quote_plus(f"Estoque mínimo inválido para {item.nome}."), status_code=303)
        if item_controla_cor(item) and not cor:
            return RedirectResponse("/organiza/itens?erro=" + quote_plus(f"Informe a cor para definir o mínimo de {item.nome}."), status_code=303)
        _salvar_minimo_estoque(db, item.id, cor, minimo)
        alterados += 1
    db.commit()
    return RedirectResponse("/organiza/itens?ok=" + quote_plus(f"Estoque mínimo atualizado em {alterados} linha(s)."), status_code=303)


@app.post("/organiza/itens/{item_id}/editar")
async def item_editar(item_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(404)
    form = dict(await request.form())
    nome = normalizar_nome_item(form.get("nome"))
    repetido = db.query(Item).filter(func.lower(Item.nome) == nome.lower(), Item.id != item_id).first() if nome else None
    if nome and not repetido:
        item.nome = nome
        item.categoria = (form.get("categoria") or "Geral").strip() or "Geral"
        if _texto_sem_acento(item.nome) in ESTOQUE_ITENS_SEM_CONTROLE:
            item.categoria = "Sistema"
        controla = 1 if str(form.get("controla_estoque") or "1").strip() == "1" else 0
        if _texto_sem_acento(item.categoria) in ESTOQUE_CATEGORIAS_SEM_CONTROLE or _texto_sem_acento(item.nome) in ESTOQUE_ITENS_SEM_CONTROLE:
            controla = 0
        item.controla_estoque = controla
        try:
            fornecedor_id = int(form.get("fornecedor_id") or 0) or None
        except (TypeError, ValueError):
            fornecedor_id = None
        if fornecedor_id and not db.query(Fornecedor.id).filter(Fornecedor.id == fornecedor_id, Fornecedor.ativo == 1).first():
            fornecedor_id = None
        item.fornecedor_id = fornecedor_id
        item.preco_custo = moeda_num(form.get("preco_custo"))
        item.preco_venda = moeda_num(form.get("preco_venda"))
        # Se o item saiu do controle, remove resíduos de reserva legada. Movimentos físicos históricos permanecem.
        if not item_controla_estoque(item):
            db.query(EstoqueReserva).filter(EstoqueReserva.item_id == item.id).delete(synchronize_session=False)
        # Reaplica a regra aos trabalhos em produção, sem tocar em histórico concluído.
        for eq_aberto in db.query(Equipamento).filter(Equipamento.status.in_(tuple(ESTOQUE_VENDA_A_FAZER))).all():
            sincronizar_estoque_venda(eq_aberto, db)
        manut_ids = [mid for (mid,) in db.query(Manutencao.id).filter(~Manutencao.status.in_(tuple(ESTOQUE_MANUTENCAO_FINAL | ESTOQUE_MANUTENCAO_CANCELADA))).all()]
        for mid in manut_ids:
            manut = carregar_manutencao(db, mid)
            if manut:
                sincronizar_estoque_manutencao(manut, db)
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



@app.get("/organiza/manutencoes/rapida", response_class=HTMLResponse)
def manutencao_rapida_form(
    request: Request,
    salva: int = 0,
    erro: str = "",
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Tela enxuta para registrar uma manutenção em poucos toques.

    Não depende do fluxo de aprovação do orçamento. Ao salvar, a manutenção já
    nasce aprovada administrativamente e os materiais passam a comprometer o
    estoque conforme a regra normal de manutenção aprovada.
    """
    clientes = (
        db.query(Cliente)
        .options(selectinload(Cliente.equipamentos))
        .order_by(Cliente.nome)
        .all()
    )
    clientes_dados = []
    for cliente in clientes:
        equipamentos = [
            eq for eq in ordenar_equipamentos(cliente.equipamentos)
            if (eq.status or "Ativo") == "Ativo"
        ]
        if not equipamentos:
            continue
        clientes_dados.append({
            "id": cliente.id,
            "nome": cliente.nome,
            "telefone": cliente.telefone or "",
            "equipamentos": [
                {
                    "id": eq.id,
                    "rotulo": f"{rotulo_maquina(eq)} · {eq.tipo or ''}{(' · ' + eq.modelo) if eq.modelo else ''} · Cód. {codigo_tecnico(eq)}",
                }
                for eq in equipamentos
            ],
        })
    itens = db.query(Item).filter(Item.ativo == 1).order_by(Item.nome).all()
    itens_dados = [
        {
            "id": item.id,
            "nome": item.nome,
            "preco": round(float(item.preco_venda or 0), 2),
            "unidade": (item.unidade or "UN").upper(),
            "categoria": item.categoria or "Geral",
            "controla_estoque": bool(item_controla_estoque(item)),
        }
        for item in itens
    ]
    manutencao_salva = carregar_manutencao(db, salva) if salva else None
    whatsapp_salvo = ""
    if manutencao_salva:
        whatsapp_salvo = _whatsapp_manutencao_rapida_url(manutencao_salva, db)
    return templates.TemplateResponse("organiza/manutencao_rapida.html", {
        "request": request,
        "usuario": usuario,
        "clientes_dados": clientes_dados,
        "itens_dados": itens_dados,
        "manutencao_salva": manutencao_salva,
        "whatsapp_salvo": whatsapp_salvo,
        "erro": erro,
        "hoje_pagamento": _hoje_organiza().isoformat(),
    })


def _manutencao_rapida_mensagem(m: Manutencao) -> str:
    o = _orcamento_atual(m)
    if not o:
        return ""
    totais = totais_orcamento(o)
    linhas = [
        f"Olá, {m.cliente.nome}!",
        "",
        f"Segue o orçamento da manutenção #{m.id}:",
        f"Equipamento: {descricao_equipamento(m.equipamento)}",
    ]
    if float(o.valor_manutencao or 0) > 0:
        linhas.append(f"Serviço de manutenção: {formatar_moeda(o.valor_manutencao)}")
    if o.itens:
        linhas.extend(["", "Materiais:"])
        for oi in o.itens:
            unidade = "UN"
            if oi.item_id:
                # O item pode ser alterado depois; a mensagem usa a unidade atual.
                item = next((x for x in getattr(o, "_itens_catalogo_rapido", []) if x.id == oi.item_id), None)
                if item:
                    unidade = (item.unidade or "UN").upper()
            linhas.append(f"• {oi.quantidade} {unidade} · {oi.descricao} — {formatar_moeda(float(oi.preco_venda or 0) * int(oi.quantidade or 0))}")
    if float(totais.get("desconto_informado", 0) or 0) > 0:
        linhas.extend(["", f"Desconto: {formatar_moeda(totais['desconto_informado'])}"])
    linhas.append(f"Total: {formatar_moeda(totais.get('aprovado', 0))}")
    recebido = float(totais.get("recebido", 0) or 0)
    if recebido > 0:
        linhas.append(f"Pagamento registrado: {formatar_moeda(recebido)}")
        linhas.append(f"Saldo: {formatar_moeda(totais.get('falta', 0))}")
    linhas.extend(["", "Mensagem informativa, sem necessidade de aprovação pelo link.", "", "Karaokê RJ"])
    return "\n".join(linhas)


def _whatsapp_manutencao_rapida_url(m: Manutencao, db: Session | None = None) -> str:
    o = _orcamento_atual(m)
    if not o or not m.cliente:
        return ""
    item_ids = [oi.item_id for oi in o.itens if oi.item_id]
    itens_catalogo = db.query(Item).filter(Item.id.in_(item_ids)).all() if (db is not None and item_ids) else []
    setattr(o, "_itens_catalogo_rapido", itens_catalogo)
    return _whatsapp_url_pronta(m.cliente.whatsapp_completo() or "", _manutencao_rapida_mensagem(m))


@app.post("/organiza/manutencoes/rapida")
async def manutencao_rapida_salvar(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    form = await request.form()

    def _int(v, default=0):
        try:
            return int(v or default)
        except (TypeError, ValueError):
            return default

    cliente_id = _int(form.get("cliente_id"))
    equipamento_id = _int(form.get("equipamento_id"))
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    equipamento = db.query(Equipamento).filter(
        Equipamento.id == equipamento_id,
        Equipamento.cliente_id == cliente_id,
        Equipamento.status == "Ativo",
    ).first()
    if not cliente or not equipamento:
        return RedirectResponse("/organiza/manutencoes/rapida?erro=" + quote_plus("Selecione o cliente e o equipamento."), status_code=303)

    nomes = list(form.getlist("item_nome"))
    ids = list(form.getlist("item_id"))
    quantidades = list(form.getlist("quantidade"))
    valores = list(form.getlist("preco_venda"))
    linhas = []
    max_len = max(len(nomes), len(ids), len(quantidades), len(valores), 0)
    for idx in range(max_len):
        item_id = _int(ids[idx] if idx < len(ids) else 0)
        nome = (nomes[idx] if idx < len(nomes) else "").strip()
        item = None
        if item_id:
            item = db.query(Item).filter(Item.id == item_id, Item.ativo == 1).first()
        if not item and nome:
            item = db.query(Item).filter(func.lower(Item.nome) == nome.lower(), Item.ativo == 1).first()
        if not item:
            continue
        qtd = max(_int(quantidades[idx] if idx < len(quantidades) else 1, 1), 1)
        valor_txt = valores[idx] if idx < len(valores) else ""
        preco = moeda_num(valor_txt) if str(valor_txt or "").strip() else float(item.preco_venda or 0)
        linhas.append((item, qtd, max(preco, 0.0)))

    valor_servico = max(moeda_num(form.get("valor_manutencao") or ""), 0.0)
    desconto = max(moeda_num(form.get("desconto") or ""), 0.0)
    bruto = valor_servico + sum(qtd * preco for _item, qtd, preco in linhas)
    if bruto <= 0:
        return RedirectResponse("/organiza/manutencoes/rapida?erro=" + quote_plus("Informe pelo menos um material ou valor de serviço."), status_code=303)
    desconto = min(desconto, bruto)
    total = round(bruto - desconto, 2)

    observacao = (form.get("observacao") or "").strip() or None
    m = Manutencao(
        cliente_id=cliente.id,
        equipamento_id=equipamento.id,
        defeito=observacao or "Manutenção rápida",
        observacao=observacao,
        tipo_atendimento="loja",
        status="Em manutenção",
        recebido_em=datetime.now(),
        descontar_estoque=1 if form.get("descontar_estoque") else 0,
    )
    db.add(m)
    db.flush()

    registrar_pagamento = bool(form.get("registrar_pagamento"))
    forma_pagamento = (form.get("forma_pagamento") or "").strip()
    o = Orcamento(
        manutencao=m,
        versao=1,
        token=secrets.token_urlsafe(24),
        status="Aprovado manualmente",
        desconto=desconto,
        valor_manutencao=valor_servico,
        forma_pagamento_orcamento=forma_pagamento or "A combinar",
        aprovado_em=datetime.now(),
    )
    db.add(o)
    for item, qtd, preco in linhas:
        o.itens.append(OrcamentoItem(
            item_id=item.id,
            descricao=item.nome,
            quantidade=qtd,
            preco_custo=float(item.preco_custo or 0),
            preco_venda=preco,
            opcional=0,
            aprovado=1,
        ))
    db.flush()

    if registrar_pagamento:
        valor_pagamento = moeda_num(form.get("valor_pagamento") or "") or total
        if valor_pagamento <= 0 or not forma_pagamento:
            db.rollback()
            return RedirectResponse("/organiza/manutencoes/rapida?erro=" + quote_plus("Para registrar pagamento, informe a forma de pagamento."), status_code=303)
        if valor_pagamento > total + 0.01:
            db.rollback()
            return RedirectResponse("/organiza/manutencoes/rapida?erro=" + quote_plus("O pagamento não pode ser maior que o total da manutenção."), status_code=303)
        o.pagamentos.append(Pagamento(
            data=data_form(form.get("data_pagamento") or "") or _hoje_organiza(),
            valor=round(valor_pagamento, 2),
            forma=forma_pagamento,
            banco=forma_pagamento,
            observacao=_obs_pagamento_padrao(equipamento, cliente, "Manutenção rápida"),
        ))
        db.flush()

    sincronizar_estoque_manutencao(m, db)
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/rapida?salva={m.id}", status_code=303)


@app.get("/organiza/manutencoes/{manutencao_id}/whatsapp-rapido")
def manutencao_whatsapp_rapido(
    manutencao_id: int,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    o = _orcamento_atual(m)
    if not o:
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)
    # Injeta unidades apenas durante a montagem da mensagem, sem consultas externas.
    item_ids = [oi.item_id for oi in o.itens if oi.item_id]
    setattr(o, "_itens_catalogo_rapido", db.query(Item).filter(Item.id.in_(item_ids)).all() if item_ids else [])
    return RedirectResponse(_whatsapp_url_pronta(m.cliente.whatsapp_completo() or "", _manutencao_rapida_mensagem(m)), status_code=303)


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
    cobranca_infinitepay = _infinitepay_cobranca_pendente(db, "MANUTENCAO", m.id)
    return templates.TemplateResponse("organiza/manutencao_detalhe.html", {
        "request": request, "usuario": usuario, "m": m, "orcamento": orcamento,
        "itens_catalogo": itens, "equipamentos_cliente": equipamentos_cliente, "totais": totais,
        "etapa_atual": etapa_manutencao(m), "manutencoes_prontas_cliente": prontas_cliente,
        "mensagem_retirada": mensagem_retirada, "estoque_cores_orcamento": contexto_cores_manutencao(db, orcamento),
        "resumo_estoque_manutencao": resumo_estoque_manutencao(m, orcamento, db),
        "hoje_pagamento": _hoje_organiza().isoformat(),
        "infinitepay_habilitada": bool(INFINITEPAY_HANDLE and orcamento and _orcamento_aprovado(orcamento)),
        "cobranca_infinitepay": cobranca_infinitepay,
        "infinitepay_erro": request.query_params.get("infinitepay_erro", ""),
        "infinitepay_sucesso": request.query_params.get("infinitepay_sucesso", ""),
    })


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
    sincronizar_estoque_manutencao(m, db)
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
        db.flush()
        sincronizar_estoque_manutencao(m, db)
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
        db.flush()
        sincronizar_estoque_manutencao(m, db)
        db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)



@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/item/{orcamento_item_id}/cores")
async def orcamento_item_cores_salvar(
    manutencao_id: int,
    orcamento_item_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    ids_orcamentos = [o.id for o in m.orcamentos]
    oi = db.query(OrcamentoItem).filter(
        OrcamentoItem.id == orcamento_item_id,
        OrcamentoItem.orcamento_id.in_(ids_orcamentos),
    ).first()
    if not oi:
        raise HTTPException(404)
    form = dict(await request.form())
    salvar_cores_manutencao(oi, form, db)
    db.flush()
    sincronizar_estoque_manutencao(m, db)
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-2", status_code=303)


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
    sincronizar_estoque_manutencao(m, db)
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}#etapa-3", status_code=303)



@app.post("/organiza/manutencoes/{manutencao_id}/estoque/regra")
async def manutencao_estoque_regra(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    form = await request.form()
    m.descontar_estoque = 1 if str(form.get("descontar_estoque") or "").strip() == "1" else 0
    sincronizar_estoque_manutencao(m, db)
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?estoque_regra=1#estoque-resumo", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/estoque/ressincronizar")
def manutencao_estoque_ressincronizar(
    manutencao_id: int,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Recalcula reserva/saída desta manutenção sem duplicar movimentos."""
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    sincronizar_estoque_manutencao(m, db)
    db.commit()
    return RedirectResponse(
        f"/organiza/manutencoes/{manutencao_id}?estoque_corrigido=1#etapa-3",
        status_code=303,
    )

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
            data=data_form(form.get("data") or "") or _hoje_organiza(),
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
    data_pag = data_form(form.get("data") or "") or _hoje_organiza()
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


@app.post("/organiza/manutencoes/{manutencao_id}/pagamento/infinitepay")
async def manutencao_pagamento_infinitepay(
    manutencao_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    m = carregar_manutencao(db, manutencao_id)
    if not m:
        raise HTTPException(404)
    o = _orcamento_atual(m)
    if not o or not _orcamento_aprovado(o):
        msg = quote_plus("A cobrança InfinitePay só pode ser gerada depois que o orçamento estiver aprovado.")
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?infinitepay_erro={msg}#pagamento-independente", status_code=303)
    totais = totais_orcamento(o)
    saldo = max(float(totais.get("falta", 0) or 0), 0.0)
    form = dict(await request.form())
    valor = moeda_num((form.get("valor") or "").strip()) or saldo
    if valor <= 0 or valor > saldo + 0.01:
        msg = quote_plus("Informe um valor válido, limitado ao saldo atual da manutenção.")
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?infinitepay_erro={msg}#pagamento-independente", status_code=303)
    descricao = f"Manutenção #{m.id} - {m.cliente.nome if m.cliente else 'Cliente'} - {rotulo_maquina(m.equipamento) if m.equipamento else 'Equipamento'}"
    try:
        cobranca = _infinitepay_criar_cobranca_organiza(
            db,
            origem_tipo="MANUTENCAO",
            origem_id=m.id,
            valor=valor,
            cliente=m.cliente,
            descricao=descricao,
        )
    except Exception as exc:
        msg = quote_plus(str(exc))
        return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?infinitepay_erro={msg}#pagamento-independente", status_code=303)
    msg = quote_plus("Cobrança InfinitePay pronta. Abra ou copie o link abaixo.")
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?infinitepay_sucesso={msg}#pagamento-independente", status_code=303)


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
        data_pag = data_form(form.get("data") or "") or _hoje_organiza()
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
    m = carregar_manutencao(db, manutencao_id)
    db.flush()
    if m:
        sincronizar_estoque_manutencao(m, db)
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
    # 1.1.74: manutenção envia ao Connect somente o saldo global atual.
    integ = _registro_integracao(db, "manutencao", p.id)
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

    # A reserva da Manutenção a Fazer vira saída física somente agora, no encerramento.
    sincronizar_estoque_manutencao(m, db)

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
        .order_by(Item.nome.asc())
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
        sincronizar_estoque_manutencao(manutencao, db)

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
        "erro": request.query_params.get("erro", ""), "sucesso": request.query_params.get("sucesso", ""), "hoje": _hoje_organiza().isoformat()
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
    data_pagamento = data_form(form.get("data")) or _hoje_organiza()
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


def _saldos_globais_connect(db: Session) -> list[dict]:
    """Quatro saldos globais espelhados no Connect, com composição auditável.

    O Organiza é a fonte da verdade. O Connect apenas consulta o saldo atual.
    A tela do Organiza mostra quais registros formam cada total para facilitar
    a conferência antes da sincronização.
    """
    # Vendas: valor total menos pagamentos registrados no Organiza.
    equipamentos_venda = (
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
    equipamentos_venda = [eq for eq in equipamentos_venda if equipamento_eh_venda(eq)]
    _migrar_pagamentos_legados_vendas(db, equipamentos_venda)

    ids_venda = [eq.id for eq in equipamentos_venda]
    totais_pag_venda = {}
    if ids_venda:
        totais_pag_venda = {
            int(eid): float(total or 0)
            for eid, total in db.query(
                PagamentoVenda.equipamento_id,
                func.coalesce(func.sum(PagamentoVenda.valor), 0),
            )
            .filter(PagamentoVenda.equipamento_id.in_(ids_venda))
            .group_by(PagamentoVenda.equipamento_id).all()
        }

    vendas_abertas = 0.0
    detalhes_vendas = []
    for eq in equipamentos_venda:
        total = max(float(moeda_num(eq.valor)), 0.0)
        recebido = max(
            float(totais_pag_venda.get(eq.id, 0.0)),
            float(moeda_num(eq.pago)),
            0.0,
        )
        saldo = max(total - recebido, 0.0)
        vendas_abertas += saldo
        if saldo > 0.009:
            cliente_nome = (eq.cliente.nome if eq.cliente else "") or "Sem cliente"
            descricao = eq.produto_venda_nome_snapshot or eq.modelo or eq.tipo or f"Venda #{eq.id}"
            detalhes_vendas.append({
                "id": eq.id,
                "cliente": cliente_nome,
                "descricao": descricao,
                "status": eq.status or "",
                "total": round(total, 2),
                "recebido": round(recebido, 2),
                "saldo": round(saldo, 2),
                "url": f"/organiza/vendas/{eq.id}/pagamentos",
                "finalizado": (eq.status or "").strip().lower() == "entregue",
            })
    detalhes_vendas.sort(key=lambda x: (x["cliente"].lower(), x["id"]))

    # Manutenções: orçamento aprovado menos pagamentos registrados.
    manutencoes_abertas = 0.0
    detalhes_manutencoes = []
    manutencoes = db.query(Manutencao).options(
        selectinload(Manutencao.cliente),
        selectinload(Manutencao.equipamento),
        selectinload(Manutencao.orcamentos).selectinload(Orcamento.itens),
        selectinload(Manutencao.orcamentos).selectinload(Orcamento.pagamentos),
    ).filter(func.upper(func.coalesce(Manutencao.status, "")) != "CANCELADA").all()
    for manut in manutencoes:
        total, recebido, saldo = _saldo_manutencao(manut)
        saldo = max(float(saldo or 0), 0.0)
        orcamento_atual = _orcamento_atual(manut)
        aprovado_connect = _orcamento_aprovado(orcamento_atual)
        # O Connect só pode enxergar valor que já foi efetivamente aprovado pelo cliente.
        # Orçamentos ainda aguardando aprovação continuam visíveis na composição apenas
        # para acompanhamento interno, sem aumentar o saldo enviado.
        if aprovado_connect:
            manutencoes_abertas += saldo
        if saldo > 0.009:
            cliente_nome = (manut.cliente.nome if manut.cliente else "") or "Sem cliente"
            equipamento_nome = rotulo_maquina(manut.equipamento) if manut.equipamento else f"Manutenção #{manut.id}"
            detalhes_manutencoes.append({
                "id": manut.id,
                "cliente": cliente_nome,
                "descricao": equipamento_nome,
                "status": manut.status or "",
                "status_orcamento": (orcamento_atual.status if orcamento_atual else "Sem orçamento") or "",
                "total": round(float(total or 0), 2),
                "recebido": round(float(recebido or 0), 2),
                "saldo": round(saldo, 2),
                "url": f"/organiza/manutencoes/{manut.id}",
                "finalizado": bool(manut.entregue_em) or (manut.status or "").strip().lower() in {"encerrada", "entregue", "finalizada"},
                "connect_incluido": bool(aprovado_connect),
            })
    detalhes_manutencoes.sort(key=lambda x: (x["cliente"].lower(), x["id"]))

    # Atualizações: valor real lançado (inclusive adicional/frete) menos o que já foi pago.
    atualizacoes_abertas = 0.0
    detalhes_atualizacoes = []
    for compra in db.query(AtualizacaoCompra).all():
        if (compra.status or "").strip().upper() in {"CANCELADO", "CANCELADA"}:
            continue
        total_cent = int(compra.valor_a_pagar_centavos or 0) + int(compra.frete_centavos or 0)
        pago_cent = int(compra.valor_pago_centavos or 0)
        saldo = max((total_cent - pago_cent) / 100.0, 0.0)
        atualizacoes_abertas += saldo
        if saldo > 0.009:
            detalhes_atualizacoes.append({
                "id": compra.id,
                "cliente": getattr(getattr(compra, "cliente", None), "nome", "") or f"Atualização #{compra.id}",
                "descricao": getattr(compra, "periodo", None) or getattr(compra, "pacote_ate", None) or "Atualização",
                "status": compra.status or "",
                "total": round(total_cent / 100.0, 2),
                "recebido": round(pago_cent / 100.0, 2),
                "saldo": round(saldo, 2),
                "url": "",
                "finalizado": False,
            })

    # Estoque: exatamente o total exibido no relatório de compras.
    relatorio_estoque = relatorio_compras_estoque(db)
    estoque_a_pagar = round(sum(float(x.get("custo_total") or 0) for x in relatorio_estoque), 2)
    detalhes_estoque = []
    for x in relatorio_estoque:
        valor = round(float(x.get("custo_total") or 0), 2)
        if valor <= 0.009:
            continue
        item_estoque = x.get("item")
        detalhes_estoque.append({
            "id": getattr(item_estoque, "id", 0) or 0,
            "cliente": getattr(item_estoque, "categoria", "") or "",
            "descricao": getattr(item_estoque, "nome", "") or "Item",
            "status": f"Comprar {x.get('comprar') or 0}" + (f" · {x.get('cor')}" if x.get("cor") else ""),
            "total": valor,
            "recebido": 0.0,
            "saldo": valor,
            "url": "/organiza/estoque/compras",
            "finalizado": False,
        })

    hoje = _hoje_organiza().isoformat()
    saldos = [
        {"chave": "vendas", "titulo": "A receber · Vendas", "natureza": "receber", "tipo": "venda", "valor": round(vendas_abertas, 2), "detalhes": detalhes_vendas},
        {"chave": "manutencoes", "titulo": "A receber · Manutenções", "natureza": "receber", "tipo": "manutencao", "valor": round(manutencoes_abertas, 2), "detalhes": detalhes_manutencoes},
        {"chave": "atualizacoes", "titulo": "A receber · Atualizações", "natureza": "receber", "tipo": "atualizacao", "valor": round(atualizacoes_abertas, 2), "detalhes": detalhes_atualizacoes},
        {"chave": "estoque", "titulo": "A pagar · Estoque / Compras", "natureza": "pagar", "tipo": "estoque", "valor": round(estoque_a_pagar, 2), "detalhes": detalhes_estoque},
    ]
    for idx, saldo in enumerate(saldos, start=1):
        saldo["registro_id"] = idx
        saldo["id_externo"] = f"ORGANIZA-SALDO-{saldo['chave'].upper()}"
        saldo["qtd_detalhes"] = len(saldo.get("detalhes") or [])
        saldo["qtd_enviados"] = sum(1 for d in (saldo.get("detalhes") or []) if d.get("connect_incluido", True))
        saldo["payload"] = {
            "id_externo": saldo["id_externo"],
            "tipo": saldo["tipo"],
            "natureza": saldo["natureza"],
            "cliente": "Karaokê RJ",
            "descricao": saldo["titulo"],
            "valor": saldo["valor"],
            "falta_receber": saldo["valor"] if saldo["natureza"] == "receber" else 0,
            "data_pagamento": hoje,
            "banco": "Organiza",
            "observacao": "Saldo global calculado no Organiza. O Connect é somente consulta.",
            "empresa_slug": "karaokerj",
        }
        integ = _registro_integracao(db, f"saldo_{saldo['chave']}", idx)
        saldo["integracao"] = integ
        saldo["hash_atual"] = _payload_hash(saldo["payload"])
        if integ and integ.enviado_em and integ.hash_conteudo == saldo["hash_atual"]:
            saldo["status_sync"] = "sincronizado"
        elif integ and integ.enviado_em:
            saldo["status_sync"] = "alterado"
        else:
            saldo["status_sync"] = "novo"
    return saldos


@app.get("/organiza/financeiro/conect", response_class=HTMLResponse)
def central_financeiro_conect(
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    saldos = _saldos_globais_connect(db)
    total_receber = round(sum(s["valor"] for s in saldos if s["natureza"] == "receber"), 2)
    total_pagar = round(sum(s["valor"] for s in saldos if s["natureza"] == "pagar"), 2)
    ultima_sync = max((s["integracao"].enviado_em for s in saldos if s.get("integracao") and s["integracao"].enviado_em), default=None)
    return templates.TemplateResponse("organiza/central_financeiro_conect.html", {
        "request": request,
        "usuario": usuario,
        "saldos": saldos,
        "total_receber": total_receber,
        "total_pagar": total_pagar,
        "ultima_sync": ultima_sync,
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

    saldos = _saldos_globais_connect(db)
    enviados = 0
    try:
        for saldo in saldos:
            payload = saldo["payload"]
            resposta = _enviar_para_connect(payload)
            origem = f"saldo_{saldo['chave']}"
            integ = _registro_integracao(db, origem, saldo["registro_id"])
            if not integ:
                integ = IntegracaoConect(
                    origem=origem,
                    registro_id=saldo["registro_id"],
                    id_externo=saldo["id_externo"],
                )
                db.add(integ)
            integ.hash_conteudo = _payload_hash(payload)
            integ.enviado_em = datetime.now()
            integ.resposta = json.dumps(resposta, ensure_ascii=False)[:4000]
            integ.ignorado = 0
            enviados += 1
        db.commit()
    except Exception as exc:
        db.rollback()
        msg = quote_plus(f"Erro ao sincronizar saldos com o Connect: {str(exc)}")
        return RedirectResponse(f"/organiza/financeiro/conect?erro={msg}", status_code=303)

    msg = quote_plus(f"{enviados} saldo(s) sincronizado(s) com o Connect.")
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
    frete = max(float(eq.frete_venda or 0), 0)
    subtotal_produto = max(round(total - frete, 2), 0)
    recebido = sum(float(p.valor or 0) for p in pagamentos)
    cobranca_infinitepay = _infinitepay_cobranca_pendente(db, "VENDA", eq.id)
    return templates.TemplateResponse("organiza/venda_pagamentos.html", {
        "request": request, "usuario": usuario, "venda": eq, "pagamentos": pagamentos,
        "subtotal_produto": subtotal_produto, "frete": frete,
        "total": total, "recebido": recebido, "saldo": max(total - recebido, 0),
        "hoje": _hoje_organiza().isoformat(), "erro": request.query_params.get("erro", ""),
        "observacao_padrao": _obs_pagamento_padrao(eq, eq.cliente),
        "infinitepay_habilitada": bool(INFINITEPAY_HANDLE),
        "cobranca_infinitepay": cobranca_infinitepay,
        "infinitepay_erro": request.query_params.get("infinitepay_erro", ""),
        "infinitepay_sucesso": request.query_params.get("infinitepay_sucesso", ""),
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
    data_pag = data_form(form.get("data")) or _hoje_organiza()
    forma = (form.get("forma") or "PIX").strip()
    banco = forma
    nome_comprovante = (form.get("observacao") or "").strip()
    observacao = _obs_pagamento_padrao(eq, eq.cliente, nome_comprovante)
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
    atualizar_datas_producao_venda(eq, data_pag)
    db.commit()
    return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos", status_code=303)



@app.post("/organiza/vendas/{equipamento_id}/pagamentos/infinitepay")
async def venda_pagamento_infinitepay(
    equipamento_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    eq = db.query(Equipamento).options(selectinload(Equipamento.cliente)).filter(Equipamento.id == equipamento_id).first()
    if not eq or not equipamento_eh_venda(eq):
        raise HTTPException(404)
    total = max(float(moeda_num(eq.valor)), 0.0)
    recebido = sum(float(p.valor or 0) for p in db.query(PagamentoVenda).filter(PagamentoVenda.equipamento_id == equipamento_id).all())
    saldo = max(round(total - recebido, 2), 0.0)
    form = dict(await request.form())
    valor = moeda_num((form.get("valor") or "").strip()) or saldo
    if valor <= 0 or valor > saldo + 0.01:
        msg = quote_plus("Informe um valor válido, limitado ao saldo atual da venda.")
        return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos?infinitepay_erro={msg}", status_code=303)
    produto_nome = eq.produto_venda_nome_snapshot or eq.modelo or eq.tipo or "Equipamento"
    catalogo_rotulo = "Plus" if (eq.catalogo_venda or "").upper() == "PLUS" else "Básico"
    frete = max(float(eq.frete_venda or 0), 0)
    subtotal_produto = max(round(total - frete, 2), 0)
    descricao = f"Venda #{eq.id} - {eq.cliente.nome if eq.cliente else 'Cliente'} - {produto_nome} - Catálogo {catalogo_rotulo}"
    itens_checkout = []
    # Só detalha produto + frete quando o valor solicitado corresponde ao total ainda
    # integral da venda. Em cobrança parcial, usa um item único com o valor exato.
    if recebido <= 0.009 and abs(valor - total) <= 0.01:
        if subtotal_produto > 0:
            itens_checkout.append({
                "quantity": 1,
                "price": int(round(subtotal_produto * 100)),
                "description": f"{produto_nome} - Catálogo {catalogo_rotulo}",
            })
        if frete > 0:
            itens_checkout.append({
                "quantity": 1,
                "price": int(round(frete * 100)),
                "description": "Frete / entrega",
            })
    try:
        _infinitepay_criar_cobranca_organiza(
            db,
            origem_tipo="VENDA",
            origem_id=eq.id,
            valor=valor,
            cliente=eq.cliente,
            descricao=descricao,
            itens_checkout=itens_checkout,
        )
    except Exception as exc:
        msg = quote_plus(str(exc))
        return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos?infinitepay_erro={msg}", status_code=303)
    msg = quote_plus("Cobrança InfinitePay pronta. Abra ou copie o link abaixo.")
    return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos?infinitepay_sucesso={msg}", status_code=303)


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
    # 1.1.74: o Connect recebe apenas o saldo global; pagamentos individuais ficam só no Organiza.
    integ = _registro_integracao(db, "venda", p.id)
    if integ:
        db.delete(integ)
    db.delete(p)
    db.commit()
    return RedirectResponse(f"/organiza/vendas/{equipamento_id}/pagamentos", status_code=303)


@app.get("/organiza/infinitepay/retorno", response_class=HTMLResponse, include_in_schema=False)
def organiza_infinitepay_retorno(request: Request, db: Session = Depends(get_db)):
    order_nsu = str(request.query_params.get("order_nsu") or "").strip()
    transaction_nsu = str(request.query_params.get("transaction_nsu") or request.query_params.get("transaction_id") or "").strip()
    invoice_slug = str(request.query_params.get("slug") or request.query_params.get("invoice_slug") or "").strip()
    receipt_url = str(request.query_params.get("receipt_url") or "").strip()
    capture_method = str(request.query_params.get("capture_method") or "").strip()

    cobranca = None
    if order_nsu:
        cobranca = db.query(InfinitePayCobrancaOrganiza).filter(InfinitePayCobrancaOrganiza.order_nsu == order_nsu).first()
    if not cobranca and transaction_nsu:
        cobranca = db.query(InfinitePayCobrancaOrganiza).filter(InfinitePayCobrancaOrganiza.transaction_nsu == transaction_nsu).first()
    if not cobranca:
        return templates.TemplateResponse("organiza/infinitepay_retorno.html", {
            "request": request, "status_retorno": "erro",
            "mensagem": "Não foi possível localizar esta cobrança no Organiza.", "cobranca": None,
        }, status_code=404, headers={"Cache-Control": "no-store"})

    if cobranca.status != "PAGO" and transaction_nsu and invoice_slug:
        try:
            check = _infinitepay_post_organiza(INFINITEPAY_PAYMENT_CHECK_URL, {
                "handle": INFINITEPAY_HANDLE,
                "order_nsu": cobranca.order_nsu,
                "transaction_nsu": transaction_nsu,
                "slug": invoice_slug,
            })
            if bool(check.get("success")) and bool(check.get("paid")):
                amount = int(check.get("amount") or 0)
                if amount == int(cobranca.valor_centavos or 0):
                    _infinitepay_registrar_pagamento_organiza(
                        db, cobranca,
                        transaction_nsu=transaction_nsu,
                        invoice_slug=invoice_slug,
                        receipt_url=receipt_url,
                        capture_method=str(check.get("capture_method") or capture_method or ""),
                        installments=int(check.get("installments") or 0),
                        paid_amount_centavos=int(check.get("paid_amount") or amount),
                    )
        except Exception:
            pass

    db.refresh(cobranca)
    pago = cobranca.status == "PAGO"
    return templates.TemplateResponse("organiza/infinitepay_retorno.html", {
        "request": request,
        "status_retorno": "pago" if pago else "pendente",
        "mensagem": "Pagamento confirmado e registrado no Organiza." if pago else "O pagamento ainda está sendo confirmado. Aguarde alguns instantes.",
        "cobranca": cobranca,
    }, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


@app.post("/api/integracoes/infinitepay/organiza/webhook", include_in_schema=False)
async def organiza_infinitepay_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"success": False, "message": "JSON inválido"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"success": False, "message": "Payload inválido"}, status_code=400)
    order_nsu = str(payload.get("order_nsu") or "").strip()
    transaction_nsu = str(payload.get("transaction_nsu") or "").strip()
    if not order_nsu or not transaction_nsu:
        return JSONResponse({"success": False, "message": "Identificadores ausentes"}, status_code=400)
    cobranca = db.query(InfinitePayCobrancaOrganiza).filter(InfinitePayCobrancaOrganiza.order_nsu == order_nsu).first()
    if not cobranca:
        return JSONResponse({"success": False, "message": "Cobrança não encontrada"}, status_code=400)
    try:
        amount = int(payload.get("amount") or 0)
    except Exception:
        amount = 0
    if amount != int(cobranca.valor_centavos or 0):
        return JSONResponse({"success": False, "message": "Valor não confere"}, status_code=400)
    if cobranca.status == "PAGO" and cobranca.pagamento_id:
        return JSONResponse({"success": True, "message": None})
    ok = _infinitepay_registrar_pagamento_organiza(
        db, cobranca,
        transaction_nsu=transaction_nsu,
        invoice_slug=str(payload.get("invoice_slug") or payload.get("slug") or ""),
        receipt_url=str(payload.get("receipt_url") or ""),
        capture_method=str(payload.get("capture_method") or ""),
        installments=int(payload.get("installments") or 0),
        paid_amount_centavos=int(payload.get("paid_amount") or amount),
    )
    return JSONResponse({"success": bool(ok), "message": None if ok else "Falha ao registrar pagamento"}, status_code=200 if ok else 500)


AGENDA_MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
    7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}
AGENDA_RESUMO_CATEGORIAS = {
    "CONNECT": {"singular": "Connect", "plural": "Connect", "classe": "connect"},
    "GOOGLE": {"singular": "evento Google", "plural": "eventos Google", "classe": "google"},
    "ATUALIZACAO": {"singular": "atualização", "plural": "atualizações", "classe": "atualizacao"},
    "MANUTENCAO": {"singular": "manutenção", "plural": "manutenções", "classe": "manutencao"},
    "VENDA": {"singular": "venda", "plural": "vendas", "classe": "venda"},
    "VISITA": {"singular": "atendimento", "plural": "atendimentos", "classe": "visita"},
    "OUTRO": {"singular": "outro", "plural": "outros", "classe": "outro"},
}


def _agenda_mes_referencia(valor: str | None) -> date:
    hoje = date.today()
    texto_mes = (valor or "").strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}", texto_mes):
            ano, mes = [int(x) for x in texto_mes.split("-", 1)]
            return date(ano, mes, 1)
    except (TypeError, ValueError):
        pass
    return hoje.replace(day=1)


def _agenda_somar_meses(referencia: date, quantidade: int) -> date:
    indice = referencia.year * 12 + (referencia.month - 1) + quantidade
    return date(indice // 12, (indice % 12) + 1, 1)


def _agenda_retorno_seguro(valor: str | None, padrao: str = "/organiza/agenda") -> str:
    retorno = (valor or "").strip()
    return retorno if retorno.startswith("/organiza/agenda") else padrao


def _google_calendar_data_hora_local(evento_google: dict) -> tuple[datetime | None, bool]:
    """Converte o início retornado pelo Google para horário local sem timezone.

    A agenda interna do Organiza trabalha com datetimes sem timezone. Normalizamos
    aqui para America/Sao_Paulo para permitir ordenação conjunta sem misturar
    objetos aware/naive. Eventos de dia inteiro retornam meia-noite apenas para
    agrupamento e são identificados pelo booleano retornado.
    """
    inicio = evento_google.get("start") or {}
    texto = str(inicio.get("dateTime") or "").strip()
    if texto:
        try:
            dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone(ZoneInfo(ORGANIZA_GOOGLE_TZ)).replace(tzinfo=None)
            return dt, False
        except (TypeError, ValueError):
            return None, False
    dia = str(inicio.get("date") or "").strip()
    if dia:
        try:
            return datetime.combine(date.fromisoformat(dia), time.min), True
        except (TypeError, ValueError):
            return None, True
    return None, False


def _google_calendar_evento_connect(evento_google: dict) -> bool:
    """Identifica eventos que chegaram ao Google com o padrão atual do Connect.

    A classificação usa somente os dados devolvidos pelo Google. Nenhuma consulta
    a contrato, cliente ou banco do Connect é realizada.
    """
    titulo = str(evento_google.get("summary") or "").strip()
    descricao = str(evento_google.get("description") or "").strip()
    texto = f"{titulo}\n{descricao}".lower()
    if "conect.humiat.com.br" in texto:
        return True
    if re.match(r"^\s*contrato\s*#\d+", titulo, flags=re.IGNORECASE):
        return True
    return "etapa:" in texto and "itens:" in texto and "contrato #" in texto


def _google_calendar_listar_mes(db: Session, inicio_mes: datetime, fim_mes: datetime) -> tuple[list[dict], str]:
    """Lê diretamente do Google os compromissos do calendário conectado.

    Retorna apenas o que existe no Google no momento da consulta. Eventos criados
    pelo próprio Organiza são removidos desta lista para não aparecerem duas vezes,
    pois já fazem parte das categorias locais da agenda.
    """
    integ = _google_integracao(db)
    if not integ or not (integ.refresh_token or integ.access_token):
        return [], ""

    try:
        token = _google_access_token(db)
        calendar_id = urllib.parse.quote((integ.calendar_id or ORGANIZA_GOOGLE_CALENDAR_ID), safe="")
        tz = ZoneInfo(ORGANIZA_GOOGLE_TZ)
        inicio_api = inicio_mes.replace(tzinfo=tz).isoformat()
        fim_api = fim_mes.replace(tzinfo=tz).isoformat()

        # IDs que o próprio Organiza já mostra nas categorias locais.
        ids_locais = {
            str(x[0]).strip()
            for x in db.query(AgendaManual.google_event_id)
            .filter(
                AgendaManual.google_event_id.isnot(None), AgendaManual.google_event_id != "",
                AgendaManual.data_hora >= inicio_mes, AgendaManual.data_hora < fim_mes,
            )
            .all()
            if x and x[0]
        }
        ids_locais.update({
            str(x[0]).strip()
            for x in db.query(AtualizacaoAgendamento.google_event_id)
            .filter(
                AtualizacaoAgendamento.google_event_id.isnot(None), AtualizacaoAgendamento.google_event_id != "",
                AtualizacaoAgendamento.data_hora >= inicio_mes, AtualizacaoAgendamento.data_hora < fim_mes,
            )
            .all()
            if x and x[0]
        })

        saida: list[dict] = []
        page_token = ""
        paginas = 0
        while paginas < 10:
            parametros = {
                "timeMin": inicio_api,
                "timeMax": fim_api,
                "singleEvents": "true",
                "orderBy": "startTime",
                "showDeleted": "false",
                "maxResults": "2500",
                "timeZone": ORGANIZA_GOOGLE_TZ,
                "fields": "items(id,status,summary,description,location,htmlLink,start,end,creator,organizer),nextPageToken",
            }
            if page_token:
                parametros["pageToken"] = page_token
            url = (
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events?"
                + urllib.parse.urlencode(parametros)
            )
            dados = _google_http_json(url, token=token, timeout=12)
            for item in dados.get("items") or []:
                if str(item.get("status") or "").lower() == "cancelled":
                    continue
                event_id = str(item.get("id") or "").strip()
                if not event_id or event_id in ids_locais:
                    continue
                data_hora, dia_inteiro = _google_calendar_data_hora_local(item)
                if not data_hora or not (inicio_mes <= data_hora < fim_mes):
                    continue
                connect = _google_calendar_evento_connect(item)
                descricao = str(item.get("description") or "").strip()
                local = str(item.get("location") or "").strip()
                detalhes = []
                if local:
                    detalhes.append(local)
                if descricao:
                    detalhes.append(descricao)
                saida.append({
                    "tipo": "connect" if connect else "google",
                    "categoria_resumo": "CONNECT" if connect else "GOOGLE",
                    "titulo": "Connect" if connect else "Google Agenda",
                    "data_hora": data_hora,
                    "dia_inteiro": dia_inteiro,
                    "cliente": str(item.get("summary") or "(Sem título)").strip() or "(Sem título)",
                    "equipamento": "\n".join(detalhes),
                    "descricao_google": descricao,
                    "local_google": local,
                    "link": str(item.get("htmlLink") or "").strip() or "#",
                    "editar_link": None,
                    "manual": False,
                    "google_externo": True,
                    "google_event_id": event_id,
                    "atualizar_data_action": None,
                    "excluir_action": None,
                })
            page_token = str(dados.get("nextPageToken") or "").strip()
            paginas += 1
            if not page_token:
                break
        return saida, ""
    except Exception as exc:
        return [], str(exc)[:700]


@app.get("/organiza/agenda", response_class=HTMLResponse)
def agenda(
    request: Request,
    mes: str = "",
    data: str = "",
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    mes_ref = _agenda_mes_referencia(mes)
    proximo_mes = _agenda_somar_meses(mes_ref, 1)
    mes_anterior = _agenda_somar_meses(mes_ref, -1)
    inicio_mes = datetime.combine(mes_ref, time.min)
    fim_mes = datetime.combine(proximo_mes, time.min)

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
                Manutencao.entrega_prevista_em.between(inicio_mes, fim_mes - timedelta(microseconds=1)),
                Manutencao.retirada_em.between(inicio_mes, fim_mes - timedelta(microseconds=1)),
                Manutencao.pronto_em.between(inicio_mes, fim_mes - timedelta(microseconds=1)),
            ),
        )
        .all()
    )

    eventos = []
    for m in manutencoes:
        etapa = etapa_manutencao(m)
        if etapa == 1 and m.entrega_prevista_em and inicio_mes <= m.entrega_prevista_em < fim_mes:
            online = (m.tipo_atendimento or "loja") == "online"
            eventos.append({
                "tipo": "online" if online else "entrada",
                "categoria_resumo": "MANUTENCAO",
                "titulo": "Atendimento online" if online else "Cliente vai trazer",
                "data_hora": m.entrega_prevista_em,
                "cliente": m.cliente.nome,
                "equipamento": descricao_equipamento(m.equipamento),
                "link": f"/organiza/manutencoes/{m.id}#etapa-1",
                "editar_link": None,
                "manual": False,
                "manutencao_id": m.id,
                "agendamento_tipo": "entrada",
                "atualizar_data_action": f"/organiza/agenda/manutencao/{m.id}/atualizar-data",
                "excluir_action": f"/organiza/agenda/manutencao/{m.id}/excluir",
            })
        elif etapa == 6 and m.retirada_em and inicio_mes <= m.retirada_em < fim_mes:
            eventos.append({
                "tipo": "retirada",
                "categoria_resumo": "MANUTENCAO",
                "titulo": "Cliente vem buscar",
                "data_hora": m.retirada_em,
                "cliente": m.cliente.nome,
                "equipamento": descricao_equipamento(m.equipamento),
                "link": f"/organiza/manutencoes/{m.id}#etapa-6",
                "editar_link": None,
                "manual": False,
                "manutencao_id": m.id,
                "agendamento_tipo": "retirada",
                "atualizar_data_action": f"/organiza/agenda/manutencao/{m.id}/atualizar-data",
                "excluir_action": f"/organiza/agenda/manutencao/{m.id}/excluir",
            })

    # Agenda manual limitada ao mês exibido; clientes são resolvidos em lote.
    agenda_manuais = (
        db.query(AgendaManual)
        .filter(AgendaManual.data_hora >= inicio_mes, AgendaManual.data_hora < fim_mes)
        .order_by(AgendaManual.data_hora.asc())
        .all()
    )
    cliente_ids_manuais = {e.cliente_id for e in agenda_manuais if e.cliente_id}
    clientes_manuais = {
        c.id: c
        for c in (db.query(Cliente).filter(Cliente.id.in_(cliente_ids_manuais)).all() if cliente_ids_manuais else [])
    }
    for e in agenda_manuais:
        cliente_manual = clientes_manuais.get(e.cliente_id) if e.cliente_id else None
        categoria_manual = _agenda_categoria_evento(e)
        local_manual = _agenda_local_evento(e)
        eventos.append({
            "tipo": _agenda_tipo_visual(categoria_manual, local_manual),
            "categoria_resumo": categoria_manual,
            "titulo": f"{AGENDA_CATEGORIAS[categoria_manual]} · {AGENDA_LOCAIS[local_manual]}",
            "data_hora": e.data_hora,
            "cliente": cliente_manual.nome if cliente_manual else (e.contato or "Compromisso manual"),
            "equipamento": " · ".join(filter(None, [e.observacao or "", f"Google {e.google_sync_status or 'aguardando'}"])),
            "link": f"/organiza/clientes/{cliente_manual.id}#agenda-cliente" if cliente_manual else f"/organiza/agenda/manual/{e.id}/editar",
            "editar_link": f"/organiza/agenda/manual/{e.id}/editar",
            "manual": True,
            "evento_id": e.id,
            "google_sync_status": e.google_sync_status,
            "google_sync_erro": e.google_sync_erro,
            "atualizar_data_action": f"/organiza/agenda/manual/{e.id}/atualizar-data",
            "excluir_action": f"/organiza/agenda/manual/{e.id}/excluir",
        })

    # Atualizações agendadas do mês: compra/cliente também em lote.
    atualizacoes_agendadas = (
        db.query(AtualizacaoAgendamento)
        .filter(
            AtualizacaoAgendamento.status == "RESERVADO",
            AtualizacaoAgendamento.data_hora >= inicio_mes,
            AtualizacaoAgendamento.data_hora < fim_mes,
        )
        .order_by(AtualizacaoAgendamento.data_hora.asc())
        .all()
    )
    compra_ids = {a.compra_id for a in atualizacoes_agendadas if a.compra_id}
    cliente_ids_at = {a.cliente_id for a in atualizacoes_agendadas if a.cliente_id}
    compras_por_id = {
        c.id: c
        for c in (db.query(AtualizacaoCompra).filter(AtualizacaoCompra.id.in_(compra_ids)).all() if compra_ids else [])
    }
    clientes_at_por_id = {
        c.id: c
        for c in (db.query(Cliente).filter(Cliente.id.in_(cliente_ids_at)).all() if cliente_ids_at else [])
    }
    for a in atualizacoes_agendadas:
        compra = compras_por_id.get(a.compra_id)
        cliente_at = clientes_at_por_id.get(a.cliente_id)
        if not compra or not cliente_at:
            continue
        eventos.append({
            "tipo": "atualizacao-casa" if a.tipo == "CASA" else ("atualizacao-cliente" if a.tipo == "CLIENTE" else "atualizacao-loja"),
            "categoria_resumo": "ATUALIZACAO",
            "titulo": "Atualização · Online" if a.tipo == "CASA" else ("Atualização · Cliente" if a.tipo == "CLIENTE" else "Atualização · Loja"),
            "data_hora": a.data_hora,
            "cliente": cliente_at.nome,
            "equipamento": f"Pacotes {compra.pacote_inicio} a {compra.pacote_fim} · Google {a.google_sync_status or 'aguardando'}",
            "link": f"/organiza/clientes/{cliente_at.id}#atualizacoes",
            "editar_link": f"/organiza/clientes/{cliente_at.id}#atualizacoes",
            "manual": False,
            "evento_id": a.id,
            "atualizacao": True,
            "google_sync_status": a.google_sync_status,
            "google_sync_erro": a.google_sync_erro,
            "atualizar_data_action": f"/organiza/agenda/atualizacao/{a.id}/atualizar-data",
            "excluir_action": None,
        })

    # Lê diretamente a agenda Google da empresa. A fonte é o próprio Google:
    # nenhuma informação adicional de contratos do Connect é consultada.
    eventos_google, google_consulta_erro = _google_calendar_listar_mes(db, inicio_mes, fim_mes)
    eventos.extend(eventos_google)

    eventos.sort(key=lambda e: (e["data_hora"], e["titulo"], e.get("cliente") or ""))

    # Contadores diários por categoria: a visão mensal mostra somente quantidades.
    contagens_por_dia: dict[date, dict[str, int]] = {}
    for evento in eventos:
        dia = evento["data_hora"].date()
        categoria = evento.get("categoria_resumo") or "OUTRO"
        por_categoria = contagens_por_dia.setdefault(dia, {})
        por_categoria[categoria] = por_categoria.get(categoria, 0) + 1

    semanas = []
    cal = calendar.Calendar(firstweekday=0)  # segunda-feira
    for semana in cal.monthdatescalendar(mes_ref.year, mes_ref.month):
        dias_semana = []
        for dia in semana:
            dentro_mes = dia.month == mes_ref.month
            contagens = contagens_por_dia.get(dia, {}) if dentro_mes else {}
            resumos = []
            for chave in ("CONNECT", "GOOGLE", "ATUALIZACAO", "MANUTENCAO", "VENDA", "VISITA", "OUTRO"):
                qtd = int(contagens.get(chave, 0) or 0)
                if not qtd:
                    continue
                meta = AGENDA_RESUMO_CATEGORIAS[chave]
                resumos.append({
                    "categoria": chave,
                    "quantidade": qtd,
                    "rotulo": meta["singular"] if qtd == 1 else meta["plural"],
                    "classe": meta["classe"],
                })
            dias_semana.append({
                "data": dia,
                "iso": dia.isoformat(),
                "numero": dia.day,
                "dentro_mes": dentro_mes,
                "resumos": resumos,
                "total": sum(contagens.values()) if contagens else 0,
            })
        semanas.append(dias_semana)

    data_selecionada = None
    try:
        if data:
            candidata = datetime.strptime(data, "%Y-%m-%d").date()
            if candidata.year == mes_ref.year and candidata.month == mes_ref.month:
                data_selecionada = candidata
    except ValueError:
        data_selecionada = None
    eventos_detalhe = [e for e in eventos if data_selecionada and e["data_hora"].date() == data_selecionada]

    google = _google_integracao(db)
    mes_chave = mes_ref.strftime("%Y-%m")
    total_mes = len(eventos)
    retorno_base = f"/organiza/agenda?mes={mes_chave}"
    if data_selecionada:
        retorno_base += f"&data={data_selecionada.isoformat()}#agenda-detalhe"

    return templates.TemplateResponse("organiza/agenda.html", {
        "request": request,
        "usuario": usuario,
        "eventos": eventos,
        "eventos_detalhe": eventos_detalhe,
        "semanas": semanas,
        "mes_ref": mes_ref,
        "mes_chave": mes_chave,
        "mes_titulo": f"{AGENDA_MESES_PT[mes_ref.month]} {mes_ref.year}",
        "mes_anterior": mes_anterior.strftime("%Y-%m"),
        "proximo_mes": proximo_mes.strftime("%Y-%m"),
        "data_selecionada": data_selecionada,
        "data_selecionada_titulo": data_selecionada.strftime("%d/%m/%Y") if data_selecionada else "",
        "total_mes": total_mes,
        "retorno_agenda": retorno_base,
        "google": google,
        "google_configurado": _google_configurado(),
        "google_consulta_erro": google_consulta_erro,
        "google_eventos_mes": len(eventos_google),
        "mensagem": request.query_params.get("mensagem", ""),
        "erro": request.query_params.get("erro", ""),
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



@app.post("/organiza/agenda/{origem}/{registro_id}/atualizar-data")
async def agenda_atualizar_data_existente(
    origem: str,
    registro_id: int,
    request: Request,
    usuario: Usuario = Depends(usuario_logado),
    db: Session = Depends(get_db),
):
    """Altera somente a data/hora do compromisso existente.

    Não cria uma nova linha no Organiza. Quando o compromisso possui vínculo com
    o Google Agenda, o mesmo google_event_id é atualizado via PUT.
    """
    form = await request.form()
    nova_data = datetime_form(form.get("data_hora") or "")
    retorno = _agenda_retorno_seguro(form.get("retorno"))
    if not nova_data:
        separador = "&" if "?" in retorno else "?"
        return RedirectResponse(retorno.replace("#agenda-detalhe", "") + separador + "erro=" + quote_plus("Informe uma nova data e horário.") + "#agenda-detalhe", status_code=303)

    origem = (origem or "").strip().lower()
    mensagem = "Data atualizada no mesmo compromisso. Nenhum novo registro foi criado."

    if origem == "manual":
        evento = db.get(AgendaManual, registro_id)
        if not evento:
            raise HTTPException(404)
        evento.data_hora = nova_data
        _google_calendar_manual_sincronizar(db, evento)
        if evento.google_sync_status != "SINCRONIZADO":
            mensagem = "Data atualizada no mesmo compromisso do Organiza; a sincronização com o Google Agenda ficou pendente."

    elif origem == "atualizacao":
        ag = db.get(AtualizacaoAgendamento, registro_id)
        if not ag or ag.status != "RESERVADO":
            raise HTTPException(404)
        tipo = (ag.tipo or "LOJA").strip().upper()
        if not _atualizacao_horario_valido(tipo, nova_data):
            if tipo == "CLIENTE":
                erro = "Escolha um horário futuro para a visita do técnico."
            else:
                horario = "10:00 às 20:00" if tipo == "CASA" else "14:00 às 18:00"
                erro = f"Horário inválido. Segunda a sexta, {horario}, de 1 em 1 hora."
            separador = "&" if "?" in retorno else "?"
            return RedirectResponse(retorno.replace("#agenda-detalhe", "") + separador + "erro=" + quote_plus(erro) + "#agenda-detalhe", status_code=303)
        if _atualizacao_horario_ocupado(db, nova_data, ag.id):
            separador = "&" if "?" in retorno else "?"
            return RedirectResponse(retorno.replace("#agenda-detalhe", "") + separador + "erro=" + quote_plus("Esse horário conflita com outro compromisso do Organiza.") + "#agenda-detalhe", status_code=303)
        compra = db.get(AtualizacaoCompra, ag.compra_id)
        cliente = db.get(Cliente, ag.cliente_id)
        if not compra or not cliente:
            raise HTTPException(404)
        ag.data_hora = nova_data
        _google_calendar_sincronizar(db, ag, cliente, compra)
        if ag.google_sync_status != "SINCRONIZADO":
            mensagem = "Data atualizada no mesmo agendamento; a sincronização com o Google Agenda ficou pendente."

    elif origem == "manutencao":
        m = db.get(Manutencao, registro_id)
        if not m:
            raise HTTPException(404)
        tipo = (form.get("tipo") or "entrada").strip().lower()
        if tipo == "retirada":
            m.retirada_em = nova_data
            m.status = "Retirada agendada"
        else:
            if horario_atendimento_ocupado(db, nova_data, m.id):
                separador = "&" if "?" in retorno else "?"
                return RedirectResponse(retorno.replace("#agenda-detalhe", "") + separador + "erro=" + quote_plus("Este horário já está ocupado.") + "#agenda-detalhe", status_code=303)
            m.entrega_prevista_em = nova_data
            if not m.recebido_em:
                m.status = "Aguardando equipamento"
    else:
        raise HTTPException(404)

    db.commit()
    # Se a nova data mudou de mês/dia, leva o usuário diretamente ao compromisso atualizado.
    destino = f"/organiza/agenda?mes={nova_data.strftime('%Y-%m')}&data={nova_data.strftime('%Y-%m-%d')}&mensagem={quote_plus(mensagem)}#agenda-detalhe"
    return RedirectResponse(destino, status_code=303)


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
def agenda_manual_novo(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    clientes_agenda = db.query(Cliente).order_by(Cliente.nome.asc()).all()
    return templates.TemplateResponse("organiza/agenda_manual_form.html", {
        "request": request, "usuario": usuario, "evento": None, "erro": "", "clientes_agenda": clientes_agenda,
        "agenda_categorias": AGENDA_CATEGORIAS, "agenda_locais": AGENDA_LOCAIS,
    })


@app.post("/organiza/agenda/manual/novo")
async def agenda_manual_criar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = await request.form()
    data_hora = datetime_form(form.get("data_hora"))
    categoria = _agenda_categoria(form.get("categoria"))
    local = _agenda_local(form.get("local_atendimento"))
    try:
        cliente_id = int(form.get("cliente_id") or 0)
    except (TypeError, ValueError):
        cliente_id = 0
    cliente = db.get(Cliente, cliente_id) if cliente_id else None
    nome_visitante = (form.get("nome_visitante") or form.get("contato") or "").strip()
    telefone_visitante = (form.get("telefone_visitante") or "").strip()
    contato_livre = " · ".join(x for x in (nome_visitante, telefone_visitante) if x)
    nome = cliente.nome if cliente else nome_visitante
    if not nome or not data_hora:
        clientes_agenda = db.query(Cliente).order_by(Cliente.nome.asc()).all()
        return templates.TemplateResponse("organiza/agenda_manual_form.html", {
            "request": request, "usuario": usuario, "evento": None,
            "erro": "Informe um cliente ou nome/contato e a data com horário.",
            "clientes_agenda": clientes_agenda, "agenda_categorias": AGENDA_CATEGORIAS, "agenda_locais": AGENDA_LOCAIS,
        }, status_code=400)
    contato = (f"{cliente.nome} · +{cliente.ddi} {cliente.telefone_formatado()}" if cliente else contato_livre)
    evento = AgendaManual(
        cliente_id=cliente.id if cliente else None,
        titulo=_agenda_titulo(categoria, local, nome),
        tipo=_agenda_tipo_visual(categoria, local), categoria=categoria, local_atendimento=local,
        data_hora=data_hora, contato=contato or None,
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
    clientes_agenda = db.query(Cliente).order_by(Cliente.nome.asc()).all()
    return templates.TemplateResponse("organiza/agenda_manual_form.html", {
        "request": request, "usuario": usuario, "evento": evento, "erro": "", "clientes_agenda": clientes_agenda,
        "agenda_categorias": AGENDA_CATEGORIAS, "agenda_locais": AGENDA_LOCAIS,
    })


@app.post("/organiza/agenda/manual/{evento_id}/editar")
async def agenda_manual_salvar(evento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    evento = db.get(AgendaManual, evento_id)
    if not evento:
        raise HTTPException(404)
    form = await request.form()
    data_hora = datetime_form(form.get("data_hora"))
    categoria = _agenda_categoria(form.get("categoria"))
    local = _agenda_local(form.get("local_atendimento"))
    try:
        cliente_id = int(form.get("cliente_id") or 0)
    except (TypeError, ValueError):
        cliente_id = 0
    cliente = db.get(Cliente, cliente_id) if cliente_id else None
    nome_visitante = (form.get("nome_visitante") or form.get("contato") or "").strip()
    telefone_visitante = (form.get("telefone_visitante") or "").strip()
    contato_livre = " · ".join(x for x in (nome_visitante, telefone_visitante) if x)
    nome = cliente.nome if cliente else nome_visitante
    if not nome or not data_hora:
        clientes_agenda = db.query(Cliente).order_by(Cliente.nome.asc()).all()
        return templates.TemplateResponse("organiza/agenda_manual_form.html", {
            "request": request, "usuario": usuario, "evento": evento,
            "erro": "Informe um cliente ou nome/contato e a data com horário.",
            "clientes_agenda": clientes_agenda, "agenda_categorias": AGENDA_CATEGORIAS, "agenda_locais": AGENDA_LOCAIS,
        }, status_code=400)
    evento.cliente_id = cliente.id if cliente else None
    evento.categoria = categoria
    evento.local_atendimento = local
    evento.tipo = _agenda_tipo_visual(categoria, local)
    evento.titulo = _agenda_titulo(categoria, local, nome)
    evento.data_hora = data_hora
    evento.contato = (f"{cliente.nome} · +{cliente.ddi} {cliente.telefone_formatado()}" if cliente else contato_livre) or None
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
    sincronizar_estoque_manutencao(o.manutencao, db)
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
