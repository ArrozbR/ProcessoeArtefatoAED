# arquivo: servicos/notificacoes/main.py
import copy
import threading
from typing import Any, Dict

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI(title="Notificações")

MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}
MENSAGEM_CRIADO = "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}."

_lock = threading.Lock()
_notificacoes = []
_eventos_processados = set()
_contador = {"valor": 0}


def erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


def _descrever_erros(exc):
    partes = []
    try:
        for e in exc.errors():
            loc = ".".join(str(p) for p in e.get("loc", ()))
            partes.append(f"{loc}: {e.get('msg', '')}")
    except Exception:
        pass
    return "Dados inválidos" + (": " + "; ".join(partes) if partes else ".")


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", _descrever_erros(exc))


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    codigo = "RECURSO_NAO_ENCONTRADO" if exc.status_code == 404 else "ERRO_HTTP"
    return erro(exc.status_code, codigo, str(exc.detail))


class Evento(BaseModel):
    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: Dict[str, Any]


def _registrar(evento, tipo, mensagem):
    dados = evento.dados
    _contador["valor"] += 1
    _notificacoes.append({
        "id": f"NOT-{_contador['valor']:06d}",
        "email": dados.get("destinatario_email"),
        "pedido_id": dados.get("pedido_id"),
        "codigo_rastreio": dados.get("codigo_rastreio"),
        "tipo": tipo,
        "mensagem": mensagem,
        "criada_em": evento.ocorrido_em,
    })


@app.post("/admin/reset")
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
        dados = evento.dados
        pedido_id = dados.get("pedido_id")
        if evento.tipo == "envio.criado":
            mensagem = MENSAGEM_CRIADO.format(pedido_id=pedido_id, codigo_rastreio=dados.get("codigo_rastreio"))
            _registrar(evento, "CRIADO", mensagem)
            _eventos_processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            status = dados.get("status")
            modelo = MENSAGENS_STATUS.get(status) if isinstance(status, str) else None
            if modelo is not None:
                _registrar(evento, status, modelo.format(pedido_id=pedido_id))
            _eventos_processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _lock:
        itens = [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
    return JSONResponse(status_code=200, content=itens)
