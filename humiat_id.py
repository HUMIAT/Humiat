import hashlib
import hmac
import html
import json
import base64
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from io import BytesIO
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func, inspect, text
from sqlalchemy.orm import Session, relationship

from database import Base, SessionLocal, get_db
from config import ADMIN_NOME, ADMIN_SENHA, PUBLIC_BASE_URL

router = APIRouter()
templates = Jinja2Templates(directory="templates")

COOKIE_NAME = "humiat_id"
SESSION_DAYS = 1  # sessão central Humiat ID: 24 horas
SSO_MINUTES = int(os.getenv("HUMIAT_SSO_MINUTES", "2"))
SSO_SECRET = os.getenv("HUMIAT_SSO_SECRET", "").strip()
SOLVOZ_BASE_URL = os.getenv("HUMIAT_SOLVOZ_URL", "https://www.solvoz.com.br").strip().rstrip("/")
SOLVOZ_API_TIMEOUT = float(os.getenv("HUMIAT_SOLVOZ_API_TIMEOUT", "8") or "8")
SOLVOZ_DIAGNOSTICS_PATH = os.getenv("HUMIAT_SOLVOZ_DIAGNOSTICS_PATH", "/_sv/uso/7f29c4b8").strip() or "/_sv/uso/7f29c4b8"
CONNECT_BASE_URL = os.getenv("HUMIAT_CONNECT_URL", "https://conect.humiat.com.br").strip().rstrip("/")
LOKAFEST_BASE_URL = os.getenv("HUMIAT_LOKAFEST_URL", "https://lokafest.com.br").strip().rstrip("/")
CONNECT_LOGOUT_URL = os.getenv("HUMIAT_CONNECT_LOGOUT_URL", f"{CONNECT_BASE_URL}/_connect/logout-humiat").strip()
SOLVOZ_LOGOUT_URL = os.getenv("HUMIAT_SOLVOZ_LOGOUT_URL", f"{SOLVOZ_BASE_URL}/_sv/logout-humiat").strip()

RESET_MINUTES = int(os.getenv("HUMIAT_RESET_MINUTES", "30") or "30")
PAINEL_CACHE_MINUTES = int(os.getenv("HUMIAT_PAINEL_CACHE_MINUTES", "240") or "240")
PENDENCIAS_CACHE_MINUTES = int(os.getenv("HUMIAT_PENDENCIAS_CACHE_MINUTES", "60") or "60")
RESEND_API_KEY = os.getenv("HUMIAT_RESEND_API_KEY", "").strip()
EMAIL_FROM = os.getenv("HUMIAT_EMAIL_FROM", "").strip()
RESEND_API_URL = os.getenv("HUMIAT_RESEND_API_URL", "https://api.resend.com/emails").strip()

TIPO_ADMIN_HUMIAT = "ADMIN_HUMIAT"
# APP 8.7 — o acesso é decidido pelo vínculo com empresa.
# Sem vínculo = equipe interna/perfil completo. Com vínculo = Área da Empresa.
# O campo tipo continua apenas por compatibilidade com dados antigos.
TIPO_CLIENTE_EMPRESA = "CLIENTE_EMPRESA"
TIPO_ADMIN_EMPRESA = "ADMIN_EMPRESA"  # legado, somente para migração/compatibilidade

# Usuários internos legados do Organiza. O vínculo (ou ausência dele) continua
# sendo a regra final; esta lista serve somente para limpar vínculos antigos
# criados por versões anteriores. Pode ser ampliada no Render sem novo deploy.
_EQUIPE_INTERNA_PADRAO = {"junior", "debora", "luiz"}
_EQUIPE_INTERNA_EMAILS_PADRAO = {"jr.delphi@gmail.com", "deborapavonerabello@gmail.com", "bidults@gmail.com"}

def _norm_identidade(valor: str | None) -> str:
    return (valor or "").strip().lower()

def _equipe_interna_usuarios_configurados() -> set[str]:
    extra = os.getenv("HUMIAT_EQUIPE_INTERNA_USUARIOS", "")
    return _EQUIPE_INTERNA_PADRAO | {_norm_identidade(x) for x in extra.split(",") if _norm_identidade(x)}

def _equipe_interna_emails_configurados() -> set[str]:
    extra = os.getenv("HUMIAT_EQUIPE_INTERNA_EMAILS", "")
    return _EQUIPE_INTERNA_EMAILS_PADRAO | {_norm_identidade(x) for x in extra.split(",") if _norm_identidade(x)}


def _eh_equipe_interna_prioritaria(nome: str | None = None, email: str | None = None) -> bool:
    """Regra inicial do Humiat ID para Junior, Debora e Luiz.

    A comparação por nome aceita sobrenome (ex.: ``Luiz Souza``) e a lista de
    e-mails pode ser ampliada no Render. A regra serve apenas para o acesso
    inicial; depois que uma permissão por produto existe, a edição manual é
    respeitada.
    """
    nome_n = _norm_identidade(nome)
    email_n = _norm_identidade(email)
    if email_n and email_n in _equipe_interna_emails_configurados():
        return True
    for base in _equipe_interna_usuarios_configurados():
        if nome_n == base or nome_n.startswith(base + " "):
            return True
    return False


def _usuario_equipe_interna_prioritaria(usuario: "HumiatUsuario") -> bool:
    return _eh_equipe_interna_prioritaria(
        (usuario.organiza_usuario or usuario.nome or ""),
        usuario.email,
    ) or _eh_equipe_interna_prioritaria(usuario.nome, usuario.email)


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
    tipo = Column(String(30), nullable=False, default=TIPO_CLIENTE_EMPRESA)
    ativo = Column(Integer, nullable=False, default=1)
    organiza_usuario = Column(String(80), nullable=True)
    documento = Column(String(30), nullable=True)
    telefone = Column(String(40), nullable=True)
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


class HumiatUsuarioProduto(Base):
    """Permissões da identidade Humiat por produto.

    `acesso_sistema` e `acesso_adm` são independentes. Nesta primeira fase a
    autorização administrativa é aplicada ao SolVoz; a tabela já fica pronta
    para Connect, Organiza e LokaFest sem criar novas identidades.
    """
    __tablename__ = "humiat_usuario_produtos"
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("humiat_usuarios.id"), nullable=False)
    produto_id = Column(Integer, ForeignKey("humiat_produtos.id"), nullable=False)
    acesso_sistema = Column(Integer, nullable=False, default=0)
    acesso_adm = Column(Integer, nullable=False, default=0)
    # SolVoz possui dois perfis de cliente distintos além do ADM.
    # Nos demais produtos estes campos ficam em 0 e `acesso_sistema` continua
    # representando o acesso normal ao produto.
    acesso_solvoz_comprado = Column(Integer, nullable=False, default=0)
    acesso_solvoz_catalogo = Column(Integer, nullable=False, default=0)


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


class HumiatMigracaoLokaFest(Base):
    __tablename__ = "humiat_migracao_lokafest"
    id = Column(Integer, primary_key=True)
    lokafest_usuario_id = Column(Integer, unique=True, nullable=False, index=True)
    humiat_usuario_id = Column(Integer, ForeignKey("humiat_usuarios.id"), nullable=True)
    status = Column(String(30), nullable=False, default="PENDENTE")
    email = Column(String(180), nullable=True)
    email_enviado_em = Column(DateTime, nullable=True)
    ultimo_erro = Column(Text, nullable=True)
    atualizado_em = Column(DateTime, server_default=func.now(), onupdate=func.now())


class HumiatIntegracaoCache(Base):
    __tablename__ = "humiat_integracao_cache"
    id = Column(Integer, primary_key=True)
    chave = Column(String(220), unique=True, nullable=False, index=True)
    conteudo = Column(Text, nullable=False, default="{}")
    atualizado_em = Column(DateTime, nullable=False, default=datetime.utcnow)


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
    acesso_modo = Column(String(30), nullable=False, default="sistema")
    destino_slug = Column(String(120), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    expira_em = Column(DateTime, nullable=False)
    usado_em = Column(DateTime, nullable=True)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _b64url_encode(bruto: bytes) -> str:
    return base64.urlsafe_b64encode(bruto).decode("ascii").rstrip("=")


def _ticket_sso_v2_assinado(usuario, empresa, produto_codigo: str, modo: str, destino_slug: str | None = None) -> str:
    """Ticket curto auto-validável pelos produtos Humiat que compartilham SSO_SECRET.

    Usado inicialmente pelo Connect para eliminar a chamada HTTP de retorno ao
    Humiat durante cada abertura do produto.
    """
    if not SSO_SECRET:
        raise HTTPException(status_code=503, detail="HUMIAT_SSO_SECRET não configurado")
    agora = int(datetime.now(timezone.utc).timestamp())
    ttl = max(30, min(int(SSO_MINUTES * 60), 120))
    payload = {
        "ok": True,
        "v": 2,
        "iat": agora,
        "exp": agora + ttl,
        "jti": secrets.token_urlsafe(18),
        "usuario": {
            "id": int(usuario.id),
            "nome": usuario.nome or "",
            "email": usuario.email or "",
            "tipo": usuario.tipo or "",
            "documento": usuario.documento or "",
            "telefone": usuario.telefone or "",
        },
        "empresa": ({"id": int(empresa.id), "nome": empresa.nome or "", "slug": empresa.slug or ""} if empresa else None),
        "produto": (produto_codigo or "").strip().upper(),
        "modo": (modo or "sistema").strip().lower(),
        "destino_slug": (destino_slug or (empresa.slug if empresa else "") or "").strip().lower(),
        "acesso": "EMPRESA" if empresa else "INTERNO",
    }
    payload_b64 = _b64url_encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    base = f"v2.{payload_b64}"
    assinatura = hmac.new(SSO_SECRET.encode("utf-8"), base.encode("ascii"), hashlib.sha256).digest()
    return f"{base}.{_b64url_encode(assinatura)}"


def gerar_hash_senha_id(senha: str, salt: Optional[str] = None) -> str:
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), 180_000).hex()
    return f"{salt}${digest}"


def _verificar_hash_organiza_legado(senha: str, senha_hash: str) -> bool:
    """Valida somente o hash PBKDF2/120k usado pelos usuários antigos do Organiza."""
    try:
        bruto = str(senha_hash or "")
        if bruto.startswith("organiza120$"):
            bruto = bruto[len("organiza120$"):]
        salt, esperado = bruto.split("$", 1)
        digest = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), 120_000).hex()
        return hmac.compare_digest(digest, esperado)
    except Exception:
        return False


def verificar_senha_id(senha: str, senha_hash: str) -> bool:
    try:
        bruto = str(senha_hash or "")
        if bruto.startswith("organiza120$"):
            return _verificar_hash_organiza_legado(senha, bruto)
        salt, esperado = bruto.split("$", 1)
        atual = gerar_hash_senha_id(senha, salt).split("$", 1)[1]
        return hmac.compare_digest(atual, esperado)
    except Exception:
        return False


def _hash_organiza_legado(senha: str, salt: Optional[str] = None) -> str:
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), 120_000).hex()
    return f"{salt}${digest}"


def _sincronizar_senha_usuario_organiza(db: Session, usuario: "HumiatUsuario", senha: str) -> None:
    """Mantém a mesma senha no login legado do Organiza durante o piloto dos ADMs.

    A autenticação principal passa a ser o Humiat ID, mas o login direto antigo do
    Organiza continua funcionando enquanto a migração não é encerrada.
    """
    if not usuario or not senha:
        return
    nome = (usuario.organiza_usuario or "").strip()
    email = (usuario.email or "").strip().lower()
    if not nome and not email:
        return
    try:
        if nome:
            row = db.execute(text("SELECT id FROM usuarios WHERE nome=:nome LIMIT 1"), {"nome": nome}).first()
        else:
            row = db.execute(text("SELECT id FROM usuarios WHERE LOWER(COALESCE(email,''))=:email LIMIT 1"), {"email": email}).first()
        if row:
            db.execute(text("UPDATE usuarios SET senha_hash=:hash WHERE id=:id"), {"hash": _hash_organiza_legado(senha), "id": int(row[0])})
    except Exception as exc:
        print(f"[HUMIAT ID] Não foi possível sincronizar senha com o Organiza: {exc}")


def _sincronizar_admins_organiza_no_humiat(db: Session) -> int:
    """Cria a ponte inicial dos usuários internos do Organiza.

    Importa os administradores já existentes e também Junior, Debora e Luiz,
    mesmo que um deles ainda estivesse como usuário operacional. Para estes três
    o Organiza passa a reconhecer o perfil administrativo conforme a regra
    inicial solicitada para o Humiat ID.
    """
    criados = 0
    try:
        rows = db.execute(text("""
            SELECT id, nome, email, senha_hash, is_admin
            FROM usuarios
            WHERE ativo=1 AND TRIM(COALESCE(email,''))<>''
            ORDER BY id
        """)).mappings().all()
    except Exception:
        return 0
    for row in rows:
        email = str(row.get("email") or "").strip().lower()
        nome = str(row.get("nome") or "").strip()
        prioritario = _eh_equipe_interna_prioritaria(nome, email)
        if not bool(int(row.get("is_admin") or 0)) and not prioritario:
            continue
        if not email or not nome:
            continue
        if prioritario and not bool(int(row.get("is_admin") or 0)):
            db.execute(text("UPDATE usuarios SET is_admin=1 WHERE id=:id"), {"id": int(row["id"])})
        existente = db.query(HumiatUsuario).filter(func.lower(HumiatUsuario.email) == email).first()
        if existente:
            if not (existente.organiza_usuario or "").strip():
                existente.organiza_usuario = nome
            # Administrador do Organiza no piloto fica sem empresa vinculada no Humiat.
            db.query(HumiatUsuarioEmpresa).filter(HumiatUsuarioEmpresa.usuario_id == existente.id).delete(synchronize_session=False)
            existente.tipo = TIPO_ADMIN_HUMIAT
            existente.ativo = 1
            continue
        antigo = str(row.get("senha_hash") or "").strip()
        if not antigo:
            continue
        db.add(HumiatUsuario(
            nome=nome,
            email=email,
            senha_hash=f"organiza120${antigo}",
            tipo=TIPO_ADMIN_HUMIAT,
            ativo=1,
            organiza_usuario=nome,
        ))
        criados += 1
    return criados


def _nome_usuario_organiza_disponivel(db: Session, nome: str, email: str) -> str:
    base = (nome or (email.split("@", 1)[0] if email else "usuario") or "usuario").strip()[:70]
    base = " ".join(base.split()) or "usuario"
    candidato = base
    contador = 2
    while db.execute(text("SELECT 1 FROM usuarios WHERE LOWER(nome)=LOWER(:nome) LIMIT 1"), {"nome": candidato}).first():
        candidato = f"{base[:64]} {contador}"
        contador += 1
    return candidato[:80]


def _garantir_usuario_central_organiza(db: Session, nome: str, email: str, senha: str = "", *, admin: bool = False) -> tuple[str, bool]:
    """Garante a identidade também na tabela central `usuarios` do Organiza.

    Primeiro procura por e-mail. Se já existir, apenas devolve o usuário local e
    nunca troca a senha silenciosamente. Se não existir, cria o registro mínimo
    usando a mesma senha informada no Humiat ID.
    """
    email_n = (email or "").strip().lower()
    row = None
    if email_n:
        row = db.execute(text("SELECT id,nome,email,senha_hash,is_admin,ativo FROM usuarios WHERE LOWER(COALESCE(email,''))=:email ORDER BY id LIMIT 1"), {"email": email_n}).mappings().first()
    if row:
        if not int(row.get("ativo") or 0):
            db.execute(text("UPDATE usuarios SET ativo=1 WHERE id=:id"), {"id": int(row["id"])})
        if admin and not int(row.get("is_admin") or 0):
            # O papel Humiat/ADM de produto não transforma automaticamente o
            # usuário em administrador operacional do Organiza.
            pass
        return str(row.get("nome") or "").strip(), False
    if not senha or len(senha.strip()) < 8:
        raise ValueError("Para um usuário novo, informe uma senha inicial com pelo menos 8 caracteres.")
    nome_local = _nome_usuario_organiza_disponivel(db, nome, email_n)
    db.execute(text("""
        INSERT INTO usuarios (
            nome,telefone,email,cargo,senha_hash,is_admin,
            pode_criar_tarefa,pode_criar_projeto,pode_criar_usuario,pode_criar_etapa,
            ativo,departamentos,pode_cadastrar_cliente,pode_cadastrar_item,
            pode_acessar_compras,pode_acessar_financeiro,pode_cadastrar_banco,pode_acessar_externo
        ) VALUES (
            :nome,NULL,:email,:cargo,:senha_hash,0,
            0,0,0,0,1,NULL,0,0,0,0,0,0
        )
    """), {
        "nome": nome_local,
        "email": email_n or None,
        "cargo": "Humiat ID",
        "senha_hash": _hash_organiza_legado(senha.strip()),
    })
    return nome_local, True


