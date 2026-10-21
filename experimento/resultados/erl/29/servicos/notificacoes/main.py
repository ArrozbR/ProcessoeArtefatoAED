# arquivo: servicos/notificacoes/main.py
import threading
from typing import Any, Optional

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


@app.exception_handler(ErroAPI)
async def _erro_api(request: Request, exc: ErroAPI):
    return JSONResponse(status_code=exc.status, content={"erro": exc.codigo, "mensagem": exc.mensagem})


@app.exception_handler(RequestValidationError)
async def _erro_validacao(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"erro": "DADOS_INVALIDOS", "mensagem": "Dados inválidos."})


@app.exception_handler(StarletteHTTPException)
async def _erro_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        codigo = "NAO_ENCONTRADO"
    elif exc.status_code == 405:
        codigo = "METODO_NAO_PERMITIDO"
    elif exc.status_code == 422:
        codigo = "DADOS_INVALIDOS"
    else:
        codigo = "ERRO"
    return JSONResponse(status_code=exc.status_code, content={"erro": codigo, "mensagem": str(exc.detail)})


def invalido(mensagem: str = "Dados inválidos.") -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


CAMPOS_ENVELOPE = ("id", "tipo", "ocorrido_em", "dados")
MENSAGENS = {
    "CRIADO": "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}.",
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}
TIPOS_CONHECIDOS = {"envio.criado", "envio.status_atualizado"}

_lock = threading.RLock()
_notificacoes: list = []
_eventos_processados: set = set()
_contador = {"notificacao": 0}


def _chave_evento(v: Any) -> str:
    return v if isinstance(v, str) else repr(v)


@app.post("/eventos")
def receber_evento(body: Any = Body(None)):
    if not isinstance(body, dict):
        raise invalido("Envelope inválido.")
    for campo in CAMPOS_ENVELOPE:
        if campo not in body:
            raise invalido(f"Campo '{campo}' ausente no envelope.")
    tipo = body["tipo"]
    dados = body["dados"]
    chave = _chave_evento(body["id"])

    with _lock:
        if chave in _eventos_processados:
            return {"processado": False}
        if tipo not in TIPOS_CONHECIDOS:
            return {"processado": False}
        if not isinstance(dados, dict):
            raise invalido("Campo 'dados' inválido.")

        if tipo == "envio.criado":
            status = "CRIADO"
        else:
            if "status" not in dados:
                raise invalido("Campo 'dados.status' ausente.")
            status = dados["status"]

        if status in MENSAGENS:
            try:
                pedido_id = dados["pedido_id"]
                codigo = dados["codigo_rastreio"]
                email = dados["destinatario_email"]
            except KeyError:
                raise invalido("Campo 'dados' incompleto.")
            _contador["notificacao"] += 1
            _notificacoes.append({
                "id": f"NOT-{_contador['notificacao']:06d}",
                "email": email,
                "pedido_id": pedido_id,
                "codigo_rastreio": codigo,
                "tipo": status,
                "mensagem": MENSAGENS[status].format(pedido_id=pedido_id, codigo_rastreio=codigo),
                "criada_em": body["ocorrido_em"],
            })

        _eventos_processados.add(chave)
        return {"processado": True}


@app.get("/notificacoes")
def listar(email: Optional[str] = None):
    if email is None:
        raise invalido("Parâmetro 'email' obrigatório.")
    with _lock:
        itens = [dict(n) for n in _notificacoes if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return itens


@app.post("/admin/reset", status_code=204)
def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador["notificacao"] = 0
    return Response(status_code=204)
