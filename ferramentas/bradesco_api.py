"""Cliente da consulta de rede referenciada do site da Bradesco Seguros.

E a mesma API que a pagina publica usa
(https://www.bradescoseguros.com.br/clientes/produtos/plano-saude/consulta-de-rede-referenciada/):
o portal entrega um token de visitante e a busca devolve os prestadores de uma
rede num raio de 20 km de um ponto, para ate 5 especialidades por vez.

So leitura; nenhum dado de beneficiario e usado.
"""
import json
import threading
import time
import urllib.error
import urllib.request

PORTAL = "https://www.bradescoseguros.com.br"
PAGINA = PORTAL + "/clientes/produtos/plano-saude/consulta-de-rede-referenciada/"
BFF = "/bssi-bff-busca-rede-refer"
CABECALHO = {"User-Agent": "Mozilla/5.0 (rede-bradesco; consulta de rede)",
             "Origin": PORTAL, "Referer": PAGINA}

_token = {"acesso": None, "chave": None, "base": None, "quando": 0.0}
_trava = threading.Lock()


def _pede(url, corpo=None, cabecalho=None, bruto=False):
    h = dict(CABECALHO)
    h.update(cabecalho or {})
    dados = None
    if corpo is not None:
        dados = json.dumps(corpo).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=dados, headers=h,
                                 method="POST" if dados else "GET")
    with urllib.request.urlopen(req, timeout=90) as r:
        texto = r.read().decode()
    return texto if bruto else json.loads(texto)


def autentica(forcar=False):
    """Token de visitante, como o navegador pega. Vale 15 min; renova aos 10."""
    with _trava:
        if not forcar and _token["acesso"] and time.time() - _token["quando"] < 600:
            return
        if not _token["base"]:
            url = _pede(PORTAL + "/PA_BSSI-ScosPortal-PU/datapower/url/externo", bruto=True)
            _token["base"] = url.strip().strip('"').replace(":443", "").rstrip("/") + "/V2"
        cofre = "cell/persistent/str/bssi-portal-institucional/"
        r = _pede(PORTAL + "/PA_BSSI-ScosPortal-PU/datapower/autenticar", {
            "client_id": cofre + "client_id", "client_secret": cofre + "client_secret",
            "grant_type": cofre + "grant_type", "scope": cofre + "scope",
            "xApiKey": cofre + "xapikey", "datapowerUrl": _token["base"] + "/Auth"})
        _token.update(acesso=r["accessToken"], chave=r["xApiKey"], quando=time.time())


def chama(caminho, corpo=None):
    """GET (sem corpo) ou POST na API da busca. 404 vira None."""
    for tentativa in range(6):
        autentica()
        url = _token["base"] + BFF + caminho + "?x-api-key=" + _token["chave"]
        try:
            return _pede(url, corpo, {"Authorization": "Bearer " + _token["acesso"]})
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 400:
                raise RuntimeError(e.read().decode()[:300])
            if e.code in (401, 403):
                autentica(forcar=True)
            if tentativa == 5:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if tentativa == 5:
                raise
        time.sleep(2 ** tentativa)


def redes():
    return {int(r["codigoRede"]): r["nomeRede"] for r in chama("/obterredes")}


def tipos_atendimento():
    return [{k: v for k, v in t.items() if k != "icone"}
            for t in chama("/colecoes", {"colecao": "tiposAtendimento"})]


def especialidades(tipo):
    return chama("/especialidades", {"tipoAtendimento": tipo})


def busca(rede, tipo, esps, lat, lon):
    """Prestadores da rede num raio de 20 km de (lat, lon), para ate 5 especialidades."""
    r = chama("/prestadores/especialidade", {
        "codigoRede": rede, "listaEspecialidades": list(esps),
        "codigoTipoAcomodacao": "Q",   # a acomodacao nao muda o resultado; o site manda "Q"
        "enderecoConsulta": {"latitude": lat, "longitude": lon},
        "instaAdapt": "N", "totalPaginas": 1, "codigoTipoEstabelecimento": tipo})
    return (r or {}).get("listaReferenciados") or []
