
import os
import secrets
import hashlib
import hmac
from datetime import date, datetime
from typing import Optional, List, Dict

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, Date, func, inspect, text, or_
from sqlalchemy.orm import Session, relationship

from config import CHAVE_SESSAO, ORGANIZA_VERSAO, ADMIN_NOME, ADMIN_SENHA
from database import Base, SessionLocal, engine, get_db


app = FastAPI(title="HUMIAT", version=ORGANIZA_VERSAO)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

STATUS_VALIDOS = ["Para Fazer", "Em andamento", "Pendente", "Aguardando Cliente", "Aguardando Compras", "Aguardando Financeiro", "Aguardando Externo", "Concluída", "Cancelada"]
DEPARTAMENTOS_CHAMADO = ["Cliente", "Compras", "Financeiro", "Externo"]
STATUS_CHAMADO = ["Aberto", "Em andamento", "Pedido realizado", "Previsão de entrega", "Produto recebido", "Concluído"]
MESES_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
DIAS_PT = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
RESPONSAVEIS = ["Junior", "Debora", "Luiz"]
DIAS_SEMANA = []  # legado interno: não aparece mais na tela


class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True)
    nome = Column(String(80), unique=True, nullable=False)
    telefone = Column(String(20), nullable=True)
    email = Column(String(140), nullable=True)
    cargo = Column(String(80), nullable=True)
    senha_hash = Column(String(255), nullable=False)
    is_admin = Column(Integer, nullable=False, default=0)
    pode_criar_tarefa = Column(Integer, nullable=False, default=1)
    pode_criar_projeto = Column(Integer, nullable=False, default=0)
    pode_criar_usuario = Column(Integer, nullable=False, default=0)
    pode_criar_etapa = Column(Integer, nullable=False, default=0)
    ativo = Column(Integer, nullable=False, default=1)
    departamentos = Column(String(200), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())


class Cliente(Base):
    __tablename__ = "clientes"
    id = Column(Integer, primary_key=True)
    nome = Column(String(140), nullable=False)
    telefone = Column(String(20), unique=True, nullable=False)
    empresa = Column(String(140), nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())


class Externo(Base):
    __tablename__ = "externos"
    id = Column(Integer, primary_key=True)
    nome = Column(String(140), nullable=False)
    telefone = Column(String(20), unique=True, nullable=False)
    empresa = Column(String(140), nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())


def limpar_telefone(valor: str) -> str:
    return "".join(ch for ch in (valor or "") if ch.isdigit())


def telefone_valido(valor: str) -> bool:
    numero = limpar_telefone(valor)
    return len(numero) in (10, 11) and numero[:2] != "00"


def formatar_telefone(valor: str) -> str:
    numero = limpar_telefone(valor)
    if len(numero) == 11:
        return f"({numero[:2]}) {numero[2:7]}-{numero[7:]}"
    if len(numero) == 10:
        return f"({numero[:2]}) {numero[2:6]}-{numero[6:]}"
    return valor or ""


templates.env.filters["telefone"] = formatar_telefone


def formatar_data_br(valor):
    if not valor:
        return "-"
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y")
    return valor.strftime("%d/%m/%Y")


templates.env.filters["data_br"] = formatar_data_br


class Projeto(Base):
    __tablename__ = "projetos"
    id = Column(Integer, primary_key=True)
    nome = Column(String(140), nullable=False)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    missoes = relationship("Missao", back_populates="projeto", cascade="all, delete-orphan")


class Missao(Base):
    """
    Mantive o nome interno Missao para não quebrar banco antigo.
    Na tela, este cadastro aparece como Departamento / Etapa.
    """
    __tablename__ = "missoes"
    id = Column(Integer, primary_key=True)
    projeto_id = Column(Integer, ForeignKey("projetos.id"), nullable=False)
    nome = Column(String(140), nullable=False)
    observacao = Column(Text, nullable=True)
    finalidade = Column(String(30), nullable=False, default="interna")
    criado_em = Column(DateTime, server_default=func.now())
    projeto = relationship("Projeto", back_populates="missoes")
    tarefas = relationship("Tarefa", back_populates="missao", cascade="all, delete-orphan")


class Tarefa(Base):
    __tablename__ = "tarefas"
    id = Column(Integer, primary_key=True)
    projeto_id = Column(Integer, ForeignKey("projetos.id"), nullable=False)
    missao_id = Column(Integer, ForeignKey("missoes.id"), nullable=False)
    descricao = Column(Text, nullable=False)  # Na tela este campo aparece como Tarefa.
    responsavel = Column(String(80), nullable=False)
    status = Column(String(30), nullable=False, default="Pendente")
    ordem = Column(Integer, nullable=False, default=0)
    # Campos mantidos apenas para compatibilidade com bancos antigos.
    # Não aparecem mais nas telas e não participam do fluxo atual.
    tipo = Column(String(20), nullable=False, default="unica")
    dias_semana = Column(String(30), nullable=True)
    dependencia_id = Column(Integer, ForeignKey("tarefas.id"), nullable=True)
    observacao = Column(Text, nullable=True)  # Na tela aparece como Descrição opcional.
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    projeto = relationship("Projeto")
    cliente = relationship("Cliente")
    missao = relationship("Missao", back_populates="tarefas", foreign_keys=[missao_id])
    dependencia = relationship("Tarefa", remote_side=[id])
    chamados = relationship("Chamado", back_populates="tarefa", cascade="all, delete-orphan", foreign_keys="Chamado.tarefa_id")
    historicos = relationship("Historico", back_populates="tarefa", cascade="all, delete-orphan", order_by="Historico.criado_em.desc()")
    anexos = relationship("Anexo", back_populates="tarefa", cascade="all, delete-orphan")
    mensagens = relationship("Mensagem", back_populates="tarefa", cascade="all, delete-orphan")




