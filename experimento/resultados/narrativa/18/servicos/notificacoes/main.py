# arquivo: servicos/notificacoes/main.py
import copy
import threading
from typing import Any

from fastapi import Body, FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resp_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc: ErroAPI):
    return _resp_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resp_erro(422, "DADOS_INVALIDOS", "Dados invalidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resp_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _invalido(msg: str = "Dados invalidos.") -> None:
    raise ErroAPI(422, "DADOS_INVALIDOS", msg)


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
_contador = {"valor": 0}
_eventos_processados: set = set()


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _notificacoes.clear()
        _contador["valor"] = 0
        _eventos_processados.clear()
    return Response(status_code=204)


def _registrar(tipo: str, dados: dict, ocorrido_em: Any) -> None:
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _contador["valor"] += 1
    _notificacoes.append({
        "id": f"NOT-{_contador['valor']:06d}",
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": ocorrido_em,
    })


@app.post("/eventos")
def receber_evento(corpo: Any = Body(None)):
    if not isinstance(corpo, dict):
        _invalido("Corpo deve ser um objeto JSON.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo or corpo[campo] is None:
            _invalido(f"Campo '{campo}' ausente.")
    dados = corpo["dados"]
    if not isinstance(dados, dict):
        _invalido("Campo 'dados' invalido.")
    eid = corpo["id"]
    chave = eid if isinstance(eid, (str, int, float, bool)) else repr(eid)
    tipo = corpo["tipo"]

    with _lock:
        if chave in _eventos_processados or tipo not in TIPOS_CONHECIDOS:
            return {"processado": False}
        if tipo == "envio.criado":
            _registrar("CRIADO", dados, corpo["ocorrido_em"])
        else:
            status = dados.get("status")
            if status in MENSAGENS and status != "CRIADO":
                _registrar(status, dados, corpo["ocorrido_em"])
        _eventos_processados.add(chave)
        return {"processado": True}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _lock:
        return [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
