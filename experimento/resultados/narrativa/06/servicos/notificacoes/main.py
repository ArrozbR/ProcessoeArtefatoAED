# arquivo: servicos/notificacoes/main.py
import copy
import threading
from typing import Any, Dict

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr

app = FastAPI(title="Notificações")

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
_contador = {"notificacao": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos na requisição."},
    )


class Evento(BaseModel):
    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: Dict[str, Any]


def _registrar(tipo: str, dados: dict, ocorrido_em: str):
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _contador["notificacao"] += 1
    _notificacoes.append(
        {
            "id": f"NOT-{_contador['notificacao']:06d}",
            "email": dados.get("destinatario_email"),
            "pedido_id": pedido_id,
            "codigo_rastreio": codigo,
            "tipo": tipo,
            "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
            "criada_em": ocorrido_em,
        }
    )


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador["notificacao"] = 0
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _lock:
        if evento.id in _eventos_processados:
            return {"processado": False}
        if evento.tipo == "envio.criado":
            _registrar("CRIADO", evento.dados, evento.ocorrido_em)
            _eventos_processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            status = evento.dados.get("status")
            if status in MENSAGENS and status != "CRIADO":
                _registrar(status, evento.dados, evento.ocorrido_em)
            _eventos_processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _lock:
        itens = [n for n in _notificacoes if n["email"] == email]
        itens.sort(key=lambda n: n["id"])
        return copy.deepcopy(itens)
