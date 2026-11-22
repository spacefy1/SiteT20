"""Catálogo das células editáveis da ficha (a whitelist), validação e diff do upload.

O servidor só grava em células listadas aqui. As opções das listas vêm das
validações de dados do próprio modelo, salvo quando o campo traz `opcoes`.
"""
import hashlib
import re
import unicodedata
from dataclasses import dataclass

from openpyxl.utils.cell import coordinate_to_tuple

import modelo
from leitor import normalizar

TEXTO, INTEIRO, DECIMAL, MARCA, LISTA = "texto", "inteiro", "decimal", "marca", "lista"
AUTOMATICO = "__auto__"   # no formulário: apaga o override e a célula volta para a fórmula
LIMITE_TEXTO = 500
LISTA_LONGA = 250         # acima disso, campo de texto com sugestões (datalist)
LISTA_BUSCA = 1000        # acima disso, as sugestões vêm do servidor conforme a digitação


class ErroCampo(ValueError):
    pass


@dataclass(frozen=True)
class Campo:
    endereco: str
    rotulo: str
    tipo: str = TEXTO
    opcoes: tuple | None = None    # lista fixa; None = validação de dados do modelo
    minimo: float | None = None
    maximo: float | None = None
    automatico: bool = False       # célula com fórmula: sem entrada vale o cálculo da planilha
    dinamica: bool = False         # escolha racial: as opções dependem da raça da ficha (motor.opcoes_raciais)

    @property
    def id(self):
        return "c-" + re.sub(r"[^A-Za-z0-9]+", "-", self.endereco)


@dataclass(frozen=True)
class Bloco:
    titulo: str | None
    colunas: tuple | None          # None = campos soltos em grade
    linhas: tuple                  # ((rótulo da linha, (Campo | None, ...)), ...)
    visiveis: int | None = None    # linhas vazias depois desta posição ficam recolhidas
    especial: str | None = None    # "raca", "atributos" ou "poderes": o editor monta este bloco do seu jeito


def _c(endereco, rotulo, tipo=TEXTO, **k):
    return Campo(endereco, rotulo, tipo, **k)


def _solto(titulo, *campos):
    return Bloco(titulo, None, ((None, campos),))


SIGLAS = ("FOR", "DES", "CON", "INT", "SAB", "CAR")
LINHAS_ATRIBUTO = (12, 17, 22, 27, 32, 37)          # PJ!C: valor base
ATRIBUTO_PERICIA = ("For", "Des", "Con", "Int", "Sab", "Car")
PERICIAS = ("Acrobacia", "Adestramento", "Atletismo", "Atuação", "Cavalgar", "Conhecimento", "Cura",
            "Diplomacia", "Enganação", "Fortitude", "Furtividade", "Guerra", "Iniciativa", "Intimidação",
            "Intuição", "Investigação", "Jogatina", "Ladinagem", "Luta", "Misticismo", "Nobreza", "Ofício",
            "Percepção", "Pilotagem", "Pontaria", "Reflexos", "Religião", "Sobrevivência", "Vontade")  # PJ 8..36
LINHAS_MAGIA_PJ = (50, 51, 52, 53, 54, 56, 57, 58, 59, 61, 62, 63, 64, 66, 67, 68, 70, 71, 72)  # Grimório 5..23
AJUSTES_MANUAIS = ("Defesa", "PV máximos", "PM máximos", "Deslocamento (m)", "Capacidade (espaços)",
                   "CD de habilidades", "Manobras", "Ataque", "Dano")                          # Ajustes 16..24

OUTROS_LIMITE = 99


def outros_da_pericia(linha):
    """Bônus manual (buffs) da perícia na linha `linha` da PJ: Ajustes, linha + 21."""
    return f"Ajustes!B{linha + 21}"