class Historico(Base):
    __tablename__ = "historicos"
    id = Column(Integer, primary_key=True)
    tarefa_id = Column(Integer, ForeignKey("tarefas.id"), nullable=False)
    tipo = Column(String(40), nullable=False, default="evento")
    titulo = Column(String(160), nullable=False)
    descricao = Column(Text, nullable=True)
    usuario = Column(String(80), nullable=True)
    origem = Column(String(40), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    tarefa = relationship("Tarefa", back_populates="historicos")


class Anexo(Base):
    __tablename__ = "anexos"
    id = Column(Integer, primary_key=True)
    tarefa_id = Column(Integer, ForeignKey("tarefas.id"), nullable=False)
    nome_arquivo = Column(String(255), nullable=False)
    caminho = Column(String(500), nullable=False)
    tipo = Column(String(80), nullable=True)
    observacao = Column(Text, nullable=True)
    criado_por = Column(String(80), nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    tarefa = relationship("Tarefa", back_populates="anexos")


class Mensagem(Base):
    __tablename__ = "mensagens"
    id = Column(Integer, primary_key=True)
    tarefa_id = Column(Integer, ForeignKey("tarefas.id"), nullable=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=True)
    canal = Column(String(30), nullable=False, default="WhatsApp")
    direcao = Column(String(20), nullable=False, default="saida")
    texto = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="rascunho")
    enviado_por = Column(String(80), nullable=True)
    enviado_em = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    tarefa = relationship("Tarefa", back_populates="mensagens")
    cliente = relationship("Cliente")

class Chamado(Base):
    __tablename__ = "chamados"
    id = Column(Integer, primary_key=True)
    tarefa_id = Column(Integer, ForeignKey("tarefas.id"), nullable=False)
    departamento = Column(String(30), nullable=False)
    descricao = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="Aberto")
    criado_por = Column(String(80), nullable=True)
    finalizado_por = Column(String(80), nullable=True)
    fornecedor = Column(String(140), nullable=True)
    valor = Column(String(40), nullable=True)
    previsao_entrega = Column(Date, nullable=True)
    data_recebimento = Column(Date, nullable=True)
    externo_id = Column(Integer, ForeignKey("externos.id"), nullable=True)
    data_envio = Column(Date, nullable=True)
    data_devolucao = Column(Date, nullable=True)
    observacao = Column(Text, nullable=True)
    criado_em = Column(DateTime, server_default=func.now())
    concluido_em = Column(DateTime, nullable=True)
    tarefa = relationship("Tarefa", back_populates="chamados", foreign_keys=[tarefa_id])
    externo = relationship("Externo")


def gerar_hash_senha(senha: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    chave = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"{salt}${chave.hex()}"


def verificar_senha(senha: str, senha_hash: str) -> bool:
    try:
        salt, hash_salvo = senha_hash.split("$", 1)
    except ValueError:
        return False
    hash_digitado = gerar_hash_senha(senha, salt).split("$", 1)[1]
    return hmac.compare_digest(hash_digitado, hash_salvo)


def criar_assinatura(valor: str) -> str:
    return hmac.new(CHAVE_SESSAO.encode("utf-8"), valor.encode("utf-8"), hashlib.sha256).hexdigest()


def criar_cookie_sessao(nome_usuario: str) -> str:
    return f"{nome_usuario}|{criar_assinatura(nome_usuario)}"


def ler_usuario_cookie(request: Request) -> Optional[str]:
    cookie = request.cookies.get("organiza_sessao")
    if not cookie or "|" not in cookie:
        return None
    nome, assinatura = cookie.rsplit("|", 1)
    if hmac.compare_digest(assinatura, criar_assinatura(nome)):
        return nome
    return None


def usuario_logado(request: Request, db: Session = Depends(get_db)) -> Usuario:
    nome = ler_usuario_cookie(request)
    if not nome:
        raise HTTPException(status_code=307, headers={"Location": "/area-restrita/login"})
    usuario = db.query(Usuario).filter(Usuario.nome == nome).first()
    if not usuario or not usuario.ativo:
        raise HTTPException(status_code=307, headers={"Location": "/area-restrita/login"})
    return usuario


def exigir_admin(usuario: Usuario):
    if not usuario.is_admin:
        raise HTTPException(status_code=307, headers={"Location": "/organiza/acesso-negado"})


def tem_permissao(usuario: Usuario, permissao: str) -> bool:
    return bool(usuario and (usuario.is_admin or getattr(usuario, permissao, 0)))


def exigir_permissao(usuario: Usuario, permissao: str):
    if tem_permissao(usuario, permissao):
        return
    raise HTTPException(status_code=307, headers={"Location": "/organiza/acesso-negado"})





def data_hora_atual():
    agora = datetime.now()
    return {
        "data_extenso": f"{DIAS_PT[agora.weekday()]}, {agora.day:02d} de {MESES_PT[agora.month - 1]} de {agora.year}",
        "hora": agora.strftime("%H:%M"),
    }

templates.env.globals["data_hora_atual"] = data_hora_atual
templates.env.globals["tem_permissao"] = tem_permissao

def ordenar_tarefas_query(query):
    return query.order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc())


def normalizar_ordem_tarefas(db: Session, responsavel: Optional[str] = None, missao_id: Optional[int] = None):
    query = db.query(Tarefa)
    if responsavel:
        query = query.filter(Tarefa.responsavel == responsavel)
    if missao_id:
        query = query.filter(Tarefa.missao_id == missao_id)
    tarefas = query.order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc()).all()
    for indice, tarefa in enumerate(tarefas, start=1):
        tarefa.ordem = indice
    db.flush()


def trocar_ordem_tarefa(db: Session, tarefa: Tarefa, direcao: str):
    normalizar_ordem_tarefas(db, responsavel=tarefa.responsavel)
    tarefas = db.query(Tarefa).filter(Tarefa.responsavel == tarefa.responsavel).order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc()).all()
    pos = next((i for i, item in enumerate(tarefas) if item.id == tarefa.id), None)
    if pos is None:
        return
    if direcao == "subir" and pos > 0:
        tarefas[pos].ordem, tarefas[pos - 1].ordem = tarefas[pos - 1].ordem, tarefas[pos].ordem
    elif direcao == "descer" and pos < len(tarefas) - 1:
        tarefas[pos].ordem, tarefas[pos + 1].ordem = tarefas[pos + 1].ordem, tarefas[pos].ordem
    elif direcao == "topo":
        alvo = tarefas.pop(pos)
        tarefas.insert(0, alvo)
        for indice, item in enumerate(tarefas, start=1):
            item.ordem = indice
    elif direcao == "final":
        alvo = tarefas.pop(pos)
        tarefas.append(alvo)
        for indice, item in enumerate(tarefas, start=1):
            item.ordem = indice



def responsaveis_db(db: Session) -> List[str]:
    nomes = [u.nome for u in db.query(Usuario).filter(Usuario.ativo == 1).order_by(Usuario.nome).all()]
    return nomes or RESPONSAVEIS


def tarefa_aparece_hoje(tarefa: Tarefa) -> bool:
    if tarefa.status in {"Concluída", "Cancelada", "Entregue"}:
        return False
    return True


def calcular_percentual_projeto(projeto: Projeto) -> int:
    tarefas = [t for etapa in projeto.missoes for t in etapa.tarefas]
    if not tarefas:
        return 0
    entregues = len([t for t in tarefas if t.status in {"Concluída", "Entregue"}])
    return round((entregues / len(tarefas)) * 100)


def agrupar_por_projeto(tarefas: List[Tarefa]) -> Dict[str, List[Tarefa]]:
    grupos: Dict[str, List[Tarefa]] = {}
    for tarefa in tarefas:
        grupos.setdefault(tarefa.projeto.nome, []).append(tarefa)
    return grupos


def departamentos_usuario(usuario: Usuario) -> List[str]:
    return [d.strip() for d in (usuario.departamentos or "").split(",") if d.strip()]


def usuario_tem_departamento(usuario: Usuario, departamento: str) -> bool:
    return bool(usuario.is_admin or departamento in departamentos_usuario(usuario))


