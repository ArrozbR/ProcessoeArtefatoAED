# arquivo: servicos/cotacao/main.py
import copy
import datetime
import os
import re

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

CEP_REGEX = re.compile(r"^[0-9]{8}$")
DATA_REGEX = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

_estado = {"cotacoes": {}, "contador": 0}


class ErroAPI(Exception):
    def __init__(self, status, codigo, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _resposta_erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 422:
        return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    return corpo


def _e_int(valor):
    return type(valor) is int


def _parse_data(texto):
    if not isinstance(texto, str) or not DATA_REGEX.match(texto):
        return None
    try:
        return datetime.date.fromisoformat(texto)
    except ValueError:
        return None


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
        atual = atual + datetime.timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _validar_corpo(corpo):
    cep_origem = corpo.get("cep_origem")
    cep_destino = corpo.get("cep_destino")
    if not isinstance(cep_origem, str) or not CEP_REGEX.match(cep_origem):
        raise _invalido("Campo 'cep_origem' inválido.")
    if not isinstance(cep_destino, str) or not CEP_REGEX.match(cep_destino):
        raise _invalido("Campo 'cep_destino' inválido.")
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        raise _invalido("Campo 'pacote' inválido.")
    peso = pacote.get("peso_g")
    if not _e_int(peso) or peso < 1 or peso > 100000:
        raise _invalido("Campo 'pacote.peso_g' inválido.")
    dims = []
    for campo in ("comprimento_cm", "largura_cm", "altura_cm"):
        v = pacote.get(campo)
        if not _e_int(v) or v < 1 or v > 200:
            raise _invalido(f"Campo 'pacote.{campo}' inválido.")
        dims.append(v)
    data_texto = corpo.get("data")
    data = _parse_data(data_texto)
    if data is None:
        raise _invalido("Campo 'data' inválido.")
    return cep_origem, cep_destino, peso, dims, data_texto, data


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


@app.post("/cotacoes")
async def cotar(request: Request):
    corpo = await _ler_corpo(request)
    cep_origem, cep_destino, peso, dims, data_texto, data = _validar_corpo(corpo)

    uf_origem = _uf_do_cep(cep_origem)
    uf_destino = _uf_do_cep(cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    transportadoras = _buscar_transportadoras()

    volume = dims[0] * dims[1] * dims[2]
    peso_cubado = -(-volume // 6)
    peso_taxado = max(peso, peso_cubado)
    kg_cobrados = -(-peso_taxado // 1000)
    interestadual = uf_origem != uf_destino

    opcoes = []
    for t in transportadoras:
        if not isinstance(t, dict):
            continue
        if t.get("ativa") is not True:
            continue
        ufs = t.get("ufs_atendidas") or []
        if uf_origem not in ufs or uf_destino not in ufs:
            continue
        if peso_taxado > t["peso_maximo_g"]:
            continue
        preco_base = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg_cobrados
        if interestadual:
            preco = -(-(preco_base * (100 + t["adicional_interestadual_pct"])) // 100)
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
            "data_prevista_entrega": data_prevista.isoformat(),
        })

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    numero = _estado["contador"] + 1
    cotacao = {
        "id": f"COT-{numero:06d}",
        "cep_origem": cep_origem,
        "cep_destino": cep_destino,
        "uf_origem": uf_origem,
        "uf_destino": uf_destino,
        "peso_taxado_g": peso_taxado,
        "data": data_texto,
        "valida_ate": (data + datetime.timedelta(days=7)).isoformat(),
        "opcoes": opcoes,
    }
    _estado["contador"] = numero
    _estado["cotacoes"][cotacao["id"]] = cotacao
    return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{id_cotacao}")
async def consultar_cotacao(id_cotacao: str):
    cotacao = _estado["cotacoes"].get(id_cotacao)
    if cotacao is None:
        raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    return JSONResponse(status_code=200, content=copy.deepcopy(cotacao))


@app.post("/admin/reset")
async def reset():
    _estado["cotacoes"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)
