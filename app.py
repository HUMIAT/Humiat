import hashlib
import hmac
import os
import re
from datetime import date, datetime
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text, func, or_
from sqlalchemy.orm import Session, relationship, selectinload

from config import ADMIN_NOME, ADMIN_SENHA, CHAVE_SESSAO, ORGANIZA_VERSAO
from database import Base, SessionLocal, engine, get_db

app = FastAPI(title="HUMIAT", version=ORGANIZA_VERSAO)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


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


class Cliente(Base):
    __tablename__ = "clientes"
    id = Column(Integer, primary_key=True)
    nome = Column(String(140), nullable=False)
    codigo_whatsapp = Column(String(40), nullable=True)
    telefone = Column(String(20), unique=True, nullable=False)
    empresa = Column(String(140), nullable=True)
    documento = Column(String(30), nullable=True)
    cep = Column(String(20), nullable=True)
    cidade = Column(String(120), nullable=True)
    municipio = Column(String(120), nullable=True)
    estado = Column(String(60), nullable=True)
    bairro = Column(String(120), nullable=True)
    endereco = Column(String(255), nullable=True)
    endereco_numero = Column(String(30), nullable=True)
    complemento = Column(String(120), nullable=True)
    email = Column(String(140), nullable=True)
    pacote = Column(String(30), nullable=True)
    falta_pacote = Column(Integer, nullable=True)
    plano = Column(String(60), nullable=True)
    observacao = Column(Text, nullable=True)
    token_ficha = Column(String(64), nullable=True, unique=True)
    criado_em = Column(DateTime, server_default=func.now())
    equipamentos = relationship("Equipamento", back_populates="cliente", cascade="all, delete-orphan")


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
    criado_em = Column(DateTime, server_default=func.now())
    cliente = relationship("Cliente", back_populates="equipamentos")


def limpar_telefone(valor: str) -> str:
    numero = re.sub(r"\D", "", valor or "")
    if numero.startswith("55") and len(numero) > 11:
        numero = numero[2:]
    return numero


def telefone_valido(valor: str) -> bool:
    numero = limpar_telefone(valor)
    return len(numero) == 11 and numero[2] == "9"


def formatar_telefone(valor: str) -> str:
    numero = limpar_telefone(valor)
    return f"({numero[:2]}) {numero[2:7]}-{numero[7:]}" if len(numero) == 11 else (valor or "")


def formatar_data(valor):
    return valor.strftime("%d/%m/%Y") if valor else "-"


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
templates.env.filters["data_br"] = formatar_data
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
    if not usuario:
        raise HTTPException(status_code=303, headers={"Location": "/area-restrita/login"})
    return usuario


def exigir_admin(usuario: Usuario):
    if not usuario.is_admin:
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador")


def data_form(valor: str):
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date() if valor else None
    except ValueError:
        return None


@app.on_event("startup")
def iniciar_banco():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(Usuario).filter(Usuario.nome == ADMIN_NOME).first():
            db.add(Usuario(nome=ADMIN_NOME, senha_hash=gerar_hash_senha(ADMIN_SENHA), is_admin=1, ativo=1, cargo="Administrador"))
            db.commit()
    finally:
        db.close()


