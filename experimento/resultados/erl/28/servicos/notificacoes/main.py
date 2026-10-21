# arquivo: servicos/notificacoes/main.py
import copy
import threading

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

CAMPOS_ENVELOPE = ("id", "tipo", "ocorrido_em", "dados")
CAMPOS_CRIADO = ("pedido_id", "codigo_rastreio", "destinatario_email")
CAMPOS_STATUS = ("pedido_id", "codigo_rastreio", "destinatario_email", "status")

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
_contador = {"valor": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    codigos = {404: "NAO_ENCONTRADO", 405: "METODO_NAO_PERMITIDO"}
    return _resposta_erro(exc.status_code, codigos.get(exc.status_code, "ERRO"), str(exc.detail))


def _invalido(mensagem: str = "Dados inválidos na requisição.") -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo JSON ausente ou inválido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    return corpo


def _chave_evento(eid) -> str:
    return eid if isinstance(eid, str) else repr(eid)


def _registrar(tipo: str, dados: dict, criada_em) -> None:
    _contador["valor"] += 1
    pedido_id = dados["pedido_id"]
    codigo = dados["codigo_rastreio"]
    _notificacoes.append({
        "id": f"NOT-{_contador['valor']:06d}",
        "email": dados["destinatario_email"],
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": criada_em,
    })


@app.post("/eventos")
async def consumir_evento(request: Request):
    envelope = await _ler_corpo(request)
    for campo in CAMPOS_ENVELOPE:
        if campo not in envelope:
            raise _invalido(f"Envelope sem o campo '{campo}'.")
    chave = _chave_evento(envelope["id"])
    tipo = envelope["tipo"]
    dados = envelope["dados"]

    with _lock:
        if chave in _eventos_processados:
            return JSONResponse(status_code=200, content={"processado": False})
        if tipo not in ("envio.criado", "envio.status_atualizado"):
            return JSONResponse(status_code=200, content={"processado": False})
        if not isinstance(dados, dict):
            raise _invalido("Campo 'dados' inválido.")

        if tipo == "envio.criado":
            for campo in CAMPOS_CRIADO:
                if campo not in dados:
                    raise _invalido(f"Dados do evento sem o campo '{campo}'.")
            _registrar("CRIADO", dados, envelope["ocorrido_em"])
        else:
            for campo in CAMPOS_STATUS:
                if campo not in dados:
                    raise _invalido(f"Dados do evento sem o campo '{campo}'.")
            status = dados["status"]
            if isinstance(status, str) and status in MENSAGENS:
                _registrar(status, dados, envelope["ocorrido_em"])

        _eventos_processados.add(chave)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar_notificacoes(email: str = Query(...)):
    with _lock:
        lista = [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
    lista.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=lista)


@app.post("/admin/reset")
async def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador["valor"] = 0
    return Response(status_code=204)
