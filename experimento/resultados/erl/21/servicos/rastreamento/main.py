# arquivo: servicos/rastreamento/main.py
import copy
import re
from datetime import date

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado = {"rastreios": {}, "processados": set()}


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem="Dados inválidos."):
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request, exc):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request, exc):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


_INVALIDO = object()


async def ler_json(request):
    try:
        return await request.json()
    except Exception:
        return _INVALIDO


def chave_evento(valor):
    if isinstance(valor, str):
        return valor
    return repr(valor)


def nao_processado():
    return JSONResponse(status_code=200, content={"processado": False})


def processado():
    return JSONResponse(status_code=200, content={"processado": True})


def data_valida(s):
    if not RE_DATA.fullmatch(s):
        return False
    try:
        date.fromisoformat(s)
    except ValueError:
        return False
    return True


@app.post("/admin/reset")
async def reset():
    _estado["rastreios"] = {}
    _estado["processados"] = set()
    return Response(status_code=204)


@app.post("/eventos")
async def eventos(request: Request):
    corpo = await ler_json(request)
    if corpo is _INVALIDO or not isinstance(corpo, dict):
        return dados_invalidos("Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            return dados_invalidos(f"Envelope sem o campo '{campo}'.")

    chave = chave_evento(corpo["id"])
    if chave in _estado["processados"]:
        return nao_processado()

    tipo = corpo["tipo"]
    dados = corpo["dados"]

    if tipo == "envio.criado":
        if not isinstance(dados, dict):
            return dados_invalidos("Campo 'dados' inválido.")
        try:
            codigo = dados["codigo_rastreio"]
            rastreio = {
                "codigo_rastreio": codigo,
                "envio_id": dados["envio_id"],
                "pedido_id": dados["pedido_id"],
                "transportadora_id": dados["transportadora_id"],
                "data_prevista_entrega": dados["data_prevista_entrega"],
                "historico": [
                    {"status": "CRIADO", "data_hora": dados["data_hora"], "local": None}
                ],
            }
        except (KeyError, TypeError):
            return dados_invalidos("Campo 'dados' incompleto.")
        _estado["rastreios"][codigo] = rastreio
        _estado["processados"].add(chave)
        return processado()

    if tipo == "envio.status_atualizado":
        if not isinstance(dados, dict):
            return dados_invalidos("Campo 'dados' inválido.")
        codigo = dados.get("codigo_rastreio")
        if not isinstance(codigo, str) or codigo not in _estado["rastreios"]:
            return nao_processado()
        try:
            item = {
                "status": dados["status"],
                "data_hora": dados["data_hora"],
                "local": dados.get("local"),
            }
        except (KeyError, TypeError):
            return dados_invalidos("Campo 'dados' incompleto.")
        _estado["rastreios"][codigo]["historico"].append(item)
        _estado["processados"].add(chave)
        return processado()

    return nao_processado()


@app.get("/rastreamento/{codigo}")
async def consultar(codigo: str, request: Request):
    data = request.query_params.get("data")
    if data is not None and not data_valida(data):
        return dados_invalidos("Parâmetro 'data' inválido.")
    r = _estado["rastreios"].get(codigo)
    if r is None:
        return erro(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")
    status = r["historico"][-1]["status"]
    atrasado = False
    if data is not None and status not in TERMINAIS:
        atrasado = data > r["data_prevista_entrega"]
    resposta = {
        "codigo_rastreio": r["codigo_rastreio"],
        "envio_id": r["envio_id"],
        "pedido_id": r["pedido_id"],
        "transportadora_id": r["transportadora_id"],
        "data_prevista_entrega": r["data_prevista_entrega"],
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": copy.deepcopy(r["historico"]),
    }
    return JSONResponse(status_code=200, content=resposta)
