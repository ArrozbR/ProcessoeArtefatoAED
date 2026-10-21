# arquivo: servicos/notificacoes/main.py
import threading
from typing import Any, Dict

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
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


class EventoEntrada(BaseModel):
    id: str = Field(..., strict=True)
    tipo: str = Field(..., strict=True)
    ocorrido_em: str = Field(..., strict=True)
    dados: Dict[str, Any]


MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_trava = threading.RLock()
_notificacoes: list = []
_processados: set = set()
_contador = {"notificacao": 0}


def _registrar(dados: dict, tipo: str, mensagem: str, criada_em: str) -> None:
    _contador["notificacao"] += 1
    _notificacoes.append(
        {
            "id": f"NOT-{_contador['notificacao']:06d}",
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
    with _trava:
        _notificacoes.clear()
        _processados.clear()
        _contador["notificacao"] = 0
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: EventoEntrada):
    with _trava:
        if evento.id in _processados:
            return {"processado": False}
        dados = evento.dados
        pedido_id = dados.get("pedido_id")
        if evento.tipo == "envio.criado":
            mensagem = f"Pedido {pedido_id}: envio criado. Rastreio: {dados.get('codigo_rastreio')}."
            _registrar(dados, "CRIADO", mensagem, evento.ocorrido_em)
            _processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            status = dados.get("status")
            modelo = MENSAGENS_STATUS.get(status) if isinstance(status, str) else None
            if modelo is not None:
                _registrar(dados, status, modelo.format(pedido_id=pedido_id), evento.ocorrido_em)
            _processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _trava:
        return [dict(n) for n in _notificacoes if n["email"] == email]
