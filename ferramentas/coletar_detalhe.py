"""Varredura de detalhe da rede Bradesco em Curitiba e na regiao metropolitana.

O coletar.py cobre o pais com poucos pontos por cidade e guarda cada prestador
uma vez so. Aqui, para os 29 municipios da RMC, a busca oficial e refeita no
detalhe:

1. grade de pontos a cada 6 km no miolo urbano (Curitiba e as cidades coladas
   nela) e um ponto na sede de cada municipio. A busca vai a 20 km, entao cada
   endereco e visto de varios pontos. Prestador da RMC a mais de 8 km de todos
   os pontos ganha um ponto so dele.
2. as 7 redes x todos os tipos de atendimento x todas as especialidades do
   catalogo (5 por consulta, como o site aceita). Consulta que volta com menos
   linhas do que o total que ela mesma informa e refeita especialidade a
   especialidade.
3. uma linha por ENDERECO: a busca devolve o mesmo prestador (mesmo codigo) uma
   vez por endereco, com o numero do endereco em "nome" ("2-BATEL/CURITIBA/PR").
   O coletar.py ficava so com o primeiro: o medico com dois consultorios perdia
   um.
4. a ficha de especialidades de cada estabelecimento (com CNPJ), por rede e tipo
   (/prestadores/buscarespecialidades): acha especialidade cadastrada fora do
   catalogo da busca.
5. busca por nome, nas 7 redes, de cada estabelecimento que a Amil ou a
   SulAmerica lista na RMC e a varredura nao trouxe (a lista vem do comparativo
   do rede-amil-bradesco), de quem estava na coleta anterior e nao voltou, e de
   quem esta na base anterior (planilha e listas em PDF).

Grava ferramentas/consulta/PR/<CIDADE>.json no formato do coletar.py (um item
por endereco) e o relatorio ferramentas/detalhe_rmc.json.

As respostas ficam em ferramentas/.cache-detalhe/ (fora do git); rodar de novo
retoma de onde parou, --novo descarta.

Uso: python3 ferramentas/coletar_detalhe.py [--paralelo 12]
       [--comparativo ../rede-amil-bradesco/comparativo/dados.js]
"""
import argparse
import gzip
import json
import math
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bradesco_api as api  # noqa: E402
import nomes  # noqa: E402
from coletar import (BIT, PASTA, REDES, chave_cidade, dados_da_pagina, km,  # noqa: E402
                     localidade, posicao)

RAIZ = Path(__file__).resolve().parent.parent
CACHE = RAIZ / "ferramentas" / ".cache-detalhe"
RELATORIO = RAIZ / "ferramentas" / "detalhe_rmc.json"
UF = "PR"
# sede de cada municipio da RMC (a da pagina, quando ela tem a cidade, prevalece)
RMC = {
    "ADRIANOPOLIS": (-24.6606, -48.9922), "AGUDOS DO SUL": (-25.9899, -49.3343),
    "ALMIRANTE TAMANDARE": (-25.3188, -49.3037), "ARAUCARIA": (-25.5859, -49.4047),
    "BALSA NOVA": (-25.5804, -49.6291), "BOCAIUVA DO SUL": (-25.2066, -49.1141),
    "CAMPINA GRANDE DO SUL": (-25.3044, -49.0551), "CAMPO DO TENENTE": (-25.9800, -49.6844),
    "CAMPO LARGO": (-25.4525, -49.5290), "CAMPO MAGRO": (-25.3687, -49.4501),
    "CERRO AZUL": (-24.8246, -49.2610), "COLOMBO": (-25.2925, -49.2262),
    "CONTENDA": (-25.6788, -49.5350), "CURITIBA": (-25.4284, -49.2733),
    "DOUTOR ULYSSES": (-24.5665, -49.4219), "FAZENDA RIO GRANDE": (-25.6624, -49.3073),
    "ITAPERUCU": (-25.2193, -49.3454), "LAPA": (-25.7671, -49.7168),
    "MANDIRITUBA": (-25.7770, -49.3282), "PIEN": (-26.0965, -49.4336),
    "PINHAIS": (-25.4429, -49.1927), "PIRAQUARA": (-25.4422, -49.0624),
    "QUATRO BARRAS": (-25.3673, -49.0763), "QUITANDINHA": (-25.8734, -49.4973),
    "RIO BRANCO DO SUL": (-25.1892, -49.3143), "RIO NEGRO": (-26.1000, -49.7900),
    "SAO JOSE DOS PINHAIS": (-25.5313, -49.2031), "TIJUCAS DO SUL": (-25.9311, -49.1950),
    "TUNAS DO PARANA": (-24.9731, -49.0879),
}
CHAVES_RMC = {chave_cidade(c): c for c in RMC}
MIOLO = (-25.66, -25.28, -49.42, -49.08)     # lat min, lat max, lon min, lon max
PASSO = 6.0                                  # km entre os pontos da grade do miolo
LONGE = 8.0                                  # km: mais longe que isso de todo ponto, ganha ponto
AMBULATORIAL, EXAME, HOSP_DIA, INTERNACAO, TERAPIA, URGENCIA = 29, 30, 31, 32, 33, 34
HOSPITALAR = {HOSP_DIA, INTERNACAO, URGENCIA}


