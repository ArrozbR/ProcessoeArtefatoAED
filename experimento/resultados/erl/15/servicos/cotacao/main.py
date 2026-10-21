# arquivo: servicos/cotacao/main.py
import os
import re
from datetime import date, timedelta

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

_estado = {"contador": 0, "cotacoes": {}}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    return corpo


def _eh_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validar_int(obj: dict, campo: str, minimo: int, maximo: int) -> int:
    if campo not in obj:
        raise _invalido(f"Campo '{campo}' ausente.")
    v = obj[campo]
    if not _eh_int(v) or v < minimo or v > maximo:
        raise _invalido(f"Campo '{campo}' inválido.")
    return v


def _validar_cep(corpo: dict, campo: str) -> str:
    if campo not in corpo:
        raise _invalido(f"Campo '{campo}' ausente.")
    v = corpo[campo]
    if not isinstance(v, str) or not RE_CEP.fullmatch(v):
        raise _invalido(f"Campo '{campo}' deve ter exatamente 8 dígitos.")
    return v


def _validar_data(corpo: dict, campo: str) -> date:
    if campo not in corpo:
        raise _invalido(f"Campo '{campo}' ausente.")
    v = corpo[campo]
    if not isinstance(v, str) or not RE_DATA.fullmatch(v):
        raise _invalido(f"Campo '{campo}' deve estar no formato AAAA-MM-DD.")
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise _invalido(f"Campo '{campo}' não é uma data válida.")


def _uf_do_cep(cep: str):
    n = int(cep)
    for uf, inicio, fim in FAIXAS_CEP:
        if inicio <= n <= fim:
            return uf
    return None


def _teto_div(a: int, b: int) -> int:
    return -((-a) // b)


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    atual = inicio
    restantes = dias
    while restantes > 0:
        atual = atual + timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _obter_transportadoras() -> list:
    url = os.environ["URL_TRANSPORTADORAS"]
    try:
        resp = httpx.get(
            f"{url}/transportadoras",
            params={"somente_ativas": "true"},
            timeout=10.0,
        )
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    if resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    try:
        dados = resp.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    return dados


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["cotacoes"] = {}
    return Response(status_code=204)


@app.post("/cotacoes")
async def cotar(request: Request):
    corpo = await _ler_corpo(request)

    cep_origem = _validar_cep(corpo, "cep_origem")
    cep_destino = _validar_cep(corpo, "cep_destino")

    if "pacote" not in corpo or not isinstance(corpo["pacote"], dict):
        raise _invalido("Campo 'pacote' ausente ou inválido.")
    pacote = corpo["pacote"]
    peso_g = _validar_int(pacote, "peso_g", 1, 100000)
    comprimento = _validar_int(pacote, "comprimento_cm", 1, 200)
    largura = _validar_int(pacote, "largura_cm", 1, 200)
    altura = _validar_int(pacote, "altura_cm", 1, 200)

    data_cotacao = _validar_data(corpo, "data")

    uf_origem = _uf_do_cep(cep_origem)
    uf_destino = _uf_do_cep(cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP de origem ou de destino fora da área atendida.")

    transportadoras = _obter_transportadoras()

    peso_cubado = _teto_div(comprimento * largura * altura, 6)
    peso_taxado = max(peso_g, peso_cubado)
    kg_cobrados = _teto_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino

    opcoes = []
    for t in transportadoras:
        if not isinstance(t, dict):
            continue
        if not t.get("ativa", True):
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

    _estado["contador"] += 1
    cid = f"COT-{_estado['contador']:06d}"
    cotacao = {
        "id": cid,
        "cep_origem": cep_origem,
        "cep_destino": cep_destino,
        "uf_origem": uf_origem,
        "uf_destino": uf_destino,
        "peso_taxado_g": peso_taxado,
        "data": corpo["data"],
        "valida_ate": (data_cotacao + timedelta(days=7)).isoformat(),
        "opcoes": opcoes,
    }
    _estado["cotacoes"][cid] = cotacao
    return JSONResponse(status_code=201, content=cotacao)


@app.get("/cotacoes/{cid}")
async def consultar(cid: str):
    cotacao = _estado["cotacoes"].get(cid)
    if cotacao is None:
        raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    return JSONResponse(status_code=200, content=cotacao)
