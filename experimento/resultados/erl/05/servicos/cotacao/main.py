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
RE_CEP = re.compile(r"^[0-9]{8}$")
RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

_estado = {"contador": 0, "cotacoes": {}}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


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


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


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


def _validar_int(obj: dict, campo: str, minimo: int, maximo: int) -> int:
    valor = obj.get(campo)
    if campo not in obj or not _eh_int(valor):
        raise _invalido(f"Campo '{campo}' ausente ou não é inteiro.")
    if valor < minimo or valor > maximo:
        raise _invalido(f"Campo '{campo}' fora dos limites permitidos.")
    return valor


def _validar_data(valor) -> date:
    if not isinstance(valor, str) or not RE_DATA.fullmatch(valor):
        raise _invalido("Campo 'data' deve estar no formato AAAA-MM-DD.")
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise _invalido("Campo 'data' não é uma data válida.")


def _uf_do_cep(cep: str):
    numero = int(cep)
    for uf, inicio, fim in FAIXAS_CEP:
        if inicio <= numero <= fim:
            return uf
    return None


def _teto_div(a: int, b: int) -> int:
    return -(-a // b)


def _somar_dias_uteis(inicio: date, dias: int) -> date:
    atual = inicio
    restantes = dias
    while restantes > 0:
        atual = atual + timedelta(days=1)
        if atual.weekday() < 5:
            restantes -= 1
    return atual


def _buscar_transportadoras_ativas() -> list:
    url = os.environ["URL_TRANSPORTADORAS"]
    try:
        resposta = httpx.get(
            f"{url}/transportadoras",
            params={"somente_ativas": "true"},
            timeout=10.0,
        )
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


@app.post("/cotacoes", status_code=201)
async def cotar(request: Request):
    corpo = await _ler_corpo(request)

    cep_origem = corpo.get("cep_origem")
    if not isinstance(cep_origem, str) or not RE_CEP.fullmatch(cep_origem):
        raise _invalido("Campo 'cep_origem' deve ter exatamente 8 dígitos.")
    cep_destino = corpo.get("cep_destino")
    if not isinstance(cep_destino, str) or not RE_CEP.fullmatch(cep_destino):
        raise _invalido("Campo 'cep_destino' deve ter exatamente 8 dígitos.")
    pacote = corpo.get("pacote")
    if not isinstance(pacote, dict):
        raise _invalido("Campo 'pacote' ausente ou inválido.")
    peso_g = _validar_int(pacote, "peso_g", 1, 100000)
    comprimento = _validar_int(pacote, "comprimento_cm", 1, 200)
    largura = _validar_int(pacote, "largura_cm", 1, 200)
    altura = _validar_int(pacote, "altura_cm", 1, 200)
    data_txt = corpo.get("data")
    data_cotacao = _validar_data(data_txt)

    uf_origem = _uf_do_cep(cep_origem)
    uf_destino = _uf_do_cep(cep_destino)
    if uf_origem is None or uf_destino is None:
        raise ErroAPI(422, "CEP_FORA_DA_AREA", "CEP de origem ou de destino fora da área atendida.")

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

    novo_numero = _estado["contador"] + 1
    cotacao = {
        "id": f"COT-{novo_numero:06d}",
        "cep_origem": cep_origem,
        "cep_destino": cep_destino,
        "uf_origem": uf_origem,
        "uf_destino": uf_destino,
        "peso_taxado_g": peso_taxado,
        "data": data_txt,
        "valida_ate": (data_cotacao + timedelta(days=7)).isoformat(),
        "opcoes": opcoes,
    }
    _estado["contador"] = novo_numero
    _estado["cotacoes"][cotacao["id"]] = cotacao
    return JSONResponse(status_code=201, content=cotacao)


@app.get("/cotacoes/{cid}")
async def consultar_cotacao(cid: str):
    cotacao = _estado["cotacoes"].get(cid)
    if cotacao is None:
        raise ErroAPI(404, "COTACAO_NAO_ENCONTRADA", "Cotação não encontrada.")
    return JSONResponse(status_code=200, content=cotacao)


@app.post("/admin/reset", status_code=204)
async def reset():
    _estado["contador"] = 0
    _estado["cotacoes"] = {}
    return Response(status_code=204)
