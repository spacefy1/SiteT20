"""Calcula os efeitos das condições sobre os valores importados da ficha."""
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal

import dados


IMPLICAM = {
    "agarrado": {"desprevenido", "imovel"},
    "atordoado": {"desprevenido"},
    "cego": {"desprevenido", "lento"},
    "enredado": {"lento", "vulneravel"},
    "exausto": {"debilitado", "lento", "vulneravel"},
    "fatigado": {"fraco", "vulneravel"},
    "inconsciente": {"indefeso"},
    "indefeso": {"desprevenido"},
    "paralisado": {"imovel", "indefeso"},
    "petrificado": {"inconsciente"},
    "surpreendido": {"desprevenido"},
}
PROGRESSAO = {
    "abalado": "apavorado", "fraco": "debilitado", "debilitado": "inconsciente",
    "frustrado": "esmorecido", "fatigado": "exausto", "exausto": "inconsciente",
}
EFEITOS_DE_TURNO = {"confuso", "em-chamas", "sangrando"}
SEM_ACOES = {"atordoado", "fascinado", "inconsciente", "pasmo", "surpreendido"}
FISICOS = {"for", "des", "con"}
MENTAIS = {"int", "sab", "car"}
DISTANCIA = re.compile(r"(?<![\d.,])(\d+(?:[,.]\d+)?)\s*m\b", re.IGNORECASE)
PASSO = Decimal("1.5")


def efetivas(ativas):
    resultado = set(ativas)
    pendentes = list(resultado)
    while pendentes:
        novas = IMPLICAM.get(pendentes.pop(), set()) - resultado
        resultado.update(novas)
        pendentes.extend(novas)
    return resultado


def _normalizar(texto):
    return unicodedata.normalize("NFKD", str(texto or "").casefold()).encode("ascii", "ignore").decode()


def _numero(valor):
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _somar(valor, ajuste):
    return valor + ajuste if _numero(valor) else valor


def _valor_ou_zero(valor):
    return valor if _numero(valor) else 0


def _penalidade(ativas, leve, grave):
    return 5 if grave in ativas else 2 if leve in ativas else 0