def ident(x):
    """Um endereco de um prestador: o codigo e o endereco numerado ("2-BATEL/CURITIBA/PR")."""
    return f'{x["codigo"]}|{(x.get("nome") or "").strip()}'


def cidade_rmc(x):
    c, u = localidade(x)
    return CHAVES_RMC.get(chave_cidade(c)) if u == UF else None


def grade():
    la0, la1, lo0, lo1 = MIOLO
    dla = PASSO / 111.2
    dlo = PASSO / (111.2 * math.cos(math.radians((la0 + la1) / 2)))
    pts, la = [], la0
    while la <= la1 + 1e-9:
        lo = lo0
        while lo <= lo1 + 1e-9:
            pts.append((round(la, 4), round(lo, 4)))
            lo += dlo
        la += dla
    return pts


def jsonl(caminho):
    if not caminho.exists():
        return
    for linha in caminho.open(encoding="utf-8"):
        try:
            yield json.loads(linha)
        except ValueError:        # linha cortada por uma parada no meio
            continue


class Detalhe:
    def __init__(self, paralelo):
        self.paralelo = paralelo
        self._trava = threading.Lock()
        nomes_redes = api.redes()
        for rede, nome in REDES:
            if nomes_redes.get(rede) != nome:
                sys.exit(f"a rede {rede} agora se chama {nomes_redes.get(rede)!r}")
        self.cat = [(t["codigoTipoAtendimento"],
                     [e["codigo"] for e in api.especialidades(t["codigoTipoAtendimento"])])
                    for t in api.tipos_atendimento()]
        self.nome_esp = {}
        for t, _ in self.cat:
            for e in api.especialidades(t):
                self.nome_esp[e["codigo"]] = e["descricao"]
        CACHE.mkdir(parents=True, exist_ok=True)
        self.arq_c, self.arq_f = CACHE / "consultas.jsonl", CACHE / "fichas.jsonl"
        self.arq_e, self.arq_n = CACHE / "especialidades.jsonl", CACHE / "nomes.jsonl"
        self.feitas = {}      # consulta -> {"n": total informado, "l": linhas, "d": dia}
        self.hits = {}        # id -> {(tipo, esp): mascara de redes}
        self.fichas = {}      # id -> linha como a busca devolve
        self.ficha_esp = {}   # (rede, codigo, tipo) -> [[esp, descricao], ...]
        self.por_nome = {}    # (rede, termo, lat, lon) -> [[id, tipo, [[esp, desc], ...]], ...]
        for x in jsonl(self.arq_f):
            self.fichas[x["id"]] = x["x"]
        for d in jsonl(self.arq_c):
            k = d["k"]
            self._guarda((k[0], k[1], tuple(k[2]), k[3], k[4]), d)
        for d in jsonl(self.arq_e):
            self.ficha_esp[tuple(d["k"])] = d["r"]
        for d in jsonl(self.arq_n):
            self._guarda_nome(tuple(d["k"]), d["r"])

    # ------------------------------------------------------------ cache
    def _ficha(self, x, arquivo):
        i = ident(x)
        if i not in self.fichas:
            x = {c: v for c, v in x.items() if c not in ("distancia", "ranking")}
            self.fichas[i] = x
            arquivo.write(json.dumps({"id": i, "x": x}, ensure_ascii=False) + "\n")
        return i

    def _guarda(self, chave, d):
        self.feitas[chave] = {"n": d["n"], "l": len(d["r"]), "d": d["d"]}
        bit = 1 << BIT[chave[0]]
        for i, esps in d["r"]:
            h = self.hits.setdefault(i, {})
            for e in esps:
                h[(chave[1], e)] = h.get((chave[1], e), 0) | bit

    def _guarda_nome(self, chave, linhas):
        self.por_nome[chave] = linhas
        bit = 1 << BIT[chave[0]]
        for i, tipo, esps in linhas:
            h = self.hits.setdefault(i, {})
            for e, desc in esps:
                self.nome_esp.setdefault(e, desc)
                h[(tipo, e)] = h.get((tipo, e), 0) | bit

    # ------------------------------------------------------- varredura
    def consultas(self, ponto):
        for rede, _ in REDES:
            for tipo, cods in self.cat:
                for i in range(0, len(cods), 5):
                    yield (rede, tipo, tuple(cods[i:i + 5]), ponto[0], ponto[1])

    def incompletas(self):
        return [k for k, v in self.feitas.items() if v["l"] < v["n"]]

    def consulta(self, chaves, rotulo=""):
        falta = [c for c in chaves if c not in self.feitas]
        if not falta:
            return
        print(f"  {rotulo}{len(falta)} consultas…", flush=True)
        inicio, hoje = time.time(), date.today().isoformat()

        def uma(c):
            try:
                r = api.chama("/prestadores/especialidade", {
                    "codigoRede": c[0], "listaEspecialidades": list(c[2]),
                    "codigoTipoAcomodacao": "Q",
                    "enderecoConsulta": {"latitude": c[3], "longitude": c[4]},
                    "instaAdapt": "N", "totalPaginas": 1, "codigoTipoEstabelecimento": c[1]})
                return c, r or {}
            except Exception as e:  # noqa: BLE001
                print(f"  falhou {c[:3]} em {c[3]},{c[4]}: {str(e)[:120]}", flush=True)
                return c, None

        with self.arq_c.open("a", encoding="utf-8") as fc, \
                self.arq_f.open("a", encoding="utf-8") as ff, \
                ThreadPoolExecutor(self.paralelo) as ex:
            for n, (chave, r) in enumerate(ex.map(uma, falta), 1):
                if r is None:
                    continue
                lista = r.get("listaReferenciados") or []
                d = {"k": [chave[0], chave[1], list(chave[2]), chave[3], chave[4]], "d": hoje,
                     "n": int(r.get("totalReferenciados") or len(lista)),
                     "r": [[self._ficha(x, ff), [e["codigo"] for e in x["especialidades"]]]
                           for x in lista]}
                fc.write(json.dumps(d, ensure_ascii=False) + "\n")
                self._guarda(chave, d)
                if n % 1000 == 0:
                    fc.flush()
                    ff.flush()
                    print(f"  {n}/{len(falta)} · {time.time() - inicio:.0f}s", flush=True)
        print(f"  pronto em {time.time() - inicio:.0f}s", flush=True)

    def varrer(self, pontos):
        for volta in range(3):
            self.consulta([c for p in pontos for c in self.consultas(p)],
                          f"{len(pontos)} ponto(s), volta {volta + 1}: ")
            if all(c in self.feitas for p in pontos for c in self.consultas(p)):
                break
        # consulta que informou mais do que devolveu: uma especialidade por vez
        partes = [(k[0], k[1], (e,), k[3], k[4]) for k in self.incompletas()
                  if len(k[2]) > 1 for e in k[2]]
        if partes:
            self.consulta(partes, "incompletas, uma especialidade por vez: ")

    def da_rmc(self):
        return {i: c for i, x in self.fichas.items() for c in [cidade_rmc(x)] if c}

    def cobrir(self, pontos):
        """Varre os pontos e acrescenta ponto onde houver prestador da RMC longe de todos."""
        self.varrer(pontos)
        for _ in range(60):
            longe = []
            for i in self.da_rmc():
                pos = posicao(self.fichas[i])
                if not pos or km(pos, RMC["CURITIBA"]) > 120:      # coordenada errada
                    continue
                d = min(km(pos, q) for q in pontos)
                if d > LONGE:
                    longe.append((d, (round(pos[0], 4), round(pos[1], 4))))
            if not longe:
                break
            pontos.append(max(longe)[1])
            print(f"  ponto novo em {pontos[-1]} ({max(longe)[0]:.1f} km do mais perto)",
                  flush=True)
            self.varrer(pontos)
        return pontos

    # ------------------------------------------- ficha de especialidades
    def fichas_especialidades(self):
        """Para cada estabelecimento da RMC, a lista completa de especialidades por rede e
        tipo em que ele aparece; hospital, em todos os tipos das redes em que aparece."""
        pedidos = set()
        for i in self.da_rmc():
            x = self.fichas[i]
            if not x.get("cnpj"):
                continue
            h = self.hits.get(i, {})
            redes = {r for (t, e), m in h.items() for r, _ in REDES if m >> BIT[r] & 1}
            tipos = {t for (t, e) in h}
            if tipos & HOSPITALAR:
                tipos = {t for t, _ in self.cat}
            for r in redes:
                for t in tipos:
                    pedidos.add((r, x["codigo"], t))
        falta = sorted(p for p in pedidos if p not in self.ficha_esp)
        print(f"  fichas de especialidades: {len(pedidos)} ({len(falta)} a pedir)…", flush=True)

        def uma(k):
            try:
                r = api.chama("/prestadores/buscarespecialidades", {
                    "codigoRede": k[0], "codigoPrestador": k[1], "codigoTipoEstabelecimento": k[2]})
                return k, [[e["codigo"], e["descricao"]] for e in (r or [])]
            except Exception as e:  # noqa: BLE001
                print(f"  falhou ficha {k}: {str(e)[:120]}", flush=True)
                return k, None

        inicio = time.time()
        with self.arq_e.open("a", encoding="utf-8") as fe, ThreadPoolExecutor(self.paralelo) as ex:
            for n, (k, r) in enumerate(ex.map(uma, falta), 1):
                if r is None:
                    continue
                self.ficha_esp[k] = r
                fe.write(json.dumps({"k": list(k), "r": r}, ensure_ascii=False) + "\n")
                if n % 1000 == 0:
                    print(f"  {n}/{len(falta)} · {time.time() - inicio:.0f}s", flush=True)

    # ------------------------------------------------------ busca por nome
    def busca_nome(self, termo, centro):
        falta = [(r, termo, centro[0], centro[1]) for r, _ in REDES
                 if (r, termo, centro[0], centro[1]) not in self.por_nome]

        def uma(k):
            try:
                r = api.chama("/prestadores/nome", {
                    "codigoRede": k[0], "codigoTipoAcomodacao": "Q",
                    "enderecoConsulta": {"latitude": k[2], "longitude": k[3]},
                    "instaAdapt": "N", "nomeReferenciado": k[1], "totalPaginas": 1})
                return k, (r or {}).get("listaReferenciados") or []
            except Exception as e:  # noqa: BLE001
                print(f"  falhou busca por nome {k}: {str(e)[:120]}", flush=True)
                return k, None

        if falta:
            with ThreadPoolExecutor(min(7, self.paralelo)) as ex:
                voltas = list(ex.map(uma, falta))
            # varias buscas por nome correm juntas: grava uma de cada vez
            with self._trava, self.arq_n.open("a", encoding="utf-8") as fn, \
                    self.arq_f.open("a", encoding="utf-8") as ff:
                for k, lista in voltas:
                    if lista is None:
                        continue
                    linhas = [[self._ficha(x, ff), x["codigoEstabelecimento"],
                               [[e["codigo"], e["descricao"]] for e in x["especialidades"]]]
                              for x in lista]
                    fn.write(json.dumps({"k": list(k), "r": linhas}, ensure_ascii=False) + "\n")
                    self._guarda_nome(k, linhas)
        return {i for r, _ in REDES for i, _, _ in
                self.por_nome.get((r, termo, centro[0], centro[1]), [])}

    def procurar(self, alvos):
        """alvos: [{"nome", "outros": [...], "cidade", "end", "origem"}]. Devolve cada alvo com
        os enderecos achados na mesma cidade (ou vazio)."""
        saida = []
        termos = {}
        for a in alvos:
            centro = RMC.get(a["cidade"], RMC["CURITIBA"])
            ts = {nomes.termo_de_busca(n, a["cidade"]) for n in [a["nome"]] + a.get("outros", [])}
            termos[id(a)] = [(t, centro) for t in ts if t]
        todos = {tc for v in termos.values() for tc in v}
        print(f"  busca por nome: {len(alvos)} alvos, {len(todos)} termos…", flush=True)
        with ThreadPoolExecutor(max(1, self.paralelo // 7)) as ex:
            voltas = dict(zip(todos, ex.map(lambda tc: self.busca_nome(*tc), todos)))
        for a in alvos:
            ids = set()
            for tc in termos[id(a)]:
                ids |= voltas.get(tc, set())
            achados = []
            for i in ids:
                x = self.fichas[i]
                if cidade_rmc(x) != a["cidade"]:
                    continue
                # o mesmo prestador: nome igual depois de normalizar (todas as palavras que o
                # identificam), ou o mesmo endereco com nome parecido
                nomes_x = [x.get("nomeFantasia"), x.get("razaoSocial")]
                todos = [a["nome"]] + a.get("outros", [])
                if any(nomes.mesmo_estrito(n, nomes_x, a["cidade"]) for n in todos) \
                        or (a.get("end") and mesmo_endereco(a["end"], endereco(x))
                            and any(nomes.nota(n, nomes_x, a["cidade"]) >= 0.5 for n in todos)):
                    achados.append(i)
            saida.append(dict(a, achados=sorted(achados)))
        return saida


# ---------------------------------------------------------------- endereco
def endereco(x):
    e = (x.get("listaEnderecos") or [{}])[0]
    return ", ".join(s.strip(" -") for s in (e.get("logradouro"), e.get("numero"), e.get("complemento"))
                     if s and s.strip(" -") not in ("", "0", "S/N", "SN"))


def _rua_numero(end):
    s = nomes.sem_acento(end or "")
    m = re.search(r"^(.*?)[, ]+(\d{1,5})\b", s)
    if not m:
        return None, None
    rua = [t for t in re.sub(r"[^A-Z0-9 ]", " ", m.group(1)).split()
           if t not in {"R", "RUA", "AV", "AVENIDA", "AL", "ALAMEDA", "TV", "TRAVESSA", "PC",
                        "PRACA", "ROD", "RODOVIA", "EST", "ESTRADA", "BR", "DE", "DA", "DO",
                        "DAS", "DOS", "E", "PROF", "DR", "DES", "GAL", "GEN", "CEL", "VER", "PE",
                        "ENG", "MAL", "BPO", "D", "DQ", "STA", "STO", "SAO", "SEN", "PRES", "CAP"}]
    return rua, m.group(2)


def mesmo_endereco(a, b):
    ra, na = _rua_numero(a)
    rb, nb = _rua_numero(b)
    if not ra or not rb or na != nb:
        return False
    return any(len(t) >= 4 and any(t == u or (min(len(t), len(u)) >= 5 and
                                              (t.startswith(u) or u.startswith(t))) for u in rb)
               for t in ra)


# ----------------------------------------------------------------- alvos
def alvos_do_comparativo(caminho):
    """Estabelecimentos que a Amil ou a SulAmerica lista na RMC e a Bradesco nao (no
    comparativo publicado)."""
    if not caminho or not Path(caminho).exists():
        return []
    texto = Path(caminho).read_text(encoding="utf-8")
    C = json.JSONDecoder().raw_decode(texto, texto.index("{", texto.index("window.COMPARATIVO")))[0]
    saida = []
    for c in C["cidades"]:
        if c["uf"] != UF:
            continue
        cidade = CHAVES_RMC.get(chave_cidade(c["nome"]))
        if not cidade:
            continue
        for l in C["rede"].get(c["k"], []):
            if l.get("m") and l["m"].strip("0"):
                continue            # a Bradesco ja tem
            outros = [n for n in (l.get("al"), l.get("nu")) if n and n != l["n"]]
            saida.append({"nome": l["n"], "outros": outros, "cidade": cidade,
                          "end": l.get("e") or "", "bairro": l.get("b") or "",
                          "origem": "comparativo"})
    return saida


def anteriores():
    """O que as coletas anteriores (consulta/PR) tinham na RMC: (cidade, codigo) -> item."""
    out = {}
    for cidade in RMC:
        f = PASTA / UF / f"{cidade}.json"
        if f.exists():
            for p in json.loads(f.read_text(encoding="utf-8"))["prestadores"]:
                out.setdefault((cidade, p["cod"]), dict(p, cidade=cidade))
    return out


# ------------------------------------------------------------------ saida
def item(x, at):
    e = (x.get("listaEnderecos") or [{}])[0]
    return {
        "cod": x["codigo"], "seq": (x.get("nome") or "").strip(),
        "nome": (x.get("nomeFantasia") or x.get("razaoSocial") or "").strip(),
        "razao": x.get("razaoSocial"), "cnpj": x.get("cnpj") or "",
        "bairro": (e.get("bairro") or "").strip(), "end": endereco(x),
        "cep": e.get("cep") or "",
        "tel": [f"{t['ddd']}{t['numero']}" for t in x.get("listaTelefones") or []],
        "lat": e.get("latitude"), "lon": e.get("longitude"),
        "qual": [q["descricao"] for q in x.get("listaQualificacoes") or []], "at": at}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paralelo", type=int, default=12)
    ap.add_argument("--comparativo", default=str(RAIZ.parent / "rede-amil-bradesco" /
                                                 "comparativo" / "dados.js"))
    ap.add_argument("--novo", action="store_true", help="descarta o cache antes")
    ap.add_argument("--sem-fichas", action="store_true",
                    help="nao pede a ficha de especialidades de cada estabelecimento")
    a = ap.parse_args()
    if a.novo:
        for f in CACHE.glob("*.jsonl"):
            f.unlink()

    D = dados_da_pagina()
    for i, c in enumerate(D["cid"]):
        if D["uf"][c[0]] == UF and c[1] in RMC:
            RMC[c[1]] = tuple(D["geo"][i])
    antes = anteriores()
    det = Detalhe(a.paralelo)
    inicio = time.time()

    # 1-3: grade, sedes e pontos novos onde houver prestador longe
    pontos = grade()
    for p in RMC.values():
        if min(km(p, q) for q in pontos) > 3:
            pontos.append(p)
    print(f"varredura: {len(pontos)} pontos iniciais", flush=True)
    pontos = det.cobrir(pontos)

    # 4: ficha de especialidades dos estabelecimentos
    if not a.sem_fichas:
        det.fichas_especialidades()

    # 5: busca por nome
    rmc = det.da_rmc()
    codigos_agora = {det.fichas[i]["codigo"] for i in rmc}
    alvos = alvos_do_comparativo(a.comparativo)
    sumiram = [p for (_, c), p in antes.items() if c not in codigos_agora]
    alvos += [{"nome": p["nome"], "outros": [p["razao"]] if p.get("razao") else [],
               "cidade": p["cidade"], "end": p.get("end", ""), "bairro": p.get("bairro", ""),
               "origem": "coleta anterior", "cod": p["cod"]} for p in sumiram]
    base = RAIZ / "ferramentas" / "base_anterior.json.gz"
    base = json.loads(gzip.decompress(base.read_bytes()))["cidades"] if base.exists() else {}
    for cidade in RMC:
        for b in base.get(f"{UF}|{cidade}", []):
            nomes_c = [(det.fichas[i].get("nomeFantasia"), det.fichas[i].get("razaoSocial"))
                       for i, c in rmc.items() if c == cidade]
            if not any(nomes.mesmo_estrito(b["nome"], list(n), cidade) for n in nomes_c):
                alvos.append({"nome": b["nome"], "outros": [], "cidade": cidade, "end": "",
                              "bairro": b["bairro"], "origem": "base anterior",
                              "fonte": (["buscador"] if b["buscador"] else []) +
                                       (["hospitais"] if b["hosp"] else []) +
                                       (["laboratorios"] if b["lab"] else [])})
    procurados = det.procurar(alvos) if alvos else []
    rmc = det.da_rmc()

    # saida por cidade
    hoje = date.today().isoformat()
    por_cidade = {c: [] for c in RMC}
    extras = 0
    for i, cidade in rmc.items():
        x = det.fichas[i]
        h = det.hits.get(i, {})
        if not h:
            continue
        # ficha de especialidades: o que a rede cadastra para o prestador naquele tipo e
        # a busca por especialidade nao alcanca, no endereco que aparece naquele tipo e rede
        for r, _ in REDES:
            bit = 1 << BIT[r]
            for t in {t for (t, e), m in h.items() if m & bit}:
                for e, desc in det.ficha_esp.get((r, x["codigo"], t)) or []:
                    det.nome_esp.setdefault(e, desc)
                    if not h.get((t, e), 0) & bit:
                        h[(t, e)] = h.get((t, e), 0) | bit
                        extras += 1
        at = {}
        for (tipo, esp), m in sorted(h.items()):
            if esp in det.nome_esp:
                at.setdefault(str(tipo), {})[det.nome_esp[esp]] = m
        if at:
            por_cidade[cidade].append(item(x, at))

    conferencia_por = {c: {"total": len(base.get(f"{UF}|{c}", [])), "itens": []} for c in RMC}
    for p in procurados:
        if p["origem"] != "base anterior":
            continue
        it = {"nome": p["nome"], "bairro": p["bairro"], "fonte": p["fonte"]}
        if p["achados"]:
            x = det.fichas[p["achados"][0]]
            it.update(achado=x["codigo"], nome_hoje=x.get("nomeFantasia"),
                      cidade_hoje=localidade(x)[0])
        conferencia_por[p["cidade"]]["itens"].append(it)
    for c, v in conferencia_por.items():
        v["varredura"] = v["total"] - len(v["itens"])

    PASTA.joinpath(UF).mkdir(parents=True, exist_ok=True)
    resumo = []
    for cidade, prest in por_cidade.items():
        f = PASTA / UF / f"{cidade}.json"
        if not prest:
            if f.exists():
                print(f"  atencao: {cidade} tinha arquivo e agora nao tem ninguem", flush=True)
            continue
        prest.sort(key=lambda p: (p["cod"], p["seq"]))
        sede = RMC[cidade]
        usados = [sede] + sorted((p for p in pontos if km(p, sede) <= 20 and p != sede),
                                 key=lambda p: km(p, sede))
        f.write_text(json.dumps({
            "cidade": cidade, "uf": UF, "data": hoje, "fonte": api.PAGINA,
            "detalhe": True, "pontos": [list(p) for p in usados],
            "redes": [r for r, _ in REDES], "prestadores": prest,
            "conferencia": conferencia_por[cidade]},
            ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        cods = {p["cod"] for p in prest}
        velhos = {c for (cid, c) in antes if cid == cidade}
        resumo.append({"cidade": cidade, "enderecos": len(prest), "prestadores": len(cods),
                       "antes": len(velhos), "novos": len(cods - velhos),
                       "sairam": len(velhos - cods)})
        print(f"{cidade}: {len(prest)} enderecos de {len(cods)} prestadores "
              f"(antes {len(velhos)}; {len(cods - velhos)} novos, {len(velhos - cods)} sairam)",
              flush=True)

    RELATORIO.write_text(json.dumps({
        "data": hoje, "pontos": [list(p) for p in pontos], "consultas": len(det.feitas),
        "incompletas": len(det.incompletas()),
        "fichas_especialidades": len(det.ficha_esp), "especialidades_da_ficha": extras,
        "cidades": resumo,
        "procurados": [{k: v for k, v in p.items() if k != "outros"} | {
            "achados": [{"id": i, "nome": det.fichas[i].get("nomeFantasia"),
                         "end": endereco(det.fichas[i])} for i in p["achados"]]}
            for p in procurados]}, ensure_ascii=False, indent=1), encoding="utf-8")
    ach = sum(1 for p in procurados if p["achados"])
    print(f"\n{len(pontos)} pontos, {len(det.feitas)} consultas "
          f"({len(det.incompletas())} incompletas), {extras} especialidades so da ficha; "
          f"busca por nome: {ach} de {len(procurados)} achados; "
          f"{time.time() - inicio:.0f}s", flush=True)


if __name__ == "__main__":
    main()
