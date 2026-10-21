# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date
from typing import Any, Dict, Optional

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

REGEX_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
FINAIS_SEM_ATRASO = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_lock = threading.Lock()
_rastreamentos = {}
_eventos_processados = set()


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _erro(exc.status_code, codigos.get(exc.status_code, "ERRO_HTTP"), str(exc.detail))


@app.exception_handler(Exception)
async def _tratar_generico(request, exc: Exception):
    return _erro(500, "ERRO_INTERNO", "Erro interno.")


class Evento(BaseModel):
    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: Dict[str, Any]


def _parse_data(v: str) -> date:
    if not REGEX_DATA.match(v):
        raise ValueError("data invalida")
    return date.fromisoformat(v)


def _invalido() -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados do evento invalidos.")


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _lock:
        if evento.id in _eventos_processados:
            return {"processado": False}
        dados = evento.dados

        if evento.tipo == "envio.criado":
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
                raise _invalido()
            _rastreamentos[codigo] = registro
            _eventos_processados.add(evento.id)
            return {"processado": True}

        if evento.tipo == "envio.status_atualizado":
            try:
                codigo = dados["codigo_rastreio"]
                item = {
                    "status": dados["status"],
                    "data_hora": dados["data_hora"],
                    "local": dados.get("local"),
                }
            except (KeyError, TypeError):
                raise _invalido()
            registro = _rastreamentos.get(codigo)
            if registro is None:
                return {"processado": False}
            registro["historico"].append(item)
            _eventos_processados.add(evento.id)
            return {"processado": True}

        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = Query(None)):
    data_consulta = None
    if data is not None:
        try:
            data_consulta = _parse_data(data)
        except ValueError:
            raise ErroAPI(422, "DADOS_INVALIDOS", "Data mal formada.")

    with _lock:
        registro = _rastreamentos.get(codigo_rastreio)
        if registro is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Codigo de rastreio nao encontrado.")
        registro = copy.deepcopy(registro)

    status = registro["historico"][-1]["status"]
    atrasado = False
    if data_consulta is not None and status not in FINAIS_SEM_ATRASO:
        try:
            prevista = date.fromisoformat(registro["data_prevista_entrega"])
            atrasado = data_consulta > prevista
        except (TypeError, ValueError):
            atrasado = False

    return {
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
