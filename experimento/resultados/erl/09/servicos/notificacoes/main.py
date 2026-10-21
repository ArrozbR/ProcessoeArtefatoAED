# arquivo: servicos/notificacoes/main.py
import copy
import json
import threading
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

TIPOS_CONHECIDOS = {"envio.criado", "envio.status_atualizado"}
MENSAGENS = {
    "CRIADO": "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}.",
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_lock = threading.Lock()
_notificacoes: list = []
_processados: set = set()
_contador = {"valor": 0}


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _validacao(request: Request, exc: RequestValidationError):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 422:
        return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _chave(id_evento: Any) -> str:
    return json.dumps(id_evento, sort_keys=True, ensure_ascii=False)


def _registrar(tipo: str, dados: dict, ocorrido_em: Any) -> None:
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _contador["valor"] += 1
    _notificacoes.append(
        {
            "id": f"NOT-{_contador['valor']:06d}",
            "email": dados.get("destinatario_email"),
            "pedido_id": pedido_id,
            "codigo_rastreio": codigo,
            "tipo": tipo,
            "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
            "criada_em": ocorrido_em,
        }
    )


@app.post("/eventos")
def receber_evento(corpo: Any = Body(default=None)):
    if not isinstance(corpo, dict):
        return erro(422, "DADOS_INVALIDOS", "Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            return erro(422, "DADOS_INVALIDOS", f"Campo {campo} ausente no envelope.")
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        return erro(422, "DADOS_INVALIDOS", "Campo dados inválido.")
    chave = _chave(corpo["id"])
    tipo = corpo["tipo"]

    with _lock:
        if chave in _processados:
            return JSONResponse(status_code=200, content={"processado": False})
        if tipo not in TIPOS_CONHECIDOS:
            return JSONResponse(status_code=200, content={"processado": False})

        if tipo == "envio.criado":
            _registrar("CRIADO", dados, corpo["ocorrido_em"])
        else:
            status = dados.get("status")
            if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
                _registrar(status, dados, corpo["ocorrido_em"])
        _processados.add(chave)
        return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
def listar(request: Request):
    if "email" not in request.query_params:
        return erro(422, "DADOS_INVALIDOS", "Parâmetro email obrigatório.")
    email = request.query_params["email"]
    with _lock:
        itens = [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=itens)


@app.post("/admin/reset")
def reset():
    with _lock:
        _notificacoes.clear()
        _processados.clear()
        _contador["valor"] = 0
    return Response(status_code=204)
