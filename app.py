import hashlib
import hmac
import os
import re
import json
import secrets
import csv
import io
import xml.etree.ElementTree as ET
import unicodedata
from datetime import date, datetime, time
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import ClientDisconnect
from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text, Float, func, or_, inspect, text
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
    inscricao_estadual = Column(String(30), nullable=True)
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
    garantia_meses = Column(Integer, nullable=True, default=3)
    numero_serie = Column(String(120), nullable=True)
    fabricante = Column(String(80), nullable=False, default="KARAOKERJ")
    criado_em = Column(DateTime, server_default=func.now())
    cliente = relationship("Cliente", back_populates="equipamentos")


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


class Manutencao(Base):
    __tablename__ = "assistencias"
    id = Column(Integer, primary_key=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False)
    equipamento_id = Column(Integer, ForeignKey("equipamentos.id"), nullable=False)
    defeito = Column(Text, nullable=False)
    diagnostico = Column(Text, nullable=True)
    status = Column(String(40), nullable=False, default="Recebida")
    entrega_prevista_em = Column(DateTime, nullable=True)
    recebido_em = Column(DateTime, nullable=True)
    prazo = Column(String(120), nullable=True)
    pronto_em = Column(DateTime, nullable=True)
    retirada_em = Column(DateTime, nullable=True)
    entregue_em = Column(DateTime, nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    cliente = relationship("Cliente")
    equipamento = relationship("Equipamento")
    orcamentos = relationship("Orcamento", back_populates="manutencao", cascade="all, delete-orphan")


class Orcamento(Base):
    __tablename__ = "assistencia_orcamentos"
    id = Column(Integer, primary_key=True)
    manutencao_id = Column(Integer, ForeignKey("assistencias.id"), nullable=False)
    versao = Column(Integer, nullable=False, default=1)
    token = Column(String(64), unique=True, nullable=False)
    status = Column(String(40), nullable=False, default="Rascunho")
    observacao = Column(Text, nullable=True)
    desconto = Column(Float, nullable=False, default=0)
    valor_manutencao = Column(Float, nullable=False, default=0)
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
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    orcamento = relationship("Orcamento", back_populates="pagamentos")


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


def datetime_form(valor: str):
    try:
        return datetime.strptime(valor, "%Y-%m-%dT%H:%M") if valor else None
    except ValueError:
        return None


def etapa_manutencao(m):
    if m.status == "Encerrada" or m.entregue_em: return 7
    if m.retirada_em: return 6
    if m.pronto_em: return 5
    o = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None
    if o and o.status in ("Aprovado", "Aprovado parcialmente", "Aprovado manualmente"):
        recebido = sum(p.valor for p in o.pagamentos)
        return 4 if recebido > 0 else 4
    if o and o.status in ("Enviado", "Aguardando aprovação"): return 3
    if o and o.itens: return 2
    if m.recebido_em: return 2
    return 1


@app.on_event("startup")
def iniciar_banco():
    Base.metadata.create_all(bind=engine)
    # Migração leve para bancos já existentes (SQLite e PostgreSQL)
    insp = inspect(engine)
    if "assistencias" in insp.get_table_names():
        existentes = {c["name"] for c in insp.get_columns("assistencias")}
        tipo_dt = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
        with engine.begin() as conn:
            for coluna in ("entrega_prevista_em", "recebido_em", "entregue_em"):
                if coluna not in existentes:
                    conn.execute(text(f"ALTER TABLE assistencias ADD COLUMN {coluna} {tipo_dt}"))
    if "assistencia_orcamentos" in insp.get_table_names():
        existentes_orcamento = {c["name"] for c in insp.get_columns("assistencia_orcamentos")}
        with engine.begin() as conn:
            if "desconto" not in existentes_orcamento:
                conn.execute(text("ALTER TABLE assistencia_orcamentos ADD COLUMN desconto FLOAT NOT NULL DEFAULT 0"))
            if "valor_manutencao" not in existentes_orcamento:
                conn.execute(text("ALTER TABLE assistencia_orcamentos ADD COLUMN valor_manutencao FLOAT NOT NULL DEFAULT 0"))
    if "clientes" in insp.get_table_names():
        existentes_clientes = {c["name"] for c in insp.get_columns("clientes")}
        with engine.begin() as conn:
            if "inscricao_estadual" not in existentes_clientes:
                conn.execute(text("ALTER TABLE clientes ADD COLUMN inscricao_estadual VARCHAR(30)"))
    if "equipamentos" in insp.get_table_names():
        existentes_equipamentos = {c["name"] for c in insp.get_columns("equipamentos")}
        with engine.begin() as conn:
            if "garantia_meses" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN garantia_meses INTEGER DEFAULT 3"))
            if "numero_serie" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN numero_serie VARCHAR(120)"))
            if "fabricante" not in existentes_equipamentos:
                conn.execute(text("ALTER TABLE equipamentos ADD COLUMN fabricante VARCHAR(80) DEFAULT 'KARAOKERJ'"))
    db = SessionLocal()
    try:
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

        # Padroniza todos os equipamentos existentes.
        equipamentos_existentes = db.query(Equipamento).order_by(Equipamento.id.asc()).all()
        codigos_usados = set()
        maior_codigo = 0
        for equipamento in equipamentos_existentes:
            codigo = (equipamento.maquina or "").strip().upper()
            encontrado = re.fullmatch(r"KRJ(\d+)", codigo)
            if encontrado and codigo not in codigos_usados:
                equipamento.maquina = codigo
                codigos_usados.add(codigo)
                maior_codigo = max(maior_codigo, int(encontrado.group(1)))
            else:
                maior_codigo += 1
                while f"KRJ{maior_codigo:04d}" in codigos_usados:
                    maior_codigo += 1
                equipamento.maquina = f"KRJ{maior_codigo:04d}"
                codigos_usados.add(equipamento.maquina)
            equipamento.fabricante = equipamento.fabricante or "KARAOKERJ"
        for (cliente_id,) in db.query(Equipamento.cliente_id).distinct().all():
            reordenar_series_cliente(db, cliente_id)
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
    manutencoes = db.query(Manutencao).options(
        selectinload(Manutencao.cliente),
        selectinload(Manutencao.equipamento),
        selectinload(Manutencao.orcamentos).selectinload(Orcamento.itens),
        selectinload(Manutencao.orcamentos).selectinload(Orcamento.pagamentos),
    ).order_by(Manutencao.criado_em.desc()).all()

    pendencias = {
        "entrada": [],
        "orcamento": [],
        "aceite": [],
        "pagamento": [],
        "producao": [],
        "agenda": [],
        "retirada": [],
    }

    for m in manutencoes:
        orcamento = sorted(m.orcamentos, key=lambda x: x.versao)[-1] if m.orcamentos else None
        totais = totais_orcamento(orcamento) if orcamento else {"falta": 0}
        m.painel_saldo = totais.get("falta", 0)

        if m.status == "Aguardando equipamento":
            pendencias["entrada"].append(m)
        elif m.status in ("Recebida", "Orçamento em elaboração") or (orcamento and orcamento.status == "Rascunho"):
            pendencias["orcamento"].append(m)
        elif m.status == "Aguardando aprovação" or (orcamento and orcamento.status in ("Enviado", "Aguardando aprovação")):
            pendencias["aceite"].append(m)
        elif m.status == "Aprovado" or (orcamento and orcamento.status in ("Aprovado", "Aprovado parcialmente", "Aprovado manualmente") and (totais.get("falta", 0) > 0 or not m.prazo)):
            pendencias["pagamento"].append(m)
        elif m.status == "Em manutenção":
            pendencias["producao"].append(m)
        elif m.status == "Pronto para retirada" and not m.retirada_em:
            pendencias["agenda"].append(m)
        elif m.status == "Retirada agendada":
            pendencias["retirada"].append(m)

    return templates.TemplateResponse("organiza/painel.html", {
        "request": request,
        "usuario": usuario,
        "total_manutencoes": len(manutencoes),
        "pendencias": pendencias,
        "total_pendencias": sum(len(lista) for lista in pendencias.values()),
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


def preencher_cliente(cliente: Cliente, form: dict):
    cliente.nome = limpar_nome_cliente(form.get("nome") or "")
    cliente.telefone = limpar_telefone(form.get("telefone") or "")
    cliente.empresa = (form.get("empresa") or "").strip() or None
    cliente.documento = (form.get("documento") or "").strip() or None
    cliente.inscricao_estadual = (form.get("inscricao_estadual") or "").strip() or None
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
    if not cliente.token_ficha:
        cliente.token_ficha = secrets.token_urlsafe(24)
        db.commit()
    manutencoes = db.query(Manutencao).filter(Manutencao.cliente_id == cliente_id).order_by(Manutencao.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/cliente_detalhe.html", {"request": request, "usuario": usuario, "cliente": cliente, "manutencoes": manutencoes})


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
    # O saldo é sempre calculado no servidor para não depender do navegador.
    total = moeda_num(eq.valor)
    recebido = moeda_num(eq.pago)
    eq.falta = f"{max(total - recebido, 0):.2f}" if (eq.valor or eq.pago) else None
    eq.data_compra = data_form(form.get("data_compra") or "")
    eq.previsao_entrega = data_form(form.get("previsao_entrega") or "")
    # Máquina e número de série são gerados pelo sistema e não podem ser alterados no formulário.
    eq.status = (form.get("status") or "Ativo").strip()
    eq.observacao = (form.get("observacao") or "").strip() or None
    fabricante = (form.get("fabricante") or "KARAOKERJ").strip().upper()
    eq.fabricante = fabricante if fabricante in ("KARAOKERJ", "OUTROS") else "KARAOKERJ"
    try:
        eq.garantia_meses = max(int(form.get("garantia_meses") or 3), 0)
    except ValueError:
        eq.garantia_meses = 3


def proximo_codigo_maquina(db: Session) -> str:
    maior = 0
    for (codigo,) in db.query(Equipamento.maquina).filter(Equipamento.maquina.isnot(None)).all():
        encontrado = re.fullmatch(r"KRJ(\d+)", (codigo or "").strip().upper())
        if encontrado:
            maior = max(maior, int(encontrado.group(1)))
    return f"KRJ{maior + 1:04d}"


def reordenar_series_cliente(db: Session, cliente_id: int):
    equipamentos = db.query(Equipamento).filter(Equipamento.cliente_id == cliente_id).order_by(
        Equipamento.data_compra.is_(None), Equipamento.data_compra.asc(), Equipamento.criado_em.asc(), Equipamento.id.asc()
    ).all()
    for ordem, equipamento in enumerate(equipamentos, start=1):
        if equipamento.maquina:
            equipamento.numero_serie = f"{equipamento.maquina}-{ordem}"


def garantir_identificacao_equipamento(db: Session, equipamento: Equipamento):
    if not equipamento.maquina:
        equipamento.maquina = proximo_codigo_maquina(db)
    if not equipamento.fabricante:
        equipamento.fabricante = "KARAOKERJ"


def opcoes_equipamentos(db: Session):
    tipos_bd = [x[0] for x in db.query(Equipamento.tipo).filter(Equipamento.tipo.isnot(None), Equipamento.tipo != "").distinct().order_by(Equipamento.tipo).all()]
    pacotes_bd = [x[0] for x in db.query(Equipamento.pacote).filter(Equipamento.pacote.isnot(None), Equipamento.pacote != "").distinct().order_by(Equipamento.pacote.desc()).all()]
    tipos = list(dict.fromkeys(["JUKEBOX", "MALETA", "FLIPERAMA", "COMPUTADOR", "SISTEMA"] + tipos_bd))
    pacotes = list(dict.fromkeys(["2026.2", "2026.1"] + pacotes_bd))
    return tipos, pacotes


@app.get("/organiza/clientes/{cliente_id}/equipamentos/novo", response_class=HTMLResponse)
def equipamento_novo(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    tipos, pacotes = opcoes_equipamentos(db)
    return templates.TemplateResponse("organiza/equipamento_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "equipamento": None, "erro": "", "tipos": tipos, "pacotes": pacotes})


@app.post("/organiza/clientes/{cliente_id}/equipamentos/novo")
async def equipamento_criar(cliente_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente: raise HTTPException(404)
    form = dict(await request.form())
    if not (form.get("tipo") or "").strip():
        eq = Equipamento(cliente_id=cliente_id); preencher_equipamento(eq, form)
        tipos, pacotes = opcoes_equipamentos(db)
        return templates.TemplateResponse("organiza/equipamento_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq, "erro": "Informe o tipo do equipamento.", "tipos": tipos, "pacotes": pacotes}, status_code=400)
    eq = Equipamento(cliente_id=cliente_id); preencher_equipamento(eq, form)
    garantir_identificacao_equipamento(db, eq)
    db.add(eq); db.flush()
    reordenar_series_cliente(db, cliente_id)
    db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente_id}", status_code=303)


@app.get("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/editar", response_class=HTMLResponse)
def equipamento_editar(cliente_id: int, equipamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id, Equipamento.cliente_id == cliente_id).first()
    if not cliente or not eq: raise HTTPException(404)
    tipos, pacotes = opcoes_equipamentos(db)
    return templates.TemplateResponse("organiza/equipamento_form.html", {"request": request, "usuario": usuario, "cliente": cliente, "equipamento": eq, "erro": "", "tipos": tipos, "pacotes": pacotes})


@app.post("/organiza/clientes/{cliente_id}/equipamentos/{equipamento_id}/editar")
async def equipamento_salvar(cliente_id: int, equipamento_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id, Equipamento.cliente_id == cliente_id).first()
    if not eq: raise HTTPException(404)
    form = dict(await request.form())
    preencher_equipamento(eq, form)
    garantir_identificacao_equipamento(db, eq)
    reordenar_series_cliente(db, cliente_id)
    db.commit()
    return RedirectResponse(f"/organiza/clientes/{cliente_id}", status_code=303)


# ---------------------------------------------------------
# VENDAS SIMPLES
# Uma venda cria (ou reutiliza) o cliente e já cadastra o equipamento.
# ---------------------------------------------------------

STATUS_VENDA = ("Solicitar gabinete", "Montagem", "Pronto para entrega", "Entregue")


def equipamento_eh_venda(eq: Equipamento) -> bool:
    return bool(eq.data_compra or eq.previsao_entrega or eq.valor or eq.pago or eq.status in STATUS_VENDA)


@app.get("/organiza/vendas", response_class=HTMLResponse)
def vendas(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    equipamentos = db.query(Equipamento).options(selectinload(Equipamento.cliente)).order_by(
        Equipamento.previsao_entrega.asc(), Equipamento.criado_em.desc()
    ).all()
    equipamentos = [eq for eq in equipamentos if equipamento_eh_venda(eq)]
    return templates.TemplateResponse("organiza/vendas.html", {
        "request": request, "usuario": usuario, "vendas": equipamentos
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
    clientes = db.query(Cliente).order_by(Cliente.nome.asc()).all()
    cliente = None
    cliente_id = int(form.get("cliente_id") or 0)
    telefone = limpar_telefone(form.get("telefone") or "")
    nome = (form.get("nome") or "").strip()

    if cliente_id:
        cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    elif telefone:
        cliente = db.query(Cliente).filter(Cliente.telefone == telefone).first()
        if not cliente:
            if not nome:
                erro = "Informe o nome para cadastrar o novo cliente."
                return templates.TemplateResponse("organiza/venda_nova.html", {"request": request, "usuario": usuario, "clientes": clientes, "cliente_id": 0, "erro": erro, "dados": form, "status_venda": STATUS_VENDA, "tipos": tipos, "pacotes": pacotes}, status_code=400)
            if not telefone_valido(telefone):
                erro = "Informe um WhatsApp válido com 11 dígitos, incluindo DDD."
                return templates.TemplateResponse("organiza/venda_nova.html", {"request": request, "usuario": usuario, "clientes": clientes, "cliente_id": 0, "erro": erro, "dados": form, "status_venda": STATUS_VENDA, "tipos": tipos, "pacotes": pacotes}, status_code=400)
            cliente = Cliente(nome=nome, telefone=telefone)
            cliente.email = (form.get("email") or "").strip() or None
            cliente.municipio = (form.get("municipio") or "").strip() or None
            cliente.cidade = cliente.municipio
            cliente.observacao = (form.get("cliente_observacao") or "").strip() or None
            db.add(cliente)
            db.flush()

    if not cliente:
        erro = "Selecione um cliente existente ou informe nome e WhatsApp para criar um novo."
        return templates.TemplateResponse("organiza/venda_nova.html", {"request": request, "usuario": usuario, "clientes": clientes, "cliente_id": cliente_id, "erro": erro, "dados": form, "status_venda": STATUS_VENDA, "tipos": tipos, "pacotes": pacotes}, status_code=400)
    if not (form.get("tipo") or "").strip():
        erro = "Informe o tipo do equipamento."
        return templates.TemplateResponse("organiza/venda_nova.html", {"request": request, "usuario": usuario, "clientes": clientes, "cliente_id": cliente.id, "erro": erro, "dados": form, "status_venda": STATUS_VENDA, "tipos": tipos, "pacotes": pacotes}, status_code=400)

    eq = Equipamento(cliente_id=cliente.id)
    preencher_equipamento(eq, form)
    if eq.status not in STATUS_VENDA:
        eq.status = "Solicitar gabinete"
    garantir_identificacao_equipamento(db, eq)
    db.add(eq)
    db.flush()
    reordenar_series_cliente(db, cliente.id)
    db.commit()
    db.refresh(eq)
    return RedirectResponse(f"/organiza/clientes/{cliente.id}/equipamentos/{eq.id}/editar?criado=1", status_code=303)


@app.get("/cadastro/{token}", response_class=HTMLResponse)
def cadastro_publico(token: str, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    return templates.TemplateResponse("organiza/cadastro_publico.html", {"request": request, "cliente": cliente, "erro": "", "salvo": request.query_params.get("salvo")})


@app.post("/cadastro/{token}")
async def cadastro_publico_salvar(token: str, request: Request, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.token_ficha == token).first()
    if not cliente:
        raise HTTPException(404)
    form = dict(await request.form())
    telefone_original = cliente.telefone
    preencher_cliente(cliente, form)
    if not cliente.nome or not telefone_valido(cliente.telefone):
        cliente.telefone = telefone_original
        return templates.TemplateResponse("organiza/cadastro_publico.html", {"request": request, "cliente": cliente, "erro": "Informe nome e WhatsApp válidos.", "salvo": False}, status_code=400)
    duplicado = db.query(Cliente).filter(Cliente.telefone == cliente.telefone, Cliente.id != cliente.id).first()
    if duplicado:
        cliente.telefone = telefone_original
        return templates.TemplateResponse("organiza/cadastro_publico.html", {"request": request, "cliente": cliente, "erro": "Este WhatsApp já pertence a outro cadastro.", "salvo": False}, status_code=400)
    db.commit()
    return RedirectResponse(f"/cadastro/{token}?salvo=1", status_code=303)


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

    logo_path = os.path.join(os.path.dirname(__file__), "static", "img", "karaoke-rj-garantia.jpeg")
    logo = Image(logo_path, width=25 * mm, height=25 * mm) if os.path.exists(logo_path) else Spacer(25 * mm, 25 * mm)
    dados_empresa = Paragraph(
        "<b>EMPRESA KARAOKE &amp; GAMES RJ.</b><br/>"
        "CNPJ: 35.458.112/0001-75 &nbsp;&nbsp;&nbsp; IM: 1213508-4<br/>"
        "Rua João Romariz, 313 - Ramos - Rio de Janeiro/RJ - CEP: 21031-700<br/>"
        "WhatsApp: (21) 99507-9690 / (21) 99650-4516<br/>"
        "www.karaokerj.com.br &nbsp;&nbsp; contato@karaokerj.com.br", cabecalho
    )
    header = Table([[logo, dados_empresa]], colWidths=[30 * mm, 145 * mm])
    header.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("LINEBELOW", (0,0), (-1,-1), 0.7, colors.HexColor("#555555")), ("LEFTPADDING", (0,0), (-1,-1), 0), ("RIGHTPADDING", (0,0), (-1,-1), 0), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))

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


@app.get("/organiza/equipamentos/{equipamento_id}/nota.xml")
def dados_nota_xml(equipamento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).options(selectinload(Equipamento.cliente)).filter(Equipamento.id == equipamento_id).first()
    if not eq:
        raise HTTPException(404)
    c = eq.cliente
    raiz = ET.Element("dados_para_emissao")
    ET.SubElement(raiz, "aviso").text = "Arquivo de apoio para importacao. Nao e uma NF-e autorizada pela SEFAZ."
    dest = ET.SubElement(raiz, "destinatario")
    for chave, valor in {"nome":c.nome,"cpf_cnpj":c.documento,"inscricao_estadual":c.inscricao_estadual,"email":c.email,"telefone":c.telefone,"cep":c.cep,"logradouro":c.endereco,"numero":c.endereco_numero,"complemento":c.complemento,"bairro":c.bairro,"municipio":c.municipio or c.cidade,"uf":c.estado}.items():
        ET.SubElement(dest,chave).text = valor or ""
    item = ET.SubElement(raiz, "item")
    for chave, valor in {"descricao":f"{eq.tipo or ''} {eq.modelo or ''}".strip(),"codigo":eq.maquina,"numero_serie":eq.numero_serie,"valor_total":str(moeda_num(eq.valor or eq.preco_venda or '0')),"data_compra":eq.data_compra.isoformat() if eq.data_compra else ""}.items():
        ET.SubElement(item,chave).text = valor or ""
    conteudo = ET.tostring(raiz, encoding="utf-8", xml_declaration=True)
    return Response(conteudo, media_type="application/xml", headers={"Content-Disposition": f'attachment; filename="dados_nota_{eq.id}.xml"'})


@app.get("/organiza/equipamentos/{equipamento_id}/nota.csv")
def dados_nota_csv(equipamento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    eq = db.query(Equipamento).options(selectinload(Equipamento.cliente)).filter(Equipamento.id == equipamento_id).first()
    if not eq:
        raise HTTPException(404)
    c = eq.cliente
    out = io.StringIO()
    campos = ["nome","cpf_cnpj","inscricao_estadual","email","telefone","cep","logradouro","numero","complemento","bairro","municipio","uf","descricao","codigo","numero_serie","valor_total","data_compra"]
    w = csv.DictWriter(out, fieldnames=campos, delimiter=';')
    w.writeheader()
    w.writerow({"nome":c.nome,"cpf_cnpj":c.documento or "","inscricao_estadual":c.inscricao_estadual or "","email":c.email or "","telefone":c.telefone,"cep":c.cep or "","logradouro":c.endereco or "","numero":c.endereco_numero or "","complemento":c.complemento or "","bairro":c.bairro or "","municipio":c.municipio or c.cidade or "","uf":c.estado or "","descricao":f"{eq.tipo or ''} {eq.modelo or ''}".strip(),"codigo":eq.maquina or "","numero_serie":eq.numero_serie or "","valor_total":str(moeda_num(eq.valor or eq.preco_venda or '0')).replace('.',','),"data_compra":eq.data_compra.strftime('%d/%m/%Y') if eq.data_compra else ""})
    conteudo = '\ufeff' + out.getvalue()
    return Response(conteudo.encode('utf-8'), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="dados_nota_{eq.id}.csv"'})


@app.get("/organiza/usuarios", response_class=HTMLResponse)
def usuarios(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_admin(usuario)
    return templates.TemplateResponse("organiza/usuarios.html", {"request": request, "usuario": usuario, "usuarios": db.query(Usuario).order_by(Usuario.nome).all()})


def moeda_num(valor: str) -> float:
    texto = (valor or "0").replace("R$", "").strip().replace(".", "").replace(",", ".")
    try:
        return round(float(texto), 2)
    except ValueError:
        return 0.0


def totais_orcamento(orcamento: Orcamento):
    manutencao = max(float(orcamento.valor_manutencao or 0), 0)
    itens_obrigatorios = sum(i.preco_venda * i.quantidade for i in orcamento.itens if not i.opcional)
    obrigatorio = manutencao + itens_obrigatorios
    opcionais = sum(i.preco_venda * i.quantidade for i in orcamento.itens if i.opcional)
    subtotal_aprovado = manutencao + sum(i.preco_venda * i.quantidade for i in orcamento.itens if (not i.opcional) or i.aprovado)
    desconto_informado = max(float(orcamento.desconto or 0), 0)
    desconto_aplicado = min(desconto_informado, subtotal_aprovado)
    aprovado = max(subtotal_aprovado - desconto_aplicado, 0)
    geral_bruto = obrigatorio + opcionais
    geral = max(geral_bruto - min(desconto_informado, geral_bruto), 0)
    obrigatorio_final = max(obrigatorio - min(desconto_informado, obrigatorio), 0)
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
def manutencoes_lista(request: Request, status: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    query = db.query(Manutencao).options(selectinload(Manutencao.cliente), selectinload(Manutencao.equipamento))
    filtros = {
        "entrada": ["Aguardando equipamento"],
        "orcamento": ["Recebida", "Orçamento em elaboração"],
        "aceite": ["Aguardando aprovação"],
        "pagamento": ["Aprovado"],
        "producao": ["Em manutenção"],
        "agenda": ["Pronto para retirada"],
        "retirada": ["Retirada agendada"],
        "encerradas": ["Encerrada"],
    }
    if status in filtros:
        query = query.filter(Manutencao.status.in_(filtros[status]))
    lista = query.order_by(Manutencao.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/manutencoes.html", {"request": request, "usuario": usuario, "manutencoes": lista, "filtro_status": status})


@app.get("/organiza/manutencoes/nova", response_class=HTMLResponse)
def manutencao_nova(request: Request, cliente_id: int = 0, equipamento_id: int = 0, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    clientes = db.query(Cliente).options(selectinload(Cliente.equipamentos)).order_by(Cliente.nome).all()
    return templates.TemplateResponse("organiza/manutencao_form.html", {"request": request, "usuario": usuario, "clientes": clientes, "cliente_id": cliente_id, "equipamento_id": equipamento_id, "erro": ""})


@app.post("/organiza/manutencoes/nova")
async def manutencao_criar(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    form = dict(await request.form())
    cliente_id = int(form.get("cliente_id") or 0); equipamento_id = int(form.get("equipamento_id") or 0)
    eq = db.query(Equipamento).filter(Equipamento.id == equipamento_id, Equipamento.cliente_id == cliente_id).first()
    if not eq or not (form.get("defeito") or "").strip():
        return RedirectResponse(f"/organiza/manutencoes/nova?cliente_id={cliente_id}&equipamento_id={equipamento_id}", status_code=303)
    m = Manutencao(cliente_id=cliente_id, equipamento_id=equipamento_id, defeito=form.get("defeito").strip(), observacao=(form.get("observacao") or "").strip() or None, entrega_prevista_em=datetime_form(form.get("entrega_prevista_em") or ""), status="Aguardando equipamento")
    db.add(m); db.commit(); db.refresh(m)
    o = Orcamento(manutencao_id=m.id, versao=1, token=secrets.token_urlsafe(24), status="Rascunho")
    db.add(o); db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{m.id}", status_code=303)


@app.get("/organiza/manutencoes/{manutencao_id}", response_class=HTMLResponse)
def manutencao_detalhe(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id)
    if not m: raise HTTPException(404)
    itens = db.query(Item).filter(Item.ativo == 1).order_by(Item.nome).all()
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
        identificacao = equipamento.maquina or f"Equipamento #{equipamento.id}"
        descricao = f"{equipamento.tipo} {equipamento.modelo or ''}".strip()
        linhas_prontas.append(f"• {identificacao} — {descricao}")
    mensagem_retirada = (
        f"Olá, {m.cliente.nome}. Os equipamentos abaixo estão prontos para retirada:\n"
        + "\n".join(linhas_prontas)
        + f"\n\nEscolha a data e o horário neste link: {request.url.scheme}://{request.url.netloc}/retirada/{orcamento.token}"
        + "\n\nRetiradas de segunda a sexta-feira, somente das 14:00 às 17:00."
    ) if orcamento and prontas_cliente else ""
    return templates.TemplateResponse("organiza/manutencao_detalhe.html", {"request": request, "usuario": usuario, "m": m, "orcamento": orcamento, "itens_catalogo": itens, "totais": totais, "etapa_atual": etapa_manutencao(m), "manutencoes_prontas_cliente": prontas_cliente, "mensagem_retirada": mensagem_retirada})


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
    db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/orcamento/enviar")
def orcamento_enviar(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id); o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    if float(o.valor_manutencao or 0) <= 0: return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}?erro=Informe o valor obrigatório da manutenção", status_code=303)
    o.status = "Enviado"; m.status = "Aguardando aprovação"; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/aprovar-manual")
def aprovar_manual(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id); o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    for i in o.itens: i.aprovado = 1
    o.status = "Aprovado manualmente"; o.aprovado_em = datetime.now(); m.status = "Aprovado"; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/pagamento")
async def pagamento_registrar(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = carregar_manutencao(db, manutencao_id); form = dict(await request.form()); o = sorted(m.orcamentos, key=lambda x: x.versao)[-1]
    valor = moeda_num(form.get("valor"))
    if valor > 0:
        db.add(Pagamento(orcamento_id=o.id, data=data_form(form.get("data") or "") or date.today(), valor=valor, forma=(form.get("forma") or "PIX").strip(), observacao=(form.get("observacao") or "").strip() or None))
        m.status = "Em manutenção"; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/prazo")
async def prazo_salvar(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first(); form = dict(await request.form())
    m.prazo = (form.get("prazo") or "").strip() or None; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/pronto")
def manutencao_pronto(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first(); m.status = "Pronto para retirada"; m.pronto_em = datetime.now(); db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)


@app.post("/organiza/manutencoes/{manutencao_id}/retirada")
async def retirada_agendar(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first(); form = dict(await request.form())
    m.retirada_em = datetime_form(form.get("retirada_em") or "")
    if m.retirada_em and m.retirada_em.weekday() < 5 and time(14,0) <= m.retirada_em.time() <= time(17,0): m.status = "Retirada agendada"
    else: m.retirada_em = None
    db.commit(); return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)



@app.post("/organiza/manutencoes/{manutencao_id}/editar")
async def manutencao_editar(manutencao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m = db.query(Manutencao).filter(Manutencao.id == manutencao_id).first()
    if not m: raise HTTPException(404)
    form = dict(await request.form())
    m.defeito = (form.get("defeito") or "").strip()
    m.observacao = (form.get("observacao") or "").strip() or None
    m.diagnostico = (form.get("diagnostico") or "").strip() or None
    m.entrega_prevista_em = datetime_form(form.get("entrega_prevista_em") or "")
    db.commit(); return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

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
    form = dict(await request.form()); p.data = data_form(form.get("data") or "") or p.data; p.valor = moeda_num(form.get("valor")); p.forma=(form.get("forma") or "PIX").strip(); p.observacao=(form.get("observacao") or "").strip() or None
    db.commit(); return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

@app.post("/organiza/manutencoes/{manutencao_id}/pagamento/{pagamento_id}/excluir")
def pagamento_excluir(manutencao_id: int, pagamento_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    p=db.query(Pagamento).filter(Pagamento.id==pagamento_id).first()
    if p: db.delete(p); db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

@app.post("/organiza/manutencoes/{manutencao_id}/encerrar")
def manutencao_encerrar(manutencao_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    m=db.query(Manutencao).filter(Manutencao.id==manutencao_id).first()
    if not m or not m.retirada_em: raise HTTPException(400, "Agende a retirada antes de encerrar")
    m.entregue_em=datetime.now(); m.status="Encerrada"; db.commit()
    return RedirectResponse(f"/organiza/manutencoes/{manutencao_id}", status_code=303)

@app.get("/organiza/agenda", response_class=HTMLResponse)
def agenda(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    # A agenda é operacional: nunca exibe manutenção encerrada/cancelada
    # nem equipamento de venda já entregue ou apenas cadastrado como "Ativo".
    lista = (
        db.query(Manutencao)
        .options(selectinload(Manutencao.cliente), selectinload(Manutencao.equipamento))
        .filter(
            or_(Manutencao.entrega_prevista_em.isnot(None), Manutencao.retirada_em.isnot(None)),
            Manutencao.entregue_em.is_(None),
            ~Manutencao.status.in_(("Encerrada", "Cancelada")),
        )
        .order_by(Manutencao.entrega_prevista_em.asc(), Manutencao.retirada_em.asc())
        .all()
    )

    status_venda_pendentes = ("Solicitar gabinete", "Montagem", "Pronto para entrega")
    vendas = (
        db.query(Equipamento)
        .options(selectinload(Equipamento.cliente))
        .filter(
            Equipamento.previsao_entrega.isnot(None),
            Equipamento.status.in_(status_venda_pendentes),
        )
        .order_by(Equipamento.previsao_entrega.asc())
        .all()
    )

    return templates.TemplateResponse(
        "organiza/agenda.html",
        {"request": request, "usuario": usuario, "manutencoes": lista, "vendas": vendas},
    )

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

@app.get("/orcamento/{token}", response_class=HTMLResponse)
def orcamento_publico(token: str, request: Request, db: Session = Depends(get_db)):
    o = db.query(Orcamento).options(selectinload(Orcamento.itens), selectinload(Orcamento.manutencao).selectinload(Manutencao.cliente), selectinload(Orcamento.manutencao).selectinload(Manutencao.equipamento)).filter(Orcamento.token == token).first()
    if not o: raise HTTPException(404)
    return templates.TemplateResponse("organiza/orcamento_publico.html", {"request": request, "orcamento": o, "m": o.manutencao, "totais": totais_orcamento(o)})


@app.post("/orcamento/{token}/responder")
async def orcamento_responder(token: str, request: Request, db: Session = Depends(get_db)):
    o = db.query(Orcamento).options(selectinload(Orcamento.itens), selectinload(Orcamento.manutencao)).filter(Orcamento.token == token).first(); form = dict(await request.form())
    if not o: raise HTTPException(404)
    acao = form.get("acao")
    if acao == "cancelar":
        o.status = "Cancelado"; o.manutencao.status = "Cancelado"
    else:
        selecionados = {int(v) for k,v in form.items() if k.startswith("opcional_") and str(v).isdigit()}
        for i in o.itens:
            i.aprovado = 1 if (not i.opcional or acao == "aprovar" or i.id in selecionados) else 0
        o.status = "Aprovado" if acao == "aprovar" else "Aprovado parcialmente"
        o.aprovado_em = datetime.now(); o.manutencao.status = "Aprovado"
    db.commit(); return RedirectResponse(f"/orcamento/{token}?ok=1", status_code=303)

# -----------------------------------------------------------------------------
# Portal público simplificado para solicitação de manutenção
# -----------------------------------------------------------------------------
HORARIOS_ENTREGA_PUBLICA = ["14:00", "14:30", "15:00", "15:30", "16:00", "16:30", "17:00"]
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
    resultado = []
    for equipamento in sorted(cliente.equipamentos, key=lambda e: ((e.tipo or ""), (e.modelo or ""))):
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
        "horarios": HORARIOS_ENTREGA_PUBLICA,
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
    if not erro and hora_texto not in HORARIOS_ENTREGA_PUBLICA:
        erro = "Escolha um horário entre 14:00 e 17:00."
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
    criadas = []
    for equipamento in equipamentos_validos:
        manutencao = Manutencao(
            cliente_id=cliente.id,
            equipamento_id=equipamento.id,
            defeito=(form.get(f"descricao_{equipamento.id}") or "").strip(),
            entrega_prevista_em=entrega_em,
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
        "horarios": HORARIOS_ENTREGA_PUBLICA,
    })
