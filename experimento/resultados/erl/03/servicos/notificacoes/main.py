# arquivo: servicos/notificacoes/main.py
import copy
import threading

from fastapi import FastAPI, Request
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

TIPOS_CONHECIDOS = {"envio.criado", "envio.status_atualizado"}

_lock = threading.Lock()
_notificacoes: list = []
_eventos_processados: set = set()
_contador = {"notificacao": 0}


class ErroApi(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def _tratar_erro_api(request: Request, exc: ErroApi):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados da requisição inválidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _invalido(mensagem: str) -> ErroApi:
    return ErroApi(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _registrar(tipo_notificacao: str, dados: dict, ocorrido_em) -> None:
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    mensagem = MENSAGENS[tipo_notificacao].format(pedido_id=pedido_id, codigo_rastreio=codigo)
    _contador["notificacao"] += 1
    _notificacoes.append({
        "id": f"NOT-{_contador['notificacao']:06d}",
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo_notificacao,
        "mensagem": mensagem,
        "criada_em": ocorrido_em,
    })


@app.post("/eventos")
async def consumir_evento(request: Request):
    corpo = await _ler_corpo(request)
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            raise _invalido(f"Envelope sem o campo '{campo}'.")
    if not isinstance(corpo["dados"], dict):
        raise _invalido("Campo 'dados' deve ser um objeto.")

    evento_id = corpo["id"]
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    ocorrido_em = corpo["ocorrido_em"]

    with _lock:
        if not isinstance(evento_id, (str, int)) or isinstance(evento_id, bool):
            raise _invalido("Campo 'id' inválido.")
        if evento_id in _eventos_processados:
            return JSONResponse(status_code=200, content={"processado": False})
        if tipo not in TIPOS_CONHECIDOS:
            return JSONResponse(status_code=200, content={"processado": False})

        if tipo == "envio.criado":
            _registrar("CRIADO", dados, ocorrido_em)
        else:
            status = dados.get("status")
            if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
                _registrar(status, dados, ocorrido_em)

        _eventos_processados.add(evento_id)
        return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar_notificacoes(request: Request):
    params = request.query_params
    if "email" not in params:
        raise _invalido("Parâmetro 'email' é obrigatório.")
    email = params["email"]
    with _lock:
        lista = [copy.deepcopy(n) for n in _notificacoes if n["email"] == email]
    lista.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=lista)


@app.post("/admin/reset")
async def reset():
    with _lock:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador["notificacao"] = 0
    return Response(status_code=204)
