# arquivo: servicos/rastreamento/main.py
import copy
import re
import threading
from datetime import date
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI(title="Rastreamento")

STATUS_FINAIS = {"ENTREGUE", "CANCELADO", "DEVOLVIDO"}

_lock = threading.Lock()
_rastreamentos = {}
_eventos_processados = set()


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
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
        return _resposta_erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


@app.exception_handler(Exception)
async def _tratar_inesperado(request: Request, exc: Exception):
    return _resposta_erro(500, "ERRO_INTERNO", "Erro interno.")


class EventoIn(BaseModel):
    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: dict


def _data_valida(v: str) -> bool:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _rastreamentos.clear()
        _eventos_processados.clear()
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: EventoIn):
    with _lock:
        if evento.id in _eventos_processados:
            return {"processado": False}
        dados = evento.dados
        if evento.tipo == "envio.criado":
            codigo = dados.get("codigo_rastreio")
            _rastreamentos[codigo] = {
                "codigo_rastreio": codigo,
                "envio_id": dados.get("envio_id"),
                "pedido_id": dados.get("pedido_id"),
                "transportadora_id": dados.get("transportadora_id"),
                "data_prevista_entrega": dados.get("data_prevista_entrega"),
                "historico": [
                    {"status": "CRIADO", "data_hora": dados.get("data_hora", evento.ocorrido_em), "local": None}
                ],
            }
            _eventos_processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            codigo = dados.get("codigo_rastreio")
            r = _rastreamentos.get(codigo) if isinstance(codigo, str) else None
            if r is None:
                return {"processado": False}
            r["historico"].append({
                "status": dados.get("status"),
                "data_hora": dados.get("data_hora", evento.ocorrido_em),
                "local": dados.get("local"),
            })
            _eventos_processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/rastreamento/{codigo_rastreio}")
def obter_rastreamento(codigo_rastreio: str, data: Optional[str] = None):
    if data is not None and not _data_valida(data):
        raise ErroAPI(422, "DADOS_INVALIDOS", "Data mal formada.")
    with _lock:
        r = _rastreamentos.get(codigo_rastreio)
        if r is None:
            raise ErroAPI(404, "RASTREIO_NAO_ENCONTRADO", "Código de rastreio não encontrado.")
        r = copy.deepcopy(r)
    status = r["historico"][-1]["status"]
    atrasado = False
    if data is not None and status not in STATUS_FINAIS:
        prevista = r.get("data_prevista_entrega")
        if isinstance(prevista, str) and _data_valida(prevista):
            atrasado = date.fromisoformat(data) > date.fromisoformat(prevista)
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