@app.get("/", response_class=HTMLResponse)
def inicio_publico(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/saude")
def saude():
    return {"status": "ok", "versao": ORGANIZA_VERSAO}


@app.get("/area-restrita/login", response_class=HTMLResponse)
def login(request: Request, erro: str = ""):
    return templates.TemplateResponse("organiza/login.html", {"request": request, "erro": erro})


@app.post("/area-restrita/login")
def entrar(usuario: str = Form(...), senha: str = Form(...), db: Session = Depends(get_db)):
    encontrado = db.query(Usuario).filter(Usuario.nome == usuario.strip(), Usuario.ativo == 1).first()
    if not encontrado or not verificar_senha(senha, encontrado.senha_hash):
        return RedirectResponse("/area-restrita/login?erro=Usuário ou senha inválidos", status_code=303)
    resposta = RedirectResponse("/organiza", status_code=303)
    resposta.set_cookie("humiat_sessao", f"{encontrado.nome}.{assinatura(encontrado.nome)}", httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14)
    return resposta


@app.get("/area-restrita/sair")
def sair():
    resposta = RedirectResponse("/area-restrita/login", status_code=303)
    resposta.delete_cookie("humiat_sessao")
    return resposta


@app.get("/organiza", response_class=HTMLResponse)
def painel(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    total_clientes = db.query(Cliente).count()
    total_equipamentos = db.query(Equipamento).count()
    clientes_com_equipamento = db.query(Cliente).join(Equipamento).distinct().count()
    recentes = db.query(Cliente).options(selectinload(Cliente.equipamentos)).order_by(Cliente.criado_em.desc()).limit(8).all()
    return templates.TemplateResponse("organiza/painel.html", {
        "request": request, "usuario": usuario, "total_clientes": total_clientes,
        "total_equipamentos": total_equipamentos, "clientes_com_equipamento": clientes_com_equipamento,
        "recentes": recentes,
    })


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


def preencher_cliente(cliente: Cliente, form: dict):
    cliente.nome = (form.get("nome") or "").strip()
    cliente.codigo_whatsapp = (form.get("codigo_whatsapp") or "").strip() or None
    cliente.telefone = limpar_telefone(form.get("telefone") or "")
    cliente.empresa = (form.get("empresa") or "").strip() or None
    cliente.documento = (form.get("documento") or "").strip() or None
    cliente.cep = (form.get("cep") or "").strip() or None
    cliente.municipio = (form.get("municipio") or "").strip() or None
    cliente.cidade = cliente.municipio
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
    telefone = limpar_telefone(form.get("telefone") or "")
    erro = ""
    if not (form.get("nome") or "").strip():
        erro = "Informe o nome do cliente."
    elif not telefone_valido(telefone):
        erro = "Informe um celular válido com 11 dígitos, incluindo DDD."
    elif db.query(Cliente).filter(Cliente.telefone == telefone).first():
        erro = "Já existe um cliente com este telefone."
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
    cliente = db.query(Cliente).options(selectinload(Cliente.equipamentos)).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    return templates.TemplateResponse("organiza/cliente_detalhe.html", {"request": request, "usuario": usuario, "cliente": cliente})


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
    telefone = limpar_telefone(form.get("telefone") or "")
    erro = ""
    if not (form.get("nome") or "").strip(): erro = "Informe o nome do cliente."
    elif not telefone_valido(telefone): erro = "Informe um celular válido com 11 dígitos, incluindo DDD."
    elif db.query(Cliente).filter(Cliente.telefone == telefone, Cliente.id != cliente_id).first(): erro = "Já existe outro cliente com este telefone."
    if erro:
        preencher_cliente(cliente, form)
        return templates.TemplateResponse("organiza/cliente_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "erro": erro}, status_code=400)
    preencher_cliente(cliente, form); db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente.id}", status_code=303)


def preencher_equipamento(eq: Equipamento, form: dict):
    eq.tipo = (form.get("tipo") or "").strip() or None
    eq.modelo = (form.get("modelo") or "").strip() or None
    eq.pacote = (form.get("pacote") or "").strip() or None
    try: eq.falta_pacote = int(form.get("falta_pacote")) if form.get("falta_pacote") else None
    except ValueError: eq.falta_pacote = None
    eq.plano = (form.get("plano") or "").strip() or None
    eq.valor = (form.get("valor") or "").strip() or None
    eq.preco_custo = (form.get("preco_custo") or "").strip() or None
    eq.preco_venda = (form.get("preco_venda") or "").strip() or None
    eq.pago = (form.get("pago") or "").strip() or None
    eq.falta = (form.get("falta") or "").strip() or None
    eq.data_compra = data_form(form.get("data_compra") or "")
    eq.previsao_entrega = data_form(form.get("previsao_entrega") or "")
    eq.maquina = (form.get("maquina") or "").strip() or None
    eq.rede_instalada = (form.get("rede_instalada") or "").strip() or None
    eq.anydesk = (form.get("anydesk") or "").strip() or None
    eq.status = (form.get("status") or "Ativo").strip()
    eq.observacao = (form.get("observacao") or "").strip() or None


@app.get("/organiza/clientes/{cliente_id}/equipamentos/novo", response_class=HTMLResponse)
def equipamento_novo(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    return templates.TemplateResponse("organiza/equipamento_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "equipamento": None, "erro": ""})


@app.post("/organiza/clientes/{cliente_id}/equipamentos/novo")
async def equipamento_criar(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    form = dict(await request.form())
    if not (form.get("tipo") or "").strip():
        eq = Equipamento(cliente_id=cliente_id); preencher_equipamento(eq, form)
        return templates.TemplateResponse("organiza/equipamento_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq, "erro": "Informe o tipo do equipamento."}, status_code=400)
    eq = Equipamento(cliente_id=cliente_id); preencher_equipamento(eq, form)
    db.add(eq); db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente_id}", status_code=303)


@app.get("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/editar", response_class=HTMLResponse)
def equipamento_editar(cliente_id: int, equipamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id, Equipamento.cliente_id == cliente_id).first()
    if not cliente or not eq: raise HTTPException(404)
    return templates.TemplateResponse("organiza/equipamento_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq, "erro": ""})


@app.post("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/editar")
async def equipamento_salvar(cliente_id: int, equipamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id, Equipamento.cliente_id == cliente_id).first()
    if not eq: raise HTTPException(404)
    form = dict(await request.form())
    preencher_equipamento(eq, form); db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente_id}", status_code=303)


@app.get("/organiza/usuarios", response_class=HTMLResponse)
def usuarios(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_admin(usuario)
    return templates.TemplateResponse("organiza/usuarios.html", {"request": request, "usuario": usuario, "usuarios": db.query(Usuario).order_by(Usuario.nome).all()})
