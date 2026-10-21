# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date
from typing import Any, Optional

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


@app.exception_handler(ErroAPI)
async def _erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _erro_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _erro_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        codigo = "NAO_ENCONTRADO"
    elif exc.status_code == 405:
        codigo = "METODO_NAO_PERMITIDO"
    elif exc.status_code == 422:
        codigo = "DADOS_INVALIDOS"
    else:
        codigo = "ERRO"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


def invalido(mensagem: str = "Dados inválidos.") -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


RE_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TERMINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}
CAMPOS_ENVELOPE = ("id", "tipo", "ocorrido_em", "dados")

_lock = threading.RLock()
_rastreamentos: dict = {}
_eventos_processados: set = set()


def _chave_evento(v: Any) -> str:
    return v if isinstance(v, str) else repr(v)


@app.post("/eventos")
def receber_evento(body: Any = Body(None)):
    if not isinstance(body, dict):
        raise invalido("Envelope inválido.")
    for campo in CAMPOS_ENVELOPE:
        if campo not in body:
            raise invalido(f"Campo '{campo}' ausente no envelope.")
    dados = body["dados"]
    tipo = body["tipo"]
    chave = _chave_evento(body["id"])

    with _lock:
        if chave in _eventos_processados:
            return {"processado": False}

        if tipo == "envio.criado":
            if not isinstance(dados, dict):
                raise invalido("Campo 'dados' inválido.")
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
                raise invalido("Campo 'dados' incompleto.")
            _rastreamentos[codigo] = registro
            _eventos_processados.add(chave)
            return {"processado": True}

        if tipo == "envio.status_atualizado":
            if not isinstance(dados, dict):
                raise invalido("Campo 'dados' inválido.")
            try:
                codigo = dados["codigo_rastreio"]
                item = {"status": dados["status"], "data_hora": dados["data_hora"], "local": dados.get("local")}
            except KeyError:
                raise invalido("Campo 'dados' incompleto.")
            registro = _rastreamentos.get(codigo) if isinstance(codigo, str) else None
            if registro is None:
                return {"processado": False}
            registro["historico"].append(item)
            _eventos_processados.add(chave)
            return {"processado": True}

        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def consultar(codigo_rastreio: str, data: Optional[str] = None):
    data_ref = None
    if data is not None:
        if not RE_DATA.match(data):
            raise invalido("Parâmetro 'data' inválido.")
        try:
            data_ref = date.fromisoformat(data)
        except ValueError:
            raise invalido("Parâmetro 'data' inválido.")

    with _lock:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Rastreamento não encontrado.")
        historico = copy.deepcopy(r["historico"])
        status = historico[-1]["status"]
        atrasado = False
        if data_ref is not None and status not in TERMINAIS:
            try:
                atrasado = data_ref > date.fromisoformat(r["data_prevista_entrega"])
            except (ValueError, TypeError):
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
            "historico": historico,
        }


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)
