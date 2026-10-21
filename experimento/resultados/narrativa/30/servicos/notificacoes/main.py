# arquivo: servicos/notificacoes/main.py
import threading
from typing import Any, Dict

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

MENSAGENS_STATUS = {
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
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


class Evento(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(strict=True)
    tipo: str = Field(strict=True)
    ocorrido_em: str = Field(strict=True)
    dados: Dict[str, Any]


def _registrar(evento, tipo, mensagem):
    d = evento.dados
    numero = _contador["valor"] + 1
    _contador["valor"] = numero
    _notificacoes.append(
        {
            "id": "NOT-%06d" % numero,
            "email": d.get("destinatario_email"),
            "pedido_id": d.get("pedido_id"),
            "codigo_rastreio": d.get("codigo_rastreio"),
            "tipo": tipo,
            "mensagem": mensagem,
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
        d = evento.dados
        pedido_id = d.get("pedido_id")
        if evento.tipo == "envio.criado":
            mensagem = "Pedido {}: envio criado. Rastreio: {}.".format(pedido_id, d.get("codigo_rastreio"))
            _registrar(evento, "CRIADO", mensagem)
            _eventos_processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            status = d.get("status")
            modelo = MENSAGENS_STATUS.get(status)
            if modelo is not None:
                _registrar(evento, status, modelo.format(pedido_id=pedido_id))
            _eventos_processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _lock:
        itens = [dict(n) for n in _notificacoes if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return itens
