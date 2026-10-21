# arquivo: servicos/cotacao/main.py
import math
import os
import re
from datetime import date, timedelta
from fractions import Fraction
from urllib.parse import quote

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

_cliente = httpx.Client(timeout=10.0)

_estado = {"contador": 0, "cotacoes": {}}


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
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resposta_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _invalido(mensagem="Dados inválidos."):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


def _indisponivel():
    return ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço de transportadoras indisponível.")


async def _ler_json(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo JSON inválido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    return corpo


def _eh_inteiro(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _parse_data(v):
    if not isinstance(v, str) or not RE_DATA.fullmatch(v):
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _uf_do_cep(cep):
    n = int(cep)
    for uf, inicio, fim in FAIXAS_CEP:
        if inicio <= n <= fim:
            return uf
    return None


def _somar_dias_uteis(inicio, dias):
    d = inicio
    restantes = dias
    while restantes > 0:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            restantes -= 1
    return d


def _como_fracao(v):
    if isinstance(v, float):
        return Fraction(str(v))
    return Fraction(v)


def _buscar_transportadoras_ativas():
    base = os.environ.get("URL_TRANSPORTADORAS", "")
    try:
        r = _cliente.get(f"{base}/transportadoras", params={"somente_ativas": "true"})
    except Exception:
        raise _indisponivel()
    if r.status_code != 200:
        raise _indisponivel()
    try:
        dados = r.json()
    except Exception:
        raise _indisponivel()
    if not isinstance(dados, list):
        raise _indisponivel()
    return dados


def _validar(corpo):
    for campo in ("cep_origem", "cep_destino"):
        v = corpo.get(campo)
        if not isinstance(v, str) or not RE_CEP.fullmatch(v):
            raise _invalido(f"Campo '{campo}' inválido.")
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        raise _invalido("Campo 'pacote' inválido.")
    peso = pacote.get("peso_g")
    if not _eh_inteiro(peso) or peso < 1 or peso > 100000:
        raise _invalido("Campo 'pacote.peso_g' inválido.")
    for campo in ("comprimento_cm", "largura_cm", "altura_cm"):
        v = pacote.get(campo)
        if not _eh_inteiro(v) or v < 1 or v > 200:
            raise _invalido(f"Campo 'pacote.{campo}' inválido.")
    data = _parse_data(corpo.get("data"))
    if data is None:
        raise _invalido("Campo 'data' inválido.")
    return data


@app.post("/admin/reset")
async def reset():
    _estado["contador"] = 0
    _estado["cotacoes"].clear()
    return Response(status_code=204)


@app.post("/cotacoes")
async def criar_cotacao(request: Request):
    corpo = await _ler_json(request)
    data = _validar(corpo)
    cep_origem = corpo["cep_origem"]
    cep_destino = corpo["cep_destino"]
    pacote = corpo["pacote"]

    uf_origem = _uf_do_cep(cep_origem)
    uf_destino = _uf_do_cep(cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    volume = pacote["comprimento_cm"] * pacote["largura_cm"] * pacote["altura_cm"]
    peso_cubado = (volume + 5) // 6
    peso_taxado = max(pacote["peso_g"], peso_cubado)
    quilos = (peso_taxado + 999) // 1000
    interestadual = uf_origem != uf_destino

    transportadoras = _buscar_transportadoras_ativas()

    opcoes = []
    for t in transportadoras:
        if not isinstance(t, dict):
            continue
        if not t.get("ativa", True):
            continue
        ufs = t.get("ufs_atendidas") or []
        if uf_origem not in ufs or uf_destino not in ufs:
            continue
        if t.get("peso_maximo_g", 0) < peso_taxado:
            continue
        preco = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * quilos
        prazo = t["prazo_dias_uteis"]
        if interestadual:
            adicional = _como_fracao(t["adicional_interestadual_pct"])
            preco = math.ceil(Fraction(preco) * (100 + adicional) / 100)
            prazo = prazo + t["dias_adicionais_interestadual"]
        opcoes.append({
            "transportadora_id": t["id"],
            "nome": t["nome"],
            "preco_centavos": int(preco),
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": _somar_dias_uteis(data, prazo).isoformat(),
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
        "data": data.isoformat(),
        "valida_ate": (data + timedelta(days=7)).isoformat(),
        "opcoes": opcoes,
    }
    _estado["contador"] = numero
    _estado["cotacoes"][cotacao["id"]] = cotacao
    return JSONResponse(status_code=201, content=cotacao)


@app.get("/cotacoes/{cotacao_id}")
async def obter_cotacao(cotacao_id: str):
    cotacao = _estado["cotacoes"].get(cotacao_id)
    if cotacao is None:
        raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    return JSONResponse(status_code=200, content=cotacao)
