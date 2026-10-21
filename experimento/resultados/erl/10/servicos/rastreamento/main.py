# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_lock = threading.Lock()
_rastreios: dict = {}
_eventos_processados: set = set()


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def dados_invalidos(mensagem: str = "Dados inválidos.") -> JSONResponse:
    return erro(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(RequestValidationError)
async def _handler_validacao(request: Request, exc: RequestValidationError):
    return dados_invalidos("Requisição com dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _handler_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return erro(exc.status_code, "ERRO", str(exc.detail))


async def ler_json(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def parse_data(valor):
    if not isinstance(valor, str) or not DATA_RE.match(valor):
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


@app.post("/eventos")
async def consumir(request: Request):
    corpo = await ler_json(request)
    if not isinstance(corpo, dict):
        return dados_invalidos("Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            return dados_invalidos("Envelope sem o campo %s." % campo)
    evt_id = corpo["id"]
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    if not isinstance(evt_id, str) or not isinstance(tipo, str) or not isinstance(dados, dict):
        return dados_invalidos("Envelope com tipos inválidos.")

    with _lock:
        if evt_id in _eventos_processados:
            return JSONResponse(status_code=200, content={"processado": False})

        if tipo == "envio.criado":
            try:
                codigo = dados["codigo_rastreio"]
                registro = {
                    "codigo_rastreio": codigo,
                    "envio_id": dados["envio_id"],
                    "pedido_id": dados["pedido_id"],
                    "transportadora_id": dados["transportadora_id"],
                    "data_prevista_entrega": dados["data_prevista_entrega"],
                    "historico": [{"status": "CRIADO", "data_hora": dados["data_hora"], "local": None}],
                }
            except KeyError:
                return dados_invalidos("Dados do evento incompletos.")
            _rastreios[codigo] = registro
            _eventos_processados.add(evt_id)
            return JSONResponse(status_code=200, content={"processado": True})

        if tipo == "envio.status_atualizado":
            try:
                codigo = dados["codigo_rastreio"]
                item = {
                    "status": dados["status"],
                    "data_hora": dados["data_hora"],
                    "local": dados.get("local"),
                }
            except KeyError:
                return dados_invalidos("Dados do evento incompletos.")
            registro = _rastreios.get(codigo)
            if registro is None:
                return JSONResponse(status_code=200, content={"processado": False})
            registro["historico"].append(item)
            _eventos_processados.add(evt_id)
            return JSONResponse(status_code=200, content={"processado": True})

        return JSONResponse(status_code=200, content={"processado": False})


@app.get("/rastreamento/{codigo}")
async def consultar(codigo: str, request: Request):
    data_consulta = None
    if "data" in request.query_params:
        data_consulta = parse_data(request.query_params.get("data"))
        if data_consulta is None:
            return dados_invalidos("Parâmetro data inválido.")
    with _lock:
        registro = _rastreios.get(codigo)
        if registro is None:
            return erro(404, "RASTREIO_NAO_ENCONTRADO", "Rastreio não encontrado.")
        registro = copy.deepcopy(registro)
    status = registro["historico"][-1]["status"]
    atrasado = False
    if data_consulta is not None and status not in TERMINAIS:
        try:
            prevista = date.fromisoformat(registro["data_prevista_entrega"])
            atrasado = data_consulta > prevista
        except Exception:
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
        "historico": registro["historico"],
    }
    return JSONResponse(status_code=200, content=resposta)


@app.post("/admin/reset")
async def reset():
    with _lock:
        _rastreios.clear()
        _eventos_processados.clear()
    return Response(status_code=204)
