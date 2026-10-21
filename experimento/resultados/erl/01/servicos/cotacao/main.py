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

RE_CEP = re.compile(r"[0-9]{8}")
RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

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
        return _resposta_erro(404, "ROTA_NAO_ENCONTRADA", "Rota não encontrada.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _eh_int(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


def _validar_data(texto):
    if not isinstance(texto, str) or not RE_DATA.fullmatch(texto):
        return None
    try:
        return datetime.datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        return None


def _validar_corpo(corpo):
    for campo in ("cep_origem", "cep_destino"):
        valor = corpo.get(campo)
        if not isinstance(valor, str) or not RE_CEP.fullmatch(valor):
            raise _invalido(f"Campo '{campo}' deve ter exatamente 8 dígitos.")
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        raise _invalido("Campo 'pacote' ausente ou inválido.")
    peso = pacote.get("peso_g")
    if not _eh_int(peso) or peso < 1 or peso > 100000:
        raise _invalido("Campo 'pacote.peso_g' deve ser inteiro de 1 a 100000.")
    for campo in ("comprimento_cm", "largura_cm", "altura_cm"):
        valor = pacote.get(campo)
        if not _eh_int(valor) or valor < 1 or valor > 200:
            raise _invalido(f"Campo 'pacote.{campo}' deve ser inteiro de 1 a 200.")
    data = _validar_data(corpo.get("data"))
    if data is None:
        raise _invalido("Campo 'data' deve ser uma data válida AAAA-MM-DD.")
    return data


def _uf_do_cep(cep):
    numero = int(cep)
    for uf, inicio, fim in FAIXAS_CEP:
        if inicio <= numero <= fim:
            return uf
    return None


def _teto_div(a, b):
    return -(-a // b)


def _somar_dias_uteis(data, dias):
    atual = data
    restantes = dias
    while restantes > 0:
        atual = atual + datetime.timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _buscar_transportadoras_ativas():
    base = os.environ["URL_TRANSPORTADORAS"]
    try:
        resposta = httpx.get(
            f"{base}/transportadoras",
            params={"somente_ativas": "true"},
            timeout=10.0,
        )
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    if resposta.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")
    try:
        dados = resposta.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço de transportadoras.")
    return dados


@app.post("/cotacoes")
async def cotar(request: Request):
    corpo = await _ler_corpo(request)
    data = _validar_corpo(corpo)
    cep_origem = corpo["cep_origem"]
    cep_destino = corpo["cep_destino"]
    pacote = corpo["pacote"]

    uf_origem = _uf_do_cep(cep_origem)
    uf_destino = _uf_do_cep(cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP de origem ou de destino fora da área atendida.")

    transportadoras = _buscar_transportadoras_ativas()

    volume = pacote["comprimento_cm"] * pacote["largura_cm"] * pacote["altura_cm"]
    peso_cubado = _teto_div(volume, 6)
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
        prazo = t["prazo_dias_uteis"]
        if interestadual:
            preco = _teto_div(preco_base * (100 + t["adicional_interestadual_pct"]), 100)
            prazo = prazo + t["dias_adicionais_interestadual"]
        else:
            preco = preco_base
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
        "data": corpo["data"],
        "valida_ate": (data + datetime.timedelta(days=7)).isoformat(),
        "opcoes": opcoes,
    }
    _estado["contador"] = numero
    _estado["cotacoes"][cotacao["id"]] = cotacao
    return JSONResponse(status_code=201, content=copy.deepcopy(cotacao))


@app.get("/cotacoes/{ident}")
async def consultar_cotacao(ident: str):
    cotacao = _estado["cotacoes"].get(ident)
    if cotacao is None:
        raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    return JSONResponse(status_code=200, content=copy.deepcopy(cotacao))


@app.post("/admin/reset")
async def reset():
    _estado["cotacoes"] = {}
    _estado["contador"] = 0
    return Response(status_code=204)
