# arquivo: servicos/notificacoes/main.py
import threading
from typing import Any, Dict

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}
MENSAGEM_CRIADO = "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}."

_lock = threading.RLock()
_notificacoes = []
_eventos_processados = set()
_contador = {"valor": 0}


class ErroApi(Exception):
    def __init__(self, status, codigo, mensagem):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def _tratar_erro_api(request: Request, exc: ErroApi):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


class Evento(BaseModel):
    id: StrictStr
    tipo: StrictStr
    ocorrido_em: StrictStr
    dados: Dict[str, Any]


CAMPOS_CRIADO = ["codigo_rastreio", "pedido_id", "destinatario_email"]
CAMPOS_STATUS = ["codigo_rastreio", "pedido_id", "status", "destinatario_email"]


def _dados_invalidos():
    return ErroApi(422, "DADOS_INVALIDOS", "Dados inválidos.")


@app.post("/admin/reset")
def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador["valor"] = 0
    return Response(status_code=204)


def _registrar(email, pedido_id, codigo_rastreio, tipo, mensagem, criada_em):
    _contador["valor"] += 1
    _notificacoes.append(
        {
            "id": f"NOT-{_contador['valor']:06d}",
            "email": email,
            "pedido_id": pedido_id,
            "codigo_rastreio": codigo_rastreio,
            "tipo": tipo,
            "mensagem": mensagem,
            "criada_em": criada_em,
        }
    )


@app.post("/eventos")
def receber_evento(evento: Evento):
    dados = evento.dados
    with _lock:
        if evento.tipo == "envio.criado":
            campos = CAMPOS_CRIADO
        elif evento.tipo == "envio.status_atualizado":
            campos = CAMPOS_STATUS
        else:
            return {"processado": False}
        for campo in campos:
            if campo not in dados:
                raise _dados_invalidos()

        if evento.id in _eventos_processados:
            return {"processado": False}

        pedido_id = dados["pedido_id"]
        codigo = dados["codigo_rastreio"]
        email = dados["destinatario_email"]

        if evento.tipo == "envio.criado":
            mensagem = MENSAGEM_CRIADO.format(pedido_id=pedido_id, codigo_rastreio=codigo)
            _registrar(email, pedido_id, codigo, "CRIADO", mensagem, evento.ocorrido_em)
        else:
            status = dados["status"]
            modelo = MENSAGENS_STATUS.get(status)
            if modelo is not None:
                mensagem = modelo.format(pedido_id=pedido_id)
                _registrar(email, pedido_id, codigo, status, mensagem, evento.ocorrido_em)

        _eventos_processados.add(evento.id)
        return {"processado": True}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _lock:
        itens = [dict(n) for n in _notificacoes if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return itens