def _connect_slug_por_global(db: Session, slug_global: str | None) -> str:
    """Resolve o alias legado do Connect a partir do slug global do SolVoz/Organiza.

    O slug global nunca muda. Apenas empresas antigas podem ter um alias local
    no Connect para preservar URLs já enviadas aos clientes.
    """
    slug_n = _slug(slug_global or "")
    if not slug_n:
        return ""
    try:
        linha = db.execute(
            text("SELECT connect_slug FROM solvoz_empresas WHERE LOWER(slug) = :slug LIMIT 1"),
            {"slug": slug_n},
        ).first()
        alias = _slug((linha[0] if linha else "") or "")
        return alias or slug_n
    except Exception:
        # Compatibilidade durante primeiro deploy/migração.
        return slug_n


def _produto_por_codigo(db: Session, codigo: str) -> HumiatProduto | None:
    return db.query(HumiatProduto).filter(HumiatProduto.codigo == (codigo or "").strip().upper()).first()


def _usuario_produto_permissoes(db: Session, usuario_id: int, codigo: str) -> dict:
    produto = _produto_por_codigo(db, codigo)
    padrao = {"sistema": False, "adm": False, "solvoz_comprado": False, "solvoz_catalogo": False}
    if not produto:
        return padrao
    item = db.query(HumiatUsuarioProduto).filter(
        HumiatUsuarioProduto.usuario_id == int(usuario_id),
        HumiatUsuarioProduto.produto_id == int(produto.id),
    ).first()
    if not item:
        return padrao
    return {
        "sistema": bool(item.acesso_sistema),
        "adm": bool(item.acesso_adm),
        "solvoz_comprado": bool(getattr(item, "acesso_solvoz_comprado", 0)),
        "solvoz_catalogo": bool(getattr(item, "acesso_solvoz_catalogo", 0)),
    }


def _usuario_produto_acesso(db: Session, usuario_id: int, codigo: str) -> tuple[bool, bool]:
    permissoes = _usuario_produto_permissoes(db, usuario_id, codigo)
    return permissoes["sistema"], permissoes["adm"]


def _salvar_usuario_produto_acesso(
    db: Session, usuario_id: int, produto: HumiatProduto, *, sistema: bool, adm: bool,
    solvoz_comprado: bool = False, solvoz_catalogo: bool = False,
) -> None:
    item = db.query(HumiatUsuarioProduto).filter(
        HumiatUsuarioProduto.usuario_id == int(usuario_id),
        HumiatUsuarioProduto.produto_id == int(produto.id),
    ).first()
    if not item:
        item = HumiatUsuarioProduto(usuario_id=int(usuario_id), produto_id=int(produto.id))
        db.add(item)
    item.acesso_adm = 1 if adm else 0
    if (produto.codigo or "").upper() == "SOLVOZ":
        item.acesso_solvoz_comprado = 1 if solvoz_comprado else 0
        item.acesso_solvoz_catalogo = 1 if solvoz_catalogo else 0
        # Compatibilidade com rotas antigas: qualquer perfil de cliente SolVoz
        # mantém `acesso_sistema` ligado.
        item.acesso_sistema = 1 if (solvoz_comprado or solvoz_catalogo or sistema) else 0
    else:
        item.acesso_sistema = 1 if sistema else 0
        item.acesso_solvoz_comprado = 0
        item.acesso_solvoz_catalogo = 0


def _garantir_acesso_admin_solvoz_piloto(db: Session, usuario: HumiatUsuario) -> None:
    """Preserva o acesso dos ADMs já existentes durante o piloto."""
    produto = _produto_por_codigo(db, "SOLVOZ")
    if not produto or not usuario:
        return
    item = db.query(HumiatUsuarioProduto).filter(
        HumiatUsuarioProduto.usuario_id == usuario.id,
        HumiatUsuarioProduto.produto_id == produto.id,
    ).first()
    if not item:
        db.add(HumiatUsuarioProduto(usuario_id=usuario.id, produto_id=produto.id, acesso_sistema=1, acesso_adm=1, acesso_solvoz_comprado=1, acesso_solvoz_catalogo=1))


def _garantir_acessos_iniciais_equipe(db: Session) -> int:
    """Dá a Junior, Debora e Luiz acesso inicial completo aos produtos.

    Apenas vínculos AUSENTES são criados. Se uma permissão já existir, inclusive
    desligada manualmente, ela não é sobrescrita em reinicializações futuras.
    """
    produtos = db.query(HumiatProduto).filter(HumiatProduto.ativo == 1).all()
    criados = 0
    for usuario in db.query(HumiatUsuario).filter(HumiatUsuario.ativo == 1).all():
        if not _usuario_equipe_interna_prioritaria(usuario):
            continue
        db.query(HumiatUsuarioEmpresa).filter(HumiatUsuarioEmpresa.usuario_id == usuario.id).delete(synchronize_session=False)
        usuario.tipo = TIPO_ADMIN_HUMIAT
        for produto in produtos:
            item = db.query(HumiatUsuarioProduto).filter(
                HumiatUsuarioProduto.usuario_id == usuario.id,
                HumiatUsuarioProduto.produto_id == produto.id,
            ).first()
            eh_solvoz = (produto.codigo or "").upper() == "SOLVOZ"
            if item:
                # Regra inicial explícita: Junior, Debora e Luiz começam com
                # acesso total aos produtos integrados. No SolVoz isso inclui
                # os três perfis próprios.
                item.acesso_sistema = 1
                item.acesso_adm = 1
                if eh_solvoz:
                    item.acesso_solvoz_comprado = 1
                    item.acesso_solvoz_catalogo = 1
                continue
            db.add(HumiatUsuarioProduto(
                usuario_id=usuario.id, produto_id=produto.id,
                acesso_sistema=1, acesso_adm=1,
                acesso_solvoz_comprado=1 if eh_solvoz else 0,
                acesso_solvoz_catalogo=1 if eh_solvoz else 0,
            ))
            criados += 1
    return criados


def _produto_adm_url(codigo: str) -> str:
    codigo = (codigo or "").strip().upper()
    if codigo == "ORGANIZA":
        return "/organiza"
    if codigo == "SOLVOZ":
        return "/painel/produto/SOLVOZ?modo=adm"
    if codigo == "CONNECT":
        return os.getenv("HUMIAT_CONNECT_ADM_URL", "").strip()
    if codigo == "LOKAFEST":
        return os.getenv("HUMIAT_LOKAFEST_ADM_URL", "").strip()
    return ""


def _ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded:
        return forwarded[:80]
    return (request.client.host if request.client else "")[:80]


def _auditar(db: Session, request: Request, acao: str, usuario_id: int | None = None, empresa_id: int | None = None, detalhe: str = ""):
    db.add(HumiatAuditoria(usuario_id=usuario_id, empresa_id=empresa_id, acao=acao, detalhe=detalhe[:3000], ip=_ip(request)))



def migrar_humiat_id_schema(engine) -> None:
    """Migração aditiva do Humiat ID, sem apagar nem recriar usuários existentes."""
    insp = inspect(engine)
    if "humiat_usuarios" not in insp.get_table_names():
        return
    existentes = {c["name"] for c in insp.get_columns("humiat_usuarios")}
    with engine.begin() as conn:
        if "documento" not in existentes:
            conn.execute(text("ALTER TABLE humiat_usuarios ADD COLUMN documento VARCHAR(30)"))
        if "telefone" not in existentes:
            conn.execute(text("ALTER TABLE humiat_usuarios ADD COLUMN telefone VARCHAR(40)"))
        # Usuários de empresa antigos continuam com o mesmo vínculo, apenas deixam
        # de carregar o rótulo/permissão de administrador.
        conn.execute(text("UPDATE humiat_usuarios SET tipo='CLIENTE_EMPRESA' WHERE tipo='ADMIN_EMPRESA'"))
    # Perfis específicos do SolVoz (ADM + Cliente Site + Cliente Catálogo).
    # Bancos existentes recebem as colunas sem recriar a tabela.
    insp = inspect(engine)
    if "humiat_usuario_produtos" in insp.get_table_names():
        cols_prod = {c["name"] for c in insp.get_columns("humiat_usuario_produtos")}
        with engine.begin() as conn:
            if "acesso_solvoz_comprado" not in cols_prod:
                conn.execute(text("ALTER TABLE humiat_usuario_produtos ADD COLUMN acesso_solvoz_comprado INTEGER NOT NULL DEFAULT 0"))
            if "acesso_solvoz_catalogo" not in cols_prod:
                conn.execute(text("ALTER TABLE humiat_usuario_produtos ADD COLUMN acesso_solvoz_catalogo INTEGER NOT NULL DEFAULT 0"))

    # APP 1.1.31 — o ticket SSO informa qual perfil/destino do produto foi solicitado.
    insp = inspect(engine)
    if "humiat_sso_tickets" in insp.get_table_names():
        cols_ticket = {c["name"] for c in insp.get_columns("humiat_sso_tickets")}
        with engine.begin() as conn:
            if "acesso_modo" not in cols_ticket:
                conn.execute(text("ALTER TABLE humiat_sso_tickets ADD COLUMN acesso_modo VARCHAR(30) NOT NULL DEFAULT 'sistema'"))
            if "destino_slug" not in cols_ticket:
                conn.execute(text("ALTER TABLE humiat_sso_tickets ADD COLUMN destino_slug VARCHAR(120)"))

    # A tabela humiat_usuario_produtos é criada por Base.metadata.create_all no startup.
    # Mantemos um índice único aditivo quando o banco suportar a operação.
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_humiat_usuario_produto ON humiat_usuario_produtos (usuario_id, produto_id)"))
    except Exception:
        pass


def _novo_token_reset(db: Session, usuario: HumiatUsuario, request: Request | None = None) -> str:
    token = secrets.token_urlsafe(40)
    db.add(HumiatSenhaReset(
        token_hash=_hash_token(token),
        usuario_id=usuario.id,
        expira_em=datetime.utcnow() + timedelta(minutes=RESET_MINUTES),
        ip=_ip(request) if request else "",
    ))
    if request:
        _auditar(db, request, "ACESSO_SOLVOZ_TOKEN_CRIADO", usuario_id=usuario.id)
    db.flush()
    return token


