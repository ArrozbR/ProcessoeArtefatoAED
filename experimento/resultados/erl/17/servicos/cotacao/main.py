# arquivo: servicos/cotacao/main.py
import os
import re
from datetime import date, timedelta

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
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

RE_CEP = re.compile(r"^[0-9]{8}$")
RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

_estado = {"contador": 0, "cotacoes": {}}


class ErroAPI(Exception):
    def __init__(self, status, erro, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _resposta_erro(404, "RECURSO_NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _int(corpo, campo, minimo, maximo):
    if campo not in corpo:
        raise _invalido(f"Campo '{campo}' ausente.")
    valor = corpo[campo]
    if isinstance(valor, bool):
        raise _invalido(f"Campo '{campo}' deve ser inteiro.")
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    if not isinstance(valor, int):
        raise _invalido(f"Campo '{campo}' deve ser inteiro.")
    if valor < minimo or valor > maximo:
        raise _invalido(f"Campo '{campo}' fora dos limites.")
    return valor


def _cep(corpo, campo):
    valor = corpo.get(campo)
    if not isinstance(valor, str) or not RE_CEP.match(valor):
        raise _invalido(f"Campo '{campo}' deve ter exatamente 8 dígitos.")
    return valor


def _data(valor, campo):
    if not isinstance(valor, str) or not RE_DATA.match(valor):
        raise _invalido(f"Campo '{campo}' deve ser uma data AAAA-MM-DD.")
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise _invalido(f"Campo '{campo}' não é uma data válida.")


def _uf_do_cep(cep):
    numero = int(cep)
    for uf, inicio, fim in FAIXAS_CEP:
        if inicio <= numero <= fim:
            return uf
    return None


def _teto_div(a, b):
    return -(-a // b)


def _somar_dias_uteis(inicio, dias):
    atual = inicio
    contados = 0
    while contados < dias:
        atual = atual + timedelta(days=1)
        if atual.weekday() < 5:
            contados += 1
    return atual


def _buscar_transportadoras():
    url = os.environ["URL_TRANSPORTADORAS"] + "/transportadoras"
    try:
        resposta = httpx.get(url, params={"somente_ativas": "true"}, timeout=10.0)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    if resposta.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras respondeu com erro.")
    try:
        dados = resposta.json()
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

    cep_origem = _cep(corpo, "cep_origem")
    cep_destino = _cep(corpo, "cep_destino")
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        raise _invalido("Campo 'pacote' ausente ou inválido.")
    peso_g = _int(pacote, "peso_g", 1, 100000)
    comprimento = _int(pacote, "comprimento_cm", 1, 200)
    largura = _int(pacote, "largura_cm", 1, 200)
    altura = _int(pacote, "altura_cm", 1, 200)
    data_str = corpo.get("data")
    data_cot = _data(data_str, "data")

    uf_origem = _uf_do_cep(cep_origem)
    uf_destino = _uf_do_cep(cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP fora da área atendida.")

    transportadoras = await run_in_threadpool(_buscar_transportadoras)

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
        if interestadual:
            preco = _teto_div(preco_base * (100 + t["adicional_interestadual_pct"]), 100)
            prazo = t["prazo_dias_uteis"] + t["dias_adicionais_interestadual"]
        else:
            preco = preco_base
            prazo = t["prazo_dias_uteis"]
        data_prevista = _somar_dias_uteis(data_cot, prazo)
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
        "data": data_str,
        "valida_ate": (data_cot + timedelta(days=7)).isoformat(),
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
