# arquivo: servicos/cotacao/main.py
import copy
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

RE_CEP = re.compile(r"[0-9]{8}")
RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

_estado = {"contador": 0, "cotacoes": {}}


class ErroAPI(Exception):
    def __init__(self, status, erro, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        erro = "NAO_ENCONTRADO"
    elif exc.status_code == 405:
        erro = "METODO_NAO_PERMITIDO"
    else:
        erro = "ERRO"
    return _resposta_erro(exc.status_code, erro, str(exc.detail))


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


def _int_faixa(obj, campo, minimo, maximo):
    if campo not in obj:
        raise _invalido(f"Campo '{campo}' ausente.")
    valor = obj[campo]
    if not _eh_int(valor) or valor < minimo or valor > maximo:
        raise _invalido(f"Campo '{campo}' deve ser inteiro entre {minimo} e {maximo}.")
    return valor


def _cep(corpo, campo):
    if campo not in corpo:
        raise _invalido(f"Campo '{campo}' ausente.")
    valor = corpo[campo]
    if not isinstance(valor, str) or not RE_CEP.fullmatch(valor):
        raise _invalido(f"Campo '{campo}' deve ter exatamente 8 dígitos.")
    return valor


def _uf_do_cep(cep):
    numero = int(cep)
    for uf, inicio, fim in FAIXAS_CEP:
        if inicio <= numero <= fim:
            return uf
    return None


def _teto_div(a, b):
    return -((-a) // b)


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
        resp = httpx.get(url, params={"somente_ativas": "true"}, timeout=10)
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras indisponível.")
    if resp.status_code != 200:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Serviço Transportadoras respondeu com erro.")
    try:
        dados = resp.json()
    except Exception:
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    if not isinstance(dados, list):
        raise ErroAPI(503, "SERVICO_INDISPONIVEL", "Resposta inválida do serviço Transportadoras.")
    return dados


@app.post("/cotacoes", status_code=201)
async def cotar(request: Request):
    corpo = await _ler_corpo(request)

    cep_origem = _cep(corpo, "cep_origem")
    cep_destino = _cep(corpo, "cep_destino")

    if "pacote" not in corpo:
        raise _invalido("Campo 'pacote' ausente.")
    pacote = corpo["pacote"]
    if not isinstance(pacote, dict):
        raise _invalido("Campo 'pacote' deve ser um objeto.")
    peso_g = _int_faixa(pacote, "peso_g", 1, 100000)
    comprimento = _int_faixa(pacote, "comprimento_cm", 1, 200)
    largura = _int_faixa(pacote, "largura_cm", 1, 200)
    altura = _int_faixa(pacote, "altura_cm", 1, 200)

    if "data" not in corpo:
        raise _invalido("Campo 'data' ausente.")
    data_txt = corpo["data"]
    if not isinstance(data_txt, str) or not RE_DATA.fullmatch(data_txt):
        raise _invalido("Campo 'data' deve estar no formato AAAA-MM-DD.")
    try:
        data_cotacao = date.fromisoformat(data_txt)
    except ValueError:
        raise _invalido("Campo 'data' não é uma data válida.")

    uf_origem = _uf_do_cep(cep_origem)
    uf_destino = _uf_do_cep(cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP de origem ou de destino fora da área atendida.")

    transportadoras = await run_in_threadpool(_buscar_transportadoras)

    peso_cubado = _teto_div(comprimento * largura * altura, 6)
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
        preco = t["tarifa_base_centavos"] + t["tarifa_kg_centavos"] * kg_cobrados
        prazo = t["prazo_dias_uteis"]
        if interestadual:
            preco = _teto_div(preco * (100 + t["adicional_interestadual_pct"]), 100)
            prazo = prazo + t["dias_adicionais_interestadual"]
        opcoes.append({
            "transportadora_id": t["id"],
            "nome": t["nome"],
            "preco_centavos": preco,
            "prazo_dias_uteis": prazo,
            "data_prevista_entrega": _somar_dias_uteis(data_cotacao, prazo).isoformat(),
        })

    opcoes.sort(key=lambda o: (o["preco_centavos"], o["prazo_dias_uteis"], o["transportadora_id"]))

    numero = _estado["contador"] + 1
    id_cotacao = f"COT-{numero:06d}"
    cotacao = {
        "id": id_cotacao,
        "cep_origem": cep_origem,
        "cep_destino": cep_destino,
        "uf_origem": uf_origem,
        "uf_destino": uf_destino,
        "peso_taxado_g": peso_taxado,
        "data": data_txt,
        "valida_ate": (data_cotacao + timedelta(days=7)).isoformat(),
        "opcoes": opcoes,
    }
    _estado["contador"] = numero
    _estado["cotacoes"][id_cotacao] = cotacao
    return copy.deepcopy(cotacao)


@app.get("/cotacoes/{id_cotacao}")
async def consultar_cotacao(id_cotacao: str):
    cotacao = _estado["cotacoes"].get(id_cotacao)
    if cotacao is None:
        raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    return copy.deepcopy(cotacao)


@app.post("/admin/reset", status_code=204)
async def reiniciar():
    _estado["contador"] = 0
    _estado["cotacoes"] = {}
    return Response(status_code=204)
