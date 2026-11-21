"""Descrições resumidas das condições de Tormenta20."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Condicao:
    nome: str
    descricao: str
    tipo: str = ""


# Tormenta20 — Edição Jogo do Ano, pp. 394–395.
PERSONAGEM = {
    "abalado": Condicao("Abalado", "Aplica −2 aos testes de perícia. Uma nova aplicação deixa o personagem apavorado.", "Medo"),
    "agarrado": Condicao(
        "Agarrado",
        "Fica imóvel e desprevenido. Só pode atacar com armas leves e tem −2 nos testes de ataque. "
        "Um ataque à distância contra alguém envolvido no agarrão tem 50% de chance de atingir o outro envolvido.",
        "Movimento",
    ),
    "alquebrado": Condicao("Alquebrado", "Cada habilidade que o personagem usa custa 1 PM a mais.", "Mental"),
    "apavorado": Condicao("Apavorado", "Aplica −5 aos testes de perícia e impede a aproximação voluntária da fonte do medo.", "Medo"),
    "atordoado": Condicao("Atordoado", "Fica desprevenido e perde a capacidade de agir.", "Mental"),
    "caido": Condicao(
        "Caído",
        "O deslocamento passa a 1,5 m e os ataques corpo a corpo sofrem −5. A Defesa diminui em 5 contra ataques "
        "corpo a corpo e aumenta em 5 contra ataques à distância. Esses ajustes na Defesa se somam aos de outras condições.",
    ),
    "cego": Condicao(
        "Cego",
        "Fica lento e desprevenido, perde os testes de Percepção que dependem da visão e sofre −5 nas perícias "
        "de Força e Destreza. Alvos de seus ataques têm camuflagem total. Escuridão completa causa esta condição "
        "quando o personagem não tem como perceber no escuro.",
        "Sentidos",
    ),
    "confuso": Condicao(
        "Confuso",
        "No começo do turno, role 1d6. Com 1, mova-se na direção indicada por 1d8. Com 2 ou 3, apenas balbucie, "
        "sem realizar ações. Com 4 ou 5, ataque a criatura mais próxima com a arma em mãos. Se estiver sozinho, "
        "atinja a si mesmo e role diretamente o dano. Com 6, recupere o controle e aja normalmente.",
        "Mental",
    ),
    "debilitado": Condicao(
        "Debilitado", "Aplica −5 a testes de Força, Destreza e Constituição e às perícias ligadas a esses atributos. "
        "Uma nova aplicação deixa o personagem inconsciente.",
    ),
    "desprevenido": Condicao(
        "Desprevenido", "Reduz a Defesa e os testes de Reflexos em 5. Aplica-se também contra inimigos que o personagem não percebe.",
    ),
    "doente": Condicao("Doente", "A doença contraída determina os efeitos, a duração e como o personagem pode se curar.", "Metabolismo"),
    "em-chamas": Condicao(
        "Em Chamas", "Recebe 1d6 de dano de fogo no começo de cada turno. Para extinguir as chamas, pode usar uma "
        "ação padrão para abafá-las com as mãos ou mergulhar em água.",
    ),
    "enfeiticado": Condicao(
        "Enfeitiçado", "Mantém sua autonomia, mas se mostra prestativo e interpreta as intenções e atitudes da fonte "
        "da condição da maneira mais favorável. A fonte ganha +10 em Diplomacia com ele.", "Mental",
    ),
    "enjoado": Condicao(
        "Enjoado", "Em cada rodada, pode usar uma ação padrão ou uma ação de movimento. Uma investida pode ser feita "
        "com a ação padrão e fica limitada ao deslocamento normal.", "Metabolismo",
    ),
    "enredado": Condicao("Enredado", "Combina Lento e Vulnerável com um redutor de 2 nos testes de ataque.", "Movimento"),
    "envenenado": Condicao(
        "Envenenado", "Consulte o veneno aplicado para saber quais efeitos causa, como perda recorrente de PV ou outra "
        "condição. A perda recorrente de PV se soma, inclusive entre aplicações do mesmo veneno.", "Veneno",
    ),
    "esmorecido": Condicao("Esmorecido", "Aplica −5 aos testes de Inteligência, Sabedoria e Carisma e às perícias ligadas a esses atributos.", "Mental"),
    "exausto": Condicao("Exausto", "Combina Debilitado, Lento e Vulnerável. Uma nova aplicação deixa o personagem inconsciente.", "Cansaço"),
    "fascinado": Condicao(
        "Fascinado", "Só consegue observar o que prendeu sua atenção e tem −5 em Percepção. A condição termina se "
        "ele perder a fonte de vista, sofrer uma ação hostil ou for sacudido por alguém que gaste uma ação padrão.", "Mental",
    ),
    "fatigado": Condicao("Fatigado", "Combina Fraco e Vulnerável. Uma nova aplicação deixa o personagem exausto.", "Cansaço"),
    "fraco": Condicao(
        "Fraco", "Aplica −2 a testes de Força, Destreza e Constituição e às perícias ligadas a esses atributos. "
        "Uma nova aplicação deixa o personagem debilitado.",
    ),
    "frustrado": Condicao(
        "Frustrado", "Aplica −2 a testes de Inteligência, Sabedoria e Carisma e às perícias ligadas a esses atributos. "
        "Uma nova aplicação deixa o personagem esmorecido.", "Mental",
    ),
    "imovel": Condicao("Imóvel", "Reduz a 0 m todos os tipos de deslocamento do personagem.", "Movimento"),
    "inconsciente": Condicao(
        "Inconsciente", "Fica indefeso, sem ações ou reações. Ainda faz testes que independem da consciência, "
        "como o de Constituição para estabilizar sangramento. Sacudir a criatura para despertá-la exige uma ação padrão.",
    ),
    "indefeso": Condicao(
        "Indefeso", "Fica desprevenido, com penalidade de Defesa de −10 no lugar de −5. Falha automaticamente "
        "em Reflexos e fica sujeito a um golpe de misericórdia.",
    ),
    "lento": Condicao(
        "Lento", "Reduz cada deslocamento à metade, arredondando para baixo ao múltiplo de 1,5 m. "
        "Correr e fazer investidas ficam indisponíveis.", "Movimento",
    ),
    "ofuscado": Condicao("Ofuscado", "Reduz em 2 os testes de ataque e de Percepção.", "Sentidos"),
    "paralisado": Condicao("Paralisado", "Combina Imóvel e Indefeso. As únicas ações possíveis são as puramente mentais.", "Movimento"),
    "pasmo": Condicao("Pasmo", "Impede qualquer ação, inclusive reações.", "Mental"),
    "petrificado": Condicao("Petrificado", "Deixa o personagem inconsciente e lhe dá redução de dano 8.", "Metamorfose"),
    "sangrando": Condicao(
        "Sangrando", "No começo de cada turno, teste Constituição contra CD 15. O sucesso encerra o sangramento. "
        "Em caso de falha, perde 1d6 PV e continua sangrando.", "Metabolismo",
    ),
    "sobrecarregado": Condicao("Sobrecarregado", "Impõe −5 de penalidade de armadura e diminui o deslocamento em 3 m.", "Movimento"),
    "surdo": Condicao(
        "Surdo", "Impede testes de Percepção que dependam da audição e reduz Iniciativa em 5. "
        "Lançar magias conta como conjuração em condição ruim.", "Sentidos",
    ),
    "surpreendido": Condicao("Surpreendido", "Deixa o personagem desprevenido e impede ações, inclusive reações."),
    "vulneravel": Condicao("Vulnerável", "Reduz a Defesa em 2."),
}


# Ameaças de Arton, p. 375.
ITENS = {
    "avariado": Condicao(
        "Avariado", "Um efeito danifica o item. Armas e ferramentas avariadas dão −5 aos testes em que são usadas. "
        "Armaduras e escudos avariados reduzem a Defesa em 5. Esses redutores se somam aos demais. "
        "Se o item sofrer uma nova avaria, passa a Destruído. Um conserto remove a avaria.",
    ),
    "destruido": Condicao(
        "Destruído", "Se um efeito destruir o item ou ele perder todos os seus PV, fica inutilizável "
        "e deixa de oferecer seus benefícios até ser consertado.",
    ),
}
