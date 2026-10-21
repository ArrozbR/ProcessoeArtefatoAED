# arquivo: servicos/notificacoes/main.py
import copy
import threading
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


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
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


MENSAGENS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_lock = threading.RLock()
_notificacoes = []
_processados = set()
_contador = {"notificacao": 0}


class Evento(BaseModel):
    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: Dict[str, Any]


def _dados_invalidos():
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados do evento inválidos.")


def _registrar(evento: Evento, tipo: str, mensagem: str):
    d = evento.dados
    _contador["notificacao"] += 1
    _notificacoes.append({
        "id": "NOT-%06d" % _contador["notificacao"],
        "email": d.get("destinatario_email"),
        "pedido_id": d.get("pedido_id"),
        "codigo_rastreio": d.get("codigo_rastreio"),
        "tipo": tipo,
        "mensagem": mensagem,
        "criada_em": evento.ocorrido_em,
    })


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _notificacoes.clear()
        _processados.clear()
        _contador["notificacao"] = 0
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _lock:
        if evento.id in _processados:
            return {"processado": False}
        d = evento.dados
        if evento.tipo == "envio.criado":
            if any(c not in d for c in ("pedido_id", "codigo_rastreio", "destinatario_email")):
                raise _dados_invalidos()
            mensagem = "Pedido {}: envio criado. Rastreio: {}.".format(d["pedido_id"], d["codigo_rastreio"])
            _registrar(evento, "CRIADO", mensagem)
            _processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            if "status" not in d:
                raise _dados_invalidos()
            modelo = MENSAGENS.get(d["status"])
            if modelo is not None:
                if any(c not in d for c in ("pedido_id", "codigo_rastreio", "destinatario_email")):
                    raise _dados_invalidos()
                _registrar(evento, d["status"], modelo.format(pedido_id=d["pedido_id"]))
            _processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str):
    with _lock:
        itens = [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return itens
