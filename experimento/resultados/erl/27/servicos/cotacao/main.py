# arquivo: servicos/cotacao/main.py
import json
import os
import re
from datetime import date, timedelta

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

CEP_REGEX = re.compile(r"^[0-9]{8}$")
DATA_REGEX = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

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

_estado = {"cotacoes": {}, "contador": 0}


class ErroApi(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def _tratar_erro_api(request: Request, exc: ErroApi):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str = "Dados inválidos na requisição.") -> ErroApi:
    return ErroApi(422, "DADOS_INVALIDOS", mensagem)


async def _ler_json(request: Request) -> dict:
    corpo = await request.body()
    try:
        dados = json.loads(corpo)
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(dados, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    return dados


def _eh_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _teto_div(a: int, b: int) -> int:
    return -((-a) // b)


def _parse_data(valor):
    if not isinstance(valor, str) or not DATA_REGEX.match(valor):
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _uf_do_cep(cep: str):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
            return uf
    return None


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    d = inicio
    restantes = dias
    while restantes > 0:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            restantes -= 1
    return d


def _validar_corpo(dados: dict):
    for c in ("cep_origem", "cep_destino", "pacote", "data"):
        if c not in dados:
            raise _invalido(f"Campo obrigatório ausente: {c}.")
    for c in ("cep_origem", "cep_destino"):
        v = dados[c]
        if not isinstance(v, str) or not CEP_REGEX.match(v):
            raise _invalido(f"Campo {c} deve ter exatamente 8 dígitos.")
    pacote = dados["pacote"]
    if not isinstance(pacote, dict):
        raise _invalido("Campo pacote inválido.")
    if "peso_g" not in pacote:
        raise _invalido("Campo pacote.peso_g ausente.")
    peso = pacote["peso_g"]
    if not _eh_int(peso) or peso < 1 or peso > 100000:
        raise _invalido("Campo pacote.peso_g inválido.")
    for c in ("comprimento_cm", "largura_cm", "altura_cm"):
        if c not in pacote:
            raise _invalido(f"Campo pacote.{c} ausente.")
        v = pacote[c]
        if not _eh_int(v) or v < 1 or v > 200:
            raise _invalido(f"Campo pacote.{c} inválido.")
    d = _parse_data(dados["data"])
    if d is None:
        raise _invalido("Campo data inválido.")
    return d


def _buscar_transportadoras_ativas() -> list:
    url = os.environ["URL_TRANSPORTADORAS"] + "/transportadoras"
    try:
        resp = httpx.get(url, params={"somente_ativas": "true"}, timeout=10.0)
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    if resp.status_code != 200:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    try:
        lista = resp.json()
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    if not isinstance(lista, list):
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    return lista


@app.post("/cotacoes")
async def cotar(request: Request):
    dados = await _ler_json(request)
    data_cotacao = _validar_corpo(dados)

    cep_origem = dados["cep_origem"]
    cep_destino = dados["cep_destino"]
    uf_origem = _uf_do_cep(cep_origem)
    uf_destino = _uf_do_cep(cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroApi(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    transportadoras = _buscar_transportadoras_ativas()

    pacote = dados["pacote"]
    peso_cubado = _teto_div(pacote["comprimento_cm"] * pacote["largura_cm"] * pacote["altura_cm"], 6)
    peso_taxado = max(pacote["peso_g"], peso_cubado)
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
        data_prevista = _somar_dias_uteis(data_cotacao, prazo)
        opcoes.append(
            {
                "transportadora_id": t["id"],
                "nome": t["nome"],
                "preco_centavos": preco,
                "prazo_dias_uteis": prazo,
                "data_prevista_entrega": data_prevista.isoformat(),
            }
        )

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    numero = _estado["contador"] + 1
    cotacao = {
        "id": f"COT-{numero:06d}",
        "cep_origem": cep_origem,
        "cep_destino": cep_destino,
        "uf_origem": uf_origem,
        "uf_destino": uf_destino,
        "peso_taxado_g": peso_taxado,
        "data": dados["data"],
        "valida_ate": (data_cotacao + timedelta(days=7)).isoformat(),
        "opcoes": opcoes,
    }
    _estado["contador"] = numero
    _estado["cotacoes"][cotacao["id"]] = cotacao
    return JSONResponse(status_code=201, content=cotacao)


@app.get("/cotacoes/{cid}")
async def consultar(cid: str):
    c = _estado["cotacoes"].get(cid)
    if c is None:
        raise ErroApi(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    return JSONResponse(status_code=200, content=c)


@app.post("/admin/reset")
async def reset():
    _estado["cotacoes"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)
