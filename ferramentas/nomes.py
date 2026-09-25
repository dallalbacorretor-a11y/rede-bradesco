"""Compara nomes de prestador escritos de jeitos diferentes.

Mesma normalizacao do comparativo do rede-amil-bradesco
(ferramentas/montar_comparativo.py): sem acento, abreviacoes expandidas, sem
"LTDA"/"S/A" e palavras de ligacao, e palavra a palavra (uma casa com o comeco
da outra). Aqui serve para achar, na consulta oficial, quem estava na planilha
ou nas listas em PDF: "LAB SANTA BRIGIDA" x "LAB ANAL CLIN SANTA BRIGIDA".
"""
import re
import unicodedata

ABREV = {
    "HOSP": "HOSPITAL", "HOSPIT": "HOSPITAL", "HOSPITALAR": "HOSPITAL",
    "STA": "SANTA", "STO": "SANTO", "SRA": "SENHORA", "SR": "SENHOR",
    "MAT": "MATERNIDADE", "MATER": "MATERNIDADE", "MATERN": "MATERNIDADE",
    "INST": "INSTITUTO", "LAB": "LABORATORIO", "LABOR": "LABORATORIO",
    "LABS": "LABORATORIO", "LABORATORIOS": "LABORATORIO",
    "CLIN": "CLINICA", "CLINIC": "CLINICA", "CLINICAS": "CLINICA", "CLINCA": "CLINICA",
    "CENT": "CENTRO", "CTR": "CENTRO", "CTO": "CENTRO",
    "DIAG": "DIAGNOSTICO", "DIAGN": "DIAGNOSTICO", "DIAGNOSTICOS": "DIAGNOSTICO",
    "ASSOC": "ASSOCIACAO", "SOC": "SOCIEDADE", "BENEF": "BENEFICENTE",
    "FUND": "FUNDACAO", "UNIV": "UNIVERSITARIO", "INF": "INFANTIL",
    "IRM": "IRMANDADE", "MISERICORD": "MISERICORDIA", "PQ": "PARQUE",
    "JD": "JARDIM", "VL": "VILA", "AV": "AVENIDA", "R": "RUA",
    "ESP": "ESPECIALIDADES", "ESPEC": "ESPECIALIDADES",
    "ODONTO": "ODONTOLOGIA", "OFTALMO": "OFTALMOLOGIA", "ORTOP": "ORTOPEDIA",
    "CARDIO": "CARDIOLOGIA", "IMAG": "IMAGEM", "RADIOL": "RADIOLOGIA",
    "ANAL": "ANALISES",
}
LIGACAO = {"DE", "DA", "DO", "DAS", "DOS", "E", "EM", "A", "O", "AS", "OS"}
JURIDICO = {"LTDA", "SA", "ME", "EPP", "EIRELI", "SS", "SIMPLES", "LIMITADA",
            "UNIDADE", "FILIAL", "MATRIZ", "UN", "UND"}
# palavras que todo prestador tem: sozinhas nao identificam ninguem
GENERICAS = {"HOSPITAL", "CLINICA", "CENTRO", "LABORATORIO", "INSTITUTO",
             "MEDICO", "MEDICA", "MEDICINA", "MED", "SAUDE", "DIAGNOSTICO",
             "IMAGEM", "SANTA", "SANTO", "SAO", "MATERNIDADE", "ESPECIALIDADES",
             "ANALISES", "SERVICOS", "SERVICO", "ASSOCIACAO",
             "SOCIEDADE", "INTEGRADA", "INTEGRADO", "NOSSA", "SENHORA",
             "RADIOLOGIA", "ULTRASSONOGRAFIA", "GRUPO", "REDE",
             "PRONTO", "SOCORRO", "ATENDIMENTO", "DR", "DRA"}
TIPO_PALAVRA = {"HOSPITAL": "H", "MATERNIDADE": "H", "CLINICA": "C",
                "LABORATORIO": "L", "ANALISES": "L", "IMAGEM": "I",
                "RADIOLOGIA": "I", "DIAGNOSTICO": "I", "ULTRASSONOGRAFIA": "I"}


