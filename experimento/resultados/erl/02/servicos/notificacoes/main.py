# arquivo: servicos/notificacoes/main.py
import copy
import threading
from typing import Any

from fastapi import Body, FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
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
_notificacoes: list = []
_eventos_processados: set = set()
_contador = 0


def erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    codigos = {404: "RECURSO_NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return erro(exc.status_code, codigos.get(exc.status_code, "ERRO_HTTP"), str(exc.detail))


def _registrar(tipo: str, dados: dict, ocorrido_em) -> None:
    global _contador
    _contador += 1
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _notificacoes.append({
        "id": f"NOT-{_contador:06d}",
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": ocorrido_em,
    })


@app.post("/admin/reset", status_code=204)
def reset():
    global _contador
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador = 0
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(payload: Any = Body(None)):
    if not isinstance(payload, dict):
        return erro(422, "DADOS_INVALIDOS", "Envelope inválido.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in payload:
            return erro(422, "DADOS_INVALIDOS", f"Envelope sem o campo '{campo}'.")
    dados = payload["dados"]
    if not isinstance(dados, dict):
        return erro(422, "DADOS_INVALIDOS", "Campo 'dados' inválido.")
    evento_id = payload["id"]
    tipo = payload["tipo"]
    chave = evento_id if isinstance(evento_id, (str, int, float, bool)) or evento_id is None else repr(evento_id)

    with _lock:
        if chave in _eventos_processados:
            return JSONResponse(status_code=200, content={"processado": False})

        if tipo == "envio.criado":
            _registrar("CRIADO", dados, payload["ocorrido_em"])
            _eventos_processados.add(chave)
            return JSONResponse(status_code=200, content={"processado": True})

        if tipo == "envio.status_atualizado":
            status = dados.get("status")
            if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
                _registrar(status, dados, payload["ocorrido_em"])
            _eventos_processados.add(chave)
            return JSONResponse(status_code=200, content={"processado": True})

        return JSONResponse(status_code=200, content={"processado": False})


@app.get("/notificacoes")
def listar(email: str = Query(...)):
    with _lock:
        itens = [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=itens)
