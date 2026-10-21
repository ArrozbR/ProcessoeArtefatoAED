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

app = FastAPI()

RE_CEP = re.compile(r"[0-9]{8}")
RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

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

_lock = threading.Lock()
_cotacoes: dict = {}
_contador = {"cotacao": 0}


class ErroApi(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
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
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados da requisição inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _invalido(mensagem: str) -> ErroApi:
    return ErroApi(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _eh_int(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _teto_div(a: int, b: int) -> int:
    return -((-a) // b)


def _parse_data(valor) -> date:
    if not isinstance(valor, str) or not RE_DATA.fullmatch(valor):
        raise _invalido("Data inválida; use AAAA-MM-DD.")
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise _invalido("Data inválida; use AAAA-MM-DD.")


def _validar_corpo(corpo: dict) -> dict:
    for campo in ("cep_origem", "cep_destino"):
        valor = corpo.get(campo)
        if not isinstance(valor, str) or not RE_CEP.fullmatch(valor):
            raise _invalido(f"Campo '{campo}' deve ter exatamente 8 dígitos.")
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        raise _invalido("Campo 'pacote' ausente ou inválido.")
    peso = pacote.get("peso_g")
    if not _eh_int(peso) or peso < 1 or peso > 100000:
        raise _invalido("Campo 'pacote.peso_g' deve ser inteiro entre 1 e 100000.")
    for campo in ("comprimento_cm", "largura_cm", "altura_cm"):
        valor = pacote.get(campo)
        if not _eh_int(valor) or valor < 1 or valor > 200:
            raise _invalido(f"Campo 'pacote.{campo}' deve ser inteiro entre 1 e 200.")
    data = _parse_data(corpo.get("data"))
    return {
        "cep_origem": corpo["cep_origem"],
        "cep_destino": corpo["cep_destino"],
        "peso_g": peso,
        "comprimento_cm": pacote["comprimento_cm"],
        "largura_cm": pacote["largura_cm"],
        "altura_cm": pacote["altura_cm"],
        "data_str": corpo["data"],
        "data": data,
    }


def _uf_do_cep(cep: str):
    numero = int(cep)
    for uf, inicio, fim in FAIXAS_CEP:
        if inicio <= numero <= fim:
            return uf
    return None


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    atual = inicio
    restantes = dias
    while restantes > 0:
        atual = atual + timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _buscar_transportadoras_ativas() -> list:
    try:
        url = os.environ["URL_TRANSPORTADORAS"]
        resposta = httpx.get(
            f"{url}/transportadoras",
            params={"somente_ativas": "true"},
            timeout=10.0,
        )
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    if resposta.status_code != 200:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras respondeu com erro.")
    try:
        dados = resposta.json()
    except Exception:
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    if not isinstance(dados, list):
        raise ErroApi(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    return dados


@app.post("/cotacoes")
async def cotar(request: Request):
    corpo = await _ler_corpo(request)
    entrada = _validar_corpo(corpo)

    uf_origem = _uf_do_cep(entrada["cep_origem"])
    uf_destino = _uf_do_cep(entrada["cep_destino"])
    if uf_origem is None or uf_destino is None:
        raise ErroApi(422, "CEP_FORA_DA_AREA", "CEP de origem ou de destino fora da área atendida.")

    transportadoras = _buscar_transportadoras_ativas()

    volume = entrada["comprimento_cm"] * entrada["largura_cm"] * entrada["altura_cm"]
    peso_cubado = _teto_div(volume, 6)
    peso_taxado = max(entrada["peso_g"], peso_cubado)
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
        data_prevista = _somar_dias_uteis(entrada["data"], prazo)
        opcoes.append({
            "transportadora_id": t["id"],
            "nome": t["nome"],
            "preco_centavos": preco,
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": data_prevista.isoformat(),
        })

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    valida_ate = (entrada["data"] + timedelta(days=7)).isoformat()

    with _lock:
        _contador["cotacao"] += 1
        cid = f"COT-{_contador['cotacao']:06d}"
        cotacao = {
            "id": cid,
            "cep_origem": entrada["cep_origem"],
            "cep_destino": entrada["cep_destino"],
            "uf_origem": uf_origem,
            "uf_destino": uf_destino,
            "peso_taxado_g": peso_taxado,
            "data": entrada["data_str"],
            "valida_ate": valida_ate,
            "opcoes": opcoes,
        }
        _cotacoes[cid] = cotacao
        return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{cid}")
async def consultar_cotacao(cid: str):
    with _lock:
        cotacao = _cotacoes.get(cid)
        if cotacao is None:
            raise ErroApi(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
        return JSONResponse(status_code=200, content=copy.deepcopy(cotacao))


@app.post("/admin/reset")
async def reset():
    with _lock:
        _cotacoes.clear()
        _contador["cotacao"] = 0
    return Response(status_code=204)
