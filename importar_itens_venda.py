# Importação inicial da tabela de custo e preço normal.
from app import SessionLocal, ProdutoServico, iniciar_banco

ITENS = [(1, 'AMPLIFICADOR MONO C/ BLUETOOTH', 580, 900), (2, 'AMPLIFICADOR STEREO', 730, 1100), (3, 'BLUETTOTH', 50, 150), (4, 'BOTOES', 6, 10), (5, 'CABO BIPOLAR', 3, 5), (6, 'CABO DE FORÇA', 6, 15), (7, 'CABO GPIO OU JAMA', 70, 120), (8, 'CABO HDMI', 6, 15), (9, 'CABO LIGAR PLACA', 2, 10), (10, 'CABO P2 X P2', 6, 15), (11, 'CABO P2 X RCA', 6, 15), (12, 'CABO SATA', 5, 15), (13, 'CABO USB X PS2', 25, 40), (14, 'CABO VGA', 15, 25), (15, 'CAIXA DE SOM C3TECK', 30, 50), (16, 'CAPA CORNETA', 5, 20), (17, 'CARTA SD 64', 70, 120), (18, 'CATALOGO ENCARDENADO', 50, 250), (20, 'COMANDOS', 30, 50), (21, 'CONECTOR 2 PINOS', 2, 10), (22, 'CONECTOR FORÇA', 12, 40), (23, 'CONECTOR P10 FEMEA', 5, 20), (24, 'CONECTOR P10 MACHO', 5, 20), (25, 'CONECTOR P2 FEMEA', 2, 10), (26, 'CONECTOR P2 MACHO', 5, 20), (27, 'COOLER 12 MM', 25, 40), (28, 'COOLER 8 MM', 15, 40), (29, 'DRIVER JBL', 130, 150), (30, 'DRIVER PREMIUM', 55, 100), (31, 'ESPELHO MIC MALETA', 16, 50), (32, 'ESPELHO TRASEIRO MALETA', 16, 50), (33, 'ESPELHO MIC MAQUINA', 16, 50), (34, 'ESPELHO TRASEIRO MAQUINA', 16, 50), (35, 'ESPELHO FLIPERAMA', 15, 50), (36, 'EXTENSAO', 15, 30), (37, 'EXTENSOR HDMI', 25, 40), (38, 'FALANTE 10 JBL', 300, 400), (39, 'FALANTE 12 JBL', 380, 480), (40, 'FALANTE 12 STURDY', 140, 200), (41, 'FITA LED', 5, 10), (42, 'FONTE 12 V', 25, 40), (43, 'FONTE C3TECK', 50, 150), (44, 'GABINETE FLIPER GOLD', 950, 1300), (45, 'GABINETE FLIPER PORT', 230, 350), (46, 'GABINETE IPHONE OU HEINEKEN EM LED', 1600, 2000), (47, 'GABINETE JUKEBOX 17', 785, 1200), (48, 'GABINETE MALETA', 250, 400), (49, 'GABINETE MALETA C/ TELA', 300, 600), (50, 'GABINETE MINI IPHONE OU HEINEKEN', 1000, 1400), (51, 'GRADE 12 FALANTE', 20, 40), (52, 'GRADE COOLER 12MM', 7, 20), (53, 'GRADE COOLER 8MM', 7, 20), (54, 'HD', 200, 250), (55, 'INTERFACE', 85, 120), (56, 'MEMORIA 2GB', 20, 50), (57, 'MICROFONE', 55, 100), (58, 'MONITO19', 300, 400), (59, 'MONITOR 15', 200, 400), (60, 'MONITOR 17', 250, 400), (61, 'MONITOR 22', 350, 450), (62, 'TV 32', 1000, 1200), (62, 'PLACA DE MICROFONE', 90, 250), (63, 'PLACA MAE', 200, 700), (68, 'SUPORTE DE MIC', 7, 20), (64, 'RASPBERRY ou PANDORA', 400, 550), (65, 'TECLADO', 60, 100), (66, 'TWEETER JBL', 130, 200)]

def moeda(valor):
    return f"{float(valor):.2f}".replace(".", ",")

def executar():
    iniciar_banco()
    db = SessionLocal()
    try:
        existentes = {i.nome.strip().upper(): i for i in db.query(ProdutoServico).all()}
        for codigo, nome, custo, venda in ITENS:
            chave = nome.strip().upper()
            item = existentes.get(chave)
            if item is None:
                item = ProdutoServico(nome=nome, categoria="Itens de equipamento", tipo="Produto", ativo=1)
                db.add(item)
                existentes[chave] = item
            item.preco_custo = moeda(custo)
            item.preco_normal = moeda(venda)
            item.valor_padrao = moeda(venda)
            if not item.observacao:
                item.observacao = f"Código da tabela: {codigo}" if codigo is not None else None
        db.commit()
        print(f"{len(ITENS)} itens importados/atualizados.")
    finally:
        db.close()

if __name__ == "__main__":
    executar()
