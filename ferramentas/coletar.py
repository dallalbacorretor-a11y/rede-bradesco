"""Coleta a rede Bradesco na consulta oficial do site, cidade a cidade.

Para cada rede dos produtos da pagina, cada tipo de atendimento (consulta,
exame, internacao, pronto-socorro, terapia, hospital-dia, remocao) e cada
especialidade, pergunta a busca oficial quem atende num raio de 20 km de um
ponto. Fica so quem tem endereco na cidade pedida. Cidade grande nao cabe num
raio so: enquanto houver prestador da cidade a mais de 12 km de todos os
pontos ja consultados, consulta de novo a partir dele.

Grava ferramentas/consulta/<UF>/<CIDADE>.json, que o montar.py poe na pagina.

As respostas ficam em ferramentas/.cache/ (fora do git) e valem para todas as
cidades: um ponto consultado para Curitiba ja traz Sao Jose dos Pinhais, e a
cidade vizinha so consulta o que faltar. Rodar de novo retoma de onde parou;
--novo descarta o cache.

Uso:
  python3 ferramentas/coletar.py CURITIBA/PR
  python3 ferramentas/coletar.py "SAO JOSE DOS PINHAIS/PR" --pontos=-25.53,-49.20
  python3 ferramentas/coletar.py --uf PR        # todas as cidades do estado
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
BIT = {r: i for i, (r, _) in enumerate(REDES)}
RAIO_SEGURO = 12.0   # km; a busca vai ate 20


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2 +
         math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 12742 * math.asin(math.sqrt(h))


def dados_da_pagina():
    html = (RAIZ / "index.html").read_bytes().decode("utf-8")
    i = html.index("const DADOS = ") + len("const DADOS = ")
    return json.JSONDecoder().raw_decode(html, i)[0]


def localidade(x):
    """'1-B RETIRO/CURITIBA/PR' -> ('CURITIBA', 'PR')"""
    partes = ((x.get("listaEnderecos") or [{}])[0].get("nomeLocalidade") or "").split("/")
    return (partes[-2].strip(), partes[-1].strip()) if len(partes) >= 3 else ("", "")


def posicao(x):
    e = (x.get("listaEnderecos") or [{}])[0]
    try:
        return float(e["latitude"]), float(e["longitude"])
    except (TypeError, ValueError, KeyError):
        return None


class Coleta:
    """Cache compartilhado: consultas feitas e ficha de cada prestador."""

    def __init__(self, paralelo=4):
        self.paralelo = paralelo
        nomes = api.redes()
        for rede, nome in REDES:
            if nomes.get(rede) != nome:
                sys.exit(f"a rede {rede} agora se chama {nomes.get(rede)!r}, nao {nome!r}: "
                         "confira REDES antes de coletar")
        self.cat = [(t["codigoTipoAtendimento"], api.especialidades(t["codigoTipoAtendimento"]))
                    for t in api.tipos_atendimento()]
        self.nome_esp = {(t, e["codigo"]): e["descricao"] for t, es in self.cat for e in es}
        CACHE.mkdir(parents=True, exist_ok=True)
        self.arq_c, self.arq_p = CACHE / "consultas.jsonl", CACHE / "prestadores.jsonl"
        self.feitas = {}          # consulta -> data
        self.hits = {}            # codigo -> {(tipo, esp): mascara de redes}
        self.fichas = {}          # codigo -> prestador como a busca devolve
        if self.arq_p.exists():
            for linha in self.arq_p.open(encoding="utf-8"):
                try:
                    x = json.loads(linha)
                except ValueError:    # linha cortada por uma parada no meio
                    continue
                self.fichas[x["codigo"]] = x
        if self.arq_c.exists():
            for linha in self.arq_c.open(encoding="utf-8"):
                try:
                    d = json.loads(linha)
                except ValueError:
                    continue
                k = d["k"]
                self._guarda((k[0], k[1], tuple(k[2]), k[3], k[4]), d["r"], d["d"])

    def _guarda(self, chave, lista, dia):
        self.feitas[chave] = dia
        bit = 1 << BIT[chave[0]]
        for cod, esps in lista:
            h = self.hits.setdefault(cod, {})
            for e in esps:
                h[(chave[1], e)] = h.get((chave[1], e), 0) | bit

    def consultas(self, ponto):
        for rede, _ in REDES:
            for tipo, esps in self.cat:
                cods = [e["codigo"] for e in esps]
                for i in range(0, len(cods), 5):
                    yield (rede, tipo, tuple(cods[i:i + 5]), ponto[0], ponto[1])

    def completo(self, ponto):
        return all(c in self.feitas for c in self.consultas(ponto))

    def pontos_feitos(self):
        return {(k[3], k[4]) for k in self.feitas}

    def consulta(self, pontos):
        falta = [c for p in pontos for c in self.consultas(p) if c not in self.feitas]
        if not falta:
            return
        print(f"  {len(pontos)} ponto(s), {len(falta)} consultas…", flush=True)
        inicio, hoje = time.time(), date.today().isoformat()

        def uma(c):
            # falha que sobrou das novas tentativas nao derruba a coleta: a consulta
            # fica fora do cache e e refeita na proxima passada
            try:
                return c, api.busca(*c)
            except Exception as e:  # noqa: BLE001
                print(f"  falhou {c[:3]} em {c[3]},{c[4]}: {e}", flush=True)
                return c, None

        with self.arq_c.open("a", encoding="utf-8") as fc, \
                self.arq_p.open("a", encoding="utf-8") as fp, \
                ThreadPoolExecutor(self.paralelo) as ex:
            for n, (chave, lista) in enumerate(ex.map(uma, falta), 1):
                if lista is None:
                    continue
                enxuto = []
                for x in lista:
                    if x["codigo"] not in self.fichas:
                        x = {k: v for k, v in x.items() if k not in ("distancia", "ranking")}
                        self.fichas[x["codigo"]] = x
                        fp.write(json.dumps(x, ensure_ascii=False) + "\n")
                    enxuto.append([x["codigo"], [e["codigo"] for e in x["especialidades"]]])
                fc.write(json.dumps({"k": [chave[0], chave[1], list(chave[2]), chave[3], chave[4]],
                                     "d": hoje, "r": enxuto}) + "\n")
                self._guarda(chave, enxuto, hoje)
                if n % 350 == 0:
                    fc.flush()
                    fp.flush()
                    print(f"  {n}/{len(falta)} · {time.time() - inicio:.0f}s", flush=True)

    def da_cidade(self, cidade, uf):
        return [c for c, x in self.fichas.items() if localidade(x) == (cidade, uf)]

    def cidade(self, cidade, uf, centro, pontos=None):
        """Consulta ate cobrir a cidade e grava ferramentas/consulta/<UF>/<CIDADE>.json."""
        feitos = [p for p in self.pontos_feitos() if km(p, centro) < 40 and self.completo(p)]
        if pontos:
            pontos = list(pontos)
        else:
            perto = [p for p in feitos if km(p, centro) <= RAIO_SEGURO]
            pontos = [] if perto else [centro]
        while True:
            for _ in range(3):
                self.consulta(pontos)
                if all(self.completo(p) for p in pontos):
                    break
            else:
                print(f"  atencao: {cidade}/{uf} ficou com consultas faltando", flush=True)
            usados = pontos + [p for p in feitos if p not in pontos]
            # prestador da cidade longe de todos os pontos: a cidade passa do raio
            longe = []
            for c in self.da_cidade(cidade, uf):
                pos = posicao(self.fichas[c])
                if not pos or km(pos, centro) > 60:     # coordenada errada na base
                    continue
                d = min(km(pos, q) for q in usados)
                if d > RAIO_SEGURO:
                    longe.append((d, pos))
            if not longe:
                break
            novo = max(longe)[1]
            pontos.append((round(novo[0], 4), round(novo[1], 4)))
        dias = sorted({self.feitas[c] for p in usados for c in self.consultas(p)})

        prest = []
        for cod in sorted(self.da_cidade(cidade, uf)):
            x = self.fichas[cod]
            e = x["listaEnderecos"][0]
            at = {}
            for (tipo, esp), m in sorted(self.hits.get(cod, {}).items()):
                if (tipo, esp) in self.nome_esp:
                    at.setdefault(str(tipo), {})[self.nome_esp[(tipo, esp)]] = m
            if not at:
                continue
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
            "cidade": cidade, "uf": uf, "data": dias[0] if dias else date.today().isoformat(),
            "fonte": api.PAGINA, "pontos": [list(p) for p in usados],
            "redes": [r for r, _ in REDES], "prestadores": prest},
            ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"{cidade}/{uf}: {len(prest)} prestadores, {len(usados)} ponto(s)", flush=True)
        return prest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cidade", nargs="?", help='"CIDADE/UF", sem acento, como na pagina')
    ap.add_argument("--uf", help="todas as cidades do estado (as da pagina e as que aparecerem)")
    ap.add_argument("--pontos", help="lat,lon;lat,lon — pontos de partida (so com cidade)")
    ap.add_argument("--refazer", action="store_true",
                    help="com --uf, refaz tambem as cidades que ja tem arquivo")
    ap.add_argument("--novo", action="store_true", help="descarta o cache antes")
    ap.add_argument("--paralelo", type=int, default=4)
    a = ap.parse_args()
    if bool(a.cidade) == bool(a.uf):
        ap.error("passe uma cidade ou --uf")
    if a.novo:
        for f in CACHE.glob("*.jsonl"):
            f.unlink()

    D = dados_da_pagina()
    geo = {(D["uf"][c[0]], c[1]): tuple(D["geo"][i]) for i, c in enumerate(D["cid"])}
    col = Coleta(a.paralelo)

    if a.cidade:
        cidade, uf = [s.strip().upper() for s in a.cidade.rsplit("/", 1)]
        pontos = [tuple(map(float, p.split(","))) for p in a.pontos.split(";")] if a.pontos else None
        centro = pontos[0] if pontos else geo.get((uf, cidade))
        if not centro:
            sys.exit("cidade sem coordenada na pagina: passe --pontos=lat,lon")
        col.cidade(cidade, uf, centro, pontos)
        return

    uf = a.uf.upper()
    qt = {}
    for p in D["pr"]:
        qt[p[1]] = qt.get(p[1], 0) + 1
    fila = [c[1] for i, c in sorted(enumerate(D["cid"]), key=lambda ic: -qt.get(ic[0], 0))
            if D["uf"][c[0]] == uf]
    vistas = set()
    while fila:
        cidade = fila.pop(0)
        vistas.add(cidade)
        if not a.refazer and (PASTA / uf / f"{cidade}.json").exists():
            continue
        centro = geo.get((uf, cidade))
        if not centro:
            # cidade nova, que so apareceu na consulta: o centro e a media dos prestadores
            pos = [posicao(col.fichas[c]) for c in col.da_cidade(cidade, uf)]
            pos = [p for p in pos if p]
            if not pos:
                continue
            centro = (sum(p[0] for p in pos) / len(pos), sum(p[1] for p in pos) / len(pos))
        col.cidade(cidade, uf, centro)
        # cidades do estado que a consulta trouxe e a pagina ainda nao tem
        novas = sorted({localidade(x)[0] for x in col.fichas.values()
                        if localidade(x)[1] == uf} - vistas - set(fila) - {""})
        fila += novas


if __name__ == "__main__":
    main()
