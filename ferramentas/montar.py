"""Poe na pagina (index.html) as cidades coletadas na consulta oficial.

Para cada ferramentas/consulta/<UF>/<CIDADE>.json, os prestadores da cidade
em DADOS.pr sao trocados pelos da consulta: a cidade passa a vir inteira da
busca oficial (consultas, exames, internacao, pronto-socorro, terapias), com
endereco e telefone. As outras cidades continuam como estavam (planilha do
buscador e listas de hospitais e laboratorios). DADOS.consulta guarda quais
cidades vieram da consulta e em que data.

O formato de DADOS.pr nao muda (o comparativo do rede-amil-bradesco le os
mesmos campos); a consulta so acrescenta o 8o campo, [endereco, telefones]:
  [nome, cidade, bairro, [codigos], tipo, [esp, mascara, ...],
   [blocos de internacao, mascara de exames, 0], [endereco, telefones]]

Uso: python3 ferramentas/montar.py
"""
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PAGINA = RAIZ / "index.html"
PASTA = RAIZ / "ferramentas" / "consulta"
MARCA = "const DADOS = "

# tipos de atendimento da busca oficial
AMBULATORIAL, EXAME, HOSP_DIA, INTERNACAO, TERAPIA, URGENCIA, REMOCAO = 29, 30, 31, 32, 33, 34, 36

# ---------------------------------------------------------- classificacao
# 0 clinica, 1 hospital, 2 laboratorio, 3 medico, 4 centro de imagem
LAB = {"Análises clínicas", "Patologia", "Citopatologia", "Genética"}
IMAGEM = {"Radiologia e Diagnóstico por Imagem", "Tomografia computadorizada",
          "Ressonância magnética", "Ultrassonografia", "Mamografia", "Densitometria óssea",
          "Medicina nuclear", "Ecografia Vascular", "Radiologia vascular", "Ecodoppler fetal",
          "PET-CT", "Radiologia intervencionista"}
NOME_IMAGEM = re.compile(r"IMAG|RADIOL|TOMO|RESSON|ULTRA ?SON|SONOGRAF|\bRX\b|RAIO|"
                         r"DIAGNOSTICO POR|MAMO|DENSITO")
NOME_LAB = re.compile(r"\bLAB|LABORAT|ANALISES|PATOLOG|CITOLOG|GENETIC")
NOME_HOSP = re.compile(r"^(HOSP|HOSPITAL|MAT |MATERNIDADE|PRONTO SOCORRO|PS )")
NOME_DR = re.compile(r"^(DR|DRA)\.?\s")
# empresa, nao pessoa: medico com CNPJ so conta como medico se o nome for de gente
EMPRESA = re.compile(r"CLIN|CENTRO|CTO\b|INST|SERV|MEDIC|SAUDE|LTDA|ASSOC|GRUPO|NUCLEO|"
                     r"ESPACO|CONSULT|ODONTO|FISIO|PSICO|LAB|HOSP|DIAG|IMAG|CARDIO|ORTO|"
                     r"OFTALM|OLHOS|UROL|GASTRO|DERM|NEURO|ONCO|NEFRO|PEDIAT|GINEC|MULHER|"
                     r"CRIANCA|VIDA|CORPO|SORRISO|TERAPIA|REABILIT|UNIDADE|S/?S\b|EIRELI|"
                     r"\bME\b|&|\bE\b|CIA")


def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn").upper()


def classe(p, prenomes):
    nome = sem_acento(p["nome"])
    at = p["at"]
    tipos = {int(t) for t in at}
    exames = set(at.get(str(EXAME), {}))
    consultas = set(at.get(str(AMBULATORIAL), {})) | set(at.get(str(TERAPIA), {}))
    if not p["cnpj"] or NOME_DR.match(nome):
        return 3
    palavras = nome.split()
    if (len(palavras) >= 2 and palavras[0] in prenomes and not EMPRESA.search(nome)
            and not tipos & {EXAME, INTERNACAO, URGENCIA, HOSP_DIA}):
        return 3
    if tipos & {HOSP_DIA, INTERNACAO, URGENCIA} or NOME_HOSP.match(nome):
        return 1
    if exames & LAB and (len(consultas) <= 2 or NOME_LAB.search(nome)):
        return 2
    if exames & IMAGEM and (not consultas or NOME_IMAGEM.search(nome)):
        return 4
    if exames and not consultas and NOME_LAB.search(nome):
        return 2
    return 0


# ------------------------------------------------------------ montagem
def telefone(t):
    t = re.sub(r"\D", "", t)
    if len(t) < 10:
        return t
    ddd, n = t[:2], t[2:]
    return f"({ddd}) {n[:-4]}-{n[-4:]}"


def indice(lista, valor, mapa):
    if valor not in mapa:
        mapa[valor] = len(lista)
        lista.append(valor)
    return mapa[valor]


