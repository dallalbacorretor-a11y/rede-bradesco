"""Coleta a rede Bradesco de uma cidade na consulta oficial do site.

Para cada rede dos produtos da pagina, cada tipo de atendimento (consulta,
exame, internacao, pronto-socorro, terapia, hospital-dia, remocao) e cada
especialidade, pergunta a busca oficial quem atende num raio de 20 km. Fica
so quem tem endereco na cidade pedida. Cidade grande nao cabe num raio so:
enquanto houver prestador da cidade a mais de 12 km de todos os pontos ja
consultados, consulta de novo a partir dele.

Grava ferramentas/consulta/<UF>/<CIDADE>.json, que o montar.py poe na pagina.
As respostas brutas ficam em ferramentas/.cache/ (fora do git): rodar de novo
retoma de onde parou; --novo apaga o cache da cidade e consulta tudo outra vez.

Uso:
  python3 ferramentas/coletar.py CURITIBA/PR
  python3 ferramentas/coletar.py "SAO JOSE DOS PINHAIS/PR" --pontos=-25.53,-49.20
"""
import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bradesco_api as api  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
PASTA = RAIZ / "ferramentas" / "consulta"
CACHE = RAIZ / "ferramentas" / ".cache"

# redes da Bradesco na ordem dos produtos da pagina (DADOS.planos): bit 0..6
REDES = [(136, "Efetivo IV"), (253, "Efetivo Plus E"), (252, "Efetivo Plus Q"),
         (227, "Nacional II"), (243, "Nacional III"), (30, "Nacional Plus"),
         (116, "Premium")]
RAIO_SEGURO = 12.0   # km; a busca vai ate 20


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2 +
         math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 12742 * math.asin(math.sqrt(h))


def centro_da_pagina(cidade, uf):
    """Coordenada que a pagina ja tem para a cidade (DADOS.geo)."""
    html = (RAIZ / "index.html").read_text(encoding="utf-8")
    i = html.index("const DADOS = ") + len("const DADOS = ")
    d, _ = json.JSONDecoder().raw_decode(html, i)
    for k, c in enumerate(d["cid"]):
        if c[1] == cidade and d["uf"][c[0]] == uf:
            return tuple(d["geo"][k])
    return None


def localidade(x):
    """'1-B RETIRO/CURITIBA/PR' -> ('CURITIBA', 'PR')"""
    partes = (x["listaEnderecos"][0].get("nomeLocalidade") or "").split("/")
    return (partes[-2].strip(), partes[-1].strip()) if len(partes) >= 3 else ("", "")


def catalogo():
    tipos = api.tipos_atendimento()
    return [(t["codigoTipoAtendimento"], t["nomeTipoAtendimento"],
             [e for e in api.especialidades(t["codigoTipoAtendimento"])])
            for t in tipos]