SECOES = (
    ("Identidade", (
        _solto(None,
               _c("PJ!B3", "Nome do personagem"), _c("PJ!AO2", "Jogador"), _c("PJ!Z2", "Origem", LISTA),
               _c("PJ!C6", "Divindade"), _c("PJ!AO4", "Experiência", INTEIRO, minimo=0)),
        Bloco("Classes", ("Classe", "Nível"), (
            ("Principal", (_c("PJ!S4", "Classe", LISTA), _c("PJ!Z4", "Nível", LISTA))),
            ("Multiclasse", (_c("PJ!AD4", "Multiclasse", LISTA), _c("PJ!AK4", "Nível da multiclasse", LISTA))),
            ("Segunda multiclasse", (_c("PJ!AD5", "Segunda multiclasse", LISTA),
                                     _c("PJ!AK5", "Nível da segunda multiclasse", LISTA))),
        )),
    )),
    ("Raça", (
        # Raça e herança em cima; as escolhas da aba Raça (linhas 11 a 27) aparecem só quando a raça as tem
        Bloco(None, None, (
            (None, (_c("PJ!S2", "Raça", LISTA), _c("Raça!B4", "Herança de Moreau", LISTA))),
            *((f"Escolha {l - 10}", (_c(f"Raça!B{l}", f"Escolha racial {l - 10}", dinamica=True),))
              for l in range(11, 28)),
        ), especial="raca"),
    )),
    ("Atributos", (
        # Bônus da raça (BD TRAÇOS!A5:F5) e ajustes entram pela fórmula BD!B31:B36; o final é do último recálculo
        Bloco(None, SIGLAS, (
            ("Valor base", tuple(_c(f"PJ!C{l}", f"{s} base", INTEIRO) for s, l in zip(SIGLAS, LINHAS_ATRIBUTO))),
        ), especial="atributos"),
    )),
    ("Perícias", (
        # Outros = Ajustes!B29:B57 ("Ajuste manual"), que entra em BD!E2:E30. PJ!O8:O36 não entra em fórmula nenhuma.
        Bloco(None, ("Treinada", "Atributo", "Outros"), tuple(
            (nome, (_c(f"PJ!G{l}", f"{nome}: treinada", MARCA),
                    _c(f"PJ!M{l}", f"{nome}: atributo", LISTA, opcoes=ATRIBUTO_PERICIA),
                    _c(outros_da_pericia(l), f"{nome}: outros", INTEIRO, minimo=-OUTROS_LIMITE, maximo=OUTROS_LIMITE)))
            for nome, l in zip(PERICIAS, range(8, 37)))),
    )),
    ("Combate", (
        Bloco("Ataques", ("Arma", "Teste", "Tipo de dano", "Alcance", "Atributo no dano", "Ataque extra", "Dano extra"),
              tuple((f"Ataque {i}", (
                  _c(f"PJ!Q{l}", f"Ataque {i}: arma", LISTA),
                  _c(f"PJ!Z{l}", f"Ataque {i}: teste", LISTA, automatico=True),
                  _c(f"PJ!AO{l}", f"Ataque {i}: tipo de dano", LISTA, automatico=True),
                  _c(f"PJ!AT{l}", f"Ataque {i}: alcance", LISTA, automatico=True),
                  _c(f"Ajustes!G{l + 3}", f"Ataque {i}: atributo no dano", LISTA, automatico=True),
                  _c(f"Ajustes!H{l + 3}", f"Ataque {i}: ataque extra", INTEIRO),
                  _c(f"Ajustes!I{l + 3}", f"Ataque {i}: dano extra", INTEIRO)))
                  for i, l in enumerate(range(26, 32), 1))),
        _solto("Proteção e conjuração",
               _c("PJ!Q39", "Armadura", LISTA), _c("PJ!Q41", "Escudo", LISTA),
               _c("PJ!Q34", "Atributo de conjuração", LISTA, automatico=True),
               _c("PJ!B41", "Proficiência em armaduras leves", MARCA),
               _c("PJ!B43", "Proficiência em armas simples", MARCA)),
        _solto("Bônus de cena",
               _c("PJ!W9", "Defesa", INTEIRO), _c("PJ!Z33", "CD de magia", INTEIRO),
               _c("PJ!AV33", "Teste de manobra", INTEIRO)),
        _solto("Defesas e deslocamento",
               _c("PJ!AD9", "Deslocamento especial"), _c("PJ!T17", "Resistências"),
               _c("PJ!T18", "Imunidades"), _c("PJ!T19", "Situacionais")),
    )),
    ("Equipamento", (
        _solto(None, _c("PJ!Q44", "Dinheiro (T$)", DECIMAL, minimo=0)),
        Bloco("Itens", ("Item", "Quantidade", "Espaços por unidade (manual)", "Guardado ⛺️"), tuple(
            (f"Item {i}", (_c(f"PJ!U{l}", f"Item {i}", LISTA),
                           _c(f"PJ!Q{l}", f"Item {i}: quantidade", INTEIRO, minimo=0),
                           _c(f"PJ!AC{l}", f"Item {i}: espaços por unidade", DECIMAL, minimo=0),
                           _c(f"PJ!T{l}", f"Item {i}: guardado", MARCA)))
            for i, l in enumerate(range(49, 73), 1)), visiveis=8),
    )),
    ("Poderes", (
        Bloco("Poderes escolhidos", ("Ativo", "Opção"), tuple(
            (f"{i}", (_c(f"Poderes escolhidos!A{l}", f"Poder {i}: ativo", MARCA),
                      _c(f"Poderes escolhidos!B{l}", f"Poder {i}: opção", LISTA)))
            for i, l in enumerate(range(5, 105), 1)), visiveis=8, especial="poderes"),
        Bloco("Classe e geral: nível de cada linha", ("Nível (N1 a N20)",), tuple(
            (f"Linha {l}", (_c(f"PJ!AM{l}", f"Linha {l}: nível"),)) for l in range(47, 67)), visiveis=4),
    )),
    ("Magias", (
        Bloco("Grimório", ("Magia", "Aprimoramento (PM)", "Redução (PM)", "Atributo da CD", "P? na ficha"), tuple(
            (f"{i}", (_c(f"Grimório!A{l}", f"Magia {i}", LISTA),
                      _c(f"Grimório!D{l}", f"Magia {i}: aprimoramento", INTEIRO),
                      _c(f"Grimório!E{l}", f"Magia {i}: redução de PM", INTEIRO),
                      _c(f"Grimório!G{l}", f"Magia {i}: atributo da CD", LISTA),
                      _c(f"PJ!B{LINHAS_MAGIA_PJ[i - 1]}", f"Magia {i}: P?", MARCA) if i <= len(LINHAS_MAGIA_PJ) else None))
            for i, l in enumerate(range(5, 65), 1)), visiveis=8),
        Bloco("Magias extras escritas na ficha", ("P?", "Magia"), tuple(
            (f"{i}", (_c(f"PJ!H{l}", f"Magia extra {i}: P?", MARCA), _c(f"PJ!I{l}", f"Magia extra {i}")))
            for i, l in enumerate(LINHAS_MAGIA_PJ, 1)), visiveis=3),
    )),
    ("Ajustes", (
        Bloco("Ajuste racial manual", SIGLAS, (
            ("Ajuste", tuple(_c(f"Ajustes!{col}6", f"{s}: ajuste racial manual", INTEIRO)
                             for s, col in zip(SIGLAS, "BCDEFG"))),
        )),
        Bloco("Aumentos de atributo (poderes e outros)", SIGLAS, (
            ("Aumentos", tuple(_c(f"Ajustes!{col}7", f"{s}: aumentos de atributo", INTEIRO)
                               for s, col in zip(SIGLAS, "BCDEFG"))),
        )),
        Bloco("Ajustes manuais", ("Ajuste manual", "Poderes ativos"), tuple(
            (nome, (_c(f"Ajustes!B{l}", f"{nome}: ajuste manual", INTEIRO),
                    _c(f"Ajustes!C{l}", f"{nome}: poderes ativos", INTEIRO) if l >= 19 else None))
            for nome, l in zip(AJUSTES_MANUAIS, range(16, 25)))),
        _solto("Substituições",
               _c("Ajustes!G16", "Tamanho substituto", LISTA), _c("Ajustes!G17", "Deslocamento substituto (m)", DECIMAL, minimo=0),
               _c("Ajustes!G18", "Atributo de magia", LISTA)),
    )),
)

