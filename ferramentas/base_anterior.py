"""Guarda a base de antes da consulta oficial, cidade a cidade.

E a referencia de conferencia: a planilha do buscador (set/2026) e as listas
oficiais de hospitais e laboratorios em PDF. Tudo o que estava nelas tem de
aparecer na consulta oficial; o coletar.py procura pelo nome quem nao veio na
varredura, e o montar.py mantem na pagina, com etiqueta, quem nao for achado.

Gera ferramentas/base_anterior.json.gz a partir do index.html de um commit
anterior a consulta (padrao: ba3f9c3, base setembro/2026).

Uso: python3 ferramentas/base_anterior.py [commit]
"""
import gzip
import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SAIDA = RAIZ / "ferramentas" / "base_anterior.json.gz"


def main():
    ref = sys.argv[1] if len(sys.argv) > 1 else "ba3f9c3"
    html = subprocess.run(["git", "-C", str(RAIZ), "show", f"{ref}:index.html"],
                          capture_output=True, check=True).stdout.decode("utf-8")
    i = html.index("const DADOS = ") + len("const DADOS = ")
    D = json.JSONDecoder().raw_decode(html, i)[0]
    cidades = {}
    for p in D["pr"]:
        c = D["cid"][p[1]]
        extra = p[6] if len(p) > 6 and p[6] else [0, 0, 0]
        cidades.setdefault(f"{D['uf'][c[0]]}|{c[1]}", []).append({
            "nome": D["nom"][p[0]], "bairro": D["bai"][p[2]], "cod": p[3], "tipo": p[4],
            "esp": [[D["esp"][p[5][k]], p[5][k + 1]] for k in range(0, len(p[5]), 2)],
            # o que as listas em PDF dizem: servico de internacao por produto e exames
            "hosp": [[D["serv"][v] for v in bl] for bl in extra[0]] if extra[0] else [],
            "lab": extra[1] or 0,
            "buscador": extra[2] != 1})
    SAIDA.write_bytes(gzip.compress(json.dumps({
        "commit": ref, "ref": D["ref"], "fonte": D["fonte"], "planos": D["planos"],
        "hospRef": D.get("hospRef", {}), "labRef": D.get("labRef", {}),
        "cidades": cidades}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9))
    print(f"{sum(len(v) for v in cidades.values())} prestadores em {len(cidades)} cidades -> {SAIDA}")


if __name__ == "__main__":
    main()
