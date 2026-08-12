import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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


@router.get("/entrar", response_class=HTMLResponse)
def login_humiat(request: Request, erro: str = "", db: Session = Depends(get_db)):
    if humiat_usuario_da_requisicao(request, db):
        return RedirectResponse("/painel", status_code=303)
    return templates.TemplateResponse("humiat/login.html", {"request": request, "erro": erro})


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
    empresas = empresas_do_usuario(db, usuario)
    empresa = None
    if empresa_id:
        empresa = next((e for e in empresas if e.id == empresa_id), None)
        if not empresa:
            raise HTTPException(status_code=403, detail="Empresa não autorizada")
    elif len(empresas) == 1:
        empresa = empresas[0]

    produtos = produtos_da_empresa(db, empresa.id) if empresa else []
    return templates.TemplateResponse("humiat/painel.html", {"request": request, "usuario": usuario, "empresas": empresas, "empresa": empresa, "produtos": produtos, "admin_humiat": usuario.tipo == TIPO_ADMIN_HUMIAT})


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


@router.get("/admin-humiat", response_class=HTMLResponse)
def admin_humiat(request: Request, usuario: HumiatUsuario = Depends(exigir_admin_humiat), db: Session = Depends(get_db)):
    empresas = db.query(HumiatEmpresa).order_by(HumiatEmpresa.nome).all()
    usuarios = db.query(HumiatUsuario).order_by(HumiatUsuario.nome).all()
    produtos = db.query(HumiatProduto).order_by(HumiatProduto.nome).all()
    vinculos = db.query(HumiatUsuarioEmpresa).all()
    ep = db.query(HumiatEmpresaProduto).all()
    return templates.TemplateResponse("humiat/admin.html", {"request": request, "usuario": usuario, "empresas": empresas, "usuarios": usuarios, "produtos": produtos, "vinculos": vinculos, "empresa_produtos": ep})


@router.post("/admin-humiat/empresas")
def criar_empresa_humiat(request: Request, nome: str = Form(...), slug: str = Form(...), usuario: HumiatUsuario = Depends(exigir_admin_humiat), db: Session = Depends(get_db)):
    slug = "-".join(slug.lower().strip().split())
    if db.query(HumiatEmpresa).filter(HumiatEmpresa.slug == slug).first():
        return RedirectResponse("/admin-humiat?erro=Slug já utilizado", status_code=303)
    e = HumiatEmpresa(nome=nome.strip(), slug=slug, ativo=1)
    db.add(e); db.flush()
    _auditar(db, request, "CRIAR_EMPRESA", usuario.id, e.id, e.nome)
    db.commit()
    return RedirectResponse("/admin-humiat", status_code=303)


@router.post("/admin-humiat/usuarios")
def criar_usuario_humiat(request: Request, nome: str = Form(...), email: str = Form(...), senha: str = Form(...), tipo: str = Form(TIPO_ADMIN_EMPRESA), empresa_id: str = Form(""), usuario: HumiatUsuario = Depends(exigir_admin_humiat), db: Session = Depends(get_db)):
    email = email.strip().lower()
    if db.query(HumiatUsuario).filter(HumiatUsuario.email == email).first():
        return RedirectResponse("/admin-humiat?erro=E-mail já cadastrado", status_code=303)
    tipo = tipo if tipo in {TIPO_ADMIN_HUMIAT, TIPO_ADMIN_EMPRESA} else TIPO_ADMIN_EMPRESA
    if tipo == TIPO_ADMIN_EMPRESA and not empresa_id.strip().isdigit():
        return RedirectResponse("/admin-humiat?erro=Selecione a empresa para o Administrador da Empresa", status_code=303)
    novo = HumiatUsuario(nome=nome.strip(), email=email, senha_hash=gerar_hash_senha_id(senha), tipo=tipo, ativo=1, organiza_usuario=None)
    db.add(novo); db.flush()
    if tipo == TIPO_ADMIN_EMPRESA:
        db.add(HumiatUsuarioEmpresa(usuario_id=novo.id, empresa_id=int(empresa_id)))
    _auditar(db, request, "CRIAR_USUARIO", usuario.id, int(empresa_id) if empresa_id.strip().isdigit() else None, email)
    db.commit()
    return RedirectResponse("/admin-humiat", status_code=303)


@router.post("/admin-humiat/empresa/{empresa_id}/produto/{produto_id}")
def alternar_produto(empresa_id: int, produto_id: int, request: Request, usuario: HumiatUsuario = Depends(exigir_admin_humiat), db: Session = Depends(get_db)):
    item = db.query(HumiatEmpresaProduto).filter(HumiatEmpresaProduto.empresa_id == empresa_id, HumiatEmpresaProduto.produto_id == produto_id).first()
    if item:
        item.ativo = 0 if item.ativo else 1
    else:
        item = HumiatEmpresaProduto(empresa_id=empresa_id, produto_id=produto_id, ativo=1)
        db.add(item)
    _auditar(db, request, "ALTERAR_PRODUTO_EMPRESA", usuario.id, empresa_id, f"produto={produto_id}; ativo={item.ativo}")
    db.commit()
    return RedirectResponse("/admin-humiat", status_code=303)


@router.post("/admin-humiat/usuario/{usuario_id}/editar")
def editar_usuario_humiat(
    usuario_id: int,
    request: Request,
    nome: str = Form(...),
    email: str = Form(...),
    tipo: str = Form(TIPO_ADMIN_EMPRESA),
    empresa_id: str = Form(""),
    ativo: str = Form("1"),
    admin: HumiatUsuario = Depends(exigir_admin_humiat),
    db: Session = Depends(get_db),
):
    alvo = db.query(HumiatUsuario).filter(HumiatUsuario.id == usuario_id).first()
    if not alvo:
        raise HTTPException(status_code=404)

    email = email.strip().lower()
    existente = db.query(HumiatUsuario).filter(HumiatUsuario.email == email, HumiatUsuario.id != usuario_id).first()
    if existente:
        return RedirectResponse("/admin-humiat?erro=E-mail já utilizado por outro usuário", status_code=303)

    tipo = tipo if tipo in {TIPO_ADMIN_HUMIAT, TIPO_ADMIN_EMPRESA} else TIPO_ADMIN_EMPRESA
    if tipo == TIPO_ADMIN_EMPRESA and not empresa_id.strip().isdigit():
        return RedirectResponse("/admin-humiat?erro=Administrador da Empresa precisa estar vinculado a uma empresa", status_code=303)

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
    return RedirectResponse("/admin-humiat", status_code=303)


@router.post("/admin-humiat/usuario/{usuario_id}/senha")
def redefinir_senha(usuario_id: int, request: Request, senha: str = Form(...), admin: HumiatUsuario = Depends(exigir_admin_humiat), db: Session = Depends(get_db)):
    alvo = db.query(HumiatUsuario).filter(HumiatUsuario.id == usuario_id).first()
    if not alvo:
        raise HTTPException(status_code=404)
    alvo.senha_hash = gerar_hash_senha_id(senha)
    _auditar(db, request, "REDEFINIR_SENHA", admin.id, detalhe=alvo.email)
    db.commit()
    return RedirectResponse("/admin-humiat", status_code=303)