# Editáveis, mas fora do editor: a página da ficha muda estes pelos controles rápidos, sem recalcular
FORA_DO_EDITOR = (
    _c("PJ!S14", "PV atuais", INTEIRO), _c("PJ!X14", "PV temporários", INTEIRO, minimo=0),
    _c("PJ!AA14", "PM atuais", INTEIRO), _c("PJ!AF14", "PM temporários", INTEIRO),
)

CAMPOS = {}
for _campo in (*(c for _, blocos in SECOES for b in blocos for _, linha in b.linhas for c in linha if c),
               *FORA_DO_EDITOR):
    assert _campo.endereco not in CAMPOS, f"célula repetida no catálogo: {_campo.endereco}"
    CAMPOS[_campo.endereco] = _campo
AJUSTE_RACIAL = tuple(f"Ajustes!{col}6" for col in "BCDEFG")   # nunca zerado sozinho
ESCOLHAS_RACIAIS = tuple(f"Raça!B{l}" for l in range(11, 28))
CHAVE_RACA = ("PJ!S2", "Raça!B4", *ESCOLHAS_RACIAIS)   # o que muda as escolhas da aba Raça
AUMENTOS = tuple(f"Ajustes!{col}7" for col in "BCDEFG")
BASES = tuple(f"PJ!C{l}" for l in LINHAS_ATRIBUTO)


