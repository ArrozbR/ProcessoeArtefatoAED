# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date, datetime
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

RE_DATA = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
STATUS_FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()

_lock = threading.Lock()
_rastreamentos = {}
_eventos_processados = set()


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


class EventoEntrada(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: Dict[str, Any]


def _dados_invalidos() -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados do evento invalidos.")


@app.post("/admin/reset")
def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: EventoEntrada):
    with _lock:
        if evento.id in _eventos_processados:
            return {"processado": False}
        dados = evento.dados
        if evento.tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            if not isinstance(codigo, str):
                raise _dados_invalidos()
            data_hora = dados.get("data_hora", evento.ocorrido_em)
            _rastreamentos[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [{"status": "CRIADO", "data_hora": data_hora, "local": None}],
            }
            _eventos_processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            codigo = dados.get("codigo_rastreio")
            if not isinstance(codigo, str) or codigo not in _rastreamentos:
                return {"processado": False}
            status = dados.get("status")
            if not isinstance(status, str):
                raise _dados_invalidos()
            _rastreamentos[codigo]["historico"].append(
                {
                    "status": status,
                    "data_hora": dados.get("data_hora", evento.ocorrido_em),
                    "local": dados.get("local"),
                }
            )
            _eventos_processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    data_consulta = None
    if data is not None:
        try:
            if not RE_DATA.fullmatch(data):
                raise ValueError("data invalida")
            data_consulta = datetime.strptime(data, "%Y-%m-%d").date()
        except ValueError:
            raise ErroAPI(422, "DADOS_INVALIDOS", "Parametro data invalido.")

    with _lock:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Codigo de rastreio nao encontrado.")
        r = copy.deepcopy(r)

    status = r["historico"][-1]["status"]
    atrasado = False
    if data_consulta is not None and status not in STATUS_FINAIS:
        try:
            prevista = date.fromisoformat(str(r["data_prevista_entrega"]))
            atrasado = data_consulta > prevista
        except ValueError:
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