def _deslocamento(valor, ativas):
    if not ativas.intersection({"imovel", "caido", "lento", "sobrecarregado"}):
        return valor

    def calcular(base):
        if "imovel" in ativas:
            return Decimal(0)
        limites = [base]
        if "lento" in ativas:
            limites.append((base / 2 // PASSO) * PASSO)
        if "caido" in ativas:
            limites.append(PASSO)
        if "sobrecarregado" in ativas:
            limites.append(max(Decimal(0), base - 3))
        return min(limites)

    if _numero(valor):
        return float(calcular(Decimal(str(valor))))
    if not isinstance(valor, str):
        return valor

    def substituir(m):
        base = Decimal(m[1].replace(",", "."))
        ajustado = calcular(base)
        return m[0] if base == ajustado else f"{ajustado.normalize():f}".replace(".", ",") + " m"

    return DISTANCIA.sub(substituir, valor)


def _teste_ataque(ataque):
    teste = _normalizar(ataque.get("teste"))
    atributo = re.search(r"\b(for|des|con|int|sab|car)\b", teste)
    alcance = _normalizar(ataque.get("alcance")).strip()
    if "corpo a corpo" in alcance:
        tipo = "corpo-a-corpo"
    elif re.search(r"\b(curto|medio|longo|distancia)\b", alcance) or teste.startswith("pont"):
        tipo = "distancia"
    elif teste.startswith("luta"):
        tipo = "corpo-a-corpo"
    else:
        tipo = "indefinido"
    atributo_padrao = {"corpo-a-corpo": "for", "distancia": "des"}.get(tipo, "")
    return tipo, atributo[1] if atributo else atributo_padrao


def _estado_item(item, estados):
    nome = item.get("nome")
    return estados.get(nome.strip()) if isinstance(nome, str) else None


def calcular(ficha, ativas, itens, confusao=None):
    f = deepcopy(ficha)
    c = efetivas(ativas)
    geral = _penalidade(c, "abalado", "apavorado")
    fisico = _penalidade(c, "fraco", "debilitado")
    mental = _penalidade(c, "frustrado", "esmorecido")
    reflexos = _penalidade(c, "", "desprevenido")
    defesa = 10 if "indefeso" in c else _penalidade(c, "vulneravel", "desprevenido")
    reducao_dano = 8 if "petrificado" in c else 0

    def penalidade_pericia(atributo):
        if atributo in FISICOS:
            return max(geral, fisico, 5 if "cego" in c and atributo in {"for", "des"} else 0)
        return max(geral, mental if atributo in MENTAIS else 0)

    ajuste_armadura = -5 if "sobrecarregado" in c else 0
    for equipamento in (f["armadura"], f["escudo"]):
        estado = _estado_item(equipamento, itens)
        if estado == "avariado":
            defesa += 5
            equipamento["defesa"] = _somar(equipamento.get("defesa"), -5)
        elif estado == "destruido":
            defesa += _valor_ou_zero(equipamento.get("defesa"))
            ajuste_armadura -= _valor_ou_zero(equipamento.get("penalidade"))
            equipamento.update(defesa=0, penalidade=0)

    f["defesa"] = _somar(f.get("defesa"), -defesa)
    caido = "caido" in c
    f["deslocamento"] = _deslocamento(f.get("deslocamento"), c)
    for p in f["pericias"]:
        nome = _normalizar(p["nome"])
        especifica = 0
        if nome == "reflexos":
            especifica = reflexos
            if "indefeso" in c:
                p["bloqueio"] = "Falha automática em Reflexos (Indefeso)."
        elif nome == "percepcao":
            especifica = 5 if "fascinado" in c else 2 if "ofuscado" in c else 0
        elif nome == "iniciativa" and "surdo" in c:
            especifica = 5
        ajuste = ajuste_armadura if p.get("penalidade_armadura") else 0
        p["total"] = _somar(p.get("total"), ajuste - max(penalidade_pericia(_normalizar(p.get("atributo"))[:3]), especifica))

    chance_falha = 50 if "cego" in c else 0
    surdo = "surdo" in c
    sem_acoes = bool(c & SEM_ACOES) or ("confuso" in c and confusao in (1, 2, 3))
    bloqueio_ataque = sem_acoes or "paralisado" in c
    ataque_restrito = "agarrado" in c
    for a in f["ataques"]:
        tipo, atributo = _teste_ataque(a)
        estado = _estado_item(a, itens)
        penalidade = max(penalidade_pericia(atributo),
                         2 if c & {"agarrado", "enredado", "ofuscado"} else 0,
                         5 if caido and tipo == "corpo-a-corpo" else 0)
        a["bonus"] = _somar(a.get("bonus"), -penalidade - (5 if estado == "avariado" else 0))
        if bloqueio_ataque:
            a["bloqueio"] = "A condição ativa impede este ataque."
        elif estado == "destruido":
            a["bloqueio"] = "O equipamento está destruído."
        elif ataque_restrito and a.get("arma_leve") is False:
            a["bloqueio"] = "Agarrado permite apenas armas leves."
        elif ataque_restrito and a.get("arma_leve") is not True:
            a["confirmar_arma_leve"] = True
        if caido and tipo == "indefinido":
            a["redutor_se_corpo_a_corpo"] = max(0, 5 - penalidade)

    f["efeitos_condicoes"] = {
        "efetivas": sorted(c),
        "defesa_corpo_a_corpo": _somar(f["defesa"], -5) if caido else f["defesa"],
        "defesa_a_distancia": _somar(f["defesa"], 5) if caido else f["defesa"],
        "custo_pm_adicional": int("alquebrado" in c),
        "reducao_dano": reducao_dano,
        "chance_falha_ataque": chance_falha,
        "penalidade_teste_atributo": {sigla.upper(): penalidade for grupo, penalidade in
                                     ((FISICOS, fisico), (MENTAIS, mental)) if penalidade for sigla in grupo},
        "percepcao_visao_impedida": bool(chance_falha),
        "percepcao_audicao_impedida": surdo,
        "acoes_restritas": bloqueio_ataque,
        "inicio_turno": bool(c & EFEITOS_DE_TURNO),
    }
    return f


@dataclass
class InicioTurno:
    mensagens: list[str] = field(default_factory=list)
    remover: set[str] = field(default_factory=set)
    adicionar: set[str] = field(default_factory=set)
    pv: int | float | None = None
    confusao: int | None = None


def iniciar_turno(f, ativas, rd_fogo):
    c = efetivas(ativas)
    r = InicioTurno()
    if "confuso" in c:
        r.confusao = dados.d(6)[0]
        if r.confusao == 1:
            texto = f"mova-se na direção indicada pelo d8: {dados.d(8)[0]}."
        elif r.confusao <= 3:
            texto = "balbucie e fique sem ações neste turno."
        elif r.confusao <= 5:
            texto = "ataque a criatura mais próxima. Se estiver só, role apenas o dano contra si mesmo."
        else:
            texto = "a condição terminou."
            r.remover.add("confuso")
        r.mensagens.append(f"Confuso · d6 {r.confusao}, {texto}")

    perda = 0
    if "em-chamas" in c:
        fogo = dados.d(6)[0]
        rd = max(rd_fogo, 8 if "petrificado" in c else 0)
        dano = max(0, fogo - rd)
        perda += dano
        r.mensagens.append(f"Em Chamas · d6 {fogo}, {dano} de dano de fogo após RD {rd}.")

    if "sangrando" in c:
        const = f.get("atributos", {}).get("CON", {}).get("mod")
        if _numero(const):
            penalidade = _penalidade(c, "fraco", "debilitado")
            teste = dados.teste(const - penalidade)
            passou = teste["natural"] == 20 or (teste["natural"] != 1 and teste["total"] >= 15)
            resumo = f"Sangrando · Constituição {teste['total']} (CD 15)"
            if passou:
                r.remover.add("sangrando")
                r.mensagens.append(f"{resumo}, estabilizou.")
            else:
                sangramento = dados.d(6)[0]
                perda += sangramento
                r.mensagens.append(f"{resumo}, perdeu {sangramento} PV.")
        else:
            r.mensagens.append("Sangrando · faça o teste de Constituição CD 15 na mesa. A ficha está sem o modificador.")

    atual = f.get("pv", {}).get("atual")
    if perda and _numero(atual):
        r.pv = atual - perda
        if atual > 0 >= r.pv:
            r.adicionar.update({"inconsciente", "sangrando"})
            r.mensagens.append("Chegou a 0 PV ou menos e ficou Inconsciente e Sangrando.")
    elif perda:
        r.mensagens.append(f"A ficha está sem o valor de PV atual. Registre a perda de {perda} PV na mesa.")
    return r
