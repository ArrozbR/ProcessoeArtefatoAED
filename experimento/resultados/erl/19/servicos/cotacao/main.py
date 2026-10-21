# arquivo: servicos/cotacao/main.py
import copy
import os
import re
import threading
from datetime import date, timedelta

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

CEP_RE = re.compile(r"[0-9]{8}")
DATA_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

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


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


app = FastAPI()

_lock = threading.Lock()
_cotacoes: dict = {}
_contador = {"cotacao": 0}


def _erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.erro, exc.mensagem)


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
        raise _invalido("Corpo JSON inválido ou ausente.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    return corpo


def _e_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _int_faixa(obj: dict, campo: str, minimo: int, maximo: int) -> int:
    v = obj.get(campo)
    if not _e_int(v) or v < minimo or v > maximo:
        raise _invalido(f"Campo '{campo}' ausente ou fora das restrições.")
    return v


def _validar_cep(obj: dict, campo: str) -> str:
    v = obj.get(campo)
    if not isinstance(v, str) or not CEP_RE.fullmatch(v):
        raise _invalido(f"Campo '{campo}' deve ter exatamente 8 dígitos.")
    return v


def _validar_data(v) -> date:
    if not isinstance(v, str) or not DATA_RE.fullmatch(v):
        raise _invalido("Campo 'data' inválido.")
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise _invalido("Campo 'data' inválido.")


def _uf_do_cep(cep: str):
    n = int(cep)
    for uf, ini, fim in FAIXAS_CEP:
        if ini <= n <= fim:
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


def _buscar_transportadoras_ativas() -> list:
    url = os.environ["URL_TRANSPORTADORAS"] + "/transportadoras"
    try:
        resp = httpx.get(url, params={"somente_ativas": "true"}, timeout=10)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    if resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    try:
        dados = resp.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de transportadoras.")
    return dados


@app.post("/cotacoes")
async def cotar(request: Request):
    corpo = await _ler_corpo(request)
    cep_origem = _validar_cep(corpo, "cep_origem")
    cep_destino = _validar_cep(corpo, "cep_destino")
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        raise _invalido("Campo 'pacote' ausente ou inválido.")
    peso_g = _int_faixa(pacote, "peso_g", 1, 100000)
    comprimento = _int_faixa(pacote, "comprimento_cm", 1, 200)
    largura = _int_faixa(pacote, "largura_cm", 1, 200)
    altura = _int_faixa(pacote, "altura_cm", 1, 200)
    data_str = corpo.get("data")
    data = _validar_data(data_str)

    uf_origem = _uf_do_cep(cep_origem)
    uf_destino = _uf_do_cep(cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    transportadoras = _buscar_transportadoras_ativas()

    peso_cubado = _teto_div(comprimento * largura * altura, 6)
    peso_taxado = max(peso_g, peso_cubado)
    kg_cobrados = _teto_div(peso_taxado, 1000)
    interestadual = uf_origem != uf_destino

    opcoes = []
    for t in transportadoras:
        if not isinstance(t, dict):
            continue
        if not t.get("ativa", False):
            continue
        ufs = t.get("ufs_atendidas") or []
        if uf_origem not in ufs or uf_destino not in ufs:
            continue
        if peso_taxado > t["peso_maximo_g"]:
            continue
        preco_base = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg_cobrados
        prazo = t["prazo_dias_uteis"]
        if interestadual:
            preco = _teto_div(preco_base * (100 + t["adicional_interestadual_pct"]), 100)
            prazo += t["dias_adicionais_interestadual"]
        else:
            preco = preco_base
        opcoes.append({
            "transportadora_id": t["id"],
            "nome": t["nome"],
            "preco_centavos": preco,
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": _somar_dias_uteis(data, prazo).isoformat(),
        })
    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    with _lock:
        _contador["cotacao"] += 1
        cid = f"COT-{_contador['cotacao']:06d}"
        cotacao = {
            "id": cid,
            "cep_origem": cep_origem,
            "cep_destino": cep_destino,
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": data_str,
            "valida_ate": (data + timedelta(days=7)).isoformat(),
            "opcoes": opcoes,
        }
        _cotacoes[cid] = cotacao
        resposta = copy.deepcopy(cotacao)
    return JSONResponse(status_code=201, content=resposta)


@app.get("/cotacoes/{cid}")
async def consultar(cid: str):
    with _lock:
        c = _cotacoes.get(cid)
        if c is None:
            raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        resposta = copy.deepcopy(c)
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
async def reset():
    with _lock:
        _cotacoes.clear()
        _contador["cotacao"] = 0
    return Response(status_code=204)
