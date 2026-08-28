import hashlib
import hmac
import json
import os
import secrets
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Session, relationship

from database import Base, SessionLocal, get_db
from config import ADMIN_NOME, ADMIN_SENHA, PUBLIC_BASE_URL

router = APIRouter()
templates = Jinja2Templates(directory="templates")

COOKIE_NAME = "humiat_id"
SESSION_DAYS = int(os.getenv("HUMIAT_SESSION_DAYS", "14"))
SSO_MINUTES = int(os.getenv("HUMIAT_SSO_MINUTES", "2"))
SSO_SECRET = os.getenv("HUMIAT_SSO_SECRET", "").strip()
SOLVOZ_BASE_URL = os.getenv("HUMIAT_SOLVOZ_URL", "https://www.solvoz.com.br").strip().rstrip("/")
SOLVOZ_API_TIMEOUT = float(os.getenv("HUMIAT_SOLVOZ_API_TIMEOUT", "8") or "8")
SOLVOZ_DIAGNOSTICS_PATH = os.getenv("HUMIAT_SOLVOZ_DIAGNOSTICS_PATH", "/_sv/uso/7f29c4b8").strip() or "/_sv/uso/7f29c4b8"

RESET_MINUTES = int(os.getenv("HUMIAT_RESET_MINUTES", "30") or "30")
SMTP_HOST = os.getenv("HUMIAT_SMTP_HOST", "smtp.gmail.com").strip()
SMTP_PORT = int(os.getenv("HUMIAT_SMTP_PORT", "587") or "587")
SMTP_USER = os.getenv("HUMIAT_SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("HUMIAT_SMTP_PASSWORD", "").strip()
SMTP_FROM = os.getenv("HUMIAT_SMTP_FROM", SMTP_USER).strip()

TIPO_ADMIN_HUMIAT = "ADMIN_HUMIAT"
TIPO_ADMIN_EMPRESA = "ADMIN_EMPRESA"


class HumiatEmpresa(Base):
    __tablename__ = "humiat_empresas"
    id = Column(Integer, primary_key=True)
    nome = Column(String(140), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    ativo = Column(Integer, nullable=False, default=1)
    criado_em = Column(DateTime, server_default=func.now())


class HumiatUsuario(Base):
    __tablename__ = "humiat_usuarios"
    id = Column(Integer, primary_key=True)
    nome = Column(String(120), nullable=False)
    email = Column(String(180), unique=True, nullable=False)
    senha_hash = Column(String(255), nullable=False)
    tipo = Column(String(30), nullable=False, default=TIPO_ADMIN_EMPRESA)
    ativo = Column(Integer, nullable=False, default=1)
    organiza_usuario = Column(String(80), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())


class HumiatUsuarioEmpresa(Base):
    __tablename__ = "humiat_usuario_empresas"
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("humiat_usuarios.id"), nullable=False)
    empresa_id = Column(Integer, ForeignKey("humiat_empresas.id"), nullable=False)


class HumiatProduto(Base):
    __tablename__ = "humiat_produtos"
    id = Column(Integer, primary_key=True)
    codigo = Column(String(30), unique=True, nullable=False)
    nome = Column(String(80), nullable=False)
    descricao = Column(String(240), nullable=True)
    url_publica = Column(String(300), nullable=True)
    url_sso = Column(String(300), nullable=True)
    icone = Column(String(80), nullable=True)
    ativo = Column(Integer, nullable=False, default=1)


class HumiatEmpresaProduto(Base):
    __tablename__ = "humiat_empresa_produtos"
    id = Column(Integer, primary_key=True)
    empresa_id = Column(Integer, ForeignKey("humiat_empresas.id"), nullable=False)
    produto_id = Column(Integer, ForeignKey("humiat_produtos.id"), nullable=False)
    ativo = Column(Integer, nullable=False, default=1)


class HumiatSessao(Base):
    __tablename__ = "humiat_sessoes"
    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), unique=True, nullable=False)
    usuario_id = Column(Integer, ForeignKey("humiat_usuarios.id"), nullable=False)
    criado_em = Column(DateTime, server_default=func.now())
    expira_em = Column(DateTime, nullable=False)
    ultimo_acesso = Column(DateTime, nullable=True)
    ip = Column(String(80), nullable=True)
    user_agent = Column(String(300), nullable=True)


class HumiatSenhaReset(Base):
    __tablename__ = "humiat_senha_resets"
    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), unique=True, nullable=False)
    usuario_id = Column(Integer, ForeignKey("humiat_usuarios.id"), nullable=False)
    criado_em = Column(DateTime, server_default=func.now())
    expira_em = Column(DateTime, nullable=False)
    usado_em = Column(DateTime, nullable=True)
    ip = Column(String(80), nullable=True)


class HumiatAuditoria(Base):
    __tablename__ = "humiat_auditoria"
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("humiat_usuarios.id"), nullable=True)
    empresa_id = Column(Integer, ForeignKey("humiat_empresas.id"), nullable=True)
    acao = Column(String(100), nullable=False)
    detalhe = Column(Text, nullable=True)
    ip = Column(String(80), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())


class HumiatSSOTicket(Base):
    __tablename__ = "humiat_sso_tickets"
    id = Column(Integer, primary_key=True)
    token_hash = Column(String(64), unique=True, nullable=False)
    usuario_id = Column(Integer, ForeignKey("humiat_usuarios.id"), nullable=False)
    empresa_id = Column(Integer, ForeignKey("humiat_empresas.id"), nullable=True)
    produto_codigo = Column(String(30), nullable=False)
    criado_em = Column(DateTime, server_default=func.now())
    expira_em = Column(DateTime, nullable=False)
    usado_em = Column(DateTime, nullable=True)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def gerar_hash_senha_id(senha: str, salt: Optional[str] = None) -> str:
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), 180_000).hex()
    return f"{salt}${digest}"


def verificar_senha_id(senha: str, senha_hash: str) -> bool:
    try:
        salt, esperado = senha_hash.split("$", 1)
        atual = gerar_hash_senha_id(senha, salt).split("$", 1)[1]
        return hmac.compare_digest(atual, esperado)
    except Exception:
        return False


def _ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded:
        return forwarded[:80]
    return (request.client.host if request.client else "")[:80]


def _auditar(db: Session, request: Request, acao: str, usuario_id: int | None = None, empresa_id: int | None = None, detalhe: str = ""):
    db.add(HumiatAuditoria(usuario_id=usuario_id, empresa_id=empresa_id, acao=acao, detalhe=detalhe[:3000], ip=_ip(request)))