STATUS_DEPARTAMENTO_CHAMADO = {
    "Aguardando Cliente": "Cliente",
    "Aguardando Compras": "Compras",
    "Aguardando Financeiro": "Financeiro",
    "Aguardando Externo": "Externo",
}


def status_aguardando_departamento(departamento: str) -> str:
    return f"Aguardando {departamento}"


def departamento_por_status(status: str) -> Optional[str]:
    return STATUS_DEPARTAMENTO_CHAMADO.get(status)


def validar_acao_para_chamado(status: str, acao_chamado: str):
    departamento = departamento_por_status(status)
    if departamento and not (acao_chamado or "").strip():
        raise HTTPException(
            status_code=400,
            detail=f"Para enviar a tarefa para {status}, preencha a Ação do chamado de {departamento}."
        )


def criar_chamado_se_necessario(db: Session, tarefa: Tarefa, status_anterior: Optional[str], usuario: Usuario, acao_chamado: str = ""):
    departamento = departamento_por_status(tarefa.status)
    if not departamento or tarefa.status == status_anterior:
        return

    descricao_chamado = (acao_chamado or "").strip()
    if not descricao_chamado:
        raise HTTPException(
            status_code=400,
            detail=f"A ação do chamado de {departamento} é obrigatória."
        )

    aberto = db.query(Chamado).filter(
        Chamado.tarefa_id == tarefa.id,
        Chamado.departamento == departamento,
        Chamado.status != "Concluído"
    ).first()
    if aberto:
        return
    db.add(Chamado(
        tarefa_id=tarefa.id,
        departamento=departamento,
        descricao=descricao_chamado,
        criado_por=usuario.nome,
        status="Aberto"
    ))
    registrar_historico(db, tarefa.id, f"Chamado aberto para {departamento}", descricao_chamado, usuario, tipo="chamado")



def registrar_historico(db: Session, tarefa_id: int, titulo: str, descricao: str = "", usuario: Optional[Usuario] = None, tipo: str = "evento", origem: str = "Organiza"):
    db.add(Historico(
        tarefa_id=tarefa_id,
        tipo=tipo,
        titulo=titulo.strip(),
        descricao=(descricao or "").strip() or None,
        usuario=usuario.nome if usuario else None,
        origem=origem,
    ))


def status_tarefa_aberta(status: str) -> bool:
    return status not in {"Concluída", "Cancelada", "Entregue"}

def parse_data(valor: str):
    valor = (valor or "").strip()
    if not valor:
        return None
    return datetime.strptime(valor, "%Y-%m-%d").date()


@app.on_event("startup")
def iniciar_banco():
    Base.metadata.create_all(bind=engine)
    # Atualização simples para bancos SQLite já existentes.
    insp = inspect(engine)
    with engine.begin() as conn:
        if "missoes" in insp.get_table_names():
            colunas_missoes = [c["name"] for c in insp.get_columns("missoes")]
            if "finalidade" not in colunas_missoes:
                conn.execute(text("ALTER TABLE missoes ADD COLUMN finalidade VARCHAR(30) NOT NULL DEFAULT 'interna'"))
        if "usuarios" in insp.get_table_names():
            colunas_usuarios = [c["name"] for c in insp.get_columns("usuarios")]
            novas_colunas = {
                "telefone": "VARCHAR(20)",
                "email": "VARCHAR(140)",
                "cargo": "VARCHAR(80)",
                "is_admin": "INTEGER NOT NULL DEFAULT 0",
                "pode_criar_tarefa": "INTEGER NOT NULL DEFAULT 1",
                "pode_criar_projeto": "INTEGER NOT NULL DEFAULT 0",
                "pode_criar_usuario": "INTEGER NOT NULL DEFAULT 0",
                "pode_criar_etapa": "INTEGER NOT NULL DEFAULT 0",
                "ativo": "INTEGER NOT NULL DEFAULT 1",
                "departamentos": "VARCHAR(200)",
            }
            for coluna, tipo_sql in novas_colunas.items():
                if coluna not in colunas_usuarios:
                    conn.execute(text(f"ALTER TABLE usuarios ADD COLUMN {coluna} {tipo_sql}"))
        if "tarefas" in insp.get_table_names():
            colunas_tarefas = [c["name"] for c in insp.get_columns("tarefas")]
            if "cliente_id" not in colunas_tarefas:
                conn.execute(text("ALTER TABLE tarefas ADD COLUMN cliente_id INTEGER"))
            if "ordem" not in colunas_tarefas:
                conn.execute(text("ALTER TABLE tarefas ADD COLUMN ordem INTEGER NOT NULL DEFAULT 0"))
            conn.execute(text("UPDATE tarefas SET status = 'Para Fazer' WHERE status = 'Aberta'"))
            conn.execute(text("UPDATE tarefas SET status = 'Aguardando Cliente' WHERE status = 'Aguardando'"))
            conn.execute(text("UPDATE tarefas SET status = 'Concluída' WHERE status IN ('Pronta', 'Entregue')"))
    db = SessionLocal()
    try:
        # Banco novo em produção:
        # cria somente 1 administrador inicial.
        # Depois, os demais usuários devem ser criados pela tela de Usuários.
        admin_nome = ADMIN_NOME
        admin_senha = ADMIN_SENHA

        total_usuarios = db.query(Usuario).count()
        if total_usuarios == 0:
            db.add(Usuario(
                nome=admin_nome,
                senha_hash=gerar_hash_senha(admin_senha),
                cargo="Administrador",
                is_admin=1,
                pode_criar_tarefa=1,
                pode_criar_projeto=1,
                pode_criar_usuario=1,
                pode_criar_etapa=1,
                ativo=1,
                departamentos=",".join(DEPARTAMENTOS_CHAMADO),
            ))
        else:
            admin = db.query(Usuario).filter(Usuario.nome == admin_nome).first()
            if admin:
                admin.is_admin = 1
                admin.pode_criar_tarefa = 1
                admin.pode_criar_projeto = 1
                admin.pode_criar_usuario = 1
                admin.pode_criar_etapa = 1
                admin.ativo = 1

        db.commit()
    finally:
        db.close()


