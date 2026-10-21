# arquivo: servicos/notificacoes/main.py
import copy
import threading
from typing import Annotated, Any, Dict

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
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
    return _erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


class Evento(BaseModel):
    id: Annotated[str, Field(strict=True)]
    tipo: Annotated[str, Field(strict=True)]
    ocorrido_em: Annotated[str, Field(strict=True)]
    dados: Dict[str, Any]


MENSAGENS = {
    "CRIADO": "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}.",
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_trava = threading.RLock()
_notificacoes = []
_processados = set()
_contador = 0


@app.post("/admin/reset", status_code=204)
def reset():
    global _contador
    with _trava:
        _notificacoes.clear()
        _processados.clear()
        _contador = 0
    return Response(status_code=204)


def _exigir(dados: dict, campos):
    for c in campos:
        if c not in dados:
            raise ErroAPI(422, "DADOS_INVALIDOS", "Campo ausente em dados: %s." % c)


def _registrar(tipo: str, dados: dict, ocorrido_em: str):
    global _contador
    _contador += 1
    pedido_id = dados["pedido_id"]
    codigo = dados["codigo_rastreio"]
    _notificacoes.append({
        "id": "NOT-%06d" % _contador,
        "email": dados["destinatario_email"],
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": ocorrido_em,
    })


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _trava:
        if evento.id in _processados:
            return {"processado": False}
        d = evento.dados
        if evento.tipo == "envio.criado":
            _exigir(d, ["pedido_id", "codigo_rastreio", "destinatario_email"])
            _registrar("CRIADO", d, evento.ocorrido_em)
            _processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            _exigir(d, ["status"])
            status = d["status"]
            if status in MENSAGENS and status != "CRIADO":
                _exigir(d, ["pedido_id", "codigo_rastreio", "destinatario_email"])
                _registrar(status, d, evento.ocorrido_em)
            _processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _trava:
        return [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
