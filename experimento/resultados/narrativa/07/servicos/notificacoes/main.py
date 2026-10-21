# arquivo: servicos/notificacoes/main.py
import copy
import threading
from typing import Any, Dict

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

MENSAGENS = {
    "CRIADO": "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}.",
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_lock = threading.Lock()
_notificacoes = []
_eventos_processados = set()
_contador = {"valor": 0}


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


def _invalido() -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados do evento invalidos.")


def _registrar(tipo: str, evento: Evento) -> None:
    dados = evento.dados
    try:
        pedido_id = dados["pedido_id"]
        codigo = dados["codigo_rastreio"]
        email = dados["destinatario_email"]
    except (KeyError, TypeError):
        raise _invalido()
    _contador["valor"] += 1
    _notificacoes.append(
        {
            "id": "NOT-%06d" % _contador["valor"],
            "email": email,
            "pedido_id": pedido_id,
            "codigo_rastreio": codigo,
            "tipo": tipo,
            "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
            "criada_em": evento.ocorrido_em,
        }
    )


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _lock:
        if evento.id in _eventos_processados:
            return {"processado": False}

        if evento.tipo == "envio.criado":
            _registrar("CRIADO", evento)
            _eventos_processados.add(evento.id)
            return {"processado": True}

        if evento.tipo == "envio.status_atualizado":
            status = evento.dados.get("status")
            if not isinstance(status, str):
                raise _invalido()
            if status in MENSAGENS and status != "CRIADO":
                _registrar(status, evento)
            _eventos_processados.add(evento.id)
            return {"processado": True}

        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _lock:
        return [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