@app.get("/", response_class=HTMLResponse)
def pagina_inicial(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/saude")
def saude():
    return {"ok": True, "projeto": "HUMIAT", "modulo": "Organiza", "versao": ORGANIZA_VERSAO}


@app.get("/area-restrita/login", response_class=HTMLResponse)
def login(request: Request, erro: str = ""):
    return templates.TemplateResponse("organiza/login.html", {"request": request, "erro": erro})


@app.post("/area-restrita/login")
def entrar(usuario: str = Form(...), senha: str = Form(...), db: Session = Depends(get_db)):
    encontrado = db.query(Usuario).filter(Usuario.nome == usuario.strip()).first()
    if not encontrado or not verificar_senha(senha, encontrado.senha_hash):
        return RedirectResponse("/area-restrita/login?erro=Usuário ou senha inválidos", status_code=303)
    resposta = RedirectResponse("/organiza", status_code=303)
    resposta.set_cookie("organiza_sessao", criar_cookie_sessao(encontrado.nome), httponly=True, samesite="lax")
    return resposta


@app.get("/area-restrita/sair")
def sair():
    resposta = RedirectResponse("/area-restrita/login", status_code=303)
    resposta.delete_cookie("organiza_sessao")
    return resposta


@app.get("/organiza/acesso-negado", response_class=HTMLResponse)
def acesso_negado(request: Request, usuario: Usuario = Depends(usuario_logado)):
    return templates.TemplateResponse("organiza/acesso_negado.html", {"request": request, "usuario": usuario})


@app.get("/organiza", response_class=HTMLResponse)
def painel(request: Request, responsavel: Optional[str] = None, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefas = db.query(Tarefa).order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc()).all()
    lista_responsaveis = responsaveis_db(db)
    responsavel_painel = responsavel if responsavel in lista_responsaveis else usuario.nome

    tarefas_do_responsavel = [t for t in tarefas if t.responsavel == responsavel_painel and tarefa_aparece_hoje(t)]
    tarefas_hoje_usuario = [t for t in tarefas_do_responsavel if t.status in ["Para Fazer", "Em andamento", "Pendente"]]
    aguardando_cliente = [t for t in tarefas_do_responsavel if t.status == "Aguardando Cliente"]
    aguardando_compras = [t for t in tarefas_do_responsavel if t.status == "Aguardando Compras"]
    aguardando_financeiro = [t for t in tarefas_do_responsavel if t.status == "Aguardando Financeiro"]
    aguardando_externo = [t for t in tarefas_do_responsavel if t.status == "Aguardando Externo"]

    deps_usuario = departamentos_usuario(usuario)
    chamados_query = db.query(Chamado).filter(Chamado.status != "Concluído")
    if not usuario.is_admin:
        if deps_usuario:
            chamados_query = chamados_query.filter(Chamado.departamento.in_(deps_usuario))
        else:
            chamados_query = chamados_query.filter(Chamado.id == -1)
    chamados_visiveis = chamados_query.order_by(Chamado.criado_em.desc()).all()

    hoje = date.today()
    chamados_atrasados = [
        c for c in chamados_visiveis
        if (c.departamento == "Compras" and c.previsao_entrega and c.previsao_entrega < hoje and not c.data_recebimento)
        or (c.departamento == "Externo" and c.data_devolucao and c.data_devolucao < hoje)
    ]

    resumo = {status: len([t for t in tarefas if t.responsavel == responsavel_painel and t.status == status]) for status in STATUS_VALIDOS}
    resumo_chamados = {dep: len([c for c in chamados_visiveis if c.departamento == dep]) for dep in DEPARTAMENTOS_CHAMADO}
    por_responsavel = {nome: len([t for t in tarefas if t.responsavel == nome and tarefa_aparece_hoje(t)]) for nome in lista_responsaveis}
    projetos = db.query(Projeto).order_by(Projeto.criado_em.desc()).all()
    percentuais = {p.id: calcular_percentual_projeto(p) for p in projetos}

    return templates.TemplateResponse("organiza/painel.html", {
        "request": request, "usuario": usuario, "responsavel_painel": responsavel_painel,
        "tarefas_hoje_usuario": tarefas_hoje_usuario, "tarefas_do_responsavel": tarefas_do_responsavel,
        "aguardando_cliente": aguardando_cliente, "aguardando_compras": aguardando_compras,
        "aguardando_financeiro": aguardando_financeiro, "aguardando_externo": aguardando_externo,
        "chamados_visiveis": chamados_visiveis, "chamados_atrasados": chamados_atrasados,
        "resumo_chamados": resumo_chamados, "departamentos_usuario": deps_usuario,
        "resumo": resumo, "por_responsavel": por_responsavel, "projetos": projetos, "percentuais": percentuais,
        "status_validos": STATUS_VALIDOS, "responsaveis": lista_responsaveis, "agora": data_hora_atual(),
    })

@app.get("/organiza/detalhes/{tipo}/{valor}", response_class=HTMLResponse)
def detalhes(tipo: str, valor: str, request: Request, responsavel: Optional[str] = None, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefas = db.query(Tarefa).order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc()).all()
    titulo = "Tarefas"
    if tipo == "responsavel":
        tarefas = [t for t in tarefas if t.responsavel == valor and tarefa_aparece_hoje(t)]
        titulo = f"Tarefas de {valor}"
    elif tipo == "status":
        tarefas = [t for t in tarefas if t.status == valor]
        if responsavel in responsaveis_db(db):
            tarefas = [t for t in tarefas if t.responsavel == responsavel]
            titulo = f"{valor} de {responsavel}"
        else:
            titulo = f"Tarefas com status: {valor}"
    elif tipo == "hoje":
        tarefas = [t for t in tarefas if tarefa_aparece_hoje(t)]
        titulo = "Tarefas de hoje"
    else:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/detalhes.html", {
        "request": request, "usuario": usuario, "titulo": titulo, "grupos": agrupar_por_projeto(tarefas),
        "status_validos": STATUS_VALIDOS, "tipo": tipo, "valor": valor
    })


@app.get("/organiza/projetos/novo", response_class=HTMLResponse)
def novo_projeto(request: Request, usuario: Usuario = Depends(usuario_logado)):
    exigir_permissao(usuario, "pode_criar_projeto")
    return templates.TemplateResponse("organiza/projeto_form.html", {"request": request, "usuario": usuario, "projeto": None})