# ---- opções das listas
def fonte(c):
    """De onde vêm as opções (a fórmula da validação de dados, ou a lista fixa)."""
    return ",".join(c.opcoes) if c.opcoes is not None else modelo.carregar().validacoes[c.endereco][0]


def opcoes(c):
    return c.opcoes if c.opcoes is not None else modelo.carregar().validacoes[c.endereco][1]


def lista_id(c):
    return "l" + hashlib.md5(fonte(c).encode()).hexdigest()[:8]


def modo(c):
    """Como o editor mostra uma lista: select, datalist (sugestões na página) ou busca no servidor."""
    n = len(opcoes(c))
    return "busca" if n > LISTA_BUSCA else "datalist" if n > LISTA_LONGA else "select"


def campo_da_lista(identificador):
    return next((c for c in CAMPOS.values() if c.tipo == LISTA and lista_id(c) == identificador), None)


def _sem_acento(texto):
    return unicodedata.normalize("NFKD", str(texto).casefold()).encode("ascii", "ignore").decode()


def buscar(c, termo, limite=40):
    t = _sem_acento(termo.strip())
    return [o for o in opcoes(c) if t in _sem_acento(o)][:limite] if t else []


# ---- validação
def _numero(v, inteiro):
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, str):
        try:
            v = float(v.replace("−", "-").replace(" ", "").replace(",", "."))
        except ValueError:
            raise ErroCampo("Digite um número.") from None
    if not isinstance(v, (int, float)) or v != v or abs(v) > 1_000_000:
        raise ErroCampo("Digite um número.")
    if inteiro and not float(v).is_integer():
        raise ErroCampo("Digite um número inteiro.")
    return int(v) if inteiro else normalizar(float(v))


def _chave(v):
    return str(v).strip().casefold()


def validar(c, bruto):
    """Converte um valor do formulário ou da planilha. Levanta ErroCampo se não servir."""
    if c.automatico and bruto == AUTOMATICO:
        return AUTOMATICO
    v = normalizar(bruto)
    if v is None:
        return None
    if c.tipo == TEXTO:  # inclui as escolhas raciais; a lista delas é conferida por validar_escolha
        v = str(v)
        if len(v) > LIMITE_TEXTO:
            raise ErroCampo(f"Use no máximo {LIMITE_TEXTO} caracteres.")
        return v
    if c.tipo == MARCA:
        if _chave(v) in ("1", "1.0", "true"):
            return 1
        if _chave(v) in ("0", "0.0", "false"):
            return 0
        raise ErroCampo("Use 0 ou 1.")
    if c.tipo in (INTEIRO, DECIMAL):
        n = _numero(v, c.tipo == INTEIRO)
        if c.minimo is not None and n < c.minimo:
            raise ErroCampo(f"O mínimo é {normalizar(float(c.minimo))}.")
        if c.maximo is not None and n > c.maximo:
            raise ErroCampo(f"O máximo é {normalizar(float(c.maximo))}.")
        return n
    chave = _chave(normalizar(v))
    achada = next((o for o in opcoes(c) if _chave(o) == chave), None)
    if achada is None:
        raise ErroCampo("Escolha uma opção da lista.")
    return achada


def validar_escolha(c, valor, escolha):
    """Escolha racial contra a lista calculada para a raça da ficha (`motor.opcoes_raciais`)."""
    if valor is None:
        return None
    if escolha is None:
        raise ErroCampo("Esta escolha não existe para a raça atual.")
    if escolha["livre"]:
        return valor
    achada = next((o for o in escolha["opcoes"] if _chave(o) == _chave(valor)), None)
    if achada is None:
        raise ErroCampo("Escolha uma opção da lista.")
    return achada