def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn").upper()


def tokens(nome):
    s = sem_acento(nome)
    s = re.sub(r"\bN\.?\s*S(RA|A)?\.?\b", " NOSSA SENHORA ", s)
    s = re.sub(r"\bS\.?\s*/\s*A\b", " SA ", s)
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    out = []
    for t in s.split():
        t = ABREV.get(t, t)
        if t.startswith("MEDIC"):
            t = "MED"
        if t in LIGACAO or t in JURIDICO:
            continue
        if len(t) == 1 and not t.isdigit():
            continue
        out.extend(t.split())
    return out


def _casa(a, b):
    return a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))


def parecido(ta, tb, genericas=GENERICAS):
    """0..1: quanto os nomes batem, olhando as palavras que identificam."""
    if not ta or not tb:
        return 0.0
    da = [a for a in ta if a not in genericas]
    db = [b for b in tb if b not in genericas]
    if not da and not db:
        return 1.0 if sorted(ta) == sorted(tb) else 0.0
    usados, iguais, distintivas = set(), 0, 0
    for a in ta:
        for j, b in enumerate(tb):
            if j not in usados and _casa(a, b):
                usados.add(j)
                iguais += 1
                if a not in genericas and b not in genericas:
                    distintivas += 1
                break
    if not distintivas:
        return 0.0
    s = (0.45 * distintivas / max(1, min(len(da), len(db))) +
         0.35 * distintivas / max(1, max(len(da), len(db))) +
         0.20 * iguais / max(len(ta), len(tb)))
    ka = {TIPO_PALAVRA[t] for t in ta if t in TIPO_PALAVRA}
    kb = {TIPO_PALAVRA[t] for t in tb if t in TIPO_PALAVRA}
    if ka and kb and not (ka & kb):
        s -= 0.15
    return s


def nota(nome, outros, cidade=""):
    """Melhor nota do nome contra os outros (nome fantasia, razao social). O nome
    da cidade tambem nao identifica: "LAB CURITIBA" nao e a "CLINICA CURITIBA"."""
    g = GENERICAS | set(tokens(cidade))
    t = tokens(nome)
    return max((parecido(t, tokens(o), g) for o in outros if o), default=0.0)


def mesmo(nome, outros, cidade="", corte=0.6):
    return nota(nome, outros, cidade) >= corte


def termo_de_busca(nome, cidade=""):
    """Palavra para a busca por nome do site (3+ letras, sem simbolo): a mais
    longa que identifica o prestador (nem generica, nem o nome da cidade)."""
    palavras = re.sub(r"[^A-Z0-9 ]+", " ", sem_acento(nome)).split()
    fora = LIGACAO | JURIDICO | set(sem_acento(cidade).split())
    boas = [p for p in palavras if len(p) >= 3 and p not in fora and not p.isdigit()
            and ABREV.get(p, p) not in GENERICAS]
    if not boas:
        boas = [p for p in palavras if len(p) >= 3 and p not in fora and not p.isdigit()]
    return max(boas, key=len) if boas else None


def mesmo_estrito(nome, outros, cidade=""):
    """Para conferir a mesma operadora em duas datas: nome igual depois de
    normalizar, ou nota alta com todas as palavras que identificam o prestador
    presentes do outro lado. A ultima palavra de nome cortado no limite do
    cadastro ("... POR IM") nao precisa casar."""
    g = GENERICAS | set(tokens(cidade))
    t = tokens(nome)
    exige = [a for a in t if a not in g]
    if len(nome) >= 40 and exige and exige[-1] == t[-1]:
        exige = exige[:-1]
    for o in outros:
        if not o:
            continue
        to = tokens(o)
        if t == to:
            return True
        if parecido(t, to, g) >= 0.75 and all(any(_casa(a, b) for b in to) for a in exige):
            return True
    return False