def servicos(at, bit):
    """O que o hospital cobre num produto, como nas listas oficiais: H/P.S/M/A/HDIA."""
    tem = lambda tipo: any(m >> bit & 1 for m in at.get(str(tipo), {}).values())
    partes = []
    if tem(INTERNACAO):
        partes.append("H")
    if tem(URGENCIA):
        partes.append("P.S")
    inter = at.get(str(INTERNACAO), {})
    if (inter.get("Obstetrícia", 0) | inter.get("Maternidade", 0)) >> bit & 1:
        partes.append("M")
    if partes and tem(AMBULATORIAL):
        partes.append("A")
    if tem(HOSP_DIA):
        partes.append("HDIA")
    return "/".join(partes)


def main():
    # a pagina usa CRLF: le e grava sem traduzir as quebras de linha
    html = PAGINA.read_bytes().decode("utf-8")
    ini = html.index(MARCA) + len(MARCA)
    D, fim = json.JSONDecoder().raw_decode(html, ini)
    nplanos = len(D["planos"])

    cidades = sorted(PASTA.glob("*/*.json"))
    if not cidades:
        sys.exit("nada em ferramentas/consulta/: rode antes o coletar.py")
    coletas = [json.loads(c.read_text(encoding="utf-8")) for c in cidades]

    # prenomes de quem a propria consulta diz ser pessoa fisica (sem CNPJ)
    prenomes = {sem_acento(p["nome"]).split()[0] for c in coletas for p in c["prestadores"]
                if not p["cnpj"] and p["nome"].split() and len(p["nome"].split()[0]) >= 3}

    esp_i = {e: i for i, e in enumerate(D["esp"])}
    serv_i = {s: i for i, s in enumerate(D["serv"])}
    ufs = {u: i for i, u in enumerate(D["uf"])}
    cid_i = {(D["uf"][c[0]], c[1]): i for i, c in enumerate(D["cid"])}
    nom, bai = D["nom"], D["bai"]
    novos_nom, novos_bai = [], []
    consulta = {int(k): v for k, v in (D.get("consulta") or {}).items()}
    por_cidade = {}

    for col in coletas:
        chave = (col["uf"], col["cidade"])
        if chave not in cid_i:
            if col["uf"] not in ufs:
                sys.exit(f"UF desconhecida: {col['uf']}")
            cid_i[chave] = len(D["cid"])
            D["cid"].append([ufs[col["uf"]], col["cidade"]])
            D["geo"].append([round(col["pontos"][0][0], 4), round(col["pontos"][0][1], 4)])
        c = cid_i[chave]
        linhas = []
        for p in col["prestadores"]:
            at = p["at"]
            mascara = {}
            for tipo, esps in at.items():
                for e, m in esps.items():
                    k = indice(D["esp"], e, esp_i)
                    mascara[k] = mascara.get(k, 0) | m
            pares = []
            for k in sorted(mascara, key=lambda k: sem_acento(D["esp"][k])):
                pares += [k, mascara[k]]
            blocos = 0
            if any(str(t) in at for t in (INTERNACAO, URGENCIA, HOSP_DIA)):
                blocos = [[indice(D["serv"], servicos(at, b), serv_i) for b in range(nplanos)]]
            exames = 0
            for m in at.get(str(EXAME), {}).values():
                exames |= m
            linhas.append([
                p["nome"], c, p["bairro"], [p["cod"]], classe(p, prenomes), pares,
                [blocos, exames, 0],
                [p["end"], " · ".join(telefone(t) for t in p["tel"][:3])]])
        ordem = {1: 0, 0: 1, 3: 2, 2: 3, 4: 4}
        linhas.sort(key=lambda r: (ordem[r[4]], sem_acento(r[0])))
        por_cidade[c] = linhas
        d = date.fromisoformat(col["data"])
        consulta[c] = d.strftime("%d/%m/%Y")

    # nome e bairro viram texto de novo para recompactar as listas sem sobras
    pr = []
    for p in D["pr"]:
        if p[1] in por_cidade:
            continue
        q = list(p)
        q[0], q[2] = nom[p[0]], bai[p[2]]
        pr.append(q)
    for linhas in por_cidade.values():
        pr += linhas
    # PR fica agrupado por cidade, na ordem das cidades (a pagina acha a faixa de cada uma)
    pr.sort(key=lambda p: p[1])      # sort estavel: mantem a ordem dentro da cidade
    nom_i, bai_i = {}, {}
    for q in pr:
        q[0] = indice(novos_nom, q[0], nom_i)
        q[2] = indice(novos_bai, q[2], bai_i)
    D["nom"], D["bai"], D["pr"] = novos_nom, novos_bai, pr
    D["consulta"] = {str(k): consulta[k] for k in sorted(consulta)}
    D["gerado"] = date.today().strftime("%d/%m/%Y")

    texto = json.dumps(D, ensure_ascii=False, separators=(",", ":"))
    PAGINA.write_bytes((html[:ini] + texto + html[fim:]).encode("utf-8"))
    n = sum(len(v) for v in por_cidade.values())
    print(f"{len(por_cidade)} cidade(s) da consulta oficial, {n} prestadores; "
          f"total na pagina: {len(pr)} prestadores em {len({p[1] for p in pr})} cidades")


if __name__ == "__main__":
    main()
