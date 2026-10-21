# arquivo: servicos/rastreamento/main.py
import copy
import json
import re
from datetime import date

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_estado = {"rastreamentos": {}, "processados": set()}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def invalido(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return invalido("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request: Request):
    try:
        corpo = await request.body()
        dados = json.loads(corpo)
    except Exception:
        return None
    if not isinstance(dados, dict):
        return None
    return dados


def parse_data(v):
    if not isinstance(v, str) or not DATA_RE.fullmatch(v):
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def processado(valor: bool) -> JSONResponse:
    return JSONResponse(status_code=200, content={"processado": valor})


@app.post("/admin/reset")
async def reset():
    _estado["rastreamentos"] = {}
    _estado["processados"] = set()
    return Response(status_code=204)


@app.post("/eventos")
async def eventos(request: Request):
    ev = await ler_json(request)
    if ev is None:
        return invalido("Corpo da requisição inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in ev:
            return invalido(f"Campo '{campo}' ausente no envelope.")
    eid = ev["id"]
    tipo = ev["tipo"]
    dados = ev["dados"]
    if not isinstance(eid, str) or not isinstance(dados, dict):
        return invalido("Envelope inválido.")

    if eid in _estado["processados"]:
        return processado(False)

    if tipo == "envio.criado":
        try:
            codigo = dados["codigo_rastreio"]
            registro = {
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
            return invalido("Dados do evento inválidos.")
        _estado["rastreamentos"][codigo] = registro
        _estado["processados"].add(eid)
        return processado(True)

    if tipo == "envio.status_atualizado":
        try:
            codigo = dados["codigo_rastreio"]
            item = {
                "status": dados["status"],
                "data_hora": dados["data_hora"],
                "local": dados.get("local"),
            }
        except (KeyError, TypeError):
            return invalido("Dados do evento inválidos.")
        registro = _estado["rastreamentos"].get(codigo)
        if registro is None:
            return processado(False)
        registro["historico"].append(item)
        _estado["processados"].add(eid)
        return processado(True)

    return processado(False)


@app.get("/rastreamento/{codigo}")
async def consultar(codigo: str, request: Request):
    data_consulta = None
    if "data" in request.query_params:
        data_consulta = parse_data(request.query_params["data"])
        if data_consulta is None:
            return invalido("Parâmetro 'data' inválido.")
    registro = _estado["rastreamentos"].get(codigo)
    if registro is None:
        return erro(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")

    status = registro["historico"][-1]["status"]
    atrasado = False
    if data_consulta is not None and status not in TERMINAIS:
        try:
            prevista = date.fromisoformat(registro["data_prevista_entrega"])
            atrasado = data_consulta > prevista
        except (ValueError, TypeError):
            atrasado = False

    resposta = {
        "codigo_rastreio": registro["codigo_rastreio"],
        "envio_id": registro["envio_id"],
        "pedido_id": registro["pedido_id"],
        "transportadora_id": registro["transportadora_id"],
        "data_prevista_entrega": registro["data_prevista_entrega"],
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": copy.deepcopy(registro["historico"]),
    }
    return JSONResponse(status_code=200, content=resposta)