@app.post("/organiza/projetos/novo")
def criar_projeto(nome: str = Form(...), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_projeto")
    db.add(Projeto(nome=nome.strip(), observacao=observacao.strip() or None))
    db.commit()
    return RedirectResponse("/organiza/projetos", status_code=303)


@app.get("/organiza/projetos/{projeto_id}/editar", response_class=HTMLResponse)
def editar_projeto(projeto_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_projeto")
    projeto = db.query(Projeto).filter(Projeto.id == projeto_id).first()
    if not projeto: raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/projeto_form.html", {"request": request, "usuario": usuario, "projeto": projeto})


@app.post("/organiza/projetos/{projeto_id}/editar")
def salvar_projeto(projeto_id: int, nome: str = Form(...), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_projeto")
    projeto = db.query(Projeto).filter(Projeto.id == projeto_id).first()
    if not projeto: raise HTTPException(status_code=404)
    projeto.nome = nome.strip()
    projeto.observacao = observacao.strip() or None
    db.commit()
    return RedirectResponse(f"/organiza/projetos/{projeto_id}", status_code=303)


@app.get("/organiza/projetos/{projeto_id}", response_class=HTMLResponse)
def ver_projeto(projeto_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    projeto = db.query(Projeto).filter(Projeto.id == projeto_id).first()
    if not projeto: raise HTTPException(status_code=404)
    tarefas = db.query(Tarefa).filter(Tarefa.projeto_id == projeto_id).order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc()).all()
    percentual = calcular_percentual_projeto(projeto)
    return templates.TemplateResponse("organiza/projeto.html", {
        "request": request, "usuario": usuario, "projeto": projeto, "tarefas": tarefas,
        "percentual": percentual, "status_validos": STATUS_VALIDOS
    })


@app.get("/organiza/projetos/{projeto_id}/missoes/nova", response_class=HTMLResponse)
def nova_missao(projeto_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_etapa")
    projeto = db.query(Projeto).filter(Projeto.id == projeto_id).first()
    if not projeto: raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/missao_form.html", {"request": request, "usuario": usuario, "projeto": projeto, "missao": None})


@app.post("/organiza/projetos/{projeto_id}/missoes/nova")
def criar_missao(projeto_id: int, nome: str = Form(...), finalidade: str = Form("interna"), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_etapa")
    finalidade = finalidade if finalidade in ["interna", "cliente"] else "interna"
    db.add(Missao(projeto_id=projeto_id, nome=nome.strip(), finalidade=finalidade, observacao=observacao.strip() or None))
    db.commit()
    return RedirectResponse(f"/organiza/projetos/{projeto_id}", status_code=303)


@app.get("/organiza/projetos/{projeto_id}/missoes/{missao_id}/editar", response_class=HTMLResponse)
def editar_missao(projeto_id: int, missao_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_etapa")
    projeto = db.query(Projeto).filter(Projeto.id == projeto_id).first()
    missao = db.query(Missao).filter(Missao.id == missao_id, Missao.projeto_id == projeto_id).first()
    if not projeto or not missao: raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/missao_form.html", {"request": request, "usuario": usuario, "projeto": projeto, "missao": missao})


@app.post("/organiza/projetos/{projeto_id}/missoes/{missao_id}/editar")
def salvar_missao(projeto_id: int, missao_id: int, nome: str = Form(...), finalidade: str = Form("interna"), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_etapa")
    missao = db.query(Missao).filter(Missao.id == missao_id, Missao.projeto_id == projeto_id).first()
    if not missao: raise HTTPException(status_code=404)
    missao.nome = nome.strip()
    missao.finalidade = finalidade if finalidade in ["interna", "cliente"] else "interna"
    missao.observacao = observacao.strip() or None
    db.commit()
    return RedirectResponse(f"/organiza/projetos/{projeto_id}", status_code=303)


@app.get("/organiza/tarefas/nova", response_class=HTMLResponse)
def nova_tarefa(request: Request, projeto_id: Optional[int] = None, missao_id: Optional[int] = None, dependencia_id: Optional[int] = None, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_tarefa")
    projetos = db.query(Projeto).order_by(Projeto.nome).all()
    missoes = db.query(Missao).order_by(Missao.nome).all()
    tarefas = db.query(Tarefa).order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc()).all()
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    return templates.TemplateResponse("organiza/tarefa_form.html", {
        "request": request, "usuario": usuario, "projetos": projetos, "missoes": missoes, "tarefa": None,
        "tarefas": tarefas, "responsaveis": responsaveis_db(db), "status_validos": STATUS_VALIDOS,
        "projeto_id": projeto_id, "missao_id": missao_id, "dependencia_id": dependencia_id, "clientes": clientes
    })


@app.post("/organiza/tarefas/nova")
def criar_tarefa(projeto_id: int = Form(...), missao_id: int = Form(...), descricao: str = Form(...), responsavel: str = Form(...), status: str = Form("Pendente"), acao_chamado: str = Form(""), dependencia_id: str = Form(""), cliente_id: str = Form(""), cliente_nome: str = Form(""), cliente_telefone: str = Form(""), cliente_telefone_manual: str = Form(""), cliente_empresa: str = Form(""), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_tarefa")
    missao = db.query(Missao).filter(Missao.id == missao_id, Missao.projeto_id == projeto_id).first()
    if not missao:
        raise HTTPException(status_code=400, detail="Departamento / Etapa não pertence ao projeto selecionado.")
    dependencia = None
    if dependencia_id:
        dep = db.query(Tarefa).filter(Tarefa.id == int(dependencia_id), Tarefa.projeto_id == projeto_id).first()
        dependencia = dep.id if dep else None
    dias = None
    cliente_final = None
    if missao.finalidade == "cliente":
        if cliente_id:
            cliente = db.query(Cliente).filter(Cliente.id == int(cliente_id)).first()
            cliente_final = cliente.id if cliente else None
        if not cliente_final:
            telefone = limpar_telefone(cliente_telefone or cliente_telefone_manual)
            if not telefone_valido(telefone):
                raise HTTPException(status_code=400, detail="Telefone do cliente inválido.")
            cliente = db.query(Cliente).filter(Cliente.telefone == telefone).first()
            if not cliente:
                if not cliente_nome.strip():
                    raise HTTPException(status_code=400, detail="Informe o nome do cliente.")
                cliente = Cliente(nome=cliente_nome.strip(), telefone=telefone, empresa=cliente_empresa.strip() or None)
                db.add(cliente)
                db.flush()
            cliente_final = cliente.id
    validar_acao_para_chamado(status, acao_chamado)
    normalizar_ordem_tarefas(db, responsavel=responsavel)
    proxima_ordem = (db.query(func.max(Tarefa.ordem)).filter(Tarefa.responsavel == responsavel).scalar() or 0) + 1
    nova = Tarefa(projeto_id=projeto_id, missao_id=missao_id, descricao=descricao.strip(), responsavel=responsavel, status=status, ordem=proxima_ordem, tipo="unica", dias_semana=dias, dependencia_id=dependencia, cliente_id=cliente_final, observacao=observacao.strip() or None)
    db.add(nova)
    db.flush()
    registrar_historico(db, nova.id, "Tarefa criada", nova.descricao, usuario, tipo="tarefa")
    criar_chamado_se_necessario(db, nova, None, usuario, acao_chamado)
    db.commit()
    return RedirectResponse("/organiza", status_code=303)


@app.get("/organiza/tarefas/{tarefa_id}/editar", response_class=HTMLResponse)
def editar_tarefa(tarefa_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa: raise HTTPException(status_code=404)
    projetos = db.query(Projeto).order_by(Projeto.nome).all()
    missoes = db.query(Missao).order_by(Missao.nome).all()
    tarefas = db.query(Tarefa).filter(Tarefa.id != tarefa_id).order_by(Tarefa.ordem.asc(), Tarefa.criado_em.asc(), Tarefa.id.asc()).all()
    clientes = db.query(Cliente).order_by(Cliente.nome).all()
    historicos = db.query(Historico).filter(Historico.tarefa_id == tarefa.id).order_by(Historico.criado_em.desc()).all()
    chamados = db.query(Chamado).filter(Chamado.tarefa_id == tarefa.id).order_by(Chamado.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/tarefa_form.html", {
        "request": request, "usuario": usuario, "projetos": projetos, "missoes": missoes, "tarefa": tarefa,
        "tarefas": tarefas, "responsaveis": responsaveis_db(db), "status_validos": STATUS_VALIDOS,
        "projeto_id": tarefa.projeto_id, "missao_id": tarefa.missao_id, "dependencia_id": tarefa.dependencia_id, "clientes": clientes,
        "historicos": historicos, "chamados_tarefa": chamados, "departamentos_chamado": DEPARTAMENTOS_CHAMADO
    })


@app.post("/organiza/tarefas/{tarefa_id}/editar")
def salvar_tarefa(tarefa_id: int, projeto_id: int = Form(...), missao_id: int = Form(...), descricao: str = Form(...), responsavel: str = Form(...), status: str = Form("Pendente"), acao_chamado: str = Form(""), dependencia_id: str = Form(""), cliente_id: str = Form(""), cliente_nome: str = Form(""), cliente_telefone: str = Form(""), cliente_telefone_manual: str = Form(""), cliente_empresa: str = Form(""), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa: raise HTTPException(status_code=404)
    missao = db.query(Missao).filter(Missao.id == missao_id, Missao.projeto_id == projeto_id).first()
    if not missao:
        raise HTTPException(status_code=400, detail="Departamento / Etapa não pertence ao projeto selecionado.")
    dependencia = None
    if dependencia_id:
        dep = db.query(Tarefa).filter(Tarefa.id == int(dependencia_id), Tarefa.projeto_id == projeto_id, Tarefa.id != tarefa_id).first()
        dependencia = dep.id if dep else None
    status_anterior = tarefa.status
    if status_anterior != status:
        validar_acao_para_chamado(status, acao_chamado)
    tarefa.projeto_id = projeto_id
    tarefa.missao_id = missao_id
    tarefa.descricao = descricao.strip()
    tarefa.responsavel = responsavel
    tarefa.status = status
    tarefa.tipo = "unica"
    tarefa.dias_semana = None
    tarefa.dependencia_id = dependencia
    tarefa.cliente_id = None
    if missao.finalidade == "cliente":
        if cliente_id:
            cliente = db.query(Cliente).filter(Cliente.id == int(cliente_id)).first()
            tarefa.cliente_id = cliente.id if cliente else None
        if not tarefa.cliente_id:
            telefone = limpar_telefone(cliente_telefone or cliente_telefone_manual)
            if not telefone_valido(telefone):
                raise HTTPException(status_code=400, detail="Telefone do cliente inválido.")
            cliente = db.query(Cliente).filter(Cliente.telefone == telefone).first()
            if not cliente:
                if not cliente_nome.strip():
                    raise HTTPException(status_code=400, detail="Informe o nome do cliente.")
                cliente = Cliente(nome=cliente_nome.strip(), telefone=telefone, empresa=cliente_empresa.strip() or None)
                db.add(cliente)
                db.flush()
            tarefa.cliente_id = cliente.id
    tarefa.observacao = observacao.strip() or None
    if status_anterior != tarefa.status:
        registrar_historico(db, tarefa.id, f"Status alterado para {tarefa.status}", f"Antes: {status_anterior}", usuario, tipo="status")
    criar_chamado_se_necessario(db, tarefa, status_anterior, usuario, acao_chamado)
    db.commit()
    return RedirectResponse("/organiza", status_code=303)



@app.get("/organiza/projetos", response_class=HTMLResponse)
def listar_projetos(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    projetos = db.query(Projeto).order_by(Projeto.nome).all()
    percentuais = {p.id: calcular_percentual_projeto(p) for p in projetos}
    return templates.TemplateResponse("organiza/projetos.html", {"request": request, "usuario": usuario, "projetos": projetos, "percentuais": percentuais})


@app.get("/organiza/etapas", response_class=HTMLResponse)
def listar_etapas(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    projetos = db.query(Projeto).order_by(Projeto.nome).all()
    return templates.TemplateResponse("organiza/etapas.html", {"request": request, "usuario": usuario, "projetos": projetos})


@app.get("/organiza/etapas/nova", response_class=HTMLResponse)
def escolher_projeto_etapa(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_etapa")
    projetos = db.query(Projeto).order_by(Projeto.nome).all()
    return templates.TemplateResponse("organiza/etapa_escolher_projeto.html", {"request": request, "usuario": usuario, "projetos": projetos})


@app.get("/organiza/usuarios", response_class=HTMLResponse)
def listar_usuarios(request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_usuario")
    usuarios = db.query(Usuario).order_by(Usuario.nome).all()
    return templates.TemplateResponse("organiza/usuarios.html", {"request": request, "usuario": usuario, "usuarios": usuarios})


@app.get("/organiza/usuarios/novo", response_class=HTMLResponse)
def novo_usuario(request: Request, usuario: Usuario = Depends(usuario_logado)):
    exigir_permissao(usuario, "pode_criar_usuario")
    return templates.TemplateResponse("organiza/usuario_form.html", {"request": request, "usuario": usuario, "editado": None})


@app.post("/organiza/usuarios/novo")
def criar_usuario(nome: str = Form(...), telefone: str = Form(...), senha: str = Form(...), email: str = Form(""), cargo: str = Form(""), is_admin: str = Form(""), pode_criar_tarefa: str = Form(""), pode_criar_projeto: str = Form(""), pode_criar_usuario: str = Form(""), pode_criar_etapa: str = Form(""), ativo: str = Form("1"), departamentos: Optional[List[str]] = Form(None), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_usuario")
    numero = limpar_telefone(telefone)
    if not telefone_valido(numero):
        raise HTTPException(status_code=400, detail="Telefone do usuário inválido. Use DDD + número.")
    if db.query(Usuario).filter(Usuario.nome == nome.strip()).first():
        raise HTTPException(status_code=400, detail="Já existe usuário com este nome.")
    admin = 1 if is_admin else 0
    db.add(Usuario(nome=nome.strip(), telefone=numero, email=email.strip() or None, cargo=cargo.strip() or None, senha_hash=gerar_hash_senha(senha), is_admin=admin, pode_criar_tarefa=1 if (pode_criar_tarefa or admin) else 0, pode_criar_projeto=1 if (pode_criar_projeto or admin) else 0, pode_criar_usuario=1 if (pode_criar_usuario or admin) else 0, pode_criar_etapa=1 if (pode_criar_etapa or admin) else 0, ativo=1 if ativo else 0, departamentos=",".join(departamentos or [])))
    db.commit()
    return RedirectResponse("/organiza/usuarios", status_code=303)


@app.get("/organiza/usuarios/{usuario_id}/editar", response_class=HTMLResponse)
def editar_usuario(usuario_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_usuario")
    editado = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not editado: raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/usuario_form.html", {"request": request, "usuario": usuario, "editado": editado})


@app.post("/organiza/usuarios/{usuario_id}/editar")
def salvar_usuario(usuario_id: int, nome: str = Form(...), telefone: str = Form(...), senha: str = Form(""), email: str = Form(""), cargo: str = Form(""), is_admin: str = Form(""), pode_criar_tarefa: str = Form(""), pode_criar_projeto: str = Form(""), pode_criar_usuario: str = Form(""), pode_criar_etapa: str = Form(""), ativo: str = Form(""), departamentos: Optional[List[str]] = Form(None), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    exigir_permissao(usuario, "pode_criar_usuario")
    editado = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not editado: raise HTTPException(status_code=404)
    numero = limpar_telefone(telefone)
    if not telefone_valido(numero):
        raise HTTPException(status_code=400, detail="Telefone do usuário inválido. Use DDD + número.")
    conflito = db.query(Usuario).filter(Usuario.nome == nome.strip(), Usuario.id != usuario_id).first()
    if conflito:
        raise HTTPException(status_code=400, detail="Já existe usuário com este nome.")
    admin = 1 if is_admin else 0
    editado.nome = nome.strip()
    editado.telefone = numero
    editado.email = email.strip() or None
    editado.cargo = cargo.strip() or None
    editado.is_admin = admin
    editado.pode_criar_tarefa = 1 if (pode_criar_tarefa or admin) else 0
    editado.pode_criar_projeto = 1 if (pode_criar_projeto or admin) else 0
    editado.pode_criar_usuario = 1 if (pode_criar_usuario or admin) else 0
    editado.pode_criar_etapa = 1 if (pode_criar_etapa or admin) else 0
    editado.ativo = 1 if ativo else 0
    editado.departamentos = ",".join(departamentos or [])
    if senha.strip():
        editado.senha_hash = gerar_hash_senha(senha.strip())
    db.commit()
    return RedirectResponse("/organiza/usuarios", status_code=303)

@app.get("/organiza/clientes/novo", response_class=HTMLResponse)
def novo_cliente(request: Request, telefone: str = "", voltar: str = "/organiza", usuario: Usuario = Depends(usuario_logado)):
    return templates.TemplateResponse("organiza/cliente_form.html", {"request": request, "usuario": usuario, "cliente": None, "telefone": limpar_telefone(telefone), "voltar": voltar})


@app.post("/organiza/clientes/novo")
def criar_cliente(nome: str = Form(...), telefone: str = Form(...), empresa: str = Form(""), observacao: str = Form(""), voltar: str = Form("/organiza"), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    numero = limpar_telefone(telefone)
    if not telefone_valido(numero):
        raise HTTPException(status_code=400, detail="Telefone inválido. Use DDD + número.")
    existe = db.query(Cliente).filter(Cliente.telefone == numero).first()
    if existe:
        return RedirectResponse(voltar or "/organiza", status_code=303)
    db.add(Cliente(nome=nome.strip(), telefone=numero, empresa=empresa.strip() or None, observacao=observacao.strip() or None))
    db.commit()
    return RedirectResponse(voltar or "/organiza", status_code=303)


@app.get("/api/clientes/buscar")
def buscar_cliente(q: str = "", telefone: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    termo = (q or telefone or "").strip()
    numero = limpar_telefone(termo)
    query = db.query(Cliente)
    if numero:
        query = query.filter(Cliente.telefone.like(f"%{numero}%"))
    elif termo:
        like = f"%{termo}%"
        query = query.filter(or_(Cliente.nome.ilike(like), Cliente.empresa.ilike(like)))
    else:
        return {"ok": False, "clientes": []}
    clientes = query.order_by(Cliente.nome).limit(8).all()
    return {"ok": bool(clientes), "clientes": [{"id": c.id, "nome": c.nome, "telefone": c.telefone, "telefone_formatado": formatar_telefone(c.telefone), "empresa": c.empresa or ""} for c in clientes]}



@app.get("/organiza/tarefas/{tarefa_id}/chamados/novo", response_class=HTMLResponse)
def novo_chamado_tarefa(tarefa_id: int, request: Request, departamento: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse("organiza/chamado_novo.html", {"request": request, "usuario": usuario, "tarefa": tarefa, "departamentos": DEPARTAMENTOS_CHAMADO, "departamento_atual": departamento})


@app.post("/organiza/tarefas/{tarefa_id}/chamados/novo")
def criar_chamado_tarefa(tarefa_id: int, departamento: str = Form(...), descricao: str = Form(...), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    if departamento not in DEPARTAMENTOS_CHAMADO:
        raise HTTPException(status_code=400, detail="Tipo de chamado inválido.")
    if not descricao.strip():
        raise HTTPException(status_code=400, detail="A observação do chamado é obrigatória.")
    aberto = db.query(Chamado).filter(Chamado.tarefa_id == tarefa.id, Chamado.departamento == departamento, Chamado.status != "Concluído").first()
    if aberto:
        return RedirectResponse(f"/organiza/chamados/{aberto.id}", status_code=303)
    chamado = Chamado(tarefa_id=tarefa.id, departamento=departamento, descricao=descricao.strip(), criado_por=usuario.nome, status="Aberto")
    db.add(chamado)
    status_anterior = tarefa.status
    tarefa.status = status_aguardando_departamento(departamento)
    registrar_historico(db, tarefa.id, f"Chamado aberto para {departamento}", descricao, usuario, tipo="chamado")
    registrar_historico(db, tarefa.id, f"Status alterado para {tarefa.status}", f"Antes: {status_anterior}", usuario, tipo="status")
    db.commit()
    return RedirectResponse("/organiza", status_code=303)


@app.get("/organiza/chamados", response_class=HTMLResponse)
def listar_chamados(request: Request, departamento: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    deps = departamentos_usuario(usuario)
    query = db.query(Chamado).filter(Chamado.status != "Concluído")
    if departamento:
        query = query.filter(Chamado.departamento == departamento)
    elif not usuario.is_admin:
        if deps:
            query = query.filter(Chamado.departamento.in_(deps))
        else:
            query = query.filter(Chamado.id == -1)
    chamados = query.order_by(Chamado.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/chamados.html", {"request": request, "usuario": usuario, "chamados": chamados, "departamentos": DEPARTAMENTOS_CHAMADO, "departamento_atual": departamento})


@app.get("/organiza/chamados/{chamado_id}", response_class=HTMLResponse)
def abrir_chamado(chamado_id: int, request: Request, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    chamado = db.query(Chamado).filter(Chamado.id == chamado_id).first()
    if not chamado:
        raise HTTPException(status_code=404)
    if not usuario_tem_departamento(usuario, chamado.departamento):
        raise HTTPException(status_code=403, detail="Este chamado pertence a outro departamento.")
    externos = db.query(Externo).order_by(Externo.nome).all()
    historicos = db.query(Historico).filter(Historico.tarefa_id == chamado.tarefa_id).order_by(Historico.criado_em.desc()).all()
    return templates.TemplateResponse("organiza/chamado_form.html", {"request": request, "usuario": usuario, "chamado": chamado, "externos": externos, "status_chamado": STATUS_CHAMADO, "historicos": historicos})


@app.post("/organiza/chamados/{chamado_id}")
def salvar_chamado(chamado_id: int, descricao: str = Form(""), status: str = Form("Aberto"), fornecedor: str = Form(""), valor: str = Form(""), previsao_entrega: str = Form(""), data_recebimento: str = Form(""), externo_id: str = Form(""), externo_nome: str = Form(""), externo_telefone: str = Form(""), data_envio: str = Form(""), data_devolucao: str = Form(""), observacao: str = Form(""), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    chamado = db.query(Chamado).filter(Chamado.id == chamado_id).first()
    if not chamado:
        raise HTTPException(status_code=404)
    if not usuario_tem_departamento(usuario, chamado.departamento):
        raise HTTPException(status_code=403, detail="Este chamado pertence a outro departamento.")
    status_anterior = chamado.status
    descricao_anterior = chamado.descricao
    chamado.descricao = descricao.strip() or chamado.descricao
    chamado.status = status if status in STATUS_CHAMADO else chamado.status
    chamado.fornecedor = fornecedor.strip() or None
    chamado.valor = valor.strip() or None
    chamado.previsao_entrega = parse_data(previsao_entrega)
    chamado.data_recebimento = parse_data(data_recebimento)
    chamado.data_envio = parse_data(data_envio)
    chamado.data_devolucao = parse_data(data_devolucao)
    chamado.observacao = observacao.strip() or None
    if chamado.status != status_anterior:
        registrar_historico(db, chamado.tarefa_id, f"Chamado {chamado.departamento} alterado para {chamado.status}", f"Antes: {status_anterior}", usuario, tipo="chamado")

        # REGRA ORGANIZA 6.0:
        # Quando qualquer chamado é concluído, a tarefa de origem volta para Pendente.
        # Isso devolve a tarefa ao responsável original para continuar o fluxo.
        if chamado.status == "Concluído":
            chamado.finalizado_por = usuario.nome
            chamado.concluido_em = datetime.now()
            status_tarefa_anterior = chamado.tarefa.status
            chamado.tarefa.status = "Pendente"
            registrar_historico(
                db,
                chamado.tarefa_id,
                "Status alterado para Pendente",
                f"Antes: {status_tarefa_anterior}. Chamado concluído e tarefa devolvida ao responsável original.",
                usuario,
                tipo="status",
            )
    elif chamado.descricao != descricao_anterior:
        registrar_historico(db, chamado.tarefa_id, f"Chamado {chamado.departamento} atualizado", chamado.descricao, usuario, tipo="chamado")

    if chamado.departamento == "Externo":
        if externo_id:
            chamado.externo_id = int(externo_id)
        elif externo_nome.strip() and telefone_valido(externo_telefone):
            numero = limpar_telefone(externo_telefone)
            externo = db.query(Externo).filter(Externo.telefone == numero).first()
            if not externo:
                externo = Externo(nome=externo_nome.strip(), telefone=numero)
                db.add(externo)
                db.flush()
            chamado.externo_id = externo.id
    db.commit()
    return RedirectResponse("/organiza", status_code=303)


@app.post("/organiza/chamados/{chamado_id}/concluir")
def concluir_chamado(chamado_id: int, usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    chamado = db.query(Chamado).filter(Chamado.id == chamado_id).first()
    if not chamado:
        raise HTTPException(status_code=404)
    if not usuario_tem_departamento(usuario, chamado.departamento):
        raise HTTPException(status_code=403, detail="Este chamado pertence a outro departamento.")
    if chamado.departamento == "Compras" and not chamado.data_recebimento:
        raise HTTPException(status_code=400, detail="Compras só pode concluir depois de informar a data de recebimento.")
    chamado.status = "Concluído"
    chamado.finalizado_por = usuario.nome
    chamado.concluido_em = datetime.now()
    chamado.tarefa.status = "Pendente"
    registrar_historico(db, chamado.tarefa_id, f"Chamado encerrado: {chamado.departamento}", chamado.descricao, usuario, tipo="chamado")
    registrar_historico(db, chamado.tarefa_id, "Status alterado para Pendente", "Chamado concluído e tarefa devolvida ao responsável original.", usuario, tipo="status")
    db.commit()
    return RedirectResponse("/organiza", status_code=303)


@app.get("/api/externos/buscar")
def buscar_externos(q: str = "", usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    termo = (q or "").strip()
    numero = limpar_telefone(termo)
    if not termo:
        return {"ok": False, "externos": []}
    query = db.query(Externo)
    if numero:
        query = query.filter(Externo.telefone.like(f"%{numero}%"))
    else:
        like = f"%{termo}%"
        query = query.filter(or_(Externo.nome.ilike(like), Externo.empresa.ilike(like)))
    externos = query.order_by(Externo.nome).limit(8).all()
    return {"ok": bool(externos), "externos": [{"id": e.id, "nome": e.nome, "telefone": e.telefone, "telefone_formatado": formatar_telefone(e.telefone), "empresa": e.empresa or ""} for e in externos]}


@app.post("/organiza/tarefas/{tarefa_id}/ordem")
def alterar_ordem_tarefa(tarefa_id: int, direcao: str = Form(...), voltar: str = Form("/organiza"), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    if not usuario.is_admin and tarefa.responsavel != usuario.nome:
        raise HTTPException(status_code=403, detail="Você só pode ordenar tarefas sob sua responsabilidade.")
    if direcao in {"subir", "descer", "topo", "final"}:
        trocar_ordem_tarefa(db, tarefa, direcao)
        db.commit()
    return RedirectResponse(voltar or "/organiza", status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/excluir")
def excluir_tarefa(tarefa_id: int, voltar: str = Form("/organiza"), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa:
        raise HTTPException(status_code=404)
    if not usuario.is_admin and tarefa.responsavel != usuario.nome:
        raise HTTPException(status_code=403, detail="Você só pode excluir tarefas sob sua responsabilidade.")
    destino = voltar or f"/organiza/projetos/{tarefa.projeto_id}"
    responsavel = tarefa.responsavel
    db.delete(tarefa)
    db.flush()
    normalizar_ordem_tarefas(db, responsavel=responsavel)
    db.commit()
    return RedirectResponse(destino, status_code=303)


@app.post("/organiza/tarefas/{tarefa_id}/status")
def alterar_status(tarefa_id: int, status: str = Form(...), voltar: str = Form("/organiza"), usuario: Usuario = Depends(usuario_logado), db: Session = Depends(get_db)):
    tarefa = db.query(Tarefa).filter(Tarefa.id == tarefa_id).first()
    if not tarefa: raise HTTPException(status_code=404)
    if status in STATUS_VALIDOS:
        status_anterior = tarefa.status
        tarefa.status = status
        if status_anterior != status:
            registrar_historico(db, tarefa.id, f"Status alterado para {status}", f"Antes: {status_anterior}", usuario, tipo="status")
        criar_chamado_se_necessario(db, tarefa, status_anterior, usuario)
        db.commit()
    return RedirectResponse(voltar or "/organiza", status_code=303)
