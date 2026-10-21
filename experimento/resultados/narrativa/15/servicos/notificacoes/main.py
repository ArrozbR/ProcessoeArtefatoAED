# arquivo: servicos/notificacoes/main.py
import copy
import json
import threading
from typing import Any, Dict

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return JSONResponse(
        status_code=exc.status_code,
        content={"erro": codigos.get(exc.status_code, "ERRO_HTTP"), "mensagem": str(exc.detail)},
    )


MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_lock = threading.RLock()
_notificacoes = []
_eventos_processados = set()
_contador = [0]


class Evento(BaseModel):
    id: Any
    tipo: Any
    ocorrido_em: Any
    dados: Dict[str, Any]


def _chave(valor: Any) -> str:
    if isinstance(valor, str):
        return valor
    return json.dumps(valor, sort_keys=True)


def _registrar(dados: dict, tipo: str, mensagem: str, criada_em: Any):
    _contador[0] += 1
    _notificacoes.append(
        {
            "id": "NOT-%06d" % _contador[0],
            "email": dados.get("destinatario_email"),
            "pedido_id": dados.get("pedido_id"),
            "codigo_rastreio": dados.get("codigo_rastreio"),
            "tipo": tipo,
            "mensagem": mensagem,
            "criada_em": criada_em,
        }
    )


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador[0] = 0
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _lock:
        chave = _chave(evento.id)
        if chave in _eventos_processados:
            return {"processado": False}
        dados = evento.dados
        pedido_id = dados.get("pedido_id")
        if evento.tipo == "envio.criado":
            mensagem = "Pedido {}: envio criado. Rastreio: {}.".format(pedido_id, dados.get("codigo_rastreio"))
            _registrar(dados, "CRIADO", mensagem, evento.ocorrido_em)
            _eventos_processados.add(chave)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            status = dados.get("status")
            modelo = MENSAGENS_STATUS.get(status) if isinstance(status, str) else None
            if modelo is not None:
                _registrar(dados, status, modelo.format(pedido_id=pedido_id), evento.ocorrido_em)
            _eventos_processados.add(chave)
            return {"processado": True}
        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _lock:
        itens = [n for n in _notificacoes if n["email"] == email]
        itens.sort(key=lambda n: n["id"])
        return copy.deepcopy(itens)
