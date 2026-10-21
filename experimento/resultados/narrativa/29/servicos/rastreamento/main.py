# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

DATA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATUS_FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_lock = threading.Lock()
_rastreios = {}
_eventos_processados = set()


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso nao encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Metodo nao permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _dados_invalidos():
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.post("/admin/reset")
def reset():
    with _lock:
        _rastreios.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
async def receber_evento(request: Request):
    try:
        corpo = await request.json()
    except Exception:
        raise _dados_invalidos()
    if not isinstance(corpo, dict):
        raise _dados_invalidos()
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo or corpo[campo] is None:
            raise _dados_invalidos()
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        raise _dados_invalidos()
    evento_id = corpo["id"]
    tipo = corpo["tipo"]
    try:
        hash(evento_id)
    except TypeError:
        raise _dados_invalidos()

    with _lock:
        if evento_id in _eventos_processados:
            return {"processado": False}
        if tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            _rastreios[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [{"status": "CRIADO", "data_hora": dados.get("data_hora"), "local": None}],
            }
            _eventos_processados.add(evento_id)
            return {"processado": True}
        if tipo == "envio.status_atualizado":
            codigo = dados.get("codigo_rastreio")
            rastreio = _rastreios.get(codigo)
            if rastreio is None:
                return {"processado": False}
            rastreio["historico"].append({
                "status": dados.get("status"),
                "data_hora": dados.get("data_hora"),
                "local": dados.get("local"),
            })
            _eventos_processados.add(evento_id)
            return {"processado": True}
        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    data_consulta = None
    if data is not None:
        if not DATA_RE.match(data):
            raise _dados_invalidos()
        try:
            data_consulta = date.fromisoformat(data)
        except ValueError:
            raise _dados_invalidos()

    with _lock:
        rastreio = _rastreios.get(codigo_rastreio)
        if rastreio is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Codigo de rastreio nao encontrado.")
        r = copy.deepcopy(rastreio)

    status = r["historico"][-1]["status"]
    atrasado = False
    if data_consulta is not None and status not in STATUS_FINAIS:
        try:
            prevista = date.fromisoformat(r["data_prevista_entrega"])
            atrasado = data_consulta > prevista
        except Exception:
            atrasado = False

    return {
        "codigo_rastreio": r["codigo_rastreio"],
        "envio_id": r["envio_id"],
        "pedido_id": r["pedido_id"],
        "transportadora_id": r["transportadora_id"],
        "data_prevista_entrega": r["data_prevista_entrega"],
        "status": status,
        "entregue": status == "ENTREGUE",
        "atrasado": atrasado,
        "historico": r["historico"],
    }