def consultas(cat, ponto):
    for rede, _ in REDES:
        for tipo, _, esps in cat:
            cods = [e["codigo"] for e in esps]
            for i in range(0, len(cods), 5):
                yield (rede, tipo, tuple(cods[i:i + 5]), ponto[0], ponto[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cidade", help='"CIDADE/UF", sem acento, como na pagina')
    ap.add_argument("--pontos", help="lat,lon;lat,lon — pontos de partida")
    ap.add_argument("--novo", action="store_true", help="ignora o cache")
    ap.add_argument("--paralelo", type=int, default=4)
    a = ap.parse_args()
    cidade, uf = [s.strip().upper() for s in a.cidade.rsplit("/", 1)]

    nomes = api.redes()
    for rede, nome in REDES:
        if nomes.get(rede) != nome:
            sys.exit(f"a rede {rede} agora se chama {nomes.get(rede)!r}, nao {nome!r}: "
                     "confira REDES antes de coletar")
    cat = catalogo()
    nome_esp = {(t, e["codigo"]): e["descricao"] for t, _, es in cat for e in es}

    if a.pontos:
        pontos = [tuple(map(float, p.split(","))) for p in a.pontos.split(";")]
    else:
        c = centro_da_pagina(cidade, uf)
        if not c:
            sys.exit("cidade sem coordenada na pagina: passe --pontos lat,lon")
        pontos = [c]
    centro = pontos[0]

    CACHE.mkdir(parents=True, exist_ok=True)
    bruto = CACHE / f"{uf}-{cidade}.jsonl"
    if a.novo and bruto.exists():
        bruto.unlink()
    feitas, achados = set(), {}

    def guarda(chave, lista):
        feitas.add(chave)
        for x in lista:
            achados.setdefault(x["codigo"], {"x": x, "at": {}})
            h = achados[x["codigo"]]["at"]
            for e in x["especialidades"]:
                k = (chave[1], e["codigo"])
                h[k] = h.get(k, 0) | 1 << [r for r, _ in REDES].index(chave[0])

    if bruto.exists():
        for linha in bruto.open(encoding="utf-8"):
            d = json.loads(linha)
            k = d["k"]
            guarda((k[0], k[1], tuple(k[2]), k[3], k[4]), d["r"])
        pontos += [p for p in {(k[3], k[4]) for k in feitas} if p not in pontos]

    inicio = time.time()
    with bruto.open("a", encoding="utf-8") as f, ThreadPoolExecutor(a.paralelo) as ex:
        while True:
            falta = [c for p in pontos for c in consultas(cat, p) if c not in feitas]
            if falta:
                print(f"{len(pontos)} ponto(s), {len(falta)} consultas…", flush=True)
            for n, (chave, lista) in enumerate(ex.map(
                    lambda c: (c, api.busca(*c)), falta), 1):
                f.write(json.dumps({"k": [chave[0], chave[1], list(chave[2]),
                                          chave[3], chave[4]], "r": lista},
                                   ensure_ascii=False) + "\n")
                guarda(chave, lista)
                if n % 200 == 0:
                    print(f"  {n}/{len(falta)} · {time.time() - inicio:.0f}s", flush=True)
            f.flush()
            # prestador da cidade longe de todos os pontos: a cidade passa do raio
            longe = []
            for p in achados.values():
                if localidade(p["x"]) != (cidade, uf):
                    continue
                e = p["x"]["listaEnderecos"][0]
                try:
                    pos = (float(e["latitude"]), float(e["longitude"]))
                except (TypeError, ValueError, KeyError):
                    continue
                if km(pos, centro) > 60:     # coordenada errada na base
                    continue
                d = min(km(pos, q) for q in pontos)
                if d > RAIO_SEGURO:
                    longe.append((d, pos))
            if not longe:
                break
            novo = max(longe)[1]
            pontos.append((round(novo[0], 4), round(novo[1], 4)))

    prest = []
    for cod, p in sorted(achados.items()):
        x = p["x"]
        if localidade(x) != (cidade, uf):
            continue
        e = x["listaEnderecos"][0]
        at = {}
        for (tipo, esp), m in sorted(p["at"].items()):
            at.setdefault(str(tipo), {})[nome_esp[(tipo, esp)]] = m
        prest.append({
            "cod": cod, "nome": (x.get("nomeFantasia") or x.get("razaoSocial") or "").strip(),
            "razao": x.get("razaoSocial"), "cnpj": x.get("cnpj") or "",
            "bairro": (e.get("bairro") or "").strip(),
            "end": ", ".join(s.strip(" -") for s in (e.get("logradouro"), e.get("numero"),
                                                   e.get("complemento"))
                             if s and s.strip(" -") not in ("", "0", "S/N", "SN")),
            "cep": e.get("cep") or "",
            "tel": [f"{t['ddd']}{t['numero']}" for t in x.get("listaTelefones") or []],
            "lat": e.get("latitude"), "lon": e.get("longitude"),
            "qual": [q["descricao"] for q in x.get("listaQualificacoes") or []],
            "at": at})

    PASTA.joinpath(uf).mkdir(parents=True, exist_ok=True)
    saida = PASTA / uf / f"{cidade}.json"
    saida.write_text(json.dumps({
        "cidade": cidade, "uf": uf, "data": date.today().isoformat(),
        "fonte": api.PAGINA, "pontos": pontos,
        "redes": [r for r, _ in REDES], "prestadores": prest},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{cidade}/{uf}: {len(prest)} prestadores, {len(pontos)} ponto(s) -> {saida}")


if __name__ == "__main__":
    main()
