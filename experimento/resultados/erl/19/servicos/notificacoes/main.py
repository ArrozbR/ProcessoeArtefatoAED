# arquivo: servicos/notificacoes/main.py
import threading

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

TIPOS_CONHECIDOS = {"envio.criado", "envio.status_atualizado"}

MENSAGENS = {
    "CRIADO": "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}.",
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


app = FastAPI()

_lock = threading.Lock()
_notificacoes: list = []
_eventos_processados: set = set()
_contador = {"notificacao": 0}


def _erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_envelope(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo JSON inválido ou ausente.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo deve ser um objeto JSON.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            raise _invalido(f"Envelope sem o campo '{campo}'.")
    eid = corpo["id"]
    if not isinstance(eid, (str, int)) or isinstance(eid, bool):
        raise _invalido("Campo 'id' inválido.")
    if not isinstance(corpo["dados"], dict):
        raise _invalido("Campo 'dados' inválido.")
    return corpo


def _registrar(tipo: str, dados: dict, criada_em) -> None:
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _contador["notificacao"] += 1
    _notificacoes.append({
        "id": f"NOT-{_contador['notificacao']:06d}",
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": criada_em,
    })


@app.post("/eventos")
async def consumir(request: Request):
    env = await _ler_envelope(request)
    eid = str(env["id"])
    tipo = env["tipo"]
    dados = env["dados"]
    with _lock:
        if eid in _eventos_processados:
            return JSONResponse(status_code=200, content={"processado": False})
        if tipo not in TIPOS_CONHECIDOS:
            return JSONResponse(status_code=200, content={"processado": False})
        if tipo == "envio.criado":
            _registrar("CRIADO", dados, env["ocorrido_em"])
        else:
            status = dados.get("status")
            if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
                _registrar(status, dados, env["ocorrido_em"])
        _eventos_processados.add(eid)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar(request: Request):
    if "email" not in request.query_params:
        raise _invalido("Parâmetro 'email' é obrigatório.")
    email = request.query_params["email"]
    with _lock:
        lista = [dict(n) for n in _notificacoes if n["email"] == email]
    return JSONResponse(status_code=200, content=lista)


@app.post("/admin/reset")
async def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador["notificacao"] = 0
    return Response(status_code=204)