def mesmo(a, b):
    a, b = normalizar(a), normalizar(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b  # 0 e 0.0 são o mesmo valor
    return type(a) is type(b) and a == b


def valor_atual(entradas, endereco):
    """O que o editor mostra: a entrada da ficha, ou o padrão do modelo (fórmula = Automático)."""
    m = modelo.carregar()
    if endereco in entradas:
        return entradas[endereco]
    return AUTOMATICO if m.tem_formula(endereco) else m.padrao(endereco)


def ler_formulario(form):
    """MultiDict do POST -> (mudanças {endereço: valor}, erros {endereço: mensagem})."""
    mudancas, erros = {}, {}
    for chave in form:
        if chave == "csrf":
            continue
        c = CAMPOS.get(chave)
        if c is None:
            erros[chave] = "Esta célula não é editável."
            continue
        try:
            mudancas[chave] = validar(c, form.getlist(chave)[-1])  # caixas de marcar mandam "0" e depois "1"
        except ErroCampo as e:
            erros[chave] = str(e)
    return mudancas, erros


def raca_mudou(entradas, mudancas):
    """A raça (ou a herança de Moreau) muda com este envio?"""
    m = modelo.carregar()
    return any(e in mudancas and not mesmo(mudancas[e], entradas.get(e, m.padrao(e)))
               for e in ("PJ!S2", "Raça!B4"))


def aplicar(entradas, mudancas):
    """Entradas depois do editor: valor igual ao do modelo (ou Automático) apaga a entrada.

    Trocar a raça apaga a herança e as escolhas raciais (eram da raça anterior); trocar a
    herança apaga as escolhas. Ajustes!B6:G6 (ajuste racial manual) nunca é apagado sozinho.
    """
    m = modelo.carregar()
    limpar = ()
    if "PJ!S2" in mudancas and raca_mudou(entradas, {"PJ!S2": mudancas["PJ!S2"]}):
        limpar = ("Raça!B4", *ESCOLHAS_RACIAIS)
    elif raca_mudou(entradas, mudancas):
        limpar = ESCOLHAS_RACIAIS
    # Entradas que saíram do catálogo (como PJ!O8:O36) também caem: nem estão mais no cache do pycel
    novas = {k: v for k, v in entradas.items() if k in CAMPOS and k not in limpar}
    for endereco, valor in mudancas.items():
        if endereco in limpar:
            continue
        if valor == AUTOMATICO or (not m.tem_formula(endereco) and mesmo(valor, m.padrao(endereco))):
            novas.pop(endereco, None)
        else:
            novas[endereco] = valor
    return novas


def _ordem(endereco):
    aba, a1 = modelo.separar(endereco)
    return modelo.ABAS_EDITAVEIS.index(aba), *coordinate_to_tuple(a1)


def entradas_da_planilha(arquivo):
    """Diff do .xlsx enviado contra o modelo -> (entradas, avisos)."""
    m, enviadas = modelo.abrir_planilha(arquivo)  # m: o modelo da planilha (o atual ou um anterior)
    entradas, fora, invalidas = {}, [], []
    for endereco in sorted(set(m.celulas) | set(enviadas), key=_ordem):
        novo = enviadas.get(endereco)
        formula_nova = isinstance(novo, str) and novo.startswith("=")
        if m.tem_formula(endereco):
            if formula_nova:
                continue  # a fórmula continua lá (mesmo que o Sheets a tenha reescrito)
        elif mesmo(novo, m.padrao(endereco)):
            continue
        c = CAMPOS.get(endereco)
        if c is None or formula_nova:
            fora.append(endereco)
            continue
        try:
            valor = validar(c, novo)
        except ErroCampo as e:
            invalidas.append(f"{c.rotulo} ({endereco}), {normalizar(novo)!r}: {e}")
            continue
        if m.tem_formula(endereco) or not mesmo(valor, m.padrao(endereco)):
            entradas[endereco] = valor
    avisos = []
    if m.nome != modelo.ATUAL:
        # Mesmos endereços no modelo novo; o que já é o padrão dele não precisa virar entrada
        atual = modelo.carregar()
        entradas = {e: v for e, v in entradas.items() if atual.tem_formula(e) or not mesmo(v, atual.padrao(e))}
        avisos.append("Planilha do modelo anterior convertida. Revise as escolhas raciais.")
    if fora:
        lista = ", ".join(fora[:8]) + ("…" if len(fora) > 8 else "")
        avisos.append(f"{len(fora)} célula(s) fora dos campos editáveis foram ignoradas "
                      f"(fórmulas trocadas por valores ou rótulos alterados): {lista}.")
    avisos += [f"Valor ignorado em {x}" for x in invalidas]
    return entradas, avisos