def _enviar_email_primeiro_acesso(destino: str, nome: str, empresa_nome: str, link: str) -> None:
    """E-mail de ativação do acesso SolVoz; não envia senha temporária."""
    if not RESEND_API_KEY:
        raise RuntimeError("HUMIAT_RESEND_API_KEY não configurada no servidor")
    if not EMAIL_FROM:
        raise RuntimeError("HUMIAT_EMAIL_FROM não configurado no servidor")
    nome_exibicao = (nome or "cliente").strip()
    empresa_exibicao = (empresa_nome or "sua empresa").strip()
    texto_msg = (
        f"Olá, {nome_exibicao}.\n\n"
        f"Seu acesso ao SolVoz da empresa {empresa_exibicao} foi criado.\n"
        f"Crie sua senha neste link em até {RESET_MINUTES} minutos:\n{link}\n\n"
        "Este acesso é exclusivo ao SolVoz e não libera acesso ao Organiza.\n"
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;color:#0b1220">
      <h2 style="margin-bottom:8px">Seu acesso ao SolVoz</h2>
      <p>Olá, {nome_exibicao}.</p>
      <p>O acesso da empresa <strong>{empresa_exibicao}</strong> foi criado.</p>
      <p>Defina sua senha pelo botão abaixo. O link é válido por <strong>{RESET_MINUTES} minutos</strong> e só pode ser usado uma vez.</p>
      <p style="margin:28px 0"><a href="{link}" style="background:#111827;color:#fff;text-decoration:none;padding:13px 20px;border-radius:9px;font-weight:700">Criar minha senha</a></p>
      <p style="font-size:13px;color:#475569">Este acesso é exclusivo ao SolVoz e não dá acesso ao Organiza.</p>
      <p style="font-size:13px;color:#475569">Se o botão não abrir, copie este endereço:<br>{link}</p>
    </div>
    """
    payload = json.dumps({
        "from": EMAIL_FROM,
        "to": [destino],
        "subject": f"SolVoz - acesso da {empresa_exibicao}",
        "text": texto_msg,
        "html": html,
    }).encode("utf-8")
    req = urllib.request.Request(
        RESEND_API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Humiat-ID-SolVoz/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status < 200 or resp.status >= 300:
                raise RuntimeError(f"Resend retornou HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        detalhe = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Falha Resend HTTP {exc.code}: {detalhe[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Falha de rede ao acessar Resend: {exc.reason}") from exc


def garantir_empresa_solvoz_humiat(db: Session, nome: str, slug: str, ativo: int = 1) -> HumiatEmpresa:
    slug_n = _slug(slug)
    empresa = db.query(HumiatEmpresa).filter(HumiatEmpresa.slug == slug_n).first()
    if not empresa:
        empresa = HumiatEmpresa(nome=(nome or slug_n).strip(), slug=slug_n, ativo=1 if ativo else 0)
        db.add(empresa)
        db.flush()
    else:
        if nome and nome.strip():
            empresa.nome = nome.strip()
        # Não reativa uma empresa já desativada por decisão administrativa.
    produto = _produto_solvoz(db)
    if produto:
        item = db.query(HumiatEmpresaProduto).filter(
            HumiatEmpresaProduto.empresa_id == empresa.id,
            HumiatEmpresaProduto.produto_id == produto.id,
        ).first()
        if item:
            item.ativo = 1
        else:
            db.add(HumiatEmpresaProduto(empresa_id=empresa.id, produto_id=produto.id, ativo=1))
    db.flush()
    return empresa


def provisionar_acesso_solvoz_cliente(
    db: Session,
    *,
    empresa_nome: str,
    empresa_slug: str,
    cliente_nome: str,
    cliente_email: str,
    cliente_documento: str = "",
    cliente_telefone: str = "",
    request: Request | None = None,
    enviar_email: bool = True,
) -> dict:
    """Cria/vincula um acesso de cliente apenas ao SolVoz e envia ativação."""
    email = (cliente_email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("O cliente precisa ter um e-mail válido no Organiza.")
    empresa = garantir_empresa_solvoz_humiat(db, empresa_nome, empresa_slug, ativo=1)
    usuario = db.query(HumiatUsuario).filter(HumiatUsuario.email == email).first()
    criado = False
    if not usuario:
        usuario = HumiatUsuario(
            nome=(cliente_nome or empresa_nome or email).strip()[:120],
            email=email,
            senha_hash=gerar_hash_senha_id(secrets.token_urlsafe(32)),
            tipo=TIPO_CLIENTE_EMPRESA,
            ativo=1,
            organiza_usuario=None,
            documento=(cliente_documento or "").strip()[:30] or None,
            telefone=(cliente_telefone or "").strip()[:40] or None,
        )
        db.add(usuario)
        db.flush()
        criado = True
    else:
        # Regra 8.7: uma conta existente SEM qualquer empresa vinculada pertence
        # à equipe interna. A automação nunca transforma Junior/Debora/Luiz em
        # cliente só porque o e-mail deles aparece no cadastro do Organiza.
        if not _usuario_tem_empresa_vinculada(db, usuario.id):
            usuario.ativo = 1
            db.commit()
            return {
                "ok": True,
                "usuario_id": usuario.id,
                "empresa_id": empresa.id,
                "email": email,
                "criado": False,
                "email_enviado": False,
                "email_erro": "",
                "link_ativacao": "",
                "acesso_interno": True,
            }
        usuario.nome = (cliente_nome or usuario.nome or empresa_nome).strip()[:120]
        usuario.tipo = TIPO_CLIENTE_EMPRESA
        usuario.ativo = 1
        usuario.organiza_usuario = None
        usuario.documento = (cliente_documento or usuario.documento or "").strip()[:30] or None
        usuario.telefone = (cliente_telefone or usuario.telefone or "").strip()[:40] or None

    # APP 1.1.96: cliente externo possui uma única empresa no Humiat ID.
    # Ao provisionar/atualizar pelo SolVoz, substitui qualquer vínculo legado
    # (por exemplo Karaokê RJ usado como fallback antes da empresa real existir).
    vinculos = db.query(HumiatUsuarioEmpresa).filter(
        HumiatUsuarioEmpresa.usuario_id == usuario.id
    ).all()
    manteve = False
    for vinculo_existente in vinculos:
        if int(vinculo_existente.empresa_id) == int(empresa.id) and not manteve:
            manteve = True
            continue
        db.delete(vinculo_existente)
    if not manteve:
        db.add(HumiatUsuarioEmpresa(usuario_id=usuario.id, empresa_id=empresa.id))

    token = _novo_token_reset(db, usuario, request=request)
    link = f"{PUBLIC_BASE_URL.rstrip('/')}/redefinir-senha?token={urllib.parse.quote(token)}"
    if request:
        _auditar(
            db, request, "PROVISIONAR_ACESSO_SOLVOZ", usuario_id=usuario.id,
            empresa_id=empresa.id,
            detalhe=f"email={email}; criado={int(criado)}; slug={empresa.slug}",
        )
    db.commit()

    email_enviado = False
    email_erro = ""
    if enviar_email:
        try:
            _enviar_email_primeiro_acesso(email, usuario.nome, empresa.nome, link)
            email_enviado = True
            if request:
                _auditar(db, request, "ACESSO_SOLVOZ_EMAIL_ENVIADO", usuario_id=usuario.id, empresa_id=empresa.id)
                db.commit()
        except Exception as exc:
            email_erro = str(exc)
            if request:
                _auditar(db, request, "ACESSO_SOLVOZ_EMAIL_ERRO", usuario_id=usuario.id, empresa_id=empresa.id, detalhe=email_erro)
                db.commit()
    return {
        "ok": True,
        "usuario_id": usuario.id,
        "empresa_id": empresa.id,
        "email": email,
        "criado": criado,
        "email_enviado": email_enviado,
        "email_erro": email_erro,
        "link_ativacao": link if not enviar_email else "",
    }


def _enviar_email_recuperacao(destino: str, nome: str, link: str) -> None:
    """Envia a recuperação pela API HTTPS do Resend (porta 443, compatível com Render Free)."""
    if not RESEND_API_KEY:
        raise RuntimeError("HUMIAT_RESEND_API_KEY não configurada no servidor")
    if not EMAIL_FROM:
        raise RuntimeError("HUMIAT_EMAIL_FROM não configurado no servidor")

    nome_exibicao = (nome or "usuário").strip()
    texto = (
        f"Olá, {nome_exibicao}.\n\n"
        "Recebemos uma solicitação para redefinir sua senha do Humiat ID.\n"
        f"Use este link em até {RESET_MINUTES} minutos:\n{link}\n\n"
        "Se você não pediu a alteração, ignore esta mensagem.\n"
    )
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;color:#0b1220">
      <h2 style="margin-bottom:8px">Humiat ID</h2>
      <p>Olá, {nome_exibicao}.</p>
      <p>Recebemos uma solicitação para redefinir sua senha do Humiat ID.</p>
      <p>O link abaixo é válido por <strong>{RESET_MINUTES} minutos</strong> e só pode ser utilizado uma vez.</p>
      <p style="margin:28px 0"><a href="{link}" style="background:#0ea5e9;color:white;text-decoration:none;padding:12px 18px;border-radius:8px;font-weight:700">Criar nova senha</a></p>
      <p style="font-size:13px;color:#475569">Se o botão não abrir, copie este endereço:<br>{link}</p>
      <p style="font-size:13px;color:#475569">Se você não solicitou a alteração, ignore este e-mail.</p>
    </div>
    """

    payload = json.dumps({
        "from": EMAIL_FROM,
        "to": [destino],
        "subject": "Humiat ID - Redefinição de senha",
        "text": texto,
        "html": html,
    }).encode("utf-8")
    req = urllib.request.Request(
        RESEND_API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Humiat-ID/1.0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status < 200 or resp.status >= 300:
                raise RuntimeError(f"Resend retornou HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        detalhe = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Falha Resend HTTP {exc.code}: {detalhe[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Falha de rede ao acessar Resend: {exc.reason}") from exc



def _enviar_resend_humiat(destino: str, assunto: str, texto: str, html_corpo: str, *, user_agent: str = "HUMIAT/1.0") -> None:
    """Transporte central de e-mail do HUMIAT pelo Resend.

    O Organiza é o único responsável pelo transporte dos e-mails de acesso do
    SolVoz. O SolVoz continua responsável apenas pela credencial e pelos tokens.
    """
    para = (destino or "").strip().lower()
    if not para or "@" not in para:
        raise RuntimeError("E-mail do destinatário inválido")
    if not RESEND_API_KEY:
        raise RuntimeError("HUMIAT_RESEND_API_KEY não configurada no servidor")
    if not EMAIL_FROM:
        raise RuntimeError("HUMIAT_EMAIL_FROM não configurado no servidor")

    payload = json.dumps({
        "from": EMAIL_FROM,
        "to": [para],
        "subject": str(assunto or "HUMIAT")[:200],
        "text": str(texto or ""),
        "html": str(html_corpo or ""),
    }).encode("utf-8")
    req = urllib.request.Request(
        RESEND_API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status < 200 or resp.status >= 300:
                raise RuntimeError(f"Resend retornou HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        detalhe = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Falha Resend HTTP {exc.code}: {detalhe[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Falha de rede ao acessar Resend: {exc.reason}") from exc




def usuario_humiat_interno(db: Session, usuario: HumiatUsuario) -> bool:
    """API interna estável para o Organiza consultar se a identidade é da equipe."""
    return _usuario_acesso_interno(db, usuario)


def usuario_humiat_equipe_prioritaria(usuario: HumiatUsuario) -> bool:
    """Junior, Débora e Luiz: perfis usados no piloto central do Humiat ID."""
    return _usuario_equipe_interna_prioritaria(usuario)


def permissoes_usuario_humiat(db: Session, usuario_id: int, codigo: str) -> dict:
    return _usuario_produto_permissoes(db, usuario_id, codigo)


def salvar_permissoes_usuario_humiat(
    db: Session, usuario_id: int, produto: HumiatProduto, *, sistema: bool = False, adm: bool = False,
    cliente_site: bool = False, cliente_catalogo: bool = False,
) -> None:
    _salvar_usuario_produto_acesso(
        db, usuario_id, produto, sistema=sistema, adm=adm,
        solvoz_comprado=cliente_site, solvoz_catalogo=cliente_catalogo,
    )


def enviar_link_acesso_humiat(
    db: Session, usuario: HumiatUsuario, *, request: Request | None = None, primeiro_acesso: bool = False,
) -> str:
    """Gera um único link Humiat ID para criar/refazer a senha de todos os sistemas.

    Links anteriores ainda não utilizados são invalidados. Assim o atendimento pode
    clicar em "Reenviar link" quantas vezes for necessário sem deixar vários tokens
    válidos para o mesmo cliente.
    """
    if not usuario or not (usuario.email or '').strip():
        raise ValueError('O usuário precisa ter um e-mail válido no Humiat ID.')
    agora = datetime.utcnow()
    db.query(HumiatSenhaReset).filter(
        HumiatSenhaReset.usuario_id == int(usuario.id),
        HumiatSenhaReset.usado_em.is_(None),
    ).update({HumiatSenhaReset.usado_em: agora}, synchronize_session=False)
    token = _novo_token_reset(db, usuario, request=request)
    link = f"{PUBLIC_BASE_URL.rstrip('/')}/redefinir-senha?token={urllib.parse.quote(token)}"
    nome = (usuario.nome or 'cliente').strip()
    titulo = 'Crie sua senha' if primeiro_acesso else 'Refaça seu acesso'
    texto = (
        f"Olá, {nome}.\n\n"
        f"{titulo} do Humiat ID pelo link abaixo.\n"
        "O mesmo Humiat ID será usado em todos os sistemas liberados para você.\n\n"
        f"Link válido por {RESET_MINUTES} minutos:\n{link}\n\n"
        "Se precisar de ajuda, responda ao atendimento que enviou este acesso.\n"
    )
    html_nome = html.escape(nome)
    html_link = html.escape(link, quote=True)
    html_corpo = f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;color:#0b1220">
      <h2 style="margin-bottom:8px">Humiat ID</h2>
      <p>Olá, {html_nome}.</p>
      <p>{titulo} pelo botão abaixo.</p>
      <p><strong>Um único login</strong> dá acesso a todos os sistemas que estiverem liberados no seu cadastro.</p>
      <p style="margin:28px 0"><a href="{html_link}" style="background:#111827;color:#fff;text-decoration:none;padding:13px 20px;border-radius:9px;font-weight:700">{titulo}</a></p>
      <p style="font-size:13px;color:#475569">O link é válido por {RESET_MINUTES} minutos e só pode ser usado uma vez.</p>
      <p style="font-size:13px;color:#475569">Se o botão não abrir, copie este endereço:<br>{html_link}</p>
    </div>
    """
    _enviar_resend_humiat(
        usuario.email,
        'Humiat ID - Seu acesso' if primeiro_acesso else 'Humiat ID - Refazer acesso',
        texto, html_corpo, user_agent='HUMIAT-Organiza/1.1.33',
    )
    if request:
        _auditar(
            db, request, 'HUMIAT_LINK_ACESSO_ENVIADO', usuario_id=usuario.id,
            detalhe='primeiro_acesso=1' if primeiro_acesso else 'reenvio=1',
        )
    db.commit()
    return link


def enviar_email_solvoz_senha_provisoria(
    destino: str,
    nome: str,
    empresa_nome: str,
    senha_provisoria: str,
    acesso_url: str,
    equipamentos: list[str] | None = None,
) -> None:
    """Entrega a senha provisória gerada pelo SolVoz usando o Resend do Organiza."""
    senha = str(senha_provisoria or "").strip()
    if not senha:
        raise RuntimeError("Senha provisória do SolVoz não recebida")
    nome_exibicao = (nome or "cliente").strip()
    empresa_exibicao = (empresa_nome or "sua empresa").strip()
    itens = [str(x).strip() for x in (equipamentos or []) if str(x).strip()]
    equipamentos_txt = ", ".join(itens) or "Equipamentos vinculados ao cadastro"
    url = (acesso_url or "").strip()
    texto = (
        f"Olá, {nome_exibicao}.\n\n"
        "Seu acesso administrativo ao SolVoz foi criado.\n\n"
        f"Empresa: {empresa_exibicao}\n"
        f"Equipamento(s): {equipamentos_txt}\n"
        f"Usuário: {(destino or '').strip().lower()}\n"
        f"Senha provisória: {senha}\n\n"
        "No primeiro acesso será obrigatório criar uma nova senha. "
        "A senha definitiva ficará somente no SolVoz.\n\n"
        f"Acessar SolVoz: {url}\n\n"
        "SolVoz • HUMIAT"
    )
    nome_h = html.escape(nome_exibicao)
    empresa_h = html.escape(empresa_exibicao)
    equipamentos_h = html.escape(equipamentos_txt)
    email_h = html.escape((destino or "").strip().lower())
    senha_h = html.escape(senha)
    url_h = html.escape(url, quote=True)
    html_corpo = f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;color:#0b1220">
      <h2 style="margin-bottom:8px">Seu acesso ao SolVoz</h2>
      <p>Olá, {nome_h}.</p>
      <p>Seu acesso administrativo ao SolVoz foi criado a partir do cadastro da HUMIAT.</p>
      <p><strong>Empresa:</strong> {empresa_h}<br>
         <strong>Equipamento(s):</strong> {equipamentos_h}<br>
         <strong>Usuário:</strong> {email_h}<br>
         <strong>Senha provisória:</strong> <code style="font-size:16px">{senha_h}</code></p>
      <p>No primeiro acesso será obrigatório criar uma nova senha. Depois disso, a senha fica somente no SolVoz.</p>
      <p style="margin:28px 0"><a href="{url_h}" style="background:#111827;color:#fff;text-decoration:none;padding:13px 20px;border-radius:9px;font-weight:700">Acessar SolVoz</a></p>
      <p style="font-size:13px;color:#475569">Este acesso é exclusivo do SolVoz e não libera acesso ao Organiza.</p>
      <p style="font-size:13px;color:#475569">Se o botão não abrir, copie este endereço:<br>{url_h}</p>
    </div>
    """
    _enviar_resend_humiat(
        destino,
        "Seu acesso ao SolVoz foi criado",
        texto,
        html_corpo,
        user_agent="HUMIAT-Organiza-SolVoz/1.1.23",
    )


def enviar_email_solvoz_recuperacao(
    destino: str,
    nome: str,
    empresa_nome: str,
    link: str,
    *,
    validade_minutos: int = 30,
) -> None:
    """Envia pelo Organiza o link de recuperação cuja validade é controlada pelo SolVoz."""
    nome_exibicao = (nome or "cliente").strip()
    empresa_exibicao = (empresa_nome or "sua empresa").strip()
    url = (link or "").strip()
    minutos = max(1, int(validade_minutos or 30))
    texto = (
        f"Olá, {nome_exibicao}.\n\n"
        f"Recebemos uma solicitação para redefinir sua senha do SolVoz ({empresa_exibicao}).\n"
        f"Use este link em até {minutos} minutos:\n{url}\n\n"
        "Se você não solicitou a alteração, ignore esta mensagem.\n\n"
        "SolVoz • HUMIAT"
    )
    nome_h = html.escape(nome_exibicao)
    empresa_h = html.escape(empresa_exibicao)
    url_h = html.escape(url, quote=True)
    html_corpo = f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;color:#0b1220">
      <h2 style="margin-bottom:8px">Redefinir senha do SolVoz</h2>
      <p>Olá, {nome_h}.</p>
      <p>Recebemos uma solicitação para redefinir sua senha do SolVoz da empresa <strong>{empresa_h}</strong>.</p>
      <p>O link abaixo é válido por <strong>{minutos} minutos</strong> e só pode ser utilizado uma vez.</p>
      <p style="margin:28px 0"><a href="{url_h}" style="background:#111827;color:#fff;text-decoration:none;padding:13px 20px;border-radius:9px;font-weight:700">Criar nova senha</a></p>
      <p style="font-size:13px;color:#475569">Se você não solicitou a alteração, ignore este e-mail.</p>
      <p style="font-size:13px;color:#475569">Se o botão não abrir, copie este endereço:<br>{url_h}</p>
    </div>
    """
    _enviar_resend_humiat(
        destino,
        "SolVoz - redefinição de senha",
        texto,
        html_corpo,
        user_agent="HUMIAT-Organiza-SolVoz/1.1.23",
    )


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


def _usuario_tem_empresa_vinculada(db: Session, usuario_id: int) -> bool:
    return db.query(HumiatUsuarioEmpresa.id).filter(HumiatUsuarioEmpresa.usuario_id == usuario_id).first() is not None


def _usuario_acesso_interno(db: Session, usuario: HumiatUsuario) -> bool:
    """Equipe interna é explícita; cliente LokaFest pode existir sem empresa."""
    if _usuario_equipe_interna_prioritaria(usuario):
        return True
    return (usuario.tipo or "").strip().upper() == TIPO_ADMIN_HUMIAT


def _limpar_vinculos_equipe_interna_legada(db: Session) -> int:
    """Remove vínculos indevidos da equipe interna criados por versões antigas.

    Junior, Debora e Luiz eram usuários operacionais do Organiza antes da Área
    da Empresa existir. Se algum deles ganhou vínculo por engano, volta a ficar
    sem empresa e, portanto, com o perfil completo conforme a regra 8.7.
    """
    nomes = _equipe_interna_usuarios_configurados()
    emails = _equipe_interna_emails_configurados()
    alterados = 0
    for usuario in db.query(HumiatUsuario).all():
        eh_legado = _norm_identidade(usuario.organiza_usuario) in nomes
        eh_email = _norm_identidade(usuario.email) in emails
        if not (eh_legado or eh_email):
            continue
        qtd = db.query(HumiatUsuarioEmpresa).filter(HumiatUsuarioEmpresa.usuario_id == usuario.id).delete(synchronize_session=False)
        if qtd:
            alterados += int(qtd)
        # Mantido apenas por compatibilidade; a autorização real usa o vínculo.
        usuario.tipo = TIPO_ADMIN_HUMIAT
        usuario.ativo = 1
    return alterados


def _reconciliar_acessos_clientes_humiat(db: Session) -> int:
    """Corrige clientes já vinculados para as regras atuais sem consultar sistemas externos."""
    try:
        rows = db.execute(text("""
            SELECT id,nome,email,documento,telefone,ddi,cep,cidade,municipio,estado,bairro,endereco,humiat_usuario_id
            FROM clientes
            WHERE humiat_usuario_id IS NOT NULL
            ORDER BY id
        """)).mappings().all()
    except Exception:
        return 0
    alterados = 0
    for row in rows:
        uid = int(row.get("humiat_usuario_id") or 0)
        if not uid:
            continue
        hu = db.query(HumiatUsuario).filter(HumiatUsuario.id == uid).first()
        if not hu:
            continue
        _aplicar_acessos_cliente_humiat(db, hu, dict(row), liberar_lokafest=True)
        alterados += 1
    return alterados


def seed_humiat_id():
    """Cria estrutura lógica inicial sem destruir dados existentes."""
    db = SessionLocal()
    try:
        db.query(HumiatUsuario).filter(HumiatUsuario.tipo == TIPO_ADMIN_EMPRESA).update({HumiatUsuario.tipo: TIPO_CLIENTE_EMPRESA}, synchronize_session=False)
        removidos = _limpar_vinculos_equipe_interna_legada(db)
        if removidos:
            print(f"[HUMIAT ID] APP 8.7: {removidos} vínculo(s) antigo(s) removido(s) da equipe interna.")
        admins_importados = _sincronizar_admins_organiza_no_humiat(db)
        if admins_importados:
            print(f"[HUMIAT ID] 1.1.29: {admins_importados} administrador(es) do Organiza vinculados ao Humiat ID.")
        produtos = [
            ("CONNECT", "Connect", "Contratos, agenda, operação, rotas e financeiro.", os.getenv("HUMIAT_CONNECT_URL", "https://conect.humiat.com.br"), os.getenv("HUMIAT_CONNECT_SSO_URL", "https://conect.humiat.com.br/_connect/sso/humiat"), "connect"),
            ("LOKAFEST", "LokaFest", "Indicações e oportunidades para festas.", os.getenv("HUMIAT_LOKAFEST_URL", "https://lokafest.com.br"), os.getenv("HUMIAT_LOKAFEST_SSO_URL", "https://lokafest.com.br/_lokafest/sso/humiat"), "lokafest"),
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
        db.flush()
        clientes_reconciliados = _reconciliar_acessos_clientes_humiat(db)
        if clientes_reconciliados:
            print(f"[HUMIAT ID] 1.2.03: {clientes_reconciliados} cliente(s) reconciliado(s) com Tarefas rápidas, LokaFest e vínculo SolVoz local.")
        acessos_iniciais = _garantir_acessos_iniciais_equipe(db)
        if acessos_iniciais:
            print(f"[HUMIAT ID] 1.1.31: {acessos_iniciais} acesso(s) iniciais de Junior/Debora/Luiz criados.")
        # SessionLocal usa autoflush=False. Persistimos os vínculos iniciais antes
        # de consultar o piloto legado para não tentar inserir SolVoz duas vezes.
        db.flush()
        # Piloto 1.1.29: somente os administradores que já eram ADM no Organiza
        # (e o bootstrap Humiat) recebem acesso inicial ao ADM SolVoz. Usuários
        # novos respeitam exatamente as caixas marcadas no cadastro.
        for usuario_interno in db.query(HumiatUsuario).filter(HumiatUsuario.ativo == 1).all():
            if not _usuario_acesso_interno(db, usuario_interno):
                continue
            legado_admin = False
            if (usuario_interno.organiza_usuario or "").strip():
                row_admin = db.execute(
                    text("SELECT is_admin FROM usuarios WHERE nome=:nome LIMIT 1"),
                    {"nome": usuario_interno.organiza_usuario.strip()},
                ).first()
                legado_admin = bool(row_admin and int(row_admin[0] or 0))
            if legado_admin or (usuario_interno.email or "").strip().lower() == admin_email:
                _garantir_acesso_admin_solvoz_piloto(db, usuario_interno)
        db.commit()
    finally:
        db.close()


def humiat_usuario_da_requisicao(request: Request, db: Session) -> HumiatUsuario | None:
    """Valida a sessão com uma única leitura e evita UPDATE em todo request."""
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        return None
    agora = datetime.utcnow()
    row = (
        db.query(HumiatSessao, HumiatUsuario)
        .join(HumiatUsuario, HumiatUsuario.id == HumiatSessao.usuario_id)
        .filter(
            HumiatSessao.token_hash == _hash_token(token),
            HumiatUsuario.ativo == 1,
        )
        .first()
    )
    if not row:
        return None
    sessao, usuario = row
    if sessao.expira_em < agora:
        return None
    # A navegação não grava atividade. A sessão já possui criado_em/expira_em;
    # evitar UPDATE de ultimo_acesso deixa cada abertura de tela somente leitura.
    return usuario


def exigir_humiat_login(request: Request, db: Session = Depends(get_db)) -> HumiatUsuario:
    usuario = humiat_usuario_da_requisicao(request, db)
    if not usuario:
        raise HTTPException(status_code=303, headers={"Location": "/entrar"})
    return usuario


def exigir_admin_humiat(usuario: HumiatUsuario = Depends(exigir_humiat_login), db: Session = Depends(get_db)) -> HumiatUsuario:
    # Nome legado da dependência. A partir da 8.7 não existe "cargo admin" como
    # critério: usuário sem empresa vinculada é equipe interna e recebe o painel completo.
    if not _usuario_acesso_interno(db, usuario):
        raise HTTPException(status_code=403, detail="Acesso exclusivo da equipe interna")
    return usuario


def empresas_do_usuario(db: Session, usuario: HumiatUsuario):
    if _usuario_acesso_interno(db, usuario):
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


def _produto_conheca_url(produto: HumiatProduto) -> str:
    codigo = (produto.codigo or "").strip().upper()
    configuradas = {
        "ORGANIZA": os.getenv("HUMIAT_ORGANIZA_PRODUTO_URL", PUBLIC_BASE_URL).strip(),
        "CONNECT": os.getenv("HUMIAT_CONNECT_PRODUTO_URL", produto.url_publica or CONNECT_BASE_URL).strip(),
        "SOLVOZ": os.getenv("HUMIAT_SOLVOZ_PRODUTO_URL", produto.url_publica or SOLVOZ_BASE_URL).strip(),
        "LOKAFEST": os.getenv("HUMIAT_LOKAFEST_PRODUTO_URL", produto.url_publica or "https://lokafest.com.br").strip(),
    }
    return configuradas.get(codigo) or (produto.url_publica or PUBLIC_BASE_URL)


def _painel_numero(valor) -> float:
    texto_v = str(valor or "").strip().replace("R$", "").replace(" ", "")
    if not texto_v:
        return 0.0
    if "," in texto_v:
        texto_v = texto_v.replace(".", "").replace(",", ".")
    elif texto_v.count(".") > 1:
        partes = texto_v.split(".")
        texto_v = "".join(partes[:-1]) + "." + partes[-1] if len(partes[-1]) <= 2 else "".join(partes)
    try:
        return max(float(texto_v), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _painel_moeda(valor: float) -> str:
    bruto = f"{max(float(valor or 0), 0.0):,.2f}"
    return "R$ " + bruto.replace(",", "X").replace(".", ",").replace("X", ".")


def _pendencia_financeira_cliente_humiat(db: Session, cliente_id: int) -> float:
    """Saldo do cliente no Organiza para aviso do portal Humiat.

    O card não expõe a composição. Considera vendas de equipamento, manutenções
    aprovadas e atualizações ainda não quitadas.
    """
    total_aberto = 0.0
    try:
        vendas = db.execute(text("""
            SELECT e.id, e.valor, e.pago, e.status, e.data_compra, e.previsao_entrega,
                   COALESCE(SUM(vp.valor), 0) AS pagamentos
            FROM equipamentos e
            LEFT JOIN venda_pagamentos vp ON vp.equipamento_id=e.id
            WHERE e.cliente_id=:cid
              AND (e.data_compra IS NOT NULL OR e.previsao_entrega IS NOT NULL
                   OR COALESCE(TRIM(e.valor),'')<>'' OR COALESCE(TRIM(e.pago),'')<>''
                   OR e.status IN ('Solicitar gabinete','Montagem','Pronto para entrega','Entregue'))
            GROUP BY e.id, e.valor, e.pago, e.status, e.data_compra, e.previsao_entrega
        """), {"cid": int(cliente_id)}).mappings().all()
        for row in vendas:
            valor = _painel_numero(row.get("valor"))
            recebido = max(_painel_numero(row.get("pago")), float(row.get("pagamentos") or 0))
            total_aberto += max(valor - recebido, 0.0)
    except Exception:
        pass

    try:
        manutencoes = db.execute(text("""
            SELECT id FROM assistencias
            WHERE cliente_id=:cid AND UPPER(COALESCE(status,'')) <> 'CANCELADA'
        """), {"cid": int(cliente_id)}).mappings().all()
        for m in manutencoes:
            orc = db.execute(text("""
                SELECT id, status, valor_manutencao, desconto, desconto_somente_com_opcionais
                FROM assistencia_orcamentos
                WHERE manutencao_id=:mid
                ORDER BY versao DESC, id DESC LIMIT 1
            """), {"mid": int(m["id"])}).mappings().first()
            if not orc:
                continue
            st = str(orc.get("status") or "")
            if not (st in {"Aprovado", "Aprovado parcialmente", "Aprovado manualmente"} or st.startswith("Aprovado:")):
                continue
            itens = db.execute(text("""
                SELECT quantidade, preco_venda, opcional, aprovado
                FROM assistencia_orcamento_itens WHERE orcamento_id=:oid
            """), {"oid": int(orc["id"])}).mappings().all()
            subtotal = max(float(orc.get("valor_manutencao") or 0), 0.0)
            opcionais = [i for i in itens if int(i.get("opcional") or 0)]
            for i in itens:
                if not int(i.get("opcional") or 0) or int(i.get("aprovado") or 0):
                    subtotal += max(float(i.get("preco_venda") or 0), 0.0) * max(int(i.get("quantidade") or 0), 0)
            todos_opcionais = all(int(i.get("aprovado") or 0) for i in opcionais)
            condicional = bool(int(orc.get("desconto_somente_com_opcionais") or 0))
            desconto = max(float(orc.get("desconto") or 0), 0.0) if (not condicional or todos_opcionais) else 0.0
            devido = max(subtotal - min(desconto, subtotal), 0.0)
            recebido = db.execute(text("""
                SELECT COALESCE(SUM(valor),0) FROM assistencia_pagamentos WHERE orcamento_id=:oid
            """), {"oid": int(orc["id"])}).scalar() or 0
            total_aberto += max(devido - float(recebido or 0), 0.0)
    except Exception:
        pass

    try:
        atualizacoes = db.execute(text("""
            SELECT valor_a_pagar_centavos, frete_centavos, valor_pago_centavos
            FROM atualizacao_compras
            WHERE cliente_id=:cid AND UPPER(COALESCE(status,'')) NOT IN ('CANCELADO','CANCELADA')
        """), {"cid": int(cliente_id)}).mappings().all()
        for row in atualizacoes:
            devido = (int(row.get("valor_a_pagar_centavos") or 0) + int(row.get("frete_centavos") or 0)) / 100.0
            recebido = int(row.get("valor_pago_centavos") or 0) / 100.0
            total_aberto += max(devido - recebido, 0.0)
    except Exception:
        pass
    return round(total_aberto, 2)


def _cache_integracao_get(db: Session, chave: str, minutos: int) -> dict | None:
    item = db.query(HumiatIntegracaoCache).filter(HumiatIntegracaoCache.chave == chave).first()
    if not item or not item.atualizado_em:
        return None
    if datetime.utcnow() - item.atualizado_em > timedelta(minutes=max(1, minutos)):
        return None
    try:
        return json.loads(item.conteudo or "{}")
    except Exception:
        return None


def _cache_integracao_put(db: Session, chave: str, dados: dict) -> dict:
    item = db.query(HumiatIntegracaoCache).filter(HumiatIntegracaoCache.chave == chave).first()
    if not item:
        item = HumiatIntegracaoCache(chave=chave)
        db.add(item)
    item.conteudo = json.dumps(dados or {}, ensure_ascii=False)
    item.atualizado_em = datetime.utcnow()
    db.commit()
    return dados


def _cache_integracao_apagar(db: Session, prefixo: str) -> None:
    db.query(HumiatIntegracaoCache).filter(HumiatIntegracaoCache.chave.like(prefixo + "%")).delete(synchronize_session=False)


def _cliente_rapido_humiat(db: Session, usuario_id: int) -> dict:
    """Resumo do Organiza mostrado no portal sem liberar o sistema completo."""
    try:
        row = db.execute(text("""
            SELECT id, token_ficha, telefone, email, pacote, falta_pacote
            FROM clientes
            WHERE humiat_usuario_id=:uid
            ORDER BY id
            LIMIT 1
        """), {"uid": int(usuario_id)}).mappings().first()
    except Exception:
        row = None
    if not row:
        return {"vinculado": False, "cadastro_url": "", "chamado_url": "", "equipamentos": 0, "pendencia": 0.0, "pendencia_fmt": ""}
    cliente_id = int(row.get("id") or 0)
    try:
        equipamentos = int(db.execute(text("""
            SELECT COUNT(*) FROM equipamentos
            WHERE cliente_id=:cid AND UPPER(COALESCE(status,'')) <> 'INATIVO'
        """), {"cid": cliente_id}).scalar() or 0)
    except Exception:
        equipamentos = 0
    pendencia = _pendencia_financeira_cliente_humiat(db, cliente_id)
    token = str(row.get("token_ficha") or "").strip()
    return {
        "vinculado": True,
        "cliente_id": cliente_id,
        "cadastro_url": "/humiat/organiza/cadastro",
        "chamado_url": "/humiat/organiza/chamado",
        "tem_token": bool(token),
        "equipamentos": equipamentos,
        "pacote": str(row.get("pacote") or "").strip(),
        "falta_pacote": int(row.get("falta_pacote") or 0) if row.get("falta_pacote") is not None else None,
        "pendencia": pendencia,
        "pendencia_fmt": _painel_moeda(pendencia) if pendencia > 0.009 else "",
    }


def _solvoz_resumo_humiat(db: Session, empresa: HumiatEmpresa | None, *, forcar: bool = False) -> dict:
    """Resumo do SolVoz com snapshot local para não consultar o produto a cada abertura do painel."""
    if not empresa or not SSO_SECRET:
        return {"ok": False, "catalogo_existe": False}
    chave = f"painel:solvoz:{int(empresa.id)}"
    if not forcar:
        cache = _cache_integracao_get(db, chave, PAINEL_CACHE_MINUTES)
        if cache is not None:
            return cache
    url = f"{SOLVOZ_BASE_URL}/_sv/api/humiat/painel?{urlencode({'slug': empresa.slug})}"
    req = urllib.request.Request(url, headers={
        "X-Humiat-SSO-Secret": SSO_SECRET,
        "Accept": "application/json",
        "User-Agent": "Humiat-ID-Painel/1.2.03",
    })
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
        catalogo = dados.get("catalogo") or {}
        dias = catalogo.get("dias_restantes")
        try:
            dias = int(dias) if dias is not None else None
        except (TypeError, ValueError):
            dias = None
        return _cache_integracao_put(db, chave, {
            "ok": bool(dados.get("ok")),
            "catalogo_existe": bool(dados.get("catalogo_existe")),
            "url_catalogo": str(catalogo.get("url") or ""),
            "status": str(catalogo.get("status") or ""),
            "valido_ate": str(catalogo.get("valido_ate_br") or catalogo.get("valido_ate") or ""),
            "dias_restantes": dias,
            "pacote_mais_recente": str((dados.get("pacote_mais_recente") or {}).get("label") or ""),
        })
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"ok": True, "catalogo_existe": False}
        return {"ok": False, "catalogo_existe": False}
    except Exception:
        return {"ok": False, "catalogo_existe": False}


def _connect_resumo_humiat(db: Session, empresa: HumiatEmpresa | None, *, forcar: bool = False) -> dict:
    """Consulta enxuta do Connect somente quando o usuário possui acesso."""
    if not empresa:
        return {"ok": False}
    slug_connect = _connect_slug_por_global(db, empresa.slug)
    chave = f"painel:connect:{int(empresa.id)}"
    if not forcar:
        cache = _cache_integracao_get(db, chave, PAINEL_CACHE_MINUTES)
        if cache is not None:
            return cache
    if not SSO_SECRET:
        return {"ok": False, "slug": slug_connect}
    url = f"{CONNECT_BASE_URL}/_connect/api/humiat/painel?{urlencode({'slug': slug_connect})}"
    req = urllib.request.Request(url, headers={
        "X-Humiat-SSO-Secret": SSO_SECRET,
        "Accept": "application/json",
        "User-Agent": "Humiat-ID-Painel/1.2.03",
    })
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
        gratis = dados.get("contratos_gratis") or {}
        return _cache_integracao_put(db, chave, {
            "ok": bool(dados.get("ok")),
            "slug": slug_connect,
            "gratis_limite": int(gratis.get("limite") or 4),
            "gratis_usados": int(gratis.get("usados") or 0),
            "gratis_restantes": int(gratis.get("restantes") or 0),
            "faturado": float(dados.get("faturado_contratos") or 0),
            "faturado_fmt": _painel_moeda(float(dados.get("faturado_contratos") or 0)),
        })
    except Exception:
        return {"ok": False, "slug": slug_connect, "gratis_limite": 4}



def _lokafest_resumo_humiat(db: Session, usuario: HumiatUsuario, *, forcar: bool = False) -> dict:
    """Consulta o LokaFest pela identidade pessoal do Humiat.

    LokaFest nao usa empresa/tenant. O cadastro local e localizado por CPF e,
    como contingencia, pelo telefone. A permissao Humiat continua decidindo
    apenas se o botao de abertura do sistema fica disponivel.
    """
    if not usuario or not SSO_SECRET:
        return {"ok": False, "usuario_existe": False}
    chave = f"painel:lokafest:{int(usuario.id)}"
    if not forcar:
        cache = _cache_integracao_get(db, chave, PAINEL_CACHE_MINUTES)
        if cache is not None:
            return cache

    payload = urlencode({
        "documento": (usuario.documento or "").strip(),
        "telefone": (usuario.telefone or "").strip(),
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{LOKAFEST_BASE_URL}/_lokafest/api/humiat/painel",
        data=payload,
        method="POST",
        headers={
            "X-Humiat-SSO-Secret": SSO_SECRET,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "Humiat-ID-Painel/1.2.03",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
        return _cache_integracao_put(db, chave, {
            "ok": bool(dados.get("ok")),
            "usuario_existe": bool(dados.get("usuario_existe")),
            "cadastro_ativo": bool(dados.get("cadastro_ativo")),
            "cadastro_aprovado": bool(dados.get("cadastro_aprovado")),
            "apto_indicacoes": bool(dados.get("apto_indicacoes")),
            "total_global": int(dados.get("total_global") or 0),
            "recebidas": int(dados.get("recebidas") or 0),
            "repassadas": int(dados.get("repassadas") or 0),
            "prioridades": int(dados.get("prioridades") or 0),
            "grupo_whatsapp": bool(dados.get("grupo_whatsapp")),
            "grupo_url": str(dados.get("grupo_url") or ""),
            "pacote": str(dados.get("pacote") or ""),
            "pacote_mais_recente": str(dados.get("pacote_mais_recente") or ""),
            "status_pacote": str(dados.get("status_pacote") or ""),
            "mensagem": str(dados.get("mensagem") or ""),
            "cadastro_url": str(dados.get("cadastro_url") or f"{LOKAFEST_BASE_URL}/cadastro"),
        })
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"ok": True, "usuario_existe": False, "cadastro_url": f"{LOKAFEST_BASE_URL}/cadastro"}
        return {"ok": False, "usuario_existe": False}
    except Exception:
        return {"ok": False, "usuario_existe": False}

def _so_digitos_humiat(valor: str | None) -> str:
    return "".join(ch for ch in str(valor or "") if ch.isdigit())


def _cliente_organiza_humiat(db: Session, *, email: str = "", documento: str = "", telefone: str = "") -> dict | None:
    email_n = (email or "").strip().lower()
    doc_n = _so_digitos_humiat(documento)
    tel_n = _so_digitos_humiat(telefone)
    try:
        rows = db.execute(text("""
            SELECT id,nome,email,documento,telefone,ddi,cep,cidade,municipio,estado,bairro,endereco,humiat_usuario_id
            FROM clientes ORDER BY id
        """)).mappings().all()
    except Exception:
        return None
    if email_n:
        for row in rows:
            if str(row.get("email") or "").strip().lower() == email_n:
                return dict(row)
    if doc_n:
        for row in rows:
            if _so_digitos_humiat(row.get("documento")) == doc_n:
                return dict(row)
    if tel_n:
        candidatos = {tel_n, tel_n[-11:] if len(tel_n) >= 11 else tel_n}
        for row in rows:
            bruto = _so_digitos_humiat(f"{row.get('ddi') or ''}{row.get('telefone') or ''}")
            local = _so_digitos_humiat(row.get("telefone"))
            if bruto in candidatos or local in candidatos or (len(local) >= 10 and local[-11:] in candidatos):
                return dict(row)
    return None


def _empresa_solvoz_do_cliente_humiat(db: Session, cliente_id: int) -> dict | None:
    try:
        row = db.execute(text("""
            SELECT se.id,se.nome,se.slug
            FROM equipamentos e
            JOIN solvoz_empresas se ON se.id=e.solvoz_empresa_id
            WHERE e.cliente_id=:cid AND se.ativo=1
              AND UPPER(COALESCE(e.status,'')) <> 'INATIVO'
            ORDER BY COALESCE(e.catalogo_online,0) DESC,e.id
            LIMIT 1
        """), {"cid": int(cliente_id)}).mappings().first()
        return dict(row) if row else None
    except Exception:
        return None


def _zona_lokafest_por_cliente(cliente: dict | None) -> str:
    if not cliente:
        return "Outros RJ"
    municipio = _norm_identidade(cliente.get("municipio") or cliente.get("cidade"))
    bairro = _norm_identidade(cliente.get("bairro"))
    baixada = {"belford roxo","duque de caxias","japeri","mage","mesquita","nilopolis","nova iguacu","queimados","sao joao de meriti","seropedica"}
    lagos = {"araruama","armacao dos buzios","buzios","arraial do cabo","cabo frio","iguaba grande","saquarema","sao pedro da aldeia"}
    serrana = {"petropolis","teresopolis","nova friburgo","cachoeiras de macacu","guapimirim"}
    costa = {"angra dos reis","mangaratiba","paraty","itatiaia"}
    marica = {"marica","itaborai","tangua","rio bonito"}
    niteroi = {"niteroi","sao goncalo"}
    if municipio in baixada: return "Baixada Fluminense"
    if municipio in lagos: return "Região dos Lagos"
    if municipio in serrana: return "Região Serrana"
    if municipio in costa: return "Costa Verde"
    if municipio in marica: return "Maricá / Itaboraí"
    if municipio in niteroi: return "Niterói / São Gonçalo"
    if municipio in {"rio de janeiro","rio"}:
        if any(x in bairro for x in ("barra", "recreio", "jacarepagua", "vargem grande", "vargem pequena")):
            return "Barra / Recreio / Jacarepaguá / Vargens"
        if "campo grande" in bairro:
            return "Campo Grande"
        if any(x in bairro for x in ("santa cruz", "guaratiba", "pedra de guaratiba", "barra de guaratiba")):
            return "Santa Cruz / Guaratiba"
        if any(x in bairro for x in ("centro", "santa teresa", "lapa", "gloria", "catete")):
            return "Centro"
        return "Zona Norte"
    return "Outros RJ"


def _lokafest_humiat_request(path: str, *, form: dict | None = None, method: str | None = None) -> dict:
    if not SSO_SECRET:
        raise RuntimeError("HUMIAT_SSO_SECRET não configurado")
    data = urllib.parse.urlencode(form or {}).encode("utf-8") if form is not None else None
    paths = [path]
    if path.startswith("/_lokafest/api/"):
        paths.append(path.replace("/_lokafest/api/", "/api/", 1))
    ultimo_erro = None
    for caminho in paths:
        url = f"{LOKAFEST_BASE_URL}{caminho}"
        req = urllib.request.Request(url, data=data, method=method or ("POST" if data is not None else "GET"), headers={
            "X-Humiat-SSO-Secret": SSO_SECRET,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Humiat-ID-LokaFest/1.2.08",
        })
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detalhe = exc.read().decode("utf-8", errors="replace")
            ultimo_erro = f"LokaFest HTTP {exc.code}: {detalhe[:400]}"
            if exc.code == 404 and caminho != paths[-1]:
                continue
            raise RuntimeError(ultimo_erro) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Falha de rede com LokaFest: {exc.reason}") from exc
    raise RuntimeError(ultimo_erro or "Falha ao consultar LokaFest")


def _garantir_usuario_lokafest_humiat(cliente: dict, nome: str) -> dict:
    return _lokafest_humiat_request("/_lokafest/api/humiat/garantir-usuario", form={
        "nome": nome or cliente.get("nome") or "Cliente Humiat",
        "documento": cliente.get("documento") or "",
        "telefone": cliente.get("telefone") or "",
        "zona": _zona_lokafest_por_cliente(cliente),
    })


def _enviar_email_migracao_humiat(destino: str, nome: str, link: str) -> None:
    nome_exibicao = (nome or "cliente").strip()
    texto_msg = (
        f"Olá, {nome_exibicao}.\n\n"
        "Seu acesso foi migrado para o Humiat ID. Por segurança, crie uma nova senha para continuar acessando seus sistemas.\n"
        f"Use este link em até {RESET_MINUTES} minutos:\n{link}\n"
    )
    html_msg = f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;color:#0b1220">
      <h2>Seu acesso agora é pelo Humiat ID</h2>
      <p>Olá, {html.escape(nome_exibicao)}.</p>
      <p>Seu acesso foi migrado para o <strong>Humiat ID</strong>. Por segurança, crie uma nova senha única para continuar acessando seus sistemas.</p>
      <p style="margin:28px 0"><a href="{html.escape(link)}" style="background:#0b5bd3;color:#fff;text-decoration:none;padding:13px 20px;border-radius:9px;font-weight:700">Criar minha nova senha</a></p>
      <p style="font-size:13px;color:#475569">O link é válido por {RESET_MINUTES} minutos e só pode ser usado uma vez.</p>
    </div>
    """
    _enviar_resend_humiat(destino, "Humiat ID - Crie sua nova senha", texto_msg, html_msg, user_agent="Humiat-ID-Migracao/1.2.03")


def _enviar_email_primeiro_acesso_humiat(destino: str, nome: str, link: str) -> None:
    """Envia o primeiro acesso de um cliente criado diretamente pelo Organiza/Humiat."""
    nome_exibicao = (nome or "cliente").strip()
    texto_msg = (
        f"Olá, {nome_exibicao}.\n\n"
        "Seu Humiat ID foi criado e seus acessos já estão preparados.\n"
        "Crie sua senha única para acessar Organiza, LokaFest e SolVoz.\n"
        f"Use este link em até {RESET_MINUTES} minutos:\n{link}\n"
    )
    html_msg = f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;color:#0b1220">
      <h2>Seu Humiat ID está pronto</h2>
      <p>Olá, {html.escape(nome_exibicao)}.</p>
      <p>Seu acesso foi criado com os dados já cadastrados no Organiza. Use uma única senha para acessar seus sistemas.</p>
      <p style="margin:28px 0"><a href="{html.escape(link)}" style="background:#0b5bd3;color:#fff;text-decoration:none;padding:13px 20px;border-radius:9px;font-weight:700">Criar minha senha</a></p>
      <p style="font-size:13px;color:#475569">O link é válido por {RESET_MINUTES} minutos e só pode ser usado uma vez.</p>
    </div>
    """
    _enviar_resend_humiat(destino, "Humiat ID - Seu acesso está pronto", texto_msg, html_msg, user_agent="Humiat-ID-Primeiro-Acesso/1.2.08")


def _aplicar_acessos_cliente_humiat(db: Session, usuario_h: HumiatUsuario, cliente: dict, *, liberar_lokafest: bool = True) -> None:
    """Aplica as rotinas padrão em lote para reduzir round-trips ao banco."""
    produtos = db.query(HumiatProduto).filter(HumiatProduto.codigo.in_(["ORGANIZA", "LOKAFEST", "SOLVOZ"])).all()
    por_codigo = {(p.codigo or "").upper(): p for p in produtos}
    existentes = db.query(HumiatUsuarioProduto).filter(
        HumiatUsuarioProduto.usuario_id == int(usuario_h.id),
        HumiatUsuarioProduto.produto_id.in_([int(p.id) for p in produtos]) if produtos else False,
    ).all() if produtos else []
    por_produto = {int(item.produto_id): item for item in existentes}

    def garantir(produto: HumiatProduto | None, *, sistema: bool, adm: bool = False, catalogo: bool = False) -> None:
        if not produto:
            return
        item = por_produto.get(int(produto.id))
        if not item:
            item = HumiatUsuarioProduto(usuario_id=int(usuario_h.id), produto_id=int(produto.id))
            db.add(item)
            por_produto[int(produto.id)] = item
        item.acesso_sistema = 1 if (sistema or catalogo) else 0
        item.acesso_adm = 1 if adm else 0
        if (produto.codigo or "").upper() == "SOLVOZ":
            item.acesso_solvoz_catalogo = 1 if catalogo else 0
            item.acesso_solvoz_comprado = int(getattr(item, "acesso_solvoz_comprado", 0) or 0)
        else:
            item.acesso_solvoz_catalogo = 0
            item.acesso_solvoz_comprado = 0

    garantir(por_codigo.get("ORGANIZA"), sistema=True)
    if liberar_lokafest:
        garantir(por_codigo.get("LOKAFEST"), sistema=True)

    empresa_sv = _empresa_solvoz_do_cliente_humiat(db, int(cliente.get("id") or 0))
    if empresa_sv:
        garantir(por_codigo.get("SOLVOZ"), sistema=True, catalogo=True)
        slug = str(empresa_sv.get("slug") or "").lower()
        empresa_h = db.query(HumiatEmpresa).filter(func.lower(HumiatEmpresa.slug) == slug).first()
        if empresa_h:
            vinculo = db.query(HumiatUsuarioEmpresa).filter(
                HumiatUsuarioEmpresa.usuario_id == int(usuario_h.id),
                HumiatUsuarioEmpresa.empresa_id == int(empresa_h.id),
            ).first()
            if not vinculo:
                db.add(HumiatUsuarioEmpresa(usuario_id=int(usuario_h.id), empresa_id=int(empresa_h.id)))


def aplicar_rotinas_cliente_humiat(db: Session, usuario_h: HumiatUsuario, cliente_id: int, *, garantir_lokafest: bool = True) -> None:
    """Aplica no automático as mesmas regras do cadastro manual de cliente."""
    cliente = None
    try:
        row = db.execute(text("""SELECT id,nome,email,documento,telefone,ddi,cep,cidade,municipio,estado,bairro,endereco,humiat_usuario_id FROM clientes WHERE id=:id LIMIT 1"""), {"id": int(cliente_id)}).mappings().first()
        cliente = dict(row) if row else None
    except Exception:
        cliente = None
    if not cliente:
        raise ValueError("Cliente não localizado no Organiza")
    _aplicar_acessos_cliente_humiat(db, usuario_h, cliente, liberar_lokafest=True)
    if garantir_lokafest:
        _garantir_usuario_lokafest_humiat(cliente, usuario_h.nome)
    _cache_integracao_apagar(db, f"painel:lokafest:{int(usuario_h.id)}")


def _pendencias_lokafest_humiat(db: Session) -> tuple[list[dict], str]:
    chave = "migracao:lokafest:usuarios"
    dados = _cache_integracao_get(db, chave, PENDENCIAS_CACHE_MINUTES)
    if dados is None:
        try:
            dados = _lokafest_humiat_request("/_lokafest/api/humiat/usuarios")
            _cache_integracao_put(db, chave, dados)
        except Exception as exc:
            return [], str(exc)
    migrados = {int(x.lokafest_usuario_id): x for x in db.query(HumiatMigracaoLokaFest).all()}
    pendencias = []
    for item in dados.get("usuarios") or []:
        try:
            lid = int(item.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if not lid or (lid in migrados and migrados[lid].status in {"APROVADO", "EMAIL_ENVIADO", "EMAIL_ERRO"}):
            continue
        cliente = _cliente_organiza_humiat(db, documento=item.get("cpf") or "", telefone=item.get("whatsapp") or "")
        email = str((cliente or {}).get("email") or "").strip().lower()
        humiat = None
        if cliente and cliente.get("humiat_usuario_id"):
            humiat = db.query(HumiatUsuario).filter(HumiatUsuario.id == int(cliente.get("humiat_usuario_id"))).first()
        if not humiat and email:
            humiat = db.query(HumiatUsuario).filter(func.lower(HumiatUsuario.email) == email).first()
        pendencias.append({
            **item,
            "cliente": cliente,
            "email": email,
            "organiza_encontrado": bool(cliente),
            "humiat_existente": bool(humiat),
            "pode_aprovar": bool(cliente and email and "@" in email),
            "solvoz": _empresa_solvoz_do_cliente_humiat(db, int((cliente or {}).get("id") or 0)) if cliente else None,
        })
    return pendencias, ""


def _novos_usuarios_lokafest_humiat(db: Session, pendencias_lokafest: list[dict], pendencias_erro: str = "") -> tuple[list[dict], str]:
    """Clientes do Organiza prontos para nascer no ecossistema Humiat/LokaFest.

    Regra: possui empresa SolVoz vinculada por equipamento, ainda não possui
    Humiat ID e não corresponde a uma pendência legada do LokaFest. A consulta
    é feita em lote para não repetir o N+1 das telas antigas.
    """
    if pendencias_erro:
        # Sem conseguir confirmar a fila legada, não arriscamos duplicar perfil.
        return [], "Não foi possível confirmar as pendências do LokaFest. Tente atualizar novamente."

    pendentes_cliente_ids: set[int] = set()
    for item in pendencias_lokafest or []:
        cliente = item.get("cliente") or {}
        try:
            if cliente.get("id"):
                pendentes_cliente_ids.add(int(cliente.get("id")))
        except (TypeError, ValueError):
            pass

    try:
        rows = db.execute(text("""
            SELECT
                c.id, c.nome, c.email, c.documento, c.telefone, c.ddi, c.empresa,
                c.cep, c.cidade, c.municipio, c.estado, c.bairro, c.endereco,
                se.id AS solvoz_empresa_id, se.nome AS solvoz_empresa_nome, se.slug AS solvoz_empresa_slug,
                COALESCE(e.catalogo_online,0) AS catalogo_online, e.id AS equipamento_id
            FROM clientes c
            JOIN equipamentos e ON e.cliente_id=c.id
            JOIN solvoz_empresas se ON se.id=e.solvoz_empresa_id AND se.ativo=1
            WHERE c.humiat_usuario_id IS NULL
              AND UPPER(COALESCE(e.status,'')) <> 'INATIVO'
            ORDER BY c.id, COALESCE(e.catalogo_online,0) DESC, e.id
        """)).mappings().all()
    except Exception as exc:
        return [], str(exc)

    novos: list[dict] = []
    vistos: set[int] = set()
    for row in rows:
        cid = int(row.get("id") or 0)
        if not cid or cid in vistos or cid in pendentes_cliente_ids:
            continue
        vistos.add(cid)
        item = dict(row)
        email = str(item.get("email") or "").strip().lower()
        documento = _so_digitos_humiat(item.get("documento"))
        telefone = _so_digitos_humiat(f"{item.get('ddi') or ''}{item.get('telefone') or ''}")
        faltas = []
        if not email or "@" not in email:
            faltas.append("e-mail")
        if len(documento) != 11:
            faltas.append("CPF")
        if len(telefone) < 10:
            faltas.append("WhatsApp")
        item.update({
            "email": email,
            "documento_limpo": documento,
            "telefone_completo": telefone,
            "pode_criar": not faltas,
            "faltas": faltas,
        })
        novos.append(item)
    return novos, ""


def _contexto_admin_humiat(request: Request, usuario: HumiatUsuario, db: Session, empresa_id: int | None = None) -> dict:
    """Contexto do hub Humiat.

    O hub não administra mais rotinas internas dos produtos. Ele mantém somente
    identidade/usuários e atalhos SSO para os ADMs.
    """
    empresas = db.query(HumiatEmpresa).order_by(HumiatEmpresa.ativo.desc(), HumiatEmpresa.nome).all()
    usuarios = db.query(HumiatUsuario).order_by(HumiatUsuario.nome).all()
    produtos = db.query(HumiatProduto).filter(HumiatProduto.ativo == 1).order_by(HumiatProduto.nome).all()
    acessos = db.query(HumiatUsuarioProduto).all()
    acessos_usuario = {
        (a.usuario_id, a.produto_id): {
            "sistema": bool(a.acesso_sistema),
            "adm": bool(a.acesso_adm),
            "solvoz_comprado": bool(getattr(a, "acesso_solvoz_comprado", 0)),
            "solvoz_catalogo": bool(getattr(a, "acesso_solvoz_catalogo", 0)),
        } for a in acessos
    }
    meus_acessos = {
        p.codigo: acessos_usuario.get((usuario.id, p.id), {
            "sistema": False, "adm": False, "solvoz_comprado": False, "solvoz_catalogo": False
        }) for p in produtos
    }
    adm_disponivel = {p.codigo: bool(_produto_adm_url(p.codigo) or p.url_sso) for p in produtos}
    vinculos = db.query(HumiatUsuarioEmpresa).all()
    empresa_por_usuario = {}
    for v in vinculos:
        empresa_por_usuario.setdefault(v.usuario_id, v.empresa_id)
    pendencias_lokafest, pendencias_lokafest_erro = _pendencias_lokafest_humiat(db)
    novos_usuarios_lokafest, novos_usuarios_lokafest_erro = _novos_usuarios_lokafest_humiat(
        db, pendencias_lokafest, pendencias_lokafest_erro
    )
    erros_email_lokafest = db.query(HumiatMigracaoLokaFest).filter(HumiatMigracaoLokaFest.status == "EMAIL_ERRO").order_by(HumiatMigracaoLokaFest.id.desc()).all()
    try:
        cliente_humiat_ids = {int(x[0]) for x in db.execute(text("SELECT DISTINCT humiat_usuario_id FROM clientes WHERE humiat_usuario_id IS NOT NULL")).all() if x[0]}
    except Exception:
        cliente_humiat_ids = set()
    return {
        "request": request,
        "usuario": usuario,
        "empresas": empresas,
        "usuarios": usuarios,
        "produtos": produtos,
        "acessos_usuario": acessos_usuario,
        "meus_acessos": meus_acessos,
        "adm_disponivel": adm_disponivel,
        "conheca_urls": {p.codigo: _produto_conheca_url(p) for p in produtos},
        "vinculos": vinculos,
        "empresa_por_usuario": empresa_por_usuario,
        "pendencias_lokafest": pendencias_lokafest,
        "pendencias_lokafest_erro": pendencias_lokafest_erro,
        "novos_usuarios_lokafest": novos_usuarios_lokafest,
        "novos_usuarios_lokafest_erro": novos_usuarios_lokafest_erro,
        "erros_email_lokafest": erros_email_lokafest,
        "cliente_humiat_ids": cliente_humiat_ids,
        "admin_humiat": True,
    }


@router.get("/entrar", response_class=HTMLResponse)
def login_humiat(request: Request, erro: str = "", next: str = "", db: Session = Depends(get_db)):
    destino = next if next.startswith("/") and not next.startswith("//") else "/painel"
    if humiat_usuario_da_requisicao(request, db):
        return RedirectResponse(destino, status_code=303)
    return templates.TemplateResponse("humiat/login.html", {"request": request, "erro": erro, "next": destino})


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
    if _usuario_acesso_interno(db, usuario):
        _sincronizar_senha_usuario_organiza(db, usuario, senha)
    item.usado_em = datetime.utcnow()
    # Encerra sessões antigas após troca de senha.
    db.query(HumiatSessao).filter(HumiatSessao.usuario_id == usuario.id).delete(synchronize_session=False)
    _auditar(db, request, "SENHA_RESET_CONCLUIDO", usuario_id=usuario.id)
    db.commit()
    return RedirectResponse("/entrar?erro=Senha redefinida. Entre com a nova senha.", status_code=303)


def _tentar_migrar_senha_do_organiza(db: Session, usuario: HumiatUsuario, senha: str) -> bool:
    """Fallback de migração para identidades Humiat já existentes.

    Algumas contas (ex.: equipe interna criada antes do Humiat ID) já existiam em
    ``humiat_usuarios`` com uma senha própria, enquanto o usuário real continuava
    usando a senha do Organiza. Se a senha Humiat falhar, valida a senha atual na
    tabela central ``usuarios`` pelo mesmo e-mail e, se estiver correta, promove
    essa senha para o Humiat ID. Isso evita exigir redefinição manual na migração.
    """
    if not usuario or not senha or not (usuario.email or "").strip():
        return False
    try:
        row = db.execute(text("""
            SELECT id, senha_hash
            FROM usuarios
            WHERE ativo=1 AND LOWER(COALESCE(email,''))=:email
            ORDER BY id
            LIMIT 1
        """), {"email": (usuario.email or "").strip().lower()}).mappings().first()
    except Exception:
        return False
    if not row or not _verificar_hash_organiza_legado(senha, str(row.get("senha_hash") or "")):
        return False
    usuario.senha_hash = gerar_hash_senha_id(senha)
    if not (usuario.organiza_usuario or "").strip():
        nome_row = db.execute(text("SELECT nome FROM usuarios WHERE id=:id"), {"id": int(row["id"])}).first()
        if nome_row and nome_row[0]:
            usuario.organiza_usuario = str(nome_row[0]).strip()
    return True


@router.post("/entrar")
def entrar_humiat(request: Request, email: str = Form(...), senha: str = Form(...), next: str = Form(""), db: Session = Depends(get_db)):
    login = email.strip().lower()
    usuario = db.query(HumiatUsuario).filter(HumiatUsuario.email == login, HumiatUsuario.ativo == 1).first()
    senha_ok = bool(usuario and verificar_senha_id(senha, usuario.senha_hash))
    if usuario and not senha_ok:
        senha_ok = _tentar_migrar_senha_do_organiza(db, usuario, senha)
    if not usuario or not senha_ok:
        _auditar(db, request, "LOGIN_FALHOU", detalhe=login)
        db.commit()
        destino_erro = "/entrar?" + urllib.parse.urlencode({"erro": "E-mail ou senha inválidos", "next": next or "/painel"})
        return RedirectResponse(destino_erro, status_code=303)

    # Primeiro login de um ADM importado do Organiza: converte o hash legado
    # para o padrão atual do Humiat sem pedir troca de senha ao usuário.
    if str(usuario.senha_hash or "").startswith("organiza120$"):
        usuario.senha_hash = gerar_hash_senha_id(senha)
        _sincronizar_senha_usuario_organiza(db, usuario, senha)

    token = secrets.token_urlsafe(40)
    db.add(HumiatSessao(token_hash=_hash_token(token), usuario_id=usuario.id, expira_em=datetime.utcnow() + timedelta(days=SESSION_DAYS), ultimo_acesso=datetime.utcnow(), ip=_ip(request), user_agent=request.headers.get("user-agent", "")[:300]))
    _auditar(db, request, "LOGIN_OK", usuario_id=usuario.id)
    db.commit()
    destino = next if next.startswith("/") and not next.startswith("//") else "/painel"
    resposta = RedirectResponse(destino, status_code=303)
    resposta.set_cookie(COOKIE_NAME, token, httponly=True, secure=PUBLIC_BASE_URL.startswith("https://"), samesite="lax", max_age=SESSION_DAYS * 86400, path="/")
    return resposta


@router.get("/sair")
def sair_humiat(request: Request, db: Session = Depends(get_db)):
    """Logout global: encerra Humiat e percorre os produtos integrados."""
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        sessao = db.query(HumiatSessao).filter(HumiatSessao.token_hash == _hash_token(token)).first()
        if sessao:
            db.delete(sessao)
            db.commit()

    retorno = f"{PUBLIC_BASE_URL.rstrip('/')}/entrar"
    solvoz_logout = SOLVOZ_LOGOUT_URL
    if solvoz_logout:
        sep = "&" if "?" in solvoz_logout else "?"
        solvoz_logout = f"{solvoz_logout}{sep}{urlencode({'retorno': retorno})}"
    destino = solvoz_logout or retorno
    if CONNECT_LOGOUT_URL:
        sep = "&" if "?" in CONNECT_LOGOUT_URL else "?"
        destino = f"{CONNECT_LOGOUT_URL}{sep}{urlencode({'retorno': destino})}"

    resposta = RedirectResponse(destino, status_code=303)
    resposta.delete_cookie(COOKIE_NAME, path="/")
    # Compatibilidade: elimina também a sessão local antiga do Organiza no mesmo domínio.
    resposta.delete_cookie("humiat_sessao", path="/")
    return resposta


@router.get("/painel", response_class=HTMLResponse)
def painel_humiat(request: Request, empresa_id: int | None = None, usuario: HumiatUsuario = Depends(exigir_humiat_login), db: Session = Depends(get_db)):
    # O Administrador Humiat trabalha em uma única tela. A rota /admin-humiat
    # continua existindo somente por compatibilidade e redireciona para cá.
    if _usuario_acesso_interno(db, usuario):
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

    # APP 1.1.33: para clientes, a fonte dos acessos deixa de ser uma regra fixa
    # do produto e passa a ser exatamente o que foi marcado no cadastro do
    # Cliente dentro do Organiza. A empresa apenas dá o contexto/tenant.
    produtos_todos = db.query(HumiatProduto).filter(HumiatProduto.ativo == 1).order_by(HumiatProduto.nome).all()
    meus_acessos = {p.codigo: _usuario_produto_permissoes(db, usuario.id, p.codigo) for p in produtos_todos}
    # Todos os produtos aparecem no portal. A permissão decide se o botão de
    # acesso fica ativo; sem permissão o cliente pode apenas conhecer o produto.
    produtos = produtos_todos
    connect_liberado = bool(meus_acessos.get("CONNECT", {}).get("sistema", False))
    connect_resumo = _connect_resumo_humiat(db, empresa) if connect_liberado else {"ok": False, "gratis_limite": 4}
    organiza_rapido = _cliente_rapido_humiat(db, usuario.id)
    solvoz_resumo = _solvoz_resumo_humiat(db, empresa)
    lokafest_resumo = _lokafest_resumo_humiat(db, usuario)
    return templates.TemplateResponse(
        "humiat/painel.html",
        {
            "request": request, "usuario": usuario, "empresas": empresas, "empresa": empresa,
            "produtos": produtos, "meus_acessos": meus_acessos, "admin_humiat": False,
            "conheca_urls": {p.codigo: _produto_conheca_url(p) for p in produtos},
            "organiza_rapido": organiza_rapido,
            "connect_resumo": connect_resumo,
            "solvoz_resumo": solvoz_resumo,
            "lokafest_resumo": lokafest_resumo,
        },
    )


@router.get("/painel/produto/{codigo}")
def abrir_produto(
    codigo: str, request: Request, empresa_id: int | None = None, modo: str = "adm", destino: str = "",
    usuario: HumiatUsuario = Depends(exigir_humiat_login), db: Session = Depends(get_db),
):
    codigo = codigo.strip().upper()
    modo = (modo or "adm").strip().lower()
    # Alias legado preservado para links antigos. Na interface o nome oficial é Cliente Site.
    if modo == "cliente_comprado":
        modo = "cliente_site"
    modos_validos = {"adm", "sistema", "cliente_site", "cliente_catalogo"}
    if modo not in modos_validos:
        raise HTTPException(status_code=400, detail="Modo de acesso inválido")
    if codigo != "SOLVOZ" and modo in {"cliente_site", "cliente_catalogo"}:
        raise HTTPException(status_code=400, detail="Perfil disponível somente no SolVoz")
    acesso_interno = _usuario_acesso_interno(db, usuario)
    produto = db.query(HumiatProduto).filter(HumiatProduto.codigo == codigo, HumiatProduto.ativo == 1).first()
    if not produto:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    perm = _usuario_produto_permissoes(db, usuario.id, codigo)
    if modo == "adm":
        permitido, rotulo = perm["adm"], "ADM"
    elif modo == "cliente_site":
        permitido, rotulo = perm["solvoz_comprado"], "Cliente Site"
    elif modo == "cliente_catalogo":
        permitido, rotulo = perm["solvoz_catalogo"], "Cliente Catálogo"
    else:
        permitido, rotulo = perm["sistema"], "Usuário"
    if not permitido:
        raise HTTPException(status_code=403, detail=f"Seu Humiat ID não possui acesso ao perfil {rotulo} de {produto.nome}.")
    if modo == "adm" and not acesso_interno:
        raise HTTPException(status_code=403, detail="Acesso administrativo exclusivo da equipe autorizada.")
    if codigo == "ORGANIZA" and not acesso_interno:
        raise HTTPException(status_code=403, detail="No Organiza, o cliente usa somente as tarefas rápidas do Humiat ID.")

    empresas = empresas_do_usuario(db, usuario)
    empresa = next((e for e in empresas if e.id == empresa_id), None) if empresa_id else (empresas[0] if len(empresas) == 1 else None)
    if not acesso_interno and codigo != "LOKAFEST":
        if not empresa:
            raise HTTPException(status_code=400, detail="Selecione a empresa")
        permitidos = {p.codigo for p in produtos_da_empresa(db, empresa.id)}
        if codigo not in permitidos:
            raise HTTPException(status_code=403, detail="Produto não habilitado para esta empresa")

    _auditar(db, request, "ABRIR_PRODUTO", usuario_id=usuario.id, empresa_id=empresa.id if empresa else None, detalhe=f"{codigo}:{modo}")
    db.commit()

    if codigo == "ORGANIZA":
        # No hub administrativo, "Abrir sistema" abre o Organiza e "Abrir ADM"
        # fica reservado ao diagnóstico/performance. Clientes continuam usando
        # somente as tarefas rápidas exibidas no Humiat ID.
        if acesso_interno and modo == "sistema":
            return RedirectResponse("/organiza", status_code=303)
        if acesso_interno and modo == "adm":
            return RedirectResponse("/organiza/diagnostico-performance", status_code=303)
        raise HTTPException(status_code=403, detail="Use as tarefas rápidas do Organiza no Humiat ID.")

    # Cliente Site e Cliente Catálogo também usam SSO. Para o piloto da equipe
    # interna, ambos entram no contexto da Karaokê RJ sem novo login.
    empresa_sso = empresa
    if codigo == "SOLVOZ" and modo == "cliente_site":
        karaoke = db.query(HumiatEmpresa).filter(
            HumiatEmpresa.slug == "karaokerj", HumiatEmpresa.ativo == 1
        ).first()
        if karaoke and (acesso_interno or any(int(e.id) == int(karaoke.id) for e in empresas)):
            empresa_sso = karaoke
    elif codigo == "SOLVOZ" and acesso_interno and modo == "cliente_catalogo":
        # Piloto interno abre o Catálogo da Karaokê RJ. Clientes reais usam a
        # empresa selecionada no próprio cadastro/vínculo.
        empresa_sso = db.query(HumiatEmpresa).filter(
            HumiatEmpresa.slug == "karaokerj", HumiatEmpresa.ativo == 1
        ).first()

    if acesso_interno and modo == "sistema" and not produto.url_sso:
        # Produtos ainda sem SSO mantêm a experiência pública. Quando o produto
        # possui SSO (Connect/SolVoz), o ticket central identifica o usuário.
        if produto.url_publica:
            return RedirectResponse(produto.url_publica, status_code=303)
        raise HTTPException(status_code=503, detail="Produto sem URL pública configurada")

    if acesso_interno and modo == "adm" and not produto.url_sso:
        adm_url = _produto_adm_url(codigo)
        if adm_url:
            return RedirectResponse(adm_url, status_code=303)
        raise HTTPException(status_code=503, detail=f"ADM {produto.nome} ainda não integrado ao hub Humiat")

    if produto.url_sso:
        if codigo == "LOKAFEST":
            ticket_empresa_id = None
        else:
            ticket_empresa_id = (empresa_sso.id if empresa_sso else None) if (codigo == "SOLVOZ" and modo in {"cliente_site", "cliente_catalogo"}) else (None if acesso_interno else (empresa.id if empresa else None))
        destino_slug = None
        if codigo == "SOLVOZ" and modo in {"cliente_site", "cliente_catalogo"}:
            destino_slug = (empresa_sso.slug if empresa_sso else "karaokerj")
        elif codigo == "CONNECT" and empresa:
            # O Humiat ID identifica a empresa pelo slug global do Organiza, mas
            # o Connect pode manter um alias legado para não quebrar URLs antigas.
            destino_slug = _connect_slug_por_global(db, empresa.slug)
        if codigo == "CONNECT":
            # Connect 1.0.85+ valida este ticket localmente por HMAC; não há
            # round-trip HTTP para o Humiat ao abrir o sistema.
            token = _ticket_sso_v2_assinado(
                usuario,
                None if acesso_interno else empresa,
                codigo,
                modo,
                destino_slug=destino_slug,
            )
        else:
            token = secrets.token_urlsafe(40)
            db.add(HumiatSSOTicket(
                token_hash=_hash_token(token), usuario_id=usuario.id, empresa_id=ticket_empresa_id,
                produto_codigo=codigo, acesso_modo=modo,
                destino_slug=destino_slug,
                expira_em=datetime.utcnow() + timedelta(minutes=SSO_MINUTES)
            ))
            db.commit()
        sep = "&" if "?" in produto.url_sso else "?"
        params_sso = {"humiat_ticket": token}
        if codigo == "CONNECT" and destino.startswith("/") and not destino.startswith("//"):
            params_sso["destino"] = destino
        return RedirectResponse(f"{produto.url_sso}{sep}{urlencode(params_sso)}", status_code=303)

    if produto.url_publica:
        return RedirectResponse(produto.url_publica, status_code=303)
    raise HTTPException(status_code=503, detail="Produto sem URL configurada")


@router.get("/acessar/solvoz/{empresa_slug}")
def acessar_solvoz_empresa(empresa_slug: str, request: Request, db: Session = Depends(get_db)):
    """Entrada curta usada pelo cadeado da empresa no SolVoz."""
    slug_n = _slug(empresa_slug)
    usuario = humiat_usuario_da_requisicao(request, db)
    if not usuario:
        destino = f"/acessar/solvoz/{urllib.parse.quote(slug_n)}"
        return RedirectResponse("/entrar?" + urllib.parse.urlencode({"next": destino}), status_code=303)
    empresa = db.query(HumiatEmpresa).filter(HumiatEmpresa.slug == slug_n, HumiatEmpresa.ativo == 1).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    acesso_interno = _usuario_acesso_interno(db, usuario)
    if acesso_interno:
        _sistema, _adm = _usuario_produto_acesso(db, usuario.id, "SOLVOZ")
        if not _adm:
            raise HTTPException(status_code=403, detail="Seu Humiat ID não possui acesso ao ADM SolVoz.")
    if not acesso_interno:
        permitido = db.query(HumiatUsuarioEmpresa).filter(
            HumiatUsuarioEmpresa.usuario_id == usuario.id,
            HumiatUsuarioEmpresa.empresa_id == empresa.id,
        ).first()
        if not permitido:
            raise HTTPException(status_code=403, detail="Empresa não autorizada para este usuário")
        perm = _usuario_produto_permissoes(db, usuario.id, "SOLVOZ")
        if not perm.get("solvoz_catalogo"):
            raise HTTPException(status_code=403, detail="Seu Humiat ID não possui acesso ao Cliente Catálogo.")
    if not _empresa_tem_produto(db, empresa.id, "SOLVOZ"):
        raise HTTPException(status_code=403, detail="SolVoz não habilitado para esta empresa")
    produto = db.query(HumiatProduto).filter(HumiatProduto.codigo == "SOLVOZ", HumiatProduto.ativo == 1).first()
    if not produto or not produto.url_sso:
        raise HTTPException(status_code=503, detail="SolVoz sem SSO configurado")
    token = secrets.token_urlsafe(40)
    db.add(HumiatSSOTicket(
        token_hash=_hash_token(token), usuario_id=usuario.id, empresa_id=None if acesso_interno else empresa.id,
        produto_codigo="SOLVOZ", acesso_modo="adm" if acesso_interno else "cliente_catalogo",
        destino_slug=slug_n, expira_em=datetime.utcnow() + timedelta(minutes=SSO_MINUTES),
    ))
    _auditar(db, request, "ABRIR_SOLVOZ_EMPRESA", usuario_id=usuario.id, empresa_id=empresa.id)
    db.commit()
    sep = "&" if "?" in produto.url_sso else "?"
    return RedirectResponse(f"{produto.url_sso}{sep}{urlencode({'humiat_ticket': token})}", status_code=303)


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
    return {
        "ok": True,
        "usuario": {"id": usuario.id, "nome": usuario.nome, "email": usuario.email, "tipo": usuario.tipo, "documento": usuario.documento or "", "telefone": usuario.telefone or ""},
        "empresa": ({"id": empresa.id, "nome": empresa.nome, "slug": empresa.slug} if empresa else None),
        "produto": item.produto_codigo,
        "modo": (getattr(item, "acesso_modo", None) or "sistema"),
        "destino_slug": (getattr(item, "destino_slug", None) or (empresa.slug if empresa else "")),
        "acesso": "EMPRESA" if empresa else "INTERNO",
    }


@router.post("/api/humiat/integracoes/usuario/validar")
def validar_usuario_integracao(
    email: str = Form(...),
    produto: str = Form("CONNECT"),
    x_humiat_sso_secret: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Valida uma identidade central para produtos Humiat.

    Usado pelo ADM do Connect para vincular usuários locais existentes sem
    duplicá-los. A consulta é privada e exige o mesmo segredo do SSO.
    """
    if not SSO_SECRET or not hmac.compare_digest(x_humiat_sso_secret, SSO_SECRET):
        raise HTTPException(status_code=401, detail="Integração Humiat não autorizada")
    email_n = (email or "").strip().lower()
    codigo = (produto or "CONNECT").strip().upper()
    usuario = db.query(HumiatUsuario).filter(
        func.lower(HumiatUsuario.email) == email_n, HumiatUsuario.ativo == 1
    ).first()
    if not usuario:
        raise HTTPException(status_code=404, detail="Humiat ID não encontrado")
    permissoes = _usuario_produto_permissoes(db, int(usuario.id), codigo)
    if not any(bool(v) for v in permissoes.values()):
        raise HTTPException(status_code=403, detail=f"Humiat ID sem acesso liberado ao {codigo}")
    return {
        "ok": True,
        "usuario": {
            "id": int(usuario.id), "nome": usuario.nome, "email": usuario.email,
            "telefone": usuario.telefone or "", "tipo": usuario.tipo,
        },
        "produto": codigo,
        "permissoes": permissoes,
    }


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


@router.get("/admin-humiat/diagnostico-organiza")
def diagnostico_organiza(
    usuario: HumiatUsuario = Depends(exigir_admin_humiat),
):
    """Atalho protegido do Humiat ID para o diagnóstico de performance do Organiza."""
    return RedirectResponse("/organiza/diagnostico-performance", status_code=303)


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

    # O clone no SolVoz já pode ter criado esta empresa automaticamente no Organiza/Humiat.
    # Reutilize-a para não duplicar o cadastro central.
    nova = db.query(HumiatEmpresa).filter(HumiatEmpresa.slug == slug_n).first()
    if not nova:
        nova = HumiatEmpresa(nome=nome_n, slug=slug_n, ativo=0)
        db.add(nova)
        db.flush()
    else:
        nova.nome = nome_n
    for item in acessos:
        existente_item = db.query(HumiatEmpresaProduto).filter(
            HumiatEmpresaProduto.empresa_id == nova.id,
            HumiatEmpresaProduto.produto_id == item.produto_id,
        ).first()
        if existente_item:
            existente_item.ativo = item.ativo
        else:
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


@router.post("/admin-humiat/migracao-lokafest/{lokafest_usuario_id}/aprovar")
def aprovar_migracao_lokafest(
    lokafest_usuario_id: int,
    request: Request,
    usuario: HumiatUsuario = Depends(exigir_admin_humiat),
    db: Session = Depends(get_db),
):
    try:
        remoto = _lokafest_humiat_request(f"/_lokafest/api/humiat/usuarios?usuario_id={int(lokafest_usuario_id)}")
        itens = remoto.get("usuarios") or []
        if not itens:
            raise ValueError("Usuário não encontrado no LokaFest")
        item = itens[0]
        cliente = _cliente_organiza_humiat(db, documento=item.get("cpf") or "", telefone=item.get("whatsapp") or "")
        if not cliente:
            raise ValueError("Cliente não localizado no Organiza por CPF ou WhatsApp")
        email = str(cliente.get("email") or "").strip().lower()
        if not email or "@" not in email:
            raise ValueError("O cliente foi localizado no Organiza, mas precisa ter um e-mail válido antes da aprovação")

        alvo = None
        if cliente.get("humiat_usuario_id"):
            alvo = db.query(HumiatUsuario).filter(HumiatUsuario.id == int(cliente.get("humiat_usuario_id"))).first()
        if not alvo:
            alvo = db.query(HumiatUsuario).filter(func.lower(HumiatUsuario.email) == email).first()
        if not alvo:
            alvo = HumiatUsuario(
                nome=str(cliente.get("nome") or item.get("nome") or email).strip()[:120],
                email=email,
                senha_hash=gerar_hash_senha_id(secrets.token_urlsafe(32)),
                tipo=TIPO_CLIENTE_EMPRESA,
                ativo=1,
                documento=str(cliente.get("documento") or item.get("cpf") or "").strip()[:30] or None,
                telefone=str(cliente.get("telefone") or item.get("whatsapp") or "").strip()[:40] or None,
            )
            db.add(alvo)
            db.flush()
        else:
            alvo.ativo = 1
            alvo.documento = str(cliente.get("documento") or alvo.documento or item.get("cpf") or "").strip()[:30] or None
            alvo.telefone = str(cliente.get("telefone") or alvo.telefone or item.get("whatsapp") or "").strip()[:40] or None

        db.execute(text("UPDATE clientes SET humiat_usuario_id=:uid WHERE id=:cid"), {"uid": int(alvo.id), "cid": int(cliente.get("id"))})
        aplicar_rotinas_cliente_humiat(db, alvo, int(cliente.get("id")), garantir_lokafest=True)

        mig = db.query(HumiatMigracaoLokaFest).filter(HumiatMigracaoLokaFest.lokafest_usuario_id == int(lokafest_usuario_id)).first()
        if not mig:
            mig = HumiatMigracaoLokaFest(lokafest_usuario_id=int(lokafest_usuario_id))
            db.add(mig)
        mig.humiat_usuario_id = alvo.id
        mig.status = "APROVADO"
        mig.email = email
        mig.ultimo_erro = None

        token = _novo_token_reset(db, alvo, request=request)
        link = f"{PUBLIC_BASE_URL.rstrip('/')}/redefinir-senha?token={urllib.parse.quote(token)}"
        _auditar(db, request, "MIGRACAO_LOKAFEST_APROVADA", usuario.id, detalhe=f"lokafest_id={lokafest_usuario_id}; humiat_id={alvo.id}")
        db.commit()
        try:
            _enviar_email_migracao_humiat(email, alvo.nome, link)
            mig.status = "EMAIL_ENVIADO"
            mig.email_enviado_em = datetime.utcnow()
            mig.ultimo_erro = None
            _auditar(db, request, "MIGRACAO_LOKAFEST_EMAIL_ENVIADO", usuario.id, detalhe=f"lokafest_id={lokafest_usuario_id}; humiat_id={alvo.id}")
            db.commit()
            return RedirectResponse("/painel?ok=LokaFest aprovado, Humiat vinculado e e-mail enviado", status_code=303)
        except Exception as exc:
            mig.status = "EMAIL_ERRO"
            mig.ultimo_erro = str(exc)[:1000]
            db.commit()
            return RedirectResponse(f"/painel?erro={urllib.parse.quote('Usuário aprovado, mas o e-mail falhou: ' + str(exc))}", status_code=303)
    except Exception as exc:
        db.rollback()
        return RedirectResponse(f"/painel?erro={urllib.parse.quote(str(exc))}", status_code=303)


@router.post("/admin-humiat/migracao-lokafest/{lokafest_usuario_id}/reenviar-email")
def reenviar_email_migracao_lokafest(
    lokafest_usuario_id: int,
    request: Request,
    usuario: HumiatUsuario = Depends(exigir_admin_humiat),
    db: Session = Depends(get_db),
):
    mig = db.query(HumiatMigracaoLokaFest).filter(HumiatMigracaoLokaFest.lokafest_usuario_id == int(lokafest_usuario_id)).first()
    alvo = db.query(HumiatUsuario).filter(HumiatUsuario.id == mig.humiat_usuario_id).first() if mig and mig.humiat_usuario_id else None
    if not mig or not alvo:
        return RedirectResponse("/painel?erro=Migração não encontrada", status_code=303)
    token = _novo_token_reset(db, alvo, request=request)
    link = f"{PUBLIC_BASE_URL.rstrip('/')}/redefinir-senha?token={urllib.parse.quote(token)}"
    db.commit()
    try:
        _enviar_email_primeiro_acesso_humiat(alvo.email, alvo.nome, link)
        mig.status = "EMAIL_ENVIADO"
        mig.email_enviado_em = datetime.utcnow()
        mig.ultimo_erro = None
        db.commit()
        return RedirectResponse("/painel?ok=E-mail reenviado com sucesso", status_code=303)
    except Exception as exc:
        mig.status = "EMAIL_ERRO"
        mig.ultimo_erro = str(exc)[:1000]
        db.commit()
        return RedirectResponse(f"/painel?erro={urllib.parse.quote(str(exc))}", status_code=303)


@router.post("/admin-humiat/usuarios-novos/{cliente_id}/criar")
def criar_usuario_novo_lokafest_humiat(
    cliente_id: int,
    request: Request,
    usuario: HumiatUsuario = Depends(exigir_admin_humiat),
    db: Session = Depends(get_db),
):
    """Cria o ecossistema completo para cliente novo vindo do Organiza.

    O cadastro do Organiza é a fonte. Não depende de aprovação posterior no
    LokaFest: o perfil remoto nasce ativo/aprovado e recebe equipamentos/pacote
    pela integração já existente.
    """
    try:
        row = db.execute(text("""
            SELECT id,nome,email,documento,telefone,ddi,cep,cidade,municipio,estado,bairro,endereco,humiat_usuario_id
            FROM clientes WHERE id=:id LIMIT 1
        """), {"id": int(cliente_id)}).mappings().first()
        cliente = dict(row) if row else None
    except Exception as exc:
        return RedirectResponse(f"/painel?erro={urllib.parse.quote(str(exc)[:180])}", status_code=303)
    if not cliente:
        return RedirectResponse("/painel?erro=Cliente não encontrado no Organiza", status_code=303)
    if cliente.get("humiat_usuario_id"):
        return RedirectResponse("/painel?ok=Cliente já possui Humiat ID", status_code=303)

    pendencias, erro_pendencias = _pendencias_lokafest_humiat(db)
    if erro_pendencias:
        return RedirectResponse(f"/painel?erro={urllib.parse.quote('Não foi possível confirmar a fila antiga do LokaFest: ' + erro_pendencias[:140])}", status_code=303)
    if any(int(((p.get("cliente") or {}).get("id") or 0)) == int(cliente_id) for p in pendencias):
        return RedirectResponse("/painel?erro=Este cliente já está em Pendências Humiat ID do LokaFest", status_code=303)

    empresa_sv = _empresa_solvoz_do_cliente_humiat(db, int(cliente_id))
    if not empresa_sv:
        return RedirectResponse("/painel?erro=Cliente sem empresa SolVoz vinculada", status_code=303)

    email = str(cliente.get("email") or "").strip().lower()
    documento = _so_digitos_humiat(cliente.get("documento"))
    telefone = _so_digitos_humiat(f"{cliente.get('ddi') or ''}{cliente.get('telefone') or ''}")
    faltas = []
    if not email or "@" not in email:
        faltas.append("e-mail")
    if len(documento) != 11:
        faltas.append("CPF")
    if len(telefone) < 10:
        faltas.append("WhatsApp")
    if faltas:
        return RedirectResponse(f"/painel?erro={urllib.parse.quote('Complete no Organiza: ' + ', '.join(faltas))}", status_code=303)

    # 1) Cria/atualiza primeiro o perfil do LokaFest. A operação é idempotente.
    # Assim, se a etapa central falhar, uma nova tentativa apenas reaproveita o perfil remoto.
    try:
        perfil_lokafest = _garantir_usuario_lokafest_humiat(cliente, str(cliente.get("nome") or "Cliente Humiat"))
    except Exception as exc:
        return RedirectResponse(f"/painel?erro={urllib.parse.quote('LokaFest: ' + str(exc)[:180])}", status_code=303)

    # 2) Garante a empresa central e cria/reaproveita o Humiat ID.
    empresa_h = garantir_empresa_solvoz_humiat(
        db,
        str(empresa_sv.get("nome") or empresa_sv.get("slug") or "Empresa"),
        str(empresa_sv.get("slug") or ""),
        ativo=1,
    )
    alvo = db.query(HumiatUsuario).filter(func.lower(HumiatUsuario.email) == email).first()
    criado = alvo is None
    if alvo is None:
        alvo = HumiatUsuario(
            nome=str(cliente.get("nome") or "Cliente Humiat").strip(),
            email=email,
            senha_hash=gerar_hash_senha_id(secrets.token_urlsafe(32)),
            tipo=TIPO_CLIENTE_EMPRESA,
            ativo=1,
            organiza_usuario=None,
            documento=str(cliente.get("documento") or "").strip()[:30] or None,
            telefone=str(cliente.get("telefone") or "").strip()[:40] or None,
        )
        db.add(alvo)
        db.flush()
    else:
        alvo.ativo = 1
        # Reaproveita a identidade central sem rebaixar um eventual perfil interno.
        if not (alvo.documento or "").strip():
            alvo.documento = str(cliente.get("documento") or "").strip()[:30] or None
        if not (alvo.telefone or "").strip():
            alvo.telefone = str(cliente.get("telefone") or "").strip()[:40] or None

    vinculo = db.query(HumiatUsuarioEmpresa).filter(
        HumiatUsuarioEmpresa.usuario_id == int(alvo.id),
        HumiatUsuarioEmpresa.empresa_id == int(empresa_h.id),
    ).first()
    if not vinculo:
        db.add(HumiatUsuarioEmpresa(usuario_id=int(alvo.id), empresa_id=int(empresa_h.id)))

    db.execute(text("UPDATE clientes SET humiat_usuario_id=:uid WHERE id=:cid"), {"uid": int(alvo.id), "cid": int(cliente_id)})
    _aplicar_acessos_cliente_humiat(db, alvo, cliente, liberar_lokafest=True)

    # O registro também impede que o usuário criado pelo Humiat volte como
    # 'pendência legada' na próxima leitura da base do LokaFest.
    lokafest_usuario_id = int(perfil_lokafest.get("usuario_id") or 0)
    mig = None
    if lokafest_usuario_id:
        mig = db.query(HumiatMigracaoLokaFest).filter(HumiatMigracaoLokaFest.lokafest_usuario_id == lokafest_usuario_id).first()
        if not mig:
            mig = HumiatMigracaoLokaFest(lokafest_usuario_id=lokafest_usuario_id)
            db.add(mig)
        mig.humiat_usuario_id = int(alvo.id)
        mig.email = email
        mig.status = "APROVADO"
        mig.ultimo_erro = None

    token_primeiro = _novo_token_reset(db, alvo, request=request)
    link_primeiro = f"{PUBLIC_BASE_URL.rstrip('/')}/redefinir-senha?token={urllib.parse.quote(token_primeiro)}"
    _auditar(
        db, request, "CRIAR_USUARIO_NOVO_LOKAFEST", usuario.id, int(empresa_h.id),
        f"cliente={cliente_id}; humiat={alvo.id}; lokafest={lokafest_usuario_id}; criado={int(criado)}",
    )
    _cache_integracao_apagar(db, "migracao:lokafest:usuarios")
    db.commit()

    aviso = "novo_usuario_criado"
    try:
        _enviar_email_primeiro_acesso_humiat(email, alvo.nome, link_primeiro)
        aviso = "novo_usuario_criado_email_enviado"
        if mig:
            mig.status = "EMAIL_ENVIADO"
            mig.email_enviado_em = datetime.utcnow()
            mig.ultimo_erro = None
            db.commit()
    except Exception as exc:
        aviso = "novo_usuario_criado_email_erro"
        if mig:
            mig.status = "EMAIL_ERRO"
            mig.ultimo_erro = str(exc)[:1000]
            db.commit()
    return RedirectResponse(f"/painel?ok={urllib.parse.quote(aviso)}", status_code=303)


@router.post("/admin-humiat/usuarios")
async def criar_usuario_humiat(
    request: Request,
    nome: str = Form(...),
    email: str = Form(...),
    senha: str = Form(""),
    tipo: str = Form(TIPO_CLIENTE_EMPRESA),
    empresa_id: str = Form(""),
    usuario: HumiatUsuario = Depends(exigir_admin_humiat),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    if db.query(HumiatUsuario).filter(func.lower(HumiatUsuario.email) == email).first():
        return RedirectResponse(f"/painel?empresa_id={empresa_id if empresa_id.strip().isdigit() else ''}&erro=E-mail já cadastrado", status_code=303)
    empresa_vinculada = int(empresa_id) if empresa_id.strip().isdigit() else None
    tipo = TIPO_CLIENTE_EMPRESA if empresa_vinculada else TIPO_ADMIN_HUMIAT

    cliente_organiza = _cliente_organiza_humiat(db, email=email)
    if cliente_organiza:
        tipo = TIPO_CLIENTE_EMPRESA
    # A identidade central usa o cadastro do cliente no Organiza. Usuários locais
    # antigos do Organiza continuam sendo reaproveitados quando existirem.
    try:
        row_legado = db.execute(text("SELECT nome,senha_hash FROM usuarios WHERE LOWER(COALESCE(email,''))=:email ORDER BY id LIMIT 1"), {"email": email}).mappings().first()
        if row_legado:
            organiza_usuario = str(row_legado.get("nome") or "").strip()
            hash_humiat = f"organiza120${str(row_legado.get('senha_hash') or '').strip()}"
        else:
            if cliente_organiza:
                organiza_usuario = None
                hash_humiat = gerar_hash_senha_id(senha.strip() or secrets.token_urlsafe(32))
            else:
                organiza_usuario, _ = _garantir_usuario_central_organiza(db, nome, email, senha, admin=False)
                hash_humiat = gerar_hash_senha_id(senha.strip())
    except ValueError as exc:
        return RedirectResponse(f"/painel?erro={urllib.parse.quote(str(exc))}", status_code=303)

    novo = HumiatUsuario(
        nome=nome.strip(), email=email, senha_hash=hash_humiat,
        tipo=tipo, ativo=1, organiza_usuario=organiza_usuario,
        documento=str((cliente_organiza or {}).get("documento") or "").strip()[:30] or None,
        telefone=str((cliente_organiza or {}).get("telefone") or "").strip()[:40] or None,
    )
    db.add(novo); db.flush()
    if empresa_vinculada:
        db.add(HumiatUsuarioEmpresa(usuario_id=novo.id, empresa_id=empresa_vinculada))

    form = await request.form()
    resumo_acessos = []
    for produto in db.query(HumiatProduto).filter(HumiatProduto.ativo == 1).order_by(HumiatProduto.nome).all():
        sistema = str(form.get(f"produto_{produto.id}_sistema") or "0") == "1"
        adm = str(form.get(f"produto_{produto.id}_adm") or "0") == "1"
        comprado = str(form.get(f"produto_{produto.id}_solvoz_comprado") or "0") == "1"
        catalogo = str(form.get(f"produto_{produto.id}_solvoz_catalogo") or "0") == "1"
        _salvar_usuario_produto_acesso(db, novo.id, produto, sistema=sistema, adm=adm, solvoz_comprado=comprado, solvoz_catalogo=catalogo)
        if sistema or adm or comprado or catalogo:
            resumo_acessos.append(f"{produto.codigo}:S{int(sistema)}A{int(adm)}C{int(comprado)}K{int(catalogo)}")
    lokafest_solicitado = False
    produto_lf = _produto_por_codigo(db, "LOKAFEST")
    if produto_lf:
        lokafest_solicitado = str(form.get(f"produto_{produto_lf.id}_sistema") or "0") == "1"
    if cliente_organiza:
        db.execute(text("UPDATE clientes SET humiat_usuario_id=:uid WHERE id=:cid"), {"uid": int(novo.id), "cid": int(cliente_organiza.get("id"))})
        _aplicar_acessos_cliente_humiat(db, novo, cliente_organiza, liberar_lokafest=True)
        lokafest_solicitado = True
    _auditar(db, request, "CRIAR_USUARIO", usuario.id, empresa_vinculada, f"{email}; " + ",".join(resumo_acessos))
    token_primeiro = _novo_token_reset(db, novo, request=request) if cliente_organiza else ""
    link_primeiro = f"{PUBLIC_BASE_URL.rstrip('/')}/redefinir-senha?token={urllib.parse.quote(token_primeiro)}" if token_primeiro else ""
    db.commit()
    aviso = "usuario_criado"
    if cliente_organiza and link_primeiro:
        try:
            _enviar_email_migracao_humiat(email, novo.nome, link_primeiro)
            aviso = "usuario_criado_email_enviado"
        except Exception as exc:
            aviso = "usuario_criado_email_erro_" + urllib.parse.quote(str(exc)[:160])
    if lokafest_solicitado and cliente_organiza:
        try:
            _garantir_usuario_lokafest_humiat(cliente_organiza, novo.nome)
        except Exception as exc:
            aviso = "usuario_criado_lokafest_erro_" + urllib.parse.quote(str(exc)[:160])
    return RedirectResponse(f"/painel?empresa_id={empresa_id if empresa_id.strip().isdigit() else ''}&ok={aviso}", status_code=303)


@router.post("/admin-humiat/usuarios/{usuario_id}/editar")
async def editar_usuario_humiat(
    usuario_id: int,
    request: Request,
    nome: str = Form(...),
    email: str = Form(...),
    senha: str = Form(""),
    tipo: str = Form(TIPO_CLIENTE_EMPRESA),
    empresa_id: str = Form(""),
    ativo: str = Form("0"),
    usuario: HumiatUsuario = Depends(exigir_admin_humiat),
    db: Session = Depends(get_db),
):
    alvo = db.query(HumiatUsuario).filter(HumiatUsuario.id == usuario_id).first()
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    email_normalizado = email.strip().lower()
    duplicado = db.query(HumiatUsuario).filter(
        HumiatUsuario.email == email_normalizado,
        HumiatUsuario.id != usuario_id,
    ).first()
    if duplicado:
        return RedirectResponse("/painel?erro=E-mail já cadastrado por outro usuário", status_code=303)

    empresa_vinculada = int(empresa_id) if empresa_id.strip().isdigit() else None
    tipo = TIPO_CLIENTE_EMPRESA if empresa_vinculada else TIPO_ADMIN_HUMIAT

    # Evita o usuário interno derrubar a própria sessão ou se vincular por engano.
    novo_ativo = 1 if ativo == "1" else 0
    if alvo.id == usuario.id and not novo_ativo:
        return RedirectResponse("/painel?erro=Você não pode desativar o seu próprio usuário", status_code=303)
    if alvo.id == usuario.id and empresa_vinculada:
        return RedirectResponse("/painel?erro=Você não pode vincular seu próprio usuário a uma empresa", status_code=303)

    alvo.nome = nome.strip()
    alvo.email = email_normalizado
    alvo.tipo = tipo
    alvo.ativo = novo_ativo
    if (alvo.organiza_usuario or "").strip():
        try:
            db.execute(text("UPDATE usuarios SET email=:email WHERE nome=:nome"), {"email": email_normalizado, "nome": alvo.organiza_usuario.strip()})
        except Exception:
            pass
    if not (alvo.organiza_usuario or "").strip():
        try:
            organiza_usuario, _ = _garantir_usuario_central_organiza(db, alvo.nome, alvo.email, senha.strip(), admin=False)
            alvo.organiza_usuario = organiza_usuario
        except ValueError as exc:
            return RedirectResponse(f"/painel?erro={urllib.parse.quote(str(exc))}", status_code=303)
    if senha.strip():
        if len(senha.strip()) < 8:
            return RedirectResponse("/painel?erro=A nova senha deve ter pelo menos 8 caracteres", status_code=303)
        alvo.senha_hash = gerar_hash_senha_id(senha.strip())
        if not empresa_vinculada:
            _sincronizar_senha_usuario_organiza(db, alvo, senha.strip())

    db.query(HumiatUsuarioEmpresa).filter(HumiatUsuarioEmpresa.usuario_id == alvo.id).delete(synchronize_session=False)
    empresa_auditoria = empresa_vinculada
    if empresa_vinculada:
        db.add(HumiatUsuarioEmpresa(usuario_id=alvo.id, empresa_id=empresa_vinculada))

    form = await request.form()
    resumo_acessos = []
    for produto in db.query(HumiatProduto).filter(HumiatProduto.ativo == 1).order_by(HumiatProduto.nome).all():
        sistema = str(form.get(f"produto_{produto.id}_sistema") or "0") == "1"
        adm = str(form.get(f"produto_{produto.id}_adm") or "0") == "1"
        comprado = str(form.get(f"produto_{produto.id}_solvoz_comprado") or "0") == "1"
        catalogo = str(form.get(f"produto_{produto.id}_solvoz_catalogo") or "0") == "1"
        _salvar_usuario_produto_acesso(db, alvo.id, produto, sistema=sistema, adm=adm, solvoz_comprado=comprado, solvoz_catalogo=catalogo)
        if sistema or adm or comprado or catalogo:
            resumo_acessos.append(f"{produto.codigo}:S{int(sistema)}A{int(adm)}C{int(comprado)}K{int(catalogo)}")

    cliente_vinculado = _cliente_organiza_humiat(db, email=alvo.email, documento=alvo.documento or "", telefone=alvo.telefone or "")
    if cliente_vinculado:
        db.execute(text("UPDATE clientes SET humiat_usuario_id=:uid WHERE id=:cid"), {"uid": int(alvo.id), "cid": int(cliente_vinculado.get("id"))})
        _aplicar_acessos_cliente_humiat(db, alvo, cliente_vinculado, liberar_lokafest=True)
    _auditar(db, request, "EDITAR_USUARIO", usuario.id, empresa_auditoria, f"usuario_id={alvo.id}; email={alvo.email}; tipo={alvo.tipo}; ativo={alvo.ativo}; " + ",".join(resumo_acessos))
    db.commit()
    return RedirectResponse(f"/painel?empresa_id={empresa_id if empresa_id.strip().isdigit() else ''}&ok=usuario_atualizado", status_code=303)


@router.post("/admin-humiat/usuarios/{usuario_id}/reenviar-email")
def reenviar_email_usuario_humiat(
    usuario_id: int, request: Request, usuario: HumiatUsuario = Depends(exigir_admin_humiat), db: Session = Depends(get_db)
):
    alvo = db.query(HumiatUsuario).filter(HumiatUsuario.id == int(usuario_id)).first()
    if not alvo:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if not int(alvo.ativo or 0):
        return RedirectResponse("/painel?erro=Ative o Humiat ID antes de reenviar o e-mail", status_code=303)
    try:
        enviar_link_acesso_humiat(db, alvo, request=request, primeiro_acesso=False)
        return RedirectResponse("/painel?ok=E-mail de acesso reenviado", status_code=303)
    except Exception as exc:
        db.rollback()
        return RedirectResponse(f"/painel?erro={urllib.parse.quote(str(exc))}", status_code=303)


@router.post("/painel/atualizar-dados")
def atualizar_dados_painel_humiat(request: Request, usuario: HumiatUsuario = Depends(exigir_humiat_login), db: Session = Depends(get_db)):
    _cache_integracao_apagar(db, f"painel:lokafest:{int(usuario.id)}")
    for empresa in empresas_do_usuario(db, usuario):
        _cache_integracao_apagar(db, f"painel:solvoz:{int(empresa.id)}")
        _cache_integracao_apagar(db, f"painel:connect:{int(empresa.id)}")
    db.commit()
    return RedirectResponse("/painel", status_code=303)


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

    if not _usuario_acesso_interno(db, usuario):
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
    tipo: str = Form(TIPO_CLIENTE_EMPRESA),
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

    empresa_vinculada = int(empresa_id) if empresa_id.strip().isdigit() else None
    tipo = TIPO_CLIENTE_EMPRESA if empresa_vinculada else TIPO_ADMIN_HUMIAT
    if alvo.id == admin.id and empresa_vinculada:
        return RedirectResponse(f"/painel?empresa_id={retorno_empresa_id}&erro=Você não pode vincular seu próprio usuário a uma empresa", status_code=303)

    alvo.nome = nome.strip()
    alvo.email = email
    alvo.tipo = tipo
    alvo.ativo = 1 if ativo == "1" else 0
    # Campo legado mantido apenas no banco por compatibilidade; não faz parte do Humiat ID.
    alvo.organiza_usuario = None

    db.query(HumiatUsuarioEmpresa).filter(HumiatUsuarioEmpresa.usuario_id == usuario_id).delete(synchronize_session=False)
    empresa_auditoria = empresa_vinculada
    if empresa_vinculada:
        db.add(HumiatUsuarioEmpresa(usuario_id=usuario_id, empresa_id=empresa_vinculada))

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
    if _usuario_acesso_interno(db, alvo):
        _sincronizar_senha_usuario_organiza(db, alvo, senha)
    _auditar(db, request, "REDEFINIR_SENHA", admin.id, detalhe=alvo.email)
    db.commit()
    return RedirectResponse(f"/painel?empresa_id={retorno_empresa_id}&ok=senha_redefinida", status_code=303)