def _enviar_email_recuperacao(destino: str, nome: str, link: str) -> None:
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD and SMTP_FROM):
        raise RuntimeError("E-mail de recuperação não configurado no servidor")
    msg = EmailMessage()
    msg["Subject"] = "Humiat ID - Redefinição de senha"
    msg["From"] = SMTP_FROM
    msg["To"] = destino
    msg.set_content(
        f"Olá, {nome or 'usuário'}.\n\n"
        "Recebemos uma solicitação para redefinir sua senha do Humiat ID.\n"
        f"Use este link em até {RESET_MINUTES} minutos:\n{link}\n\n"
        "Se você não pediu a alteração, ignore esta mensagem.\n"
    )
    contexto = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)


def _slug(valor: str) -> str:
    """Normaliza o identificador público de empresa sem aceitar caracteres ambíguos."""
    bruto = (valor or "").strip().lower()
    bruto = "".join(c if (c.isalnum() or c in "-_ ") else "-" for c in bruto)
    bruto = "-".join(bruto.replace("_", "-").split())
    while "--" in bruto:
        bruto = bruto.replace("--", "-")
    return bruto.strip("-")[:100]


def _solvoz_api(caminho: str, *, metodo: str = "GET", dados: dict | None = None) -> dict:
    """Chamada servidor-servidor protegida pelo mesmo segredo usado no SSO."""
    if not SSO_SECRET:
        raise RuntimeError("HUMIAT_SSO_SECRET não configurado")
    url = f"{SOLVOZ_BASE_URL}{caminho}"
    corpo = None
    headers = {
        "X-Humiat-SSO-Secret": SSO_SECRET,
        "Accept": "application/json",
        "User-Agent": "Humiat-ID-SolVoz-Admin/1.0",
    }
    if dados is not None:
        corpo = urllib.parse.urlencode({k: "" if v is None else str(v) for k, v in dados.items()}).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=corpo, method=metodo.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=SOLVOZ_API_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        detalhe = ""
        try:
            detalhe = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(detalhe)
            detalhe = parsed.get("detail") or parsed.get("erro") or detalhe
        except Exception:
            pass
        raise RuntimeError(f"SolVoz respondeu HTTP {exc.code}: {detalhe or exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"Não foi possível comunicar com o SolVoz: {exc}") from exc


def _solvoz_api_arquivo(
    caminho: str,
    *,
    nome_arquivo: str,
    conteudo: bytes,
    content_type: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    dados: dict | None = None,
) -> dict:
    if not SSO_SECRET:
        raise RuntimeError("HUMIAT_SSO_SECRET não configurado")

    boundary = "----HumiatSolVoz" + secrets.token_hex(16)
    partes = []
    for chave, valor in (dados or {}).items():
        partes.append(f"--{boundary}\r\n".encode("ascii"))
        partes.append(f'Content-Disposition: form-data; name="{chave}"\r\n\r\n'.encode("utf-8"))
        partes.append(("" if valor is None else str(valor)).encode("utf-8"))
        partes.append(b"\r\n")

    safe_name = (nome_arquivo or "catalogo.xlsx").replace('"', "")
    partes.append(f"--{boundary}\r\n".encode("ascii"))
    partes.append(f'Content-Disposition: form-data; name="arquivo"; filename="{safe_name}"\r\n'.encode("utf-8"))
    partes.append(f"Content-Type: {content_type}\r\n\r\n".encode("ascii"))
    partes.append(conteudo)
    partes.append(b"\r\n")
    partes.append(f"--{boundary}--\r\n".encode("ascii"))
    corpo = b"".join(partes)

    req = urllib.request.Request(
        f"{SOLVOZ_BASE_URL}{caminho}",
        data=corpo,
        method="POST",
        headers={
            "X-Humiat-SSO-Secret": SSO_SECRET,
            "Accept": "application/json",
            "User-Agent": "Humiat-ID-SolVoz-Admin/1.0",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(corpo)),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=max(SOLVOZ_API_TIMEOUT,120)) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detalhe = ""
        try:
            detalhe = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(detalhe)
            detalhe = parsed.get("detail") or parsed.get("erro") or detalhe
        except Exception:
            pass
        raise RuntimeError(f"SolVoz respondeu HTTP {exc.code}: {detalhe or exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"Não foi possível comunicar com o SolVoz: {exc}") from exc


def _solvoz_catalogo_resumo() -> tuple[dict | None, str]:
    try:
        return _solvoz_api("/_sv/api/humiat/catalogo/resumo"), ""
    except Exception as exc:
        return None, str(exc)


def _formatar_data_br(valor) -> str:
    """Formata timestamps técnicos do SolVoz para o painel humano do ADM."""
    texto = str(valor or "").strip()
    if not texto:
        return ""
    try:
        # Aceita o formato vindo de SQLite/Postgres e também ISO 8601.
        iso = texto.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        # CURRENT_TIMESTAMP no banco é UTC. Quando vier sem fuso, tratamos como UTC.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone(timedelta(hours=-3)))
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return texto


def _solvoz_resumo(slug: str) -> tuple[dict | None, str]:
    try:
        dados = _solvoz_api(f"/_sv/api/humiat/empresa/{urllib.parse.quote(_slug(slug))}")
        if dados.get("ok") and isinstance(dados.get("maquinas"), dict):
            dados["maquinas"]["ultima_sincronizacao_br"] = _formatar_data_br(
                dados["maquinas"].get("ultima_sincronizacao")
            )
        return dados if dados.get("ok") else None, ""
    except Exception as exc:
        return None, str(exc)


def _produto_solvoz(db: Session) -> HumiatProduto | None:
    return db.query(HumiatProduto).filter(HumiatProduto.codigo == "SOLVOZ").first()


def _empresa_tem_produto(db: Session, empresa_id: int, codigo: str) -> bool:
    produto = db.query(HumiatProduto).filter(HumiatProduto.codigo == codigo.upper()).first()
    if not produto:
        return False
    item = db.query(HumiatEmpresaProduto).filter(
        HumiatEmpresaProduto.empresa_id == empresa_id,
        HumiatEmpresaProduto.produto_id == produto.id,
        HumiatEmpresaProduto.ativo == 1,
    ).first()
    return bool(item)


def _usuario_empresa_id(db: Session, usuario_id: int) -> int | None:
    vinculo = db.query(HumiatUsuarioEmpresa).filter(HumiatUsuarioEmpresa.usuario_id == usuario_id).first()
    return int(vinculo.empresa_id) if vinculo else None


def seed_humiat_id():
    """Cria estrutura lógica inicial sem destruir dados existentes."""
    db = SessionLocal()
    try:
        produtos = [
            ("CONNECT", "Connect", "Contratos, agenda, operação, rotas e financeiro.", os.getenv("HUMIAT_CONNECT_URL", "https://conect.humiat.com.br"), os.getenv("HUMIAT_CONNECT_SSO_URL", ""), "connect"),
            ("LOKAFEST", "LokaFest", "Indicações e oportunidades para festas.", os.getenv("HUMIAT_LOKAFEST_URL", "https://lokafest.com.br"), os.getenv("HUMIAT_LOKAFEST_SSO_URL", ""), "lokafest"),
            ("SOLVOZ", "SolVoz", "Catálogo musical, identidade e site para locadores.", os.getenv("HUMIAT_SOLVOZ_URL", "https://www.solvoz.com.br"), os.getenv("HUMIAT_SOLVOZ_SSO_URL", "https://www.solvoz.com.br/_sv/sso/humiat"), "solvoz"),
            ("ORGANIZA", "Organiza", "Chamados, manutenção, clientes e operação técnica.", f"{PUBLIC_BASE_URL}/organiza", "", "organiza"),
        ]
        for codigo, nome, descricao, url_publica, url_sso, icone in produtos:
            p = db.query(HumiatProduto).filter(HumiatProduto.codigo == codigo).first()
            if not p:
                p = HumiatProduto(codigo=codigo, nome=nome, descricao=descricao, url_publica=url_publica, url_sso=url_sso or None, icone=icone, ativo=1)
                db.add(p)
            else:
                p.nome = nome
                p.descricao = descricao
                p.url_publica = url_publica
                if url_sso:
                    p.url_sso = url_sso
                p.ativo = 1

        admin_email = os.getenv("HUMIAT_ADMIN_EMAIL", "admin@humiat.com.br").strip().lower()
        # Segurança: o Humiat ID nunca cria um administrador novo com senha padrão conhecida.
        # No primeiro deploy, configure HUMIAT_ADMIN_SENHA (ou reutilize explicitamente ORGANIZA_ADMIN_SENHA).
        admin_senha = (os.getenv("HUMIAT_ADMIN_SENHA") or os.getenv("ORGANIZA_ADMIN_SENHA") or "").strip()
        admin_nome = os.getenv("HUMIAT_ADMIN_NOME", ADMIN_NOME).strip() or "Administrador"
        admin = db.query(HumiatUsuario).filter(HumiatUsuario.email == admin_email).first()
        if not admin and admin_senha:
            db.add(HumiatUsuario(
                nome=admin_nome,
                email=admin_email,
                senha_hash=gerar_hash_senha_id(admin_senha),
                tipo=TIPO_ADMIN_HUMIAT,
                ativo=1,
                organiza_usuario=ADMIN_NOME,
            ))
            print(f"[HUMIAT ID] Administrador inicial criado: {admin_email}")
        elif admin:
            # O e-mail de bootstrap pode já existir de um deploy anterior.
            # Nesse caso, as variáveis do Render devem conseguir recuperar o acesso
            # sem exigir edição manual no banco. Sincronizamos nome/tipo/status e,
            # quando HUMIAT_ADMIN_SENHA estiver definida, sincronizamos a senha.
            admin.nome = admin_nome
            admin.tipo = TIPO_ADMIN_HUMIAT
            admin.ativo = 1
            if not (admin.organiza_usuario or "").strip():
                admin.organiza_usuario = ADMIN_NOME
            if admin_senha and not verificar_senha_id(admin_senha, admin.senha_hash):
                admin.senha_hash = gerar_hash_senha_id(admin_senha)
                print(f"[HUMIAT ID] Senha do administrador bootstrap sincronizada: {admin_email}")
        elif not admin_senha:
            print("[HUMIAT ID] Administrador inicial não criado: configure HUMIAT_ADMIN_SENHA no ambiente.")
        db.commit()
    finally:
        db.close()


def humiat_usuario_da_requisicao(request: Request, db: Session) -> HumiatUsuario | None:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        return None
    sessao = db.query(HumiatSessao).filter(HumiatSessao.token_hash == _hash_token(token)).first()
    if not sessao or sessao.expira_em < datetime.utcnow():
        return None
    usuario = db.query(HumiatUsuario).filter(HumiatUsuario.id == sessao.usuario_id, HumiatUsuario.ativo == 1).first()
    if usuario:
        sessao.ultimo_acesso = datetime.utcnow()
        db.commit()
    return usuario


def exigir_humiat_login(request: Request, db: Session = Depends(get_db)) -> HumiatUsuario:
    usuario = humiat_usuario_da_requisicao(request, db)
    if not usuario:
        raise HTTPException(status_code=303, headers={"Location": "/entrar"})
    return usuario


def exigir_admin_humiat(usuario: HumiatUsuario = Depends(exigir_humiat_login)) -> HumiatUsuario:
    if usuario.tipo != TIPO_ADMIN_HUMIAT:
        raise HTTPException(status_code=403, detail="Acesso exclusivo do Administrador Humiat")
    return usuario


def empresas_do_usuario(db: Session, usuario: HumiatUsuario):
    if usuario.tipo == TIPO_ADMIN_HUMIAT:
        return db.query(HumiatEmpresa).filter(HumiatEmpresa.ativo == 1).order_by(HumiatEmpresa.nome).all()
    ids = [x.empresa_id for x in db.query(HumiatUsuarioEmpresa).filter(HumiatUsuarioEmpresa.usuario_id == usuario.id).all()]
    if not ids:
        return []
    return db.query(HumiatEmpresa).filter(HumiatEmpresa.id.in_(ids), HumiatEmpresa.ativo == 1).order_by(HumiatEmpresa.nome).all()


def produtos_da_empresa(db: Session, empresa_id: int):
    joins = db.query(HumiatEmpresaProduto).filter(HumiatEmpresaProduto.empresa_id == empresa_id, HumiatEmpresaProduto.ativo == 1).all()
    ids = [j.produto_id for j in joins]
    if not ids:
        return []
    return db.query(HumiatProduto).filter(HumiatProduto.id.in_(ids), HumiatProduto.ativo == 1).order_by(HumiatProduto.nome).all()


def _contexto_admin_humiat(request: Request, usuario: HumiatUsuario, db: Session, empresa_id: int | None = None) -> dict:
    empresas = db.query(HumiatEmpresa).order_by(HumiatEmpresa.ativo.desc(), HumiatEmpresa.nome).all()
    empresa = next((e for e in empresas if e.id == empresa_id), None) if empresa_id else None
    if not empresa and empresas:
        empresa = next((e for e in empresas if e.ativo), empresas[0])

    produtos = db.query(HumiatProduto).filter(HumiatProduto.ativo == 1).order_by(HumiatProduto.nome).all()
    ep = db.query(HumiatEmpresaProduto).all()
    ativos_por_empresa = {(x.empresa_id, x.produto_id): bool(x.ativo) for x in ep}

    usuarios = db.query(HumiatUsuario).order_by(HumiatUsuario.nome).all()
    vinculos = db.query(HumiatUsuarioEmpresa).all()
    empresa_por_usuario = {}
    for v in vinculos:
        empresa_por_usuario.setdefault(v.usuario_id, v.empresa_id)

    usuarios_empresa = []
    if empresa:
        ids_empresa = {v.usuario_id for v in vinculos if v.empresa_id == empresa.id}
        usuarios_empresa = [u for u in usuarios if u.id in ids_empresa]

    status_produtos = {}
    if empresa:
        status_produtos = {p.id: bool(ativos_por_empresa.get((empresa.id, p.id), False)) for p in produtos}

    solvoz = None
    solvoz_erro = ""
    solvoz_habilitado = bool(empresa and _empresa_tem_produto(db, empresa.id, "SOLVOZ"))
    if solvoz_habilitado:
        solvoz, solvoz_erro = _solvoz_resumo(empresa.slug)

    catalogo_solvoz, catalogo_solvoz_erro = _solvoz_catalogo_resumo()

    return {
        "request": request,
        "usuario": usuario,
        "empresas": empresas,
        "empresa": empresa,
        "produtos": produtos,
        "empresa_produtos": ep,
        "ativos_por_empresa": ativos_por_empresa,
        "usuarios": usuarios,
        "usuarios_empresa": usuarios_empresa,
        "vinculos": vinculos,
        "empresa_por_usuario": empresa_por_usuario,
        "solvoz": solvoz,
        "solvoz_erro": solvoz_erro,
        "solvoz_habilitado": solvoz_habilitado,
        "catalogo_solvoz": catalogo_solvoz,
        "catalogo_solvoz_erro": catalogo_solvoz_erro,
        "status_produtos": status_produtos,
        "solvoz_base_url": SOLVOZ_BASE_URL,
        "solvoz_diagnostics_url": f"{SOLVOZ_BASE_URL}{SOLVOZ_DIAGNOSTICS_PATH}",
        "admin_humiat": True,
    }


@router.get("/entrar", response_class=HTMLResponse)
def login_humiat(request: Request, erro: str = "", db: Session = Depends(get_db)):
    if humiat_usuario_da_requisicao(request, db):
        return RedirectResponse("/painel", status_code=303)
    return templates.TemplateResponse("humiat/login.html", {"request": request, "erro": erro})


@router.get("/esqueci-senha", response_class=HTMLResponse)
def esqueci_senha_humiat(request: Request, enviado: str = "", erro: str = ""):
    return templates.TemplateResponse("humiat/esqueci_senha.html", {"request": request, "enviado": enviado, "erro": erro})


@router.post("/esqueci-senha")
def solicitar_reset_humiat(request: Request, email: str = Form(...), db: Session = Depends(get_db)):
    login = email.strip().lower()
    usuario = db.query(HumiatUsuario).filter(HumiatUsuario.email == login, HumiatUsuario.ativo == 1).first()
    # Resposta pública é sempre igual para não revelar quais e-mails estão cadastrados.
    if usuario:
        agora = datetime.utcnow()
        recente = (db.query(HumiatSenhaReset)
            .filter(HumiatSenhaReset.usuario_id == usuario.id, HumiatSenhaReset.criado_em >= agora - timedelta(minutes=2))
            .order_by(HumiatSenhaReset.id.desc()).first())
        if not recente:
            token = secrets.token_urlsafe(40)
            reset = HumiatSenhaReset(
                token_hash=_hash_token(token), usuario_id=usuario.id,
                expira_em=agora + timedelta(minutes=RESET_MINUTES), ip=_ip(request)
            )
            db.add(reset)
            _auditar(db, request, "SENHA_RESET_SOLICITADO", usuario_id=usuario.id)
            db.commit()
            link = f"{PUBLIC_BASE_URL.rstrip('/')}/redefinir-senha?token={urllib.parse.quote(token)}"
            try:
                _enviar_email_recuperacao(usuario.email, usuario.nome, link)
                _auditar(db, request, "SENHA_RESET_EMAIL_ENVIADO", usuario_id=usuario.id)
                db.commit()
            except Exception as exc:
                _auditar(db, request, "SENHA_RESET_EMAIL_ERRO", usuario_id=usuario.id, detalhe=str(exc))
                db.commit()
                print(f"[HUMIAT ID] Falha ao enviar recuperação para {usuario.email}: {exc}")
    return RedirectResponse("/esqueci-senha?enviado=1", status_code=303)


@router.get("/redefinir-senha", response_class=HTMLResponse)
def formulario_reset_humiat(request: Request, token: str = "", erro: str = "", db: Session = Depends(get_db)):
    item = db.query(HumiatSenhaReset).filter(HumiatSenhaReset.token_hash == _hash_token(token)).first() if token else None
    valido = bool(item and not item.usado_em and item.expira_em >= datetime.utcnow())
    return templates.TemplateResponse("humiat/redefinir_senha.html", {"request": request, "token": token, "valido": valido, "erro": erro})


@router.post("/redefinir-senha")
def concluir_reset_humiat(request: Request, token: str = Form(...), senha: str = Form(...), confirmar: str = Form(...), db: Session = Depends(get_db)):
    item = db.query(HumiatSenhaReset).filter(HumiatSenhaReset.token_hash == _hash_token(token)).first()
    if not item or item.usado_em or item.expira_em < datetime.utcnow():
        return RedirectResponse("/redefinir-senha?erro=Link expirado ou inválido", status_code=303)
    if senha != confirmar:
        return RedirectResponse(f"/redefinir-senha?token={urllib.parse.quote(token)}&erro=As senhas não conferem", status_code=303)
    if len(senha) < 8:
        return RedirectResponse(f"/redefinir-senha?token={urllib.parse.quote(token)}&erro=A senha precisa ter pelo menos 8 caracteres", status_code=303)
    usuario = db.query(HumiatUsuario).filter(HumiatUsuario.id == item.usuario_id, HumiatUsuario.ativo == 1).first()
    if not usuario:
        return RedirectResponse("/redefinir-senha?erro=Link expirado ou inválido", status_code=303)
    usuario.senha_hash = gerar_hash_senha_id(senha)
    item.usado_em = datetime.utcnow()
    # Encerra sessões antigas após troca de senha.
    db.query(HumiatSessao).filter(HumiatSessao.usuario_id == usuario.id).delete(synchronize_session=False)
    _auditar(db, request, "SENHA_RESET_CONCLUIDO", usuario_id=usuario.id)
    db.commit()
    return RedirectResponse("/entrar?erro=Senha redefinida. Entre com a nova senha.", status_code=303)


@router.post("/entrar")
def entrar_humiat(request: Request, email: str = Form(...), senha: str = Form(...), db: Session = Depends(get_db)):
    login = email.strip().lower()
    usuario = db.query(HumiatUsuario).filter(HumiatUsuario.email == login, HumiatUsuario.ativo == 1).first()
    if not usuario or not verificar_senha_id(senha, usuario.senha_hash):
        _auditar(db, request, "LOGIN_FALHOU", detalhe=login)
        db.commit()
        return RedirectResponse("/entrar?erro=E-mail ou senha inválidos", status_code=303)

    token = secrets.token_urlsafe(40)
    db.add(HumiatSessao(token_hash=_hash_token(token), usuario_id=usuario.id, expira_em=datetime.utcnow() + timedelta(days=SESSION_DAYS), ultimo_acesso=datetime.utcnow(), ip=_ip(request), user_agent=request.headers.get("user-agent", "")[:300]))
    _auditar(db, request, "LOGIN_OK", usuario_id=usuario.id)
    db.commit()
    resposta = RedirectResponse("/painel", status_code=303)
    resposta.set_cookie(COOKIE_NAME, token, httponly=True, secure=PUBLIC_BASE_URL.startswith("https://"), samesite="lax", max_age=SESSION_DAYS * 86400, path="/")
    return resposta


@router.get("/sair")
def sair_humiat(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        sessao = db.query(HumiatSessao).filter(HumiatSessao.token_hash == _hash_token(token)).first()
        if sessao:
            db.delete(sessao)
            db.commit()
    resposta = RedirectResponse("/entrar", status_code=303)
    resposta.delete_cookie(COOKIE_NAME, path="/")
    return resposta


@router.get("/painel", response_class=HTMLResponse)
def painel_humiat(request: Request, empresa_id: int | None = None, usuario: HumiatUsuario = Depends(exigir_humiat_login), db: Session = Depends(get_db)):
    # O Administrador Humiat trabalha em uma única tela. A rota /admin-humiat
    # continua existindo somente por compatibilidade e redireciona para cá.
    if usuario.tipo == TIPO_ADMIN_HUMIAT:
        return templates.TemplateResponse("humiat/admin.html", _contexto_admin_humiat(request, usuario, db, empresa_id))

    empresas = empresas_do_usuario(db, usuario)
    empresa = None
    if empresa_id:
        empresa = next((e for e in empresas if e.id == empresa_id), None)
        if not empresa:
            raise HTTPException(status_code=403, detail="Empresa não autorizada")
    elif len(empresas) == 1:
        empresa = empresas[0]
    elif empresas:
        empresa = empresas[0]

    produtos = produtos_da_empresa(db, empresa.id) if empresa else []
    return templates.TemplateResponse(
        "humiat/painel.html",
        {"request": request, "usuario": usuario, "empresas": empresas, "empresa": empresa, "produtos": produtos, "admin_humiat": False},
    )


@router.get("/painel/produto/{codigo}")
def abrir_produto(codigo: str, request: Request, empresa_id: int | None = None, usuario: HumiatUsuario = Depends(exigir_humiat_login), db: Session = Depends(get_db)):
    codigo = codigo.strip().upper()
    produto = db.query(HumiatProduto).filter(HumiatProduto.codigo == codigo, HumiatProduto.ativo == 1).first()
    if not produto:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    empresas = empresas_do_usuario(db, usuario)
    empresa = next((e for e in empresas if e.id == empresa_id), None) if empresa_id else (empresas[0] if len(empresas) == 1 else None)
    if usuario.tipo != TIPO_ADMIN_HUMIAT:
        if not empresa:
            raise HTTPException(status_code=400, detail="Selecione a empresa")
        permitidos = {p.codigo for p in produtos_da_empresa(db, empresa.id)}
        if codigo not in permitidos:
            raise HTTPException(status_code=403, detail="Produto não habilitado para esta empresa")

    _auditar(db, request, "ABRIR_PRODUTO", usuario_id=usuario.id, empresa_id=empresa.id if empresa else None, detalhe=codigo)
    db.commit()

    if codigo == "ORGANIZA":
        # O Organiza está no mesmo projeto. O app.py reconhece a sessão Humiat para usuários mapeados.
        return RedirectResponse("/organiza", status_code=303)

    if produto.url_sso:
        token = secrets.token_urlsafe(40)
        db.add(HumiatSSOTicket(token_hash=_hash_token(token), usuario_id=usuario.id, empresa_id=empresa.id if empresa else None, produto_codigo=codigo, expira_em=datetime.utcnow() + timedelta(minutes=SSO_MINUTES)))
        db.commit()
        sep = "&" if "?" in produto.url_sso else "?"
        return RedirectResponse(f"{produto.url_sso}{sep}{urlencode({'humiat_ticket': token})}", status_code=303)

    # Enquanto o receptor SSO do produto ainda não foi publicado, mantém acesso ao produto público.
    if produto.url_publica:
        return RedirectResponse(produto.url_publica, status_code=303)
    raise HTTPException(status_code=503, detail="Produto sem URL configurada")


@router.post("/api/humiat/sso/validar")
def validar_ticket_sso(ticket: str = Form(...), x_humiat_sso_secret: str = Header(default=""), db: Session = Depends(get_db)):
    if not SSO_SECRET or not hmac.compare_digest(x_humiat_sso_secret, SSO_SECRET):
        raise HTTPException(status_code=401, detail="Integração SSO não autorizada")
    item = db.query(HumiatSSOTicket).filter(HumiatSSOTicket.token_hash == _hash_token(ticket)).first()
    if not item or item.usado_em or item.expira_em < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Ticket inválido ou expirado")
    usuario = db.query(HumiatUsuario).filter(HumiatUsuario.id == item.usuario_id, HumiatUsuario.ativo == 1).first()
    empresa = db.query(HumiatEmpresa).filter(HumiatEmpresa.id == item.empresa_id).first() if item.empresa_id else None
    item.usado_em = datetime.utcnow()
    db.commit()
    return {"ok": True, "usuario": {"id": usuario.id, "nome": usuario.nome, "email": usuario.email, "tipo": usuario.tipo}, "empresa": ({"id": empresa.id, "nome": empresa.nome, "slug": empresa.slug} if empresa else None), "produto": item.produto_codigo}



@router.post("/admin-humiat/solvoz/catalogo/importar")
async def importar_catalogo_solvoz(
    request: Request,
    arquivo: UploadFile = File(...),
    empresa_id: int | None = Form(None),
    usuario: HumiatUsuario = Depends(exigir_admin_humiat),
    db: Session = Depends(get_db),
):
    nome = (arquivo.filename or "").strip()
    if not nome.lower().endswith(".xlsx"):
        msg = urllib.parse.quote("Selecione uma planilha Excel .xlsx.")
        destino = f"/painel?erro={msg}" + (f"&empresa_id={empresa_id}" if empresa_id else "")
        return RedirectResponse(destino, status_code=303)

    dados = await arquivo.read()
    if not dados:
        msg = urllib.parse.quote("A planilha selecionada está vazia.")
        destino = f"/painel?erro={msg}" + (f"&empresa_id={empresa_id}" if empresa_id else "")
        return RedirectResponse(destino, status_code=303)
    if len(dados) > 30*1024*1024:
        msg = urllib.parse.quote("A planilha ultrapassa o limite de 30 MB.")
        destino = f"/painel?erro={msg}" + (f"&empresa_id={empresa_id}" if empresa_id else "")
        return RedirectResponse(destino, status_code=303)

    try:
        retorno = _solvoz_api_arquivo(
            "/_sv/api/humiat/catalogo/importar",
            nome_arquivo=nome,
            conteudo=dados,
            content_type=arquivo.content_type or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            dados={},
        )
    except Exception as exc:
        _auditar(db, request, "IMPORTAR_CATALOGO_SOLVOZ_FALHOU", usuario.id, detalhe=str(exc))
        db.commit()
        msg = urllib.parse.quote(str(exc))
        destino = f"/painel?erro={msg}" + (f"&empresa_id={empresa_id}" if empresa_id else "")
        return RedirectResponse(destino, status_code=303)

    _auditar(db, request, "IMPORTAR_CATALOGO_SOLVOZ", usuario.id,
             detalhe=f"arquivo={nome};modo=upsert;total={retorno.get('total')};tempo={retorno.get('com_tempo')}")
    db.commit()

    params = {
        "ok": "catalogo_importado",
        "total": retorno.get("total", 0),
        "novos": retorno.get("novos", 0),
        "atualizados": retorno.get("atualizados", 0),
        "tempo": retorno.get("com_tempo", 0),
    }
    if empresa_id:
        params["empresa_id"]=empresa_id
    return RedirectResponse("/painel?"+urllib.parse.urlencode(params),status_code=303)


@router.get("/admin-humiat/diagnosticos-solvoz")
def diagnosticos_solvoz(
    usuario: HumiatUsuario = Depends(exigir_admin_humiat),
):
    """Atalho protegido do ADM Humiat para o painel técnico do SolVoz/Render."""
    return RedirectResponse(
        f"{SOLVOZ_BASE_URL}{SOLVOZ_DIAGNOSTICS_PATH}",
        status_code=303,
    )


@router.get("/admin-humiat", response_class=HTMLResponse)
def admin_humiat(request: Request, empresa_id: int | None = None, usuario: HumiatUsuario = Depends(exigir_admin_humiat), db: Session = Depends(get_db)):
    destino = "/painel"
    if empresa_id:
        destino += f"?empresa_id={empresa_id}"
    return RedirectResponse(destino, status_code=303)


@router.post("/admin-humiat/empresas")
def criar_empresa_humiat(
    request: Request,
    nome: str = Form(...),
    slug: str = Form(...),
    criar_solvoz: str = Form(""),
    usuario: HumiatUsuario = Depends(exigir_admin_humiat),
    db: Session = Depends(get_db),
):
    slug_n = _slug(slug)
    nome_n = nome.strip()
    if not slug_n or not nome_n:
        return RedirectResponse("/painel?erro=Informe nome e slug válidos", status_code=303)
    if db.query(HumiatEmpresa).filter(HumiatEmpresa.slug == slug_n).first():
        return RedirectResponse("/painel?erro=Slug já utilizado", status_code=303)

    # Quando solicitado, cria primeiro a estrutura em branco no SolVoz. Assim a
    # empresa nunca aparece como habilitada no Humiat sem existir no produto.
    solvoz_criado = False
    if criar_solvoz == "1":
        try:
            _solvoz_api(
                "/_sv/api/humiat/empresa/criar",
                metodo="POST",
                dados={"nome": nome_n, "slug": slug_n},
            )
            solvoz_criado = True
        except Exception as exc:
            return RedirectResponse(
                f"/painel?erro={urllib.parse.quote('Falha ao criar a empresa no SolVoz: ' + str(exc))}",
                status_code=303,
            )

    e = HumiatEmpresa(nome=nome_n, slug=slug_n, ativo=0 if solvoz_criado else 1)
    db.add(e)
    db.flush()

    if solvoz_criado:
        produto = _produto_solvoz(db)
        if produto:
            db.add(HumiatEmpresaProduto(empresa_id=e.id, produto_id=produto.id, ativo=1))

    _auditar(db, request, "CRIAR_EMPRESA", usuario.id, e.id, e.nome)
    db.commit()
    return RedirectResponse(f"/painel?empresa_id={e.id}&ok=empresa_criada", status_code=303)


@router.post("/admin-humiat/empresa/{empresa_id}/clonar")
def clonar_empresa_humiat(
    empresa_id: int,
    request: Request,
    nome: str = Form(...),
    slug: str = Form(...),
    copiar_inicio: str = Form(""),
    copiar_home: str = Form(""),
    copiar_redes: str = Form(""),
    copiar_logo: str = Form(""),
    copiar_cores: str = Form(""),
    usuario: HumiatUsuario = Depends(exigir_admin_humiat),
    db: Session = Depends(get_db),
):
    origem = db.query(HumiatEmpresa).filter(HumiatEmpresa.id == empresa_id).first()
    if not origem:
        raise HTTPException(status_code=404, detail="Empresa modelo não encontrada")

    nome_n = nome.strip()
    slug_n = _slug(slug)
    if not nome_n or not slug_n:
        return RedirectResponse(f"/painel?empresa_id={empresa_id}&erro=Informe nome e slug válidos", status_code=303)
    if db.query(HumiatEmpresa).filter(HumiatEmpresa.slug == slug_n).first():
        return RedirectResponse(
            f"/painel?empresa_id={empresa_id}&erro={urllib.parse.quote('Já existe uma empresa com esse slug no Humiat')}",
            status_code=303,
        )

    acessos = db.query(HumiatEmpresaProduto).filter(HumiatEmpresaProduto.empresa_id == origem.id).all()
    solvoz = _produto_solvoz(db)
    solvoz_ativo = bool(solvoz and any(x.produto_id == solvoz.id and x.ativo for x in acessos))

    # A cópia do conteúdo SolVoz acontece antes da gravação central. Se o
    # produto recusar a clonagem, o Humiat não cria um cadastro pela metade.
    if solvoz_ativo:
        try:
            _solvoz_api(
                "/_sv/api/humiat/empresa/clonar",
                metodo="POST",
                dados={
                    "origem_slug": origem.slug,
                    "nome": nome_n,
                    "slug": slug_n,
                    "copiar_inicio": "1" if copiar_inicio == "1" else "",
                    "copiar_home": "1" if copiar_home == "1" else "",
                    "copiar_redes": "1" if copiar_redes == "1" else "",
                    "copiar_logo": "1" if copiar_logo == "1" else "",
                    "copiar_cores": "1" if copiar_cores == "1" else "",
                },
            )
        except Exception as exc:
            msg = urllib.parse.quote("Falha ao clonar no SolVoz: " + str(exc))
            return RedirectResponse(f"/painel?empresa_id={empresa_id}&erro={msg}", status_code=303)

    nova = HumiatEmpresa(nome=nome_n, slug=slug_n, ativo=0)
    db.add(nova)
    db.flush()
    for item in acessos:
        db.add(HumiatEmpresaProduto(empresa_id=nova.id, produto_id=item.produto_id, ativo=item.ativo))

    _auditar(
        db,
        request,
        "CLONAR_EMPRESA",
        usuario.id,
        nova.id,
        f"origem={origem.id}:{origem.slug}; nova={slug_n}; solvoz={int(solvoz_ativo)}",
    )
    db.commit()
    return RedirectResponse(f"/painel?empresa_id={nova.id}&ok=empresa_clonada", status_code=303)


@router.post("/admin-humiat/empresa/{empresa_id}/status")
def alternar_status_empresa(
    empresa_id: int,
    request: Request,
    usuario: HumiatUsuario = Depends(exigir_admin_humiat),
    db: Session = Depends(get_db),
):
    empresa = db.query(HumiatEmpresa).filter(HumiatEmpresa.id == empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=404)
    novo_ativo = 0 if empresa.ativo else 1

    # Publicação do SolVoz acompanha o status central. Isso evita o cenário em
    # que o Humiat diz "Ativa", mas o catálogo continua em revisão.
    if _empresa_tem_produto(db, empresa_id, "SOLVOZ"):
        try:
            _solvoz_api(
                f"/_sv/api/humiat/empresa/{urllib.parse.quote(empresa.slug)}/status",
                metodo="POST",
                dados={"ativo": str(novo_ativo)},
            )
        except Exception as exc:
            msg = urllib.parse.quote("Não foi possível atualizar o status no SolVoz: " + str(exc))
            return RedirectResponse(f"/painel?empresa_id={empresa_id}&erro={msg}", status_code=303)

    empresa.ativo = novo_ativo
    _auditar(db, request, "ALTERAR_STATUS_EMPRESA", usuario.id, empresa.id, f"ativo={empresa.ativo}")
    db.commit()
    return RedirectResponse(f"/painel?empresa_id={empresa.id}&ok=status_atualizado", status_code=303)


@router.post("/admin-humiat/usuarios")
def criar_usuario_humiat(request: Request, nome: str = Form(...), email: str = Form(...), senha: str = Form(...), tipo: str = Form(TIPO_ADMIN_EMPRESA), empresa_id: str = Form(""), usuario: HumiatUsuario = Depends(exigir_admin_humiat), db: Session = Depends(get_db)):
    email = email.strip().lower()
    if db.query(HumiatUsuario).filter(HumiatUsuario.email == email).first():
        return RedirectResponse(f"/painel?empresa_id={empresa_id if empresa_id.strip().isdigit() else ''}&erro=E-mail já cadastrado", status_code=303)
    tipo = tipo if tipo in {TIPO_ADMIN_HUMIAT, TIPO_ADMIN_EMPRESA} else TIPO_ADMIN_EMPRESA
    if tipo == TIPO_ADMIN_EMPRESA and not empresa_id.strip().isdigit():
        return RedirectResponse("/painel?erro=Selecione a empresa para o Administrador da Empresa", status_code=303)
    novo = HumiatUsuario(nome=nome.strip(), email=email, senha_hash=gerar_hash_senha_id(senha), tipo=tipo, ativo=1, organiza_usuario=None)
    db.add(novo); db.flush()
    if tipo == TIPO_ADMIN_EMPRESA:
        db.add(HumiatUsuarioEmpresa(usuario_id=novo.id, empresa_id=int(empresa_id)))
    _auditar(db, request, "CRIAR_USUARIO", usuario.id, int(empresa_id) if empresa_id.strip().isdigit() else None, email)
    db.commit()
    return RedirectResponse(f"/painel?empresa_id={empresa_id if empresa_id.strip().isdigit() else ''}&ok=usuario_criado", status_code=303)


@router.post("/admin-humiat/empresa/{empresa_id}/produto/{produto_id}")
def alternar_produto(empresa_id: int, produto_id: int, request: Request, usuario: HumiatUsuario = Depends(exigir_admin_humiat), db: Session = Depends(get_db)):
    empresa = db.query(HumiatEmpresa).filter(HumiatEmpresa.id == empresa_id).first()
    produto = db.query(HumiatProduto).filter(HumiatProduto.id == produto_id).first()
    if not empresa or not produto:
        raise HTTPException(status_code=404)

    item = db.query(HumiatEmpresaProduto).filter(
        HumiatEmpresaProduto.empresa_id == empresa_id,
        HumiatEmpresaProduto.produto_id == produto_id,
    ).first()
    novo_ativo = 0 if (item and item.ativo) else 1

    # Ao habilitar o SolVoz, garante que exista uma empresa correspondente no
    # produto. O endpoint é idempotente: empresa existente não é sobrescrita.
    # Ao desligar, o catálogo também sai de publicação.
    if produto.codigo == "SOLVOZ":
        try:
            if novo_ativo:
                _solvoz_api(
                    "/_sv/api/humiat/empresa/criar",
                    metodo="POST",
                    dados={"nome": empresa.nome, "slug": empresa.slug},
                )
            _solvoz_api(
                f"/_sv/api/humiat/empresa/{urllib.parse.quote(empresa.slug)}/status",
                metodo="POST",
                dados={"ativo": "1" if (novo_ativo and empresa.ativo) else "0"},
            )
        except Exception as exc:
            msg = urllib.parse.quote("Não foi possível atualizar o SolVoz: " + str(exc))
            return RedirectResponse(f"/painel?empresa_id={empresa_id}&erro={msg}", status_code=303)

    if item:
        item.ativo = novo_ativo
    else:
        item = HumiatEmpresaProduto(empresa_id=empresa_id, produto_id=produto_id, ativo=novo_ativo)
        db.add(item)

    _auditar(db, request, "ALTERAR_PRODUTO_EMPRESA", usuario.id, empresa_id, f"produto={produto_id}; ativo={item.ativo}")
    db.commit()
    return RedirectResponse(f"/painel?empresa_id={empresa_id}&ok=produto_atualizado", status_code=303)


@router.post("/admin-humiat/empresa/{empresa_id}/solvoz/identidade")
def salvar_identidade_solvoz(
    empresa_id: int,
    request: Request,
    tema: str = Form("party"),
    brand: str = Form("#ff3fb4"),
    brand_2: str = Form("#35c7ff"),
    accent: str = Form("#ffe44c"),
    bg: str = Form("#fff7ff"),
    surface: str = Form("#ffffff"),
    text: str = Form("#1a1230"),
    usuario: HumiatUsuario = Depends(exigir_admin_humiat),
    db: Session = Depends(get_db),
):
    empresa = db.query(HumiatEmpresa).filter(HumiatEmpresa.id == empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=404)
    if not _empresa_tem_produto(db, empresa_id, "SOLVOZ"):
        return RedirectResponse(f"/painel?empresa_id={empresa_id}&erro=SolVoz não está habilitado", status_code=303)
    try:
        _solvoz_api(
            f"/_sv/api/humiat/empresa/{urllib.parse.quote(empresa.slug)}/identidade",
            metodo="POST",
            dados={
                "tema": tema,
                "brand": brand,
                "brand_2": brand_2,
                "accent": accent,
                "bg": bg,
                "surface": surface,
                "text": text,
            },
        )
    except Exception as exc:
        msg = urllib.parse.quote("Não foi possível salvar as cores: " + str(exc))
        return RedirectResponse(f"/painel?empresa_id={empresa_id}&erro={msg}", status_code=303)
    _auditar(db, request, "ALTERAR_IDENTIDADE_SOLVOZ", usuario.id, empresa_id, f"tema={tema}")
    db.commit()
    return RedirectResponse(f"/painel?empresa_id={empresa_id}&ok=cores_salvas", status_code=303)


@router.post("/admin-humiat/empresa/{empresa_id}/solvoz/maquinas/sincronizar")
def sincronizar_maquinas_solvoz(
    empresa_id: int,
    request: Request,
    usuario: HumiatUsuario = Depends(exigir_admin_humiat),
    db: Session = Depends(get_db),
):
    """Aciona pelo ADM unificado a sincronização Organiza -> SolVoz."""
    empresa = db.query(HumiatEmpresa).filter(HumiatEmpresa.id == empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=404)
    if not _empresa_tem_produto(db, empresa_id, "SOLVOZ"):
        return RedirectResponse(
            f"/painel?empresa_id={empresa_id}&erro={urllib.parse.quote('SolVoz não está habilitado para esta empresa')}",
            status_code=303,
        )
    try:
        retorno = _solvoz_api(
            f"/_sv/api/humiat/empresa/{urllib.parse.quote(empresa.slug)}/maquinas/sincronizar",
            metodo="POST",
            dados={},
        )
        sync = retorno.get("sincronizacao") or {}
        qtd = int(sync.get("importadas") or 0)
    except Exception as exc:
        msg = urllib.parse.quote("Falha ao sincronizar máquinas do Organiza: " + str(exc))
        return RedirectResponse(f"/painel?empresa_id={empresa_id}&erro={msg}", status_code=303)

    _auditar(
        db, request, "SINCRONIZAR_MAQUINAS_ORGANIZA", usuario.id, empresa_id,
        f"importadas={qtd}",
    )
    db.commit()
    return RedirectResponse(
        f"/painel?empresa_id={empresa_id}&ok=maquinas_sincronizadas&qtd={qtd}",
        status_code=303,
    )


@router.get("/painel/empresa/{empresa_id}/qr.png")
def qr_empresa_humiat(
    empresa_id: int,
    request: Request,
    download: int = 0,
    usuario: HumiatUsuario = Depends(exigir_humiat_login),
    db: Session = Depends(get_db),
):
    empresa = db.query(HumiatEmpresa).filter(HumiatEmpresa.id == empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=404)

    if usuario.tipo != TIPO_ADMIN_HUMIAT:
        if not empresa.ativo:
            raise HTTPException(status_code=404)
        permitidos = {e.id for e in empresas_do_usuario(db, usuario)}
        if empresa_id not in permitidos:
            raise HTTPException(status_code=403)

    if not _empresa_tem_produto(db, empresa_id, "SOLVOZ"):
        raise HTTPException(status_code=404, detail="SolVoz não habilitado para esta empresa")

    try:
        import qrcode
    except Exception:
        raise HTTPException(status_code=500, detail="Biblioteca qrcode não instalada")

    destino = f"{SOLVOZ_BASE_URL}/{empresa.slug}/catalogo"
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=9, border=4)
    qr.add_data(destino)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO()
    img.save(bio, format="PNG")
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="qr-{empresa.slug}.png"'
    return Response(content=bio.getvalue(), media_type="image/png", headers=headers)


@router.post("/admin-humiat/usuario/{usuario_id}/editar")
def editar_usuario_humiat(
    usuario_id: int,
    request: Request,
    nome: str = Form(...),
    email: str = Form(...),
    tipo: str = Form(TIPO_ADMIN_EMPRESA),
    empresa_id: str = Form(""),
    ativo: str = Form("1"),
    retorno_empresa_id: str = Form(""),
    admin: HumiatUsuario = Depends(exigir_admin_humiat),
    db: Session = Depends(get_db),
):
    alvo = db.query(HumiatUsuario).filter(HumiatUsuario.id == usuario_id).first()
    if not alvo:
        raise HTTPException(status_code=404)

    email = email.strip().lower()
    existente = db.query(HumiatUsuario).filter(HumiatUsuario.email == email, HumiatUsuario.id != usuario_id).first()
    if existente:
        return RedirectResponse(f"/painel?empresa_id={retorno_empresa_id}&erro=E-mail já utilizado por outro usuário", status_code=303)

    tipo = tipo if tipo in {TIPO_ADMIN_HUMIAT, TIPO_ADMIN_EMPRESA} else TIPO_ADMIN_EMPRESA
    if tipo == TIPO_ADMIN_EMPRESA and not empresa_id.strip().isdigit():
        return RedirectResponse(f"/painel?empresa_id={retorno_empresa_id}&erro=Administrador da Empresa precisa estar vinculado a uma empresa", status_code=303)

    alvo.nome = nome.strip()
    alvo.email = email
    alvo.tipo = tipo
    alvo.ativo = 1 if ativo == "1" else 0
    # Campo legado mantido apenas no banco por compatibilidade; não faz parte do Humiat ID.
    alvo.organiza_usuario = None

    db.query(HumiatUsuarioEmpresa).filter(HumiatUsuarioEmpresa.usuario_id == usuario_id).delete(synchronize_session=False)
    empresa_auditoria = None
    if tipo == TIPO_ADMIN_EMPRESA:
        empresa_auditoria = int(empresa_id)
        db.add(HumiatUsuarioEmpresa(usuario_id=usuario_id, empresa_id=empresa_auditoria))

    _auditar(db, request, "EDITAR_USUARIO", admin.id, empresa_auditoria, f"usuario={alvo.email}; tipo={tipo}; ativo={alvo.ativo}")
    db.commit()
    destino_id = retorno_empresa_id if retorno_empresa_id.strip().isdigit() else (str(empresa_auditoria) if empresa_auditoria else "")
    return RedirectResponse(f"/painel?empresa_id={destino_id}&ok=usuario_atualizado", status_code=303)


@router.post("/admin-humiat/usuario/{usuario_id}/senha")
def redefinir_senha(
    usuario_id: int,
    request: Request,
    senha: str = Form(...),
    retorno_empresa_id: str = Form(""),
    admin: HumiatUsuario = Depends(exigir_admin_humiat),
    db: Session = Depends(get_db),
):
    alvo = db.query(HumiatUsuario).filter(HumiatUsuario.id == usuario_id).first()
    if not alvo:
        raise HTTPException(status_code=404)
    alvo.senha_hash = gerar_hash_senha_id(senha)
    _auditar(db, request, "REDEFINIR_SENHA", admin.id, detalhe=alvo.email)
    db.commit()
    return RedirectResponse(f"/painel?empresa_id={retorno_empresa_id}&ok=senha_redefinida", status_code=303)
