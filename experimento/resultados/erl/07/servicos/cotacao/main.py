# arquivo: servicos/cotacao/main.py
import json
import os
import re
from datetime import datetime, timedelta

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

FAIXAS_CEP = [
    ("SP", 1000000, 19999999),
    ("RJ", 20000000, 28999999),
    ("ES", 29000000, 29999999),
    ("MG", 30000000, 39999999),
    ("BA", 40000000, 48999999),
    ("PR", 80000000, 87999999),
    ("SC", 88000000, 89999999),
    ("RS", 90000000, 99999999),
]

RE_CEP = re.compile(r"[0-9]{8}")
RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

_estado = {"cotacoes": {}, "contador": 0}


class ErroAPI(Exception):
    def __init__(self, status, erro, mensagem):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 422:
        return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem):
    raise ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request):
    try:
        bruto = await request.body()
        corpo = json.loads(bruto)
    except Exception:
        _invalido("Corpo JSON inválido.")
    if not isinstance(corpo, dict):
        _invalido("O corpo deve ser um objeto JSON.")
    return corpo


def _eh_int(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


def _teto_div(a, b):
    return -((-a) // b)


def _uf_do_cep(cep):
    numero = int(cep)
    for uf, inicio, fim in FAIXAS_CEP:
        if inicio <= numero <= fim:
            return uf
    return None


def _somar_dias_uteis(data, dias):
    atual = data
    restantes = dias
    while restantes > 0:
        atual = atual + timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _validar_corpo(corpo):
    for campo in ("cep_origem", "cep_destino"):
        if campo not in corpo:
            _invalido(f"Campo '{campo}' ausente.")
        valor = corpo[campo]
        if not isinstance(valor, str) or not RE_CEP.fullmatch(valor):
            _invalido(f"Campo '{campo}' deve ter exatamente 8 dígitos.")

    if "pacote" not in corpo or not isinstance(corpo["pacote"], dict):
        _invalido("Campo 'pacote' ausente ou inválido.")
    pacote = corpo["pacote"]
    limites = [
        ("peso_g", 1, 100000),
        ("comprimento_cm", 1, 200),
        ("largura_cm", 1, 200),
        ("altura_cm", 1, 200),
    ]
    for campo, minimo, maximo in limites:
        if campo not in pacote:
            _invalido(f"Campo 'pacote.{campo}' ausente.")
        valor = pacote[campo]
        if not _eh_int(valor) or valor < minimo or valor > maximo:
            _invalido(f"Campo 'pacote.{campo}' inválido.")

    if "data" not in corpo:
        _invalido("Campo 'data' ausente.")
    data_txt = corpo["data"]
    if not isinstance(data_txt, str) or not RE_DATA.fullmatch(data_txt):
        _invalido("Campo 'data' inválido.")
    try:
        data = datetime.strptime(data_txt, "%Y-%m-%d").date()
    except ValueError:
        _invalido("Campo 'data' inválido.")
    return data


def _buscar_transportadoras():
    url = os.environ["URL_TRANSPORTADORAS"] + "/transportadoras"
    try:
        resposta = httpx.get(url, params={"somente_ativas": "true"}, timeout=10.0)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    if resposta.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    try:
        dados = resposta.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    return dados


@app.post("/admin/reset")
def reset():
    _estado["cotacoes"].clear()
    _estado["contador"] = 0
    return Response(status_code=204)


@app.post("/cotacoes")
async def cotar(request: Request):
    corpo = await _ler_corpo(request)
    data = _validar_corpo(corpo)

    cep_origem = corpo["cep_origem"]
    cep_destino = corpo["cep_destino"]
    uf_origem = _uf_do_cep(cep_origem)
    uf_destino = _uf_do_cep(cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    transportadoras = _buscar_transportadoras()

    pacote = corpo["pacote"]
    peso_g = pacote["peso_g"]
    volume = pacote["comprimento_cm"] * pacote["largura_cm"] * pacote["altura_cm"]
    peso_cubado = _teto_div(volume, 6)
    peso_taxado = max(peso_g, peso_cubado)
    kg_cobrados = _teto_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino

    opcoes = []
    for t in transportadoras:
        if not t.get("ativa", False):
            continue
        ufs = t.get("ufs_atendidas", [])
        if uf_origem not in ufs or uf_destino not in ufs:
            continue
        if peso_taxado > t["peso_maximo_g"]:
            continue
        preco_base = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg_cobrados
        if interestadual:
            preco = _teto_div(preco_base * (100 + t["adicional_interestadual_pct"]), 100)
            prazo = t["prazo_dias_uteis"] + t["dias_adicionais_interestadual"]
        else:
            preco = preco_base
            prazo = t["prazo_dias_uteis"]
        data_prevista = _somar_dias_uteis(data, prazo)
        opcoes.append({
            "transportadora_id": t["id"],
            "nome": t["nome"],
            "preco_centavos": preco,
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": data_prevista.strftime("%Y-%m-%d"),
        })

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    _estado["contador"] += 1
    cid = "COT-%06d" % _estado["contador"]
    cotacao = {
        "id": cid,
        "cep_origem": cep_origem,
        "cep_destino": cep_destino,
        "uf_origem": uf_origem,
        "uf_destino": uf_destino,
        "peso_taxado_g": peso_taxado,
        "data": corpo["data"],
        "valida_ate": (data + timedelta(days=7)).strftime("%Y-%m-%d"),
        "opcoes": opcoes,
    }
    _estado["cotacoes"][cid] = cotacao
    return JSONResponse(status_code=201, content=cotacao)


@app.get("/cotacoes/{cid}")
def consultar(cid: str):
    cotacao = _estado["cotacoes"].get(cid)
    if cotacao is None:
        raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    return JSONResponse(status_code=200, content=cotacao)
