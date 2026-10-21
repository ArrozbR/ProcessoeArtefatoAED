# arquivo: servicos/notificacoes/main.py
import copy
import json
import threading

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


app = FastAPI()


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return JSONResponse(
        status_code=exc.status_code,
        content={"erro": codigos.get(exc.status_code, "ERRO"), "mensagem": str(exc.detail)},
    )


def _dados_invalidos():
    return ErroAPI(422, "DADOS_INVALIDOS", "Dados inválidos.")


MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_lock = threading.Lock()
_notificacoes = []
_processados = set()
_contador = 0


def _chave(valor) -> str:
    return json.dumps(valor, sort_keys=True, ensure_ascii=False)


@app.post("/admin/reset", status_code=204)
def reset():
    global _contador
    with _lock:
        _notificacoes.clear()
        _processados.clear()
        _contador = 0
    return Response(status_code=204)


def _registrar(email, pedido_id, codigo_rastreio, tipo, mensagem, criada_em):
    global _contador
    _contador += 1
    _notificacoes.append({
        "id": f"NOT-{_contador:06d}",
        "email": email,
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo_rastreio,
        "tipo": tipo,
        "mensagem": mensagem,
        "criada_em": criada_em,
    })


@app.post("/eventos")
async def receber_evento(request: Request):
    try:
        evento = await request.json()
    except Exception:
        raise _dados_invalidos()
    if not isinstance(evento, dict):
        raise _dados_invalidos()
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in evento:
            raise _dados_invalidos()
    dados = evento["dados"]
    if not isinstance(dados, dict):
        raise _dados_invalidos()

    chave = _chave(evento["id"])
    tipo = evento["tipo"]
    with _lock:
        if chave in _processados:
            return {"processado": False}
        if tipo == "envio.criado":
            pedido_id = dados.get("pedido_id")
            codigo = dados.get("codigo_rastreio")
            _registrar(
                dados.get("destinatario_email"),
                pedido_id,
                codigo,
                "CRIADO",
                f"Pedido {pedido_id}: envio criado. Rastreio: {codigo}.",
                evento["ocorrido_em"],
            )
            _processados.add(chave)
            return {"processado": True}
        if tipo == "envio.status_atualizado":
            status = dados.get("status")
            modelo = MENSAGENS_STATUS.get(status) if isinstance(status, str) else None
            if modelo is not None:
                pedido_id = dados.get("pedido_id")
                _registrar(
                    dados.get("destinatario_email"),
                    pedido_id,
                    dados.get("codigo_rastreio"),
                    status,
                    modelo.format(pedido_id=pedido_id),
                    evento["ocorrido_em"],
                )
            _processados.add(chave)
            return {"processado": True}
        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _lock:
        return [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
